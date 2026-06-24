import os, sys, pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import combat

def test_aggregate_math():
    out = combat.aggregate_combat_stats([
        {"kd": 1.0, "awp_rounds": 5, "total_rounds": 10},
        {"kd": 2.0, "awp_rounds": 0, "total_rounds": 10},
    ])
    assert out["kd"] == 1.5
    assert out["awp_rate"] == 25.0    # 5/20 rounds held an AWP

def test_aggregate_empty():
    assert combat.aggregate_combat_stats([None, None]) is None

FIXTURE = os.path.join(os.path.dirname(__file__), "..", "..",
    "demos_analysis", "g161-n-20260123174821830606429_de_mirage.dem")

@pytest.mark.skipif(not os.path.exists(FIXTURE), reason="fixture demo absent")
def test_parse_combat_real():
    from demoparser2 import DemoParser
    import pandas as pd, parse
    p = DemoParser(FIXTURE)
    evts = dict(p.parse_events(["round_freeze_end","round_announce_match_start",
        "round_end","player_death"], other=["tick"]))
    sid = str(pd.DataFrame(p.parse_ticks(["steamid"],
        ticks=[parse.get_round_table(evts)[0]["fe_tick"]]))["steamid"].astype(str).iloc[0])
    s = combat.parse_combat_stats(FIXTURE, sid)
    assert s is not None
    assert isinstance(s["kd"], float) and s["kd"] >= 0
    assert s["total_rounds"] >= 0 and 0 <= s["awp_rounds"] <= s["total_rounds"]
