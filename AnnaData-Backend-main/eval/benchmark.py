"""Synthetic offline guard/ranking regression benchmark, never live LLM latency."""
import argparse
import copy
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval.run import format_latency_summary, new_report, save_report  # noqa: E402


def passage(source, tier, similarity, scope=None):
    return {
        "source": source, "title": source, "authority": source,
        "url": f"https://example.gov.in/{source}",
        "content": "Synthetic benchmark guidance, not agricultural advice.",
        "tier": tier, "similarity": similarity, "scope": scope or {},
        "topics": ["fertiliser_nutrition"],
    }


CASES = [
    {
        "id": "guard_supported_fertilizer", "category": "guard",
        "answer": "Apply 55 kg DAP per acre at sowing.",
        "gathered": {
            "_guard_context": {"state": "Punjab", "crop": "wheat"},
            "_kb_passages": [{"tier": "extension", "content": "Apply 55 kg DAP per acre at sowing.",
                              "scope": {"states": ["Punjab"], "crops": ["wheat"]}}],
        },
        "changed": False, "contains": ["55 kg DAP"], "excludes": [],
    },
    {
        "id": "guard_unsupported_fertilizer", "category": "guard",
        "answer": "Apply 75 kg DAP per acre at sowing. Keep the field evenly moist.",
        "gathered": {}, "changed": True,
        "contains": ["Keep the field evenly moist."], "excludes": ["75 kg"],
    },
    {
        "id": "guard_reference_subsidy", "category": "guard",
        "answer": "The scheme pays a 50% subsidy. Ask the district office for eligibility.",
        "gathered": {"_kb_passages": [{"tier": "reference", "content": "A 50% subsidy is discussed."}]},
        "changed": True, "contains": ["district office"], "excludes": ["50%"],
    },
    {
        "id": "guard_preserves_observations", "category": "guard",
        "answer": "Soil pH is 8.0, rain was 25 mm, and recheck after 20 days.",
        "gathered": {}, "changed": False,
        "contains": ["8.0", "25 mm", "20 days"], "excludes": [],
    },
    {
        "id": "ranking_matching_scope", "category": "ranking",
        "rows": [passage("central", "official", 0.82), passage("local", "extension", 0.78,
                 {"states": ["Punjab"], "crops": ["wheat"]})],
        "state": "Punjab", "crop": "wheat", "expected_sources": ["local", "central"],
    },
    {
        "id": "ranking_rejects_wrong_scope", "category": "ranking",
        "rows": [passage("local", "extension", 0.92, {"states": ["Punjab"]})],
        "state": "West Bengal", "crop": "rice", "expected_sources": [],
    },
    {
        "id": "ranking_rejects_low_similarity", "category": "ranking",
        "rows": [passage("weak", "official", 0.69), passage("floor", "official", 0.70)],
        "state": None, "crop": None, "expected_sources": ["floor"],
    },
    {
        "id": "ranking_prefers_official", "category": "ranking",
        "rows": [passage("reference", "reference", 0.90), passage("official", "official", 0.75)],
        "state": None, "crop": None, "expected_sources": ["official", "reference"],
    },
]


def exercise(case):
    """Time only the real pure function; assertions run after the timer stops."""
    import knowledge
    import output_guards

    started = time.perf_counter()
    try:
        if case["category"] == "guard":
            actual = output_guards.scrub(case["answer"], case["gathered"])
        else:
            actual = knowledge.rank_passages(
                case["rows"], case["state"], case["crop"], min_similarity=0.70, limit=5,
            )
    except Exception as error:
        return time.perf_counter() - started, [], {
            "type": type(error).__name__, "message": str(error), "phase": case["category"],
        }
    elapsed = time.perf_counter() - started
    problems = []
    if case["category"] == "guard":
        text, changed = actual
        if changed != case["changed"]:
            problems.append(f"changed: expected {case['changed']}, got {changed}")
        for phrase in case["contains"]:
            if phrase not in text:
                problems.append(f"missing required phrase {phrase!r}")
        for phrase in case["excludes"]:
            if phrase in text:
                problems.append(f"retained forbidden phrase {phrase!r}")
    else:
        sources = [row["source"] for row in actual]
        if sources != case["expected_sources"]:
            problems.append(f"sources: expected {case['expected_sources']!r}, got {sources!r}")
    return elapsed, problems, None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--iterations", type=int, default=1,
                    help="repeat the same eight fixtures; repetitions are not independent cases")
    ap.add_argument("--output", type=Path, default=Path("eval/offline-results.json"))
    args = ap.parse_args(argv)
    if args.iterations < 1:
        ap.error("--iterations must be at least 1")

    report = new_report(
        "offline_guard_ranking", Path(__file__),
        "Synthetic in-process scrub/rank_passages wall time in seconds. Excludes imports, "
        "fixture copying, assertions and I/O. No LLM, embedding, vector search, database, "
        "network or HTTP/SMS transport; not live LLM latency. No warmup; fixed fixture order.",
    )
    report["unique_case_count"] = len(CASES)
    report["iterations"] = args.iterations
    report["selected_count"] = len(CASES) * args.iterations
    print("OFFLINE synthetic guard/ranking benchmark; not live LLM latency.")
    try:
        for iteration in range(1, args.iterations + 1):
            for fixture in CASES:
                elapsed, problems, error = exercise(copy.deepcopy(fixture))
                report["cases"].append({
                    "id": fixture["id"], "category": fixture["category"], "iteration": iteration,
                    "status": "error" if error else ("failed" if problems else "passed"),
                    "latency_seconds": elapsed, "assertion_failures": problems, "error": error,
                })
        report["status"] = "completed"
    except KeyboardInterrupt:
        report["status"] = "interrupted"
    except Exception as error:
        report["status"] = "error"
        report["run_error"] = {"type": type(error).__name__, "message": str(error)}
    finally:
        save_report(report, args.output)

    summary = report["summary"]
    print(f"{summary['passed']} passed, {summary['failed']} failed, {summary['errored']} errored; "
          f"{len(CASES)} unique fixtures x {args.iterations} iteration(s)")
    print(format_latency_summary([r["latency_seconds"] for r in report["cases"]]))
    print(f"JSON report: {args.output}")
    if report["status"] == "interrupted":
        return 130
    return 0 if report["status"] == "completed" and not (summary["failed"] or summary["errored"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
