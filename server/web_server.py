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
import uuid
from pathlib import Path

from flask import Flask, Request, abort, render_template, request, jsonify, send_from_directory
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.serving import make_server

import pipeline
import config
import maps
import local_demo_pipeline

JSON_MAX_CONTENT_LENGTH = 16 * 1024


class _Request(Request):
    @property
    def max_content_length(self):
        # Keep the legacy JSON body limit while allowing the dedicated upload
        # endpoint to receive the explicitly bounded multipart payload.
        if self.path == "/api/local-demos/inspect" and self.mimetype == "multipart/form-data":
            return config.LOCAL_DEMO_MAX_TOTAL_BYTES + 16 * 1024 * 1024
        return JSON_MAX_CONTENT_LENGTH


app = Flask(__name__, template_folder=os.path.join(config.BASE_DIR, "templates"))
app.request_class = _Request
app.config["MAX_CONTENT_LENGTH"] = JSON_MAX_CONTENT_LENGTH


@app.errorhandler(RequestEntityTooLarge)
def handle_request_too_large(_error):
    return jsonify({"error": "request body is too large"}), 413

ICONS_DIR = os.path.abspath(os.path.join(config.BASE_DIR, "..", "radar", "icons"))
GRENADE_ICON_FILES = frozenset({
    "smoke.svg", "flash.svg", "he.svg", "molotov.svg",
    "smokegrenade.svg", "flashbang.svg", "hegrenade.svg",
    "incgrenade.svg", "molotov_bottle.svg", "inferno.svg", "map_smoke.svg",
})
SAFE_DOMAIN_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
MAX_USERNAME_LENGTH = 64
CACHE_CONTROL_PATHS = frozenset({
    "/api/analyze", "/api/status", "/api/results",
    "/api/pwa/status", "/api/pwa/config", "/api/pwa/analyze",
    "/api/local-demos/inspect", "/api/local-demos/analyze",
})
CACHE_CONTROL_PREFIXES = ("/api/player/", "/api/pwa/player/", "/output/")
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

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
    "source": "5e",
}
state_lock = threading.Lock()

_pwa_service = None
_pwa_output_dir = None
_pwa_service_lock = threading.Lock()


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
    return _local_request_is_loopback()


def _local_request_is_loopback():
    """Return true only for a loopback client when local mode is enabled."""
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
        if state["status"] == "running":
            return jsonify({"error": "Analysis already running"}), 409
        mode_label = "快速" if mode == "fast" else "普通"
        state.update({"status":"running","message":f"开始{mode_label}分析...","progress":[],
                      "results":[],"failed":[],"total_players":len(usernames),
                      "max_demos":max_demos,"map":map_name,"mode":mode})
        state["source"] = "5e"
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


def _local_demo_public_info(session_id, info, display_names):
    return {
        "session_id": session_id,
        "map": info["map"],
        "files": [
            {
                "name": display_names[i],
                "size": item["size"],
                "rounds": item.get("rounds", 0),
            }
            for i, item in enumerate(info["files"])
        ],
        "players": list(info["players"]),
    }


@app.route("/api/local-demos/inspect", methods=["POST"])
def api_local_demos_inspect():
    if not _local_request_is_loopback():
        abort(404)
    local_demo_pipeline.cleanup_expired_sessions()
    uploads = request.files.getlist("demos")
    if not uploads:
        return jsonify({"error": "No Demo files uploaded"}), 400
    if len(uploads) > config.LOCAL_DEMO_MAX_FILES:
        return jsonify({
            "error": f"Maximum {config.LOCAL_DEMO_MAX_FILES} Demo files",
        }), 400

    display_names = []
    for upload in uploads:
        original_name = str(upload.filename or "").strip()
        if not original_name or Path(original_name).suffix.lower() != ".dem":
            return jsonify({"error": "Only .dem files are supported"}), 400
        display_names.append(Path(original_name).name[:255])

    session_id = None
    try:
        session_id, session_dir = local_demo_pipeline.create_session()
        paths = []
        for upload in uploads:
            stored_path = session_dir / f"{uuid.uuid4().hex}.dem"
            upload.save(stored_path)
            paths.append(stored_path)
        info = local_demo_pipeline.inspect_demos(paths)
        local_demo_pipeline.write_manifest(session_id, info, display_names)
        return jsonify(_local_demo_public_info(session_id, info, display_names))
    except local_demo_pipeline.LocalDemoError as exc:
        if session_id:
            local_demo_pipeline.cleanup_session(session_id)
        return jsonify({"error": str(exc)}), 400
    except OSError:
        log.exception("Could not save or inspect local Demo upload")
        if session_id:
            local_demo_pipeline.cleanup_session(session_id)
        return jsonify({"error": "Could not save or read Demo files"}), 400
    except Exception:
        log.exception("Unexpected local Demo inspection failure")
        if session_id:
            local_demo_pipeline.cleanup_session(session_id)
        return jsonify({"error": "Demo inspection failed"}), 400


@app.route("/api/local-demos/analyze", methods=["POST"])
def api_local_demos_analyze():
    if not _local_request_is_loopback():
        abort(404)
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be a JSON object"}), 400
    session_id = data.get("session_id")
    steamid = data.get("steamid")
    if not isinstance(session_id, str) or not local_demo_pipeline.SESSION_ID_RE.fullmatch(session_id):
        return jsonify({"error": "Invalid local Demo session"}), 400
    if not isinstance(steamid, str) or not re.fullmatch(r"\d{10,20}", steamid.strip()):
        return jsonify({"error": "Invalid SteamID"}), 400
    steamid = steamid.strip()

    try:
        manifest = local_demo_pipeline.load_manifest(session_id)
    except local_demo_pipeline.LocalDemoError as exc:
        return jsonify({"error": str(exc)}), 400
    player = next(
        (item for item in manifest.get("players", [])
         if str(item.get("steamid", "")) == steamid),
        None,
    )
    if player is None:
        return jsonify({"error": "SteamID is not a common player in this session"}), 400

    domain = f"local_{session_id}"
    with state_lock:
        if state["status"] == "running":
            return jsonify({"error": "Analysis already running"}), 409
        state.update({
            "status": "running",
            "message": "Starting local Demo analysis...",
            "progress": [{"id": steamid, "step": 0, "msg": "Queued"}],
            "results": [],
            "failed": [],
            "total_players": 1,
            "max_demos": len(manifest["paths"]),
            "map": manifest["map"],
            "mode": "local_demos",
            "source": "local_demos",
        })
        try:
            worker = threading.Thread(
                target=_run_local_demo_analysis,
                args=(session_id, manifest, player, domain),
                daemon=True,
            )
            worker.start()
        except Exception:
            log.exception("Could not start local Demo analysis worker")
            state.update({
                "status": "error",
                "message": "Unable to start local Demo analysis",
                "progress": [],
                "results": [],
                "failed": [],
            })
            local_demo_pipeline.cleanup_session(session_id)
            return jsonify({"error": "Unable to start analysis worker"}), 503
    return jsonify({
        "status": "started",
        "source": "local_demos",
        "domain": domain,
    })


def _pwa_request_is_local():
    """Perfect World state is desktop-local and must never be exposed remotely."""
    if not (config.LOCAL_MODE and config.HOST in LOOPBACK_HOSTS):
        return False
    try:
        return ipaddress.ip_address(request.remote_addr or "").is_loopback
    except ValueError:
        return False


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
    return jsonify(_get_pwa_service().snapshot())


@app.route("/api/pwa/analyze", methods=["POST"])
def api_pwa_analyze():
    if not _pwa_request_is_local():
        abort(404)
    queued = _get_pwa_service().request_analysis()
    return jsonify(queued), 200 if queued["accepted"] else 409


@app.route("/api/pwa/player/<domain>")
def api_pwa_player(domain):
    if not _pwa_request_is_local() or not re.fullmatch(r"pwa_765\d{14}", domain):
        abort(404)
    _get_pwa_service()
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
        snapshot = {
            **state,
            "progress": list(state.get("progress", [])),
            "results": list(state.get("results", [])),
            "failed": list(state.get("failed", [])),
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
                    "source": saved.get("source", "5e"),
                })
    return jsonify(snapshot)


def _load_analysis_summary():
    summary_path = os.path.join(config.OUTPUT_DIR, "analysis_summary.json")
    if not os.path.exists(summary_path):
        return {
            "results": [], "failed": [], "max_demos": 10,
            "map": "", "mode": "normal", "source": "5e",
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

def _run_local_demo_analysis(session_id, manifest, player, domain):
    steamid = str(player["steamid"])
    username = str(player.get("username") or steamid)
    demo_paths = list(manifest["paths"])

    def progress_cb(index, total, message):
        with state_lock:
            state["message"] = f"[{index + 1}/{total}] {message}"
            if state["progress"]:
                state["progress"][0].update({"step": min(3, index + 1), "msg": message})

    try:
        output_path = Path(config.OUTPUT_DIR) / f"player_{domain}.json"
        summary = local_demo_pipeline.run_local_demos(
            demo_paths,
            steamid=steamid,
            username=username,
            domain=domain,
            map_name=manifest["map"],
            output_path=output_path,
            progress_cb=progress_cb,
        )
        result = {
            "username": username,
            "domain": domain,
            "player_json": f"/output/player_{domain}.json",
            "combat_stats": summary["combat_stats"],
            "demos_found": len(demo_paths),
            "round_count": summary["total_rounds"],
        }
        saved_summary = {
            "map": manifest["map"],
            "max_demos": len(demo_paths),
            "mode": "local_demos",
            "source": "local_demos",
            "failed": [],
            "results": [result],
        }
        pipeline._write_json_atomic(
            os.path.join(config.OUTPUT_DIR, "analysis_summary.json"),
            saved_summary,
            ensure_ascii=False,
            indent=2,
        )
        with state_lock:
            state["status"] = "done"
            state["message"] = f"Local Demo analysis complete: {summary['total_rounds']} rounds"
            state["progress"] = [{"id": steamid, "step": 4, "msg": "Complete"}]
            state["results"] = [result]
            state["failed"] = []
            state["source"] = "local_demos"
    except Exception:
        log.exception("Local Demo analysis failed for %s", steamid)
        with state_lock:
            state["status"] = "error"
            state["message"] = "Local Demo analysis failed; check server logs"
            state["results"] = []
            state["failed"] = [{"username": username, "reason": "analysis failed"}]
            state["source"] = "local_demos"
    finally:
        local_demo_pipeline.cleanup_session(session_id)


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
        # logs. /api/status is public so visitors can follow live progress.
        log.exception("Analysis failed")
        with state_lock:
            state["status"] = "error"
            state["message"] = "分析失败，请查看服务器日志"


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
    try:
        local_demo_pipeline.cleanup_expired_sessions()
    except Exception:
        log.exception("Could not clean expired local Demo sessions")
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
