import os, sys, pytest
import pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import parse

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
    # pick the steamid that throws the most projectiles
    g = p.parse_grenades()
    g = g[g["grenade_type"].str.contains("Projectile") & g["x"].notna()]
    sid = str(g["steamid"].astype(str).value_counts().index[0])
    classified = parse.classify_rounds(p, rounds, {sid})
    return p, classified, sid

def test_grenades_shape(ctx):
    p, classified, sid = ctx
    gr = parse.parse_grenades_for_rounds(p, classified, sid)
    all_nades = [n for lst in gr.values() for n in lst]
    assert all_nades, "expected at least one grenade for the heaviest thrower"
    for n in all_nades:
        assert n["type"] in {"smoke","flash","he","molotov","decoy"}
        assert n["land_t"] >= n["throw_t"] >= 0
        assert len(n["arc"]) >= 1
        assert len(n["land"]) == 2
        assert n["expire_t"] >= n["land_t"]

def test_smoke_persists(ctx):
    p, classified, sid = ctx
    gr = parse.parse_grenades_for_rounds(p, classified, sid)
    smokes = [n for lst in gr.values() for n in lst if n["type"] == "smoke"]
    if smokes:  # demo may or may not have target smokes in-window
        assert all(s["expire_t"] - s["land_t"] > 10 for s in smokes)
