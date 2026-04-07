# Deploy Simplification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `python web_server.py` work out of the box — auto-install deps, auto-create dirs, print startup address.

**Architecture:** A self-setup block inserted at the very top of `web_server.py` (before all other imports). Uses only stdlib (`sys`, `os`, `subprocess`) so it runs before any third-party package is available. After installing deps, re-execs the script so all imports succeed cleanly.

**Tech Stack:** Python stdlib only (`sys`, `os`, `subprocess`)

---

### Task 1: Add self-setup block to web_server.py

**Files:**
- Modify: `server/web_server.py:1-12` (insert block before existing imports)

- [ ] **Step 1: Insert the self-setup block**

Open `server/web_server.py`. Replace the existing file header + imports section (lines 1–22, ending just before `from flask import ...`) with the following. The block must be the very first executable code:

```python
"""
CSAI Web Server — runs on VPS

Endpoints:
  POST /api/analyze      — start analysis with known steamids (Mode A)
  POST /api/analyze_auto — auto-detect opponents via 5E API (Mode B)
  GET  /api/status       — poll progress
  GET  /api/results      — get saved results
  GET  /output/<file>    — serve heatmap images
  GET  /                 — web UI
"""

# ── Self-setup (stdlib only, runs before any third-party import) ─────────────
import sys
import os
import subprocess

_HERE = os.path.dirname(os.path.abspath(__file__))

# 1. Auto-install dependencies on first run
try:
    import flask  # lightweight probe — if this works, everything is likely installed
except ImportError:
    print("[CS-Scout] 正在安装依赖，首次运行需要联网，请稍等...")
    req_file = os.path.join(_HERE, "requirements.txt")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", req_file])
    # Re-exec so all imports in this file resolve cleanly
    os.execv(sys.executable, [sys.executable] + sys.argv)

# 2. Auto-create required directories
os.makedirs(os.path.join(_HERE, "output"), exist_ok=True)
os.makedirs(os.path.join(_HERE, "demos_opponents"), exist_ok=True)
# ─────────────────────────────────────────────────────────────────────────────

import json
import threading
import logging

from flask import Flask, render_template, request, jsonify, send_from_directory

import pipeline
import api_client
import config
```

- [ ] **Step 2: Add startup print after app creation**

Find the line in `web_server.py` where Flask app is created:
```python
app = Flask(__name__, template_folder=os.path.join(config.BASE_DIR, "templates"))
```

Add a startup message in the `if __name__ == "__main__":` block at the bottom of the file. Find that block and ensure it looks like this (add the print line if missing):

```python
if __name__ == "__main__":
    print(f"[CS-Scout] 服务已启动: http://0.0.0.0:{config.PORT}")
    app.run(host=config.HOST, port=config.PORT, debug=False)
```

- [ ] **Step 3: Verify manually**

Run from the `server/` directory:
```
python web_server.py
```

Expected output on a clean environment (first run):
```
[CS-Scout] 正在安装依赖，首次运行需要联网，请稍等...
...pip output...
[CS-Scout] 服务已启动: http://0.0.0.0:5000
```

Expected output on subsequent runs:
```
[CS-Scout] 服务已启动: http://0.0.0.0:5000
 * Running on http://0.0.0.0:5000
```

- [ ] **Step 4: Commit**

```bash
git add server/web_server.py
git commit -m "feat: auto-install deps and create dirs on first run"
```
