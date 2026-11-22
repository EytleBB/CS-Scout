"""Multi-target combat statistics share one scoreboard and one inventory pass."""

import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import combat  # noqa: E402

A = "76561198348200662"
B = "76561198000000002"
C = "76561198000000003"


class FakeParser:
    """Records every tick query so the test can prove they are not repeated."""

    def __init__(self, scoreboard, inventory):
        self._scoreboard = scoreboard
        self._inventory = inventory
        self.calls = []

    def parse_ticks(self, fields, ticks):
        self.calls.append((tuple(fields), tuple(ticks)))
        if "inventory" in fields:
            return self._inventory
        return self._scoreboard


def _events(rounds):
    return {"round_end": pd.DataFrame(
        [{"tick": r["end_tick"]} for r in rounds]
    )}


def _rounds(count=2):
    return [
        {"official_num": index + 1, "fe_tick": 100 + index * 200,
         "end_tick": 200 + index * 200}
        for index in range(count)
    ]


def _classified(sids, rounds, side="CT"):
    return {
        sid: [{**r, "side": side, "rtype": "Buy"} for r in rounds]
        for sid in sids
    }


def test_multi_target_combat_uses_two_tick_passes_total():
    rounds = _rounds()
    scoreboard = pd.DataFrame(
        [{"steamid": A, "kills_total": 20, "deaths_total": 10},
         {"steamid": B, "kills_total": 5, "deaths_total": 10},
         {"steamid": C, "kills_total": 9, "deaths_total": 3}]
    )
    inventory = pd.DataFrame([
        {"tick": 100, "steamid": A, "inventory": ["AWP", "Knife"]},
        {"tick": 300, "steamid": B, "inventory": ["AK-47", "Knife"]},
        {"tick": 300, "steamid": C, "inventory": ["AWP"]},
    ])
    parser = FakeParser(scoreboard, inventory)

    stats = combat.parse_combat_stats_multi(
        parser, _events(rounds), [A, B, C], _classified([A, B, C], rounds)
    )

    assert len(parser.calls) == 2, "scoreboard and inventory must each pass once"
    assert stats[A]["kd"] == 2.0
    assert stats[B]["kd"] == 0.5
    assert stats[C]["kd"] == 3.0
    assert stats[A]["awp_rounds"] == 1
    assert stats[B]["awp_rounds"] == 0
    assert stats[C]["awp_rounds"] == 1
    assert all(item["total_rounds"] == 2 for item in stats.values())


def test_multi_target_matches_single_target_results():
    rounds = _rounds(3)
    scoreboard = pd.DataFrame(
        [{"steamid": A, "kills_total": 7, "deaths_total": 0}]
    )
    inventory = pd.DataFrame([
        {"tick": 100, "steamid": A, "inventory": ["AWP"]},
        {"tick": 500, "steamid": A, "inventory": ["AWP"]},
    ])
    classified = _classified([A], rounds)

    single = combat.parse_combat_stats_from_context(
        FakeParser(scoreboard, inventory), _events(rounds), A,
        classified=classified[A],
    )
    multi = combat.parse_combat_stats_multi(
        FakeParser(scoreboard, inventory), _events(rounds), [A], classified
    )[A]

    assert multi == single


def test_multi_target_handles_missing_scoreboard_and_inventory():
    rounds = _rounds()

    class Exploding:
        def parse_ticks(self, fields, ticks):
            raise RuntimeError("decode failed")

    stats = combat.parse_combat_stats_multi(
        Exploding(), _events(rounds), [A, B], _classified([A, B], rounds)
    )

    assert stats[A] == {"kd": 0.0, "awp_rounds": 0, "total_rounds": 2}
    assert stats[B] == {"kd": 0.0, "awp_rounds": 0, "total_rounds": 2}


def test_empty_target_list_returns_empty_mapping():
    assert combat.parse_combat_stats_multi(FakeParser(None, None), _events([]), [], {}) == {}
