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
import re
import urllib.request

import db
from config import GEMINI_API_KEY, RAG_MIN_SIMILARITY

EMBED_MODEL = "gemini-embedding-001"
EMBED_DIM = 768

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


def embed(text: str, dim: int = EMBED_DIM) -> list[float] | None:
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
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)["embedding"]["values"]
    except Exception as e:
        print(f"Embedding failed: {e}")
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

def search(query: str, limit: int = 5) -> list[dict]:
    """Passages most similar to the query, with their sources."""
    if not query or not db.is_available():
        return []
    vector = embed(query)
    if vector is None:
        return []
    try:
        with db.connection() as conn:
            rows = conn.execute(
                """
                SELECT content, source, title, url, tier,
                       1 - (embedding <=> %s::vector) AS similarity
                  FROM documents
                 WHERE embedding IS NOT NULL
              ORDER BY embedding <=> %s::vector
                 LIMIT %s
                """,
                (str(vector), str(vector), limit),
            ).fetchall()
        return [
            {"content": c, "source": s, "title": t, "url": u,
             "tier": tier, "similarity": sim}
            for c, s, t, u, tier, sim in rows
        ]
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
    useful = [p for p in passages if p.get("similarity", 0) >= threshold]
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

    official = [p for p in useful if p.get("tier") == "official"]

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
        mark = "OFFICIAL" if p.get("tier") == "official" else "NOT OFFICIAL"
        lines.append(f"- [{mark} | {p['source']}] {text}")

    if official:
        lines.append(
            "\nWhere sources disagree, prefer the OFFICIAL ones."
        )
    else:
        lines.append(
            "\nNone of the above is an official government document. You may "
            "answer from it, but say the details are indicative and tell the "
            "farmer to confirm with their agriculture office or the scheme's "
            "official portal before acting on a date, an amount or an "
            "eligibility rule."
        )
    return "\n".join(lines)


def covered_topics() -> list[str]:
    """The subjects the document store actually covers, for offering them."""
    if not db.is_available():
        return []
    try:
        with db.connection() as conn:
            rows = conn.execute("SELECT DISTINCT title, source FROM documents").fetchall()
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
            docs = conn.execute("SELECT count(*) FROM documents").fetchone()[0]
        return {"pesticide_uses": uses, "documents": docs}
    except Exception:
        return {"pesticide_uses": 0, "documents": 0}
