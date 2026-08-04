"""Perfect World -> CS-Scout replay pipeline used by the local merged app."""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
import json
import multiprocessing
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import Callable

from .demo_io import download_and_extract
from .pwa_client import (
    PerfectWorldClient,
    PerfectWorldPlayer,
    normalize_map_name,
    validate_steamid,
)


ROOT = Path(__file__).resolve().parents[1]
SERVER_DIR = ROOT / "server"
DEFAULT_DEMO_DIR = Path(os.getenv(
    "CS_SCOUT_PWA_DEMO_DIR",
    Path(__file__).resolve().parent / "demos",
))
DEFAULT_OUTPUT_DIR = Path(os.getenv(
    "CS_SCOUT_PWA_OUTPUT_DIR",
    Path(__file__).resolve().parent / "output",
))
DEFAULT_COMBAT_STATS = {"kd": 0.0, "awp_rate": 0.0}
PWA_DISCOVERY_WORKERS = 5
PWA_DOWNLOAD_WORKERS = 6
PWA_PARSE_WORKERS = 2
PWA_PARSE_MEMORY_PER_WORKER_MB = 2048
PWA_PARSE_MEMORY_RESERVE_MB = 768


def _load_cs_scout_modules():
    server_path = str(SERVER_DIR)
    if server_path not in sys.path:
        sys.path.insert(0, server_path)
    import combat
    import maps
    import parse
    import player_json

    return combat, maps, parse, player_json


def _write_json_atomic(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}-", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(payload, output, ensure_ascii=False, indent=2)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        Path(temporary_name).unlink(missing_ok=True)
        raise


def _worker_count(value, default: int, maximum: int) -> int:
    try:
        resolved = int(default if value is None else value)
    except (TypeError, ValueError, OverflowError):
        resolved = default
    return max(1, min(maximum, resolved))


def _available_memory_bytes() -> int | None:
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
            pass
    try:
        return int(os.sysconf("SC_PAGE_SIZE")) * int(os.sysconf("SC_AVPHYS_PAGES"))
    except (AttributeError, KeyError, OSError, TypeError, ValueError):
        return None


def _memory_safe_parse_workers(requested: int) -> int:
    available = _available_memory_bytes()
    if not available:
        return requested
    mib = 1024 ** 2
    reserve = PWA_PARSE_MEMORY_RESERVE_MB * mib
    per_worker = PWA_PARSE_MEMORY_PER_WORKER_MB * mib
    return max(1, min(requested, max(0, available - reserve) // per_worker))


def _parse_player_output(
    player_index: int,
    player: PerfectWorldPlayer,
    demos_and_files: list[tuple[str, list[str]]],
    normalized_map: str,
    result_dir: str,
    demos_found: int,
) -> tuple[int, dict[str, object] | None, dict[str, str] | None]:
    """Picklable worker: parse one player's demos and write its final JSON."""
    combat, _maps, parse, player_json = _load_cs_scout_modules()
    rounds: list[dict] = []
    parsed_stats: list[dict] = []
    parsed_demo_count = 0

    for _match_id, dem_files in demos_and_files:
        for dem_file in dem_files:
            if hasattr(parse, "parse_demo_with_context") and hasattr(
                combat, "parse_combat_stats_from_context"
            ):
                try:
                    records, parser, events, classified = parse.parse_demo_with_context(
                        dem_file, player.steamid
                    )
                except Exception:
                    records, parser, events, classified = [], None, None, None
                try:
                    stats = (
                        combat.parse_combat_stats_from_context(
                            parser, events, player.steamid, classified
                        )
                        if parser is not None and events is not None
                        else None
                    )
                except Exception:
                    stats = None
            else:
                try:
                    records = parse.parse_demo(dem_file, player.steamid) or []
                except Exception:
                    records = []
                try:
                    stats = combat.parse_combat_stats(dem_file, player.steamid)
                except Exception:
                    stats = None
            if stats is not None:
                parsed_stats.append(stats)
            if not records:
                continue
            for record in records:
                record["official_num"] += parsed_demo_count * 1000
            rounds.extend(records)
            parsed_demo_count += 1

    if not rounds:
        return player_index, None, {
            "username": player.nickname,
            "reason": "历史 Demo 中没有解析出可用回合",
        }

    cstats = combat.aggregate_combat_stats(parsed_stats) or DEFAULT_COMBAT_STATS.copy()
    domain = f"pwa_{player.steamid}"
    payload = player_json.build(
        player.nickname,
        domain,
        player.steamid,
        normalized_map,
        rounds,
        cstats,
    )
    payload["platform"] = "perfectworld"
    payload["demos_found"] = demos_found
    player_path = Path(result_dir) / f"player_{domain}.json"
    _write_json_atomic(player_path, payload)
    result = {
        "username": player.nickname,
        "domain": domain,
        "player_json": str(player_path),
        "combat_stats": cstats,
        "demos_found": demos_found,
        "round_count": payload["round_count"],
    }
    return player_index, result, None


def probe(
    steamid: str,
    access_token: str,
    map_name: str,
    max_demos: int,
    *,
    client: PerfectWorldClient | None = None,
) -> list[dict[str, str | None]]:
    pwa = client or PerfectWorldClient(steamid, access_token)
    demos = pwa.discover(map_name=map_name, limit=max_demos)
    # Signed URLs contain the token and signature, so probe output exposes only
    # non-secret identifiers.
    return [
        {"match_id": demo.match_id, "map": demo.map_name}
        for demo in demos
    ]


def run(
    steamid: str,
    access_token: str,
    map_name: str,
    max_demos: int,
    *,
    player_name: str | None = None,
    demo_dir: str | os.PathLike[str] = DEFAULT_DEMO_DIR,
    output_dir: str | os.PathLike[str] = DEFAULT_OUTPUT_DIR,
    client: PerfectWorldClient | None = None,
    downloader: Callable[..., list[str]] = download_and_extract,
    require_public_dns: bool = True,
    progress: Callable[[str], None] | None = None,
) -> dict[str, object]:
    safe_steamid = validate_steamid(steamid)
    normalized_map = normalize_map_name(map_name)
    if normalized_map is None:
        raise ValueError(f"不支持的地图：{map_name}")
    if not 1 <= int(max_demos) <= 10:
        raise ValueError("max_demos 必须在 1 到 10 之间")

    combat, maps, parse, player_json = _load_cs_scout_modules()
    maps.load_map(normalized_map)
    pwa = client or PerfectWorldClient(safe_steamid, access_token)
    emit = progress or (lambda _message: None)

    emit("正在查询完美平台对局…")
    demos = pwa.discover(map_name=normalized_map, limit=int(max_demos))
    if not demos:
        raise RuntimeError(f"没有找到 {normalized_map} 的可用 Demo")

    player_demo_dir = Path(demo_dir).resolve() / safe_steamid
    rounds: list[dict] = []
    parsed_stats: list[dict] = []
    parsed_demo_count = 0
    failed_matches: list[str] = []

    for demo_index, demo in enumerate(demos):
        emit(f"正在下载 Demo {demo_index + 1}/{len(demos)}…")
        try:
            dem_files = downloader(
                demo.match_id,
                demo.demo_url,
                player_demo_dir,
                headers=pwa.build_download_headers(),
                require_public_dns=require_public_dns,
            )
        except Exception:
            failed_matches.append(demo.match_id)
            continue
        for dem_file in dem_files:
            emit(f"正在解析 Demo {demo_index + 1}/{len(demos)}…")
            try:
                records = parse.parse_demo(dem_file, safe_steamid) or []
            except Exception:
                records = []
            try:
                stats = combat.parse_combat_stats(dem_file, safe_steamid)
            except Exception:
                stats = None
            if stats is not None:
                parsed_stats.append(stats)
            if not records:
                continue
            for record in records:
                record["official_num"] += parsed_demo_count * 1000
            rounds.extend(records)
            parsed_demo_count += 1

    if not rounds:
        raise RuntimeError("Demo 已查询，但没有解析出目标玩家的可用回合")

    cstats = combat.aggregate_combat_stats(parsed_stats) or DEFAULT_COMBAT_STATS.copy()
    domain = f"pwa_{safe_steamid}"
    display_name = (player_name or safe_steamid).strip() or safe_steamid
    payload = player_json.build(
        display_name,
        domain,
        safe_steamid,
        normalized_map,
        rounds,
        cstats,
    )
    payload["platform"] = "perfectworld"
    payload["demos_found"] = len(demos)

    result_dir = Path(output_dir).resolve()
    player_path = result_dir / f"player_{domain}.json"
    summary_path = result_dir / "analysis_summary.json"
    summary = {
        "platform": "perfectworld",
        "map": normalized_map,
        "max_demos": int(max_demos),
        "failed_matches": failed_matches,
        "results": [
            {
                "username": display_name,
                "domain": domain,
                "player_json": str(player_path),
                "combat_stats": cstats,
                "demos_found": len(demos),
                "round_count": payload["round_count"],
            }
        ],
    }
    _write_json_atomic(player_path, payload)
    _write_json_atomic(summary_path, summary)
    emit("完美平台实验分析完成。")
    return summary


def run_roster(
    players: list[PerfectWorldPlayer],
    account_steamid: str,
    access_token: str,
    map_name: str,
    max_demos: int,
    *,
    current_match_id: str | None = None,
    target_scope: str | None = None,
    demo_dir: str | os.PathLike[str] = DEFAULT_DEMO_DIR,
    output_dir: str | os.PathLike[str] = DEFAULT_OUTPUT_DIR,
    client: PerfectWorldClient | None = None,
    downloader: Callable[..., list[str]] = download_and_extract,
    require_public_dns: bool = True,
    progress: Callable[[str], None] | None = None,
    discovery_workers: int | None = None,
    download_workers: int | None = None,
    parse_workers: int | None = None,
) -> dict[str, object]:
    """Analyze an automatically detected roster with shared Demo downloads."""
    total_started = time.perf_counter()
    safe_account = validate_steamid(account_steamid)
    normalized_map = normalize_map_name(map_name)
    if normalized_map is None:
        raise ValueError(f"不支持的地图：{map_name}")
    if not 1 <= int(max_demos) <= 10:
        raise ValueError("max_demos 必须在 1 到 10 之间")

    ordered_players: list[PerfectWorldPlayer] = []
    seen_players: set[str] = set()
    for player in players:
        steamid = validate_steamid(player.steamid)
        if steamid in seen_players:
            continue
        seen_players.add(steamid)
        ordered_players.append(
            PerfectWorldPlayer(
                str(player.player_id),
                steamid,
                str(player.nickname).strip() or steamid,
            )
        )
    if not ordered_players or len(ordered_players) > 10:
        raise ValueError("自动识别的侦察目标数量必须在 1 到 10 之间")

    _combat, maps, _parse, _player_json = _load_cs_scout_modules()
    maps.load_map(normalized_map)
    pwa = client or PerfectWorldClient(safe_account, access_token)
    emit = progress or (lambda _message: None)
    result_dir = Path(output_dir).resolve()
    shared_demo_dir = Path(demo_dir).resolve() / "shared"
    discovery_worker_count = _worker_count(
        discovery_workers,
        os.getenv("CS_SCOUT_PWA_DISCOVERY_WORKERS", PWA_DISCOVERY_WORKERS),
        5,
    )
    download_worker_count = _worker_count(
        download_workers,
        os.getenv("CS_SCOUT_PWA_DOWNLOAD_WORKERS", PWA_DOWNLOAD_WORKERS),
        12,
    )
    parse_worker_count = _worker_count(
        parse_workers,
        os.getenv("CS_SCOUT_PWA_PARSE_WORKERS", PWA_PARSE_WORKERS),
        4,
    )
    if parse_workers is None:
        parse_worker_count = _memory_safe_parse_workers(parse_worker_count)

    emit(f"已识别 {len(ordered_players)} 名目标，正在查询历史对局…")
    discovery_started = time.perf_counter()
    discoveries: dict[str, list] = {}
    failure_by_index: dict[int, dict[str, str]] = {}

    def discover_player(player_index, player):
        discovery_client = (
            pwa if client is not None
            else PerfectWorldClient(safe_account, access_token)
        )
        return player_index, discovery_client.discover(
                map_name=normalized_map,
                limit=int(max_demos),
                target_steamid=player.steamid,
            )

    completed_discoveries = 0
    with ThreadPoolExecutor(
        max_workers=min(discovery_worker_count, len(ordered_players)),
        thread_name_prefix="pwa-discovery",
    ) as discovery_pool:
        discovery_futures = {
            discovery_pool.submit(discover_player, index, player): (index, player)
            for index, player in enumerate(ordered_players)
        }
        for future in as_completed(discovery_futures):
            index, player = discovery_futures[future]
            try:
                _returned_index, demos = future.result()
                discoveries[player.steamid] = demos
            except Exception:
                discoveries[player.steamid] = []
                failure_by_index[index] = {
                    "username": player.nickname,
                    "reason": "历史对局查询失败",
                }
            completed_discoveries += 1
            emit(
                f"历史对局查询完成（{completed_discoveries}/{len(ordered_players)}）…"
            )
    discovery_seconds = time.perf_counter() - discovery_started

    # Several detected players often share old matches. Cache by match id so a
    # 100–200 MB archive is downloaded and extracted only once.
    download_cache: dict[str, list[str]] = {}
    failed_downloads: set[str] = set()
    unique_demos = {}
    for player in ordered_players:
        for demo in discoveries.get(player.steamid, []):
            unique_demos.setdefault(demo.match_id, demo)
    total_unique = len(unique_demos)
    download_started = time.perf_counter()
    download_headers = pwa.build_download_headers() if unique_demos else {}

    def download_demo(demo):
        return demo.match_id, downloader(
            demo.match_id,
            demo.demo_url,
            shared_demo_dir,
            headers=dict(download_headers),
            require_public_dns=require_public_dns,
        )

    completed_downloads = 0
    if unique_demos:
        with ThreadPoolExecutor(
            max_workers=min(download_worker_count, total_unique),
            thread_name_prefix="pwa-download",
        ) as download_pool:
            download_futures = {
                download_pool.submit(download_demo, demo): demo
                for demo in unique_demos.values()
            }
            for future in as_completed(download_futures):
                demo = download_futures[future]
                try:
                    match_id, dem_files = future.result()
                    download_cache[match_id] = dem_files
                except Exception:
                    failed_downloads.add(demo.match_id)
                completed_downloads += 1
                emit(f"历史 Demo 准备完成（{completed_downloads}/{total_unique}）…")
    download_seconds = time.perf_counter() - download_started

    parse_started = time.perf_counter()
    parse_jobs = []
    for player_index, player in enumerate(ordered_players):
        demos = discoveries.get(player.steamid, [])
        if not demos:
            if player_index not in failure_by_index:
                failure_by_index[player_index] = {
                    "username": player.nickname,
                    "reason": "该地图没有可用历史 Demo",
                }
            continue
        demos_and_files = [
            (demo.match_id, download_cache.get(demo.match_id, []))
            for demo in demos
        ]
        parse_jobs.append(
            (player_index, player, demos_and_files, len(demos))
        )

    result_by_index: dict[int, dict[str, object]] = {}
    completed_players = 0

    def accept_parse_result(parsed):
        nonlocal completed_players
        index, result, player_failure = parsed
        if result is not None:
            result_by_index[index] = result
        if player_failure is not None:
            failure_by_index[index] = player_failure
        completed_players += 1
        emit(f"玩家侦察数据生成完成（{completed_players}/{len(parse_jobs)}）…")

    if parse_worker_count == 1:
        for index, player, demos_and_files, demos_found in parse_jobs:
            accept_parse_result(_parse_player_output(
                index, player, demos_and_files, normalized_map,
                str(result_dir), demos_found,
            ))
    elif parse_jobs:
        with ProcessPoolExecutor(
            max_workers=min(parse_worker_count, len(parse_jobs)),
            mp_context=multiprocessing.get_context("spawn"),
        ) as parse_pool:
            parse_futures = {
                parse_pool.submit(
                    _parse_player_output,
                    index,
                    player,
                    demos_and_files,
                    normalized_map,
                    str(result_dir),
                    demos_found,
                ): (index, player)
                for index, player, demos_and_files, demos_found in parse_jobs
            }
            for future in as_completed(parse_futures):
                index, player = parse_futures[future]
                try:
                    accept_parse_result(future.result())
                except Exception:
                    accept_parse_result((index, None, {
                        "username": player.nickname,
                        "reason": "Demo 并行解析失败",
                    }))
    parse_seconds = time.perf_counter() - parse_started
    results = [result_by_index[index] for index in sorted(result_by_index)]
    failed = [failure_by_index[index] for index in sorted(failure_by_index)]

    summary = {
        "platform": "perfectworld",
        "source": "auto_current_match",
        "current_match_id": current_match_id,
        "target_scope": target_scope,
        "map": normalized_map,
        "max_demos": int(max_demos),
        "targets_detected": len(ordered_players),
        "unique_demos_downloaded": len(download_cache),
        "workers": {
            "discovery": discovery_worker_count,
            "download": download_worker_count,
            "parse": parse_worker_count,
        },
        "timings_s": {
            "discovery": round(discovery_seconds, 2),
            "download_extract": round(download_seconds, 2),
            "parse_build": round(parse_seconds, 2),
            "total": round(time.perf_counter() - total_started, 2),
        },
        "failed": failed,
        "results": results,
    }
    _write_json_atomic(result_dir / "analysis_summary.json", summary)
    emit(f"完美平台自动侦察完成：成功 {len(results)} 人，失败 {len(failed)} 人。")
    return summary
