# AnnaData CV Evidence

Draft bullets below are 137-139 characters each, counting spaces and punctuation,
excluding the bullet marker. Use only after understanding and verifying the
corresponding implementation. These do not claim public deployment, real farmer
adoption, field accuracy, production SLA, or measured percentage improvement.

## Software CV

- Built a React and FastAPI advisory platform integrating Gemini, weather, mandi prices and Earth Engine soil data with protected API access
- Implemented bearer authentication, bounded concurrency, upload validation and rate limits, protecting farmer records and public API access
- Validated backend reliability with 250+ offline tests, CI checks and desktop/mobile browser tests, logging 5/5 live safety checks as passed

## Data / AI CV

- Built a 768-dimensional pgvector RAG pipeline with a 9-source manifest, scoped retrieval and atomic document ingestion for farming advice
- Grounded pesticide guidance in 2,456 registered uses, combining crop-pest matching with output guardrails to reject unsupported dose claims
- Evaluated 5 live safety cases with 5/5 passing checks; recorded 8.39s p50 agent latency with structured JSON logs and source-backed answers

## Evidence and Boundaries

- Backend regression tests: 250+ offline tests passed on 2026-09-10; the exact
  final count may increase as fixes are added. This is not 250 live provider tests.
- Live evaluation: 5/5 selected safety cases passed; mean 8.75s, p50 8.39s,
  nearest-rank p95 10.33s. With five samples, p95 is the maximum. Do not claim
  100% production correctness. See `evidence/annadata-live-safety.json`.
- Browser: successful live desktop/mobile chat plus mocked rate-limit recovery.
- Corpus: 2,456 structured pesticide uses observed in the configured database.
- Nine-source manifest is the number of declared trusted document sources,
  not nine successfully loaded documents. Check the latest ingestion audit.
- 768 is the embedding dimension configured in `knowledge.py`.
- CI workflow is implemented and hosted run 34473036310 passed both jobs.
- Local benchmark reports record the base commit with a dirty-worktree flag;
  retain these changes alongside them for reproducibility.
