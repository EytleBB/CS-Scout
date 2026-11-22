import pytest

from perfectworld_experiment.current_match import CurrentMatch, RawMatchPlayer
from perfectworld_experiment.player_stats import PlayerStats, RadarDimension
from perfectworld_experiment.prep_panel import build_prep_panel

OWN = "76561198348200662"
MATE = "76561198000000001"
ENEMY = "76561198000000002"


def _player(player_id, *, steam_id=None, nickname=None, slot=None, team=None, troop=None):
    return RawMatchPlayer(
        player_id=player_id,
        steam_id=steam_id,
        nickname=nickname,
        slot_id=slot,
        team_id=team,
        troop_team_id=troop,
    )


def _stats(steam_id, **overrides):
    base = dict(
        steam_id=steam_id,
        nickname="nick",
        score=2101,
        adpr=83.0,
        recent_rating=1.1,
        season_rating=1.09,
        season_win_rate=4 / 9,
        season_total=9,
        map_win_rate=3 / 7,
        map_total=7,
        map_sample_low=False,
        recent_win_rate=2 / 3,
        recent_results=("win", "lose", "draw", "win"),
        recent_ratings=(0.94, 0.79, 1.2),
        perfect_power=3997,
        rank_desc="某大学",
        description="枪法艺术家",
        radar={"sniper": RadarDimension("sniper", 40, "D", {"sniper_kill_per_round_raw": 0.2})},
    )
    base.update(overrides)
    return PlayerStats(**base)


def _ten_player_match(extras):
    players = (
        _player(OWN, steam_id=OWN, nickname="me", slot=0, team=1),
        _player(MATE, steam_id=MATE, nickname="mate", slot=1, team=1),
        _player(ENEMY, steam_id=ENEMY, nickname="enemy", slot=5, team=2),
    )
    return CurrentMatch(
        match_id="match-1",
        map_name="de_dust2",
        players=players,
        extras=extras,
    )


def test_team_of_the_signed_in_account_is_labelled_ours():
    extras = {OWN: _stats(OWN), MATE: _stats(MATE), ENEMY: _stats(ENEMY)}
    match = _ten_player_match(extras)

    panel = build_prep_panel(match, own_steamid=OWN)

    assert panel["available"] is True
    assert panel["map"] == "de_dust2"
    assert panel["own_side"] == 1
    labels = {team["side"]: team["label"] for team in panel["teams"]}
    assert labels == {1: "ours", 2: "theirs"}
    ours = next(team for team in panel["teams"] if team["label"] == "ours")
    assert [player["steam_id"] for player in ours["players"]] == [OWN, MATE]


def test_unknown_account_splits_teams_without_claiming_a_side():
    extras = {OWN: _stats(OWN)}
    match = _ten_player_match(extras)

    panel = build_prep_panel(match, own_steamid=None)

    assert panel["own_side"] is None
    assert {team["label"] for team in panel["teams"]} == {"unknown"}
    assert len(panel["teams"]) == 2


def test_stats_are_joined_by_steam_id_not_by_raw_player_id():
    # The raw roster id differs from the SteamID; stats are keyed by SteamID.
    players = (_player("internal-1", steam_id=OWN, nickname="me", slot=0, team=1),)
    match = CurrentMatch(
        match_id="m", map_name="de_dust2", players=players,
        extras={OWN: _stats(OWN)},
    )

    panel = build_prep_panel(match, own_steamid=OWN)

    entry = panel["teams"][0]["players"][0]
    assert entry["has_stats"] is True
    assert entry["score"] == 2101
    assert entry["radar"]["sniper"]["level"] == "D"


def test_players_without_platform_stats_degrade_to_none():
    players = (
        _player(OWN, steam_id=OWN, nickname="me", slot=0, team=1),
        _player("internal-2", steam_id=None, nickname="ghost", slot=1, team=1),
    )
    match = CurrentMatch(
        match_id="m", map_name="de_dust2", players=players,
        extras={OWN: _stats(OWN)},
    )

    panel = build_prep_panel(match, own_steamid=OWN)

    ghost = panel["teams"][0]["players"][1]
    assert ghost["has_stats"] is False
    assert ghost["score"] is None
    assert ghost["radar"] == {}
    assert ghost["nickname"] == "ghost"
    assert panel["stats_coverage"] == {"with_stats": 1, "total": 2}


def test_players_without_a_side_are_reported_separately():
    players = (
        _player(OWN, steam_id=OWN, slot=0, team=1),
        _player("internal-9", steam_id=None, nickname="late", slot=9, team=None),
    )
    match = CurrentMatch(
        match_id="m", map_name="de_dust2", players=players, extras={},
    )

    panel = build_prep_panel(match, own_steamid=OWN)

    assert len(panel["teams"]) == 1
    assert [player["nickname"] for player in panel["unassigned"]] == ["late"]


def test_players_are_ordered_by_slot():
    players = (
        _player("c", steam_id=None, nickname="third", slot=2, team=1),
        _player("a", steam_id=None, nickname="first", slot=0, team=1),
        _player("b", steam_id=None, nickname="second", slot=1, team=1),
    )
    match = CurrentMatch(
        match_id="m", map_name="de_dust2", players=players, extras={},
    )

    panel = build_prep_panel(match, own_steamid=None)

    assert [player["nickname"] for player in panel["teams"][0]["players"]] == [
        "first", "second", "third",
    ]


def test_missing_match_reports_unavailable():
    panel = build_prep_panel(None, own_steamid=OWN)

    assert panel["available"] is False
    assert panel["teams"] == []


def test_premade_parties_get_stable_ordinals_and_sizes():
    players = (
        _player("a", nickname="duo-a", slot=0, team=1, troop=7001),
        _player("b", nickname="duo-b", slot=1, team=1, troop=7001),
        _player("c", nickname="trio-a", slot=2, team=1, troop=7002),
        _player("d", nickname="trio-b", slot=3, team=1, troop=7002),
        _player("e", nickname="trio-c", slot=4, team=1, troop=7002),
        _player("f", nickname="solo", slot=5, team=1, troop=None),
    )
    match = CurrentMatch(
        match_id="m", map_name="de_dust2", players=players, extras={},
    )

    panel = build_prep_panel(match, own_steamid=None)

    marks = [
        (player["party"], player["party_size"])
        for player in panel["teams"][0]["players"]
    ]
    assert marks == [(1, 2), (1, 2), (2, 3), (2, 3), (2, 3), (None, 1)]


def test_party_guards_ignore_unset_and_oversized_groups():
    players = (
        _player("a", nickname="unset-a", slot=0, team=1, troop=0),
        _player("b", nickname="unset-b", slot=1, team=1, troop=0),
        _player("c", nickname="big-a", slot=2, team=1, troop=9001),
        _player("d", nickname="big-b", slot=3, team=1, troop=9001),
        _player("e", nickname="big-c", slot=4, team=1, troop=9001),
        _player("f", nickname="big-d", slot=5, team=1, troop=9001),
        _player("g", nickname="big-e", slot=6, team=1, troop=9001),
        _player("h", nickname="big-f", slot=7, team=1, troop=9001),
    )
    match = CurrentMatch(
        match_id="m", map_name="de_dust2", players=players, extras={},
    )

    panel = build_prep_panel(match, own_steamid=None)

    marks = [
        (player["party"], player["party_size"])
        for player in panel["teams"][0]["players"]
    ]
    assert marks == [(None, 1)] * len(players)


def test_party_is_scoped_to_one_team():
    players = (
        _player("a", nickname="ours", slot=0, team=1, troop=8001),
        _player("b", nickname="theirs", slot=1, team=2, troop=8001),
    )
    match = CurrentMatch(
        match_id="m", map_name="de_dust2", players=players, extras={},
    )

    panel = build_prep_panel(match, own_steamid=None)

    marks = {
        team["side"]: [(player["party"], player["party_size"]) for player in team["players"]]
        for team in panel["teams"]
    }
    assert marks == {1: [(None, 1)], 2: [(None, 1)]}


def test_team_aggregates_weight_map_rate_and_count_the_low_samples():
    extras = {
        OWN: _stats(
            OWN, score=2100, adpr=80.0, season_rating=1.0,
            perfect_power=3000, map_win_rate=4 / 10, map_total=10,
            map_sample_low=False,
        ),
        MATE: _stats(
            MATE, score=2200, adpr=90.0, season_rating=1.2,
            perfect_power=4000, map_win_rate=1 / 2, map_total=2,
            map_sample_low=True,
        ),
        ENEMY: _stats(ENEMY, score=1900),
    }
    players = (
        _player(OWN, steam_id=OWN, nickname="me", slot=0, team=1),
        _player(MATE, steam_id=MATE, nickname="mate", slot=1, team=1),
        # Raw player with no platform data must not dilute the averages.
        _player("ghost", nickname="ghost", slot=2, team=1),
        _player(ENEMY, steam_id=ENEMY, nickname="enemy", slot=5, team=2),
    )
    match = CurrentMatch(
        match_id="m", map_name="de_dust2", players=players, extras=extras,
    )

    panel = build_prep_panel(match, own_steamid=OWN)

    ours = next(team for team in panel["teams"] if team["label"] == "ours")
    assert ours["aggregates"] == {
        "avg_score": 2150.0,
        "avg_adpr": 85.0,
        "avg_season_rating": pytest.approx(1.1),
        "avg_perfect_power": 3500.0,
        # Weighted by map sample: (4 + 1) / (10 + 2), not the mean of ratios.
        "map_win_rate": pytest.approx(5 / 12),
        "with_stats": 2,
        "map_sample_low": 1,
    }


def test_team_aggregates_degrade_to_none_without_stats():
    players = (_player("ghost", nickname="ghost", slot=0, team=1),)
    match = CurrentMatch(
        match_id="m", map_name="de_dust2", players=players, extras={},
    )

    panel = build_prep_panel(match, own_steamid=None)

    assert panel["teams"][0]["aggregates"] == {
        "avg_score": None,
        "avg_adpr": None,
        "avg_season_rating": None,
        "avg_perfect_power": None,
        "map_win_rate": None,
        "with_stats": 0,
        "map_sample_low": 0,
    }
