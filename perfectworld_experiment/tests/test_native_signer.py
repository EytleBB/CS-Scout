import json
from pathlib import Path
import struct

from perfectworld_experiment import native_signer


def _write_pe(path: Path, machine: int = native_signer.X86_PE_MACHINE) -> None:
    payload = bytearray(0x86)
    payload[:2] = b"MZ"
    struct.pack_into("<I", payload, 0x3C, 0x80)
    payload[0x80:0x84] = b"PE\x00\x00"
    struct.pack_into("<H", payload, 0x84, machine)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def test_candidate_dll_accepts_install_plugin_or_exact_dll(tmp_path):
    root = tmp_path / "perfectworldarena"
    expected = (root / "plugin" / "PvpAlive.dll").resolve()

    assert native_signer._candidate_dll(root) == expected
    assert native_signer._candidate_dll(root / "plugin") == expected
    assert native_signer._candidate_dll(expected) == expected


def test_read_pe_machine_rejects_malformed_file(tmp_path):
    malformed = tmp_path / "PvpAlive.dll"
    malformed.write_bytes(b"not a PE")

    try:
        native_signer._read_pe_machine(malformed)
    except ValueError as exc:
        assert "DOS" in str(exc)
    else:
        raise AssertionError("malformed DLL was accepted")


def test_validate_dll_accepts_only_x86_official_signed_export(monkeypatch, tmp_path):
    dll = tmp_path / "client" / "plugin" / "PvpAlive.dll"
    _write_pe(dll)
    monkeypatch.setattr(
        native_signer,
        "_authenticode_info",
        lambda _path: ("Valid", "CN=完美世界征奇（上海）多媒体科技有限公司"),
    )
    monkeypatch.setattr(native_signer, "_probe_swap_data_export", lambda _path: (True, ""))

    result = native_signer._validate_dll(dll, "selected")

    assert result == {
        "ready": True,
        "code": "ready",
        "message": "完美平台组件已就绪",
        "path": str(dll),
        "source": "selected",
    }


def test_validate_dll_distinguishes_bad_signature_architecture_and_export(
    monkeypatch, tmp_path
):
    dll = tmp_path / "client" / "plugin" / "PvpAlive.dll"
    _write_pe(dll, machine=0x8664)
    wrong_arch = native_signer._validate_dll(dll, "selected")
    assert wrong_arch["code"] == "incompatible_version"

    _write_pe(dll)
    monkeypatch.setattr(native_signer, "_authenticode_info", lambda _path: ("NotSigned", ""))
    bad_signature = native_signer._validate_dll(dll, "selected")
    assert bad_signature["code"] == "invalid_signature"

    monkeypatch.setattr(
        native_signer,
        "_authenticode_info",
        lambda _path: ("Valid", "CN=Perfect World Co., Ltd."),
    )
    monkeypatch.setattr(
        native_signer,
        "_probe_swap_data_export",
        lambda _path: (False, "missing swapData"),
    )
    missing_export = native_signer._validate_dll(dll, "selected")
    assert missing_export["code"] == "incompatible_version"
    assert missing_export["detail"] == "missing swapData"


def test_environment_override_is_authoritative(monkeypatch, tmp_path):
    dll = tmp_path / "client" / "plugin" / "PvpAlive.dll"
    captured = []
    monkeypatch.setenv("CS_SCOUT_PWA_DLL", str(dll))
    monkeypatch.setattr(
        native_signer,
        "_validate_dll",
        lambda path, source: captured.append((path, source)) or native_signer._status(
            False, "not_found", "未找到完美平台组件", path=path, source=source
        ),
    )
    native_signer.invalidate_status_cache()

    result = native_signer.get_dll_status(force=True)

    assert result["code"] == "not_found"
    assert captured == [(dll.resolve(), "environment")]


def test_configure_install_directory_persists_only_valid_selection(
    monkeypatch, tmp_path
):
    config_path = tmp_path / "state" / "pwa-install.json"
    root = tmp_path / "portable-client"
    dll = (root / "plugin" / "PvpAlive.dll").resolve()
    monkeypatch.setenv("CS_SCOUT_PWA_CONFIG", str(config_path))
    monkeypatch.delenv("CS_SCOUT_PWA_DLL", raising=False)
    monkeypatch.setattr(
        native_signer,
        "_validate_dll",
        lambda path, source: native_signer._status(
            True, "ready", "完美平台组件已就绪", path=path, source=source
        ),
    )

    result = native_signer.configure_install_directory(root)

    assert result["ready"] is True
    assert json.loads(config_path.read_text(encoding="utf-8")) == {
        "install_dir": str(root.resolve())
    }
    assert native_signer.get_dll_status()["path"] == str(dll)


def test_choose_install_directory_can_be_cancelled(monkeypatch):
    monkeypatch.setattr(native_signer, "_show_directory_picker", lambda: None)
    monkeypatch.setattr(
        native_signer,
        "get_dll_status",
        lambda **_kwargs: native_signer._status(
            False, "not_found", "未找到完美平台组件"
        ),
    )

    result = native_signer.choose_install_directory()

    assert result["selected"] is False
    assert result["cancelled"] is True
    assert result["signer"]["code"] == "not_found"
