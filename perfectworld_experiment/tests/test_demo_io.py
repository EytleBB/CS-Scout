import io
from pathlib import Path
import zipfile

import pytest

from perfectworld_experiment import demo_io


class FakeResponse:
    def __init__(self, body: bytes, status_code=200, headers=None):
        self.raw = io.BytesIO(body)
        self.status_code = status_code
        self.headers = headers or {}

    def close(self):
        pass

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("http error")


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)

    def get(self, *args, **kwargs):
        return self.responses.pop(0)


def _zip_bytes(entries):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, payload in entries:
            archive.writestr(name, payload)
    return output.getvalue()


def test_download_extracts_demo_into_isolated_directory(tmp_path):
    response = FakeResponse(_zip_bytes([("nested/demo.dem", b"PBDEMS2")]))
    paths = demo_io.download_and_extract(
        "match-1",
        "https://pwaweblogin.wmpvp.com/demo",
        tmp_path,
        headers={"X-PWA-Signature": "secret"},
        session=FakeSession([response]),
        require_public_dns=False,
    )
    assert len(paths) == 1
    assert Path(paths[0]).read_bytes() == b"PBDEMS2"
    assert not list(tmp_path.glob("*.download"))


def test_zip_traversal_is_rejected_and_partial_files_are_removed(tmp_path):
    response = FakeResponse(_zip_bytes([("../escape.dem", b"bad")]))
    with pytest.raises(demo_io.PerfectWorldDownloadError, match="越界"):
        demo_io.download_and_extract(
            "match-1",
            "https://pwaweblogin.wmpvp.com/demo",
            tmp_path,
            headers={},
            session=FakeSession([response]),
            require_public_dns=False,
        )
    assert not list(tmp_path.rglob("*.dem"))
    assert not list(tmp_path.glob("*.download"))


@pytest.mark.parametrize("url", [
    "http://pwaweblogin.wmpvp.com/demo",
    "https://user:pass@pwaweblogin.wmpvp.com/demo",
    "https://127.0.0.1/demo",
])
def test_unsafe_urls_are_rejected(monkeypatch, url):
    if "127.0.0.1" in url:
        monkeypatch.setattr(
            demo_io.socket,
            "getaddrinfo",
            lambda *args, **kwargs: [(None, None, None, None, ("127.0.0.1", 443))],
        )
    with pytest.raises(demo_io.UnsafePerfectWorldUrl):
        demo_io.normalize_download_url(url)


def test_trusted_pwa_hosts_accept_local_proxy_fake_ip(monkeypatch):
    monkeypatch.setattr(
        demo_io.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (None, None, None, None, ("198.18.1.38", 443)),
            (None, None, None, None, ("fdfe:dcba:9876::113", 443)),
        ],
    )
    assert demo_io.normalize_download_url(
        "https://pwaweblogin.wmpvp.com/csgo/demo/example.dem"
    ).startswith("https://pwaweblogin.wmpvp.com/")
    assert demo_io.normalize_download_url(
        "https://pvp-demo-hz.oss-cn-hangzhou.aliyuncs.com/example.dem"
    ).startswith("https://pvp-demo-hz.oss-cn-hangzhou.aliyuncs.com/")


def test_untrusted_host_still_rejects_proxy_fake_ip(monkeypatch):
    monkeypatch.setattr(
        demo_io.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(None, None, None, None, ("198.18.1.38", 443))],
    )
    with pytest.raises(demo_io.UnsafePerfectWorldUrl):
        demo_io.normalize_download_url("https://example.test/demo.dem")
