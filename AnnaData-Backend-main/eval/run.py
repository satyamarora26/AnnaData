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

Each case costs two model calls, so the whole suite does not fit inside the
free tier's daily quota. Tags exist so a change can be checked against the
cases it might plausibly have broken.
"""
import argparse
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402
import startup  # noqa: E402
from Agent import run_agent, script_of  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "AnnaData-SMS-main"))
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


def evaluate(case: dict, result) -> list[str]:
    """Return the list of assertion failures for one case."""
    expect = case.get("expect") or {}
    answer = result.answer or ""
    plain = to_plain_text(answer)
    problems = []

    def fail(msg):
        problems.append(msg)

    if "script" in expect:
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", help="only cases carrying this tag")
    ap.add_argument("--id", help="only this case")
    ap.add_argument("--limit", type=int, help="stop after N cases")
    ap.add_argument("--verbose", action="store_true", help="print every answer")
    args = ap.parse_args()

    cases = yaml.safe_load(CASES.read_text(encoding="utf-8"))
    if args.tag:
        cases = [c for c in cases if args.tag in (c.get("tags") or [])]
    if args.id:
        cases = [c for c in cases if c["id"] == args.id]
    if args.limit:
        cases = cases[: args.limit]

    if not cases:
        print("No cases matched.")
        return 1

    db.init()
    startup.init_earth_engine()

    print(f"Running {len(cases)} case(s)  (~{len(cases) * 2} model calls)\n")

    passed = failed = errored = 0
    failures = []

    for case in cases:
        profile = case.get("profile")
        try:
            result = run_agent(
                query=case["query"],
                latitude=(profile or {}).get("latitude"),
                longitude=(profile or {}).get("longitude"),
                history=case.get("history"),
                channel=case.get("channel", "sms"),
                profile=profile,
            )
        except Exception as e:
            errored += 1
            print(f"  ERROR  {case['id']}: {type(e).__name__}: {str(e)[:110]}")
            continue

        problems = evaluate(case, result)
        if problems:
            failed += 1
            print(f"  FAIL   {case['id']}")
            for p in problems:
                print(f"           - {p}")
            print(f"           answer: {result.answer[:150]}")
            failures.append(case["id"])
        else:
            passed += 1
            print(f"  pass   {case['id']}")
            if args.verbose:
                print(f"           {result.answer[:150]}")

    print(f"\n{passed} passed, {failed} failed, {errored} errored")
    if failures:
        print("failing: " + ", ".join(failures))
    db.close()
    return 0 if not (failed or errored) else 1


if __name__ == "__main__":
    raise SystemExit(main())
