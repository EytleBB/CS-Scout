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
    # The economy filter is deliberately not applied here: the denominator is
    # every round the player participated in, on both CT and T.
    played = [r for r in classified if r.get("side") in {"CT", "T"}]
    awp_rounds = _count_awp_hold_rounds(p, played, sid)
    return {"kd": kd_val, "awp_rounds": awp_rounds,
            "total_rounds": len(played)}


def _count_awp_hold_rounds(parser, played, sid):
    """Count rounds where ``sid`` held an AWP at any sampled tick."""
    if not played:
        return 0

    sample_ticks = []
    tick_round = {}
    for r in played:
        # Combat stats cover the full round, not only the shorter replay window;
        # otherwise an AWP picked up late in a round would be missed.
        for tick in range(r["fe_tick"], r["end_tick"] + 1,
                          config.SAMPLE_EVERY):
            sample_ticks.append(tick)
            tick_round[tick] = r["official_num"]
    if not sample_ticks:
        return 0

    try:
        df = parser.parse_ticks(["inventory", "steamid"], ticks=sample_ticks)
    except Exception as exc:
        log.warning("inventory parse failed while calculating AWP rate: %s", exc)
        return 0
    if not isinstance(df, pd.DataFrame):
        df = pd.DataFrame(df)
    required = {"tick", "steamid", "inventory"}
    if df.empty or not required.issubset(df.columns):
        return 0

    df = df.copy()
    df["steamid"] = df["steamid"].astype(str)
    df = df[df["steamid"] == str(sid)]
    held_rounds = set()
    for tick, inventory in zip(df["tick"], df["inventory"]):
        try:
            round_num = tick_round.get(int(tick))
        except (TypeError, ValueError, OverflowError):
            continue
        if round_num is not None and _inventory_contains_awp(inventory):
            held_rounds.add(round_num)
    return len(held_rounds)


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
