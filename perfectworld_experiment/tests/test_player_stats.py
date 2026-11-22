import json

from perfectworld_experiment.player_stats import (
    locate_extra_info,
    parse_extra_info,
)

STEAM_A = "76561198348200662"
STEAM_B = "76561198123456789"


def _raw_payload() -> dict:
    """Shape of one ``playerlist_extrainfo`` entry, per docs/response.md."""
    return {
        "success": True,
        "code": 0,
        "data": {
            STEAM_A: {
                "uid": STEAM_A,
                "nickname": "\u4e36Goat",
                "avatar": "//img.wmpvp.com/avatar/a.jpg",
                "score": 2101,
                "adpr": 83,
                "season_rating_pro_average": 1.0977778,
                "season_win_num": 4,
                "season_total_num": 9,
                "season_map_win_num": "3",
                "season_map_total_num": "7",
                "latest_10_win_num": 4,
                "latest_10_total_num": 9,
                "radar_chart": {
                    "fire_power": {
                        "score": "65",
                        "level": "B",
                        "detail": {
                            "kills_per_round_raw": 0.81,
                            "rounds_with_a_kill_raw": 0.52,
                            "we_raw": 9.4,
                        },
                    },
                    "sniper": {"score": "40", "level": "D", "detail": {}},
                    "description": "\u4e0d\u61c8\u53ef\u51fb\u7684\u67aa\u6cd5\u827a\u672f\u5bb6",
                },
                "recent_10_stats": {
                    "pw_rating_avg": "1.10",
                    "pw_rating_list": ["0.94", "0.79", "1.20"],
                    "we_avg": "8.9",
                    "we_list": ["5.9", "6.8"],
                    "win_stats": ["win", "lose", "draw", "win"],
                },
                "perfectPower": {
                    "perfectPower": 3997,
                    "steamId": STEAM_A,
                    "achievedRankList": [
                        {"rank": 2, "rankDesc": "\u957f\u6625\u4e2d\u533b\u836f\u5927\u5b66", "rankType": 4}
                    ],
                },
            },
            STEAM_B: {
                "uid": STEAM_B,
                "nickname": "Newcomer",
                "score": 1200,
                "season_map_win_num": "1",
                "season_map_total_num": "1",
                "recent_10_stats": {"win_stats": ["lose", "lose"]},
            },
        },
    }


def test_parse_extra_info_reads_nested_string_payload():
    payload = {
        "platform_game_id": "9222775918321036300",
        "map_name": "de_dust2",
        "players": [{"player_id": STEAM_A}],
        # The client nests this as a JSON *string* that needs parsing twice.
        "playerlist_extrainfo": json.dumps(_raw_payload(), ensure_ascii=False),
    }

    stats = parse_extra_info(payload)

    assert set(stats) == {STEAM_A, STEAM_B}
    best = stats[STEAM_A]
    assert best.nickname == "\u4e36Goat"
    assert best.score == 2101
    assert best.adpr == 83.0
    assert best.season_rating == 1.0977778
    assert best.perfect_power == 3997
    assert best.rank_desc == "\u957f\u6625\u4e2d\u533b\u836f\u5927\u5b66"


def test_parse_extra_info_accepts_mapping_and_numeric_strings():
    stats = parse_extra_info({"playerlist_extrainfo": _raw_payload()})

    best = stats[STEAM_A]
    # season_map_* arrive as strings in the real payload.
    assert best.map_win_rate == 3 / 7
    assert best.map_total == 7
    assert best.map_sample_low is False
    # radar score is a string too.
    assert best.radar["fire_power"].score == 65
    assert best.radar["fire_power"].level == "B"
    assert best.radar["fire_power"].raw["kills_per_round_raw"] == 0.81
    assert "description" not in best.radar
    assert best.description == "\u4e0d\u61c8\u53ef\u51fb\u7684\u67aa\u6cd5\u827a\u672f\u5bb6"


def test_recent_win_rate_excludes_draws():
    stats = parse_extra_info({"playerlist_extrainfo": _raw_payload()})

    best = stats[STEAM_A]
    # win_stats = win, lose, draw, win -> 2 wins / 3 decided games.
    assert best.recent_win_rate == 2 / 3
    assert best.recent_results == ("win", "lose", "draw", "win")
    assert best.recent_ratings == (0.94, 0.79, 1.2)


def test_season_win_rate_uses_season_totals():
    stats = parse_extra_info({"playerlist_extrainfo": _raw_payload()})

    best = stats[STEAM_A]
    assert best.season_win_rate == 4 / 9
    assert best.season_total == 9


def test_thin_map_sample_is_flagged():
    stats = parse_extra_info({"playerlist_extrainfo": _raw_payload()})

    newcomer = stats[STEAM_B]
    # 1 win in 1 map is 100%, which must never be shown as a reliable rate.
    assert newcomer.map_win_rate == 1.0
    assert newcomer.map_sample_low is True


def test_missing_fields_degrade_to_none_instead_of_zero():
    stats = parse_extra_info({"playerlist_extrainfo": _raw_payload()})

    newcomer = stats[STEAM_B]
    assert newcomer.adpr is None
    assert newcomer.season_rating is None
    assert newcomer.perfect_power is None
    assert newcomer.radar == {}


def test_invalid_payloads_never_raise():
    assert parse_extra_info({}) == {}
    assert parse_extra_info({"playerlist_extrainfo": "not json"}) == {}
    assert parse_extra_info(
        {"playerlist_extrainfo": json.dumps({"success": False, "data": None})}
    ) == {}
    assert parse_extra_info({"playerlist_extrainfo": {"data": {STEAM_B: {}}}}) == {}


def test_locate_extra_info_finds_nested_game_info_shape():
    payload = {
        "game_info": {
            "platform_game_id": "1",
            "playerlist_extrainfo": json.dumps(_raw_payload()),
        }
    }

    located = locate_extra_info(payload)

    assert set(located) == {STEAM_A, STEAM_B}
