"""
Download the CIB&RC "Major Uses of Pesticides" lists.

These are the statutory registers of what may legally be sold for which crop
and pest, at what dose, with what pre-harvest interval - published by the
Directorate of Plant Protection, Quarantine & Storage. They are the reason a
dose AnnaData quotes can be checked rather than merely sounding right.

    python tools/fetch_cibrc.py [--out DIR]
"""
import sys
from pathlib import Path

import requests

BASE = "https://ppqs.gov.in"
SOURCE_PAGE = f"{BASE}/divisions/cib-rc/major-uses-of-pesticides"
AS_ON = "31.03.2026"

# Keyed by the pesticide category, since that is what the table records.
FILES = {
    "insecticide":  "/sites/default/files/updated_mup_insecticide_as_on_31.03.2026_c.pdf",
    "fungicide":    "/sites/default/files/2._chemical_mup_fungicide_as_on_31.03.2026_0.pdf",
    "biofungicide": "/sites/default/files/3._bio_pesticide_mup_biofungicide_as_on_31.03.2026.pdf",
    "herbicide":    "/sites/default/files/4._herbicides_mup_as_on_31.03.2026.pdf",
    "pgr":          "/sites/default/files/5._pgr_mup_as_on_31.03.2026.pdf",
    "bioinsecticide": "/sites/default/files/6._mup_bio_insecticide_31.03.2026.pdf",
}

UA = ("Mozilla/5.0 (compatible; AnnaData/1.0 agricultural advisory; "
      "+https://github.com/vibhuu22/AnnaData)")


def main() -> int:
    out = Path("data/cibrc")
    if "--out" in sys.argv:
        out = Path(sys.argv[sys.argv.index("--out") + 1])
    out.mkdir(parents=True, exist_ok=True)

    print(f"Source: {SOURCE_PAGE}  (as on {AS_ON})")
    ok = 0
    for category, path in FILES.items():
        target = out / f"{category}.pdf"
        try:
            r = requests.get(BASE + path, headers={"User-Agent": UA}, timeout=300)
            r.raise_for_status()
            if not r.content.startswith(b"%PDF"):
                print(f"  {category:15} not a PDF, skipped")
                continue
            target.write_bytes(r.content)
            print(f"  {category:15} {len(r.content)/1e6:5.1f} MB -> {target}")
            ok += 1
        except Exception as e:
            print(f"  {category:15} FAILED: {e}")

    print(f"\n{ok}/{len(FILES)} downloaded into {out}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
