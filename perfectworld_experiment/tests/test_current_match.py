import json

from perfectworld_experiment.current_match import (
    LogFollower,
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
    # troop_team_id is the premade party, kept separate from the side assignment.
    assert [player.troop_team_id for player in snapshot.current_match.players] == [9001, 9002]

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

# ── Incremental log tailing (M0-3) ──────────────────────────────────────────


def _append(path, text: str) -> None:
    with path.open("a", encoding="utf-8", newline="") as handle:
        handle.write(text)


def test_log_follower_reads_only_appended_bytes(tmp_path):
    log = tmp_path / "pvpClient.2026-11-13.log"
    first = _encoded_line("ordinary trace one")
    log.write_text(first, encoding="utf-8")

    follower = LogFollower(tmp_path)
    assert follower.poll() is True
    after_prime = follower.diagnostics()
    assert after_prime["lines_received"] == 1
    assert after_prime["file_exists"] is True

    second = _encoded_line("ordinary trace two")
    _append(log, second)
    assert follower.poll() is True

    diagnostics = follower.diagnostics()
    assert diagnostics["lines_received"] == 2
    # The follower must consume the appended bytes only, never re-read the file.
    assert diagnostics["bytes_read"] - after_prime["bytes_read"] == len(
        second.encode("utf-8")
    )


def test_log_follower_buffers_partial_lines_until_terminated(tmp_path):
    log = tmp_path / "pvpClient.2026-11-13.log"
    line = _encoded_line(_start_message())
    split = len(line) // 2
    log.write_text(line[:split], encoding="utf-8")

    follower = LogFollower(tmp_path)
    follower.poll()
    assert follower.snapshot().current_match is None
    assert follower.diagnostics()["partial_pending"] is True

    _append(log, line[split:])
    follower.poll()
    assert follower.snapshot().current_match is not None
    assert follower.snapshot().current_match.match_id == "987654321"
    assert follower.diagnostics()["partial_pending"] is False


def test_log_follower_switches_to_rotated_file(tmp_path):
    older = tmp_path / "pvpClient.2026-11-12.log"
    older.write_text(_encoded_line("ordinary trace from yesterday"), encoding="utf-8")

    follower = LogFollower(tmp_path)
    follower.poll()
    assert follower.snapshot().current_match is None

    newer = tmp_path / "pvpClient.2026-11-13.log"
    newer.write_text(_encoded_line(_start_message("555000111")), encoding="utf-8")
    assert follower.poll() is True

    current = follower.snapshot().current_match
    assert current is not None
    assert current.match_id == "555000111"
    assert follower.diagnostics()["file"].endswith("pvpClient.2026-11-13.log")


def test_log_follower_resets_offset_when_file_shrinks(tmp_path):
    log = tmp_path / "pvpClient.2026-11-13.log"
    log.write_text(
        _encoded_line("ordinary trace one")
        + _encoded_line("ordinary trace two")
        + _encoded_line(_start_message("111")),
        encoding="utf-8",
    )

    follower = LogFollower(tmp_path)
    follower.poll()
    assert follower.snapshot().current_match.match_id == "111"

    # The client restarted and rewrote the log from the beginning: the new
    # content is shorter, so a stale offset would read nothing at all.
    log.write_text(_encoded_line(_start_message("222")), encoding="utf-8")
    assert follower.poll() is True

    assert follower.snapshot().current_match.match_id == "222"
    assert follower.diagnostics()["offset"] <= log.stat().st_size


def test_log_follower_detects_rewrite_with_new_file_head(tmp_path):
    log = tmp_path / "pvpClient.2026-11-13.log"
    log.write_text(_encoded_line(_start_message("111")), encoding="utf-8")

    follower = LogFollower(tmp_path)
    follower.poll()
    assert follower.snapshot().current_match.match_id == "111"

    # Same byte count, different head (a restarted client rewrites its header
    # block).  Only the head fingerprint can prove the old content is gone.
    before = log.stat().st_size
    log.write_text(
        _encoded_line(_start_message("222"), prefix="[TRACE] gam3 - "),
        encoding="utf-8",
    )
    assert log.stat().st_size == before
    assert follower.poll() is True

    assert follower.snapshot().current_match.match_id == "222"


def test_log_follower_waits_for_missing_log_directory(tmp_path):
    target = tmp_path / "Wmpvp" / "Log"
    follower = LogFollower(target)

    assert follower.poll() is False
    diagnostics = follower.diagnostics()
    assert diagnostics["file_exists"] is False
    assert diagnostics["watching"] == str(tmp_path)

    target.mkdir(parents=True)
    (target / "pvpClient.2026-11-13.log").write_text(
        _encoded_line(_start_message("999")), encoding="utf-8",
    )

    assert follower.poll() is True
    assert follower.snapshot().current_match.match_id == "999"
    assert follower.diagnostics()["watching"] == str(target)


def test_current_match_carries_platform_statistics(tmp_path):
    """The create-game payload already contains the pre-match statistics."""
    steam_a = "76561198348200662"
    steam_b = "76561198123456789"
    extra = {
        "success": True,
        "code": 0,
        "data": {
            steam_a: {
                "uid": steam_a,
                "nickname": "Goat",
                "score": 2101,
                "adpr": 83,
                "season_map_win_num": "3",
                "season_map_total_num": "7",
                "radar_chart": {"sniper": {"score": "40", "level": "D", "detail": {}}},
            },
            steam_b: {
                "uid": steam_b,
                "score": 1200,
                "season_map_win_num": "1",
                "season_map_total_num": "1",
            },
        },
    }
    payload = {
        "game_info": {
            "platform_game_id": "777000",
            "map_name": "de_dust2",
            "players": [
                {"player_id": steam_a, "roll_team_id": 1},
                {"player_id": steam_b, "roll_team_id": 2},
            ],
            "playerlist_extrainfo": json.dumps(extra, ensure_ascii=False),
        }
    }
    message = "recv a game start notify msg: " + json.dumps(payload, ensure_ascii=False)
    (tmp_path / "pvpClient.2026-11-13.log").write_text(
        _encoded_line(message), encoding="utf-8",
    )

    match = read_local_state(tmp_path).current_match

    assert match is not None
    assert set(match.extras) == {steam_a, steam_b}
    assert match.extras[steam_a].score == 2101
    assert match.extras[steam_a].adpr == 83.0
    assert match.extras[steam_a].radar["sniper"].level == "D"
    assert match.extras[steam_b].map_sample_low is True
