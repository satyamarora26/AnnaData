# AnnaData Data and Knowledge Integration Design

Date: 2026-09-09
Status: Approved in chat; pending written-spec review

## 1. Purpose

Complete AnnaData's free-first data layer so agricultural answers use verified
soil, weather, price, scheme, pesticide, and agronomy evidence. The system must
remain useful when an upstream service is unavailable and must not invent a
number that a farmer could act upon.

This design covers the backend data and knowledge subsystem. Public deployment,
API authentication, rate limiting, durable background jobs, and SMS/WhatsApp
operations will be handled in later designs.

## 2. Current State

The following integrations are configured and have been exercised locally:

- Gemini generates and parses responses.
- OpenStreetMap Nominatim geocodes Indian place names without an API key.
- Open-Meteo supplies weather, with MET Norway implemented as a fallback.
- Google Earth Engine returns OpenLandMap soil texture, pH, and organic carbon.
- Neon PostgreSQL stores farmer profiles, messages, feedback, MSP data, and
  health state.
- Neon pgvector is enabled, and the `documents` and `pesticide_uses` tables
  exist.
- A data.gov.in key is configured for mandi prices. The upstream endpoint is
  currently timing out, so AnnaData correctly degrades to cached data or MSP.

The following gaps remain:

- The document and pesticide tables are empty.
- Knowledge schema creation is not part of normal application startup.
- Ingestion is not idempotent and has no durable source manifest.
- Fertiliser questions do not retrieve supporting agronomy documents.
- Deterministic guards cover pesticide, scheme, and support-price claims but do
  not block unsupported fertiliser quantities.
- Health output sometimes describes configured credentials rather than actual
  runtime readiness and loaded record counts.
- Provider User-Agent strings still contain upstream repository ownership.

## 3. Source Policy

### 3.1 Trust tiers

Sources are stored with one of three trust tiers:

1. `official`: Government of India, state-government departments, CIB&RC,
   CACP, data.gov.in, and ICAR publications.
2. `extension`: State agricultural universities and Krishi Vigyan Kendra
   packages of practices. Advice is valid only within the source's geographic
   and crop scope.
3. `reference`: Useful supporting material that cannot authorize a dose,
   financial figure, eligibility rule, or legal claim.

Blogs, anonymous advice, commercial input-company pages, generated text, and
unattributed summaries are excluded from the corpus.

### 3.2 Initial corpus

The first load will contain:

- All six CIB&RC Major Uses of Pesticides lists dated 31.03.2026.
- Current official MSP schedules from CACP or a Government of India release.
- PM-KISAN operational material.
- Pradhan Mantri Fasal Bima Yojana material.
- Kisan Credit Card material.
- Soil Health Card guidance.
- eNAM and official post-harvest or cold-storage guidance.
- Selected ICAR and PAU crop-production guidance needed for the crops covered
  by the evaluation suite.

Every item retains its authority, title, canonical URL, publication or
effective date when available, retrieval date, trust tier, and geographic or
crop scope.

## 4. Data Model

The existing PostgreSQL and pgvector database remains the only persistent data
service.

### 4.1 Documents

Extend `documents` with:

- `authority`: organization responsible for the source.
- `published_on`: publication or effective date when available.
- `retrieved_at`: when AnnaData acquired the source.
- `content_hash`: SHA-256 hash of cleaned source content.
- `scope`: JSONB metadata for states, crops, topics, and languages.

Add an idempotency constraint over source identity, content hash, and chunk
index. Re-running ingestion must update changed material or leave identical
chunks untouched rather than duplicating them.

### 4.2 Ingestion runs

Add an `ingestion_runs` table recording source, start and completion times,
status, source hash, parsed chunks, stored chunks, rejected rows, and a bounded
error message. This makes source freshness and partial failures inspectable.

### 4.3 Structured facts

Keep pesticide uses and MSP figures in structured tables. Pesticide rows remain
keyed by product, crop, and pest; no value is inferred when a PDF row cannot be
parsed safely. MSP rows remain keyed by normalized commodity aliases and
marketing year.

Unstructured scheme and agronomy prose remains in `documents`. A future source
can be added without introducing another database.

## 5. Ingestion Pipeline

1. Acquire only allowlisted source URLs or user-supplied files with recorded
   provenance.
2. Validate content type, file signature, source size, and non-empty text.
3. Parse structured pesticide tables with the existing conservative loader.
4. Clean prose, preserve paragraph boundaries, and create overlapping chunks.
5. Attach trust, authority, date, and scope metadata.
6. Hash the cleaned source and skip unchanged data.
7. Generate 768-dimensional Gemini embeddings and store them in pgvector.
8. Record counts and failures in `ingestion_runs`.
9. Run post-load coverage checks before the source becomes available to the
   agent.

An ingestion failure must never prevent FastAPI from starting. Previously
loaded data remains active until a replacement load completes successfully.

## 6. Retrieval and Answer Flow

The existing deterministic intent planner remains responsible for tool
selection.

- Scheme, subsidy, storage, and general programme questions use `kb`.
- Fertiliser and nutrient-management questions use `soil`, `weather`, and `kb`.
- Pest and disease questions use registered `doses` and weather where useful.
- Market questions use live mandi data, cached mandi data, and annual MSP as
  distinct facts.

Retrieval will:

1. Embed the standalone farmer question.
2. Retrieve a wider pgvector candidate set.
3. Remove passages below the measured similarity threshold.
4. Prefer matching crop and geographic scope.
5. Rank `official` above `extension`, and `extension` above `reference` when
   relevance is otherwise comparable.
6. Pass at most five concise passages to Gemini, each labelled with its source
   and trust tier.

If no passage answers the question, the system names the subjects it does cover
and refers the farmer to an agriculture office or KVK instead of stretching an
unrelated passage.

## 7. Numeric Safety Guard

Prompt instructions remain useful but are not the enforcement boundary.
Deterministic post-generation checks will cover claims that farmers may act on.

- Pesticide products, doses, dilution, and waiting periods are allowed only
  when an exact crop-pest record was retrieved from `pesticide_uses`.
- MSP and subsidy figures are allowed only when the corresponding structured
  record or relevant retrieved passage supports them.
- Fertiliser quantities such as kg, g, ml, or litres per acre or hectare are
  allowed only when the same normalized quantity and unit occur in a retrieved
  `official` or geographically matching `extension` passage.
- Soil measurements, dates, weather readings, and crop-stage intervals are not
  mistaken for input doses.
- An unsupported sentence is removed and replaced with a short request for a
  soil test or referral to the relevant KVK or agriculture office.

The guard records only the claim category and removal reason. It does not log a
farmer's full message or any credential.

## 8. Runtime Readiness

Application startup will initialize all database schemas, including knowledge
and ingestion tables, without loading source data during a web request.

`GET /health` will distinguish:

- Credential configured.
- Client initialized.
- Upstream currently reachable or circuit-open.
- Database connected.
- Counts for documents, pesticide uses, MSP commodities, and recent successful
  ingestion runs.

Soil readiness will use the Earth Engine startup result rather than the mere
presence of a key. Farmer-profile readiness will use the active connection pool
rather than the presence of `DATABASE_URL`. Knowledge readiness requires loaded
documents or a deliberately configured external fallback.

Nominatim and MET Norway User-Agent values will identify the current AnnaData
repository and provide a valid contact URL.

## 9. Failure Handling

- Data.gov.in timeouts open the existing circuit breaker; cached dated prices
  or MSP are returned when available.
- Earth Engine failures omit soil evidence without blocking weather or general
  agronomy advice.
- Neon failures fall back to stateless operation and expose a degraded health
  state.
- Embedding or ingestion failures preserve the previous corpus.
- Malformed or ambiguous pesticide rows are rejected rather than guessed.
- Model output that fails a numeric safety guard is corrected before it reaches
  the web or messaging channel.

## 10. Verification

### 10.1 Unit tests

- Source validation, hashing, and idempotent ingestion.
- Trust-tier and geographic-scope ranking.
- Fertiliser quantity allowed with matching evidence.
- Fertiliser quantity removed without matching evidence.
- Existing pesticide, subsidy, MSP, and SMS formatting guards.
- Health status based on live component state.

### 10.2 Integration tests

- Neon schema initialization and pgvector search.
- A known CIB&RC crop-pest pair returns a registered use.
- An unknown crop-pest pair returns no chemical or dose.
- A covered scheme answer includes its source.
- An uncovered scheme question refuses unsupported figures.
- A fertiliser query uses soil plus scoped agronomy evidence.
- Data.gov.in outage falls back without failing the agent.

Live Gemini and Earth Engine tests remain opt-in because they consume external
quota. The default test suite uses fixtures and mocked clients.

### 10.3 Acceptance criteria

- Database startup creates every required schema automatically.
- Re-running an unchanged ingestion creates zero duplicate chunks.
- Health reports non-zero loaded counts after ingestion.
- Every actionable pesticide, fertiliser, support-price, or subsidy figure is
  traceable to retrieved evidence.
- Existing multilingual and conversation-memory behavior does not regress.
- The application continues answering when any optional upstream is disabled.

## 11. Delivery Sequence

1. Schema initialization, metadata, and health reporting.
2. Idempotent document ingestion and source manifest.
3. CIB&RC and MSP structured-data loading.
4. Verified scheme and agronomy corpus loading.
5. Scoped retrieval and citation formatting.
6. Fertiliser numeric guard and regression tests.
7. End-to-end verification through FastAPI and the React client.

## 12. Non-goals

- No AWS Bedrock or separate managed vector database.
- No migration away from Python, FastAPI, React, or PostgreSQL.
- No model fine-tuning.
- No scraping of sources that prohibit automation.
- No public deployment, SMS gateway setup, WhatsApp configuration, API
  authentication, rate limiting, Redis queue, or CI/CD changes in this phase.
