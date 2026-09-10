import encoding_setup  # noqa: F401  (must be first)

from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import config
import db
import feedback
import knowledge
import msp
import profile_store
import readiness
import startup
from Agent import run_agent
from process_media import process_media
from api_security import APISecurityMiddleware, require_service_token
from chat_stream import progress_response

@asynccontextmanager
async def lifespan(_app: FastAPI):
    on_startup()
    try:
        yield
    finally:
        on_shutdown()


app = FastAPI(title="Annadata Agent API", lifespan=lifespan)


def _build_origins() -> list[str]:
    origins = [
        "http://localhost:3000",
        "http://localhost:8000",
        "http://127.0.0.1:3000",
    ]
    if config.FRONTEND_URL:
        origins.append(config.FRONTEND_URL.rstrip("/"))
    if config.CORS_ORIGINS:
        origins.extend(
            o.strip().rstrip("/") for o in config.CORS_ORIGINS.split(",") if o.strip()
        )
    return sorted(set(origins))


# CORS is outermost so security failures remain readable by the public UI.
app.add_middleware(APISecurityMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_build_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def invalid_request(_request: Request, _exc: RequestValidationError):
    # FastAPI's default validation errors echo input, including farmer data.
    return JSONResponse({"detail": "Invalid request"}, status_code=422)


def on_startup():
    """Warm up optional subsystems without letting a failure block the service."""
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


class QueryRequest(BaseModel):
    query: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    history: Optional[List[dict]] = None
    # "sms" produces a short plain-text answer; "web" allows markdown.
    channel: Optional[str] = "web"
    # Stable identity for the farmer - their phone number on SMS. When given,
    # the agent recalls their profile and recent conversation, and remembers
    # whatever it learns from this message.
    user_id: Optional[str] = None
    # Gateway message id, so a redelivered webhook cannot be logged twice.
    message_id: Optional[str] = None


def on_shutdown():
    db.close()


# HEAD as well as GET. Render serves every response chunked with no
# Content-Length, and monitoring services that cannot bound a response in
# advance reject even a seventy-byte one as "output too large". A HEAD request
# has no body at all, which sidesteps the problem entirely.
@app.api_route("/", methods=["GET", "HEAD"])
def root():
    return {"message": "AnnaData Agent API is running!"}


@app.api_route("/health", methods=["GET", "HEAD"])
def health():
    """Read-only readiness for configured integrations."""
    state = readiness.snapshot()
    configured_failures = [
        name for name, details in state.items()
        if isinstance(details, dict)
        and details.get("configured")
        and details.get("ready") is False
    ]
    return {
        "status": "degraded" if configured_failures else "ok",
        "integrations": state,
    }


@app.post("/agent")
def run_agent_endpoint(request: QueryRequest, http_request: Request):
    _validate_agent_request(request, http_request)
    return _agent_response(request)


@app.post("/agent/stream")
def stream_agent_endpoint(request: QueryRequest, http_request: Request):
    _validate_agent_request(request, http_request)
    return progress_response(lambda progress: _agent_response(request, progress))


def _validate_agent_request(request: QueryRequest, http_request: Request):
    channel = (request.channel or "web").strip().lower()
    if request.user_id is not None or channel == "sms":
        require_service_token(http_request)
    if not request.query or not request.query.strip():
        raise HTTPException(status_code=400, detail="query must not be empty")


def _agent_response(request: QueryRequest, on_progress=None):
    channel = (request.channel or "web").strip().lower()
    user_id = (request.user_id or "").strip() or None

    profile = profile_store.get_profile(user_id) if user_id else None

    # An explicit position in the request wins; otherwise fall back to what we
    # remember, which is what gives SMS access to the location-aware tools.
    latitude, longitude = request.latitude, request.longitude
    if (latitude is None or longitude is None) and profile:
        latitude = profile.get("latitude")
        longitude = profile.get("longitude")

    # The caller may pass history explicitly (the web app does); otherwise
    # rebuild it from what this farmer has sent before.
    history = request.history
    if history is None and user_id:
        history = profile_store.recent_history(user_id)

    session_id = feedback.current_session(user_id) if user_id else None
    if user_id:
        profile_store.log_message(user_id, "inbound", request.query,
                                  gateway_message_id=request.message_id,
                                  session_id=session_id)

    try:
        result = run_agent(
            query=request.query,
            latitude=latitude,
            longitude=longitude,
            history=history,
            channel=channel,
            profile=profile,
            **({"on_progress": on_progress} if on_progress else {}),
        )
    except Exception as e:
        # Previously this returned 200 with an {"error": ...} body, so callers
        # (the SMS bridge especially) treated failures as successful replies.
        print(f"Error in agent function: {type(e).__name__}")
        raise HTTPException(status_code=502, detail="Agent is temporarily unavailable") from None

    needs_location = False
    if user_id:
        profile_store.remember(
            user_id,
            channel=channel,
            location_text=result.location,
            latitude=result.latitude,
            longitude=result.longitude,
            state=result.state,
            crop=result.crop,
        )
        profile_store.log_message(
            user_id, "outbound", result.answer,
            meta={"tools": result.tools_used, "channel": channel,
                  "intent": result.intent,
        "message_type": result.message_type, "missing": result.missing_slots},
        )
        # Re-read so the decision reflects anything just learned.
        needs_location = profile_store.should_ask_location(
            profile_store.get_profile(user_id)
        )
        if needs_location:
            profile_store.mark_location_asked(user_id)

    return {
        "answer": result.answer,
        "needs_location": needs_location,
        "tools_used": result.tools_used,
        "intent": result.intent,
        "message_type": result.message_type,
        # Exactly what the farmer has not told us yet, so the caller can ask
        # for that one thing instead of a generic prompt.
        "missing_slots": result.missing_slots,
        "session_id": session_id,
    }


class UserRequest(BaseModel):
    """Anything addressed at a single farmer by their identifier."""
    user_id: str


class RatingRequest(BaseModel):
    user_id: str
    message: str
    session_id: Optional[int] = None


@app.post("/feedback/rating")
def record_rating(request: RatingRequest):
    """Record a farmer's rating, if their reply contains one.

    Returns whether it was a rating at all, so the caller can fall back to
    answering the message as an ordinary question when it was not.
    """
    user_id = (request.user_id or "").strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id must not be empty")

    if not feedback.awaiting_reply(user_id):
        return {"is_rating": False, "reason": "not awaiting a rating"}

    rating = feedback.parse_rating(request.message)
    if rating is None:
        return {"is_rating": False, "reason": "no rating found in the message"}

    session_id = request.session_id or feedback.current_session(user_id)
    stored = feedback.record(user_id, rating, request.message, session_id)
    return {"is_rating": True, "rating": rating, "stored": stored}


@app.get("/feedback/due")
def feedback_due():
    """Farmers whose conversation has ended and who are due to be asked.

    Called on a schedule by the SMS bridge; kept as a query rather than a push
    so the backend never needs to send anything itself.
    """
    return {"due": feedback.due_for_feedback()}


@app.post("/feedback/asked")
def feedback_asked(request: UserRequest):
    """Record that a farmer has been asked, so they are not asked again."""
    feedback.mark_asked((request.user_id or "").strip())
    return {"ok": True}


@app.get("/feedback/summary")
def feedback_summary():
    """Ratings so far, and which kinds of question score worst."""
    return feedback.summary()


@app.post("/forget")
def forget(request: UserRequest):
    """Erase a farmer's profile and message history. Backs the STOP keyword."""
    user_id = (request.user_id or "").strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id must not be empty")
    return {"erased": profile_store.forget(user_id)}


@app.post("/api/chat/describe")
async def chat_describe(
    audio: Optional[UploadFile] = File(None, description="Optional audio file"),
    image: Optional[UploadFile] = File(None, description="Optional image file"),
):
    async def read_upload(upload):
        if upload is None:
            return None
        data = await upload.read(config.API_MAX_UPLOAD_BYTES + 1)
        if len(data) > config.API_MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="Uploaded file is too large")
        return data

    audio_bytes = await read_upload(audio)
    image_bytes = await read_upload(image)

    if not audio_bytes and not image_bytes:
        return JSONResponse(content={"error": "No file provided"}, status_code=400)

    if audio_bytes and image_bytes:
        prompt_text = (
            "First transcribe the audio into English, then describe the crop "
            "details from the image. Output format: Audio: , Crop Name: , "
            "Crop Type: , Crop Stage: , Pests/Diseases: ."
        )
    elif audio_bytes:
        prompt_text = "Transcribe this audio into English."
    else:
        prompt_text = (
            "Provide the crop name, crop type, crop stage, and any visible pests "
            "or diseases in one line. Output format: Crop Name: , Crop Type: , "
            "Crop Stage: , Pests/Diseases: ."
        )

    try:
        output = await process_media(
            audio_bytes=audio_bytes,
            image_bytes=image_bytes,
            audio_filename=audio.filename if audio else None,
            audio_content_type=audio.content_type if audio else None,
            image_filename=image.filename if image else None,
            extra_prompt=prompt_text,
        )
    except Exception as e:
        print(f"Media processing failed: {type(e).__name__}")
        raise HTTPException(status_code=502, detail="Media processing is temporarily unavailable") from None

    return JSONResponse(content={"Result": (output or "").strip()})
