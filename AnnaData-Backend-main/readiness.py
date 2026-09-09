"""Read-only integration readiness snapshot for startup logs and /health."""
import config
import db
import knowledge
import msp
import startup
import utils
import Mandi_Price_Tool
import weather_tool


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
            "status": db.status(),
        },
        "earth_engine": {
            "configured": bool(config.EE_SERVICE_KEY),
            "ready": startup.is_available(),
            "status": startup.status(),
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
            "last_error": Mandi_Price_Tool.last_error(),
        },
        "weather": {
            "ready": weather_tool.is_available(),
            "last_error": weather_tool.last_error(),
        },
        "knowledge": {
            "ready": knowledge_loaded or bedrock_configured,
            "provider": (
                "pgvector" if knowledge_loaded else "bedrock" if bedrock_configured else None
            ),
            **knowledge.counts(),
            "recent_ingestions": knowledge.recent_ingestions(limit=5),
        },
        "msp": {
            "ready": bool(msp_counts.get("msp_commodities")),
            **msp_counts,
        },
    }
