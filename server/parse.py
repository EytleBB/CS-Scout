"""Demo parsing for 2.0 replay: round table, side+economy classification,
position sampling, grenade extraction."""
import logging
import numpy as np
import pandas as pd
import config

log = logging.getLogger("parse")


def _event_ticks(evts, event_name):
    """Return sorted, finite integer ticks for a required event."""
    if not hasattr(evts, "get"):
        log.warning("round table events are not a mapping")
        return None
    raw = evts.get(event_name)
    if raw is None:
        log.warning("round table is missing required event %s", event_name)
        return None
    try:
        df = raw if isinstance(raw, pd.DataFrame) else pd.DataFrame(raw)
    except (TypeError, ValueError) as exc:
        log.warning("invalid %s event table: %s", event_name, exc)
        return None
    if df.empty or "tick" not in df.columns:
        log.warning("%s has no usable tick column", event_name)
        return None

    ticks = pd.to_numeric(df["tick"], errors="coerce")
    max_tick = np.iinfo(np.int64).max
    ticks = ticks[np.isfinite(ticks) & (ticks >= 0) & (ticks <= max_tick)]
    if ticks.empty:
        log.warning("%s has no finite ticks", event_name)
        return None
    return ticks.astype("int64").sort_values().drop_duplicates().reset_index(drop=True)


def get_round_table(evts):
    match_ticks = _event_ticks(evts, "round_announce_match_start")
    fe_all = _event_ticks(evts, "round_freeze_end")
    re_all = _event_ticks(evts, "round_end")
    if match_ticks is None or fe_all is None or re_all is None:
        return []

    match_tick = int(match_ticks.iloc[0])
    real_fe = fe_all[fe_all >= match_tick].reset_index(drop=True)
    if real_fe.empty:
        log.warning("round table has no freeze-end event after match start")
        return []
    rounds = []
    for i, fe_tick in enumerate(real_fe):
        later = re_all[re_all > fe_tick]
        end_tick = int(later.iloc[0]) if not later.empty else int(fe_tick) + 115 * config.TICK_RATE
        rounds.append({"official_num": i + 1, "fe_tick": int(fe_tick), "end_tick": end_tick})
    return rounds

def classify_rounds(parser, rounds, target_sids):
    """Classify each round's side (for the target) and economy type.
    Pistol is the first round of each observed half-segment.  Other rounds are
    kept as Buy only when the target's own freeze-end equipment value reaches
    EQ_BUY_MIN.  Low-economy rounds retain their side but have rtype=None so
    they cannot disturb half tracking and downstream parsers can drop them.
    """
    if not rounds:
        return []
    target_sids = {str(sid) for sid in target_sids}
    fe_ticks = [r["fe_tick"] for r in rounds]
    df = parser.parse_ticks(["steamid", "team_name", "current_equip_value"], ticks=fe_ticks)
    if not isinstance(df, pd.DataFrame):
        df = pd.DataFrame(df)
    required = {"tick", "steamid", "team_name", "current_equip_value"}
    if df.empty or not required.issubset(df.columns):
        return [{**r, "side": None, "rtype": None} for r in rounds]
    df["steamid"] = df["steamid"].astype(str)
    grouped = {tick: g for tick, g in df.groupby("tick")}

    result = []
    prev_side = None
    for r in rounds:
        g = grouped.get(r["fe_tick"])
        if g is None or g.empty:
            result.append({**r, "side": None, "rtype": None})
            continue
        tgt = g[g["steamid"].isin(target_sids)]
        if tgt.empty:
            result.append({**r, "side": None, "rtype": None})
            continue
        team_name = str(tgt["team_name"].iloc[0]).upper()
        side = {"CT": "CT", "T": "T", "TERRORIST": "T"}.get(team_name)
        if side is None:
            result.append({**r, "side": None, "rtype": None})
            continue
        if side != prev_side:
            # The first valid target snapshot, and every later side flip, starts
            # a half-segment.  Missing snapshots do not invent extra pistols.
            rtype = "Pistol"
        else:
            personal_eq = pd.to_numeric(
                tgt["current_equip_value"].iloc[0], errors="coerce")
            rtype = (
                "Buy"
                if pd.notna(personal_eq) and personal_eq >= config.EQ_BUY_MIN
                else None
            )
        prev_side = side
        result.append({**r, "side": side, "rtype": rtype})
    return result


def parse_positions(parser, classified, target_steamid):
    """Sample target's X/Y every SAMPLE_EVERY ticks across [fe, fe+WINDOW_S]
    for each kept classified round. Returns per-round path lists."""
    sid = str(target_steamid)
    active = [r for r in classified if r.get("side") and r.get("rtype")]
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
    required = {"tick", "steamid", "X", "Y"}
    if df.empty or not required.issubset(df.columns):
        return []
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
    active = [r for r in classified if r.get("side") and r.get("rtype")]
    if not active:
        return {}
    g = parser.parse_grenades()
    try:
        if not isinstance(g, pd.DataFrame):
            g = pd.DataFrame(g)
    except (TypeError, ValueError):
        return {}
    required = {"tick", "steamid", "grenade_type", "grenade_entity_id", "x", "y"}
    if g.empty or not required.issubset(g.columns):
        return {}
    g = g.copy()
    g["steamid"] = g["steamid"].astype(str)
    for column in ("tick", "x", "y"):
        g[column] = pd.to_numeric(g[column], errors="coerce")
    g = g[(g["steamid"] == sid) & g["grenade_type"].isin(_PROJ_TYPE.keys())
          & np.isfinite(g["tick"]) & np.isfinite(g["x"]) & np.isfinite(g["y"])]
    if g.empty:
        return {}
    span = config.WINDOW_S * config.TICK_RATE
    out = {r["official_num"]: [] for r in active}
    # assign each projectile-tick row to a round window, then group per entity
    for r in active:
        lo = r["fe_tick"]
        hi = min(lo + span, r["end_tick"])
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
    active = [r for r in classified if r.get("side") and r.get("rtype")]
    out = {}
    if not active or not hasattr(evts, "get"):
        return out
    dd = evts.get("player_death")
    if dd is None:
        return out
    try:
        if not isinstance(dd, pd.DataFrame):
            dd = pd.DataFrame(dd)
    except (TypeError, ValueError):
        return out
    required = {"tick", "user_steamid"}
    if dd.empty or not required.issubset(dd.columns):
        return out
    dd = dd.copy()
    dd["user_steamid"] = dd["user_steamid"].astype(str)
    dd["tick"] = pd.to_numeric(dd["tick"], errors="coerce")
    dd = dd[np.isfinite(dd["tick"])]
    if dd.empty:
        return out
    span = config.WINDOW_S * config.TICK_RATE
    for r in active:
        lo = r["fe_tick"]
        hi = min(lo + span, r["end_tick"])
        d = dd[(dd["user_steamid"] == sid) &
               (dd["tick"] >= lo) & (dd["tick"] < hi)]
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
    try:
        from demoparser2 import DemoParser

        p = DemoParser(path)
        evts = dict(p.parse_events(
            ["round_freeze_end","round_announce_match_start","round_end","player_death"],
            other=["tick"]))
        rounds = get_round_table(evts)
        if not rounds:
            log.warning("parse_demo failed %s: no valid round table", path)
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
    except Exception as exc:
        # A single malformed demo must not abort the pipeline for every player.
        log.warning("parse_demo failed %s: %s", path, exc)
        return []
