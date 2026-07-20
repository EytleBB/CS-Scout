import os
import sys
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pipeline


class ImmediateThread:
    def __init__(self, target, daemon=None):
        self.target = target

    def start(self):
        self.target()


def test_demo_lookup_failure_has_explicit_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(pipeline, "cleanup_demos", lambda demo_dir: None)
    monkeypatch.setattr(pipeline.config, "DEMO_DIR", str(tmp_path / "demos"))
    monkeypatch.setattr(pipeline.config, "OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr(
        pipeline.api_client,
        "search_player",
        lambda username: ("target-domain", "Matched Player"),
    )

    def fail_lookup(domain, map_name, count):
        raise pipeline.api_client.DemoLookupError("5E API unavailable")

    monkeypatch.setattr(
        pipeline.api_client, "get_demos_by_domain", fail_lookup
    )

    results, failed = pipeline.run(["requested-player"], "de_mirage", max_demos=2)

    assert results == []
    assert failed == [{
        "username": "Matched Player",
        "reason": "获取 demo 列表失败：5E API unavailable",
    }]


def test_unexpected_lookup_error_becomes_failure_and_stage_finishes(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(pipeline.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(pipeline, "cleanup_demos", lambda demo_dir: None)
    monkeypatch.setattr(pipeline.config, "DEMO_DIR", str(tmp_path / "demos"))
    monkeypatch.setattr(pipeline.config, "OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr(
        pipeline.api_client,
        "search_player",
        lambda username: ("target-domain", "Matched Player"),
    )
    monkeypatch.setattr(
        pipeline.api_client,
        "get_demos_by_domain",
        lambda domain, map_name, count: (_ for _ in ()).throw(
            ValueError("unexpected lookup response")
        ),
    )

    results, failed = pipeline.run(["requested-player"], "de_mirage", max_demos=2)

    assert results == []
    assert failed == [{
        "username": "requested-player",
        "reason": "处理玩家失败：unexpected lookup response",
    }]


def test_success_uses_domain_for_steamid_defaults_combat_and_calls_result_cb(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(pipeline.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(pipeline, "cleanup_demos", lambda demo_dir: None)
    monkeypatch.setattr(pipeline.config, "DEMO_DIR", str(tmp_path / "demos"))
    monkeypatch.setattr(pipeline.config, "OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr(
        pipeline.api_client,
        "search_player",
        lambda username: ("target-domain", "Current Display Name"),
    )
    monkeypatch.setattr(
        pipeline.api_client,
        "get_demos_by_domain",
        lambda domain, map_name, count: [{
            "match_code": "g161-safe",
            "demo_url": "https://demo.example/safe.zip",
        }],
    )
    steam_calls = []

    def fake_steamid(match_code, username, domain=None):
        steam_calls.append((match_code, username, domain))
        return "76561198000000001"

    monkeypatch.setattr(
        pipeline.api_client, "get_steamid_for_player", fake_steamid
    )
    monkeypatch.setattr(
        pipeline,
        "download_and_extract",
        lambda match_code, demo_url, dest_dir: [str(tmp_path / "demo.dem")],
    )
    monkeypatch.setattr(
        pipeline.parse,
        "parse_demo",
        lambda path, steamid: [{
            "official_num": 1,
            "side": "CT",
            "rtype": "Buy",
            "path": [],
            "grenades": [],
        }],
    )
    monkeypatch.setattr(pipeline.combat, "parse_combat_stats", lambda *args: None)
    monkeypatch.setattr(pipeline.combat, "aggregate_combat_stats", lambda stats: None)
    build_calls = []

    def fake_build(username, domain, steamid, map_name, rounds, combat_stats):
        build_calls.append(combat_stats)
        return {
            "username": username,
            "domain": domain,
            "combat_stats": combat_stats,
            "round_count": len(rounds),
        }

    monkeypatch.setattr(pipeline.player_json, "build", fake_build)
    emitted = []

    results, failed = pipeline.run(
        ["Old Display Name"],
        "de_mirage",
        max_demos=1,
        result_cb=emitted.append,
    )

    default_stats = {"kd": 0.0, "awp_rate": 0.0}
    assert failed == []
    assert steam_calls == [(
        "g161-safe", "Current Display Name", "target-domain"
    )]
    assert build_calls == [default_stats]
    assert results[0]["combat_stats"] == default_stats
    assert emitted == results
    summary = json.loads(
        (tmp_path / "output" / "analysis_summary.json").read_text("utf-8")
    )
    assert summary["results"][0]["combat_stats"] == default_stats


def test_invalid_upstream_domain_is_rejected_before_demo_lookup(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(pipeline.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(pipeline, "cleanup_demos", lambda demo_dir: None)
    monkeypatch.setattr(pipeline.config, "DEMO_DIR", str(tmp_path / "demos"))
    monkeypatch.setattr(pipeline.config, "OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr(
        pipeline.api_client,
        "search_player",
        lambda username: ("../escape", "Matched Player"),
    )

    def forbidden_lookup(*args, **kwargs):
        raise AssertionError("unsafe domain reached demo lookup")

    monkeypatch.setattr(
        pipeline.api_client, "get_demos_by_domain", forbidden_lookup
    )

    results, failed = pipeline.run(["requested-player"], "de_mirage")

    assert results == []
    assert len(failed) == 1
    assert "标识" in failed[0]["reason"]


def test_unexpected_parse_and_combat_errors_are_isolated(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(pipeline.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(pipeline, "cleanup_demos", lambda demo_dir: None)
    monkeypatch.setattr(pipeline.config, "DEMO_DIR", str(tmp_path / "demos"))
    monkeypatch.setattr(pipeline.config, "OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr(
        pipeline.api_client,
        "search_player",
        lambda username: ("target-domain", username),
    )
    monkeypatch.setattr(
        pipeline.api_client,
        "get_demos_by_domain",
        lambda domain, map_name, count: [
            {"match_code": "g161-bad", "demo_url": "https://demo/bad.zip"},
            {"match_code": "g161-good", "demo_url": "https://demo/good.zip"},
        ],
    )
    monkeypatch.setattr(
        pipeline.api_client,
        "get_steamid_for_player",
        lambda match_code, username, domain=None: "765",
    )
    monkeypatch.setattr(
        pipeline,
        "download_and_extract",
        lambda match_code, demo_url, dest_dir: [
            str(tmp_path / f"{match_code}.dem")
        ],
    )

    def fake_parse(path, steamid):
        if path.endswith("g161-bad.dem"):
            raise RuntimeError("unexpected parser failure")
        return [{
            "official_num": 1,
            "side": "CT",
            "rtype": "Buy",
            "path": [],
            "grenades": [],
        }]

    monkeypatch.setattr(pipeline.parse, "parse_demo", fake_parse)
    combat_paths = []

    def fail_combat(path, steamid):
        combat_paths.append(path)
        raise RuntimeError("unexpected combat failure")

    monkeypatch.setattr(
        pipeline.combat,
        "parse_combat_stats",
        fail_combat,
    )
    monkeypatch.setattr(pipeline.combat, "aggregate_combat_stats", lambda stats: None)
    monkeypatch.setattr(
        pipeline.player_json,
        "build",
        lambda username, domain, steamid, map_name, rounds, stats: {
            "round_count": len(rounds),
            "combat_stats": stats,
        },
    )

    results, failed = pipeline.run(["player"], "de_mirage", max_demos=2)

    assert failed == []
    assert len(results) == 1
    assert results[0]["combat_stats"] == {"kd": 0.0, "awp_rate": 0.0}
    assert {os.path.basename(path) for path in combat_paths} == {
        "g161-bad.dem", "g161-good.dem",
    }
