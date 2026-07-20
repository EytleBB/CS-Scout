import os
import sys
import inspect
from urllib.parse import parse_qs, urlparse

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import api_client


PLAYER_UUID = "54617242-59ac-11f0-a93a-0c42a164bc3c"


def test_get_player_uuid_matches_domain_in_gate_detail(monkeypatch):
    seen = []

    def fake_detail(match_code):
        seen.append(match_code)
        return {
            "group_1": [{
                "user_info": {"user_data": {
                    "domain": "someone-else",
                    "uuid": "11111111-1111-1111-1111-111111111111",
                }}
            }],
            "group_2": [{
                "user_info": {"user_data": {
                    "domain": "target-domain",
                    "uuid": PLAYER_UUID,
                }}
            }],
        }

    monkeypatch.setattr(api_client, "get_match_detail", fake_detail)

    assert api_client._get_player_uuid(
        "target-domain", [{"match_code": "recent-match"}]
    ) == PLAYER_UUID
    assert seen == ["recent-match"]


def test_gate_match_page_sends_uuid_limit_and_distinct_page(monkeypatch):
    urls = []

    def fake_get(url):
        urls.append(url)
        return []

    monkeypatch.setattr(api_client, "_get", fake_get)

    assert api_client._get_gate_match_page(PLAYER_UUID, 1) == []
    assert api_client._get_gate_match_page(PLAYER_UUID, 2) == []

    queries = [parse_qs(urlparse(url).query) for url in urls]
    assert [query["page"] for query in queries] == [["1"], ["2"]]
    assert all(query["uuid"] == [PLAYER_UUID] for query in queries)
    assert all(query["limit"] == ["30"] for query in queries)


def test_later_gate_page_contributes_detail_only_demo(monkeypatch):
    pages = []
    first_page = [
        {"match_id": "first-map-match", "map": "de_mirage"},
        *[
            {"match_id": f"other-{i}", "map": "de_inferno"}
            for i in range(29)
        ],
    ]
    second_page = [{"match_id": "later-map-match", "map": "de_mirage"}]

    monkeypatch.setattr(
        api_client,
        "_get_public_matches",
        lambda domain, match_type=9: [{"match_code": "recent-match"}],
    )
    monkeypatch.setattr(
        api_client, "_get_player_uuid", lambda domain, matches: PLAYER_UUID
    )

    def fake_page(uuid, page, limit=30):
        pages.append((uuid, page, limit))
        return first_page if page == 1 else second_page

    monkeypatch.setattr(api_client, "_get_gate_match_page", fake_page)
    monkeypatch.setattr(
        api_client,
        "get_match_detail",
        lambda match_code: {
            "main": {"demo_url": f"https://detail.example/{match_code}.zip"}
        },
    )

    demos = api_client.get_demos_by_domain("target-domain", "de_mirage", count=2)

    assert demos == [
        {
            "match_code": "first-map-match",
            "demo_url": "https://detail.example/first-map-match.zip",
        },
        {
            "match_code": "later-map-match",
            "demo_url": "https://detail.example/later-map-match.zip",
        },
    ]
    assert pages == [(PLAYER_UUID, 1, 30), (PLAYER_UUID, 2, 30)]


def test_gate_detail_url_overrides_list_url(monkeypatch):
    monkeypatch.setattr(
        api_client,
        "_get_public_matches",
        lambda domain, match_type=9: [{"match_code": "recent-match"}],
    )
    monkeypatch.setattr(
        api_client, "_get_player_uuid", lambda domain, matches: PLAYER_UUID
    )
    monkeypatch.setattr(
        api_client,
        "_get_gate_match_page",
        lambda uuid, page, limit=30: [{
            "match_id": "map-match",
            "map": "de_mirage",
            "demo_url": "https://stale.example/demo.zip",
        }],
    )
    monkeypatch.setattr(
        api_client,
        "get_match_detail",
        lambda match_code: {
            "main": {"demo_url": "https://authoritative.example/demo.zip"}
        },
    )

    assert api_client.get_demos_by_domain(
        "target-domain", "de_mirage", count=1
    ) == [{
        "match_code": "map-match",
        "demo_url": "https://authoritative.example/demo.zip",
    }]


def test_retrying_session_covers_connections_and_5xx():
    retries = api_client._SESSION.get_adapter("https://").max_retries

    assert retries.total >= 2
    assert retries.connect >= 2
    assert retries.status >= 2
    assert {500, 502, 503, 504} <= set(retries.status_forcelist)
    assert "GET" in retries.allowed_methods


def test_uuid_bootstrap_failure_uses_bounded_public_fallback(monkeypatch):
    calls = []

    def fake_public(domain, match_type=9):
        calls.append(match_type)
        if match_type is None:
            return [{
                "match_code": "fallback-match",
                "map": "de_mirage",
                "demo_url": "https://public.example/fallback.zip",
            }]
        return [{"match_code": "bootstrap-only", "map": "de_inferno"}]

    monkeypatch.setattr(api_client, "_get_public_matches", fake_public)
    monkeypatch.setattr(api_client, "_get_player_uuid", lambda domain, matches: None)

    assert api_client.get_demos_by_domain(
        "target-domain", "de_mirage", count=1
    ) == [{
        "match_code": "fallback-match",
        "demo_url": "https://public.example/fallback.zip",
    }]
    assert calls == [9, None]


def test_lookup_error_only_when_every_public_source_fails(monkeypatch):
    calls = []

    def unavailable(domain, match_type=9):
        calls.append(match_type)
        raise RuntimeError("network down")

    monkeypatch.setattr(api_client, "_get_public_matches", unavailable)

    with pytest.raises(api_client.DemoLookupError):
        api_client.get_demos_by_domain("target-domain", "de_mirage", count=1)

    assert calls == [9, None, 1, 8]


def test_valid_empty_public_response_is_not_lookup_error(monkeypatch):
    monkeypatch.setattr(
        api_client, "_get_public_matches", lambda domain, match_type=9: []
    )

    assert api_client.get_demos_by_domain(
        "target-domain", "de_mirage", count=1
    ) == []


def test_gate_failure_is_not_downgraded_to_valid_empty_fallback(monkeypatch):
    monkeypatch.setattr(
        api_client,
        "_get_public_matches",
        lambda domain, match_type=9: [{
            "match_code": "bootstrap-match",
            "map": "de_inferno",
        }],
    )
    monkeypatch.setattr(
        api_client, "_get_player_uuid", lambda domain, matches: PLAYER_UUID
    )
    monkeypatch.setattr(
        api_client,
        "_get_gate_match_page",
        lambda uuid, page, limit=30: (_ for _ in ()).throw(
            RuntimeError("Gate unavailable")
        ),
    )

    with pytest.raises(api_client.DemoLookupError, match="Gate unavailable"):
        api_client.get_demos_by_domain(
            "target-domain", "de_mirage", count=2
        )


def test_uuid_bootstrap_request_failure_is_not_reported_as_empty(monkeypatch):
    monkeypatch.setattr(
        api_client,
        "_get_public_matches",
        lambda domain, match_type=9: [{
            "match_code": "bootstrap-match",
            "map": "de_inferno",
        }],
    )
    monkeypatch.setattr(
        api_client,
        "get_match_detail",
        lambda match_code: (_ for _ in ()).throw(
            RuntimeError("detail endpoint unavailable")
        ),
    )

    with pytest.raises(
        api_client.DemoLookupError, match="detail endpoint unavailable"
    ):
        api_client.get_demos_by_domain(
            "target-domain", "de_mirage", count=2
        )


def test_gate_failure_can_use_nonempty_public_fallback(monkeypatch):
    recent = [{
        "match_code": "fallback-match",
        "map": "de_mirage",
        "demo_url": "https://public.example/fallback.zip",
    }]
    monkeypatch.setattr(
        api_client,
        "_get_public_matches",
        lambda domain, match_type=9: recent,
    )
    monkeypatch.setattr(
        api_client, "_get_player_uuid", lambda domain, matches: PLAYER_UUID
    )
    monkeypatch.setattr(
        api_client,
        "_get_gate_match_page",
        lambda uuid, page, limit=30: (_ for _ in ()).throw(
            RuntimeError("Gate unavailable")
        ),
    )

    assert api_client.get_demos_by_domain(
        "target-domain", "de_mirage", count=2
    ) == [{
        "match_code": "fallback-match",
        "demo_url": "https://public.example/fallback.zip",
    }]


def test_later_gate_page_failure_preserves_partial_demos(monkeypatch):
    first_page = [
        {"match_id": "gate-match", "map": "de_mirage"},
        *[
            {"match_id": f"other-{i}", "map": "de_inferno"}
            for i in range(29)
        ],
    ]
    monkeypatch.setattr(
        api_client,
        "_get_public_matches",
        lambda domain, match_type=9: [{"match_code": "bootstrap-match"}],
    )
    monkeypatch.setattr(
        api_client, "_get_player_uuid", lambda domain, matches: PLAYER_UUID
    )

    def fake_page(uuid, page, limit=30):
        if page == 1:
            return first_page
        raise RuntimeError("page two failed")

    monkeypatch.setattr(api_client, "_get_gate_match_page", fake_page)
    monkeypatch.setattr(
        api_client,
        "get_match_detail",
        lambda match_code: {
            "main": {"demo_url": "https://detail.example/gate-match.zip"}
        },
    )

    assert api_client.get_demos_by_domain(
        "target-domain", "de_mirage", count=2
    ) == [{
        "match_code": "gate-match",
        "demo_url": "https://detail.example/gate-match.zip",
    }]


def test_http_client_never_disables_tls_verification():
    assert "verify=False" not in inspect.getsource(api_client)


def test_renamed_player_resolves_steamid_by_domain_first(monkeypatch):
    monkeypatch.setattr(
        api_client,
        "get_match_detail",
        lambda match_code: {
            "group_1": [{
                "user_info": {"user_data": {
                    "domain": "target-domain",
                    "username": "New Display Name",
                    "steam": {"steamId": "76561198000000001"},
                }}
            }],
            "group_2": [],
        },
    )

    assert api_client.get_steamid_for_player(
        "g161-safe", "Old Display Name", domain="target-domain"
    ) == "76561198000000001"


@pytest.mark.parametrize(
    "value",
    ["", "../escape", "a/b", "a\\b", ".", "C:drive", "CON", "aux", "LPT9"],
)
def test_upstream_identifiers_reject_unsafe_path_syntax(value):
    with pytest.raises(ValueError):
        api_client.validate_domain(value)
    with pytest.raises(ValueError):
        api_client.validate_match_id(value)
