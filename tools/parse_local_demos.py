"""Run the active replay parser directly against local 5E Demo files.

This is a development harness, not a replacement for the web pipeline. It
skips player discovery and downloading, then reuses the same parse, round
offset, combat-stat, and player-JSON stages used by the server.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


REPO_DIR = Path(__file__).resolve().parents[1]
SERVER_DIR = REPO_DIR / "server"
sys.path.insert(0, str(SERVER_DIR))

import combat  # noqa: E402
import config  # noqa: E402
import parse  # noqa: E402
import pipeline  # noqa: E402
import player_json  # noqa: E402
from local_demo_pipeline import run_local_demos as service_run_local_demos  # noqa: E402


def _slug(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    return value.strip("-._") or "local-player"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="直接把本地 5E Demo 输入 CS-Scout 回放解析链"
    )
    parser.add_argument(
        "demos", nargs="+", type=Path,
        help="一个或多个 .dem 文件路径，按输入顺序合并回合",
    )
    parser.add_argument("--steamid", required=True, help="目标玩家 SteamID")
    parser.add_argument("--username", default=None, help="输出 JSON 中的玩家名")
    parser.add_argument("--domain", default=None, help="输出 JSON 使用的 domain")
    parser.add_argument("--map", required=True, dest="map_name", help="地图名，例如 de_nuke")
    parser.add_argument(
        "--output", type=Path, default=None,
        help="输出 player JSON；默认写入 server/output/",
    )
    return parser


def _legacy_run_local_demos(
    demo_paths: list[Path],
    *,
    steamid: str,
    username: str,
    domain: str,
    map_name: str,
    output_path: Path,
) -> dict:
    rounds = []
    combat_stats = []
    per_demo = []

    for demo_index, raw_path in enumerate(demo_paths):
        path = raw_path.expanduser().resolve()
        if path.suffix.lower() != ".dem":
            raise ValueError(f"不是 .dem 文件：{path}")
        if not path.is_file():
            raise FileNotFoundError(f"Demo 文件不存在：{path}")

        parsed = parse.parse_demo(str(path), steamid) or []
        pipeline.assemble_round_offset(parsed, demo_index)
        rounds.extend(parsed)

        stats = combat.parse_combat_stats(str(path), steamid)
        if stats is not None:
            combat_stats.append(stats)

        per_demo.append({
            "demo": str(path),
            "rounds": len(parsed),
            "path_points": sum(len(r.get("path", [])) for r in parsed),
            "grenades": sum(len(r.get("grenades", [])) for r in parsed),
            "death_rounds": sum(
                r.get("death_t") is not None for r in parsed
            ),
        })

    aggregate = combat.aggregate_combat_stats(combat_stats)
    payload = player_json.build(
        username, domain, steamid, map_name, rounds, aggregate,
    )
    payload["demos_found"] = len(demo_paths)

    output_path = output_path.expanduser().resolve()
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
        "map": map_name,
        "demos": per_demo,
        "total_rounds": payload["round_count"],
        "total_path_points": sum(item["path_points"] for item in per_demo),
        "total_grenades": sum(item["grenades"] for item in per_demo),
        "total_death_rounds": sum(item["death_rounds"] for item in per_demo),
        "combat_stats": aggregate,
    }


def main() -> int:
    args = _parser().parse_args()
    username = args.username or str(args.steamid)
    domain = args.domain or f"local-{_slug(username)}"
    output = args.output or (
        Path(config.OUTPUT_DIR) / f"player_{_slug(domain)}.json"
    )
    try:
        summary = service_run_local_demos(
            args.demos,
            steamid=str(args.steamid),
            username=username,
            domain=domain,
            map_name=args.map_name,
            output_path=output,
        )
    except (OSError, ValueError, KeyError) as exc:
        print(f"解析失败：{exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
