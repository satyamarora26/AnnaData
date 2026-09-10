# Reproducible Evaluation and CI

No live API measurements are supplied by this change. A passing offline fixture
is evidence of that regression check only. It is not production accuracy,
retrieval recall, agricultural correctness, uptime, or live LLM response time.

## Offline Benchmark

From `AnnaData-Backend-main`, using an already provisioned development environment:

```sh
PYTHON_DOTENV_DISABLED=1 .venv/bin/python eval/benchmark.py --output /tmp/annadata-offline.json
```

The eight synthetic fixtures exercise the actual `output_guards.scrub` and
`knowledge.rank_passages` functions: supported and unsupported fertilizer
quantities, reference-only subsidy figures, harmless observations, matching and
wrong geographic scope, the similarity floor, and authority preference. Fixture
figures and URLs are synthetic test inputs, not verified agricultural evidence.
The supplied similarities are fixed inputs, not measured embedding similarity.
No model, embedding service, database, vector search, weather provider, or SMS
gateway is called. This measures guard and post-retrieval ranking behavior only.

`--iterations 10` repeats those same eight fixtures ten times. The JSON records
`unique_case_count`, `iterations`, and each case's iteration, so eighty timing
samples cannot be mistaken for eighty independent cases. Timing surrounds only
the pure function call; imports, fixture copying, assertions and file I/O are
excluded. There is no warmup, fixtures run in a fixed order, and repeated calls
may benefit from process caches. The aggregate mixes guard and ranking calls;
use the per-case `category` when comparing similar workloads. No speed threshold
is enforced on variable CI hardware.

## Live Agent Evaluation

The parent task will run live evaluation separately with approved credentials
and data. The following command makes live calls and has not been run here:

```sh
python eval/run.py --tag safety --limit 5 --delay 2 --output /tmp/annadata-live-safety.json
```

Run from `AnnaData-Backend-main`. Omit `--tag`/`--limit` for the full suite or use
`--id CASE_ID` for one case. Filters combine; limits must be positive and delay
must be finite and nonnegative. `--verbose` prints every answer. Without
`--output`, the live report overwrites `eval/results.json`; the offline default
is `eval/offline-results.json`. Use a distinct path for each retained run.

Live timings measure one `run_agent` invocation, including its tool calls,
fallbacks and retries. They exclude startup, inter-case delay, evaluator
assertions and report writing. They are neither isolated LLM latency nor
deployed HTTP/SMS end-to-end latency. `configured_models` records the configured
chain, not which models were actually invoked or how many calls occurred.

## Report Contract

Both commands write schema version 1 JSON with:

- Mode, run status, UTC start/end timestamps, Python/platform, Git commit and
  dirty-worktree indicator, case source path/SHA-256, and timing scope.
- Live filter/delay settings and selected count, or offline unique fixture and
  iteration counts.
- Case IDs, `passed`/`failed`/`error` status, unrounded `latency_seconds`, assertion
  failures, and exception type/message/phase when an error occurs.
- Attempted/pass/fail/error counts and pass rate using **all attempted cases**
  as its denominator, including errors. Empty runs have a null pass rate.
- Latency sample count, mean, maximum, and nearest-rank p50/p95 in seconds.
  Sort n observations and select rank `ceil(p * n)` (one-based); there is no
  interpolation. Empty samples produce null statistics, not measured zeroes.
- `summary.latency` includes errors; `summary.completed_latency` includes only
  cases whose assertions completed (`passed` or `failed`). Always report the
  corresponding sample count and error count with a percentile.

Run status `completed` means all selected cases were processed, not that they
passed. Assertion failures and execution errors return exit code 1; successful
complete runs return 0; invalid CLI arguments return 2. A live selection matching
no cases writes status `no_cases` and returns 1. Startup errors are saved at run
level. Ctrl-C writes status `interrupted`, preserves completed case records, and
returns 130; the interrupted in-flight case has no completed timing record.
Never treat an incomplete run as a full-suite result. Abrupt process termination
or an unwritable output path can prevent report creation.

For a reproducible comparison, retain the JSON, exact code changes when Git is
dirty, dependency versions, and invocation. For live runs also retain corpus
version/counts, provider readiness, model configuration, region, and cold/warm
conditions. The source hash identifies cases, not the installed corpus. Backend
direct dependencies are pinned, but transitive dependencies are not fully locked;
CI reruns alone do not establish an identical Python environment. Frontend
dependencies use `package-lock.json` and `npm ci`.

Small hand-authored suites do not estimate field accuracy. Nearest-rank p95 with
fewer than twenty samples is the maximum. A CV statement should name the exact
suite, observed passed/attempted counts, error count, and timing scope. Do not
infer a percentage improvement without a comparable measured baseline, or
describe repeated synthetic fixtures as farmer traffic. Review live exception
messages and assertion details before sharing reports because they may contain
provider diagnostics or answer excerpts. Reports omit raw queries and answers.

## CI and Monitoring

`.github/workflows/ci.yml` runs on pushes, pull requests and manual dispatch:

- Backend: install declared test dependencies on the runner, compile Python,
  run `tests/` with the existing `tests/conftest.py` offline environment, and
  block outbound socket connections in the pytest process. Dotenv loading is
  disabled and optional credentials are empty; no repository secrets are used.
- Run the synthetic benchmark and publish its status, fixture counts and labelled
  timings in the workflow summary. This provides regression monitoring, not
  production provider monitoring.
- Frontend: `npm ci`, noninteractive Jest tests with mocked backend responses,
  and a production build with `CI=true` so build warnings fail the check.
- Retain backend JUnit, benchmark JSON and frontend Jest JSON for fourteen days,
  including failure runs when those files were produced. The benchmark still
  runs after a test failure; the job remains failed.

No installation or live call is required to run checks in an existing local
environment. From the backend directory:

```sh
PYTHON_DOTENV_DISABLED=1 GOOGLE_API_KEY= .venv/bin/python -m pytest tests -q
```

From `AnnaData-Frontend-main` with dependencies already available:

```sh
CI=true npm test -- --watchAll=false --runInBand
CI=true npm run build
```

The live evaluator and database feedback evaluator are deliberately absent from
CI. Runtime provider availability and corpus readiness need separate live
observations; green fixture checks do not establish either.
