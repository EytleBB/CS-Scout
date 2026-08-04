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


def test_auto_scout_requires_confirmation_before_analysis():
    service = AutoScoutService(max_demos=3)

    assert service.request_analysis() == {"accepted": False, "phase": "waiting"}

    service._update(phase="awaiting_confirmation")
    assert service.request_analysis() == {"accepted": True, "phase": "queued"}
    assert service.snapshot()["phase"] == "queued"
    assert service._analysis_requested.is_set()
