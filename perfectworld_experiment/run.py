"""Command-line entry point for the isolated Perfect World implementation."""

from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path
import sys


if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from perfectworld_experiment.auto_scout import run_auto_state
from perfectworld_experiment.current_match import wait_for_current_match
from perfectworld_experiment.pipeline import probe, run


def _configure_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CS-Scout 完美平台隔离实验版")
    parser.add_argument("action", choices=("auto", "probe", "analyze"))
    parser.add_argument("--steamid", help="仅手动调试模式使用：目标玩家 SteamID64")
    parser.add_argument("--map", dest="map_name", help="仅手动调试模式使用：例如 de_mirage")
    parser.add_argument("--max-demos", type=int, default=3)
    parser.add_argument("--player-name", help="只用于输出显示，不参与查询")
    parser.add_argument(
        "--allow-private-dns",
        action="store_true",
        help="只在国内 CDN 被透明解析到私网地址时使用",
    )
    parser.add_argument(
        "--all-players",
        action="store_true",
        help="自动模式分析整局；默认只分析对手",
    )
    parser.add_argument(
        "--wait-timeout",
        type=float,
        help="自动模式等待进入对局的最长秒数；默认持续等待",
    )
    return parser


def _access_token() -> str:
    """Read the credential without persisting or echoing it."""
    token = os.getenv("CS_SCOUT_PWA_ACCESS_TOKEN", "").strip()
    if token:
        return token
    if sys.stdin.isatty() and sys.stderr.isatty():
        return getpass.getpass("完美平台 access_token（输入不会显示或保存）：").strip()
    return ""


def main(argv: list[str] | None = None) -> int:
    _configure_utf8_stdio()
    args = _parser().parse_args(argv)
    try:
        if args.action == "auto":
            print("正在监听完美平台；进入对局后将自动识别地图和对手，无需输入用户名。")
            state = wait_for_current_match(timeout=args.wait_timeout)
            match = state.current_match
            print(
                f"已检测到当前对局：{match.map_name}，阵容记录 {len(match.players)} 人。"
            )
            result = run_auto_state(
                state,
                args.max_demos,
                all_players=args.all_players,
                require_public_dns=not args.allow_private_dns,
                progress=print,
            )
        else:
            if not args.steamid or not args.map_name:
                print("手动调试模式需要 --steamid 和 --map。", file=sys.stderr)
                return 2
            token = _access_token()
            if not token:
                print(
                    "缺少完美平台 access_token；请在本地交互输入或设置 "
                    "CS_SCOUT_PWA_ACCESS_TOKEN。",
                    file=sys.stderr,
                )
                return 2
        if args.action == "probe":
            result = probe(args.steamid, token, args.map_name, args.max_demos)
        elif args.action == "analyze":
            result = run(
                args.steamid,
                token,
                args.map_name,
                args.max_demos,
                player_name=args.player_name,
                require_public_dns=not args.allow_private_dns,
                progress=print,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        # Protocol exceptions intentionally omit access tokens and signed URLs.
        print(f"失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
