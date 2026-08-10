"""Local 5E match detection through the Electron CDP endpoint.

The monitor only observes the loopback Chrome DevTools endpoint exposed by the
locally installed 5E client.  It never returns browser storage, cookies, or
WebSocket payloads through the web API; only the detected match, map, and
resolved roster are published.
"""

from __future__ import annotations

import base64
import csv
from copy import deepcopy
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from typing import Iterable
from urllib.parse import urlparse

import requests

import api_client
import maps


log = logging.getLogger("fivee-monitor")

DEFAULT_CDP_PORT = 9222
CDP_RETRY_SECONDS = 2.0
MATCH_RESOLVE_ATTEMPTS = 8
MAX_FRAME_TEXT = 2 * 1024 * 1024
MAX_ENCODED_FRAME = (MAX_FRAME_TEXT * 4 // 3) + 32
MAX_SEEN_MATCHES = 100
MATCH_CANDIDATE_TTL = 10 * 60
PLATFORM_USER_INFO_URL = "https://platform-api.5eplay.com/api/user/info"

_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z"
)
_STEAM_ID_RE = re.compile(r"765\d{14}\Z")
_MAP_CODE_RE = re.compile(r"(?i)(?:^|[^a-z0-9_])(de_[a-z0-9_]+)(?:$|[^a-z0-9_])")
_SELF_KEYS = frozenset({
    "myuuid", "selfuuid", "currentuseruuid", "loginuuid", "accountuuid",
    "myuid", "selfuid", "currentuserid", "loginuid", "accountuid",
})
_MAP_KEYS = frozenset({
    "map", "mapname", "mapcode", "mapid", "mapinfo", "gamemap", "selectedmap",
})
_MAP_ALIASES = {
    "ancient": "de_ancient",
    "anubis": "de_anubis",
    "cache": "de_cache",
    "dust2": "de_dust2",
    "dust_2": "de_dust2",
    "inferno": "de_inferno",
    "mirage": "de_mirage",
    "nuke": "de_nuke",
    "overpass": "de_overpass",
    "train": "de_train",
    "vertigo": "de_vertigo",
    "炙热沙城2": "de_dust2",
    "炙热沙城ii": "de_dust2",
    "荒漠迷城": "de_mirage",
    "炼狱小镇": "de_inferno",
    "核子危机": "de_nuke",
    "死亡游乐园": "de_overpass",
    "远古遗迹": "de_ancient",
    "阿努比斯": "de_anubis",
    "殒命大厦": "de_vertigo",
    "列车停放站": "de_train",
}


def _port_from_environment() -> int:
    try:
        value = int(os.getenv("CS_SCOUT_5E_CDP_PORT", str(DEFAULT_CDP_PORT)))
    except (TypeError, ValueError, OverflowError):
        value = DEFAULT_CDP_PORT
    return value if 1 <= value <= 65535 else DEFAULT_CDP_PORT


def _unique_strings(values: Iterable[object]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _member_uuid(member: object) -> str:
    if isinstance(member, str):
        value = member.strip()
        return value if _UUID_RE.fullmatch(value) else ""
    if isinstance(member, dict):
        for key in ("uuid", "user_uuid", "uid", "id"):
            value = member.get(key)
            if isinstance(value, str) and value.strip():
                value = value.strip()
                return value if _UUID_RE.fullmatch(value) else ""
    return ""


def _team_members(team: object) -> list[str]:
    if not isinstance(team, dict):
        return []
    values: list[str] = []
    rooms = team.get("rooms", [])
    if isinstance(rooms, list):
        for room in rooms:
            if not isinstance(room, dict):
                continue
            members = room.get("members", [])
            if isinstance(members, list):
                values.extend(_member_uuid(member) for member in members)
    direct_members = team.get("members", [])
    if isinstance(direct_members, list):
        values.extend(_member_uuid(member) for member in direct_members)
    return _unique_strings(values)


def normalize_map_name(value: object, allowed_maps: Iterable[str] = ()) -> str:
    """Return a supported ``de_*`` map name from one platform value."""
    if isinstance(value, dict):
        for key in ("name_en", "map_name", "mapName", "name", "code", "value"):
            normalized = normalize_map_name(value.get(key), allowed_maps)
            if normalized:
                return normalized
        return ""
    if not isinstance(value, str):
        return ""
    raw = value.strip()
    if not raw:
        return ""
    folded = raw.casefold().replace(" ", "").replace("-", "_")
    match = _MAP_CODE_RE.search(folded)
    candidate = match.group(1).casefold() if match else ""
    if not candidate:
        candidate = _MAP_ALIASES.get(folded, "")
    allowed = {str(item).casefold() for item in allowed_maps if item}
    if candidate and (not allowed or candidate in allowed):
        return candidate
    return ""


def _extract_map_name(value: object, allowed_maps: Iterable[str]) -> str:
    if not isinstance(value, dict):
        return ""
    priority = (
        "map_name", "mapName", "map", "map_code", "mapCode", "map_info",
        "selected_map",
    )
    for key in priority:
        normalized = normalize_map_name(value.get(key), allowed_maps)
        if normalized:
            return normalized

    pending: list[tuple[object, int]] = [(value, 0)]
    visited = 0
    while pending and visited < 256:
        current, depth = pending.pop(0)
        visited += 1
        if depth > 5:
            continue
        if isinstance(current, dict):
            for key, child in current.items():
                key_name = re.sub(r"[^a-z0-9]", "", str(key).casefold())
                if key_name in _MAP_KEYS:
                    normalized = normalize_map_name(child, allowed_maps)
                    if normalized:
                        return normalized
                if isinstance(child, (dict, list)):
                    pending.append((child, depth + 1))
        elif isinstance(current, list):
            pending.extend((child, depth + 1) for child in current[:32])
    return ""


def _strings_in(value: object, *, depth: int = 0) -> Iterable[str]:
    if depth > 3:
        return
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _strings_in(child, depth=depth + 1)
    elif isinstance(value, list):
        for child in value[:32]:
            yield from _strings_in(child, depth=depth + 1)


def _explicit_self_uuid(game_ctx: dict, roster: set[str]) -> str:
    """Use only fields that explicitly describe the current/local user."""
    pending: list[tuple[object, int]] = [(game_ctx, 0)]
    candidates: set[str] = set()
    visited = 0
    while pending and visited < 192:
        current, depth = pending.pop(0)
        visited += 1
        if depth > 4:
            continue
        if isinstance(current, dict):
            for key, child in current.items():
                key_name = re.sub(r"[^a-z0-9]", "", str(key).casefold())
                if key_name in _SELF_KEYS or key_name.startswith(("my", "self", "currentuser")):
                    candidates.update(
                        text for text in _strings_in(child) if text in roster
                    )
                if key_name not in {"t1", "t2", "rooms", "members"} and isinstance(child, (dict, list)):
                    pending.append((child, depth + 1))
        elif isinstance(current, list):
            pending.extend((child, depth + 1) for child in current[:24])
    return next(iter(candidates)) if len(candidates) == 1 else ""


def parse_game_context(game_ctx: object, allowed_maps: Iterable[str] = ()) -> dict | None:
    """Validate one Comet ``game_ctx`` and return its 5v5 roster."""
    if not isinstance(game_ctx, dict):
        return None
    match_id = str(game_ctx.get("id") or game_ctx.get("match_id") or "").strip()
    try:
        match_id = api_client.validate_match_id(match_id)
    except ValueError:
        return None
    gmi = game_ctx.get("gmi")
    if not isinstance(gmi, dict):
        return None
    team_1 = _team_members(gmi.get("t1"))
    team_2 = _team_members(gmi.get("t2"))
    if len(team_1) != 5 or len(team_2) != 5:
        return None
    roster = team_1 + team_2
    if len(set(roster)) != 10:
        return None
    return {
        "match_id": match_id,
        "map": _extract_map_name(game_ctx, allowed_maps),
        "team_1": team_1,
        "team_2": team_2,
        "self_uuid": _explicit_self_uuid(game_ctx, set(roster)),
    }


def _json_roots(text: str) -> Iterable[object]:
    decoder = json.JSONDecoder()
    position = 0
    yielded = 0
    while position < len(text) and yielded < 16:
        brace = min(
            (index for index in (text.find("{", position), text.find("[", position)) if index >= 0),
            default=-1,
        )
        if brace < 0:
            return
        try:
            value, end = decoder.raw_decode(text, brace)
        except json.JSONDecodeError:
            position = brace + 1
            continue
        yield value
        yielded += 1
        position = max(end, brace + 1)


def _game_contexts_in(value: object) -> Iterable[dict]:
    pending: list[tuple[object, int]] = [(value, 0)]
    visited = 0
    while pending and visited < 384:
        current, depth = pending.pop()
        visited += 1
        if depth > 8:
            continue
        if isinstance(current, dict):
            game_ctx = current.get("game_ctx")
            if isinstance(game_ctx, dict):
                yield game_ctx
            pending.extend(
                (child, depth + 1)
                for child in current.values()
                if isinstance(child, (dict, list))
            )
        elif isinstance(current, list):
            pending.extend(
                (child, depth + 1)
                for child in current[:64]
                if isinstance(child, (dict, list))
            )


def parse_websocket_frame(
    opcode: object,
    payload: object,
    allowed_maps: Iterable[str] = (),
) -> list[dict]:
    """Extract every valid 5v5 match from one CDP WebSocket frame."""
    if not isinstance(payload, str) or not payload:
        return []
    if str(opcode) == "2":
        if len(payload) > MAX_ENCODED_FRAME:
            return []
        try:
            raw = base64.b64decode(payload, validate=False)
        except (ValueError, TypeError):
            return []
        text = raw.decode("utf-8", errors="ignore")
    else:
        text = payload
    text = text[:MAX_FRAME_TEXT]
    found: list[dict] = []
    seen_ids: set[str] = set()
    for root in _json_roots(text):
        for game_ctx in _game_contexts_in(root):
            parsed = parse_game_context(game_ctx, allowed_maps)
            if parsed and not parsed["self_uuid"] and isinstance(root, dict):
                roster = set(parsed["team_1"] + parsed["team_2"])
                parsed["self_uuid"] = _explicit_self_uuid(root, roster)
            if parsed and parsed["match_id"] not in seen_ids:
                seen_ids.add(parsed["match_id"])
                found.append(parsed)
    return found


def _nested_value(value: object, paths: Iterable[tuple[str, ...]]) -> object:
    for path in paths:
        current = value
        for key in path:
            if not isinstance(current, dict) or key not in current:
                current = None
                break
            current = current[key]
        if current not in (None, ""):
            return current
    return ""


def _normalize_player(uuid: str, value: object) -> dict:
    record = value if isinstance(value, dict) else {}
    user_data = _nested_value(record, (("user_info", "user_data"),))
    if isinstance(user_data, dict):
        record = {**record, **user_data}
    steam_id = _nested_value(record, (
        ("steam_id",), ("steamid",), ("steamId",),
        ("steam", "steamId"), ("steam", "steam_id"),
    ))
    username = _nested_value(record, (
        ("username",), ("nickname",), ("nick_name",), ("name",),
    ))
    domain = _nested_value(record, (("domain",), ("user_domain",)))
    record_uuid = _nested_value(record, (("uuid",), ("user_uuid",), ("uid",)))
    return {
        "uuid": str(record_uuid or uuid).strip(),
        "username": str(username or "").strip(),
        "steamid": str(steam_id or "").strip(),
        "domain": str(domain or "").strip(),
    }


def players_from_match_detail(detail: object) -> tuple[dict[str, dict], str]:
    """Return UUID-keyed players and map metadata from a Gate match detail."""
    players: dict[str, dict] = {}
    if isinstance(detail, dict):
        for group_key in ("group_1", "group_2"):
            group = detail.get(group_key, [])
            if not isinstance(group, list):
                continue
            for raw in group:
                player = _normalize_player("", raw)
                if player["uuid"]:
                    players[player["uuid"]] = player
    return players, _extract_map_name(detail if isinstance(detail, dict) else {}, maps.available_maps())


def _players_from_user_info(payload: object, roster: Iterable[str]) -> dict[str, dict]:
    uuids = list(roster)
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data", payload)
    result: dict[str, dict] = {}
    if isinstance(data, dict):
        for uuid in uuids:
            raw = data.get(uuid)
            if raw is not None:
                result[uuid] = _normalize_player(uuid, raw)
        for list_key in ("list", "users", "items"):
            values = data.get(list_key)
            if isinstance(values, list):
                for raw in values:
                    player = _normalize_player("", raw)
                    if player["uuid"] in uuids:
                        result[player["uuid"]] = player
    elif isinstance(data, list):
        for raw in data:
            player = _normalize_player("", raw)
            if player["uuid"] in uuids:
                result[player["uuid"]] = player
    return result


def _runtime_evaluate_value(
    ws_url: str,
    expression: str,
    websocket_module,
    *,
    await_promise: bool = False,
    timeout: float = 8,
) -> object:
    """Evaluate one expression in a verified 5E renderer and return its value."""
    connection = None
    try:
        connection = websocket_module.create_connection(
            ws_url, timeout=timeout, suppress_origin=True
        )
        message_id = int(time.time_ns() % 1_000_000_000) or 1
        connection.send(json.dumps({
            "id": message_id,
            "method": "Runtime.evaluate",
            "params": {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": await_promise,
            },
        }))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            message = json.loads(connection.recv())
            if message.get("id") != message_id:
                continue
            result = message.get("result", {})
            if result.get("exceptionDetails"):
                return None
            return result.get("result", {}).get("value")
    except Exception:
        return None
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass
    return None


def _fetch_user_info_in_page(
    ws_url: str,
    roster: list[str],
    websocket_module,
) -> dict[str, dict]:
    """Resolve public player data inside the logged-in 5E renderer.

    Newer 5E API versions require the client's login authorization.  The token
    is deliberately consumed by Node's HTTPS client *inside* the renderer.  It
    is never returned to Python; only four allow-listed player fields leave the
    page.
    """
    if not ws_url or len(roster) != 10 or any(not _UUID_RE.fullmatch(x) for x in roster):
        return {}
    expression = r"""
(async () => {
  const roster = %s;
  let authorization = "";
  for (const key of ["authorization", "token"]) {
    try {
      const raw = String(globalThis.localStorage.getItem(key) || "");
      const match = raw.match(/(?:Bearer\s+)?(eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)/i);
      if (match) {
        authorization = "Bearer " + match[1];
        break;
      }
    } catch (_) {}
  }
  if (!authorization || typeof globalThis.require !== "function") return {};
  const body = JSON.stringify({uuids: roster});
  return await new Promise((resolve) => {
    try {
      const https = globalThis.require("https");
      const request = https.request({
        hostname: "platform-api.5eplay.com",
        port: 443,
        path: "/api/user/info",
        method: "POST",
        headers: {
          Authorization: authorization,
          "Content-Type": "application/json",
          "Content-Length": Buffer.byteLength(body),
          "User-Agent": "Mozilla/5.0",
        },
      }, (response) => {
        let text = "";
        response.setEncoding("utf8");
        response.on("data", (chunk) => {
          if (text.length <= 512 * 1024) text += chunk;
        });
        response.on("end", () => {
          try {
            const payload = JSON.parse(text);
            const data = payload && payload.code === 0 && payload.data;
            if (!data || typeof data !== "object") return resolve({});
            const safe = {};
            for (const uuid of roster) {
              const raw = data[uuid];
              if (!raw || typeof raw !== "object") continue;
              safe[uuid] = {
                uuid: String(raw.uuid || uuid).slice(0, 64),
                username: String(raw.username || "").slice(0, 128),
                domain: String(raw.domain || "").slice(0, 256),
                steam_id: String(raw.steam_id || raw.steamid || "").slice(0, 32),
              };
            }
            resolve(safe);
          } catch (_) {
            resolve({});
          }
        });
      });
      request.on("error", () => resolve({}));
      request.setTimeout(7000, () => request.destroy());
      request.end(body);
    } catch (_) {
      resolve({});
    }
  });
})()
""" % json.dumps(roster, ensure_ascii=True)
    value = _runtime_evaluate_value(
        ws_url,
        expression,
        websocket_module,
        await_promise=True,
        timeout=10,
    )
    return _players_from_user_info(
        {"data": value} if isinstance(value, dict) else {}, roster
    )


def _matching_map_in_page(
    ws_url: str,
    match_id: str,
    websocket_module,
) -> str:
    """Read only the selected map code cached for this exact 5E match."""
    if not ws_url:
        return ""
    expression = r"""
(() => {
  const matchId = %s;
  try {
    const recent = JSON.parse(globalThis.localStorage.getItem("Recentcompetitioninformation") || "null");
    if (!recent || typeof recent !== "object") return "";
    let matched = false;
    const pending = [[recent, 0]];
    let visited = 0;
    while (pending.length && visited < 256) {
      const [current, depth] = pending.shift();
      visited += 1;
      if (!current || typeof current !== "object" || depth > 8) continue;
      if (String(current.id || current.match_id || current.gid || "") === matchId) {
        matched = true;
        break;
      }
      for (const child of Object.values(current)) {
        if (child && typeof child === "object") pending.push([child, depth + 1]);
      }
    }
    return matched ? String(recent.map_id || recent.mapId || "").slice(0, 64) : "";
  } catch (_) {
    return "";
  }
})()
""" % json.dumps(match_id, ensure_ascii=True)
    value = _runtime_evaluate_value(
        ws_url, expression, websocket_module, timeout=5
    )
    return normalize_map_name(value, maps.available_maps())


def _merge_player_records(primary: dict, secondary: dict) -> dict:
    return {
        key: primary.get(key) or secondary.get(key) or ""
        for key in ("uuid", "username", "steamid", "domain")
    }


def _player_has_exact_identity(player: object) -> bool:
    if not isinstance(player, dict):
        return False
    if not str(player.get("username") or "").strip():
        return False
    if not _STEAM_ID_RE.fullmatch(str(player.get("steamid") or "").strip()):
        return False
    try:
        api_client.validate_domain(player.get("domain"))
    except ValueError:
        return False
    return True


def _steam_id_from_account_id(value: object) -> str:
    try:
        account_id = int(value)
    except (TypeError, ValueError, OverflowError):
        return ""
    if account_id <= 0:
        return ""
    if account_id >= 76561197960265728:
        candidate = str(account_id)
    else:
        candidate = str(76561197960265728 + account_id)
    return candidate if _STEAM_ID_RE.fullmatch(candidate) else ""


def active_steam_id() -> str:
    """Read the active/most-recent local Steam account without credentials."""
    if sys.platform != "win32":
        return ""
    try:
        import winreg
    except ImportError:
        return ""

    for key_path in (
        r"Software\Valve\Steam\ActiveProcess",
        r"Software\Valve\Steam",
    ):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                for value_name in ("ActiveUser", "activeuser"):
                    try:
                        steam_id = _steam_id_from_account_id(
                            winreg.QueryValueEx(key, value_name)[0]
                        )
                    except OSError:
                        continue
                    if steam_id:
                        return steam_id
        except OSError:
            pass

    steam_path = ""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            steam_path = str(winreg.QueryValueEx(key, "SteamPath")[0])
    except OSError:
        pass
    candidates = [
        Path(steam_path) / "config" / "loginusers.vdf" if steam_path else None,
        Path(os.getenv("PROGRAMFILES(X86)", "")) / "Steam" / "config" / "loginusers.vdf",
    ]
    for candidate in candidates:
        if candidate is None or not candidate.is_file():
            continue
        try:
            text = candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        matches = []
        for block in re.finditer(r'"(765\d{14})"\s*\{(.*?)\n\s*\}', text, re.DOTALL):
            body = block.group(2)
            if re.search(r'"(?:MostRecent|AutoLogin)"\s+"1"', body, re.IGNORECASE):
                matches.append(block.group(1))
        unique = _unique_strings(matches)
        if len(unique) == 1:
            return unique[0]
    return ""


def find_5e_executable() -> str:
    """Return a validated local 5E executable path, if installed."""
    candidates: list[Path] = []
    explicit = os.getenv("CS_SCOUT_5E_EXE", "").strip()
    if explicit:
        candidates.append(Path(os.path.expandvars(os.path.expanduser(explicit))))
    for env_name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        root = os.getenv(env_name, "").strip()
        if root:
            candidates.extend((
                Path(root) / "5EClient" / "5EClient.exe",
                Path(root) / "5EPlay" / "5EClient.exe",
            ))
    candidates.append(Path(r"C:\Program Files\5EClient\5EClient.exe"))
    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = str(candidate.resolve(strict=True))
        except (OSError, RuntimeError):
            continue
        key = resolved.casefold()
        if key in seen:
            continue
        seen.add(key)
        if Path(resolved).is_file() and Path(resolved).name.casefold() == "5eclient.exe":
            return resolved
    return ""


def _fivee_running_via_powershell() -> bool | None:
    script = (
        "$p=@(Get-Process -Name 5EClient -ErrorAction SilentlyContinue);"
        "if($p.Count -gt 0){exit 0}else{exit 3}"
    )
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        completed = subprocess.run(
            [
                "powershell.exe", "-NoProfile", "-NonInteractive",
                "-ExecutionPolicy", "Bypass", "-Command", script,
            ],
            capture_output=True,
            timeout=8,
            check=False,
            creationflags=creation_flags,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode == 0:
        return True
    if completed.returncode == 3:
        return False
    return None


def is_5e_running() -> bool | None:
    if sys.platform != "win32":
        return False
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        completed = subprocess.run(
            ["tasklist.exe", "/FI", "IMAGENAME eq 5EClient.exe", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=5,
            check=False,
            creationflags=creation_flags,
        )
    except (OSError, subprocess.SubprocessError):
        return _fivee_running_via_powershell()
    if completed.returncode != 0:
        return _fivee_running_via_powershell()
    try:
        rows = csv.reader(completed.stdout.splitlines())
        return any(row and row[0].casefold() == "5eclient.exe" for row in rows)
    except csv.Error:
        if "5eclient.exe" in completed.stdout.casefold():
            return True
        return _fivee_running_via_powershell()


def _process_name_via_powershell(pid: str) -> str:
    if not pid.isdigit():
        return ""
    script = (
        f"$p=Get-Process -Id {pid} -ErrorAction SilentlyContinue;"
        "if($null -ne $p){[Console]::Write([string]$p.ProcessName)}"
    )
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        completed = subprocess.run(
            [
                "powershell.exe", "-NoProfile", "-NonInteractive",
                "-ExecutionPolicy", "Bypass", "-Command", script,
            ],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=8,
            check=False,
            creationflags=creation_flags,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return completed.stdout.strip() if completed.returncode == 0 else ""


def cdp_listener_is_5e(port: int) -> bool:
    """Confirm that the loopback CDP listener is owned by 5EClient.exe."""
    if sys.platform != "win32":
        return False
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        completed = subprocess.run(
            ["netstat.exe", "-ano", "-p", "TCP"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=5,
            check=False,
            creationflags=creation_flags,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if completed.returncode != 0:
        return False

    listener_pids: set[str] = set()
    expected_endpoints = {f"127.0.0.1:{port}", f"[::1]:{port}"}
    for line in completed.stdout.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].casefold() != "tcp":
            continue
        if parts[1] not in expected_endpoints or parts[-2].casefold() != "listening":
            continue
        if parts[-1].isdigit():
            listener_pids.add(parts[-1])
    if not listener_pids:
        return False

    for pid in listener_pids:
        try:
            process = subprocess.run(
                ["tasklist.exe", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=5,
                check=False,
                creationflags=creation_flags,
            )
            rows = list(csv.reader(process.stdout.splitlines()))
        except (OSError, subprocess.SubprocessError, csv.Error):
            rows = []
        if any(row and row[0].casefold() == "5eclient.exe" for row in rows):
            return True
        if _process_name_via_powershell(pid).casefold() == "5eclient":
            return True
    return False


def _official_5e_signature(executable: str) -> bool:
    """Require a valid signature from the current official 5E publisher."""
    if sys.platform != "win32":
        return False
    encoded_path = base64.b64encode(executable.encode("utf-8")).decode("ascii")
    script = (
        "[Console]::OutputEncoding=[Text.UTF8Encoding]::new();"
        f"$p=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded_path}'));"
        "$s=Get-AuthenticodeSignature -LiteralPath $p;"
        "[PSCustomObject]@{Status=[string]$s.Status;"
        "Subject=[string]$s.SignerCertificate.Subject}|ConvertTo-Json -Compress"
    )
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        completed = subprocess.run(
            [
                "powershell.exe", "-NoProfile", "-NonInteractive",
                "-ExecutionPolicy", "Bypass", "-Command", script,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
            creationflags=creation_flags,
        )
        signature = json.loads(completed.stdout.strip())
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return False
    subject = str(signature.get("Subject") or "").casefold()
    return (
        completed.returncode == 0
        and str(signature.get("Status") or "").casefold() == "valid"
        and ("杭州乐于战网络科技有限公司" in subject or "leyuzhan" in subject)
    )


def launch_5e_with_cdp(executable: str, port: int) -> None:
    """Start (never restart) the official client with loopback-only CDP."""
    path = Path(executable).resolve(strict=True)
    if path.name.casefold() != "5eclient.exe":
        raise ValueError("invalid 5E executable")
    if not _official_5e_signature(str(path)):
        raise PermissionError("5E executable does not have the expected official signature")
    arguments = [
        f"--remote-debugging-port={port}",
        "--remote-debugging-address=127.0.0.1",
    ]
    try:
        subprocess.Popen(
            [str(path), *arguments],
            cwd=str(path.parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        return
    except OSError as exc:
        if sys.platform != "win32" or getattr(exc, "winerror", None) != 740:
            raise

    # Current 5E releases request elevation in their application manifest.
    # Launch through a hidden elevated PowerShell helper which drains the child
    # stdout/stderr.  Direct ShellExecute inheritance lets 5E print its bearer
    # token into the CS-Scout console, which is both noisy and unsafe to share.
    import ctypes

    launch_data = base64.b64encode(json.dumps({
        "path": str(path),
        "cwd": str(path.parent),
        "arguments": subprocess.list2cmdline(arguments),
    }).encode("utf-8")).decode("ascii")
    script = (
        "$ErrorActionPreference='Stop';"
        f"$d=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{launch_data}'))|ConvertFrom-Json;"
        "$p=New-Object Diagnostics.ProcessStartInfo;"
        "$p.FileName=$d.path;$p.WorkingDirectory=$d.cwd;$p.Arguments=$d.arguments;"
        "$p.UseShellExecute=$false;$p.CreateNoWindow=$true;"
        "$p.RedirectStandardOutput=$true;$p.RedirectStandardError=$true;"
        "$c=[Diagnostics.Process]::Start($p);"
        "$c.BeginOutputReadLine();$c.BeginErrorReadLine();$c.WaitForExit()"
    )
    encoded_script = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    powershell = str(
        Path(os.getenv("WINDIR", r"C:\Windows"))
        / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    )
    shell_execute = ctypes.windll.shell32.ShellExecuteW
    shell_execute.restype = ctypes.c_void_p
    result = shell_execute(
        None,
        "runas",
        powershell,
        subprocess.list2cmdline([
            "-NoLogo", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
            "-EncodedCommand", encoded_script,
        ]),
        str(path.parent),
        0,
    )
    result_code = int(result or 0)
    if result_code <= 32:
        raise OSError(result_code, "Windows did not start 5E with elevation")


def _matching_roster_uuid_in_page(ws_url: str, roster: list[str], websocket_module) -> str:
    """Ask the page to return only a roster UUID match, never its storage."""
    expression = """
(() => {
  const roster = %s;
  const chunks = [];
  try { chunks.push(String(document.cookie || "")); } catch (_) {}
  for (const storageName of ["localStorage", "sessionStorage"]) {
    try {
      const storage = globalThis[storageName];
      for (let index = 0; index < storage.length; index += 1) {
        const key = storage.key(index);
        chunks.push(String(key || ""), String(storage.getItem(key) || ""));
      }
    } catch (_) {}
  }
  const haystack = chunks.join("\n");
  const matches = roster.filter(value => haystack.includes(value));
  return matches.length === 1 ? matches[0] : "";
})()
""" % json.dumps(roster, ensure_ascii=True)
    connection = None
    try:
        connection = websocket_module.create_connection(
            ws_url, timeout=4, suppress_origin=True
        )
        message_id = 9017
        connection.send(json.dumps({
            "id": message_id,
            "method": "Runtime.evaluate",
            "params": {"expression": expression, "returnByValue": True},
        }))
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            message = json.loads(connection.recv())
            if message.get("id") != message_id:
                continue
            value = (
                message.get("result", {})
                .get("result", {})
                .get("value", "")
            )
            return value if value in roster else ""
    except Exception:
        return ""
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass
    return ""


class FiveEAutoScoutService:
    """Thread-safe 5E detector with an explicit confirmation state."""

    _BUSY_PHASES = frozenset({"queued", "analyzing"})

    def __init__(
        self,
        *,
        max_demos: int = 6,
        mode: str = "normal",
        cdp_port: int | None = None,
        auto_launch: bool | None = None,
        http_session=None,
        websocket_module=None,
    ):
        self.max_demos = max(1, min(10, int(max_demos)))
        self.mode = mode if mode in {"normal", "fast"} else "normal"
        self.cdp_port = cdp_port or _port_from_environment()
        if auto_launch is None:
            auto_launch = os.getenv("CS_SCOUT_5E_AUTO_LAUNCH", "1").strip().casefold() not in {
                "0", "false", "no", "off",
            }
        self.auto_launch = bool(auto_launch)
        self._http = http_session or requests.Session()
        self._http_lock = threading.Lock()
        self._websocket_module = websocket_module
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._target_threads: dict[str, threading.Thread] = {}
        self._connections: set[object] = set()
        self._connected_target_ids: set[str] = set()
        self._processing_ids: set[str] = set()
        self._seen_ids: list[str] = []
        self._active_match_id = ""
        self._active_match_started = 0.0
        self._pending_match: dict | None = None
        self._next_match_retry = 0.0
        self._targets_first_seen = 0.0
        self._launched_by_scout = False
        self._last_launch_failed = False
        self._next_launch_attempt = 0.0
        self._candidate_teams: dict[str, list[dict]] = {}
        self._state = {
            "platform": "fivee",
            "phase": "connecting",
            "message": "正在连接 5E 自动侦察…",
            "auto_available": False,
            "manual_fallback": False,
            "client_running": False,
            "launched_by_scout": False,
            "cdp_port": self.cdp_port,
            "max_demos": self.max_demos,
            "mode": self.mode,
            "map": None,
            "needs_map": False,
            "current_match_id": None,
            "roster_count": 0,
            "self_player": None,
            "identity_confirmed": False,
            "identity_source": None,
            "targets": [],
            "team_options": [],
            "analysis_active": False,
            "analysis_id": None,
            "updated_at": time.time(),
        }

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return deepcopy(self._state)

    def _update(self, **changes) -> None:
        with self._lock:
            self._state.update(changes)
            self._state["updated_at"] = time.time()

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._supervisor_loop,
                name="fivee-cdp-supervisor",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            connections = list(self._connections)
        for connection in connections:
            try:
                connection.close()
            except Exception:
                pass

    def configure(self, *, max_demos: int, mode: str) -> dict[str, object]:
        max_demos = max(1, min(10, int(max_demos)))
        if mode not in {"normal", "fast"}:
            raise ValueError("mode must be 'normal' or 'fast'")
        with self._lock:
            busy = self._state.get("phase") in self._BUSY_PHASES
            if not busy:
                self.max_demos = max_demos
                self.mode = mode
                self._state["max_demos"] = max_demos
                self._state["mode"] = mode
                self._state["updated_at"] = time.time()
            return {"max_demos": self.max_demos, "mode": self.mode, "busy": busy}

    def select_own_team(self, team_id: str) -> dict[str, object]:
        with self._lock:
            accepted = (
                self._state.get("phase") == "awaiting_team_selection"
                and team_id in self._candidate_teams
            )
            if accepted:
                opponent_id = "t2" if team_id == "t1" else "t1"
                targets = deepcopy(self._candidate_teams[opponent_id])
                self._state.update({
                    "phase": "awaiting_confirmation",
                    "message": f"已识别 {len(targets)} 名对手，请确认后开始分析",
                    "self_player": None,
                    "identity_confirmed": True,
                    "identity_source": "user_team_selection",
                    "targets": targets,
                    "team_options": [],
                    "updated_at": time.time(),
                })
            return {
                "accepted": accepted,
                "phase": str(self._state.get("phase", "connecting")),
            }

    def analysis_payload(self, *, map_override: str = "") -> dict[str, object] | None:
        with self._lock:
            if self._state.get("phase") != "awaiting_confirmation":
                return None
            targets = deepcopy(self._state.get("targets", []))
            map_name = str(self._state.get("map") or map_override or "").strip()
            if (
                len(targets) != 5
                or not map_name
                or not all(_player_has_exact_identity(player) for player in targets)
            ):
                return None
            return {
                "usernames": [str(player.get("username") or "") for player in targets],
                "player_hints": targets,
                "map": map_name,
                "max_demos": self.max_demos,
                "mode": self.mode,
            }

    def mark_analysis_started(self, analysis_id=None) -> None:
        self._update(
            phase="analyzing",
            message="已确认对手，正在开始 5E 分析…",
            analysis_active=True,
            analysis_id=analysis_id,
        )

    def mark_analysis_start_failed(self) -> None:
        with self._lock:
            if self._state.get("phase") == "analyzing":
                self._state.update({
                    "phase": "awaiting_confirmation",
                    "message": "分析未能启动，请重试",
                    "analysis_active": False,
                    "analysis_id": None,
                    "updated_at": time.time(),
                })

    def finish_analysis(self, *, success: bool, message: str) -> None:
        with self._lock:
            if not self._state.get("analysis_active"):
                return
            match_id = str(self._state.get("current_match_id") or "")
            if match_id and match_id not in self._seen_ids:
                self._seen_ids.append(match_id)
                self._seen_ids = self._seen_ids[-MAX_SEEN_MATCHES:]
            # Keep the resolved roster and map after every completed run. This
            # lets the user correct the map/depth/mode and analyze the same
            # opponents again, while a newly detected match can still replace
            # this retained snapshot.
            self._active_match_id = ""
            self._active_match_started = 0.0
            self._pending_match = None
            self._candidate_teams = {}
            retry_label = "可重新分析" if success else "可修改后重试"
            self._state.update({
                "phase": "awaiting_confirmation",
                "message": f"{message}；{retry_label}",
                "analysis_active": False,
                "updated_at": time.time(),
            })

    def _websocket(self):
        if self._websocket_module is None:
            import websocket
            self._websocket_module = websocket
        return self._websocket_module

    def _cdp_targets(self) -> list[dict]:
        with self._http_lock:
            response = self._http.get(
                f"http://127.0.0.1:{self.cdp_port}/json/list",
                timeout=2,
            )
        response.raise_for_status()
        if not cdp_listener_is_5e(self.cdp_port):
            raise RuntimeError("CDP listener is not owned by 5EClient.exe")
        targets = response.json()
        if not isinstance(targets, list):
            raise RuntimeError("CDP target list is not an array")
        result = []
        for target in targets:
            if not isinstance(target, dict) or target.get("type") not in {None, "page", "webview"}:
                continue
            ws_url = target.get("webSocketDebuggerUrl")
            page_url = target.get("url")
            if not isinstance(ws_url, str) or not ws_url or not isinstance(page_url, str):
                continue
            parsed = urlparse(page_url)
            if parsed.scheme != "https" or parsed.hostname != "view-arena.5eplay.com":
                continue
            result.append(target)
        return result

    def _connection_state(self, *, phase: str, message: str, **changes) -> None:
        with self._lock:
            current_phase = self._state.get("phase")
            protected = current_phase in {
                "detected", "awaiting_team_selection", "awaiting_confirmation",
                "queued", "analyzing", "ready",
            } or (current_phase == "error" and bool(self._state.get("current_match_id")))
            self._state.update(changes)
            if not protected:
                self._state["phase"] = phase
                self._state["message"] = message
                if phase in {"connecting", "waiting", "manual"}:
                    self._state.update({
                        "current_match_id": None,
                        "roster_count": 0,
                        "self_player": None,
                        "targets": [],
                        "team_options": [],
                    })
            self._state["updated_at"] = time.time()

    def _supervisor_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._websocket()
            except ImportError:
                self._connection_state(
                    phase="manual",
                    message="5E 自动监听组件未安装，可暂时手动输入用户名",
                    auto_available=False,
                    manual_fallback=True,
                )
                self._stop.wait(10)
                continue

            try:
                targets = self._cdp_targets()
            except Exception:
                targets = []

            if targets:
                now = time.monotonic()
                if not self._targets_first_seen:
                    self._targets_first_seen = now
                self._start_target_workers(targets)
                with self._lock:
                    connected = bool(self._connected_target_ids)
                if not connected:
                    elapsed = now - self._targets_first_seen
                    self._connection_state(
                        phase="manual" if elapsed >= 10 else "connecting",
                        message=(
                            "无法建立 5E 自动侦察连接，可暂时手动输入用户名"
                            if elapsed >= 10 else "正在建立 5E 自动侦察连接…"
                        ),
                        auto_available=False,
                        manual_fallback=elapsed >= 10,
                        client_running=True,
                        launched_by_scout=self._launched_by_scout,
                    )
            else:
                self._targets_first_seen = 0.0
                running = is_5e_running()
                now = time.monotonic()
                if running is False and self.auto_launch:
                    executable = find_5e_executable()
                    if executable and now >= self._next_launch_attempt:
                        self._next_launch_attempt = now + 120
                        try:
                            launch_5e_with_cdp(executable, self.cdp_port)
                            self._launched_by_scout = True
                            self._last_launch_failed = False
                            self._connection_state(
                                phase="connecting",
                                message="正在启动 5E 并连接自动侦察；如出现 Windows 确认，请允许",
                                auto_available=False,
                                manual_fallback=False,
                                client_running=True,
                                launched_by_scout=True,
                            )
                        except Exception:
                            self._last_launch_failed = True
                            log.exception("Could not launch 5E with loopback CDP")
                            self._connection_state(
                                phase="manual",
                                message="无法自动启动 5E，可暂时手动输入用户名",
                                auto_available=False,
                                manual_fallback=True,
                                client_running=False,
                            )
                    elif not executable:
                        self._connection_state(
                            phase="manual",
                            message="未找到 5E 客户端，可暂时手动输入用户名",
                            auto_available=False,
                            manual_fallback=True,
                            client_running=False,
                        )
                    elif self._last_launch_failed:
                        self._connection_state(
                            phase="manual",
                            message="Windows 未能启动 5E 自动侦察，可暂时手动输入用户名",
                            auto_available=False,
                            manual_fallback=True,
                            client_running=False,
                        )
                    else:
                        self._connection_state(
                            phase="connecting",
                            message="正在等待 5E 自动侦察端口…",
                            auto_available=False,
                            manual_fallback=False,
                            client_running=False,
                        )
                elif running is True:
                    self._connection_state(
                        phase="manual",
                        message="5E 已运行但未开放自动侦察；退出 5E 后，CS-Scout 会自动重新启动它。当前可手动输入用户名",
                        auto_available=False,
                        manual_fallback=True,
                        client_running=True,
                    )
                elif running is False:
                    self._connection_state(
                        phase="manual",
                        message="当前系统不支持自动启动 5E，可手动输入用户名",
                        auto_available=False,
                        manual_fallback=True,
                        client_running=False,
                    )
                else:
                    self._connection_state(
                        phase="manual",
                        message="无法确认 5E 是否正在运行，可暂时手动输入用户名",
                        auto_available=False,
                        manual_fallback=True,
                        client_running=False,
                    )

            self._prune_target_workers()
            self._expire_stale_candidate()
            self._retry_pending_match()
            self._stop.wait(CDP_RETRY_SECONDS)

    def _start_target_workers(self, targets: list[dict]) -> None:
        with self._lock:
            existing = dict(self._target_threads)
        for index, target in enumerate(targets):
            target_id = str(target.get("id") or target.get("targetId") or index)
            current = existing.get(target_id)
            if current and current.is_alive():
                continue
            thread = threading.Thread(
                target=self._target_loop,
                args=(target_id, target["webSocketDebuggerUrl"]),
                name=f"fivee-cdp-{target_id[:12]}",
                daemon=True,
            )
            with self._lock:
                self._target_threads[target_id] = thread
            thread.start()

    def _prune_target_workers(self) -> None:
        with self._lock:
            self._target_threads = {
                key: thread for key, thread in self._target_threads.items()
                if thread.is_alive()
            }

    def _target_loop(self, target_id: str, ws_url: str) -> None:
        websocket_module = self._websocket()
        connection = None

        def handle_message(message: object) -> None:
            if not isinstance(message, dict):
                return
            if message.get("method") != "Network.webSocketFrameReceived":
                return
            response = message.get("params", {}).get("response", {})
            for match in parse_websocket_frame(
                response.get("opcode"),
                response.get("payloadData"),
                maps.available_maps(),
            ):
                match["target_ws_url"] = ws_url
                self._queue_match(match)

        try:
            connection = websocket_module.create_connection(
                ws_url, timeout=5, suppress_origin=True
            )
            with self._lock:
                self._connections.add(connection)
            connection.send(json.dumps({"id": 1, "method": "Network.enable", "params": {}}))
            connection.settimeout(1)
            enabled = False
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not self._stop.is_set():
                try:
                    raw = connection.recv()
                except websocket_module.WebSocketTimeoutException:
                    continue
                message = json.loads(raw)
                if message.get("id") == 1:
                    if message.get("error"):
                        raise RuntimeError("5E CDP rejected Network.enable")
                    enabled = True
                    break
                handle_message(message)
            if not enabled:
                raise RuntimeError("5E CDP did not confirm Network.enable")
            with self._lock:
                self._connected_target_ids.add(target_id)
            self._connection_state(
                phase="waiting",
                message="已连接 5E，等待匹配到对局…",
                auto_available=True,
                manual_fallback=False,
                client_running=True,
                launched_by_scout=self._launched_by_scout,
            )
            while not self._stop.is_set():
                try:
                    raw = connection.recv()
                except websocket_module.WebSocketTimeoutException:
                    continue
                if not raw:
                    break
                try:
                    message = json.loads(raw)
                except (TypeError, json.JSONDecodeError):
                    continue
                handle_message(message)
        except Exception as exc:
            if not self._stop.is_set():
                log.info("5E CDP target disconnected: %s", type(exc).__name__)
        finally:
            if connection is not None:
                with self._lock:
                    self._connections.discard(connection)
                    self._connected_target_ids.discard(target_id)
                try:
                    connection.close()
                except Exception:
                    pass

    def _queue_match(self, match: dict) -> None:
        match_id = match["match_id"]
        now = time.monotonic()
        with self._lock:
            if match_id in self._seen_ids or match_id in self._processing_ids:
                return
            if self._state.get("phase") in self._BUSY_PHASES:
                return
            if self._active_match_id and self._active_match_id != match_id:
                return
            if (
                self._active_match_id == match_id
                and self._state.get("phase") in {
                    "detected", "awaiting_team_selection", "awaiting_confirmation",
                }
            ):
                return
            if not self._active_match_id:
                self._active_match_id = match_id
                self._active_match_started = now
            self._pending_match = deepcopy(match)
            self._processing_ids.add(match_id)
        threading.Thread(
            target=self._resolve_match,
            args=(match,),
            name=f"fivee-match-{match_id[:16]}",
            daemon=True,
        ).start()

    def _retry_pending_match(self) -> None:
        with self._lock:
            match = deepcopy(self._pending_match)
            retry_at = self._next_match_retry
            processing = bool(match and match.get("match_id") in self._processing_ids)
        if match and not processing and time.monotonic() >= retry_at:
            self._queue_match(match)

    def _expire_stale_candidate(self) -> None:
        with self._lock:
            if (
                not self._active_match_id
                or self._state.get("analysis_active")
                or self._active_match_id in self._processing_ids
                or time.monotonic() - self._active_match_started < MATCH_CANDIDATE_TTL
            ):
                return
            self._active_match_id = ""
            self._active_match_started = 0.0
            self._pending_match = None
            self._next_match_retry = 0.0
            self._candidate_teams = {}
            self._state.update({
                "phase": "waiting",
                "message": "上一场候选对局已过期，正在等待新的 5E 匹配…",
                "current_match_id": None,
                "map": None,
                "needs_map": False,
                "roster_count": 0,
                "self_player": None,
                "identity_confirmed": False,
                "identity_source": None,
                "targets": [],
                "team_options": [],
                "analysis_active": False,
                "analysis_id": None,
                "updated_at": time.time(),
            })

    def _fetch_user_info(self, roster: list[str]) -> dict[str, dict]:
        with self._http_lock:
            response = self._http.post(
                PLATFORM_USER_INFO_URL,
                json={"uuids": roster},
                headers={
                    "User-Agent": api_client.HEADERS["User-Agent"],
                    "Content-Type": "application/json",
                },
                timeout=15,
            )
        response.raise_for_status()
        return _players_from_user_info(response.json(), roster)

    def _resolve_match(self, match: dict) -> None:
        match_id = match["match_id"]
        roster = match["team_1"] + match["team_2"]
        self._update(
            phase="detected",
            message="已匹配到 5E 对局，正在识别地图和对手…",
            auto_available=True,
            manual_fallback=False,
            current_match_id=match_id,
            map=match.get("map") or None,
            roster_count=10,
            targets=[],
            team_options=[],
            analysis_active=False,
        )
        final_error = "暂时无法解析 5E 对局名单"
        try:
            for attempt in range(MATCH_RESOLVE_ATTEMPTS):
                if self._stop.is_set():
                    return
                try:
                    detail = api_client.get_match_detail(match_id)
                except Exception:
                    detail = {}
                detail_players, detail_map = players_from_match_detail(detail)
                user_players = _fetch_user_info_in_page(
                    str(match.get("target_ws_url") or ""),
                    roster,
                    self._websocket(),
                )
                if not user_players:
                    try:
                        user_players = self._fetch_user_info(roster)
                    except Exception:
                        user_players = {}

                players_by_uuid: dict[str, dict] = {}
                for uuid in roster:
                    player = _merge_player_records(
                        detail_players.get(uuid, {}),
                        user_players.get(uuid, {}),
                    )
                    player["uuid"] = uuid
                    players_by_uuid[uuid] = player
                if not all(players_by_uuid[uuid]["username"] for uuid in roster):
                    final_error = "5E 玩家资料尚未就绪，正在重试"
                    if attempt + 1 < MATCH_RESOLVE_ATTEMPTS:
                        self._stop.wait(1.5 * (attempt + 1))
                        continue
                    raise RuntimeError(final_error)

                self_uuid = match.get("self_uuid") or ""
                if self_uuid not in roster:
                    self_uuid = ""
                identity_source = "game_ctx" if self_uuid else ""
                if not self_uuid:
                    self_uuid = _matching_roster_uuid_in_page(
                        match.get("target_ws_url", ""), roster, self._websocket()
                    )
                    if self_uuid:
                        identity_source = "fivee_session"
                if not self_uuid:
                    local_steam_id = active_steam_id()
                    steam_matches = [
                        uuid for uuid, player in players_by_uuid.items()
                        if local_steam_id and player.get("steamid") == local_steam_id
                    ]
                    if len(steam_matches) == 1:
                        self_uuid = steam_matches[0]
                        identity_source = "steam_active_user"

                map_name = (
                    match.get("map")
                    or detail_map
                    or _matching_map_in_page(
                        str(match.get("target_ws_url") or ""),
                        match_id,
                        self._websocket(),
                    )
                    or ""
                )
                team_1 = [deepcopy(players_by_uuid[uuid]) for uuid in match["team_1"]]
                team_2 = [deepcopy(players_by_uuid[uuid]) for uuid in match["team_2"]]
                for player in team_1:
                    player["team"] = "t1"
                for player in team_2:
                    player["team"] = "t2"

                if self_uuid:
                    own_team_id = "t1" if self_uuid in match["team_1"] else "t2"
                    targets = team_2 if own_team_id == "t1" else team_1
                    if not all(_player_has_exact_identity(player) for player in targets):
                        final_error = "5E 对手的稳定账号标识尚未就绪，正在重试"
                        if attempt + 1 < MATCH_RESOLVE_ATTEMPTS:
                            self._stop.wait(min(8.0, 1.5 * (attempt + 1)))
                            continue
                        raise RuntimeError(final_error)
                    self_player = deepcopy(players_by_uuid[self_uuid])
                    self._candidate_teams = {}
                    self._publish_resolved_match(
                        match_id=match_id,
                        map_name=map_name,
                        targets=targets,
                        self_player=self_player,
                        identity_source=identity_source,
                    )
                else:
                    if not all(_player_has_exact_identity(player) for player in team_1 + team_2):
                        final_error = "5E 队伍的稳定账号标识尚未就绪，正在重试"
                        if attempt + 1 < MATCH_RESOLVE_ATTEMPTS:
                            self._stop.wait(min(8.0, 1.5 * (attempt + 1)))
                            continue
                        raise RuntimeError(final_error)
                    self._candidate_teams = {"t1": team_1, "t2": team_2}
                    self._update(
                        phase="awaiting_team_selection",
                        message="已识别十名玩家，但无法唯一确认本人；请选择你的队伍",
                        map=map_name or None,
                        needs_map=not bool(map_name),
                        identity_confirmed=False,
                        identity_source=None,
                        self_player=None,
                        targets=[],
                        team_options=[
                            {"id": "t1", "players": deepcopy(team_1)},
                            {"id": "t2", "players": deepcopy(team_2)},
                        ],
                    )
                with self._lock:
                    self._pending_match = None
                    self._next_match_retry = 0.0
                return
        except Exception as exc:
            log.warning("Could not resolve 5E match %s: %s", match_id, exc)
            self._update(
                phase="error",
                message=f"5E 对局识别失败：{final_error}；自动监听会继续重试",
                manual_fallback=False,
            )
            with self._lock:
                self._next_match_retry = time.monotonic() + 10
        finally:
            with self._lock:
                self._processing_ids.discard(match_id)

    def _publish_resolved_match(
        self,
        *,
        match_id: str,
        map_name: str,
        targets: list[dict],
        self_player: dict,
        identity_source: str,
    ) -> None:
        if len(targets) != 5:
            raise RuntimeError("opponent team is not five players")
        if map_name:
            message = "已识别 5 名 5E 对手，请确认后开始分析"
        else:
            message = "已识别 5 名 5E 对手，请选择地图并确认开始分析"
        self._update(
            phase="awaiting_confirmation",
            message=message,
            current_match_id=match_id,
            map=map_name or None,
            needs_map=not bool(map_name),
            roster_count=10,
            self_player=self_player,
            identity_confirmed=True,
            identity_source=identity_source,
            targets=deepcopy(targets),
            team_options=[],
            manual_fallback=False,
            analysis_active=False,
            analysis_id=None,
        )
