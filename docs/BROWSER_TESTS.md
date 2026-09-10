# Browser Verification

`tools/browser-smoke.cjs` exercises the actual React application in isolated
Chromium contexts at 1440x900 and 390x844. Location permission is denied by the
test so it never shares the operator's location with providers.

Install Playwright in a development/test environment, start the frontend and
backend, and wait for the backend `/health` response before running live mode:

```sh
node tools/browser-smoke.cjs
node tools/browser-smoke.cjs --live
node tools/browser-smoke.cjs --live --answer-stream
```

Use `APP_URL` for a different frontend URL, `EVIDENCE_DIR` for report/screenshot
output, and `CHROME_PATH` to use an existing Chrome executable. The default
evidence directory is `/tmp/annadata-browser-evidence`. `NODE_PATH` can point to
an existing runtime's Playwright package instead of installing another copy.

Default mode mocks only `/agent/stream`: it tests answer rendering, composer layout,
HTTP 429 error presentation, and recovery. Its timings are not backend latency.
Live mode performs two actual public chat requests, one per viewport. It checks
that a PM-KISAN answer is received, contains the expected annual amount, has no
service error, and the page/composer fit the viewport. Screenshots and a JSON
report are saved. Timings include the UI request/response and screenshot work;
they are smoke-test observations, not isolated API percentiles or a load test.

`--answer-stream` uses a longer scheme question, waits for partial answer text
while generation is pending, and captures `partial-1440.png` / `partial-390.png`.
It records observed time to first visible text separately from completion.
`python tools/check-answer-stream.py --url <backend-url>` measures individual SSE
arrival times with real providers. It verifies multiple text deltas before the
final response and matching partial/final text without logging answer content.

On 2026-09-10, both live viewports passed after backend startup completed. The
initial attempt ran before startup completed and correctly failed on a network
error. Both mocked viewports also passed answer/error/recovery checks. The live
screenshots were visually reviewed. No farmer records or SMS sends were used.
