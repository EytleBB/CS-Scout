"""
5E Platform API client

Arena search + demo download flow (username → domain → demos → steamid).
Match detail via gate.5eplay.com.
"""

import requests
import urllib3
import logging
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

urllib3.disable_warnings()

log = logging.getLogger("api_client")

GATE = "https://gate.5eplay.com"
ARENA = "https://arena.5eplay.com"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130.0"}
TIMEOUT = 15
MATCH_PAGE_LIMIT = 30
MAX_MATCH_PAGES = 30


def _build_http_session():
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset(["GET"]),
    )
    session = requests.Session()
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


HTTP = _build_http_session()


class DemoLookupError(RuntimeError):
    """No match-list source could be read after retrying."""


def _get(url):
    r = HTTP.get(url, timeout=TIMEOUT, verify=False, headers=HEADERS)
    r.raise_for_status()
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"API error: {data.get('message')} (code={data.get('code')})")
    return data["data"]


# ── Match detail ──────────────────────────────────────────────────────────────

def get_match_detail(match_id):
    url = f"{GATE}/crane/http/api/data/match/{match_id}"
    return _get(url)


def get_demo_url(match_code):
    """Authoritative demo zip URL from gate match-detail.

    The arena match-list `demo_url` often points at a stale CDN subdomain that
    404s (5E changed arena CDN routing); the gate match-detail `main.demo_url`
    is the correct Tencent-COS URL. Returns None on failure / missing.
    """
    try:
        d = get_match_detail(match_code)
        return d.get("main", {}).get("demo_url") or None
    except Exception as e:
        log.warning(f"get_demo_url({match_code}) failed: {e}")
        return None


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
        r = HTTP.get(url, timeout=TIMEOUT, verify=False, headers=HEADERS)
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


def _get_public_matches(domain, match_type=9):
    suffix = f"?match_type={match_type}" if match_type is not None else ""
    sep = "&" if suffix else "?"
    url = f"{ARENA}/api/data/player/{domain}{suffix}{sep}page=1"
    r = HTTP.get(url, timeout=TIMEOUT, verify=False, headers=HEADERS)
    r.raise_for_status()
    return r.json().get("match", [])


def _player_uuid_from_matches(domain, matches):
    """Resolve the Gate UUID needed by the real paginated match-list API."""
    for match in matches[:3]:
        match_code = match.get("match_code") or match.get("match_id")
        if not match_code:
            continue
        try:
            detail = get_match_detail(match_code)
        except Exception as e:
            log.warning(f"UUID bootstrap from {match_code} failed: {e}")
            continue
        for group_key in ("group_1", "group_2"):
            for player in detail.get(group_key, []):
                user_data = player.get("user_info", {}).get("user_data", {})
                if user_data.get("domain") == domain and user_data.get("uuid"):
                    return user_data["uuid"]
    return None


def _get_gate_match_page(player_uuid, page, limit=MATCH_PAGE_LIMIT):
    url = (
        f"{GATE}/crane/http/api/data/match/list"
        f"?match_type=-1&page={page}&date=0&uuid={player_uuid}"
        f"&limit={limit}&cs_type=0"
    )
    return _get(url) or []


def _get_recent_public_demos(domain, map_name, count):
    """Bounded fallback for players whose Gate UUID cannot be bootstrapped."""
    results = []
    seen_codes = set()
    successful_requests = 0
    last_error = None

    for match_type in (9, None, 1, 8):
        if len(results) >= count:
            break
        try:
            matches = _get_public_matches(domain, match_type)
        except Exception as e:
            last_error = e
            log.warning(f"get_demos public match_type={match_type} failed: {e}")
            continue
        successful_requests += 1
        for match in matches:
            match_code = match.get("match_code", "")
            if not match_code or match_code in seen_codes:
                continue
            seen_codes.add(match_code)
            if match.get("map") != map_name:
                continue
            demo_url = match.get("demo_url") or get_demo_url(match_code)
            if demo_url:
                results.append({"match_code": match_code, "demo_url": demo_url})
                if len(results) >= count:
                    break
    return results, successful_requests, last_error


def get_demos_by_domain(domain, map_name, count=10):
    """Collect up to ``count`` downloadable demos from real Gate history pages."""
    bootstrap_error = None
    try:
        bootstrap_matches = _get_public_matches(domain, 9)
    except Exception as e:
        bootstrap_error = e
        log.warning(f"get_demos bootstrap failed for domain {domain}: {e}")
        bootstrap_matches = []

    player_uuid = _player_uuid_from_matches(domain, bootstrap_matches)
    if not player_uuid:
        results, successful_requests, fallback_error = _get_recent_public_demos(
            domain, map_name, count)
        if not successful_requests and bootstrap_error:
            raise DemoLookupError(str(fallback_error or bootstrap_error))
        if not results:
            log.warning(f"get_demos: no {map_name} demos found for domain {domain}")
        return results

    results = []
    seen_codes = set()
    for page in range(1, MAX_MATCH_PAGES + 1):
        try:
            matches = _get_gate_match_page(player_uuid, page)
        except Exception as e:
            log.warning(f"get_demos Gate page {page} failed: {e}")
            if not results:
                fallback, _, _ = _get_recent_public_demos(domain, map_name, count)
                if fallback:
                    return fallback
                raise DemoLookupError(str(e))
            break
        if not matches:
            break

        for match in matches:
            match_code = match.get("match_id") or match.get("match_code")
            if (not match_code or match_code in seen_codes
                    or match.get("map") != map_name):
                continue
            seen_codes.add(match_code)
            demo_url = get_demo_url(match_code)
            if not demo_url:
                continue
            results.append({"match_code": match_code, "demo_url": demo_url})
            if len(results) >= count:
                return results

        if len(matches) < MATCH_PAGE_LIMIT:
            break

    if not results:
        log.warning(f"get_demos: no {map_name} demos found for domain {domain}")
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
    r = HTTP.get(url, stream=True, timeout=120, verify=False, headers=HEADERS)
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
