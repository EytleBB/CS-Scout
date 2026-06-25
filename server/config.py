"""
CSAI Server Configuration
"""

import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DEMO_DIR = os.path.join(BASE_DIR, "demos_opponents")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")

# Map data
MAPS_DIR = os.path.join(DATA_DIR, "maps")
# Grenade icon SVGs (repo-root radar/icons, one level above server/)
ICONS_DIR = os.path.join(BASE_DIR, "..", "radar", "icons")

# Analysis parameters
TICK_RATE = 64
WINDOW_S = 20          # per-round capture window (seconds from freeze_end)
SAMPLE_EVERY = 8       # downsample stride in ticks (~8Hz)
EQ_FULL_BUY = 3800     # (legacy) team-avg threshold; no longer used by classify_rounds
EQ_BUY_MIN = 2000      # per-player equip value floor: below this a non-pistol round is dropped

# Server
HOST = "0.0.0.0"
PORT = 5000
SECRET_KEY = "csai_2026"  # 简单验证，防止随意调用
