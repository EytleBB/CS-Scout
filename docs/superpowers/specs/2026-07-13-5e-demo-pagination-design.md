# 5E Demo Pagination Design

## Goal

Make demo discovery scan real historical pages instead of repeatedly reading the public player's latest ten matches, while preserving the current analyze API and player JSON contract.

## Design

1. Use the public Arena player endpoint only to obtain one recent match code.
2. Read that match's Gate detail and identify the requested player by `domain`; extract the player's 36-character UUID.
3. Page through `gate.5eplay.com/crane/http/api/data/match/list` with `page`, `limit=30`, and the UUID. Stop after enough downloadable demos are found, the endpoint returns fewer than 30 records, or the safety page limit is reached.
4. Filter Gate list rows by map, then resolve every candidate's authoritative `main.demo_url` from match detail. A missing list-level URL must not discard a candidate.
5. Reuse a retrying HTTP session for transient connection and 5xx failures.
6. If UUID bootstrap cannot be completed, fall back to the public recent-match scan. Network/API failure must be reported separately from a valid empty result.

## Compatibility

- `get_demos_by_domain(domain, map_name, count)` continues returning a list of `{match_code, demo_url}`.
- `pipeline.run`, `/api/analyze`, and output JSON shapes remain unchanged.
- No 5E Bearer token or new user input is required.

## Tests

- Gate pages are distinct and later pages contribute demos.
- Map candidates with no list-level `demo_url` are retained when match detail has one.
- Transient HTTP failures are retried.
- UUID bootstrap failure uses the bounded public fallback.
- Existing server tests remain green.
