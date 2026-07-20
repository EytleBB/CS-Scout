"""
5E Platform API client

Arena search + demo download flow (username → domain → demos → steamid).
Match detail via gate.5eplay.com.
"""

import ipaddress
import logging
import os
import re
import socket
import tempfile
import threading
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import config

log = logging.getLogger("api_client")

GATE = "https://gate.5eplay.com"
ARENA = "https://arena.5eplay.com"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130.0"}
TIMEOUT = 15
DOWNLOAD_TIMEOUT = 120
DOWNLOAD_MAX_REDIRECTS = 5
GATE_PAGE_SIZE = 30
GATE_MAX_PAGES = 30
PUBLIC_MATCH_TYPES = (9, None, 1, 8)
_DOMAIN_RE = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
_MATCH_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_DATE_RELATIVE_DEMO_RE = re.compile(r"\d{8}/")
_WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)

# Gate occasionally returns a path instead of the absolute CDN URL. The
# Guangzhou endpoint is a stable HTTPS origin for those relative objects; full
# URLs from other 5E regions remain unchanged.
DEMO_DOWNLOAD_BASE = "https://gz-t-demo.5eplaycdn.com/"
_ALLOWED_DEMO_HOST_SUFFIXES = (".5eplaycdn.com",)
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


class DemoLookupError(RuntimeError):
    """No 5E demo-discovery source returned a valid response."""


class UnsafeDemoURLError(ValueError):
    """A demo URL or redirect target failed the outbound-request policy."""


class DemoDownloadTooLarge(ValueError):
    """A compressed demo exceeded the configured download limit."""


def validate_domain(domain):
    """Return a filesystem-safe 5E domain or reject untrusted input."""
    if (
        not isinstance(domain, str)
        or not _DOMAIN_RE.fullmatch(domain)
        or domain.upper() in _WINDOWS_RESERVED_NAMES
    ):
        raise ValueError("invalid 5E player domain")
    return domain


def validate_match_id(match_id):
    """Return a filesystem-safe 5E match ID or reject untrusted input."""
    if (
        not isinstance(match_id, str)
        or not _MATCH_ID_RE.fullmatch(match_id)
        or match_id.upper() in _WINDOWS_RESERVED_NAMES
    ):
        raise ValueError("invalid 5E match ID")
    return match_id


def _is_allowed_demo_host(hostname):
    hostname = hostname.rstrip(".").casefold()
    return any(
        hostname.endswith(suffix) and hostname != suffix[1:]
        for suffix in _ALLOWED_DEMO_HOST_SUFFIXES
    )


def _resolve_demo_host(hostname):
    """Resolve a CDN host and reject every non-public destination address."""
    try:
        answers = socket.getaddrinfo(
            hostname, 443, type=socket.SOCK_STREAM
        )
    except (OSError, UnicodeError) as exc:
        raise UnsafeDemoURLError("demo host could not be resolved") from exc
    addresses = {answer[4][0].split("%", 1)[0] for answer in answers}
    if not addresses:
        raise UnsafeDemoURLError("demo host did not resolve to an address")
    for value in addresses:
        try:
            address = ipaddress.ip_address(value)
        except ValueError as exc:
            raise UnsafeDemoURLError("demo host returned an invalid address") from exc
        # is_global excludes loopback, private, link-local, multicast,
        # unspecified, documentation, and other reserved address ranges.
        if not address.is_global:
            raise UnsafeDemoURLError(
                "demo host resolved to a non-public address"
            )
    return addresses


def normalize_demo_url(url, *, base_url=None, resolve=True):
    """Normalize and validate one 5E demo URL or redirect target.

    A path from Gate is resolved against :data:`DEMO_DOWNLOAD_BASE`; a
    redirect's relative ``Location`` is instead resolved against its current
    validated URL. Only HTTPS 5E CDN hosts are accepted. Public-only DNS
    enforcement is optional because some Chinese networks legitimately return
    RFC1918 CDN acceleration addresses.
    """
    if not isinstance(url, str):
        raise UnsafeDemoURLError("demo URL is not text")
    value = url.strip()
    if not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise UnsafeDemoURLError("demo URL is empty or contains control characters")

    parsed_input = urlsplit(value)
    if not parsed_input.scheme and not parsed_input.netloc:
        if base_url is None:
            relative = value.lstrip("/")
            if _DATE_RELATIVE_DEMO_RE.match(relative):
                relative = f"pug/{relative}"
            value = urljoin(DEMO_DOWNLOAD_BASE, relative)
        else:
            value = urljoin(base_url, value)
    elif not parsed_input.scheme and parsed_input.netloc:
        # Protocol-relative redirects are made explicitly HTTPS before the
        # same host and address checks below.
        value = f"https:{value}"

    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise UnsafeDemoURLError("demo URL contains an invalid port") from exc
    if parsed.scheme.casefold() != "https":
        raise UnsafeDemoURLError("demo URL must use HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeDemoURLError("demo URL must not contain credentials")
    if port not in (None, 443):
        raise UnsafeDemoURLError("demo URL uses a non-standard port")
    if parsed.fragment:
        raise UnsafeDemoURLError("demo URL must not contain a fragment")
    hostname = (parsed.hostname or "").rstrip(".").casefold()
    if not hostname or not _is_allowed_demo_host(hostname):
        raise UnsafeDemoURLError("demo URL host is not an allowed 5E CDN")
    if resolve and config.DEMO_REQUIRE_PUBLIC_DNS:
        _resolve_demo_host(hostname)

    try:
        canonical_host = hostname.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise UnsafeDemoURLError("demo URL host is invalid") from exc
    return urlunsplit(("https", canonical_host, parsed.path or "/", parsed.query, ""))


def _build_retrying_session():
    retries = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=0.5,
        status_forcelist=(500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
    )
    session = requests.Session()
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


_SESSION = _build_retrying_session()
_SESSION_LOCAL = threading.local()


def _session():
    """Return one retrying Session per thread.

    ``requests.Session`` is not guaranteed to be safe for simultaneous use by
    many worker threads. The original session remains the main-thread session
    (and public test hook); fast workers lazily get isolated connection pools.
    """
    session = getattr(_SESSION_LOCAL, "value", None)
    if session is None:
        session = _SESSION if threading.current_thread() is threading.main_thread() else _build_retrying_session()
        _SESSION_LOCAL.value = session
    return session


def _get(url):
    r = _session().get(url, timeout=TIMEOUT, headers=HEADERS)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, dict):
        raise RuntimeError("API returned a non-object response")
    if data.get("code") != 0:
        raise RuntimeError(f"API error: {data.get('message')} (code={data.get('code')})")
    if "data" not in data:
        raise RuntimeError("API response is missing data")
    return data["data"]


# ── Match detail ──────────────────────────────────────────────────────────────

def get_match_detail(match_id):
    match_id = validate_match_id(match_id)
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
    for group_key in ("group_1", "group_2"):
        for p in match_detail.get(group_key, []):
            ud = p.get("user_info", {}).get("user_data", {})
            steam = ud.get("steam", {})
            players.append({
                "steamid": steam.get("steamId", ""),
                "username": ud.get("username", ""),
                "domain": ud.get("domain", ""),
                "uuid": ud.get("uuid", ""),
            })
    return players


def _match_code(match):
    """Return the match identifier used by both Arena and Gate rows."""
    if not isinstance(match, dict):
        return ""
    value = str(
        match.get("match_code")
        or match.get("match_id")
        or match.get("id")
        or ""
    )
    try:
        return validate_match_id(value)
    except ValueError:
        return ""


def _get_player_uuid(domain, matches):
    """Bootstrap a player's Gate UUID from one recent Arena match detail."""
    domain = validate_domain(domain)
    match_code = next((_match_code(match) for match in matches if _match_code(match)), "")
    if not match_code:
        return None

    detail = get_match_detail(match_code)
    for player in _extract_players(detail):
        player_uuid = player.get("uuid")
        if (
            player.get("domain") == domain
            and isinstance(player_uuid, str)
            and len(player_uuid) == 36
        ):
            return player_uuid
    log.warning(
        "UUID bootstrap: domain %s was not present in match %s",
        domain,
        match_code,
    )
    return None


def _get_gate_match_page(player_uuid, page, limit=GATE_PAGE_SIZE):
    """Return one real page from Gate's UUID-based match history."""
    if page < 1 or limit < 1:
        raise ValueError("page and limit must be positive")
    quoted_uuid = requests.utils.quote(player_uuid, safe="")
    url = (
        f"{GATE}/crane/http/api/data/match/list"
        f"?uuid={quoted_uuid}&page={page}&limit={limit}&match_type=-1&cs_type=0"
    )
    matches = _get(url)
    if not isinstance(matches, list):
        raise RuntimeError("Gate match-list data is not a list")
    return matches


# ── Arena player search (username → domain → demos) ─────────────────────────

def search_player(username):
    """Search 5E arena by username. Returns (domain, matched_username) or (None, None)."""
    url = f"{ARENA}/api/search?keywords={requests.utils.quote(username)}"
    try:
        r = _session().get(url, timeout=TIMEOUT, headers=HEADERS)
        r.raise_for_status()
        d = r.json()
        users = d.get("data", {}).get("user", {}).get("list", [])
        if not users:
            log.warning(f"search_player: no results for '{username}'")
            return None, None
        domain = validate_domain(users[0].get("domain"))
        return domain, users[0].get("username") or username
    except Exception as e:
        log.warning(f"search_player({username}) failed: {e}")
        return None, None


def _get_public_matches(domain, match_type=9):
    """Read one bounded Arena recent-match list and validate its response."""
    domain = validate_domain(domain)
    quoted_domain = requests.utils.quote(domain, safe="")
    suffix = "" if match_type is None else f"?match_type={match_type}"
    url = f"{ARENA}/api/data/player/{quoted_domain}{suffix}"
    r = _session().get(url, timeout=TIMEOUT, headers=HEADERS)
    r.raise_for_status()
    payload = r.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Arena returned a non-object response")
    if payload.get("success") is False or payload.get("errcode") not in (None, 0, "0"):
        raise RuntimeError(
            f"Arena API error: {payload.get('message')} "
            f"(errcode={payload.get('errcode')})"
        )
    matches = payload.get("match")
    if not isinstance(matches, list):
        raise RuntimeError("Arena response is missing a match list")
    return matches


def _matches_map(match, map_name):
    value = match.get("map") if isinstance(match, dict) else None
    return isinstance(value, str) and value.casefold() == map_name.casefold()


def _get_gate_demos(player_uuid, map_name, count):
    """Scan bounded Gate history and resolve authoritative detail URLs."""
    results = []
    seen_codes = set()
    detail_errors = []

    for page in range(1, GATE_MAX_PAGES + 1):
        try:
            matches = _get_gate_match_page(player_uuid, page, GATE_PAGE_SIZE)
        except Exception:
            if results:
                log.warning(
                    "Gate page %s failed; returning %s already resolved demo(s)",
                    page,
                    len(results),
                    exc_info=True,
                )
                return results
            raise
        for match in matches:
            match_code = _match_code(match)
            if (
                not match_code
                or match_code in seen_codes
                or not _matches_map(match, map_name)
            ):
                continue
            seen_codes.add(match_code)
            try:
                detail = get_match_detail(match_code)
                if not isinstance(detail, dict):
                    raise RuntimeError("match detail data is not an object")
            except Exception as e:
                detail_errors.append((match_code, e))
                log.warning("Demo detail lookup for %s failed: %s", match_code, e)
                continue

            main = detail.get("main")
            demo_url = main.get("demo_url") if isinstance(main, dict) else None
            if demo_url:
                results.append({"match_code": match_code, "demo_url": demo_url})
                if len(results) >= count:
                    return results

        if len(matches) < GATE_PAGE_SIZE:
            break

    if not results and detail_errors:
        raise RuntimeError(
            f"failed to resolve {len(detail_errors)} Gate match detail response(s)"
        )
    return results


def _get_public_fallback(domain, map_name, count, recent_matches, recent_ok):
    """Use each Arena recent-match variant at most once as a fallback."""
    results = []
    seen_codes = set()
    valid_source = recent_ok
    errors = []

    for match_type in PUBLIC_MATCH_TYPES:
        if match_type == 9:
            if recent_ok:
                matches = recent_matches
            else:
                # Ranked was already attempted for UUID bootstrap. Retrying is
                # delegated to the shared session rather than issuing it twice.
                continue
        else:
            try:
                matches = _get_public_matches(domain, match_type)
                valid_source = True
            except Exception as e:
                errors.append((match_type, e))
                log.warning(
                    "Public demo fallback match_type=%s failed: %s",
                    match_type,
                    e,
                )
                continue

        for match in matches:
            match_code = _match_code(match)
            if not match_code or match_code in seen_codes:
                continue
            seen_codes.add(match_code)
            demo_url = match.get("demo_url") if isinstance(match, dict) else None
            if _matches_map(match, map_name) and demo_url:
                results.append({"match_code": match_code, "demo_url": demo_url})
                if len(results) >= count:
                    return results

    if not valid_source:
        detail = "; ".join(str(error) for _, error in errors[-3:])
        raise DemoLookupError(detail or "5E demo sources unavailable")
    return results


def get_demos_by_domain(domain, map_name, count=10):
    """Return up to ``count`` downloadable demos for a player and map.

    The public Arena endpoint bootstraps one recent match. The player's UUID
    from that match detail then drives real Gate pagination. If UUID bootstrap
    or Gate traversal fails, a bounded scan of Arena's recent lists preserves
    the legacy fallback. A valid empty history returns ``[]``; only a complete
    source outage raises :class:`DemoLookupError`.
    """
    if count <= 0:
        return []
    domain = validate_domain(domain)

    recent_matches = []
    recent_ok = False
    source_error = None
    try:
        recent_matches = _get_public_matches(domain, 9)
        recent_ok = True
    except Exception as e:
        log.warning("Ranked match bootstrap for %s failed: %s", domain, e)

    if recent_ok and recent_matches:
        try:
            player_uuid = _get_player_uuid(domain, recent_matches)
        except Exception as e:
            player_uuid = None
            source_error = ("UUID bootstrap", e)
            log.warning("UUID bootstrap for %s failed: %s", domain, e)
        if player_uuid:
            try:
                results = _get_gate_demos(player_uuid, map_name, count)
                if not results:
                    log.warning(
                        "get_demos: no %s demos found for domain %s",
                        map_name,
                        domain,
                    )
                return results
            except Exception as e:
                source_error = ("Gate history lookup", e)
                log.warning("Gate history lookup for %s failed: %s", domain, e)

    try:
        results = _get_public_fallback(
            domain, map_name, count, recent_matches, recent_ok
        )
    except DemoLookupError as fallback_error:
        if source_error is not None:
            stage, error = source_error
            raise DemoLookupError(
                f"{stage} failed: {error}; "
                f"public fallback failed: {fallback_error}"
            ) from error
        raise

    if source_error is not None and not results:
        stage, error = source_error
        raise DemoLookupError(
            f"{stage} failed: {error}; "
            "public fallback found no downloadable demos"
        ) from error

    if not results:
        log.warning("get_demos: no %s demos found for domain %s", map_name, domain)
    return results


def get_steamid_for_player(match_code, username, domain=None):
    """Resolve steamid by stable domain, falling back to display username."""
    try:
        detail = get_match_detail(match_code)
        players = _extract_players(detail)
        if domain is not None:
            domain = validate_domain(domain)
            for p in players:
                if p.get("domain") == domain and p.get("steamid"):
                    return str(p["steamid"])
        for p in players:
            if p.get("username") == username:
                return str(p["steamid"])
    except Exception as e:
        log.warning(f"get_steamid_for_player({match_code}) failed: {e}")
    return None


# ── Demo download ─────────────────────────────────────────────────────────────

def _content_length(response):
    raw = response.headers.get("content-length")
    if raw in (None, ""):
        return 0
    try:
        value = int(raw)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("download returned an invalid Content-Length") from exc
    if value < 0:
        raise ValueError("download returned a negative Content-Length")
    return value


def _close_response(response):
    close = getattr(response, "close", None)
    if callable(close):
        close()


def download_demo(url, save_path, progress_cb=None):
    """Download one compressed demo with URL, redirect, and size controls.

    Redirects are deliberately handled here instead of by ``requests`` so
    every target is checked before a connection is made. Data is written to a
    same-directory temporary file and atomically published only after success.
    """
    max_bytes = int(config.DEMO_MAX_DOWNLOAD_MB * 1024 ** 2)
    current_url = normalize_demo_url(url)
    response = None
    temp_path = None
    try:
        for redirect_count in range(DOWNLOAD_MAX_REDIRECTS + 1):
            response = _session().get(
                current_url,
                stream=True,
                timeout=DOWNLOAD_TIMEOUT,
                headers=HEADERS,
                allow_redirects=False,
            )
            status_code = int(getattr(response, "status_code", 0))
            if status_code not in _REDIRECT_STATUSES:
                break
            location = response.headers.get("location")
            _close_response(response)
            response = None
            if not location:
                raise UnsafeDemoURLError("demo redirect is missing Location")
            if redirect_count >= DOWNLOAD_MAX_REDIRECTS:
                raise UnsafeDemoURLError("demo download has too many redirects")
            current_url = normalize_demo_url(location, base_url=current_url)
        if response is None:
            raise RuntimeError("demo download returned no response")

        response.raise_for_status()
        total = _content_length(response)
        if total > max_bytes:
            raise DemoDownloadTooLarge(
                f"compressed demo exceeds {config.DEMO_MAX_DOWNLOAD_MB:g} MB"
            )
        if progress_cb:
            progress_cb(0, total)

        save_path = os.path.realpath(os.path.abspath(save_path))
        parent = os.path.dirname(save_path)
        os.makedirs(parent, exist_ok=True)
        downloaded = 0
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=parent,
            prefix=f".{os.path.basename(save_path)}.",
            suffix=".part",
            delete=False,
        ) as output:
            temp_path = output.name
            for chunk in response.iter_content(chunk_size=65536):
                if not chunk:
                    continue
                downloaded += len(chunk)
                if downloaded > max_bytes:
                    raise DemoDownloadTooLarge(
                        f"compressed demo exceeds {config.DEMO_MAX_DOWNLOAD_MB:g} MB"
                    )
                output.write(chunk)
                if progress_cb:
                    progress_cb(downloaded, total)
            if total and downloaded != total:
                raise ValueError(
                    "downloaded size does not match Content-Length"
                )
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp_path, save_path)
        temp_path = None
        return save_path
    finally:
        if response is not None:
            _close_response(response)
        if temp_path and os.path.isfile(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
