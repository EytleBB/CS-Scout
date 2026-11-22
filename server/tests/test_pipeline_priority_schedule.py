"""Fast-mode scheduling: discovery barrier + priority-ordered downloads.

Fast mode must not start downloading until every player's discovery has
resolved (success or failure), so ``states`` is complete when a finished
download is grouped across players that share the same match.  Download slots
are then handed out by ``player_priorities`` while keeping a per-player demo
order and a fair rotation that cannot starve low-priority players.
"""

import logging
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pipeline  # noqa: E402


def _records():
    return [{
        "official_num": 1,
        "side": "CT",
        "rtype": "Buy",
        "path": [],
        "grenades": [],
    }]


def _install_fast_fakes(tmp_path, monkeypatch, demos_per_player=2):
    monkeypatch.setattr(pipeline, "cleanup_demos", lambda *args, **kwargs: None)
    monkeypatch.setattr(pipeline.config, "DEMO_DIR", str(tmp_path / "demos"))
    monkeypatch.setattr(pipeline.config, "OUTPUT_DIR", str(tmp_path / "output"))
    # Real fast mode parses in a spawn process pool; these tests exercise the
    # in-process scheduler, so a thread pool keeps monkeypatched stubs visible.
    monkeypatch.setattr(
        pipeline,
        "_new_fast_parse_executor",
        lambda workers, cancel_event=None: ThreadPoolExecutor(max_workers=workers),
    )
    monkeypatch.setattr(
        pipeline.api_client,
        "search_player",
        lambda username: (f"domain-{username.lower()}", username),
    )
    monkeypatch.setattr(
        pipeline.api_client,
        "get_demos_by_domain",
        lambda domain, map_name, count: [
            {
                "match_code": f"{domain}-match-{index}",
                "demo_url": f"https://demo/{domain}/{index}.zip",
            }
            for index in range(min(count, demos_per_player))
        ],
    )
    monkeypatch.setattr(
        pipeline.api_client,
        "get_steamid_for_player",
        lambda match_code, username, domain=None: f"steam-{domain}",
    )


def _install_parse_stubs(monkeypatch):
    monkeypatch.setattr(
        pipeline.parse,
        "parse_demo_with_context",
        lambda path, steamid: (_records(), object(), {}, []),
    )
    monkeypatch.setattr(
        pipeline.combat,
        "parse_combat_stats_from_context",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        pipeline.player_json,
        "build",
        lambda username, domain, steamid, map_name, rounds, combat_stats: {
            "round_count": len(rounds),
            "combat_stats": combat_stats,
        },
    )


def _recording_download(tmp_path, calls, lock):
    def fake_download(
        match_code, demo_url, dest_dir, progress_cb=None, cancel_event=None
    ):
        with lock:
            calls.append(match_code)
        return [str(tmp_path / f"{match_code}.dem")]

    return fake_download


def test_download_start_order_follows_player_priorities(tmp_path, monkeypatch):
    _install_fast_fakes(tmp_path, monkeypatch, demos_per_player=2)
    _install_parse_stubs(monkeypatch)
    calls = []
    calls_lock = threading.Lock()
    monkeypatch.setattr(
        pipeline, "download_and_extract",
        _recording_download(tmp_path, calls, calls_lock),
    )

    results, failed = pipeline.run_fast(
        ["Alpha", "Bravo", "Charlie"], "de_mirage", max_demos=2,
        download_workers=1, parse_workers=1,
        player_priorities=[1, 100, 50],
    )

    assert failed == []
    assert [result["username"] for result in results] == [
        "Alpha", "Bravo", "Charlie"
    ]
    # Highest priority starts first; each player still downloads in demo_order
    # (never interleaving another player's demos out of order).
    assert calls == [
        "domain-bravo-match-0",
        "domain-charlie-match-0",
        "domain-alpha-match-0",
        "domain-bravo-match-1",
        "domain-charlie-match-1",
        "domain-alpha-match-1",
    ]


def test_priority_players_claim_parallel_download_slots_first(
    tmp_path, monkeypatch
):
    _install_fast_fakes(tmp_path, monkeypatch, demos_per_player=1)
    _install_parse_stubs(monkeypatch)
    started = []
    started_lock = threading.Lock()
    three_started = threading.Event()
    release = threading.Event()
    errors = []

    def fake_download(
        match_code, demo_url, dest_dir, progress_cb=None, cancel_event=None
    ):
        with started_lock:
            started.append(match_code)
            if len(started) >= 3:
                three_started.set()
        if not release.wait(timeout=5):
            raise AssertionError("test did not release the priority wave")
        return [str(tmp_path / f"{match_code}.dem")]

    monkeypatch.setattr(pipeline, "download_and_extract", fake_download)

    def coordinate():
        if not three_started.wait(timeout=5):
            errors.append("three download slots were never used")
            release.set()
            return
        with started_lock:
            first_wave = set(started)
        expected = {
            "domain-delta-match-0",
            "domain-bravo-match-0",
            "domain-charlie-match-0",
        }
        if first_wave != expected:
            errors.append(f"unexpected first download wave: {sorted(first_wave)}")
        release.set()

    coordinator = threading.Thread(target=coordinate, daemon=True)
    coordinator.start()
    results, failed = pipeline.run_fast(
        ["Alpha", "Bravo", "Charlie", "Delta"], "de_mirage", max_demos=1,
        download_workers=3, parse_workers=2,
        player_priorities=[0, 10, 5, 20],
    )
    coordinator.join(timeout=5)

    assert coordinator.is_alive() is False
    assert errors == []
    assert failed == []
    assert [result["username"] for result in results] == [
        "Alpha", "Bravo", "Charlie", "Delta"
    ]


def test_missing_priorities_start_downloads_in_input_order(
    tmp_path, monkeypatch
):
    _install_fast_fakes(tmp_path, monkeypatch, demos_per_player=2)
    _install_parse_stubs(monkeypatch)
    calls = []
    calls_lock = threading.Lock()
    monkeypatch.setattr(
        pipeline, "download_and_extract",
        _recording_download(tmp_path, calls, calls_lock),
    )

    results, failed = pipeline.run_fast(
        ["Alpha", "Bravo", "Charlie"], "de_mirage", max_demos=2,
        download_workers=1, parse_workers=1,
        player_priorities=None,
    )

    assert failed == []
    assert len(results) == 3
    assert calls == [
        "domain-alpha-match-0",
        "domain-bravo-match-0",
        "domain-charlie-match-0",
        "domain-alpha-match-1",
        "domain-bravo-match-1",
        "domain-charlie-match-1",
    ]


@pytest.mark.parametrize(
    "priorities",
    [
        pytest.param([99], id="length-mismatch"),
        pytest.param([1, "high", 3], id="non-numeric"),
        pytest.param([1, float("nan"), 3], id="not-finite"),
    ],
)
def test_invalid_priorities_fall_back_to_input_order_with_one_warning(
    tmp_path, monkeypatch, caplog, priorities
):
    _install_fast_fakes(tmp_path, monkeypatch, demos_per_player=1)
    _install_parse_stubs(monkeypatch)
    calls = []
    calls_lock = threading.Lock()
    monkeypatch.setattr(
        pipeline, "download_and_extract",
        _recording_download(tmp_path, calls, calls_lock),
    )

    with caplog.at_level(logging.WARNING, logger=pipeline.log.name):
        results, failed = pipeline.run_fast(
            ["Alpha", "Bravo", "Charlie"], "de_mirage", max_demos=1,
            download_workers=1, parse_workers=1,
            player_priorities=priorities,
        )

    assert failed == []
    assert len(results) == 3
    assert calls == [
        "domain-alpha-match-0",
        "domain-bravo-match-0",
        "domain-charlie-match-0",
    ]
    warnings = [
        record for record in caplog.records
        if record.levelno == logging.WARNING
    ]
    assert len(warnings) == 1
    assert "player_priorities" in warnings[0].getMessage()


def test_discovery_barrier_blocks_downloads_until_every_player_resolves(
    tmp_path, monkeypatch
):
    _install_fast_fakes(tmp_path, monkeypatch, demos_per_player=1)
    _install_parse_stubs(monkeypatch)
    charlie_blocked = threading.Event()
    release_charlie = threading.Event()
    all_discovered = threading.Event()
    discovered = set()
    started = []
    state_lock = threading.Lock()
    violations = []
    errors = []

    def fake_demos(domain, map_name, count):
        if domain == "domain-charlie":
            charlie_blocked.set()
            if not release_charlie.wait(timeout=5):
                raise AssertionError("test never released Charlie discovery")
        with state_lock:
            discovered.add(domain)
            if len(discovered) == 3:
                all_discovered.set()
        return [{
            "match_code": f"{domain}-match-0",
            "demo_url": f"https://demo/{domain}/0.zip",
        }]

    def fake_download(
        match_code, demo_url, dest_dir, progress_cb=None, cancel_event=None
    ):
        with state_lock:
            if not all_discovered.is_set():
                violations.append(match_code)
            started.append(match_code)
        return [str(tmp_path / f"{match_code}.dem")]

    monkeypatch.setattr(
        pipeline.api_client, "get_demos_by_domain", fake_demos
    )
    monkeypatch.setattr(pipeline, "download_and_extract", fake_download)

    def coordinate():
        if not charlie_blocked.wait(timeout=5):
            errors.append("Charlie discovery never started")
            release_charlie.set()
            return
        # Give a scheduler that ignores the barrier a chance to misbehave.
        time.sleep(0.2)
        with state_lock:
            premature = list(started)
        if premature:
            errors.append(
                f"downloads started before the discovery barrier: {premature}"
            )
        release_charlie.set()

    coordinator = threading.Thread(target=coordinate, daemon=True)
    coordinator.start()
    results, failed = pipeline.run_fast(
        ["Alpha", "Bravo", "Charlie"], "de_mirage", max_demos=1,
        download_workers=2, parse_workers=1,
    )
    coordinator.join(timeout=5)

    assert coordinator.is_alive() is False
    assert errors == []
    assert violations == []
    assert failed == []
    assert len(results) == 3
    assert len(started) == 3


def test_high_priority_players_do_not_starve_low_priority(
    tmp_path, monkeypatch
):
    _install_fast_fakes(tmp_path, monkeypatch, demos_per_player=3)
    _install_parse_stubs(monkeypatch)
    calls = []
    calls_lock = threading.Lock()
    monkeypatch.setattr(
        pipeline, "download_and_extract",
        _recording_download(tmp_path, calls, calls_lock),
    )

    results, failed = pipeline.run_fast(
        ["Alpha", "Bravo"], "de_mirage", max_demos=3,
        download_workers=1, parse_workers=1,
        player_priorities=[100, 0],
    )

    assert failed == []
    assert len(results) == 2
    # The high-priority player starts first but cannot hold the only slot for
    # its whole demo list: the rotation must reach low-priority Bravo before
    # Alpha's last demo.
    assert calls == [
        "domain-alpha-match-0",
        "domain-bravo-match-0",
        "domain-alpha-match-1",
        "domain-bravo-match-1",
        "domain-alpha-match-2",
        "domain-bravo-match-2",
    ]


def test_discovery_barrier_groups_a_shared_match_into_one_parse(
    tmp_path, monkeypatch
):
    _install_fast_fakes(tmp_path, monkeypatch, demos_per_player=1)
    _install_parse_stubs(monkeypatch)
    shared_dem = str(tmp_path / "shared.dem")

    def slow_bravo_discovery(domain, map_name, count):
        # Under the old "download as soon as discovery returns" schedule this
        # delay let Alpha download and parse the shared demo alone first.
        if domain == "domain-bravo":
            time.sleep(0.2)
        return [{
            "match_code": "g161-shared-match",
            "demo_url": "https://demo/shared.zip",
        }]

    multi_calls = []
    calls_lock = threading.Lock()

    def fake_multi(path, steamids):
        with calls_lock:
            multi_calls.append((path, tuple(steamids)))
        return ({steamid: _records() for steamid in steamids}, object(), {}, {})

    monkeypatch.setattr(
        pipeline.api_client, "get_demos_by_domain", slow_bravo_discovery
    )
    monkeypatch.setattr(
        pipeline, "download_and_extract", lambda *args, **kwargs: [shared_dem]
    )
    monkeypatch.setattr(pipeline.parse, "parse_demo_multi", fake_multi)
    monkeypatch.setattr(
        pipeline.combat, "parse_combat_stats_multi",
        lambda parser, events, steamids, classified: {
            steamid: None for steamid in steamids
        },
    )

    results, failed = pipeline.run_fast(
        ["Alpha", "Bravo"], "de_mirage", max_demos=1,
        download_workers=2, parse_workers=2,
    )

    assert failed == []
    assert [result["username"] for result in results] == ["Alpha", "Bravo"]
    assert len(multi_calls) == 1
    assert multi_calls[0][0] == shared_dem
    assert set(multi_calls[0][1]) == {
        "steam-domain-alpha", "steam-domain-bravo",
    }
