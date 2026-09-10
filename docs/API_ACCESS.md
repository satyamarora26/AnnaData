# API Access and Demo Limits

The public frontend remains a stateless client. It needs only `REACT_APP_API_URL`
and an allowed backend CORS origin. It does not need a service token.

The default Render Blueprint deploys **only backend and frontend**, with no SMS
service or service-token setting. Leave `API_SERVICE_TOKEN` unset for the public
demo so every privileged endpoint fails closed. Use a demo database containing
no real farmer records. When reusing existing hosting services, remove any
previous service token or inherited secret configuration and verify that no old
bridge remains publicly reachable; a repository change does not remove deployed
secrets or stop an existing service.

## Endpoint Contract

| Endpoint | Access |
| --- | --- |
| `GET/HEAD /`, `GET/HEAD /health` | Public; excluded from request-rate limits |
| `POST /agent` without an identity, with the default/web channel | Public, stateless, rate and body limited |
| `POST /agent/stream` | Same payload and access controls as `/agent`; progress-only SSE |
| `POST /api/chat/describe` | Public, rate and upload limited |
| `POST /agent` with any non-null `user_id` or `channel=sms` | Service bearer token required before profile/history access or agent execution |
| All `/feedback/*` endpoints, including due and summary | Service bearer token required |
| `POST /forget` | Service bearer token required |
| CORS preflight (`OPTIONS`) | Public; excluded from rate limits |

An empty/whitespace `user_id` still requires authorization. Channel comparison
ignores surrounding whitespace and case. An absent or null identity preserves
the existing stateless web contract. WhatsApp requests from the bridge include
an identity and therefore also require authorization. Tokens in query strings,
JSON bodies, cookies or custom headers are not accepted as authorization.

Use `Authorization: Bearer <service-token>` for server-to-server requests.
Comparison uses `hmac.compare_digest` on bytes. Duplicate authorization headers,
wrong tokens and non-bearer authentication are rejected with `401` and
`WWW-Authenticate: Bearer`. When the backend token is absent, privileged requests
fail closed with `503`; public chat and uploads remain available. Rate rejection
can occur first and returns `429` with a whole-second `Retry-After` header.
Concurrent admission is checked first and returns `503` with `Retry-After: 1`
when full. An incomplete request body exceeding its total deadline returns `408`.

## Operator Configuration

### Chat Progress Stream

The web client posts to `/agent/stream`. Each SSE `data:` frame is JSON with a
`type`: `status`, `result`, or `error`. Status frames carry only a fixed stage
identifier, never queries, raw model text, or provider errors. A `result` carries
the existing `/agent` response after normal agent processing and output guards.
Ten-second SSE comments keep active connections from appearing silent. A
terminal error is generic because HTTP headers have already been sent.

This is live processing progress, **not token-by-token generation**. There is
no artificial typing delay. Existing non-streaming clients can keep `/agent`.
The browser bounds optional GPS waiting to one second and sends only previous
turns as history; the current query has its own field. Requests time out after
three minutes. Disconnecting does not abandon running provider work or release
its admission slot early. Provider timeouts still govern underlying execution.

Deploy the backend before the frontend, since the new UI uses the new route.
No new secrets, services, or paid hosting are required. Streaming cannot remove
the free host's idle-start delay or shorten the provider's own processing time.

For the public demo, configure only the provider credentials needed for public
chat and approved knowledge retrieval, the frontend URL, and the limits below.
Keep `API_SERVICE_TOKEN` unset. The repository's `.env.example` files contain
empty placeholders; `render.yaml` does not request a service token.

SMS/WhatsApp is an explicit **manual deployment opt-in only after webhook and
scheduler authentication has been implemented and verified**. Require verified
provider signatures or a verified authenticated ingress on inbound webhooks,
authenticated access to `/tasks/feedback`, and HTTPS backend transport. The
current bridge source does not meet those ingress requirements. Do not deploy
it from the public-demo Blueprint or add it back as a suspended service.

After those checks, a separate private deployment may provision a strong random
secret (at least 32 random bytes) through the hosting secret manager as
`API_SERVICE_TOKEN` on both backend and bridge, with identical values. This
explicitly enables farmer-data endpoints and changes the public-demo access
policy. Changing the secret requires restarting both services; requests fail
during a mismatch.

Never place this secret in `REACT_APP_*`, frontend `.env` files, JavaScript,
browser storage, a static host's build environment, URLs, or client-side headers.
This is a shared service credential with access to all farmer identities, not
an end-user login token. A future browser feature that reads or writes farmer
data needs its own authenticated server, not this credential in a bundle.

Set `AI_ENDPOINT` on the bridge to the backend's final HTTPS `/agent` URL.
Plain HTTP is suitable only for trusted local development. The bridge adds the
token to each backend request: agent, forget, feedback rating/due/asked and
health. It disables redirects on these calls, so configure the final URL rather
than a redirecting alias. Its shared HTTP session has no default service headers;
SMS Gateway and WhatsApp requests retain their own provider authentication.
Without the token the bridge reports a configuration problem and makes no
backend calls. Gateway-only `webhook.py` configuration checks do not require
backend credentials.

Both SMS and WhatsApp confirm deletion only after a successful backend response.
If backend access is unavailable or refused, STOP returns `STOP_FAILURE_REPLY`
asking the farmer to retry instead of claiming that deletion succeeded.

Set `FRONTEND_URL` to the public frontend origin and optionally `CORS_ORIGINS`
to additional comma-separated origins. Existing localhost origins remain
allowed. CORS wraps security responses, so the UI can read `401`, `413`, `429`
and `500` responses. CORS is not authentication and does not restrict scripts
outside a browser.

## Limits

All numeric settings must be positive integers; invalid values fail startup.

| Backend setting | Default | Meaning |
| --- | ---: | --- |
| `API_RATE_WINDOW_SECONDS` | 60 | Sliding window duration |
| `API_RATE_PER_CLIENT` | 12 | Admitted requests per client address per window |
| `API_RATE_GLOBAL` | 120 | Admitted requests across all clients per window |
| `API_RATE_MAX_CLIENTS` | 2048 | Maximum retained active client addresses |
| `API_MAX_CONCURRENT_REQUESTS` | 4 | Maximum requests admitted simultaneously, through response completion |
| `API_BODY_READ_TIMEOUT_SECONDS` | 15 | Total deadline for reading the complete request body |
| `API_MAX_REQUEST_BYTES` | 65536 | Total JSON/other request body (64 KiB), including history |
| `API_MAX_UPLOAD_BYTES` | 5242880 | Each image or audio file (5 MiB) |
| `API_MAX_MEDIA_REQUEST_BYTES` | 11534336 | Entire media multipart body, including both files and framing (11 MiB) |

Agent, media, feedback, forget and other non-exempt requests share the same
budget, including authenticated service calls and requests later rejected for
authorization/validation. The bridge shares its client-address budget across
farmers; allow headroom for its rating and agent calls and scheduled feedback
batch. There is no privileged rate-limit bypass. Failed admissions do not
extend the window. `Retry-After` indicates the earliest retry assuming no other
traffic consumes capacity.

A thread-safe bounded semaphore admits at most four concurrent requests by
default. Admission is nonblocking: surplus requests receive `503` immediately,
without reading their bodies or joining a waiting queue. Every admitted HTTP
request holds its slot through authorization, body reading, parsing, handler
execution and response sending. Slots release in `finally`, including on
validation/authorization rejection, oversized bodies, timeouts, disconnects,
handler errors, send failures and cancellation. Health requests share this cap
and may report `503` under saturation even though they are rate-exempt. CORS
preflight handled by the outer CORS middleware remains immediately available.

The body timeout is a single `asyncio.timeout` deadline around the complete read;
receiving another chunk does not reset it. Expiry cancels the pending receive,
returns a generic `408`, and frees the slot without invoking the handler.
Provider execution is outside this body deadline and retains its slot until
the request finishes. Response failures after headers have started propagate
as a generic interruption rather than attempting a second response.

The limiter uses a monotonic clock and a lock around the combined client/global
check and update. Timestamp queues and the client table are bounded. Expired
clients are removed; if the table is full, new clients receive `429` rather than
evicting active clients and resetting their limits. Body limits check both
declared length and actual received chunks before JSON/multipart parsing, so
missing or understated `Content-Length` does not bypass the cap. Oversized
bodies/files return `413` before provider execution. Compressed request bodies
are not accepted (`415`). Body caps include encoding/multipart overhead; they
are not character limits.

## Process and Ingress Boundary

**Run exactly one backend worker and one instance for these limits.** The Render
start command explicitly sets `--workers 1`. Limits are in memory, reset on a
restart, and are not shared across workers or replicas. Multiple processes
multiply both the rate and concurrent-request allowance. Use shared admission
controls (for example, at the gateway or backed by Redis) before adding workers
or instances. These are not a durable spending ceiling. Configure provider
budgets and hosting connection/time limits separately; a stalled handler or
response occupies a slot, reducing capacity, rather than admitting more work.
Underlying provider libraries remain responsible for their own request timeouts.

Client identity is the ASGI peer address. Application code never trusts
`X-Forwarded-For` or `Forwarded` itself. The Render command defaults to
`--no-proxy-headers`: behind ingress, clients can therefore share the ingress
address and its 12-request budget. To restore original client addresses, first
establish a trusted ingress that overwrites forwarding headers and prevents
direct public backend access; then replace `--no-proxy-headers` with Uvicorn's
`--proxy-headers --forwarded-allow-ips <trusted-proxy-IP-or-CIDR-list>`. Do not
trust `*` on an unrestricted network. Test this boundary before increasing
traffic. The global budget remains effective when client addresses are shared.

The service token authenticates the bridge to the backend; it does not verify
inbound SMS/WhatsApp webhook senders or authenticate the bridge's existing
`/tasks/feedback` scheduler endpoint. Those bridge ingress routes must be
restricted/verified at the ingress or via provider signature verification
before enabling real SMS delivery or exposing real farmer records. An
unrestricted bridge would let callers induce privileged backend requests.
This change does not establish webhook authenticity or make provider calls.

## Errors and Offline Verification

Agent/media failures return generic `502` details. Unhandled API errors return
generic JSON `500` responses, and validation errors return generic `422`
responses instead of echoing input. API failure logging records exception types
without provider exception text. Readiness is maintained separately.

Run from `AnnaData-Backend-main`:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 .venv/bin/python -m pytest tests/test_api_security.py -q
```

These tests exercise HTTP authorization, public chat and CORS, body/upload caps,
chunked and understated-length bodies, sanitized failures, concurrent sliding
windows, bounded client churn, concurrency saturation, total body deadlines,
slot release across failures/cancellation, and bridge token forwarding. Provider/storage
boundaries and the bridge framework/HTTP transport are inert test doubles;
backend lifespan startup does not run. No SMS messages, provider requests,
credential changes or deployment are needed for this verification.
