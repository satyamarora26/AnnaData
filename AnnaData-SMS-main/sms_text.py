"""
Turn the agent's markdown answer into something sendable as SMS.

The backend replies in markdown (headers, bold, bullet lists, tables). Sent raw
over SMS a farmer sees literal ** and ## noise, so it is flattened to plain text
and trimmed to a whole sentence within the length budget.
"""
import re

# Non-GSM-7 characters cost double (UCS-2). Replace the common markdown ones.
REPLACEMENTS = {
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", "…": "...", "•": "-",
    "₹": "Rs ", " ": " ",
}


def to_plain_text(text: str) -> str:
    """Flatten markdown to plain text."""
    if not text:
        return ""

    # Fenced code blocks and inline code.
    text = re.sub(r"```[a-zA-Z]*\n?", "", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)

    # Images and links: keep the label, drop the target.
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)

    # Headers, blockquotes, horizontal rules.
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s{0,3}>\s?", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s{0,3}([-*_])\s*\1\s*\1[-*_\s]*$", "", text, flags=re.MULTILINE)

    # Bold / italic / strikethrough markers.
    text = re.sub(r"(\*\*\*|___)(.+?)\1", r"\2", text, flags=re.DOTALL)
    text = re.sub(r"(\*\*|__)(.+?)\1", r"\2", text, flags=re.DOTALL)
    text = re.sub(r"(?<!\w)([*_])(?!\s)(.+?)(?<!\s)\1(?!\w)", r"\2", text, flags=re.DOTALL)
    text = re.sub(r"~~(.+?)~~", r"\1", text, flags=re.DOTALL)

    # Bullet and numbered list markers.
    text = re.sub(r"^\s*[-*+]\s+", "- ", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*(\d+)[.)]\s+", r"\1. ", text, flags=re.MULTILINE)

    # Table pipes.
    text = re.sub(r"^\s*\|?[\s:|-]+\|[\s:|-]*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s*\|\s*", " ", text)

    for src, dst in REPLACEMENTS.items():
        text = text.replace(src, dst)

    # Collapse whitespace, keeping paragraph breaks as single newlines.
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return "\n".join(line.strip() for line in text.splitlines() if line.strip()).strip()


# --- SMS segmentation -------------------------------------------------------
# A message in the GSM-7 alphabet packs 160 characters into one segment (153
# when concatenated). Anything outside it - Devanagari, Gurmukhi, Telugu,
# Bengali - forces UCS-2, which drops that to 70 (67 concatenated). Capping on
# raw character count therefore bills far more for Indic scripts than for
# Latin, so the cap is applied in segments instead.

GSM7_BASIC = (
    "@£$¥èéùìòÇ\nØø\rÅå"
    "Δ_ΦΓΛΩΠΨΣΘΞÆæßÉ"
    " !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§"
    "¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
# These occupy two GSM-7 positions each.
GSM7_EXTENDED = "^{}\\[~]|€"

_GSM7_BASIC_SET = set(GSM7_BASIC)
_GSM7_EXTENDED_SET = set(GSM7_EXTENDED)

SINGLE_GSM7, MULTI_GSM7 = 160, 153
SINGLE_UCS2, MULTI_UCS2 = 70, 67


def is_gsm7(text: str) -> bool:
    return all(c in _GSM7_BASIC_SET or c in _GSM7_EXTENDED_SET for c in text)


def gsm7_length(text: str) -> int:
    return sum(2 if c in _GSM7_EXTENDED_SET else 1 for c in text)


def segment_count(text: str) -> int:
    """Number of SMS segments `text` will actually be billed as."""
    if not text:
        return 0
    if is_gsm7(text):
        length, single, multi = gsm7_length(text), SINGLE_GSM7, MULTI_GSM7
    else:
        length, single, multi = len(text), SINGLE_UCS2, MULTI_UCS2
    return 1 if length <= single else -(-length // multi)


def chars_for_segments(text: str, max_segments: int) -> int:
    """How many characters of `text` fit within `max_segments` segments."""
    if max_segments < 1:
        max_segments = 1
    if is_gsm7(text):
        return SINGLE_GSM7 if max_segments == 1 else MULTI_GSM7 * max_segments
    return SINGLE_UCS2 if max_segments == 1 else MULTI_UCS2 * max_segments


def truncate(text: str, limit: int) -> str:
    """Trim to `limit` characters, preferring a sentence or line boundary."""
    if len(text) <= limit:
        return text

    window = text[: limit - 3]

    # A complete instruction that stops early beats a longer one cut mid-dose,
    # so a sentence boundary is accepted well before the other break types.
    sentence_end = max(
        window.rfind(". "), window.rfind("। "), window.rfind("।"),
        window.rfind("? "), window.rfind("! "),
    )
    if sentence_end > limit * 0.35:
        return window[: sentence_end + 1].strip()

    for candidate in (window.rfind("\n"), window.rfind(" ")):
        if candidate > limit * 0.6:
            return window[: candidate + 1].strip() + "..."

    return window.strip() + "..."


def prepare(text: str, limit: int, max_segments: int = 0, suffix: str = "") -> str:
    """Full pipeline: markdown answer -> SMS-ready body.

    `limit` is a hard character ceiling. `max_segments`, when > 0, additionally
    caps billed SMS segments - which is what a character ceiling alone fails to
    control for non-Latin scripts.

    `suffix` is appended within the same budget rather than on top of it, so a
    trailing prompt cannot quietly push the message into another paid segment.
    """
    plain = to_plain_text(text)
    if max_segments > 0:
        limit = min(limit, chars_for_segments(plain + suffix, max_segments))

    if suffix:
        room = limit - len(suffix) - 1
        if room < 40:            # no useful answer would survive; drop the suffix
            return truncate(plain, limit)
        return truncate(plain, room) + "\n" + suffix

    return truncate(plain, limit)
