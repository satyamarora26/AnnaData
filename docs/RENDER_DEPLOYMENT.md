# Render Deployment: 2026-09-10

- Frontend: https://annadata-web.onrender.com
- Backend: https://annadata-api-ixsf.onrender.com
- Deployed branch: `codex/verified-ag-data`
- Deployed application commit: `8878f873fc760ba4b52fe61904b5c98b1e475cd6`
- Backend service: `srv-dahe2le743jc73cjb1qg`, free instance, Singapore.
- Frontend service: `srv-dahe4l6k1f9s73ffqg8g`, static site.

## Configuration

Backend uses Python 3.12.7, `pip install -r requirements.txt`, and:

```sh
uvicorn app:app --host 0.0.0.0 --port $PORT --workers 1 --no-proxy-headers
```

Health check: `/health`. `FRONTEND_URL` is the exact frontend HTTPS origin.
The user approved importing four backend secrets: `GEMINI_API_KEY`,
`DATABASE_URL`, `GOV_API_KEY`, and `EE_SERVICE_KEY`. They are stored on Render's
backend only. The temporary owner-readable import file was removed afterward.
`API_SERVICE_TOKEN` remains unset and no SMS service was deployed.

Frontend uses `AnnaData-Frontend-main`, `npm ci && npm run build`, publish path
`build`, and only `REACT_APP_API_URL` pointing at the public backend address.
The `/*` to `/index.html` rewrite enables single-page route fallback.

## Verification

- Backend `/health`: HTTP 200; configured database, Earth Engine, Gemini client,
  mandi and existing knowledge stores ready. Weather is unknown until queried.
- `/agent` CORS preflight: HTTP 200, allows the exact frontend origin.
- Unauthenticated `/feedback/summary`: HTTP 503, privileged access fails closed.
- Frontend `/verification-route`: HTTP 200 via the rewrite.
- Real browser chat passed at 1440x900 and 390x844 with no uncaught browser
  errors or page/composer overflow. Both answers included the expected PM-KISAN
  annual amount. Screenshots were visually reviewed.
- Observed UI smoke times were 15.03s and 24.25s. These two samples include
  browser/screenshot work and are not production latency percentiles.

Reproduction: set `APP_URL=https://annadata-web.onrender.com` and run
`tools/browser-smoke.cjs --live` as described in `BROWSER_TESTS.md`.
The saved report is `evidence/render-browser-report.json`.

## Limits

The free backend can sleep after inactivity; Render warns a wake-up can delay
requests by 50 seconds or more. API/provider quotas, one-process concurrency
limits and outstanding document-ingestion gates still apply. Public deployment
does not establish an SLA, field accuracy, or readiness for real SMS traffic.
