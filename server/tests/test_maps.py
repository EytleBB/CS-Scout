import os, json, pytest
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import maps, config

def test_game_to_pixel_mirage():
    t = {"pos_x": -3230.0, "pos_y": 1713.0, "scale": 5.0}
    px, py = maps.game_to_pixel(t, -3230.0, 1713.0)
    assert px == 0.0 and py == 0.0
    px2, py2 = maps.game_to_pixel(t, -3225.0, 1708.0)
    assert px2 == 1.0 and py2 == 1.0   # +5 game = +1 px; Y inverted

def test_load_map_reads_meta(tmp_path, monkeypatch):
    d = tmp_path / "maps" / "de_test"
    d.mkdir(parents=True)
    (d / "radar.png").write_bytes(b"\x89PNG")
    (d / "meta.json").write_text(json.dumps(
        {"transform": {"pos_x": -1.0, "pos_y": 2.0, "scale": 3.0}}))
    monkeypatch.setattr(config, "MAPS_DIR", str(tmp_path / "maps"))
    m = maps.load_map("de_test")
    assert m["transform"]["scale"] == 3.0
    assert m["radar_rel"] == "/maps/de_test/radar.png"

def test_load_map_missing_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "MAPS_DIR", str(tmp_path))
    with pytest.raises(FileNotFoundError):
        maps.load_map("de_nope")

def test_available_maps_requires_metadata_and_radar(monkeypatch, tmp_path):
    maps_root = tmp_path / "maps"
    complete = maps_root / "de_complete"
    meta_only = maps_root / "de_meta_only"
    radar_only = maps_root / "de_radar_only"
    for directory in (complete, meta_only, radar_only):
        directory.mkdir(parents=True)
    (complete / "meta.json").write_text("{}", encoding="utf-8")
    (complete / "radar.png").write_bytes(b"png")
    (meta_only / "meta.json").write_text("{}", encoding="utf-8")
    (radar_only / "radar.png").write_bytes(b"png")

    monkeypatch.setattr(config, "MAPS_DIR", str(maps_root))

    assert maps.available_maps() == ["de_complete"]
