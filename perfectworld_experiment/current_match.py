"""Detect the active Perfect World Arena match from the local desktop log.

The official client already receives the authoritative game-start payload.  We
only decode the client's local log and keep the resulting identifiers in
memory; no client files, cookies, or databases are modified.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from json import JSONDecodeError, JSONDecoder
from pathlib import Path
import codecs
import os
import re
import time
from typing import Iterable, Mapping

from .player_stats import PlayerStats, parse_extra_info


DEFAULT_LOG_DIR = Path(os.getenv("APPDATA", "")) / "Wmpvp" / "Log"
_ENCODED_PAYLOAD_RE = re.compile(r"\^([0-9a-fA-F]+)\$\s*$")
_LOGIN_STEAMID_RE = re.compile(r"\buser_id=(765\d{14})\b")
_MATCH_ID_RE = re.compile(r'"matchId"\s*:\s*"?([A-Za-z0-9_-]*)"?')
_TOKEN_RE = re.compile(r"queryUserDetailsByToken:\s*([^\s]+)", re.IGNORECASE)
_STEAMID_RE = re.compile(r"765\d{14}\Z")
_JSON_DECODER = JSONDecoder()


@dataclass(frozen=True)
class RawMatchPlayer:
    player_id: str
    steam_id: str | None = None
    nickname: str | None = None
    slot_id: int | None = None
    team_id: int | None = None


@dataclass(frozen=True)
class CurrentMatch:
    match_id: str
    map_name: str
    players: tuple[RawMatchPlayer, ...]
    host_info: str | None = None
    zone_name: str | None = None
    # Platform-computed pre-match statistics, keyed by SteamID.  The client
    # ships these with the create-game payload, so no extra request is needed.
    extras: Mapping[str, "PlayerStats"] = field(default_factory=dict)


@dataclass(frozen=True)
class SessionCredentials:
    account_steamid: str
    # Secret values never appear in an accidental repr or CLI status message.
    access_token: str = field(repr=False)
    jt: str = field(repr=False)


@dataclass(frozen=True)
class LocalPerfectWorldState:
    current_match: CurrentMatch | None
    session: SessionCredentials | None


def decode_log_line(line: str) -> str:
    """Decode the XOR/hex message section used by the desktop logger."""
    match = _ENCODED_PAYLOAD_RE.search(line.rstrip("\r\n"))
    if not match:
        return line.rstrip("\r\n")
    try:
        encoded = bytes.fromhex(match.group(1))
    except ValueError:
        return line.rstrip("\r\n")
    decoded = bytes(
        value ^ ((42 + 3 * index) % 255)
        for index, value in enumerate(encoded)
    ).decode("utf-8", errors="replace")
    return line[: match.start()] + decoded


def _json_objects(text: str) -> Iterable[Mapping[str, object]]:
    """Yield JSON objects embedded after arbitrary log prefixes."""
    cursor = 0
    while True:
        start = text.find("{", cursor)
        if start < 0:
            return
        try:
            value, consumed = _JSON_DECODER.raw_decode(text[start:])
        except JSONDecodeError:
            cursor = start + 1
            continue
        if isinstance(value, Mapping):
            yield value
        cursor = start + consumed


def _text_id(value: object) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    return text if text and text not in {"0", "-1", "None", "null"} else None


def _integer(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _extract_game_info(payload: Mapping[str, object]) -> Mapping[str, object] | None:
    candidate = payload.get("game_info") or payload.get("gameInfo")
    if isinstance(candidate, Mapping):
        return candidate
    data = payload.get("data")
    if isinstance(data, Mapping):
        candidate = data.get("game_info") or data.get("gameInfo")
        if isinstance(candidate, Mapping):
            return candidate
    return None


def _parse_match(payload: Mapping[str, object], fallback_match_id: str | None) -> CurrentMatch | None:
    game_info = _extract_game_info(payload)
    if not game_info:
        return None
    rows = game_info.get("players")
    if not isinstance(rows, list) or not rows:
        return None

    players: list[RawMatchPlayer] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        player_id = _text_id(row.get("player_id") or row.get("playerId"))
        steam_id = _text_id(row.get("steam_id") or row.get("steamId"))
        if not player_id:
            player_id = steam_id
        if not player_id or player_id in seen:
            continue
        seen.add(player_id)
        if steam_id and not _STEAMID_RE.fullmatch(steam_id):
            steam_id = None
        nickname_value = row.get("nick_name") or row.get("nickname") or row.get("nickName")
        nickname = str(nickname_value).strip() if nickname_value is not None else None
        # roll_team_id is the actual side assignment (1/2). troop_team_id is
        # the premade-party id and can differ for every player.
        team_id = _integer(
            row.get("roll_team_id")
            if row.get("roll_team_id") is not None
            else row.get("troop_team_id")
        )
        slot_value = (
            row.get("slot_id")
            if row.get("slot_id") is not None
            else row.get("slotId")
        )
        players.append(
            RawMatchPlayer(
                player_id=player_id,
                steam_id=steam_id,
                nickname=nickname or None,
                slot_id=_integer(slot_value),
                team_id=team_id,
            )
        )

    map_value = game_info.get("map_name") or game_info.get("mapName") or game_info.get("map")
    map_name = str(map_value).strip() if map_value is not None else ""
    match_id = fallback_match_id or _text_id(
        game_info.get("platform_game_id")
        or game_info.get("platformGameId")
        or payload.get("match_id")
        or payload.get("matchId")
    )
    if not players or not map_name:
        return None
    return CurrentMatch(
        match_id=match_id or "",
        map_name=map_name,
        players=tuple(players),
        host_info=_text_id(game_info.get("host_info") or game_info.get("hostInfo")),
        zone_name=_text_id(game_info.get("zone_name") or game_info.get("zoneName")),
        extras=parse_extra_info(payload),
    )


class PerfectWorldLogState:
    """Accumulate session and active-match state from decoded log messages."""

    def __init__(self) -> None:
        self.account_steamid: str | None = None
        self.session: SessionCredentials | None = None
        self.current_match: CurrentMatch | None = None
        self._synced_match_id: str | None = None

    def consume(self, raw_line: str) -> None:
        message = decode_log_line(raw_line)
        lowered = message.casefold()

        login_match = _LOGIN_STEAMID_RE.search(message)
        if login_match:
            self.account_steamid = login_match.group(1)

        objects = list(_json_objects(message))
        token_match = _TOKEN_RE.search(message)
        if token_match:
            for payload in reversed(objects):
                data = payload.get("data")
                user = data.get("user") if isinstance(data, Mapping) else None
                if not isinstance(user, Mapping):
                    continue
                steamid = _text_id(user.get("steam_id") or user.get("steamId"))
                jt = user.get("jt")
                if steamid and _STEAMID_RE.fullmatch(steamid) and isinstance(jt, str) and jt:
                    self.account_steamid = steamid
                    self.session = SessionCredentials(
                        account_steamid=steamid,
                        access_token=token_match.group(1),
                        jt=jt,
                    )
                    break

        is_game_over = "game over notify" in lowered or "mt_game_over_notify" in lowered
        if is_game_over:
            self.current_match = None
            self._synced_match_id = None

        match_updates = _MATCH_ID_RE.findall(message)
        if match_updates:
            raw_match_id = match_updates[-1].strip()
            # The launcher emits -1 for roughly one second between the
            # authoritative game-start payload and the real match id. It is a
            # transient initialization marker, not a match-over signal.
            if raw_match_id == "-1":
                candidate = self._synced_match_id
            elif raw_match_id in {"", "0"}:
                candidate = None
                self._synced_match_id = None
                self.current_match = None
            else:
                candidate = raw_match_id
                self._synced_match_id = candidate
                if self.current_match is not None:
                    self.current_match = replace(self.current_match, match_id=candidate)

        is_game_info = any(
            marker in lowered
            for marker in (
                "game start notify",
                "mt_game_start_notify",
                "restore match",
                "game info notify",
            )
        )
        if is_game_info:
            for payload in objects:
                match = _parse_match(payload, self._synced_match_id)
                if match is not None:
                    self.current_match = match
                    break

    def invalidate_match(self) -> None:
        """Drop the active match after the log was rewritten.

        Session credentials survive because a rotated log still belongs to the
        same signed-in client, while the previous match no longer exists.
        """
        self.current_match = None
        self._synced_match_id = None

    def snapshot(self) -> LocalPerfectWorldState:
        session = self.session
        if session is None and self.account_steamid:
            # An account id alone is useful to the watcher, but not enough to
            # authenticate API requests, so it is intentionally not promoted.
            session = None
        return LocalPerfectWorldState(self.current_match, session)


def find_log_files(log_dir: str | os.PathLike[str] = DEFAULT_LOG_DIR) -> list[Path]:
    directory = Path(log_dir)
    if not directory.is_dir():
        return []
    files = [path for path in directory.glob("pvpClient.*.log") if path.is_file()]
    return sorted(files, key=lambda path: (path.stat().st_mtime_ns, path.name))


def _nearest_existing(path: Path) -> Path:
    """Return the closest ancestor that exists, so a missing log dir is visible."""
    current = path
    while not current.exists():
        parent = current.parent
        if parent == current:
            return current
        current = parent
    return current


class LogFollower:
    """Incrementally tail the rotating client log instead of re-reading it.

    The desktop client keeps appending to ``pvpClient.*.log`` while a match is
    running, so re-reading entire files on every poll costs more the longer the
    client has been open.  This follower reads only the appended bytes, survives
    log rotation and truncation, and reports why nothing is arriving.
    """

    def __init__(
        self,
        log_dir: str | os.PathLike[str] = DEFAULT_LOG_DIR,
        *,
        max_initial_files: int = 3,
    ) -> None:
        self.log_dir = Path(log_dir)
        self.max_initial_files = max(1, int(max_initial_files))
        self.state = PerfectWorldLogState()
        self._path: Path | None = None
        self._offset = 0
        self._partial = ""
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._prefix: bytes | None = None
        self._primed = False
        self._bytes_read = 0
        self._lines_received = 0
        self._last_error: str | None = None
        self._source_lost = False
        self._watching = _nearest_existing(self.log_dir)

    def poll(self) -> bool:
        """Consume whatever the client appended since the previous poll."""
        files = find_log_files(self.log_dir)
        if not files:
            # The client was never started, or its log directory moved: keep
            # watching so the panel can explain the wait instead of failing.
            self._source_lost = self._path is not None
            self._watching = _nearest_existing(self.log_dir)
            return False

        self._source_lost = False
        self._watching = self.log_dir
        if not self._primed:
            self._primed = True
            return self._prime(files)

        newest = files[-1]
        if newest != self._path:
            # Daily rotation (or a fresh client run): start the new file at 0.
            self._path = newest
            self._offset = 0
            self._prefix = None
            self._reset_buffer()
        return self._read_appended()

    def snapshot(self) -> LocalPerfectWorldState:
        return self.state.snapshot()

    def diagnostics(self) -> dict[str, object]:
        path = self._path
        size = 0
        mtime = None
        if path is not None:
            try:
                stat = path.stat()
                size = stat.st_size
                mtime = stat.st_mtime
            except OSError:
                size = 0
                mtime = None
        return {
            "log_dir": str(self.log_dir),
            "watching": str(self._watching),
            "file": str(path) if path is not None else None,
            "file_exists": bool(path is not None and path.exists()),
            "file_size": size,
            "file_mtime": mtime,
            "offset": self._offset,
            "bytes_read": self._bytes_read,
            "lines_received": self._lines_received,
            "partial_pending": bool(self._partial),
            "last_error": self._last_error,
            "log_source_lost": self._source_lost,
        }

    def _prime(self, files: list[Path]) -> bool:
        """Replay recent history once so a cold start still sees the live match."""
        recent = files[-self.max_initial_files:]
        consumed = False
        for path in recent[:-1]:
            self._reset_buffer()
            consumed |= self._consume_range(path, 0, flush_tail=True)
        self._path = recent[-1]
        self._offset = 0
        self._reset_buffer()
        consumed |= self._consume_range(recent[-1], 0)
        self._prefix = self._head_bytes(recent[-1])
        return consumed

    @staticmethod
    def _head_bytes(path: Path, length: int = 64) -> bytes:
        try:
            with path.open("rb") as source:
                return source.read(length)
        except OSError:
            return b""

    def _reset_buffer(self) -> None:
        self._partial = ""
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")

    def _read_appended(self) -> bool:
        path = self._path
        if path is None:
            return False
        try:
            with path.open("rb") as source:
                prefix = source.read(64)
                source.seek(0, os.SEEK_END)
                size = source.tell()
        except OSError as exc:
            self._last_error = str(exc)
            return False

        # A restart truncates the file and rewrites it from the beginning, which
        # can leave the same byte count.  The head we already consumed must stay
        # untouched in an append-only log, so any change proves a rewrite.  The
        # head also grows while the file is still shorter than the probe size,
        # hence the overlap comparison.
        if self._prefix is not None:
            overlap = min(len(self._prefix), len(prefix))
            if prefix[:overlap] != self._prefix[:overlap]:
                self._offset = 0
                self._reset_buffer()
                self.state.invalidate_match()
        if self._prefix is None or len(prefix) > len(self._prefix):
            self._prefix = prefix[:64]
        if size < self._offset:
            self._offset = 0
            self._reset_buffer()
            self.state.invalidate_match()
        return self._consume_range(path, self._offset)

    def _consume_range(
        self, path: Path, start: int, *, flush_tail: bool = False
    ) -> bool:
        try:
            with path.open("rb") as source:
                source.seek(start)
                data = source.read()
        except OSError as exc:
            self._last_error = str(exc)
            return False

        self._last_error = None
        if not data:
            return False

        self._bytes_read += len(data)
        self._offset = start + len(data)
        buffered = self._partial + self._decoder.decode(data)
        lines = buffered.split("\n")
        self._partial = lines.pop()
        if flush_tail and self._partial:
            # An older file cannot be mid-line, so its tail is a real record.
            lines.append(self._partial)
            self._partial = ""

        consumed = False
        for line in lines:
            self.state.consume(line)
            self._lines_received += 1
            consumed = True
        return consumed


def read_local_state(
    log_dir: str | os.PathLike[str] = DEFAULT_LOG_DIR,
    *,
    recent_files: int = 3,
) -> LocalPerfectWorldState:
    state = PerfectWorldLogState()
    files = find_log_files(log_dir)
    keep = max(1, int(recent_files))
    recent = files[-keep:]
    for path in recent:
        with path.open("r", encoding="utf-8", errors="replace") as source:
            for line in source:
                state.consume(line)
    snapshot = state.snapshot()
    if snapshot.session is not None:
        return snapshot

    # A platform process can remain signed in across daily log rotations. The
    # active-match state must only use recent files, but credentials may safely
    # be recovered from the newest older login record.
    for path in reversed(files[:-keep]):
        older = PerfectWorldLogState()
        with path.open("r", encoding="utf-8", errors="replace") as source:
            for line in source:
                older.consume(line)
        session = older.snapshot().session
        if session is not None:
            return LocalPerfectWorldState(snapshot.current_match, session)
    return snapshot


def wait_for_current_match(
    log_dir: str | os.PathLike[str] = DEFAULT_LOG_DIR,
    *,
    timeout: float | None = None,
    poll_interval: float = 1.0,
    max_log_idle: float = 120.0,
    follower: LogFollower | None = None,
) -> LocalPerfectWorldState:
    """Wait until a fresh active match and an in-memory session are available."""
    started = time.monotonic()
    if follower is None:
        follower = LogFollower(log_dir)
    while True:
        follower.poll()
        state = follower.snapshot()
        diagnostics = follower.diagnostics()
        mtime = diagnostics.get("file_mtime")
        fresh = mtime is not None and time.time() - float(mtime) <= max_log_idle
        match = state.current_match
        if (
            fresh
            and match is not None
            and match.match_id
            and match.players
            and state.session is not None
        ):
            return state
        if timeout is not None and time.monotonic() - started >= timeout:
            raise TimeoutError("等待完美平台当前对局超时")
        time.sleep(max(0.1, poll_interval))
