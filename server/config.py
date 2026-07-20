"""
CSAI Server Configuration
"""

import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")


def _absolute_path_env(name, default):
    """Return an absolute operator-provided path, or the repository default."""
    value = os.getenv(name, "").strip()
    return os.path.abspath(os.path.expanduser(value or default))


DEMO_DIR = _absolute_path_env(
    "CS_SCOUT_DEMO_DIR", os.path.join(BASE_DIR, "demos_opponents")
)
OUTPUT_DIR = _absolute_path_env(
    "CS_SCOUT_OUTPUT_DIR", os.path.join(BASE_DIR, "output")
)

# Map data
MAPS_DIR = _absolute_path_env(
    "CS_SCOUT_MAPS_DIR", os.path.join(DATA_DIR, "maps")
)

# Analysis parameters
TICK_RATE = 64
WINDOW_S = 20          # per-round capture window (seconds from freeze_end)
SAMPLE_EVERY = 8       # downsample stride in ticks (~8Hz)
EQ_FULL_BUY = 3800     # legacy team-average threshold; retained for compatibility
EQ_BUY_MIN = 2000      # personal equip floor for keeping a non-pistol Buy round

# Server. Bind locally and disable analysis until a secret is explicitly set;
# deployments can opt into public listening after configuring authentication.
HOST = os.getenv("CS_SCOUT_HOST", "127.0.0.1")
PORT = 5000
SECRET_KEY = os.getenv("CS_SCOUT_SECRET_KEY", "").strip()


def _positive_number(name, default, minimum, maximum):
    """Read a finite, positive, bounded float without breaking startup."""
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError, OverflowError):
        value = float(default)
    if value != value or value in (float("inf"), float("-inf")):
        value = float(default)
    return max(float(minimum), min(float(maximum), value))


def _boolean_env(name, default=False):
    value = os.getenv(name)
    if value is None:
        return bool(default)
    return value.strip().casefold() in {"1", "true", "yes", "on"}


# Demo storage limits. The repository default keeps the legacy 30 GB local
# cache threshold. The 60 GB VPS template overrides it to 16 GB, leaving more
# room for the operating system and extraction workspace.
DEMO_MAX_DOWNLOAD_MB = _positive_number(
    "CS_SCOUT_DEMO_MAX_DOWNLOAD_MB", 1024, 16, 4096
)
DEMO_CACHE_LIMIT_GB = _positive_number(
    "CS_SCOUT_DEMO_CACHE_LIMIT_GB", 30, 1, 1024
)
DEMO_CACHE_TARGET_GB = min(
    DEMO_CACHE_LIMIT_GB,
    _positive_number("CS_SCOUT_DEMO_CACHE_TARGET_GB", 10, 0.5, 1024),
)
DEMO_MIN_FREE_GB = _positive_number(
    "CS_SCOUT_DEMO_MIN_FREE_GB", 8, 1, 1024
)
DEMO_TASK_DOWNLOAD_LIMIT_GB = _positive_number(
    "CS_SCOUT_DEMO_TASK_DOWNLOAD_LIMIT_GB", 12, 1, 1024
)
# Some Chinese networks resolve official CDN names to RFC1918 addresses through
# transparent acceleration. The strict 5E CDN hostname allowlist remains on;
# operators with ordinary public DNS can opt into the additional address check.
DEMO_REQUIRE_PUBLIC_DNS = _boolean_env(
    "CS_SCOUT_DEMO_REQUIRE_PUBLIC_DNS", False
)


def _worker_count(name, default, maximum):
    """Read a positive bounded worker count without breaking startup."""
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError, OverflowError):
        value = default
    return max(1, min(maximum, value))


# Fast mode keeps download and parse pressure independently bounded. Operators
# with unusually fast storage/network or more RAM can raise these explicitly.
_CPU_COUNT = max(1, os.cpu_count() or 1)
FAST_DOWNLOAD_WORKERS = _worker_count(
    "CS_SCOUT_FAST_DOWNLOAD_WORKERS", max(6, min(12, _CPU_COUNT * 2)), 32
)
_DEFAULT_FAST_PARSE_WORKERS = 1 if _CPU_COUNT <= 2 else 2
FAST_PARSE_WORKERS = _worker_count(
    "CS_SCOUT_FAST_PARSE_WORKERS", _DEFAULT_FAST_PARSE_WORKERS, 16
)
FAST_PARSE_MEMORY_PER_WORKER_MB = _worker_count(
    "CS_SCOUT_FAST_PARSE_MEMORY_PER_WORKER_MB", 2048, 65536
)
FAST_PARSE_MEMORY_RESERVE_MB = _worker_count(
    "CS_SCOUT_FAST_PARSE_MEMORY_RESERVE_MB", 1024, 65536
)
