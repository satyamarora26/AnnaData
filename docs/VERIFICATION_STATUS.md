# Verification Status: 2026-09-10

Implementation commit: `a043a49`, branch `codex/verified-ag-data`.
Main was not changed.

## Passed

- 278 backend regression tests, run locally with dotenv disabled.
- Two frontend tests and the production build.
- Eight offline guard/ranking fixtures, not live model tests.
- Five selected live safety checks: 5/5 passed, mean 8.75s, p50 8.39s,
  p95 10.33s. This is a small direct-agent test, not production accuracy.
- Live desktop/mobile chat and mocked HTTP 429 recovery checks.
- Public unauthenticated feedback access refused; service token remains unset.
- [GitHub CI run](https://github.com/satyamarora26/AnnaData/actions/runs/34473036310):
  backend tests with outbound connections blocked, offline benchmark, frontend
  tests and production build all passed.

The default deployment contains only a stateless public web frontend/backend.
Bearer protection guards identity, feedback and deletion endpoints. In-memory
rate limits, four concurrent request slots, upload/body size caps and a total
15-second body-read deadline bound public admission. Run one worker/instance.
These controls do not establish a production SLA or durable spending ceiling.

## Active Knowledge

79 active document chunks: PM-KISAN 38, Soil Health Card 8, PIB KCC 33.
2,456 structured pesticide uses and 25 MSP labels remain available.
KCC ingestion completed without rejection. ICAR downloaded from the current
official domain and parsed successfully; this is not a completed import.

## Remaining External Gates

- PMFBY, NHB, PAU Kharif, PAU Rabi and ICAR full imports remain unactivated.
  PMFBY hit HTTP 429 after 140/805 staged chunks even with provider-directed
  retry. The failed run did not replace live data. Further imports were paused
  to avoid repeating the same quota failure. A quota-available, completed atomic
  run is needed before claiming these sources are loaded.
- eNAM's official PDF delivery remains invalid/unavailable. No certificate
  bypass, mirror or HTML-as-PDF substitution was accepted.
- Render account registration is complete and a free backend configuration is
  prepared, but no deployment has been submitted. Credential import requires
  explicit approval to transfer the specified backend secrets to Render.
- SMS is excluded from the default Blueprint. Authenticating inbound webhooks
  and scheduler calls is required before deploying that optional channel.

No paid upgrade, SMS send, credential publication, or production-readiness
claim was made. See `CV_EVIDENCE.md` for appropriately bounded CV wording.
