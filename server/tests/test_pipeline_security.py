import os
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pipeline


@pytest.fixture
def demo_root(tmp_path, monkeypatch):
    root = tmp_path / "demos"
    monkeypatch.setattr(pipeline.config, "DEMO_DIR", str(root))
    monkeypatch.setattr(pipeline, "_dem_index", {})
    monkeypatch.setattr(pipeline, "_dem_index_ready", True)
    monkeypatch.setattr(pipeline.api_client, "get_demo_url", lambda match_id: None)
    return root


def _install_zip_download(monkeypatch, members):
    def fake_download(url, save_path, progress_cb=None):
        with zipfile.ZipFile(save_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in members:
                archive.writestr(name, data)
        return save_path

    monkeypatch.setattr(pipeline.api_client, "download_demo", fake_download)


def test_download_rejects_unsafe_match_id_before_writing(
    demo_root, monkeypatch
):
    called = []
    monkeypatch.setattr(
        pipeline.api_client,
        "download_demo",
        lambda *args, **kwargs: called.append(args),
    )

    result = pipeline.download_and_extract(
        "../escape", "https://demo.example/unsafe.zip", str(demo_root / "player")
    )

    assert result == []
    assert called == []


def test_download_rejects_destination_outside_demo_root(
    demo_root, tmp_path, monkeypatch
):
    called = []
    monkeypatch.setattr(
        pipeline.api_client,
        "download_demo",
        lambda *args, **kwargs: called.append(args),
    )

    result = pipeline.download_and_extract(
        "g161-safe", "https://demo.example/unsafe.zip", str(tmp_path / "outside")
    )

    assert result == []
    assert called == []


@pytest.mark.parametrize(
    "member_name",
    ["../escaped.dem", "folder/../../escaped.dem", "/absolute.dem", "C:/drive.dem", "..\\escaped.dem"],
)
def test_zip_extraction_rejects_traversal_and_absolute_members(
    member_name, demo_root, tmp_path, monkeypatch
):
    _install_zip_download(monkeypatch, [(member_name, b"demo")])

    result = pipeline.download_and_extract(
        "g161-safe", "https://demo.example/unsafe.zip", str(demo_root / "player")
    )

    assert result == []
    assert not (tmp_path / "escaped.dem").exists()
    assert not (demo_root / "player" / "absolute.dem").exists()
    assert not (demo_root / "player" / "drive.dem").exists()


def test_zip_member_count_and_size_limits_are_enforced(
    demo_root, monkeypatch
):
    monkeypatch.setattr(pipeline, "ZIP_MAX_MEMBERS", 1)
    _install_zip_download(
        monkeypatch,
        [("one.dem", b"1"), ("two.dem", b"2")],
    )
    assert pipeline.download_and_extract(
        "g161-count", "https://demo.example/count.zip", str(demo_root / "player")
    ) == []

    monkeypatch.setattr(pipeline, "ZIP_MAX_MEMBERS", 64)
    monkeypatch.setattr(pipeline, "ZIP_MAX_UNCOMPRESSED_SIZE", 3)
    _install_zip_download(monkeypatch, [("large.dem", b"1234")])
    assert pipeline.download_and_extract(
        "g161-size", "https://demo.example/size.zip", str(demo_root / "player")
    ) == []


def test_valid_nested_demo_is_extracted_within_demo_root(
    demo_root, monkeypatch
):
    _install_zip_download(monkeypatch, [("nested/match.dem", b"valid-demo")])

    result = pipeline.download_and_extract(
        "g161-safe", "https://demo.example/safe.zip", str(demo_root / "player")
    )

    assert len(result) == 1
    extracted = os.path.realpath(result[0])
    assert os.path.commonpath([os.path.realpath(demo_root), extracted]) == os.path.realpath(demo_root)
    with open(extracted, "rb") as demo_file:
        assert demo_file.read() == b"valid-demo"


def test_zip_does_not_overwrite_an_existing_demo(
    demo_root, monkeypatch
):
    destination = demo_root / "player"
    destination.mkdir(parents=True)
    existing = destination / "match.dem"
    existing.write_bytes(b"keep-me")
    _install_zip_download(monkeypatch, [("match.dem", b"replace-me")])

    result = pipeline.download_and_extract(
        "g161-safe", "https://demo.example/safe.zip", str(destination)
    )

    assert result == []
    assert existing.read_bytes() == b"keep-me"


def test_completed_orphan_demo_is_reused_and_reindexed(
    demo_root, monkeypatch
):
    destination = demo_root / "player"
    destination.mkdir(parents=True)
    orphan = destination / "match.dem"
    orphan.write_bytes(b"complete-demo")
    _install_zip_download(monkeypatch, [("match.dem", b"complete-demo")])

    result = pipeline.download_and_extract(
        "g161-orphan", "https://demo.example/orphan.zip", str(destination)
    )

    assert result == [os.path.realpath(orphan)]
    assert pipeline._dem_index["g161-orphan"] == [os.path.realpath(orphan)]


def test_atomic_json_write_preserves_previous_file_on_serialization_error(
    tmp_path,
):
    target = tmp_path / "summary.json"
    target.write_text('{"status":"complete"}', encoding="utf-8")

    with pytest.raises(TypeError):
        pipeline._write_json_atomic(target, {"bad": object()})

    assert target.read_text(encoding="utf-8") == '{"status":"complete"}'
    assert list(tmp_path.glob(".summary.json.*.tmp")) == []
