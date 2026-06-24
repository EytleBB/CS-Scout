import os, sys, pytest
import pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import parse, config

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
    df = pd.DataFrame(p.parse_ticks(["steamid"], ticks=[rounds[0]["fe_tick"]]))
    sid = str(df["steamid"].astype(str).iloc[0])
    classified = parse.classify_rounds(p, rounds, {sid})
    return evts, classified, sid

def test_death_t_within_window(ctx):
    evts, classified, sid = ctx
    deaths = parse.parse_deaths_for_rounds(evts, classified, sid)
    assert deaths, "expected the target to die in at least one round"
    for num, dt in deaths.items():
        assert 0 <= dt <= config.WINDOW_S

def test_parse_demo_includes_death_t(ctx):
    _, _, sid = ctx
    rounds = parse.parse_demo(FIXTURE, sid)
    assert all("death_t" in r for r in rounds)
    assert any(r["death_t"] is not None for r in rounds)   # died at least once
    assert any(r["death_t"] is None for r in rounds)        # survived at least once
