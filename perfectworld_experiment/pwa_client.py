"""Direct Perfect World Arena client for the isolated CS-Scout experiment."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Callable, Iterable, Mapping

import requests

from .pwa_protocol import (
    WEB_API_BASE,
    build_api_headers,
    build_demo_url,
    build_download_headers,
    build_signature_params,
    build_signed_params,
    decode_api_payload,
)


PWA_ACCOUNT_API_BASE = "https://pwa-account.wmpvp.com"


STEAM_ID_RE = re.compile(r"765\d{14}\Z")
MATCH_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
MAP_ALIASES = {
    "ancient": "de_ancient", "de_ancient": "de_ancient", "远古遗迹": "de_ancient",
    "anubis": "de_anubis", "de_anubis": "de_anubis", "阿努比斯": "de_anubis",
    "dust2": "de_dust2", "dust_2": "de_dust2", "de_dust2": "de_dust2",
    "炙热沙城ii": "de_dust2", "炙热沙城2": "de_dust2",
    "inferno": "de_inferno", "de_inferno": "de_inferno", "炼狱小镇": "de_inferno",
    "mirage": "de_mirage", "de_mirage": "de_mirage", "荒漠迷城": "de_mirage",
    "nuke": "de_nuke", "de_nuke": "de_nuke", "核子危机": "de_nuke",
    "overpass": "de_overpass", "de_overpass": "de_overpass", "死亡游乐园": "de_overpass",
    "train": "de_train", "de_train": "de_train", "列车停放站": "de_train",
}


class PerfectWorldError(RuntimeError):
    pass


class PerfectWorldDependencyError(PerfectWorldError):
    pass


class PerfectWorldLookupError(PerfectWorldError):
    pass


@dataclass(frozen=True)
class PerfectWorldDemo:
    match_id: str
    demo_url: str
    map_name: str | None


@dataclass(frozen=True)
class PerfectWorldPlayer:
    player_id: str
    steamid: str
    nickname: str


@dataclass(frozen=True)
class _Metadata:
    match_id: str
    demo_url: str
    map_name: str | None
    map_label: str | None = None


def validate_steamid(steamid: str) -> str:
    value = str(steamid).strip()
    if not STEAM_ID_RE.fullmatch(value):
        raise ValueError("SteamID64 必须是以 765 开头的 17 位数字")
    return value


def validate_access_token(access_token: str) -> str:
    if not isinstance(access_token, str):
        raise ValueError("access_token 缺失")
    value = access_token.strip()
    if not value or len(value) > 4096:
        raise ValueError("access_token 缺失或长度异常")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("access_token 含有非法控制字符")
    return value


def validate_match_id(match_id: str) -> str:
    value = str(match_id).strip()
    if not MATCH_ID_RE.fullmatch(value):
        raise ValueError("完美平台返回了非法对局 ID")
    return value


def normalize_map_name(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return MAP_ALIASES.get(value.strip().casefold().replace(" ", ""))


def _field(item: object, name: str) -> Any:
    return item.get(name) if isinstance(item, Mapping) else getattr(item, name, None)


def _records(payload: object) -> list[Mapping[str, object]]:
    candidate = payload
    if isinstance(candidate, Mapping):
        for key in ("list", "records", "matches", "items", "data"):
            if isinstance(candidate.get(key), list):
                candidate = candidate[key]
                break
    if not isinstance(candidate, list):
        return []
    return [row for row in candidate if isinstance(row, Mapping)]


class PerfectWorldClient:
    """Fetch demo descriptors without the former third-party downloader."""

    def __init__(
        self,
        steamid: str,
        access_token: str,
        *,
        metadata_fetcher: Callable[..., Iterable[Any]] | None = None,
        header_builder: Callable[[str], dict[str, str]] | None = None,
        session: requests.Session | None = None,
    ):
        self.steamid = validate_steamid(steamid)
        self._access_token = validate_access_token(access_token)
        self._session = session or requests.Session()
        self._metadata_fetcher = metadata_fetcher or self._fetch_metadata
        self._header_builder = header_builder or self._build_headers
        self._detail_cache: dict[
            str, tuple[str | None, int | str | None, bool] | None
        ] = {}

    def _fetch_metadata(self, target_steamid: str, access_token: str, *, size: int) -> list[_Metadata]:
        params = build_signed_params(
            {"access_token": access_token, "size": size, "uid": target_steamid}
        )
        try:
            response = self._session.get(
                WEB_API_BASE + "/user-info/recent-ladder-score-list",
                params=params,
                headers=build_api_headers(self.steamid, access_token),
                timeout=(10, 20),
            )
            response.raise_for_status()
            envelope = response.json()
            if isinstance(envelope, Mapping) and int(envelope.get("code", 0)) != 0:
                raise PerfectWorldLookupError("完美平台拒绝了对局查询")
            payload = decode_api_payload(envelope)
        except PerfectWorldLookupError:
            raise
        except (requests.RequestException, ValueError) as exc:
            # Requests exceptions can contain the signed URL, so never expose them.
            raise PerfectWorldLookupError("完美平台对局查询失败") from exc

        output: list[_Metadata] = []
        for row in _records(payload):
            match_id = next((str(row[key]) for key in ("match", "match_id", "matchId") if row.get(key)), "")
            if not MATCH_ID_RE.fullmatch(match_id):
                continue
            map_value = row.get("map") or row.get("map_name")
            cup_id = row.get("cup_id")
            downloadable = True
            if map_value is None:
                detail = self._fetch_match_detail(match_id, access_token)
                if detail is None:
                    continue
                map_value, cup_id, downloadable = detail
            output.append(
                _Metadata(
                    match_id=match_id,
                    demo_url=(
                        build_demo_url(match_id, cup_id or 0, access_token)
                        if downloadable
                        else ""
                    ),
                    map_name=str(map_value) if map_value is not None else None,
                )
            )
        return output

    def _fetch_match_detail(
        self,
        match_id: str,
        access_token: str,
    ) -> tuple[str | None, int | str | None, bool] | None:
        if match_id in self._detail_cache:
            return self._detail_cache[match_id]
        body = json.dumps({"match_id": match_id}, ensure_ascii=False, separators=(",", ":"))
        headers = build_api_headers(self.steamid, access_token)
        headers["Content-Type"] = "application/json;charset=UTF-8"
        try:
            response = self._session.post(
                WEB_API_BASE + "/match-api/detail",
                params=build_signature_params(body),
                headers=headers,
                data=body.encode("utf-8"),
                timeout=(10, 20),
            )
            response.raise_for_status()
            envelope = response.json()
            if not isinstance(envelope, Mapping) or int(envelope.get("code", -1)) != 0:
                self._detail_cache[match_id] = None
                return None
            data = envelope.get("data")
            if not isinstance(data, Mapping):
                self._detail_cache[match_id] = None
                return None
        except (requests.RequestException, ValueError):
            self._detail_cache[match_id] = None
            return None

        # Current client (2026) detail shape.
        base_info = data.get("baseInfo") or data.get("base_info")
        demo_info = data.get("demo_info") or data.get("demoInfo")
        if isinstance(base_info, Mapping):
            map_info = base_info.get("map_info") or base_info.get("mapInfo")
            if isinstance(map_info, Mapping):
                map_name = map_info.get("name_en") or map_info.get("nameEn")
            else:
                map_name = base_info.get("map") or base_info.get("map_name")
            cup_id = base_info.get("cup_id") or base_info.get("cupId")
            downloadable = True
            if isinstance(demo_info, Mapping):
                available = demo_info.get("demo_is_available")
                if available is None:
                    available = demo_info.get("demoIsAvailable")
                downloadable = available is not False and demo_info.get("expired") is not True
            result = (
                str(map_name) if map_name is not None else None,
                cup_id,
                downloadable,
            )
            self._detail_cache[match_id] = result
            return result

        # Older report shape retained for compatibility with archived records.
        report = data.get("report")
        cup = data.get("cup")
        if isinstance(report, Mapping):
            map_name = report.get("map") or report.get("map_name")
            cup_id = cup.get("id") if isinstance(cup, Mapping) else data.get("cup_id")
            result = (
                str(map_name) if map_name is not None else None,
                cup_id,
                not bool(report.get("no_demo")),
            )
            self._detail_cache[match_id] = result
            return result
        self._detail_cache[match_id] = None
        return None

    def _build_headers(self, steamid: str) -> dict[str, str]:
        return build_download_headers(steamid, session=self._session)

    def recent(
        self,
        *,
        limit: int = 20,
        target_steamid: str | None = None,
    ) -> list[PerfectWorldDemo]:
        """Return newest records across maps; signed URLs remain in memory only."""
        if not 1 <= int(limit) <= 20:
            raise ValueError("limit 必须在 1 到 20 之间")
        target = validate_steamid(target_steamid or self.steamid)
        try:
            metadata = self._metadata_fetcher(
                target, self._access_token, size=int(limit)
            )
        except PerfectWorldLookupError:
            raise
        except Exception as exc:
            raise PerfectWorldLookupError("完美平台对局查询失败") from exc
        output: list[PerfectWorldDemo] = []
        seen: set[str] = set()
        for item in metadata or []:
            try:
                match_id = validate_match_id(_field(item, "match_id"))
            except ValueError:
                continue
            demo_url = _field(item, "demo_url")
            if match_id in seen or not isinstance(demo_url, str) or not demo_url:
                continue
            seen.add(match_id)
            output.append(PerfectWorldDemo(
                match_id,
                demo_url,
                normalize_map_name(_field(item, "map_name") or _field(item, "map_label")),
            ))
        return output

    def discover(
        self,
        *,
        map_name: str,
        limit: int,
        target_steamid: str | None = None,
    ) -> list[PerfectWorldDemo]:
        wanted_map = normalize_map_name(map_name)
        if wanted_map is None:
            raise ValueError(f"不支持的地图：{map_name}")
        if not 1 <= int(limit) <= 20:
            raise ValueError("limit 必须在 1 到 20 之间")
        fetch_size = min(20, max(int(limit) * 4, int(limit)))
        metadata = self.recent(
            limit=fetch_size,
            target_steamid=target_steamid,
        )
        demos: list[PerfectWorldDemo] = []
        seen: set[str] = set()
        for item in metadata or []:
            match_id = item.match_id
            demo_url = item.demo_url
            item_map = item.map_name
            if item_map != wanted_map or not isinstance(demo_url, str) or not demo_url:
                continue
            try:
                safe_match_id = validate_match_id(match_id)
            except ValueError:
                continue
            if safe_match_id in seen:
                continue
            seen.add(safe_match_id)
            demos.append(PerfectWorldDemo(safe_match_id, demo_url, item_map))
            if len(demos) >= limit:
                break
        return demos

    def build_download_headers(self) -> dict[str, str]:
        try:
            headers = self._header_builder(self.steamid)
        except Exception as exc:
            raise PerfectWorldDependencyError("完美平台 Demo 下载签名生成失败") from exc
        if not isinstance(headers, dict):
            raise PerfectWorldDependencyError("完美平台签名组件返回了非法请求头")
        return {str(key): str(value) for key, value in headers.items()}

    def resolve_players(
        self,
        player_ids: Iterable[str | int],
        *,
        jt: str,
    ) -> list[PerfectWorldPlayer]:
        """Resolve game-start player ids to SteamID64 and display names."""
        ordered_ids: list[str] = []
        seen: set[str] = set()
        for value in player_ids:
            player_id = str(value).strip()
            if not player_id or player_id in seen or not player_id.isdigit():
                continue
            seen.add(player_id)
            ordered_ids.append(player_id)
        if not ordered_ids or len(ordered_ids) > 20:
            raise ValueError("当前对局玩家 ID 数量必须在 1 到 20 之间")
        jt_value = str(jt).strip()
        if not jt_value or len(jt_value) > 4096 or any(
            ord(char) < 32 or ord(char) == 127 for char in jt_value
        ):
            raise ValueError("完美平台本地会话凭据无效")

        query = {
            "token": self._access_token,
            "uid_list": ",".join(ordered_ids),
            "with_perfect_power": 1,
            "with_roles": 1,
            "with_badge_info": 1,
        }
        params = build_signed_params(query)
        headers = build_api_headers(self.steamid, self._access_token)
        headers["Pwa-Jt"] = jt_value
        try:
            response = self._session.get(
                PWA_ACCOUNT_API_BASE + "/user/playersInfo",
                params=params,
                headers=headers,
                timeout=(10, 20),
            )
            response.raise_for_status()
            envelope = response.json()
            if not isinstance(envelope, Mapping):
                raise PerfectWorldLookupError("完美平台玩家信息响应格式异常")
            if int(envelope.get("code", 0)) != 0:
                raise PerfectWorldLookupError("完美平台拒绝了当前阵容查询")
            data = envelope.get("data")
            if not isinstance(data, Mapping):
                raise PerfectWorldLookupError("完美平台玩家信息响应缺少 data")
            players_info = data.get("players_info") or data.get("playersInfo")
            if not isinstance(players_info, Mapping):
                raise PerfectWorldLookupError("完美平台玩家信息响应缺少 players_info")
        except PerfectWorldLookupError:
            raise
        except (requests.RequestException, ValueError) as exc:
            raise PerfectWorldLookupError("完美平台当前阵容查询失败") from exc

        resolved: list[PerfectWorldPlayer] = []
        for player_id in ordered_ids:
            row = players_info.get(player_id)
            if row is None:
                row = players_info.get(int(player_id))
            if not isinstance(row, Mapping):
                continue
            steamid_value = row.get("uid") or row.get("steam_id") or row.get("steamId")
            try:
                steamid = validate_steamid(str(steamid_value))
            except ValueError:
                continue
            name_value = (
                row.get("nickname")
                or row.get("nick_name")
                or row.get("name")
                or steamid
            )
            nickname = str(name_value).strip() or steamid
            resolved.append(PerfectWorldPlayer(player_id, steamid, nickname))
        return resolved
