import copy
import json
import os, sys
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import web_server


@pytest.fixture(autouse=True)
def configured_analysis_secret(monkeypatch):
    monkeypatch.setattr(web_server.config, "SECRET_KEY", "test-secret")
    monkeypatch.setattr(web_server.config, "LOCAL_MODE", False)
    monkeypatch.setattr(web_server.config, "HOST", "127.0.0.1")
    with web_server.state_lock:
        previous_pwa_token = web_server._pwa_analysis_token
        previous_cancel_event = web_server._analysis_cancel_event
        previous_cancel_id = web_server._analysis_cancel_id
        web_server._pwa_analysis_token = None
        web_server._analysis_cancel_event = None
        web_server._analysis_cancel_id = None
    yield
    with web_server.state_lock:
        web_server._pwa_analysis_token = previous_pwa_token
        web_server._analysis_cancel_event = previous_cancel_event
        web_server._analysis_cancel_id = previous_cancel_id


@pytest.fixture
def isolated_web_state():
    with web_server.state_lock:
        previous_state = copy.deepcopy(web_server.state)
        web_server.state.clear()
        web_server.state.update({
            "status": "idle",
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
        })
    try:
        yield
    finally:
        with web_server.state_lock:
            web_server.state.clear()
            web_server.state.update(previous_state)

def test_status_shape():
    c = web_server.app.test_client()
    r = c.get("/api/status")
    assert r.status_code == 200
    assert "status" in r.get_json()
    assert r.headers["Cache-Control"] == "no-store"


def test_cancel_running_analysis_sets_cooperative_event(isolated_web_state):
    cancel_event = threading.Event()
    with web_server.state_lock:
        web_server.state.update({
            "status": "running",
            "platform": "fivee",
            "message": "working",
            "analysis_id": 41,
        })
        web_server._analysis_cancel_event = cancel_event
        web_server._analysis_cancel_id = 41

    response = web_server.app.test_client().post(
        "/api/cancel",
        json={},
        headers={"Authorization": f"Bearer {web_server.config.SECRET_KEY}"},
    )

    assert response.status_code == 202
    assert response.get_json() == {"status": "cancelling", "platform": "fivee"}
    assert response.headers["Cache-Control"] == "no-store"
    assert cancel_event.is_set()
    with web_server.state_lock:
        assert web_server.state["status"] == "cancelling"
        assert web_server.state["message"] == "正在取消分析…"


def test_cancel_requires_auth_and_rejects_when_idle(isolated_web_state):
    client = web_server.app.test_client()
    unauthorized = client.post("/api/cancel", json={})
    assert unauthorized.status_code == 401

    idle = client.post(
        "/api/cancel",
        json={},
        headers={"Authorization": f"Bearer {web_server.config.SECRET_KEY}"},
    )
    assert idle.status_code == 409
    assert idle.get_json()["error"] == "No analysis is running"


def test_background_runner_publishes_cancelled_state(monkeypatch, isolated_web_state):
    cancel_event = threading.Event()
    cancel_event.set()

    def cancelled_runner(usernames, map_name, **options):
        assert options["cancel_event"] is cancel_event
        raise web_server.pipeline.AnalysisCancelled("cancelled")

    monkeypatch.setattr(web_server.pipeline, "run", cancelled_runner)
    with web_server.state_lock:
        web_server.state.update({
            "status": "cancelling",
            "platform": "fivee",
            "analysis_id": 42,
        })
        web_server._analysis_cancel_event = cancel_event
        web_server._analysis_cancel_id = 42

    completed, message = web_server._run_analysis(
        ["Alpha"], "de_mirage", cancel_event=cancel_event, analysis_id=42
    )

    assert completed is False
    assert message == "分析已取消，可重新开始"
    with web_server.state_lock:
        assert web_server.state["status"] == "cancelled"
        assert web_server._analysis_cancel_event is None
        assert web_server._analysis_cancel_id is None


def test_unified_cancel_routes_to_perfectworld_service(
    monkeypatch, isolated_web_state
):
    monkeypatch.setattr(web_server.config, "LOCAL_MODE", True)

    class FakeService:
        def __init__(self):
            self.calls = 0

        def cancel_analysis(self):
            self.calls += 1
            return {"accepted": True, "phase": "cancelling"}

    service = FakeService()
    monkeypatch.setattr(web_server, "_pwa_service", service)
    with web_server.state_lock:
        web_server._pwa_analysis_token = 91

    response = web_server.app.test_client().post("/api/cancel", json={})

    assert response.status_code == 202
    assert response.get_json() == {
        "status": "cancelling", "platform": "perfectworld"
    }
    assert service.calls == 1


def test_public_routes_do_not_require_access_key(monkeypatch, tmp_path):
    monkeypatch.setattr(web_server.maps, "available_maps", lambda: ["de_mirage"])
    monkeypatch.setattr(web_server.config, "OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr(web_server.config, "DEMO_DIR", str(tmp_path / "demos"))
    c = web_server.app.test_client()

    assert c.get("/").status_code == 200
    assert c.get("/api/maps").status_code == 200
    assert c.get("/healthz").get_json() == {"status": "alive"}
    ready = c.get("/readyz")
    assert ready.status_code == 200
    assert ready.get_json()["status"] == "ready"


def test_result_routes_are_public_and_do_not_cache(monkeypatch, tmp_path):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    player_payload = {"username": "Alpha", "rounds": []}
    player_path = output_dir / "player_safe-domain.json"
    player_path.write_text(json.dumps(player_payload), encoding="utf-8")
    (output_dir / "analysis_summary.json").write_text(json.dumps({
        "results": [{"username": "Alpha", "domain": "safe-domain"}],
        "failed": [], "map": "de_mirage", "max_demos": 2, "mode": "normal",
    }), encoding="utf-8")
    monkeypatch.setattr(web_server.config, "OUTPUT_DIR", str(output_dir))
    c = web_server.app.test_client()

    for path in ("/api/status", "/api/results", "/api/player/safe-domain",
                 "/output/player_safe-domain.json"):
        response = c.get(path)
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["Pragma"] == "no-cache"


def test_status_restores_latest_saved_results_after_restart(
    monkeypatch, tmp_path, isolated_web_state
):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    saved_result = {"username": "Alpha", "domain": "safe-domain"}
    (output_dir / "analysis_summary.json").write_text(json.dumps({
        "results": [saved_result], "failed": [], "map": "de_mirage",
        "max_demos": 4, "mode": "fast",
    }), encoding="utf-8")
    monkeypatch.setattr(web_server.config, "OUTPUT_DIR", str(output_dir))

    response = web_server.app.test_client().get("/api/status")
    body = response.get_json()

    assert response.status_code == 200
    assert body["status"] == "done"
    assert body["message"] == "已加载最近一次分析结果"
    assert body["results"] == [saved_result]
    assert body["map"] == "de_mirage"
    assert body["max_demos"] == 4
    assert body["mode"] == "fast"


def test_authorization_header_takes_precedence_over_legacy_body_key(monkeypatch):
    monkeypatch.setattr(web_server.maps, "available_maps", lambda: ["de_mirage"])
    c = web_server.app.test_client()
    response = c.post("/api/analyze", json={
        "usernames": ["Alpha"],
        "map": "de_mirage",
        "key": web_server.config.SECRET_KEY,
    }, headers={"Authorization": "Bearer wrong"})

    assert response.status_code == 403
    assert response.headers["Cache-Control"] == "no-store"


def test_health_and_readiness_are_generic_and_self_cleaning(monkeypatch, tmp_path):
    output_dir = tmp_path / "new-output"
    demo_dir = tmp_path / "new-demos"
    monkeypatch.setattr(web_server.config, "OUTPUT_DIR", str(output_dir))
    monkeypatch.setattr(web_server.config, "DEMO_DIR", str(demo_dir))
    monkeypatch.setattr(web_server.maps, "available_maps", lambda: ["de_mirage"])
    c = web_server.app.test_client()

    health = c.get("/healthz")
    assert health.status_code == 200
    assert health.get_json() == {"status": "alive"}

    ready = c.get("/readyz")
    assert ready.status_code == 200
    assert ready.get_json() == {
        "status": "ready",
        "checks": {
            "secret_configured": True,
            "maps_available": True,
            "output_writable": True,
            "demo_cache_writable": True,
        },
    }
    assert ready.headers["Cache-Control"] == "no-store"
    assert list(output_dir.iterdir()) == []
    assert list(demo_dir.iterdir()) == []


def test_local_mode_is_ready_without_a_server_secret(monkeypatch, tmp_path):
    monkeypatch.setattr(web_server.config, "SECRET_KEY", "")
    monkeypatch.setattr(web_server.config, "LOCAL_MODE", True)
    monkeypatch.setattr(web_server.config, "HOST", "127.0.0.1")
    monkeypatch.setattr(web_server.config, "OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr(web_server.config, "DEMO_DIR", str(tmp_path / "demos"))
    monkeypatch.setattr(web_server.maps, "available_maps", lambda: ["de_mirage"])

    response = web_server.app.test_client().get("/readyz")

    assert response.status_code == 200
    assert response.get_json()["checks"]["secret_configured"] is True


def test_readiness_failure_does_not_expose_paths_or_errors(monkeypatch, tmp_path):
    monkeypatch.setattr(web_server.config, "SECRET_KEY", "")
    monkeypatch.setattr(web_server.maps, "available_maps", lambda: [])
    monkeypatch.setattr(web_server, "_directory_is_writable", lambda _path: False)
    c = web_server.app.test_client()

    response = c.get("/readyz")
    body = response.get_json()
    assert response.status_code == 503
    assert body["status"] == "not_ready"
    assert set(body) == {"status", "checks"}
    assert set(body["checks"]) == {
        "secret_configured", "maps_available", "output_writable",
        "demo_cache_writable",
    }
    assert not any(body["checks"].values())
    assert str(tmp_path) not in response.get_data(as_text=True)

def test_analyze_requires_map():
    c = web_server.app.test_client()
    r = c.post("/api/analyze", json={
        "usernames": ["x"], "key": web_server.config.SECRET_KEY
    })
    assert r.status_code == 400          # missing map
    body = r.get_json()
    assert "map" in body["error"].lower()

def test_analyze_bad_key():
    c = web_server.app.test_client()
    r = c.post("/api/analyze", json={"usernames":["x"],"map":"de_mirage","key":"wrong"})
    assert r.status_code == 403


def test_analyze_requires_access_key():
    c = web_server.app.test_client()
    response = c.post("/api/analyze", json={
        "usernames": ["x"], "map": "de_mirage",
    })
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == 'Bearer realm="CS-Scout"'


def test_loopback_local_mode_allows_keyless_analysis(monkeypatch, isolated_web_state):
    monkeypatch.setattr(web_server.config, "LOCAL_MODE", True)
    monkeypatch.setattr(web_server.maps, "available_maps", lambda: ["de_mirage"])
    launched = []

    class CapturedThread:
        def __init__(self, target, args=(), daemon=None):
            launched.append((target, args, daemon))

        def start(self):
            return None

    monkeypatch.setattr(web_server.threading, "Thread", CapturedThread)
    response = web_server.app.test_client().post("/api/analyze", json={
        "usernames": ["Alpha"], "map": "de_mirage", "max_demos": 1,
    })

    assert response.status_code == 200
    assert len(launched) == 1


def test_local_mode_never_bypasses_key_for_non_loopback_request(monkeypatch):
    monkeypatch.setattr(web_server.config, "LOCAL_MODE", True)
    response = web_server.app.test_client().post(
        "/api/analyze",
        json={"usernames": ["Alpha"], "map": "de_mirage"},
        environ_base={"REMOTE_ADDR": "203.0.113.10"},
    )
    assert response.status_code == 401


def test_local_index_hides_key_field_and_hosted_index_has_blank_field(monkeypatch):
    client = web_server.app.test_client()
    hosted_html = client.get("/").get_data(as_text=True)
    assert 'id="key"' in hosted_html
    assert 'id="key" type="password"' in hosted_html
    assert 'placeholder=' not in hosted_html[hosted_html.index('id="key"'):hosted_html.index('id="key"') + 180]
    assert 'data-local-analysis="false"' in hosted_html

    monkeypatch.setattr(web_server.config, "LOCAL_MODE", True)
    local_html = client.get("/").get_data(as_text=True)
    assert 'id="key"' not in local_html
    assert 'data-local-analysis="true"' in local_html

    monkeypatch.setattr(web_server.config, "HOST", "0.0.0.0")
    unsafe_html = client.get("/").get_data(as_text=True)
    assert 'id="key"' in unsafe_html
    assert 'data-local-analysis="false"' in unsafe_html


def test_index_contains_unified_replay_layout():
    c = web_server.app.test_client()
    r = c.get("/")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'id="speed-1"' in html
    assert 'id="speed-2"' in html
    assert 'id="speed-4"' in html
    assert 'data-playback-speed="2" class="active" aria-pressed="true"' in html
    assert 'id="side-ct"' in html
    assert 'id="side-t"' in html
    assert 'id="playpause"' in html
    assert 'id="scrub"' in html
    assert 'id="view-switcher"' in html
    assert 'id="view-toolbar"' in html
    assert 'role="group" aria-label="回放视图选择"' in html
    assert 'id="pistol"' in html
    assert 'id="pistol-canvas"' in html
    assert 'class="replay-canvas"' in html
    assert 'id="cards"' in html
    assert 'id="mode-normal"' in html
    assert 'id="mode-fast"' in html
    normal_start = html.index('<button id="mode-normal"')
    normal_tag = html[normal_start:html.index(">", normal_start)]
    assert 'data-analysis-mode="normal"' in normal_tag
    assert 'class="active"' in normal_tag
    assert 'aria-pressed="true"' in normal_tag
    assert html.index('id="mode-normal"') < html.index('id="run"')
    assert "max-height: min(72dvh, calc(100dvh - var(--header-height) - 190px), 760px)" in html
    assert "<h2>扫描设置</h2>" not in html
    assert "<h1>对手回放分析</h1>" not in html
    assert "20 秒战术窗口" not in html
    assert "<h2>合并手枪局</h2>" not in html
    assert "0.0 / 20.0s" in html
    header = html[html.index("<header"):html.index("</header>")]
    assert 'id="speed-2"' in header
    assert 'id="side-ct"' not in header
    assert html.index('id="view-switcher"') < html.index('id="side-ct"') < html.index('id="pistol"')


def test_index_places_platform_switch_above_shared_map_controls(monkeypatch):
    monkeypatch.setattr(web_server.config, "LOCAL_MODE", True)
    html = web_server.app.test_client().get("/").get_data(as_text=True)

    assert '<div class="brand-copy">CS-Scout</div>' in html
    assert "Replay intelligence" not in html
    assert '<span class="platform-caption">平台</span>' in html
    assert 'id="platform-5e"' in html
    assert 'id="platform-perfectworld"' in html
    assert 'id="scout-auto"' in html
    assert 'id="scout-manual"' in html
    assert 'data-scout-mode="auto" class="active" aria-pressed="true"' in html
    assert html.index('id="platform-5e"') < html.index('id="scout-auto"')
    assert html.index('id="scout-auto"') < html.index('id="map"')
    assert 'id="pwa-component"' in html
    assert 'id="pwa-select-directory"' in html
    assert 'data-platform="5e" class="active" aria-pressed="true"' in html
    assert html.index('id="platform-5e"') < html.index('id="map"')
    assert 'id="player-input-label"' in html
    assert 'id="platform-player-list"' in html
    assert 'id="platform-actions"' in html
    assert 'data-five-e-only' in html
    assert 'id="pwa-hint"' not in html
    assert '<label id="player-input-label" for="u0">对手</label>' in html
    assert '<button id="run" type="button">开始分析</button>' in html
    assert '<strong id="empty-title">等待分析</strong>' in html
    assert 'id="empty-description"' not in html
    assert 'class="sidebar-scroll"' in html
    assert 'id="progress-panel"' in html
    assert 'id="progress-track"' in html
    assert 'id="progress-fill"' in html
    assert 'id="failure-details"' in html


def test_hosted_index_hides_desktop_only_perfectworld_switch(monkeypatch):
    monkeypatch.setattr(web_server.config, "LOCAL_MODE", False)
    html = web_server.app.test_client().get("/").get_data(as_text=True)

    assert 'id="platform-5e"' in html
    assert 'id="platform-perfectworld"' not in html


def test_desktop_platform_routes_are_disabled_outside_local_mode(monkeypatch):
    monkeypatch.setattr(web_server.config, "LOCAL_MODE", False)

    assert web_server.app.test_client().get("/api/pwa/status").status_code == 404
    assert web_server.app.test_client().get("/api/5e/status").status_code == 404
    assert web_server.app.test_client().post(
        "/api/pwa/manual/analyze", json={}
    ).status_code == 404


def test_manual_pwa_analysis_starts_shared_cancellable_job(
    monkeypatch, isolated_web_state
):
    monkeypatch.setattr(web_server.config, "LOCAL_MODE", True)
    monkeypatch.setattr(web_server.maps, "available_maps", lambda: ["de_mirage"])

    class FakeService:
        def snapshot(self):
            return {"signer": {"ready": True}}

    started = []

    class FakeThread:
        def __init__(self, *, target, args, daemon):
            started.append((target, args, daemon))

        def start(self):
            return None

    monkeypatch.setattr(web_server, "_pwa_service", FakeService())
    monkeypatch.setattr(web_server.threading, "Thread", FakeThread)

    response = web_server.app.test_client().post(
        "/api/pwa/manual/analyze",
        json={
            "usernames": ["Alpha", "Bravo"],
            "map": "de_mirage",
            "max_demos": 4,
        },
    )

    assert response.status_code == 200
    assert response.get_json() == {"status": "started", "count": 2}
    assert started[0][0] is web_server._run_pwa_manual_analysis
    assert started[0][1][:3] == (["Alpha", "Bravo"], "de_mirage", 4)
    with web_server.state_lock:
        assert web_server.state["status"] == "running"
        assert web_server.state["platform"] == "perfectworld"
        assert web_server.state["total_players"] == 2
        assert web_server._analysis_cancel_event is started[0][1][3]


def test_manual_pwa_analysis_requires_ready_component(
    monkeypatch, isolated_web_state
):
    monkeypatch.setattr(web_server.config, "LOCAL_MODE", True)
    monkeypatch.setattr(web_server.maps, "available_maps", lambda: ["de_mirage"])

    class FakeService:
        def snapshot(self):
            return {"signer": {"ready": False}}

    monkeypatch.setattr(web_server, "_pwa_service", FakeService())
    response = web_server.app.test_client().post(
        "/api/pwa/manual/analyze",
        json={"usernames": ["Alpha"], "map": "de_mirage", "max_demos": 2},
    )

    assert response.status_code == 409
    assert response.get_json()["error"] == "Perfect World component is not ready"


def test_pwa_status_includes_manual_analysis_results(
    monkeypatch, isolated_web_state
):
    monkeypatch.setattr(web_server.config, "LOCAL_MODE", True)

    class FakeService:
        def snapshot(self):
            return {
                "platform": "perfectworld",
                "phase": "waiting",
                "signer": {"ready": True},
            }

    monkeypatch.setattr(web_server, "_pwa_service", FakeService())
    with web_server.state_lock:
        web_server.state.update({
            "status": "done",
            "platform": "perfectworld",
            "message": "done",
            "results": [{"username": "Alpha", "domain": "pwa_76561198000000001"}],
            "failed": [],
        })

    body = web_server.app.test_client().get("/api/pwa/status").get_json()

    assert body["analysis"]["status"] == "done"
    assert body["analysis"]["results"][0]["username"] == "Alpha"


def test_manual_pwa_runner_publishes_results_and_can_run_again(
    monkeypatch, isolated_web_state
):
    monkeypatch.setattr(
        web_server,
        "_execute_pwa_manual_analysis",
        lambda usernames, map_name, max_demos, **options: {
            "results": [{"username": usernames[0], "domain": "pwa_76561198000000001"}],
            "failed": [],
        },
    )
    cancel_event = threading.Event()
    with web_server.state_lock:
        web_server.state.update({
            "status": "running",
            "platform": "perfectworld",
            "analysis_id": 73,
        })
        web_server._analysis_cancel_event = cancel_event
        web_server._analysis_cancel_id = 73

    assert web_server._run_pwa_manual_analysis(
        ["Alpha"], "de_mirage", 2, cancel_event, 73
    ) is True

    with web_server.state_lock:
        assert web_server.state["status"] == "done"
        assert web_server.state["results"][0]["username"] == "Alpha"
        assert web_server._analysis_cancel_event is None


def test_manual_pwa_identity_failure_does_not_discard_other_players(monkeypatch):
    def resolve(username):
        if username == "Missing":
            raise LookupError("not found")
        return {
            "username": username,
            "steamid": "76561198000000001",
            "domain": "alpha-domain",
        }

    monkeypatch.setattr(web_server.api_client, "resolve_player_identity", resolve)
    players, failed = web_server._resolve_pwa_manual_players(
        ["Alpha", "Missing"], SimpleNamespace(current_match=None)
    )

    assert [player.nickname for player in players] == ["Alpha"]
    assert failed == [{
        "username": "Missing",
        "reason": "未找到玩家或无法解析 SteamID",
    }]


def test_perfectworld_routes_share_the_main_app_and_stay_loopback_only(
    monkeypatch, tmp_path, isolated_web_state
):
    monkeypatch.setattr(web_server.config, "LOCAL_MODE", True)
    monkeypatch.setattr(web_server.config, "HOST", "127.0.0.1")
    output_dir = tmp_path / "pwa-output"
    output_dir.mkdir()
    domain = "pwa_76561198000000000"
    payload = {"username": "Opponent", "rounds": []}
    (output_dir / f"player_{domain}.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )

    class FakeService:
        def __init__(self):
            self.started = 0
            self.max_demos = 6
            self.directory_selections = 0

        def start(self):
            self.started += 1

        def snapshot(self):
            return {
                "platform": "perfectworld", "phase": "waiting",
                "message": "waiting", "results": [], "failed": [],
            }

        def configure(self, *, max_demos):
            self.max_demos = max_demos
            return {"max_demos": max_demos, "busy": False}

        def request_analysis(self):
            return {"accepted": True, "phase": "queued"}

        def choose_install_directory(self):
            self.directory_selections += 1
            return {
                "selected": True,
                "cancelled": False,
                "signer": {
                    "ready": True,
                    "code": "ready",
                    "message": "完美平台组件已就绪",
                    "path": "C:/Perfect/plugin/PvpAlive.dll",
                    "source": "selected",
                },
            }

    service = FakeService()
    monkeypatch.setattr(web_server, "_pwa_service", service)
    monkeypatch.setattr(web_server, "_pwa_output_dir", str(output_dir))
    client = web_server.app.test_client()

    configured = client.post("/api/pwa/config", json={"max_demos": 4})
    assert configured.status_code == 200
    assert configured.get_json() == {"max_demos": 4, "busy": False}
    assert service.max_demos == 4

    status = client.get("/api/pwa/status")
    assert status.status_code == 200
    assert status.get_json()["platform"] == "perfectworld"
    assert status.headers["Cache-Control"] == "no-store"

    assert client.post("/api/pwa/dll/select").status_code == 403
    selected = client.post(
        "/api/pwa/dll/select", headers={"X-CS-Scout-Request": "1"}
    )
    assert selected.status_code == 200
    assert selected.get_json()["signer"]["code"] == "ready"
    assert service.directory_selections == 1
    assert selected.headers["Cache-Control"] == "no-store"

    with web_server.state_lock:
        web_server.state.update({
            "status": "done",
            "platform": "perfectworld",
            "results": [{"username": "Old manual result"}],
        })
    analyze = client.post("/api/pwa/analyze")
    assert analyze.status_code == 200
    assert analyze.get_json() == {"accepted": True, "phase": "queued"}
    with web_server.state_lock:
        assert web_server.state["status"] == "idle"
        assert web_server.state["results"] == []

    player = client.get(f"/api/pwa/player/{domain}")
    assert player.status_code == 200
    assert player.get_json() == payload
    assert player.headers["Cache-Control"] == "no-store"

    remote = client.get(
        "/api/pwa/status", environ_base={"REMOTE_ADDR": "203.0.113.10"}
    )
    assert remote.status_code == 404


def test_fivee_routes_detect_confirm_and_start_shared_pipeline(
    monkeypatch, isolated_web_state
):
    monkeypatch.setattr(web_server.config, "LOCAL_MODE", True)
    monkeypatch.setattr(web_server.config, "HOST", "127.0.0.1")
    monkeypatch.setattr(web_server.maps, "available_maps", lambda: ["de_mirage"])
    launched = []

    class CapturedThread:
        def __init__(self, target, args=(), daemon=None):
            self.target = target
            self.args = args

        def start(self):
            launched.append((self.target, self.args))

    class FakeService:
        def __init__(self):
            self.started = 0
            self.phase = "awaiting_confirmation"
            self.analysis_started = 0
            self.executable_selections = 0

        def start(self):
            self.started += 1

        def configure(self, *, max_demos, mode):
            return {"max_demos": max_demos, "mode": mode, "busy": False}

        def choose_executable(self):
            self.executable_selections += 1
            return {
                "selected": True,
                "cancelled": False,
                "executable": {
                    "ready": True,
                    "path": r"D:\Games\5E\5EClient.exe",
                },
            }

        def snapshot(self):
            return {
                "platform": "fivee",
                "phase": self.phase,
                "message": "ready",
                "analysis_id": getattr(self, "analysis_id", None),
                "targets": [{"username": f"Opponent {index}"} for index in range(5)],
            }

        def select_own_team(self, team):
            self.phase = "awaiting_confirmation"
            return {"accepted": team == "t1", "phase": self.phase}

        def analysis_payload(self, *, map_override=""):
            targets = [{
                "username": f"Opponent {index}",
                "domain": f"opponent-{index}",
                "steamid": f"7656119800000{index:04d}",
            } for index in range(5)]
            return {
                "usernames": [target["username"] for target in targets],
                "player_hints": targets,
                "map": map_override or "de_mirage",
                "max_demos": 4,
                "mode": "fast",
            }

        def mark_analysis_started(self, analysis_id=None):
            self.analysis_started += 1
            self.analysis_id = analysis_id
            self.phase = "analyzing"

        def mark_analysis_start_failed(self):
            self.phase = "awaiting_confirmation"

    service = FakeService()
    monkeypatch.setattr(web_server, "_fivee_service", service)
    monkeypatch.setattr(web_server, "threading", SimpleNamespace(Thread=CapturedThread))
    client = web_server.app.test_client()

    configured = client.post("/api/5e/config", json={
        "max_demos": 4, "mode": "fast",
    })
    assert configured.status_code == 200
    assert configured.get_json() == {"max_demos": 4, "mode": "fast", "busy": False}

    status = client.get("/api/5e/status")
    assert status.status_code == 200
    assert status.get_json()["platform"] == "fivee"
    assert status.headers["Cache-Control"] == "no-store"

    missing_marker = client.post("/api/5e/exe/select")
    assert missing_marker.status_code == 403
    executable = client.post(
        "/api/5e/exe/select", headers={"X-CS-Scout-Request": "1"}
    )
    assert executable.status_code == 200
    assert executable.get_json()["selected"] is True
    assert service.executable_selections == 1

    selected = client.post("/api/5e/team", json={"team": "t1"})
    assert selected.status_code == 200

    analyze = client.post("/api/5e/analyze", json={"map": "de_mirage"})
    assert analyze.status_code == 200
    assert analyze.get_json() == {"status": "started", "count": 5, "mode": "fast"}
    assert service.analysis_started == 1
    assert len(launched) == 1
    assert launched[0][0] is web_server._run_fivee_analysis
    assert launched[0][1][4][0]["domain"] == "opponent-0"
    with web_server.state_lock:
        assert web_server.state["status"] == "running"
        assert web_server.state["map"] == "de_mirage"

    remote = client.get(
        "/api/5e/status", environ_base={"REMOTE_ADDR": "203.0.113.10"}
    )
    assert remote.status_code == 404


def test_fivee_status_is_read_only_and_merges_same_run_results(
    monkeypatch, isolated_web_state
):
    monkeypatch.setattr(web_server.config, "LOCAL_MODE", True)

    class FakeService:
        def __init__(self):
            self.started = 0

        def start(self):
            self.started += 1

        def snapshot(self):
            return {
                "platform": "fivee", "phase": "ready", "message": "ready",
                "analysis_id": 12, "targets": [], "team_options": [],
            }

    service = FakeService()
    monkeypatch.setattr(web_server, "_fivee_service", service)
    with web_server.state_lock:
        web_server.state.update({
            "status": "done", "platform": "fivee", "analysis_id": 12,
            "message": "done", "results": [{"domain": "exact-domain"}],
            "failed": [], "progress": [],
        })

    body = web_server.app.test_client().get("/api/5e/status").get_json()

    assert service.started == 0
    assert body["analysis"]["status"] == "done"
    assert body["analysis"]["results"] == [{"domain": "exact-domain"}]


def test_fivee_status_keeps_completed_manual_results_without_service_run_id(
    monkeypatch, isolated_web_state
):
    monkeypatch.setattr(web_server.config, "LOCAL_MODE", True)

    class FakeService:
        def snapshot(self):
            return {
                "platform": "fivee", "phase": "waiting", "message": "waiting",
                "analysis_id": None, "targets": [], "team_options": [],
            }

    monkeypatch.setattr(web_server, "_fivee_service", FakeService())
    with web_server.state_lock:
        web_server.state.update({
            "status": "done", "platform": "fivee", "analysis_id": 99,
            "message": "done", "results": [
                {"domain": "first"}, {"domain": "last-finished"}
            ],
            "failed": [], "progress": [],
        })

    body = web_server.app.test_client().get("/api/5e/status").get_json()

    assert [item["domain"] for item in body["analysis"]["results"]] == [
        "first", "last-finished",
    ]


def test_perfectworld_and_fivee_analysis_are_mutually_exclusive(
    monkeypatch, isolated_web_state
):
    monkeypatch.setattr(web_server.config, "LOCAL_MODE", True)
    monkeypatch.setattr(web_server.maps, "available_maps", lambda: ["de_mirage"])

    class FakePwaService:
        def request_analysis(self):
            raise AssertionError("busy coordinator should reject before queueing")

    monkeypatch.setattr(web_server, "_pwa_service", FakePwaService())
    with web_server.state_lock:
        web_server.state["status"] = "running"

    client = web_server.app.test_client()
    assert client.post("/api/pwa/analyze").status_code == 409

    with web_server.state_lock:
        web_server.state["status"] = "idle"
        web_server._pwa_analysis_token = 99
    response = client.post("/api/analyze", json={
        "usernames": ["Alpha"], "map": "de_mirage", "max_demos": 1,
    })
    assert response.status_code == 409


def test_frontend_registers_button_switched_replay_views():
    c = web_server.app.test_client()
    response = c.get("/static/app.js")
    assert response.status_code == 200
    source = response.get_data(as_text=True)
    assert 'registerReplayView("pistol", "手枪局（全员）"' in source
    assert 'registerReplayView(`buy:${domain}`, username, card, buyPlayer, color, `${username} 购买局`)' in source
    assert 'button.setAttribute("aria-pressed", String(active))' in source
    assert 'const activeView = replayViews.get(activeViewKey)' in source
    assert 'clock = { elapsed: 0, playing: true, speed: 2' in source
    assert 'requestProtectedJSON("/api/analyze"' in source
    assert 'requestJSON("/api/status")' in source
    assert 'requestJSON(`/api/player/${encodeURIComponent(domain)}`' in source
    assert 'requestJSON("/api/pwa/status")' in source
    assert 'requestJSON(`/api/pwa/player/${encodeURIComponent(domain)}`' in source
    assert 'requestJSON("/api/pwa/config"' in source
    assert 'requestJSON("/api/pwa/analyze"' in source
    assert 'requestJSON("/api/pwa/dll/select"' in source
    assert 'automaticMode && pwaSignerReady &&' in source
    assert 'requestJSON("/api/pwa/manual/analyze"' in source
    assert 'else if (publicMonitoringEnabled) schedulePoll(epoch, 5000)' in source
    assert 'void poll(pollEpoch)' in source


def test_analyze_mode_defaults_to_normal_and_accepts_fast(monkeypatch):
    launched = []

    class CapturedThread:
        def __init__(self, target, args=(), daemon=None):
            self.target = target
            self.args = args

        def start(self):
            launched.append(self.args)

    monkeypatch.setattr(web_server, "threading", SimpleNamespace(Thread=CapturedThread))
    monkeypatch.setattr(web_server.maps, "available_maps", lambda: ["de_mirage"])
    with web_server.state_lock:
        previous_state = copy.deepcopy(web_server.state)
    try:
        client = web_server.app.test_client()
        normal = client.post("/api/analyze", json={
            "usernames": ["Alpha"], "map": "de_mirage",
            "max_demos": 2, "key": web_server.config.SECRET_KEY,
        })
        assert normal.status_code == 200
        assert normal.get_json()["mode"] == "normal"
        assert launched[-1][-1] == "normal"
        with web_server.state_lock:
            assert web_server.state["mode"] == "normal"
            web_server.state["status"] = "idle"

        fast = client.post("/api/analyze", json={
            "usernames": ["Bravo"], "map": "de_mirage", "mode": "fast",
            "max_demos": 2,
        }, headers={"Authorization": f"Bearer {web_server.config.SECRET_KEY}"})
        assert fast.status_code == 200
        assert fast.get_json()["mode"] == "fast"
        assert launched[-1][-1] == "fast"
        with web_server.state_lock:
            assert web_server.state["mode"] == "fast"
    finally:
        with web_server.state_lock:
            web_server.state.clear()
            web_server.state.update(previous_state)


def test_analyze_rejects_invalid_mode(monkeypatch):
    monkeypatch.setattr(web_server.maps, "available_maps", lambda: ["de_mirage"])
    client = web_server.app.test_client()
    base = {
        "usernames": ["Alpha"], "map": "de_mirage",
        "max_demos": 1, "key": web_server.config.SECRET_KEY,
    }
    for invalid_mode in ("turbo", "", 1, True, None):
        response = client.post(
            "/api/analyze", json={**base, "mode": invalid_mode}
        )
        assert response.status_code == 400
        assert "mode" in response.get_json()["error"]


def test_concurrent_analyze_requests_start_only_one_job(
    monkeypatch, isolated_web_state
):
    request_barrier = threading.Barrier(2)
    map_barrier = threading.Barrier(2)
    started = []
    started_lock = threading.Lock()

    class CapturedThread:
        def __init__(self, target, args=(), daemon=None):
            self.target = target
            self.args = args

        def start(self):
            with started_lock:
                started.append((self.target, self.args))

    def available_maps():
        map_barrier.wait(timeout=5)
        return ["de_mirage"]

    monkeypatch.setattr(web_server, "threading", SimpleNamespace(Thread=CapturedThread))
    monkeypatch.setattr(web_server.maps, "available_maps", available_maps)

    def post_analyze(username):
        request_barrier.wait(timeout=5)
        with web_server.app.test_client() as client:
            response = client.post("/api/analyze", json={
                "usernames": [username],
                "map": "de_mirage",
                "max_demos": 1,
                "key": web_server.config.SECRET_KEY,
            })
            return response.status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(post_analyze, "Alpha"),
            executor.submit(post_analyze, "Bravo"),
        ]
        statuses = [future.result(timeout=5) for future in futures]

    assert sorted(statuses) == [200, 409]
    assert len(started) == 1
    with web_server.state_lock:
        assert web_server.state["status"] == "running"


def test_analyze_thread_start_failure_restores_api_state(
    monkeypatch, isolated_web_state
):
    class FailingThread:
        def __init__(self, target, args=(), daemon=None):
            self.target = target
            self.args = args

        def start(self):
            raise RuntimeError("worker unavailable")

    monkeypatch.setattr(web_server, "threading", SimpleNamespace(Thread=FailingThread))
    monkeypatch.setattr(web_server.maps, "available_maps", lambda: ["de_mirage"])

    client = web_server.app.test_client()
    response = client.post("/api/analyze", json={
        "usernames": ["Alpha"],
        "map": "de_mirage",
        "mode": "fast",
        "max_demos": 1,
        "key": web_server.config.SECRET_KEY,
    })

    assert response.status_code == 503
    assert response.is_json
    assert response.get_json().get("error")
    with web_server.state_lock:
        assert web_server.state["status"] == "error"
        assert web_server.state["message"]


def test_run_analysis_dispatches_fast_pipeline(monkeypatch):
    called = []

    def fake_fast(usernames, map_name, max_demos, progress_cb, result_cb):
        called.append((usernames, map_name, max_demos))
        progress_cb(0, 1, "Alpha input", 2, "解析 Steam ID...")
        progress_cb(0, 1, "Alpha", 4, "解析 demo 1/2...")
        return [], []

    monkeypatch.setattr(web_server.pipeline, "run_fast", fake_fast)
    with web_server.state_lock:
        previous_state = copy.deepcopy(web_server.state)
        web_server.state.update({"status": "running", "results": [], "failed": []})
    try:
        web_server._run_analysis(["Alpha"], "de_mirage", max_demos=2, mode="fast")
        assert called == [(["Alpha"], "de_mirage", 2)]
        with web_server.state_lock:
            assert web_server.state["status"] == "done"
            assert web_server.state["message"].startswith("快速分析完成")
            progress = web_server.state["progress"]
            assert len(progress) == 1
            assert progress[0]["id"] == "Alpha"
            assert progress[0]["step"] == 4
            assert progress[0]["msg"] == "解析 demo 1/2..."
            assert progress[0]["index"] == 0
            assert progress[0]["total"] == 1
            assert isinstance(progress[0]["updated_at"], float)
    finally:
        with web_server.state_lock:
            web_server.state.clear()
            web_server.state.update(previous_state)


def test_run_analysis_does_not_publish_internal_exception_details(monkeypatch):
    internal_detail = "private-path /var/lib/cs-scout/demos/example.dem"

    def failing_run(*args, **kwargs):
        raise RuntimeError(internal_detail)

    monkeypatch.setattr(web_server.pipeline, "run", failing_run)
    with web_server.state_lock:
        previous_state = copy.deepcopy(web_server.state)
        web_server.state.update({"status": "running", "results": [], "failed": []})
    try:
        web_server._run_analysis(["Alpha"], "de_mirage", max_demos=1)
        with web_server.state_lock:
            assert web_server.state["status"] == "error"
            assert internal_detail not in web_server.state["message"]
            assert web_server.state["message"] == "分析失败，请查看服务器日志"
    finally:
        with web_server.state_lock:
            web_server.state.clear()
            web_server.state.update(previous_state)

def test_grenade_icon_route_is_allowlisted():
    c = web_server.app.test_client()
    expected = {
        "smokegrenade.svg", "flashbang.svg", "hegrenade.svg",
        "incgrenade.svg", "molotov_bottle.svg", "inferno.svg", "map_smoke.svg",
    }
    for filename in expected:
        r = c.get(f"/icons/{filename}")
        assert r.status_code == 200
        assert r.mimetype == "image/svg+xml"
        assert b"<svg" in r.data

    assert c.get("/icons/decoy.svg").status_code == 404
    assert c.get("/icons/%2e%2e/AGENTS.md").status_code == 404


def test_analyze_rejects_non_object_json():
    c = web_server.app.test_client()
    assert c.post("/api/analyze", json=["not", "an", "object"]).status_code == 400
    assert c.post("/api/analyze", data="not-json",
                  content_type="application/json").status_code == 400


def test_analyze_rejects_invalid_usernames():
    c = web_server.app.test_client()
    key = web_server.config.SECRET_KEY
    base = {"map": "de_mirage", "key": key}
    assert c.post("/api/analyze", json={**base, "usernames": "player"}).status_code == 400
    assert c.post("/api/analyze", json={**base, "usernames": [""]}).status_code == 400
    assert c.post("/api/analyze", json={**base, "usernames": [123]}).status_code == 400
    assert c.post("/api/analyze", json={
        **base, "usernames": ["x" * (web_server.MAX_USERNAME_LENGTH + 1)]
    }).status_code == 400


def test_analyze_rejects_unknown_map_and_bad_depth(monkeypatch):
    c = web_server.app.test_client()
    key = web_server.config.SECRET_KEY
    monkeypatch.setattr(web_server.maps, "available_maps", lambda: ["de_mirage"])
    unknown = c.post("/api/analyze", json={
        "usernames": ["player"], "map": "de_fake", "max_demos": 2, "key": key
    })
    assert unknown.status_code == 400
    assert "map" in unknown.get_json()["error"].lower()

    malformed = c.post("/api/analyze", json={
        "usernames": ["player"], "map": "de_mirage", "max_demos": "many", "key": key
    })
    assert malformed.status_code == 400
    assert "max_demos" in malformed.get_json()["error"]


def test_player_domain_rejects_unsafe_identifier():
    c = web_server.app.test_client()
    headers = {"Authorization": f"Bearer {web_server.config.SECRET_KEY}"}
    assert c.get("/api/player/not.a.domain", headers=headers).status_code == 404
    assert c.get("/api/player/%2e%2e", headers=headers).status_code == 404


def test_analyze_is_disabled_without_configured_secret(monkeypatch):
    monkeypatch.setattr(web_server.config, "SECRET_KEY", "")
    c = web_server.app.test_client()
    response = c.post("/api/analyze", json={
        "usernames": ["player"], "map": "de_mirage", "key": "anything"
    })
    assert response.status_code == 503
    assert "CS_SCOUT_SECRET_KEY" in response.get_json()["error"]


def test_analyze_rejects_oversized_request_body():
    c = web_server.app.test_client()
    response = c.post(
        "/api/analyze",
        data="x" * (web_server.app.config["MAX_CONTENT_LENGTH"] + 1),
        content_type="application/json",
    )
    assert response.status_code == 413


def test_analysis_publishes_each_player_result_incrementally(monkeypatch):
    result = {
        "username": "Neo", "domain": "safe-domain",
        "player_json": "/output/player_safe-domain.json",
        "combat_stats": {"kd": 1.2, "awp_rate": 40.0},
        "demos_found": 1, "round_count": 3,
    }
    observed_during_run = []

    def fake_run(usernames, map_name, max_demos, progress_cb, result_cb):
        result_cb(result)
        with web_server.state_lock:
            observed_during_run.extend(web_server.state["results"])
        return [result], []

    monkeypatch.setattr(web_server.pipeline, "run", fake_run)
    with web_server.state_lock:
        previous_state = copy.deepcopy(web_server.state)
        web_server.state.update({"status": "running", "results": [], "failed": []})
    try:
        web_server._run_analysis(["Neo"], "de_mirage", max_demos=1)
        assert observed_during_run == [result]
        with web_server.state_lock:
            assert web_server.state["status"] == "done"
            assert web_server.state["results"] == [result]
    finally:
        with web_server.state_lock:
            web_server.state.clear()
            web_server.state.update(previous_state)
