"""Ingest only sources declared in the trusted source manifest."""
import argparse
import math
from pathlib import Path
import sys
import time
from urllib.parse import urljoin

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402
import ingestion  # noqa: E402
import knowledge  # noqa: E402
from source_catalog import SourceSpec, assert_trusted_url, load_catalog  # noqa: E402


USER_AGENT = "AnnaData/1.0 (+https://github.com/satyamarora26/AnnaData)"
GENERIC_CONTENT_TYPES = {"", "application/octet-stream"}
DEFAULT_INGESTION_DEADLINE_SECONDS = 300
FETCH_VALIDATION_DEADLINE_SECONDS = 120
MAX_REDIRECTS = 5
REDIRECT_STATUSES = {301, 302, 303, 307, 308}


def _content_type_is_allowed(spec: SourceSpec, content_type: str) -> bool:
    media_type = content_type.split(";", 1)[0].strip().casefold()
    if media_type in GENERIC_CONTENT_TYPES:
        return True
    if spec.local_path.suffix.casefold() == ".pdf":
        return media_type == "application/pdf"
    return media_type in {"text/html", "text/plain"}


def _record_fetch_failure(spec: SourceSpec, error: Exception) -> None:
    try:
        knowledge.record_ingestion_failure("fetch", spec.id, spec.source_url, str(error))
    except Exception as audit_error:
        print(f"Could not record fetch failure for {spec.id}: {audit_error}", file=sys.stderr)


def _trusted_response(spec: SourceSpec):
    current_url = spec.source_url
    for redirects in range(MAX_REDIRECTS + 1):
        assert_trusted_url(current_url)
        response = requests.get(
            current_url,
            headers={"User-Agent": USER_AGENT},
            timeout=(10, 120),
            stream=True,
            allow_redirects=False,
        )
        response_url = getattr(response, "url", None) or current_url
        assert_trusted_url(response_url)
        if getattr(response, "status_code", 200) not in REDIRECT_STATUSES:
            response.raise_for_status()
            return response
        location = response.headers.get("Location")
        if not location:
            raise ValueError("redirect response did not provide a location")
        target = urljoin(response_url, location)
        assert_trusted_url(target)
        close = getattr(response, "close", None)
        if close:
            close()
        current_url = target
    raise ValueError(f"source exceeded {MAX_REDIRECTS} redirects")


def fetch_source(spec: SourceSpec) -> bool:
    """Fetch one HTTP source without replacing a valid local file prematurely."""
    if spec.fetch_mode != "http":
        if spec.fetch_mode == "browser":
            print(f"browser download required: {spec.source_url} -> {spec.local_path}")
        else:
            print(f"unsupported fetch mode for {spec.id}: {spec.fetch_mode}", file=sys.stderr)
        return False

    path = spec.local_path
    part = path.with_suffix(path.suffix + ".part")
    try:
        assert_trusted_url(spec.source_url)
        response = _trusted_response(spec)
        if not _content_type_is_allowed(spec, response.headers.get("Content-Type", "")):
            raise ValueError("response content type does not match source extension")

        path.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        with part.open("wb") as handle:
            for block in response.iter_content(chunk_size=1024 * 1024):
                if not block:
                    continue
                written += len(block)
                if written > ingestion.MAX_SOURCE_BYTES:
                    raise ValueError(f"download exceeds {ingestion.MAX_SOURCE_BYTES} bytes")
                handle.write(block)
        ingestion.validate_source_file(part)
        validation_deadline = time.monotonic() + FETCH_VALIDATION_DEADLINE_SECONDS
        extracted = ingestion.clean_text(
            ingestion.read_source(part, deadline_at=validation_deadline)
        )
        ingestion.validate_extracted_text(spec, extracted)
        part.replace(path)
        print(f"fetched {spec.id}: {path}")
        return True
    except Exception as exc:
        _record_fetch_failure(spec, exc)
        try:
            part.unlink(missing_ok=True)
        except Exception as cleanup_error:
            print(f"could not remove temporary fetch file for {spec.id}: {cleanup_error}", file=sys.stderr)
        print(f"fetch failed for {spec.id}: {exc}", file=sys.stderr)
        return False


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--source-id", help="one manifest entry")
    selection.add_argument("--all", action="store_true", help="every manifest entry")
    parser.add_argument("--fetch", action="store_true", help="download entries whose fetch mode is http")
    parser.add_argument("--dry-run", action="store_true", help="parse and report without database writes")
    parser.add_argument(
        "--deadline-seconds",
        type=float,
        default=DEFAULT_INGESTION_DEADLINE_SECONDS,
        help="per-source embedding deadline; defaults to %(default)s seconds",
    )
    parser.add_argument("--manifest", type=Path, default=Path("data/source_manifest.json"), help="source manifest path")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if not math.isfinite(args.deadline_seconds) or args.deadline_seconds <= 0:
        print("--deadline-seconds must be a finite positive number", file=sys.stderr)
        return 2
    catalog = load_catalog(args.manifest)
    if args.all:
        specs = list(catalog.values())
    else:
        spec = catalog.get(args.source_id)
        if spec is None:
            print(f"unknown source id: {args.source_id}", file=sys.stderr)
            return 2
        specs = [spec]

    available: list[SourceSpec] = []
    for spec in specs:
        if args.fetch and not fetch_source(spec):
            continue
        available.append(spec)

    if not available:
        return 1

    if not args.dry_run:
        db.init()
        if not db.is_available():
            print("No database. Set DATABASE_URL.", file=sys.stderr)
            return 1
        knowledge.init()

    status = 0
    deadline_at = time.monotonic() + args.deadline_seconds
    for spec in available:
        remaining_seconds = deadline_at - time.monotonic()
        if remaining_seconds <= 0:
            print("ingestion deadline exceeded before remaining sources", file=sys.stderr)
            status = 1
            break
        try:
            result = ingestion.ingest_source(
                spec,
                spec.local_path,
                args.dry_run,
                deadline_seconds=remaining_seconds,
            )
        except Exception as exc:
            print(f"ingestion failed for {spec.id}: {exc}", file=sys.stderr)
            status = 1
            continue
        print(
            f"{result.source_id}: {result.status}; parsed={result.parsed} "
            f"stored={result.stored} rejected={result.rejected}"
        )
        if result.status == "failed":
            status = 1

    if not args.dry_run:
        db.close()
    return status


if __name__ == "__main__":
    raise SystemExit(main())
