# Verified Agricultural Data and Knowledge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a free-first, source-governed agricultural knowledge layer that ingests verifiable documents safely, ranks evidence by authority and scope, and prevents unsupported actionable numbers from reaching farmers.

**Architecture:** Keep live soil, weather, mandi, MSP, pesticide, and document retrieval as separate evidence providers behind the existing planner. Add a checked-in source catalog, staged pgvector ingestion, explicit source metadata, scope-aware ranking, and deterministic numeric guards around the LLM. All database and upstream failures remain fail-soft so the agent can answer from whichever verified providers are available.

**Tech Stack:** Python 3.11+, FastAPI, PostgreSQL 16, pgvector, psycopg 3, Gemini embeddings, pypdf, pdfplumber, requests, pytest.

**Spec:** `docs/superpowers/specs/2026-09-09-annadata-data-knowledge-design.md`

## Global Constraints

- This plan changes only the backend data and knowledge subsystem; public deployment, authentication, rate limiting, durable jobs, Redis, SMS, and WhatsApp are outside this phase.
- Preserve the current Python/FastAPI implementation and the existing PostgreSQL plus pgvector architecture.
- Use only `official`, `extension`, and `reference` source tiers; reference material cannot authorize pesticide doses or financial, legal, or fertilizer quantities.
- Restrict automated downloads to the checked-in allowlist; accept browser-downloaded local files only when they map to a checked-in source entry.
- Never replace an active document corpus until every replacement chunk has parsed, embedded, and staged successfully.
- Pesticide quantities must match the structured CIB&RC data; MSP and subsidy figures must be present in retrieved evidence.
- Fertilizer quantities must be present in an official passage or a crop/state-compatible extension passage.
- Do not treat soil measurements, weather values, dates, intervals, phone numbers, or crop spacing as actionable input quantities.
- Keep runtime dependencies separate from offline ingestion and test dependencies in `requirements-tools.txt`.
- Use the current repository URL `https://github.com/satyamarora26/AnnaData` in outbound User-Agent defaults.
- Unit tests must run without network access; live provider and Neon checks are explicit verification steps only.
- Record only measured counts, latencies, and pass rates in documentation or CV notes; do not invent production metrics.

---

## File Map

**Create**

- `AnnaData-Backend-main/source_catalog.py`: parse and validate the trusted source manifest and calculate state/crop scope compatibility.
- `AnnaData-Backend-main/ingestion.py`: validate local files, extract and chunk text, hash content, and coordinate staged document ingestion.
- `AnnaData-Backend-main/readiness.py`: assemble honest readiness details and corpus counts for `/health`.
- `AnnaData-Backend-main/data/source_manifest.json`: stable IDs, authority, tier, dates, topics, scope, and official URLs for the initial corpus.
- `AnnaData-Backend-main/tools/load_msp.py`: reviewed command-line entry point for loading a current official MSP CSV.
- `AnnaData-Backend-main/tests/conftest.py`: isolate default pytest runs from local credentials and live services.
- `AnnaData-Backend-main/tests/test_source_catalog.py`: source-policy and scope tests.
- `AnnaData-Backend-main/tests/test_knowledge_ingestion.py`: ingestion lifecycle and active-corpus tests.
- `AnnaData-Backend-main/tests/test_ingestion.py`: file validation, hashing, chunking, skip, success, and rollback tests.
- `AnnaData-Backend-main/tests/test_structured_loaders.py`: CIB&RC and MSP parser tests.
- `AnnaData-Backend-main/tests/test_retrieval.py`: threshold, tier, scope, and citation formatting tests.
- `AnnaData-Backend-main/tests/test_output_guards.py`: deterministic actionable-number safety tests.
- `AnnaData-Backend-main/tests/test_readiness.py`: startup and health-state tests.
- `AnnaData-Backend-main/tests/fixtures/msp_sample.csv`: a small reviewed MSP export fixture.

**Modify**

- `AnnaData-Backend-main/knowledge.py`: schema migrations, ingestion audit lifecycle, active-document retrieval, ranking metadata, and corpus counts.
- `AnnaData-Backend-main/tools/ingest_docs.py`: manifest-driven CLI using the ingestion service.
- `AnnaData-Backend-main/tools/fetch_cibrc.py`: argparse, allowlisted downloads, current repository identity, and content hashes.
- `AnnaData-Backend-main/tools/load_cibrc.py`: atomic category refreshes and ingestion-run reporting.
- `AnnaData-Backend-main/msp.py`: source metadata, parse-before-write behavior, and atomic MSP refreshes.
- `AnnaData-Backend-main/planner.py`: add grounded knowledge to fertilizer questions.
- `AnnaData-Backend-main/Agent.py`: pass intent, state, and crop into retrieval and retain raw trusted evidence for guards.
- `AnnaData-Backend-main/output_guards.py`: verify fertilizer and pesticide quantities against gathered evidence.
- `AnnaData-Backend-main/app.py`: initialize every schema and expose detailed readiness.
- `AnnaData-Backend-main/config.py`: correct User-Agent defaults and remove configuration-only capability claims.
- `AnnaData-Backend-main/provenance.py`: count only active documents and describe the three source tiers accurately.
- `AnnaData-Backend-main/.env.example`: document provider User-Agents and ingestion settings.
- `AnnaData-Backend-main/requirements-tools.txt`: add pytest.
- `.gitignore`: exclude downloaded source documents while retaining the manifest.
- `README.md`: document source loading, health fields, and verification commands.
- `ENGINEERING_NOTES.md`: record measured post-load results and safety decisions.

---

### Task 1: Trusted Source Catalog

**Files:**
- Create: `AnnaData-Backend-main/source_catalog.py`
- Create: `AnnaData-Backend-main/data/source_manifest.json`
- Create: `AnnaData-Backend-main/tests/test_source_catalog.py`
- Modify: `AnnaData-Backend-main/requirements-tools.txt:13-17`
- Modify: `.gitignore:7-20`

**Interfaces:**
- Consumes: JSON manifest entries with stable source metadata.
- Produces: `SourceSpec`, `load_catalog(path: Path) -> dict[str, SourceSpec]`, `assert_trusted_url(url: str) -> None`, and `scope_score(spec_scope: dict, state: str | None, crop: str | None) -> int | None`.

- [ ] **Step 1: Add the offline test dependency and isolate pytest from local credentials**

Add `pytest==8.4.2` under a new test heading in `requirements-tools.txt`, then create:

```python
# tests/conftest.py
import os


os.environ["GEMINI_API_KEY"] = "offline-test-key"
os.environ["DATABASE_URL"] = ""
os.environ["EE_SERVICE_KEY"] = ""
os.environ["GOV_API_KEY"] = ""
os.environ["LOCATION_API_KEY"] = ""
```

These values are set before test modules import `config`, so `load_dotenv()` cannot pull live credentials from the developer's ignored `.env`.

- [ ] **Step 2: Write the failing catalog tests**

Create:

```python
# tests/test_source_catalog.py
from pathlib import Path

import pytest

from source_catalog import assert_trusted_url, load_catalog, scope_score


MANIFEST = Path(__file__).resolve().parents[1] / "data" / "source_manifest.json"


def test_initial_catalog_contains_each_approved_domain():
    catalog = load_catalog(MANIFEST)
    assert set(catalog) == {
        "pm_kisan_guidelines",
        "pmfby_2023_guidelines",
        "kcc_2026_overview",
        "soil_health_card_faq",
        "enam_operational_guidelines",
        "nhb_cold_chain_standards_2025",
        "icar_crop_management_2023_24",
        "pau_kharif_2025",
        "pau_rabi_2025_26",
    }
    assert {item.tier for item in catalog.values()} == {"official", "extension"}
    assert all(item.required_terms for item in catalog.values())


def test_unlisted_host_is_rejected():
    with pytest.raises(ValueError, match="untrusted source host"):
        assert_trusted_url("https://example.com/fertilizer-advice.pdf")


def test_extension_scope_matches_only_its_declared_state_and_crop():
    scope = {"states": ["Punjab"], "crops": ["wheat", "mustard"]}
    assert scope_score(scope, "Punjab", "wheat") == 4
    assert scope_score(scope, "Punjab", "rice") is None
    assert scope_score(scope, "West Bengal", "wheat") is None
```

- [ ] **Step 3: Run the catalog tests and confirm the missing-module failure**

Run: `cd AnnaData-Backend-main && python -m pytest tests/test_source_catalog.py -q`

Expected: collection fails with `ModuleNotFoundError: No module named 'source_catalog'`.

- [ ] **Step 4: Implement the catalog parser and scope policy**

Create `source_catalog.py` with these public types and rules:

```python
from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse


Tier = Literal["official", "extension", "reference"]
FetchMode = Literal["http", "browser"]

TRUSTED_HOSTS = {
    "pmkisan.gov.in",
    "pmfby.gov.in",
    "www.pib.gov.in",
    "soilhealth.dac.gov.in",
    "enam.gov.in",
    "icar.gov.in",
    "www.icar.gov.in",
    "nhb.gov.in",
    "www.nhb.gov.in",
    "pau.edu",
    "www.pau.edu",
}


@dataclass(frozen=True)
class SourceSpec:
    id: str
    title: str
    authority: str
    tier: Tier
    source_url: str
    published_on: date | None
    topics: tuple[str, ...]
    required_terms: tuple[str, ...]
    scope: dict[str, tuple[str, ...]]
    local_path: Path
    fetch_mode: FetchMode


def assert_trusted_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in TRUSTED_HOSTS:
        raise ValueError(f"untrusted source host: {parsed.hostname or url}")


def _tuple_scope(raw: dict) -> dict[str, tuple[str, ...]]:
    return {key: tuple(str(value) for value in values) for key, values in raw.items()}


def load_catalog(path: Path) -> dict[str, SourceSpec]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    catalog: dict[str, SourceSpec] = {}
    for row in rows:
        assert_trusted_url(row["source_url"])
        if row["tier"] not in {"official", "extension", "reference"}:
            raise ValueError(f"invalid source tier: {row['tier']}")
        published = date.fromisoformat(row["published_on"]) if row.get("published_on") else None
        spec = SourceSpec(
            id=row["id"],
            title=row["title"],
            authority=row["authority"],
            tier=row["tier"],
            source_url=row["source_url"],
            published_on=published,
            topics=tuple(row["topics"]),
            required_terms=tuple(row["required_terms"]),
            scope=_tuple_scope(row.get("scope", {})),
            local_path=Path(row["local_path"]),
            fetch_mode=row["fetch_mode"],
        )
        if spec.id in catalog:
            raise ValueError(f"duplicate source id: {spec.id}")
        catalog[spec.id] = spec
    return catalog


def scope_score(scope: dict, state: str | None, crop: str | None) -> int | None:
    score = 0
    for key, requested in (("states", state), ("crops", crop)):
        allowed = {str(value).casefold() for value in scope.get(key, ())}
        if not allowed:
            continue
        if not requested or requested.casefold() not in allowed:
            return None
        score += 2
    return score
```

Create `data/source_manifest.json` with the exact eight records below. `browser` means the official page requires a human browser download; it does not permit the tool to crawl around the site.

```json
[
  {"id":"pm_kisan_guidelines","title":"PM-KISAN Revised Operational Guidelines","authority":"Department of Agriculture and Farmers Welfare, Government of India","tier":"official","source_url":"https://pmkisan.gov.in/Documents/RevisedPM-KISANOperationalGuidelines%28English%29.pdf","published_on":"2020-03-29","topics":["scheme_subsidy"],"required_terms":["pm-kisan","operational guidelines"],"scope":{},"local_path":"data/downloads/pm-kisan-guidelines.pdf","fetch_mode":"http"},
  {"id":"pmfby_2023_guidelines","title":"2023 Operational Guidelines of PMFBY","authority":"Ministry of Agriculture and Farmers Welfare, Government of India","tier":"official","source_url":"https://pmfby.gov.in/guidelines","published_on":"2024-11-07","topics":["scheme_subsidy"],"required_terms":["pradhan mantri fasal bima yojana","operational guidelines"],"scope":{},"local_path":"data/downloads/pmfby-2023-guidelines.pdf","fetch_mode":"browser"},
  {"id":"kcc_2026_overview","title":"Government Measures Strengthening the Kisan Credit Card Ecosystem","authority":"Press Information Bureau, Government of India","tier":"official","source_url":"https://www.pib.gov.in/PressReleasePage.aspx?PRID=2246855&lang=1&reg=1","published_on":"2026-03-30","topics":["scheme_subsidy","storage_postharvest"],"required_terms":["kisan credit card","farmer"],"scope":{},"local_path":"data/downloads/kcc-2026.html","fetch_mode":"http"},
  {"id":"soil_health_card_faq","title":"Soil Health Card Frequently Asked Questions","authority":"Department of Agriculture and Farmers Welfare, Government of India","tier":"official","source_url":"https://soilhealth.dac.gov.in/files/FAQ_Final_English.pdf","published_on":null,"topics":["fertiliser_nutrition","general"],"required_terms":["soil health card","soil sample"],"scope":{},"local_path":"data/downloads/soil-health-card-faq.pdf","fetch_mode":"http"},
  {"id":"enam_operational_guidelines","title":"National Agriculture Market Operational Guidelines","authority":"Department of Agriculture and Farmers Welfare, Government of India","tier":"official","source_url":"https://enam.gov.in/web/docs/namguidelines.pdf","published_on":"2016-10-04","topics":["market_price","storage_postharvest"],"required_terms":["national agriculture market","agricultural marketing"],"scope":{},"local_path":"data/downloads/enam-guidelines.pdf","fetch_mode":"http"},
  {"id":"nhb_cold_chain_standards_2025","title":"Engineering Guidelines and Minimum System Standards for Cold Chain Components","authority":"National Centre for Cold-chain Development, Ministry of Agriculture and Farmers Welfare","tier":"official","source_url":"https://www.nhb.gov.in/writereaddata/112325022353ENGINEERING%20GUIDELINES%20AND%20MINIMUM%20SYSTEM%20STANDARDS%20FOR%20IMPLEMENTATION%20IN%20COLD%20CHAIN%20COMPONENTS%20%28with%20cover%20page%29.pdf","published_on":null,"topics":["storage_postharvest"],"required_terms":["cold chain components","minimum system standards"],"scope":{},"local_path":"data/downloads/nhb-cold-chain-standards-2025.pdf","fetch_mode":"http"},
  {"id":"icar_crop_management_2023_24","title":"ICAR Annual Report 2023-24: Crop Management","authority":"Indian Council of Agricultural Research","tier":"official","source_url":"https://icar.gov.in/sites/default/files/2025-04/ICAR%20Annual%20Report%202023-24-english.pdf","published_on":null,"topics":["sowing_planting","fertiliser_nutrition","irrigation_water","storage_postharvest"],"required_terms":["crop management","crop production"],"scope":{},"local_path":"data/downloads/icar-annual-report-2023-24.pdf","fetch_mode":"http"},
  {"id":"pau_kharif_2025","title":"Package of Practices for Crops of Punjab: Kharif 2025","authority":"Punjab Agricultural University","tier":"extension","source_url":"https://pau.edu/content/ccil/pf/pp_kharif.pdf","published_on":null,"topics":["sowing_planting","fertiliser_nutrition","irrigation_water","disease_pest"],"required_terms":["package of practices","punjab"],"scope":{"states":["Punjab"]},"local_path":"data/downloads/pau-kharif-2025.pdf","fetch_mode":"http"},
  {"id":"pau_rabi_2025_26","title":"Package of Practices for Crops of Punjab: Rabi 2025-26","authority":"Punjab Agricultural University","tier":"extension","source_url":"https://pau.edu/content/ccil/pf/pp_rabi.pdf","published_on":null,"topics":["sowing_planting","fertiliser_nutrition","irrigation_water","disease_pest"],"required_terms":["package of practices","punjab"],"scope":{"states":["Punjab"]},"local_path":"data/downloads/pau-rabi-2025-26.pdf","fetch_mode":"http"}
]
```

Append `AnnaData-Backend-main/data/downloads/` to `.gitignore`; the manifest remains tracked while copyrighted or large source files remain local.

- [ ] **Step 5: Run the catalog tests**

Run: `cd AnnaData-Backend-main && python -m pytest tests/test_source_catalog.py -q`

Expected: `3 passed`.

- [ ] **Step 6: Commit the trusted-source boundary**

```bash
git add .gitignore AnnaData-Backend-main/requirements-tools.txt AnnaData-Backend-main/source_catalog.py AnnaData-Backend-main/data/source_manifest.json AnnaData-Backend-main/tests/conftest.py AnnaData-Backend-main/tests/test_source_catalog.py
git commit -m "feat: define trusted agricultural source catalog"
```

---

### Task 2: Audited and Atomic Document Storage

**Files:**
- Create: `AnnaData-Backend-main/tests/test_knowledge_ingestion.py`
- Modify: `AnnaData-Backend-main/knowledge.py:30-87,337-492`

**Interfaces:**
- Consumes: `SourceSpec` from Task 1 and an SHA-256 content hash.
- Produces: `start_ingestion(kind: str, source: str, source_url: str, content_hash: str) -> int | None`, `stage_document(run_id: int, spec: SourceSpec, content_hash: str, content: str, chunk_index: int, embedding: list[float]) -> bool`, `activate_documents(run_id: int, spec: SourceSpec, content_hash: str, parsed: int, stored: int, rejected: int) -> bool`, `fail_ingestion(run_id: int, error: str, parsed: int = 0, stored: int = 0, rejected: int = 0) -> None`, `record_ingestion_failure(kind: str, source: str, source_url: str, error: str, content_hash: str | None = None) -> None`, `recent_ingestions(limit: int = 5) -> list[dict]`, and `latest_ingestion() -> dict | None`.

- [ ] **Step 1: Write failing lifecycle tests with a recording database connection**

```python
# tests/test_knowledge_ingestion.py
from contextlib import nullcontext

import knowledge
from source_catalog import load_catalog
from pathlib import Path


class Result:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class RecordingConnection:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=()):
        compact = " ".join(sql.split())
        self.calls.append((compact, params))
        if "SELECT 1 FROM ingestion_runs" in compact:
            return Result(None)
        if "RETURNING id" in compact:
            return Result((41,))
        return Result()


def _spec():
    manifest = Path(__file__).resolve().parents[1] / "data" / "source_manifest.json"
    return load_catalog(manifest)["pm_kisan_guidelines"]


def test_start_ingestion_returns_audit_id(monkeypatch):
    conn = RecordingConnection()
    monkeypatch.setattr(knowledge.db, "is_available", lambda: True)
    monkeypatch.setattr(knowledge.db, "connection", lambda: nullcontext(conn))
    spec = _spec()
    assert knowledge.start_ingestion("document", spec.id, spec.source_url, "abc123") == 41
    assert any("INSERT INTO ingestion_runs" in sql for sql, _ in conn.calls)


def test_document_activation_deactivates_old_version_before_publishing_new(monkeypatch):
    conn = RecordingConnection()
    monkeypatch.setattr(knowledge.db, "is_available", lambda: True)
    monkeypatch.setattr(knowledge.db, "connection", lambda: nullcontext(conn))
    assert knowledge.activate_documents(41, _spec(), "abc123", 3, 3, 0)
    statements = [sql for sql, _ in conn.calls]
    assert statements.index("UPDATE documents SET active = FALSE WHERE source = %s AND active = TRUE") < statements.index("UPDATE documents SET active = TRUE WHERE ingestion_run_id = %s")
    assert "status = 'completed'" in statements[-1]


def test_partial_stage_is_rejected_without_deactivating_current_corpus(monkeypatch):
    conn = RecordingConnection()
    monkeypatch.setattr(knowledge.db, "is_available", lambda: True)
    monkeypatch.setattr(knowledge.db, "connection", lambda: nullcontext(conn))
    assert not knowledge.activate_documents(41, _spec(), "abc123", 3, 2, 1)
    assert not any("SET active = FALSE" in sql for sql, _ in conn.calls)
```

- [ ] **Step 2: Run the lifecycle tests and confirm missing APIs**

Run: `cd AnnaData-Backend-main && python -m pytest tests/test_knowledge_ingestion.py -q`

Expected: failures report that `start_ingestion` and `activate_documents` do not exist.

- [ ] **Step 3: Extend the schema without breaking existing rows**

In `knowledge.py`, create `ingestion_runs` before `documents`, then add idempotent migrations:

```sql
CREATE TABLE IF NOT EXISTS ingestion_runs (
    id              BIGSERIAL PRIMARY KEY,
    kind            TEXT NOT NULL,
    source          TEXT NOT NULL,
    source_url      TEXT,
    content_hash    TEXT,
    status          TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed', 'skipped')),
    parsed_count    INTEGER NOT NULL DEFAULT 0,
    stored_count    INTEGER NOT NULL DEFAULT 0,
    rejected_count  INTEGER NOT NULL DEFAULT 0,
    error           TEXT,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at    TIMESTAMPTZ
);

ALTER TABLE documents ADD COLUMN IF NOT EXISTS authority TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS published_on DATE;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS retrieved_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE documents ADD COLUMN IF NOT EXISTS content_hash TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS scope JSONB NOT NULL DEFAULT '{}';
ALTER TABLE documents ADD COLUMN IF NOT EXISTS topics TEXT[] NOT NULL DEFAULT '{}';
ALTER TABLE documents ADD COLUMN IF NOT EXISTS ingestion_run_id BIGINT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS active BOOLEAN NOT NULL DEFAULT TRUE;

CREATE UNIQUE INDEX IF NOT EXISTS documents_source_hash_chunk
    ON documents (source, content_hash, chunk_index)
    WHERE content_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS documents_active_embedding
    ON documents USING hnsw (embedding vector_cosine_ops)
    WHERE active = TRUE;
```

Do not drop the existing `documents_embedding` index in this migration; removing it can be a separate measured database-maintenance change after the partial index is verified in Neon.

- [ ] **Step 4: Add the ingestion lifecycle functions**

Use the following behavior in `knowledge.py`:

```python
from dataclasses import asdict
from source_catalog import SourceSpec


def start_ingestion(kind: str, source: str, source_url: str,
                    content_hash: str) -> int | None:
    if not db.is_available():
        raise RuntimeError("database unavailable during ingestion")
    with db.connection() as conn:
        if kind == "document":
            prior = conn.execute(
                """SELECT 1 FROM documents
                     WHERE source = %s AND content_hash = %s AND active = TRUE
                     LIMIT 1""",
                (source, content_hash),
            ).fetchone()
        else:
            prior = conn.execute(
                """SELECT 1 FROM ingestion_runs
                     WHERE kind = %s AND source = %s AND content_hash = %s
                       AND status = 'completed' LIMIT 1""",
                (kind, source, content_hash),
            ).fetchone()
        status = "skipped" if prior else "running"
        row = conn.execute(
            """INSERT INTO ingestion_runs
                   (kind, source, source_url, content_hash, status, completed_at)
                 VALUES (%s, %s, %s, %s, %s,
                         CASE WHEN %s = 'skipped' THEN now() ELSE NULL END)
              RETURNING id""",
            (kind, source, source_url, content_hash, status, status),
        ).fetchone()
    return None if prior else row[0]


def stage_document(run_id: int, spec: SourceSpec, content_hash: str,
                   content: str, chunk_index: int, embedding: list[float]) -> bool:
    try:
        with db.connection() as conn:
            conn.execute(
                """INSERT INTO documents
                       (source, title, url, authority, tier, published_on, scope,
                        topics, content_hash, ingestion_run_id, active,
                        chunk_index, content, embedding)
                     VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s,
                             FALSE, %s, %s, %s::vector)""",
                (spec.id, spec.title, spec.source_url, spec.authority, spec.tier,
                 spec.published_on, json.dumps(asdict(spec)["scope"]),
                 list(spec.topics), content_hash, run_id, chunk_index, content,
                 str(embedding)),
            )
        return True
    except Exception as exc:
        print(f"Could not stage document: {exc}")
        return False


def activate_documents(run_id: int, spec: SourceSpec, content_hash: str,
                       parsed: int, stored: int, rejected: int) -> bool:
    if parsed <= 0 or stored != parsed or rejected:
        fail_ingestion(run_id, "replacement corpus was incomplete", parsed, stored, rejected)
        return False
    with db.connection() as conn:
        conn.execute("UPDATE documents SET active = FALSE WHERE source = %s AND active = TRUE", (spec.id,))
        conn.execute("UPDATE documents SET active = TRUE WHERE ingestion_run_id = %s", (run_id,))
        conn.execute("DELETE FROM documents WHERE source = %s AND active = FALSE", (spec.id,))
        conn.execute(
            """UPDATE ingestion_runs
                  SET status = 'completed', parsed_count = %s, stored_count = %s,
                      rejected_count = %s, completed_at = now()
                WHERE id = %s""",
            (parsed, stored, rejected, run_id),
        )
    return True


def fail_ingestion(run_id: int, error: str, parsed: int = 0,
                   stored: int = 0, rejected: int = 0) -> None:
    with db.connection() as conn:
        conn.execute("DELETE FROM documents WHERE ingestion_run_id = %s AND active = FALSE", (run_id,))
        conn.execute(
            """UPDATE ingestion_runs SET status = 'failed', error = %s,
                      parsed_count = %s, stored_count = %s, rejected_count = %s,
                      completed_at = now() WHERE id = %s""",
            (error[:2000], parsed, stored, rejected, run_id),
        )


def record_ingestion_failure(kind: str, source: str, source_url: str,
                             error: str, content_hash: str | None = None) -> None:
    if not db.is_available():
        return
    with db.connection() as conn:
        conn.execute(
            """INSERT INTO ingestion_runs
                   (kind, source, source_url, content_hash, status, error, completed_at)
                 VALUES (%s, %s, %s, %s, 'failed', %s, now())""",
            (kind, source, source_url, content_hash, error[:2000]),
        )
```

Also make every document query use `WHERE active = TRUE`, including `search`, `covered_topics`, `documents_loaded`, and `counts`. Add `recent_ingestions(limit=5)` returning bounded completed, failed, and skipped audit rows newest first, and implement `latest_ingestion()` as the first row or `None`.

- [ ] **Step 5: Run lifecycle tests and all existing offline tests**

Run: `cd AnnaData-Backend-main && python -m pytest tests/test_knowledge_ingestion.py -q && python eval/test_feedback.py`

Expected: lifecycle tests pass and the existing feedback script exits zero.

- [ ] **Step 6: Commit atomic document storage**

```bash
git add AnnaData-Backend-main/knowledge.py AnnaData-Backend-main/tests/test_knowledge_ingestion.py
git commit -m "feat: stage and audit knowledge ingestion"
```

---

### Task 3: Manifest-Driven File Ingestion

**Files:**
- Create: `AnnaData-Backend-main/ingestion.py`
- Create: `AnnaData-Backend-main/tests/test_ingestion.py`
- Modify: `AnnaData-Backend-main/tools/ingest_docs.py:1-171`

**Interfaces:**
- Consumes: `SourceSpec`, Task 2 lifecycle functions, pypdf, and Gemini embeddings.
- Produces: `IngestResult`, `sha256_file(path: Path) -> str`, `sha256_text(text: str) -> str`, `validate_source_file(path: Path) -> None`, `validate_extracted_text(spec: SourceSpec, text: str) -> None`, `clean_text(text: str) -> str`, `chunk_text(text: str) -> list[str]`, and `ingest_source(spec: SourceSpec, path: Path, dry_run: bool = False) -> IngestResult`.

- [ ] **Step 1: Write failing validation and transaction tests**

```python
# tests/test_ingestion.py
from pathlib import Path

import pytest

import ingestion
from source_catalog import load_catalog


def _spec():
    manifest = Path(__file__).resolve().parents[1] / "data" / "source_manifest.json"
    return load_catalog(manifest)["pm_kisan_guidelines"]


def test_pdf_signature_is_required(tmp_path):
    path = tmp_path / "fake.pdf"
    path.write_text("<html>blocked</html>", encoding="utf-8")
    with pytest.raises(ValueError, match="PDF signature"):
        ingestion.validate_source_file(path)


def test_same_bytes_produce_same_sha256(tmp_path):
    first = tmp_path / "one.txt"
    second = tmp_path / "two.txt"
    first.write_text("verified agricultural guidance", encoding="utf-8")
    second.write_text("verified agricultural guidance", encoding="utf-8")
    assert ingestion.sha256_file(first) == ingestion.sha256_file(second)


def test_extracted_text_must_contain_source_identity_terms():
    with pytest.raises(ValueError, match="required source term"):
        ingestion.validate_extracted_text(_spec(), "generic farming advice " * 40)


def test_embedding_failure_preserves_active_corpus(tmp_path, monkeypatch):
    path = tmp_path / "guidance.txt"
    path.write_text(("Apply inputs only from a soil-test recommendation. " * 30), encoding="utf-8")
    events = []
    monkeypatch.setattr(ingestion.knowledge, "start_ingestion", lambda kind, source, url, digest: 9)
    monkeypatch.setattr(ingestion.knowledge, "embed", lambda text: None)
    monkeypatch.setattr(ingestion.knowledge, "fail_ingestion", lambda run_id, error, parsed, stored, rejected: events.append((run_id, parsed, stored, rejected)))
    result = ingestion.ingest_source(_spec(), path)
    assert result.status == "failed"
    assert events == [(9, result.parsed, 0, result.parsed)]


def test_complete_ingestion_activates_all_chunks(tmp_path, monkeypatch):
    path = tmp_path / "guidance.txt"
    path.write_text(("Wheat nutrient guidance for Punjab fields. " * 60), encoding="utf-8")
    staged = []
    monkeypatch.setattr(ingestion.knowledge, "start_ingestion", lambda kind, source, url, digest: 12)
    monkeypatch.setattr(ingestion.knowledge, "embed", lambda text: [0.0] * 768)
    monkeypatch.setattr(ingestion.knowledge, "stage_document", lambda run_id, spec, digest, text, index, vector: staged.append(index) is None or True)
    monkeypatch.setattr(ingestion.knowledge, "activate_documents", lambda run_id, spec, digest, parsed, stored, rejected: parsed == stored and rejected == 0)
    result = ingestion.ingest_source(_spec(), path)
    assert result.status == "completed"
    assert result.parsed == result.stored == len(staged)
```

- [ ] **Step 2: Run the tests and confirm the missing ingestion module**

Run: `cd AnnaData-Backend-main && python -m pytest tests/test_ingestion.py -q`

Expected: collection fails with `ModuleNotFoundError: No module named 'ingestion'`.

- [ ] **Step 3: Move extraction and chunking into the ingestion service**

Create `ingestion.py`. Keep the current paragraph-aware 1,000-character chunks and 150-character overlap, and add strict validation:

```python
from dataclasses import dataclass
import hashlib
from pathlib import Path
import re

import knowledge
from source_catalog import SourceSpec


MAX_SOURCE_BYTES = 80 * 1024 * 1024
CHUNK_CHARS = 1000
OVERLAP_CHARS = 150
MIN_CHUNK_CHARS = 120


@dataclass(frozen=True)
class IngestResult:
    source_id: str
    status: str
    content_hash: str
    parsed: int
    stored: int
    rejected: int


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def validate_source_file(path: Path) -> None:
    if not path.is_file():
        raise ValueError(f"source file not found: {path}")
    size = path.stat().st_size
    if size == 0 or size > MAX_SOURCE_BYTES:
        raise ValueError(f"source file size outside 1-{MAX_SOURCE_BYTES} bytes")
    if path.suffix.casefold() == ".pdf" and path.read_bytes()[:5] != b"%PDF-":
        raise ValueError("source does not have a PDF signature")
    if path.suffix.casefold() not in {".pdf", ".html", ".htm", ".txt"}:
        raise ValueError(f"unsupported source extension: {path.suffix}")


def validate_extracted_text(spec: SourceSpec, text: str) -> None:
    normalized = " ".join(text.casefold().split())
    if len(normalized) < 500:
        raise ValueError("extracted source text is shorter than 500 characters")
    for term in spec.required_terms:
        if term.casefold() not in normalized:
            raise ValueError(f"required source term missing: {term}")
```

Move the existing `read_pdf`, `read_html`, `clean`, and `chunk` behavior from `tools/ingest_docs.py` into `read_source`, `clean_text`, and `chunk_text`. Preserve the control-byte removal and paragraph overlap exactly.

- [ ] **Step 4: Implement strict all-or-nothing orchestration**

Add this control flow to `ingestion.py`:

```python
def ingest_source(spec: SourceSpec, path: Path, dry_run: bool = False) -> IngestResult:
    validate_source_file(path)
    cleaned = clean_text(read_source(path))
    validate_extracted_text(spec, cleaned)
    content_hash = sha256_text(cleaned)
    chunks = chunk_text(cleaned)
    if not chunks:
        raise ValueError(f"no usable text extracted from {path}")
    if dry_run:
        return IngestResult(spec.id, "dry-run", content_hash, len(chunks), 0, 0)

    run_id = knowledge.start_ingestion(
        "document", spec.id, spec.source_url, content_hash
    )
    if run_id is None:
        return IngestResult(spec.id, "skipped", content_hash, len(chunks), 0, 0)

    stored = 0
    rejected = 0
    for index, text in enumerate(chunks):
        vector = knowledge.embed(text)
        if vector is None:
            rejected += 1
            continue
        if knowledge.stage_document(run_id, spec, content_hash, text, index, vector):
            stored += 1
        else:
            rejected += 1

    if not knowledge.activate_documents(run_id, spec, content_hash, len(chunks), stored, rejected):
        return IngestResult(spec.id, "failed", content_hash, len(chunks), stored, rejected)
    return IngestResult(spec.id, "completed", content_hash, len(chunks), stored, rejected)
```

Wrap the embedding loop in `try/except`; on an unexpected exception call `knowledge.fail_ingestion(run_id, str(exc), len(chunks), stored, len(chunks) - stored)` before returning a failed result.

- [ ] **Step 5: Replace the CLI with catalog IDs and bounded downloads**

`tools/ingest_docs.py` must accept only these modes:

```text
--source-id SOURCE_ID   one manifest entry
--all                   every manifest entry
--fetch                 download entries whose fetch_mode is http
--dry-run               parse and report without database writes
--manifest PATH         defaults to data/source_manifest.json
```

For HTTP fetching, call `assert_trusted_url`, then open the response with the exact bounded request below. Reject bodies beyond `MAX_SOURCE_BYTES`, write to `path.with_suffix(path.suffix + ".part")`, validate the temporary file, then use `Path.replace(path)`. Never overwrite a valid local file with an invalid response. For `fetch_mode="browser"`, print the exact `source_url` and `local_path` and leave the source unmodified.

```python
response = requests.get(
    spec.source_url,
    headers={"User-Agent": "AnnaData/1.0 (+https://github.com/satyamarora26/AnnaData)"},
    timeout=(10, 120),
    stream=True,
)
response.raise_for_status()
```

Before writing the body, require `application/pdf` for `.pdf` targets and `text/html` or `text/plain` for HTML/text targets. A missing or generic `application/octet-stream` header may proceed only when the downloaded bytes pass the local signature/extension validation; a conflicting declared media type is rejected. Call `knowledge.record_ingestion_failure("fetch", spec.id, spec.source_url, str(exc))` for every failed fetch without logging response bodies.

- [ ] **Step 6: Run ingestion tests and CLI help**

Run: `cd AnnaData-Backend-main && python -m pytest tests/test_ingestion.py -q && python tools/ingest_docs.py --help`

Expected: `5 passed`; help exits zero without downloading or initializing the database.

- [ ] **Step 7: Commit the ingestion service**

```bash
git add AnnaData-Backend-main/ingestion.py AnnaData-Backend-main/tools/ingest_docs.py AnnaData-Backend-main/tests/test_ingestion.py
git commit -m "feat: add atomic manifest driven ingestion"
```

---

### Task 4: Safe Structured CIB&RC and MSP Refreshes

**Files:**
- Create: `AnnaData-Backend-main/tools/load_msp.py`
- Create: `AnnaData-Backend-main/tests/test_structured_loaders.py`
- Create: `AnnaData-Backend-main/tests/fixtures/msp_sample.csv`
- Modify: `AnnaData-Backend-main/tools/fetch_cibrc.py:1-61`
- Modify: `AnnaData-Backend-main/tools/load_cibrc.py:169-236`
- Modify: `AnnaData-Backend-main/msp.py:24-110`
- Modify: `AnnaData-Backend-main/knowledge.py:30-74`

**Interfaces:**
- Consumes: Task 2 ingestion-run audit functions and reviewed local CIB&RC PDFs/MSP CSVs.
- Produces: `msp.parse_csv(path: str) -> list[tuple[str, str, float]]`, `msp.replace_csv(path: str, year: str, source: str, source_url: str) -> dict`, and atomic category replacement in `tools/load_cibrc.py`.

- [ ] **Step 1: Write failing parser and replacement tests**

Create `tests/fixtures/msp_sample.csv`:

```csv
Report generated by Agmarknet
Marketing year 2026-27
Group,Commodity,MSP,Unit
Cereals,Wheat,2585,Rs/Quintal
Pulses,Red gram/Arhar/Tur(whole),8000,Rs/Quintal
Vegetables,Onion,-,Rs/Quintal
```

Create the tests:

```python
# tests/test_structured_loaders.py
from pathlib import Path

import msp
from tools.load_cibrc import looks_like_product, valid_waiting_period


FIXTURE = Path(__file__).parent / "fixtures" / "msp_sample.csv"


def test_msp_parser_keeps_declared_prices_and_drops_dash():
    rows = msp.parse_csv(str(FIXTURE))
    assert rows == [
        ("Cereals", "Wheat", 2585.0),
        ("Pulses", "Red gram/Arhar/Tur(whole)", 8000.0),
    ]


def test_cibrc_product_and_waiting_period_safety_rules():
    assert looks_like_product("Acephate 75% SP", ["", "", "", "", ""])
    assert not looks_like_product("For control of bollworm", ["", "", "", "", ""])
    assert valid_waiting_period("21") == "21"
    assert valid_waiting_period("500-1000") is None
```

- [ ] **Step 2: Run the structured-loader tests**

Run: `cd AnnaData-Backend-main && python -m pytest tests/test_structured_loaders.py -q`

Expected: `test_msp_parser_keeps_declared_prices_and_drops_dash` fails because `parse_csv` does not exist.

- [ ] **Step 3: Separate MSP parsing from database writes and record provenance**

Extend `commodity_msp` with `source`, `source_url`, and `content_hash` columns. Replace the alias-only primary key with a unique index on `(alias, year)`, preserving existing rows:

```sql
ALTER TABLE commodity_msp ADD COLUMN IF NOT EXISTS source TEXT;
ALTER TABLE commodity_msp ADD COLUMN IF NOT EXISTS source_url TEXT;
ALTER TABLE commodity_msp ADD COLUMN IF NOT EXISTS content_hash TEXT;
ALTER TABLE commodity_msp DROP CONSTRAINT IF EXISTS commodity_msp_pkey;
CREATE UNIQUE INDEX IF NOT EXISTS commodity_msp_alias_year
    ON commodity_msp (alias, year);
```

Change `for_crop` to select `ORDER BY year DESC LIMIT 1`, and change the upsert target to `ON CONFLICT (alias, year)`. Move lines 76-92 of `msp.load_csv` into the pure `parse_csv` function used by the test. Implement `replace_csv` so it parses the entire file before opening a transaction, rejects zero valid rows, then upserts aliases with source metadata. Return:

```python
{
    "commodities": len(rows),
    "aliases": written,
    "year": year,
    "source": source,
    "content_hash": content_hash,
}
```

The upsert must update `msp`, `crop_group`, `year`, `source`, `source_url`, `content_hash`, and `updated_at`. It must never delete a previously loaded marketing year when parsing returns no valid rows.

- [ ] **Step 4: Add the reviewed MSP CLI**

Create `tools/load_msp.py` with required `--csv`, `--year`, and `--source-url` arguments plus `--source` defaulting to `Government of India Minimum Support Prices`. Validate that `--source-url` uses HTTPS and a `.gov.in`, `gov.in`, or `agmarknet.gov.in` host before calling `msp.replace_csv`.

Run: `cd AnnaData-Backend-main && python tools/load_msp.py --help`

Expected: help exits zero and makes no database connection.

- [ ] **Step 5: Make CIB&RC fetching explicit and side-effect-free on help**

Replace manual `sys.argv` handling in `tools/fetch_cibrc.py` with `argparse`. Keep `--out` default `data/cibrc`, add `--timeout` default `300`, and change the User-Agent to:

```python
UA = (
    "Mozilla/5.0 (compatible; AnnaData/1.0 agricultural advisory; "
    "+https://github.com/satyamarora26/AnnaData)"
)
```

Download through `.part` files, require the `%PDF-` signature, enforce an 80 MiB limit per file, and replace the target only after validation.

- [ ] **Step 6: Make each CIB&RC category refresh atomic**

In `tools/load_cibrc.py`, parse each PDF fully and compute its SHA-256 before changing rows. For a non-empty parse, open one transaction, delete the existing category, insert every parsed row with `executemany`, and mark the ingestion run completed. If parsing returns zero rows or an insert raises, preserve the previous category and mark the run failed. Change `ON CONFLICT DO NOTHING` to an update of dose, dilution, waiting period, source, and URL so corrected registers refresh existing keys.

Use one connection context for each replacement so psycopg commits all three statements together or rolls all of them back:

```python
def replace_category(category: str, uses: list[dict], run_id: int) -> int:
    if not uses:
        raise ValueError(f"no safe CIB&RC rows parsed for {category}")
    with db.connection() as conn:
        conn.execute("DELETE FROM pesticide_uses WHERE category = %s", (category,))
        with conn.cursor() as cursor:
            cursor.executemany(INSERT_SQL, uses)
        conn.execute(
            """UPDATE ingestion_runs SET status = 'completed',
                      parsed_count = %s, stored_count = %s, completed_at = now()
                WHERE id = %s""",
            (len(uses), len(uses), run_id),
        )
    return len(uses)
```

Replace the `sys.argv` flag handling with `argparse`: `--folder` defaults to `data/cibrc` and `--dry-run` is a boolean flag. Parsing `--help` must return before `db.init()` or opening a PDF.

- [ ] **Step 7: Run parser tests and no-side-effect help checks**

Run: `cd AnnaData-Backend-main && python -m pytest tests/test_structured_loaders.py -q && python tools/fetch_cibrc.py --help && python tools/load_cibrc.py --help`

Expected: `2 passed`; both help commands exit zero without network or database access.

- [ ] **Step 8: Commit structured loaders**

```bash
git add AnnaData-Backend-main/knowledge.py AnnaData-Backend-main/msp.py AnnaData-Backend-main/tools/fetch_cibrc.py AnnaData-Backend-main/tools/load_cibrc.py AnnaData-Backend-main/tools/load_msp.py AnnaData-Backend-main/tests/test_structured_loaders.py AnnaData-Backend-main/tests/fixtures/msp_sample.csv
git commit -m "feat: make structured agricultural loads auditable"
```

---

### Task 5: Scope-Aware Retrieval and Source Citations

**Files:**
- Create: `AnnaData-Backend-main/tests/test_retrieval.py`
- Modify: `AnnaData-Backend-main/knowledge.py:337-424`
- Modify: `AnnaData-Backend-main/planner.py:15-42`
- Modify: `AnnaData-Backend-main/Agent.py:472-509,681-690`
- Modify: `AnnaData-Backend-main/provenance.py:32-55,129-170`

**Interfaces:**
- Consumes: active document rows with `tier`, `authority`, `scope`, `topics`, and similarity.
- Produces: `rank_passages(passages: list[dict], state: str | None, crop: str | None, limit: int = 5, min_similarity: float | None = None) -> list[dict]` and `search(query: str, state: str | None = None, crop: str | None = None, topic: str | None = None, candidate_limit: int = 20, limit: int = 5) -> list[dict]`.

- [ ] **Step 1: Write failing retrieval and planner tests**

```python
# tests/test_retrieval.py
import knowledge
import planner


def _passage(source, tier, similarity, scope=None):
    return {
        "content": f"guidance from {source}",
        "source": source,
        "title": source,
        "url": f"https://example.gov.in/{source}",
        "authority": source,
        "tier": tier,
        "scope": scope or {},
        "topics": ["fertiliser_nutrition"],
        "similarity": similarity,
    }


def test_matching_extension_guidance_beats_unscoped_material():
    rows = [
        _passage("central", "official", 0.82),
        _passage("pau", "extension", 0.78, {"states": ["Punjab"], "crops": ["wheat"]}),
    ]
    ranked = knowledge.rank_passages(rows, "Punjab", "wheat", min_similarity=0.70)
    assert [row["source"] for row in ranked] == ["pau", "central"]


def test_out_of_scope_extension_guidance_is_removed():
    rows = [_passage("pau", "extension", 0.92, {"states": ["Punjab"]})]
    assert knowledge.rank_passages(rows, "West Bengal", "rice", min_similarity=0.70) == []


def test_reference_loses_to_official_at_equal_scope():
    rows = [_passage("blog", "reference", 0.90), _passage("icar", "official", 0.75)]
    ranked = knowledge.rank_passages(rows, None, None, min_similarity=0.70)
    assert [row["source"] for row in ranked] == ["icar", "blog"]


def test_only_five_passages_reach_the_prompt():
    rows = [_passage(f"official-{index}", "official", 0.90 - index / 100)
            for index in range(8)]
    assert len(knowledge.rank_passages(rows, None, None, limit=5,
                                       min_similarity=0.70)) == 5


def test_formatted_passage_names_authority_tier_and_url():
    text = knowledge.format_passages([_passage("icar", "official", 0.82)],
                                     min_similarity=0.70)
    assert "OFFICIAL" in text
    assert "icar" in text
    assert "https://example.gov.in/icar" in text


def test_fertilizer_questions_retrieve_knowledge_when_available():
    tools = planner.plan_tools("fertiliser_nutrition", has_coords=True,
                               state="Punjab", crop="wheat", kb_available=True)
    assert tools == {"soil", "weather", "kb"}
```

- [ ] **Step 2: Run the retrieval tests**

Run: `cd AnnaData-Backend-main && python -m pytest tests/test_retrieval.py -q`

Expected: failures report that `rank_passages` is missing and fertilizer planning omits `kb`.

- [ ] **Step 3: Add deterministic threshold, scope, and tier ranking**

Implement in `knowledge.py`:

```python
from source_catalog import scope_score


TIER_PRIORITY = {"official": 2, "extension": 1, "reference": 0}


def rank_passages(passages: list[dict], state: str | None, crop: str | None,
                  limit: int = 5, min_similarity: float | None = None) -> list[dict]:
    threshold = RAG_MIN_SIMILARITY if min_similarity is None else min_similarity
    ranked = []
    for passage in passages:
        if passage.get("similarity", 0) < threshold:
            continue
        match = scope_score(passage.get("scope") or {}, state, crop)
        if match is None:
            continue
        ranked.append((match, TIER_PRIORITY.get(passage.get("tier"), -1),
                       passage.get("similarity", 0), passage))
    ranked.sort(key=lambda item: item[:3], reverse=True)
    return [item[3] for item in ranked[:limit]]
```

Update `search` to fetch 20 active candidates, optionally constrain `topic = ANY(topics)`, decode JSONB scope into dictionaries, then call `rank_passages`. Do not apply scope in SQL because unscoped central documents must remain eligible.

- [ ] **Step 4: Include auditable citations in prompt evidence**

Format every accepted passage as:

```text
- [OFFICIAL | Indian Council of Agricultural Research | ICAR Annual Report 2023-24: Crop Management | https://icar.gov.in/sites/default/files/2025-04/ICAR%20Annual%20Report%202023-24-english.pdf] passage text
```

Use `EXTENSION` and `REFERENCE` for the other tiers. Add one prompt instruction: when an answer uses a scheme amount, deadline, MSP, or fertilizer quantity, name the cited authority in the same sentence or the next sentence.

- [ ] **Step 5: Route and scope knowledge queries through the agent**

Change the fertilizer mapping in `planner.py` to `{"soil", "weather", "kb"}`. Extend `gather` with `intent: str`, and call:

```python
passages = knowledge.search(
    query,
    state=state,
    crop=crop,
    topic=intent,
    candidate_limit=20,
    limit=5,
)
```

Return the formatted text under `kb` and the ranked passage dictionaries under `_kb_passages`. For the `doses` tool, call `approved_uses` once, return its formatted text under `doses`, and retain its raw `uses` only when `pest_matched` is true under `_dose_records`. In `get_farming_advice`, ignore gathered keys beginning with `_`; in `tools_used`, report only selected planner tools so `_kb_passages` and `_dose_records` never appear as tool names.

- [ ] **Step 6: Update provenance to use active source metadata**

Filter source-name and scheme-name queries with `active = TRUE`. Describe `official`, `extension`, and `reference` separately, including the rule that state extension guidance is used only inside its declared state scope.

- [ ] **Step 7: Run retrieval and regression tests**

Run: `cd AnnaData-Backend-main && python -m pytest tests/test_retrieval.py tests/test_source_catalog.py -q && python eval/test_feedback.py`

Expected: `9 passed`; the feedback regression script exits zero.

- [ ] **Step 8: Commit scoped retrieval**

```bash
git add AnnaData-Backend-main/knowledge.py AnnaData-Backend-main/planner.py AnnaData-Backend-main/Agent.py AnnaData-Backend-main/provenance.py AnnaData-Backend-main/tests/test_retrieval.py
git commit -m "feat: rank agricultural evidence by scope and authority"
```

---

### Task 6: Deterministic Actionable-Quantity Guards

**Files:**
- Create: `AnnaData-Backend-main/tests/test_output_guards.py`
- Modify: `AnnaData-Backend-main/output_guards.py:28-118`
- Modify: `AnnaData-Backend-main/Agent.py:688-692`
- Modify: `AnnaData-Backend-main/eval/run.py:20-22,151-213`
- Modify: `AnnaData-Backend-main/eval/cases.yaml:220-245`

**Interfaces:**
- Consumes: final answer text, formatted `doses`, raw `_dose_records`, and raw ranked `_kb_passages` from Task 5.
- Produces: `extract_input_claims(text: str) -> set[tuple[str, str, str, str]]`, `extract_waiting_periods(text: str) -> set[int]`, `extract_financial_figures(text: str) -> set[str]`, `extract_product_recommendations(text: str) -> set[str]`, and extended `scrub(answer: str, gathered: dict) -> tuple[str, bool]`.

- [ ] **Step 1: Write failing safety tests**

```python
# tests/test_output_guards.py
import output_guards


def _extension(content):
    return {"tier": "extension", "content": content,
            "scope": {"states": ["Punjab"], "crops": ["wheat"]}}


def test_supported_fertilizer_quantity_survives():
    answer = "Apply 55 kg DAP per acre at sowing."
    gathered = {"_kb_passages": [_extension("Apply 55 kg DAP per acre at sowing.")]}
    assert output_guards.scrub(answer, gathered) == (answer, False)


def test_different_fertilizer_quantity_is_removed():
    answer = "Apply 75 kg DAP per acre at sowing. Keep the field evenly moist."
    gathered = {"_kb_passages": [_extension("Apply 55 kg DAP per acre at sowing.")]}
    cleaned, changed = output_guards.scrub(answer, gathered)
    assert changed
    assert "75 kg" not in cleaned
    assert "evenly moist" in cleaned


def test_soil_weather_dates_and_intervals_are_not_input_claims():
    text = "Soil pH is 8.0, rain was 25 mm, and recheck after 20 days."
    assert output_guards.extract_input_claims(text) == set()


def test_pesticide_quantity_must_match_structured_context():
    answer = "Spray 500 ml per hectare. Remove affected leaves as well."
    gathered = {"doses": "Registered pesticide uses: dose 250 ml per hectare"}
    cleaned, changed = output_guards.scrub(answer, gathered)
    assert changed
    assert "500 ml" not in cleaned
    assert "Remove affected leaves" in cleaned


def test_pesticide_waiting_period_must_match_structured_context():
    answer = "Wait 30 days before harvest. Remove affected leaves now."
    gathered = {"doses": "Registered pesticide uses: wait 21 days before harvest"}
    cleaned, changed = output_guards.scrub(answer, gathered)
    assert changed
    assert "30 days" not in cleaned
    assert "Remove affected leaves" in cleaned


def test_different_msp_figure_is_removed_even_when_msp_exists():
    answer = "The MSP for wheat is Rs 3,000 per quintal."
    gathered = {"msp": "Minimum Support Price for Wheat in 2026-27: Rs 2,585 per quintal."}
    cleaned, changed = output_guards.scrub(answer, gathered)
    assert changed
    assert "3,000" not in cleaned


def test_reference_passage_cannot_authorize_a_subsidy_figure():
    answer = "The scheme pays a 50% subsidy. Ask the district office for eligibility."
    gathered = {"_kb_passages": [{"tier": "reference", "content": "A 50% subsidy is discussed."}]}
    cleaned, changed = output_guards.scrub(answer, gathered)
    assert changed
    assert "50%" not in cleaned
    assert "district office" in cleaned


def test_unregistered_pesticide_product_is_removed():
    answer = "Spray Acephate 75% SP at 500 ml per hectare. Remove affected leaves."
    gathered = {
        "doses": "Registered pesticide uses: Emamectin Benzoate 5% SG, dose 250 ml per hectare",
        "_dose_records": [{"product": "Emamectin Benzoate 5% SG"}],
    }
    cleaned, changed = output_guards.scrub(answer, gathered)
    assert changed
    assert "Acephate" not in cleaned
    assert "Remove affected leaves" in cleaned
```

- [ ] **Step 2: Run safety tests and confirm missing claim extraction**

Run: `cd AnnaData-Backend-main && python -m pytest tests/test_output_guards.py -q`

Expected: failures report that `extract_input_claims` is missing and unsupported fertilizer/pesticide quantities survive.

- [ ] **Step 3: Implement normalized material, amount, unit, and area claims**

Use case-insensitive patterns for these fertilizer materials: `urea`, `DAP`, `MOP`, `SSP`, `potash`, `zinc sulphate`, `zinc sulfate`, `nitrogen`, `phosphorus`, `potassium`, `fertiliser`, `fertilizer`, `manure`, and `compost`. Normalize units as `kg`, `g`, `l`, `ml`, or `tonne`; normalize area as `acre`, `ha`, or `litre`. A claim key is `(material, amount_or_range, unit, basis)`. Use `unspecified` as the material for an area-based quantity with no recognized fertilizer name, which lets the same extractor compare pesticide dose strings such as `500 ml per hectare`.

Recognize both `55 kg DAP per acre` and `DAP at 55 kg/acre`. If a fertilizer quantity has no area or dilution basis, mark its basis as `missing` so it never matches a scoped recommendation that includes a basis. Add `extract_waiting_periods(text: str) -> set[int]` that recognizes only `wait N days before harvest` and `pre-harvest interval of N days`; it must not classify `recheck after N days` as a pesticide interval. Do not include `mm`, `cm`, pH, percentages without an input material, dates, phone numbers, or crop-stage intervals.

- [ ] **Step 4: Strip unsupported sentences while preserving practical advice**

Build trusted fertilizer claims only from `_kb_passages` whose tier is `official` or `extension`. Build subsidy and eligibility evidence only from `official` passages. Build pesticide claims only from the structured `doses` string. For each answer sentence:

```python
claims = extract_input_claims(sentence)
if FERTILIZER_SUBJECT.search(sentence) and not claims.issubset(fertilizer_claims):
    removed = True
    continue
if PESTICIDE_SUBJECT.search(sentence) and not claims.issubset(pesticide_claims):
    removed = True
    continue
```

Apply this guard before the existing MSP and scheme guards. If stripping leaves no useful answer, return a short referral to the nearest Krishi Vigyan Kendra or agriculture officer. Keep non-chemical measures in neighboring sentences.

Compare answer waiting periods with `extract_waiting_periods(gathered["doses"])`. If the structured context starts with `WARNING:` or `No registered pesticide use`, treat its allowed dose and waiting-period sets as empty. Log only `category` and `reason` through the standard `logging` module, for example `output_guard_removed category=fertilizer reason=unsupported_quantity`; never log the answer, source passage, user ID, or credential.

Use `extract_product_recommendations` to capture the product phrase after English action verbs `spray`, `apply`, or `use` and before `at`, `@`, `for`, `on`, a comma, or sentence punctuation. Normalize spaces and case, then require an exact match to a product in `_dose_records`. If no exact crop-pest records were retained, remove every sentence that recommends a captured product. Keep the dose and product checks in the same pesticide branch so a sentence fails when either field is unsupported.

Replace the existing presence-only MSP and scheme checks with exact normalized figure comparison. `extract_financial_figures(text: str) -> set[str]` must canonicalize comma-separated rupee values and percentages, so `Rs 2,585` matches `INR 2585` and `50 percent` matches `50%`. An MSP sentence may use only figures in `gathered["msp"]`; a subsidy or eligibility sentence may use only figures in official `_kb_passages`. Reference passages never authorize those claims.

- [ ] **Step 5: Add an evaluation case for the observed hallucination class**

Add this tagged case, using the existing assertion schema:

```yaml
- id: unsupported_fertilizer_quantity_is_removed
  tags: [fertilizer, safety]
  query: "My wheat field near Ludhiana is alkaline. Exactly how many kilograms of zinc fertilizer should I apply per acre?"
  channel: web
  profile:
    crop: wheat
    location: Ludhiana
    state: Punjab
    latitude: 30.9293211
    longitude: 75.5004841
  expect:
    contains_any: ["soil test", "KVK", "Krishi Vigyan Kendra", "agriculture officer"]
    not_contains_any: ["kg/acre", "kg per acre", "g/acre", "grams per acre"]
```

Add an optional delay to `eval/run.py` so quota-bounded commands in this plan are valid:

```python
import time

ap.add_argument("--delay", type=float, default=0,
                help="seconds to sleep between live cases")

for index, case in enumerate(cases):
    if index and args.delay:
        time.sleep(args.delay)
```

- [ ] **Step 6: Run deterministic tests and one quota-bounded live case**

Run: `cd AnnaData-Backend-main && python -m pytest tests/test_output_guards.py -q`

Expected: `8 passed` without network.

Run after source ingestion: `cd AnnaData-Backend-main && python eval/run.py --id unsupported_fertilizer_quantity_is_removed --delay 4`

Expected: the new fertilizer safety case passes; do not run the full live suite repeatedly because the Gemini free tier is rate-limited.

- [ ] **Step 7: Commit deterministic guards**

```bash
git add AnnaData-Backend-main/output_guards.py AnnaData-Backend-main/Agent.py AnnaData-Backend-main/eval/cases.yaml AnnaData-Backend-main/tests/test_output_guards.py
git commit -m "feat: block unsupported agricultural input quantities"
```

---

### Task 7: Honest Startup and Readiness Reporting

**Files:**
- Create: `AnnaData-Backend-main/readiness.py`
- Create: `AnnaData-Backend-main/tests/test_readiness.py`
- Modify: `AnnaData-Backend-main/app.py:10-55,87-96`
- Modify: `AnnaData-Backend-main/config.py:27-37,143-187`
- Modify: `AnnaData-Backend-main/utils.py:55-65`
- Modify: `AnnaData-Backend-main/.env.example:12-33,59-71`

**Interfaces:**
- Consumes: `db.status`, `startup.status`, `knowledge.counts`, `knowledge.recent_ingestions`, `msp.counts`, and provider status functions.
- Produces: `readiness.snapshot() -> dict` and a detailed `/health` response.

- [ ] **Step 1: Write failing readiness and startup tests**

```python
# tests/test_readiness.py
import app
import readiness


def test_snapshot_distinguishes_configuration_from_runtime(monkeypatch):
    monkeypatch.setattr(readiness.config, "DATABASE_URL", "postgresql://configured")
    monkeypatch.setattr(readiness.db, "is_available", lambda: False)
    monkeypatch.setattr(readiness.db, "status", lambda: "connection timed out")
    monkeypatch.setattr(readiness.knowledge, "counts", lambda: {"documents": 0, "pesticide_uses": 0})
    monkeypatch.setattr(readiness.knowledge, "recent_ingestions", lambda limit=5: [])
    state = readiness.snapshot()
    assert state["database"] == {
        "configured": True,
        "ready": False,
        "status": "connection timed out",
    }
    assert state["knowledge"]["ready"] is False


def test_startup_initializes_knowledge_after_database(monkeypatch):
    calls = []
    monkeypatch.setattr(app.startup, "init_earth_engine", lambda: calls.append("earth_engine"))
    monkeypatch.setattr(app.db, "init", lambda: calls.append("db") or True)
    monkeypatch.setattr(app.feedback, "init", lambda: calls.append("feedback") or True)
    monkeypatch.setattr(app.msp, "init", lambda: calls.append("msp") or True)
    monkeypatch.setattr(app.knowledge, "init", lambda: calls.append("knowledge") or True)
    app.on_startup()
    assert calls.index("db") < calls.index("knowledge")
    assert {"db", "knowledge", "msp", "feedback", "earth_engine"} <= set(calls)


def test_health_is_degraded_when_a_configured_provider_is_not_ready(monkeypatch):
    monkeypatch.setattr(app.readiness, "snapshot", lambda: {
        "database": {"configured": True, "ready": False, "status": "timeout"},
        "gemini": {"configured": True, "ready": True},
    })
    assert app.health()["status"] == "degraded"
```

- [ ] **Step 2: Run readiness tests and confirm missing module/imports**

Run: `cd AnnaData-Backend-main && python -m pytest tests/test_readiness.py -q`

Expected: collection fails for missing `readiness`, and `app` does not expose `knowledge`.

- [ ] **Step 3: Implement a single readiness snapshot**

Create `readiness.py` returning this stable shape:

```python
{
    "database": {"configured": bool(config.DATABASE_URL), "ready": db.is_available(), "status": db.status()},
    "earth_engine": {"configured": bool(config.EE_SERVICE_KEY), "ready": startup.is_available(), "status": startup.status()},
    "gemini": {"configured": bool(config.GEMINI_API_KEY), "ready": utils.client_status()["initialized"], "client_initialized": utils.client_status()["initialized"], "models": list(config.TEXT_MODELS)},
    "mandi": {"configured": bool(config.GOV_API_KEY), "ready": Mandi_Price_Tool.is_available(), "last_error": Mandi_Price_Tool.last_error()},
    "weather": {"ready": weather_tool.is_available(), "last_error": weather_tool.last_error()},
    "knowledge": {"ready": knowledge.documents_loaded() or bool(config.KNOWLEDGE_BASE_ID and config.AWS_ACCESS_KEY and config.AWS_SECRET_KEY), "provider": "pgvector" if knowledge.documents_loaded() else "bedrock" if config.KNOWLEDGE_BASE_ID and config.AWS_ACCESS_KEY and config.AWS_SECRET_KEY else None, **knowledge.counts(), "recent_ingestions": knowledge.recent_ingestions(limit=5)},
    "msp": {"ready": bool(msp.counts().get("msp_commodities")), **msp.counts()},
}
```

If a provider lacks the named `is_available` or `last_error` accessor, add a read-only accessor around its existing internal state; do not issue a new upstream request from `/health`.

Add this non-network accessor to `utils.py`:

```python
def client_status() -> dict:
    return {
        "initialized": client is not None,
        "text_models": list(TEXT_MODELS),
        "media_model": MEDIA_MODEL,
    }
```

- [ ] **Step 4: Initialize all schemas and expose readiness**

Import `knowledge` and `readiness` in `app.py`. Startup order must be `db.init()`, then `feedback.init()`, `msp.init()`, `knowledge.init()`, and `startup.init_earth_engine()`. Use this independent fail-soft loop so one exception does not prevent the remaining initializers from running:

```python
@app.on_event("startup")
def on_startup():
    initializers = (
        ("database", db.init),
        ("feedback", feedback.init),
        ("msp", msp.init),
        ("knowledge", knowledge.init),
        ("earth_engine", startup.init_earth_engine),
    )
    for name, initialize in initializers:
        try:
            initialize()
        except Exception as exc:
            print(f"{name} initialization failed: {exc}")
    print(f"CORS origins: {_build_origins()}")
    print(f"Readiness: {readiness.snapshot()}")
```

Return from `/health`:

```python
state = readiness.snapshot()
configured_failures = [
    name for name, details in state.items()
    if isinstance(details, dict) and details.get("configured") and details.get("ready") is False
]
return {"status": "degraded" if configured_failures else "ok", "integrations": state}
```

Remove the old `config.feature_status()` call path from `app.py`; keep a compatibility wrapper in `config.py` only if another module still imports it, and make that wrapper return configuration facts without claiming runtime reachability.

- [ ] **Step 5: Correct provider identity and environment documentation**

Change both Nominatim and MET Norway defaults in `config.py` to `https://github.com/satyamarora26/AnnaData`. Add `METNO_USER_AGENT`, `RAG_MIN_SIMILARITY=0.70`, `GOV_API_TIMEOUT=12`, `GOV_API_DEADLINE=20`, and `WEATHER_CACHE_TTL=3600` to `.env.example`. Keep all secret values empty.

- [ ] **Step 6: Run readiness and full offline tests**

Run: `cd AnnaData-Backend-main && python -m pytest tests -q && python eval/test_feedback.py`

Expected: all pytest tests pass and the feedback script exits zero.

- [ ] **Step 7: Commit readiness reporting**

```bash
git add AnnaData-Backend-main/readiness.py AnnaData-Backend-main/app.py AnnaData-Backend-main/config.py AnnaData-Backend-main/utils.py AnnaData-Backend-main/.env.example AnnaData-Backend-main/tests/test_readiness.py
git commit -m "feat: report runtime readiness and corpus state"
```

---

### Task 8: Load, Measure, and Document the Verified Corpus

**Files:**
- Modify: `README.md:35-100,206-225`
- Modify: `ENGINEERING_NOTES.md:699-812`
- Verify: `AnnaData-Backend-main/data/source_manifest.json`
- Verify: Neon tables `documents`, `ingestion_runs`, `pesticide_uses`, and `commodity_msp`

**Interfaces:**
- Consumes: every API and CLI produced in Tasks 1-7 plus the configured ignored `.env`.
- Produces: a populated verified corpus, reproducible measured counts, a clean health report, and source-backed end-to-end evidence.

- [ ] **Step 1: Install the offline toolchain and run the complete offline suite**

Run:

```bash
cd AnnaData-Backend-main
python -m pip install -r requirements-tools.txt
python -m pytest tests -q
python eval/test_feedback.py
```

Expected: installation succeeds; all deterministic tests pass; the feedback regression exits zero.

- [ ] **Step 2: Fetch approved HTTP sources without overwriting browser sources**

Run:

```bash
cd AnnaData-Backend-main
python tools/ingest_docs.py --all --fetch --dry-run
```

Expected: the eight `http` sources download and parse; `pmfby_2023_guidelines` prints its official URL and exact local path for browser download. If an official host refuses automated access, download that exact manifest URL in the browser to the manifest's declared `local_path`, then rerun the dry run. Do not substitute mirrors or search-result copies.

- [ ] **Step 3: Ingest all document sources and prove idempotence**

Run twice:

```bash
cd AnnaData-Backend-main
python tools/ingest_docs.py --all
python tools/ingest_docs.py --all
```

Expected: the first run reports `completed` for every present source; the second reports `skipped` for unchanged hashes and creates no duplicate active chunks.

- [ ] **Step 4: Refresh all six CIB&RC categories**

Run:

```bash
cd AnnaData-Backend-main
python tools/fetch_cibrc.py --out data/cibrc
python tools/load_cibrc.py --dry-run
python tools/load_cibrc.py
```

Expected: six PDFs pass signature validation, dry-run parses non-zero uses for each category, and the load completes six audit runs. Compare the new total with the prior measured 2,456 uses; investigate any change rather than forcing the old count.

- [ ] **Step 5: Load the current reviewed MSP schedule**

Transcribe the 2026-27 crop/MSP columns from the comprehensive Press Information Bureau table at `https://www.pib.gov.in/PressReleasePage.aspx?PRID=2269182&lang=1&reg=3` into `data/downloads/msp-current.csv`, and independently compare the kharif values with CCEA release `https://www.pib.gov.in/PressReleasePage.aspx?PRID=2260618&lang=1&reg=48`. Run:

```bash
cd AnnaData-Backend-main
python tools/load_msp.py --csv data/downloads/msp-current.csv --year 2026-27 --source-url 'https://www.pib.gov.in/PressReleasePage.aspx?PRID=2269182&lang=1&reg=3'
```

Expected: the CLI reports a non-zero commodity count, crops with `-` remain absent, and `msp.for_crop("wheat")` cites marketing year `2026-27`. The CSV must retain only values visibly present in that Government of India table; do not relabel an older export or infer missing crop values.

- [ ] **Step 6: Verify database invariants directly**

Run this read-only check with the backend virtual environment:

```bash
cd AnnaData-Backend-main
python -c "import db,knowledge,msp; assert db.init(); assert knowledge.init(); print(knowledge.counts()); print(msp.counts()); print(knowledge.latest_ingestion()); db.close()"
```

Expected: `documents > 0`, `pesticide_uses > 0`, `msp_commodities > 0`, and the latest ingestion status is `completed` or `skipped`.

Run the structured and vector retrieval checks:

```bash
cd AnnaData-Backend-main
python -c "import db,knowledge; assert db.init(); known=knowledge.approved_uses('cotton','bollworm'); unknown=knowledge.approved_uses('cotton','elephant'); assert known['pest_matched'] and known['uses']; assert not unknown['pest_matched']; hits=knowledge.search('Who is eligible for PM-KISAN?',topic='scheme_subsidy'); assert hits and hits[0]['url']; print(hits[0]['title'],hits[0]['similarity']); db.close()"
```

Expected: cotton/bollworm returns one or more registered records, cotton/elephant is not a pest match, and pgvector returns a PM-KISAN passage with a canonical URL.

- [ ] **Step 7: Verify startup, health, and end-to-end routing**

Start the backend, then run:

```bash
curl -s http://127.0.0.1:8000/health
curl -s -X POST http://127.0.0.1:8000/agent -H 'Content-Type: application/json' -d '{"query":"How much fertilizer should I apply to wheat in Ludhiana?","latitude":30.9293211,"longitude":75.5004841,"channel":"web"}'
curl -s -X POST http://127.0.0.1:8000/agent -H 'Content-Type: application/json' -d '{"query":"What support is available under PM-KISAN?","channel":"web"}'
```

Expected: health distinguishes configured and ready states and reports corpus counts; fertilizer routing includes `soil`, `weather`, and `kb`; any fertilizer amount is traceable to an official or Punjab-compatible extension passage; the PM-KISAN answer names its official authority.

Run the unchanged frontend tests and send the same two questions through `http://127.0.0.1:3000`:

```bash
cd AnnaData-Frontend-main
npm test -- --watchAll=false
```

Expected: the React test suite passes; the browser renders both answers, source-aware wording is visible, and neither response contains an unsupported actionable quantity.

- [ ] **Step 8: Measure retrieval and safety without fabricating benchmarks**

Run the deterministic suite once and each live evaluation tag once with spacing:

```bash
cd AnnaData-Backend-main
python -m pytest tests -q
python eval/run.py --id unsupported_fertilizer_quantity_is_removed
python eval/run.py --id dose_registered
python eval/run.py --id kb_answers_from_the_document
python eval/run.py --id script_punjabi
python eval/run.py --id profile_supplies_the_crop
```

Expected: all five focused cases pass without exhausting a full-suite quota. Record the exact pass counts, active chunk count, pesticide-use count, MSP commodity count, and measured query latency printed by the harness. Do not extrapolate these local measurements into active-user, production-throughput, or deployment claims.

- [ ] **Step 9: Update operator documentation with measured results**

In `README.md`, add the manifest fetch/dry-run/load commands, explain `official`/`extension`/`reference`, document the detailed `/health` shape, and state that `data/downloads` is intentionally ignored. In `ENGINEERING_NOTES.md`, record the date, source hashes, actual stored counts, exact deterministic/live test results, and any official endpoint that required a browser download.

- [ ] **Step 10: Final verification and commit**

Run:

```bash
git status --short
git diff --check
cd AnnaData-Backend-main && python -m pytest tests -q && python eval/test_feedback.py
```

Expected: only intended documentation or measured-data changes remain, `git diff --check` is silent, and all offline tests pass.

Commit:

```bash
git add README.md ENGINEERING_NOTES.md
git commit -m "docs: record verified AnnaData corpus results"
```

---

## Final Acceptance Checklist

- [ ] A clean database initializes farmer, feedback, MSP, pesticide, document, vector, and ingestion-run schemas automatically at startup.
- [ ] Re-ingesting unchanged cleaned content reports `skipped` and does not call the embedding API again.
- [ ] A failed parse, embedding, or database write leaves the previous active source corpus unchanged.
- [ ] Every active document chunk records source ID, title, authority, tier, URL, publication date when available, retrieval time, SHA-256, topics, and scope.
- [ ] Punjab extension advice is not retrieved for a farmer in another state.
- [ ] Fertilizer questions invoke soil, weather, and grounded knowledge when all three are ready.
- [ ] Unsupported fertilizer, pesticide, MSP, and subsidy quantities are removed deterministically from generated answers.
- [ ] Soil pH, organic carbon, rainfall, ordinary dates, crop-stage intervals, and phone numbers survive; pesticide waiting periods require an exact structured match.
- [ ] `/health` distinguishes configured credentials, initialized clients, reachable cached providers, database connectivity, corpus counts, and latest ingestion state.
- [ ] Source downloads are ignored by Git; the catalog and all ingestion code are tracked.
- [ ] Documentation contains only observed counts and test results.

## Self-Review Record

- Spec coverage: source tiers and initial corpus map to Task 1; metadata, auditing, hashing, idempotence, and rollback map to Tasks 2-4; routing, candidate ranking, scope, tier priority, and top-five selection map to Task 5; all actionable-number rules map to Task 6; startup and readiness map to Task 7; source population and acceptance checks map to Task 8.
- Boundary check: deployment, authentication, rate limits, durable queues, Redis, SMS, WhatsApp, language migration, model fine-tuning, and additional vector databases remain outside this plan.
- Interface check: `SourceSpec` is defined once in Task 1; ingestion lifecycle names introduced in Task 2 are used unchanged in Tasks 3-4; ranked `_kb_passages` introduced in Task 5 is the evidence consumed by Task 6.
