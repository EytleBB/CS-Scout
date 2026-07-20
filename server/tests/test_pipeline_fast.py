import json
import os
import sys
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pipeline


def test_fast_parse_executor_uses_spawn_and_executes_picklable_work():
    with pipeline._new_fast_parse_executor(1) as executor:
        assert executor._mp_context.get_start_method() == "spawn"
        assert executor.submit(pow, 2, 8).result(timeout=15) == 256


def _install_fast_pipeline_fakes(tmp_path, monkeypatch, demos_per_player=2):
    monkeypatch.setattr(pipeline, "cleanup_demos", lambda demo_dir: None)
    monkeypatch.setattr(pipeline.config, "DEMO_DIR", str(tmp_path / "demos"))
    monkeypatch.setattr(pipeline.config, "OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr(
        pipeline,
        "_new_fast_parse_executor",
        lambda workers: ThreadPoolExecutor(max_workers=workers),
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


def test_fast_mode_overlaps_download_and_parse_but_keeps_output_order(
    tmp_path, monkeypatch
):
    _install_fast_pipeline_fakes(tmp_path, monkeypatch, demos_per_player=2)
    counters = {
        "downloads": 0,
        "max_downloads": 0,
        "parses": 0,
        "max_parses": 0,
        "download_parse_overlap": False,
    }
    counter_lock = threading.Lock()
    two_downloads_started = threading.Event()
    release_first_download = threading.Event()
    release_other_downloads = threading.Event()
    first_parse_started = threading.Event()
    two_parses_started = threading.Event()
    release_parses = threading.Event()
    coordination_errors = []
    download_call_count = 0

    def fake_download(match_code, demo_url, dest_dir):
        nonlocal download_call_count
        with counter_lock:
            call_index = download_call_count
            download_call_count += 1
            counters["downloads"] += 1
            counters["max_downloads"] = max(
                counters["max_downloads"], counters["downloads"]
            )
            if counters["downloads"] >= 2:
                two_downloads_started.set()
        release = (
            release_first_download
            if call_index == 0
            else release_other_downloads
        )
        try:
            if not release.wait(timeout=5):
                raise AssertionError("test did not release a download worker")
        finally:
            with counter_lock:
                counters["downloads"] -= 1
        return [str(tmp_path / f"{match_code}.dem")]

    def fake_parse(path, steamid):
        with counter_lock:
            counters["parses"] += 1
            counters["max_parses"] = max(
                counters["max_parses"], counters["parses"]
            )
            if counters["downloads"] > 0:
                counters["download_parse_overlap"] = True
            first_parse_started.set()
            if counters["parses"] >= 2:
                two_parses_started.set()
        try:
            if not release_parses.wait(timeout=5):
                raise AssertionError("test did not release a parse worker")
        finally:
            with counter_lock:
                counters["parses"] -= 1
        return [{
            "official_num": 1,
            "side": "CT",
            "rtype": "Buy",
            "path": [],
            "grenades": [],
        }]

    monkeypatch.setattr(pipeline, "download_and_extract", fake_download)
    monkeypatch.setattr(pipeline.parse, "parse_demo", fake_parse)
    monkeypatch.setattr(
        pipeline.combat,
        "parse_combat_stats",
        lambda path, steamid: {"kd": 1.0, "awp_rounds": 1, "total_rounds": 2},
    )
    built_rounds = {}

    def fake_build(username, domain, steamid, map_name, rounds, combat_stats):
        built_rounds[username] = [round_data["official_num"] for round_data in rounds]
        return {"round_count": len(rounds), "combat_stats": combat_stats}

    monkeypatch.setattr(pipeline.player_json, "build", fake_build)
    progress = []

    def coordinate_workers():
        try:
            if not two_downloads_started.wait(timeout=5):
                coordination_errors.append("two download workers did not overlap")
                return
            # Let exactly one download finish. Its parse must begin while at
            # least one other download is still blocked in the network stage.
            release_first_download.set()
            if not first_parse_started.wait(timeout=5):
                coordination_errors.append("parse did not start after first download")
                return
            release_other_downloads.set()
            if not two_parses_started.wait(timeout=5):
                coordination_errors.append("two parse workers did not overlap")
                return
        finally:
            # Always unblock executor shutdown, including assertion failures.
            release_first_download.set()
            release_other_downloads.set()
            release_parses.set()

    coordinator = threading.Thread(target=coordinate_workers, daemon=True)
    coordinator.start()

    results, failed = pipeline.run_fast(
        ["Alpha", "Bravo"],
        "de_mirage",
        max_demos=2,
        progress_cb=lambda *args: progress.append(args),
        download_workers=2,
        parse_workers=2,
    )
    coordinator.join(timeout=5)

    assert coordinator.is_alive() is False
    assert coordination_errors == []
    assert failed == []
    assert [result["username"] for result in results] == ["Alpha", "Bravo"]
    assert built_rounds == {"Alpha": [1, 1001], "Bravo": [1, 1001]}
    assert counters["max_downloads"] >= 2
    assert counters["max_parses"] >= 2
    assert counters["download_parse_overlap"] is True
    assert any("快速下载" in item[4] for item in progress)
    assert any("并行解析" in item[4] for item in progress)
    summary = json.loads(
        (tmp_path / "output" / "analysis_summary.json").read_text("utf-8")
    )
    assert summary["mode"] == "fast"
    assert [result["username"] for result in summary["results"]] == [
        "Alpha", "Bravo"
    ]


def test_fast_mode_keeps_demo_offsets_when_an_earlier_parse_is_empty(
    tmp_path, monkeypatch
):
    _install_fast_pipeline_fakes(tmp_path, monkeypatch, demos_per_player=3)
    monkeypatch.setattr(
        pipeline,
        "download_and_extract",
        lambda match_code, demo_url, dest_dir: [
            str(tmp_path / f"{match_code}.dem")
        ],
    )

    def fake_parse(path, steamid):
        if path.endswith("match-0.dem"):
            return []
        return [{
            "official_num": 1,
            "side": "T",
            "rtype": "Pistol",
            "path": [],
            "grenades": [],
        }]

    monkeypatch.setattr(pipeline.parse, "parse_demo", fake_parse)
    monkeypatch.setattr(
        pipeline.combat,
        "parse_combat_stats",
        lambda path, steamid: {
            "kd": 1.0,
            "awp_rounds": 0,
            "total_rounds": 1,
            "source": os.path.basename(path),
        },
    )
    aggregated_sources = []

    def fake_aggregate(stats):
        aggregated_sources.extend(item["source"] for item in stats)
        return {"kd": 1.0, "awp_rate": 0.0}

    monkeypatch.setattr(
        pipeline.combat, "aggregate_combat_stats", fake_aggregate
    )
    captured = []

    def fake_build(username, domain, steamid, map_name, rounds, combat_stats):
        captured.extend(round_data["official_num"] for round_data in rounds)
        return {"round_count": len(rounds), "combat_stats": combat_stats}

    monkeypatch.setattr(pipeline.player_json, "build", fake_build)

    results, failed = pipeline.run_fast(
        ["Alpha"], "de_mirage", max_demos=3,
        download_workers=3, parse_workers=3,
    )

    assert failed == []
    assert len(results) == 1
    assert captured == [1001, 2001]
    assert aggregated_sources == [
        "domain-alpha-match-0.dem",
        "domain-alpha-match-1.dem",
        "domain-alpha-match-2.dem",
    ]


def test_fast_mode_offset_failure_is_isolated_to_one_player(tmp_path, monkeypatch):
    _install_fast_pipeline_fakes(tmp_path, monkeypatch, demos_per_player=1)
    monkeypatch.setattr(
        pipeline,
        "download_and_extract",
        lambda match_code, demo_url, dest_dir: [
            str(tmp_path / f"{match_code}.dem")
        ],
    )

    def fake_parse(path, steamid):
        record = {
            "side": "CT",
            "rtype": "Buy",
            "path": [],
            "grenades": [],
        }
        if "domain-bravo" in path:
            record["official_num"] = 1
        return [record]

    monkeypatch.setattr(pipeline.parse, "parse_demo", fake_parse)
    monkeypatch.setattr(pipeline.combat, "parse_combat_stats", lambda *args: None)
    monkeypatch.setattr(
        pipeline.player_json,
        "build",
        lambda username, domain, steamid, map_name, rounds, combat_stats: {
            "round_count": len(rounds),
            "combat_stats": combat_stats,
        },
    )

    results, failed = pipeline.run_fast(
        ["Alpha", "Bravo"], "de_mirage", max_demos=1,
        download_workers=2, parse_workers=2,
    )

    assert [result["username"] for result in results] == ["Bravo"]
    assert len(failed) == 1
    assert failed[0]["username"] == "Alpha"
    assert isinstance(failed[0]["reason"], str) and failed[0]["reason"]


def test_broken_parse_pool_rejects_players_without_aborting_scan(
    tmp_path, monkeypatch
):
    _install_fast_pipeline_fakes(tmp_path, monkeypatch, demos_per_player=1)
    monkeypatch.setattr(
        pipeline,
        "download_and_extract",
        lambda match_code, demo_url, dest_dir: [
            str(tmp_path / f"{match_code}.dem")
        ],
    )

    class RejectingParsePool:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def submit(self, *args, **kwargs):
            raise RuntimeError("process pool is broken")

    monkeypatch.setattr(
        pipeline, "_new_fast_parse_executor", lambda workers: RejectingParsePool()
    )

    results, failed = pipeline.run_fast(
        ["Alpha", "Bravo"], "de_mirage", max_demos=1,
        download_workers=2, parse_workers=2,
    )

    assert results == []
    assert [item["username"] for item in failed] == ["Alpha", "Bravo"]
    assert all(item["reason"] for item in failed)
    summary = json.loads(
        (tmp_path / "output" / "analysis_summary.json").read_text("utf-8")
    )
    assert [item["username"] for item in summary["failed"]] == [
        "Alpha", "Bravo",
    ]


def test_concurrent_shared_match_is_downloaded_only_once(tmp_path, monkeypatch):
    demo_root = tmp_path / "demos"
    monkeypatch.setattr(pipeline.config, "DEMO_DIR", str(demo_root))
    monkeypatch.setattr(pipeline, "_dem_index", {})
    monkeypatch.setattr(pipeline, "_dem_index_ready", False)
    monkeypatch.setattr(pipeline, "_match_download_locks", {})
    monkeypatch.setattr(pipeline.api_client, "get_demo_url", lambda match_id: None)
    download_count = 0
    count_lock = threading.Lock()
    download_started = threading.Event()
    release_download = threading.Event()
    two_callers_reserved_match = threading.Event()
    reserve_count = 0
    original_reserve = pipeline._reserve_match_download_lock

    def tracked_reserve(match_id):
        nonlocal reserve_count
        entry = original_reserve(match_id)
        with count_lock:
            reserve_count += 1
            if reserve_count >= 2:
                two_callers_reserved_match.set()
        return entry

    monkeypatch.setattr(pipeline, "_reserve_match_download_lock", tracked_reserve)

    def fake_download(url, save_path, progress_cb=None):
        nonlocal download_count
        with count_lock:
            download_count += 1
        download_started.set()
        if not release_download.wait(timeout=5):
            raise AssertionError("test did not release the shared download")
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        with zipfile.ZipFile(save_path, "w") as archive:
            archive.writestr("shared.dem", b"demo")
        return save_path

    monkeypatch.setattr(pipeline.api_client, "download_demo", fake_download)
    destinations = [demo_root / "one", demo_root / "two"]

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                pipeline.download_and_extract,
                "shared-match",
                "https://demo/shared.zip",
                str(destination),
            )
            for destination in destinations
        ]
        assert download_started.wait(timeout=5)
        # Both callers have reserved the same match entry, so one is now
        # deterministically waiting on the per-match single-flight lock.
        assert two_callers_reserved_match.wait(timeout=5)
        release_download.set()
        outputs = [future.result(timeout=5) for future in futures]

    assert download_count == 1
    assert outputs[0] == outputs[1]
    assert len(outputs[0]) == 1
    assert os.path.isfile(outputs[0][0])
    assert pipeline._match_download_locks == {}
