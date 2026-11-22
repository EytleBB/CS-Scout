"""Assemble the pre-match panel payload for both teams.

The platform already ships every player's profile with the create-game payload,
so this module only shapes that data for the UI.  It never invents a metric: a
player the platform did not describe is reported as ``has_stats: False`` with
``None`` fields, and the payload says how many players actually have data.
"""

from __future__ import annotations

from typing import Iterable, Mapping

from .current_match import CurrentMatch, RawMatchPlayer
from .player_stats import PlayerStats, RadarDimension

SOURCE = "perfectworld"

# A 5v5 queue cannot field a premade group larger than five, and the platform
# uses 0/-1 for "no party".  Both cases must stay unmarked instead of turning a
# shared placeholder into a fake stack.
PARTY_MIN_SIZE = 2
PARTY_MAX_SIZE = 5


def _stat_steam_id(player: RawMatchPlayer) -> str | None:
    """Return the SteamID used to look stats up for a raw roster entry."""
    for candidate in (player.steam_id, player.player_id):
        if candidate and candidate.isdigit() and len(candidate) == 17:
            return candidate
    return None


def _radar_payload(radar: Mapping[str, RadarDimension]) -> dict[str, dict]:
    return {
        key: {
            "score": dimension.score,
            "level": dimension.level,
            "raw": dict(dimension.raw),
        }
        for key, dimension in radar.items()
    }


def _player_payload(
    player: RawMatchPlayer,
    stats: PlayerStats | None,
    *,
    party: int | None = None,
    party_size: int = 1,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "player_id": player.player_id,
        "steam_id": _stat_steam_id(player),
        "nickname": player.nickname,
        "slot_id": player.slot_id,
        "team_id": player.team_id,
        "party": party,
        "party_size": party_size,
        "has_stats": stats is not None,
    }
    if stats is None:
        payload.update({
            "avatar": None,
            "score": None,
            "adpr": None,
            "recent_rating": None,
            "season_rating": None,
            "season_win_rate": None,
            "season_total": None,
            "map_win_rate": None,
            "map_total": None,
            "map_sample_low": False,
            "recent_win_rate": None,
            "recent_results": [],
            "recent_ratings": [],
            "perfect_power": None,
            "rank_desc": None,
            "description": None,
            "radar": {},
        })
        return payload

    payload.update({
        "avatar": stats.avatar,
        "score": stats.score,
        "adpr": stats.adpr,
        "recent_rating": stats.recent_rating,
        "season_rating": stats.season_rating,
        "season_win_rate": stats.season_win_rate,
        "season_total": stats.season_total,
        "map_win_rate": stats.map_win_rate,
        "map_total": stats.map_total,
        "map_sample_low": stats.map_sample_low,
        "recent_win_rate": stats.recent_win_rate,
        "recent_results": list(stats.recent_results),
        "recent_ratings": list(stats.recent_ratings),
        "perfect_power": stats.perfect_power,
        "rank_desc": stats.rank_desc,
        "description": stats.description,
        "radar": _radar_payload(stats.radar),
    })
    return payload


def _sorted_players(players: Iterable[RawMatchPlayer]) -> list[RawMatchPlayer]:
    return sorted(
        players,
        key=lambda player: (
            player.slot_id if player.slot_id is not None else 1 << 30,
            player.nickname or player.player_id,
        ),
    )


def _party_marks(players: Iterable[RawMatchPlayer]) -> dict[str, tuple[int, int]]:
    """Map player id to ``(party ordinal, party size)`` for premade groups.

    Only positive party ids with a plausible 5v5 group size are marked; solo
    players keep no party so the UI never invents a stack.
    """
    groups: dict[int, list[RawMatchPlayer]] = {}
    for player in players:
        troop = player.troop_team_id
        if troop is None or troop <= 0:
            continue
        groups.setdefault(troop, []).append(player)

    marks: dict[str, tuple[int, int]] = {}
    ordinal = 0
    for members in groups.values():
        if not PARTY_MIN_SIZE <= len(members) <= PARTY_MAX_SIZE:
            continue
        ordinal += 1
        for player in members:
            marks[player.player_id] = (ordinal, len(members))
    return marks


def _average(values: Iterable[float | None]) -> float | None:
    numbers = [value for value in values if value is not None]
    if not numbers:
        return None
    return sum(numbers) / len(numbers)


def _team_aggregates(
    players: Iterable[RawMatchPlayer], stats_by_id: Mapping[str, PlayerStats]
) -> dict[str, object]:
    """Summarise one side without letting players without data dilute it."""
    stats = [
        stats_by_id[steam_id]
        for steam_id in (_stat_steam_id(player) for player in players)
        if steam_id is not None and steam_id in stats_by_id
    ]
    weighted_total = 0
    weighted_wins = 0.0
    for stat in stats:
        if stat.map_win_rate is None or not stat.map_total:
            continue
        weighted_total += stat.map_total
        weighted_wins += stat.map_win_rate * stat.map_total
    return {
        "avg_score": _average(stat.score for stat in stats),
        "avg_adpr": _average(stat.adpr for stat in stats),
        "avg_season_rating": _average(stat.season_rating for stat in stats),
        "avg_perfect_power": _average(stat.perfect_power for stat in stats),
        "map_win_rate": (weighted_wins / weighted_total) if weighted_total else None,
        "with_stats": len(stats),
        "map_sample_low": sum(1 for stat in stats if stat.map_sample_low),
    }


def build_prep_panel(
    match: CurrentMatch | None,
    *,
    own_steamid: str | None = None,
    fresh: bool | None = None,
    source: str = SOURCE,
) -> dict[str, object]:
    """Return the ``/api/prep/current`` payload for one active match."""
    if match is None:
        return {
            "available": False,
            "source": source,
            "fresh": fresh,
            "match_id": None,
            "map": None,
            "own_side": None,
            "teams": [],
            "unassigned": [],
            "stats_coverage": {"with_stats": 0, "total": 0},
        }

    extras = getattr(match, "extras", None) or {}
    grouped: dict[int, list[RawMatchPlayer]] = {}
    unassigned: list[RawMatchPlayer] = []
    with_stats = 0
    for player in match.players:
        steam_id = _stat_steam_id(player)
        if steam_id is not None and steam_id in extras:
            with_stats += 1
        if player.team_id is None:
            unassigned.append(player)
        else:
            grouped.setdefault(player.team_id, []).append(player)

    own_player = None
    if own_steamid:
        own_player = next(
            (player for player in match.players if _stat_steam_id(player) == own_steamid),
            None,
        )
    own_side = own_player.team_id if own_player is not None else None

    teams = []
    for side in sorted(grouped):
        players = _sorted_players(grouped[side])
        if own_side is None:
            label = "unknown"
        else:
            label = "ours" if side == own_side else "theirs"
        marks = _party_marks(players)
        teams.append({
            "side": side,
            "label": label,
            "aggregates": _team_aggregates(players, extras),
            "players": [
                _player_payload(
                    player,
                    extras.get(_stat_steam_id(player) or ""),
                    party=marks.get(player.player_id, (None, 1))[0],
                    party_size=marks.get(player.player_id, (None, 1))[1],
                )
                for player in players
            ],
        })

    return {
        "available": True,
        "source": source,
        "fresh": fresh,
        "match_id": match.match_id or None,
        "map": match.map_name,
        "own_side": own_side,
        "teams": teams,
        "unassigned": [
            _player_payload(player, extras.get(_stat_steam_id(player) or ""))
            for player in _sorted_players(unassigned)
        ],
        "stats_coverage": {
            "with_stats": with_stats,
            "total": len(match.players),
        },
    }
