import encoding_setup  # noqa: F401  (must be first)

import asyncio
import re
import time

import aiohttp
from quart import Quart, request, jsonify

import config
import whatsapp
from sms_text import prepare, segment_count

app = Quart(__name__)

# In-memory cache for processed message IDs (deduplication).
processed_ids: dict[str, float] = {}


# === Lifecycle Hooks ===
@app.before_serving
async def startup():
    app.config["HTTP"] = aiohttp.ClientSession()
    problems = config.validate()
    if problems:
        for p in problems:
            print(f"CONFIG WARNING: {p}")
    print(f"SMS mode: {config.SMS_MODE} -> {config.messages_url()}")
    print(f"AI endpoint: {config.AI_ENDPOINT}")
    print("HTTP session ready")


def http_session() -> aiohttp.ClientSession:
    """The shared client session, rebuilt if it has been closed.

    Every outbound call - to the gateway and to the agent backend - goes through
    one session created at startup. When that session dies the process keeps
    accepting webhooks and failing every one of them, while /health, which only
    checked configuration, went on reporting "ok". Recovering took someone
    noticing the silence and restarting the service by hand.

    A closed session is cheap to replace, so it is replaced rather than reported.
    """
    session = app.config.get("HTTP")
    if session is None or session.closed:
        print("HTTP session was closed; opening a new one")
        session = aiohttp.ClientSession()
        app.config["HTTP"] = session
    return session


@app.after_serving
async def shutdown():
    session = app.config.get("HTTP")
    if session is not None and not session.closed:
        await session.close()
    print("Shutdown complete")


# === Deduplication ===
def is_processed(message_id: str) -> bool:
    now = time.time()
    for mid in [m for m, ts in processed_ids.items() if now - ts > config.DEDUP_TTL]:
        processed_ids.pop(mid, None)
    return message_id in processed_ids


def mark_processed(message_id: str):
    processed_ids[message_id] = time.time()


# === AI Service ===
async def forget_farmer(phone: str) -> bool:
    """Erase everything stored about this number."""
    url = config.forget_url()
    if not url:
        return False
    http = http_session()
    try:
        async with http.post(url, json={"user_id": phone},
                             timeout=aiohttp.ClientTimeout(total=30)) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"Erase request failed for {phone}: {e}")
        return False


async def record_rating(phone: str, message: str) -> bool:
    """Offer the message to the backend as a rating.

    Returns True when it was one, so the message is not also answered as a
    farming question - a farmer replying "4" is not asking about anything.
    """
    base = config.backend_base()
    if not base:
        return False
    http = http_session()
    try:
        async with http.post(f"{base}/feedback/rating",
                             json={"user_id": phone, "message": message},
                             timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status != 200:
                return False
            data = await resp.json(content_type=None)
            return bool(data.get("is_rating"))
    except Exception as e:
        print(f"Rating check failed for {phone}: {e}")
        return False


async def send_feedback_requests() -> dict:
    """Ask farmers whose conversation has finished to rate it."""
    base = config.backend_base()
    if not base:
        return {"asked": 0, "reason": "no backend configured"}

    http = http_session()
    try:
        async with http.get(f"{base}/feedback/due",
                            timeout=aiohttp.ClientTimeout(total=60)) as resp:
            due = (await resp.json(content_type=None)).get("due", [])
    except Exception as e:
        print(f"Could not fetch farmers due for feedback: {e}")
        return {"asked": 0, "error": str(e)[:120]}

    asked = 0
    for phone in due:
        if await send_sms(phone, config.FEEDBACK_PROMPT):
            try:
                await http.post(f"{base}/feedback/asked", json={"user_id": phone},
                                timeout=aiohttp.ClientTimeout(total=30))
            except Exception as e:
                print(f"Could not mark {phone} as asked: {e}")
            asked += 1

    if asked:
        print(f"Asked {asked} farmer(s) for a rating")
    return {"asked": asked, "due": len(due)}


async def generate_response(message: str, phone: str, message_id: str | None,
                            channel: str = "sms") -> str | None:
    """Ask the agent backend for a reply, already trimmed for SMS."""
    if not config.AI_ENDPOINT:
        print("AI_ENDPOINT not configured")
        return None

    http = http_session()
    # The farmer's message is sent verbatim. Brevity and plain-text formatting
    # are requested via `channel`, not by appending instructions to the query -
    # appended text was being parsed as part of the question and skewed the
    # crop and location extraction. `user_id` is what lets the backend recall
    # this farmer's location and recent conversation.
    payload = {
        "query": message,
        "channel": channel,
        "user_id": phone,
        "message_id": message_id,
    }

    # A sleeping backend can take over two minutes to wake, which is longer
    # than any sane single timeout. The first attempt doubles as the wake-up
    # call; by the retry the service is usually warm and answers in seconds.
    for attempt in range(1, config.AI_ATTEMPTS + 1):
        try:
            async with http.post(
                config.AI_ENDPOINT,
                json=payload,
                headers={"accept": "application/json", "Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=config.AI_TIMEOUT),
            ) as resp:
                body = await resp.text()
                if resp.status != 200:
                    print(f"AI API error {resp.status} (attempt {attempt}): {body[:300]}")
                    if 500 <= resp.status < 600 and attempt < config.AI_ATTEMPTS:
                        # A cold backend refuses immediately, so retrying at
                        # once just collects the same refusal. Waiting is what
                        # gives it time to finish waking; without this, a farmer
                        # who wrote after a quiet spell lost their message.
                        # The wait grows, so the attempts land at roughly 0, 20,
                        # 60 and 120 seconds. A free-tier cold start takes about
                        # 140, which a flat retry would spend entirely inside.
                        wait = config.AI_RETRY_WAIT * attempt
                        print(f"Backend is waking; retrying in {wait}s")
                        await asyncio.sleep(wait)
                        continue
                    return None

                data = await resp.json(content_type=None)

                # The backend returns {"answer": ...}. An earlier version read
                # "response", which never existed, so every reply was dropped.
                answer = data.get("answer") or data.get("response")
                if not answer:
                    print(f"AI API returned no answer field: {body[:300]}")
                    return None

                # Ask for the single most useful thing still missing. The
                # prompt shares this message's segment budget rather than
                # adding a paid segment of its own.
                suffix = ""
                for slot in data.get("missing_slots") or []:
                    if slot in config.SLOT_PROMPTS:
                        suffix = config.SLOT_PROMPTS[slot]
                        break
                if not suffix and data.get("needs_location"):
                    suffix = config.LOCATION_PROMPT
                # The segment budget is an SMS constraint. WhatsApp has no
                # segments, so trimming there would discard useful advice for
                # no reason; only the slot prompt is appended.
                if channel != "sms":
                    return f"{answer}\n\n{suffix}" if suffix else answer
                return prepare(answer, config.MAX_SMS_CHARS,
                               config.MAX_SMS_SEGMENTS, suffix)

        except asyncio.TimeoutError:
            print(f"AI request timed out after {config.AI_TIMEOUT}s (attempt {attempt})")
        except Exception as e:
            print(f"AI request failed (attempt {attempt}): {e}")

    return None


# === SMS Sending ===
async def send_sms(phone_number: str, message: str) -> bool:
    http = http_session()
    auth = aiohttp.BasicAuth(config.USERNAME or "", config.PASSWORD or "")
    payload = {"phoneNumbers": [phone_number], "textMessage": {"text": message}}

    try:
        async with http.post(
            config.messages_url(),
            json=payload,
            auth=auth,
            timeout=aiohttp.ClientTimeout(total=config.SMS_TIMEOUT),
        ) as resp:
            if resp.status in (200, 201, 202):
                print(f"SMS sent to {phone_number} "
                      f"({len(message)} chars, {segment_count(message)} segment(s))")
                return True
            print(f"SMS send failed {resp.status}: {(await resp.text())[:300]}")
            return False
    except Exception as e:
        print(f"SMS sending error: {e}")
        return False


# An Indian handset receives far more machine traffic than conversation: bank
# alerts, OTPs, delivery notices, marketing. Those arrive from DLT sender IDs
# like "JR-JIOPAY-S" or "VM-HDFCBK" rather than from a number, and they are not
# people. One reached the agent, was answered as though it were a farming
# question, and was written to the farmer table - where it was then permanently
# due for a rating request that could never be delivered, since an alphanumeric
# sender ID cannot receive SMS.
#
# The test is whether a reply could ever arrive: a sender we can answer is a
# phone number. Anything containing a letter is a machine, and is ignored
# before it costs a model call or a database row.
def is_replyable(sender: str) -> bool:
    digits = re.sub(r"[\s()\-.]", "", sender or "")
    if digits.startswith("+"):
        digits = digits[1:]
    return digits.isdigit() and len(digits) >= 8


# === Processing Logic ===
async def process_sms(data: dict):
    payload = data.get("payload", {})
    msg = payload.get("message")

    # The gateway sends the originating number as "sender". The previous code
    # read "phoneNumber", so replies were addressed to None.
    phone = payload.get("sender") or payload.get("phoneNumber")
    received_at = payload.get("receivedAt")

    print("\nIncoming SMS")
    print(f" From: {phone}")
    print(f" Text: {msg}")
    print(f" At  : {received_at}")
    print("-" * 40)

    if not msg or not msg.strip():
        print("Empty message body, nothing to answer")
        return
    if not phone:
        print("No sender number in payload, cannot reply")
        return
    if not is_replyable(phone):
        print(f"Ignoring machine sender {phone!r} (not a number we can reply to)")
        return

    # Opting out has to work before anything else, and must never depend on the
    # agent being reachable.
    if msg.strip().lower() in config.STOP_WORDS:
        print(f"Opt-out from {phone}")
        await forget_farmer(phone)
        await send_sms(phone, config.STOP_REPLY)
        return

    # A reply to the rating request is a number, not a question. Checking
    # first stops "4" being answered as though it were about farming.
    if await record_rating(phone, msg):
        await send_sms(phone, config.FEEDBACK_THANKS)
        return

    reply = await generate_response(msg, phone, payload.get("messageId"))
    if reply:
        print("AI Reply:", reply)
        await send_sms(phone, reply)
    else:
        await send_sms(
            phone,
            "Sorry, AnnaData could not answer right now. Please try again shortly.",
        )


# === Endpoints ===
@app.route("/health", methods=["GET", "HEAD"])
async def health():
    """Whether the bridge can actually do its job, not merely whether it is configured.

    It reports on the two things every reply depends on - a usable HTTP session
    and a reachable agent backend - because the failure that took the service
    down was invisible to a check that only read configuration.
    """
    problems = list(config.validate())

    session = app.config.get("HTTP")
    session_ok = session is not None and not session.closed
    if not session_ok:
        problems.append("HTTP session is closed (it will be reopened on next use)")

    # A HEAD request is the keep-warm ping, and it must stay instant: probing a
    # third party would make this endpoint's latency depend on that party's cold
    # start, against a scheduler timeout of thirty seconds. HEAD answers "this
    # process is alive"; GET answers "and here is what it can reach".
    backend_ok = None
    base = config.backend_base()
    if base and request.method != "HEAD":
        try:
            async with http_session().get(
                f"{base}/health", timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                backend_ok = resp.status == 200
            if not backend_ok:
                problems.append(f"agent backend returned HTTP {resp.status}")
        except Exception as e:
            backend_ok = False
            problems.append(f"agent backend unreachable: {str(e)[:80]}")

    return jsonify({
        "status": "ok" if not problems else "degraded",
        "mode": config.SMS_MODE,
        "http_session": "open" if session_ok else "closed",
        "whatsapp": "configured" if config.whatsapp_enabled() else "not configured",
        "backend_reachable": backend_ok,
        "problems": problems,
        "pending_dedup_entries": len(processed_ids),
    }), 200


@app.route("/tasks/feedback", methods=["GET", "POST"])
async def feedback_task():
    """Send any rating requests that are due.

    Driven by an external scheduler rather than a timer inside the process:
    free hosting sleeps, so a background loop would simply stop, while a cron
    ping also wakes the service.

    GET is accepted as well as POST because scheduling services default to GET,
    and a 405 from a cron job is a confusing way to discover that.

    The result is logged rather than returned. Render sends every response
    chunked with no Content-Length, and a scheduler that cannot bound a response
    rejects even a twenty-byte one as "output too large" - so the endpoint that
    has to be called on a schedule returns no body at all. Ask
    /feedback/summary on the backend for the numbers.
    """
    result = await send_feedback_requests()
    print(f"Feedback task: {result}")
    return "", 204


async def process_whatsapp(incoming: dict):
    """Answer one WhatsApp message.

    Deliberately the same pipeline as SMS - opt-out first, then a possible
    rating, then the agent - because a farmer switching channels should not get
    a different assistant. Only the delivery differs.
    """
    sender, text = incoming["sender"], incoming["text"]

    if not text:
        await whatsapp.send(
            http_session(), sender,
            "I can only read text messages at the moment. Please type your "
            "farming question and I will answer it."
        )
        return

    print(f"\nIncoming WhatsApp\n From: {sender}\n Text: {text}\n" + "-" * 40)

    if text.strip().lower() in config.STOP_WORDS:
        print(f"Opt-out from {sender}")
        await forget_farmer(sender)
        await whatsapp.send(http_session(), sender, config.STOP_REPLY)
        return

    if await record_rating(sender, text):
        await whatsapp.send(http_session(), sender, config.FEEDBACK_THANKS)
        return

    reply = await generate_response(text, sender, incoming.get("message_id"),
                                    channel="whatsapp")
    await whatsapp.send(
        http_session(), sender,
        reply or "Sorry, AnnaData could not answer right now. Please try again shortly."
    )


@app.get("/whatsapp")
async def whatsapp_verify():
    """Meta's one-time webhook handshake."""
    body, status = whatsapp.verify_challenge(request.args)
    return body, status, {"Content-Type": "text/plain"}


@app.post("/whatsapp")
async def whatsapp_incoming():
    """Inbound WhatsApp events.

    Meta retries anything it does not get a prompt 200 for, and delivers status
    callbacks through the same endpoint as messages, so this acknowledges
    everything immediately and answers out of band - exactly as the SMS webhook
    does for the same reason.
    """
    body = await request.get_json(force=True, silent=True) or {}
    incoming = whatsapp.extract(body)

    if not incoming:
        return jsonify({"status": "ignored"}), 200      # status callback, not a message

    message_id = incoming.get("message_id")
    if message_id:
        if is_processed(message_id):
            print(f"Duplicate WhatsApp message ({message_id}) - skipping")
            return jsonify({"status": "duplicate"}), 200
        mark_processed(message_id)

    asyncio.create_task(process_whatsapp(incoming))
    return jsonify({"status": "ok"}), 200


@app.post(config.WEBHOOK_PATH)
async def incoming_sms():
    data = await request.get_json(force=True, silent=True) or {}
    message_id = data.get("payload", {}).get("messageId")
    print("Received SMS with ID:", message_id)

    if not message_id:
        return jsonify({"status": "error", "reason": "missing messageId"}), 400

    if is_processed(message_id):
        print(f"Duplicate SMS (ID: {message_id}) - skipping")
        return jsonify({"status": "duplicate"}), 200

    mark_processed(message_id)

    # Reply within the gateway's 30s webhook deadline; answer out of band.
    asyncio.create_task(process_sms(data))
    return jsonify({"status": "ok"}), 200


# Run with:
#   uvicorn app:app --host 0.0.0.0 --port 5000
