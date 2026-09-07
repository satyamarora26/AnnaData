# AnnaData — Deployment Runbook

AI agricultural advisory for Indian farmers, reachable over the **web** and over **plain SMS** (no smartphone or internet needed on the farmer's side).

```
                                  ┌──────────────────────────┐
  Farmer (web)  ──────────────▶   │  Frontend (React/CRA)    │
                                  │  Render static site      │
                                  └────────────┬─────────────┘
                                               │ POST /agent
                                               ▼
  Farmer (SMS) ──▶ Android phone  ┌──────────────────────────┐
                   running SMS    │  Backend (FastAPI)       │
                   Gateway app    │  Render web service      │
                        │         │                          │
                        │ webhook │  Gemini 3.1 Flash Lite + │
                        │ sms:    │  soil / weather / mandi /│
                        │ received│  schemes KB              │
                        ▼         └────────────▲─────────────┘
              ┌──────────────────┐             │ POST /agent
              │  SMS Bridge      │─────────────┘
              │  (Quart)         │
              │  Render service  │──▶ api.sms-gate.app ──▶ reply SMS
              └──────────────────┘
```

| Service | Directory | Runtime | Deploys to |
|---|---|---|---|
| Backend agent | `AnnaData-Backend-main` | Python 3.11+ / FastAPI | Render web service |
| SMS bridge | `AnnaData-SMS-main` | Python 3.11+ / Quart | Render web service |
| Web frontend | `AnnaData-Frontend-main` | Node 18+ / CRA | Render static site |

All three deploy together from `render.yaml`. AWS configs are committed too — see section 4.

---

## 1. Credentials

**Only `GEMINI_API_KEY` is required.** Every other integration is optional: without it the corresponding tool reports itself unavailable, the agent routes around it, and the advisory still goes out. `GET /health` on the backend tells you exactly what is configured.

| Credential | Unlocks | Without it | Cost | Where |
|---|---|---|---|---|
| `GEMINI_API_KEY` | **Everything** | Service refuses to start | Free tier is 20 req/day/model — see below | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |
| `GOV_API_KEY` | Mandi (market) prices | Advice omits prices | Free | [data.gov.in](https://data.gov.in/) → register → profile → API key |
| *(none needed)* | Place name → coordinates | — | Free | OpenStreetMap Nominatim, used by default. `LOCATION_API_KEY` switches to Google Maps if you want better handling of village names |
| `EE_SERVICE_KEY` | Soil texture / pH / carbon | Advice omits soil | Free (non-commercial) | [Earth Engine](https://earthengine.google.com/) → service account JSON, as one line |
| AWS Bedrock (`KNOWLEDGE_BASE_ID`, `AWS_ACCESS_KEY`, `AWS_SECRET_KEY`) | Govt schemes & cold storage answers | Those questions get general answers | Pay per query | Bedrock console, `ap-south-1` |
| SMS Gateway `APP_USERNAME` / `PASSWORD` | SMS channel | Web only | Free | The Android app, Cloud Server section |

Weather needs no key — Open-Meteo is open access.

### Gemini quota — the binding constraint

The free tier allows **20 `generateContent` requests per day, per model**. One farmer question costs **2 calls** (parse + answer), so a free-tier key serves roughly **10 questions per day in total** before every reply becomes the fallback apology. That is fine for testing and unusable in the field.

**Enable billing on the Google Cloud project behind the key before going live.** Nothing in the code changes; the same key stops being rate-limited.

Quota is tracked per model, so switching `TEXT_MODEL` gives a fresh 20/day — useful for testing, not a fix.

### Model selection

`gemini-2.0-flash` and the `gemini-2.5-*` line are **no longer callable by new API keys** — the API returns 404 pointing at the 3.x line. Current defaults:

`TEXT_MODELS` is a comma-separated chain, **fastest first**. Each model is tried in order; a quota (429), overload (503) or missing-model (404) error fails over to the next.

| Setting | Default |
|---|---|
| `TEXT_MODELS` | `gemini-3.1-flash-lite,gemini-3.6-flash,gemini-3.5-flash-lite` |
| `MEDIA_MODEL` | `gemini-3.5-flash-lite` |

Measured end-to-end agent latency: **2.4s mean** on the current primary, versus ~23s on `gemini-3.6-flash`. Answers stayed correct and specific across Hinglish, Devanagari and English in testing.

Two things make failover fast. The retry budget is bound as a *call* kwarg — `langchain_google_genai` reads `max_retries` from call kwargs and otherwise silently defaults to 6 attempts with exponential backoff, which stalls ~60s on a dead model before the next is tried. Bound correctly, the same failover takes ~1s. And because free-tier quota is **per model**, the chain also multiplies usable daily capacity.

Note that `models.list` reports models the key **cannot** actually invoke. Verify with a real `generateContent` call before changing these.

**Suggested order:** Gemini first (gets the whole thing working), then `GOV_API_KEY` (free, high value for farmers), then the SMS gateway credentials. Earth Engine, Maps, and Bedrock are refinements.

---

## 2. Run locally

```bash
# --- Backend ---
cd AnnaData-Backend-main
python -m venv .venv && .venv/Scripts/activate      # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                                # add GEMINI_API_KEY
uvicorn app:app --reload --port 8000
curl http://127.0.0.1:8000/health                   # confirms what is configured

# --- Frontend ---
cd AnnaData-Frontend-main
npm install
cp .env.example .env                                # REACT_APP_API_URL=http://127.0.0.1:8000
npm start

# --- SMS bridge ---
cd AnnaData-SMS-main
python -m venv .venv && .venv/Scripts/activate
pip install -r requirements.txt
cp .env.example .env                                # gateway creds + AI_ENDPOINT
uvicorn app:app --host 0.0.0.0 --port 5000
```

---

## 3. SMS setup (Cloud mode)

Cloud mode is the important change. The old setup needed the phone and the server on the same Wi-Fi plus a hand-started ngrok tunnel whose URL changed on every restart and had to be re-registered. In cloud mode the phone talks to `api.sms-gate.app` over mobile data from anywhere, and the bridge has a permanent App Runner URL.

1. Install [SMS Gateway for Android](https://github.com/capcom6/android-sms-gateway/releases) on a phone with an active SIM.
2. Grant `SEND_SMS`, `RECEIVE_SMS`, `READ_PHONE_STATE`.
3. Enable **Cloud Server** (not Local Server). Copy the username and password it shows.
4. Put those in the bridge's `APP_USERNAME` / `PASSWORD`, keep `SMS_MODE=cloud`.
5. Deploy the bridge (below). `PUBLIC_URL` is wired automatically by `render.yaml`.
6. Register the webhook, once:
   ```bash
   python webhook.py           # lists existing, registers if absent
   python webhook.py --list    # inspect only
   ```
7. Text the phone's number. A reply should arrive within ~10–30s.

Keep the phone charged, online, and excluded from battery optimisation — it is the actual SMS transmitter.

> **Note on cost and regulation:** each reply is a normal SMS from that SIM, billed by the mobile plan. This phone-as-gateway design deliberately avoids Indian A2P/DLT registration, which would otherwise require pre-approved templates and block free-form AI replies.

### Reply length and script

SMS billing depends on the alphabet. Latin text packs **153 characters per segment**; Devanagari, Gurmukhi, Telugu and Bengali force UCS-2 at **67 characters per segment** — so an identical-looking Hindi reply costs more than twice as much as an English one.

`MAX_SMS_SEGMENTS` (default `3`) caps *billed segments*, which is what actually controls cost across languages. `MAX_SMS_CHARS` remains a hard character ceiling. At the default, an Indic-script answer gets roughly 200 characters and a Latin one roughly 460; answers are trimmed at a sentence boundary, never mid-word.

---

## 4. Deploy

### Render (recommended first deploy)

No credit card, no AWS account, and `render.yaml` already describes all three
services, so this is one Blueprint rather than three separate setups.

**Step 1 — put the code on GitHub.** The repo is already initialised and
committed locally. Create an empty repo at [github.com/new](https://github.com/new)
(call it `AnnaData`, do *not* add a README or .gitignore), then:

```bash
cd "<this folder>"
git remote add origin https://github.com/<your-username>/AnnaData.git
git push -u origin main
```

**Step 2 — create the Blueprint.** At
[dashboard.render.com/blueprints](https://dashboard.render.com/blueprints) →
**New Blueprint Instance** → pick the repo. Render reads `render.yaml` and
offers all three services.

**Step 3 — fill in the secrets Render prompts for.** Only `GEMINI_API_KEY` is
required to get the web app working. `APP_USERNAME` and `PASSWORD` come from the
Android app and are needed for SMS. Leave the rest blank; `/health` will report
what is enabled.

**Step 4 — wire the services together.** Render cannot do this for you: its
`fromService` resolves to an *internal* hostname (the bare service name, with no
`.onrender.com`), which a browser cannot reach. Render also appends a random
suffix when a service name is already taken, so the public host is often not the
name in `render.yaml` — check the URL at the top of each service's page.

Using `<backend>` and `<frontend>` for the real public hosts:

| Service | Variable | Value | Then |
|---|---|---|---|
| `annadata-backend` | `FRONTEND_URL` | `https://<frontend>.onrender.com` | restart |
| `annadata-sms` | `AI_ENDPOINT` | `https://<backend>.onrender.com/agent` | restart |
| `annadata-sms` | `PUBLIC_URL` | `https://<sms-bridge>.onrender.com` | restart |
| `annadata-frontend` | `REACT_APP_API_URL` | `https://<backend>.onrender.com` | **redeploy** |

The frontend needs a full redeploy, not a restart: Create React App inlines env
vars at build time.

**Step 5 — register the SMS webhook** (see section 3).

### Keeping the services warm

Free Render instances sleep after ~15 minutes idle and take **~50s to wake**.
For SMS that means the first message after a quiet spell may time out at the
gateway. The gateway retries, and `messageId` deduplication stops a double
reply, but the farmer waits.

Point a free external pinger (for example [cron-job.org](https://cron-job.org))
at `https://<backend>/health` and `https://<sms-bridge>/health` every 10
minutes. Both endpoints are cheap and make no LLM calls.

Upgrading either service to Render's paid tier removes the sleep entirely.

### AWS (later)

`apprunner.yaml` and `amplify.yml` are committed and ready. Note that App Runner
and Amplify both bill from the first hour — there is no free tier — so this is
worth doing once the project is past the prototype stage.

- **Backend / SMS bridge → App Runner.** Create service → Source: GitHub → pick
  the repo, set the source directory to the service folder. It reads
  `apprunner.yaml`. Add env vars in the console; health check path `/health`.
- **Frontend → Amplify Hosting.** It reads `amplify.yml`. Set
  `REACT_APP_API_URL`, and add the SPA rewrite from `amplify-rewrites.json`.

`Dockerfile`s are included in both Python services for ECS/Fargate or any
container host.

---

## 5. Verifying a deploy

```bash
curl https://<backend>/health          # features map: which integrations are live
curl https://<sms-bridge>/health       # status "ok" or "degraded" + specific problems

curl -X POST https://<backend>/agent \
  -H "Content-Type: application/json" \
  -d "{\"query\":\"When should I sow wheat in Punjab?\"}"
```

Then send a real SMS to the gateway phone.

---

## 6. Known gaps

- **No authentication or rate limiting on `/agent`.** It is a public endpoint spending your Gemini and Bedrock budget. Add an API key or AWS WAF rate rule before publicising the URL.
- **Deduplication is in-memory**, so a bridge restart can allow one duplicate reply. Fine for a single instance; move to Redis/DynamoDB if you scale past one.
- **No conversation memory over SMS** — each message is answered standalone. The web app has history; SMS does not.
- **RCS is invisible to the gateway.** Google Messages sends Android-to-Android as RCS, not SMS, so those messages never reach the app and get no reply. Senders must disable chat features. Feature phones are unaffected.
- **Two LLM calls per question.** Halving this would double free-tier capacity and cut latency, but needs the parse and answer steps merged.
- **CRA is unmaintained.** It builds fine today; a Vite migration is the eventual path.
