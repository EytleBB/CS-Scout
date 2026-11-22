"""The per-chunk disk budget check must not walk the demo directory each time.

A 150 MB download reports progress every 64 KB, so a full ``os.walk`` per chunk
serialises every concurrent download behind the budget lock.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pipeline  # noqa: E402


def _budget(monkeypatch, tmp_path, sizes=None):
    walks = []

    def fake_size(directory):
        walks.append(directory)
        if sizes:
            return sizes[min(len(walks) - 1, len(sizes) - 1)]
        return 0

    monkeypatch.setattr(pipeline, "_demo_storage_size", fake_size)
    monkeypatch.setattr(pipeline, "_disk_free_bytes", lambda directory: 10 ** 12)
    return pipeline._TaskDiskBudget(str(tmp_path)), walks


def test_progress_chunks_do_not_walk_the_directory_each_time(monkeypatch, tmp_path):
    budget, walks = _budget(monkeypatch, tmp_path)
    token, progress = budget.new_download()

    for downloaded in range(1024, 1024 * 9, 1024):
        progress(downloaded, 1024 * 16)

    # One measurement is enough for the whole burst of chunks.
    assert len(walks) == 1


def test_finishing_a_download_invalidates_the_cached_size(monkeypatch, tmp_path):
    budget, walks = _budget(monkeypatch, tmp_path, sizes=[0, 4096])
    token, progress = budget.new_download()
    progress(1024, 2048)
    assert len(walks) == 1

    budget.finish_download(token)
    # The archive has landed on disk, so the next check must re-measure.
    budget._check_storage_locked(1)

    assert len(walks) == 2


def test_workspace_release_invalidates_the_cached_size(monkeypatch, tmp_path):
    budget, walks = _budget(monkeypatch, tmp_path)
    budget.reserve_workspace(1024)
    budget._check_storage_locked(1)
    measured = len(walks)

    budget.release_workspace(1024)
    budget._check_storage_locked(1)

    assert len(walks) > measured


def test_cached_size_still_rejects_an_oversized_download(monkeypatch, tmp_path):
    # 900 GB already on disk against a much smaller cache limit.
    budget, _walks = _budget(monkeypatch, tmp_path, sizes=[900 * 1024 ** 3])
    token, progress = budget.new_download()

    try:
        progress(1024, 1024 * 1024)
    except pipeline.DemoDiskBudgetError:
        return
    raise AssertionError("cache limit was not enforced with a cached size")
