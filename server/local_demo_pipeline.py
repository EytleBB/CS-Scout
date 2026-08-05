"""Local 5E Demo inspection, session storage, and replay parsing."""

from __future__ import annotations

import json
import logging
import re
import shutil
import time
import uuid
from pathlib import Path

import pandas as pd

from demoparser2 import DemoParser

import combat
import config
import maps
import parse
import pipeline
import player_json


log = logging.getLogger("local_demo")
SESSION_ID_RE = re.compile(r"^[0-9a-f]{32}$")


class LocalDemoError(ValueError):
    """A user-correctable local Demo validation or session error."""


def _slug(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    return value.strip("-._") or "local-player"


def _session_root() -> Path:
    root = Path(config.LOCAL_DEMO_DIR).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _session_path(session_id: str) -> Path:
    if not isinstance(session_id, str) or not SESSION_ID_RE.fullmatch(session_id):
        raise LocalDemoError("无效的本地 Demo 会话")
    root = _session_root()
    path = (root / session_id).resolve()
    if path.parent != root:
        raise LocalDemoError("无效的本地 Demo 会话")
    return path


def create_session() -> tuple[str, Path]:
    root = _session_root()
    for _ in range(5):
        session_id = uuid.uuid4().hex
        path = root / session_id
        try:
            path.mkdir()
        except FileExistsError:
            continue
        return session_id, path
    raise LocalDemoError("无法创建本地 Demo 会话")


def _validate_paths(paths: list[Path]) -> list[Path]:
    if not paths:
        raise LocalDemoError("至少需要一个 Demo 文件")
    if len(paths) > config.LOCAL_DEMO_MAX_FILES:
        raise LocalDemoError(
            f"最多同时上传 {config.LOCAL_DEMO_MAX_FILES} 个 Demo"
        )

    checked = []
    total_size = 0
    for raw_path in paths:
        path = Path(raw_path).resolve()
        if path.suffix.lower() != ".dem":
            raise LocalDemoError("只支持 .dem 文件")
        if not path.is_file():
            raise LocalDemoError(f"Demo 文件不存在：{path.name}")
        size = path.stat().st_size
        if size > config.LOCAL_DEMO_MAX_FILE_BYTES:
            raise LocalDemoError(f"单个 Demo 文件过大：{path.name}")
        total_size += size
        checked.append(path)
    if total_size > config.LOCAL_DEMO_MAX_TOTAL_BYTES:
        raise LocalDemoError("本次上传的 Demo 总大小超过限制")
    return checked


def _players_at_first_round(parser: DemoParser, rounds: list[dict]) -> dict[str, str]:
    if not rounds:
        raise LocalDemoError("Demo 中没有可用回合")
    frame = parser.parse_ticks(
        ["steamid", "name", "team_name"], ticks=[rounds[0]["fe_tick"]]
    )
    frame = frame if isinstance(frame, pd.DataFrame) else pd.DataFrame(frame)
    required = {"steamid", "name"}
    if frame.empty or not required.issubset(frame.columns):
        raise LocalDemoError("Demo 中没有可识别的玩家名单")
    players = {}
    for _, row in frame.iterrows():
        sid = str(row.get("steamid", "")).strip()
        if not sid or sid == "nan":
            continue
        name = str(row.get("name", "")).strip()
        if name == "nan":
            name = ""
        players.setdefault(sid, name)
    if not players:
        raise LocalDemoError("Demo 中没有可识别的玩家名单")
    return players


def _inspect_one(path: Path) -> dict:
    try:
        parser = DemoParser(str(path))
        header = parser.parse_header()
        events = dict(parser.parse_events(
            ["round_freeze_end", "round_announce_match_start", "round_end"],
            other=["tick"],
        ))
        rounds = parse.get_round_table(events)
        players = _players_at_first_round(parser, rounds)
    except LocalDemoError:
        raise
    except Exception as exc:
        log.exception("Could not inspect local Demo %s", path)
        raise LocalDemoError(f"无法读取 Demo：{path.name}") from exc

    map_name = str(header.get("map_name", "")).strip()
    if not map_name:
        raise LocalDemoError(f"Demo 没有地图信息：{path.name}")
    return {
        "path": str(path),
        "name": path.name,
        "size": path.stat().st_size,
        "map": map_name,
        "rounds": len(rounds),
        "players": players,
    }


def inspect_demos(paths: list[Path]) -> dict:
    checked = _validate_paths(paths)
    details = [_inspect_one(path) for path in checked]
    maps_found = {item["map"] for item in details}
    if len(maps_found) != 1:
        raise LocalDemoError("所有 Demo 必须使用同一张地图")
    map_name = next(iter(maps_found))
    if map_name not in maps.available_maps():
        raise LocalDemoError(f"地图资源未准备：{map_name}")

    all_ids = set()
    for item in details:
        all_ids.update(item["players"])
    if not all_ids:
        raise LocalDemoError("这些 Demo 中没有可识别的玩家")

    players = []
    for sid in sorted(all_ids):
        names = [item["players"].get(sid, "") for item in details]
        username = next((name for name in names if name), sid)
        appearances = sum(1 for item in details if sid in item["players"])
        players.append({
            "steamid": sid,
            "username": username,
            "appearances": appearances,
        })
    return {"map": map_name, "files": details, "players": players}


def write_manifest(session_id: str, info: dict, original_names: list[str]) -> None:
    session = _session_path(session_id)
    if not session.is_dir():
        raise LocalDemoError("本地 Demo 会话不存在")
    if len(original_names) != len(info["files"]):
        raise LocalDemoError("本地 Demo 会话文件数量不一致")
    files = []
    for detail, original_name in zip(info["files"], original_names):
        files.append({
            "name": str(original_name)[:255],
            "stored_name": Path(detail["path"]).name,
            "size": detail["size"],
            "rounds": detail["rounds"],
        })
    manifest = {
        "session_id": session_id,
        "created_at": time.time(),
        "map": info["map"],
        "files": files,
        "players": info["players"],
    }
    temporary = session / ".manifest.json.tmp"
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temporary.replace(session / "manifest.json")


def load_manifest(session_id: str) -> dict:
    session = _session_path(session_id)
    manifest_path = session / "manifest.json"
    try:
        with manifest_path.open(encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise LocalDemoError("本地 Demo 会话已失效") from exc
    if manifest.get("session_id") != session_id:
        raise LocalDemoError("本地 Demo 会话无效")
    stored_paths = []
    for item in manifest.get("files", []):
        stored_name = item.get("stored_name", "")
        if not re.fullmatch(r"[0-9a-f]{32}\.dem", stored_name):
            raise LocalDemoError("本地 Demo 会话文件无效")
        path = (session / stored_name).resolve()
        if path.parent != session or not path.is_file():
            raise LocalDemoError("本地 Demo 会话文件缺失")
        stored_paths.append(path)
    if not stored_paths:
        raise LocalDemoError("本地 Demo 会话没有文件")
    manifest["paths"] = stored_paths
    return manifest


def cleanup_session(session_id: str) -> None:
    try:
        shutil.rmtree(_session_path(session_id), ignore_errors=True)
    except LocalDemoError:
        return


def cleanup_expired_sessions() -> None:
    root = _session_root()
    cutoff = time.time() - config.LOCAL_DEMO_SESSION_TTL_SECONDS
    for child in root.iterdir():
        if not child.is_dir():
            continue
        try:
            if child.stat().st_mtime < cutoff:
                shutil.rmtree(child, ignore_errors=True)
        except OSError:
            log.warning("Could not inspect local Demo session %s", child)


def run_local_demos(
    demo_paths: list[Path],
    *,
    steamid: str,
    username: str,
    domain: str,
    map_name: str,
    output_path: Path,
    progress_cb=None,
) -> dict:
    rounds = []
    combat_stats = []
    per_demo = []
    total = len(demo_paths)

    for demo_index, raw_path in enumerate(demo_paths):
        path = Path(raw_path).resolve()
        if progress_cb:
            progress_cb(demo_index, total, f"解析 Demo {demo_index + 1}/{total}...")
        parsed = parse.parse_demo(str(path), steamid) or []
        pipeline.assemble_round_offset(parsed, demo_index)
        rounds.extend(parsed)

        stats = combat.parse_combat_stats(str(path), steamid)
        if stats is not None:
            combat_stats.append(stats)
        per_demo.append({
            "demo": path.name,
            "rounds": len(parsed),
            "path_points": sum(len(r.get("path", [])) for r in parsed),
            "grenades": sum(len(r.get("grenades", [])) for r in parsed),
            "death_rounds": sum(r.get("death_t") is not None for r in parsed),
        })

    aggregate = combat.aggregate_combat_stats(combat_stats)
    payload = player_json.build(
        username, domain, steamid, map_name, rounds, aggregate,
    )
    payload["demos_found"] = len(demo_paths)

    output_path = Path(output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(output_path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temporary.replace(output_path)

    return {
        "output": str(output_path),
        "username": username,
        "steamid": str(steamid),
        "domain": domain,
        "map": map_name,
        "demos": per_demo,
        "total_rounds": payload["round_count"],
        "total_path_points": sum(item["path_points"] for item in per_demo),
        "total_grenades": sum(item["grenades"] for item in per_demo),
        "total_death_rounds": sum(item["death_rounds"] for item in per_demo),
        "combat_stats": aggregate,
    }
