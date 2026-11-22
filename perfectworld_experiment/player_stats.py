"""Decode the Perfect World ``playerlist_extrainfo`` pre-match payload.

The desktop client already receives every player's platform-computed profile
alongside the match: a seven-dimension play-style radar, season and per-map win
rates, recent-form series, ADPR and the perfect-power rating.  That payload is
nested as a JSON *string* inside the create-game message, so it needs two
parses before any of it can be shown.

Only facts the platform states are exposed here.  Metrics the platform does not
publish are deliberately left as ``None`` instead of being estimated, because a
fabricated K/D would be indistinguishable from a real one in the pre-match
panel.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from json import JSONDecodeError, loads
import re
from typing import Mapping

EXTRA_INFO_KEY = "playerlist_extrainfo"
_STEAM_ID_RE = re.compile(r"765\d{14}\Z")
_MAP_SAMPLE_FLOOR = 3

# Containers the create-game payload may nest the extra-info string under.
_NESTED_CONTAINERS = ("game_info", "gameInfo", "data", "match", "match_info")


@dataclass(frozen=True)
class RadarDimension:
    """One play-style axis scored by the platform (0-100 plus a letter grade)."""

    key: str
    score: int | None = None
    level: str | None = None
    raw: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class PlayerStats:
    steam_id: str
    nickname: str | None = None
    avatar: str | None = None
    score: int | None = None
    adpr: float | None = None
    recent_rating: float | None = None
    season_rating: float | None = None
    season_win_rate: float | None = None
    season_total: int | None = None
    map_win_rate: float | None = None
    map_total: int | None = None
    map_sample_low: bool = False
    recent_win_rate: float | None = None
    recent_results: tuple[str, ...] = ()
    recent_ratings: tuple[float, ...] = ()
    perfect_power: int | None = None
    rank_desc: str | None = None
    description: str | None = None
    radar: Mapping[str, RadarDimension] = field(default_factory=dict)


def _number(value: object) -> float | None:
    """Read a number that the platform may send as a string."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


def _integer(value: object) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _text(value: object) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    return text or None


def _ratio(numerator: object, denominator: object) -> float | None:
    top = _number(numerator)
    bottom = _number(denominator)
    if top is None or bottom is None or bottom <= 0:
        return None
    return top / bottom


def _decode(raw: object) -> Mapping[str, object] | None:
    """Accept the payload as a mapping, or as the client's JSON string."""
    if isinstance(raw, Mapping):
        return raw
    if isinstance(raw, str):
        try:
            decoded = loads(raw)
        except (JSONDecodeError, TypeError, ValueError):
            return None
        return decoded if isinstance(decoded, Mapping) else None
    return None


def _data_map(raw: object) -> Mapping[str, Mapping[str, object]]:
    payload = _decode(raw)
    if not isinstance(payload, Mapping):
        return {}
    if payload.get("success") is False:
        return {}
    data = payload.get("data")
    if not isinstance(data, Mapping):
        return {}
    return {
        str(key): value
        for key, value in data.items()
        if isinstance(value, Mapping) and value
    }


def locate_extra_info(payload: object) -> Mapping[str, Mapping[str, object]]:
    """Find the per-SteamID extra-info map inside a create-game payload."""
    if not isinstance(payload, Mapping):
        return {}

    candidates: list[object] = [payload.get(EXTRA_INFO_KEY)]
    for container in _NESTED_CONTAINERS:
        nested = payload.get(container)
        if isinstance(nested, Mapping):
            candidates.append(nested.get(EXTRA_INFO_KEY))

    for candidate in candidates:
        data = _data_map(candidate)
        if data:
            return data

    # Some builds hand over the already-decoded ``{success, data}`` envelope.
    return _data_map(payload)


def _radar(chart: object) -> tuple[Mapping[str, RadarDimension], str | None]:
    if not isinstance(chart, Mapping):
        return {}, None

    description = _text(chart.get("description"))
    dimensions: dict[str, RadarDimension] = {}
    for key, value in chart.items():
        if key == "description" or not isinstance(value, Mapping):
            continue
        detail = value.get("detail")
        raw: dict[str, float] = {}
        if isinstance(detail, Mapping):
            for metric, metric_value in detail.items():
                if not str(metric).endswith("_raw"):
                    continue
                number = _number(metric_value)
                if number is not None:
                    raw[str(metric)] = number
        dimensions[str(key)] = RadarDimension(
            key=str(key),
            score=_integer(value.get("score")),
            level=_text(value.get("level")),
            raw=raw,
        )
    return dimensions, description


def _recent(recent: object) -> tuple[float | None, tuple[str, ...], tuple[float, ...], float | None]:
    if not isinstance(recent, Mapping):
        return None, (), (), None

    results = recent.get("win_stats")
    outcomes = tuple(
        str(item).strip().lower()
        for item in results
        if isinstance(item, str) and str(item).strip()
    ) if isinstance(results, (list, tuple)) else ()

    decided = [item for item in outcomes if item in {"win", "lose"}]
    wins = sum(1 for item in decided if item == "win")
    win_rate = (wins / len(decided)) if decided else None

    ratings_raw = recent.get("pw_rating_list")
    ratings = tuple(
        number
        for number in (
            _number(item)
            for item in ratings_raw
        )
        if number is not None
    ) if isinstance(ratings_raw, (list, tuple)) else ()

    return _number(recent.get("pw_rating_avg")), outcomes, ratings, win_rate


def _perfect_power(power: object) -> tuple[int | None, str | None]:
    if not isinstance(power, Mapping):
        return None, None
    value = _integer(power.get("perfectPower"))
    rank_desc = None
    ranks = power.get("achievedRankList")
    if isinstance(ranks, (list, tuple)):
        for entry in ranks:
            if not isinstance(entry, Mapping):
                continue
            rank_desc = _text(entry.get("rankDesc"))
            if rank_desc:
                break
    return value, rank_desc


def _build(steam_id: str, entry: Mapping[str, object]) -> PlayerStats:
    map_win = _integer(entry.get("season_map_win_num"))
    map_total = _integer(entry.get("season_map_total_num"))
    map_win_rate = _ratio(map_win, map_total)

    season_total = _integer(entry.get("season_total_num"))
    season_win_rate = _ratio(entry.get("season_win_num"), season_total)

    radar, description = _radar(entry.get("radar_chart"))
    recent_rating, outcomes, ratings, recent_win_rate = _recent(
        entry.get("recent_10_stats")
    )
    perfect_power, rank_desc = _perfect_power(entry.get("perfectPower"))

    return PlayerStats(
        steam_id=steam_id,
        nickname=_text(entry.get("nickname")),
        avatar=_text(entry.get("avatar") or entry.get("csgoRgbAvatar")),
        score=_integer(entry.get("score")),
        adpr=_number(entry.get("adpr")),
        recent_rating=recent_rating,
        season_rating=_number(entry.get("season_rating_pro_average")),
        season_win_rate=season_win_rate,
        season_total=season_total,
        map_win_rate=map_win_rate,
        map_total=map_total,
        # A 1-0 map record is a 100% win rate: never present it as reliable.
        map_sample_low=bool(map_total is not None and map_total < _MAP_SAMPLE_FLOOR),
        recent_win_rate=recent_win_rate,
        recent_results=outcomes,
        recent_ratings=ratings,
        perfect_power=perfect_power,
        rank_desc=rank_desc,
        description=description,
        radar=radar,
    )


def parse_extra_info(payload: object) -> dict[str, PlayerStats]:
    """Return per-SteamID platform statistics found in ``payload``."""
    located = locate_extra_info(payload)
    stats: dict[str, PlayerStats] = {}
    for steam_id, entry in located.items():
        if not isinstance(entry, Mapping) or not entry:
            continue
        if not _STEAM_ID_RE.fullmatch(steam_id):
            continue
        stats[steam_id] = _build(steam_id, entry)
    return stats
