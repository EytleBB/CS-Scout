import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import api_client

def test_get_demo_url_from_gate(monkeypatch):
    monkeypatch.setattr(api_client, "get_match_detail",
        lambda mc: {"main": {"demo_url": "https://gz-t-demo.5eplaycdn.com/x.zip"}})
    assert api_client.get_demo_url("g161-x") == "https://gz-t-demo.5eplaycdn.com/x.zip"

def test_get_demo_url_missing_returns_none(monkeypatch):
    monkeypatch.setattr(api_client, "get_match_detail", lambda mc: {"main": {}})
    assert api_client.get_demo_url("g161-x") is None

def test_get_demo_url_error_returns_none(monkeypatch):
    def boom(mc):
        raise RuntimeError("network")
    monkeypatch.setattr(api_client, "get_match_detail", boom)
    assert api_client.get_demo_url("g161-x") is None
