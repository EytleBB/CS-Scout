"""CSAI 2.0 pipeline — parse opponent demos into per-player replay JSON."""
import os, json, zipfile, logging, threading
from queue import Queue
import api_client, config, parse, combat, player_json

log = logging.getLogger("pipeline")


def _lookup_demos(domain, map_name, count):
    try:
        return api_client.get_demos_by_domain(domain, map_name, count), None
    except api_client.DemoLookupError as e:
        return [], f"获取 demo 列表失败：{e}"


def assemble_round_offset(records, dem_idx):
    """Offset official_num by dem_idx*1000 so rounds from different demos don't collide."""
    for r in records:
        r["official_num"] += dem_idx * 1000
    return records


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

    # The arena-list demo_url often 404s (stale CDN subdomain); resolve the
    # authoritative URL from gate match-detail, falling back to the arena one.
    real_url = api_client.get_demo_url(match_id) or demo_url

    os.makedirs(dest_dir, exist_ok=True)
    zip_path = os.path.join(dest_dir, f"{match_id}.zip")

    try:
        log.info(f"Downloading: {match_id}")
        api_client.download_demo(real_url, zip_path)

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

def run(usernames, map_name, max_demos=10, progress_cb=None):
    total = len(usernames)
    dl_queue = Queue(maxsize=10)
    cleanup_demos(config.DEMO_DIR)

    def _download_stage():
        for i, username in enumerate(usernames):
            def cb(step, msg, _i=i, _n=username):
                if progress_cb: progress_cb(_i, total, _n, step, msg)
            cb(0, f"搜索 {username}...")
            domain, matched = api_client.search_player(username)
            if not domain:
                dl_queue.put({"type":"player_failed","i":i,"username":username,
                              "reason":"5E 上未找到该玩家"}); continue
            name = matched or username
            cb(1, f"获取 {map_name} demo 列表...")
            demos, lookup_error = _lookup_demos(domain, map_name, max_demos)
            if lookup_error:
                dl_queue.put({"type":"player_failed","i":i,"username":name,
                              "reason":lookup_error}); continue
            if not demos:
                dl_queue.put({"type":"player_failed","i":i,"username":name,
                              "reason":f"无 {map_name} demo 可用"}); continue
            cb(2, "解析 Steam ID...")
            steamid = None
            for m in demos[:3]:
                steamid = api_client.get_steamid_for_player(m["match_code"], name)
                if steamid: break
            if not steamid:
                dl_queue.put({"type":"player_failed","i":i,"username":name,
                              "reason":"无法解析 Steam ID"}); continue
            base = {"username":name,"domain":domain,"steamid":steamid,"demos_found":len(demos)}
            opp_dir = os.path.join(config.DEMO_DIR, domain)
            dem_idx = 0
            for mi, m in enumerate(demos):
                cb(3, f"下载 demo {mi+1}/{len(demos)}...")
                for f in download_and_extract(m["match_code"], m["demo_url"], opp_dir):
                    dl_queue.put({"type":"demo","i":i,**base,"dem_file":f,"dem_idx":dem_idx})
                    dem_idx += 1
            if dem_idx == 0:
                dl_queue.put({"type":"player_failed","i":i,"username":name,
                              "reason":"demo 下载全部失败"})
            else:
                dl_queue.put({"type":"player_done","i":i,**base})
        dl_queue.put(None)

    threading.Thread(target=_download_stage, daemon=True).start()

    results, failed = [], []
    rec, demf = {}, {}
    while True:
        item = dl_queue.get()
        if item is None: break
        t, i, name = item["type"], item["i"], item["username"]
        def cb(step, msg, _i=i, _n=name):
            if progress_cb: progress_cb(_i, total, _n, step, msg)
        if t == "player_failed":
            failed.append({"username":name,"reason":item["reason"]}); cb(0, item["reason"]); continue
        if t == "demo":
            rec.setdefault(i, []); demf.setdefault(i, [])
            demf[i].append(item["dem_file"])
            cb(4, f"解析 demo {len(demf[i])}...")
            records = parse.parse_demo(item["dem_file"], item["steamid"])
            rec[i].extend(assemble_round_offset(records, item["dem_idx"]))
            continue
        if t == "player_done":
            rounds = rec.pop(i, []); files = demf.pop(i, [])
            if not rounds:
                failed.append({"username":name,"reason":"未找到可用回合数据"}); continue
            cb(5, "生成回放数据...")
            cstats = combat.aggregate_combat_stats(
                [combat.parse_combat_stats(f, item["steamid"]) for f in files])
            pj = player_json.build(name, item["domain"], item["steamid"],
                                   map_name, rounds, cstats)
            pj["demos_found"] = item["demos_found"]
            os.makedirs(config.OUTPUT_DIR, exist_ok=True)
            jpath = os.path.join(config.OUTPUT_DIR, f"player_{item['domain']}.json")
            with open(jpath, "w", encoding="utf-8") as f:
                json.dump(pj, f, ensure_ascii=False)
            results.append({"username":name,"domain":item["domain"],
                "player_json":f"/output/player_{item['domain']}.json",
                "combat_stats":cstats,"demos_found":item["demos_found"],
                "round_count":pj["round_count"]})

    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    summary = {"map":map_name,"max_demos":max_demos,"failed":failed,"results":results}
    with open(os.path.join(config.OUTPUT_DIR,"analysis_summary.json"),"w",encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    log.info(f"Pipeline complete: {len(results)}/{total} players")
    return results, failed
