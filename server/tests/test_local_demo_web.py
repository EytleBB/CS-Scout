import io
import json
import os
import sys
import copy
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import local_demo_pipeline
import web_server


@pytest.fixture(autouse=True)
def local_environment(monkeypatch, tmp_path):
    monkeypatch.setattr(web_server.config, "SECRET_KEY", "")
    monkeypatch.setattr(web_server.config, "LOCAL_MODE", True)
    monkeypatch.setattr(web_server.config, "HOST", "127.0.0.1")
    monkeypatch.setattr(web_server.config, "LOCAL_DEMO_DIR", str(tmp_path / "sessions"))
    monkeypatch.setattr(web_server.config, "OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr(web_server.maps, "available_maps", lambda: ["de_nuke"])
    previous = copy.deepcopy(web_server.state)
    with web_server.state_lock:
        web_server.state.clear()
        web_server.state.update({
            "status": "idle", "message": "", "progress": [],
            "results": [], "failed": [], "total_players": 0,
            "max_demos": 10, "map": "", "mode": "normal", "source": "5e",
        })
    yield
    with web_server.state_lock:
        web_server.state.clear()
        web_server.state.update(previous)


def _fake_inspect(paths):
    details = []
    for path in paths:
        path = Path(path)
        details.append({
            "path": str(path), "name": path.name, "size": path.stat().st_size,
            "map": "de_nuke", "rounds": 17,
            "players": {"76561198146001127": "L4n", "76561198000000001": "Other"},
        })
    return {
        "map": "de_nuke", "files": details,
        "players": [
            {"steamid": "76561198000000001", "username": "Other", "appearances": len(details)},
            {"steamid": "76561198146001127", "username": "L4n", "appearances": len(details)},
        ],
    }


def _upload(client, names=("one.dem", "two.dem")):
    data = {
        "demos": [(io.BytesIO(b"fake-demo"), name) for name in names],
    }
    return client.post("/api/local-demos/inspect", data=data, content_type="multipart/form-data")


def test_local_demo_routes_are_loopback_only():
    client = web_server.app.test_client()
    inspect = client.post(
        "/api/local-demos/inspect",
        data={"demos": (io.BytesIO(b"demo"), "one.dem")},
        content_type="multipart/form-data",
        environ_base={"REMOTE_ADDR": "203.0.113.10"},
    )
    analyze = client.post(
        "/api/local-demos/analyze",
        json={"session_id": "0" * 32, "steamid": "76561198146001127"},
        environ_base={"REMOTE_ADDR": "203.0.113.10"},
    )
    assert inspect.status_code == 404
    assert analyze.status_code == 404


def test_upload_validation_and_size_limits(monkeypatch):
    client = web_server.app.test_client()
    assert client.post("/api/local-demos/inspect").status_code == 400
    bad = client.post(
        "/api/local-demos/inspect",
        data={"demos": (io.BytesIO(b"zip"), "archive.zip")},
        content_type="multipart/form-data",
    )
    assert bad.status_code == 400

    monkeypatch.setattr(web_server.config, "LOCAL_DEMO_MAX_FILES", 1)
    too_many = _upload(client)
    assert too_many.status_code == 400

    monkeypatch.setattr(web_server.config, "LOCAL_DEMO_MAX_FILES", 10)
    monkeypatch.setattr(web_server.config, "LOCAL_DEMO_MAX_FILE_BYTES", 1)
    too_large = _upload(client, ("one.dem",))
    assert too_large.status_code == 400


def test_inspect_returns_map_files_and_common_players(monkeypatch):
    monkeypatch.setattr(local_demo_pipeline, "inspect_demos", _fake_inspect)
    response = _upload(web_server.app.test_client())
    body = response.get_json()
    assert response.status_code == 200
    assert body["map"] == "de_nuke"
    assert [item["name"] for item in body["files"]] == ["one.dem", "two.dem"]
    assert body["players"] == [
        {"steamid": "76561198000000001", "username": "Other", "appearances": 2},
        {"steamid": "76561198146001127", "username": "L4n", "appearances": 2},
    ]
    session = Path(web_server.config.LOCAL_DEMO_DIR) / body["session_id"]
    manifest = json.loads((session / "manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["files"]) == 2
    assert all(item["stored_name"].endswith(".dem") for item in manifest["files"])


def test_inspect_rejects_map_mismatch_and_no_common_player(monkeypatch):
    def mismatch(_paths):
        raise local_demo_pipeline.LocalDemoError("all Demos must use one map")

    monkeypatch.setattr(local_demo_pipeline, "inspect_demos", mismatch)
    response = _upload(web_server.app.test_client())
    assert response.status_code == 400
    assert "one map" in response.get_json()["error"]
    assert list(Path(web_server.config.LOCAL_DEMO_DIR).iterdir()) == []

    def no_players(_paths):
        raise local_demo_pipeline.LocalDemoError("no recognizable players")

    monkeypatch.setattr(local_demo_pipeline, "inspect_demos", no_players)
    response = _upload(web_server.app.test_client())
    assert response.status_code == 400
    assert "players" in response.get_json()["error"]


def test_analyze_runs_in_background_writes_replay_data_and_cleans_session(monkeypatch):
    monkeypatch.setattr(local_demo_pipeline, "inspect_demos", _fake_inspect)
    inspected = _upload(web_server.app.test_client(), ("a.dem", "b.dem"))
    session_id = inspected.get_json()["session_id"]

    launched = []
    class CapturedThread:
        def __init__(self, target, args=(), daemon=None):
            launched.append((target, args, daemon))
        def start(self):
            return None
    monkeypatch.setattr(web_server.threading, "Thread", CapturedThread)

    def fake_run(paths, *, steamid, username, domain, map_name, output_path, progress_cb=None):
        if progress_cb:
            progress_cb(0, len(paths), "parsed")
        payload = {
            "username": username, "steamid": steamid, "map": map_name,
            "rounds": [{"round_id": 1, "path": [[1, 2, 0]], "grenades": [{"type": "smoke"}], "death_t": 4}],
            "round_count": 1,
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload), encoding="utf-8")
        return {"combat_stats": {}, "total_rounds": 1}

    monkeypatch.setattr(local_demo_pipeline, "run_local_demos", fake_run)
    response = web_server.app.test_client().post(
        "/api/local-demos/analyze",
        json={"session_id": session_id},
    )
    assert response.status_code == 200
    assert response.get_json()["source"] == "local_demos"
    assert len(launched) == 1
    with web_server.state_lock:
        assert web_server.state["source"] == "local_demos"
        assert web_server.state["status"] == "running"
        assert web_server.state["total_players"] == 2

    target, args, _daemon = launched[0]
    target(*args)
    status = web_server.app.test_client().get("/api/status").get_json()
    assert status["status"] == "done"
    assert status["source"] == "local_demos"
    assert len(status["results"]) == 2
    for result in status["results"]:
        assert result["round_count"] == 1
        assert result["player_json"].startswith("/output/player_local_")
        domain = result["domain"]
        output = web_server.app.test_client().get(f"/api/player/{domain}")
        assert output.status_code == 200
        assert "grenades" in output.get_json()["rounds"][0]
        assert output.get_json()["rounds"][0]["death_t"] == 4
    assert not (Path(web_server.config.LOCAL_DEMO_DIR) / session_id).exists()


def test_analyze_rejects_invalid_session():
    client = web_server.app.test_client()
    assert client.post(
        "/api/local-demos/analyze",
        json={"session_id": "bad"},
    ).status_code == 400
    assert client.post(
        "/api/local-demos/analyze",
        json={"session_id": "0" * 32},
    ).status_code == 400


def test_analyze_with_steamids_filter(monkeypatch):
    monkeypatch.setattr(local_demo_pipeline, "inspect_demos", _fake_inspect)
    inspected = _upload(web_server.app.test_client(), ("a.dem", "b.dem"))
    session_id = inspected.get_json()["session_id"]

    launched = []
    class CapturedThread:
        def __init__(self, target, args=(), daemon=None):
            launched.append((target, args, daemon))
        def start(self):
            return None
    monkeypatch.setattr(web_server.threading, "Thread", CapturedThread)

    def fake_run(paths, *, steamid, username, domain, map_name, output_path, progress_cb=None):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps({"username": username, "steamid": steamid, "rounds": [], "round_count": 0}), encoding="utf-8")
        return {"combat_stats": {}, "total_rounds": 0}

    monkeypatch.setattr(local_demo_pipeline, "run_local_demos", fake_run)
    response = web_server.app.test_client().post(
        "/api/local-demos/analyze",
        json={"session_id": session_id, "steamids": ["76561198146001127"]},
    )
    assert response.status_code == 200
    target, args, _daemon = launched[0]
    target(*args)
    status = web_server.app.test_client().get("/api/status").get_json()
    assert len(status["results"]) == 1
    assert status["results"][0]["domain"] == "local_76561198146001127"

    bad = web_server.app.test_client().post(
        "/api/local-demos/analyze",
        json={"session_id": session_id, "steamids": ["123"]},
    )
    assert bad.status_code == 400
