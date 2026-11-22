"""Defaults for fast-mode parse process fan-out (server/config.py)."""

import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config


SERVER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _configured_parse_workers(cpu_count, overrides=None):
    """Import config in a clean subprocess with a stubbed CPU count.

    ``config`` resolves worker counts at import time, so the module-level
    value can only be exercised from a fresh interpreter.  Stubbing
    ``os.cpu_count`` before the import keeps the expectation independent of
    the machine running the suite.
    """
    env = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith("CS_SCOUT_")
    }
    env["PYTHONPATH"] = os.pathsep.join(
        [SERVER_DIR, env.get("PYTHONPATH", "")]
    ).strip(os.pathsep)
    env.update(overrides or {})
    script = (
        "import json, os\n"
        "os.cpu_count = lambda: {cpu}\n"
        "import config\n"
        "print(json.dumps(config.FAST_PARSE_WORKERS))\n"
    ).format(cpu=int(cpu_count))
    completed = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    return json.loads(completed.stdout.strip())


@pytest.mark.parametrize(
    ("cpu_count", "expected"),
    [(1, 1), (2, 1), (4, 2), (8, 4), (16, 4), (64, 4)],
)
def test_default_parse_workers_scale_with_cpu_count(cpu_count, expected):
    assert config._default_parse_workers(cpu_count) == expected


@pytest.mark.parametrize(
    ("cpu_count", "expected"),
    [(4, 2), (8, 4), (16, 4)],
)
def test_imported_default_uses_cpu_scaling(cpu_count, expected):
    assert _configured_parse_workers(cpu_count) == expected


def test_env_override_wins_over_cpu_scaling():
    assert _configured_parse_workers(
        8, {"CS_SCOUT_FAST_PARSE_WORKERS": "3"}
    ) == 3


def test_env_override_stays_capped_at_sixteen():
    assert _configured_parse_workers(
        8, {"CS_SCOUT_FAST_PARSE_WORKERS": "99"}
    ) == 16
