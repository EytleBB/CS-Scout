"""Capture 5E business HTTP responses from CDP Network events.

This module is a pure protocol handler.  It owns no WebSocket connection and
no thread: the caller feeds decoded CDP messages in and sends the commands the
handler asks for.  The state machine for one business request is::

    requestWillBeSent  -> pending request (URL / method / JSON request body)
    loadingFinished    -> emit Network.getResponseBody, wait for the body
    response to that   -> parse the JSON body, keep it in results()

Whitelisting is done on URL *path fragments* (not on host or full URL), so it
survives the ``cranenew`` / ``crane`` prefix splits described in the cmh
reference docs.

The handler is intentionally not thread-safe; the caller must serialise calls
to the instance.  ``on_message`` returns the CDP method names the caller has to
send next, while :meth:`ResponseCapture.take_commands` returns the complete
wire commands (including the generated JSON-RPC ``id``) that carry the
``requestId``.
"""

from __future__ import annotations

import base64
import binascii
from copy import deepcopy
import json
import time
from typing import Any

DEFAULT_MAX_PENDING = 256

GET_RESPONSE_BODY = "Network.getResponseBody"

# Path fragment -> payload kind.  Order matters only for readability; the
# fragments are disjoint in practice.
WHITELIST: tuple[tuple[str, str], ...] = (
    ("/player/map-ext/batch", "map_ext_batch"),
    ("/player/elo/info/batch", "elo_info_batch"),
    ("/api/user/info", "user_info"),
    ("/api/data/match/", "match_detail"),
)

_MATCH_DETAIL_FRAGMENT = "/api/data/match/"
_FIRST_COMMAND_ID = 1_000_000


def classify_url(url: object) -> tuple[str, str] | None:
    """Return ``(kind, match_code)`` for a whitelisted URL, else ``None``.

    Matching is case-insensitive and fragment-based.  ``match_code`` is only
    populated for the single-match detail endpoint; it is the last non-empty
    path segment.
    """
    if not isinstance(url, str) or not url:
        return None
    lowered = url.casefold()
    for fragment, kind in WHITELIST:
        if fragment in lowered:
            match_code = _match_code(url) if kind == "match_detail" else ""
            return kind, match_code
    return None


def _match_code(url: str) -> str:
    without_suffix = url.split("#", 1)[0].split("?", 1)[0]
    index = without_suffix.casefold().find(_MATCH_DETAIL_FRAGMENT)
    if index < 0:
        return ""
    remainder = without_suffix[index + len(_MATCH_DETAIL_FRAGMENT):]
    segments = [segment for segment in remainder.split("/") if segment]
    return segments[-1] if segments else ""


def _decode_json(text: object) -> tuple[bool, Any]:
    if not isinstance(text, str) or not text:
        return False, None
    try:
        return True, json.loads(text)
    except (TypeError, ValueError):
        return False, None


def _request_body(value: object) -> object:
    parsed, payload = _decode_json(value)
    return payload if parsed else None


class ResponseCapture:
    """CDP Network response collector for the 5E business whitelist."""

    def __init__(self, *, max_pending: int = DEFAULT_MAX_PENDING) -> None:
        try:
            limit = int(max_pending)
        except (TypeError, ValueError, OverflowError):
            limit = DEFAULT_MAX_PENDING
        self.max_pending = max(1, limit)
        self._pending_requests: dict[str, dict[str, Any]] = {}
        self._pending_bodies: dict[int, dict[str, Any]] = {}
        self._commands: list[dict[str, Any]] = []
        self._payloads: list[dict[str, Any]] = []
        self._next_command_id = _FIRST_COMMAND_ID
        self._counters = {
            "captured": 0,
            "ignored": 0,
            "failed": 0,
            "overflowed": 0,
            "dropped": 0,
            "duplicate": 0,
            "unknown": 0,
            "aborted": 0,
        }

    def on_message(self, message: dict) -> list[str]:
        """Feed one decoded CDP message.

        Returns the CDP method names the caller must send next.  The matching
        wire commands (with ``params.requestId`` and a JSON-RPC ``id``) are
        available from :meth:`take_commands`.
        """
        if not isinstance(message, dict):
            return []

        method = message.get("method")
        if isinstance(method, str):
            if method == "Network.requestWillBeSent":
                self._on_request_will_be_sent(message)
            elif method == "Network.loadingFinished":
                request_id = self._param_request_id(message)
                if request_id:
                    return self._on_loading_finished(request_id)
            elif method == "Network.loadingFailed":
                self._on_loading_failed(message)
            return []

        # A response to one of our own commands: no method, only id/result.
        if "id" in message:
            self._on_command_response(message)
        return []

    def take_commands(self) -> list[dict]:
        """Drain the wire commands the caller must send."""
        commands = self._commands
        self._commands = []
        return commands

    def results(self) -> dict:
        """Return a deep copy of the captured payloads and per-kind counts."""
        payloads = deepcopy(self._payloads)
        counts: dict[str, int] = {}
        for payload in payloads:
            kind = str(payload.get("kind") or "")
            counts[kind] = counts.get(kind, 0) + 1
        return {"payloads": payloads, "counts": counts}

    def stats(self) -> dict:
        """Return the counters used by the zero-event watchdog."""
        return {
            "captured": self._counters["captured"],
            "ignored": self._counters["ignored"],
            "failed": self._counters["failed"],
            "pending": len(self._pending_requests) + len(self._pending_bodies),
            "overflowed": self._counters["overflowed"],
            "dropped": self._counters["dropped"],
            "duplicate": self._counters["duplicate"],
            "unknown": self._counters["unknown"],
            "aborted": self._counters["aborted"],
        }

    # -- request lifecycle -------------------------------------------------

    @staticmethod
    def _param_request_id(message: dict) -> str:
        params = message.get("params")
        if not isinstance(params, dict):
            return ""
        request_id = params.get("requestId")
        return request_id if isinstance(request_id, str) else ""

    def _on_request_will_be_sent(self, message: dict) -> None:
        request_id = self._param_request_id(message)
        if not request_id:
            return
        params = message.get("params")
        request = params.get("request") if isinstance(params, dict) else None
        if not isinstance(request, dict):
            request = {}

        url = request.get("url")
        if not isinstance(url, str):
            url = ""
        classified = classify_url(url)
        if classified is None:
            self._counters["ignored"] += 1
            return

        kind, match_code = classified
        method = request.get("method")
        if not isinstance(method, str) or not method:
            method = "GET"

        tracked = len(self._pending_requests) + len(self._pending_bodies)
        if request_id not in self._pending_requests and tracked >= self.max_pending:
            self._purge_pending()

        self._pending_requests[request_id] = {
            "request_id": request_id,
            "kind": kind,
            "url": url,
            "method": method,
            "match_code": match_code,
            "request_body": _request_body(request.get("postData")),
        }

    def _on_loading_finished(self, request_id: str) -> list[str]:
        record = self._pending_requests.pop(request_id, None)
        if record is None:
            return []
        command_id = self._next_command_id
        self._next_command_id += 1
        self._pending_bodies[command_id] = record
        self._commands.append({
            "id": command_id,
            "method": GET_RESPONSE_BODY,
            "params": {"requestId": request_id},
        })
        return [GET_RESPONSE_BODY]

    def _on_loading_failed(self, message: dict) -> None:
        request_id = self._param_request_id(message)
        if request_id and self._pending_requests.pop(request_id, None) is not None:
            self._counters["aborted"] += 1

    def _purge_pending(self) -> None:
        dropped = len(self._pending_requests) + len(self._pending_bodies)
        self._pending_requests.clear()
        self._pending_bodies.clear()
        self._commands.clear()
        self._counters["overflowed"] += 1
        self._counters["dropped"] += dropped

    # -- response to Network.getResponseBody ------------------------------

    def _on_command_response(self, message: dict) -> None:
        command_id = message.get("id")
        if isinstance(command_id, bool) or not isinstance(command_id, int):
            return

        record = self._pending_bodies.pop(command_id, None)
        if record is None:
            if _FIRST_COMMAND_ID <= command_id < self._next_command_id:
                self._counters["duplicate"] += 1
            else:
                self._counters["unknown"] += 1
            return

        if message.get("error") is not None:
            self._counters["failed"] += 1
            return

        result = message.get("result")
        if not isinstance(result, dict):
            self._counters["failed"] += 1
            return
        body = result.get("body")
        if not isinstance(body, str):
            self._counters["failed"] += 1
            return
        if result.get("base64Encoded") is True:
            try:
                body = base64.b64decode(body).decode("utf-8")
            except (binascii.Error, UnicodeDecodeError, ValueError):
                self._counters["failed"] += 1
                return

        parsed, payload = _decode_json(body)
        if not parsed:
            self._counters["failed"] += 1
            return

        captured = dict(record)
        captured["captured_at"] = time.time()
        captured["response_body"] = payload
        self._payloads.append(captured)
        self._counters["captured"] += 1
