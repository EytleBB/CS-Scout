import os
import sys
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import api_client


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_get_demos_uses_real_gate_pages_and_detail_demo_urls(monkeypatch):
    requested_gate_pages = []
    bootstrap_code = "g161-bootstrap"
    target_uuid = "54617242-59ac-11f0-a93a-0c42a164bc3c"

    page_one = [
        {"match_id": f"g161-nuke-{i}", "map": "de_nuke"}
        for i in range(30)
    ]
    page_two = [
        {"match_id": "g161-mirage-1", "map": "de_mirage"},
        {"match_id": "g161-mirage-2", "map": "de_mirage"},
    ]

    def fake_get(url, **kwargs):
        if "/api/data/player/" in url:
            return FakeResponse({
                "match": [{
                    "match_code": bootstrap_code,
                    "map": "de_nuke",
                    "demo_url": "",
                }]
            })

        if url.endswith(f"/match/{bootstrap_code}"):
            return FakeResponse({
                "code": 0,
                "data": {
                    "main": {"demo_url": ""},
                    "group_1": [{
                        "user_info": {"user_data": {
                            "domain": "target-domain",
                            "uuid": target_uuid,
                            "username": "target",
                            "steam": {"steamId": "765"},
                        }}
                    }],
                    "group_2": [],
                },
            })

        if "/match/list" in url:
            page = int(parse_qs(urlparse(url).query)["page"][0])
            requested_gate_pages.append(page)
            rows = page_one if page == 1 else page_two
            return FakeResponse({"code": 0, "data": rows})

        if "/match/g161-mirage-" in url:
            match_code = url.rsplit("/", 1)[-1]
            return FakeResponse({
                "code": 0,
                "data": {"main": {"demo_url": f"https://demo/{match_code}.zip"}},
            })

        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(api_client.requests, "get", fake_get)
    monkeypatch.setattr(api_client, "HTTP", SimpleNamespace(get=fake_get), raising=False)

    demos = api_client.get_demos_by_domain("target-domain", "de_mirage", count=2)

    assert requested_gate_pages == [1, 2]
    assert demos == [
        {
            "match_code": "g161-mirage-1",
            "demo_url": "https://demo/g161-mirage-1.zip",
        },
        {
            "match_code": "g161-mirage-2",
            "demo_url": "https://demo/g161-mirage-2.zip",
        },
    ]


def test_http_session_retries_transient_failures():
    retries = api_client.HTTP.adapters["https://"].max_retries

    assert retries.total == 3
    assert retries.status_forcelist == [429, 500, 502, 503, 504]


def test_get_demos_raises_when_every_match_list_request_fails(monkeypatch):
    assert hasattr(api_client, "DemoLookupError")

    def fail_get(url, **kwargs):
        raise requests.ConnectionError("5E unavailable")

    monkeypatch.setattr(api_client, "HTTP", SimpleNamespace(get=fail_get))

    with pytest.raises(api_client.DemoLookupError, match="5E unavailable"):
        api_client.get_demos_by_domain("target-domain", "de_mirage", count=2)
