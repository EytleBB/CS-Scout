import time
from types import SimpleNamespace

from perfectworld_experiment.web_server import AutoScoutService, create_app


def test_isolated_web_ui_starts_without_touching_watcher():
    app = create_app(start_watcher=False)
    client = app.test_client()

    index = client.get("/")
    assert index.status_code == 200
    assert "无需输入用户名" in index.get_data(as_text=True)

    status = client.get("/api/status")
    assert status.status_code == 200
    assert status.get_json()["platform"] == "perfectworld"
    assert status.get_json()["phase"] == "waiting"

    assert client.get("/core/replay.js").status_code == 200
    assert client.get("/api/player/not-safe").status_code == 404


def test_auto_scout_depth_can_change_only_between_analyses():
    service = AutoScoutService(max_demos=3)

    assert service.configure(max_demos=7) == {"max_demos": 7, "busy": False}
    assert service.snapshot()["max_demos"] == 7

    service._update(phase="analyzing")
    assert service.configure(max_demos=2) == {"max_demos": 7, "busy": True}
    assert service.snapshot()["max_demos"] == 7


def test_auto_scout_requires_confirmation_before_analysis(monkeypatch):
    monkeypatch.setattr(
        "perfectworld_experiment.native_signer.get_dll_status",
        lambda **_kwargs: {
            "ready": True, "code": "ready", "message": "ready",
            "path": "C:/client/plugin/PvpAlive.dll", "source": "test",
        },
    )
    service = AutoScoutService(max_demos=3)

    assert service.request_analysis() == {"accepted": False, "phase": "waiting"}

    service._update(phase="awaiting_confirmation")
    assert service.request_analysis() == {"accepted": True, "phase": "queued"}
    assert service.snapshot()["phase"] == "queued"
    assert service._analysis_requested.is_set()


def test_auto_scout_blocks_analysis_when_official_component_is_unavailable(
    monkeypatch
):
    monkeypatch.setattr(
        "perfectworld_experiment.native_signer.get_dll_status",
        lambda **_kwargs: {
            "ready": False, "code": "invalid_signature",
            "message": "完美平台组件签名无效", "path": None, "source": None,
        },
    )
    service = AutoScoutService(max_demos=3)
    service._update(phase="awaiting_confirmation")

    result = service.request_analysis()

    assert result["accepted"] is False
    assert result["phase"] == "setup_required"
    assert result["error"] == "完美平台组件签名无效"
    # A missing component blocks the analysis, but it must not erase the
    # detected match: log-based detection works without the component.
    assert service.snapshot()["phase"] == "awaiting_confirmation"
    assert service.snapshot()["signer"]["ready"] is False


def test_missing_component_keeps_detection_phase(monkeypatch):
    monkeypatch.setattr(
        "perfectworld_experiment.native_signer.get_dll_status",
        lambda **_kwargs: {
            "ready": False, "code": "not_found",
            "message": "未找到完美平台组件", "path": None, "source": None,
        },
    )
    service = AutoScoutService(max_demos=3)
    service._update(phase="awaiting_confirmation")

    signer = service.refresh_signer_status()

    assert signer["ready"] is False
    assert service.snapshot()["phase"] == "awaiting_confirmation"
    assert service.snapshot()["signer"]["message"] == "未找到完美平台组件"


def test_auto_scout_detects_matches_without_ready_component(monkeypatch):
    """Log-based detection must not wait for the official component."""
    monkeypatch.setattr(
        "perfectworld_experiment.native_signer.get_dll_status",
        lambda **_kwargs: {
            "ready": False, "code": "not_found",
            "message": "未找到完美平台组件", "path": None, "source": None,
        },
    )
    local = SimpleNamespace(current_match=SimpleNamespace(
        match_id="match-1", map_name="de_dust2", players=[object()],
    ))
    monkeypatch.setattr(
        "perfectworld_experiment.web_server.wait_for_current_match",
        lambda *_args, **_kwargs: local,
    )

    class FakeFollower:
        def __init__(self, *_args, **_kwargs):
            pass

        def poll(self):
            return False

        def snapshot(self):
            return local

        def diagnostics(self):
            return {"file_exists": False, "watching": None}

    monkeypatch.setattr(
        "perfectworld_experiment.web_server.LogFollower", FakeFollower,
    )
    monkeypatch.setattr(
        "perfectworld_experiment.web_server.prepare_auto_state",
        lambda *_args, **_kwargs: SimpleNamespace(
            players=[SimpleNamespace(nickname="Opponent")],
        ),
    )

    service = AutoScoutService(max_demos=3)
    service.start()
    try:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if service.snapshot()["phase"] == "awaiting_confirmation":
                break
            time.sleep(0.05)
        assert service.snapshot()["phase"] == "awaiting_confirmation"
        assert service.snapshot()["signer"]["ready"] is False
    finally:
        service.stop()


def test_auto_scout_cancel_keeps_task_in_cancelling_until_worker_stops():
    service = AutoScoutService(max_demos=3)
    service._update(
        phase="analyzing",
        targets=[{"username": "Opponent"}],
        map="de_mirage",
    )

    cancelled = service.cancel_analysis()

    assert cancelled == {"accepted": True, "phase": "cancelling"}
    assert service._analysis_cancel.is_set()
    snapshot = service.snapshot()
    assert snapshot["phase"] == "cancelling"
    assert snapshot["targets"] == [{"username": "Opponent"}]
    assert snapshot["map"] == "de_mirage"


def test_service_reports_log_directory_diagnostics(tmp_path):
    """The panel must be able to explain a missing client log directory."""
    service = AutoScoutService(max_demos=3, log_dir=tmp_path / "missing")
    service.start()
    try:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if service.snapshot()["log"]["watching"]:
                break
            time.sleep(0.05)
        log = service.snapshot()["log"]
        assert log["file_exists"] is False
        # The follower watches the nearest existing ancestor, so a log
        # directory created later is picked up without a restart.
        assert log["watching"] == str(tmp_path)
    finally:
        service.stop()
