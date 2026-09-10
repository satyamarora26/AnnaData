"""Offline HTTP regressions; no lifespan startup, database or provider calls."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app


TOKEN = "test-only-service-token"
ORIGIN = "http://localhost:3000"


@pytest.fixture
def client(monkeypatch):
    settings = {
        "API_SERVICE_TOKEN": TOKEN,
        "API_RATE_WINDOW_SECONDS": 60,
        "API_RATE_PER_CLIENT": 100,
        "API_RATE_GLOBAL": 200,
        "API_RATE_MAX_CLIENTS": 100,
        "API_MAX_REQUEST_BYTES": 1024,
        "API_MAX_UPLOAD_BYTES": 32,
        "API_MAX_MEDIA_REQUEST_BYTES": 2048,
        "API_MAX_CONCURRENT_REQUESTS": 4,
        "API_BODY_READ_TIMEOUT_SECONDS": 15,
    }
    for key, value in settings.items():
        monkeypatch.setattr(app.config, key, value, raising=False)
    monkeypatch.setattr(app.app, "middleware_stack", None)
    monkeypatch.setattr(app.readiness, "snapshot", lambda: {})
    monkeypatch.setattr(app, "run_agent", lambda **kwargs: SimpleNamespace(
        answer="Offline answer", tools_used=[], intent="general",
        message_type="answer", missing_slots=[], location=None,
        latitude=None, longitude=None, state=None, crop=None,
    ))
    # A security regression must fail before touching storage, even if storage
    # would otherwise be unavailable and silently return an empty result.
    def no_storage(*args, **kwargs):
        raise RuntimeError("private-database-password")
    monkeypatch.setattr(app.profile_store, "get_profile", no_storage)
    monkeypatch.setattr(app.profile_store, "forget", no_storage)
    monkeypatch.setattr(app.feedback, "summary", no_storage)
    monkeypatch.setattr(app.feedback, "due_for_feedback", no_storage)
    monkeypatch.setattr(app.feedback, "mark_asked", no_storage)
    monkeypatch.setattr(app.feedback, "awaiting_reply", no_storage)
    return TestClient(app.app, raise_server_exceptions=False)


def test_public_web_contract_and_preflight(client):
    response = client.post("/agent", json={
        "query": "What can I grow?", "history": [], "latitude": 20, "longitude": 75,
    }, headers={"Origin": ORIGIN})
    assert response.status_code == 200
    assert response.json()["answer"] == "Offline answer"
    assert response.json()["session_id"] is None
    assert response.headers["access-control-allow-origin"] == ORIGIN
    preflight = client.options("/agent", headers={
        "Origin": ORIGIN, "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
    })
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == ORIGIN


@pytest.mark.parametrize("fields", [
    {"user_id": "+910000000000"}, {"user_id": ""}, {"user_id": "  "},
    {"channel": "sms"}, {"channel": " SMS "},
    {"user_id": "farmer", "channel": "whatsapp"},
])
@pytest.mark.parametrize("authorization", [None, "Bearer wrong", "Basic abc"])
def test_agent_identity_requires_service_token(client, fields, authorization):
    headers = {"Origin": ORIGIN}
    if authorization:
        headers["Authorization"] = authorization
    response = client.post("/agent", json={"query": "hello", **fields}, headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert "private-database-password" not in response.text


@pytest.mark.parametrize("method,path,payload", [
    ("GET", "/feedback/due", None), ("GET", "/feedback/summary", None),
    ("POST", "/feedback/asked", {"user_id": "farmer"}),
    ("POST", "/feedback/rating", {"user_id": "farmer", "message": "4"}),
    ("POST", "/forget", {"user_id": "farmer"}),
])
def test_privileged_routes_fail_closed(client, monkeypatch, method, path, payload):
    assert client.request(method, path, json=payload).status_code == 401
    monkeypatch.setattr(app.config, "API_SERVICE_TOKEN", None)
    response = client.request(method, path, json=payload,
                              headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 503
    assert "private-database-password" not in response.text


def test_public_chat_works_with_no_service_token(client, monkeypatch):
    monkeypatch.setattr(app.config, "API_SERVICE_TOKEN", None)
    assert client.post("/agent", json={"query": "hello"}).status_code == 200
    assert client.post("/agent", json={"query": "hello", "channel": "sms"}).status_code == 503


def test_authorized_identity_and_forget(client, monkeypatch):
    identities = []
    monkeypatch.setattr(app.profile_store, "get_profile", lambda uid: identities.append(uid) or {})
    monkeypatch.setattr(app.profile_store, "recent_history", lambda uid: [])
    monkeypatch.setattr(app.profile_store, "log_message", lambda *a, **kw: None)
    monkeypatch.setattr(app.profile_store, "remember", lambda *a, **kw: None)
    monkeypatch.setattr(app.profile_store, "should_ask_location", lambda profile: False)
    monkeypatch.setattr(app.feedback, "current_session", lambda uid: 17)
    monkeypatch.setattr(app.profile_store, "forget", lambda uid: uid == "farmer")
    headers = {"Authorization": f"bEaReR {TOKEN}"}
    response = client.post("/agent", json={
        "query": "hello", "user_id": "farmer", "channel": "sms",
    }, headers=headers)
    assert response.status_code == 200
    assert response.json()["session_id"] == 17
    assert identities == ["farmer", "farmer"]
    response = client.post("/forget", json={"user_id": "farmer"}, headers=headers)
    assert response.status_code == 200
    assert response.json() == {"erased": True}


def test_duplicate_authorization_is_rejected(client):
    response = client.get("/feedback/due", headers=[
        ("Authorization", f"Bearer {TOKEN}"), ("Authorization", "Bearer wrong"),
    ])
    assert response.status_code == 401


def test_request_cap_and_cors(client):
    response = client.post("/agent", json={"query": "x" * 1024}, headers={"Origin": ORIGIN})
    assert response.status_code == 413
    assert response.headers["access-control-allow-origin"] == ORIGIN


@pytest.mark.parametrize("content_length", [None, "1"])
def test_streamed_body_cap_counts_actual_bytes(client, content_length):
    # ASGI receive events model chunked bodies and a dishonest Content-Length.
    # No network server or httpx request buffering obscures the received bytes.
    async def run():
        events = iter([
            {"type": "http.request", "body": b"{" + b" " * 600, "more_body": True},
            {"type": "http.request", "body": b" " * 600, "more_body": False},
        ])
        sent = []
        async def receive():
            return next(events)
        async def send(message):
            sent.append(message)
        headers = [(b"content-type", b"application/json")]
        if content_length:
            headers.append((b"content-length", content_length.encode()))
        await app.app({
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": "POST", "scheme": "http", "path": "/agent",
            "raw_path": b"/agent", "query_string": b"", "root_path": "",
            "headers": headers, "client": ("127.0.0.1", 123), "server": ("test", 80),
        }, receive, send)
        return sent
    sent = asyncio.run(run())
    assert sent[0]["status"] == 413


def test_media_limits_before_provider_and_public_success(client, monkeypatch):
    calls = []
    async def media(**kwargs):
        calls.append(kwargs)
        return "Crop description"
    monkeypatch.setattr(app, "process_media", media)
    response = client.post("/api/chat/describe", files={"image": ("crop.png", b"x" * 33, "image/png")})
    assert response.status_code == 413
    assert not calls
    response = client.post("/api/chat/describe", files={"image": ("crop.png", b"x" * 32, "image/png")})
    assert response.status_code == 200
    assert response.json() == {"Result": "Crop description"}
    assert len(calls) == 1
    response = client.post("/api/chat/describe", content=b"x" * 2049,
                           headers={"Content-Type": "multipart/form-data; boundary=test"})
    assert response.status_code == 413


def test_exceptions_and_validation_do_not_echo_sensitive_details(client, monkeypatch):
    def fail(**kwargs):
        raise RuntimeError("https://provider.invalid/?key=private-provider-secret")
    monkeypatch.setattr(app, "run_agent", fail)
    response = client.post("/agent", json={"query": "hello"})
    assert response.status_code == 502
    assert "private-provider-secret" not in response.text
    async def fail_media(**kwargs):
        fail()
    monkeypatch.setattr(app, "process_media", fail_media)
    response = client.post("/api/chat/describe", files={"audio": ("clip.wav", b"abc")})
    assert response.status_code == 502
    assert "private-provider-secret" not in response.text
    response = client.get("/feedback/summary", headers={"Authorization": f"Bearer {TOKEN}", "Origin": ORIGIN})
    assert response.status_code == 500
    assert "private-database-password" not in response.text
    assert response.headers["access-control-allow-origin"] == ORIGIN
    response = client.post("/agent", json={"query": {"secret": "private-input"}})
    assert response.status_code == 422
    assert "private-input" not in response.text


def test_per_client_limit_ignores_forged_forwarded_headers(client, monkeypatch):
    monkeypatch.setattr(app.config, "API_RATE_PER_CLIENT", 2)
    for forwarded in ("1.2.3.4", "5.6.7.8"):
        assert client.post("/agent", json={"query": "hello"}, headers={"X-Forwarded-For": forwarded}).status_code == 200
    response = client.post("/agent", json={"query": "hello"}, headers={"Origin": ORIGIN})
    assert response.status_code == 429
    assert int(response.headers["retry-after"]) > 0
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert client.head("/health").status_code == 200
    assert client.get("/").status_code == 200
    assert client.options("/agent", headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST"}).status_code == 200


def test_global_limit_applies_across_clients_and_privileged_calls(client, monkeypatch):
    monkeypatch.setattr(app.config, "API_RATE_GLOBAL", 2)
    assert client.post("/agent", json={"query": "hello"}).status_code == 200
    other = TestClient(app.app, client=("192.0.2.4", 42))
    assert other.post("/agent", json={"query": "hello"}).status_code == 200
    response = other.get("/feedback/due", headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 429


def test_limiter_is_thread_safe_and_recovers_after_window():
    from api_security import RequestLimiter
    now = [0.0]
    limiter = RequestLimiter(per_client=7, global_limit=11, window_seconds=60,
                             max_clients=20, clock=lambda: now[0])
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(lambda _: limiter.check("one"), range(100)))
    assert results.count(0) == 7
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(lambda n: limiter.check(str(n)), range(100)))
    assert results.count(0) == 4
    now[0] = 60.0
    assert limiter.check("one") == 0


def test_client_table_is_bounded_without_evicting_active_limits():
    from api_security import RequestLimiter
    now = [0.0]
    limiter = RequestLimiter(per_client=1, global_limit=100, window_seconds=60,
                             max_clients=2, clock=lambda: now[0])
    assert limiter.check("a") == 0
    assert limiter.check("b") == 0
    for n in range(100):
        assert limiter.check(f"new-{n}") > 0
    assert len(limiter.clients) == 2
    assert limiter.check("a") > 0
    now[0] = 60.0
    assert limiter.check("new") == 0
    assert len(limiter.clients) == 1


@pytest.fixture
def bridge(monkeypatch):
    # Import the actual bridge with inert framework/transport boundaries. The
    # backend test environment need not install a second server's dependencies.
    class InertQuart:
        def __init__(self, name):
            self.config = {}
        def before_serving(self, function):
            return function
        after_serving = before_serving
        def route(self, *args, **kwargs):
            return lambda function: function
        post = get = route

    monkeypatch.setitem(sys.modules, "quart", SimpleNamespace(
        Quart=InertQuart, request=SimpleNamespace(method="GET"), jsonify=lambda value: value,
    ))
    monkeypatch.setitem(sys.modules, "aiohttp", SimpleNamespace(
        ClientSession=object, ClientTimeout=lambda **kw: kw,
        BasicAuth=lambda *args: args,
    ))
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    monkeypatch.setenv("API_SERVICE_TOKEN", TOKEN)
    monkeypatch.setenv("AI_ENDPOINT", "https://backend.invalid/agent")
    monkeypatch.setenv("AI_ATTEMPTS", "1")
    sms = Path(__file__).resolve().parents[2] / "AnnaData-SMS-main"
    modules = {}
    for name in ("config", "sms_text", "whatsapp", "app"):
        spec = importlib.util.spec_from_file_location(f"sms_test_{name}", sms / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules[name] = module
        if name != "app":
            monkeypatch.setitem(sys.modules, name, module)
    return modules["app"]


def test_bridge_authenticates_every_backend_call_without_gateway_token(bridge, monkeypatch):
    calls = []
    class Response:
        status = 200
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def json(self, **kwargs):
            return {"answer": "Test reply", "is_rating": True, "due": ["farmer"], "erased": True}
        async def text(self):
            return "{}"
        def __await__(self):
            return self.__aenter__().__await__()
    class Session:
        closed = False
        def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return Response()
        get = post
    bridge.app.config["HTTP"] = Session()

    async def exercise():
        assert await bridge.forget_farmer("farmer") is True
        assert await bridge.record_rating("farmer", "4") is True
        assert await bridge.generate_response("hello", "farmer", "id") == "Test reply"
        assert (await bridge.send_feedback_requests())["asked"] == 1
        await bridge.health()
    asyncio.run(exercise())
    backend_calls = [(url, kw) for url, kw in calls if url.startswith("https://backend.invalid/")]
    assert {url.rsplit("/", 1)[-1] for url, kw in backend_calls} == {
        "forget", "rating", "agent", "due", "asked", "health",
    }
    assert len(backend_calls) == 6
    for url, kwargs in backend_calls:
        assert kwargs["headers"]["Authorization"] == f"Bearer {TOKEN}"
        assert kwargs["allow_redirects"] is False
    gateway_calls = [kw for url, kw in calls if not url.startswith("https://backend.invalid/")]
    assert len(gateway_calls) == 1
    assert "Authorization" not in gateway_calls[0].get("headers", {})


def test_unconfigured_bridge_makes_no_backend_calls(bridge, monkeypatch):
    monkeypatch.setattr(bridge.config, "API_SERVICE_TOKEN", None, raising=False)
    def no_http():
        pytest.fail("Unconfigured bridge attempted an outbound request")
    monkeypatch.setattr(bridge, "http_session", no_http)
    async def exercise():
        assert await bridge.forget_farmer("farmer") is False
        assert await bridge.record_rating("farmer", "4") is False
        assert await bridge.generate_response("hello", "farmer", "id") is None
        assert (await bridge.send_feedback_requests())["asked"] == 0
        await bridge.health()
    asyncio.run(exercise())
    assert any("API_SERVICE_TOKEN" in item for item in bridge.config.validate())


@pytest.mark.parametrize("channel", ["sms", "whatsapp"])
def test_bridge_does_not_claim_deletion_when_backend_refuses(bridge, monkeypatch, channel):
    replies = []
    monkeypatch.setattr(bridge.config, "API_SERVICE_TOKEN", None, raising=False)
    async def capture_sms(phone, message):
        replies.append(message)
        return True
    async def capture_whatsapp(session, phone, message):
        return await capture_sms(phone, message)
    monkeypatch.setattr(bridge, "send_sms", capture_sms)
    monkeypatch.setattr(bridge.whatsapp, "send", capture_whatsapp)
    monkeypatch.setattr(bridge, "http_session", lambda: None)
    if channel == "sms":
        asyncio.run(bridge.process_sms({"payload": {
            "sender": "+910000000000", "message": "STOP", "messageId": "test-stop",
        }}))
    else:
        asyncio.run(bridge.process_whatsapp({"sender": "+910000000000", "text": "STOP"}))
    assert len(replies) == 1
    assert replies[0] != bridge.config.STOP_REPLY
    assert "try" in replies[0].lower()


def test_authorized_feedback_routes_preserve_response_contract(client, monkeypatch):
    monkeypatch.setattr(app.feedback, "due_for_feedback", lambda: ["farmer"])
    monkeypatch.setattr(app.feedback, "summary", lambda: {"count": 3})
    monkeypatch.setattr(app.feedback, "awaiting_reply", lambda uid: True)
    monkeypatch.setattr(app.feedback, "current_session", lambda uid: 17)
    monkeypatch.setattr(app.feedback, "record", lambda *args: True)
    asked = []
    monkeypatch.setattr(app.feedback, "mark_asked", asked.append)
    headers = {"Authorization": f"Bearer {TOKEN}"}
    assert client.get("/feedback/due", headers=headers).json() == {"due": ["farmer"]}
    assert client.get("/feedback/summary", headers=headers).json() == {"count": 3}
    response = client.post("/feedback/asked", json={"user_id": "farmer"}, headers=headers)
    assert response.status_code == 200
    assert asked == ["farmer"]
    response = client.post("/feedback/rating", json={"user_id": "farmer", "message": "4"}, headers=headers)
    assert response.status_code == 200
    assert response.json() == {"is_rating": True, "rating": 4, "stored": True}


@pytest.mark.parametrize("value", ["0", "-1", "1.5", "not-a-number"])
def test_invalid_limits_cannot_disable_protection(monkeypatch, value):
    monkeypatch.setenv("API_RATE_GLOBAL", value)
    with pytest.raises(ValueError, match="API_RATE_GLOBAL must be a positive integer"):
        app.config._positive_int("API_RATE_GLOBAL", 120)


def test_bridge_honors_failed_deletion_in_successful_http_response(bridge):
    class Response:
        status = 200
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def json(self, **kwargs):
            return {"erased": False}
    bridge.app.config["HTTP"] = SimpleNamespace(closed=False, post=lambda *a, **kw: Response())
    assert asyncio.run(bridge.forget_farmer("farmer")) is False


def security_scope(path="/agent", method="POST"):
    return {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": method, "scheme": "http", "path": path,
        "raw_path": path.encode(), "query_string": b"", "root_path": "",
        "headers": [], "client": ("127.0.0.1", 123), "server": ("test", 80),
    }


async def empty_body():
    return {"type": "http.request", "body": b"", "more_body": False}


async def successful_asgi(scope, receive, send):
    await receive()
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


def test_concurrency_rejects_without_waiting_or_reading_and_holds_until_send(client, monkeypatch):
    from api_security import APISecurityMiddleware
    monkeypatch.setattr(app.config, "API_MAX_CONCURRENT_REQUESTS", 1)
    async def exercise():
        reading, processing, sending = asyncio.Event(), asyncio.Event(), asyncio.Event()
        read_gate, process_gate, send_gate = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def receive():
            reading.set()
            await read_gate.wait()
            return await empty_body()
        async def downstream(scope, receive, send):
            await receive()
            processing.set()
            await process_gate.wait()
            await successful_asgi(scope, empty_body, send)
        async def slow_send(message):
            sending.set()
            await send_gate.wait()
        middleware = APISecurityMiddleware(downstream)
        async def assert_busy():
            responses = []
            async def must_not_read():
                pytest.fail("Saturated request must not read its body")
            async def send(message):
                responses.append(message)
            # Health is rate-exempt, but must not bypass concurrent admission.
            await asyncio.wait_for(middleware(security_scope("/health", "GET"), must_not_read, send), 0.2)
            assert responses[0]["status"] == 503
            assert (b"retry-after", b"1") in responses[0]["headers"]
        active = asyncio.create_task(middleware(security_scope(), receive, slow_send))
        try:
            await asyncio.wait_for(reading.wait(), 1)
            await assert_busy()
            read_gate.set()
            await asyncio.wait_for(processing.wait(), 1)
            await assert_busy()
            process_gate.set()
            await asyncio.wait_for(sending.wait(), 1)
            await assert_busy()
        finally:
            read_gate.set()
            process_gate.set()
            send_gate.set()
            await active
        statuses = []
        async def send(message):
            if message["type"] == "http.response.start":
                statuses.append(message["status"])
        await middleware(security_scope(), empty_body, send)
        assert statuses == [200]
    asyncio.run(exercise())


@pytest.mark.parametrize("failure", [
    "disconnect", "receive_error", "app_error", "send_error", "error_response_send",
    "after_headers", "cancel",
])
def test_concurrency_slots_release_on_every_failure(client, monkeypatch, failure):
    from api_security import APISecurityMiddleware
    monkeypatch.setattr(app.config, "API_MAX_CONCURRENT_REQUESTS", 1)
    async def exercise():
        bad = True
        reading = asyncio.Event()
        responses = []
        async def receive():
            if bad and failure == "disconnect":
                return {"type": "http.disconnect"}
            if bad and failure == "receive_error":
                raise RuntimeError("private-receive-secret")
            if bad and failure == "cancel":
                reading.set()
                await asyncio.Event().wait()
            return await empty_body()
        async def downstream(scope, receive, send):
            if bad and failure in {"app_error", "error_response_send"}:
                raise RuntimeError("private-app-secret")
            if bad and failure == "after_headers":
                await send({"type": "http.response.start", "status": 200, "headers": []})
                raise RuntimeError("private-after-headers-secret")
            await successful_asgi(scope, receive, send)
        async def send(message):
            if bad and failure in {"send_error", "error_response_send"}:
                raise RuntimeError("private-send-secret")
            responses.append(message)
        middleware = APISecurityMiddleware(downstream)
        task = asyncio.create_task(middleware(security_scope(), receive, send))
        if failure == "cancel":
            await asyncio.wait_for(reading.wait(), 1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        elif failure in {"send_error", "error_response_send", "after_headers"}:
            with pytest.raises(RuntimeError) as exc:
                await task
            assert "private-" not in str(exc.value)
        else:
            await task
            assert "private-" not in repr(responses)
            if failure.endswith("error"):
                assert responses[0]["status"] == 500
        bad = False
        responses.clear()
        await middleware(security_scope(), empty_body, send)
        assert responses[0]["status"] == 200
    asyncio.run(exercise())


@pytest.mark.parametrize("path,headers,status", [
    ("/feedback/due", [], 401),
    ("/agent", [(b"content-length", b"999999")], 413),
    ("/agent", [(b"content-encoding", b"gzip")], 415),
])
def test_security_rejections_release_concurrency_slot(client, monkeypatch, path, headers, status):
    from api_security import APISecurityMiddleware
    monkeypatch.setattr(app.config, "API_MAX_CONCURRENT_REQUESTS", 1)
    async def exercise():
        middleware = APISecurityMiddleware(successful_asgi)
        responses = []
        async def send(message):
            responses.append(message)
        scope = security_scope(path)
        scope["headers"] = headers
        await middleware(scope, empty_body, send)
        assert responses[0]["status"] == status
        responses.clear()
        await middleware(security_scope(), empty_body, send)
        assert responses[0]["status"] == 200
    asyncio.run(exercise())


def test_total_body_deadline_expires_despite_arriving_chunks_and_releases_slot(client, monkeypatch):
    from api_security import APISecurityMiddleware
    monkeypatch.setattr(app.config, "API_MAX_CONCURRENT_REQUESTS", 1)
    monkeypatch.setattr(app.config, "API_BODY_READ_TIMEOUT_SECONDS", 0.05)
    async def exercise():
        chunks = 0
        cancelled = False
        called = False
        responses = []
        async def trickle():
            nonlocal chunks, cancelled
            try:
                await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                cancelled = True
                raise
            chunks += 1
            return {"type": "http.request", "body": b"x", "more_body": True}
        async def downstream(scope, receive, send):
            nonlocal called
            called = True
            await successful_asgi(scope, receive, send)
        async def send(message):
            responses.append(message)
        middleware = APISecurityMiddleware(downstream)
        await asyncio.wait_for(middleware(security_scope(), trickle, send), 0.5)
        assert responses[0]["status"] == 408
        assert not called
        assert cancelled
        assert chunks > 0
        responses.clear()
        await middleware(security_scope(), empty_body, send)
        assert responses[0]["status"] == 200
    asyncio.run(exercise())


def test_busy_response_preserves_public_cors(client, monkeypatch):
    import httpx
    monkeypatch.setattr(app.config, "API_MAX_CONCURRENT_REQUESTS", 1)
    async def exercise():
        entered, release = asyncio.Event(), asyncio.Event()
        async def media(**kwargs):
            entered.set()
            await release.wait()
            return "Crop"
        monkeypatch.setattr(app, "process_media", media)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.app), base_url="http://test") as http:
            active = asyncio.create_task(http.post("/api/chat/describe", files={"image": ("a.png", b"x")}))
            try:
                await asyncio.wait_for(entered.wait(), 1)
                response = await http.post("/agent", json={"query": "hello"}, headers={"Origin": ORIGIN})
                assert response.status_code == 503
                assert response.headers["access-control-allow-origin"] == ORIGIN
            finally:
                release.set()
                await active
            assert (await http.post("/agent", json={"query": "hello"})).status_code == 200
    asyncio.run(exercise())
