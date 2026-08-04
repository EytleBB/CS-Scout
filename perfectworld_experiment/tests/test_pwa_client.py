from dataclasses import dataclass

import pytest

from perfectworld_experiment.pwa_client import (
    PerfectWorldClient,
    normalize_map_name,
    validate_access_token,
    validate_steamid,
)


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.request = None

    def get(self, url, **kwargs):
        self.request = (url, kwargs)
        return FakeResponse(self.payload)


class HistorySession:
    def __init__(self):
        self.posts = []

    def get(self, url, **kwargs):
        return FakeResponse(
            {
                "code": 0,
                "data": [
                    {"match": "match-new"},
                    {"match": "match-expired"},
                ],
            }
        )

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        match_id = __import__("json").loads(kwargs["data"])["match_id"]
        return FakeResponse(
            {
                "code": 0,
                "data": {
                    "baseInfo": {
                        "match": match_id,
                        "cup_id": None,
                        "map_info": {"name_en": "de_mirage"},
                    },
                    "demo_info": {
                        "demo_is_available": match_id == "match-new",
                        "expired": match_id == "match-expired",
                    },
                },
            }
        )


@dataclass
class Metadata:
    match_id: str
    demo_url: str
    map_name: str | None = None
    map_label: str | None = None


def test_map_aliases_cover_platform_labels():
    assert normalize_map_name("荒漠迷城") == "de_mirage"
    assert normalize_map_name("Mirage") == "de_mirage"
    assert normalize_map_name("炙热沙城II") == "de_dust2"


def test_credentials_are_strictly_validated():
    assert validate_steamid("76561198123456789") == "76561198123456789"
    with pytest.raises(ValueError):
        validate_steamid("123")
    with pytest.raises(ValueError):
        validate_access_token("bad\ntoken")


def test_discovery_filters_map_deduplicates_and_never_returns_token():
    token = "secret-token"

    def fetcher(steamid, access_token, size):
        assert access_token == token
        assert size == 12
        return [
            Metadata("match-a", f"https://example.test/a?access_token={token}", "荒漠迷城"),
            Metadata("match-a", "https://example.test/duplicate", "de_mirage"),
            Metadata("match-b", "https://example.test/b", "de_inferno"),
            Metadata("match-c", "https://example.test/c", None, "Mirage"),
        ]

    client = PerfectWorldClient(
        "76561198123456789",
        token,
        metadata_fetcher=fetcher,
        header_builder=lambda steamid: {"X-PWA-SteamId": steamid},
    )
    demos = client.discover(map_name="de_mirage", limit=3)
    assert [item.match_id for item in demos] == ["match-a", "match-c"]
    public_result = [{"match_id": item.match_id, "map": item.map_name} for item in demos]
    assert token not in repr(public_result)


def test_backend_exception_is_sanitized():
    token = "secret-token"

    def failed_fetcher(*args, **kwargs):
        raise RuntimeError(f"request failed: ?access_token={token}")

    client = PerfectWorldClient(
        "76561198123456789",
        token,
        metadata_fetcher=failed_fetcher,
        header_builder=lambda steamid: {},
    )
    with pytest.raises(RuntimeError) as captured:
        client.discover(map_name="de_mirage", limit=1)
    assert token not in str(captured.value)


def test_recent_keeps_cross_map_order():
    client = PerfectWorldClient(
        "76561198123456789",
        "secret-token",
        metadata_fetcher=lambda *args, **kwargs: [
            Metadata("new", "https://example.test/new", "Mirage"),
            Metadata("old", "https://example.test/old", "Inferno"),
        ],
        header_builder=lambda steamid: {},
    )
    assert [(item.match_id, item.map_name) for item in client.recent(limit=2)] == [
        ("new", "de_mirage"),
        ("old", "de_inferno"),
    ]


def test_resolve_players_uses_account_session_and_keeps_roster_order(monkeypatch):
    session = FakeSession(
        {
            "code": 0,
            "data": {
                "players_info": {
                    "1001": {"uid": "76561198123456789", "nickname": "Alpha"},
                    "1002": {"uid": "76561198987654321", "nickname": "Bravo"},
                }
            },
        }
    )
    captured = {}

    def signed(params):
        captured.update(params)
        return {"signed": "yes", **{key: str(value) for key, value in params.items()}}

    monkeypatch.setattr("perfectworld_experiment.pwa_client.build_signed_params", signed)
    client = PerfectWorldClient(
        "76561198000000000",
        "access-secret",
        metadata_fetcher=lambda *args, **kwargs: [],
        header_builder=lambda steamid: {},
        session=session,
    )
    players = client.resolve_players(["1002", "1001"], jt="session-jt")

    assert [player.nickname for player in players] == ["Bravo", "Alpha"]
    assert captured["uid_list"] == "1002,1001"
    assert captured["token"] == "access-secret"
    url, request = session.request
    assert url == "https://pwa-account.wmpvp.com/user/playersInfo"
    assert request["headers"]["Pwa-Jt"] == "session-jt"
    assert request["headers"]["PwaSteamId"] == "76561198000000000"


def test_discover_can_query_an_opponent_with_the_logged_in_account():
    seen = {}

    def fetcher(steamid, access_token, size):
        seen.update(steamid=steamid, access_token=access_token, size=size)
        return [Metadata("match-a", "https://example.test/a", "Mirage")]

    client = PerfectWorldClient(
        "76561198000000000",
        "access-secret",
        metadata_fetcher=fetcher,
        header_builder=lambda steamid: {},
    )
    demos = client.discover(
        map_name="de_mirage",
        limit=1,
        target_steamid="76561198123456789",
    )
    assert [demo.match_id for demo in demos] == ["match-a"]
    assert seen["steamid"] == "76561198123456789"


def test_real_history_shape_is_enriched_by_match_detail(monkeypatch):
    session = HistorySession()
    monkeypatch.setattr(
        "perfectworld_experiment.pwa_client.build_signed_params",
        lambda params: {key: str(value) for key, value in params.items()},
    )
    monkeypatch.setattr(
        "perfectworld_experiment.pwa_client.build_signature_params",
        lambda body: {"signed": "yes"},
    )
    monkeypatch.setattr(
        "perfectworld_experiment.pwa_client.build_demo_url",
        lambda match_id, cup_id, token: f"https://example.test/{match_id}/{cup_id}",
    )
    client = PerfectWorldClient(
        "76561198000000000",
        "access-secret",
        header_builder=lambda steamid: {},
        session=session,
    )

    demos = client.discover(map_name="de_mirage", limit=2)
    assert [(demo.match_id, demo.map_name) for demo in demos] == [
        ("match-new", "de_mirage")
    ]
    assert len(session.posts) == 2
    assert all(url.endswith("/match-api/detail") for url, _ in session.posts)
