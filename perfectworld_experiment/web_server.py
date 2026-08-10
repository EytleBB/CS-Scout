"""Standalone web UI for automatic Perfect World Arena scouting."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import os
import re
import threading
import time

from flask import Flask, abort, jsonify, render_template, send_from_directory

from . import native_signer
from .auto_scout import prepare_auto_state, run_auto_state
from .current_match import read_local_state, wait_for_current_match
from .pipeline import AnalysisCancelled, DEFAULT_OUTPUT_DIR


ROOT = Path(__file__).resolve().parents[1]
SERVER_DIR = ROOT / "server"
MAPS_DIR = SERVER_DIR / "data" / "maps"
ICONS_DIR = ROOT / "radar" / "icons"
CORE_REPLAY_JS = SERVER_DIR / "static" / "replay.js"
DOMAIN_RE = re.compile(r"pwa_765\d{14}\Z")


class AutoScoutService:
    def __init__(self, *, max_demos: int = 3, all_players: bool = False):
        self.max_demos = max_demos
        self.all_players = all_players
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._analysis_requested = threading.Event()
        self._analysis_cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_match_id: str | None = None
        self._retry_match_id: str | None = None
        self._retry_after = 0.0
        self._state = {
            "platform": "perfectworld",
            "phase": "waiting",
            "message": "等待进入完美平台对局…",
            "max_demos": max_demos,
            "map": None,
            "current_match_id": None,
            "roster_count": 0,
            "targets": [],
            "timings_s": {},
            "workers": {},
            "results": [],
            "failed": [],
            "signer": {
                "ready": False,
                "code": "checking",
                "message": "正在检测完美平台组件…",
                "path": None,
                "source": None,
            },
            "updated_at": time.time(),
        }

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return deepcopy(self._state)

    def _update(self, **changes) -> None:
        with self._lock:
            self._state.update(changes)
            self._state["updated_at"] = time.time()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._watch_loop,
            name="pwa-auto-scout",
            daemon=True,
        )
        self._thread.start()

    def refresh_signer_status(self, *, force: bool = False) -> dict[str, object]:
        """Refresh the installed official component without exposing secrets."""
        signer = native_signer.get_dll_status(force=force)
        with self._lock:
            phase = str(self._state.get("phase") or "waiting")
            self._state["signer"] = signer
            if not signer["ready"] and phase in {
                "waiting", "setup_required", "error", "awaiting_confirmation"
            }:
                self._state["phase"] = "setup_required"
                self._state["message"] = str(signer["message"])
            elif signer["ready"] and phase == "setup_required":
                self._state["phase"] = "waiting"
                self._state["message"] = "等待进入完美平台对局…"
            self._state["updated_at"] = time.time()
        return signer

    def choose_install_directory(self) -> dict[str, object]:
        """Open the Windows directory picker and remember a valid install root."""
        with self._lock:
            if self._state.get("phase") in {
                "detected", "queued", "analyzing", "cancelling"
            }:
                return {
                    "selected": False,
                    "cancelled": False,
                    "error": "分析进行中，暂时不能更改目录",
                    "signer": deepcopy(self._state.get("signer", {})),
                }
        result = native_signer.choose_install_directory()
        self.refresh_signer_status()
        return result

    def configure(self, *, max_demos: int) -> dict[str, object]:
        """Update settings used by the next detected match.

        A running analysis keeps the value it started with, so changing the
        sidebar cannot produce a mixed-depth result midway through a player.
        """
        max_demos = max(1, min(10, int(max_demos)))
        with self._lock:
            busy = self._state.get("phase") in {
                "detected", "queued", "analyzing", "cancelling"
            }
            if not busy:
                self.max_demos = max_demos
                self._state["max_demos"] = max_demos
                self._state["updated_at"] = time.time()
            return {
                "max_demos": self.max_demos,
                "busy": busy,
            }

    def request_analysis(self) -> dict[str, object]:
        """Queue analysis only after the detected target list is confirmed."""
        signer = self.refresh_signer_status(force=True)
        if not signer["ready"]:
            return {
                "accepted": False,
                "phase": "setup_required",
                "error": str(signer["message"]),
                "signer": signer,
            }
        with self._lock:
            accepted = self._state.get("phase") == "awaiting_confirmation"
            if accepted:
                self._analysis_cancel.clear()
                self._state["phase"] = "queued"
                self._state["message"] = "已确认对手，准备开始分析…"
                self._state["updated_at"] = time.time()
            phase = str(self._state.get("phase", "waiting"))
        if accepted:
            self._analysis_requested.set()
        return {"accepted": accepted, "phase": phase}

    def cancel_analysis(self) -> dict[str, object]:
        """Request a cooperative stop while keeping the detected roster."""
        with self._lock:
            accepted = self._state.get("phase") in {
                "queued", "analyzing", "cancelling"
            }
            if accepted:
                self._analysis_cancel.set()
                self._state["phase"] = "cancelling"
                self._state["message"] = "正在取消分析…"
                self._state["updated_at"] = time.time()
            phase = str(self._state.get("phase", "waiting"))
        return {"accepted": accepted, "phase": phase}

    def stop(self) -> None:
        self._stop.set()

    def _watch_loop(self) -> None:
        while not self._stop.is_set():
            signer = self.refresh_signer_status()
            if not signer["ready"]:
                self._stop.wait(3.0)
                continue
            try:
                local = wait_for_current_match(timeout=3.0, poll_interval=0.5)
            except TimeoutError:
                if self._last_match_id is None:
                    self._update(
                        phase="waiting",
                        message="等待进入完美平台对局…",
                        current_match_id=None,
                        map=None,
                        roster_count=0,
                        targets=[],
                    )
                continue
            except Exception:
                self._update(phase="error", message="暂时无法读取完美平台本地状态")
                self._stop.wait(3.0)
                continue

            match = local.current_match
            if match is None:
                continue
            if match.match_id == self._last_match_id:
                # Do not repeatedly analyze the same active match.
                self._stop.wait(2.0)
                continue
            if (
                match.match_id == self._retry_match_id
                and time.monotonic() < self._retry_after
            ):
                self._stop.wait(2.0)
                continue

            self._update(
                phase="detected",
                message=f"已进入 {match.map_name}，正在识别对手…",
                current_match_id=match.match_id,
                map=match.map_name,
                roster_count=len(match.players),
                results=[],
                failed=[],
                timings_s={},
                workers={},
            )

            try:
                targets = prepare_auto_state(local, all_players=self.all_players)
            except Exception as exc:
                self._update(phase="error", message=f"对手识别失败：{exc}")
                self._retry_match_id = match.match_id
                self._retry_after = time.monotonic() + 30.0
                continue

            def progress(message: str) -> None:
                if not self._analysis_cancel.is_set():
                    self._update(phase="analyzing", message=message)

            self._analysis_requested.clear()
            self._update(
                phase="awaiting_confirmation",
                message=f"已识别 {len(targets.players)} 名对手，请确认后开始分析",
                targets=[{"username": player.nickname} for player in targets.players],
            )
            match_ended = False
            analysis_failed = False
            summary = None
            while not self._stop.is_set():
                while not self._stop.is_set():
                    if self._analysis_requested.wait(0.5):
                        self._analysis_requested.clear()
                        break
                    now = read_local_state()
                    current = now.current_match
                    if current is None or current.match_id != match.match_id:
                        match_ended = True
                        self._update(
                            phase="waiting",
                            message="等待进入下一场完美平台对局…",
                            current_match_id=None,
                            map=None,
                            roster_count=0,
                            targets=[],
                        )
                        break
                if match_ended or self._stop.is_set():
                    break

                try:
                    with self._lock:
                        max_demos = self.max_demos
                    summary = run_auto_state(
                        local,
                        max_demos,
                        all_players=self.all_players,
                        targets=targets,
                        progress=progress,
                        cancel_event=self._analysis_cancel,
                    )
                except AnalysisCancelled:
                    self._analysis_cancel.clear()
                    self._update(
                        phase="awaiting_confirmation",
                        message="分析已取消，可重新开始",
                    )
                    continue
                except Exception as exc:
                    # Protocol errors are deliberately sanitized by lower layers.
                    self._update(phase="error", message=f"自动侦察失败：{exc}")
                    self._retry_match_id = match.match_id
                    self._retry_after = time.monotonic() + 30.0
                    analysis_failed = True
                break

            if match_ended or self._stop.is_set() or analysis_failed:
                continue
            if summary is None:
                continue

            self._last_match_id = match.match_id
            self._retry_match_id = None
            self._update(
                phase="ready",
                message=f"侦察完成：成功 {len(summary['results'])} 人",
                results=summary["results"],
                failed=summary["failed"],
                map=summary["map"],
                timings_s=summary.get("timings_s", {}),
                workers=summary.get("workers", {}),
            )

            # Keep completed results visible until the current match ends or a
            # different game-start notification arrives.
            while not self._stop.wait(2.0):
                now = read_local_state()
                current = now.current_match
                if current is None or current.match_id != self._last_match_id:
                    self._last_match_id = None
                    self._update(
                        phase="waiting",
                        message="等待进入下一场完美平台对局…",
                        current_match_id=None,
                        roster_count=0,
                        targets=[],
                    )
                    break


def create_app(
    *,
    start_watcher: bool = True,
    max_demos: int = 3,
    all_players: bool = False,
) -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    service = AutoScoutService(max_demos=max_demos, all_players=all_players)
    app.config["AUTO_SCOUT_SERVICE"] = service

    @app.get("/")
    def index():
        return render_template("index.html", max_demos=max_demos)

    @app.get("/api/status")
    def api_status():
        return jsonify(service.snapshot())

    @app.post("/api/analyze")
    def api_analyze():
        queued = service.request_analysis()
        return jsonify(queued), 200 if queued["accepted"] else 409

    @app.get("/api/player/<domain>")
    def api_player(domain: str):
        if not DOMAIN_RE.fullmatch(domain):
            abort(404)
        path = Path(DEFAULT_OUTPUT_DIR) / f"player_{domain}.json"
        if not path.is_file():
            abort(404)
        return send_from_directory(path.parent, path.name)

    @app.get("/api/results")
    def api_results():
        path = Path(DEFAULT_OUTPUT_DIR) / "analysis_summary.json"
        if not path.is_file():
            abort(404)
        return send_from_directory(path.parent, path.name)

    @app.get("/maps/<path:filename>")
    def maps(filename: str):
        return send_from_directory(MAPS_DIR, filename)

    @app.get("/icons/<path:filename>")
    def icons(filename: str):
        return send_from_directory(ICONS_DIR, filename)

    @app.get("/core/replay.js")
    def replay_js():
        return send_from_directory(CORE_REPLAY_JS.parent, CORE_REPLAY_JS.name)

    if start_watcher:
        service.start()
    return app


def main() -> None:
    max_demos = int(os.getenv("CS_SCOUT_PWA_MAX_DEMOS", "3"))
    all_players = os.getenv("CS_SCOUT_PWA_ALL_PLAYERS", "0") == "1"
    app = create_app(max_demos=max_demos, all_players=all_players)
    app.run(
        host=os.getenv("CS_SCOUT_PWA_HOST", "127.0.0.1"),
        port=int(os.getenv("CS_SCOUT_PWA_PORT", "5010")),
        debug=False,
        use_reloader=False,
    )


if __name__ == "__main__":
    main()
