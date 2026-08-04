"""Invoke Perfect World Arena's installed signing export through our x86 bridge."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import threading


DEFAULT_PLUGIN_DIR = Path(r"C:\Program Files (x86)\perfectworldarena\plugin")
DEFAULT_DLL = DEFAULT_PLUGIN_DIR / "PvpAlive.dll"
SOURCE = Path(__file__).resolve().parent / "native" / "PwaSwapBridge.cs"
BIN_DIR = SOURCE.parent / "bin"
BRIDGE = BIN_DIR / "PwaSwapBridge.exe"
CSC = Path(os.environ.get(
    "CS_SCOUT_PWA_CSC",
    r"C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe",
))
_BUILD_LOCK = threading.Lock()


class PerfectWorldSigningError(RuntimeError):
    """The locally installed official signing component could not be used."""


def _dll_path() -> Path:
    return Path(os.environ.get("CS_SCOUT_PWA_DLL", str(DEFAULT_DLL))).resolve()


def ensure_bridge() -> Path:
    """Compile the committed bridge source when the binary is absent or stale."""
    with _BUILD_LOCK:
        if BRIDGE.is_file() and BRIDGE.stat().st_mtime_ns >= SOURCE.stat().st_mtime_ns:
            return BRIDGE
        if os.name != "nt":
            raise PerfectWorldSigningError("完美平台签名目前只能在 Windows 客户端旁运行")
        if not CSC.is_file():
            raise PerfectWorldSigningError("没有找到 Windows 32 位 C# 编译器")
        BIN_DIR.mkdir(parents=True, exist_ok=True)
        completed = subprocess.run(
            [
                str(CSC),
                "/nologo",
                "/target:exe",
                "/platform:x86",
                "/optimize+",
                f"/out:{BRIDGE}",
                str(SOURCE),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        if completed.returncode != 0 or not BRIDGE.is_file():
            raise PerfectWorldSigningError("本地签名桥编译失败")
        return BRIDGE


def generate_signature(
    randnum: str | int,
    timestamp: str | int,
    data: str,
    *,
    version: int = 1,
) -> str:
    """Generate the `s` parameter used by signed Perfect World web requests."""
    dll = _dll_path()
    if not dll.is_file():
        raise PerfectWorldSigningError("没有找到已安装客户端的 PvpAlive.dll")
    payload = json.dumps(
        {
            "randnum": str(randnum),
            "ts": str(timestamp),
            "data": str(data),
            "version": int(version),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    try:
        completed = subprocess.run(
            [str(ensure_bridge()), str(dll)],
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
            cwd=str(dll.parent),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PerfectWorldSigningError("调用完美平台签名组件失败") from exc
    signature = completed.stdout.strip()
    if completed.returncode != 0 or not signature:
        raise PerfectWorldSigningError("完美平台签名组件没有返回有效签名")
    if any(ord(char) < 33 or ord(char) > 126 for char in signature):
        raise PerfectWorldSigningError("完美平台签名格式异常")
    return signature


def query_current_ingame_parameters() -> int | None:
    """Best-effort query of the client's exported current-game parameter value."""
    dll = _dll_path()
    if not dll.is_file():
        return None
    try:
        completed = subprocess.run(
            [str(ensure_bridge()), "--current", str(dll)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="ascii",
            errors="replace",
            timeout=5,
            check=False,
            cwd=str(dll.parent),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = completed.stdout.strip()
    if completed.returncode != 0 or not value.isdigit():
        return None
    return int(value)
