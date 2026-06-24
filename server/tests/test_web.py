import os, sys, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import web_server

def test_status_shape():
    c = web_server.app.test_client()
    r = c.get("/api/status")
    assert r.status_code == 200
    assert "status" in r.get_json()

def test_analyze_requires_map():
    c = web_server.app.test_client()
    r = c.post("/api/analyze", json={"usernames":["x"],"key":"csai_2026"})
    assert r.status_code == 400          # missing map
    body = r.get_json()
    assert "map" in body["error"].lower()

def test_analyze_bad_key():
    c = web_server.app.test_client()
    r = c.post("/api/analyze", json={"usernames":["x"],"map":"de_mirage","key":"wrong"})
    assert r.status_code == 403
