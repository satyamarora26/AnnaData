# Task 1 Report: Trusted Source Catalog

## Implementation Summary

Implemented the trusted agricultural source catalog foundation for the Python/FastAPI backend.

- Added immutable `SourceSpec` metadata and `load_catalog` JSON parsing.
- Added HTTPS trusted-host validation for approved government, ICAR, NHB, and PAU domains.
- Added tier validation, duplicate source-ID detection, publication-date parsing, and scope scoring.
- Added the exact nine-record manifest required by the controller ruling.
- Added an offline pytest dependency and test environment credential isolation.
- Ignored local downloaded source documents while keeping the manifest tracked.

## Commands and Results

| Command | Result |
| --- | --- |
| `.venv/bin/python -m pytest tests/test_source_catalog.py -q` | `3 passed in 0.01s` |
| `.venv/bin/python -m pytest -q` | `3 passed in 0.01s` |
| `git diff --check` | Passed with no whitespace errors |
| JSON manifest validation | 9 records; tiers are `official` and `extension`; all URLs use HTTPS |

The brief's command using `python` could not run because this shell has no `python` alias; the repository-local `.venv/bin/python` was used instead.

## TDD Evidence

### RED

After adding the offline test harness and the three catalog tests, the focused test command failed during collection with the expected:

`ModuleNotFoundError: No module named 'source_catalog'`

### GREEN

After implementing `source_catalog.py` and `data/source_manifest.json`, the same focused command passed with `3 passed`.

## Files Changed

- `.gitignore`
- `AnnaData-Backend-main/requirements-tools.txt`
- `AnnaData-Backend-main/source_catalog.py`
- `AnnaData-Backend-main/data/source_manifest.json`
- `AnnaData-Backend-main/tests/conftest.py`
- `AnnaData-Backend-main/tests/test_source_catalog.py`

## Self-Review

- Confirmed all nine stable IDs from the brief are present exactly once.
- Confirmed manifest URLs are restricted by `assert_trusted_url` to HTTPS and the declared trusted hosts.
- Confirmed manifest tiers contain only `official` and `extension`, with required terms on every record.
- Confirmed extension scope scoring requires matching declared state and crop values and returns `None` on mismatch.
- Confirmed `data/downloads/` is ignored and no downloaded source content was added.
- Confirmed unit tests make no network requests and overwrite credential environment variables before test imports.
- Confirmed the worktree diff passes whitespace validation.

## Concerns

- The full backend suite currently discovers only the three catalog tests; no additional backend pytest tests are present in this worktree.
- Source downloads and content verification are intentionally deferred to later tasks; `browser` remains metadata indicating a human browser download requirement.
