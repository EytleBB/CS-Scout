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

# Analysis parameters
TICK_RATE = 64
WINDOW_S = 45          # per-round capture window (seconds from freeze_end)
SAMPLE_EVERY = 8       # downsample stride in ticks (~8Hz)
EQ_FULL_BUY = 3800     # team-avg equip value threshold for Full vs Eco

# Server
HOST = "0.0.0.0"
PORT = 5000
SECRET_KEY = "csai_2026"  # 简单验证，防止随意调用
