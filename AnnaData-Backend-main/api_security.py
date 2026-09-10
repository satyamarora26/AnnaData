"""Single-process API admission controls. See docs/API_ACCESS.md before scaling."""
from collections import deque
import asyncio
import hmac
import logging
import math
import threading
import time

from fastapi import HTTPException, Request
from starlette.responses import JSONResponse

import config

logger = logging.getLogger(__name__)


def require_service_token(request: Request) -> None:
    expected = config.API_SERVICE_TOKEN
    if not expected:
        raise HTTPException(503, "Service access is not configured")
    values = request.headers.getlist("authorization")
    parts = values[0].split() if len(values) == 1 else []
    valid = (
        len(parts) == 2
        and parts[0].lower() == "bearer"
        and hmac.compare_digest(parts[1].encode("utf-8"), expected.encode("utf-8"))
    )
    if not valid:
        raise HTTPException(401, "Service authorization required",
                            headers={"WWW-Authenticate": "Bearer"})


class RequestLimiter:
    """Atomic sliding windows with bounded clients and timestamp queues.

    Active clients are never evicted to admit a new identity: doing so would
    let identity churn reset existing limits. Refusals do not extend a window.
    """

    def __init__(self, *, per_client, global_limit, window_seconds, max_clients,
                 clock=time.monotonic):
        if min(per_client, global_limit, window_seconds, max_clients) <= 0:
            raise ValueError("Request limits must be positive")
        self.per_client = per_client
        self.global_limit = global_limit
        self.window = window_seconds
        self.max_clients = max_clients
        self.clock = clock
        self.clients = {}
        self.global_requests = deque()
        self.lock = threading.Lock()

    def check(self, client: str) -> int:
        """Return zero on admission, or whole seconds until retry is possible."""
        with self.lock:
            now = self.clock()
            cutoff = now - self.window
            while self.global_requests and self.global_requests[0] <= cutoff:
                self.global_requests.popleft()
            for key in list(self.clients):
                if self.clients[key][-1] <= cutoff:
                    del self.clients[key]
            requests = self.clients.get(client)
            if requests is not None:
                while requests and requests[0] <= cutoff:
                    requests.popleft()

            waits = []
            if len(self.global_requests) >= self.global_limit:
                waits.append(self.global_requests[0] + self.window - now)
            if requests is not None and len(requests) >= self.per_client:
                waits.append(requests[0] + self.window - now)
            if requests is None and len(self.clients) >= self.max_clients:
                waits.append(min(q[-1] for q in self.clients.values()) + self.window - now)
            if waits:
                return max(1, math.ceil(max(waits)))

            if requests is None:
                requests = self.clients[client] = deque()
            requests.append(now)
            self.global_requests.append(now)
            return 0


class APISecurityMiddleware:
    def __init__(self, app):
        self.app = app
        self.slots = threading.BoundedSemaphore(config.API_MAX_CONCURRENT_REQUESTS)
        self.body_read_timeout = config.API_BODY_READ_TIMEOUT_SECONDS
        self.limiter = RequestLimiter(
            per_client=config.API_RATE_PER_CLIENT,
            global_limit=config.API_RATE_GLOBAL,
            window_seconds=config.API_RATE_WINDOW_SECONDS,
            max_clients=config.API_RATE_MAX_CLIENTS,
        )

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        path = scope["path"].rstrip("/") or "/"
        response_started = False

        async def tracked_send(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            try:
                await send(message)
            except Exception:
                raise RuntimeError("Response interrupted") from None

        # Never queue a waiter or read an unadmitted body. This semaphore also
        # covers rate-exempt health requests and stays held through response IO.
        if not self.slots.acquire(blocking=False):
            await JSONResponse(
                {"detail": "Service is busy; try again later"}, status_code=503,
                headers={"Retry-After": "1"},
            )(scope, receive, tracked_send)
            return

        try:
            exempt = (request.method == "OPTIONS" or
                      (path in {"/", "/health"} and request.method in {"GET", "HEAD"}))
            if not exempt:
                # Use the ASGI peer only. Proxy headers are the server's trust
                # decision; accepting X-Forwarded-For here enables spoofing.
                peer = scope.get("client")
                retry = self.limiter.check(peer[0] if peer else "unknown")
                if retry:
                    raise HTTPException(429, "Request limit reached; try again later",
                                        headers={"Retry-After": str(retry)})

            if request.method != "OPTIONS" and (
                path == "/forget" or path == "/feedback" or path.startswith("/feedback/")
            ):
                require_service_token(request)

            limit = (config.API_MAX_MEDIA_REQUEST_BYTES if path == "/api/chat/describe"
                     else config.API_MAX_REQUEST_BYTES)
            lengths = request.headers.getlist("content-length")
            if lengths:
                if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit():
                    raise HTTPException(400, "Invalid Content-Length")
                if int(lengths[0]) > limit:
                    raise HTTPException(413, "Request body is too large")
            if request.headers.get("content-encoding", "identity").lower() != "identity":
                raise HTTPException(415, "Content encoding is not supported")

            # Buffer at most the configured cap before JSON/multipart parsing.
            # Content-Length alone cannot bound chunked or dishonest requests.
            body = bytearray()
            try:
                # One deadline for the complete body, not a fresh timeout for
                # each chunk. The receive coroutine is cancelled on expiry.
                async with asyncio.timeout(self.body_read_timeout):
                    while True:
                        message = await receive()
                        if message["type"] == "http.disconnect":
                            return
                        chunk = message.get("body", b"")
                        if len(body) + len(chunk) > limit:
                            raise HTTPException(413, "Request body is too large")
                        body.extend(chunk)
                        if not message.get("more_body", False):
                            break
            except TimeoutError:
                raise HTTPException(408, "Request body read timed out") from None

            delivered = False
            async def replay_receive():
                nonlocal delivered
                if delivered:
                    return await receive()
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}

            await self.app(scope, replay_receive, tracked_send)
        except HTTPException as exc:
            if response_started:
                raise RuntimeError("Response interrupted") from None
            await JSONResponse({"detail": exc.detail}, status_code=exc.status_code,
                               headers=exc.headers)(scope, receive, tracked_send)
        except Exception as exc:
            # Exception messages may contain credentials, farmer data or
            # provider URLs. Keep diagnostics to the exception type here.
            logger.error("API request failed (%s)", type(exc).__name__)
            if response_started:
                raise RuntimeError("Response interrupted") from None
            await JSONResponse({"detail": "Internal server error"}, status_code=500)(
                scope, receive, tracked_send)
        finally:
            self.slots.release()
