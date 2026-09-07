"""
Removing figures the retrieved data does not support.

Twice now a prompt instruction has failed to stop the model stating a number it
had no basis for, and both times the fix was to check the output in code rather
than to word the request better.

The first was a support price. Asked for one it did not hold, the model wrote
"the Fair and Remunerative Price for the 2026-27 season is 340 rupees per
quintal" - fluent, specific, invented, and produced despite an explicit
prohibition in the same prompt. The second was a subsidy: retrieval correctly
reported that it covered nothing about solar pumps, the prompt correctly said to
invent no amount, and a percentage appeared anyway in two runs out of four.

The pattern is the same both times. A negative constraint expressed in the same
channel as the generation competes with a fluent continuation, and loses often
enough to matter. Where a figure will be acted upon - a price a harvest is sold
against, a subsidy a farmer travels to claim - the constraint belongs in the
deterministic layer around the model.

Both checks work the same way: a sentence asserting a *scheme or price and a
number together* is removed when nothing retrieved backs it. Sentences that name
the thing without a figure survive, because telling a farmer to ask at the sugar
mill or the district office is useful and true.
"""
import re

# --- what counts as a claim -------------------------------------------------

PRICE_SCHEME = re.compile(
    r"\b(msp|frp|minimum support price|fair and remunerative|"
    r"support price|procurement price|floor price|samarthan mulya)\b",
    re.I,
)

WELFARE_SCHEME = re.compile(
    r"\b(scheme|yojana|yojna|subsidy|subsid(?:ies|ised|y)|anudan|"
    r"pm-?kisan|pmfby|kcc|kisan credit card|fasal bima|crop insurance|"
    r"soil health card|kusum|sarkari yojana)\b",
    re.I,
)

# A quantity that reads as money or a proportion. Bare digits are deliberately
# not enough: a helpline number and a district office address both contain them,
# and removing those sentences would cost the farmer the one useful thing left
# in a refusal.
FIGURE = re.compile(
    r"(?:₹|\brs\.?\b|\binr\b)\s*[\d०-९][\d,०-९]*"
    r"|[\d०-९][\d,०-९]*\s*(?:%|percent|per\s?cent|pratishat|"
    r"rupees?|rupaye|rupaiya|lakh|crore|/-)",
    re.I,
)

# Retrieval says this, verbatim, when nothing it found addresses the question.
KB_UNCOVERED = "The passages retrieved do not answer this specific question"

PRICE_FALLBACK = (
    "I do not have the official support price for this crop on record. "
    "Your local APMC mandi can give you the current rate, and for sugarcane "
    "the cooperative sugar mill publishes the price it pays."
)
SCHEME_FALLBACK = (
    "I do not have the amounts or eligibility rules for that scheme on record. "
    "Your district agriculture office or nearest Krishi Vigyan Kendra can give "
    "you the current figures."
)


def _sentences(text: str) -> list[str]:
    return re.split(r"(?<=[.!?।])\s+", text)


def _strip(answer: str, subject: re.Pattern, fallback: str) -> tuple[str, bool]:
    """Drop sentences asserting both the subject and a figure."""
    kept, removed = [], False
    for sentence in _sentences(answer):
        if subject.search(sentence) and FIGURE.search(sentence):
            removed = True
            continue
        kept.append(sentence)

    if not removed:
        return answer, False

    text = " ".join(s for s in kept if s.strip()).strip()
    # Stripping can leave a reply that no longer answers anything.
    if len(text) < 40:
        text = fallback
    return text, True


def scrub(answer: str, gathered: dict) -> tuple[str, bool]:
    """Remove price and scheme figures that nothing retrieved supports.

    Returns the answer and whether anything was removed. Each check is applied
    only when its own evidence is missing, so a retrieved figure is never
    stripped: a farmer who asks what PM-KISAN pays, and whose question retrieval
    actually covered, still gets the amount.
    """
    if not answer:
        return answer, False

    changed = False

    # A support price, with none retrieved for this crop.
    if not gathered.get("msp"):
        answer, hit = _strip(answer, PRICE_SCHEME, PRICE_FALLBACK)
        changed = changed or hit

    # A subsidy or scheme amount, where retrieval ran and covered nothing. If
    # retrieval was not consulted at all there is no claim to check against, and
    # if it returned usable passages the figure may well be theirs.
    kb = gathered.get("kb") or ""
    if kb.startswith(KB_UNCOVERED):
        answer, hit = _strip(answer, WELFARE_SCHEME, SCHEME_FALLBACK)
        changed = changed or hit

    return answer, changed
