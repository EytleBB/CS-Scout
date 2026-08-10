"""CSAI 2.0 pipeline — parse opponent demos into per-player replay JSON."""
import json
import logging
import ntpath
import os
import re
import shutil
import stat
import tempfile
import threading
import time
import zipfile
import multiprocessing
import zlib
from concurrent.futures import (
    FIRST_COMPLETED,
    ProcessPoolExecutor,
    ThreadPoolExecutor,
    wait,
)
from queue import Queue
import api_client, config, parse, combat, player_json

log = logging.getLogger("pipeline")

ZIP_MAX_MEMBERS = 64
ZIP_MAX_MEMBER_SIZE = 2 * 1024 ** 3
ZIP_MAX_UNCOMPRESSED_SIZE = 4 * 1024 ** 3
ZIP_COPY_CHUNK = 1024 * 1024
DEFAULT_COMBAT_STATS = {"kd": 0.0, "awp_rate": 0.0}
_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


class AnalysisCancelled(RuntimeError):
    """Raised when the active analysis was cancelled by the user."""


def _raise_if_cancelled(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise AnalysisCancelled("分析已取消")


def _path_is_within(base_dir, path):
    """Return whether ``path`` resolves inside ``base_dir``."""
    base_real = os.path.realpath(os.path.abspath(base_dir))
    path_real = os.path.realpath(os.path.abspath(path))
    try:
        common = os.path.commonpath([base_real, path_real])
    except ValueError:
        return False
    return os.path.normcase(common) == os.path.normcase(base_real)


def _safe_join(base_dir, *parts):
    """Join path components and enforce realpath containment."""
    path = os.path.realpath(os.path.abspath(os.path.join(base_dir, *parts)))
    if not _path_is_within(base_dir, path):
        raise ValueError("path escapes its configured directory")
    return path


def _write_json_atomic(path, payload, *, ensure_ascii=False, indent=None):
    """Write JSON through a same-directory temporary file, then replace it.

    Readers therefore observe either the previous complete document or the new
    complete document, never a partially written player/summary response.
    """
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=directory,
            prefix=f".{os.path.basename(path)}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = handle.name
            json.dump(
                payload,
                handle,
                ensure_ascii=ensure_ascii,
                indent=indent,
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path and os.path.isfile(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


def _safe_zip_parts(info):
    """Validate a ZIP member name on POSIX and Windows path rules."""
    name = info.filename
    if not isinstance(name, str) or not name or "\x00" in name:
        raise ValueError("ZIP contains an invalid member name")
    if name.startswith(("/", "\\")) or ntpath.isabs(name):
        raise ValueError(f"ZIP contains an absolute member: {name!r}")

    normalized = name.replace("\\", "/")
    trimmed = normalized[:-1] if normalized.endswith("/") else normalized
    parts = trimmed.split("/")
    if not trimmed or any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"ZIP contains a traversal member: {name!r}")
    for part in parts:
        if ":" in part or part.endswith((" ", ".")):
            raise ValueError(f"ZIP contains an unsafe member: {name!r}")
        stem = part.split(".", 1)[0].upper()
        if stem in _WINDOWS_RESERVED_NAMES:
            raise ValueError(f"ZIP contains a reserved member: {name!r}")

    mode = info.external_attr >> 16
    if mode and stat.S_ISLNK(mode):
        raise ValueError(f"ZIP contains a symbolic link: {name!r}")
    return parts


def _validated_demo_members(archive, dest_dir):
    infos = archive.infolist()
    if len(infos) > ZIP_MAX_MEMBERS:
        raise ValueError(f"ZIP contains too many members ({len(infos)})")

    total_size = 0
    demo_members = []
    seen_targets = set()
    for info in infos:
        if info.file_size < 0 or info.file_size > ZIP_MAX_MEMBER_SIZE:
            raise ValueError(f"ZIP member is too large: {info.filename!r}")
        total_size += info.file_size
        if total_size > ZIP_MAX_UNCOMPRESSED_SIZE:
            raise ValueError("ZIP uncompressed size exceeds the safety limit")

        parts = _safe_zip_parts(info)
        if info.is_dir():
            continue
        target = _safe_join(dest_dir, *parts)
        target_key = os.path.normcase(target)
        if target_key in seen_targets:
            raise ValueError(f"ZIP contains a duplicate member: {info.filename!r}")
        seen_targets.add(target_key)
        if info.filename.casefold().endswith(".dem"):
            demo_members.append((info, target))
    return demo_members


def _file_matches_zip_member(path, info):
    """Verify a pre-existing member left by an interrupted index commit."""
    try:
        if not os.path.isfile(path) or os.path.getsize(path) != info.file_size:
            return False
        checksum = 0
        with open(path, "rb") as handle:
            while True:
                chunk = handle.read(ZIP_COPY_CHUNK)
                if not chunk:
                    break
                checksum = zlib.crc32(chunk, checksum)
        return checksum & 0xFFFFFFFF == info.CRC
    except OSError:
        return False


def _extract_demo_members(archive, dest_dir, members=None):
    if members is None:
        members = _validated_demo_members(archive, dest_dir)
    touched = []
    temporary_paths = []
    extracted = []
    try:
        for info, target in members:
            parent = os.path.dirname(target)
            os.makedirs(parent, exist_ok=True)
            target = _safe_join(dest_dir, os.path.relpath(target, dest_dir))
            if os.path.exists(target):
                if _file_matches_zip_member(target, info):
                    extracted.append(target)
                    continue
                raise FileExistsError(
                    f"refusing to overwrite existing demo: {info.filename!r}"
                )

            written = 0
            checksum = 0
            with archive.open(info, "r") as source:
                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    dir=parent,
                    prefix=f".{os.path.basename(target)}.",
                    suffix=".part",
                    delete=False,
                ) as output:
                    temp_path = output.name
                    temporary_paths.append(temp_path)
                    while True:
                        chunk = source.read(ZIP_COPY_CHUNK)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > ZIP_MAX_MEMBER_SIZE:
                            raise ValueError(
                                f"ZIP member exceeded its safety limit: {info.filename!r}"
                            )
                        output.write(chunk)
                        checksum = zlib.crc32(chunk, checksum)
            if written != info.file_size:
                raise ValueError(f"ZIP member size changed: {info.filename!r}")
            if checksum & 0xFFFFFFFF != info.CRC:
                raise ValueError(f"ZIP member checksum changed: {info.filename!r}")

            try:
                # A hard link publishes the already-closed temporary file
                # atomically and refuses to overwrite a concurrent/existing
                # target on both Windows and POSIX filesystems.
                os.link(temp_path, target)
                touched.append(target)
            except FileExistsError:
                if not _file_matches_zip_member(target, info):
                    raise
            os.remove(temp_path)
            temporary_paths.remove(temp_path)
            extracted.append(target)
        return extracted
    except Exception:
        for path in temporary_paths:
            if _path_is_within(dest_dir, path) and os.path.isfile(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
        for path in touched:
            if _path_is_within(dest_dir, path) and os.path.isfile(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
        raise


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
_match_download_locks = {}
_match_download_locks_guard = threading.Lock()


def _reserve_match_download_lock(match_id):
    """Reserve a reference-counted single-flight lock for one match."""
    with _match_download_locks_guard:
        entry = _match_download_locks.get(match_id)
        if entry is None:
            entry = {"lock": threading.Lock(), "users": 0}
            _match_download_locks[match_id] = entry
        entry["users"] += 1
        return entry


def _release_match_download_lock(match_id, entry):
    with _match_download_locks_guard:
        entry["users"] -= 1
        if entry["users"] == 0 and _match_download_locks.get(match_id) is entry:
            _match_download_locks.pop(match_id, None)


def _dem_idx_path():
    return _safe_join(config.DEMO_DIR, ".demo_index.json")


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
    match_id = api_client.validate_match_id(match_id)
    _ensure_dem_index()
    with _dem_index_lock:
        paths = _dem_index.get(match_id, [])
        if not isinstance(paths, list):
            paths = []
        valid = [
            os.path.realpath(p)
            for p in paths
            if (
                isinstance(p, str)
                and p.casefold().endswith(".dem")
                and _path_is_within(config.DEMO_DIR, p)
                and os.path.isfile(p)
            )
        ]
        if len(valid) != len(paths):
            _dem_index[match_id] = valid
        return valid


def _index_save(match_id, paths):
    match_id = api_client.validate_match_id(match_id)
    safe_paths = [
        os.path.realpath(path)
        for path in paths
        if (
            isinstance(path, str)
            and path.casefold().endswith(".dem")
            and _path_is_within(config.DEMO_DIR, path)
            and os.path.isfile(path)
        )
    ]
    with _dem_index_lock:
        _dem_index[match_id] = safe_paths
        try:
            _write_json_atomic(_dem_idx_path(), _dem_index, ensure_ascii=True)
        except Exception as e:
            log.warning(f"Could not save demo index: {e}")


# ── Demo disk cleanup ─────────────────────────────────────────────────────────

_ORPHAN_SUFFIXES = (".zip", ".part")
_active_disk_budget = None
_active_disk_budget_lock = threading.Lock()


class DemoDiskBudgetError(RuntimeError):
    """The current task cannot safely consume more demo storage."""


def _demo_storage_files(demo_dir):
    """Return cache files, including transient download/extraction artifacts."""
    entries = []
    if not os.path.isdir(demo_dir):
        return entries
    for root, _, files in os.walk(demo_dir):
        for name in files:
            path = os.path.join(root, name)
            try:
                if not _path_is_within(demo_dir, path):
                    continue
                size = os.path.getsize(path)
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            lower_name = name.casefold()
            is_demo = lower_name.endswith(".dem")
            is_transient = lower_name.endswith(_ORPHAN_SUFFIXES)
            entries.append((mtime, size, path, is_demo, is_transient))
    return entries


def _demo_storage_size(demo_dir):
    return sum(entry[1] for entry in _demo_storage_files(demo_dir))


def _disk_free_bytes(path):
    os.makedirs(path, exist_ok=True)
    return shutil.disk_usage(path).free


def cleanup_orphan_demo_artifacts(demo_dir):
    """Remove only artifacts that our atomic writers can leave after a crash.

    This is called only at task boundaries, when no download or extraction
    worker from this process is active. Completed ``.dem`` files and the main
    index are never removed here.
    """
    removed = 0
    for _, size, path, _, is_transient in _demo_storage_files(demo_dir):
        name = os.path.basename(path)
        is_index_temp = (
            name.startswith(".demo_index.json.") and name.endswith(".tmp")
        )
        if not (is_transient or is_index_temp):
            continue
        try:
            os.remove(path)
            removed += size
        except OSError as exc:
            log.warning("Could not remove orphan demo artifact %s: %s", path, exc)
    if removed:
        log.info("Removed %.1f MB of orphan demo artifacts", removed / 1024 ** 2)
    return removed


def cleanup_demos(demo_dir, limit_gb=None, target_gb=None, min_free_gb=None):
    """Trim oldest demos for both cache-size and filesystem-free-space limits."""
    os.makedirs(demo_dir, exist_ok=True)
    limit_gb = config.DEMO_CACHE_LIMIT_GB if limit_gb is None else float(limit_gb)
    target_gb = config.DEMO_CACHE_TARGET_GB if target_gb is None else float(target_gb)
    min_free_gb = config.DEMO_MIN_FREE_GB if min_free_gb is None else float(min_free_gb)
    limit_bytes = max(0, int(limit_gb * 1024 ** 3))
    target_bytes = min(limit_bytes, max(0, int(target_gb * 1024 ** 3)))
    min_free_bytes = max(0, int(min_free_gb * 1024 ** 3))

    entries = _demo_storage_files(demo_dir)
    total_bytes = sum(entry[1] for entry in entries)
    free_bytes = _disk_free_bytes(demo_dir)
    if total_bytes <= limit_bytes and free_bytes >= min_free_bytes:
        return 0

    # Transients are normally removed before this function. Count any that
    # remain, but delete only completed demos here so an unexpected caller can
    # never unlink a live in-flight file.
    demo_files = sorted(entry for entry in entries if entry[3])
    need_for_cache = max(0, total_bytes - target_bytes)
    need_for_free = max(0, min_free_bytes - free_bytes)
    need_to_free = max(need_for_cache, need_for_free)
    log.info(
        "Demo cleanup: cache %.1f GB, free %.1f GB; need %.1f GB",
        total_bytes / 1024 ** 3,
        free_bytes / 1024 ** 3,
        need_to_free / 1024 ** 3,
    )
    freed = 0
    for _, size, path, _, _ in demo_files:
        if freed >= need_to_free:
            break
        try:
            os.remove(path)
            freed += size
            log.info("Deleted old demo: %s (%.0f MB)", path, size / 1024 ** 2)
        except OSError as exc:
            log.warning("Could not delete %s: %s", path, exc)
    log.info("Demo cleanup freed %.1f GB", freed / 1024 ** 3)
    return freed


class _TaskDiskBudget:
    """Concurrent-safe compressed-download and extraction reservations."""

    def __init__(self, demo_dir):
        self.demo_dir = demo_dir
        self.download_limit = int(
            config.DEMO_TASK_DOWNLOAD_LIMIT_GB * 1024 ** 3
        )
        self.cache_limit = int(config.DEMO_CACHE_LIMIT_GB * 1024 ** 3)
        self.min_free = int(config.DEMO_MIN_FREE_GB * 1024 ** 3)
        self._lock = threading.Lock()
        self._download_consumed = 0
        self._download_reserved = 0
        self._workspace_reserved = 0

    def _check_storage_locked(self, additional, *, download_reserved=None):
        cache_size = _demo_storage_size(self.demo_dir)
        # Existing on-disk partial bytes are already in cache_size. Only other
        # outstanding reservations plus the new prospective allocation are
        # added here.
        if download_reserved is None:
            download_reserved = self._download_reserved
        outstanding = self._workspace_reserved + download_reserved
        if cache_size + outstanding + additional > self.cache_limit:
            raise DemoDiskBudgetError("demo cache limit would be exceeded")
        if _disk_free_bytes(self.demo_dir) - outstanding - additional < self.min_free:
            raise DemoDiskBudgetError("minimum free disk reserve would be crossed")

    def new_download(self):
        token = {"remaining": 0, "accounted": 0, "finished": False}

        def progress(downloaded, total):
            downloaded = max(0, int(downloaded))
            total = max(0, int(total))
            with self._lock:
                if token["finished"]:
                    raise DemoDiskBudgetError("download budget token is closed")
                if downloaded < token["accounted"]:
                    raise DemoDiskBudgetError("download progress moved backwards")
                delta = downloaded - token["accounted"]
                covered = min(delta, token["remaining"])
                remaining = token["remaining"] - covered
                reserved = self._download_reserved - covered
                desired_remaining = max(0, total - downloaded)
                additional = max(0, desired_remaining - remaining)
                if (
                    self._download_consumed
                    + delta
                    + reserved
                    + additional
                    > self.download_limit
                ):
                    raise DemoDiskBudgetError("per-task demo download budget exceeded")
                # iter_content invokes progress after writing a chunk, so
                # cache_size already includes ``delta``. Only bytes promised by
                # Content-Length but not yet written remain reserved here.
                self._check_storage_locked(
                    additional, download_reserved=reserved
                )
                self._download_consumed += delta
                self._download_reserved = reserved + additional
                token["remaining"] = remaining + additional
                token["accounted"] = downloaded

        return token, progress

    def finish_download(self, token):
        with self._lock:
            if token["finished"]:
                return
            token["finished"] = True
            self._download_reserved -= token["remaining"]
            # Bytes acknowledged by progress remain consumed even when a
            # bad/partial archive is removed, so failures cannot bypass the cap.

    def reserve_workspace(self, size):
        size = max(0, int(size))
        with self._lock:
            self._check_storage_locked(size)
            self._workspace_reserved += size
        return size

    def release_workspace(self, size):
        with self._lock:
            self._workspace_reserved = max(0, self._workspace_reserved - int(size))


def _begin_task_storage():
    global _active_disk_budget
    os.makedirs(config.DEMO_DIR, exist_ok=True)
    budget = _TaskDiskBudget(config.DEMO_DIR)
    with _active_disk_budget_lock:
        if _active_disk_budget is not None:
            raise RuntimeError("a demo storage task is already active")
        _active_disk_budget = budget
    try:
        cleanup_orphan_demo_artifacts(config.DEMO_DIR)
        # Start every task at the configured low-water mark.  Waiting until
        # the cache is already over the hard limit leaves too little room for
        # the next archive: a cache just under the limit can reject every
        # download before the ordinary high-water cleanup ever runs.
        cleanup_demos(
            config.DEMO_DIR,
            limit_gb=config.DEMO_CACHE_TARGET_GB,
            target_gb=config.DEMO_CACHE_TARGET_GB,
        )
    except Exception:
        with _active_disk_budget_lock:
            if _active_disk_budget is budget:
                _active_disk_budget = None
        raise
    return budget


def _finish_task_storage(budget):
    global _active_disk_budget
    try:
        cleanup_orphan_demo_artifacts(config.DEMO_DIR)
        cleanup_demos(config.DEMO_DIR)
    finally:
        with _active_disk_budget_lock:
            if _active_disk_budget is budget:
                _active_disk_budget = None


# ── Download + extract ────────────────────────────────────────────────────────

def download_and_extract(match_id, demo_url, dest_dir, progress_cb=None):
    """Download one match once, even when fast workers request it together."""
    try:
        match_id = api_client.validate_match_id(match_id)
    except Exception as exc:
        log.error("Download failed for %r: %s", match_id, exc)
        return []

    lock_entry = _reserve_match_download_lock(match_id)
    try:
        with lock_entry["lock"]:
            return _download_and_extract_once(
                match_id, demo_url, dest_dir, progress_cb=progress_cb
            )
    finally:
        _release_match_download_lock(match_id, lock_entry)


def _download_and_extract_once(match_id, demo_url, dest_dir, progress_cb=None):
    zip_path = None
    workspace_reservation = 0
    budget = _active_disk_budget
    try:
        dest_dir = os.path.realpath(os.path.abspath(dest_dir))
        if not _path_is_within(config.DEMO_DIR, dest_dir):
            raise ValueError("demo destination escapes DEMO_DIR")

        # Recheck the index after entering the match lock. A different fast
        # worker may have completed the same shared match while this one waited.
        cached = _index_lookup(match_id)
        if cached:
            log.info(f"Reusing cached demo: {match_id} ({len(cached)} file(s))")
            return cached

        # Resolve the authoritative Gate URL, falling back to the Arena URL.
        real_url = api_client.get_demo_url(match_id) or demo_url
        os.makedirs(dest_dir, exist_ok=True)
        dest_dir = _safe_join(
            config.DEMO_DIR, os.path.relpath(dest_dir, config.DEMO_DIR)
        )
        zip_path = _safe_join(dest_dir, f"{match_id}.zip")

        log.info(f"Downloading: {match_id}")
        budget_token = None
        budget_progress = None
        if budget is not None:
            budget_token, budget_progress = budget.new_download()

        def combined_progress(downloaded, total):
            if budget_progress is not None:
                budget_progress(downloaded, total)
            if progress_cb is not None:
                progress_cb(downloaded, total)

        try:
            api_client.download_demo(
                real_url,
                zip_path,
                progress_cb=(
                    combined_progress
                    if budget_progress is not None or progress_cb is not None
                    else None
                ),
            )
        finally:
            if budget is not None and budget_token is not None:
                budget.finish_download(budget_token)

        with zipfile.ZipFile(zip_path, "r") as zf:
            members = _validated_demo_members(zf, dest_dir)
            if budget is not None:
                workspace_reservation = budget.reserve_workspace(
                    sum(info.file_size for info, _ in members)
                )
            try:
                dem_files = _extract_demo_members(zf, dest_dir, members)
            finally:
                if budget is not None and workspace_reservation:
                    budget.release_workspace(workspace_reservation)
                    workspace_reservation = 0

        os.remove(zip_path)
        zip_path = None
        if dem_files:
            _index_save(match_id, dem_files)
        return dem_files

    except Exception as e:
        log.error(f"Download failed for {match_id}: {e}")
        if budget is not None and workspace_reservation:
            budget.release_workspace(workspace_reservation)
        if (
            zip_path
            and _path_is_within(config.DEMO_DIR, zip_path)
            and os.path.isfile(zip_path)
        ):
            try:
                os.remove(zip_path)
            except OSError:
                pass
        return []


# ── Username-based pipeline ──────────────────────────────────────────────────

_STEAM_ID_RE = re.compile(r"765\d{14}\Z")


def _validated_player_hint(username, hint):
    """Validate a CDP-resolved 5E identity or request normal search fallback."""
    if not isinstance(hint, dict):
        return None
    name = str(hint.get("username") or username or "").strip()
    steamid = str(hint.get("steamid") or "").strip()
    try:
        domain = api_client.validate_domain(hint.get("domain"))
    except ValueError:
        return None
    if not name or not _STEAM_ID_RE.fullmatch(steamid):
        return None
    return {"username": name, "domain": domain, "steamid": steamid}


def _run_normal(
    usernames, map_name, max_demos=10, progress_cb=None, result_cb=None,
    player_hints=None, cancel_event=None,
):
    total = len(usernames)
    dl_queue = Queue(maxsize=10)

    def emit_progress(i, name, step, msg):
        _raise_if_cancelled(cancel_event)
        if not progress_cb:
            return
        try:
            progress_cb(i, total, name, step, msg)
        except Exception:
            log.exception("Progress callback failed for %s", name)
        _raise_if_cancelled(cancel_event)

    def _download_player(i, username):
        _raise_if_cancelled(cancel_event)
        def cb(step, msg, _i=i, _n=username):
            emit_progress(_i, _n, step, msg)
        hints_required = player_hints is not None
        raw_hint = None
        if isinstance(player_hints, (list, tuple)) and i < len(player_hints):
            raw_hint = player_hints[i]
        hint = _validated_player_hint(username, raw_hint)
        if hints_required and not hint:
            dl_queue.put({
                "type": "player_failed", "i": i, "username": username,
                "reason": "自动识别到的玩家标识不完整，请重新匹配或使用手动模式",
            })
            return
        if hint:
            name = hint["username"]
            domain = hint["domain"]
            steamid = hint["steamid"]
            cb(0, f"准备 {name}...")
        else:
            cb(0, f"搜索 {username}...")
            domain, matched = api_client.search_player(username)
            _raise_if_cancelled(cancel_event)
            if not domain:
                dl_queue.put({"type":"player_failed","i":i,"username":username,
                              "reason":"5E 上未找到该玩家"})
                return
            name = matched or username
            try:
                domain = api_client.validate_domain(domain)
            except ValueError:
                dl_queue.put({"type":"player_failed","i":i,"username":name,
                              "reason":"玩家标识无效"})
                return
            steamid = None
        cb(1, f"获取 {map_name} demo 列表...")
        try:
            demos = api_client.get_demos_by_domain(
                domain, map_name, count=max_demos
            )
            _raise_if_cancelled(cancel_event)
        except api_client.DemoLookupError as e:
            dl_queue.put({"type":"player_failed","i":i,"username":name,
                          "reason":f"获取 demo 列表失败：{e}"})
            return
        if not demos:
            dl_queue.put({"type":"player_failed","i":i,"username":name,
                          "reason":f"无 {map_name} demo 可用"})
            return

        safe_demos = []
        for demo in demos:
            if not isinstance(demo, dict):
                continue
            try:
                match_code = api_client.validate_match_id(demo.get("match_code"))
            except ValueError:
                log.warning("Ignoring unsafe match ID from 5E: %r", demo.get("match_code"))
                continue
            demo_url = demo.get("demo_url")
            if not isinstance(demo_url, str) or not demo_url:
                continue
            safe_demo = {"match_code": match_code, "demo_url": demo_url}
            discovered_steamid = str(demo.get("steamid") or "").strip()
            if _STEAM_ID_RE.fullmatch(discovered_steamid):
                safe_demo["steamid"] = discovered_steamid
            safe_demos.append(safe_demo)
        if not safe_demos:
            dl_queue.put({"type":"player_failed","i":i,"username":name,
                          "reason":"demo 列表包含无效比赛标识"})
            return
        demos = safe_demos

        cb(2, "解析 Steam ID...")
        if not steamid:
            steamid = next(
                (demo.get("steamid") for demo in demos if demo.get("steamid")),
                None,
            )
        for m in demos[:3]:
            _raise_if_cancelled(cancel_event)
            if steamid:
                break
            steamid = api_client.get_steamid_for_player(
                m["match_code"], name, domain=domain
            )
        if not steamid:
            dl_queue.put({"type":"player_failed","i":i,"username":name,
                          "reason":"无法解析 Steam ID"})
            return
        base = {"username":name,"domain":domain,"steamid":steamid,"demos_found":len(demos)}
        opp_dir = _safe_join(config.DEMO_DIR, domain)
        dem_idx = 0
        for mi, m in enumerate(demos):
            _raise_if_cancelled(cancel_event)
            cb(3, f"下载 demo {mi+1}/{len(demos)}...")
            for f in download_and_extract(m["match_code"], m["demo_url"], opp_dir):
                _raise_if_cancelled(cancel_event)
                dl_queue.put({"type":"demo","i":i,**base,"dem_file":f,"dem_idx":dem_idx})
                dem_idx += 1
        if dem_idx == 0:
            dl_queue.put({"type":"player_failed","i":i,"username":name,
                          "reason":"demo 下载全部失败"})
        else:
            dl_queue.put({"type":"player_done","i":i,**base})

    def _download_stage():
        try:
            for i, username in enumerate(usernames):
                try:
                    _raise_if_cancelled(cancel_event)
                    _download_player(i, username)
                except AnalysisCancelled:
                    break
                except Exception as e:
                    log.exception("Unexpected download-stage error for %s", username)
                    detail = str(e) or type(e).__name__
                    dl_queue.put({"type":"player_failed","i":i,
                                  "username":username,
                                  "reason":f"处理玩家失败：{detail}"})
        finally:
            dl_queue.put(None)

    threading.Thread(target=_download_stage, daemon=True).start()

    results, failed = [], []
    rec, demf = {}, {}
    cancelled = False
    while True:
        item = dl_queue.get()
        if item is None: break
        if cancel_event is not None and cancel_event.is_set():
            cancelled = True
            continue
        t, i, name = item["type"], item["i"], item["username"]
        def cb(step, msg, _i=i, _n=name):
            emit_progress(_i, _n, step, msg)
        if t == "player_failed":
            rec.pop(i, None); demf.pop(i, None)
            failed.append({"username":name,"reason":item["reason"]}); cb(6, item["reason"]); continue
        if t == "demo":
            rec.setdefault(i, []); demf.setdefault(i, [])
            demf[i].append(item["dem_file"])
            cb(4, f"解析 demo {len(demf[i])}...")
            try:
                records = parse.parse_demo(item["dem_file"], item["steamid"])
                records = assemble_round_offset(records or [], item["dem_idx"])
            except Exception:
                log.exception("Demo parse failed unexpectedly: %s", item["dem_file"])
                continue
            if records:
                rec[i].extend(records)
            continue
        if t == "player_done":
            rounds = rec.pop(i, []); files = demf.pop(i, [])
            if not rounds:
                reason = "未找到可用回合数据"
                failed.append({"username":name,"reason":reason}); cb(6, reason); continue
            cb(5, "生成回放数据...")
            parsed_stats = []
            for dem_file in files:
                try:
                    parsed_stats.append(
                        combat.parse_combat_stats(dem_file, item["steamid"])
                    )
                except Exception:
                    log.exception(
                        "Combat parse failed unexpectedly: %s", dem_file
                    )
            try:
                cstats = combat.aggregate_combat_stats(parsed_stats)
            except Exception:
                log.exception("Combat aggregation failed unexpectedly for %s", name)
                cstats = None
            cstats = cstats or DEFAULT_COMBAT_STATS.copy()

            try:
                pj = player_json.build(name, item["domain"], item["steamid"],
                                       map_name, rounds, cstats)
                pj["demos_found"] = item["demos_found"]
                os.makedirs(config.OUTPUT_DIR, exist_ok=True)
                jpath = _safe_join(
                    config.OUTPUT_DIR, f"player_{item['domain']}.json"
                )
                _write_json_atomic(jpath, pj, ensure_ascii=False)
                result = {"username":name,"domain":item["domain"],
                    "player_json":f"/output/player_{item['domain']}.json",
                    "combat_stats":cstats,"demos_found":item["demos_found"],
                    "round_count":pj["round_count"]}
            except Exception as e:
                log.exception("Could not build player result for %s", name)
                reason = f"生成回放数据失败：{str(e) or type(e).__name__}"
                failed.append({"username":name,"reason":reason})
                cb(6, reason)
                continue

            results.append(result)
            if result_cb:
                try:
                    result_cb(result.copy())
                except Exception:
                    log.exception("Result callback failed for %s", name)

    if cancelled or (cancel_event is not None and cancel_event.is_set()):
        raise AnalysisCancelled("分析已取消")

    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    summary = {"map":map_name,"max_demos":max_demos,"mode":"normal",
               "failed":failed,"results":results}
    summary_path = _safe_join(config.OUTPUT_DIR, "analysis_summary.json")
    _write_json_atomic(
        summary_path, summary, ensure_ascii=False, indent=2
    )
    log.info(f"Pipeline complete: {len(results)}/{total} players")
    return results, failed


def run(
    usernames, map_name, max_demos=10, progress_cb=None, result_cb=None,
    player_hints=None, cancel_event=None,
):
    """Run the stable pipeline within one bounded demo-storage task."""
    budget = _begin_task_storage()
    try:
        return _run_normal(
            usernames, map_name, max_demos=max_demos,
            progress_cb=progress_cb, result_cb=result_cb,
            player_hints=player_hints,
            cancel_event=cancel_event,
        )
    finally:
        _finish_task_storage(budget)


# ── Fast concurrent pipeline ─────────────────────────────────────────────────

def _prepare_fast_player(
    i, username, map_name, max_demos, emit_progress, player_hint=None,
    require_hint=False,
):
    """Resolve one player and return immutable work metadata for fast mode."""
    def cb(step, msg):
        emit_progress(i, username, step, msg)

    hint = _validated_player_hint(username, player_hint)
    if require_hint and not hint:
        return None, {
            "username": username,
            "reason": "自动识别到的玩家标识不完整，请重新匹配或使用手动模式",
        }
    if hint:
        name = hint["username"]
        domain = hint["domain"]
        steamid = hint["steamid"]
        cb(0, f"准备 {name}...")
    else:
        cb(0, f"搜索 {username}...")
        domain, matched = api_client.search_player(username)
        if not domain:
            return None, {"username": username, "reason": "5E 上未找到该玩家"}
        name = matched or username
        try:
            domain = api_client.validate_domain(domain)
        except ValueError:
            return None, {"username": name, "reason": "玩家标识无效"}
        steamid = None

    emit_progress(i, name, 1, f"获取 {map_name} demo 列表...")
    try:
        demos = api_client.get_demos_by_domain(
            domain, map_name, count=max_demos
        )
    except api_client.DemoLookupError as exc:
        return None, {
            "username": name,
            "reason": f"获取 demo 列表失败：{exc}",
        }
    if not demos:
        return None, {"username": name, "reason": f"无 {map_name} demo 可用"}

    safe_demos = []
    for demo in demos:
        if not isinstance(demo, dict):
            continue
        try:
            match_code = api_client.validate_match_id(demo.get("match_code"))
        except ValueError:
            log.warning("Ignoring unsafe match ID from 5E: %r", demo.get("match_code"))
            continue
        demo_url = demo.get("demo_url")
        if not isinstance(demo_url, str) or not demo_url:
            continue
        safe_demo = {"match_code": match_code, "demo_url": demo_url}
        discovered_steamid = str(demo.get("steamid") or "").strip()
        if _STEAM_ID_RE.fullmatch(discovered_steamid):
            safe_demo["steamid"] = discovered_steamid
        safe_demos.append(safe_demo)
    if not safe_demos:
        return None, {
            "username": name,
            "reason": "demo 列表包含无效比赛标识",
        }

    emit_progress(i, name, 2, "解析 Steam ID...")
    if not steamid:
        steamid = next(
            (
                demo.get("steamid")
                for demo in safe_demos
                if demo.get("steamid")
            ),
            None,
        )
    for demo in safe_demos[:3]:
        if steamid:
            break
        steamid = api_client.get_steamid_for_player(
            demo["match_code"], name, domain=domain
        )
    if not steamid:
        return None, {"username": name, "reason": "无法解析 Steam ID"}

    return {
        "i": i,
        "username": name,
        "domain": domain,
        "steamid": steamid,
        "demos": safe_demos,
        "demos_found": len(safe_demos),
        "opp_dir": _safe_join(config.DEMO_DIR, domain),
    }, None


class _FastDownloadProgress:
    """Thread-safe aggregate byte progress for one player's parallel demos."""

    def __init__(self, context, emit_progress):
        self._context = context
        self._emit_progress = emit_progress
        self._total = len(context["demos"])
        self._fractions = [0.0] * self._total
        self._processed = set()
        self._last_percent = -1
        self._last_emit = 0.0
        self._lock = threading.Lock()

    def _snapshot(self, force=False):
        now = time.monotonic()
        percent = int(round(
            100.0 * sum(self._fractions) / max(1, self._total)
        ))
        if not force and percent < self._last_percent + 2 and now - self._last_emit < 1.0:
            return None
        self._last_percent = max(self._last_percent, percent)
        self._last_emit = now
        return len(self._processed), max(self._last_percent, percent)

    def update(self, demo_order, downloaded, total):
        with self._lock:
            if total:
                self._fractions[demo_order] = max(
                    self._fractions[demo_order],
                    min(1.0, max(0.0, float(downloaded) / float(total))),
                )
            snapshot = self._snapshot()
        if snapshot is not None:
            self._emit(*snapshot)

    def finish(self, demo_order):
        with self._lock:
            self._fractions[demo_order] = 1.0
            self._processed.add(demo_order)
            snapshot = self._snapshot(force=True)
        self._emit(*snapshot)

    def _emit(self, processed, percent):
        self._emit_progress(
            self._context["i"], self._context["username"], 3,
            f"下载 demo {processed}/{self._total} · {percent}%...",
        )


def _download_fast_demo(context, demo_order, download_progress):
    demo = context["demos"][demo_order]
    try:
        return download_and_extract(
            demo["match_code"], demo["demo_url"], context["opp_dir"],
            progress_cb=lambda downloaded, total: download_progress.update(
                demo_order, downloaded, total
            ),
        )
    finally:
        # A 404 or a cached/shared match is still completed work from the
        # task's point of view, even when no byte callback was emitted.
        download_progress.finish(demo_order)


def _parse_fast_demo_worker(steamid, demo_order, file_order, dem_file):
    """Process-safe CPU task; all arguments and results are picklable."""
    parser = None
    events = None
    classified = None
    try:
        records, parser, events, classified = parse.parse_demo_with_context(
            dem_file, steamid
        )
        records = records or []
    except Exception:
        log.exception("Demo parse failed unexpectedly: %s", dem_file)
        records = []

    stats = None
    if parser is not None and events is not None:
        try:
            stats = combat.parse_combat_stats_from_context(
                parser, events, steamid, classified=classified
            )
        except Exception:
            log.exception("Combat parse failed unexpectedly: %s", dem_file)
    return {
        "demo_order": demo_order,
        "file_order": file_order,
        "dem_file": dem_file,
        "records": records,
        "combat_stats": stats,
    }


def _build_fast_result(context, entries, map_name):
    """Assemble completed demo work in original list/member order."""
    rounds = []
    parsed_stats = []
    for dem_idx, entry in enumerate(sorted(
        entries, key=lambda value: (value["demo_order"], value["file_order"])
    )):
        records = entry.get("records") or []
        if entry.get("combat_stats") is not None:
            parsed_stats.append(entry["combat_stats"])
        if not records:
            continue
        rounds.extend(assemble_round_offset(records, dem_idx))
    if not rounds:
        return None, "未找到可用回合数据"

    try:
        cstats = combat.aggregate_combat_stats(parsed_stats)
    except Exception:
        log.exception(
            "Combat aggregation failed unexpectedly for %s",
            context["username"],
        )
        cstats = None
    cstats = cstats or DEFAULT_COMBAT_STATS.copy()

    try:
        pj = player_json.build(
            context["username"], context["domain"], context["steamid"],
            map_name, rounds, cstats,
        )
        pj["demos_found"] = context["demos_found"]
        os.makedirs(config.OUTPUT_DIR, exist_ok=True)
        jpath = _safe_join(
            config.OUTPUT_DIR, f"player_{context['domain']}.json"
        )
        _write_json_atomic(jpath, pj, ensure_ascii=False)
    except Exception as exc:
        log.exception("Could not build fast player result for %s", context["username"])
        return None, f"生成回放数据失败：{str(exc) or type(exc).__name__}"

    return {
        "username": context["username"],
        "domain": context["domain"],
        "player_json": f"/output/player_{context['domain']}.json",
        "combat_stats": cstats,
        "demos_found": context["demos_found"],
        "round_count": pj["round_count"],
    }, None


def _resolve_fast_workers(value, default, maximum):
    try:
        workers = int(default if value is None else value)
    except (TypeError, ValueError, OverflowError):
        workers = default
    return max(1, min(maximum, workers))


def _available_memory_bytes():
    """Best-effort available physical-memory query without extra packages."""
    if os.name == "nt":
        try:
            import ctypes

            class MemoryStatusEx(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = MemoryStatusEx()
            status.dwLength = ctypes.sizeof(status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.ullAvailPhys)
        except Exception:
            log.debug("Could not query Windows memory", exc_info=True)
    try:
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        available_pages = int(os.sysconf("SC_AVPHYS_PAGES"))
        if page_size > 0 and available_pages > 0:
            return page_size * available_pages
    except (AttributeError, KeyError, OSError, TypeError, ValueError):
        pass
    return None


def _memory_safe_parse_workers(requested):
    """Cap default process fan-out before large demo frames can exhaust RAM."""
    available = _available_memory_bytes()
    if not available:
        return requested
    mib = 1024 ** 2
    reserve = config.FAST_PARSE_MEMORY_RESERVE_MB * mib
    per_worker = config.FAST_PARSE_MEMORY_PER_WORKER_MB * mib
    memory_limit = max(1, (max(0, available - reserve)) // per_worker)
    safe_workers = min(requested, memory_limit)
    if safe_workers < requested:
        log.warning(
            "Fast parse workers capped from %s to %s by available memory",
            requested, safe_workers,
        )
    return safe_workers


def _new_fast_parse_executor(workers):
    """Use spawn so process creation is safe from Flask's background thread."""
    return ProcessPoolExecutor(
        max_workers=workers,
        mp_context=multiprocessing.get_context("spawn"),
    )


def _run_fast(
    usernames, map_name, max_demos=10, progress_cb=None, result_cb=None,
    download_workers=None, parse_workers=None, player_hints=None,
    cancel_event=None,
):
    """Run discovery, downloads and parsing concurrently.

    Network and CPU pools are independent, so completed downloads begin parsing
    while other demos are still in flight. Final output remains deterministic in
    username/demo order even though progress and incremental results may finish
    out of order.
    """
    _raise_if_cancelled(cancel_event)
    total = len(usernames)
    download_worker_count = _resolve_fast_workers(
        download_workers, config.FAST_DOWNLOAD_WORKERS, 32
    )
    parse_worker_count = _resolve_fast_workers(
        parse_workers, config.FAST_PARSE_WORKERS, 16
    )
    if parse_workers is None:
        parse_worker_count = _memory_safe_parse_workers(parse_worker_count)
    discovery_worker_count = max(1, min(5, total or 1))
    progress_lock = threading.Lock()
    pending = {}

    def cancel_pending():
        for future in list(pending):
            future.cancel()

    def emit_progress(i, name, step, msg):
        if cancel_event is not None and cancel_event.is_set():
            cancel_pending()
            raise AnalysisCancelled("分析已取消")
        if not progress_cb:
            return
        try:
            with progress_lock:
                progress_cb(i, total, name, step, msg)
        except Exception:
            log.exception("Progress callback failed for %s", name)
        if cancel_event is not None and cancel_event.is_set():
            cancel_pending()
            raise AnalysisCancelled("分析已取消")

    results_by_index = {}
    failures_by_index = {}
    states = {}

    def record_failure(i, username, reason):
        if i in results_by_index or i in failures_by_index:
            return
        failures_by_index[i] = {"username": username, "reason": reason}
        emit_progress(i, username, 6, reason)

    def maybe_finalize(i):
        state = states.get(i)
        if (
            not state
            or state["finalized"]
            or state["downloads_done"] != state["download_total"]
            or state["parse_pending"] != 0
        ):
            return
        state["finalized"] = True
        context = state["context"]
        if state["downloaded_files"] == 0:
            record_failure(i, context["username"], "demo 下载全部失败")
            return
        emit_progress(i, context["username"], 5, "生成回放数据...")
        try:
            result, reason = _build_fast_result(
                context, state["entries"], map_name
            )
        except Exception as exc:
            log.exception(
                "Unexpected fast result assembly error for %s",
                context["username"],
            )
            reason = (
                "生成回放数据失败："
                f"{str(exc) or type(exc).__name__}"
            )
            record_failure(i, context["username"], reason)
            return
        if result is None:
            record_failure(i, context["username"], reason)
            return
        results_by_index[i] = result
        if result_cb:
            try:
                result_cb(result.copy())
            except Exception:
                log.exception("Result callback failed for %s", context["username"])

    with (
        ThreadPoolExecutor(
            max_workers=discovery_worker_count,
            thread_name_prefix="cs-scout-discovery",
        ) as discovery_pool,
        ThreadPoolExecutor(
            max_workers=download_worker_count,
            thread_name_prefix="cs-scout-download",
        ) as download_pool,
        _new_fast_parse_executor(parse_worker_count) as parse_pool,
    ):
        initial_player_download_limit = max(
            1,
            (download_worker_count + max(1, total) - 1) // max(1, total),
        )
        preparations_remaining = total

        def submit_player_download(i):
            state = states[i]
            context = state["context"]
            demo_order = state["next_download_order"]
            state["next_download_order"] += 1
            state["downloads_inflight"] += 1
            download_future = download_pool.submit(
                _download_fast_demo,
                context,
                demo_order,
                state["download_progress"],
            )
            pending[download_future] = (
                "download", i, (context, demo_order)
            )

        def schedule_available_downloads(preferred=None):
            active = sum(
                state["downloads_inflight"] for state in states.values()
            )
            if preparations_remaining > 0:
                if preferred not in states:
                    return
                state = states[preferred]
                while (
                    active < download_worker_count
                    and state["downloads_inflight"] < initial_player_download_limit
                    and state["next_download_order"] < state["download_total"]
                ):
                    submit_player_download(preferred)
                    active += 1
                return

            # Once every discovery has resolved (including failures), lend all
            # unused slots to the least-loaded unfinished player. This keeps
            # the first wave fair without slowing a scan where only one or two
            # names have downloadable history.
            while active < download_worker_count:
                candidates = [
                    i for i, state in states.items()
                    if state["next_download_order"] < state["download_total"]
                ]
                if not candidates:
                    return
                player_index = min(
                    candidates,
                    key=lambda i: (states[i]["downloads_inflight"], i),
                )
                submit_player_download(player_index)
                active += 1

        for i, username in enumerate(usernames):
            player_hint = None
            if isinstance(player_hints, (list, tuple)) and i < len(player_hints):
                player_hint = player_hints[i]
            future = discovery_pool.submit(
                _prepare_fast_player, i, username, map_name, max_demos,
                emit_progress, player_hint, player_hints is not None,
            )
            pending[future] = ("prepare", i, username)

        while pending:
            if cancel_event is not None and cancel_event.is_set():
                cancel_pending()
                raise AnalysisCancelled("分析已取消")
            completed, _ = wait(
                tuple(pending), timeout=0.2, return_when=FIRST_COMPLETED
            )
            if not completed:
                continue
            touched_players = set()
            for future in completed:
                if cancel_event is not None and cancel_event.is_set():
                    cancel_pending()
                    raise AnalysisCancelled("分析已取消")
                kind, i, payload = pending.pop(future)
                touched_players.add(i)

                if kind == "prepare":
                    preparations_remaining -= 1
                    try:
                        context, failure = future.result()
                    except Exception as exc:
                        log.exception("Unexpected fast discovery error for %s", payload)
                        record_failure(
                            i, payload,
                            f"处理玩家失败：{str(exc) or type(exc).__name__}",
                        )
                        schedule_available_downloads()
                        continue
                    if failure:
                        record_failure(i, failure["username"], failure["reason"])
                        schedule_available_downloads()
                        continue
                    states[i] = {
                        "context": context,
                        "download_total": len(context["demos"]),
                        "next_download_order": 0,
                        "downloads_inflight": 0,
                        "downloads_done": 0,
                        "downloaded_files": 0,
                        "parse_pending": 0,
                        "parses_done": 0,
                        "entries": [],
                        "finalized": False,
                    }
                    states[i]["download_progress"] = _FastDownloadProgress(
                        context, emit_progress
                    )
                    schedule_available_downloads(i)
                    continue

                state = states[i]
                context = state["context"]
                if kind == "download":
                    _, demo_order = payload
                    state["downloads_done"] += 1
                    state["downloads_inflight"] -= 1
                    try:
                        dem_files = future.result() or []
                    except Exception:
                        log.exception(
                            "Unexpected fast download error for %s demo %s",
                            context["username"], demo_order + 1,
                        )
                        dem_files = []
                    if not isinstance(dem_files, (list, tuple)):
                        dem_files = []
                    state["downloaded_files"] += len(dem_files)
                    for file_order, dem_file in enumerate(dem_files):
                        state["parse_pending"] += 1
                        try:
                            parse_future = parse_pool.submit(
                                _parse_fast_demo_worker, context["steamid"],
                                demo_order, file_order, dem_file,
                            )
                        except Exception:
                            # A crashed/broken process pool rejects new work.
                            # Treat that demo as unavailable and keep draining
                            # other players instead of aborting the whole scan.
                            state["parse_pending"] -= 1
                            log.exception(
                                "Could not submit fast parse work: %s",
                                dem_file,
                            )
                            state["entries"].append({
                                "demo_order": demo_order,
                                "file_order": file_order,
                                "dem_file": dem_file,
                                "records": [],
                                "combat_stats": None,
                            })
                            continue
                        pending[parse_future] = (
                            "parse", i, (demo_order, file_order, dem_file)
                        )
                    schedule_available_downloads(i)
                    if (
                        state["downloads_done"] == state["download_total"]
                        and state["downloaded_files"] > 0
                    ):
                        emit_progress(
                            i, context["username"], 4,
                            f"解析 demo {state['parses_done']}/{state['download_total']}...",
                        )
                    continue

                demo_order, file_order, dem_file = payload
                state["parse_pending"] -= 1
                state["parses_done"] += 1
                try:
                    entry = future.result()
                except Exception:
                    log.exception("Unexpected fast parse worker error: %s", dem_file)
                    entry = {
                        "demo_order": demo_order,
                        "file_order": file_order,
                        "dem_file": dem_file,
                        "records": [],
                        "combat_stats": None,
                    }
                state["entries"].append(entry)
                if state["downloads_done"] == state["download_total"]:
                    emit_progress(
                        i, context["username"], 4,
                        f"解析 demo {state['parses_done']}/{state['download_total']}...",
                    )

            for player_index in touched_players:
                maybe_finalize(player_index)

    _raise_if_cancelled(cancel_event)

    results = [results_by_index[i] for i in sorted(results_by_index)]
    failed = [failures_by_index[i] for i in sorted(failures_by_index)]
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    summary = {
        "map": map_name,
        "max_demos": max_demos,
        "mode": "fast",
        "failed": failed,
        "results": results,
    }
    summary_path = _safe_join(config.OUTPUT_DIR, "analysis_summary.json")
    _write_json_atomic(
        summary_path, summary, ensure_ascii=False, indent=2
    )
    log.info(
        "Fast pipeline complete: %s/%s players (%s download, %s parse workers)",
        len(results), total, download_worker_count, parse_worker_count,
    )
    return results, failed


def run_fast(
    usernames, map_name, max_demos=10, progress_cb=None, result_cb=None,
    download_workers=None, parse_workers=None, player_hints=None,
    cancel_event=None,
):
    """Run the fast pipeline within one bounded demo-storage task."""
    budget = _begin_task_storage()
    try:
        return _run_fast(
            usernames, map_name, max_demos=max_demos,
            progress_cb=progress_cb, result_cb=result_cb,
            download_workers=download_workers, parse_workers=parse_workers,
            player_hints=player_hints,
            cancel_event=cancel_event,
        )
    finally:
        _finish_task_storage(budget)
