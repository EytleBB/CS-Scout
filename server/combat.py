"""Combat stats: global K/D (scoreboard) + AWP-hold rate (share of rounds the
player held an AWP, both sides)."""
import logging
import pandas as pd
from demoparser2 import DemoParser
import config
import parse

log = logging.getLogger("combat")

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
    # Denominator: every round the player was present for (both sides, any economy).
    played = [r for r in classified if r["side"]]
    awp_rounds = _count_awp_hold_rounds(p, played, sid)
    return {"kd": kd_val, "awp_rounds": awp_rounds, "total_rounds": len(played)}

def _count_awp_hold_rounds(parser, played, sid):
    """Number of `played` rounds where the player ever held an AWP. Samples the
    `inventory` tick field (weapon display names) across each round window."""
    if not played:
        return 0
    span = config.WINDOW_S * config.TICK_RATE
    sample_ticks, tick_round = [], {}
    for r in played:
        for t in range(r["fe_tick"], min(r["fe_tick"] + span, r["end_tick"]), config.SAMPLE_EVERY):
            sample_ticks.append(t); tick_round[t] = r["official_num"]
    if not sample_ticks:
        return 0
    df = parser.parse_ticks(["inventory", "steamid"], ticks=sample_ticks)
    if not isinstance(df, pd.DataFrame):
        df = pd.DataFrame(df)
    df["steamid"] = df["steamid"].astype(str)
    df = df[df["steamid"] == sid]
    held = set()
    for tick, inv in zip(df["tick"], df["inventory"]):
        if inv is not None and "AWP" in inv:
            held.add(tick_round[int(tick)])
    return len(held)

def aggregate_combat_stats(stats_list):
    valid = [s for s in stats_list if s is not None]
    if not valid:
        return None
    kd = round(sum(s["kd"] for s in valid) / len(valid), 2)
    tr = sum(s["total_rounds"] for s in valid)
    ar = sum(s["awp_rounds"] for s in valid)
    awp_rate = round(ar / tr * 100, 1) if tr > 0 else 0.0
    return {"kd": kd, "awp_rate": awp_rate}
