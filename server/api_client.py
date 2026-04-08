"""
5E Platform API client

Arena search + demo download flow (username → domain → demos → steamid).
Match detail via gate.5eplay.com.
"""

import requests
import urllib3
import logging

urllib3.disable_warnings()

log = logging.getLogger("api_client")

GATE = "https://gate.5eplay.com"
ARENA = "https://arena.5eplay.com"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130.0"}
TIMEOUT = 15


def _get(url):
    r = requests.get(url, timeout=TIMEOUT, verify=False, headers=HEADERS)
    r.raise_for_status()
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"API error: {data.get('message')} (code={data.get('code')})")
    return data["data"]


# ── Match detail ──────────────────────────────────────────────────────────────

def get_match_detail(match_id):
    url = f"{GATE}/crane/http/api/data/match/{match_id}"
    return _get(url)


def _extract_players(match_detail):
    players = []
    for group_key, group_num in [("group_1", 1), ("group_2", 2)]:
        for p in match_detail.get(group_key, []):
            ud = p.get("user_info", {}).get("user_data", {})
            steam = ud.get("steam", {})
            players.append({
                "steamid": steam.get("steamId", ""),
                "username": ud.get("username", ""),
            })
    return players


# ── Arena player search (username → domain → demos) ─────────────────────────

def search_player(username):
    """Search 5E arena by username. Returns (domain, matched_username) or (None, None)."""
    url = f"{ARENA}/api/search?keywords={requests.utils.quote(username)}"
    try:
        r = requests.get(url, timeout=TIMEOUT, verify=False, headers=HEADERS)
        r.raise_for_status()
        d = r.json()
        users = d.get("data", {}).get("user", {}).get("list", [])
        if not users:
            log.warning(f"search_player: no results for '{username}'")
            return None, None
        return users[0]["domain"], users[0]["username"]
    except Exception as e:
        log.warning(f"search_player({username}) failed: {e}")
        return None, None


def get_mirage_demos_by_domain(domain, count=10):
    """Paginate through a player's match history and collect up to `count` Mirage demos.

    Tries match_type=9 (ranked, always has demo_url) first, then falls through to
    other match_types if still short of `count`.

    Returns list of {match_code, demo_url}.
    """
    results = []
    seen_codes = set()

    for candidate in ["?match_type=9", "", "?match_type=1", "?match_type=8"]:
        if len(results) >= count:
            break

        sep = "&" if "?" in candidate else "?"

        for page in range(1, 30):
            if len(results) >= count:
                break
            try:
                url = f"{ARENA}/api/data/player/{domain}{candidate}{sep}page={page}"
                r = requests.get(url, timeout=TIMEOUT, verify=False, headers=HEADERS)
                r.raise_for_status()
                matches = r.json().get("match", [])
                if not matches:
                    break

                new_on_page = False
                for m in matches:
                    mc = m.get("match_code", "")
                    if not mc or mc in seen_codes:
                        continue
                    seen_codes.add(mc)
                    new_on_page = True
                    if m.get("map") == "de_mirage" and m.get("demo_url"):
                        results.append({"match_code": mc, "demo_url": m["demo_url"]})
                        if len(results) >= count:
                            break

                if not new_on_page:
                    break

            except Exception as e:
                log.warning(f"get_mirage_demos {candidate} page {page} failed: {e}")
                break

    if not results:
        log.warning(f"get_mirage_demos: no Mirage demos found for domain {domain}")
    return results


def get_steamid_for_player(match_code, username):
    """Extract a player's steamid from a match detail by matching username."""
    try:
        detail = get_match_detail(match_code)
        for p in _extract_players(detail):
            if p.get("username") == username:
                return str(p["steamid"])
    except Exception as e:
        log.warning(f"get_steamid_for_player({match_code}) failed: {e}")
    return None


# ── Demo download ─────────────────────────────────────────────────────────────

def download_demo(url, save_path, progress_cb=None):
    r = requests.get(url, stream=True, timeout=120, verify=False, headers=HEADERS)
    r.raise_for_status()
    total = int(r.headers.get("content-length", 0))
    downloaded = 0

    with open(save_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=65536):
            f.write(chunk)
            downloaded += len(chunk)
            if progress_cb:
                progress_cb(downloaded, total)

    return save_path
