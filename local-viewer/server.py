#!/usr/bin/env python3
"""Standalone local Demo viewer - no Flask, no 5E platform.

A single-file HTTP server that:
  1. Serves the viewer UI (index.html) and static assets (engine modules, radar icons).
  2. Accepts .dem file uploads, inspects them for player lists.
  3. Parses a selected player's demo data into the same JSON format the main
     server produces, so the browser replay engine works unchanged.

Usage:
    python local-viewer/server.py [--port 5050] [--host 127.0.0.1]

Open http://127.0.0.1:5050 in a browser, drag in a .dem file, pick a player,
and watch the replay.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import traceback
import urllib.parse
from http import HTTPStatus
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

# --- Path setup: make server/ importable ---
REPO = Path(__file__).resolve().parents[1]
SERVER_DIR = REPO / "server"
sys.path.insert(0, str(SERVER_DIR))

import combat          # noqa: E402
import config          # noqa: E402
import maps            # noqa: E402
import parse           # noqa: E402
import pipeline        # noqa: E402
import player_json     # noqa: E402
from local_demo_pipeline import (  # noqa: E402
    inspect_demos,
    run_local_demos,
)

log = logging.getLogger("local-viewer")

# --- Static file roots ---
VIEWER_DIR = Path(__file__).resolve().parent
STATIC_DIR = SERVER_DIR / "static"
MAPS_DIR = SERVER_DIR / "data" / "maps"
ICONS_DIR = REPO / "radar" / "icons"

# MIME types for common extensions
MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


class ViewerHandler(BaseHTTPRequestHandler):
    """HTTP handler: serve files + demo inspect/parse API."""

    # HTTP/1.1 so browsers can upload large files with Expect: 100-continue.
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        # Quieter logging - only show non-GET requests.
        if self.command != "GET":
            super().log_message(fmt, *args)

    # --- File serving ---

    def _serve_file(self, path: Path, mime: str | None = None):
        if not path.is_file():
            self._send_error(HTTPStatus.NOT_FOUND, f"Not found: {path.name}")
            return
        content_type = mime or MIME.get(path.suffix, "application/octet-stream")
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, status: int, obj: dict):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status: int, message: str):
        self._send_json(status, {"error": message})

    # --- GET routes ---

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "/index.html":
            self._serve_file(VIEWER_DIR / "index.html")
            return

        # Viewer app JS
        if path == "/app-viewer.js":
            self._serve_file(VIEWER_DIR / "app-viewer.js")
            return

        # Engine modules and replay.js
        if path.startswith("/static/"):
            rel = path[len("/static/"):]
            self._serve_file(STATIC_DIR / rel)
            return

        # Radar images
        if path.startswith("/maps/"):
            rel = urllib.parse.unquote(path[len("/maps/"):])
            self._serve_file(MAPS_DIR / rel)
            return

        # Grenade icons
        if path.startswith("/icons/"):
            rel = urllib.parse.unquote(path[len("/icons/"):])
            self._serve_file(ICONS_DIR / rel)
            return

        # Map list API
        if path == "/api/maps":
            self._send_json(HTTPStatus.OK, {"maps": maps.available_maps()})
            return

        self._send_error(HTTPStatus.NOT_FOUND, f"Unknown route: {path}")

    # --- POST routes ---

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/api/inspect":
            self._handle_inspect()
            return

        if path == "/api/parse":
            self._handle_parse()
            return

        self._send_error(HTTPStatus.NOT_FOUND, f"Unknown route: {path}")

    # --- Multipart form parsing (streaming, no external deps) ---

    def _parse_multipart_streaming(self, tmp_dir: Path) -> tuple[dict, list[Path]]:
        """Stream-parse multipart/form-data, writing file parts to tmp_dir.

        Returns (fields, file_paths). Each file is written directly to disk
        so 1 GB+ demos don't need to fit in RAM.
        """
        content_type = self.headers.get("Content-Type", "")
        if "boundary=" not in content_type:
            raise ValueError("Missing multipart boundary")

        boundary = content_type.split("boundary=", 1)[1].strip()
        if boundary.startswith('"') and boundary.endswith('"'):
            boundary = boundary[1:-1]

        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            raise ValueError("Empty request body")

        boundary_bytes = b"--" + boundary.encode()
        # The \r\n before a boundary (except the very first one) is part of
        # the previous part's content terminator.
        buf = b""
        remaining = length
        fields: dict[str, str] = {}
        file_paths: list[Path] = []
        file_idx = 0

        def read_more(need: int):
            nonlocal buf, remaining
            while len(buf) < need and remaining > 0:
                chunk_size = min(65536, remaining)
                chunk = self.rfile.read(chunk_size)
                if not chunk:
                    break
                buf += chunk
                remaining -= len(chunk)

        # Read until we find the boundary
        # State machine: HEADER -> BODY -> (boundary) -> HEADER or DONE
        state = "preamble"
        current_file = None
        current_field_name = None
        current_filename = None
        header_lines: list[str] = []

        while remaining > 0 or buf:
            if state == "preamble":
                # Find first boundary
                idx = buf.find(boundary_bytes)
                if idx == -1:
                    # Keep last len(boundary_bytes)-1 bytes in case boundary
                    # spans chunks
                    keep = len(boundary_bytes) - 1
                    if len(buf) > keep:
                        buf = buf[-keep:] if keep > 0 else b""
                    read_more(65536)
                    continue
                buf = buf[idx + len(boundary_bytes):]
                state = "header"
                header_lines = []

            elif state == "header":
                # Find header/body separator \r\n\r\n
                idx = buf.find(b"\r\n\r\n")
                if idx == -1:
                    read_more(65536)
                    if not buf:
                        break
                    continue
                header_data = buf[:idx].decode("utf-8", errors="replace")
                buf = buf[idx + 4:]
                header_lines = header_data.split("\r\n")

                current_field_name = None
                current_filename = None
                for line in header_lines:
                    if line.lower().startswith("content-disposition:"):
                        for field in line.split(";"):
                            field = field.strip()
                            if field.startswith("name="):
                                current_field_name = field[5:].strip('"')
                            elif field.startswith("filename="):
                                current_filename = field[9:].strip('"')

                if current_filename is not None:
                    # File part - open a temp file
                    if current_file:
                        current_file.close()
                    fp = tmp_dir / f"upload_{file_idx}.dem"
                    current_file = fp.open("wb")
                    file_paths.append(fp)
                    file_idx += 1
                else:
                    # Field part - accumulate in buffer
                    pass
                state = "body"

            elif state == "body":
                # Look for the next boundary
                # The boundary is preceded by \r\n
                search_buf = b"\r\n" + boundary_bytes
                idx = buf.find(search_buf)
                if idx == -1:
                    # No boundary found yet - write/keep everything except
                    # the last len(search_buf)-1 bytes (might be partial boundary)
                    safe = len(buf) - (len(search_buf) - 1)
                    if safe > 0:
                        data = buf[:safe]
                        buf = buf[safe:]
                        if current_file:
                            current_file.write(data)
                        elif current_field_name is not None:
                            # Accumulate field value (should be small)
                            fields.setdefault(current_field_name, "")
                            fields[current_field_name] += data.decode("utf-8", errors="replace")
                    read_more(65536)
                    if not buf and remaining == 0:
                        # End of body without boundary - write remaining
                        if current_file:
                            current_file.write(buf)
                            buf = b""
                        break
                    continue
                # Found boundary - write data up to it
                data = buf[:idx]
                buf = buf[idx + len(search_buf):]
                if current_file:
                    current_file.write(data)
                    current_file.close()
                    current_file = None
                elif current_field_name is not None:
                    fields[current_field_name] = data.decode("utf-8", errors="replace")

                # Check if this is the closing boundary (--)
                if buf[:2] == b"--":
                    break
                state = "header"
                header_lines = []

            if not buf and remaining == 0:
                break
            if not buf:
                read_more(65536)
                if not buf:
                    break

        if current_file:
            current_file.close()

        return fields, file_paths

    def _handle_inspect(self):
        import tempfile
        import shutil

        tmp_dir = Path(tempfile.mkdtemp(prefix="localviewer_"))
        try:
            fields, file_paths = self._parse_multipart_streaming(tmp_dir)
        except ValueError as exc:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return

        if not file_paths:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            self._send_error(HTTPStatus.BAD_REQUEST, "No .dem files uploaded")
            return

        try:
            result = inspect_demos(file_paths)
            # Don't send absolute paths to the browser.
            for f in result["files"]:
                f["name"] = Path(f["path"]).name
                del f["path"]
            self._send_json(HTTPStatus.OK, result)
        except Exception as exc:
            log.warning("Inspect failed: %s", exc)
            self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def _handle_parse(self):
        import tempfile
        import shutil

        tmp_dir = Path(tempfile.mkdtemp(prefix="localviewer_"))
        try:
            fields, file_paths = self._parse_multipart_streaming(tmp_dir)
        except ValueError as exc:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return

        if not file_paths:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            self._send_error(HTTPStatus.BAD_REQUEST, "No .dem files uploaded")
            return

        steamid = fields.get("steamid", "")
        username = fields.get("username", steamid)
        map_name = fields.get("map", "")

        if not steamid:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            self._send_error(HTTPStatus.BAD_REQUEST, "Missing steamid")
            return
        if not map_name:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            self._send_error(HTTPStatus.BAD_REQUEST, "Missing map")
            return

        try:
            map_data = maps.load_map(map_name)
            output_path = tmp_dir / "player.json"

            run_local_demos(
                file_paths,
                steamid=steamid,
                username=username,
                domain="local-viewer",
                map_name=map_name,
                output_path=output_path,
            )

            with output_path.open(encoding="utf-8") as f:
                player_json_data = json.load(f)

            # Add radar path for the browser
            player_json_data["radar"] = f"/maps/{map_name}/radar.png"
            player_json_data["transform"] = map_data["transform"]

            self._send_json(HTTPStatus.OK, player_json_data)
        except Exception as exc:
            log.warning("Parse failed: %s\n%s", exc, traceback.format_exc())
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Standalone local Demo viewer")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=5050, help="Port (default: 5050)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    available = maps.available_maps()
    if not available:
        log.error("No map data found in %s. Run setup_maps.py first.", MAPS_DIR)
        return 1
    log.info("Available maps: %s", ", ".join(available))

    server = HTTPServer((args.host, args.port), ViewerHandler)
    url = f"http://{args.host}:{args.port}"
    log.info("Local viewer ready at %s", url)
    print(f"\n  >>> Open {url} in your browser <<<\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down.")
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
