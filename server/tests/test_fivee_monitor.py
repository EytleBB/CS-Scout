import base64
import json
from types import SimpleNamespace

import pytest

import fivee_monitor


def _uuid(index):
    return f"00000000-0000-0000-0000-{index:012d}"


def _game_ctx(*, map_name="de_mirage", self_uuid=None):
    team_1 = [_uuid(index) for index in range(1, 6)]
    team_2 = [_uuid(index) for index in range(6, 11)]
    value = {
        "id": "match_123",
        "mapName": map_name,
        "gmi": {
            "t1": {"rooms": [{"members": team_1[:2]}, {"members": team_1[2:]}]},
            "t2": {"rooms": [{"members": team_2}]},
        },
    }
    if self_uuid:
        value["current_user"] = {"uuid": self_uuid}
    return value, team_1, team_2


def test_text_frame_extracts_nested_match_map_and_explicit_self():
    game_ctx, team_1, team_2 = _game_ctx(self_uuid=_uuid(3))
    payload = "42/prefix" + json.dumps({"data": {"game_ctx": game_ctx}})

    matches = fivee_monitor.parse_websocket_frame(
        1, payload, ["de_mirage", "de_dust2"]
    )

    assert matches == [{
        "match_id": "match_123",
        "map": "de_mirage",
        "team_1": team_1,
        "team_2": team_2,
        "self_uuid": _uuid(3),
    }]


def test_text_frame_accepts_explicit_self_from_outer_envelope():
    game_ctx, _team_1, _team_2 = _game_ctx()
    payload = json.dumps({
        "current_user": {"uuid": _uuid(7)},
        "data": {"game_ctx": game_ctx},
    })

    matches = fivee_monitor.parse_websocket_frame(1, payload, ["de_mirage"])

    assert len(matches) == 1
    assert matches[0]["self_uuid"] == _uuid(7)


def test_outer_player_event_uuid_is_not_treated_as_logged_in_user():
    game_ctx, _team_1, _team_2 = _game_ctx()
    payload = json.dumps({
        "player_uuid": _uuid(7),
        "data": {"game_ctx": game_ctx},
    })

    matches = fivee_monitor.parse_websocket_frame(1, payload, ["de_mirage"])

    assert len(matches) == 1
    assert matches[0]["self_uuid"] == ""


def test_binary_frame_accepts_protocol_bytes_around_json():
    game_ctx, _team_1, _team_2 = _game_ctx(map_name="荒漠迷城")
    raw = b"\x00\x01comet:" + json.dumps(
        {"game_ctx": game_ctx}, ensure_ascii=False
    ).encode("utf-8") + b"\x00tail"

    matches = fivee_monitor.parse_websocket_frame(
        2, base64.b64encode(raw).decode("ascii"), ["de_mirage"]
    )

    assert len(matches) == 1
    assert matches[0]["map"] == "de_mirage"


def test_frame_rejects_incomplete_or_duplicate_roster():
    game_ctx, _team_1, _team_2 = _game_ctx()
    game_ctx["gmi"]["t2"]["rooms"][0]["members"][-1] = _uuid(1)
    payload = json.dumps({"game_ctx": game_ctx})

    assert fivee_monitor.parse_websocket_frame(1, payload, ["de_mirage"]) == []


def test_match_detail_resolves_stable_identity_and_map(monkeypatch):
    monkeypatch.setattr(
        fivee_monitor.maps, "available_maps", lambda: ["de_mirage"]
    )
    detail = {
        "main": {"map_info": {"name_en": "Mirage"}},
        "group_1": [{
            "user_info": {"user_data": {
                "uuid": _uuid(1),
                "username": "Alpha",
                "domain": "alpha-domain",
                "steam": {"steamId": "76561198000000001"},
            }}
        }],
        "group_2": [],
    }

    players, map_name = fivee_monitor.players_from_match_detail(detail)

    assert map_name == "de_mirage"
    assert players[_uuid(1)] == {
        "uuid": _uuid(1),
        "username": "Alpha",
        "domain": "alpha-domain",
        "steamid": "76561198000000001",
    }


def test_unknown_identity_requires_one_click_team_selection():
    service = fivee_monitor.FiveEAutoScoutService(
        auto_launch=False, websocket_module=object()
    )
    team_1 = [{
        "uuid": _uuid(index),
        "username": f"Own {index}",
        "domain": f"own-{index}",
        "steamid": f"7656119800000{index:04d}",
        "team": "t1",
    } for index in range(1, 6)]
    team_2 = [{
        "uuid": _uuid(index),
        "username": f"Opponent {index}",
        "domain": f"opponent-{index}",
        "steamid": f"7656119800000{index:04d}",
        "team": "t2",
    } for index in range(6, 11)]
    with service._lock:
        service._candidate_teams = {"t1": team_1, "t2": team_2}
        service._state.update({
            "phase": "awaiting_team_selection",
            "map": "de_mirage",
            "team_options": [
                {"id": "t1", "players": team_1},
                {"id": "t2", "players": team_2},
            ],
        })

    selected = service.select_own_team("t1")
    payload = service.analysis_payload()

    assert selected == {"accepted": True, "phase": "awaiting_confirmation"}
    assert payload["usernames"] == [f"Opponent {index}" for index in range(6, 11)]
    assert payload["map"] == "de_mirage"
    assert len(payload["player_hints"]) == 5


def test_account_id_is_converted_to_steam_id_64():
    assert fivee_monitor._steam_id_from_account_id(39734273) == "76561198000000001"


def test_logged_in_renderer_resolves_players_without_returning_token():
    roster = [_uuid(index) for index in range(1, 11)]
    players = {
        uuid: {
            "uuid": uuid,
            "username": f"Player {index}",
            "domain": f"player-{index}",
            "steam_id": f"7656119800000{index:04d}",
        }
        for index, uuid in enumerate(roster, 1)
    }

    class FakeConnection:
        def __init__(self):
            self.request = None

        def send(self, raw):
            self.request = json.loads(raw)

        def recv(self):
            expression = self.request["params"]["expression"]
            assert "platform-api.5eplay.com" in expression
            assert 'localStorage.getItem(key)' in expression
            return json.dumps({
                "id": self.request["id"],
                "result": {"result": {"value": players}},
            })

        def close(self):
            pass

    websocket_module = SimpleNamespace(
        create_connection=lambda *_args, **_kwargs: FakeConnection()
    )

    resolved = fivee_monitor._fetch_user_info_in_page(
        "ws://127.0.0.1/devtools/page/1", roster, websocket_module
    )

    assert len(resolved) == 10
    assert resolved[roster[0]]["username"] == "Player 1"
    assert set(resolved[roster[0]]) == {"uuid", "username", "steamid", "domain"}


def test_selected_map_is_read_only_for_the_exact_cached_match(monkeypatch):
    class FakeConnection:
        def __init__(self):
            self.request = None

        def send(self, raw):
            self.request = json.loads(raw)

        def recv(self):
            assert "Recentcompetitioninformation" in self.request["params"]["expression"]
            return json.dumps({
                "id": self.request["id"],
                "result": {"result": {"value": "de_dust2"}},
            })

        def close(self):
            pass

    websocket_module = SimpleNamespace(
        create_connection=lambda *_args, **_kwargs: FakeConnection()
    )
    monkeypatch.setattr(
        fivee_monitor.maps, "available_maps", lambda: ["de_dust2", "de_mirage"]
    )

    assert fivee_monitor._matching_map_in_page(
        "ws://127.0.0.1/devtools/page/1", "match_123", websocket_module
    ) == "de_dust2"


def test_cdp_targets_only_include_verified_fivee_pages(monkeypatch):
    targets = [
        {
            "id": "main",
            "type": "page",
            "url": "https://view-arena.5eplay.com/home/room",
            "webSocketDebuggerUrl": "ws://127.0.0.1/main",
        },
        {
            "id": "blank",
            "type": "page",
            "url": "",
            "webSocketDebuggerUrl": "ws://127.0.0.1/blank",
        },
        {
            "id": "worker",
            "type": "worker",
            "url": "https://view-arena.5eplay.com/worker.js",
            "webSocketDebuggerUrl": "ws://127.0.0.1/worker",
        },
    ]

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return targets

    service = fivee_monitor.FiveEAutoScoutService(
        auto_launch=False,
        http_session=SimpleNamespace(get=lambda *_args, **_kwargs: FakeResponse()),
        websocket_module=object(),
    )
    monkeypatch.setattr(fivee_monitor, "cdp_listener_is_5e", lambda _port: True)

    assert [target["id"] for target in service._cdp_targets()] == ["main"]


def test_cdp_listener_must_be_owned_by_fivee(monkeypatch):
    monkeypatch.setattr(fivee_monitor.sys, "platform", "win32")

    def fake_run(command, **_kwargs):
        if command[0] == "netstat.exe":
            return SimpleNamespace(
                returncode=0,
                stdout="TCP 127.0.0.1:9222 0.0.0.0:0 LISTENING 321\n",
            )
        return SimpleNamespace(
            returncode=0,
            stdout='"5EClient.exe","321","Console","1","100 K"\n',
        )

    monkeypatch.setattr(fivee_monitor.subprocess, "run", fake_run)

    assert fivee_monitor.cdp_listener_is_5e(9222) is True


def test_unknown_process_state_does_not_claim_fivee_is_stopped(monkeypatch):
    monkeypatch.setattr(fivee_monitor.sys, "platform", "win32")
    monkeypatch.setattr(
        fivee_monitor.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout=""),
    )
    monkeypatch.setattr(
        fivee_monitor, "_fivee_running_via_powershell", lambda: None
    )

    assert fivee_monitor.is_5e_running() is None


def test_custom_executable_is_validated_saved_and_discovered(monkeypatch, tmp_path):
    executable = tmp_path / "Custom 5E" / "5EClient.exe"
    executable.parent.mkdir()
    executable.write_bytes(b"signed test executable")
    config_path = tmp_path / "data" / "fivee-install.json"
    monkeypatch.setenv("CS_SCOUT_5E_CONFIG", str(config_path))
    monkeypatch.setattr(fivee_monitor, "_official_5e_signature", lambda _path: True)

    configured = fivee_monitor.configure_5e_executable(executable)
    located = fivee_monitor.locate_5e_executable()

    assert configured["ready"] is True
    assert json.loads(config_path.read_text(encoding="utf-8"))["executable"] == str(
        executable.resolve()
    )
    assert located["found"] is True
    assert located["source"] == "saved"
    assert located["path"] == str(executable.resolve())


def test_custom_executable_rejects_wrong_file_or_signature(monkeypatch, tmp_path):
    wrong_name = tmp_path / "client.exe"
    wrong_name.write_bytes(b"not 5e")
    assert fivee_monitor.configure_5e_executable(wrong_name)["code"] == "wrong_file"

    executable = tmp_path / "5EClient.exe"
    executable.write_bytes(b"not signed")
    monkeypatch.setattr(fivee_monitor, "_official_5e_signature", lambda _path: False)
    assert fivee_monitor.configure_5e_executable(executable)["code"] == "invalid_signature"


def test_supervisor_launches_discovered_non_default_client(monkeypatch):
    launched = []
    service = fivee_monitor.FiveEAutoScoutService(
        auto_launch=True, cdp_port=9222, websocket_module=object()
    )
    monkeypatch.setattr(service, "_cdp_targets", lambda: [])
    monkeypatch.setattr(fivee_monitor, "is_5e_running", lambda: False)
    monkeypatch.setattr(fivee_monitor, "locate_5e_executable", lambda: {
        "found": True,
        "path": r"D:\\Games\\5E\\5EClient.exe",
        "source": "saved",
        "message": "ready",
    })
    monkeypatch.setattr(fivee_monitor, "_loopback_port_in_use", lambda _port: False)
    monkeypatch.setattr(
        fivee_monitor, "launch_5e_with_cdp",
        lambda executable, port: launched.append((executable, port)),
    )

    service._supervisor_iteration()

    assert launched == [(r"D:\\Games\\5E\\5EClient.exe", 9222)]
    snapshot = service.snapshot()
    assert snapshot["phase"] == "connecting"
    assert snapshot["connection_code"] == "launching"
    assert snapshot["executable_found"] is True


def test_supervisor_chooses_free_port_when_default_is_occupied(monkeypatch):
    launched = []
    service = fivee_monitor.FiveEAutoScoutService(
        auto_launch=True, cdp_port=9222, websocket_module=object()
    )
    monkeypatch.setattr(service, "_cdp_targets", lambda: [])
    monkeypatch.setattr(fivee_monitor, "is_5e_running", lambda: False)
    monkeypatch.setattr(fivee_monitor, "locate_5e_executable", lambda: {
        "found": True, "path": r"E:\\5E\\5EClient.exe", "source": "saved",
        "message": "ready",
    })
    monkeypatch.setattr(fivee_monitor, "_loopback_port_in_use", lambda _port: True)
    monkeypatch.setattr(fivee_monitor, "cdp_listener_is_5e", lambda _port: False)
    monkeypatch.setattr(fivee_monitor, "_free_loopback_port", lambda: 19333)
    monkeypatch.setattr(
        fivee_monitor, "launch_5e_with_cdp",
        lambda executable, port: launched.append((executable, port)),
    )

    service._supervisor_iteration()

    assert launched == [(r"E:\\5E\\5EClient.exe", 19333)]
    assert service.snapshot()["cdp_port"] == 19333


def test_running_client_without_cdp_stops_connecting_forever(monkeypatch):
    service = fivee_monitor.FiveEAutoScoutService(
        auto_launch=True, websocket_module=object()
    )
    monkeypatch.setattr(service, "_cdp_targets", lambda: [])
    monkeypatch.setattr(fivee_monitor, "is_5e_running", lambda: True)

    service._supervisor_iteration()

    snapshot = service.snapshot()
    assert snapshot["phase"] == "manual"
    assert snapshot["connection_code"] == "client_running_without_cdp"
    assert snapshot["manual_fallback"] is True
    assert "CDP" in snapshot["last_error"]


def test_running_client_relaunches_once_after_user_exits(monkeypatch):
    launched = []
    running_states = iter((True, False, False))
    service = fivee_monitor.FiveEAutoScoutService(
        auto_launch=True, cdp_port=9222, websocket_module=object()
    )
    monkeypatch.setattr(service, "_cdp_targets", lambda: [])
    monkeypatch.setattr(
        fivee_monitor, "is_5e_running", lambda: next(running_states)
    )
    monkeypatch.setattr(fivee_monitor, "locate_5e_executable", lambda: {
        "found": True,
        "path": r"D:\\Games\\5E\\5EClient.exe",
        "source": "saved",
        "message": "ready",
    })
    monkeypatch.setattr(fivee_monitor, "_loopback_port_in_use", lambda _port: False)
    monkeypatch.setattr(
        fivee_monitor, "launch_5e_with_cdp",
        lambda executable, port: launched.append((executable, port)),
    )

    service._supervisor_iteration()
    assert service.snapshot()["connection_code"] == "client_running_without_cdp"

    service._supervisor_iteration()
    assert launched == []
    assert service.snapshot()["connection_code"] == "relaunch_pending"

    service._next_launch_attempt = 0.0
    service._supervisor_iteration()
    assert launched == [(r"D:\\Games\\5E\\5EClient.exe", 9222)]
    assert service.snapshot()["connection_code"] == "launching"


def test_failed_controlled_relaunch_does_not_loop(monkeypatch):
    launched = []
    running_states = iter((True, False, False, True, False))
    service = fivee_monitor.FiveEAutoScoutService(
        auto_launch=True, cdp_port=9222, websocket_module=object()
    )
    monkeypatch.setattr(service, "_cdp_targets", lambda: [])
    monkeypatch.setattr(
        fivee_monitor, "is_5e_running", lambda: next(running_states)
    )
    monkeypatch.setattr(fivee_monitor, "locate_5e_executable", lambda: {
        "found": True,
        "path": r"D:\\Games\\5E\\5EClient.exe",
        "source": "saved",
        "message": "ready",
    })
    monkeypatch.setattr(fivee_monitor, "_loopback_port_in_use", lambda _port: False)
    monkeypatch.setattr(
        fivee_monitor, "launch_5e_with_cdp",
        lambda executable, port: launched.append((executable, port)),
    )

    service._supervisor_iteration()
    service._supervisor_iteration()
    service._next_launch_attempt = 0.0
    service._supervisor_iteration()
    assert len(launched) == 1

    service._launch_started_at = fivee_monitor.time.monotonic() - (
        fivee_monitor.CLIENT_LAUNCH_TIMEOUT + 1
    )
    service._supervisor_iteration()
    assert service.snapshot()["connection_code"] == "client_running_without_cdp"
    service._supervisor_iteration()

    snapshot = service.snapshot()
    assert len(launched) == 1
    assert snapshot["connection_code"] == "relaunch_failed"
    assert snapshot["manual_fallback"] is True


def test_reentering_automatic_mode_resets_relaunch_limit():
    service = fivee_monitor.FiveEAutoScoutService(
        auto_launch=True, websocket_module=object()
    )
    with service._lock:
        service._last_launch_failed = True
        service._exit_relaunches = fivee_monitor.MAX_EXIT_RELAUNCHES
        service._state.update({
            "phase": "manual",
            "client_running": False,
            "connection_code": "relaunch_failed",
        })

    service.configure(max_demos=6, mode="normal")

    assert service._last_launch_failed is False
    assert service._exit_relaunches == 0


@pytest.mark.parametrize("success", [False, True])
def test_completed_analysis_keeps_confirmed_roster_for_retry(success):
    service = fivee_monitor.FiveEAutoScoutService(
        auto_launch=False, websocket_module=object()
    )
    targets = [{
        "uuid": _uuid(index),
        "username": f"Opponent {index}",
        "domain": f"opponent-{index}",
        "steamid": f"7656119800000{index:04d}",
        "team": "t2",
    } for index in range(1, 6)]
    with service._lock:
        service._active_match_id = "match_123"
        service._state.update({
            "phase": "awaiting_confirmation",
            "current_match_id": "match_123",
            "map": "de_mirage",
            "targets": targets,
        })

    service.mark_analysis_started(17)
    service.finish_analysis(
        success=success,
        message="分析完成" if success else "分析失败",
    )

    snapshot = service.snapshot()
    assert snapshot["phase"] == "awaiting_confirmation"
    assert snapshot["analysis_active"] is False
    assert snapshot["analysis_id"] == 17
    assert "重新分析" in snapshot["message"] or "重试" in snapshot["message"]
    assert service._active_match_id == ""
    assert service._seen_ids == ["match_123"]
    assert service.analysis_payload()["usernames"] == [
        f"Opponent {index}" for index in range(1, 6)
    ]
