"""A cancelled analysis must stop streaming a demo immediately."""

import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import api_client
import pipeline

CHUNK = 65536


def _response(chunks):
    class FakeResponse:
        def iter_content(self, chunk_size):
            for chunk in chunks:
                yield chunk

    return FakeResponse()


def test_streaming_stops_as_soon_as_cancellation_is_requested(tmp_path):
    cancel = threading.Event()
    delivered = []

    def chunks():
        for index in range(5):
            if index == 2:
                cancel.set()
            delivered.append(index)
            yield b"a" * CHUNK

    output = tmp_path / "demo.part"
    with output.open("wb") as handle:
        with pytest.raises(api_client.DemoDownloadCancelled):
            api_client._stream_chunks(
                _response(chunks()),
                handle,
                1000000000,
                None,
                cancel,
            )

    assert output.stat().st_size == 2 * CHUNK
    assert delivered == [0, 1, 2]


def test_streaming_without_cancellation_writes_everything(tmp_path):
    output = tmp_path / "demo.part"
    seen = []
    with output.open("wb") as handle:
        downloaded = api_client._stream_chunks(
            _response([b"a" * CHUNK, b"bbbbbbbbbb"]),
            handle,
            1000000000,
            lambda done, total: seen.append(done),
            None,
        )

    assert downloaded == CHUNK + 10
    assert output.stat().st_size == CHUNK + 10
    assert seen == [CHUNK, CHUNK + 10]


def test_streaming_still_rejects_an_oversized_body(tmp_path):
    output = tmp_path / "demo.part"
    with output.open("wb") as handle:
        with pytest.raises(api_client.DemoDownloadTooLarge):
            api_client._stream_chunks(
                _response([b"a" * CHUNK, b"b" * CHUNK]),
                handle,
                CHUNK + 1,
                None,
                None,
            )


def test_cancelled_run_terminates_parse_workers_instead_of_waiting():
    cancel = threading.Event()
    cancel.set()

    mode = pipeline._parse_pool_shutdown_mode(cancel)

    assert mode == {"wait": False, "cancel_futures": True, "terminate": True}


def test_finished_run_closes_the_parse_pool_normally():
    mode = pipeline._parse_pool_shutdown_mode(threading.Event())

    assert mode == {"wait": True, "cancel_futures": False, "terminate": False}


def test_parse_workers_are_terminated_without_waiting():
    terminated = []

    class FakeProcess:
        def terminate(self):
            terminated.append(True)

    class FakePool:
        _processes = {1: FakeProcess(), 2: FakeProcess()}

    assert pipeline._terminate_parse_workers(FakePool()) == 2
    assert terminated == [True, True]


def test_worker_termination_failures_do_not_escape():
    class BrokenProcess:
        def terminate(self):
            raise OSError("already gone")

    class FakePool:
        _processes = {1: BrokenProcess()}

    assert pipeline._terminate_parse_workers(FakePool()) == 0
