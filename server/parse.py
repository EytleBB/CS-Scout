"""Demo parsing for 2.0 replay: round table, side+economy classification,
position sampling, grenade extraction."""
import logging
import numpy as np
import pandas as pd
import config

log = logging.getLogger("parse")

def get_round_table(evts):
    match_tick = int(evts["round_announce_match_start"]["tick"].iloc[0])
    fe_all = evts["round_freeze_end"]["tick"].sort_values().reset_index(drop=True)
    re_all = evts["round_end"]["tick"].sort_values().reset_index(drop=True)
    real_fe = fe_all[fe_all >= match_tick].reset_index(drop=True)
    rounds = []
    for i, fe_tick in enumerate(real_fe):
        later = re_all[re_all > fe_tick]
        end_tick = int(later.iloc[0]) if not later.empty else int(fe_tick) + 115 * config.TICK_RATE
        rounds.append({"official_num": i + 1, "fe_tick": int(fe_tick), "end_tick": end_tick})
    return rounds

def classify_rounds(parser, rounds, target_sids):
    """Classify each round's side (for the target) and economy type.
    Pistol = first round of each half (each time target's side flips into a new
    half-start). CT and T both classified. Returns side+rtype per round."""
    if not rounds:
        return []
    fe_ticks = [r["fe_tick"] for r in rounds]
    df = parser.parse_ticks(["steamid", "team_name", "current_equip_value"], ticks=fe_ticks)
    if not isinstance(df, pd.DataFrame):
        df = pd.DataFrame(df)
    df["steamid"] = df["steamid"].astype(str)
    grouped = {tick: g for tick, g in df.groupby("tick")}

    result = []
    prev_side = None
    pistol_num = {"CT": None, "T": None}
    for r in rounds:
        g = grouped.get(r["fe_tick"])
        if g is None or g.empty:
            result.append({**r, "side": None, "rtype": None}); prev_side = None; continue
        tgt = g[g["steamid"].isin(target_sids)]
        if tgt.empty:
            result.append({**r, "side": None, "rtype": None}); prev_side = None; continue
        side = "CT" if tgt["team_name"].iloc[0] == "CT" else "T"
        if side != prev_side:                 # entering a new half-segment on this side
            pistol_num[side] = r["official_num"]
        if r["official_num"] == pistol_num[side]:
            rtype = "Pistol"
        else:
            avg_eq = g[g["team_name"] == tgt["team_name"].iloc[0]]["current_equip_value"].mean()
            rtype = "Full" if avg_eq >= config.EQ_FULL_BUY else "Eco"
        prev_side = side
        result.append({**r, "side": side, "rtype": rtype})
    return result
