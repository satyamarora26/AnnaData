"""
Ingest reference documents into the pgvector store.

Handles PDFs and plain HTML. Text is split on paragraph boundaries into chunks
of roughly CHUNK_CHARS with a little overlap, so a passage that answers a
question is not cut in half between two chunks and lost to both.

    python tools/ingest_docs.py --pdf data/schemes/pm-kisan.pdf --source "PM-KISAN operational guidelines" --url https://...
    python tools/ingest_docs.py --dir data/schemes
    python tools/ingest_docs.py --dry-run --pdf ...

Note on sources: most Indian government agricultural portals cannot be fetched
programmatically - they are JavaScript-rendered, refuse cloud clients, or link
to documents that have moved. Where a document will not download, save it from
a browser into data/schemes and ingest the file.
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402
import knowledge  # noqa: E402

CHUNK_CHARS = 1000
OVERLAP_CHARS = 150
MIN_CHUNK_CHARS = 120


def read_pdf(path: Path) -> str:
    import pypdf

    reader = pypdf.PdfReader(str(path))
    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            continue
    return "\n\n".join(pages)


def read_html(path_or_text: str) -> str:
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ",
                  path_or_text, flags=re.S | re.I)
    text = re.sub(r"<br\s*/?>|</p>|</div>|</li>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return text


def clean(text: str) -> str:
    """Tidy extracted text without destroying paragraph structure."""
    # PDF extraction emits NUL and other control bytes, which Postgres rejects
    # outright - four chunks of a nine-page factsheet were silently lost to
    # them before this line existed.
    text = text.replace("\x00", "")
    text = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f]", "", text)
    text = text.replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    # Page numbers and rules left behind by extraction.
    text = re.sub(r"^\s*\d+\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^[-_=]{3,}$", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def chunk(text: str) -> list[str]:
    """Split into overlapping chunks on paragraph boundaries.

    Splitting mid-sentence loses the passage to both neighbours; the overlap
    means a fact sitting on a boundary still appears whole in one of them.
    """
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks, current = [], ""

    for para in paragraphs:
        if len(current) + len(para) + 2 <= CHUNK_CHARS:
            current = f"{current}\n\n{para}" if current else para
            continue

        if current:
            chunks.append(current)
            tail = current[-OVERLAP_CHARS:]
            # Resume from a sentence boundary inside the overlap where possible.
            cut = tail.find(". ")
            current = (tail[cut + 2:] if cut != -1 else tail) + "\n\n" + para
        else:
            current = para

        # A single paragraph longer than the budget is split on sentences.
        while len(current) > CHUNK_CHARS:
            window = current[:CHUNK_CHARS]
            cut = max(window.rfind(". "), window.rfind("।"))
            if cut < MIN_CHUNK_CHARS:
                cut = CHUNK_CHARS
            chunks.append(current[:cut + 1].strip())
            current = current[cut + 1:].lstrip()

    if len(current.strip()) >= MIN_CHUNK_CHARS:
        chunks.append(current.strip())

    return [c for c in chunks if len(c) >= MIN_CHUNK_CHARS]


def ingest(path: Path, source: str, url: str | None, dry: bool,
           tier: str = "reference") -> int:
    raw = read_pdf(path) if path.suffix.lower() == ".pdf" else \
        read_html(path.read_text(encoding="utf-8", errors="replace"))
    text = clean(raw)
    chunks = chunk(text)

    print(f"  {path.name}: {len(text):,} chars -> {len(chunks)} chunks")
    if dry:
        for c in chunks[:2]:
            print(f"    sample: {c[:180]}...")
        return len(chunks)

    stored = 0
    for i, c in enumerate(chunks):
        if knowledge.add_document(source=source, content=c, title=path.stem,
                                  url=url, chunk_index=i, tier=tier):
            stored += 1
        if stored and stored % 25 == 0:
            print(f"    stored {stored}/{len(chunks)}")
    print(f"    stored {stored}/{len(chunks)}")
    return stored


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", help="a single file to ingest")
    ap.add_argument("--dir", help="a folder of files to ingest")
    ap.add_argument("--source", help="human-readable source name")
    ap.add_argument("--url", help="where the document came from")
    ap.add_argument("--tier", choices=["official", "reference"], default="reference",
                    help="official for government documents, reference otherwise")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.pdf and not args.dir:
        ap.error("give --pdf or --dir")

    if not args.dry_run:
        db.init()
        if not db.is_available():
            print("No database. Set DATABASE_URL.")
            return 1
        knowledge.init()

    files = [Path(args.pdf)] if args.pdf else sorted(Path(args.dir).glob("*.*"))
    files = [f for f in files if f.suffix.lower() in (".pdf", ".html", ".htm", ".txt")]
    if not files:
        print("Nothing to ingest.")
        return 1

    total = 0
    for f in files:
        source = args.source or f.stem.replace("-", " ").replace("_", " ").title()
        total += ingest(f, source, args.url, args.dry_run, args.tier)

    print(f"\n{total} chunks {'parsed' if args.dry_run else 'stored'}")
    if not args.dry_run:
        print("knowledge store:", knowledge.counts())
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
