from contextlib import nullcontext
from pathlib import Path

import knowledge
import pytest
from source_catalog import load_catalog


class Result:
    def __init__(self, row=None, rows=None):
        self.row = row
        self.rows = rows or []

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


class RecordingConnection:
    def __init__(self):
        self.calls = []
        self.ingestion_rows = []
        self.staged_counts = (3, 3)
        self.errors = {}

    def execute(self, sql, params=()):
        compact = " ".join(sql.split())
        self.calls.append((compact, params))
        for needle, error in self.errors.items():
            if needle in compact:
                raise error
        if "SELECT 1 FROM ingestion_runs" in compact:
            return Result(None)
        if "RETURNING id" in compact:
            return Result((41,))
        if "AS staged_count" in compact:
            return Result(self.staged_counts)
        if "SELECT id, kind, source, status" in compact:
            return Result(rows=self.ingestion_rows)
        if "count(*) FROM pesticide_uses" in compact:
            return Result((0,))
        if "count(*) FROM documents" in compact:
            return Result((0,))
        return Result()


def _spec():
    manifest = Path(__file__).resolve().parents[1] / "data" / "source_manifest.json"
    return load_catalog(manifest)["pm_kisan_guidelines"]


def _recording_connection(monkeypatch):
    conn = RecordingConnection()
    monkeypatch.setattr(knowledge.db, "is_available", lambda: True)
    monkeypatch.setattr(knowledge.db, "connection", lambda: nullcontext(conn))
    return conn


def test_start_ingestion_returns_audit_id(monkeypatch):
    conn = _recording_connection(monkeypatch)
    spec = _spec()

    assert knowledge.start_ingestion("document", spec.id, spec.source_url, "abc123") == 41
    assert any("INSERT INTO ingestion_runs" in sql for sql, _ in conn.calls)


def test_document_activation_deactivates_old_version_before_publishing_new(monkeypatch):
    conn = _recording_connection(monkeypatch)

    assert knowledge.activate_documents(41, _spec(), "abc123", 3, 3, 0)

    statements = [sql for sql, _ in conn.calls]
    assert statements[0] == "SELECT pg_advisory_xact_lock(hashtext(%s))"
    deactivate_index = statements.index(
        "UPDATE documents SET active = FALSE WHERE source = %s AND active = TRUE"
    )
    publish_index = next(
        index for index, sql in enumerate(statements)
        if sql.startswith("UPDATE documents SET active = TRUE")
    )
    assert deactivate_index < publish_index
    publish = statements[publish_index]
    assert "source = %s AND content_hash = %s" in publish
    assert "status = 'completed'" in statements[-1]


def test_partial_stage_is_rejected_without_deactivating_current_corpus(monkeypatch):
    conn = _recording_connection(monkeypatch)

    assert not knowledge.activate_documents(41, _spec(), "abc123", 3, 2, 1)
    assert not any("SET active = FALSE" in sql for sql, _ in conn.calls)
    assert any("status = 'failed'" in sql for sql, _ in conn.calls)


@pytest.mark.parametrize("staged_counts", [(2, 2), (3, 2)])
def test_stage_count_or_ownership_mismatch_fails_before_deactivation(monkeypatch, staged_counts):
    conn = _recording_connection(monkeypatch)
    conn.staged_counts = staged_counts

    assert not knowledge.activate_documents(41, _spec(), "abc123", 3, 3, 0)

    statements = [sql for sql, _ in conn.calls]
    assert any("AS staged_count" in sql for sql in statements)
    assert not any("SET active = FALSE" in sql for sql in statements)
    assert any("status = 'failed'" in sql for sql in statements)


def test_activation_cleanup_preserves_another_running_stage(monkeypatch):
    conn = _recording_connection(monkeypatch)

    assert knowledge.activate_documents(41, _spec(), "abc123", 3, 3, 0)

    cleanup, params = next(
        (sql, params) for sql, params in conn.calls
        if sql.startswith("DELETE FROM documents AS stale")
    )
    assert "stale.ingestion_run_id <> %s" in cleanup
    assert "status = 'running'" in cleanup
    assert params == (_spec().id, 41)


def test_activation_error_fails_run_and_cleans_its_staged_rows(monkeypatch):
    conn = _recording_connection(monkeypatch)
    conn.errors["UPDATE documents SET active = FALSE"] = RuntimeError("activation exploded")

    assert not knowledge.activate_documents(41, _spec(), "abc123", 3, 3, 0)

    statements = [sql for sql, _ in conn.calls]
    assert any("DELETE FROM documents WHERE ingestion_run_id = %s" in sql for sql in statements)
    assert any("status = 'failed'" in sql for sql in statements)
    assert not any("status = 'completed'" in sql for sql in statements)


def test_activation_error_does_not_hide_a_secondary_audit_failure(monkeypatch):
    conn = _recording_connection(monkeypatch)
    conn.errors["UPDATE documents SET active = FALSE"] = RuntimeError("activation exploded")
    conn.errors["UPDATE ingestion_runs SET status = 'failed'"] = RuntimeError("audit exploded")

    with pytest.raises(RuntimeError, match="activation exploded; unable to record ingestion failure: audit exploded"):
        knowledge.activate_documents(41, _spec(), "abc123", 3, 3, 0)


def test_stage_document_keeps_new_chunks_inactive_until_activation(monkeypatch):
    conn = _recording_connection(monkeypatch)

    assert knowledge.stage_document(41, _spec(), "abc123", "guidance", 0, [0.1, 0.2])

    statement, params = conn.calls[-1]
    assert "INSERT INTO documents" in statement
    assert "FALSE" in statement
    assert params[0] == _spec().id
    assert params[8] == "abc123"
    assert params[9] == 41


def test_record_ingestion_failure_persists_terminal_audit_row(monkeypatch):
    conn = _recording_connection(monkeypatch)

    knowledge.record_ingestion_failure("document", "missing", "https://example.test", "not found")

    statement, params = conn.calls[-1]
    assert "INSERT INTO ingestion_runs" in statement
    assert "'failed'" in statement
    assert params[-1] == "not found"


def test_recent_ingestions_limits_and_orders_terminal_rows(monkeypatch):
    conn = _recording_connection(monkeypatch)
    conn.ingestion_rows = [
        (41, "document", "pm_kisan_guidelines", "completed", 3, 3, 0, None, "2026-09-09T10:00:00Z"),
    ]

    rows = knowledge.recent_ingestions(2)

    assert rows == [{
        "id": 41,
        "kind": "document",
        "source": "pm_kisan_guidelines",
        "status": "completed",
        "parsed_count": 3,
        "stored_count": 3,
        "rejected_count": 0,
        "error": None,
        "completed_at": "2026-09-09T10:00:00Z",
    }]


def test_latest_ingestion_returns_first_recent_row(monkeypatch):
    _recording_connection(monkeypatch)
    expected = {"id": 41, "status": "completed"}
    monkeypatch.setattr(knowledge, "recent_ingestions", lambda limit=5: [expected])

    assert knowledge.latest_ingestion() == expected


def test_document_reads_filter_to_active_rows(monkeypatch):
    conn = _recording_connection(monkeypatch)
    monkeypatch.setattr(knowledge, "embed", lambda query: [0.1, 0.2])

    knowledge.search("pm-kisan")
    knowledge.covered_topics()
    knowledge.documents_loaded()
    knowledge.counts()

    document_reads = [
        sql for sql, _ in conn.calls
        if "FROM documents" in sql and not sql.startswith("INSERT")
    ]
    assert len(document_reads) == 4
    assert all("active = TRUE" in sql for sql in document_reads)
