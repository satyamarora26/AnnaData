"""
WhatsApp Cloud API channel.

A second way in, added because the SMS path depends on one handset with one SIM
and that handset stopped working. WhatsApp reaches a different farmer - one with
a smartphone - so it widens the audience rather than replacing it, and Meta
hosts the connection, so nothing here depends on a device staying awake.

Two things make it worth having beyond redundancy. Free-form replies are
permitted inside the twenty-four hour window a farmer's own message opens, which
is the regulatory problem the SMS channel cannot solve at scale. And a test
number is issued before any business verification, so the channel can be run
today without a SIM.

The farmer is the same person on both channels, so identifiers are normalised to
the same form the SMS side stores. Someone who asks over SMS and later asks on
WhatsApp keeps their district, their crops and their history.
"""

import aiohttp

import config

GRAPH = "https://graph.facebook.com"


def enabled() -> bool:
    return bool(config.WHATSAPP_TOKEN and config.WHATSAPP_PHONE_NUMBER_ID)


def normalise_id(wa_id: str | None) -> str | None:
    """WhatsApp's wa_id in the form the farmer profile is keyed on.

    Meta sends a bare international number ("917388535376"); the SMS gateway
    sends it with a plus. Without this, the same farmer would own two profiles
    and be asked their district twice.
    """
    if not wa_id:
        return None
    digits = "".join(ch for ch in str(wa_id) if ch.isdigit())
    return f"+{digits}" if digits else None


def extract(body: dict) -> dict | None:
    """The one inbound text message in a webhook payload, or None.

    Meta delivers delivery receipts, read receipts and message echoes through
    the same webhook as real messages. Only an inbound message with text is
    something to answer; everything else is acknowledged and ignored.
    """
    # The endpoint is public, so confirm this is actually WhatsApp traffic
    # before reading anything out of it.
    if body.get("object") != "whatsapp_business_account":
        return None

    try:
        change = body["entry"][0]["changes"][0]["value"]
    except (KeyError, IndexError, TypeError):
        return None

    messages = change.get("messages")
    if not messages:
        return None                      # a status callback, not a message

    message = messages[0]
    sender = normalise_id(message.get("from"))
    if not sender:
        return None

    kind = message.get("type")
    text = None
    if kind == "text":
        text = (message.get("text") or {}).get("body")
    elif kind == "button":
        text = (message.get("button") or {}).get("text")
    elif kind == "interactive":
        interactive = message.get("interactive") or {}
        for key in ("button_reply", "list_reply"):
            if key in interactive:
                text = interactive[key].get("title")
                break

    return {
        "message_id": message.get("id"),
        "sender": sender,
        "text": text,
        "type": kind,
        # Meta gives the profile name; useful for a greeting, never for identity.
        "name": (((change.get("contacts") or [{}])[0]).get("profile") or {}).get("name"),
    }


async def send(session: aiohttp.ClientSession, to: str, text: str) -> bool:
    """Send a text message. Returns whether Meta accepted it."""
    if not enabled():
        print("WhatsApp is not configured; cannot send")
        return False
    if not to or not text:
        return False

    url = (f"{GRAPH}/{config.WHATSAPP_API_VERSION}/"
           f"{config.WHATSAPP_PHONE_NUMBER_ID}/messages")
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to.lstrip("+"),
        "type": "text",
        "text": {"preview_url": False, "body": text[:4096]},
    }
    headers = {"Authorization": f"Bearer {config.WHATSAPP_TOKEN}",
               "Content-Type": "application/json"}

    try:
        async with session.post(url, json=payload, headers=headers,
                                timeout=aiohttp.ClientTimeout(total=30)) as resp:
            body = await resp.text()
            if resp.status not in (200, 201):
                # The most common failure is a farmer outside the 24-hour
                # window, which needs an approved template rather than text.
                print(f"WhatsApp send failed {resp.status}: {body[:300]}")
                return False
            return True
    except Exception as e:
        print(f"WhatsApp send error: {e}")
        return False


def verify_challenge(args) -> tuple[str, int]:
    """Answer Meta's webhook verification handshake.

    Meta calls the webhook once with a token it was given during setup and
    expects its own challenge echoed back verbatim as plain text.
    """
    mode = args.get("hub.mode")
    token = args.get("hub.verify_token")
    challenge = args.get("hub.challenge", "")

    if mode == "subscribe" and token and token == config.WHATSAPP_VERIFY_TOKEN:
        print("WhatsApp webhook verified")
        return challenge, 200
    print(f"WhatsApp webhook verification refused (mode={mode!r})")
    return "verification failed", 403
