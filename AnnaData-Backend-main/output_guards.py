"""Deterministic checks for figures a farmer could act on."""
import logging
import re


LOGGER = logging.getLogger(__name__)

PRICE_SCHEME = re.compile(
    r"\b(msp|frp|minimum support price|fair and remunerative|"
    r"support price|procurement price|floor price|samarthan mulya)\b",
    re.I,
)
WELFARE_SCHEME = re.compile(
    r"\b(scheme|yojana|yojna|subsidy|subsid(?:ies|ised|y)|anudan|"
    r"pm-?kisan|pmfby|kcc|kisan credit card|fasal bima|crop insurance|"
    r"soil health card|kusum|sarkari yojana|eligib(?:ility|le))\b",
    re.I,
)
LEGAL_QUANTITY = re.compile(
    r"\b(?:legal|lawful|regulation|regulatory|statutory|permitted|allowed|"
    r"maximum|minimum|limit|prohibited|banned)\b",
    re.I,
)

_MATERIAL = (
    r"zinc\s+(?:sulphate|sulfate)|urea|dap|mop|ssp|potash|nitrogen|"
    r"phosphorus|potassium|fertili[sz]er|manure|compost"
)
_AMOUNT = r"\d+(?:[.,]\d+)?(?:\s*(?:-|–|to)\s*\d+(?:[.,]\d+)?)?"
_UNIT = r"kg|kgs|kilograms?|g|gm|grams?|l|lit(?:re|er)s?|ml|millilit(?:re|er)s?|tonnes?"
_BASIS = r"acres?|ha|hectares?|l|lit(?:re|er)s?"

FERTILIZER_SUBJECT = re.compile(rf"\b(?:{_MATERIAL})\b", re.I)
PESTICIDE_SUBJECT = re.compile(
    r"\b(?:spray(?:ed|ing|s)?|appl(?:y|ied|ying|ies)|use(?:d|ing|s)?|"
    r"pesticide|insecticide|fungicide|herbicide|chemical|registered\s+pesticide|dose)\b",
    re.I,
)

_QUANTITY_WITH_BASIS = re.compile(
    rf"(?:(?P<before>{_MATERIAL})\s+(?:at\s+)?)?"
    rf"(?P<amount>{_AMOUNT})\s*(?P<unit>{_UNIT})"
    rf"(?:\s+(?:of\s+)?(?P<after>{_MATERIAL}))?"
    rf"\s*(?:per|/)\s*(?P<basis>{_BASIS})\b",
    re.I,
)
_MATERIAL_BEFORE_QUANTITY = re.compile(
    rf"(?P<material>{_MATERIAL})\s+(?:at\s+)?"
    rf"(?P<amount>{_AMOUNT})\s*(?P<unit>{_UNIT})\b",
    re.I,
)
_MATERIAL_AFTER_QUANTITY = re.compile(
    rf"(?P<amount>{_AMOUNT})\s*(?P<unit>{_UNIT})\s+(?:of\s+)?"
    rf"(?P<material>{_MATERIAL})\b",
    re.I,
)
_WAITING_PERIOD = re.compile(
    r"\b(?:wait\s+(?P<wait>\d+)\s+days?\s+before\s+harvest|"
    r"pre-?harvest\s+interval\s+(?:of\s+)?(?P<interval>\d+)\s+days?)\b",
    re.I,
)
_PRODUCT_RECOMMENDATION = re.compile(
    r"\b(?:spray(?:ed|ing|s)?|appl(?:y|ied|ying|ies)|use(?:d|ing|s)?)\s+(?P<product>.+?)"
    r"(?=\s+(?:at|@|for|on)\b|,|[.!?]|$)",
    re.I,
)

_MONEY_PREFIX = re.compile(r"(?:₹|\brs\.?(?=\s|\d|$)|\binr\b)\s*([\d,]+(?:\.\d+)?)", re.I)
_MONEY_SUFFIX = re.compile(
    r"\b([\d,]+(?:\.\d+)?)\s*(?:rupees?|rupaye|rupaiya|/-)\b", re.I
)
_PERCENTAGE = re.compile(
    r"\b(\d+(?:\.\d+)?)\s*(?:%|percent|per\s?cent|pratishat)(?=\s|$|[,.!?])",
    re.I,
)
FIGURE = re.compile(
    r"(?:₹|\brs\.?(?=\s|\d|$)|\binr\b)\s*[\d,]+(?:\.\d+)?|"
    r"\b[\d,]+(?:\.\d+)?\s*%(?=\s|$|[,.!?])|"
    r"\b[\d,]+(?:\.\d+)?\s*(?:percent|per\s?cent|pratishat|rupees?|rupaye|rupaiya|/-)\b",
    re.I,
)

ACTION_FALLBACK = (
    "Please contact your nearest Krishi Vigyan Kendra or agriculture officer "
    "for a verified recommendation."
)
FERTILIZER_REFERRAL = (
    "Get a soil test and ask your nearest Krishi Vigyan Kendra or agriculture officer "
    "for a field-specific recommendation."
)
_EXACT_FERTILIZER_REQUEST = re.compile(
    r"\b(?:exact(?:ly)?|how\s+much|quantity|amount|application\s+rate|dose)\b",
    re.I,
)
_ABBREVIATION_PERIOD = re.compile(r"\b(?:rs|mr|mrs|ms|dr|prof|sr|jr|no|etc)\.", re.I)
_PROTECTED_PERIOD = "\u0000"


def _is_soft_input_wrap(left: str, right: str) -> bool:
    if (
        not left.strip()
        or not right.strip()
        or left.rstrip().endswith((".", "!", "?", "।"))
        or right.lstrip().startswith(("-", "*", "•"))
    ):
        return False
    joined_claims = extract_input_claims(f"{left.rstrip()} {right.lstrip()}")
    separate_claims = extract_input_claims(left) | extract_input_claims(right)
    return bool(joined_claims - separate_claims)


def _join_soft_input_wraps(text: str) -> str:
    lines = text.splitlines()
    joined = []
    for line in lines:
        if joined and _is_soft_input_wrap(joined[-1], line):
            joined[-1] = f"{joined[-1].rstrip()} {line.lstrip()}"
        else:
            joined.append(line)
    return "\n".join(joined)


def _sentences(text: str) -> list[str]:
    protected = _ABBREVIATION_PERIOD.sub(
        lambda match: f"{match.group()[:-1]}{_PROTECTED_PERIOD}", text
    )
    protected = _join_soft_input_wraps(protected)
    return [
        sentence.replace(_PROTECTED_PERIOD, ".").strip()
        for sentence in re.split(r"\r?\n+|(?<=[.!?।])\s+", protected)
        if sentence.strip()
    ]


def _normalise_space(value: str) -> str:
    return " ".join(value.lower().split())


def _normalise_amount(value: str) -> str:
    value = value.replace(",", "").replace("–", "-")
    return re.sub(r"\s*(?:-|to)\s*", "-", value)


def _normalise_material(value: str) -> str:
    value = _normalise_space(value)
    return {
        "zinc sulfate": "zinc sulphate",
        "fertiliser": "fertilizer",
    }.get(value, value)


def _normalise_unit(value: str) -> str:
    value = value.lower()
    if value.startswith("k"):
        return "kg"
    if value in {"g", "gm"} or value.startswith("gram"):
        return "g"
    if value == "l" or value.startswith("lit"):
        return "l"
    if value == "ml" or value.startswith("millilit"):
        return "ml"
    return "tonne"


def _normalise_basis(value: str) -> str:
    value = value.lower()
    if value.startswith("acre"):
        return "acre"
    if value == "ha" or value.startswith("hectare"):
        return "ha"
    return "litre"


def _overlaps(span: tuple[int, int], spans: list[tuple[int, int]]) -> bool:
    return any(span[0] < end and start < span[1] for start, end in spans)


def extract_input_claims(text: str) -> set[tuple[str, str, str, str]]:
    """Return normalized material, amount, unit, and application-basis claims."""
    claims = set()
    scoped_spans = []
    for match in _QUANTITY_WITH_BASIS.finditer(text):
        material = match.group("before") or match.group("after") or "unspecified"
        claims.add((
            _normalise_material(material),
            _normalise_amount(match.group("amount")),
            _normalise_unit(match.group("unit")),
            _normalise_basis(match.group("basis")),
        ))
        scoped_spans.append(match.span())

    for pattern in (_MATERIAL_BEFORE_QUANTITY, _MATERIAL_AFTER_QUANTITY):
        for match in pattern.finditer(text):
            if _overlaps(match.span(), scoped_spans):
                continue
            claims.add((
                _normalise_material(match.group("material")),
                _normalise_amount(match.group("amount")),
                _normalise_unit(match.group("unit")),
                "missing",
            ))
    return claims


def extract_waiting_periods(text: str) -> set[int]:
    """Return pesticide pre-harvest intervals, never ordinary farm intervals."""
    return {
        int(match.group("wait") or match.group("interval"))
        for match in _WAITING_PERIOD.finditer(text)
    }


def extract_financial_figures(text: str) -> set[str]:
    """Normalize rupee values and percentages for exact evidence comparison."""
    figures = {
        f"rupees:{match.group(1).replace(',', '')}"
        for match in _MONEY_PREFIX.finditer(text)
    }
    figures.update(
        f"rupees:{match.group(1).replace(',', '')}"
        for match in _MONEY_SUFFIX.finditer(text)
    )
    figures.update(f"percent:{match.group(1)}" for match in _PERCENTAGE.finditer(text))
    return figures


def extract_product_recommendations(text: str) -> set[str]:
    """Return named products recommended by a direct English action verb."""
    products = set()
    for match in _PRODUCT_RECOMMENDATION.finditer(text):
        product = match.group("product").strip()
        if product and not product[0].isdigit():
            products.add(_normalise_space(product))
    return products


def _scope_matches_extension(scope, context: dict) -> bool:
    """Apply the retrieval scope rules again before trusting extension advice."""
    if not isinstance(scope, dict):
        return False
    for key in ("states", "crops"):
        declared = scope.get(key)
        if declared is None:
            continue
        if (
            not isinstance(declared, (list, tuple))
            or any(not isinstance(value, str) or not value.strip() for value in declared)
        ):
            return False
        if declared:
            requested = context.get("state" if key == "states" else "crop")
            if not isinstance(requested, str) or requested.casefold() not in {
                value.casefold() for value in declared
            }:
                return False
    return True


def _passage_texts(gathered: dict, allowed_tiers: set[str]) -> list[str]:
    passages = gathered.get("_kb_passages") or []
    context = gathered.get("_guard_context")
    context = context if isinstance(context, dict) else gathered
    texts = []
    for passage in passages:
        if not isinstance(passage, dict):
            continue
        tier = str(passage.get("tier") or "").casefold()
        if tier not in allowed_tiers:
            continue
        if tier == "extension" and not _scope_matches_extension(passage.get("scope"), context):
            continue
        content = passage.get("content")
        if isinstance(content, str):
            texts.append(content)
    return texts


def _is_unmatched_dose_context(doses: str) -> bool:
    lowered = doses.lstrip().lower()
    return lowered.startswith("warning:") or lowered.startswith("no registered pesticide use")


def _record_claims(record: dict) -> set[tuple[str, str, str, str]]:
    claims = set()
    for key in ("dose_formulation", "dose_ai", "dilution"):
        value = record.get(key)
        if isinstance(value, str):
            claims.update(extract_input_claims(value))
    return claims


def _record_waiting_periods(record: dict) -> set[int]:
    value = record.get("waiting_period")
    if isinstance(value, int):
        return {value} if value >= 0 else set()
    if isinstance(value, str) and value.strip().isdigit():
        return {int(value.strip())}
    return set()


def _matched_dose_records(gathered: dict) -> list[tuple[str, set, set]]:
    doses = gathered.get("doses") or ""
    if _is_unmatched_dose_context(doses):
        return []
    matched = []
    for record in gathered.get("_dose_records") or []:
        if not isinstance(record, dict) or not isinstance(record.get("product"), str):
            continue
        matched.append((
            _normalise_space(record["product"]),
            _record_claims(record),
            _record_waiting_periods(record),
        ))
    return matched


def _official_authorities_for_figures(gathered: dict, figures: set[str]) -> list[str]:
    """Return authorities from official passages that support every stated figure."""
    if not figures:
        return []
    authorities = []
    for passage in gathered.get("_kb_passages") or []:
        if not isinstance(passage, dict) or str(passage.get("tier") or "").casefold() != "official":
            continue
        content = passage.get("content")
        authority = passage.get("authority")
        if (
            isinstance(content, str)
            and isinstance(authority, str)
            and authority.strip()
            and figures.issubset(extract_financial_figures(content))
        ):
            authorities.append(authority.strip())
    return list(dict.fromkeys(authorities))


def _registered_use_line(gathered: dict) -> str | None:
    context = gathered.get("_guard_context") or {}
    if context.get("intent") != "disease_pest" or context.get("script") != "Latin":
        return None
    if _is_unmatched_dose_context(gathered.get("doses") or ""):
        return None
    for record in gathered.get("_dose_records") or []:
        if not isinstance(record, dict):
            continue
        product = record.get("product")
        if not isinstance(product, str) or not product.strip():
            continue
        details = [f"Registered CIB&RC use: {product.strip()}"]
        pest = record.get("pest")
        crop = record.get("crop")
        if isinstance(pest, str) and pest.strip() and isinstance(crop, str) and crop.strip():
            details.append(f"for {pest.strip()} on {crop.strip()}")
        for key, label in (("dose_formulation", "dose"), ("dose_ai", "active ingredient dose"), ("dilution", "dilution")):
            value = record.get(key)
            if isinstance(value, str) and value.strip():
                details.append(f"{label} {value.strip()}")
        waiting = record.get("waiting_period")
        if isinstance(waiting, int) and waiting >= 0:
            details.append(f"wait {waiting} days before harvest")
        return "; ".join(details) + "."
    return None


def _matches_dose_record(products: set[str], claims: set, periods: set[int], record: tuple) -> bool:
    product, record_claims, record_periods = record
    return (
        (not products or products == {product})
        and claims.issubset(record_claims)
        and periods.issubset(record_periods)
    )


def _remove(category: str, reason: str) -> None:
    LOGGER.info("output_guard_removed category=%s reason=%s", category, reason)


def scrub(answer: str, gathered: dict) -> tuple[str, bool]:
    """Remove unsupported actionable sentences while retaining safe advice."""
    if not answer:
        return answer, False

    fertilizer_claims = set()
    for passage in _passage_texts(gathered, {"official", "extension"}):
        fertilizer_claims.update(extract_input_claims(passage))

    official_financial_figures = set()
    for passage in _passage_texts(gathered, {"official"}):
        official_financial_figures.update(extract_financial_figures(passage))
    official_legal_claims = set()
    for passage in _passage_texts(gathered, {"official"}):
        official_legal_claims.update(extract_input_claims(passage))

    dose_records = _matched_dose_records(gathered)
    msp_figures = extract_financial_figures(gathered.get("msp") or "")

    kept, changed = [], False
    for sentence in _sentences(answer):
        if not sentence.strip():
            continue
        claims = extract_input_claims(sentence)
        products = extract_product_recommendations(sentence)
        periods = extract_waiting_periods(sentence)
        category = reason = None

        if LEGAL_QUANTITY.search(sentence) and claims and not claims.issubset(official_legal_claims):
            category, reason = "legal", "unsupported_quantity"
        elif FERTILIZER_SUBJECT.search(sentence) and not claims.issubset(fertilizer_claims):
            category, reason = "fertilizer", "unsupported_quantity"
        elif PESTICIDE_SUBJECT.search(sentence) and not FERTILIZER_SUBJECT.search(sentence):
            if (claims or products or periods) and not any(
                _matches_dose_record(products, claims, periods, record)
                for record in dose_records
            ):
                category, reason = "pesticide", "unsupported_record"
        if category is None and periods and not any(
            _matches_dose_record(products, claims, periods, record)
            for record in dose_records
        ):
            category, reason = "pesticide", "unsupported_waiting_period"
        if category is None and PRICE_SCHEME.search(sentence):
            figures = extract_financial_figures(sentence)
            if figures and not figures.issubset(msp_figures):
                category, reason = "msp", "unsupported_figure"
        if category is None and WELFARE_SCHEME.search(sentence):
            figures = extract_financial_figures(sentence)
            if figures and not figures.issubset(official_financial_figures):
                category, reason = "scheme", "unsupported_figure"

        if category is not None:
            _remove(category, reason)
            changed = True
            continue
        kept.append(sentence.strip())

    cleaned = " ".join(kept).strip() if changed else answer
    context = gathered.get("_guard_context") or {}

    if (
        context.get("intent") == "fertiliser_nutrition"
        and _EXACT_FERTILIZER_REQUEST.search(str(context.get("query") or ""))
        and not fertilizer_claims
        and "soil test" not in cleaned.casefold()
    ):
        cleaned = f"{cleaned.rstrip()} {FERTILIZER_REFERRAL}".strip()
        changed = True

    scheme_authorities = []
    for sentence in _sentences(cleaned):
        if WELFARE_SCHEME.search(sentence):
            scheme_authorities.extend(
                _official_authorities_for_figures(gathered, extract_financial_figures(sentence))
            )
    for authority in dict.fromkeys(scheme_authorities):
        if authority.casefold() not in cleaned.casefold():
            cleaned = f"{cleaned.rstrip()} Source: {authority}.".strip()
            changed = True

    registered_use = _registered_use_line(gathered)
    if registered_use and registered_use.split(":", 1)[1].split(";", 1)[0].strip().casefold() not in cleaned.casefold():
        cleaned = f"{cleaned.rstrip()} {registered_use}".strip()
        changed = True

    if not changed:
        return answer, False
    return (cleaned or ACTION_FALLBACK), True
