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
import tempfile

from flask import Flask, abort, render_template, request, jsonify, send_from_directory

import pipeline
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
SENSITIVE_PATHS = frozenset({
    "/api/analyze", "/api/status", "/api/results",
})
SENSITIVE_PREFIXES = ("/api/player/", "/output/")

log = logging.getLogger("web")
logging.basicConfig(level=logging.INFO, format="[%(name)s] %(message)s")

# Global state
state = {
    "status": "idle",       # idle / running / done / error
    "message": "",
    "progress": [],
    "results": [],
    "failed": [],
    "total_players": 0,
    "max_demos": 10,
    "map": "",
    "mode": "normal",
}
state_lock = threading.Lock()


def _is_sensitive_path(path):
    return path in SENSITIVE_PATHS or path.startswith(SENSITIVE_PREFIXES)


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
    if _is_sensitive_path(request.path):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
    return response


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


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
        "secret_configured": bool(config.SECRET_KEY),
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
        if state["status"] == "running":
            return jsonify({"error": "Analysis already running"}), 409
        mode_label = "快速" if mode == "fast" else "普通"
        state.update({"status":"running","message":f"开始{mode_label}分析...","progress":[],
                      "results":[],"failed":[],"total_players":len(usernames),
                      "max_demos":max_demos,"map":map_name,"mode":mode})
        try:
            worker = threading.Thread(
                target=_run_analysis,
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
            return jsonify({"error": "Unable to start analysis worker"}), 503
    return jsonify({"status":"started","count":len(usernames),"mode":mode})


@app.route("/api/maps")
def api_maps():
    return jsonify({"maps": maps.available_maps()})


@app.route("/api/player/<domain>")
def api_player(domain):
    auth_error = _require_access_key()
    if auth_error:
        return auth_error
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
    auth_error = _require_access_key()
    if auth_error:
        return auth_error
    with state_lock:
        return jsonify(state)


@app.route("/api/results")
def api_results():
    auth_error = _require_access_key()
    if auth_error:
        return auth_error
    summary_path = os.path.join(config.OUTPUT_DIR, "analysis_summary.json")
    if not os.path.exists(summary_path):
        return jsonify({
            "results": [], "failed": [], "max_demos": 10,
            "map": "", "mode": "normal",
        })
    try:
        with open(summary_path, encoding="utf-8") as f:
            return jsonify(json.load(f))
    except (OSError, json.JSONDecodeError):
        log.exception("Could not read analysis summary")
        return jsonify({"error": "results temporarily unavailable"}), 503


@app.route("/output/<path:filename>")
def serve_output(filename):
    auth_error = _require_access_key()
    if auth_error:
        return auth_error
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

def _run_analysis(usernames, map_name, max_demos=10, mode="normal"):
    def progress_cb(opp_idx, total, username, step, msg):
        with state_lock:
            state["message"] = f"[{opp_idx+1}/{total}] {msg}"
            for p in state["progress"]:
                if p["id"] == username:
                    p["step"] = step
                    p["msg"] = msg
                    return
            state["progress"].append({"id": username, "step": step, "msg": msg})

    def result_cb(result):
        # Publish each completed player immediately so the polling UI can grow
        # the merged Pistol view while later players are still processing.
        with state_lock:
            if state["status"] == "running":
                state["results"].append(result)

    try:
        runner = pipeline.run_fast if mode == "fast" else pipeline.run
        results, failed = runner(
            usernames, map_name, max_demos=max_demos,
            progress_cb=progress_cb, result_cb=result_cb,
        )
        with state_lock:
            state["status"] = "done"
            mode_label = "快速" if mode == "fast" else "普通"
            state["message"] = (
                f"{mode_label}分析完成：{len(results)}/{len(usernames)} 位玩家"
            )
            state["results"] = results      # already slim
            state["failed"] = failed
    except Exception:
        # Keep filesystem paths, upstream URLs and parser details in server
        # logs.  /api/status is visible to every holder of the shared key.
        log.exception("Analysis failed")
        with state_lock:
            state["status"] = "error"
            state["message"] = "分析失败，请查看服务器日志"


if __name__ == "__main__":
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    print(f"CSAI Server: http://{config.HOST}:{config.PORT}")
    app.run(host=config.HOST, port=config.PORT, debug=False)
