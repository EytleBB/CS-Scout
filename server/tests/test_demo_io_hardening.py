import os
import socket
import sys
import threading

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import api_client
import config
import pipeline


PUBLIC_DNS = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]
PRIVATE_DNS = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]


class FakeResponse:
    def __init__(self, *, status=200, headers=None, chunks=()):
        self.status_code = status
        self.headers = headers or {}
        self._chunks = list(chunks)
        self.closed = False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size):
        yield from self._chunks

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.urls = []

    def get(self, url, **kwargs):
        assert kwargs["allow_redirects"] is False
        self.urls.append(url)
        return self.responses.pop(0)


def _install_public_dns(monkeypatch):
    monkeypatch.setattr(api_client.socket, "getaddrinfo", lambda *args, **kwargs: PUBLIC_DNS)


def test_relative_demo_urls_use_the_authoritative_https_base():
    assert api_client.normalize_demo_url(
        "20260107/match.zip", resolve=False
    ) == "https://gz-t-demo.5eplaycdn.com/pug/20260107/match.zip"
    assert api_client.normalize_demo_url(
        "/pug/20260107/match.zip", resolve=False
    ) == "https://gz-t-demo.5eplaycdn.com/pug/20260107/match.zip"


@pytest.mark.parametrize(
    "url",
    [
        "http://gz-t-demo.5eplaycdn.com/demo.zip",
        "https://example.com/demo.zip",
        "https://user:pass@gz-t-demo.5eplaycdn.com/demo.zip",
        "https://gz-t-demo.5eplaycdn.com:444/demo.zip",
        "file:///tmp/demo.zip",
    ],
)
def test_demo_url_rejects_unsafe_scheme_host_credentials_and_port(url):
    with pytest.raises(api_client.UnsafeDemoURLError):
        api_client.normalize_demo_url(url, resolve=False)


def test_allowlisted_cdn_with_private_acceleration_dns_is_accepted(
    monkeypatch
):
    monkeypatch.setattr(
        api_client.socket, "getaddrinfo", lambda *args, **kwargs: PRIVATE_DNS
    )
    monkeypatch.setattr(config, "DEMO_REQUIRE_PUBLIC_DNS", False)
    assert api_client.normalize_demo_url(
        "https://cd-t-demo.5eplaycdn.com/demo.zip"
    ) == "https://cd-t-demo.5eplaycdn.com/demo.zip"


def test_optional_public_dns_mode_rejects_private_answer(monkeypatch):
    monkeypatch.setattr(
        api_client.socket, "getaddrinfo", lambda *args, **kwargs: PRIVATE_DNS
    )
    monkeypatch.setattr(config, "DEMO_REQUIRE_PUBLIC_DNS", True)
    with pytest.raises(api_client.UnsafeDemoURLError, match="non-public"):
        api_client.normalize_demo_url(
            "https://gz-t-demo.5eplaycdn.com/demo.zip"
        )


def test_download_rejects_private_literal_redirect_before_following(
    monkeypatch, tmp_path
):
    redirect = FakeResponse(
        status=302,
        headers={"location": "https://127.0.0.1/private.zip"},
    )
    session = FakeSession([redirect])
    monkeypatch.setattr(api_client, "_session", lambda: session)

    with pytest.raises(api_client.UnsafeDemoURLError, match="allowed 5E CDN"):
        api_client.download_demo(
            "https://gz-t-demo.5eplaycdn.com/start.zip",
            tmp_path / "demo.zip",
        )

    assert len(session.urls) == 1
    assert redirect.closed
    assert not (tmp_path / "demo.zip").exists()


def test_download_follows_bounded_validated_relative_redirect(
    monkeypatch, tmp_path
):
    _install_public_dns(monkeypatch)
    redirect = FakeResponse(status=302, headers={"location": "next.zip"})
    final = FakeResponse(headers={}, chunks=[b"abc", b"def"])
    session = FakeSession([redirect, final])
    monkeypatch.setattr(api_client, "_session", lambda: session)

    target = tmp_path / "demo.zip"
    assert api_client.download_demo(
        "https://gz-t-demo.5eplaycdn.com/path/start.zip", target
    ) == os.path.realpath(target)

    assert target.read_bytes() == b"abcdef"
    assert session.urls == [
        "https://gz-t-demo.5eplaycdn.com/path/start.zip",
        "https://gz-t-demo.5eplaycdn.com/path/next.zip",
    ]
    assert redirect.closed and final.closed


def test_missing_content_length_is_capped_by_streamed_bytes(
    monkeypatch, tmp_path
):
    _install_public_dns(monkeypatch)
    monkeypatch.setattr(config, "DEMO_MAX_DOWNLOAD_MB", 0.00005)  # about 52 bytes
    response = FakeResponse(headers={}, chunks=[b"a" * 40, b"b" * 40])
    monkeypatch.setattr(
        api_client, "_session", lambda: FakeSession([response])
    )
    target = tmp_path / "demo.zip"
    target.write_bytes(b"previous-complete-file")

    with pytest.raises(api_client.DemoDownloadTooLarge):
        api_client.download_demo(
            "https://gz-t-demo.5eplaycdn.com/demo.zip", target
        )

    assert target.read_bytes() == b"previous-complete-file"
    assert list(tmp_path.glob("*.part")) == []
    assert response.closed


def test_lying_content_length_is_capped_and_partial_is_removed(
    monkeypatch, tmp_path
):
    _install_public_dns(monkeypatch)
    monkeypatch.setattr(config, "DEMO_MAX_DOWNLOAD_MB", 0.00005)
    response = FakeResponse(
        headers={"content-length": "1"}, chunks=[b"a" * 40, b"b" * 40]
    )
    monkeypatch.setattr(
        api_client, "_session", lambda: FakeSession([response])
    )
    target = tmp_path / "demo.zip"

    with pytest.raises(api_client.DemoDownloadTooLarge):
        api_client.download_demo(
            "https://gz-t-demo.5eplaycdn.com/demo.zip", target
        )

    assert not target.exists()
    assert list(tmp_path.glob("*.part")) == []


def test_truncated_content_length_is_rejected_and_partial_is_removed(
    monkeypatch, tmp_path
):
    _install_public_dns(monkeypatch)
    response = FakeResponse(
        headers={"content-length": "10"}, chunks=[b"short"]
    )
    monkeypatch.setattr(
        api_client, "_session", lambda: FakeSession([response])
    )
    target = tmp_path / "demo.zip"

    with pytest.raises(ValueError, match="Content-Length"):
        api_client.download_demo(
            "https://cd-t-demo.5eplaycdn.com/demo.zip", target
        )

    assert not target.exists()
    assert list(tmp_path.glob("*.part")) == []


def test_content_length_over_limit_rejects_before_streaming(
    monkeypatch, tmp_path
):
    _install_public_dns(monkeypatch)
    monkeypatch.setattr(config, "DEMO_MAX_DOWNLOAD_MB", 1)
    response = FakeResponse(
        headers={"content-length": str(2 * 1024 ** 2)}, chunks=[b"unused"]
    )
    monkeypatch.setattr(
        api_client, "_session", lambda: FakeSession([response])
    )

    with pytest.raises(api_client.DemoDownloadTooLarge):
        api_client.download_demo(
            "https://gz-t-demo.5eplaycdn.com/demo.zip",
            tmp_path / "demo.zip",
        )

    assert not (tmp_path / "demo.zip").exists()


def test_orphan_cleanup_is_narrow_and_preserves_completed_demo(tmp_path):
    completed = tmp_path / "match.dem"
    archive = tmp_path / "match.zip"
    partial = tmp_path / ".match.zip.abc.part"
    index_temp = tmp_path / ".demo_index.json.abc.tmp"
    unrelated = tmp_path / "keep.tmp"
    for path in (completed, archive, partial, index_temp, unrelated):
        path.write_bytes(b"data")

    pipeline.cleanup_orphan_demo_artifacts(str(tmp_path))

    assert completed.exists()
    assert unrelated.exists()
    assert not archive.exists()
    assert not partial.exists()
    assert not index_temp.exists()


def test_cleanup_counts_transients_and_honors_minimum_free_space(
    monkeypatch, tmp_path
):
    demo = tmp_path / "old.dem"
    archive = tmp_path / "orphan.zip"
    demo.write_bytes(b"1234")
    archive.write_bytes(b"5678")
    monkeypatch.setattr(pipeline, "_disk_free_bytes", lambda path: 0)

    freed = pipeline.cleanup_demos(
        str(tmp_path), limit_gb=100, target_gb=100, min_free_gb=1
    )

    assert freed == 4
    assert not demo.exists()
    # cleanup_demos counts but never unlinks a possibly-live transient; the
    # task-boundary orphan pass owns that operation.
    assert archive.exists()


def test_task_download_budget_reservations_are_concurrent_safe(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(config, "DEMO_TASK_DOWNLOAD_LIMIT_GB", 100 / 1024 ** 3)
    monkeypatch.setattr(config, "DEMO_CACHE_LIMIT_GB", 1)
    monkeypatch.setattr(config, "DEMO_MIN_FREE_GB", 0)
    monkeypatch.setattr(pipeline, "_demo_storage_size", lambda path: 0)
    monkeypatch.setattr(pipeline, "_disk_free_bytes", lambda path: 1024 ** 3)
    budget = pipeline._TaskDiskBudget(str(tmp_path))
    callbacks = [budget.new_download()[1] for _ in range(2)]
    barrier = threading.Barrier(2)
    outcomes = []
    outcome_lock = threading.Lock()

    def reserve(callback):
        barrier.wait()
        try:
            callback(0, 60)
            outcome = "ok"
        except pipeline.DemoDiskBudgetError:
            outcome = "rejected"
        with outcome_lock:
            outcomes.append(outcome)

    threads = [threading.Thread(target=reserve, args=(callback,)) for callback in callbacks]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert sorted(outcomes) == ["ok", "rejected"]


def test_external_runtime_directory_envs_are_absolute(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CS_SCOUT_DEMO_DIR", "runtime/demos")
    assert config._absolute_path_env(
        "CS_SCOUT_DEMO_DIR", "unused"
    ) == os.path.abspath(tmp_path / "runtime" / "demos")
