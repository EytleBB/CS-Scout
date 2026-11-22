"""Round classification on synthetic frames.

These tests deliberately avoid a real ``.dem`` file: the classifier only needs a
tick frame, so it can be verified without the 328 MB private fixture that the
integration tests skip on.
"""

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import parse  # noqa: E402

SID = "76561198348200662"
OTHER = "76561198000000002"

COLUMNS = ["tick", "steamid", "team_name", "current_equip_value"]


def _frame(rows):
    return pd.DataFrame(rows, columns=COLUMNS)


def _rounds(count, *, start=100, step=200):
    return [
        {"official_num": index + 1, "fe_tick": start + index * step,
         "end_tick": start + index * step + 100}
        for index in range(count)
    ]


def test_first_round_is_pistol_and_rich_round_is_buy():
    rounds = _rounds(2)
    frame = _frame([
        (100, SID, "CT", 800),
        (300, SID, "CT", 4200),
    ])

    classified = parse.classify_rounds_from_frame(rounds, frame, {SID})

    assert [item["rtype"] for item in classified] == ["Pistol", "Buy"]
    assert [item["side"] for item in classified] == ["CT", "CT"]


def test_side_flip_starts_a_new_pistol_segment():
    rounds = _rounds(3)
    frame = _frame([
        (100, SID, "CT", 800),
        (300, SID, "CT", 4200),
        (500, SID, "T", 3300),
    ])

    classified = parse.classify_rounds_from_frame(rounds, frame, {SID})

    assert [item["rtype"] for item in classified] == ["Pistol", "Buy", "Pistol"]
    assert [item["side"] for item in classified] == ["CT", "CT", "T"]


def test_low_equipment_round_is_dropped_but_keeps_its_side():
    rounds = _rounds(3)
    frame = _frame([
        (100, SID, "CT", 800),
        (300, SID, "CT", 1500),
        (500, SID, "CT", 2600),
    ])

    classified = parse.classify_rounds_from_frame(rounds, frame, {SID})

    assert [item["rtype"] for item in classified] == ["Pistol", None, "Buy"]
    # The dropped round still carries its side so half tracking stays intact.
    assert classified[1]["side"] == "CT"


def test_missing_snapshot_yields_no_side_and_does_not_invent_a_pistol():
    rounds = _rounds(3)
    # Only rounds 1 and 3 have a freeze-end snapshot for the target.
    frame = _frame([
        (100, SID, "CT", 800),
        (500, SID, "CT", 4000),
    ])

    classified = parse.classify_rounds_from_frame(rounds, frame, {SID})

    assert [item["side"] for item in classified] == ["CT", None, "CT"]
    # The gap must not be read as a side flip, so round 3 stays a normal Buy
    # instead of restarting a pistol segment.
    assert [item["rtype"] for item in classified] == ["Pistol", None, "Buy"]


def test_rows_for_other_players_are_ignored():
    rounds = _rounds(1)
    frame = _frame([
        (100, OTHER, "T", 4000),
        (100, SID, "CT", 900),
    ])

    classified = parse.classify_rounds_from_frame(rounds, frame, {SID})

    assert classified[0]["side"] == "CT"
    assert classified[0]["rtype"] == "Pistol"


def test_empty_frame_drops_sides_without_raising():
    rounds = _rounds(2)

    classified = parse.classify_rounds_from_frame(rounds, _frame([]), {SID})

    assert [item["side"] for item in classified] == [None, None]
    assert [item["rtype"] for item in classified] == [None, None]
