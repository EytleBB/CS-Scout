"""
CSAI Web Server — runs on VPS

Endpoints:
  POST /api/analyze      — start analysis with known steamids (Mode A)
  POST /api/analyze_auto — auto-detect opponents via 5E API (Mode B)
  GET  /api/status       — poll progress
  GET  /api/results      — get saved results
  GET  /output/<file>    — serve heatmap images
  GET  /                 — web UI
"""

import os
import json
import threading
import logging

from flask import Flask, render_template, request, jsonify, send_from_directory

import pipeline
import api_client
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
    """New main endpoint: analyze opponents by 5E username."""
    data = request.get_json()
    usernames = data.get("usernames", [])
    max_demos = int(data.get("max_demos", 10))
    max_demos = max(1, min(10, max_demos))
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
        state["status"] = "running"
        state["message"] = "开始分析..."
        state["progress"] = []
        state["results"] = []
        state["failed"] = []
        state["total_players"] = len(usernames)
        state["max_demos"] = max_demos

    thread = threading.Thread(
        target=_run_analysis_by_names, args=(usernames, max_demos), daemon=True
    )
    thread.start()

    return jsonify({"status": "started", "count": len(usernames)})


@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    data = request.get_json()
    steamids = data.get("steamids", [])
    my_uuid = data.get("my_uuid", "")
    key = data.get("key", "")

    if key != config.SECRET_KEY:
        return jsonify({"error": "Invalid key"}), 403

    if not steamids:
        return jsonify({"error": "No steamids provided"}), 400

    if not my_uuid:
        return jsonify({"error": "No my_uuid provided"}), 400

    with state_lock:
        if state["status"] == "running":
            return jsonify({"error": "Analysis already running"}), 409
        state["status"] = "running"
        state["message"] = "Starting analysis..."
        state["progress"] = []
        state["results"] = []

    thread = threading.Thread(
        target=_run_analysis, args=(my_uuid, steamids), daemon=True
    )
    thread.start()

    return jsonify({"status": "started", "count": len(steamids)})


@app.route("/api/analyze_auto", methods=["POST"])
def api_analyze_auto():
    """Auto-detect opponents via 5E current match API, then analyze them."""
    data = request.get_json()
    my_uuid = data.get("my_uuid", "")
    key = data.get("key", "")

    if key != config.SECRET_KEY:
        return jsonify({"error": "Invalid key"}), 403
    if not my_uuid:
        return jsonify({"error": "No my_uuid provided"}), 400

    with state_lock:
        if state["status"] == "running":
            return jsonify({"error": "Analysis already running"}), 409
        state["status"] = "running"
        state["message"] = "Auto-detecting opponents via 5E API..."
        state["progress"] = []
        state["results"] = []

    thread = threading.Thread(
        target=_run_auto_analysis, args=(my_uuid,), daemon=True
    )
    thread.start()

    return jsonify({"status": "started", "mode": "auto"})


@app.route("/api/probe/<steamid>")
def api_probe(steamid):
    """Probe 5E API endpoints to find steamid→UUID mapping.

    Visit http://server:5000/api/probe/76561199708556739 to test.
    """
    results = api_client.probe_steamid_to_uuid(steamid)
    hits = [r for r in results if r.get("has_data")]
    return jsonify({
        "steamid": steamid,
        "total_probed": len(results),
        "hits": len(hits),
        "results": results,
    })


@app.route("/api/status")
def api_status():
    with state_lock:
        return jsonify(state)


@app.route("/api/results")
def api_results():
    summary_path = os.path.join(config.OUTPUT_DIR, "analysis_summary.json")
    if os.path.exists(summary_path):
        with open(summary_path, encoding="utf-8") as f:
            data = json.load(f)
        # New format is dict with "results", "failed", "max_demos"; old format is a list
        if isinstance(data, dict):
            result_list = data.get("results", [])
            failed_list = data.get("failed", [])
            max_demos = data.get("max_demos", 10)
        else:
            result_list = data
            failed_list = []
            max_demos = 10
        for r in result_list:
            if "demos_found" not in r:
                r["demos_found"] = r.get("demo_count", 0)
            if "heatmap" in r and not r["heatmap"].startswith("/output/"):
                r["heatmap"] = "/output/" + r["heatmap"]
            # Normalize tile paths
            if "tiles" in r and isinstance(r["tiles"], dict):
                r["tiles"] = {k: ("/output/" + v if not v.startswith("/output/") else v)
                              for k, v in r["tiles"].items()}
        return jsonify({"results": result_list, "failed": failed_list, "max_demos": max_demos})
    return jsonify({"results": [], "failed": [], "max_demos": 10})


@app.route("/output/<path:filename>")
def serve_output(filename):
    return send_from_directory(config.OUTPUT_DIR, filename)


# ── Background runner ─────────────────────────────────────────────────────────

def _run_analysis_by_names(usernames, max_demos=10):
    try:
        results, failed = pipeline.run_by_usernames(usernames, max_demos=max_demos, progress_cb=_make_progress_cb_names())
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
        log.error(f"Analysis by names failed: {e}")
        import traceback
        traceback.print_exc()
        with state_lock:
            state["status"] = "error"
            state["message"] = str(e)


def _make_progress_cb_names():
    def progress_cb(opp_idx, total, username, step, msg):
        with state_lock:
            state["message"] = f"[{opp_idx+1}/{total}] {msg}"
            for p in state["progress"]:
                if p["id"] == username:
                    p["step"] = step
                    p["msg"] = msg
                    return
            state["progress"].append({"id": username, "step": step, "msg": msg})
    return progress_cb


def _run_analysis(my_uuid, steamids):
    try:
        results = pipeline.run(my_uuid, steamids, progress_cb=_make_progress_cb())
        with state_lock:
            state["status"] = "done"
            state["message"] = f"Analysis complete: {len(results)} opponents"
            state["results"] = [
                {
                    "steamid": r["steamid"],
                    "username": r["username"],
                    "heatmap": f"/output/heatmap_{r['steamid']}.png",
                    "demo_count": r["demo_count"],
                    "record_count": r["record_count"],
                    "round_count": r["round_count"],
                    "zone_stats": r["zone_stats"],
                }
                for r in results
            ]
    except Exception as e:
        log.error(f"Analysis failed: {e}")
        import traceback
        traceback.print_exc()
        with state_lock:
            state["status"] = "error"
            state["message"] = str(e)


def _run_auto_analysis(my_uuid):
    """Mode B: Auto-detect opponents via 5E API, then run analysis."""
    try:
        with state_lock:
            state["message"] = "Querying 5E API for current match opponents (may take up to 90s)..."

        log.info("Auto-detect mode: finding opponents from current match...")
        found = api_client.find_opponents_from_current_match(my_uuid)

        if not found:
            with state_lock:
                state["status"] = "error"
                state["message"] = "Could not detect opponents — are you in a 5E match right now?"
            return

        log.info(f"Auto-detected {len(found)} opponents, starting analysis...")
        with state_lock:
            state["message"] = f"Found {len(found)} opponents, starting analysis..."

        # Run analysis with the found opponents (UUIDs already known)
        results = pipeline.run_with_known_uuids(found, progress_cb=_make_progress_cb())

        with state_lock:
            state["status"] = "done"
            state["message"] = f"Analysis complete: {len(results)} opponents"
            state["results"] = [
                {
                    "steamid": r["steamid"],
                    "username": r["username"],
                    "heatmap": f"/output/heatmap_{r['steamid']}.png",
                    "demo_count": r["demo_count"],
                    "record_count": r["record_count"],
                    "round_count": r["round_count"],
                    "zone_stats": r["zone_stats"],
                }
                for r in results
            ]
    except Exception as e:
        log.error(f"Auto analysis failed: {e}")
        import traceback
        traceback.print_exc()
        with state_lock:
            state["status"] = "error"
            state["message"] = str(e)


def _make_progress_cb():
    def progress_cb(opp_idx, total, steamid, step, msg):
        with state_lock:
            state["message"] = f"[{opp_idx+1}/{total}] {msg}"
            found = False
            for p in state["progress"]:
                if p["steamid"] == steamid:
                    p["step"] = step
                    p["msg"] = msg
                    found = True
                    break
            if not found:
                state["progress"].append({
                    "steamid": steamid, "step": step, "msg": msg,
                })
    return progress_cb


if __name__ == "__main__":
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    print(f"CSAI Server: http://{config.HOST}:{config.PORT}")
    app.run(host=config.HOST, port=config.PORT, debug=False)
