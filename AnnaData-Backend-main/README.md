# AnnaData Backend

The agent API. Takes a farmer's question (any language), gathers real data about their location and crop, and returns practical advice in the same language.

Deployment is covered in the [root runbook](../README.md). This file covers the service itself.

## How a query is answered

```
query ──▶ rewrite as standalone (using history)
      ──▶ extract location / state / crop
      ──▶ non-agricultural?  ──▶ answer directly
      ──▶ schemes or storage? ──▶ Bedrock knowledge base
      ──▶ geocode ──▶ soil + weather + mandi prices ──▶ compose advisory
      ──▶ no coordinates?  ──▶ general agronomic answer
```

| Module | Role | Needs |
|---|---|---|
| `Refined_Farmer_Query.py` | Rewrites a follow-up into a standalone question | Gemini |
| `Query_Parser.py` | Extracts location, state, crop | Gemini |
| `Address_Convertor.py` | Place name → coordinates | `LOCATION_API_KEY` |
| `Soil_Tool.py` | Texture, pH, organic carbon | `EE_SERVICE_KEY` |
| `weather_tool.py` | 30 days history + 7 day forecast | nothing (Open-Meteo) |
| `Mandi_Price_Tool.py` | Market prices by state and commodity | `GOV_API_KEY` |
| `Web_Crawler.py` | Govt schemes / cold storage | AWS Bedrock |
| `Agent.py` | Orchestrates all of the above | — |

**Every tool except Gemini is optional.** A missing key disables only that tool: it returns an "unavailable" note, the prompt tells the model to ignore it, and the answer still goes out. Check `GET /health` to see what is live.

## Configuration

Copy `.env.example` to `.env`. Only `GEMINI_API_KEY` is required; the file documents each optional key and what it unlocks.

## Run

```bash
python -m venv .venv && .venv/Scripts/activate   # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                             # add GEMINI_API_KEY
uvicorn app:app --reload --port 8000
```

```bash
curl http://127.0.0.1:8000/health
```

## Endpoints

| Method | Path | Body | Returns |
|---|---|---|---|
| `GET` | `/` | — | Liveness message |
| `GET` | `/health` | — | Which integrations are configured |
| `POST` | `/agent` | `{query, latitude?, longitude?, history?}` | `{answer}` |
| `POST` | `/api/chat/describe` | multipart `audio` and/or `image` | `{Result}` |

`/agent` returns `400` for an empty query and `502` if the agent fails — it does not return a `200` with an error body, so callers can tell success from failure.

Example:

```bash
curl -X POST http://127.0.0.1:8000/agent \
  -H "Content-Type: application/json" \
  -d "{\"query\":\"When should I sow wheat in Punjab?\"}"
```

## Notes

- `history` is `[{"role": "user"|"assistant", "content": "..."}]`.
- CORS allows `localhost:3000` plus `FRONTEND_URL` and anything in `CORS_ORIGINS`.
- `MANDI_MAX_RECORDS` caps how many market rows enter the prompt. Raising it increases latency and token cost noticeably.
- There is no authentication or rate limiting on `/agent`. Add one before making the URL public.
