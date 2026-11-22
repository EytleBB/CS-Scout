"""Combat stats: global K/D and the share of rounds with an AWP held."""
import logging
import pandas as pd
from demoparser2 import DemoParser
import config
import parse

log = logging.getLogger("combat")

def parse_combat_stats(path, steamid):
    """Parse one demo without allowing malformed combat data to abort a run."""
    try:
        return _parse_combat_stats(path, steamid)
    except Exception as exc:
        log.warning("combat parse failed %s: %s", path, exc)
        return None


def _parse_combat_stats(path, steamid):
    sid = str(steamid)
    try:
        p = DemoParser(path)
        evts = dict(p.parse_events(["round_freeze_end","round_announce_match_start",
            "round_end","player_death"], other=["tick"]))
    except Exception as e:
        log.warning(f"combat parse failed {path}: {e}")
        return None

    return parse_combat_stats_from_context(p, evts, sid)


def parse_combat_stats_from_context(parser, events, steamid, classified=None):
    """Calculate combat stats from an existing DemoParser/event pass."""
    p = parser
    evts = events
    sid = str(steamid)

    sb = _scoreboard_frame(p, evts)
    kd_val = _kd_from_frame(sb, sid)

    if classified is None:
        rounds = parse.get_round_table(evts)
        classified = parse.classify_rounds(p, rounds, {sid}) if rounds else []
    # The economy filter is deliberately not applied here: the denominator is
    # every round the player participated in, on both CT and T.
    played = [r for r in classified if r.get("side") in {"CT", "T"}]
    awp_rounds = _count_awp_hold_rounds(p, played, sid)
    return {"kd": kd_val, "awp_rounds": awp_rounds,
            "total_rounds": len(played)}


def _scoreboard_frame(parser, events):
    """Read the final scoreboard once for every target in the demo."""
    re_df = events.get("round_end")
    if re_df is None:
        return None
    if not isinstance(re_df, pd.DataFrame):
        re_df = pd.DataFrame(re_df)
    if re_df.empty or "tick" not in re_df.columns:
        return None
    last = int(re_df["tick"].max())
    try:
        sb = pd.DataFrame(
            parser.parse_ticks(["kills_total", "deaths_total", "steamid"], ticks=[last])
        )
    except Exception as exc:
        log.warning("scoreboard parse failed: %s", exc)
        return None
    required = {"steamid", "kills_total", "deaths_total"}
    if sb.empty or not required.issubset(sb.columns):
        return None
    sb = sb.copy()
    sb["steamid"] = sb["steamid"].astype(str)
    return sb


def _kd_from_frame(sb, sid):
    if sb is None or sb.empty:
        return 0.0
    row = sb[sb["steamid"] == str(sid)]
    if row.empty:
        return 0.0
    try:
        kills = int(row["kills_total"].iloc[0])
        deaths = int(row["deaths_total"].iloc[0])
    except (TypeError, ValueError):
        return 0.0
    return round(kills / max(deaths, 1), 2)


def _awp_sample_ticks(played):
    sample_ticks = []
    tick_round = {}
    for r in played:
        # Combat stats cover the full round, not only the shorter replay window;
        # otherwise an AWP picked up late in a round would be missed.
        for tick in range(r["fe_tick"], r["end_tick"] + 1, config.SAMPLE_EVERY):
            sample_ticks.append(tick)
            tick_round[tick] = r["official_num"]
    return sample_ticks, tick_round


def _inventory_frame(parser, sample_ticks):
    if not sample_ticks:
        return None
    try:
        df = pd.DataFrame(
            parser.parse_ticks(["inventory", "steamid"], ticks=sample_ticks)
        )
    except Exception as exc:
        log.warning("inventory parse failed while calculating AWP rate: %s", exc)
        return None
    required = {"tick", "steamid", "inventory"}
    if df.empty or not required.issubset(df.columns):
        return None
    df = df.copy()
    df["steamid"] = df["steamid"].astype(str)
    return df


def _awp_rounds_from_frame(frame, sid, tick_round):
    if frame is None or frame.empty:
        return 0
    rows = frame[frame["steamid"] == str(sid)]
    held_rounds = set()
    for tick, inventory in zip(rows["tick"], rows["inventory"]):
        try:
            round_num = tick_round.get(int(tick))
        except (TypeError, ValueError, OverflowError):
            continue
        if round_num is not None and _inventory_contains_awp(inventory):
            held_rounds.add(round_num)
    return len(held_rounds)


def _count_awp_hold_rounds(parser, played, sid):
    """Count rounds where ``sid`` held an AWP at any sampled tick."""
    if not played:
        return 0
    sample_ticks, tick_round = _awp_sample_ticks(played)
    return _awp_rounds_from_frame(
        _inventory_frame(parser, sample_ticks), sid, tick_round
    )


def parse_combat_stats_multi(parser, events, sids, classified_by):
    """Combat stats for several targets from one scoreboard and one inventory pass.

    The per-target version repeats two full ``parse_ticks`` passes for every
    player in the same demo; sharing them keeps the cost flat as targets grow.
    """
    targets = [str(sid) for sid in dict.fromkeys(sids)]
    scoreboard = _scoreboard_frame(parser, events)

    played_by: dict[str, list] = {}
    union_ticks: list[int] = []
    tick_round: dict[int, object] = {}
    for sid in targets:
        played = [
            r for r in (classified_by.get(sid) or [])
            if r.get("side") in {"CT", "T"}
        ]
        played_by[sid] = played
        ticks, mapping = _awp_sample_ticks(played)
        for tick in ticks:
            if tick not in tick_round:
                union_ticks.append(tick)
                tick_round[tick] = mapping[tick]

    inventory = _inventory_frame(parser, union_ticks)
    return {
        sid: {
            "kd": _kd_from_frame(scoreboard, sid),
            "awp_rounds": _awp_rounds_from_frame(inventory, sid, tick_round),
            "total_rounds": len(played_by[sid]),
        }
        for sid in targets
    }


def _inventory_contains_awp(inventory):
    """Handle demoparser inventory lists plus string/JSON-like test values."""
    if inventory is None:
        return False
    if isinstance(inventory, str):
        return "AWP" in inventory
    if isinstance(inventory, dict):
        return any(_inventory_contains_awp(value) for value in inventory.values())
    try:
        return any(_inventory_contains_awp(item) for item in inventory)
    except TypeError:
        return False

def aggregate_combat_stats(stats_list):
    valid = [s for s in stats_list if s is not None]
    if not valid:
        return None
    kd = round(sum(s["kd"] for s in valid) / len(valid), 2)
    total_rounds = sum(s.get("total_rounds", 0) for s in valid)
    awp_rounds = sum(s.get("awp_rounds", 0) for s in valid)
    awp_rate = (
        round(awp_rounds / total_rounds * 100, 1)
        if total_rounds > 0 else 0.0
    )
    return {"kd": kd, "awp_rate": awp_rate}
