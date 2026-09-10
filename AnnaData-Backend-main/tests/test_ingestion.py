import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import ingestion
from source_catalog import load_catalog


def _spec():
    manifest = Path(__file__).resolve().parents[1] / "data" / "source_manifest.json"
    return load_catalog(manifest)["pm_kisan_guidelines"]


def _guidance_text() -> str:
    return ("PM-KISAN operational guidelines explain farmer support eligibility. " * 30)


def _cli_module():
    path = Path(__file__).resolve().parents[1] / "tools" / "ingest_docs.py"
    module_spec = importlib.util.spec_from_file_location("ingest_docs", path)
    assert module_spec and module_spec.loader
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return module


def test_pdf_signature_is_required(tmp_path):
    path = tmp_path / "fake.pdf"
    path.write_text("<html>blocked</html>", encoding="utf-8")

    with pytest.raises(ValueError, match="PDF signature"):
        ingestion.validate_source_file(path)


def test_same_bytes_produce_same_sha256(tmp_path):
    first = tmp_path / "one.txt"
    second = tmp_path / "two.txt"
    first.write_text("verified agricultural guidance", encoding="utf-8")
    second.write_text("verified agricultural guidance", encoding="utf-8")

    assert ingestion.sha256_file(first) == ingestion.sha256_file(second)


def test_extracted_text_must_contain_source_identity_terms():
    with pytest.raises(ValueError, match="required source term"):
        ingestion.validate_extracted_text(_spec(), "generic farming advice " * 40)


def test_cleaning_and_chunking_keep_paragraph_overlap():
    first = "PM-KISAN operational guidelines. " + ("First paragraph guidance. " * 48)
    second = "Second paragraph guidance. " * 48

    cleaned = ingestion.clean_text(f"{first}\x00\x01\n\n{second}")
    chunks = ingestion.chunk_text(cleaned)

    assert "\x00" not in cleaned and "\x01" not in cleaned
    assert len(chunks) >= 2
    assert chunks[0][-100:] in chunks[1]


def test_embedding_failure_preserves_active_corpus(tmp_path, monkeypatch):
    path = tmp_path / "guidance.txt"
    path.write_text(_guidance_text(), encoding="utf-8")
    events = []
    monkeypatch.setattr(ingestion.knowledge, "start_ingestion", lambda kind, source, url, digest: 9)
    monkeypatch.setattr(ingestion.knowledge, "embed", lambda text: None)
    monkeypatch.setattr(
        ingestion.knowledge,
        "activate_documents",
        lambda *args: pytest.fail("failed ingestion must not activate documents"),
    )
    monkeypatch.setattr(
        ingestion.knowledge,
        "fail_ingestion",
        lambda run_id, error, parsed, stored, rejected: events.append(
            (run_id, parsed, stored, rejected)
        ),
    )

    result = ingestion.ingest_source(_spec(), path)

    assert result.status == "failed"
    assert events == [(9, result.parsed, 0, result.parsed)]


def test_complete_ingestion_activates_all_chunks(tmp_path, monkeypatch):
    path = tmp_path / "guidance.txt"
    path.write_text(_guidance_text() + ("Wheat nutrient guidance for Punjab fields. " * 60), encoding="utf-8")
    staged = []
    monkeypatch.setattr(ingestion.knowledge, "start_ingestion", lambda kind, source, url, digest: 12)
    monkeypatch.setattr(ingestion.knowledge, "embed", lambda text: [0.0] * 768)
    monkeypatch.setattr(
        ingestion.knowledge,
        "stage_document",
        lambda run_id, spec, digest, text, index, vector: staged.append(index) is None or True,
    )
    monkeypatch.setattr(
        ingestion.knowledge,
        "activate_documents",
        lambda run_id, spec, digest, parsed, stored, rejected: parsed == stored and rejected == 0,
    )

    result = ingestion.ingest_source(_spec(), path)

    assert result.status == "completed"
    assert result.parsed == result.stored == len(staged)


def test_unexpected_embedding_error_fails_the_run(tmp_path, monkeypatch):
    path = tmp_path / "guidance.txt"
    path.write_text(_guidance_text(), encoding="utf-8")
    failures = []
    monkeypatch.setattr(ingestion.knowledge, "start_ingestion", lambda *args: 17)
    monkeypatch.setattr(ingestion.knowledge, "embed", lambda text: (_ for _ in ()).throw(RuntimeError("quota")))
    monkeypatch.setattr(ingestion.knowledge, "fail_ingestion", lambda *args: failures.append(args))

    result = ingestion.ingest_source(_spec(), path)

    assert result.status == "failed"
    assert failures == [(17, "quota", result.parsed, 0, result.parsed)]


def test_ingestion_deadline_rolls_back_without_activation(tmp_path, monkeypatch):
    path = tmp_path / "guidance.txt"
    path.write_text(_guidance_text(), encoding="utf-8")
    failures = []
    monkeypatch.setattr(ingestion.knowledge, "start_ingestion", lambda *args: "run-deadline")
    monkeypatch.setattr(
        ingestion.knowledge,
        "embed",
        lambda text: pytest.fail("deadline should prevent embedding"),
    )
    monkeypatch.setattr(ingestion.knowledge, "fail_ingestion", lambda *args: failures.append(args))
    monkeypatch.setattr(
        ingestion.knowledge,
        "activate_documents",
        lambda *args: pytest.fail("deadline must not activate staged documents"),
    )

    result = ingestion.ingest_source(_spec(), path, deadline_seconds=0)

    assert result.status == "failed"
    assert failures == [(
        "run-deadline", "ingestion deadline exceeded", result.parsed, 0, result.parsed
    )]


def test_fetch_rejects_invalid_replacement_without_overwriting_local_file(tmp_path, monkeypatch):
    cli = _cli_module()
    spec = _spec()
    path = tmp_path / "pm-kisan.pdf"
    path.write_bytes(b"%PDF-existing")
    spec = spec.__class__(**{**spec.__dict__, "local_path": path})

    class Response:
        headers = {"Content-Type": "application/pdf"}

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            yield b"<html>not a pdf</html>"

    monkeypatch.setattr(cli.requests, "get", lambda *args, **kwargs: Response())
    failures = []
    monkeypatch.setattr(cli.knowledge, "record_ingestion_failure", lambda *args: failures.append(args))

    assert not cli.fetch_source(spec)
    assert path.read_bytes() == b"%PDF-existing"
    assert failures and failures[0][:3] == ("fetch", spec.id, spec.source_url)


def test_fetch_uses_bounded_request_and_replaces_after_validation(tmp_path, monkeypatch):
    cli = _cli_module()
    spec = _spec()
    path = tmp_path / "pm-kisan.pdf"
    spec = spec.__class__(**{**spec.__dict__, "local_path": path})
    received = {}

    class Response:
        headers = {"Content-Type": "application/pdf; charset=binary"}

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            yield b"%PDF-new"

    def request(url, **kwargs):
        received["url"] = url
        received.update(kwargs)
        return Response()

    monkeypatch.setattr(cli.requests, "get", request)

    assert cli.fetch_source(spec)
    assert path.read_bytes() == b"%PDF-new"
    assert received == {
        "url": spec.source_url,
        "headers": {"User-Agent": "AnnaData/1.0 (+https://github.com/satyamarora26/AnnaData)"},
        "timeout": (10, 120),
        "stream": True,
    }


def test_fetch_accepts_a_pdf_exactly_at_the_byte_limit(tmp_path, monkeypatch):
    cli = _cli_module()
    spec = _spec()
    path = tmp_path / "pm-kisan.pdf"
    spec = spec.__class__(**{**spec.__dict__, "local_path": path})
    monkeypatch.setattr(ingestion, "MAX_SOURCE_BYTES", 8)

    class Response:
        headers = {"Content-Type": "application/pdf"}

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            yield b"%PDF-123"

    monkeypatch.setattr(cli.requests, "get", lambda *args, **kwargs: Response())

    assert cli.fetch_source(spec)
    assert path.read_bytes() == b"%PDF-123"


def test_fetch_rejects_a_pdf_one_byte_over_the_limit_without_replacing(tmp_path, monkeypatch):
    cli = _cli_module()
    spec = _spec()
    path = tmp_path / "pm-kisan.pdf"
    path.write_bytes(b"%PDF-old")
    spec = spec.__class__(**{**spec.__dict__, "local_path": path})
    monkeypatch.setattr(ingestion, "MAX_SOURCE_BYTES", 8)

    class Response:
        headers = {"Content-Type": "application/pdf"}

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            yield b"%PDF-1234"

    monkeypatch.setattr(cli.requests, "get", lambda *args, **kwargs: Response())

    assert not cli.fetch_source(spec)
    assert path.read_bytes() == b"%PDF-old"


def test_fetch_accepts_generic_octet_stream_after_pdf_signature_validation(tmp_path, monkeypatch):
    cli = _cli_module()
    spec = _spec()
    path = tmp_path / "pm-kisan.pdf"
    spec = spec.__class__(**{**spec.__dict__, "local_path": path})

    class Response:
        headers = {"Content-Type": "application/octet-stream"}

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            yield b"%PDF-verified"

    monkeypatch.setattr(cli.requests, "get", lambda *args, **kwargs: Response())

    assert cli.fetch_source(spec)
    assert path.read_bytes() == b"%PDF-verified"


def test_fetch_rejects_conflicting_declared_media_without_reading_body(tmp_path, monkeypatch):
    cli = _cli_module()
    spec = _spec()
    path = tmp_path / "pm-kisan.pdf"
    path.write_bytes(b"%PDF-old")
    spec = spec.__class__(**{**spec.__dict__, "local_path": path})

    class Response:
        headers = {"Content-Type": "text/html"}

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            pytest.fail("conflicting media type must reject before reading the response body")

    monkeypatch.setattr(cli.requests, "get", lambda *args, **kwargs: Response())

    assert not cli.fetch_source(spec)
    assert path.read_bytes() == b"%PDF-old"


def test_fetch_refuses_non_http_modes(tmp_path, monkeypatch):
    cli = _cli_module()
    spec = _spec()
    spec = spec.__class__(**{**spec.__dict__, "fetch_mode": "ftp", "local_path": tmp_path / "manual.pdf"})
    monkeypatch.setattr(cli.requests, "get", lambda *args, **kwargs: pytest.fail("only http may fetch"))

    assert not cli.fetch_source(spec)


def test_dry_run_does_not_initialize_the_database(tmp_path, monkeypatch):
    cli = _cli_module()
    spec = _spec()
    path = tmp_path / "guidance.txt"
    path.write_text(_guidance_text(), encoding="utf-8")
    spec = spec.__class__(**{**spec.__dict__, "local_path": path})
    monkeypatch.setattr(
        cli,
        "_parse_args",
        lambda: SimpleNamespace(
            source_id=spec.id,
            all=False,
            fetch=False,
            dry_run=True,
            manifest=Path("ignored.json"),
            deadline_seconds=30,
        ),
    )
    monkeypatch.setattr(cli, "load_catalog", lambda path: {spec.id: spec})
    monkeypatch.setattr(cli.db, "init", lambda: pytest.fail("dry-run must not initialize the database"))

    assert cli.main() == 0


def test_cli_has_a_positive_default_deadline(monkeypatch):
    cli = _cli_module()
    monkeypatch.setattr(sys, "argv", ["ingest_docs.py", "--source-id", "pm_kisan_guidelines"])

    args = cli._parse_args()

    assert args.deadline_seconds > 0


def test_cli_shares_one_deadline_across_sources(monkeypatch):
    cli = _cli_module()
    first = _spec()
    second = first.__class__(**{**first.__dict__, "id": "second_source"})
    args = SimpleNamespace(
        source_id=None,
        all=True,
        fetch=False,
        dry_run=False,
        manifest=Path("ignored.json"),
        deadline_seconds=5,
    )
    deadlines = []

    class Clock:
        def __init__(self):
            self.values = iter((100, 101, 102))

        def monotonic(self):
            return next(self.values)

    monkeypatch.setattr(cli, "_parse_args", lambda: args)
    monkeypatch.setattr(cli, "load_catalog", lambda path: {first.id: first, second.id: second})
    monkeypatch.setattr(cli.db, "init", lambda: None)
    monkeypatch.setattr(cli.db, "is_available", lambda: True)
    monkeypatch.setattr(cli.db, "close", lambda: None)
    monkeypatch.setattr(cli.knowledge, "init", lambda: True)
    monkeypatch.setattr(cli, "time", Clock())
    monkeypatch.setattr(
        cli.ingestion,
        "ingest_source",
        lambda spec, path, dry_run, deadline_seconds: deadlines.append(deadline_seconds)
        or ingestion.IngestResult(spec.id, "skipped", "hash", 1, 0, 0),
    )

    assert cli.main() == 0
    assert deadlines == [4, 3]


def test_cleanup_failure_still_records_the_fetch_audit(tmp_path, monkeypatch):
    cli = _cli_module()
    spec = _spec()
    path = tmp_path / "pm-kisan.pdf"
    spec = spec.__class__(**{**spec.__dict__, "local_path": path})
    part = path.with_suffix(".pdf.part")

    class Response:
        headers = {"Content-Type": "application/pdf"}

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            yield b"not a pdf"

    original_unlink = Path.unlink

    def fail_part_cleanup(candidate, *args, **kwargs):
        if candidate == part:
            raise OSError("cleanup denied")
        return original_unlink(candidate, *args, **kwargs)

    audits = []
    monkeypatch.setattr(cli.requests, "get", lambda *args, **kwargs: Response())
    monkeypatch.setattr(Path, "unlink", fail_part_cleanup)
    monkeypatch.setattr(cli.knowledge, "record_ingestion_failure", lambda *args: audits.append(args))

    assert not cli.fetch_source(spec)
    assert audits and audits[0][:3] == ("fetch", spec.id, spec.source_url)


def test_browser_source_is_not_fetched(tmp_path, monkeypatch, capsys):
    cli = _cli_module()
    spec = _spec()
    spec = spec.__class__(**{**spec.__dict__, "fetch_mode": "browser", "local_path": tmp_path / "manual.pdf"})
    monkeypatch.setattr(cli.requests, "get", lambda *args, **kwargs: pytest.fail("browser sources must not fetch"))

    assert not cli.fetch_source(spec)

    output = capsys.readouterr().out
    assert spec.source_url in output
    assert str(spec.local_path) in output
