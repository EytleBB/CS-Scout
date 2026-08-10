"""
CSAI 2.0 Web Server — runs on VPS

Endpoints:
  POST /api/analyze          — start analysis: {usernames[], map, max_demos, mode, key}
  GET  /api/status           — poll progress
  GET  /api/maps             — available maps
  GET  /api/player/<domain>  — per-player replay JSON
  GET  /api/results          — saved summary
  GET  /output/<file>        — serve output JSON
  GET  /maps/<path>          — serve radar images
  GET  /icons/<path>         — serve bundled grenade SVG icons
  GET  /healthz              — liveness check
  GET  /readyz               — dependency/configuration readiness check
  GET  /                     — web UI
"""

import os
import json
import threading
import logging
import re
import hmac
import ipaddress
import tempfile
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from functools import partial
from threading import Event as ThreadEvent

from flask import Flask, abort, render_template, request, jsonify, send_from_directory
from werkzeug.serving import make_server

import pipeline
import api_client
import config
import maps

app = Flask(__name__, template_folder=os.path.join(config.BASE_DIR, "templates"))
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024

ICONS_DIR = os.path.abspath(os.path.join(config.BASE_DIR, "..", "radar", "icons"))
GRENADE_ICON_FILES = frozenset({
    "smoke.svg", "flash.svg", "he.svg", "molotov.svg",
    "smokegrenade.svg", "flashbang.svg", "hegrenade.svg",
    "incgrenade.svg", "molotov_bottle.svg", "inferno.svg", "map_smoke.svg",
})
SAFE_DOMAIN_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
MAX_USERNAME_LENGTH = 64
CACHE_CONTROL_PATHS = frozenset({
    "/api/analyze", "/api/cancel", "/api/status", "/api/results",
    "/api/pwa/status", "/api/pwa/config", "/api/pwa/analyze",
    "/api/pwa/manual/analyze",
    "/api/pwa/dll/select",
    "/api/5e/status", "/api/5e/config", "/api/5e/exe/select",
    "/api/5e/team", "/api/5e/analyze",
})
CACHE_CONTROL_PREFIXES = ("/api/player/", "/api/pwa/player/", "/output/")
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

log = logging.getLogger("web")
logging.basicConfig(level=logging.INFO, format="[%(name)s] %(message)s")

# Global state
state = {
    "status": "idle",       # idle / running / cancelling / cancelled / done / error
    "platform": "fivee",
    "message": "",
    "progress": [],
    "results": [],
    "failed": [],
    "total_players": 0,
    "max_demos": 10,
    "map": "",
    "mode": "normal",
    "analysis_id": None,
}
state_lock = threading.RLock()
_analysis_counter = 0
_pwa_analysis_token = None
_analysis_cancel_event = None
_analysis_cancel_id = None

_pwa_service = None
_pwa_output_dir = None
_pwa_service_lock = threading.Lock()

_fivee_service = None
_fivee_service_lock = threading.Lock()


def _get_pwa_service(*, start=True):
    """Create the local Perfect World watcher only when that platform is used."""
    global _pwa_service, _pwa_output_dir
    with _pwa_service_lock:
        if _pwa_service is None:
            repository_root = str(Path(config.BASE_DIR).resolve().parent)
            if repository_root not in sys.path:
                sys.path.insert(0, repository_root)
            from perfectworld_experiment.pipeline import DEFAULT_OUTPUT_DIR
            from perfectworld_experiment.web_server import AutoScoutService

            default_depth = os.getenv("CS_SCOUT_PWA_MAX_DEMOS", "6")
            try:
                max_demos = max(1, min(10, int(default_depth)))
            except ValueError:
                max_demos = 6
            _pwa_service = AutoScoutService(max_demos=max_demos)
            _pwa_output_dir = str(DEFAULT_OUTPUT_DIR)
        service = _pwa_service
    if start:
        service.start()
    return service


def _get_fivee_service(*, start=True):
    """Create the local 5E CDP watcher only for the desktop workflow."""
    global _fivee_service
    with _fivee_service_lock:
        if _fivee_service is None:
            from fivee_monitor import FiveEAutoScoutService

            default_depth = os.getenv("CS_SCOUT_5E_MAX_DEMOS", "6")
            try:
                max_demos = max(1, min(10, int(default_depth)))
            except ValueError:
                max_demos = 6
            _fivee_service = FiveEAutoScoutService(max_demos=max_demos)
        service = _fivee_service
    if start:
        service.start()
    return service


def _next_analysis_id_locked():
    global _analysis_counter
    _analysis_counter += 1
    return _analysis_counter


def _claim_pwa_analysis():
    global _pwa_analysis_token
    with state_lock:
        if state.get("status") in {"running", "cancelling"} or _pwa_analysis_token is not None:
            return None
        token = _next_analysis_id_locked()
        _pwa_analysis_token = token
        return token


def _release_pwa_analysis(token):
    global _pwa_analysis_token
    with state_lock:
        if _pwa_analysis_token == token:
            _pwa_analysis_token = None


def _analysis_activity_locked():
    if _pwa_analysis_token is not None:
        return True, "perfectworld"
    if state.get("status") in {"running", "cancelling"}:
        return True, str(state.get("platform") or "fivee")
    return False, None


def _watch_pwa_analysis(service, token):
    try:
        while True:
            snapshot = service.snapshot()
            if snapshot.get("phase") not in {"queued", "analyzing", "cancelling"}:
                return
            threading.Event().wait(0.5)
    except Exception:
        log.exception("Could not monitor Perfect World analysis ownership")
    finally:
        _release_pwa_analysis(token)


def _begin_analysis_cancel_locked(analysis_id):
    global _analysis_cancel_event, _analysis_cancel_id
    _analysis_cancel_event = ThreadEvent()
    _analysis_cancel_id = analysis_id
    return _analysis_cancel_event


def _release_analysis_cancel(analysis_id):
    global _analysis_cancel_event, _analysis_cancel_id
    if analysis_id is None:
        return
    with state_lock:
        if _analysis_cancel_id == analysis_id:
            _analysis_cancel_event = None
            _analysis_cancel_id = None


def _must_not_cache(path):
    return path in CACHE_CONTROL_PATHS or path.startswith(CACHE_CONTROL_PREFIXES)


def _bearer_key():
    """Return the Bearer credential, or None when the header is absent/malformed."""
    authorization = request.headers.get("Authorization", "")
    if not authorization:
        return None
    scheme, separator, credential = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer":
        return None
    credential = credential.strip()
    return credential or None


def _require_access_key(data=None, allow_body_key=False):
    """Authenticate a sensitive request without leaking the configured key."""
    if _local_analysis_allowed():
        return None
    if not config.SECRET_KEY:
        return jsonify({
            "error": "Service access is disabled until CS_SCOUT_SECRET_KEY is configured"
        }), 503

    authorization_present = bool(request.headers.get("Authorization", ""))
    supplied_key = _bearer_key()
    body_key_present = False
    if supplied_key is None and not authorization_present and allow_body_key:
        body_key_present = isinstance(data, dict) and "key" in data
        candidate = data.get("key", "") if isinstance(data, dict) else ""
        supplied_key = candidate if isinstance(candidate, str) else ""

    # compare_digest is deliberately called even for a missing credential.
    valid = hmac.compare_digest(supplied_key or "", config.SECRET_KEY)
    if valid:
        return None

    credential_present = authorization_present or body_key_present
    response = jsonify({
        "error": "Invalid key" if credential_present else "Access key required"
    })
    status = 403 if credential_present else 401
    if status == 401:
        response.headers["WWW-Authenticate"] = 'Bearer realm="CS-Scout"'
    return response, status


def _loopback_local_mode_enabled():
    return config.LOCAL_MODE and config.HOST in LOOPBACK_HOSTS


def _local_analysis_allowed():
    """Allow keyless analysis only for the explicit loopback-only local mode."""
    if not _loopback_local_mode_enabled():
        return False
    try:
        return ipaddress.ip_address(request.remote_addr or "").is_loopback
    except ValueError:
        return False


def _directory_is_writable(path):
    """Create a directory if needed and perform a self-cleaning write probe."""
    try:
        os.makedirs(path, exist_ok=True)
        with tempfile.TemporaryFile(dir=path) as probe:
            probe.write(b"ready")
            probe.flush()
        return True
    except (OSError, ValueError):
        return False


@app.after_request
def prevent_sensitive_response_caching(response):
    if _must_not_cache(request.path):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
    return response


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html", local_mode=_loopback_local_mode_enabled())


@app.route("/healthz")
def healthz():
    return jsonify({"status": "alive"})


@app.route("/readyz")
def readyz():
    try:
        maps_ready = bool(maps.available_maps())
    except Exception:
        log.exception("Readiness map check failed")
        maps_ready = False
    checks = {
        "secret_configured": bool(config.SECRET_KEY) or _loopback_local_mode_enabled(),
        "maps_available": maps_ready,
        "output_writable": _directory_is_writable(config.OUTPUT_DIR),
        "demo_cache_writable": _directory_is_writable(config.DEMO_DIR),
    }
    response = jsonify({
        "status": "ready" if all(checks.values()) else "not_ready",
        "checks": checks,
    })
    response.headers["Cache-Control"] = "no-store"
    return response, 200 if all(checks.values()) else 503


@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be a JSON object"}), 400
    auth_error = _require_access_key(data, allow_body_key=True)
    if auth_error:
        return auth_error

    raw_usernames = data.get("usernames", [])
    if not isinstance(raw_usernames, list):
        return jsonify({"error": "Usernames must be a list"}), 400
    if len(raw_usernames) > 5:
        return jsonify({"error": "Maximum 5 players"}), 400
    usernames = []
    for value in raw_usernames:
        if not isinstance(value, str) or not value.strip():
            return jsonify({"error": "Each username must be a non-empty string"}), 400
        username = value.strip()
        if len(username) > MAX_USERNAME_LENGTH:
            return jsonify({
                "error": f"Each username must be at most {MAX_USERNAME_LENGTH} characters"
            }), 400
        if username not in usernames:
            usernames.append(username)
    if not usernames:
        return jsonify({"error": "No usernames provided"}), 400

    map_value = data.get("map", "")
    map_name = map_value.strip() if isinstance(map_value, str) else ""
    if not map_name:
        return jsonify({"error": "No map selected"}), 400
    if map_name not in maps.available_maps():
        return jsonify({"error": f"Unknown map: {map_name}"}), 400

    raw_max_demos = data.get("max_demos", 10)
    if isinstance(raw_max_demos, bool):
        return jsonify({"error": "max_demos must be an integer"}), 400
    try:
        max_demos = int(raw_max_demos)
    except (TypeError, ValueError, OverflowError):
        return jsonify({"error": "max_demos must be an integer"}), 400
    max_demos = max(1, min(10, max_demos))

    mode = data.get("mode", "normal")
    if not isinstance(mode, str) or mode not in {"normal", "fast"}:
        return jsonify({"error": "mode must be 'normal' or 'fast'"}), 400

    with state_lock:
        if state["status"] in {"running", "cancelling"} or _pwa_analysis_token is not None:
            return jsonify({"error": "Analysis already running"}), 409
        mode_label = "快速" if mode == "fast" else "普通"
        analysis_id = _next_analysis_id_locked()
        cancel_event = _begin_analysis_cancel_locked(analysis_id)
        state.update({"status":"running","platform":"fivee","message":f"开始{mode_label}分析...","progress":[],
                      "results":[],"failed":[],"total_players":len(usernames),
                      "max_demos":max_demos,"map":map_name,"mode":mode,
                      "analysis_id":analysis_id})
        try:
            worker = threading.Thread(
                target=partial(
                    _run_analysis,
                    cancel_event=cancel_event,
                    analysis_id=analysis_id,
                ),
                args=(usernames, map_name, max_demos, mode),
                daemon=True,
            )
            # Starting while holding state_lock closes the small race in which
            # a second request could be accepted after a failed Thread.start().
            worker.start()
        except Exception:
            log.exception("Could not start analysis worker")
            state.update({
                "status": "error",
                "message": "无法启动分析任务，请重试",
                "progress": [],
                "results": [],
                "failed": [],
            })
            _release_analysis_cancel(analysis_id)
            return jsonify({"error": "Unable to start analysis worker"}), 503
    return jsonify({"status":"started","count":len(usernames),"mode":mode})


@app.route("/api/cancel", methods=["POST"])
def api_cancel():
    data = request.get_json(silent=True)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be a JSON object"}), 400
    auth_error = _require_access_key(data, allow_body_key=True)
    if auth_error:
        return auth_error

    with state_lock:
        pwa_active = _pwa_analysis_token is not None
        cancel_event = _analysis_cancel_event
        core_active = state.get("status") in {"running", "cancelling"}

    if pwa_active:
        cancelled = _get_pwa_service(start=False).cancel_analysis()
        if cancelled.get("accepted"):
            return jsonify({"status": "cancelling", "platform": "perfectworld"}), 202
        return jsonify({"error": "Analysis is no longer running"}), 409

    if not core_active or cancel_event is None:
        return jsonify({"error": "No analysis is running"}), 409

    with state_lock:
        # Re-check after authentication/service lookup so a completed task is
        # never changed back into a cancelling state.
        if (
            state.get("status") not in {"running", "cancelling"}
            or cancel_event is not _analysis_cancel_event
        ):
            return jsonify({"error": "Analysis is no longer running"}), 409
        cancel_event.set()
        state["status"] = "cancelling"
        state["message"] = "正在取消分析…"
        platform = str(state.get("platform") or "fivee")
    return jsonify({"status": "cancelling", "platform": platform}), 202


@app.route("/api/maps")
def api_maps():
    return jsonify({"maps": maps.available_maps()})


def _pwa_request_is_local():
    """Desktop platform state must never be exposed by the hosted service."""
    if not (config.LOCAL_MODE and config.HOST in LOOPBACK_HOSTS):
        return False
    try:
        return ipaddress.ip_address(request.remote_addr or "").is_loopback
    except ValueError:
        return False


@app.route("/api/5e/config", methods=["POST"])
def api_fivee_config():
    if not _pwa_request_is_local():
        abort(404)
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be a JSON object"}), 400
    raw_max_demos = data.get("max_demos", 6)
    if isinstance(raw_max_demos, bool):
        return jsonify({"error": "max_demos must be an integer"}), 400
    try:
        max_demos = max(1, min(10, int(raw_max_demos)))
    except (TypeError, ValueError, OverflowError):
        return jsonify({"error": "max_demos must be an integer"}), 400
    mode = data.get("mode", "normal")
    if not isinstance(mode, str) or mode not in {"normal", "fast"}:
        return jsonify({"error": "mode must be 'normal' or 'fast'"}), 400
    service = _get_fivee_service(start=False)
    configured = service.configure(max_demos=max_demos, mode=mode)
    service.start()
    return jsonify(configured), 409 if configured["busy"] else 200


@app.route("/api/5e/status")
def api_fivee_status():
    if not _pwa_request_is_local():
        abort(404)
    service = _get_fivee_service(start=False)
    snapshot = service.snapshot()
    with state_lock:
        busy, owner_platform = _analysis_activity_locked()
        snapshot["analysis_busy"] = busy
        snapshot["analysis_platform"] = owner_platform
        include_analysis = (
            state.get("platform") == "fivee"
            and state.get("status") in {
                "running", "cancelling", "done", "error", "cancelled"
            }
        )
        if include_analysis:
            snapshot["analysis"] = {
                **state,
                "progress": list(state.get("progress", [])),
                "results": list(state.get("results", [])),
                "failed": list(state.get("failed", [])),
            }
    return jsonify(snapshot)


@app.route("/api/5e/exe/select", methods=["POST"])
def api_fivee_select_executable():
    if not _pwa_request_is_local():
        abort(404)
    if request.headers.get("X-CS-Scout-Request") != "1":
        return jsonify({"error": "Missing local request marker"}), 403
    with state_lock:
        busy, _owner_platform = _analysis_activity_locked()
    if busy:
        return jsonify({"error": "Analysis already running"}), 409
    service = _get_fivee_service(start=False)
    selected = service.choose_executable()
    service.start()
    if not selected.get("cancelled") and not selected.get("selected"):
        return jsonify(selected), 409
    return jsonify(selected)


@app.route("/api/5e/team", methods=["POST"])
def api_fivee_team():
    if not _pwa_request_is_local():
        abort(404)
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or data.get("team") not in {"t1", "t2"}:
        return jsonify({"error": "team must be 't1' or 't2'"}), 400
    selected = _get_fivee_service(start=False).select_own_team(data["team"])
    return jsonify(selected), 200 if selected["accepted"] else 409


@app.route("/api/5e/analyze", methods=["POST"])
def api_fivee_analyze():
    if not _pwa_request_is_local():
        abort(404)
    data = request.get_json(silent=True)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be a JSON object"}), 400
    map_override = data.get("map", "")
    if not isinstance(map_override, str):
        return jsonify({"error": "map must be a string"}), 400

    service = _get_fivee_service(start=False)
    payload = service.analysis_payload(map_override=map_override.strip())
    if payload is None:
        return jsonify({"error": "Detected 5E roster is not ready"}), 409
    if payload["map"] not in maps.available_maps():
        return jsonify({"error": f"Unknown map: {payload['map']}"}), 400

    usernames = payload["usernames"]
    player_hints = payload["player_hints"]
    mode = payload["mode"]
    max_demos = payload["max_demos"]
    with state_lock:
        if state["status"] in {"running", "cancelling"} or _pwa_analysis_token is not None:
            return jsonify({"error": "Analysis already running"}), 409
        mode_label = "快速" if mode == "fast" else "普通"
        analysis_id = _next_analysis_id_locked()
        cancel_event = _begin_analysis_cancel_locked(analysis_id)
        state.update({
            "status": "running",
            "platform": "fivee",
            "message": f"开始{mode_label}分析...",
            "progress": [],
            "results": [],
            "failed": [],
            "total_players": len(usernames),
            "max_demos": max_demos,
            "map": payload["map"],
            "mode": mode,
            "analysis_id": analysis_id,
        })
        try:
            service.mark_analysis_started(analysis_id)
            worker = threading.Thread(
                target=_run_fivee_analysis,
                args=(
                    usernames, payload["map"], max_demos, mode,
                    player_hints, service, cancel_event, analysis_id,
                ),
                daemon=True,
            )
            worker.start()
        except Exception:
            log.exception("Could not start automatic 5E analysis worker")
            state.update({
                "status": "error",
                "message": "无法启动分析任务，请重试",
                "progress": [],
                "results": [],
                "failed": [],
            })
            service.mark_analysis_start_failed()
            _release_analysis_cancel(analysis_id)
            return jsonify({"error": "Unable to start analysis worker"}), 503
    return jsonify({"status": "started", "count": len(usernames), "mode": mode})


@app.route("/api/pwa/config", methods=["POST"])
def api_pwa_config():
    if not _pwa_request_is_local():
        abort(404)
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be a JSON object"}), 400
    raw_max_demos = data.get("max_demos", 6)
    if isinstance(raw_max_demos, bool):
        return jsonify({"error": "max_demos must be an integer"}), 400
    try:
        max_demos = int(raw_max_demos)
    except (TypeError, ValueError, OverflowError):
        return jsonify({"error": "max_demos must be an integer"}), 400
    max_demos = max(1, min(10, max_demos))
    service = _get_pwa_service(start=False)
    configured = service.configure(max_demos=max_demos)
    service.start()
    return jsonify(configured), 409 if configured["busy"] else 200


@app.route("/api/pwa/status")
def api_pwa_status():
    if not _pwa_request_is_local():
        abort(404)
    snapshot = _get_pwa_service(start=False).snapshot()
    with state_lock:
        busy, owner_platform = _analysis_activity_locked()
        include_analysis = (
            _pwa_analysis_token is None
            and state.get("platform") == "perfectworld"
            and state.get("status") in {
                "running", "cancelling", "done", "error", "cancelled"
            }
        )
        if include_analysis:
            snapshot["analysis"] = {
                **state,
                "progress": list(state.get("progress", [])),
                "results": list(state.get("results", [])),
                "failed": list(state.get("failed", [])),
            }
    snapshot["analysis_busy"] = busy
    snapshot["analysis_platform"] = owner_platform
    return jsonify(snapshot)


@app.route("/api/pwa/manual/analyze", methods=["POST"])
def api_pwa_manual_analyze():
    if not _pwa_request_is_local():
        abort(404)
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be a JSON object"}), 400

    raw_usernames = data.get("usernames", [])
    if not isinstance(raw_usernames, list):
        return jsonify({"error": "usernames must be a list"}), 400
    if len(raw_usernames) > 5:
        return jsonify({"error": "Maximum 5 players"}), 400
    usernames = []
    for value in raw_usernames:
        if not isinstance(value, str) or not value.strip():
            return jsonify({"error": "Each username must be a non-empty string"}), 400
        username = value.strip()
        if len(username) > MAX_USERNAME_LENGTH:
            return jsonify({
                "error": f"Each username must be at most {MAX_USERNAME_LENGTH} characters"
            }), 400
        if username not in usernames:
            usernames.append(username)
    if not usernames:
        return jsonify({"error": "No usernames provided"}), 400

    map_value = data.get("map", "")
    map_name = map_value.strip() if isinstance(map_value, str) else ""
    if not map_name:
        return jsonify({"error": "No map selected"}), 400
    if map_name not in maps.available_maps():
        return jsonify({"error": f"Unknown map: {map_name}"}), 400

    raw_max_demos = data.get("max_demos", 6)
    if isinstance(raw_max_demos, bool):
        return jsonify({"error": "max_demos must be an integer"}), 400
    try:
        max_demos = max(1, min(10, int(raw_max_demos)))
    except (TypeError, ValueError, OverflowError):
        return jsonify({"error": "max_demos must be an integer"}), 400

    service = _get_pwa_service(start=False)
    signer = service.snapshot().get("signer", {})
    if not isinstance(signer, dict) or not signer.get("ready"):
        return jsonify({"error": "Perfect World component is not ready"}), 409

    with state_lock:
        if state["status"] in {"running", "cancelling"} or _pwa_analysis_token is not None:
            return jsonify({"error": "Analysis already running"}), 409
        analysis_id = _next_analysis_id_locked()
        cancel_event = _begin_analysis_cancel_locked(analysis_id)
        state.update({
            "status": "running",
            "platform": "perfectworld",
            "message": "正在解析手动输入的完美平台玩家…",
            "progress": [],
            "results": [],
            "failed": [],
            "total_players": len(usernames),
            "max_demos": max_demos,
            "map": map_name,
            "mode": "normal",
            "analysis_id": analysis_id,
        })
        try:
            threading.Thread(
                target=_run_pwa_manual_analysis,
                args=(usernames, map_name, max_demos, cancel_event, analysis_id),
                daemon=True,
            ).start()
        except Exception:
            log.exception("Could not start manual Perfect World analysis worker")
            state.update({
                "status": "error",
                "message": "无法启动分析任务，请重试",
                "progress": [],
                "results": [],
                "failed": [],
            })
            _release_analysis_cancel(analysis_id)
            return jsonify({"error": "Unable to start analysis worker"}), 503
    return jsonify({"status": "started", "count": len(usernames)})


@app.route("/api/pwa/dll/select", methods=["POST"])
def api_pwa_select_dll():
    if not _pwa_request_is_local():
        abort(404)
    if request.headers.get("X-CS-Scout-Request") != "1":
        return jsonify({"error": "Missing local request marker"}), 403
    with state_lock:
        busy, _owner_platform = _analysis_activity_locked()
    if busy:
        return jsonify({"error": "Analysis already running"}), 409
    service = _get_pwa_service(start=False)
    selected = service.choose_install_directory()
    service.start()
    if selected.get("error"):
        return jsonify(selected), 409
    return jsonify(selected)


@app.route("/api/pwa/analyze", methods=["POST"])
def api_pwa_analyze():
    if not _pwa_request_is_local():
        abort(404)
    service = _get_pwa_service(start=False)
    token = _claim_pwa_analysis()
    if token is None:
        return jsonify({"error": "Analysis already running"}), 409
    try:
        queued = service.request_analysis()
    except Exception:
        _release_pwa_analysis(token)
        raise
    if not queued["accepted"]:
        _release_pwa_analysis(token)
        return jsonify(queued), 409
    with state_lock:
        # Automatic Perfect World analysis owns its own service snapshot. Clear
        # an older manual result so it cannot mask the new automatic progress.
        state.update({
            "status": "idle",
            "platform": "perfectworld",
            "message": "",
            "progress": [],
            "results": [],
            "failed": [],
            "total_players": 0,
            "map": "",
            "mode": "normal",
            "analysis_id": None,
        })
    try:
        threading.Thread(
            target=_watch_pwa_analysis,
            args=(service, token),
            daemon=True,
        ).start()
    except Exception:
        log.exception("Could not start Perfect World ownership monitor")
    return jsonify(queued), 200


@app.route("/api/pwa/player/<domain>")
def api_pwa_player(domain):
    if not _pwa_request_is_local() or not re.fullmatch(r"pwa_765\d{14}", domain):
        abort(404)
    _get_pwa_service(start=False)
    return send_from_directory(_pwa_output_dir, f"player_{domain}.json")


@app.route("/api/player/<domain>")
def api_player(domain):
    if not SAFE_DOMAIN_RE.fullmatch(domain):
        return jsonify({"error": "not found"}), 404
    output_root = os.path.realpath(config.OUTPUT_DIR)
    path = os.path.realpath(os.path.join(output_root, f"player_{domain}.json"))
    try:
        in_output_dir = os.path.commonpath((output_root, path)) == output_root
    except ValueError:
        in_output_dir = False
    if not in_output_dir or not os.path.isfile(path):
        return jsonify({"error": "not found"}), 404
    try:
        with open(path, encoding="utf-8") as f:
            return jsonify(json.load(f))
    except (OSError, json.JSONDecodeError):
        log.exception("Could not read player output for %s", domain)
        return jsonify({"error": "player output temporarily unavailable"}), 503


@app.route("/api/status")
def api_status():
    with state_lock:
        busy, owner_platform = _analysis_activity_locked()
        snapshot = {
            **state,
            "progress": list(state.get("progress", [])),
            "results": list(state.get("results", [])),
            "failed": list(state.get("failed", [])),
            "analysis_busy": busy,
            "analysis_platform": owner_platform,
        }

    # Gunicorn state is in memory, while completed results live on disk. After
    # a service restart, expose the saved summary so a visitor immediately sees
    # the latest completed analysis instead of an empty idle page.
    if snapshot.get("status") == "idle" and not snapshot.get("results"):
        try:
            saved = _load_analysis_summary()
        except (OSError, json.JSONDecodeError, ValueError):
            log.exception("Could not restore saved analysis summary")
        else:
            saved_results = saved.get("results", [])
            saved_failed = saved.get("failed", [])
            if saved_results or saved_failed:
                snapshot.update({
                    "status": "done",
                    "message": "已加载最近一次分析结果",
                    "progress": [],
                    "results": saved_results,
                    "failed": saved_failed,
                    "total_players": len(saved_results) + len(saved_failed),
                    "max_demos": saved.get("max_demos", 10),
                    "map": saved.get("map", ""),
                    "mode": saved.get("mode", "normal"),
                })
    return jsonify(snapshot)


def _load_analysis_summary():
    summary_path = os.path.join(config.OUTPUT_DIR, "analysis_summary.json")
    if not os.path.exists(summary_path):
        return {
            "results": [], "failed": [], "max_demos": 10,
            "map": "", "mode": "normal",
        }
    with open(summary_path, encoding="utf-8") as f:
        summary = json.load(f)
    if not isinstance(summary, dict):
        raise ValueError("analysis summary must be an object")
    if not isinstance(summary.get("results", []), list):
        raise ValueError("analysis summary results must be a list")
    if not isinstance(summary.get("failed", []), list):
        raise ValueError("analysis summary failed must be a list")
    return summary


@app.route("/api/results")
def api_results():
    try:
        return jsonify(_load_analysis_summary())
    except (OSError, json.JSONDecodeError, ValueError):
        log.exception("Could not read analysis summary")
        return jsonify({"error": "results temporarily unavailable"}), 503


@app.route("/output/<path:filename>")
def serve_output(filename):
    return send_from_directory(config.OUTPUT_DIR, filename)


@app.route("/maps/<path:filename>")
def serve_maps(filename):
    return send_from_directory(config.MAPS_DIR, filename)


@app.route("/icons/<path:filename>")
def serve_icons(filename):
    # A fixed asset allowlist keeps this endpoint limited to replay assets.
    # Decoys intentionally have no icon.
    if filename not in GRENADE_ICON_FILES:
        abort(404)
    return send_from_directory(ICONS_DIR, filename, mimetype="image/svg+xml")


# ── Background runner ─────────────────────────────────────────────────────────


def _resolve_pwa_manual_players(usernames, local_state):
    """Resolve manual names without exposing the Perfect World session token."""
    from perfectworld_experiment.auto_scout import resolve_roster
    from perfectworld_experiment.pwa_client import PerfectWorldPlayer

    roster_by_name = {}
    if local_state.current_match is not None:
        try:
            roster = resolve_roster(local_state)
        except Exception:
            log.warning(
                "Could not use the active Perfect World roster for manual identity resolution",
                exc_info=True,
            )
        else:
            roster_by_name = {
                player.nickname.casefold(): player
                for player in roster
                if player.nickname.strip()
            }

    resolved = [None] * len(usernames)
    failures = [None] * len(usernames)
    unresolved = {}
    for index, username in enumerate(usernames):
        direct = roster_by_name.get(username.casefold())
        if direct is not None:
            resolved[index] = direct
        else:
            unresolved[index] = username

    if unresolved:
        with ThreadPoolExecutor(
            max_workers=min(5, len(unresolved)),
            thread_name_prefix="pwa-manual-identity",
        ) as pool:
            futures = {
                pool.submit(api_client.resolve_player_identity, username): index
                for index, username in unresolved.items()
            }
            for future in as_completed(futures):
                index = futures[future]
                try:
                    identity = future.result()
                except Exception:
                    failures[index] = {
                        "username": usernames[index],
                        "reason": "未找到玩家或无法解析 SteamID",
                    }
                else:
                    resolved[index] = PerfectWorldPlayer(
                        identity["steamid"],
                        identity["steamid"],
                        identity["username"],
                    )
    return (
        [player for player in resolved if player is not None],
        [failure for failure in failures if failure is not None],
    )


def _execute_pwa_manual_analysis(
    usernames, map_name, max_demos, *, progress, cancel_event
):
    from perfectworld_experiment.current_match import read_local_state
    from perfectworld_experiment.pipeline import run_roster

    local_state = read_local_state()
    session = local_state.session
    if session is None:
        raise RuntimeError("尚未检测到完美平台登录会话，请先登录完美平台")
    players, identity_failures = _resolve_pwa_manual_players(usernames, local_state)
    if not players:
        return {"results": [], "failed": identity_failures}
    summary = run_roster(
        players,
        session.account_steamid,
        session.access_token,
        map_name,
        max_demos,
        current_match_id=None,
        target_scope="manual_usernames",
        source="manual_usernames",
        progress=progress,
        cancel_event=cancel_event,
    )
    summary["failed"] = identity_failures + list(summary.get("failed", []))
    return summary


def _run_pwa_manual_analysis(
    usernames, map_name, max_demos, cancel_event=None, analysis_id=None
):
    def progress(message):
        with state_lock:
            if state.get("status") not in {"running", "cancelling"}:
                return
            state["message"] = str(message)

    try:
        summary = _execute_pwa_manual_analysis(
            usernames,
            map_name,
            max_demos,
            progress=progress,
            cancel_event=cancel_event,
        )
        if cancel_event is not None and cancel_event.is_set():
            from perfectworld_experiment.pipeline import AnalysisCancelled
            raise AnalysisCancelled("分析已取消")
        results = list(summary.get("results", []))
        failed = list(summary.get("failed", []))
        with state_lock:
            state.update({
                "status": "done",
                "message": f"完美平台分析完成：{len(results)}/{len(usernames)} 位玩家",
                "results": results,
                "failed": failed,
            })
        return True
    except Exception as exc:
        from perfectworld_experiment.pipeline import AnalysisCancelled

        if isinstance(exc, AnalysisCancelled):
            with state_lock:
                state["status"] = "cancelled"
                state["message"] = "分析已取消，可重新开始"
            return False
        log.exception("Manual Perfect World analysis failed")
        with state_lock:
            state["status"] = "error"
            state["message"] = "完美平台分析失败，请查看终端"
            state["failed"] = [
                {"username": username, "reason": "玩家解析或 Demo 分析失败"}
                for username in usernames
            ]
        return False
    finally:
        _release_analysis_cancel(analysis_id)


def _run_fivee_analysis(
    usernames, map_name, max_demos, mode, player_hints, service,
    cancel_event=None, analysis_id=None,
):
    """Run the shared 5E pipeline and close the automatic service state."""
    completed, message = _run_analysis(
        usernames, map_name, max_demos=max_demos, mode=mode,
        player_hints=player_hints,
        cancel_event=cancel_event, analysis_id=analysis_id,
    )
    try:
        service.finish_analysis(success=completed, message=message)
    except Exception:
        log.exception("Could not publish automatic 5E completion state")


def _run_analysis(
    usernames, map_name, max_demos=10, mode="normal", player_hints=None,
    cancel_event=None, analysis_id=None,
):
    def progress_cb(opp_idx, total, username, step, msg):
        with state_lock:
            if state.get("status") != "running":
                return
            state["message"] = f"[{opp_idx+1}/{total}] {msg}"
            for p in state["progress"]:
                if p["index"] == opp_idx:
                    p["id"] = username
                    p["step"] = step
                    p["msg"] = msg
                    p["index"] = opp_idx
                    p["total"] = total
                    p["updated_at"] = time.time()
                    return
            state["progress"].append({
                "id": username,
                "step": step,
                "msg": msg,
                "index": opp_idx,
                "total": total,
                "updated_at": time.time(),
            })

    def result_cb(result):
        # Publish each completed player immediately so the polling UI can grow
        # the merged Pistol view while later players are still processing.
        with state_lock:
            if state["status"] == "running":
                state["results"].append(result)

    try:
        runner = pipeline.run_fast if mode == "fast" else pipeline.run
        runner_options = {
            "max_demos": max_demos,
            "progress_cb": progress_cb,
            "result_cb": result_cb,
        }
        if player_hints is not None:
            runner_options["player_hints"] = player_hints
        if cancel_event is not None:
            runner_options["cancel_event"] = cancel_event
        results, failed = runner(usernames, map_name, **runner_options)
        if cancel_event is not None and cancel_event.is_set():
            raise pipeline.AnalysisCancelled("分析已取消")
        with state_lock:
            state["status"] = "done"
            mode_label = "快速" if mode == "fast" else "普通"
            state["message"] = (
                f"{mode_label}分析完成：{len(results)}/{len(usernames)} 位玩家"
            )
            state["results"] = results      # already slim
            state["failed"] = failed
            message = str(state["message"])
        return True, message
    except pipeline.AnalysisCancelled:
        with state_lock:
            state["status"] = "cancelled"
            state["message"] = "分析已取消，可重新开始"
            message = str(state["message"])
        return False, message
    except Exception:
        # Keep filesystem paths, upstream URLs and parser details in server
        # logs. /api/status is public so visitors can follow live progress.
        log.exception("Analysis failed")
        with state_lock:
            state["status"] = "error"
            state["message"] = "分析失败，请查看服务器日志"
            message = str(state["message"])
        return False, message
    finally:
        _release_analysis_cancel(analysis_id)


def _write_startup_info(path, token, port):
    """Atomically publish the process and actual bound port to the launcher."""
    if not path:
        return
    if not token:
        raise RuntimeError(
            "CS_SCOUT_STARTUP_TOKEN is required when CS_SCOUT_STARTUP_INFO is set"
        )

    actual_port = int(port)
    if not 1 <= actual_port <= 65535:
        raise ValueError(f"Invalid bound server port: {actual_port}")

    target = os.path.abspath(os.path.expanduser(path))
    parent = os.path.dirname(target)
    os.makedirs(parent, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=".cs-scout-startup-", suffix=".tmp", dir=parent
    )
    stream = None
    try:
        stream = os.fdopen(descriptor, "w", encoding="ascii", newline="\n")
        descriptor = None
        with stream:
            json.dump(
                {
                    "pid": os.getpid(),
                    "parent_pid": os.getppid(),
                    "port": actual_port,
                    "token": token,
                },
                stream,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        stream = None
        os.replace(temporary, target)
    finally:
        if stream is not None:
            stream.close()
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _run_development_server():
    """Run the local server and optionally report an OS-assigned port."""
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    server = None
    try:
        server = make_server(
            config.HOST,
            config.PORT,
            app,
            threaded=True,
        )
        actual_port = int(server.server_port)
        startup_info_path = os.getenv("CS_SCOUT_STARTUP_INFO", "").strip()
        startup_token = os.getenv("CS_SCOUT_STARTUP_TOKEN", "").strip()
        _write_startup_info(startup_info_path, startup_token, actual_port)

        print(f"CSAI Server: http://{config.HOST}:{actual_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    finally:
        if server is not None:
            server.server_close()


if __name__ == "__main__":
    _run_development_server()
