"""诊断 5E 玩家在指定地图上是否有可下载 demo；不会下载文件。"""

import argparse
import sys

from server.api_client import DemoLookupError, get_demos_by_domain, search_player


for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")


def diagnose(username, map_name, count):
    print(f"\n{'=' * 60}")
    print(f"玩家: {username} | 地图: {map_name}")
    print("=" * 60)

    try:
        domain, matched = search_player(username)
        demos = get_demos_by_domain(domain, map_name, count=count)
    except DemoLookupError as exc:
        print(f"  API 查询失败: {exc}")
        return
    except (RuntimeError, ValueError) as exc:
        print(f"  玩家查询失败: {exc}")
        return

    print(f"  找到玩家: matched_name={matched!r}, domain={domain}")
    if not demos:
        print("  该地图没有可下载的历史 demo（API 已正常返回）。")
        return

    print(f"  可用 demo: {len(demos)}")
    for index, demo in enumerate(demos, start=1):
        print(f"    {index:>2}. match_code={demo['match_code']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "usernames",
        nargs="+",
        help="一个或多个需要诊断的 5E 用户名",
    )
    parser.add_argument("--map", dest="map_name", default="de_mirage")
    parser.add_argument("--count", type=int, default=10)
    args = parser.parse_args()

    if args.count < 1:
        parser.error("--count 必须大于 0")

    for username in args.usernames:
        diagnose(username, args.map_name, args.count)


if __name__ == "__main__":
    main()
