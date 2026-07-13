import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import api_client
import pipeline


def test_lookup_demos_classifies_api_failure(monkeypatch):
    assert hasattr(pipeline, "_lookup_demos")

    def fail_lookup(domain, map_name, count):
        raise api_client.DemoLookupError("connection reset")

    monkeypatch.setattr(api_client, "get_demos_by_domain", fail_lookup)

    demos, reason = pipeline._lookup_demos("domain", "de_mirage", 6)

    assert demos == []
    assert reason == "获取 demo 列表失败：connection reset"
