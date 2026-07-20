import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import combat
import config
import parse


def _round(number, freeze_tick):
    return {
        "official_num": number,
        "fe_tick": freeze_tick,
        "end_tick": freeze_tick + 64,
    }


class ClassificationParser:
    def parse_ticks(self, fields, ticks):
        return pd.DataFrame([
            # Pistols are kept even below the equipment floor.
            {"tick": 100, "steamid": 765, "team_name": "CT",
             "current_equip_value": 0},
            {"tick": 100, "steamid": 999, "team_name": "CT",
             "current_equip_value": 9999},
            # Personal value, rather than team average, controls Buy.
            {"tick": 200, "steamid": 765, "team_name": "CT",
             "current_equip_value": 2000},
            {"tick": 200, "steamid": 999, "team_name": "CT",
             "current_equip_value": 0},
            {"tick": 300, "steamid": 765, "team_name": "CT",
             "current_equip_value": 1999},
            {"tick": 300, "steamid": 999, "team_name": "CT",
             "current_equip_value": 9999},
            # Tick 400 is intentionally missing.  It must not reset the half.
            {"tick": 500, "steamid": 765, "team_name": "CT",
             "current_equip_value": "2500"},
            {"tick": 600, "steamid": 765, "team_name": "TERRORIST",
             "current_equip_value": 0},
            {"tick": 700, "steamid": 765, "team_name": "TERRORIST",
             "current_equip_value": "3000"},
            {"tick": 800, "steamid": 765, "team_name": "CT",
             "current_equip_value": None},
        ])


def test_classify_uses_personal_equipment_and_side_segments():
    rounds = [_round(number, number * 100) for number in range(1, 9)]

    result = parse.classify_rounds(ClassificationParser(), rounds, {765})

    assert [r["side"] for r in result] == [
        "CT", "CT", "CT", None, "CT", "T", "T", "CT"]
    assert [r["rtype"] for r in result] == [
        "Pistol", "Buy", None, None, "Buy", "Pistol", "Buy", "Pistol"]


class ReplayParser:
    def __init__(self):
        self.position_ticks = None

    def parse_ticks(self, fields, ticks):
        self.position_ticks = list(ticks)
        return pd.DataFrame([
            {"tick": 0, "steamid": "765", "X": 10.0, "Y": 20.0},
            {"tick": 8, "steamid": "765", "X": 11.0, "Y": 21.0},
            {"tick": 16, "steamid": "765", "X": np.nan, "Y": 22.0},
        ])

    def parse_grenades(self):
        return pd.DataFrame([
            {"tick": 0, "steamid": "765",
             "grenade_type": "CSmokeGrenadeProjectile",
             "grenade_entity_id": 1, "x": 0.0, "y": 0.0},
            {"tick": 8, "steamid": "765",
             "grenade_type": "CSmokeGrenadeProjectile",
             "grenade_entity_id": 1, "x": 4.0, "y": 0.0},
            {"tick": 100, "steamid": "765",
             "grenade_type": "CFlashbangProjectile",
             "grenade_entity_id": 2, "x": 0.0, "y": 0.0},
        ])


def test_replay_parsers_exclude_dropped_rounds():
    classified = [
        {**_round(1, 0), "side": "CT", "rtype": "Buy"},
        {**_round(2, 100), "side": "CT", "rtype": None},
        {**_round(3, 200), "side": None, "rtype": "Buy"},
    ]
    parser = ReplayParser()

    positions = parse.parse_positions(parser, classified, "765")
    grenades = parse.parse_grenades_for_rounds(parser, classified, "765")
    deaths = parse.parse_deaths_for_rounds(
        {"player_death": pd.DataFrame([
            {"tick": 8, "user_steamid": "765"},
            {"tick": 108, "user_steamid": "765"},
        ])},
        classified,
        "765",
    )

    assert all(tick < 64 for tick in parser.position_ticks)
    assert positions == [{
        "official_num": 1,
        "side": "CT",
        "rtype": "Buy",
        "path": [[0.0, 10.0, 20.0], [0.125, 11.0, 21.0]],
    }]
    assert set(grenades) == {1}
    assert len(grenades[1]) == 1
    assert deaths == {1: 0.125}


class CombatParser:
    def parse_events(self, names, other):
        return {
            "round_announce_match_start": pd.DataFrame([{"tick": 50}]),
            "round_freeze_end": pd.DataFrame([
                {"tick": 100}, {"tick": 2000}, {"tick": 4000}]),
            "round_end": pd.DataFrame([
                {"tick": 1900}, {"tick": 3900}, {"tick": 5900}]),
            "player_death": pd.DataFrame(),
        }

    def parse_ticks(self, fields, ticks):
        if "kills_total" in fields:
            return pd.DataFrame([{
                "tick": 5900, "steamid": "765",
                "kills_total": 6, "deaths_total": 4,
            }])
        if "current_equip_value" in fields:
            return pd.DataFrame([
                {"tick": 100, "steamid": "765", "team_name": "CT",
                 "current_equip_value": 0},
                {"tick": 2000, "steamid": "765", "team_name": "CT",
                 "current_equip_value": 500},
                {"tick": 4000, "steamid": "765", "team_name": "TERRORIST",
                 "current_equip_value": 0},
            ])
        if "inventory" in fields:
            return pd.DataFrame([
                {"tick": 100, "steamid": "765", "inventory": ["USP-S"]},
                # AWP is held only in the dropped-economy round.  That round
                # still belongs in both the numerator and denominator.  It is
                # first seen 21 seconds after freeze_end, beyond replay time.
                {"tick": 3344, "steamid": "765",
                 "inventory": np.array(["AWP"])},
                {"tick": 3352, "steamid": "765", "inventory": ["AWP"]},
                {"tick": 4000, "steamid": "765", "inventory": ["Glock-18"]},
                {"tick": 3344, "steamid": "999", "inventory": ["AWP"]},
            ])
        raise AssertionError(f"unexpected fields: {fields}")


def test_parse_combat_counts_awp_hold_across_all_played_rounds(monkeypatch):
    monkeypatch.setattr(combat, "DemoParser", lambda path: CombatParser())

    stats = combat.parse_combat_stats("fake.dem", "765")

    assert stats == {"kd": 1.5, "awp_rounds": 1, "total_rounds": 3}


def test_runtime_window_remains_the_deliberate_20_second_contract():
    assert config.WINDOW_S == 20
