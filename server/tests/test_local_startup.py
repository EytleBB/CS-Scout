import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config
import web_server


class FakeServer:
    def __init__(self, port=54321):
        self.server_port = port
        self.served = False
        self.closed = False

    def serve_forever(self):
        self.served = True

    def server_close(self):
        self.closed = True


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, 5000),
        ("", 5000),
        ("0", 0),
        (" 5001 ", 5001),
        ("65535", 65535),
        ("-1", 5000),
        ("65536", 5000),
        ("not-a-port", 5000),
    ],
)
def test_port_env_accepts_auto_and_valid_ports(monkeypatch, raw, expected):
    name = "CS_SCOUT_TEST_PORT"
    if raw is None:
        monkeypatch.delenv(name, raising=False)
    else:
        monkeypatch.setenv(name, raw)

    assert config._port_env(name, 5000) == expected


def test_make_server_assigns_a_real_port_when_zero_is_requested():
    server = web_server.make_server(
        "127.0.0.1", 0, web_server.app, threaded=True
    )
    try:
        assert 1 <= int(server.server_port) <= 65535
    finally:
        server.server_close()


def test_write_startup_info_atomically_replaces_old_content(tmp_path):
    target = tmp_path / "runtime" / "startup.json"
    target.parent.mkdir()
    target.write_text("old", encoding="ascii")

    web_server._write_startup_info(str(target), "launch-token", 54321)

    assert json.loads(target.read_text(encoding="ascii")) == {
        "pid": os.getpid(),
        "parent_pid": os.getppid(),
        "port": 54321,
        "token": "launch-token",
    }
    assert not list(target.parent.glob(".cs-scout-startup-*.tmp"))


@pytest.mark.parametrize("port", [0, -1, 65536])
def test_write_startup_info_rejects_invalid_actual_port(tmp_path, port):
    target = tmp_path / "startup.json"

    with pytest.raises(ValueError, match="Invalid bound server port"):
        web_server._write_startup_info(str(target), "launch-token", port)

    assert not target.exists()


def test_write_startup_info_cleans_temp_file_after_replace_failure(
    monkeypatch, tmp_path
):
    target = tmp_path / "startup.json"

    def fail_replace(source, destination):
        raise OSError("replace failed")

    monkeypatch.setattr(web_server.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        web_server._write_startup_info(str(target), "launch-token", 54321)

    assert not target.exists()
    assert not list(tmp_path.glob(".cs-scout-startup-*.tmp"))


def test_run_development_server_without_startup_info(
    monkeypatch, tmp_path
):
    fake_server = FakeServer()
    make_server_args = []

    def fake_make_server(host, port, application, threaded):
        make_server_args.append((host, port, application, threaded))
        return fake_server

    monkeypatch.setattr(web_server, "make_server", fake_make_server)
    monkeypatch.setattr(web_server.config, "HOST", "127.0.0.1")
    monkeypatch.setattr(web_server.config, "PORT", 5000)
    monkeypatch.setattr(web_server.config, "OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.delenv("CS_SCOUT_STARTUP_INFO", raising=False)
    monkeypatch.delenv("CS_SCOUT_STARTUP_TOKEN", raising=False)

    web_server._run_development_server()

    assert make_server_args == [
        ("127.0.0.1", 5000, web_server.app, True),
    ]
    assert fake_server.served is True
    assert fake_server.closed is True


def test_run_development_server_reports_actual_port(
    monkeypatch, tmp_path
):
    fake_server = FakeServer(port=61234)
    startup_path = tmp_path / "runtime" / "startup.json"

    monkeypatch.setattr(
        web_server, "make_server", lambda *args, **kwargs: fake_server
    )
    monkeypatch.setattr(web_server.config, "HOST", "127.0.0.1")
    monkeypatch.setattr(web_server.config, "PORT", 0)
    monkeypatch.setattr(web_server.config, "OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setenv("CS_SCOUT_STARTUP_INFO", str(startup_path))
    monkeypatch.setenv("CS_SCOUT_STARTUP_TOKEN", "unique-launch-token")

    web_server._run_development_server()

    assert json.loads(startup_path.read_text(encoding="ascii")) == {
        "pid": os.getpid(),
        "parent_pid": os.getppid(),
        "port": 61234,
        "token": "unique-launch-token",
    }
    assert fake_server.served is True
    assert fake_server.closed is True


def test_run_development_server_closes_bound_socket_when_publish_fails(
    monkeypatch, tmp_path
):
    fake_server = FakeServer(port=61234)
    startup_path = tmp_path / "runtime" / "startup.json"

    monkeypatch.setattr(
        web_server, "make_server", lambda *args, **kwargs: fake_server
    )
    monkeypatch.setattr(web_server.config, "OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setenv("CS_SCOUT_STARTUP_INFO", str(startup_path))
    monkeypatch.delenv("CS_SCOUT_STARTUP_TOKEN", raising=False)

    with pytest.raises(RuntimeError, match="CS_SCOUT_STARTUP_TOKEN"):
        web_server._run_development_server()

    assert fake_server.served is False
    assert fake_server.closed is True
