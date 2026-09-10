"""Validated document extraction, chunking, and atomic knowledge ingestion."""
from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import time

import knowledge
from source_catalog import SourceSpec


MAX_SOURCE_BYTES = 80 * 1024 * 1024
MAX_PDF_PAGES = 2000
CHUNK_CHARS = 1000
OVERLAP_CHARS = 150
MIN_CHUNK_CHARS = 120
SUPPORTED_EXTENSIONS = {".pdf", ".html", ".htm", ".txt"}
EMBED_BATCH_SIZE = 20


@dataclass(frozen=True)
class IngestResult:
    source_id: str
    status: str
    content_hash: str
    parsed: int
    stored: int
    rejected: int


def _check_deadline(deadline_at: float | None, clock) -> None:
    if deadline_at is not None and clock() >= deadline_at:
        raise TimeoutError("extraction deadline exceeded")


def sha256_file(path: Path, *, deadline_at: float | None = None, clock=None) -> str:
    clock = clock or time.monotonic
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            _check_deadline(deadline_at, clock)
            digest.update(block)
    _check_deadline(deadline_at, clock)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _source_suffix(path: Path) -> str:
    suffix = path.suffix.casefold()
    return path.with_suffix("").suffix.casefold() if suffix == ".part" else suffix


def validate_source_file(path: Path) -> None:
    if not path.is_file():
        raise ValueError(f"source file not found: {path}")
    size = path.stat().st_size
    if size == 0 or size > MAX_SOURCE_BYTES:
        raise ValueError(f"source file size outside 1-{MAX_SOURCE_BYTES} bytes")

    suffix = _source_suffix(path)
    if suffix not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"unsupported source extension: {path.suffix}")
    if suffix == ".pdf":
        with path.open("rb") as handle:
            if handle.read(5) != b"%PDF-":
                raise ValueError("source does not have a PDF signature")


def validate_extracted_text(spec: SourceSpec, text: str) -> None:
    normalized = " ".join(text.casefold().split())
    if len(normalized) < 500:
        raise ValueError("extracted source text is shorter than 500 characters")
    for term in spec.required_terms:
        if term.casefold() not in normalized:
            raise ValueError(f"required source term missing: {term}")


def read_source(path: Path, *, deadline_at: float | None = None, clock=None) -> str:
    clock = clock or time.monotonic
    _check_deadline(deadline_at, clock)
    if _source_suffix(path) == ".pdf":
        return read_pdf(path, deadline_at=deadline_at, clock=clock)
    text = path.read_text(encoding="utf-8", errors="replace")
    _check_deadline(deadline_at, clock)
    result = read_html(text)
    _check_deadline(deadline_at, clock)
    return result


def read_pdf(path: Path, *, deadline_at: float | None = None, clock=None) -> str:
    import pypdf

    clock = clock or time.monotonic
    _check_deadline(deadline_at, clock)
    reader = pypdf.PdfReader(str(path))
    if len(reader.pages) > MAX_PDF_PAGES:
        raise ValueError(f"PDF exceeds {MAX_PDF_PAGES} page extraction limit")
    pages = []
    for page in reader.pages:
        _check_deadline(deadline_at, clock)
        try:
            pages.append(page.extract_text() or "")
        except TimeoutError:
            raise
        except Exception:
            continue
        _check_deadline(deadline_at, clock)
    return "\n\n".join(pages)


def read_html(text: str) -> str:
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<br\s*/?>|</p>|</div>|</li>", "\n", text, flags=re.I)
    return re.sub(r"<[^>]+>", " ", text)


def clean_text(text: str) -> str:
    """Tidy extracted text without destroying paragraph structure."""
    text = text.replace("\x00", "")
    text = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f]", "", text)
    text = text.replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"^\s*\d+\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^[-_=]{3,}$", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def chunk_text(text: str) -> list[str]:
    """Split into overlapping chunks on paragraph boundaries."""
    paragraphs = [paragraph.strip() for paragraph in text.split("\n\n") if paragraph.strip()]
    chunks: list[str] = []
    current = ""

    for paragraph in paragraphs:
        if len(current) + len(paragraph) + 2 <= CHUNK_CHARS:
            current = f"{current}\n\n{paragraph}" if current else paragraph
            continue

        if current:
            chunks.append(current)
            tail = current[-OVERLAP_CHARS:]
            cut = tail.find(". ")
            current = (tail[cut + 2:] if cut != -1 else tail) + "\n\n" + paragraph
        else:
            current = paragraph

        while len(current) > CHUNK_CHARS:
            window = current[:CHUNK_CHARS]
            cut = max(window.rfind(". "), window.rfind("।"))
            if cut < MIN_CHUNK_CHARS:
                cut = CHUNK_CHARS
            chunks.append(current[:cut + 1].strip())
            current = current[cut + 1:].lstrip()

    if len(current.strip()) >= MIN_CHUNK_CHARS:
        chunks.append(current.strip())

    return [chunk for chunk in chunks if len(chunk) >= MIN_CHUNK_CHARS]


def ingest_source(
    spec: SourceSpec,
    path: Path,
    dry_run: bool = False,
    deadline_seconds: float | None = None,
) -> IngestResult:
    started_at = time.monotonic()
    deadline_at = None if deadline_seconds is None else started_at + deadline_seconds

    def remaining_seconds() -> float | None:
        if deadline_at is None:
            return None
        remaining = deadline_at - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("ingestion deadline exceeded")
        return remaining

    source_hash = ""
    try:
        validate_source_file(path)
        remaining_seconds()
        source_hash = sha256_file(path, deadline_at=deadline_at)
        cleaned = clean_text(read_source(path, deadline_at=deadline_at))
        remaining_seconds()
        validate_extracted_text(spec, cleaned)
        content_hash = sha256_text(cleaned)
        chunks = chunk_text(cleaned)
        remaining_seconds()
        if not chunks:
            raise ValueError(f"no usable text extracted from {path}")
    except (Exception, KeyboardInterrupt) as exc:
        error = (
            "extraction deadline exceeded"
            if isinstance(exc, TimeoutError)
            else "ingestion interrupted"
            if isinstance(exc, KeyboardInterrupt)
            else str(exc)
        )
        if not dry_run:
            try:
                knowledge.record_ingestion_failure(
                    "document", spec.id, spec.source_url, error, source_hash or None
                )
            except Exception as audit_exc:
                print(f"Could not record extraction failure: {audit_exc}")
        return IngestResult(spec.id, "failed", source_hash, 0, 0, 0)

    if dry_run:
        return IngestResult(spec.id, "dry-run", content_hash, len(chunks), 0, 0)

    remaining_seconds()
    run_id = knowledge.start_ingestion("document", spec.id, spec.source_url, content_hash)
    if run_id is None:
        return IngestResult(spec.id, "skipped", content_hash, len(chunks), 0, 0)

    stored = 0
    rejected = 0
    try:
        for offset in range(0, len(chunks), EMBED_BATCH_SIZE):
            batch = chunks[offset:offset + EMBED_BATCH_SIZE]
            if not knowledge.renew_ingestion_lease(run_id, spec.id):
                raise knowledge.IngestionLeaseActive(
                    f"ingestion lease is no longer active for {spec.id}"
                )
            embedding_timeout = remaining_seconds() or 60
            embedding_timeout = min(
                embedding_timeout,
                max(1.0, knowledge.INGESTION_LEASE_SECONDS / 2),
            )
            vectors = knowledge.embed_batch(
                batch, timeout_seconds=embedding_timeout
            )
            remaining_seconds()
            if vectors is None or len(vectors) != len(batch):
                rejected += len(batch)
                break
            for index, (text, vector) in enumerate(zip(batch, vectors), start=offset):
                remaining_seconds()
                if knowledge.stage_document(run_id, spec, content_hash, text, index, vector):
                    stored += 1
                else:
                    rejected += 1
                remaining_seconds()
    except (Exception, KeyboardInterrupt) as exc:
        if isinstance(exc, TimeoutError):
            error = "ingestion deadline exceeded"
        elif isinstance(exc, KeyboardInterrupt):
            error = "ingestion interrupted"
        else:
            error = str(exc)
        knowledge.fail_ingestion(run_id, error, len(chunks), stored, len(chunks) - stored)
        return IngestResult(spec.id, "failed", content_hash, len(chunks), stored, len(chunks) - stored)

    if rejected or stored != len(chunks):
        knowledge.fail_ingestion(
            run_id,
            "embedding or staging failed",
            len(chunks),
            stored,
            len(chunks) - stored,
        )
        return IngestResult(spec.id, "failed", content_hash, len(chunks), stored, len(chunks) - stored)

    try:
        activation_timeout = remaining_seconds()
        activation_args = {}
        if deadline_at is not None:
            activation_args = {
                "deadline_at": deadline_at,
                "timeout_seconds": activation_timeout,
            }
        activated = knowledge.activate_documents(
            run_id, spec, content_hash, len(chunks), stored, 0, **activation_args
        )
    except Exception as exc:
        error = (
            "ingestion deadline exceeded"
            if isinstance(exc, (TimeoutError, knowledge.ActivationDeadlineExceeded))
            else str(exc)
        )
        knowledge.fail_ingestion(run_id, error, len(chunks), stored, len(chunks) - stored)
        return IngestResult(spec.id, "failed", content_hash, len(chunks), stored, len(chunks) - stored)
    if not activated:
        knowledge.fail_ingestion(
            run_id, "document activation was rejected", len(chunks), stored, 0
        )
        return IngestResult(spec.id, "failed", content_hash, len(chunks), stored, 0)
    return IngestResult(spec.id, "completed", content_hash, len(chunks), stored, 0)
