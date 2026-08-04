import json
import multiprocessing
from pathlib import Path
import threading
import time
from types import SimpleNamespace
from concurrent.futures import ProcessPoolExecutor

from perfectworld_experiment import pipeline
from perfectworld_experiment.pwa_client import PerfectWorldDemo, PerfectWorldPlayer


class FakeClient:
    def discover(self, *, map_name, limit):
        assert map_name == "de_mirage"
        assert limit == 1
        return [
            PerfectWorldDemo(
                "match-a",
                "https://example.test/demo?access_token=never-write-this",
                "de_mirage",
            )
        ]

    def build_download_headers(self):
        return {"X-PWA-Signature": "never-write-this-either"}


def test_experiment_pipeline_writes_only_to_injected_output_and_redacts_secrets(
    tmp_path, monkeypatch
):
    fake_combat = SimpleNamespace(
        parse_combat_stats=lambda path, steamid: {
            "kills": 2,
            "deaths": 1,
            "awp_rounds": 1,
            "total_rounds": 2,
        },
        aggregate_combat_stats=lambda stats: {"kd": 2.0, "awp_rate": 50.0},
    )
    fake_maps = SimpleNamespace(load_map=lambda map_name: {"name": map_name})
    fake_parse = SimpleNamespace(
        parse_demo=lambda path, steamid: [
            {
                "official_num": 1,
                "side": "CT",
                "rtype": "Pistol",
                "path": [],
                "grenades": [],
            }
        ]
    )

    def build(username, domain, steamid, map_name, rounds, combat):
        return {
            "username": username,
            "domain": domain,
            "steamid": steamid,
            "map": map_name,
            "combat_stats": combat,
            "round_count": len(rounds),
            "rounds": rounds,
        }

    fake_player_json = SimpleNamespace(build=build)
    monkeypatch.setattr(
        pipeline,
        "_load_cs_scout_modules",
        lambda: (fake_combat, fake_maps, fake_parse, fake_player_json),
    )

    def downloader(match_id, demo_url, destination, **kwargs):
        assert "never-write-this" in demo_url
        demo = Path(destination) / "match-a.dem"
        demo.parent.mkdir(parents=True, exist_ok=True)
        demo.write_bytes(b"PBDEMS2")
        return [str(demo)]

    output_dir = tmp_path / "pwa-output"
    summary = pipeline.run(
        "76561198123456789",
        "never-write-this",
        "de_mirage",
        1,
        player_name="tester",
        demo_dir=tmp_path / "pwa-demos",
        output_dir=output_dir,
        client=FakeClient(),
        downloader=downloader,
        require_public_dns=False,
    )

    assert summary["platform"] == "perfectworld"
    serialized = "\n".join(
        path.read_text(encoding="utf-8") for path in output_dir.glob("*.json")
    )
    assert "never-write-this" not in serialized
    assert json.loads((output_dir / "analysis_summary.json").read_text("utf-8"))[
        "results"
    ][0]["round_count"] == 1


def test_roster_pipeline_downloads_a_shared_match_only_once(tmp_path, monkeypatch):
    fake_combat = SimpleNamespace(
        parse_combat_stats=lambda path, steamid: {
            "kills": 1,
            "deaths": 1,
            "awp_rounds": 0,
            "total_rounds": 1,
        },
        aggregate_combat_stats=lambda stats: {"kd": 1.0, "awp_rate": 0.0},
    )
    fake_maps = SimpleNamespace(load_map=lambda map_name: {"name": map_name})
    fake_parse = SimpleNamespace(
        parse_demo=lambda path, steamid: [
            {
                "official_num": 1,
                "side": "T",
                "rtype": "Buy",
                "path": [],
                "grenades": [],
            }
        ]
    )

    def build(username, domain, steamid, map_name, rounds, combat):
        return {
            "username": username,
            "domain": domain,
            "steamid": steamid,
            "map": map_name,
            "combat_stats": combat,
            "round_count": len(rounds),
            "rounds": rounds,
        }

    monkeypatch.setattr(
        pipeline,
        "_load_cs_scout_modules",
        lambda: (
            fake_combat,
            fake_maps,
            fake_parse,
            SimpleNamespace(build=build),
        ),
    )

    class RosterClient:
        def discover(self, *, map_name, limit, target_steamid):
            return [
                PerfectWorldDemo(
                    "shared-match",
                    "https://example.test/demo?access_token=never-write-this",
                    "de_mirage",
                )
            ]

        def build_download_headers(self):
            return {"X-PWA-Signature": "never-write-this-either"}

    calls = []

    def downloader(match_id, demo_url, destination, **kwargs):
        calls.append(match_id)
        path = Path(destination) / f"{match_id}.dem"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"PBDEMS2")
        return [str(path)]

    output_dir = tmp_path / "output"
    summary = pipeline.run_roster(
        [
            PerfectWorldPlayer("1001", "76561198123456789", "Alpha"),
            PerfectWorldPlayer("1002", "76561198987654321", "Bravo"),
        ],
        "76561198000000000",
        "never-write-this",
        "de_mirage",
        1,
        current_match_id="live-match",
        demo_dir=tmp_path / "demos",
        output_dir=output_dir,
        client=RosterClient(),
        downloader=downloader,
        require_public_dns=False,
        parse_workers=1,
    )

    assert calls == ["shared-match"]
    assert [result["username"] for result in summary["results"]] == ["Alpha", "Bravo"]
    assert summary["unique_demos_downloaded"] == 1
    serialized = "\n".join(path.read_text("utf-8") for path in output_dir.glob("*.json"))
    assert "never-write-this" not in serialized


def test_roster_pipeline_queries_and_downloads_concurrently_but_keeps_order(
    tmp_path, monkeypatch
):
    fake_combat = SimpleNamespace(
        parse_combat_stats=lambda path, steamid: {
            "kd": 1.0, "awp_rounds": 0, "total_rounds": 1,
        },
        aggregate_combat_stats=lambda stats: {"kd": 1.0, "awp_rate": 0.0},
    )
    fake_maps = SimpleNamespace(load_map=lambda map_name: {"name": map_name})
    fake_parse = SimpleNamespace(parse_demo=lambda path, steamid: [{
        "official_num": 1, "side": "CT", "rtype": "Buy",
        "path": [], "grenades": [],
    }])
    fake_player_json = SimpleNamespace(build=lambda username, domain, steamid,
        map_name, rounds, combat: {
            "username": username, "domain": domain, "steamid": steamid,
            "map": map_name, "combat_stats": combat,
            "round_count": len(rounds), "rounds": rounds,
        })
    monkeypatch.setattr(
        pipeline, "_load_cs_scout_modules",
        lambda: (fake_combat, fake_maps, fake_parse, fake_player_json),
    )

    players = [
        PerfectWorldPlayer("1001", "76561198111111111", "Alpha"),
        PerfectWorldPlayer("1002", "76561198222222222", "Bravo"),
        PerfectWorldPlayer("1003", "76561198333333333", "Charlie"),
    ]
    discovery_barrier = threading.Barrier(3)
    download_barrier = threading.Barrier(3)

    class ConcurrentClient:
        def discover(self, *, map_name, limit, target_steamid):
            discovery_barrier.wait(timeout=2)
            # Reverse completion order to prove final result ordering is stable.
            time.sleep((4 - int(target_steamid[-1])) * 0.01)
            return [PerfectWorldDemo(
                f"match-{target_steamid}",
                f"https://example.test/{target_steamid}",
                "de_mirage",
            )]

        def build_download_headers(self):
            return {"X-PWA-Signature": "safe-test-signature"}

    def downloader(match_id, demo_url, destination, **kwargs):
        download_barrier.wait(timeout=2)
        path = Path(destination) / f"{match_id}.dem"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"PBDEMS2")
        return [str(path)]

    summary = pipeline.run_roster(
        players,
        "76561198000000000",
        "safe-test-token",
        "de_mirage",
        1,
        demo_dir=tmp_path / "demos",
        output_dir=tmp_path / "output",
        client=ConcurrentClient(),
        downloader=downloader,
        require_public_dns=False,
        discovery_workers=3,
        download_workers=3,
        parse_workers=1,
    )

    assert [item["username"] for item in summary["results"]] == [
        "Alpha", "Bravo", "Charlie",
    ]
    assert summary["unique_demos_downloaded"] == 3
    assert summary["workers"] == {"discovery": 3, "download": 3, "parse": 1}


def test_parse_player_worker_is_spawn_picklable(tmp_path):
    player = PerfectWorldPlayer("1001", "76561198123456789", "Alpha")
    with ProcessPoolExecutor(
        max_workers=1,
        mp_context=multiprocessing.get_context("spawn"),
    ) as executor:
        result = executor.submit(
            pipeline._parse_player_output,
            0,
            player,
            [],
            "de_mirage",
            str(tmp_path),
            0,
        ).result(timeout=30)

    assert result[0] == 0
    assert result[1] is None
    assert result[2] == {
        "username": "Alpha",
        "reason": "历史 Demo 中没有解析出可用回合",
    }


def test_default_parse_parallelism_is_capped_by_available_memory(monkeypatch):
    gib = 1024 ** 3
    monkeypatch.setattr(pipeline, "_available_memory_bytes", lambda: 2 * gib)
    assert pipeline._memory_safe_parse_workers(4) == 1

    monkeypatch.setattr(pipeline, "_available_memory_bytes", lambda: 9 * gib)
    assert pipeline._memory_safe_parse_workers(4) == 4
