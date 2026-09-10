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
