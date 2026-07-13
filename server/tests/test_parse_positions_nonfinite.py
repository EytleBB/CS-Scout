import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import parse


class FakeParser:
    def parse_ticks(self, fields, ticks):
        return pd.DataFrame([
            {"tick": 100, "steamid": "765", "X": 10.0, "Y": 20.0},
            {"tick": 108, "steamid": "765", "X": np.nan, "Y": 21.0},
            {"tick": 200, "steamid": "765", "X": np.nan, "Y": np.nan},
        ])


def test_parse_positions_drops_nonfinite_points_and_empty_rounds():
    classified = [
        {"official_num": 1, "fe_tick": 100, "end_tick": 116,
         "side": "CT", "rtype": "Buy"},
        {"official_num": 2, "fe_tick": 200, "end_tick": 208,
         "side": "T", "rtype": "Pistol"},
    ]

    positions = parse.parse_positions(FakeParser(), classified, "765")

    assert positions == [{
        "official_num": 1,
        "side": "CT",
        "rtype": "Buy",
        "path": [[0.0, 10.0, 20.0]],
    }]
