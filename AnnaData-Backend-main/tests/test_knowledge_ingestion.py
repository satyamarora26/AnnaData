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
        self.commits = 0
        self.ingestion_rows = []
        self.staged_counts = (3, 3)
        self.active_counts = (3, 3)
        self.audit_row = ("pm_kisan_guidelines", "abc123", "running", 0, 0, 0)
        self.transaction_timeout_setting = "0"
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
        if "SELECT status FROM ingestion_runs" in compact:
            return Result((self.audit_row[2],))
        if "FROM ingestion_runs WHERE id = %s FOR UPDATE" in compact:
            return Result(self.audit_row)
        if "current_setting('transaction_timeout', true)" in compact:
            return Result((self.transaction_timeout_setting,))
        if "AS active_count" in compact:
            return Result(self.active_counts)
        if "AS staged_count" in compact:
            return Result(self.staged_counts)
        if "SELECT id, kind, source, status" in compact:
            return Result(rows=self.ingestion_rows)
        if "count(*) FROM pesticide_uses" in compact:
            return Result((0,))
        if "count(*) FROM documents" in compact:
            return Result((0,))
        return Result()

    def commit(self):
        self.commits += 1


class StatefulActivationConnection(RecordingConnection):
    def __init__(self, source):
        super().__init__()
        self.runs = {
            41: {
                "source": source,
                "content_hash": "first-hash",
                "status": "running",
                "parsed_count": 0,
                "stored_count": 0,
                "rejected_count": 0,
            },
            42: {
                "source": source,
                "content_hash": "second-hash",
                "status": "running",
                "parsed_count": 0,
                "stored_count": 0,
                "rejected_count": 0,
            },
        }
        self.documents = [
            {"run_id": run_id, "source": source, "content_hash": content_hash, "active": False}
            for run_id, content_hash in ((41, "first-hash"), (41, "first-hash"),
                                         (42, "second-hash"), (42, "second-hash"))
        ]

    def execute(self, sql, params=()):
        compact = " ".join(sql.split())
        self.calls.append((compact, params))
        if "current_setting('transaction_timeout', true)" in compact:
            return Result((self.transaction_timeout_setting,))
        if "FROM ingestion_runs WHERE id = %s FOR UPDATE" in compact:
            run = self.runs.get(params[0])
            if run is None:
                return Result(None)
            return Result((
                run["source"], run["content_hash"], run["status"], run["parsed_count"],
                run["stored_count"], run["rejected_count"],
            ))
        if "AS active_count" in compact:
            run_id, source, content_hash, active_source = params
            active_count = sum(
                document["source"] == active_source and document["active"]
                for document in self.documents
            )
            owned_count = sum(
                document["run_id"] == run_id
                and document["source"] == source
                and document["content_hash"] == content_hash
                and document["active"]
                for document in self.documents
            )
            return Result((active_count, owned_count))
        if "AS staged_count" in compact:
            source, content_hash, run_id = params
            staged_count = sum(document["run_id"] == run_id for document in self.documents)
            owned_count = sum(
                document["run_id"] == run_id
                and document["source"] == source
                and document["content_hash"] == content_hash
                and not document["active"]
                for document in self.documents
            )
            return Result((staged_count, owned_count))
        if compact.startswith("UPDATE documents SET active = FALSE"):
            source = params[0]
            for document in self.documents:
                if document["source"] == source and document["active"]:
                    document["active"] = False
            return Result()
        if compact.startswith("UPDATE documents SET active = TRUE"):
            if len(params) == 1:
                run_id = params[0]
                for document in self.documents:
                    if document["run_id"] == run_id:
                        document["active"] = True
            else:
                run_id, source, content_hash = params
                for document in self.documents:
                    if (document["run_id"], document["source"], document["content_hash"]) == (
                        run_id, source, content_hash
                    ) and not document["active"]:
                        document["active"] = True
            return Result()
        if compact.startswith("DELETE FROM documents AS stale"):
            source, current_run_id = params
            self.documents = [
                document for document in self.documents
                if document["active"]
                or document["source"] != source
                or document["run_id"] == current_run_id
                or self.runs[document["run_id"]]["status"] == "running"
            ]
            return Result()
        if compact.startswith("DELETE FROM documents WHERE source"):
            source = params[0]
            self.documents = [
                document for document in self.documents
                if document["source"] != source or document["active"]
            ]
            return Result()
        if "SET status = 'completed'" in compact:
            parsed, stored, rejected, run_id = params
            self.runs[run_id].update(
                status="completed",
                parsed_count=parsed,
                stored_count=stored,
                rejected_count=rejected,
            )
            return Result()
        return Result()


def _spec():
    manifest = Path(__file__).resolve().parents[1] / "data" / "source_manifest.json"
    return load_catalog(manifest)["pm_kisan_guidelines"]


def _recording_connection(monkeypatch):
    conn = RecordingConnection()
    monkeypatch.setattr(knowledge.db, "is_available", lambda: True)
    monkeypatch.setattr(knowledge.db, "connection", lambda: nullcontext(conn))
    return conn


def _stateful_connection(monkeypatch):
    conn = StatefulActivationConnection(_spec().id)
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
    assert "SELECT pg_advisory_xact_lock(hashtext(%s))" in statements
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
    assert any("status = 'completed'" in statement for statement in statements)
    assert conn.commits == 1


def test_activation_expiry_before_audit_inspection_is_typed(monkeypatch):
    conn = _recording_connection(monkeypatch)

    with pytest.raises(knowledge.ActivationDeadlineExceeded):
        knowledge.activate_documents(
            41, _spec(), "abc123", 3, 3, 0,
            deadline_at=10, timeout_seconds=0.5, clock=lambda: 10,
        )

    statements = [sql for sql, _ in conn.calls]
    assert not any("FROM ingestion_runs WHERE id = %s FOR UPDATE" in sql for sql in statements)
    assert not any("status = 'completed'" in sql for sql in statements)
    assert conn.commits == 0


def test_activation_server_timeout_is_raised_as_typed_deadline_failure(monkeypatch):
    conn = _recording_connection(monkeypatch)

    class ServerStatementTimeout(RuntimeError):
        sqlstate = "57014"

    conn.errors["AS staged_count"] = ServerStatementTimeout("statement timeout")

    with pytest.raises(knowledge.ActivationDeadlineExceeded, match="deadline"):
        knowledge.activate_documents(
            41, _spec(), "abc123", 3, 3, 0,
            deadline_at=1, timeout_seconds=1, clock=lambda: 0,
        )

    statements = [sql for sql, _ in conn.calls]
    assert not any("status = 'completed'" in sql for sql in statements)
    assert conn.commits == 0


def test_activation_rejects_sub_millisecond_budget_without_rounding_up(monkeypatch):
    conn = _recording_connection(monkeypatch)

    with pytest.raises(knowledge.ActivationDeadlineExceeded):
        knowledge.activate_documents(
            41, _spec(), "abc123", 3, 3, 0,
            deadline_at=0.0009, clock=lambda: 0,
        )

    assert not any(params == ("1ms",) for _, params in conn.calls)
    assert not any("UPDATE documents" in sql for sql, _ in conn.calls)
    assert conn.commits == 0


def test_activation_rejects_unsupported_transaction_timeout_before_mutation(monkeypatch):
    conn = _recording_connection(monkeypatch)
    conn.transaction_timeout_setting = None

    with pytest.raises(knowledge.ActivationDeadlineUnsupported):
        knowledge.activate_documents(
            41, _spec(), "abc123", 3, 3, 0,
            deadline_at=10, clock=lambda: 0,
        )

    assert not any("UPDATE documents" in sql for sql, _ in conn.calls)
    assert not any("status = 'completed'" in sql for sql, _ in conn.calls)
    assert conn.commits == 0


def test_activation_checks_deadline_after_final_statement_before_commit(monkeypatch):
    now = [0.0]

    class FinalStatementConnection(RecordingConnection):
        def execute(self, sql, params=()):
            result = super().execute(sql, params)
            if "SET status = 'completed'" in " ".join(sql.split()):
                now[0] = 1.0
            return result

    conn = FinalStatementConnection()
    monkeypatch.setattr(knowledge.db, "is_available", lambda: True)
    monkeypatch.setattr(knowledge.db, "connection", lambda: nullcontext(conn))

    with pytest.raises(knowledge.ActivationDeadlineExceeded):
        knowledge.activate_documents(
            41, _spec(), "abc123", 3, 3, 0,
            deadline_at=1, clock=lambda: now[0],
        )

    assert conn.commits == 0


def test_activation_uses_transaction_timeout_when_server_supports_it(monkeypatch):
    conn = _recording_connection(monkeypatch)
    conn.transaction_timeout_setting = "0"

    assert knowledge.activate_documents(
        41, _spec(), "abc123", 3, 3, 0,
        deadline_at=10, timeout_seconds=1, clock=lambda: 0,
    )

    statements = [sql for sql, _ in conn.calls]
    assert any("set_config('transaction_timeout'" in sql for sql in statements)


def test_partial_stage_is_rejected_without_deactivating_current_corpus(monkeypatch):
    conn = _recording_connection(monkeypatch)

    with pytest.raises(knowledge.ActivationError, match="incomplete"):
        knowledge.activate_documents(41, _spec(), "abc123", 3, 2, 1)

    assert not any("SET active = FALSE" in sql for sql, _ in conn.calls)
    assert not any("status = 'failed'" in sql for sql, _ in conn.calls)


@pytest.mark.parametrize("staged_counts", [(2, 2), (3, 2)])
def test_stage_count_or_ownership_mismatch_fails_before_deactivation(monkeypatch, staged_counts):
    conn = _recording_connection(monkeypatch)
    conn.staged_counts = staged_counts

    with pytest.raises(knowledge.ActivationError, match="ownership/count"):
        knowledge.activate_documents(41, _spec(), "abc123", 3, 3, 0)

    statements = [sql for sql, _ in conn.calls]
    assert any("AS staged_count" in sql for sql in statements)
    assert not any("SET active = FALSE" in sql for sql in statements)
    assert not any("status = 'failed'" in sql for sql in statements)


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


def test_activation_error_is_typed_and_left_for_the_caller_to_record(monkeypatch):
    conn = _recording_connection(monkeypatch)
    conn.errors["UPDATE documents SET active = FALSE"] = RuntimeError("activation exploded")

    with pytest.raises(knowledge.ActivationError, match="activation exploded"):
        knowledge.activate_documents(41, _spec(), "abc123", 3, 3, 0)

    statements = [sql for sql, _ in conn.calls]
    assert not any("DELETE FROM documents WHERE ingestion_run_id = %s" in sql for sql in statements)
    assert not any("status = 'failed'" in sql for sql in statements)
    assert not any("status = 'completed'" in sql for sql in statements)


def test_running_audit_failure_is_terminally_recorded_and_staging_is_cleaned(monkeypatch):
    conn = _recording_connection(monkeypatch)

    assert knowledge.fail_ingestion(41, "activation exploded", 3, 3, 0)

    statements = [sql for sql, _ in conn.calls]
    assert any("SELECT status FROM ingestion_runs" in sql for sql in statements)
    assert any("DELETE FROM documents WHERE ingestion_run_id = %s" in sql for sql in statements)
    assert any("status = 'failed'" in sql for sql in statements)


@pytest.mark.parametrize("status", ["completed", "failed", "skipped"])
def test_fail_ingestion_does_not_corrupt_terminal_audit(monkeypatch, status):
    conn = _recording_connection(monkeypatch)
    conn.audit_row = (_spec().id, "abc123", status, 3, 3, 0)

    assert not knowledge.fail_ingestion(41, "late activation failure", 3, 3, 0)

    statements = [sql for sql, _ in conn.calls]
    assert any("SELECT status FROM ingestion_runs" in sql for sql in statements)
    assert not any(sql.startswith("DELETE FROM documents") for sql in statements)
    assert not any("status = 'failed'" in sql for sql in statements)


def test_audit_lookup_error_does_not_mutate_the_run(monkeypatch):
    conn = _recording_connection(monkeypatch)
    conn.errors["FROM ingestion_runs WHERE id = %s FOR UPDATE"] = RuntimeError("audit lookup exploded")

    with pytest.raises(knowledge.ActivationError, match="audit lookup exploded"):
        knowledge.activate_documents(41, _spec(), "abc123", 3, 3, 0)

    statements = [sql for sql, _ in conn.calls]
    assert not any(sql.startswith("DELETE FROM documents") for sql in statements)
    assert not any("status = 'failed'" in sql for sql in statements)


def test_completed_active_count_error_does_not_mutate_the_run(monkeypatch):
    conn = _recording_connection(monkeypatch)
    conn.audit_row = (_spec().id, "abc123", "completed", 3, 3, 0)
    conn.errors["AS active_count"] = RuntimeError("active count exploded")

    with pytest.raises(knowledge.ActivationError, match="active count exploded"):
        knowledge.activate_documents(41, _spec(), "abc123", 3, 3, 0)

    statements = [sql for sql, _ in conn.calls]
    assert not any(sql.startswith("DELETE FROM documents") for sql in statements)
    assert not any("status = 'failed'" in sql for sql in statements)


def test_activation_preserves_running_stage_then_publishes_it(monkeypatch):
    conn = _stateful_connection(monkeypatch)

    assert knowledge.activate_documents(41, _spec(), "first-hash", 2, 2, 0)
    assert conn.runs[41]["status"] == "completed"
    assert [document["active"] for document in conn.documents if document["run_id"] == 41] == [True, True]
    assert [document["active"] for document in conn.documents if document["run_id"] == 42] == [False, False]

    assert knowledge.activate_documents(42, _spec(), "second-hash", 2, 2, 0)
    assert conn.runs[42]["status"] == "completed"
    assert [document for document in conn.documents if document["active"]] == [
        {"run_id": 42, "source": _spec().id, "content_hash": "second-hash", "active": True},
        {"run_id": 42, "source": _spec().id, "content_hash": "second-hash", "active": True},
    ]


def test_completed_matching_activation_retry_is_a_non_mutating_success(monkeypatch):
    conn = _stateful_connection(monkeypatch)
    assert knowledge.activate_documents(41, _spec(), "first-hash", 2, 2, 0)
    before_documents = [document.copy() for document in conn.documents]
    conn.calls.clear()

    assert knowledge.activate_documents(41, _spec(), "first-hash", 2, 2, 0)

    assert conn.documents == before_documents
    assert conn.runs[41]["status"] == "completed"
    assert not any(
        sql.startswith(("UPDATE documents", "DELETE FROM documents", "UPDATE ingestion_runs"))
        for sql, _ in conn.calls
    )


def test_completed_nonmatching_activation_is_rejected_without_audit_mutation(monkeypatch):
    conn = _stateful_connection(monkeypatch)
    assert knowledge.activate_documents(41, _spec(), "first-hash", 2, 2, 0)
    before_documents = [document.copy() for document in conn.documents]
    conn.calls.clear()

    with pytest.raises(knowledge.ActivationStateError, match="does not match"):
        knowledge.activate_documents(41, _spec(), "wrong-hash", 2, 2, 0)

    assert conn.documents == before_documents
    assert conn.runs[41]["status"] == "completed"
    assert not any("status = 'failed'" in sql for sql, _ in conn.calls)


def test_completed_invalid_count_retry_is_rejected_without_audit_mutation(monkeypatch):
    conn = _stateful_connection(monkeypatch)
    assert knowledge.activate_documents(41, _spec(), "first-hash", 2, 2, 0)
    before_documents = [document.copy() for document in conn.documents]
    conn.calls.clear()

    with pytest.raises(knowledge.ActivationStateError, match="does not match"):
        knowledge.activate_documents(41, _spec(), "first-hash", 2, 1, 1)

    assert conn.documents == before_documents
    assert conn.runs[41]["status"] == "completed"
    assert not any("status = 'failed'" in sql for sql, _ in conn.calls)


@pytest.mark.parametrize("status", ["failed", "skipped"])
def test_terminal_noncompleted_audit_is_typed_and_not_mutated(monkeypatch, status):
    conn = _recording_connection(monkeypatch)
    conn.audit_row = (_spec().id, "abc123", status, 3, 3, 0)

    with pytest.raises(knowledge.ActivationStateError, match=status):
        knowledge.activate_documents(41, _spec(), "abc123", 3, 3, 0)

    assert not any("UPDATE documents" in sql for sql, _ in conn.calls)
    assert not any("status = 'failed'" in sql for sql, _ in conn.calls)
    assert conn.commits == 0


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
def test_batch_embed_uses_official_endpoint_and_remaining_timeout(monkeypatch):
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return b'{"embeddings": [{"values": [0.1, 0.2]}, {"values": [0.3, 0.4]}]}'

    def urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = request.data
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(knowledge, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(knowledge.urllib.request, "urlopen", urlopen)

    result = knowledge.embed_batch(["first", "second"], dim=2, timeout_seconds=7.5)

    assert result == [[0.1, 0.2], [0.3, 0.4]]
    assert captured["url"].endswith("models/gemini-embedding-001:batchEmbedContents")
    assert b'"requests"' in captured["body"]
    assert 0 < captured["timeout"] <= 7.5


def test_batch_embed_retries_one_429_within_its_remaining_budget(monkeypatch):
    calls = []
    sleeps = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return b'{"embeddings": [{"values": [0.1, 0.2]}]}'

    def urlopen(request, timeout):
        calls.append(timeout)
        if len(calls) == 1:
            raise knowledge.urllib.error.HTTPError(
                request.full_url, 429, "rate limited", {"Retry-After": "0.25"}, None
            )
        return Response()

    monkeypatch.setattr(knowledge, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(knowledge.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(knowledge.time, "monotonic", lambda: 0)

    result = knowledge.embed_batch(
        ["first"], dim=2, timeout_seconds=1, sleep=sleeps.append,
    )

    assert result == [[0.1, 0.2]]
    assert calls == [1, 1]
    assert sleeps == [0.25]


def test_batch_embed_does_not_sleep_when_retry_after_exceeds_fresh_budget(monkeypatch):
    calls = []
    sleeps = []
    clock = iter((0.0, 0.0, 0.8, 0.8))

    def urlopen(request, timeout):
        calls.append(timeout)
        raise knowledge.urllib.error.HTTPError(
            request.full_url, 429, "rate limited", {"Retry-After": "0.3"}, None
        )

    monkeypatch.setattr(knowledge, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(knowledge.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(knowledge.time, "monotonic", lambda: next(clock))

    assert knowledge.embed_batch(
        ["first"], dim=2, timeout_seconds=1, sleep=sleeps.append,
    ) is None

    assert len(calls) == 1
    assert sleeps == []


def test_batch_embed_rejects_nonfinite_retry_after(monkeypatch):
    calls = []
    sleeps = []

    def urlopen(request, timeout):
        calls.append(timeout)
        raise knowledge.urllib.error.HTTPError(
            request.full_url, 429, "rate limited", {"Retry-After": "nan"}, None
        )

    monkeypatch.setattr(knowledge, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(knowledge.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(knowledge.time, "monotonic", lambda: 0)

    assert knowledge.embed_batch(
        ["first"], dim=2, timeout_seconds=1, sleep=sleeps.append,
    ) is None

    assert len(calls) == 1
    assert sleeps == []
