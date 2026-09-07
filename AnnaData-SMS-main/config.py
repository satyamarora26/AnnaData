"""
Configuration for the SMS bridge.

Supports all three SMS Gateway for Android modes:
  cloud   - api.sms-gate.app (default; no LAN or ngrok needed)
  local   - the device's own HTTP server on your LAN
  private - your own self-hosted gateway server
"""
import os
from dotenv import load_dotenv

load_dotenv()


def _get(name, default=None):
    v = os.getenv(name, default)
    return v.strip() if isinstance(v, str) else v


SMS_MODE = (_get("SMS_MODE", "cloud") or "cloud").lower()

CLOUD_BASE_URL = _get("CLOUD_BASE_URL", "https://api.sms-gate.app/3rdparty/v1")
PRIVATE_BASE_URL = _get("PRIVATE_BASE_URL")
DEVICE_IP = _get("DEVICE_IP")
DEVICE_PORT = _get("DEVICE_PORT", "8080")

USERNAME = _get("APP_USERNAME")
PASSWORD = _get("PASSWORD")

# --- WhatsApp Cloud API (optional second channel) -------------------------
# Absent credentials disable the channel and nothing else, the same way every
# other integration in this project is optional rather than fatal.
WHATSAPP_TOKEN = _get("WHATSAPP_TOKEN")
WHATSAPP_PHONE_NUMBER_ID = _get("WHATSAPP_PHONE_NUMBER_ID")
# Any string; it is echoed back to Meta during webhook verification to prove
# the endpoint belongs to whoever configured the app.
WHATSAPP_VERIFY_TOKEN = _get("WHATSAPP_VERIFY_TOKEN", "annadata-verify")
WHATSAPP_API_VERSION = _get("WHATSAPP_API_VERSION", "v23.0")


def whatsapp_enabled() -> bool:
    return bool(WHATSAPP_TOKEN and WHATSAPP_PHONE_NUMBER_ID)


_AI_ENDPOINT_RAW = _get("AI_ENDPOINT")     # e.g. https://api.example.com/agent
PUBLIC_URL = _get("PUBLIC_URL") or _get("NGROK_URL")  # where this bridge is reachable


def _normalise_endpoint(value: str | None) -> str | None:
    """Accept a bare host or a full URL and return a full /agent URL.

    Deploy dashboards hand you a hostname, not a URL, so a pasted value often
    arrives as "annadata-backend.onrender.com" with no scheme and no path.
    """
    if not value:
        return None
    value = value.strip().rstrip("/")
    if not value.startswith(("http://", "https://")):
        value = "https://" + value
    if not value.endswith("/agent"):
        value += "/agent"
    return value


AI_ENDPOINT = _normalise_endpoint(_AI_ENDPOINT_RAW)


# Asking for a rating costs a real SMS from the farmer's own SIM, so it is
# deliberately one short message and sent at most once a month.
FEEDBACK_PROMPT = _get(
    "FEEDBACK_PROMPT",
    "How helpful was AnnaData? Reply with a number from 1 to 5. "
    "Your reply helps us improve. Reply STOP to opt out.",
)
FEEDBACK_THANKS = _get(
    "FEEDBACK_THANKS",
    "Thank you. Your rating helps us give better advice.",
)


def backend_base() -> str | None:
    if not AI_ENDPOINT:
        return None
    return AI_ENDPOINT[: -len("/agent")]


def forget_url() -> str | None:
    """Backend endpoint that erases a farmer's stored data."""
    if not AI_ENDPOINT:
        return None
    return backend_base() + "/forget"

DEDUP_TTL = int(_get("DEDUP_TTL", "600"))
AI_TIMEOUT = int(_get("AI_TIMEOUT", "150"))
# Attempts at the backend per incoming SMS. The first can be spent waking a
# sleeping free-tier instance, so more than one matters in practice.
AI_ATTEMPTS = int(_get("AI_ATTEMPTS", "4"))
# Seconds to wait before retrying a backend that answered 5xx. A sleeping
# service on free hosting refuses fast rather than holding the connection, so
# retrying at once simply collects a second refusal from the same cold start;
# the wait is what lets it finish waking.
AI_RETRY_WAIT = int(_get("AI_RETRY_WAIT", "20"))
SMS_TIMEOUT = int(_get("SMS_TIMEOUT", "30"))
MAX_SMS_CHARS = int(_get("MAX_SMS_CHARS", "480"))    # hard character ceiling
# Billed segments. Latin fits 153 chars per segment, Devanagari only 67, so
# this is the cap that actually controls cost across languages.
MAX_SMS_SEGMENTS = int(_get("MAX_SMS_SEGMENTS", "4"))
WEBHOOK_PATH = _get("WEBHOOK_PATH", "/incoming-sms")

# Appended when the backend reports it still does not know where the farmer is.
# Deliberately short: it shares the same SMS segment budget as the answer.
LOCATION_PROMPT = _get(
    "LOCATION_PROMPT",
    "Reply with your district for advice specific to your area.",
)
# Asking for the one detail actually missing beats a generic prompt: a farmer
# asking about prices needs to tell us the crop, not their village.
SLOT_PROMPTS = {
    "crop": _get("PROMPT_CROP", "Reply with your crop name for specific advice."),
    "location": _get("PROMPT_LOCATION", "Reply with your district for advice specific to your area."),
    "state": _get("PROMPT_STATE", "Reply with your state so I can check local mandi prices."),
}
# Words that erase a farmer's stored profile, in the languages we have seen.
STOP_WORDS = {
    w.strip().lower()
    for w in (_get("STOP_WORDS", "stop,STOP,band,band karo,unsubscribe,hatao") or "").split(",")
    if w.strip()
}
STOP_REPLY = _get(
    "STOP_REPLY",
    "Your saved details have been deleted. Message us any time to start again.",
)


def public_url() -> str | None:
    """PUBLIC_URL with a scheme, however it was pasted."""
    if not PUBLIC_URL:
        return None
    url = PUBLIC_URL.strip().rstrip("/")
    return url if url.startswith(("http://", "https://")) else "https://" + url


def base_url() -> str:
    """Gateway API base URL for the configured mode."""
    if SMS_MODE == "cloud":
        return CLOUD_BASE_URL.rstrip("/")
    if SMS_MODE == "private":
        if not PRIVATE_BASE_URL:
            raise RuntimeError("SMS_MODE=private requires PRIVATE_BASE_URL")
        return PRIVATE_BASE_URL.rstrip("/") + "/3rdparty/v1"
    if SMS_MODE == "local":
        if not DEVICE_IP:
            raise RuntimeError("SMS_MODE=local requires DEVICE_IP")
        return f"http://{DEVICE_IP}:{DEVICE_PORT}"
    raise RuntimeError(f"Unknown SMS_MODE: {SMS_MODE!r} (use cloud, local or private)")


def messages_url() -> str:
    """Local mode exposes /message; cloud and private expose /messages."""
    return f"{base_url()}/message" if SMS_MODE == "local" else f"{base_url()}/messages"


def webhooks_url() -> str:
    return f"{base_url()}/webhooks"


def validate() -> list[str]:
    """Return a list of configuration problems, empty if healthy."""
    problems = []
    if not USERNAME or not PASSWORD:
        problems.append("APP_USERNAME / PASSWORD not set (gateway credentials)")
    if not AI_ENDPOINT:
        problems.append("AI_ENDPOINT not set (backend /agent URL)")
    try:
        base_url()
    except RuntimeError as e:
        problems.append(str(e))
    return problems
