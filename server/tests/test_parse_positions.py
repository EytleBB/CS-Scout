import os, sys, pytest
import pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import parse
import config

FIXTURE = os.path.join(os.path.dirname(__file__), "..", "..",
    "demos_analysis", "g161-n-20260123174821830606429_de_mirage.dem")
pytestmark = pytest.mark.skipif(not os.path.exists(FIXTURE), reason="fixture demo absent")

@pytest.fixture(scope="module")
def ctx():
    from demoparser2 import DemoParser
    p = DemoParser(FIXTURE)
    evts = dict(p.parse_events(
        ["round_freeze_end","round_announce_match_start","round_end","player_death"],
        other=["tick"]))
    rounds = parse.get_round_table(evts)
    df = pd.DataFrame(p.parse_ticks(["steamid","team_name"], ticks=[rounds[0]["fe_tick"]]))
    sid = str(df["steamid"].astype(str).iloc[0])
    classified = parse.classify_rounds(p, rounds, {sid})
    return p, classified, sid

def test_positions_have_path_within_window(ctx):
    p, classified, sid = ctx
    pos = parse.parse_positions(p, classified, sid)
    assert pos, "expected at least one round of positions"
    for r in pos:
        assert r["side"] in ("CT", "T")
        assert len(r["path"]) >= 2
        ts = [pt[0] for pt in r["path"]]
        assert ts == sorted(ts)
        assert min(ts) >= 0.0 and max(ts) <= config.WINDOW_S + 0.5
        # each point is [t, x, y] of floats
        assert all(len(pt) == 3 for pt in r["path"])

def test_positions_cover_both_sides(ctx):
    p, classified, sid = ctx
    pos = parse.parse_positions(p, classified, sid)
    assert {r["side"] for r in pos} == {"CT", "T"}
