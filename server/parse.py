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
    Two kept types, judged on the target's *own* freeze-end equip value:
      - Pistol = first round of each half-segment (side flips into a new half).
      - Buy    = non-pistol with personal equip >= EQ_BUY_MIN.
    Non-pistol rounds with personal equip < EQ_BUY_MIN are dropped: rtype=None
    (the round still carries its real side so half-segment tracking is unbroken).
    Returns side+rtype per round."""
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
        prev_side = side
        if r["official_num"] == pistol_num[side]:
            rtype = "Pistol"                  # always kept, regardless of equip floor
        else:
            personal_eq = tgt["current_equip_value"].iloc[0]
            rtype = "Buy" if personal_eq >= config.EQ_BUY_MIN else None
        result.append({**r, "side": side, "rtype": rtype})
    return result


def parse_positions(parser, classified, target_steamid):
    """Sample target's X/Y every SAMPLE_EVERY ticks across [fe, fe+WINDOW_S]
    for each classified round (side != None). Returns per-round path lists."""
    sid = str(target_steamid)
    active = [r for r in classified if r["side"] and r["rtype"]]
    if not active:
        return []
    span = config.WINDOW_S * config.TICK_RATE
    sample_ticks, tick_round = [], {}
    for r in active:
        for t in range(r["fe_tick"], min(r["fe_tick"] + span, r["end_tick"]), config.SAMPLE_EVERY):
            sample_ticks.append(t); tick_round[t] = r["official_num"]
    if not sample_ticks:
        return []
    df = parser.parse_ticks(["X", "Y", "steamid"], ticks=sample_ticks)
    if not isinstance(df, pd.DataFrame):
        df = pd.DataFrame(df)
    df["steamid"] = df["steamid"].astype(str)
    df = df[df["steamid"] == sid].copy()
    df["X"] = pd.to_numeric(df["X"], errors="coerce")
    df["Y"] = pd.to_numeric(df["Y"], errors="coerce")
    df = df[np.isfinite(df["X"]) & np.isfinite(df["Y"])].copy()
    if df.empty:
        return []
    df["official_num"] = df["tick"].map(tick_round)
    fe_by_num = {r["official_num"]: r["fe_tick"] for r in active}
    meta_by_num = {r["official_num"]: r for r in active}
    out = []
    for num, grp in df.groupby("official_num"):
        grp = grp.sort_values("tick")
        fe = fe_by_num[num]
        path = [[round((int(t) - fe) / config.TICK_RATE, 3), float(x), float(y)]
                for t, x, y in zip(grp["tick"], grp["X"], grp["Y"])]
        m = meta_by_num[num]
        out.append({"official_num": int(num), "side": m["side"],
                    "rtype": m["rtype"], "path": path})
    return out


_PROJ_TYPE = {
    "CSmokeGrenadeProjectile": "smoke",
    "CFlashbangProjectile": "flash",
    "CHEGrenadeProjectile": "he",
    "CMolotovProjectile": "molotov",
    "CDecoyProjectile": "decoy",
}
_DUR = {"smoke": 18.0, "molotov": 7.0, "flash": 0.5, "he": 0.3, "decoy": 15.0}

def parse_grenades_for_rounds(parser, classified, target_steamid):
    sid = str(target_steamid)
    active = [r for r in classified if r["side"] and r["rtype"]]
    if not active:
        return {}
    g = parser.parse_grenades()
    if not isinstance(g, pd.DataFrame) or g.empty:
        return {}
    g = g.copy()
    g["steamid"] = g["steamid"].astype(str)
    g = g[(g["steamid"] == sid) & g["grenade_type"].isin(_PROJ_TYPE.keys())
          & g["x"].notna() & g["y"].notna()]
    if g.empty:
        return {}
    span = config.WINDOW_S * config.TICK_RATE
    out = {r["official_num"]: [] for r in active}
    # assign each projectile-tick row to a round window, then group per entity
    for r in active:
        lo, hi = r["fe_tick"], r["fe_tick"] + span
        win = g[(g["tick"] >= lo) & (g["tick"] < hi)]
        if win.empty:
            continue
        for eid, grp in win.groupby("grenade_entity_id"):
            grp = grp.sort_values("tick")
            gtype = _PROJ_TYPE[grp["grenade_type"].iloc[0]]
            arc = [[round((int(t) - lo) / config.TICK_RATE, 3), float(x), float(y)]
                   for t, x, y in zip(grp["tick"], grp["x"], grp["y"])]
            # Projectile entities keep reporting their resting position long after
            # they land (smoke ~18s, decoy while beeping). Trim that stationary
            # tail so land_t is the real landing moment, not entity despawn.
            li = _landing_index(arc)
            arc = arc[:li + 1]
            throw_t, land_t = arc[0][0], arc[-1][0]
            land = [arc[-1][1], arc[-1][2]]
            expire_t = round(land_t + _DUR[gtype], 3)
            out[r["official_num"]].append(
                {"type": gtype, "throw_t": throw_t, "land_t": land_t,
                 "arc": arc, "land": land, "expire_t": expire_t})
    return out

def parse_deaths_for_rounds(evts, classified, target_steamid):
    """Per-round death time of the target (seconds from freeze_end), in-window
    only. Dead players keep reporting a frozen position to the window end, so
    death must come from player_death events. Returns {official_num: death_t}."""
    sid = str(target_steamid)
    active = [r for r in classified if r["side"] and r["rtype"]]
    out = {}
    dd = evts.get("player_death")
    if dd is None:
        return out
    if not isinstance(dd, pd.DataFrame):
        dd = pd.DataFrame(dd)
    if dd.empty or "user_steamid" not in dd.columns:
        return out
    dd = dd.copy()
    dd["user_steamid"] = dd["user_steamid"].astype(str)
    span = config.WINDOW_S * config.TICK_RATE
    for r in active:
        lo = r["fe_tick"]
        d = dd[(dd["user_steamid"] == sid) & (dd["tick"] >= lo) & (dd["tick"] < lo + span)]
        if not d.empty:
            out[r["official_num"]] = round((int(d["tick"].min()) - lo) / config.TICK_RATE, 3)
    return out

def _landing_index(arc):
    """Index of the projectile's resting point — the last sample where it still
    moved. Projectile entities linger stationary after landing; everything past
    this index is that stationary tail."""
    last_moving = 0
    for i in range(1, len(arc)):
        dx = arc[i][1] - arc[i - 1][1]
        dy = arc[i][2] - arc[i - 1][2]
        if (dx * dx + dy * dy) ** 0.5 > 3.0:
            last_moving = i
    return last_moving

def parse_demo(path, target_steamid):
    """Full single-demo parse: returns merged per-round dicts with path+grenades."""
    from demoparser2 import DemoParser
    try:
        p = DemoParser(path)
        evts = dict(p.parse_events(
            ["round_freeze_end","round_announce_match_start","round_end","player_death"],
            other=["tick"]))
    except Exception as e:
        log.warning(f"parse_demo failed {path}: {e}")
        return []
    rounds = get_round_table(evts)
    if not rounds:
        return []
    classified = classify_rounds(p, rounds, {str(target_steamid)})
    positions = parse_positions(p, classified, target_steamid)
    nades = parse_grenades_for_rounds(p, classified, target_steamid)
    deaths = parse_deaths_for_rounds(evts, classified, target_steamid)
    out = []
    for r in positions:
        out.append({"side": r["side"], "rtype": r["rtype"],
                    "official_num": r["official_num"], "path": r["path"],
                    "grenades": nades.get(r["official_num"], []),
                    "death_t": deaths.get(r["official_num"])})
    return out
