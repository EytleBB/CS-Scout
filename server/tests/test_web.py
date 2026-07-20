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


@pytest.fixture
def isolated_web_state():
    with web_server.state_lock:
        previous_state = copy.deepcopy(web_server.state)
        web_server.state.clear()
        web_server.state.update({
            "status": "idle",
            "message": "",
            "progress": [],
            "results": [],
            "failed": [],
            "total_players": 0,
            "max_demos": 10,
            "map": "",
            "mode": "normal",
        })
    try:
        yield
    finally:
        with web_server.state_lock:
            web_server.state.clear()
            web_server.state.update(previous_state)

def test_status_shape():
    c = web_server.app.test_client()
    r = c.get("/api/status", headers={
        "Authorization": f"Bearer {web_server.config.SECRET_KEY}"
    })
    assert r.status_code == 200
    assert "status" in r.get_json()
    assert r.headers["Cache-Control"] == "no-store"


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


@pytest.mark.parametrize("path", [
    "/api/status", "/api/results", "/api/player/safe-domain",
    "/output/player_safe-domain.json",
])
def test_sensitive_get_routes_require_bearer_key(path):
    c = web_server.app.test_client()

    missing = c.get(path)
    assert missing.status_code == 401
    assert missing.headers["WWW-Authenticate"] == 'Bearer realm="CS-Scout"'
    assert missing.headers["Cache-Control"] == "no-store"
    assert missing.headers["Pragma"] == "no-cache"

    invalid = c.get(path, headers={"Authorization": "Bearer wrong"})
    assert invalid.status_code == 403
    assert invalid.headers["Cache-Control"] == "no-store"


def test_sensitive_routes_accept_valid_bearer_and_do_not_cache(monkeypatch, tmp_path):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    player_payload = {"username": "Alpha", "rounds": []}
    player_path = output_dir / "player_safe-domain.json"
    player_path.write_text(json.dumps(player_payload), encoding="utf-8")
    monkeypatch.setattr(web_server.config, "OUTPUT_DIR", str(output_dir))
    headers = {"Authorization": f"Bearer {web_server.config.SECRET_KEY}"}
    c = web_server.app.test_client()

    for path in ("/api/status", "/api/results", "/api/player/safe-domain",
                 "/output/player_safe-domain.json"):
        response = c.get(path, headers=headers)
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"


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
