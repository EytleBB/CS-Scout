"""
CSAI 2.0 Web Server — runs on VPS

Endpoints:
  POST /api/analyze          — start analysis: {usernames[], map, max_demos, key}
  GET  /api/status           — poll progress
  GET  /api/maps             — available maps
  GET  /api/player/<domain>  — per-player replay JSON
  GET  /api/results          — saved summary
  GET  /output/<file>        — serve output JSON
  GET  /maps/<path>          — serve radar images
  GET  /icons/<path>         — serve grenade icon SVGs
  GET  /                     — web UI
"""

import os
import json
import threading
import logging

from flask import Flask, render_template, request, jsonify, send_from_directory

import pipeline
import config
import maps

app = Flask(__name__, template_folder=os.path.join(config.BASE_DIR, "templates"))

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
}
state_lock = threading.Lock()


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    data = request.get_json()
    usernames = data.get("usernames", [])
    map_name = data.get("map", "")
    max_demos = max(1, min(10, int(data.get("max_demos", 10))))
    if data.get("key", "") != config.SECRET_KEY:
        return jsonify({"error": "Invalid key"}), 403
    if not usernames:
        return jsonify({"error": "No usernames provided"}), 400
    if len(usernames) > 5:
        return jsonify({"error": "Maximum 5 players"}), 400
    if not map_name:
        return jsonify({"error": "No map selected"}), 400
    with state_lock:
        if state["status"] == "running":
            return jsonify({"error": "Analysis already running"}), 409
        state.update({"status":"running","message":"开始分析...","progress":[],
                      "results":[],"failed":[],"total_players":len(usernames),
                      "max_demos":max_demos,"map":map_name})
    threading.Thread(target=_run_analysis, args=(usernames, map_name, max_demos),
                     daemon=True).start()
    return jsonify({"status":"started","count":len(usernames)})


@app.route("/api/maps")
def api_maps():
    return jsonify({"maps": maps.available_maps()})


@app.route("/api/player/<domain>")
def api_player(domain):
    path = os.path.join(config.OUTPUT_DIR, f"player_{domain}.json")
    if not os.path.exists(path):
        return jsonify({"error": "not found"}), 404
    with open(path, encoding="utf-8") as f:
        return jsonify(json.load(f))


@app.route("/api/status")
def api_status():
    with state_lock:
        return jsonify(state)


@app.route("/api/results")
def api_results():
    summary_path = os.path.join(config.OUTPUT_DIR, "analysis_summary.json")
    if not os.path.exists(summary_path):
        return jsonify({"results": [], "failed": [], "max_demos": 10, "map": ""})
    with open(summary_path, encoding="utf-8") as f:
        return jsonify(json.load(f))


@app.route("/output/<path:filename>")
def serve_output(filename):
    return send_from_directory(config.OUTPUT_DIR, filename)


@app.route("/maps/<path:filename>")
def serve_maps(filename):
    return send_from_directory(config.MAPS_DIR, filename)


@app.route("/icons/<path:filename>")
def serve_icons(filename):
    return send_from_directory(config.ICONS_DIR, filename)


# ── Background runner ─────────────────────────────────────────────────────────

def _run_analysis(usernames, map_name, max_demos=10):
    def progress_cb(opp_idx, total, username, step, msg):
        with state_lock:
            state["message"] = f"[{opp_idx+1}/{total}] {msg}"
            for p in state["progress"]:
                if p["id"] == username:
                    p["step"] = step
                    p["msg"] = msg
                    return
            state["progress"].append({"id": username, "step": step, "msg": msg})

    try:
        results, failed = pipeline.run(usernames, map_name, max_demos=max_demos,
                                       progress_cb=progress_cb)
        with state_lock:
            state["status"] = "done"
            state["message"] = f"分析完成：{len(results)}/{len(usernames)} 位玩家"
            state["results"] = results      # already slim
            state["failed"] = failed
    except Exception as e:
        log.error(f"Analysis failed: {e}")
        import traceback
        traceback.print_exc()
        with state_lock:
            state["status"] = "error"
            state["message"] = str(e)


if __name__ == "__main__":
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    print(f"CSAI Server: http://{config.HOST}:{config.PORT}")
    app.run(host=config.HOST, port=config.PORT, debug=False)
