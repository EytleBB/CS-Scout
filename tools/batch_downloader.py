import os
import time
import json
import urllib.parse
from playwright.sync_api import sync_playwright

# === 配置区域 ===
# Legacy helper only: keep personal identifiers out of the public repository.
# Set these two values in the shell before running the script.
TARGET_DOMAIN_ID = os.getenv("CS_SCOUT_PLAYER_DOMAIN", "example-domain")
# 1. 强制筛选 Mirage
TARGET_MAP = "mirage"
# 2. 结果保存文件
OUTPUT_FILE = "mirage_download_links.txt"
# 5E UUID, for example: $env:CS_SCOUT_PLAYER_UUID="..."
HARDCODED_UUID = os.getenv(
    "CS_SCOUT_PLAYER_UUID", "00000000-0000-0000-0000-000000000000"
)


def run_link_extractor():
    print(f"🔗 启动链接提取器 (不下载模式)...")
    print(f"🎯 目标玩家: {TARGET_DOMAIN_ID}")
    print(f"🗺️ 目标地图: {TARGET_MAP}")

    # 清空旧的记录文件
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(viewport={'width': 1280, 'height': 800})
        page = context.new_page()

        # --- 步骤 1: 获取比赛列表 ---
        print(f"\n🔍 [Step 1] 获取比赛列表...")

        match_list_api = (
            f"https://gate.5eplay.com/crane/http/api/data/match/list"
            f"?uuid={HARDCODED_UUID}&page=1&limit=20&match_type=-1&cs_type=0"
        )

        page.goto(match_list_api)
        try:
            content = page.inner_text("body")
            data = json.loads(content)
        except:
            print("❌ 获取比赛列表失败，请检查网络或 UUID")
            return

        matches_to_process = []

        if 'data' in data and isinstance(data['data'], list):
            for m in data['data']:
                match_id = m.get('match_id') or m.get('match_code') or m.get('id')
                map_name = m.get('map', 'unknown').lower()

                # --- 核心筛选逻辑 ---
                if TARGET_MAP and TARGET_MAP not in map_name:
                    # 如果不是 Mirage，直接跳过
                    continue

                if match_id:
                    print(f"   ✅ 命中: {map_name:<10} | ID: {match_id}")
                    matches_to_process.append({"id": match_id, "map": map_name})

        print(f"   📊 共筛选出 {len(matches_to_process)} 场 {TARGET_MAP} 比赛")

        # --- 步骤 2: 逐个提取链接 ---
        # 注入间谍脚本 (劫持 window.open)
        hook_script = """
            window.captured_url = null;
            window.open = function(url) { 
                window.captured_url = url; 
                return null; 
            };
        """

        valid_links = []

        for idx, match in enumerate(matches_to_process):
            match_id = match['id']
            map_name = match['map']
            print(f"\n[处理中 {idx + 1}/{len(matches_to_process)}] ID: {match_id}")

            kernel_url = f"https://arena-next.5eplaycdn.com/home/MatchResult/{match_id}"

            try:
                # 访问页面
                page.goto(kernel_url, wait_until='domcontentloaded')
                page.add_init_script(hook_script)

                # --- 核心升级：智能等待逻辑 (死磕 15秒) ---
                btn = None
                print("   👀 正在寻找下载按钮...", end="", flush=True)

                # 循环检测 15 次，每次间隔 1 秒
                for _ in range(15):
                    # 尝试多种定位器
                    locators = [
                        page.get_by_text("下载DEMO", exact=False),
                        page.get_by_text("下载录像", exact=False),
                        page.get_by_role("button", name="下载")
                    ]

                    found = False
                    for loc in locators:
                        if loc.count() > 0 and loc.first.is_visible():
                            btn = loc.first
                            found = True
                            break

                    if found:
                        print(" 找到了！")
                        break

                    print(".", end="", flush=True)
                    time.sleep(1)

                if btn:
                    # 点击按钮
                    btn.click()

                    # 等待链接捕获
                    download_url = None
                    for _ in range(10):
                        download_url = page.evaluate("window.captured_url")
                        if download_url: break
                        time.sleep(0.5)

                    if download_url:
                        # 处理 fivegamer 协议
                        final_url = download_url
                        if "fivegamer://" in download_url and "url=" in download_url:
                            raw = download_url.split("url=")[-1]
                            final_url = urllib.parse.unquote(raw).split("&")[0]

                        print(f"   🔗 提取成功: {final_url}")

                        # 存入列表
                        valid_links.append(final_url)

                        # 实时写入文件 (防止程序中断丢失数据)
                        with open(OUTPUT_FILE, "a", encoding="utf-8") as f:
                            f.write(final_url + "\n")
                    else:
                        print("\n   ❌ 按钮点了，但没抓到链接 (可能无需 window.open?)")
                else:
                    print("\n   ⚠️ 超时未找到按钮 (可能是Demo已过期)")

            except Exception as e:
                print(f"\n   ⚠️ 处理出错: {e}")

        print("\n" + "=" * 40)
        print(f"🎉 任务完成！")
        print(f"共提取 {len(valid_links)} 个链接")
        print(f"已保存到本地文件: {os.path.abspath(OUTPUT_FILE)}")
        print("=" * 40)

        browser.close()


if __name__ == "__main__":
    run_link_extractor()
