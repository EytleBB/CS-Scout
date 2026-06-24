# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

CS-Scout 2.0: CS2 demo analysis and **multi-round replay** visualization. The server
only parses demos and emits per-player JSON; the browser renders an animated canvas
replay. Supports the 7 active-duty maps (not just Mirage). Two subsystems:

1. **Web server** (`server/`) — Flask VPS server that accepts 5E usernames + a map,
   auto-fetches demos, parses them into per-player replay JSON, and serves a canvas UI.
2. **Local tools** (`tools/`, `D:/CSAI/` root) — legacy offline scripts (heatmap viewer,
   zone editor, calibrator) retained from 1.0; not part of the 2.0 server path.

---

## Web Server System (primary active work)

### Architecture

```
Browser UI (index.html + static/app.js + static/replay.js)
    │  POST /api/analyze  {usernames[], map, max_demos, key}
    ▼
web_server.py  (Flask, port 5000)
    │  background thread → pipeline.run(usernames, map_name, ...)
    ├── [Download thread]  search_player → get_demos_by_domain(map) → get_steamid_for_player → download_and_extract
    └── [Main thread]      parse.parse_demo → combat.parse_combat_stats → player_json.build
    ▼
output/player_{domain}.json  +  output/analysis_summary.json
    ▼
Browser fetches /api/player/<domain> → ReplayPlayer canvas (looping, unified CT/T; merged-pistol + per-player Buy)
```

### Module Map (`server/`)

| Module | Role |
|--------|------|
| `maps.py` | Runtime map loader: `load_map(name)`, `game_to_pixel(transform,gx,gy)`, `available_maps()` |
| `setup_maps.py` | One-time: pull radar.png + transform from awpy into `data/maps/<map>/` |
| `parse.py` | `get_round_table`, `classify_rounds` (3-type CT/T), `parse_positions`, `parse_grenades_for_rounds`, `parse_demo` (merger) |
| `combat.py` | `parse_combat_stats` (K/D + AWP-hold rate), `aggregate_combat_stats` |
| `player_json.py` | `build(...)` — assembles per-player JSON from rounds + combat |
| `pipeline.py` | `run(usernames, map_name, ...)`, demo dedup index, `cleanup_demos`, `download_and_extract`, `assemble_round_offset` |
| `api_client.py` | 5E scraping: `search_player`, `get_demos_by_domain(domain, map_name, count)`, `get_steamid_for_player`, `download_demo` |
| `web_server.py` | Flask endpoints + background runner |

### 5E Platform API (api_client.py)

Two base URLs: `https://arena.5eplay.com` (player search, match list) and
`https://gate.5eplay.com` (match detail / steamid extraction).

- `search_player(username)` → `(domain, matched_username)` — domain is a URL-safe ID like `0705cupvvglq`.
- `get_demos_by_domain(domain, map_name, count=10)` → `[{match_code, demo_url}]`
  - Tries **`?match_type=9` first** (ranked, always has `demo_url`), then no-params / `?match_type=1/8`.
  - Filters for `map == map_name` with non-empty `demo_url`; dedups across pages via `seen_codes`.
- `get_steamid_for_player(match_code, username)` — matches steamid by username string.

### Pipeline Flow (pipeline.py `run`)

Demo-level pipeline: the download thread enqueues each `.dem` immediately after extraction
so the main thread parses it while the next downloads. Queue `maxsize=10`. Item types:
- `{"type":"demo", "i","username","domain","steamid","demos_found","dem_file","dem_idx"}`
- `{"type":"player_done", "i","username","domain","steamid","demos_found"}`
- `{"type":"player_failed", "i","username","reason"}`
- `None` — sentinel

Per-player progress steps (via `progress_cb(i, total, name, step, msg)`):
`(0)` search → `(1)` fetch demo list for map → `(2)` resolve steamid →
`(3)` download demo N → `(4)` parse demo N → `(5)` build replay JSON.

**Round dedup across demos**: `assemble_round_offset(records, dem_idx)` offsets each demo's
`official_num` by `dem_idx*1000` so round IDs from different demos don't collide.

**Demo dedup index**: `download_and_extract` checks a global in-memory + on-disk index
(`server/demos_opponents/.demo_index.json` → `{match_id: [dem_path,...]}`) before downloading;
grouped opponents often share match IDs, so this avoids re-downloading 100–200 MB demos.

**Disk cleanup**: `cleanup_demos(demo_dir, limit_gb=30, target_gb=10)` runs once per `run`;
if total `.dem` size > 30 GB, deletes oldest files until ≤ 10 GB.

### Map Data Layer (maps.py / setup_maps.py)

- `data/maps/<map>/` holds `radar.png` + `meta.json` (`{"transform":{pos_x,pos_y,scale}}`).
- Generated at deploy time by `python setup_maps.py` (needs `awpy` + `awpy get maps`); **not committed** (large binaries).
- `game_to_pixel(transform, gx, gy)` = `((gx-pos_x)/scale, (pos_y-gy)/scale)` — Y axis inverted.
- `available_maps()` lists dirs under `MAPS_DIR` that contain `meta.json`.

### Round Classification (parse.classify_rounds) — 2 kept types, CT and T

At each `round_freeze_end`, for the target steamid: `side` ∈ {CT, T}. Economy is judged on
the target's **own** `current_equip_value` (not team average):
- **Pistol** — first round of each half-segment (side flips into a new half). Always kept,
  regardless of equip.
- **Buy** — non-pistol with personal equip ≥ `EQ_BUY_MIN` (2000).
- Non-pistol with personal equip < 2000 → **dropped**: `rtype=None`, excluded from JSON.
  The round still carries its real `side` so half-segment (pistol) tracking isn't broken.

Downstream `parse_positions`/`parse_grenades_for_rounds`/`parse_deaths_for_rounds` filter on
`r["side"] and r["rtype"]`, so dropped rounds produce no path/grenades/death.

### Position Sampling (parse.parse_positions)

Samples target X/Y every `SAMPLE_EVERY=8` ticks across `[fe, fe+WINDOW_S]` (`WINDOW_S=45`s,
TICK_RATE=64 → ~8 Hz). Per-round `path = [[t, x, y], ...]`, `t` relative to freeze_end, game coords.

### Grenade Extraction (parse.parse_grenades_for_rounds)

Only `*Projectile` rows (bare-inventory rows have NaN x/y → dropped), target's throws, landing
in `[0, WINDOW_S]`. Per grenade: `{type, throw_t, land_t, arc:[[t,x,y]], land:[x,y], expire_t}`.
Types: smoke/flash/he/molotov/decoy. Durations: smoke 18s, molotov 7s, decoy 15s, flash 0.5s, he 0.3s.

### Combat Stats (combat.py)

- **K/D** — global scoreboard: `kills_total`/`deaths_total` at last `round_end` tick; averaged across demos.
- **AWP rate** — **AWP-hold rate**: rounds where the player ever held an AWP / total rounds played
  (both sides, any economy). "Held" = the `inventory` tick field (weapon display names) contains
  `"AWP"` at any sample tick across the round window. Per demo returns `{awp_rounds, total_rounds}`;
  aggregated as `sum(awp_rounds)/sum(total_rounds)`. (Replaces the old CT-side AWP-kill ratio, which
  was too sparse and read ~0% for most players.)

### Endpoints (web_server.py)

- `POST /api/analyze` — `{usernames[], map, max_demos, key}`. Validates key (403), usernames (400),
  ≤5 players (400), map present (400), not already running (409).
- `GET /api/status` — full `state` dict (polled every 2s). `state` includes `"map"`.
- `GET /api/maps` — `{"maps": [...]}` from `available_maps()`.
- `GET /api/player/<domain>` — that player's `player_{domain}.json` (404 if missing).
- `GET /api/results` — raw `analysis_summary.json` (no path normalization in 2.0).
- `GET /output/<file>` — serves output JSON. `GET /maps/<path>` — serves radar from `MAPS_DIR`.
- `GET /` — `index.html`.

### JSON Contract

`output/player_{domain}.json`:
```json
{
  "username":"...", "domain":"...", "steamid":"765...",
  "map":"de_mirage",
  "transform":{"pos_x":-3230.0,"pos_y":1713.0,"scale":5.0},
  "radar":"/maps/de_mirage/radar.png",
  "combat_stats":{"kd":1.23,"awp_rate":45.0},
  "demos_found":6, "round_count":47,
  "rounds":[
    {"side":"CT","rtype":"Full","round_id":1003,
     "path":[[0.0,-1200.0,340.0],[0.125,-1190.0,352.0]],
     "grenades":[{"type":"smoke","throw_t":8.1,"land_t":9.4,
                  "arc":[[8.1,-1100.0,300.0],[9.4,-900.0,250.0]],
                  "land":[-900.0,250.0],"expire_t":27.4}]}
  ]
}
```
`side` ∈ {CT,T}; `rtype` ∈ {Pistol,Buy}; all `t` are seconds from freeze_end; coords are game coords.

`output/analysis_summary.json`:
```json
{"map":"de_mirage","max_demos":6,
 "failed":[{"username":"X","reason":"..."}],
 "results":[{"username":"...","domain":"...","player_json":"/output/player_xxx.json",
             "combat_stats":{"kd":1.23,"awp_rate":45.0},"demos_found":6,"round_count":47}]}
```

### Frontend (templates/index.html + static/app.js + static/replay.js)

- Sticky 50px header (with a **unified CT/T toggle** + global play/pause + scrubber) + left sidebar
  (map `<select>`, 5 username inputs, depth, key, scan button, status, failed list) + main panel.
- Main panel = a **merged pistol overlay** on top (`#pistol`: one canvas overlaying *all* scanned
  players' Pistol rounds, each player a distinct color + legend) followed by per-player cards.
- The header CT/T toggle drives `side` on **every** canvas at once (merged pistol + each card's buy
  canvas); one side is shown at a time. There are no per-card tabs.
- `app.js`: `loadMaps()` fills the dropdown; `run()` POSTs `/api/analyze`; `poll()` hits `/api/status`
  every 2s and adds a card per result via `/api/player/<domain>`. Each card: K/D, AWP-hold %, round
  count, and a single **Buy** canvas. The merged overlay grows a shared `pistolRounds` array as
  players load; the toggle calls `setFilter(side, fixedRtype)` on each registered `ReplayPlayer`.
- `replay.js` `ReplayPlayer(canvas, {radar, transform, rounds, side, rtype})`:
  overlays all matching rounds on a `PLAYBACK_S` loop (`WINDOW_S` game time accelerated). **No fading
  trails.** Per-round `color` overrides the side color (used by the merged pistol overlay). Draws
  grenade in-flight arcs, landing dots, and range circles (smoke/molotov) during `[land_t, expire_t]`.
  Methods: `setFilter`, `toggleRound`, `drawAt`. `static/replay_test.html` is a standalone fixture.

### Known Data Limitations

- Players with no ranked games on the chosen map → `get_demos_by_domain` empty → reported failed.
- `get_steamid_for_player` matches by username string — fails if the player renamed since the match.
- K/D is global (both sides); CT-only K/D not implemented.
- Canvas replay has no headless unit test — verify visually via `replay_test.html` or a live scan.

### VPS Deployment

```bash
cd /home/ubuntu/server
source venv/bin/activate
pip install flask requests urllib3 pandas numpy demoparser2 awpy
awpy get maps          # downloads radar assets to ~/.awpy
python setup_maps.py   # populate data/maps/<map>/{radar.png, meta.json}
python web_server.py
```
Access at `http://<VPS公网IP>:5000` — ensure port 5000 is open. Install `fonts-noto-cjk` for CJK if needed.
(`awpy` is only used offline by `setup_maps.py`; the server itself no longer renders images.)

### Tests

`server/tests/` (pytest). Integration tests use a fixture demo at
`server/../demos_analysis/g161-n-20260123174821830606429_de_mirage.dem` (~100 MB, not in git);
they `pytest.skip` when absent. On this Windows box, run with
`--basetemp` pointing outside the access-denied system temp, e.g. a scratchpad dir.

```bash
cd server && python -m pytest tests/ -v
```

---

## Local Analysis Tools (legacy/offline, 1.0)

Located in `tools/` and `D:/CSAI/` root. These predate the 2.0 rewrite and are **not** used by
the server path; they still target Mirage + the old zone/heatmap model.

```bash
python tool_heatmap.py           # Interactive heatmap viewer (matplotlib UI)
python tool_visualize_path.py    # Path overlay on radar for a single player/round
python map_zone_editor.py        # GUI: draw/edit zone polygons on radar image
python tool_map_calibrator.py    # GUI: calibrate game→pixel coordinate transform
python zone_priority_manager.py  # GUI: assign zone priority weights
```

### demoparser2 API Pattern

```python
parser = DemoParser(path)
evts = dict(parser.parse_events(["round_freeze_end", "round_end"], other=["tick"]))
df = parser.parse_ticks(["X", "Y", "steamid", "team_name"], ticks=[tick1, tick2, ...])
```
Always cast result to `pd.DataFrame` and cast `steamid` to `str`.

**Known field notes**:
- `dmg_health` — uncapped raw bullet damage (AWP headshot = 446+). Cap at 100 for effective damage.
- `kills_total`, `deaths_total` — scoreboard running totals, available as tick fields. Reliable for end-of-match K/D.
- `weapon` in `player_death`/`player_hurt` events — plain name (e.g. `"awp"`, `"ak47"`), not prefixed.
- `active_weapon_name` — NOT a valid demoparser2 tick field. Use the event `weapon` field instead.
- `parse_grenades()` — projectile rows have `grenade_type` like `CSmokeGrenadeProjectile`, `grenade_entity_id`, `x`/`y`/`tick`. Bare-inventory rows have NaN x/y.

## Config & Data Files

| File | Purpose |
|------|---------|
| `server/config.py` | `HOST/PORT/SECRET_KEY`, paths, `MAPS_DIR`, `TICK_RATE=64`, `WINDOW_S=20`, `SAMPLE_EVERY=8`, `EQ_BUY_MIN=2000` (per-player buy floor; `EQ_FULL_BUY` legacy/unused) |
| `server/data/maps/<map>/radar.png` | Radar background (generated by setup_maps.py) |
| `server/data/maps/<map>/meta.json` | Coordinate transform `{pos_x,pos_y,scale}` |
| `server/demos_opponents/` | Downloaded .dem files + `.demo_index.json` |
| `server/output/` | `player_{domain}.json` + `analysis_summary.json` |
