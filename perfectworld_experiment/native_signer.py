"""Locate and invoke Perfect World Arena's official local signing component."""

from __future__ import annotations

import json
import os
from pathlib import Path
import struct
import subprocess
import threading
import time


DEFAULT_INSTALL_DIRS = (
    Path(r"C:\Program Files (x86)\perfectworldarena"),
    Path(r"C:\Program Files\perfectworldarena"),
)
SOURCE = Path(__file__).resolve().parent / "native" / "PwaSwapBridge.cs"
BIN_DIR = SOURCE.parent / "bin"
BRIDGE = BIN_DIR / "PwaSwapBridge.exe"
CSC = Path(os.environ.get(
    "CS_SCOUT_PWA_CSC",
    r"C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe",
))
OFFICIAL_PUBLISHER_MARKERS = ("完美世界", "perfect world", "perfectworld")
X86_PE_MACHINE = 0x014C
_BUILD_LOCK = threading.Lock()
_STATUS_LOCK = threading.Lock()
_STATUS_CACHE: dict[str, object] | None = None
_STATUS_CACHE_AT = 0.0
_STATUS_CACHE_FINGERPRINT: tuple[int, int] | None = None


class PerfectWorldSigningError(RuntimeError):
    """The locally installed official signing component could not be used."""

    def __init__(self, message: str, *, code: str = "signer_error"):
        super().__init__(message)
        self.code = code


def _status(
    ready: bool,
    code: str,
    message: str,
    *,
    path: Path | None = None,
    source: str | None = None,
    detail: str | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "ready": ready,
        "code": code,
        "message": message,
        "path": str(path) if path else None,
        "source": source,
    }
    if detail:
        result["detail"] = detail
    return result


def _config_path() -> Path:
    explicit = os.environ.get("CS_SCOUT_PWA_CONFIG", "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        return Path(local_app_data) / "CS-Scout" / "pwa-install.json"
    return Path.home() / "AppData" / "Local" / "CS-Scout" / "pwa-install.json"


def _candidate_dll(value: str | os.PathLike[str]) -> Path:
    path = Path(value).expanduser()
    if path.name.casefold() == "pvpalive.dll":
        return path.resolve()
    if path.name.casefold() == "plugin":
        return (path / "PvpAlive.dll").resolve()
    return (path / "plugin" / "PvpAlive.dll").resolve()


def _load_saved_install_dir() -> Path | None:
    try:
        payload = json.loads(_config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    value = payload.get("install_dir") if isinstance(payload, dict) else None
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return Path(value).expanduser().resolve()
    except OSError:
        return None


def _save_install_dir(path: Path) -> None:
    config_path = _config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = config_path.with_name(
        f".{config_path.name}-{os.getpid()}-{threading.get_ident()}.tmp"
    )
    try:
        temporary.write_text(
            json.dumps({"install_dir": str(path)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, config_path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _powershell_path() -> str:
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    candidate = Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    return str(candidate) if candidate.is_file() else "powershell.exe"


def _hidden_creation_flags() -> int:
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _running_process_paths() -> list[Path]:
    """Return accessible executable paths without depending on process names."""
    if os.name != "nt":
        return []
    script = (
        "$OutputEncoding=[Text.UTF8Encoding]::new($false);"
        "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);"
        "$items=@(Get-Process -ErrorAction SilentlyContinue | ForEach-Object {"
        "try { if ($_.Path) { $_.Path } } catch {} });"
        "$items | ConvertTo-Json -Compress"
    )
    try:
        completed = subprocess.run(
            [_powershell_path(), "-NoProfile", "-NonInteractive", "-Command", script],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
            check=False,
            creationflags=_hidden_creation_flags(),
        )
        payload = json.loads(completed.stdout or "[]") if completed.returncode == 0 else []
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        return []
    if isinstance(payload, str):
        payload = [payload]
    if not isinstance(payload, list):
        return []
    paths = []
    for value in payload:
        if not isinstance(value, str) or not value:
            continue
        try:
            paths.append(Path(value).resolve())
        except OSError:
            continue
    return paths


def _read_pe_machine(path: Path) -> int:
    try:
        with path.open("rb") as stream:
            dos_header = stream.read(64)
            if len(dos_header) < 64 or dos_header[:2] != b"MZ":
                raise ValueError("missing DOS header")
            pe_offset = struct.unpack_from("<I", dos_header, 0x3C)[0]
            if pe_offset < 64 or pe_offset > 64 * 1024 * 1024:
                raise ValueError("invalid PE header offset")
            stream.seek(pe_offset)
            pe_header = stream.read(6)
    except OSError as exc:
        raise ValueError("cannot read PE file") from exc
    if len(pe_header) != 6 or pe_header[:4] != b"PE\x00\x00":
        raise ValueError("missing PE signature")
    return struct.unpack_from("<H", pe_header, 4)[0]


def _authenticode_info(path: Path) -> tuple[str, str]:
    if os.name != "nt":
        raise OSError("Authenticode is only available on Windows")
    script = (
        "$OutputEncoding=[Text.UTF8Encoding]::new($false);"
        "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);"
        "$target=[Environment]::GetEnvironmentVariable('CS_SCOUT_PWA_VERIFY_TARGET','Process');"
        "$signature=Get-AuthenticodeSignature -LiteralPath $target;"
        "[pscustomobject]@{Status=[string]$signature.Status;"
        "Subject=[string]$signature.SignerCertificate.Subject} | ConvertTo-Json -Compress"
    )
    environment = os.environ.copy()
    environment["CS_SCOUT_PWA_VERIFY_TARGET"] = str(path)
    completed = subprocess.run(
        [_powershell_path(), "-NoProfile", "-NonInteractive", "-Command", script],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        check=False,
        env=environment,
        creationflags=_hidden_creation_flags(),
    )
    if completed.returncode != 0:
        raise OSError("Get-AuthenticodeSignature failed")
    payload = json.loads(completed.stdout)
    if not isinstance(payload, dict):
        raise OSError("invalid Authenticode response")
    return str(payload.get("Status") or ""), str(payload.get("Subject") or "")


def ensure_bridge() -> Path:
    """Compile the committed bridge source when the binary is absent or stale."""
    with _BUILD_LOCK:
        if BRIDGE.is_file() and BRIDGE.stat().st_mtime_ns >= SOURCE.stat().st_mtime_ns:
            return BRIDGE
        if os.name != "nt":
            raise PerfectWorldSigningError(
                "完美平台组件目前只能在 Windows 使用", code="unsupported_platform"
            )
        if not CSC.is_file():
            raise PerfectWorldSigningError(
                "缺少 Windows 32 位运行组件", code="bridge_unavailable"
            )
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
            creationflags=_hidden_creation_flags(),
        )
        if completed.returncode != 0 or not BRIDGE.is_file():
            raise PerfectWorldSigningError(
                "本地签名桥编译失败", code="bridge_unavailable"
            )
        return BRIDGE


def _probe_swap_data_export(path: Path) -> tuple[bool, str]:
    try:
        completed = subprocess.run(
            [str(ensure_bridge()), "--probe", str(path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
            cwd=str(path.parent),
            creationflags=_hidden_creation_flags(),
        )
    except (OSError, subprocess.SubprocessError, PerfectWorldSigningError) as exc:
        return False, str(exc)
    return completed.returncode == 0, completed.stderr.strip()


def _validate_dll(path: Path, source: str) -> dict[str, object]:
    if path.name.casefold() != "pvpalive.dll" or path.parent.name.casefold() != "plugin":
        return _status(
            False, "invalid_location", "请选择完美平台安装目录",
            path=path, source=source,
        )
    if not path.is_file():
        return _status(
            False, "not_found", "未找到完美平台组件",
            path=path, source=source,
        )
    try:
        machine = _read_pe_machine(path)
    except ValueError as exc:
        return _status(
            False, "incompatible_version", "完美平台组件版本不兼容",
            path=path, source=source, detail=str(exc),
        )
    if machine != X86_PE_MACHINE:
        return _status(
            False, "incompatible_version", "完美平台组件版本不兼容",
            path=path, source=source, detail=f"PE machine 0x{machine:04X}",
        )
    try:
        signature_status, subject = _authenticode_info(path)
    except (OSError, subprocess.SubprocessError, ValueError, TypeError) as exc:
        return _status(
            False, "signature_check_failed", "无法验证完美平台组件签名",
            path=path, source=source, detail=str(exc),
        )
    normalized_subject = subject.casefold()
    official_publisher = any(
        marker.casefold() in normalized_subject for marker in OFFICIAL_PUBLISHER_MARKERS
    )
    if signature_status.casefold() != "valid" or not official_publisher:
        return _status(
            False, "invalid_signature", "完美平台组件签名无效",
            path=path, source=source,
        )
    export_ok, export_error = _probe_swap_data_export(path)
    if not export_ok:
        return _status(
            False, "incompatible_version", "完美平台组件版本不兼容",
            path=path, source=source, detail=export_error or "missing swapData export",
        )
    return _status(
        True, "ready", "完美平台组件已就绪", path=path, source=source
    )


def _candidate_paths() -> list[tuple[Path, str]]:
    candidates: list[tuple[Path, str]] = []
    seen: set[str] = set()

    def add(path: Path, source: str) -> None:
        key = os.path.normcase(str(path))
        if key in seen:
            return
        seen.add(key)
        candidates.append((path, source))

    for executable in _running_process_paths():
        candidate = _candidate_dll(executable.parent)
        if candidate.is_file():
            add(candidate, "running")
    saved = _load_saved_install_dir()
    if saved is not None:
        add(_candidate_dll(saved), "saved")
    default_install_dirs = list(DEFAULT_INSTALL_DIRS)
    for variable in ("ProgramFiles(x86)", "ProgramFiles"):
        program_files = os.environ.get(variable, "").strip()
        if program_files:
            default_install_dirs.append(Path(program_files) / "perfectworldarena")
    for install_dir in default_install_dirs:
        add(_candidate_dll(install_dir), "default")
    return candidates


def _fingerprint(path_value: object) -> tuple[int, int] | None:
    if not isinstance(path_value, str) or not path_value:
        return None
    try:
        stat = Path(path_value).stat()
    except OSError:
        return None
    return stat.st_size, stat.st_mtime_ns


def invalidate_status_cache() -> None:
    global _STATUS_CACHE, _STATUS_CACHE_AT, _STATUS_CACHE_FINGERPRINT
    with _STATUS_LOCK:
        _STATUS_CACHE = None
        _STATUS_CACHE_AT = 0.0
        _STATUS_CACHE_FINGERPRINT = None


def get_dll_status(*, force: bool = False) -> dict[str, object]:
    """Locate and validate the current official signing DLL."""
    global _STATUS_CACHE, _STATUS_CACHE_AT, _STATUS_CACHE_FINGERPRINT
    now = time.monotonic()
    with _STATUS_LOCK:
        if _STATUS_CACHE is not None and not force:
            ttl = 30.0 if _STATUS_CACHE.get("ready") else 3.0
            current_fingerprint = _fingerprint(_STATUS_CACHE.get("path"))
            if (
                now - _STATUS_CACHE_AT < ttl
                and current_fingerprint == _STATUS_CACHE_FINGERPRINT
            ):
                return dict(_STATUS_CACHE)

        override = os.environ.get("CS_SCOUT_PWA_DLL", "").strip()
        if override:
            result = _validate_dll(_candidate_dll(override), "environment")
        elif os.name != "nt":
            result = _status(
                False, "unsupported_platform",
                "完美平台组件目前只能在 Windows 使用",
            )
        else:
            first_error = None
            result = None
            for candidate, source in _candidate_paths():
                checked = _validate_dll(candidate, source)
                if checked["ready"]:
                    result = checked
                    break
                if candidate.is_file() and first_error is None:
                    first_error = checked
            if result is None:
                result = first_error or _status(
                    False, "not_found", "未找到完美平台组件"
                )

        _STATUS_CACHE = dict(result)
        _STATUS_CACHE_AT = now
        _STATUS_CACHE_FINGERPRINT = _fingerprint(result.get("path"))
        return dict(result)


def _show_directory_picker() -> Path | None:
    if os.name != "nt":
        return None
    script = (
        "$OutputEncoding=[Text.UTF8Encoding]::new($false);"
        "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);"
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$owner=New-Object System.Windows.Forms.Form;"
        "$owner.TopMost=$true;$owner.ShowInTaskbar=$false;$owner.Opacity=0;"
        "$dialog=New-Object System.Windows.Forms.FolderBrowserDialog;"
        "$dialog.Description='选择完美平台安装目录';"
        "$dialog.ShowNewFolderButton=$false;"
        "$result=$dialog.ShowDialog($owner);$owner.Dispose();"
        "if($result -eq [System.Windows.Forms.DialogResult]::OK){"
        "[Console]::Out.Write($dialog.SelectedPath)}"
    )
    try:
        completed = subprocess.run(
            [_powershell_path(), "-NoProfile", "-STA", "-Command", script],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
            check=False,
            creationflags=_hidden_creation_flags(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = completed.stdout.strip()
    return Path(value).resolve() if completed.returncode == 0 and value else None


def configure_install_directory(selected: str | os.PathLike[str]) -> dict[str, object]:
    """Validate and persist a user-selected install or plugin directory."""
    global _STATUS_CACHE, _STATUS_CACHE_AT, _STATUS_CACHE_FINGERPRINT
    candidate = _candidate_dll(selected)
    result = _validate_dll(candidate, "selected")
    if result["ready"]:
        _save_install_dir(candidate.parent.parent)
        with _STATUS_LOCK:
            _STATUS_CACHE = dict(result)
            _STATUS_CACHE_AT = time.monotonic()
            _STATUS_CACHE_FINGERPRINT = _fingerprint(result.get("path"))
    else:
        invalidate_status_cache()
    return result


def choose_install_directory() -> dict[str, object]:
    selected = _show_directory_picker()
    if selected is None:
        return {"selected": False, "cancelled": True, "signer": get_dll_status()}
    result = configure_install_directory(selected)
    return {"selected": bool(result["ready"]), "cancelled": False, "signer": result}


def _dll_path() -> Path:
    result = get_dll_status()
    if not result["ready"] or not isinstance(result.get("path"), str):
        raise PerfectWorldSigningError(
            str(result.get("message") or "完美平台组件不可用"),
            code=str(result.get("code") or "signer_error"),
        )
    return Path(str(result["path"]))


def generate_signature(
    randnum: str | int,
    timestamp: str | int,
    data: str,
    *,
    version: int = 1,
) -> str:
    """Generate the `s` parameter used by signed Perfect World web requests."""
    dll = _dll_path()
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
            creationflags=_hidden_creation_flags(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PerfectWorldSigningError(
            "调用完美平台签名组件失败", code="signing_failed"
        ) from exc
    signature = completed.stdout.strip()
    if completed.returncode != 0 or not signature:
        raise PerfectWorldSigningError(
            "完美平台组件版本不兼容",
            code="incompatible_version",
        )
    if any(ord(char) < 33 or ord(char) > 126 for char in signature):
        raise PerfectWorldSigningError(
            "完美平台签名格式异常", code="signing_failed"
        )
    return signature


def query_current_ingame_parameters() -> int | None:
    """Best-effort query of the client's exported current-game parameter value."""
    try:
        dll = _dll_path()
    except PerfectWorldSigningError:
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
            creationflags=_hidden_creation_flags(),
        )
    except (OSError, subprocess.SubprocessError, PerfectWorldSigningError):
        return None
    value = completed.stdout.strip()
    if completed.returncode != 0 or not value.isdigit():
        return None
    return int(value)
