"""Protocol-level tests for the 5E CDP Network response capture.

The capture module is driven with hand-written CDP message dictionaries; it
never opens a socket or talks to a real 5E client.
"""

import base64
import json
from types import SimpleNamespace

import pytest

import fivee_capture
import fivee_monitor


MAP_EXT_URL = (
    "https://gate.5eplay.com/cranenew/http/api/data/player/map-ext/batch"
)
ELO_INFO_URL = (
    "https://gate.5eplay.com/cranenew/http/api/data/player/elo/info/batch"
)
USER_INFO_URL = "https://platform-api.5eplay.com/api/user/info"
MATCH_DETAIL_URL = (
    "https://gate.5eplay.com/crane/http/api/data/match/"
    "g161-20260614142743982331607"
)


def _request(
    request_id,
    url,
    *,
    method="POST",
    body=None,
    post_data=None,
):
    request = {"url": url, "method": method}
    if post_data is not None:
        request["postData"] = post_data
    elif body is not None:
        request["postData"] = json.dumps(body)
    return {
        "method": "Network.requestWillBeSent",
        "params": {"requestId": request_id, "request": request},
    }


def _finished(request_id):
    return {
        "method": "Network.loadingFinished",
        "params": {"requestId": request_id},
    }


def _body_response(command, payload, *, base64_encoded=False):
    body = payload if isinstance(payload, str) else json.dumps(payload)
    if base64_encoded:
        body = base64.b64encode(body.encode("utf-8")).decode("ascii")
    return {
        "id": command["id"],
        "result": {"body": body, "base64Encoded": base64_encoded},
    }


def _capture_body(capture, request_id, url, body, **kwargs):
    capture.on_message(_request(request_id, url))
    assert capture.on_message(_finished(request_id)) == ["Network.getResponseBody"]
    command = capture.take_commands()[-1]
    capture.on_message(_body_response(command, body, **kwargs))


def test_whitelisted_request_finished_and_body_yields_parsed_payload():
    payload = {"code": 0, "data": {"uuid-1": {"de_mirage": {"matchTotal": "572"}}}}
    capture = fivee_capture.ResponseCapture()

    assert capture.on_message(
        _request("req-1", MAP_EXT_URL, body={"uuids": ["uuid-1"]})
    ) == []
    assert capture.stats()["pending"] == 1

    instructions = capture.on_message(_finished("req-1"))
    assert instructions == ["Network.getResponseBody"]
    commands = capture.take_commands()
    assert len(commands) == 1
    assert commands[0]["method"] == "Network.getResponseBody"
    assert commands[0]["params"] == {"requestId": "req-1"}
    assert "id" in commands[0]
    assert capture.take_commands() == []
    assert capture.stats()["pending"] == 1

    assert capture.on_message(_body_response(commands[0], payload)) == []

    result = capture.results()
    assert len(result["payloads"]) == 1
    record = result["payloads"][0]
    assert record["kind"] == "map_ext_batch"
    assert record["url"] == MAP_EXT_URL
    assert record["method"] == "POST"
    assert record["request_id"] == "req-1"
    assert record["request_body"] == {"uuids": ["uuid-1"]}
    assert record["response_body"] == payload

    stats = capture.stats()
    assert stats["captured"] == 1
    assert stats["failed"] == 0
    assert stats["ignored"] == 0
    assert stats["pending"] == 0


@pytest.mark.parametrize(
    ("url", "kind", "match_code"),
    [
        (MAP_EXT_URL, "map_ext_batch", ""),
        (ELO_INFO_URL + "?ts=1781593342", "elo_info_batch", ""),
        (USER_INFO_URL, "user_info", ""),
        (
            MATCH_DETAIL_URL,
            "match_detail",
            "g161-20260614142743982331607",
        ),
        (
            "https://GATE.5eplay.com/Crane/http/API/data/MATCH/"
            "g161-n-20260614142743982331607?from=client",
            "match_detail",
            "g161-n-20260614142743982331607",
        ),
    ],
)
def test_whitelist_matches_path_fragments_and_classifies_requests(
    url, kind, match_code
):
    capture = fivee_capture.ResponseCapture()
    payload = {"code": 0, "data": {}}

    _capture_body(capture, "req-1", url, payload)

    record = capture.results()["payloads"][0]
    assert record["kind"] == kind
    assert record["match_code"] == match_code
    assert record["response_body"] == payload


def test_match_detail_match_code_is_used_as_the_last_path_segment():
    capture = fivee_capture.ResponseCapture()
    _capture_body(capture, "req-1", MATCH_DETAIL_URL, {"code": 0})

    assert (
        capture.results()["payloads"][0]["match_code"]
        == "g161-20260614142743982331607"
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://view-arena.5eplay.com/static/app.js",
        "https://gate.5eplay.com/crane/http/api/data/match",
        "https://example.com/api/username/info",
        "https://gate.5eplay.com/cranenew/http/api/data/player/map-ext",
        "",
    ],
)
def test_non_whitelisted_url_is_ignored_and_not_pending(url):
    capture = fivee_capture.ResponseCapture()

    assert capture.on_message(_request("req-1", url)) == []

    stats = capture.stats()
    assert stats["ignored"] == 1
    assert stats["pending"] == 0
    assert capture.on_message(_finished("req-1")) == []
    assert capture.take_commands() == []
    assert capture.results()["payloads"] == []


def test_loading_finished_without_tracked_request_sends_nothing():
    capture = fivee_capture.ResponseCapture()

    assert capture.on_message(_finished("missing")) == []
    assert capture.take_commands() == []
    assert capture.stats()["pending"] == 0


def test_non_json_body_counts_as_failed_without_raising():
    capture = fivee_capture.ResponseCapture()
    _capture_body(capture, "req-1", USER_INFO_URL, "<html>not json</html>")

    stats = capture.stats()
    assert stats["failed"] == 1
    assert stats["captured"] == 0
    assert stats["pending"] == 0
    assert capture.results()["payloads"] == []


def test_large_non_json_body_counts_as_failed_without_raising():
    capture = fivee_capture.ResponseCapture()
    _capture_body(capture, "req-1", USER_INFO_URL, "x" * (2 * 1024 * 1024))

    assert capture.stats()["failed"] == 1
    assert capture.stats()["captured"] == 0


def test_large_valid_json_body_is_captured():
    payload = {"code": 0, "data": {"blob": "y" * (2 * 1024 * 1024)}}
    capture = fivee_capture.ResponseCapture()
    _capture_body(capture, "req-1", USER_INFO_URL, payload)

    assert capture.stats()["captured"] == 1
    assert capture.results()["payloads"][0]["response_body"] == payload


@pytest.mark.parametrize(
    "response",
    [
        {"id": "command"},
        {"id": "command", "error": {"code": -32000, "message": "No body"}},
        {"id": "command", "result": None},
        {"id": "command", "result": {}},
        {"id": "command", "result": {"body": ""}},
        {"id": "command", "result": {"body": 123}},
    ],
)
def test_missing_result_or_body_counts_as_failed(response):
    capture = fivee_capture.ResponseCapture()
    capture.on_message(_request("req-1", USER_INFO_URL))
    capture.on_message(_finished("req-1"))
    command = capture.take_commands()[-1]
    message = dict(response)
    message["id"] = command["id"]

    assert capture.on_message(message) == []

    stats = capture.stats()
    assert stats["failed"] == 1
    assert stats["captured"] == 0
    assert stats["pending"] == 0


def test_base64_encoded_body_is_decoded():
    payload = {"code": 0, "data": {"uuid-1": {"username": "Naffri"}}}
    capture = fivee_capture.ResponseCapture()
    _capture_body(
        capture, "req-1", USER_INFO_URL, payload, base64_encoded=True
    )

    assert capture.results()["payloads"][0]["response_body"] == payload
    assert capture.stats()["captured"] == 1


def test_duplicate_response_is_counted_but_never_captured_twice():
    payload = {"code": 0, "data": {}}
    capture = fivee_capture.ResponseCapture()
    capture.on_message(_request("req-1", USER_INFO_URL))
    capture.on_message(_finished("req-1"))
    command = capture.take_commands()[-1]
    response = _body_response(command, payload)

    capture.on_message(response)
    capture.on_message(response)

    stats = capture.stats()
    assert stats["captured"] == 1
    assert stats["duplicate"] == 1
    assert stats["failed"] == 0
    assert len(capture.results()["payloads"]) == 1


def test_pending_overflow_purges_requests_and_bodies_then_counts():
    capture = fivee_capture.ResponseCapture(max_pending=2)

    capture.on_message(_request("req-1", MAP_EXT_URL))
    capture.on_message(_request("req-2", ELO_INFO_URL))
    capture.on_message(_finished("req-1"))
    capture.on_message(_finished("req-2"))
    assert capture.stats()["pending"] == 2
    stale_command = capture.take_commands()[-1]

    capture.on_message(_request("req-3", USER_INFO_URL))

    stats = capture.stats()
    assert stats["pending"] == 1
    assert stats["overflowed"] == 1
    assert stats["dropped"] == 2
    assert capture.take_commands() == []

    assert capture.on_message(_finished("req-1")) == []
    assert capture.on_message(_body_response(stale_command, {"code": 0})) == []
    assert capture.stats()["captured"] == 0
    assert capture.stats()["failed"] == 0


def test_stats_report_captured_ignored_failed_and_pending_together():
    capture = fivee_capture.ResponseCapture()

    capture.on_message(_request("pending", MAP_EXT_URL))
    capture.on_message(_request("ignored", "https://example.com/app.js"))
    _capture_body(capture, "failed", USER_INFO_URL, "not json")
    _capture_body(capture, "captured", MATCH_DETAIL_URL, {"code": 0})

    stats = capture.stats()
    assert stats["captured"] == 1
    assert stats["ignored"] == 1
    assert stats["failed"] == 1
    assert stats["pending"] == 1
    assert isinstance(stats["captured"], int)
    assert isinstance(stats["ignored"], int)
    assert isinstance(stats["failed"], int)
    assert isinstance(stats["pending"], int)


@pytest.mark.parametrize(
    "message",
    [
        {},
        {"method": "Network.requestWillBeSent"},
        {"method": "Network.requestWillBeSent", "params": {}},
        {"method": "Network.requestWillBeSent", "params": {"requestId": "r"}},
        {"method": "Network.loadingFinished", "params": {}},
        {"method": "Network.loadingFinished", "params": {"requestId": 7}},
        {"id": 1},
        {"id": 1, "result": {}},
        {"method": "Runtime.consoleAPICalled", "params": {}},
        None,
        [],
    ],
)
def test_malformed_or_unrelated_messages_are_tolerated(message):
    capture = fivee_capture.ResponseCapture()

    assert capture.on_message(message) == []
    assert capture.take_commands() == []
    assert capture.stats()["pending"] == 0


# --- fivee_monitor wiring -------------------------------------------------


class _FakeWebSocketModule:
    WebSocketTimeoutException = TimeoutError


class _ScriptedConnection:
    def __init__(self, script):
        self.script = list(script)
        self.sent = []
        self.closed = False

    def send(self, raw):
        self.sent.append(json.loads(raw))

    def recv(self):
        if not self.script:
            return ""
        item = self.script.pop(0)
        if callable(item):
            item = item(self.sent)
        return json.dumps(item)

    def settimeout(self, _timeout):
        pass

    def close(self):
        self.closed = True


def _captured_reply(payload):
    def reply(sent):
        command = next(
            item
            for item in reversed(sent)
            if item.get("method") == "Network.getResponseBody"
        )
        return {"id": command["id"], "result": {"body": json.dumps(payload)}}

    return reply


def _capture_script(payload):
    return [
        {"id": 1, "result": {}},
        {
            "method": "Network.requestWillBeSent",
            "params": {
                "requestId": "req-1",
                "request": {
                    "url": USER_INFO_URL,
                    "method": "POST",
                    "postData": json.dumps({"uuids": ["uuid-1"]}),
                },
            },
        },
        {
            "method": "Network.loadingFinished",
            "params": {"requestId": "req-1"},
        },
        _captured_reply(payload),
    ]


def _service_with_connection(connection, **kwargs):
    websocket_module = SimpleNamespace(
        WebSocketTimeoutException=TimeoutError,
        create_connection=lambda *_args, **_kwargs: connection,
    )
    return fivee_monitor.FiveEAutoScoutService(
        auto_launch=False,
        websocket_module=websocket_module,
        **kwargs,
    )


def test_fivee_monitor_capture_is_enabled_by_default(monkeypatch):
    monkeypatch.delenv("CS_SCOUT_5E_CAPTURE", raising=False)
    service = fivee_monitor.FiveEAutoScoutService(
        auto_launch=False, websocket_module=object()
    )

    assert isinstance(service._capture, fivee_capture.ResponseCapture)
    snapshot = service.snapshot()
    assert snapshot["capture"]["captured"] == 0
    assert snapshot["captured_payloads"] == []


def test_fivee_monitor_capture_can_be_switched_off_by_environment(monkeypatch):
    monkeypatch.setenv("CS_SCOUT_5E_CAPTURE", "0")
    service = fivee_monitor.FiveEAutoScoutService(
        auto_launch=False, websocket_module=object()
    )

    assert service._capture is None
    snapshot = service.snapshot()
    assert "capture" not in snapshot
    assert "captured_payloads" not in snapshot


def test_fivee_monitor_feeds_network_events_to_capture_when_enabled():
    payload = {"code": 0, "data": {"uuid-1": {"username": "Naffri"}}}
    connection = _ScriptedConnection(_capture_script(payload))
    service = _service_with_connection(connection, capture_responses=True)

    service._target_loop("target-1", "ws://127.0.0.1/devtools/page/1")

    assert connection.sent[0] == {
        "id": 1,
        "method": "Network.enable",
        "params": {},
    }
    body_commands = [
        command
        for command in connection.sent
        if command.get("method") == "Network.getResponseBody"
    ]
    assert len(body_commands) == 1
    assert body_commands[0]["params"] == {"requestId": "req-1"}

    snapshot = service.snapshot()
    assert snapshot["capture"]["captured"] == 1
    assert snapshot["capture"]["pending"] == 0
    assert snapshot["captured_payloads"][-1]["response_body"] == payload
    assert snapshot["captured_payloads"][-1]["request_body"] == {
        "uuids": ["uuid-1"]
    }


def test_fivee_monitor_with_capture_off_never_requests_response_bodies():
    connection = _ScriptedConnection(
        [
            {"id": 1, "result": {}},
            {
                "method": "Network.requestWillBeSent",
                "params": {
                    "requestId": "req-1",
                    "request": {"url": USER_INFO_URL, "method": "POST"},
                },
            },
            {
                "method": "Network.loadingFinished",
                "params": {"requestId": "req-1"},
            },
        ]
    )
    service = _service_with_connection(connection, capture_responses=False)

    service._target_loop("target-1", "ws://127.0.0.1/devtools/page/1")

    assert [command["method"] for command in connection.sent] == [
        "Network.enable"
    ]
    snapshot = service.snapshot()
    assert "capture" not in snapshot
    assert "captured_payloads" not in snapshot


@pytest.mark.parametrize("capture_responses", [True, False])
def test_fivee_monitor_websocket_match_detection_still_runs(
    monkeypatch, capture_responses
):
    team_1 = [f"00000000-0000-0000-0000-{index:012d}" for index in range(1, 6)]
    team_2 = [f"00000000-0000-0000-0000-{index:012d}" for index in range(6, 11)]
    game_ctx = {
        "id": "match_123",
        "mapName": "de_mirage",
        "gmi": {
            "t1": {"rooms": [{"members": team_1[:2]}, {"members": team_1[2:]}]},
            "t2": {"rooms": [{"members": team_2}]},
        },
    }
    connection = _ScriptedConnection(
        [
            {"id": 1, "result": {}},
            {
                "method": "Network.webSocketFrameReceived",
                "params": {
                    "response": {
                        "opcode": 1,
                        "payloadData": json.dumps({"game_ctx": game_ctx}),
                    }
                },
            },
        ]
    )
    service = _service_with_connection(
        connection, capture_responses=capture_responses
    )
    queued = []
    monkeypatch.setattr(service, "_queue_match", queued.append)

    service._target_loop("target-1", "ws://127.0.0.1/devtools/page/1")

    assert [match["match_id"] for match in queued] == ["match_123"]
    assert queued[0]["map"] == "de_mirage"
