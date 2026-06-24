"""Combat stats: global K/D (scoreboard) + CT-side AWP rate."""
import logging
import numpy as np
import pandas as pd
from demoparser2 import DemoParser
import parse

log = logging.getLogger("combat")
SNIPER_EVENT_NAMES = {"awp", "ssg08", "g3sg1", "scar20"}

def parse_combat_stats(path, steamid):
    sid = str(steamid)
    try:
        p = DemoParser(path)
        evts = dict(p.parse_events(["round_freeze_end","round_announce_match_start",
            "round_end","player_death"], other=["tick"]))
    except Exception as e:
        log.warning(f"combat parse failed {path}: {e}")
        return None

    kd_val = 0.0
    re_df = evts.get("round_end")
    if re_df is not None:
        if not isinstance(re_df, pd.DataFrame):
            re_df = pd.DataFrame(re_df)
        if not re_df.empty:
            last = int(re_df["tick"].max())
            try:
                sb = pd.DataFrame(p.parse_ticks(["kills_total","deaths_total","steamid"], ticks=[last]))
                sb["steamid"] = sb["steamid"].astype(str)
                row = sb[sb["steamid"] == sid]
                if not row.empty:
                    k, d = int(row["kills_total"].iloc[0]), int(row["deaths_total"].iloc[0])
                    kd_val = round(k / max(d, 1), 2)
            except Exception as e:
                log.warning(f"scoreboard parse failed {path}: {e}")

    rounds = parse.get_round_table(evts)
    classified = parse.classify_rounds(p, rounds, {sid}) if rounds else []
    ct_rounds = [r for r in classified if r["side"] == "CT"]
    if not ct_rounds:
        return {"kd": kd_val, "ct_kills": 0, "awp_kills": 0}

    fe = np.array([r["fe_tick"] for r in ct_rounds])
    end = np.array([r["end_tick"] for r in ct_rounds])
    def in_ct(ticks):
        t = ticks.values[:, None]
        m = (t >= fe) & (t <= end)
        return m.any(axis=1)

    ct_kills = awp_kills = 0
    dd = evts.get("player_death")
    if dd is not None:
        if not isinstance(dd, pd.DataFrame):
            dd = pd.DataFrame(dd)
        if not dd.empty and "attacker_steamid" in dd.columns:
            dd["attacker_steamid"] = dd["attacker_steamid"].astype(str)
            dd["user_steamid"] = dd["user_steamid"].astype(str)
            dd = dd[in_ct(dd["tick"])]
            kills = dd[(dd["attacker_steamid"] == sid) &
                       (dd["attacker_steamid"] != dd["user_steamid"])]
            ct_kills = len(kills)
            if "weapon" in kills.columns:
                awp_kills = int(kills["weapon"].isin(SNIPER_EVENT_NAMES).sum())
    return {"kd": kd_val, "ct_kills": ct_kills, "awp_kills": awp_kills}

def aggregate_combat_stats(stats_list):
    valid = [s for s in stats_list if s is not None]
    if not valid:
        return None
    kd = round(sum(s["kd"] for s in valid) / len(valid), 2)
    tk = sum(s["ct_kills"] for s in valid)
    ak = sum(s["awp_kills"] for s in valid)
    awp_rate = round(ak / tk * 100, 1) if tk > 0 else 0.0
    return {"kd": kd, "awp_rate": awp_rate}
