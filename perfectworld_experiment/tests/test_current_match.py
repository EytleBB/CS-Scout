import json

from perfectworld_experiment.current_match import (
    PerfectWorldLogState,
    SessionCredentials,
    decode_log_line,
    read_local_state,
)


def _encoded_line(message: str, prefix: str = "[TRACE] game - ") -> str:
    body = bytes(
        value ^ ((42 + 3 * index) % 255)
        for index, value in enumerate(message.encode("utf-8"))
    ).hex()
    return f"{prefix}^{body}$\n"


def _start_message(match_id="987654321") -> str:
    payload = {
        "game_info": {
            "platform_game_id": match_id,
            "map_name": "de_mirage",
            "host_info": "203.0.113.10:27015",
            "zone_name": "华南",
            "players": [
                {
                    "player_id": "1001",
                    "slot_id": 0,
                    "roll_team_id": 1,
                    "troop_team_id": 9001,
                },
                {
                    "player_id": "1002",
                    "steam_id": "76561198123456789",
                    "nick_name": "tester",
                    "slot_id": 5,
                    "roll_team_id": 2,
                    "troop_team_id": 9002,
                },
            ],
        }
    }
    return "recv a game start notify msg, reconnect notify: " + json.dumps(
        payload, ensure_ascii=False
    )


def test_log_codec_and_active_match_accumulator():
    state = PerfectWorldLogState()
    line = _encoded_line(_start_message())
    assert "recv a game start" in decode_log_line(line)

    state.consume(line)
    snapshot = state.snapshot()
    assert snapshot.current_match is not None
    assert snapshot.current_match.match_id == "987654321"
    assert snapshot.current_match.map_name == "de_mirage"
    assert [player.player_id for player in snapshot.current_match.players] == ["1001", "1002"]
    assert snapshot.current_match.players[1].steam_id == "76561198123456789"
    assert [player.team_id for player in snapshot.current_match.players] == [1, 2]

    # attach_id2, synchronized by the desktop client, is authoritative.
    state.consume(_encoded_line('DRIVE_SET_MATCH_ID {"matchId":"match-actual"}'))
    assert state.snapshot().current_match.match_id == "match-actual"

    state.consume(_encoded_line("recv a game over notify msg, notify: {}"))
    assert state.snapshot().current_match is None


def test_session_credentials_are_recovered_in_memory_and_redacted_from_repr():
    state = PerfectWorldLogState()
    payload = {
        "code": 0,
        "data": {
            "user": {
                "steam_id": "76561198123456789",
                "jt": "secret-jt",
            }
        },
    }
    state.consume(
        _encoded_line(
            "queryUserDetailsByToken: access-secret " + json.dumps(payload)
        )
    )
    session = state.snapshot().session
    assert session == SessionCredentials(
        "76561198123456789", "access-secret", "secret-jt"
    )
    assert "access-secret" not in repr(session)
    assert "secret-jt" not in repr(session)


def test_read_local_state_handles_rotated_logs(tmp_path):
    old = tmp_path / "pvpClient.2026-08-03.log"
    new = tmp_path / "pvpClient.2026-08-04.log"
    old.write_text(_encoded_line(_start_message("111")), encoding="utf-8")
    new.write_text(
        _encoded_line('DRIVE_SET_MATCH_ID {"matchId":"222"}'),
        encoding="utf-8",
    )
    old.touch()
    new.touch()

    state = read_local_state(tmp_path)
    assert state.current_match is not None
    assert state.current_match.match_id == "222"


def test_empty_match_id_clears_stale_match():
    state = PerfectWorldLogState()
    state.consume(_encoded_line(_start_message()))
    state.consume(_encoded_line('DRIVE_SET_MATCH_ID {"matchId":""}'))
    assert state.snapshot().current_match is None


def test_transient_launcher_minus_one_does_not_clear_game_start():
    state = PerfectWorldLogState()
    state.consume(_encoded_line(_start_message()))
    state.consume(_encoded_line('DRIVE_SET_MATCH_ID {"matchId":"-1"}'))
    assert state.snapshot().current_match is not None
    assert state.snapshot().current_match.match_id == "987654321"
    state.consume(_encoded_line('DRIVE_SET_MATCH_ID {"matchId":"real-match"}'))
    assert state.snapshot().current_match.match_id == "real-match"


def test_session_survives_daily_log_rotation(tmp_path):
    payload = {
        "code": 0,
        "data": {
            "user": {
                "steam_id": "76561198123456789",
                "jt": "old-login-jt",
            }
        },
    }
    login = tmp_path / "pvpClient.2026-07-31.log"
    login.write_text(
        _encoded_line("queryUserDetailsByToken: old-token " + json.dumps(payload)),
        encoding="utf-8",
    )
    for day in range(1, 5):
        path = tmp_path / f"pvpClient.2026-08-0{day}.log"
        path.write_text(_encoded_line(f"ordinary trace {day}"), encoding="utf-8")
        path.touch()

    state = read_local_state(tmp_path, recent_files=3)
    assert state.session is not None
    assert state.session.access_token == "old-token"
