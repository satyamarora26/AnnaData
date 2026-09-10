"""
Load the CIB&RC registers into the pesticide_uses table.

The PDFs are real tables, so they are read as tables rather than as flattened
text. Each page yields rows of:

    Crop | Common name of the pest | a.i (gm) | Formulation (gm/ml) | Dilution | Waiting period

with two conventions that have to be honoured or the data comes out wrong:

  * A row with only the first cell filled is a product heading - every row
    after it belongs to that product until the next heading.
  * An empty crop cell means "same crop as the row above". Losing that turns a
    pest for cotton into a pest for nothing.

Rows are skipped rather than guessed at when they do not parse. A missing row
costs a farmer one recommendation; a misparsed one could put the wrong chemical
on their field.

    python tools/load_cibrc.py            # load everything in data/cibrc
    python tools/load_cibrc.py --dry-run  # parse and report, write nothing
"""
import argparse
import re
import sys
from pathlib import Path

import pdfplumber

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402
import knowledge  # noqa: E402
from cibrc_catalog import CibrcArtifact, REQUIRED_CATEGORIES, verified_artifacts  # noqa: E402

SOURCE_URL = "https://ppqs.gov.in/divisions/cib-rc/major-uses-of-pesticides"
AS_ON = "31.03.2026"

CATEGORY_LABEL = {
    "insecticide": "Insecticides",
    "fungicide": "Fungicides",
    "biofungicide": "Bio-Fungicides",
    "herbicide": "Herbicides",
    "pgr": "Plant Growth Regulators",
    "bioinsecticide": "Bio-Insecticides",
}

# A product heading is a single-cell row naming a chemical and its strength -
# 'Acephate 75%SP', 'Carbofuran 03%CG', 'Carbosulfan 06% Granules'. Matching a
# fixed list of formulation codes missed thirty of them in forty pages, and a
# missed heading is worse than a missed row: everything beneath it gets
# attributed to the PREVIOUS product, which is the wrong chemical at a
# plausible dose. So the test is the strength, which every product carries,
# and anything that reads as prose is excluded instead.
STRENGTH_RE = re.compile(r"\d+(\.\d+)?\s*%")
PROSE_MARKERS = (
    " shall ", " should ", " may be ", " is applied", "recommendation",
    "note:", "for control of", " as per ", "applied at", "dilution",
)

SKIP_ROWS = {
    "crop", "agricultural use", "common name of the pest", "dosage/ha",
    "a.i (gm)", "formulation (gm/ml)", "waiting period (days)", "",
}


def valid_waiting_period(value: str) -> str | None:
    """Keep a pre-harvest interval only if it plausibly is one.

    Where a row is missing its active-ingredient figure the columns shift left
    and a dilution volume lands in this field, giving readings like '500 -1000'
    days. A wrong pre-harvest interval is a residue safety problem, so an
    implausible one is dropped rather than stored: no waiting period at all is
    honest, a wrong one is not.
    """
    if not value:
        return None
    text = value.strip()
    if text in {"-", "--", "NA", "N.A.", "Nil"}:
        return None
    numbers = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", text)]
    if not numbers:
        return None
    # Pre-harvest intervals are days. Anything beyond a season is a stray
    # dilution or dose that has landed in the wrong column.
    if max(numbers) > 120:
        return None
    return text


def clean(cell) -> str:
    if not cell:
        return ""
    return re.sub(r"\s+", " ", str(cell)).strip()


def looks_like_product(first: str, rest: list[str]) -> bool:
    """A heading row: only the first cell filled, naming a chemical and strength."""
    if not first or any(rest):
        return False
    low = first.lower()
    if low in SKIP_ROWS:
        return False
    if len(first) > 90 or any(m in low for m in PROSE_MARKERS):
        return False
    if not STRENGTH_RE.search(first):
        return False
    # Products start with the chemical name, not a digit or a bullet.
    return bool(re.match(r"^[A-Za-z]", first))


def parse_pdf(path: Path, category: str, *, source_url: str = SOURCE_URL,
              as_on: str = AS_ON) -> list[dict]:
    label = CATEGORY_LABEL.get(category, category)
    source = f"CIB&RC Major Uses of Pesticides ({label}), as on {as_on}"

    uses: list[dict] = []
    product = None
    crop = None
    skipped = 0

    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables() or []:
                for raw in table:
                    cells = [clean(c) for c in raw]
                    # Pad or trim to the six columns the register uses.
                    cells = (cells + [""] * 6)[:6]
                    first, rest = cells[0], cells[1:]

                    # Any lone cell that is not a recognised product ends the
                    # current one. Carrying the previous product across an
                    # unrecognised heading is what would attribute a dose to
                    # the wrong chemical, so rows after it are dropped instead.
                    if first and not any(rest):
                        product = looks_like_product(first, rest) and first or None
                        crop = None
                        continue

                    crop_cell, pest, ai, formulation, dilution, waiting = cells

                    # Blank crop continues the crop above it.
                    if crop_cell:
                        crop = crop_cell
                    if not product or not crop or not pest:
                        if any(cells):
                            skipped += 1
                        continue

                    # A row with no dose at all is a heading fragment, not a use.
                    if not (ai or formulation):
                        skipped += 1
                        continue

                    uses.append({
                        "category": category,
                        "product": product,
                        "crop": crop,
                        "pest": pest,
                        "dose_ai": ai or None,
                        "dose_formulation": formulation or None,
                        "dilution": dilution or None,
                        "waiting_period": valid_waiting_period(waiting),
                        "source": source,
                        "source_url": source_url,
                    })

    print(f"  {category:15} {len(uses):5} uses parsed, {skipped} rows skipped")
    return uses


INSERT_SQL = """
    INSERT INTO pesticide_uses
        (category, product, crop, pest, dose_ai, dose_formulation,
         dilution, waiting_period, source, source_url)
    VALUES (%(category)s, %(product)s, %(crop)s, %(pest)s,
            %(dose_ai)s, %(dose_formulation)s, %(dilution)s,
            %(waiting_period)s, %(source)s, %(source_url)s)
    ON CONFLICT (product, crop, pest) DO UPDATE SET
        dose_ai = EXCLUDED.dose_ai,
        dose_formulation = EXCLUDED.dose_formulation,
        dilution = EXCLUDED.dilution,
        waiting_period = EXCLUDED.waiting_period,
        source = EXCLUDED.source,
        source_url = EXCLUDED.source_url
"""

def replace_category(category: str, uses: list[dict], run_id: int) -> int:
    """Replace one fully parsed statutory category in a single transaction."""
    if not uses:
        raise ValueError(f"no safe CIB&RC rows parsed for {category}")
    with db.connection() as conn:
        conn.execute("DELETE FROM pesticide_uses WHERE category = %s", (category,))
        with conn.cursor() as cursor:
            cursor.executemany(INSERT_SQL, uses)
        conn.execute(
            """UPDATE ingestion_runs SET status = 'completed', parsed_count = %s,
                  stored_count = %s, completed_at = now() WHERE id = %s""",
            (len(uses), len(uses), run_id),
        )
    return len(uses)


def review_artifacts(folder: Path) -> list[tuple[CibrcArtifact, list[dict]]]:
    reviewed = []
    for artifact, path in verified_artifacts(folder):
        uses = parse_pdf(
            path,
            artifact.category,
            source_url=artifact.source_url,
            as_on=artifact.as_on,
        )
        if not uses:
            raise ValueError(f"no safe CIB&RC rows parsed for {artifact.category}")
        reviewed.append((artifact, uses))
    return reviewed


def replace_all_categories(
    reviewed: list[tuple[CibrcArtifact, list[dict]]], run_ids: dict[str, int]
) -> int:
    """Replace the complete verified register set in one transaction."""
    categories = [artifact.category for artifact, _ in reviewed]
    if (
        len(categories) != len(REQUIRED_CATEGORIES)
        or set(categories) != REQUIRED_CATEGORIES
        or set(run_ids) != REQUIRED_CATEGORIES
        or any(not uses for _, uses in reviewed)
    ):
        raise ValueError("CIB&RC replacement requires the complete six-category set")
    total = 0
    with db.connection() as conn:
        with conn.cursor() as cursor:
            for artifact, uses in reviewed:
                conn.execute(
                    "DELETE FROM pesticide_uses WHERE category = %s",
                    (artifact.category,),
                )
                cursor.executemany(INSERT_SQL, uses)
                total += len(uses)
        for artifact, uses in reviewed:
            conn.execute(
                """UPDATE ingestion_runs SET status = 'completed', parsed_count = %s,
                      stored_count = %s, completed_at = now() WHERE id = %s""",
                (len(uses), len(uses), run_ids[artifact.category]),
            )
    return total


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Load reviewed CIB&RC PDF registers.")
    parser.add_argument("--folder", type=Path, default=Path("data/cibrc"))
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    dry = args.dry_run
    folder = args.folder
    if not folder.is_dir():
        print("data/cibrc not found - run tools/fetch_cibrc.py first")
        return 1

    try:
        reviewed = review_artifacts(folder)
    except Exception as exc:
        print(f"CIB&RC artifact set rejected: {exc}")
        return 1
    total = sum(len(uses) for _, uses in reviewed)

    print(f"\n{total} approved uses parsed from {folder}")
    if dry:
        return 0

    db.init()
    if not db.is_available():
        print("No database. Set DATABASE_URL.")
        return 1
    knowledge.init()
    run_ids = {}
    try:
        for artifact, _ in reviewed:
            source = f"cibrc:{artifact.category}"
            run_id = knowledge.start_ingestion(
                "cibrc", source, artifact.source_url, artifact.sha256, skip_completed=False
            )
            if run_id is None:
                raise RuntimeError(f"could not start audit for {artifact.category}")
            run_ids[artifact.category] = run_id
        replace_all_categories(reviewed, run_ids)
    except Exception as exc:
        for artifact, uses in reviewed:
            run_id = run_ids.get(artifact.category)
            if run_id is not None:
                try:
                    knowledge.fail_ingestion(
                        run_id, f"CIB&RC batch replacement failed: {exc}", len(uses), 0, 0
                    )
                except Exception:
                    pass
        print(f"CIB&RC batch replacement failed: {exc}")
        db.close()
        return 1
    if not dry:
        print("stored:", knowledge.counts())
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
