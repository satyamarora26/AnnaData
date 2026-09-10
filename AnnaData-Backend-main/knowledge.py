"""
Grounded knowledge: approved pesticide doses, and retrieved reference text.

Two stores, because the two problems are not alike.

`pesticide_uses` is a structured table looked up on crop and pest. A dose is a
precise fact with a right answer, and semantic similarity is the wrong tool for
it - retrieving prose that mentions a chemical does not tell you the approved
rate for this crop and this pest. Matching on the pair does, and it also lets
the agent do the thing that matters most: say nothing when the pair is not
registered, rather than produce a plausible number.

`documents` is a pgvector store for the open-ended material - schemes,
subsidies, storage, general practice - where there is no single right answer and
similarity is exactly right.

Both live in the Postgres already provisioned for farmer profiles, so this adds
no infrastructure and no cost.
"""
import json
import math
import re
import time
import urllib.error
import urllib.request
from dataclasses import asdict
from urllib.parse import urlsplit

import db
from config import GEMINI_API_KEY, RAG_MIN_SIMILARITY
from source_catalog import SourceSpec, scope_score

EMBED_MODEL = "gemini-embedding-001"
EMBED_DIM = 768
ACTIVATION_TIMEOUT_SECONDS = 60
ACTIVATION_TIMEOUT_SQLSTATES = {"57014", "25P04"}
INGESTION_LEASE_SECONDS = 300


class ActivationError(RuntimeError):
    """A document activation could not complete atomically."""


class ActivationDeadlineExceeded(ActivationError):
    """The activation deadline expired locally or in PostgreSQL."""


class ActivationDeadlineUnsupported(ActivationError):
    """The database cannot enforce a transaction-wide activation deadline."""


class ActivationStateError(ActivationError):
    """The ingestion audit is missing, terminal, or does not match the activation."""


class IngestionLeaseActive(RuntimeError):
    """Another process still owns a fresh lease for this source."""


TIER_PRIORITY = {"official": 2, "extension": 1, "reference": 0}

SCHEMA = f"""
CREATE EXTENSION IF NOT EXISTS vector;

-- Approved pesticide uses. One row per product/crop/pest combination, taken
-- from the registration authority rather than from a model.
CREATE TABLE IF NOT EXISTS pesticide_uses (
    id            BIGSERIAL PRIMARY KEY,
    category      TEXT,           -- insecticide, fungicide, herbicide, bio-*
    product       TEXT NOT NULL,  -- e.g. 'Emamectin Benzoate 5% SG'
    crop          TEXT NOT NULL,
    pest          TEXT NOT NULL,
    dose_formulation TEXT,        -- rate of the product as sold
    dose_ai       TEXT,           -- rate of active ingredient
    dilution      TEXT,           -- water volume
    waiting_period TEXT,          -- days between spraying and harvest
    source        TEXT NOT NULL,  -- which document, and as of when
    source_url    TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (product, crop, pest)
);

CREATE INDEX IF NOT EXISTS pesticide_crop_pest
    ON pesticide_uses (lower(crop), lower(pest));

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

ALTER TABLE ingestion_runs ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT now();
CREATE INDEX IF NOT EXISTS ingestion_runs_running_lease
    ON ingestion_runs (kind, source, heartbeat_at) WHERE status = 'running';

-- Reference text for open-ended questions.
CREATE TABLE IF NOT EXISTS documents (
    id          BIGSERIAL PRIMARY KEY,
    source      TEXT NOT NULL,
    title       TEXT,
    url         TEXT,
    chunk_index INTEGER NOT NULL DEFAULT 0,
    -- 'official' for government documents, 'reference' for material that is
    -- useful but not authoritative. A farmer acting on an insurance deadline
    -- deserves to know which one they were told.
    tier        TEXT NOT NULL DEFAULT 'reference',
    content     TEXT NOT NULL,
    embedding   vector({EMBED_DIM}),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS documents_embedding
    ON documents USING hnsw (embedding vector_cosine_ops);

ALTER TABLE documents ADD COLUMN IF NOT EXISTS tier TEXT NOT NULL DEFAULT 'reference';
ALTER TABLE documents ADD COLUMN IF NOT EXISTS authority TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS published_on DATE;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS retrieved_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE documents ADD COLUMN IF NOT EXISTS content_hash TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS scope JSONB NOT NULL DEFAULT '{{}}';
ALTER TABLE documents ADD COLUMN IF NOT EXISTS topics TEXT[] NOT NULL DEFAULT '{{}}';
ALTER TABLE documents ADD COLUMN IF NOT EXISTS ingestion_run_id BIGINT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS active BOOLEAN NOT NULL DEFAULT TRUE;

CREATE UNIQUE INDEX IF NOT EXISTS documents_source_hash_chunk
    ON documents (source, content_hash, chunk_index)
    WHERE content_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS documents_active_embedding
    ON documents USING hnsw (embedding vector_cosine_ops)
    WHERE active = TRUE;
"""


def init() -> bool:
    """Create the knowledge tables. Safe to call repeatedly."""
    if not db.is_available():
        return False
    try:
        with db.connection() as conn:
            conn.execute(SCHEMA)
        return True
    except Exception as e:
        print(f"Knowledge store unavailable: {e}")
        return False


def embed(text: str, dim: int = EMBED_DIM, timeout_seconds: float = 60) -> list[float] | None:
    """Embed one piece of text, or None if the call fails."""
    if not GEMINI_API_KEY or not text:
        return None
    body = json.dumps({
        "model": f"models/{EMBED_MODEL}",
        "content": {"parts": [{"text": text}]},
        "outputDimensionality": dim,
    }).encode()
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{EMBED_MODEL}:embedContent?key={GEMINI_API_KEY}")
    try:
        req = urllib.request.Request(url, data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout_seconds) as r:
            return json.load(r)["embedding"]["values"]
    except Exception as e:
        print(f"Embedding failed: {e}")
        return None


def _batch_retry_delay(error: urllib.error.HTTPError) -> float | None:
    """Use only a positive finite delay supplied by the provider."""
    retry_after = error.headers.get("Retry-After") if error.headers else None
    try:
        delay = float(retry_after)
        if math.isfinite(delay) and delay > 0:
            return delay
    except (TypeError, ValueError):
        pass

    try:
        raw = error.read(65537)
        if len(raw) > 65536:
            return None
        payload = json.loads(raw)
        info = payload.get("error") if isinstance(payload, dict) else None
        details = info.get("details") if isinstance(info, dict) else None
        if not isinstance(details, list):
            return None
        for detail in details:
            if (not isinstance(detail, dict)
                    or detail.get("@type") != "type.googleapis.com/google.rpc.RetryInfo"):
                continue
            duration = detail.get("retryDelay")
            if not isinstance(duration, str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]{1,9})?s", duration):
                continue
            delay = float(duration[:-1])
            if math.isfinite(delay) and delay > 0:
                return delay
    except Exception:
        # Unreadable or malformed provider metadata must not escape or be logged.
        return None
    return None


def embed_batch(
    texts: list[str], dim: int = EMBED_DIM, timeout_seconds: float = 60,
    sleep=time.sleep,
) -> list[list[float]] | None:
    """Embed an ordered batch through Gemini's documented batch endpoint."""
    if not GEMINI_API_KEY or not texts or timeout_seconds <= 0:
        return None
    requests = [
        {
            "model": f"models/{EMBED_MODEL}",
            "content": {"parts": [{"text": text}]},
            "outputDimensionality": dim,
        }
        for text in texts
    ]
    body = json.dumps({"requests": requests}).encode()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{EMBED_MODEL}:batchEmbedContents"
    started_at = time.monotonic()
    for attempt in range(2):
        remaining = timeout_seconds - (time.monotonic() - started_at)
        if remaining <= 0:
            print("Batch embedding failed: ingestion deadline exceeded")
            return None
        try:
            req = urllib.request.Request(
                url,
                data=body,
                headers={"Content-Type": "application/json", "x-goog-api-key": GEMINI_API_KEY},
            )
            with urllib.request.urlopen(req, timeout=remaining) as response:
                embeddings = json.load(response).get("embeddings")
            if not isinstance(embeddings, list) or len(embeddings) != len(texts):
                raise ValueError("batch embedding response count did not match request count")
            values = [item.get("values") if isinstance(item, dict) else None for item in embeddings]
            if any(not isinstance(vector, list) or len(vector) != dim for vector in values):
                raise ValueError("batch embedding response had an unexpected dimension")
            return values
        except urllib.error.HTTPError as exc:
            with exc:
                delay = _batch_retry_delay(exc) if exc.code == 429 and not attempt else None
            remaining = timeout_seconds - (time.monotonic() - started_at)
            if delay is None or delay >= remaining:
                print(f"Batch embedding failed: HTTP {exc.code}")
                return None
            sleep(delay)
        except Exception:
            print("Batch embedding failed: invalid response or transport error")
            return None
    return None


# The register writes a crop one way; farmers write it several others. These
# are the names that actually turn up in messages, including the transliterated
# ones - a farmer typing "kapas" or "dhaan" must reach the same rows as one
# typing the English name.
CROP_SYNONYMS = {
    "paddy": "rice", "dhaan": "rice", "dhan": "rice", "chawal": "rice",
    "kapas": "cotton", "rui": "cotton",
    "gehu": "wheat", "gehun": "wheat", "kanak": "wheat",
    "bhindi": "okra", "ladyfinger": "okra", "lady finger": "okra",
    "baingan": "brinjal", "eggplant": "brinjal", "aubergine": "brinjal",
    "aloo": "potato", "batata": "potato",
    "pyaz": "onion", "kanda": "onion",
    "tamatar": "tomato",
    "mirch": "chilli", "mirchi": "chilli", "chili": "chilli", "chile": "chilli",
    "makka": "maize", "corn": "maize", "bhutta": "maize",
    "ganna": "sugarcane", "genna": "sugarcane",
    "soyabean": "soybean", "soya": "soybean",
    "moongphali": "groundnut", "peanut": "groundnut", "mungfali": "groundnut",
    "sarson": "mustard", "rai": "mustard",
    "chana": "chickpea", "gram": "chickpea",
    "arhar": "pigeonpea", "tur": "pigeonpea", "toor": "pigeonpea",
    "haldi": "turmeric",
}


def canonical_crop(crop: str | None) -> str | None:
    if not crop:
        return None
    key = crop.strip().lower()
    return CROP_SYNONYMS.get(key, key)


# What a farmer calls a pest, mapped to the words the register uses.
#
# Measured against the loaded register, 13 of 14 common vernacular pest names
# matched nothing - "kapas me sundi" returned no approved use while eight sat in
# the table under "Bollworm". The refusals were a vocabulary gap, not a coverage
# gap, which is a far better problem to have and a much cheaper one to fix.
#
# Each term maps to several register fragments rather than one name, because the
# vernacular is broader than the register's vocabulary and the crop already
# narrows it. "sundi" is any caterpillar or borer: in cotton that reaches
# bollworm, in rice stem borer, in gram pod borer. Letting the crop disambiguate
# is more honest than pretending one Hindi word names one species.
PEST_SYNONYMS = {
    # caterpillars and borers
    "sundi": ["worm", "borer", "caterpillar"],
    "sundhi": ["worm", "borer", "caterpillar"],
    "illi": ["worm", "borer", "caterpillar"],
    "lat": ["worm", "caterpillar", "larva"],
    "lal sundi": ["pink bollworm"],
    "gulabi sundi": ["pink bollworm"],
    "american sundi": ["helicoverpa", "american bollworm"],
    "tana chhedak": ["stem borer", "borer"],
    "tana bhedak": ["stem borer", "borer"],
    "phal chhedak": ["fruit borer", "borer"],
    "katua": ["cutworm"],
    "kambli puzhu": ["hairy caterpillar", "caterpillar"],
    # sucking pests
    "safed makkhi": ["whitefly", "white fly"],
    "makkhi": ["fly"],
    "mahu": ["aphid"],
    "mahoo": ["aphid"],
    "chepa": ["aphid"],
    "lahi": ["aphid"],
    "tela": ["jassid", "hopper"],
    "hara tela": ["jassid", "hopper"],
    "tudtuda": ["jassid", "hopper"],
    "fudka": ["planthopper", "hopper"],
    "bhura fudka": ["brown planthopper", "planthopper"],
    "makdi": ["mite"],
    "lal makdi": ["mite"],
    "thrips": ["thrips"],
    "deemak": ["termite"],
    "dimak": ["termite"],
    # diseases
    "gerua": ["rust"],
    "gerui": ["rust"],
    "ratua": ["rust"],
    "kungi": ["rust"],
    "jhulsa": ["blight"],
    "angmari": ["blight"],
    "ang mari": ["blight"],
    "kandua": ["smut"],
    "galan": ["rot"],
    "sadan": ["rot"],
    "murjhan": ["wilt"],
    "ukhta": ["wilt"],
    "chepki": ["scale"],
    "phaphundi": ["mildew", "mould"],
    "bhura dhabba": ["brown spot"],
}

# Words that name no particular pest. Resolving these to a specific one would be
# guessing at which chemical to recommend, so they deliberately match nothing -
# the answer then asks the farmer what they are actually seeing.
GENERIC_PEST_WORDS = {
    "keeda", "keede", "kida", "kide", "insect", "insects", "pest", "pests",
    "bimari", "beemari", "rog", "disease", "problem", "damage", "kuch",
}


def pest_terms(pest: str | None) -> list[str]:
    """The register fragments to search for, given what the farmer called it.

    Returns an empty list for a word that names no particular pest, so a
    generic complaint cannot be silently resolved into a specific chemical.
    """
    if not pest:
        return []
    key = pest.strip().lower()
    if key in GENERIC_PEST_WORDS:
        return []
    if key in PEST_SYNONYMS:
        return PEST_SYNONYMS[key]
    # A phrase may carry a known term inside it: "safed makkhi ka prakop".
    for term, targets in PEST_SYNONYMS.items():
        if re.search(r"\b" + re.escape(term) + r"\b", key):
            return targets
    return [key]


# --- approved doses ---------------------------------------------------------

def approved_uses(crop: str | None, pest: str | None = None, limit: int = 8) -> dict:
    """Registered product uses for a crop, optionally narrowed to a pest.

    Matching is deliberately loose on the pest - farmers say "sundi" and
    "bollworm" and "इल्ली" for the same insect - but never loose enough to
    return a use for a different crop.
    """
    if not crop or not db.is_available():
        return {"uses": [], "pest_matched": False}
    try:
        with db.connection() as conn:
            # Crop matching stays tight - a use for one crop must never be
            # returned for another - but tolerates how the register writes it:
            # "Rice (Paddy)" has to be reachable from both "rice" and "paddy".
            crop_match = """(
                    lower(crop) = %(crop)s
                 OR lower(crop) LIKE %(crop)s || ' (%%'
                 OR lower(crop) LIKE '%%(' || %(crop)s || ')%%'
                 OR lower(crop) LIKE %(crop)s || ',%%'
            )"""
            params = {"crop": canonical_crop(crop), "limit": limit,
                      "pest_like": f"%{(pest or '').lower()}%", "pest": (pest or "").lower()}

            # What the farmer called it, expanded into the register's own
            # vocabulary. A generic complaint expands to nothing and falls
            # through to the crop-level path, which carries the warning that
            # these uses are for other pests.
            terms = pest_terms(pest)
            if terms:
                params["terms"] = [f"%{t}%" for t in terms]
                rows = conn.execute(
                    f"""
                    SELECT product, crop, pest, dose_formulation, dose_ai,
                           dilution, waiting_period, source
                      FROM pesticide_uses
                     WHERE {crop_match}
                       AND (lower(pest) LIKE ANY(%(terms)s)
                            OR %(pest)s LIKE '%%' || lower(pest) || '%%')
                     LIMIT %(limit)s
                    """,
                    params,
                ).fetchall()
                if rows:
                    return {"uses": _rows_to_dicts(rows), "pest_matched": True}
            rows = conn.execute(
                f"""
                SELECT product, crop, pest, dose_formulation, dose_ai,
                       dilution, waiting_period, source
                  FROM pesticide_uses
                 WHERE {crop_match}
                 LIMIT %(limit)s
                """,
                params,
            ).fetchall()
            # Nothing registered for this pest. What is registered for the crop
            # is still worth showing, but it must never be presented as an
            # answer for the pest asked about - a bollworm product offered for
            # locusts is exactly the error this table exists to prevent.
            return {"uses": _rows_to_dicts(rows), "pest_matched": False}
    except Exception as e:
        print(f"Approved use lookup failed for crop={crop!r}: {e}")
        return {"uses": [], "pest_matched": False}


def _rows_to_dicts(rows) -> list[dict]:
    keys = ("product", "crop", "pest", "dose_formulation", "dose_ai",
            "dilution", "waiting_period", "source")
    return [dict(zip(keys, r)) for r in rows]


def format_uses(result: dict, pest: str | None = None) -> str:
    """Render approved uses for the prompt, or say plainly that none are known."""
    uses = result.get("uses") or []
    if not uses:
        return ("No registered pesticide use is on file for this crop and pest. "
                "Do NOT state any product name or dose. Tell the farmer you "
                "cannot recommend a specific chemical and refer them to their "
                "local Krishi Vigyan Kendra or agriculture officer.")

    if not result.get("pest_matched"):
        target = f"'{pest}'" if pest else "the pest asked about"
        lines = [
            f"WARNING: nothing is registered for {target} on this crop. "
            "The uses below are registered for DIFFERENT pests and must NOT be "
            "recommended for the one asked about. Say plainly that you have no "
            "approved treatment for this pest and refer the farmer to their "
            "Krishi Vigyan Kendra. The list is context only:",
        ]
    else:
        lines = ["Registered pesticide uses (these are the ONLY doses you may quote):"]
    for u in uses:
        bits = [f"{u['product']} for {u['pest']} on {u['crop']}"]
        if u.get("dose_formulation"):
            bits.append(f"dose {u['dose_formulation']}")
        if u.get("dilution"):
            bits.append(f"in {u['dilution']}")
        if u.get("waiting_period"):
            bits.append(f"wait {u['waiting_period']} before harvest")
        lines.append(f"- {', '.join(bits)} [{u['source']}]")
    return "\n".join(lines)


# --- reference text ---------------------------------------------------------

def start_ingestion(kind: str, source: str, source_url: str, content_hash: str,
                    skip_completed: bool = True,
                    lease_seconds: int = INGESTION_LEASE_SECONDS) -> int | None:
    """Create an auditable ingestion run unless this content is already live."""
    if not db.is_available():
        raise RuntimeError("database unavailable during ingestion")
    with db.connection() as conn:
        locked = conn.execute(
            "SELECT pg_try_advisory_xact_lock(hashtext(%s))",
            (source,),
        ).fetchone()
        if not locked or not locked[0]:
            raise IngestionLeaseActive(f"ingestion is already running for {source}")

        stale_rows = conn.execute(
            """UPDATE ingestion_runs
                  SET status = 'failed', error = 'stale ingestion lease expired',
                      completed_at = now()
                WHERE kind = %s AND source = %s AND status = 'running'
                  AND heartbeat_at < now() - (%s * interval '1 second')
            RETURNING id""",
            (kind, source, max(1, int(lease_seconds))),
        ).fetchall()
        stale_ids = [row[0] for row in stale_rows]
        if stale_ids:
            conn.execute(
                """DELETE FROM documents
                     WHERE ingestion_run_id = ANY(%s) AND active = FALSE""",
                (stale_ids,),
            )

        live = conn.execute(
            """SELECT 1 FROM ingestion_runs
                 WHERE kind = %s AND source = %s AND status = 'running'
                 LIMIT 1""",
            (kind, source),
        ).fetchone()
        if live:
            raise IngestionLeaseActive(f"ingestion is already running for {source}")

        if not skip_completed:
            prior = None
        elif kind == "document":
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


def _renew_ingestion_lease(conn, run_id: int, source: str) -> bool:
    return conn.execute(
        """UPDATE ingestion_runs SET heartbeat_at = now()
             WHERE id = %s AND source = %s AND status = 'running'
         RETURNING id""",
        (run_id, source),
    ).fetchone() is not None


def renew_ingestion_lease(run_id: int, source: str) -> bool:
    """Renew a live source lease before a bounded unit of expensive work."""
    if not db.is_available():
        return False
    with db.connection() as conn:
        return _renew_ingestion_lease(conn, run_id, source)


def stage_document(run_id: int, spec: SourceSpec, content_hash: str,
                   content: str, chunk_index: int,
                   embedding: list[float]) -> bool:
    """Write an inactive document chunk that can be published as a complete set."""
    try:
        with db.connection() as conn:
            if not _renew_ingestion_lease(conn, run_id, spec.id):
                return False
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


def stage_documents(run_id: int, spec: SourceSpec, content_hash: str,
                    contents: list[str], start_index: int,
                    embeddings: list[list[float]], *, deadline_at: float,
                    clock=time.monotonic) -> int:
    """Stage one bounded embedding batch in a single inactive transaction."""
    from psycopg import sql

    if not math.isfinite(deadline_at):
        raise ValueError("staging requires a finite deadline")
    if not contents or len(contents) != len(embeddings) or len(contents) > 100:
        raise ValueError("staging requires 1-100 matching chunks and embeddings")

    def remaining_ms():
        value = int((deadline_at - clock()) * 1000)
        if value < 1:
            raise TimeoutError("ingestion deadline exceeded")
        return value

    rows = []
    for index, (content, vector) in enumerate(zip(contents, embeddings), start=start_index):
        rows.extend((spec.id, spec.title, spec.source_url, spec.authority, spec.tier,
                     spec.published_on, json.dumps(asdict(spec)["scope"]),
                     list(spec.topics), content_hash, run_id, index, content, str(vector)))
    values = sql.SQL(
        "(%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,FALSE,%s,%s,%s::vector)"
    )
    statement = sql.SQL("""INSERT INTO documents
        (source,title,url,authority,tier,published_on,scope,topics,content_hash,
         ingestion_run_id,active,chunk_index,content,embedding) VALUES {}
    """).format(sql.SQL(",").join([values] * len(contents)))
    try:
        with db.connection() as conn:
            def bounded_execute(query, params=()):
                conn.execute("SELECT set_config('statement_timeout', %s, true)",
                             (f"{remaining_ms()}ms",))
                return conn.execute(query, params)

            lease = bounded_execute(
                """UPDATE ingestion_runs SET heartbeat_at=now()
                   WHERE id=%s AND source=%s AND content_hash=%s AND status='running'
                   RETURNING id""", (run_id, spec.id, content_hash)
            ).fetchone()
            if lease is None:
                raise IngestionLeaseActive("ingestion lease is no longer active")
            bounded_execute(statement, tuple(rows))
            conn.execute("SELECT set_config('statement_timeout', %s, true)",
                         (f"{remaining_ms()}ms",))
            remaining_ms()
            conn.commit()
        return len(contents)
    except Exception as exc:
        if getattr(exc, "sqlstate", None) in ACTIVATION_TIMEOUT_SQLSTATES:
            raise TimeoutError("ingestion deadline exceeded") from exc
        raise


def activate_documents(run_id: int, spec: SourceSpec, content_hash: str,
                       parsed: int, stored: int, rejected: int,
                       deadline_at: float | None = None,
                       timeout_seconds: float | None = ACTIVATION_TIMEOUT_SECONDS,
                       clock=time.monotonic) -> bool:
    """Atomically publish a complete source under a PostgreSQL 16 deadline.

    Every statement receives the aggregate budget remaining at dispatch time,
    including COMMIT. PostgreSQL 16 has no transaction-wide timeout, so a lost
    connection during COMMIT can leave its outcome unknown to this process; the
    audit row and document activation are committed together and callers must
    treat a terminal completed audit as authoritative during reconciliation.
    """
    if deadline_at is None:
        if timeout_seconds is None:
            raise ActivationDeadlineUnsupported("document activation requires a deadline")
        deadline_at = clock() + timeout_seconds

    def remaining_milliseconds() -> int:
        milliseconds = int((deadline_at - clock()) * 1000)
        if milliseconds < 1:
            raise ActivationDeadlineExceeded("ingestion deadline exceeded during activation")
        return milliseconds

    try:
        with db.connection() as conn:
            def set_statement_timeout() -> None:
                conn.execute(
                    "SELECT set_config('statement_timeout', %s, true)",
                    (f"{remaining_milliseconds()}ms",),
                )

            def execute(sql: str, params=()):
                set_statement_timeout()
                return conn.execute(sql, params)

            def commit() -> None:
                set_statement_timeout()
                remaining_milliseconds()
                conn.commit()

            execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                (spec.id,),
            )
            audit = execute(
                """SELECT source, content_hash, status, parsed_count, stored_count,
                          rejected_count
                     FROM ingestion_runs WHERE id = %s FOR UPDATE""",
                (run_id,),
            ).fetchone()
            if audit is None:
                raise ActivationStateError(f"ingestion audit {run_id} was not found")
            (audit_source, audit_hash, audit_status, audit_parsed, audit_stored,
             audit_rejected) = audit
            if audit_status == "completed":
                if (audit_source != spec.id or audit_hash != content_hash
                        or audit_parsed != parsed or audit_stored != stored
                        or audit_rejected != rejected or parsed <= 0
                        or stored != parsed or rejected):
                    raise ActivationStateError(
                        f"completed ingestion audit {run_id} does not match activation"
                    )
                active_count, owned_active_count = execute(
                    """SELECT count(*) AS active_count,
                              count(*) FILTER (
                                  WHERE ingestion_run_id = %s AND source = %s
                                    AND content_hash = %s
                              ) AS owned_active_count
                         FROM documents
                        WHERE source = %s AND active = TRUE""",
                    (run_id, spec.id, content_hash, spec.id),
                ).fetchone()
                if active_count != stored or owned_active_count != stored:
                    raise ActivationStateError(
                        f"completed ingestion audit {run_id} does not own the active corpus"
                    )
                commit()
                return True
            if audit_status != "running":
                raise ActivationStateError(
                    f"ingestion audit {run_id} is already {audit_status}"
                )
            if audit_source != spec.id or audit_hash != content_hash:
                raise ActivationStateError(
                    f"running ingestion audit {run_id} does not match activation"
                )
            if parsed <= 0 or stored != parsed or rejected:
                raise ActivationError("replacement corpus was incomplete")
            staged_count, owned_count = execute(
                """SELECT count(*) AS staged_count,
                          count(*) FILTER (
                              WHERE source = %s AND content_hash = %s AND active = FALSE
                          ) AS owned_count
                     FROM documents
                    WHERE ingestion_run_id = %s""",
                (spec.id, content_hash, run_id),
            ).fetchone()
            if staged_count != parsed or owned_count != parsed:
                raise ActivationError("staged document ownership/count mismatch")
            execute(
                "UPDATE documents SET active = FALSE WHERE source = %s AND active = TRUE",
                (spec.id,),
            )
            execute(
                """UPDATE documents SET active = TRUE
                     WHERE ingestion_run_id = %s AND source = %s
                       AND content_hash = %s AND active = FALSE""",
                (run_id, spec.id, content_hash),
            )
            execute(
                """DELETE FROM documents AS stale
                     WHERE stale.source = %s AND stale.active = FALSE
                       AND (stale.ingestion_run_id IS NULL OR stale.ingestion_run_id <> %s)
                       AND NOT EXISTS (
                           SELECT 1 FROM ingestion_runs AS stage
                            WHERE stage.id = stale.ingestion_run_id
                              AND stage.status = 'running'
                       )""",
                (spec.id, run_id),
            )
            execute(
                """UPDATE ingestion_runs
                      SET status = 'completed', parsed_count = %s, stored_count = %s,
                          rejected_count = %s, completed_at = now()
                    WHERE id = %s""",
                (parsed, stored, rejected, run_id),
            )
            commit()
            return True
    except Exception as exc:
        if isinstance(exc, ActivationError):
            raise
        if getattr(exc, "sqlstate", None) in ACTIVATION_TIMEOUT_SQLSTATES:
            raise ActivationDeadlineExceeded(
                "ingestion deadline exceeded during activation"
            ) from exc
        raise ActivationError(str(exc)) from exc


def fail_ingestion(run_id: int, error: str, parsed: int = 0,
                   stored: int = 0, rejected: int = 0) -> bool:
    """Discard incomplete staged chunks and preserve their audit outcome."""
    with db.connection() as conn:
        audit = conn.execute(
            "SELECT status FROM ingestion_runs WHERE id = %s FOR UPDATE",
            (run_id,),
        ).fetchone()
        if audit is None or audit[0] != "running":
            return False
        conn.execute(
            "DELETE FROM documents WHERE ingestion_run_id = %s AND active = FALSE",
            (run_id,),
        )
        conn.execute(
            """UPDATE ingestion_runs SET status = 'failed', error = %s,
                      parsed_count = %s, stored_count = %s, rejected_count = %s,
                      completed_at = now() WHERE id = %s""",
            (error[:2000], parsed, stored, rejected, run_id),
        )
    return True


def record_ingestion_failure(kind: str, source: str, source_url: str,
                             error: str, content_hash: str | None = None) -> None:
    """Record an ingestion failure that occurred before a run could start."""
    if not db.is_available():
        return
    with db.connection() as conn:
        conn.execute(
            """INSERT INTO ingestion_runs
                   (kind, source, source_url, content_hash, status, error, completed_at)
                 VALUES (%s, %s, %s, %s, 'failed', %s, now())""",
            (kind, source, source_url, content_hash, error[:2000]),
        )


def recent_ingestions(limit: int = 5) -> list[dict]:
    """Return a bounded newest-first view of terminal ingestion audit rows."""
    if not db.is_available():
        return []
    limit = max(1, min(limit, 100))
    try:
        with db.connection() as conn:
            rows = conn.execute(
                """SELECT id, kind, source, status, parsed_count, stored_count,
                          rejected_count, error, completed_at
                     FROM ingestion_runs
                    WHERE status IN ('completed', 'failed', 'skipped')
                 ORDER BY completed_at DESC, id DESC
                    LIMIT %s""",
                (limit,),
            ).fetchall()
    except Exception as exc:
        print(f"Could not read ingestion audit: {exc}")
        return []
    keys = ("id", "kind", "source", "status", "parsed_count", "stored_count",
            "rejected_count", "error", "completed_at")
    return [dict(zip(keys, row)) for row in rows]


def latest_ingestion() -> dict | None:
    """Return the newest terminal ingestion audit row, if one exists."""
    rows = recent_ingestions(1)
    return rows[0] if rows else None

def rank_passages(passages: list[dict], state: str | None, crop: str | None,
                  limit: int = 5, min_similarity: float | None = None) -> list[dict]:
    """Keep only relevant, in-scope evidence and order it deterministically."""
    threshold = RAG_MIN_SIMILARITY if min_similarity is None else min_similarity
    limit = _bounded_int(limit, default=5, minimum=0, maximum=5)
    ranked = []
    for passage in passages:
        prepared = _prepared_passage(passage)
        if prepared is None:
            continue
        try:
            similarity = float(prepared.get("similarity", 0))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(similarity) or similarity < threshold:
            continue
        match = scope_score(prepared["scope"], state, crop)
        if match is None:
            continue
        ranked.append((match, TIER_PRIORITY[prepared["tier"]], similarity, prepared))
    ranked.sort(key=lambda item: (-item[0], -item[1], -item[2],
                                  _metadata_sort_key(item[3])))
    return [item[3] for item in ranked[:limit]]


def _bounded_int(value, *, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(value)
    except (TypeError, ValueError, OverflowError):
        value = default
    return max(minimum, min(value, maximum))


def _scope_dict(scope) -> dict | None:
    """Decode only valid scope objects; invalid values are not unscoped evidence."""
    if scope is None:
        return {}
    if isinstance(scope, dict):
        value = scope
    elif isinstance(scope, str):
        try:
            value = json.loads(scope)
        except json.JSONDecodeError:
            return None
    else:
        return None
    if not isinstance(value, dict):
        return None
    for key in ("states", "crops"):
        declared = value.get(key)
        if declared is not None and (
            not isinstance(declared, (list, tuple))
            or any(not isinstance(item, str) or not item.strip() for item in declared)
        ):
            return None
    return value


def _clean_citation_text(value) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.replace("|", "/").replace("[", "(").replace("]", ")").split())
    return text or None


def _clean_citation_url(value) -> str | None:
    if not isinstance(value, str):
        return None
    url = "".join(value.split()).replace("|", "%7C").replace("[", "%5B").replace("]", "%5D")
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    if parsed.scheme.casefold() != "https" or not parsed.netloc:
        return None
    return url


def _prepared_passage(passage) -> dict | None:
    """Return safe evidence metadata or reject a row before it reaches a prompt."""
    if not isinstance(passage, dict) or "scope" not in passage:
        return None
    scope = _scope_dict(passage["scope"])
    authority = _clean_citation_text(passage.get("authority"))
    title = _clean_citation_text(passage.get("title"))
    url = _clean_citation_url(passage.get("url"))
    tier = str(passage.get("tier") or "").casefold()
    content = passage.get("content")
    if (scope is None or not authority or not title or not url
            or tier not in TIER_PRIORITY or not isinstance(content, str) or not content.strip()):
        return None
    return {**passage, "scope": scope, "authority": authority, "title": title,
            "url": url, "tier": tier}


def _metadata_sort_key(passage: dict) -> tuple[str, ...]:
    return tuple(str(passage.get(key) or "").casefold()
                 for key in ("source", "authority", "title", "url", "content"))


def search(query: str, state: str | None = None, crop: str | None = None,
           topic: str | None = None, candidate_limit: int = 20,
           limit: int = 5) -> list[dict]:
    """Return active, scoped passages most useful for the farmer's question."""
    if not query or not db.is_available():
        return []
    vector = embed(query)
    if vector is None:
        return []
    candidate_limit = _bounded_int(candidate_limit, default=20, minimum=1, maximum=100)
    try:
        with db.connection() as conn:
            rows = conn.execute(
                """
                SELECT content, source, title, url, authority, tier, scope, topics,
                       1 - (embedding <=> %s::vector) AS similarity
                  FROM documents
                 WHERE active = TRUE AND embedding IS NOT NULL
                   AND (%s::text IS NULL OR %s = ANY(topics))
              ORDER BY embedding <=> %s::vector,
                          source ASC, authority ASC NULLS LAST, title ASC NULLS LAST,
                          url ASC NULLS LAST, content ASC, id ASC
                 LIMIT %s
                """,
                (str(vector), topic, topic, str(vector), candidate_limit),
            ).fetchall()
        passages = []
        for content, source, title, url, authority, tier, scope, topics, similarity in rows:
            decoded_scope = _scope_dict(scope)
            if decoded_scope is None:
                continue
            passages.append({
                "content": content, "source": source, "title": title, "url": url,
                "authority": authority, "tier": tier, "scope": decoded_scope,
                "topics": topics, "similarity": similarity,
            })
        return rank_passages(passages, state, crop, limit=limit)
    except Exception as e:
        print(f"Knowledge search failed: {e}")
        return []


def format_passages(passages: list[dict], min_similarity: float = None) -> str:
    """Render retrieved passages for the prompt, with their sources.

    Weak matches are dropped rather than passed along: a passage that is only
    loosely related invites the model to answer from it anyway, which is how
    retrieval turns into a more confident kind of guess.
    """
    threshold = RAG_MIN_SIMILARITY if min_similarity is None else min_similarity
    useful = []
    for passage in passages:
        prepared = _prepared_passage(passage)
        if prepared is None:
            continue
        try:
            similarity = float(prepared.get("similarity", 0))
        except (TypeError, ValueError):
            continue
        if math.isfinite(similarity) and similarity >= threshold:
            useful.append(prepared)
    useful = useful[:5]
    if not useful:
        # Refusing without saying what IS available is the unhelpful half of
        # honesty. A farmer asking generally about "welfare schemes" matches
        # nothing specific, and telling them there is no scheme information -
        # while holding documents on four schemes - is simply wrong.
        covered = covered_topics()
        offer = ""
        if covered:
            offer = (" You DO hold information on: " + ", ".join(covered) +
                     ". Name these and ask which one they want, rather than "
                     "saying you have nothing.")
        return ("The passages retrieved do not answer this specific question." +
                offer +
                " Do NOT invent a scheme name, an amount, an eligibility rule "
                "or a website from memory. Where you genuinely have nothing "
                "relevant, say so and point the farmer to their agriculture "
                "office.")

    lines = [
        "Reference material. Read it before answering, and note that similarity "
        "search returns the closest passages whether or not they are relevant.",
        "If NONE of the passages below actually addresses what the farmer asked, "
        "say you have no information on it and point them to their agriculture "
        "office - do NOT stretch a passage about a different scheme to fit, and "
        "do NOT fall back on a scheme name from memory.",
        "",
    ]
    for p in useful:
        text = " ".join(p["content"].split())
        lines.append(f"- [{p['tier'].upper()} | {p['authority']} | {p['title']} | {p['url']}] {text}")

    lines.append(
        "\nUse a passage only when its declared state and crop scope applies. "
        "A matching scoped EXTENSION passage can be more useful than unscoped "
        "central material for local practice. Among equally applicable passages, "
        "prefer OFFICIAL material. REFERENCE material never authorizes a scheme "
        "amount, deadline, MSP, or fertiliser quantity."
    )
    return "\n".join(lines)


def covered_topics() -> list[str]:
    """The subjects the document store actually covers, for offering them."""
    if not db.is_available():
        return []
    try:
        with db.connection() as conn:
            rows = conn.execute(
                "SELECT DISTINCT title, source FROM documents WHERE active = TRUE"
            ).fetchall()
    except Exception:
        return []

    labels = set()
    for title, source in rows:
        text = f"{title or ''} {source or ''}".lower()
        for key, label in (
            ("kisan samman", "PM-KISAN (income support)"),
            ("pm-kisan", "PM-KISAN (income support)"),
            ("fasal bima", "Pradhan Mantri Fasal Bima Yojana (crop insurance)"),
            ("credit card", "Kisan Credit Card (farm credit)"),
            ("soil health", "Soil Health Card"),
        ):
            if key in text:
                labels.add(label)
    return sorted(labels)


def documents_loaded() -> bool:
    """Whether there is anything to retrieve at all."""
    return counts().get("documents", 0) > 0


def add_document(source: str, content: str, title: str = None,
                 url: str = None, chunk_index: int = 0,
                 tier: str = "reference") -> bool:
    """Store one passage with its embedding."""
    if not db.is_available() or not content.strip():
        return False
    vector = embed(content)
    if vector is None:
        return False
    try:
        with db.connection() as conn:
            conn.execute(
                """
                INSERT INTO documents (source, title, url, chunk_index, tier,
                                       content, embedding)
                VALUES (%s, %s, %s, %s, %s, %s, %s::vector)
                """,
                (source, title, url, chunk_index, tier, content, str(vector)),
            )
        return True
    except Exception as e:
        print(f"Could not store document: {e}")
        return False


def counts() -> dict:
    """How much grounded knowledge is actually loaded."""
    if not db.is_available():
        return {"pesticide_uses": 0, "documents": 0}
    try:
        with db.connection() as conn:
            uses = conn.execute("SELECT count(*) FROM pesticide_uses").fetchone()[0]
            docs = conn.execute(
                "SELECT count(*) FROM documents WHERE active = TRUE"
            ).fetchone()[0]
        return {"pesticide_uses": uses, "documents": docs}
    except Exception:
        return {"pesticide_uses": 0, "documents": 0}
