"""One-time: pull radar images + transforms from awpy into data/maps/<map>/.

Run once during deployment:  python setup_maps.py
Requires `awpy` installed and `awpy get maps` already run (downloads to ~/.awpy).
"""
import os, json, shutil
import config

ACTIVE_DUTY = ["de_mirage", "de_inferno", "de_nuke", "de_overpass",
               "de_ancient", "de_anubis", "de_dust2", "de_train"]

def main():
    from awpy.data import MAPS_DIR as AWPY_MAPS_DIR
    from awpy.data.map_data import MAP_DATA
    os.makedirs(config.MAPS_DIR, exist_ok=True)
    done = []
    for m in ACTIVE_DUTY:
        md = MAP_DATA.get(m)
        src_png = os.path.join(str(AWPY_MAPS_DIR), f"{m}.png")
        if not md or not os.path.exists(src_png):
            print(f"skip {m}: data missing (md={bool(md)} png={os.path.exists(src_png)})")
            continue
        out = os.path.join(config.MAPS_DIR, m)
        os.makedirs(out, exist_ok=True)
        shutil.copyfile(src_png, os.path.join(out, "radar.png"))
        with open(os.path.join(out, "meta.json"), "w", encoding="utf-8") as f:
            json.dump({"transform": {"pos_x": float(md["pos_x"]),
                                     "pos_y": float(md["pos_y"]),
                                     "scale": float(md["scale"])}}, f, indent=2)
        done.append(m)
    print(f"Prepared {len(done)} maps: {done}")

if __name__ == "__main__":
    main()
