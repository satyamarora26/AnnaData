# Official Source Acquisition Notes

Verified 2026-09-10; this records acquisition, not successful ingestion of every
document. The ingestion audit is authoritative for active corpus status.

- PIB KCC: the CLI user agent received HTTP 403. A download of the exact
  manifest URL using a standard browser user agent succeeded, passed source
  identity validation, and activated all 33 chunks without rejection.
- ICAR: the old `icar.gov.in` URL failed certificate verification. ICAR's
  [official annual reports page](https://icar.org.in/index.php/en/knowledge-management/annual-reports)
  lists the 2023-24 report on its current `icar.org.in` site. The corresponding
  PDF downloaded with normal TLS verification and parsed into 1,266 chunks.
  The manifest now uses that official domain; no third-party mirror is used.
- eNAM: the existing PDF URL returned HTML rather than a PDF. The revised
  [official guideline link](https://enam.gov.in/web/assest/download/Revised-Operational-Guidelines-of-e-NAM.pdf)
  also returned HTML; its `www` variant failed certificate verification. No
  bypass or unverified replacement was accepted. eNAM remains unavailable.
- PMFBY, NHB, and both PAU package PDFs are present and parse successfully.
  A bounded PMFBY retry timed out after staging 86 of 805 chunks. Its incomplete
  data was not activated. Larger sources require a successful atomic retry.
  Batch staging subsequently wrote 140 chunks in a later attempt, but Gemini
  HTTP 429 prevented completion despite provider-directed backoff. The active
  corpus remained unchanged; see `VERIFICATION_STATUS.md`.

Downloaded documents remain ignored by Git. The manifest preserves authority,
scope and source URL; ingestion records hashes and terminal results. Acquisition
failure must not be reported as a successful source import in a CV or demo.
