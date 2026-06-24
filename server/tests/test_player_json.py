import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import player_json, maps, config, json

def test_build_schema(tmp_path, monkeypatch):
    d = tmp_path / "maps" / "de_mirage"; d.mkdir(parents=True)
    (d / "radar.png").write_bytes(b"x")
    (d / "meta.json").write_text(json.dumps({"transform":{"pos_x":-3230.0,"pos_y":1713.0,"scale":5.0}}))
    monkeypatch.setattr(config, "MAPS_DIR", str(tmp_path / "maps"))
    rounds = [{"side":"CT","rtype":"Full","official_num":3,
               "path":[[0.0,-1.0,2.0]],
               "grenades":[{"type":"smoke","throw_t":1.0,"land_t":2.0,
                            "arc":[[1.0,0.0,0.0]],"land":[0.0,0.0],"expire_t":20.0}]}]
    out = player_json.build("Neo","0705abc","765","de_mirage",rounds,{"kd":1.2,"awp_rate":40.0})
    assert out["map"] == "de_mirage"
    assert out["transform"]["scale"] == 5.0
    assert out["radar"] == "/maps/de_mirage/radar.png"
    assert out["combat_stats"]["kd"] == 1.2
    assert out["round_count"] == 1
    r0 = out["rounds"][0]
    assert r0["side"]=="CT" and r0["rtype"]=="Full" and r0["round_id"]==3
    assert r0["grenades"][0]["type"]=="smoke"
