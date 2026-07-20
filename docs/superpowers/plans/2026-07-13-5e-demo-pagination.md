# 5E Demo Pagination Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retrieve downloadable demos from real 5E match-history pages without changing the web API contract.

**Architecture:** Bootstrap the player's UUID from an Arena match plus Gate detail, then page the Gate match-list endpoint and resolve authoritative demo URLs per map candidate. Keep the existing public scan as a bounded fallback and use one retrying HTTP session.

**Tech Stack:** Python, requests/urllib3 Retry, pytest

## Global Constraints

- No new credentials or frontend fields.
- Preserve `get_demos_by_domain` and JSON response shapes.
- Cap history traversal to avoid unbounded API traffic.

---

### Task 1: Demo discovery regression tests

**Files:**
- Create: `server/tests/test_api_demo_discovery.py`
- Modify: `server/api_client.py`

**Interfaces:**
- Consumes: `get_demos_by_domain(domain: str, map_name: str, count: int)`
- Produces: `_get_player_uuid(domain: str, matches: list)`, `_get_gate_match_page(uuid: str, page: int, limit: int = 30)`

- [x] **Step 1: Write failing tests** for UUID extraction, distinct Gate pagination, detail-only demo URLs, retry configuration, and public fallback.
- [x] **Step 2: Run `python -m pytest tests/test_api_demo_discovery.py -v --basetemp ../.pytest-tmp-demo-discovery`** and confirm failures are caused by missing pagination behavior.
- [x] **Step 3: Add the minimal retrying session and Gate pagination implementation in `api_client.py`.**
- [x] **Step 4: Re-run the focused test and confirm all cases pass.**

### Task 2: Pipeline error reporting

**Files:**
- Modify: `server/pipeline.py`
- Test: `server/tests/test_pipeline_demo_lookup.py`

**Interfaces:**
- Consumes: `DemoLookupError` raised only when no valid source response was obtained.
- Produces: a `player_failed` reason beginning with `获取 demo 列表失败`.

- [x] **Step 1: Write a failing pipeline test for lookup failure classification.**
- [x] **Step 2: Run the focused test and confirm the current generic/background behavior fails it.**
- [x] **Step 3: Catch `DemoLookupError` in the download stage and enqueue the explicit reason.**
- [x] **Step 4: Re-run the focused test.**

### Task 3: Verification

**Files:**
- Verify: `server/api_client.py`
- Verify: `server/pipeline.py`
- Verify: `server/tests/`

**Interfaces:**
- Consumes: all prior tasks.
- Produces: verified server behavior.

- [x] **Step 1: Run the focused demo-discovery tests.**
- [x] **Step 2: Run `python -m pytest tests/ -v --basetemp ../.pytest-tmp-full`.**
- [x] **Step 3: Inspect `git diff --check` and the final diff for unrelated changes.**
