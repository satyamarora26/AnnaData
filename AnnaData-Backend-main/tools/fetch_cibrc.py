"""
Download the CIB&RC "Major Uses of Pesticides" lists.

These are the statutory registers of what may legally be sold for which crop
and pest, at what dose, with what pre-harvest interval - published by the
Directorate of Plant Protection, Quarantine & Storage. They are the reason a
dose AnnaData quotes can be checked rather than merely sounding right.

    python tools/fetch_cibrc.py [--out DIR]
"""
import argparse
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

UA = (
    "Mozilla/5.0 (compatible; AnnaData/1.0 agricultural advisory; "
    "+https://github.com/satyamarora26/AnnaData)"
)
MAX_PDF_BYTES = 80 * 1024 * 1024


def download_file(url: str, target: Path, timeout: int) -> int:
    """Stream one PDF to a part file and publish it only after validation."""
    part = target.with_suffix(target.suffix + ".part")
    size = 0
    try:
        response = requests.get(
            url, headers={"User-Agent": UA}, timeout=timeout, stream=True
        )
        response.raise_for_status()
        with part.open("wb") as fh:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                size += len(chunk)
                if size > MAX_PDF_BYTES:
                    raise ValueError("PDF exceeds 80 MiB limit")
                fh.write(chunk)
        if not part.read_bytes().startswith(b"%PDF-"):
            raise ValueError("missing %PDF- signature")
        part.replace(target)
        return size
    except Exception:
        try:
            part.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download CIB&RC pesticide registers.")
    parser.add_argument("--out", type=Path, default=Path("data/cibrc"))
    parser.add_argument("--timeout", type=int, default=300)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out = args.out
    out.mkdir(parents=True, exist_ok=True)

    print(f"Source: {SOURCE_PAGE}  (as on {AS_ON})")
    ok = 0
    for category, path in FILES.items():
        target = out / f"{category}.pdf"
        try:
            size = download_file(BASE + path, target, args.timeout)
            print(f"  {category:15} {size / 1e6:5.1f} MB -> {target}")
            ok += 1
        except Exception as e:
            print(f"  {category:15} FAILED: {e}")

    print(f"\n{ok}/{len(FILES)} downloaded into {out}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
