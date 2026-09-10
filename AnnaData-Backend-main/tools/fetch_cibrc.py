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
import shutil
import sys
import tempfile

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cibrc_catalog import load_catalog, verified_artifacts

BASE = "https://ppqs.gov.in"
SOURCE_PAGE = f"{BASE}/divisions/cib-rc/major-uses-of-pesticides"
AS_ON = "31.03.2026"

# Keyed by the pesticide category, since that is what the table records.
CATALOG = load_catalog()
FILES = {category: artifact.source_url for category, artifact in CATALOG.items()}

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
    out.parent.mkdir(parents=True, exist_ok=True)
    staged = Path(tempfile.mkdtemp(prefix=f".{out.name}-staged-", dir=out.parent))

    print(f"Source: {SOURCE_PAGE}  (as on {AS_ON})")
    ok = 0
    try:
        for category, artifact in CATALOG.items():
            target = staged / artifact.filename
            try:
                size = download_file(artifact.source_url, target, args.timeout)
                print(f"  {category:15} {size / 1e6:5.1f} MB -> {out / artifact.filename}")
                ok += 1
            except Exception as e:
                print(f"  {category:15} FAILED: {e}")

        print(f"\n{ok}/{len(CATALOG)} downloaded for {out}")
        if ok != len(CATALOG):
            return 1
        verified_artifacts(staged, CATALOG)

        backup = None
        if out.exists():
            backup = Path(tempfile.mkdtemp(prefix=f".{out.name}-backup-", dir=out.parent))
            backup.rmdir()
            out.replace(backup)
        try:
            staged.replace(out)
        except Exception:
            if backup is not None and not out.exists():
                backup.replace(out)
            raise
        if backup is not None:
            shutil.rmtree(backup)
        return 0
    except Exception as exc:
        print(f"CIB&RC fetch set rejected: {exc}")
        return 1
    finally:
        if staged.exists():
            shutil.rmtree(staged)


if __name__ == "__main__":
    raise SystemExit(main())
