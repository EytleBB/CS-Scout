"""Assemble per-player JSON from parsed rounds + combat stats."""
import maps

def build(username, domain, steamid, map_name, rounds, combat):
    m = maps.load_map(map_name)
    out_rounds = []
    for r in rounds:
        out_rounds.append({
            "side": r["side"], "rtype": r["rtype"], "round_id": r["official_num"],
            "path": r["path"], "grenades": r["grenades"],
            "death_t": r.get("death_t"),
        })
    return {
        "username": username, "domain": domain, "steamid": str(steamid),
        "map": map_name, "transform": m["transform"], "radar": m["radar_rel"],
        "combat_stats": combat or {"kd": 0.0, "awp_rate": 0.0},
        "demos_found": 0,   # filled by pipeline
        "round_count": len(out_rounds),
        "rounds": out_rounds,
    }
