"""
CSAI Web Server — runs on VPS

Endpoints:
  POST /api/analyze_by_names — start analysis by 5E usernames
  GET  /api/status           — poll progress
  GET  /api/results          — get saved results
  GET  /output/<file>        — serve heatmap images
  GET  /                     — web UI
"""

import os
import json
import threading
import logging

from flask import Flask, render_template, request, jsonify, send_from_directory

import pipeline
import config

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
}
state_lock = threading.Lock()


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/analyze_by_names", methods=["POST"])
def api_analyze_by_names():
    data = request.get_json()
    usernames = data.get("usernames", [])
    max_demos = max(1, min(10, int(data.get("max_demos", 10))))
    key = data.get("key", "")

    if key != config.SECRET_KEY:
        return jsonify({"error": "Invalid key"}), 403
    if not usernames:
        return jsonify({"error": "No usernames provided"}), 400
    if len(usernames) > 5:
        return jsonify({"error": "Maximum 5 players"}), 400

    with state_lock:
        if state["status"] == "running":
            return jsonify({"error": "Analysis already running"}), 409
        state.update({
            "status": "running",
            "message": "开始分析...",
            "progress": [],
            "results": [],
            "failed": [],
            "total_players": len(usernames),
            "max_demos": max_demos,
        })

    thread = threading.Thread(
        target=_run_analysis, args=(usernames, max_demos), daemon=True
    )
    thread.start()

    return jsonify({"status": "started", "count": len(usernames)})


@app.route("/api/status")
def api_status():
    with state_lock:
        return jsonify(state)


@app.route("/api/results")
def api_results():
    summary_path = os.path.join(config.OUTPUT_DIR, "analysis_summary.json")
    if not os.path.exists(summary_path):
        return jsonify({"results": [], "failed": [], "max_demos": 10})

    with open(summary_path, encoding="utf-8") as f:
        data = json.load(f)

    # Support both new dict format and old list format
    if isinstance(data, dict):
        result_list = data.get("results", [])
        failed_list = data.get("failed", [])
        max_demos = data.get("max_demos", 10)
    else:
        result_list, failed_list, max_demos = data, [], 10

    for r in result_list:
        if "demos_found" not in r:
            r["demos_found"] = r.get("demo_count", 0)
        if "heatmap" in r and not r["heatmap"].startswith("/output/"):
            r["heatmap"] = "/output/" + r["heatmap"]
        if "tiles" in r and isinstance(r["tiles"], dict):
            r["tiles"] = {k: ("/output/" + v if not v.startswith("/output/") else v)
                          for k, v in r["tiles"].items()}

    return jsonify({"results": result_list, "failed": failed_list, "max_demos": max_demos})


@app.route("/output/<path:filename>")
def serve_output(filename):
    return send_from_directory(config.OUTPUT_DIR, filename)


# ── Background runner ─────────────────────────────────────────────────────────

def _run_analysis(usernames, max_demos=10):
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
        results, failed = pipeline.run_by_usernames(usernames, max_demos=max_demos, progress_cb=progress_cb)
        total_demos = sum(r["demos_found"] for r in results)

        with state_lock:
            state["status"] = "done"
            state["message"] = f"分析完成：{len(results)}/{len(usernames)} 位玩家，共 {total_demos} 个 demo"
            state["results"] = [
                {
                    "username":     r["username"],
                    "domain":       r["domain"],
                    "heatmap":      f"/output/heatmap_{r['domain']}.png",
                    "tiles":        {k: f"/output/{v}" for k, v in (r.get("tile_paths") or {}).items()},
                    "demos_found":  r["demos_found"],
                    "demo_count":   r["demo_count"],
                    "record_count": r["record_count"],
                    "round_count":  r["round_count"],
                    "zone_stats":   r["zone_stats"],
                    "combat_stats": r.get("combat_stats"),
                }
                for r in results
            ]
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
