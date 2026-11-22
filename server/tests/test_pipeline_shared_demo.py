"""Shared-demo scheduling: one parse job per demo, not per opponent.

The grouped path must stay invisible for the all-solo case (every player has
their own matches), so both the grouping decision and the worker contract are
checked here without touching a real demo file.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pipeline

SHARED = "g161-match-001"
OTHER = "g161-match-002"


def _state(steamid, username, match_codes):
    return {
        "context": {
            "steamid": steamid,
            "username": username,
            "demos": [
                {
                    "match_code": match_code,
                    "demo_url": f"https://demo/{match_code}.zip",
                }
                for match_code in match_codes
            ],
        }
    }


def _group(states, covered, player_index, demo_order, dem_files):
    return pipeline._group_parse_targets(
        states,
        covered,
        states[player_index]["context"],
        demo_order,
        dem_files,
    )


def test_two_players_in_the_same_match_share_one_job():
    states = {
        0: _state("76561198000000001", "other-a", [SHARED]),
        1: _state("76561198000000002", "other-b", [SHARED]),
    }

    groups = _group(states, {}, 0, 0, ["/demos/shared.dem"])

    assert len(groups) == 1
    dem_file, targets, file_order = groups[0]
    assert dem_file == "/demos/shared.dem"
    assert file_order == 0
    assert [target[0] for target in targets] == [0, 1]


def test_players_with_distinct_matches_keep_one_job_each():
    states = {
        0: _state("76561198000000001", "other-a", [SHARED]),
        1: _state("76561198000000002", "other-b", [OTHER]),
    }

    groups = _group(states, {}, 0, 0, ["/demos/shared.dem"])

    assert len(groups) == 1
    assert [target[0] for target in groups[0][1]] == [0]

    solo = _group(states, {}, 1, 0, ["/demos/other.dem"])
    assert len(solo) == 1
    assert [target[0] for target in solo[0][1]] == [1]


def test_multiple_extracted_files_become_separate_groups():
    states = {
        0: _state("76561198000000001", "other-a", [SHARED]),
        1: _state("76561198000000002", "other-b", [SHARED]),
    }

    groups = _group(
        states, {}, 0, 0, ["/demos/one.dem", "/demos/two.dem"]
    )

    assert [group[0] for group in groups] == ["/demos/one.dem", "/demos/two.dem"]
    assert [group[2] for group in groups] == [0, 1]
    assert [len(group[1]) for group in groups] == [2, 2]
    assert [target[3] for group in groups for target in group[1]] == [0, 0, 1, 1]


def test_a_player_is_never_scheduled_twice_for_the_same_file():
    states = {
        0: _state("76561198000000001", "other-a", [SHARED]),
        1: _state("76561198000000002", "other-b", [SHARED]),
    }
    covered = {}

    first = _group(states, covered, 0, 0, ["/demos/shared.dem"])
    second = _group(states, covered, 0, 0, ["/demos/shared.dem"])

    assert len(first) == 1
    assert second == []


def test_each_player_keeps_their_own_demo_order_for_the_shared_match():
    states = {
        0: _state("76561198000000001", "other-a", [SHARED, OTHER]),
        1: _state("76561198000000002", "other-b", [OTHER, SHARED]),
    }

    groups = _group(states, {}, 0, 0, ["/demos/shared.dem"])

    orders = {target[0]: target[2] for target in groups[0][1]}
    assert orders == {0: 0, 1: 1}


def test_group_worker_reuses_the_single_player_path_for_one_target(monkeypatch):
    calls = []

    def fake_single(steamid, demo_order, file_order, dem_file):
        calls.append((steamid, demo_order, file_order, dem_file))
        return {
            "records": [{"round_id": 1}],
            "combat_stats": {"kd": 1.0},
            "demo_order": demo_order,
            "file_order": file_order,
            "dem_file": dem_file,
        }

    monkeypatch.setattr(pipeline, "_parse_fast_demo_worker", fake_single)

    result = pipeline._parse_fast_demo_group_worker(
        "/demos/shared.dem",
        ((7, "76561198000000001", 2, 0, "/demos/shared.dem"),),
    )

    assert calls == [("76561198000000001", 2, 0, "/demos/shared.dem")]
    assert len(result) == 1
    assert result[0]["player_index"] == 7
    assert result[0]["demo_order"] == 2
    assert result[0]["records"] == [{"round_id": 1}]


def test_group_worker_decodes_once_for_several_targets(monkeypatch):
    calls = {"multi": 0, "combat": 0, "steamids": None}

    def fake_multi(dem_file, steamids):
        calls["multi"] += 1
        calls["steamids"] = list(steamids)
        records = {
            str(steamid): [{"match_code": dem_file, "sid": steamid}]
            for steamid in steamids
        }
        return records, object(), object(), {steamid: {} for steamid in steamids}

    def fake_combat(parser, events, steamids, classified):
        calls["combat"] += 1
        return {str(steamid): {"kd": 1.5} for steamid in steamids}

    monkeypatch.setattr(pipeline.parse, "parse_demo_multi", fake_multi)
    monkeypatch.setattr(pipeline.combat, "parse_combat_stats_multi", fake_combat)

    targets = (
        (0, "76561198000000001", 0, 0, "/demos/shared.dem"),
        (1, "76561198000000002", 1, 0, "/demos/shared.dem"),
    )
    result = pipeline._parse_fast_demo_group_worker("/demos/shared.dem", targets)

    assert calls["multi"] == 1
    assert calls["combat"] == 1
    assert calls["steamids"] == ["76561198000000001", "76561198000000002"]
    assert [entry["player_index"] for entry in result] == [0, 1]
    assert [entry["demo_order"] for entry in result] == [0, 1]
    assert result[0]["records"] == [
        {"match_code": "/demos/shared.dem", "sid": "76561198000000001"}
    ]
    assert result[1]["combat_stats"] == {"kd": 1.5}


def test_group_worker_isolates_a_failed_shared_decode(monkeypatch):
    def boom(dem_file, steamids):
        raise RuntimeError("corrupt demo")

    monkeypatch.setattr(pipeline.parse, "parse_demo_multi", boom)
    monkeypatch.setattr(
        pipeline.combat,
        "parse_combat_stats_multi",
        lambda *args: pytest.fail("combat must not run after a failed decode"),
    )

    targets = (
        (0, "76561198000000001", 0, 0, "/demos/shared.dem"),
        (1, "76561198000000002", 1, 0, "/demos/shared.dem"),
    )
    result = pipeline._parse_fast_demo_group_worker("/demos/shared.dem", targets)

    assert len(result) == 2
    assert [entry["records"] for entry in result] == [[], []]
    assert [entry["combat_stats"] for entry in result] == [None, None]
    assert [entry["player_index"] for entry in result] == [0, 1]
