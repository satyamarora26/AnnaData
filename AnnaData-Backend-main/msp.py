"""
Minimum Support Price: the floor the government guarantees for a crop.

This is a different kind of number from a mandi rate and answers a different
question. A mandi rate says what a particular market paid yesterday; it moves
daily and has to be fetched live. An MSP is set once a marketing year by the
Cabinet Committee on Economic Affairs, applies nationally, and does not move -
so it can be stored, and it is still correct months later.

That makes it the right thing to hold locally. When the live price service is
down - which, at the time of writing, it reliably is - "wheat's support price
is Rs 2,585 a quintal, so do not sell below that at a procurement centre" is a
useful and true answer, where "prices unavailable" is neither.

It is not a substitute for a local rate: a farmer often gets more than MSP, and
for onion, potato and tomato there is no MSP at all. The wording says so.
"""
import csv
import re

import db
from knowledge import canonical_crop

SCHEMA = """
CREATE TABLE IF NOT EXISTS commodity_msp (
    alias      TEXT PRIMARY KEY,
    label      TEXT NOT NULL,
    msp        NUMERIC NOT NULL,
    crop_group TEXT,
    year       TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def init() -> bool:
    if not db.is_available():
        return False
    try:
        with db.connection() as conn:
            conn.execute(SCHEMA)
        return True
    except Exception as e:
        print(f"MSP table unavailable: {e}")
        return False


def aliases_for(label: str) -> list[str]:
    """Every name a farmer might use for a commodity written as Agmarknet does.

    Their labels pack the synonyms into brackets and slashes -
    "Red gram/Arhar/Tur(whole)", "Sesamum(Sesame,Gingelly,Til)" - which is
    exactly the vocabulary farmers actually use, so it is worth unpacking rather
    than discarding. Qualifiers like "whole" and "common" are grades, not names,
    and would only produce false matches.
    """
    QUALIFIERS = {"whole", "common", "grade", "seed", "fresh", "dry", "raw"}
    parts = re.split(r"[/(),]", label)
    names = set()
    for part in parts:
        name = part.strip().lower()
        if not name or name in QUALIFIERS or len(name) < 3:
            continue
        names.add(name)
        canonical = canonical_crop(name)
        if canonical:
            names.add(canonical)
    return sorted(names)


def load_csv(path: str, year: str) -> dict:
    """Load MSP figures from an Agmarknet price-and-arrival export."""
    if not init():
        return {"loaded": 0, "reason": "database unavailable"}

    rows = []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for row in csv.reader(fh):
            # The export carries two banner lines before the header, so the
            # commodity rows are found by shape rather than by position.
            if len(row) < 4:
                continue
            group, label, msp = row[0].strip(), row[1].strip(), row[2].strip()
            if not label or label.lower() == "commodity":
                continue
            try:
                value = float(msp)
            except ValueError:
                continue          # "-" means the crop has no declared MSP
            if value <= 0:
                continue
            rows.append((group, label, value))

    written = 0
    with db.connection() as conn:
        for group, label, value in rows:
            for alias in aliases_for(label):
                conn.execute(
                    """
                    INSERT INTO commodity_msp (alias, label, msp, crop_group, year)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (alias) DO UPDATE SET
                        label = EXCLUDED.label, msp = EXCLUDED.msp,
                        crop_group = EXCLUDED.crop_group, year = EXCLUDED.year,
                        updated_at = now()
                    """,
                    (alias, label, value, group, year),
                )
                written += 1
    return {"commodities": len(rows), "aliases": written, "year": year}


def for_crop(crop: str | None) -> str:
    """The support price for a crop, phrased for an answer."""
    if not crop or crop.lower() == "unknown" or not db.is_available():
        return ""
    try:
        with db.connection() as conn:
            conn.execute(SCHEMA)
            row = conn.execute(
                "SELECT label, msp, year FROM commodity_msp WHERE alias = %s",
                (crop.strip().lower(),),
            ).fetchone()
            if not row:
                canonical = canonical_crop(crop)
                if canonical and canonical != crop.strip().lower():
                    row = conn.execute(
                        "SELECT label, msp, year FROM commodity_msp WHERE alias = %s",
                        (canonical,),
                    ).fetchone()
    except Exception as e:
        print(f"MSP lookup failed for {crop!r}: {e}")
        return ""

    if not row:
        # Saying nothing is right here: many crops genuinely have no MSP, and
        # the answer should not imply the figure was looked up and missing.
        return ""

    label, msp, year = row
    return (
        f"Minimum Support Price for {label} in {year}: Rs {float(msp):,.0f} per "
        f"quintal. This is the floor the government guarantees at a procurement "
        f"centre - a farmer should not sell below it there, though open-market "
        f"mandi rates are often higher. It is set nationally for the marketing "
        f"year and does not change day to day."
    )


def counts() -> dict:
    if not db.is_available():
        return {}
    try:
        with db.connection() as conn:
            conn.execute(SCHEMA)
            n = conn.execute(
                "SELECT count(DISTINCT label) FROM commodity_msp"
            ).fetchone()[0]
        return {"msp_commodities": n}
    except Exception:
        return {}
