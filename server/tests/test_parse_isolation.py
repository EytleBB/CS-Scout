import logging
import math
import os
import sys
import types

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import parse


def _install_demo_parser(monkeypatch, parser):
    module = types.ModuleType("demoparser2")
    module.DemoParser = lambda path: parser
    monkeypatch.setitem(sys.modules, "demoparser2", module)


class MissingRoundEventParser:
    def parse_events(self, names, other):
        return {
            "round_announce_match_start": pd.DataFrame([{"tick": 50}]),
            "round_freeze_end": pd.DataFrame([{"tick": 100}]),
            # round_end is deliberately absent.
            "player_death": pd.DataFrame(),
        }


def test_parse_demo_isolates_missing_round_events(monkeypatch, caplog):
    _install_demo_parser(monkeypatch, MissingRoundEventParser())

    with caplog.at_level(logging.WARNING, logger="parse"):
        result = parse.parse_demo("missing-events.dem", "765")

    assert result == []
    assert "round_end" in caplog.text
    assert "parse_demo failed missing-events.dem" in caplog.text


class CorruptGrenadeParser:
    def parse_events(self, names, other):
        return {
            "round_announce_match_start": pd.DataFrame([{"tick": 50}]),
            "round_freeze_end": pd.DataFrame([{"tick": 100}]),
            "round_end": pd.DataFrame([{"tick": 200}]),
            "player_death": pd.DataFrame(
                columns=["tick", "user_steamid"]),
        }

    def parse_ticks(self, fields, ticks):
        if "current_equip_value" in fields:
            return pd.DataFrame([{
                "tick": 100,
                "steamid": "765",
                "team_name": "CT",
                "current_equip_value": 0,
            }])
        if "X" in fields:
            return pd.DataFrame([{
                "tick": 100,
                "steamid": "765",
                "X": 1.0,
                "Y": 2.0,
            }])
        raise AssertionError(f"unexpected tick fields: {fields}")

    def parse_grenades(self):
        raise RuntimeError("corrupt grenade payload")


def test_parse_demo_isolates_failure_after_round_parsing(monkeypatch, caplog):
    _install_demo_parser(monkeypatch, CorruptGrenadeParser())

    with caplog.at_level(logging.WARNING, logger="parse"):
        result = parse.parse_demo("corrupt-grenades.dem", "765")

    assert result == []
    assert "corrupt grenade payload" in caplog.text
    assert "parse_demo failed corrupt-grenades.dem" in caplog.text


def test_get_round_table_rejects_missing_and_nonfinite_required_ticks(caplog):
    with caplog.at_level(logging.WARNING, logger="parse"):
        assert parse.get_round_table({}) == []

    assert "required event" in caplog.text

    rounds = parse.get_round_table({
        "round_announce_match_start": pd.DataFrame({"tick": [np.nan, 50]}),
        "round_freeze_end": pd.DataFrame({"tick": [np.inf, 100]}),
        "round_end": pd.DataFrame({"tick": ["bad", 200]}),
    })
    assert rounds == [{"official_num": 1, "fe_tick": 100, "end_tick": 200}]


class NonfiniteGrenadeParser:
    def parse_grenades(self):
        return pd.DataFrame([
            {"tick": 100, "steamid": "765",
             "grenade_type": "CSmokeGrenadeProjectile",
             "grenade_entity_id": 1, "x": 0.0, "y": 0.0},
            {"tick": 108, "steamid": "765",
             "grenade_type": "CSmokeGrenadeProjectile",
             "grenade_entity_id": 1, "x": 4.0, "y": 0.0},
            {"tick": 116, "steamid": "765",
             "grenade_type": "CSmokeGrenadeProjectile",
             "grenade_entity_id": 1, "x": np.inf, "y": 0.0},
            {"tick": 120, "steamid": "765",
             "grenade_type": "CFlashbangProjectile",
             "grenade_entity_id": 2, "x": 1.0, "y": np.nan},
            {"tick": np.inf, "steamid": "765",
             "grenade_type": "CHEGrenadeProjectile",
             "grenade_entity_id": 3, "x": 1.0, "y": 2.0},
        ])


def test_grenade_parser_drops_nonfinite_ticks_and_coordinates():
    classified = [{
        "official_num": 1,
        "fe_tick": 100,
        "end_tick": 200,
        "side": "CT",
        "rtype": "Pistol",
    }]

    result = parse.parse_grenades_for_rounds(
        NonfiniteGrenadeParser(), classified, "765")

    assert set(result) == {1}
    assert len(result[1]) == 1
    smoke = result[1][0]
    assert smoke["type"] == "smoke"
    assert smoke["arc"] == [[0.0, 0.0, 0.0], [0.125, 4.0, 0.0]]
    assert all(math.isfinite(value) for point in smoke["arc"] for value in point)
    assert all(math.isfinite(value) for value in smoke["land"])


def test_death_parser_requires_a_finite_tick_column():
    classified = [{
        "official_num": 1,
        "fe_tick": 100,
        "end_tick": 200,
        "side": "CT",
        "rtype": "Buy",
    }]

    assert parse.parse_deaths_for_rounds(
        {"player_death": pd.DataFrame({"user_steamid": ["765"]})},
        classified,
        "765",
    ) == {}

    result = parse.parse_deaths_for_rounds(
        {"player_death": pd.DataFrame([
            {"tick": np.inf, "user_steamid": "765"},
            {"tick": "108", "user_steamid": "765"},
        ])},
        classified,
        "765",
    )
    assert result == {1: 0.125}
