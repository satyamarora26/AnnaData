"""
Run the evaluation cases against the agent.

Every bug this project has shipped was found by a person reading an SMS
screenshot. That does not scale, and it is why regressions survived several
commits before anyone noticed - an English question answered in Hindi, a dose
quoted for a pest with no registered treatment, a farmer asked for a crop
already on file. This turns those into assertions a change can be judged
against before it reaches anyone.

    python eval/run.py                     # everything
    python eval/run.py --tag safety        # one group
    python eval/run.py --tag fast --limit 5
    python eval/run.py --id dose_refused_when_unregistered
    python eval/run.py --tag safety --output /tmp/live-eval.json

Each case costs two model calls, so the whole suite does not fit inside the
free tier's daily quota. Tags exist so a change can be checked against the
cases it might plausibly have broken.
"""
import argparse
import hashlib
import json
import math
import platform
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

sys.path.append(str(Path(__file__).resolve().parent.parent.parent / "AnnaData-SMS-main"))
try:
    from sms_text import segment_count, to_plain_text
except ImportError:  # the bridge is a separate service; degrade rather than fail
    segment_count = None
    to_plain_text = lambda t: t  # noqa: E731

CASES = Path(__file__).parent / "cases.yaml"

# A dose is a quantity with a unit. Used to assert that no dose is given where
# nothing is registered - the check that matters most.
DOSE_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:-|–|to)?\s*\d*(?:\.\d+)?\s*"
    r"(g|gm|gram|grams|kg|ml|millilitre|milliliter|litre|liter|l)\b"
    r"(?!\s*(?:of\s+)?water)",
    re.IGNORECASE,
)

MARKDOWN_RE = re.compile(r"(\*\*|##|\* |^- |\|)", re.MULTILINE)


class Failure(Exception):
    pass


def check(name: str, condition: bool, detail: str = ""):
    if not condition:
        raise Failure(f"{name}{': ' + detail if detail else ''}")


def latency_summary(latencies: list[float]) -> dict:
    """Nearest-rank percentiles; no samples means null, never a measured zero."""
    ordered = sorted(latencies)
    count = len(ordered)
    return {
        "sample_count": count,
        "method": "nearest_rank",
        "mean_seconds": sum(ordered) / count if count else None,
        "p50_seconds": ordered[math.ceil(0.50 * count) - 1] if count else None,
        "p95_seconds": ordered[math.ceil(0.95 * count) - 1] if count else None,
        "max_seconds": ordered[-1] if count else None,
    }


def format_latency_summary(latencies: list[float]) -> str:
    if not latencies:
        return "latency: no case timings recorded"
    summary = latency_summary(latencies)
    return (
        f"latency: mean={summary['mean_seconds']:.2f}s, "
        f"p50={summary['p50_seconds']:.2f}s, p95={summary['p95_seconds']:.2f}s, "
        f"max={summary['max_seconds']:.2f}s across {len(latencies)} case(s)"
    )


def new_report(mode: str, source: Path, timing_scope: str) -> dict:
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=Path(__file__).parent,
            text=True, stderr=subprocess.DEVNULL,
        ).strip()
        dirty = bool(subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=Path(__file__).parent,
            text=True, stderr=subprocess.DEVNULL,
        ).strip())
    except (OSError, subprocess.CalledProcessError):
        commit, dirty = None, None
    return {
        "schema_version": 1,
        "mode": mode,
        "status": "incomplete",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "environment": {"python": platform.python_version(), "platform": platform.platform()},
        "git": {"commit": commit, "dirty": dirty},
        "case_source": {"path": str(source), "sha256": hashlib.sha256(source.read_bytes()).hexdigest()},
        "timing_scope": timing_scope,
        "cases": [],
        "run_error": None,
    }


def save_report(report: dict, output: Path) -> None:
    records = report["cases"]
    passed = sum(r["status"] == "passed" for r in records)
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    report["summary"] = {
        "attempted": len(records),
        "passed": passed,
        "failed": sum(r["status"] == "failed" for r in records),
        "errored": sum(r["status"] == "error" for r in records),
        "pass_rate": passed / len(records) if records else None,
        "latency": latency_summary([r["latency_seconds"] for r in records]),
        "completed_latency": latency_summary([
            r["latency_seconds"] for r in records if r["status"] in {"passed", "failed"}
        ]),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def evaluate(case: dict, result) -> list[str]:
    """Return the list of assertion failures for one case."""
    expect = case.get("expect") or {}
    answer = result.answer or ""
    plain = to_plain_text(answer)
    problems = []

    def fail(msg):
        problems.append(msg)

    if "script" in expect:
        from Agent import script_of

        got = script_of(answer)
        if got != expect["script"]:
            fail(f"script: expected {expect['script']}, got {got}")

    if "message_type" in expect and result.message_type != expect["message_type"]:
        fail(f"message_type: expected {expect['message_type']}, got {result.message_type}")

    if "intent" in expect and result.intent != expect["intent"]:
        fail(f"intent: expected {expect['intent']}, got {result.intent}")

    for tool in expect.get("tools_include", []):
        if tool not in result.tools_used:
            fail(f"tools: expected {tool}, ran {result.tools_used}")

    for tool in expect.get("tools_exclude", []):
        if tool in result.tools_used:
            fail(f"tools: {tool} should not have run")

    for slot in expect.get("missing_includes", []):
        if slot not in result.missing_slots:
            fail(f"missing_slots: expected {slot}, got {result.missing_slots}")

    if "contains_any" in expect:
        wanted = expect["contains_any"]
        if not any(w.lower() in answer.lower() for w in wanted):
            fail(f"expected one of {wanted}")

    for phrase in expect.get("contains_all", []):
        if phrase.lower() not in answer.lower():
            fail(f"missing required phrase {phrase!r}")

    for phrase in expect.get("not_contains", []):
        if phrase.lower() in answer.lower():
            fail(f"should not contain {phrase!r}")

    if "not_contains_any" in expect:
        unwanted = expect["not_contains_any"]
        if any(phrase.lower() in answer.lower() for phrase in unwanted):
            fail(f"should not contain any of {unwanted}")

    if expect.get("no_dose"):
        hit = DOSE_RE.search(answer)
        if hit:
            fail(f"quoted a dose where none is registered: {hit.group(0)!r}")

    # A support price is a figure a farmer may sell against, so an unbacked one
    # is not a style problem. Mentioning the scheme without a number is allowed;
    # stating a number for a scheme we hold no figure for is not.
    if expect.get("no_support_price_figure"):
        import output_guards
        for sentence in output_guards._sentences(answer):
            if output_guards.PRICE_SCHEME.search(sentence) and output_guards.FIGURE.search(sentence):
                fail(f"stated an unbacked support price: {sentence.strip()!r}")

    # A subsidy amount is a figure a farmer travels to claim. Naming the scheme
    # without one is fine; naming it with one that nothing retrieved supports is
    # what the guard exists to stop.
    if expect.get("no_scheme_figure"):
        import output_guards
        for sentence in output_guards._sentences(answer):
            if output_guards.WELFARE_SCHEME.search(sentence) and output_guards.FIGURE.search(sentence):
                fail(f"stated an unsupported scheme figure: {sentence.strip()!r}")

    if expect.get("has_number") and not re.search(r"\d", answer):
        fail("expected a figure in the answer, found none")

    if expect.get("no_markdown") and MARKDOWN_RE.search(answer):
        fail("markdown leaked into an SMS answer")

    if expect.get("complete_sentence"):
        end = plain.rstrip()[-1:] if plain.strip() else ""
        if end not in ".।?!":
            fail(f"answer does not end on a complete sentence (ends {end!r})")

    if "max_sms_segments" in expect and segment_count:
        segs = segment_count(plain)
        if segs > expect["max_sms_segments"]:
            fail(f"segments: {segs} > {expect['max_sms_segments']}")

    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", help="only cases carrying this tag")
    ap.add_argument("--id", help="only this case")
    ap.add_argument("--limit", type=int, help="stop after N cases")
    ap.add_argument("--delay", type=float, default=0,
                    help="seconds to sleep between live cases")
    ap.add_argument("--verbose", action="store_true", help="print every answer")
    ap.add_argument("--output", type=Path, default=Path("eval/results.json"),
                    help="JSON report path (overwritten; default: eval/results.json)")
    args = ap.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        ap.error("--limit must be at least 1")
    if not math.isfinite(args.delay) or args.delay < 0:
        ap.error("--delay must be finite and nonnegative")

    cases = yaml.safe_load(CASES.read_text(encoding="utf-8")) or []
    if args.tag:
        cases = [c for c in cases if args.tag in (c.get("tags") or [])]
    if args.id:
        cases = [c for c in cases if c["id"] == args.id]
    if args.limit is not None:
        cases = cases[: args.limit]

    report = new_report(
        "live_agent", CASES,
        "run_agent wall time in seconds, including tools/retries; excludes startup, delay, "
        "assertions and report I/O. Not isolated LLM or deployed HTTP/SMS latency.",
    )
    report["selection"] = {"tag": args.tag, "id": args.id, "limit": args.limit, "delay_seconds": args.delay}
    report["selected_count"] = len(cases)
    if not cases:
        report["status"] = "no_cases"
        save_report(report, args.output)
        print("No cases matched.")
        return 1

    database = None
    try:
        # Loading reporting helpers or --help must not construct model clients.
        import db
        import startup
        from Agent import run_agent
        from config import TEXT_MODELS

        database = db
        report["configured_models"] = list(TEXT_MODELS)
        db.init()
        startup.init_earth_engine()
        print(f"Running {len(cases)} live case(s); model call count is not measured.\n")
        for index, case in enumerate(cases):
            if index and args.delay:
                time.sleep(args.delay)
            profile = case.get("profile")
            record = {"id": case["id"], "tags": case.get("tags") or [],
                      "status": "error", "assertion_failures": [], "error": None}
            phase = "agent"
            started = time.perf_counter()
            try:
                result = run_agent(
                    query=case["query"],
                    latitude=(profile or {}).get("latitude"),
                    longitude=(profile or {}).get("longitude"),
                    history=case.get("history"),
                    channel=case.get("channel", "sms"),
                    profile=profile,
                )
                record["latency_seconds"] = time.perf_counter() - started
                phase = "assertions"
                record["assertion_failures"] = evaluate(case, result)
                record["status"] = "failed" if record["assertion_failures"] else "passed"
                if args.verbose or record["status"] == "failed":
                    print(f"           answer: {(result.answer or '')[:150]}")
            except Exception as error:
                if phase == "agent":
                    record["latency_seconds"] = time.perf_counter() - started
                record["error"] = {"type": type(error).__name__, "message": str(error), "phase": phase}
            report["cases"].append(record)
            print(f"  {record['status']:6} {case['id']} ({record['latency_seconds']:.2f}s)")
            for problem in record["assertion_failures"]:
                print(f"           - {problem}")
            if record["error"]:
                print(f"           {record['error']['type']}: {record['error']['message'][:110]}")
        report["status"] = "completed"
    except KeyboardInterrupt:
        report["status"] = "interrupted"
    except Exception as error:
        report["status"] = "error"
        report["run_error"] = {"type": type(error).__name__, "message": str(error)}
        print(f"Run error: {type(error).__name__}: {error}")
    finally:
        try:
            if database is not None:
                database.close()
        finally:
            save_report(report, args.output)

    summary = report["summary"]
    print(f"\n{summary['passed']} passed, {summary['failed']} failed, {summary['errored']} errored")
    print(format_latency_summary([r["latency_seconds"] for r in report["cases"]]))
    print(f"JSON report: {args.output}")
    if report["status"] == "interrupted":
        return 130
    return 0 if report["status"] == "completed" and not (summary["failed"] or summary["errored"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
