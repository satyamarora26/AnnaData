"""Read-only integration readiness snapshot for startup logs and /health."""
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
            "ready": weather_tool.is_available(),
            "last_error": _error_code(weather_tool.last_error()),
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
