import importlib.util
from pathlib import Path

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


def test_browser_source_is_not_fetched(tmp_path, monkeypatch, capsys):
    cli = _cli_module()
    spec = _spec()
    spec = spec.__class__(**{**spec.__dict__, "fetch_mode": "browser", "local_path": tmp_path / "manual.pdf"})
    monkeypatch.setattr(cli.requests, "get", lambda *args, **kwargs: pytest.fail("browser sources must not fetch"))

    assert not cli.fetch_source(spec)

    output = capsys.readouterr().out
    assert spec.source_url in output
    assert str(spec.local_path) in output
