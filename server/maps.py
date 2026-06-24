"""Runtime map loader — reads data/maps/<name>/{radar.png, meta.json}."""
import os, json
import config

def game_to_pixel(transform, gx, gy):
    return ((gx - transform["pos_x"]) / transform["scale"],
            (transform["pos_y"] - gy) / transform["scale"])

def load_map(name):
    d = os.path.join(config.MAPS_DIR, name)
    meta_path = os.path.join(d, "meta.json")
    radar_path = os.path.join(d, "radar.png")
    if not (os.path.exists(meta_path) and os.path.exists(radar_path)):
        raise FileNotFoundError(f"Map assets missing for {name}: run setup_maps.py")
    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)
    return {"name": name, "transform": meta["transform"],
            "radar_rel": f"/maps/{name}/radar.png"}

def available_maps():
    if not os.path.isdir(config.MAPS_DIR):
        return []
    return sorted(n for n in os.listdir(config.MAPS_DIR)
                  if os.path.exists(os.path.join(config.MAPS_DIR, n, "meta.json")))
