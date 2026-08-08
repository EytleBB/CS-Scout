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

    # --- Multipart form parsing (minimal, no external deps) ---

    def _read_multipart(self) -> tuple[dict, list[tuple[str, str, bytes]]]:
        """Parse multipart/form-data. Returns (fields, files).

        files is a list of (field_name, filename, content_bytes).
        """
        content_type = self.headers.get("Content-Type", "")
        if "boundary=" not in content_type:
            raise ValueError("Missing multipart boundary")

        boundary = content_type.split("boundary=", 1)[1].strip()
        # Boundaries may be quoted.
        if boundary.startswith('"') and boundary.endswith('"'):
            boundary = boundary[1:-1]

        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            raise ValueError("Empty request body")

        body = self.rfile.read(length)
        delimiter = b"--" + boundary.encode()

        fields: dict[str, str] = {}
        files: list[tuple[str, str, bytes]] = []

        # Split on boundary
        parts = body.split(delimiter)
        for part in parts:
            # Skip preamble, epilogue, and closing boundary marker
            if part in (b"", b"--", b"--\r\n", b"\r\n", b"\r\n--\r\n"):
                continue
            # Strip leading \r\n
            if part.startswith(b"\r\n"):
                part = part[2:]
            # Strip trailing \r\n
            if part.endswith(b"\r\n"):
                part = part[:-2]
            # Check for closing boundary --
            if part == b"--":
                continue

            # Split header and content
            if b"\r\n\r\n" not in part:
                continue
            header_block, content = part.split(b"\r\n\r\n", 1)

            # Parse Content-Disposition
            disposition = ""
            for line in header_block.split(b"\r\n"):
                if line.lower().startswith(b"content-disposition:"):
                    disposition = line.decode("utf-8", errors="replace")
                    break

            if not disposition:
                continue

            # Extract name and filename
            name = ""
            filename = None
            for field in disposition.split(";"):
                field = field.strip()
                if field.startswith("name="):
                    name = field[5:].strip('"')
                elif field.startswith("filename="):
                    filename = field[9:].strip('"')

            if filename is not None:
                files.append((name, filename, content))
            else:
                fields[name] = content.decode("utf-8", errors="replace")

        return fields, files

    def _handle_inspect(self):
        try:
            fields, files = self._read_multipart()
        except ValueError as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return

        dem_files = [(name, Path(filename), content)
                     for name, filename, content in files
                     if filename.lower().endswith(".dem")]
        if not dem_files:
            self._send_error(HTTPStatus.BAD_REQUEST, "No .dem files uploaded")
            return

        # Write to temp files for inspection
        import tempfile
        import shutil

        tmp_dir = Path(tempfile.mkdtemp(prefix="localviewer_"))
        try:
            demo_paths = []
            for i, (_, filename, content) in enumerate(dem_files):
                p = tmp_dir / f"demo_{i}.dem"
                p.write_bytes(content)
                demo_paths.append(p)

            result = inspect_demos(demo_paths)
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
        try:
            fields, files = self._read_multipart()
        except ValueError as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return

        dem_files = [(name, Path(filename), content)
                     for name, filename, content in files
                     if filename.lower().endswith(".dem")]
        if not dem_files:
            self._send_error(HTTPStatus.BAD_REQUEST, "No .dem files uploaded")
            return

        steamid = fields.get("steamid", "")
        username = fields.get("username", steamid)
        map_name = fields.get("map", "")

        if not steamid:
            self._send_error(HTTPStatus.BAD_REQUEST, "Missing steamid")
            return
        if not map_name:
            self._send_error(HTTPStatus.BAD_REQUEST, "Missing map")
            return

        import tempfile
        import shutil

        tmp_dir = Path(tempfile.mkdtemp(prefix="localviewer_"))
        try:
            demo_paths = []
            for i, (_, filename, content) in enumerate(dem_files):
                p = tmp_dir / f"demo_{i}.dem"
                p.write_bytes(content)
                demo_paths.append(p)

            map_data = maps.load_map(map_name)
            output_path = tmp_dir / "player.json"

            summary = run_local_demos(
                demo_paths,
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
