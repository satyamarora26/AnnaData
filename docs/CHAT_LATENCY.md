# Chat Latency and Progress

## Changes on 2026-09-10

- Removed the current question from frontend history. Previously the first
  question could trigger a redundant history-refinement model call.
- Bounded optional browser location acquisition to one second, including a
  still-open permission prompt. Fast coordinates remain included; otherwise
  the backend receives no invented location and can request missing context.
- Added progress-only SSE, keeping the completed answer behind existing output
  checks. This improves feedback during the wait, not model throughput.

## Diagnostic Observations

Before these changes, direct requests to the warm public backend returned:

- `/health`: 3.880 seconds.
- A PM-KISAN annual-payment question: 1.648 seconds, but routed as `direct`
  rather than knowledge retrieval. Not representative of source-backed latency.
- PM-KISAN eligibility: 15.005 seconds, with `kb` retrieval.

After the changes, one local request with real providers for PM-KISAN eligibility
received its first SSE byte in 0.047 seconds and finished in 9.823 seconds.
It emitted understanding, retrieval, composition and checking stages, then a
source-backed result. Local and hosted timings are not comparable improvement
percentages, and these single observations are not latency percentiles.

Free Render idle wake-ups and provider/database latency remain. No keep-alive
automation, paid infrastructure or synthetic performance claims were added.

## Regression Coverage

283 backend tests and 7 frontend tests passed. Added checks cover the streaming
contract, access controls, body caps, generic errors, withholding unsupported
doses, waiting for worker completion on disconnect, fragmented UTF-8 frames,
truncated streams, retained prior conversation and a non-responding GPS API.
The production frontend build and mocked desktop/mobile browser checks passed.

## Public Deployment Verification

The progress-only release was verified on commit `0fe09a3`:

- Backend deploy: `dep-dahf1mm743jc73cmujrg` (completed before frontend deployment).
- Frontend deploy: `dep-dahf3d6k1f9s73fis860`.
- GitHub Actions run `34511849590`: successful.

One public PM-KISAN eligibility request received its first SSE byte in 0.437
seconds and its final `kb`-backed answer in 8.176 seconds. Progress frames
preceded the result. Browser CORS preflight passed; a streaming request with a
farmer identity remained denied with 503 because service access is unconfigured.

Public desktop (1440x900) and mobile (390x844) smoke tests at 18:08 UTC showed
live processing status before answers, with no horizontal overflow or uncaught
browser errors. Observed overall times were 11.513 and 8.420 seconds, including
screenshots. These are two smoke observations, not a percentile or SLA.
Progress and answer screenshots were visually reviewed.

## Sentence-Checked Answer Streaming

The next iteration uses the provider's streaming API and releases completed
sentences after existing claim checks, instead of withholding the whole answer.
No synthetic typing animation or sleeps are used. Referrals and source labels
are appended once at completion; unsupported claims are discarded before any
text event is emitted. Named scheme questions cannot bypass retrieval through
the parser's quick-answer field. Short direct answers can still arrive at once.

An unfinished sentence or whole-answer markdown fence waits until complete.
The existing pattern-based claim checks are not a guarantee that every generated
statement is correct. Initial parsing, retrieval, and free-host wake-up latency
remain; streaming improves progressive delivery, not those preceding steps.

Local verification with real providers produced 12 text updates before the
final result (first text 19.927 seconds, completion 20.904 seconds). Desktop and
mobile browser tests observed partial answers at 13.049 and 17.010 seconds and
completed at 13.951 and 18.398 seconds, including screenshots. These small-sample
observations illustrate variability and must not be presented as an SLA.

298 backend tests and 9 frontend tests passed. Additional coverage verifies
delivery before generation finishes, all chunk boundaries of unsupported claims,
split decimals/currency/units, final source labels, hidden reasoning blocks,
bounded buffering, partial-stream failure and visible partial UI state. The
production frontend build passed, and partial screenshots were visually checked.
