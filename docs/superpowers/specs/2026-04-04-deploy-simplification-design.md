# Deploy Simplification Design

**Date:** 2026-04-04  
**Scope:** `server/web_server.py` only

## Goal

Someone with Python installed can download the project folder and run:

```
python web_server.py
```

No manual setup steps required.

## Changes

Add a self-setup block at the top of `web_server.py` (before any other imports) that runs once per environment:

### 1. Auto-install dependencies

Check if dependencies are installed by attempting `import flask`. If it fails, call:

```python
subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", requirements_path])
```

Where `requirements_path` is resolved relative to `web_server.py`'s own location. After install, re-exec the script so imports succeed cleanly.

### 2. Auto-create directories

Create `output/` and `demos_opponents/` relative to `web_server.py` if they don't exist. Uses `os.makedirs(..., exist_ok=True)`.

### 3. Startup message

Print a clear startup line after Flask launches:

```
[CS-Scout] 服务已启动: http://0.0.0.0:5000
```

## Out of Scope

- Config file changes
- Docker / systemd
- VPS deployment scripts
