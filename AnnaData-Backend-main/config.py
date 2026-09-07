"""
Central configuration and feature detection.

Every external integration is optional except Gemini. If a key is missing the
corresponding tool reports itself unavailable and the agent routes around it,
rather than raising at import time and taking the whole service down.
"""
import os
from dotenv import load_dotenv

load_dotenv()


def _get(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name, default)
    if value is not None:
        value = value.strip()
    return value or None


# --- Required ---
GEMINI_API_KEY = _get("GEMINI_API_KEY")

# --- Optional integrations ---
LOCATION_API_KEY = _get("LOCATION_API_KEY")      # Google Maps Geocoding (optional)

# Geocoding falls back to OpenStreetMap's Nominatim when no Google key is set.
# Their policy requires a User-Agent identifying the application, with a way to
# reach whoever runs it - set a real contact address before any real traffic.
NOMINATIM_URL = _get("NOMINATIM_URL", "https://nominatim.openstreetmap.org/search")
NOMINATIM_USER_AGENT = _get(
    "NOMINATIM_USER_AGENT",
    "AnnaData/1.0 (agricultural advisory for Indian farmers; +https://github.com/vibhuu22/AnnaData)",
)
# Restricts results to India so a district name cannot resolve abroad.
GEOCODE_COUNTRY = _get("GEOCODE_COUNTRY", "in")
GOV_API_KEY = _get("GOV_API_KEY")                # data.gov.in mandi prices
EE_SERVICE_KEY = _get("EE_SERVICE_KEY")          # Earth Engine soil data
# Usually read from the service account key itself; set only if the project
# registered with Earth Engine differs from the key's own project.
EE_PROJECT = _get("EE_PROJECT")
KNOWLEDGE_BASE_ID = _get("KNOWLEDGE_BASE_ID")    # AWS Bedrock knowledge base
AWS_REGION = _get("AWS_REGION", "ap-south-1")
AWS_ACCESS_KEY = _get("AWS_ACCESS_KEY")
AWS_SECRET_KEY = _get("AWS_SECRET_KEY")

# --- Storage (optional) ---
# Postgres for farmer profiles and conversation history. Without it the agent
# runs exactly as before: stateless, no memory, no personalisation.
DATABASE_URL = _get("DATABASE_URL")
DB_POOL_MAX = int(_get("DB_POOL_MAX", "4"))
# Turns of conversation replayed to the model, and how stale they may be.
# Cosine similarity a retrieved passage must reach to be shown to the model.
# Measured against the loaded corpus: questions the documents genuinely cover
# score 0.68-0.80, while questions they do not top out around 0.58. Set too
# low, loosely-related passages are passed along and the model answers from
# general knowledge anyway - retrieval then produces a more confident guess
# rather than a grounded answer.
#
# The floor is corpus dependent and will need revisiting as documents are
# added: it began at 0.65, which was fine for a single scheme, and a question
# about tractor subsidies then scored 0.667 against a PMFBY passage on Gujarat
# leaving the scheme - close enough on wording, entirely wrong on substance. A
# similarity number alone cannot tell relevance from adjacency, so the prompt
# also requires the model to check that a passage actually addresses the
# question before using it.
RAG_MIN_SIMILARITY = float(_get("RAG_MIN_SIMILARITY", "0.70"))

CONTEXT_MESSAGES = int(_get("CONTEXT_MESSAGES", "10"))
CONTEXT_TTL_HOURS = int(_get("CONTEXT_TTL_HOURS", "48"))
# How long before a farmer who ignored the location question is asked again.
LOCATION_ASK_COOLDOWN_HOURS = int(_get("LOCATION_ASK_COOLDOWN_HOURS", "24"))

# --- Feedback ---
# A conversation is considered finished after this much silence, which is also
# when it becomes fair to ask what the farmer thought of it.
SESSION_GAP_HOURS = int(_get("SESSION_GAP_HOURS", "2"))
# Each farmer is asked at most once a calendar month, on a day drawn for them
# rather than a shared one - a single date would send every reminder at once
# and put a month of SMS through one handset in an afternoon.
FEEDBACK_COOLDOWN_DAYS = int(_get("FEEDBACK_COOLDOWN_DAYS", "25"))
# How long after asking a reply still counts as a rating rather than a question.
FEEDBACK_WINDOW_HOURS = int(_get("FEEDBACK_WINDOW_HOURS", "48"))

# --- Deployment ---
FRONTEND_URL = _get("FRONTEND_URL")
CORS_ORIGINS = _get("CORS_ORIGINS")              # comma-separated, optional

# --- Models ---
# gemini-2.0-flash and gemini-2.5-* are no longer callable by new API keys; the
# API returns 404 pointing at the 3.x line. Note that models.list reports models
# the key cannot actually invoke, so verify with a real generateContent call.
#
# Comma-separated, fastest first. Each is tried in order: on a quota (429),
# overload (503) or missing-model (404) error the next one takes over. Free-tier
# quota is per model, so the fallbacks also multiply usable daily capacity.
TEXT_MODELS = [
    m.strip()
    for m in (_get("TEXT_MODELS")
              or _get("TEXT_MODEL")
              or "gemini-3.1-flash-lite,gemini-3.6-flash,gemini-3.5-flash-lite").split(",")
    if m.strip()
]
TEXT_MODEL = TEXT_MODELS[0]
MEDIA_MODEL = _get("MEDIA_MODEL", "gemini-3.5-flash-lite")

# Retries inside one model before failing over. Kept low on purpose: the client
# backs off 2s, 4s, 8s, 16s, 32s by default, which spends a minute stalling on a
# quota error when another model would have answered immediately.
LLM_MAX_RETRIES = int(_get("LLM_MAX_RETRIES", "1"))
# The final model in the chain has no fallback behind it, so it gets a larger
# budget to ride out a transient 503.
LLM_LAST_RESORT_RETRIES = int(_get("LLM_LAST_RESORT_RETRIES", "3"))

# --- Tunables ---
MANDI_MAX_RECORDS = int(_get("MANDI_MAX_RECORDS", "80"))
HTTP_TIMEOUT = int(_get("HTTP_TIMEOUT", "20"))
# data.gov.in is markedly slower than the other upstreams and returns 502 under
# load, so it gets its own budget and is retried rather than failing the answer.
# data.gov.in answers in a second or two when it is up, and not at all when it
# is not - so a generous timeout buys nothing and costs the farmer the wait.
# At 45s x 2 attempts a market-price question sat for ninety seconds before
# reporting failure, on SMS, where nothing indicates the question was received.
GOV_API_TIMEOUT = int(_get("GOV_API_TIMEOUT", "12"))
# The whole paged fetch, not one request: the per-request timeout multiplies by
# the number of pages, so the deadline is what actually bounds the answer.
GOV_API_DEADLINE = int(_get("GOV_API_DEADLINE", "20"))
GOV_API_ATTEMPTS = int(_get("GOV_API_ATTEMPTS", "1"))
# data.gov.in goes down for long stretches. Once it has failed repeatedly there
# is no sense making every farmer wait out the timeouts to learn the same
# thing, so calls are suspended briefly and answered instantly instead.
GOV_FAILURE_THRESHOLD = int(_get("GOV_FAILURE_THRESHOLD", "2"))
GOV_CIRCUIT_COOLDOWN = int(_get("GOV_CIRCUIT_COOLDOWN", "600"))
# Open-Meteo is fast from a laptop but shared cloud egress IPs see occasional
# rate limiting, so weather retries rather than silently losing the reading.
WEATHER_TIMEOUT = int(_get("WEATHER_TIMEOUT", "25"))
WEATHER_ATTEMPTS = int(_get("WEATHER_ATTEMPTS", "3"))
# Weather is a property of a place, not of a farmer, so neighbours share a
# reading. Caching by rounded coordinate collapses a whole district's traffic
# into one upstream call and keeps us well under Open-Meteo's rate limit.
WEATHER_CACHE_TTL = int(_get("WEATHER_CACHE_TTL", "3600"))
WEATHER_CACHE_PRECISION = int(_get("WEATHER_CACHE_PRECISION", "1"))  # ~11 km
# MET Norway is the fallback when Open-Meteo rate limits us. Their terms require
# a User-Agent naming the application with a way to make contact.
METNO_USER_AGENT = _get(
    "METNO_USER_AGENT",
    "AnnaData/1.0 agricultural advisory (+https://github.com/vibhuu22/AnnaData)",
)


def _documents_loaded() -> bool:
    try:
        import knowledge
        return knowledge.documents_loaded()
    except Exception:
        return False


def _mandi_reachable() -> bool:
    """Whether market prices actually work, not merely whether a key is set.

    /health reported mandi_prices true throughout a multi-day upstream outage,
    because it was answering a question about configuration. A health check that
    describes intent rather than reality is worse than none.
    """
    try:
        import Mandi_Price_Tool
        return Mandi_Price_Tool.is_available()
    except Exception:
        return bool(GOV_API_KEY)


def feature_status() -> dict:
    """Which integrations are configured. Surfaced on /health."""
    return {
        "gemini": bool(GEMINI_API_KEY),
        # Geocoding always works now: Nominatim needs no key.
        "geocoding": True,
        "geocoding_provider": "google" if LOCATION_API_KEY else "nominatim",
        "mandi_prices": _mandi_reachable(),
        "soil": bool(EE_SERVICE_KEY),
        # Retrieval is served from the local vector store; Bedrock is optional.
        "knowledge_base": _documents_loaded() or bool(
            KNOWLEDGE_BASE_ID and AWS_ACCESS_KEY and AWS_SECRET_KEY
        ),
        "farmer_profiles": bool(DATABASE_URL),
    }
