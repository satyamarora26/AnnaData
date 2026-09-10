"""Read-only integration readiness snapshot for startup logs and /health."""
import re

import config
import db
import knowledge
import msp
import startup
import utils
import Mandi_Price_Tool
import weather_tool


def _status_code(configured: bool, ready: bool) -> str:
    if ready:
        return "ready"
    return "unavailable" if configured else "not_configured"


def _error_code(value: object) -> str | None:
    return None if value is None else "unavailable"


def _weather_error_code(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str) and re.fullmatch(r"[a-z0-9_]{1,80}", value):
        return value
    return "unavailable"


def _recent_ingestions() -> list[dict]:
    records = []
    for record in knowledge.recent_ingestions(limit=5):
        public = dict(record)
        public["error"] = None
        source = public.get("source")
        source_id = (
            source.replace("-", "").replace("_", "").replace(".", "").replace(":", "")
            if isinstance(source, str)
            else ""
        )
        if not source_id.isalnum():
            public["source"] = "redacted"
        records.append(public)
    return records


def snapshot() -> dict:
    """Return current configuration and runtime state without probing providers."""
    client = utils.client_status()
    knowledge_loaded = knowledge.documents_loaded()
    bedrock_configured = bool(
        config.KNOWLEDGE_BASE_ID and config.AWS_ACCESS_KEY and config.AWS_SECRET_KEY
    )
    msp_counts = msp.counts()
    weather = weather_tool.status()
    weather_state = weather.get("state")
    weather_provider = weather.get("provider")
    if weather_state not in {"unknown", "ready", "degraded", "unavailable"}:
        weather_state = "unavailable"
    if weather_provider not in {None, "open-meteo", "met-norway"}:
        weather_provider = None
    last_success = weather.get("last_successful_result")
    if not isinstance(last_success, dict):
        last_success = None
    elif last_success.get("provider") not in {"open-meteo", "met-norway"}:
        last_success = None
    else:
        last_success = {
            "provider": last_success["provider"],
            "at": last_success.get("at"),
        }

    return {
        "database": {
            "configured": bool(config.DATABASE_URL),
            "ready": db.is_available(),
            "status": _status_code(bool(config.DATABASE_URL), db.is_available()),
        },
        "earth_engine": {
            "configured": bool(config.EE_SERVICE_KEY),
            "ready": startup.is_available(),
            "status": _status_code(bool(config.EE_SERVICE_KEY), startup.is_available()),
        },
        "gemini": {
            "configured": bool(config.GEMINI_API_KEY),
            "ready": client["initialized"],
            "client_initialized": client["initialized"],
            "models": list(config.TEXT_MODELS),
        },
        "mandi": {
            "configured": bool(config.GOV_API_KEY),
            "ready": Mandi_Price_Tool.is_available(),
            "last_error": _error_code(Mandi_Price_Tool.last_error()),
        },
        "weather": {
            "ready": weather_state in {"ready", "degraded"},
            "state": weather_state,
            "provider": weather_provider,
            "last_error": _weather_error_code(weather.get("last_error")),
            "last_successful_result": last_success,
        },
        "knowledge": {
            "ready": knowledge_loaded or bedrock_configured,
            "provider": (
                "pgvector" if knowledge_loaded else "bedrock" if bedrock_configured else None
            ),
            **knowledge.counts(),
            "recent_ingestions": _recent_ingestions(),
        },
        "msp": {
            "ready": bool(msp_counts.get("msp_commodities")),
            **msp_counts,
        },
    }
