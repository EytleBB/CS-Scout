"""
CSAI Pipeline — opponent analysis on server side

Flow: opponent steamids → UUID lookup → Mirage history → download → parse → heatmap
"""

import os
import json
import zipfile
import logging
import threading
from queue import Queue

import warnings
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import matplotlib.font_manager as fm
from matplotlib.patches import Polygon as MplPolygon
from matplotlib.path import Path as MplPath
from scipy.ndimage import gaussian_filter

# Try to configure a CJK-capable font for player name titles
_CJK_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simsun.ttc",
]
_CJK_FONT_PATH = next((f for f in _CJK_FONT_CANDIDATES if os.path.exists(f)), None)
if _CJK_FONT_PATH:
    try:
        fm.fontManager.addfont(_CJK_FONT_PATH)
        _prop = fm.FontProperties(fname=_CJK_FONT_PATH)
        matplotlib.rcParams["font.family"] = _prop.get_name()
    except Exception:
        pass

# Suppress matplotlib glyph-missing warnings when CJK font unavailable
warnings.filterwarnings("ignore", message="Glyph .* missing from font")
from demoparser2 import DemoParser
from shapely.geometry import Point, Polygon

import api_client
import config

log = logging.getLogger("pipeline")

RTYPE_ORDER = ["Full Buy", "Force Buy", "Eco", "Pistol"]
RTYPE_COLOR = {
    "Full Buy":  "#ff5555",
    "Force Buy": "#ffaa44",
    "Eco":       "#ccaa44",
    "Pistol":    "#44aaff",
}
RTYPE_SLUG = {
    "Full Buy": "fullbuy",
    "Force Buy": "forcebuy",
    "Eco": "eco",
    "Pistol": "pistol",
}
SNIPER_EVENT_NAMES  = {"awp", "ssg08", "g3sg1", "scar20"}  # event weapon field


# ── Data loading ──────────────────────────────────────────────────────────────

def load_config():
    if os.path.exists(config.MAP_CONFIG_FILE):
        with open(config.MAP_CONFIG_FILE) as f:
            return json.load(f)
    return {"pos_x": -3230, "pos_y": 1713, "scale": 5.0}


def load_zones():
    if not os.path.exists(config.ZONES_FILE):
        return []
    with open(config.ZONES_FILE, encoding="utf-8") as f:
        raw = json.load(f)
    return [{"name": n, "coords": coords, "poly": Polygon(coords)} for n, coords in raw.items()]


def load_zones_raw():
    if not os.path.exists(config.ZONES_FILE):
        return {}
    with open(config.ZONES_FILE, encoding="utf-8") as f:
        return json.load(f)


def load_weights():
    if not os.path.exists(config.WEIGHTS_FILE):
        return {}
    with open(config.WEIGHTS_FILE, encoding="utf-8") as f:
        return json.load(f)


def game_to_pixel(cfg, gx, gy):
    return (gx - cfg["pos_x"]) / cfg["scale"], (cfg["pos_y"] - gy) / cfg["scale"]


def get_zone(zones, x, y):
    p = Point(x, y)
    for z in zones:
        if z["poly"].contains(p):
            return z["name"]
    return "Unclassified"


# ── Demo parsing ──────────────────────────────────────────────────────────────

def get_round_table(evts):
    """Build round table from a pre-parsed events dict."""
    match_tick = int(evts["round_announce_match_start"]["tick"].iloc[0])
    fe_all = evts["round_freeze_end"]["tick"].sort_values().reset_index(drop=True)
    re_all = evts["round_end"]["tick"].sort_values().reset_index(drop=True)
    real_fe = fe_all[fe_all >= match_tick].reset_index(drop=True)

    rounds = []
    for i, fe_tick in enumerate(real_fe):
        later_ends = re_all[re_all > fe_tick]
        end_tick = int(later_ends.iloc[0]) if not later_ends.empty else fe_tick + 115 * config.TICK_RATE
        rounds.append({"official_num": i + 1, "fe_tick": int(fe_tick), "end_tick": end_tick})
    return rounds


def classify_rounds(parser, rounds, target_sids):
    """Classify rounds — single batched parse_ticks call for all rounds."""
    if not rounds:
        return []

    all_fe_ticks = [r["fe_tick"] for r in rounds]
    df_all = parser.parse_ticks(
        ["steamid", "team_name", "current_equip_value"], ticks=all_fe_ticks
    )
    if not isinstance(df_all, pd.DataFrame):
        df_all = pd.DataFrame(df_all)
    df_all["steamid"] = df_all["steamid"].astype(str)
    grouped = {tick: grp for tick, grp in df_all.groupby("tick")}

    result = []
    prev_was_ct = False
    pistol_num = None

    for r in rounds:
        df = grouped.get(r["fe_tick"])
        if df is None or df.empty:
            prev_was_ct = False
            result.append({**r, "is_ct": False, "round_type": None})
            continue

        targets = df[df["steamid"].isin(target_sids)]
        if targets.empty:
            prev_was_ct = False
            result.append({**r, "is_ct": False, "round_type": None})
            continue

        is_ct = targets["team_name"].iloc[0] == "CT"
        if is_ct:
            if not prev_was_ct:
                pistol_num = r["official_num"]
            if r["official_num"] == pistol_num:
                rtype = "Pistol"
            else:
                avg_eq = df[df["team_name"] == "CT"]["current_equip_value"].mean()
                rtype = ("Full Buy" if avg_eq >= config.EQ_FULL_BUY
                         else "Force Buy" if avg_eq >= config.EQ_FORCE_BUY
                         else "Eco")
        else:
            rtype = None

        prev_was_ct = is_ct
        result.append({**r, "is_ct": is_ct, "round_type": rtype})

    return result


def _get_first_kill_ticks(evts, rounds):
    """Return dict of {official_num: first_real_kill_tick} for each round.

    A "real kill" excludes suicides (attacker == victim) and world/disconnect
    kills (attacker_steamid is 0 or null).  Disconnect deaths in CS2 are
    typically recorded as attacker == victim, so they are filtered out too.
    """
    result = {}
    deaths_df = evts.get("player_death")
    if deaths_df is None:
        return result
    if not isinstance(deaths_df, pd.DataFrame):
        deaths_df = pd.DataFrame(deaths_df)
    if deaths_df.empty:
        return result

    for col in ("attacker_steamid", "user_steamid"):
        if col not in deaths_df.columns:
            return result

    valid = deaths_df[
        deaths_df["attacker_steamid"].notna() &
        (deaths_df["attacker_steamid"] != 0) &
        (deaths_df["attacker_steamid"] != deaths_df["user_steamid"])
    ]

    if valid.empty:
        return result

    valid_ticks = valid["tick"].values
    for r in rounds:
        mask = (valid_ticks > r["fe_tick"]) & (valid_ticks <= r["end_tick"])
        if mask.any():
            result[r["official_num"]] = int(valid_ticks[mask].min())

    return result


def parse_demo(path, target_steamid, zones):
    target_sids = {str(target_steamid)}
    try:
        parser = DemoParser(path)
        # Single parse_events call for all event types
        raw = parser.parse_events(
            ["round_freeze_end", "round_announce_match_start",
             "round_end", "player_death"],
            other=["tick"]
        )
        evts = dict(raw)
    except Exception as e:
        log.warning(f"Failed to parse {path}: {e}")
        return []

    rounds = get_round_table(evts)
    if not rounds:
        return []

    classified = classify_rounds(parser, rounds, target_sids)
    ct_rounds = [r for r in classified if r["is_ct"]]
    if not ct_rounds:
        return []

    first_kill = _get_first_kill_ticks(evts, rounds)

    type_map = {r["official_num"]: r["round_type"] for r in ct_rounds}

    sample_ticks = []
    tick_to_round = {}
    for r in ct_rounds:
        lo = r["fe_tick"] + config.WINDOW_START_S * config.TICK_RATE
        fk = first_kill.get(r["official_num"])
        if fk is not None:
            hi = min(fk + 3 * config.TICK_RATE, r["end_tick"])
        else:
            hi = min(r["fe_tick"] + config.WINDOW_END_S * config.TICK_RATE, r["end_tick"])
        if lo >= hi:
            continue
        for t in range(lo, hi, config.TICK_RATE):
            sample_ticks.append(t)
            tick_to_round[t] = r["official_num"]

    if not sample_ticks:
        return []

    df_all = parser.parse_ticks(["X", "Y", "steamid", "team_name"], ticks=sample_ticks)
    if not isinstance(df_all, pd.DataFrame):
        df_all = pd.DataFrame(df_all)
    if df_all.empty:
        return []

    df_all["steamid"] = df_all["steamid"].astype(str)
    df_ct = df_all[
        (df_all["steamid"] == str(target_steamid)) & (df_all["team_name"] == "CT")
    ].copy()

    df_ct["official_round"] = df_ct["tick"].map(tick_to_round)
    df_ct = df_ct.dropna(subset=["official_round"])
    df_ct["official_round"] = df_ct["official_round"].astype(int)

    if df_ct.empty:
        return []

    # Vectorized round type assignment
    df_ct["round_type"] = df_ct["official_round"].map(type_map)

    # Vectorized zone assignment via matplotlib Path (replaces per-point shapely)
    xs = df_ct["X"].values
    ys = df_ct["Y"].values
    n = len(df_ct)
    zone_names = np.full(n, "Unclassified", dtype=object)
    points = np.column_stack([xs, ys])
    unassigned = np.ones(n, dtype=bool)
    for z in zones:
        if not unassigned.any():
            break
        path = MplPath(z["coords"])
        mask = path.contains_points(points) & unassigned
        zone_names[mask] = z["name"]
        unassigned &= ~mask
    df_ct["zone"] = zone_names

    return df_ct[["official_round", "round_type", "zone", "X", "Y", "tick"]].rename(
        columns={"official_round": "round"}
    ).to_dict("records")


# ── Heatmap generation ────────────────────────────────────────────────────────

def _render_rtype_ax(ax, df, rtype, cfg, img, h, w, zones_raw):
    """Render one round-type heatmap onto a pre-created matplotlib Axes."""
    ax.set_facecolor("#1a1a2e")
    ax.imshow(img, zorder=0)
    ax.axis("off")

    sub = df[df["round_type"] == rtype]
    n_rounds  = sub["round"].nunique() if not sub.empty else 0
    n_samples = len(sub)
    ax.set_title(f"{rtype}  ({n_rounds} rounds / {n_samples} samples)",
                 color="white", fontsize=11)

    color = RTYPE_COLOR.get(rtype, "#ffffff")

    if not sub.empty:
        px = (sub["X"].values - cfg["pos_x"]) / cfg["scale"]
        py = (cfg["pos_y"] - sub["Y"].values) / cfg["scale"]

        # Layer 1: per-round-normalized Gaussian density
        density = np.zeros((h, w), dtype=float)
        for _, grp in sub.groupby("round"):
            gxi = np.clip(
                ((grp["X"].values - cfg["pos_x"]) / cfg["scale"]).astype(int), 0, w - 1)
            gyi = np.clip(
                ((cfg["pos_y"] - grp["Y"].values) / cfg["scale"]).astype(int), 0, h - 1)
            w_per = 1.0 / len(gxi)
            np.add.at(density, (gyi, gxi), w_per)
        sigma = max(3, 7 - n_rounds // 20)
        density = gaussian_filter(density, sigma=sigma)
        if density.max() > 0:
            density /= density.max()
            density = np.power(density, 0.55)
            density[density < 0.005] = 0
            base_rgb = mcolors.to_rgb(color)
            rgba = np.zeros((h, w, 4), dtype=float)
            rgba[..., 0] = base_rgb[0]
            rgba[..., 1] = base_rgb[1]
            rgba[..., 2] = base_rgb[2]
            rgba[..., 3] = np.where(density > 0, np.clip(0.02 + 0.86 * density, 0.02, 0.88), 0.0)
            ax.imshow(rgba, zorder=2)

        # Layer 2: trajectory lines
        if "tick" in sub.columns:
            for _, grp in sub.groupby("round", sort=False):
                if len(grp) < 2:
                    continue
                grp_s = grp.sort_values("tick")
                rx = (grp_s["X"].values - cfg["pos_x"]) / cfg["scale"]
                ry = (cfg["pos_y"] - grp_s["Y"].values) / cfg["scale"]
                ax.plot(rx, ry, color=color, alpha=0.20, linewidth=0.7,
                        zorder=3, solid_capstyle="round", solid_joinstyle="round")

        # Layer 3: scatter dots
        ax.scatter(px, py, s=6, color=color, alpha=0.55, linewidths=0, zorder=4)

    # Layer 4: zone outlines + labels
    for z_name, coords in zones_raw.items():
        pixels = [game_to_pixel(cfg, p[0], p[1]) for p in coords]
        poly = MplPolygon(pixels, closed=True, edgecolor="white",
                          facecolor="none", linewidth=0.5, alpha=0.35, zorder=5)
        ax.add_patch(poly)
        cx = sum(p[0] for p in pixels) / len(pixels)
        cy = sum(p[1] for p in pixels) / len(pixels)
        ax.text(cx, cy, z_name, color="white", fontsize=5,
                ha="center", va="center", alpha=0.55, zorder=6)


def generate_heatmap(df, player_name, output_path):
    """Generate combined 2×2 heatmap and 4 individual tile images.

    Returns dict mapping rtype → tile_path (relative to OUTPUT_DIR).
    """
    cfg = load_config()
    zones_raw = load_zones_raw()
    img = plt.imread(config.MAP_IMG_PATH)
    h, w = img.shape[:2]

    # ── Combined 2×2 figure ───────────────────────────────────────────────────
    fig, axes = plt.subplots(2, 2, figsize=(16, 16), facecolor="#1a1a2e")
    fig.suptitle(f"CT Positioning — {player_name}", color="white", fontsize=16, fontweight="bold")
    for idx, rtype in enumerate(RTYPE_ORDER):
        _render_rtype_ax(axes[idx // 2][idx % 2], df, rtype, cfg, img, h, w, zones_raw)
    plt.tight_layout(rect=[0, 0, 1, 0.95])
    tmp_path = output_path + ".tmp"
    fig.savefig(tmp_path, format="png", dpi=150, facecolor="#1a1a2e", bbox_inches="tight")
    plt.close(fig)
    os.replace(tmp_path, output_path)

    # ── Individual tile figures (1×1, same dpi — full resolution per quadrant) ─
    base = output_path[:-4]  # strip ".png"
    tile_paths = {}
    for rtype in RTYPE_ORDER:
        slug = RTYPE_SLUG[rtype]
        tile_path = f"{base}_{slug}.png"
        fig_t, ax_t = plt.subplots(1, 1, figsize=(8, 8), facecolor="#1a1a2e")
        _render_rtype_ax(ax_t, df, rtype, cfg, img, h, w, zones_raw)
        plt.tight_layout()
        tmp_t = tile_path + ".tmp"
        fig_t.savefig(tmp_t, format="png", dpi=150, facecolor="#1a1a2e", bbox_inches="tight")
        plt.close(fig_t)
        os.replace(tmp_t, tile_path)
        tile_paths[rtype] = os.path.basename(tile_path)

    log.info(f"Heatmap saved: {output_path} + {len(tile_paths)} tiles")
    return tile_paths


def generate_zone_stats(df, weights):
    ICON = {1: "D", 2: "K", 3: "A"}
    stats = {}
    for rtype in RTYPE_ORDER:
        sub = df[df["round_type"] == rtype]
        if sub.empty:
            stats[rtype] = []
            continue
        vc = sub["zone"].value_counts(normalize=True).head(5)
        items = []
        for zone, rate in vc.items():
            w = weights.get(zone, 1)
            items.append({
                "zone": zone,
                "percent": round(rate * 100, 1),
                "count": int((sub["zone"] == zone).sum()),
                "weight": w,
                "tag": ICON.get(w, "D") if zone != "Unclassified" else "?",
            })
        stats[rtype] = items
    return stats


def parse_combat_stats(path, steamid):
    """Parse K/D (from global scoreboard) and AWP rate (CT AWP kills / CT total kills).

    Returns a dict or None on failure.
    """
    target_sid = str(steamid)
    try:
        parser = DemoParser(path)
        raw = parser.parse_events(
            ["round_freeze_end", "round_announce_match_start",
             "round_end", "player_death"],
            other=["tick"]
        )
        evts = dict(raw)
    except Exception as e:
        log.warning(f"Combat stats parse failed {path}: {e}")
        return None

    # --- K/D from scoreboard (global, both sides) ---
    kd_val = 0.0
    round_end_df = evts.get("round_end")
    if round_end_df is not None:
        if not isinstance(round_end_df, pd.DataFrame):
            round_end_df = pd.DataFrame(round_end_df)
        if not round_end_df.empty:
            last_tick = int(round_end_df["tick"].max())
            try:
                sb_df = pd.DataFrame(
                    parser.parse_ticks(["kills_total", "deaths_total", "steamid"], ticks=[last_tick])
                )
                sb_df["steamid"] = sb_df["steamid"].astype(str)
                row = sb_df[sb_df["steamid"] == target_sid]
                if not row.empty:
                    k = int(row["kills_total"].iloc[0])
                    d = int(row["deaths_total"].iloc[0])
                    kd_val = round(k / max(d, 1), 2)
            except Exception as e:
                log.warning(f"Scoreboard parse failed {path}: {e}")

    # --- AWP rate: CT AWP kills / CT total kills ---
    rounds = get_round_table(evts)
    if not rounds:
        return {"kd": kd_val, "ct_kills": 0, "awp_kills": 0}
    classified = classify_rounds(parser, rounds, {target_sid})
    ct_rounds = [r for r in classified if r["is_ct"]]
    if not ct_rounds:
        return {"kd": kd_val, "ct_kills": 0, "awp_kills": 0}

    fe_arr   = np.array([r["fe_tick"]      for r in ct_rounds])
    end_arr  = np.array([r["end_tick"]     for r in ct_rounds])
    rnum_arr = np.array([r["official_num"] for r in ct_rounds])

    def _round_of_ticks(tick_series):
        ticks = tick_series.values[:, None]
        in_rnd = (ticks >= fe_arr) & (ticks <= end_arr)
        idx = np.argmax(in_rnd, axis=1)
        valid = in_rnd[np.arange(len(ticks)), idx]
        return np.where(valid, rnum_arr[idx], -1)

    ct_kills = awp_kills = 0
    deaths_df = evts.get("player_death")
    if deaths_df is not None:
        if not isinstance(deaths_df, pd.DataFrame):
            deaths_df = pd.DataFrame(deaths_df)
        if not deaths_df.empty and "attacker_steamid" in deaths_df.columns:
            deaths_df["attacker_steamid"] = deaths_df["attacker_steamid"].astype(str)
            deaths_df["user_steamid"]     = deaths_df["user_steamid"].astype(str)
            deaths_df["_rnum"] = _round_of_ticks(deaths_df["tick"])
            ct_d = deaths_df[deaths_df["_rnum"] >= 0]
            real_kills = ct_d[
                (ct_d["attacker_steamid"] == target_sid) &
                (ct_d["attacker_steamid"] != ct_d["user_steamid"])
            ]
            ct_kills = len(real_kills)
            if "weapon" in real_kills.columns:
                awp_kills = int(real_kills["weapon"].isin(SNIPER_EVENT_NAMES).sum())

    return {
        "kd":       kd_val,
        "ct_kills": ct_kills,
        "awp_kills": awp_kills,
    }


def aggregate_combat_stats(stats_list):
    """Merge per-demo combat stats into a single player summary.

    K/D: average of per-demo scoreboard K/D values.
    AWP rate: total CT AWP kills / total CT kills across all demos.
    """
    valid = [s for s in stats_list if s is not None]
    if not valid:
        return None
    kd_avg = round(sum(s["kd"] for s in valid) / len(valid), 2)
    total_ct_kills  = sum(s["ct_kills"]  for s in valid)
    total_awp_kills = sum(s["awp_kills"] for s in valid)
    awp_rate = round(total_awp_kills / total_ct_kills * 100, 1) if total_ct_kills > 0 else 0.0
    return {
        "kd":       kd_avg,
        "awp_rate": awp_rate,
    }


# ── Demo deduplication index ──────────────────────────────────────────────────
# Persists across runs so repeated analysis sessions skip already-downloaded demos.

_dem_index: dict = {}        # match_id -> [dem_path, ...]
_dem_index_lock = threading.Lock()
_dem_index_ready = False


def _dem_idx_path():
    return os.path.join(config.DEMO_DIR, ".demo_index.json")


def _ensure_dem_index():
    global _dem_index_ready
    with _dem_index_lock:
        if _dem_index_ready:
            return
        p = _dem_idx_path()
        if os.path.exists(p):
            try:
                with open(p) as f:
                    _dem_index.update(json.load(f))
            except Exception:
                pass
        _dem_index_ready = True


def _index_lookup(match_id):
    """Return cached .dem paths for match_id, pruning any deleted files."""
    _ensure_dem_index()
    with _dem_index_lock:
        paths = _dem_index.get(match_id, [])
        valid = [p for p in paths if os.path.exists(p)]
        if len(valid) != len(paths):
            _dem_index[match_id] = valid
        return valid


def _index_save(match_id, paths):
    with _dem_index_lock:
        _dem_index[match_id] = paths
        try:
            os.makedirs(config.DEMO_DIR, exist_ok=True)
            with open(_dem_idx_path(), "w") as f:
                json.dump(_dem_index, f)
        except Exception as e:
            log.warning(f"Could not save demo index: {e}")


# ── Demo disk cleanup ─────────────────────────────────────────────────────────

def cleanup_demos(demo_dir, limit_gb=30, target_gb=10):
    """Delete oldest .dem files when total size exceeds limit_gb, down to target_gb."""
    dem_files = []
    for root, _, files in os.walk(demo_dir):
        for name in files:
            if name.endswith(".dem"):
                path = os.path.join(root, name)
                try:
                    dem_files.append((os.path.getmtime(path), os.path.getsize(path), path))
                except OSError:
                    pass

    total_bytes = sum(s for _, s, _ in dem_files)
    limit_bytes  = limit_gb * 1024 ** 3
    target_bytes = target_gb * 1024 ** 3

    if total_bytes <= limit_bytes:
        return

    log.info(f"Demo dir size {total_bytes / 1024**3:.1f} GB > {limit_gb} GB, cleaning up...")
    dem_files.sort()  # oldest first
    freed = 0
    need_to_free = total_bytes - target_bytes
    for mtime, size, path in dem_files:
        if freed >= need_to_free:
            break
        try:
            os.remove(path)
            freed += size
            log.info(f"Deleted old demo: {path} ({size / 1024**2:.0f} MB)")
        except OSError as e:
            log.warning(f"Could not delete {path}: {e}")
    log.info(f"Cleanup done, freed {freed / 1024**3:.1f} GB")


# ── Download + extract ────────────────────────────────────────────────────────

def download_and_extract(match_id, demo_url, dest_dir):
    # Global dedup: reuse any previously downloaded copy across all players
    cached = _index_lookup(match_id)
    if cached:
        log.info(f"Reusing cached demo: {match_id} ({len(cached)} file(s))")
        return cached

    os.makedirs(dest_dir, exist_ok=True)
    zip_path = os.path.join(dest_dir, f"{match_id}.zip")

    try:
        log.info(f"Downloading: {match_id}")
        api_client.download_demo(demo_url, zip_path)

        dem_files = []
        with zipfile.ZipFile(zip_path, "r") as zf:
            for name in zf.namelist():
                if name.endswith(".dem"):
                    zf.extract(name, dest_dir)
                    dem_files.append(os.path.join(dest_dir, name))

        os.remove(zip_path)
        if dem_files:
            _index_save(match_id, dem_files)
        return dem_files

    except Exception as e:
        log.error(f"Download failed for {match_id}: {e}")
        if os.path.exists(zip_path):
            os.remove(zip_path)
        return []


# ── Username-based pipeline ──────────────────────────────────────────────────

def run_by_usernames(usernames, max_demos=10, progress_cb=None):
    """Demo-level pipelined pipeline.

    The download thread puts each .dem file into the queue as soon as it lands
    on disk, so the main thread starts parsing immediately while the next demo
    is still downloading.  Demos shared by grouped opponents are reused via the
    global dedup index — no duplicate downloads.

    Queue item types
    ----------------
    {"type": "demo",          "i", "username", "domain", "steamid",
                              "demos_found", "dem_file", "dem_idx"}
    {"type": "player_done",   "i", "username", "domain", "steamid", "demos_found"}
    {"type": "player_failed", "i", "username", "reason"}
    None  — sentinel: all players finished
    """
    total = len(usernames)
    dl_queue = Queue(maxsize=10)  # demo-level buffer (~1 full player ahead)
    zones   = load_zones()
    weights = load_weights()

    cleanup_demos(config.DEMO_DIR)

    # ── Stage 1: search + download (background thread) ────────────────────────

    def _download_stage():
        for i, username in enumerate(usernames):
            log.info(f"\n{'='*50}\nPlayer {i+1}/{total}: {username}\n{'='*50}")

            def cb(step, _ts, msg, _i=i, _n=username):
                if progress_cb:
                    progress_cb(_i, total, _n, step, msg)

            cb(0, 5, f"搜索 {username}...")
            domain, matched_name = api_client.search_player(username)
            if not domain:
                dl_queue.put({"type": "player_failed", "i": i,
                              "username": username, "reason": "5E 上未找到该玩家"})
                continue

            display_name = matched_name or username

            def cb(step, _ts, msg, _i=i, _n=display_name):  # noqa: F811
                if progress_cb:
                    progress_cb(_i, total, _n, step, msg)

            cb(1, 5, "获取 Mirage demo 列表...")
            demos = api_client.get_mirage_demos_by_domain(domain, count=max_demos)
            demos_found = len(demos)
            if not demos:
                dl_queue.put({"type": "player_failed", "i": i,
                              "username": display_name, "reason": "无 Mirage demo 可用"})
                continue

            cb(2, 5, "解析 Steam ID...")
            steamid = None
            for m in demos[:3]:
                steamid = api_client.get_steamid_for_player(m["match_code"], display_name)
                if steamid:
                    break
            if not steamid:
                dl_queue.put({"type": "player_failed", "i": i,
                              "username": display_name, "reason": "无法解析 Steam ID"})
                continue

            opponent_dir = os.path.join(config.DEMO_DIR, domain)
            base = {"username": display_name, "domain": domain,
                    "steamid": steamid, "demos_found": demos_found}

            # dem_idx counts individual .dem files (for round dedup offset)
            dem_idx = 0
            for match_idx, m in enumerate(demos):
                cb(3, 5, f"下载 demo {match_idx+1}/{demos_found}...")
                files = download_and_extract(m["match_code"], m["demo_url"], opponent_dir)
                for f in files:
                    dl_queue.put({"type": "demo", "i": i, **base,
                                  "dem_file": f, "dem_idx": dem_idx})
                    dem_idx += 1

            if dem_idx == 0:
                dl_queue.put({"type": "player_failed", "i": i,
                              "username": display_name, "reason": "demo 下载全部失败"})
            else:
                dl_queue.put({"type": "player_done", "i": i, **base})

        dl_queue.put(None)  # sentinel: all players done

    dl_thread = threading.Thread(target=_download_stage, daemon=True)
    dl_thread.start()

    # ── Stage 2: parse demos + generate heatmap (main thread) ────────────────

    results = []
    failed  = []
    player_records:   dict = {}  # i -> list[dict]
    player_dem_count: dict = {}  # i -> int (dem files received so far, for logging)
    player_dem_files: dict = {}  # i -> list[str] (paths, for combat stats)

    while True:
        item = dl_queue.get()
        if item is None:
            break

        t            = item["type"]
        i            = item["i"]
        display_name = item["username"]

        def cb(step, _ts, msg, _i=i, _n=display_name):
            if progress_cb:
                progress_cb(_i, total, _n, step, msg)

        if t == "player_failed":
            log.warning(f"[{display_name}] {item['reason']}")
            failed.append({"username": display_name, "reason": item["reason"]})
            cb(0, 5, f"{display_name}: {item['reason']}")
            continue

        if t == "demo":
            if i not in player_records:
                player_records[i]   = []
                player_dem_count[i] = 0
                player_dem_files[i] = []

            player_dem_count[i] += 1
            player_dem_files[i].append(item["dem_file"])
            n = player_dem_count[i]
            cb(4, 5, f"解析 demo {n}...")
            records = parse_demo(item["dem_file"], item["steamid"], zones)
            for rec in records:
                rec["round"] += item["dem_idx"] * 1000
            player_records[i].extend(records)
            continue

        if t == "player_done":
            all_records = player_records.pop(i, [])
            dem_count   = player_dem_count.pop(i, 0)
            dem_files_i = player_dem_files.pop(i, [])

            if not all_records:
                log.warning(f"[{display_name}] No CT-side data found")
                failed.append({"username": display_name, "reason": "未找到 CT 方数据"})
                continue

            df = pd.DataFrame(all_records)
            cb(5, 5, "生成热图...")
            os.makedirs(config.OUTPUT_DIR, exist_ok=True)
            heatmap_path = os.path.join(config.OUTPUT_DIR, f"heatmap_{item['domain']}.png")
            tile_paths = generate_heatmap(df, display_name, heatmap_path)

            zone_stats = generate_zone_stats(df, weights)

            combat_stats = aggregate_combat_stats(
                [parse_combat_stats(f, item["steamid"]) for f in dem_files_i]
            )

            results.append({
                "username":      display_name,
                "domain":        item["domain"],
                "steamid":       item["steamid"],
                "heatmap_path":  heatmap_path,
                "tile_paths":    tile_paths,
                "zone_stats":    zone_stats,
                "combat_stats":  combat_stats,
                "demos_found":   item["demos_found"],
                "demo_count":    dem_count,
                "record_count":  len(all_records),
                "round_count":   df["round"].nunique(),
            })

    dl_thread.join()

    # ── Save summary ──────────────────────────────────────────────────────────

    summary_path = os.path.join(config.OUTPUT_DIR, "analysis_summary.json")
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    summary = {
        "max_demos": max_demos,
        "failed": failed,
        "results": [
            {
                "username":     r["username"],
                "domain":       r["domain"],
                "heatmap":      f"heatmap_{r['domain']}.png",
                "tiles":        r.get("tile_paths", {}),
                "zone_stats":   r["zone_stats"],
                "combat_stats": r.get("combat_stats"),
                "demos_found":  r["demos_found"],
                "demo_count":   r["demo_count"],
                "record_count": r["record_count"],
                "round_count":  r["round_count"],
            }
            for r in results
        ],
    }
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    log.info(f"\nPipeline complete: {len(results)}/{total} players analyzed")
    return results, failed
