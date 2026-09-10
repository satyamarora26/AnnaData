"""Measure real SSE arrival times without logging queries, answers or secrets."""
import argparse
import json
import time
import urllib.request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    request = urllib.request.Request(
        args.url.rstrip("/") + "/agent/stream",
        data=json.dumps({"query": "What are the eligibility rules and exclusions for the PM-KISAN scheme?",
                         "history": []}).encode(),
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
    )
    start = time.perf_counter()
    deltas, result, events = [], None, []
    with urllib.request.urlopen(request, timeout=180) as response:
        assert response.headers.get_content_type() == "text/event-stream"
        for line in response:
            if not line.startswith(b"data: "):
                continue
            event = json.loads(line[6:])
            elapsed = round(time.perf_counter() - start, 3)
            summary = {"type": event["type"], "seconds": elapsed}
            if event["type"] == "status":
                summary["stage"] = event["stage"]
            elif event["type"] == "delta":
                deltas.append(event["text"])
                summary["characters"] = len(event["text"])
            elif event["type"] == "result":
                result = event
                summary["characters"] = len(event["answer"])
                summary["tools_used"] = event["tools_used"]
            elif event["type"] == "error":
                raise RuntimeError("Stream reported a terminal error")
            events.append(summary)
            print(json.dumps(summary), flush=True)
    assert result and len(deltas) >= 2, "Expected multiple answer deltas and a final result"
    assert "kb" in result["tools_used"], "Expected source retrieval"
    assert ''.join(deltas).strip() == result["answer"].strip(), "Partial/final answers differ"
    first = next(event["seconds"] for event in events if event["type"] == "delta")
    assert first < events[-1]["seconds"], "Answer must start before completion"
    print(json.dumps({"verified": True, "first_text_seconds": first,
                      "total_seconds": events[-1]["seconds"], "text_updates": len(deltas)}))


if __name__ == "__main__":
    main()
