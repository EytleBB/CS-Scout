"""Turn an active Perfect World match into CS-Scout analysis targets."""

from __future__ import annotations

from dataclasses import dataclass

from .current_match import CurrentMatch, LocalPerfectWorldState, RawMatchPlayer
from .pipeline import run_roster
from .pwa_client import PerfectWorldClient, PerfectWorldPlayer, STEAM_ID_RE


@dataclass(frozen=True)
class AutoTargets:
    players: tuple[PerfectWorldPlayer, ...]
    scope: str


def _direct_steamid(player: RawMatchPlayer) -> str | None:
    for value in (player.steam_id, player.player_id):
        if value and STEAM_ID_RE.fullmatch(str(value)):
            return str(value)
    return None


def resolve_roster(
    state: LocalPerfectWorldState,
    *,
    client: PerfectWorldClient | None = None,
) -> tuple[PerfectWorldPlayer, ...]:
    match = state.current_match
    session = state.session
    if match is None or session is None:
        raise RuntimeError("尚未检测到可用的完美平台当前对局与登录会话")
    pwa = client or PerfectWorldClient(session.account_steamid, session.access_token)
    raw_ids = [player.player_id for player in match.players]
    resolved = pwa.resolve_players(raw_ids, jt=session.jt)
    by_player_id = {player.player_id: player for player in resolved}
    by_steamid = {player.steamid: player for player in resolved}

    roster: list[PerfectWorldPlayer] = []
    seen: set[str] = set()
    for raw in match.players:
        direct = _direct_steamid(raw)
        info = by_player_id.get(raw.player_id)
        if info is None and direct:
            info = by_steamid.get(direct)
        steamid = info.steamid if info is not None else direct
        if not steamid or steamid in seen:
            continue
        seen.add(steamid)
        name = (
            info.nickname
            if info is not None
            else raw.nickname or steamid
        )
        roster.append(PerfectWorldPlayer(raw.player_id, steamid, name))
    if not roster:
        raise RuntimeError("已检测到当前对局，但没有识别出可分析的 SteamID")
    return tuple(roster)


def _own_raw_player(match: CurrentMatch, account_steamid: str) -> RawMatchPlayer | None:
    for player in match.players:
        if account_steamid in {player.player_id, player.steam_id}:
            return player
    return None


def select_targets(
    match: CurrentMatch,
    roster: tuple[PerfectWorldPlayer, ...],
    account_steamid: str,
    *,
    all_players: bool = False,
) -> AutoTargets:
    if all_players:
        return AutoTargets(roster, "all_players")

    own = _own_raw_player(match, account_steamid)
    raw_by_id = {player.player_id: player for player in match.players}
    steam_by_player_id = {player.player_id: player.steamid for player in roster}
    if own is not None and own.team_id is not None:
        opponents = tuple(
            player
            for player in roster
            if raw_by_id.get(player.player_id) is not None
            and raw_by_id[player.player_id].team_id is not None
            and raw_by_id[player.player_id].team_id != own.team_id
        )
        if opponents:
            return AutoTargets(opponents, "opponents_by_team")

    if own is not None and own.slot_id is not None:
        own_half = own.slot_id // 5
        opponents = tuple(
            player
            for player in roster
            if raw_by_id.get(player.player_id) is not None
            and raw_by_id[player.player_id].slot_id is not None
            and raw_by_id[player.player_id].slot_id // 5 != own_half
        )
        if opponents:
            return AutoTargets(opponents, "opponents_by_slot")

    # Some mode payloads omit team metadata. In that case analyze everyone
    # except the signed-in player instead of guessing a team assignment.
    others = tuple(
        player
        for player in roster
        if player.steamid != account_steamid
        and steam_by_player_id.get(player.player_id) != account_steamid
    )
    if not others:
        raise RuntimeError("当前对局中没有识别到其他玩家")
    return AutoTargets(others, "other_players_team_unknown")


def run_auto_state(
    state: LocalPerfectWorldState,
    max_demos: int,
    *,
    all_players: bool = False,
    client: PerfectWorldClient | None = None,
    targets: AutoTargets | None = None,
    **pipeline_options,
) -> dict[str, object]:
    match = state.current_match
    session = state.session
    if match is None or session is None:
        raise RuntimeError("尚未检测到可用的完美平台当前对局与登录会话")
    pwa = client or PerfectWorldClient(session.account_steamid, session.access_token)
    selected = targets or prepare_auto_state(
        state, all_players=all_players, client=pwa
    )
    summary = run_roster(
        list(selected.players),
        session.account_steamid,
        session.access_token,
        match.map_name,
        max_demos,
        current_match_id=match.match_id,
        target_scope=selected.scope,
        client=pwa,
        **pipeline_options,
    )
    return summary


def prepare_auto_state(
    state: LocalPerfectWorldState,
    *,
    all_players: bool = False,
    client: PerfectWorldClient | None = None,
) -> AutoTargets:
    """Resolve displayable targets without starting Demo discovery or parsing."""
    match = state.current_match
    session = state.session
    if match is None or session is None:
        raise RuntimeError("尚未检测到可用的完美平台当前对局与登录会话")
    pwa = client or PerfectWorldClient(session.account_steamid, session.access_token)
    roster = resolve_roster(state, client=pwa)
    return select_targets(
        match,
        roster,
        session.account_steamid,
        all_players=all_players,
    )
