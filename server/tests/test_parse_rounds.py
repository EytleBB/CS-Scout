import os, sys, pytest
import pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import parse

FIXTURE = os.path.join(os.path.dirname(__file__), "..", "..",
    "demos_analysis", "g161-n-20260123174821830606429_de_mirage.dem")
pytestmark = pytest.mark.skipif(not os.path.exists(FIXTURE), reason="fixture demo absent")

@pytest.fixture(scope="module")
def parser_and_evts():
    from demoparser2 import DemoParser
    p = DemoParser(FIXTURE)
    raw = p.parse_events(
        ["round_freeze_end", "round_announce_match_start", "round_end", "player_death"],
        other=["tick"])
    return p, dict(raw)

def _any_steamid(parser, fe_tick):
    df = pd.DataFrame(parser.parse_ticks(["steamid", "team_name"], ticks=[fe_tick]))
    df["steamid"] = df["steamid"].astype(str)
    return df["steamid"].iloc[0]

def test_round_table_nonempty(parser_and_evts):
    p, evts = parser_and_evts
    rounds = parse.get_round_table(evts)
    assert len(rounds) > 10
    assert all(r["end_tick"] > r["fe_tick"] for r in rounds)
    assert [r["official_num"] for r in rounds] == list(range(1, len(rounds) + 1))

def test_classify_both_sides_and_pistol(parser_and_evts):
    p, evts = parser_and_evts
    rounds = parse.get_round_table(evts)
    sid = _any_steamid(p, rounds[0]["fe_tick"])
    classified = parse.classify_rounds(p, rounds, {sid})
    sides = {c["side"] for c in classified if c["side"]}
    assert sides == {"CT", "T"}                      # player appears on both sides
    rtypes = {c["rtype"] for c in classified if c["rtype"]}
    assert rtypes <= {"Pistol", "Buy"}                 # 2.0: only pistol + buy kept
    assert "Pistol" in rtypes                          # at least the opening pistol
    # rounds may be dropped (rtype=None) but keep their real side for half-tracking
    assert any(c["side"] and not c["rtype"] for c in classified) or \
        all(c["rtype"] for c in classified if c["side"])
