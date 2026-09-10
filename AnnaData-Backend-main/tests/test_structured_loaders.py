from contextlib import nullcontext
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import msp
import pytest
from tools import fetch_cibrc
from tools import load_cibrc
from tools.load_cibrc import looks_like_product, valid_waiting_period


FIXTURE = Path(__file__).parent / "fixtures" / "msp_sample.csv"


class RecordingConnection:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((" ".join(sql.split()), params))


class RowsResult:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class FailingCursor:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def executemany(self, sql, rows):
        raise RuntimeError("insert failed")


class FailingTransaction(RecordingConnection):
    def __init__(self):
        super().__init__()
        self.rolled_back = False

    def cursor(self):
        return FailingCursor()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, *_):
        self.rolled_back = exc_type is not None
        return False


def test_msp_parser_keeps_declared_prices_and_drops_dash():
    rows = msp.parse_csv(str(FIXTURE))

    assert rows == [
        ("Cereals", "Wheat", 2585.0),
        ("Pulses", "Red gram/Arhar/Tur(whole)", 8000.0),
    ]


def test_msp_replace_deletes_only_selected_year_then_upserts_every_alias(monkeypatch):
    connection = RecordingConnection()
    audit_calls = []
    monkeypatch.setattr(msp.db, "is_available", lambda: True)
    monkeypatch.setattr(msp.knowledge, "init", lambda: True)
    monkeypatch.setattr(msp, "init", lambda: True)
    monkeypatch.setattr(msp.db, "connection", lambda: nullcontext(connection))
    monkeypatch.setattr(
        msp.knowledge,
        "start_ingestion",
        lambda *args, **kwargs: audit_calls.append((*args, kwargs["skip_completed"])) or 41,
    )

    result = msp.replace_csv(
        str(FIXTURE), "2026-27", "Reviewed MSP", "https://agmarknet.gov.in/msp.csv"
    )

    statements = [sql for sql, _ in connection.calls]
    assert statements[0] == "DELETE FROM commodity_msp WHERE year = %s"
    assert connection.calls[0][1] == ("2026-27",)
    upserts = [sql for sql in statements if "INSERT INTO commodity_msp" in sql]
    assert len(upserts) == 13
    assert all("ON CONFLICT (alias, commodity_id, grade, year)" in sql for sql in upserts)
    assert "status = 'completed'" in statements[-1]
    assert result["commodities"] == 2
    assert result["aliases"] == 13
    assert result["year"] == "2026-27"
    assert len(result["content_hash"]) == 64
    assert audit_calls[0][-1] is False


def test_msp_replace_initializes_fresh_storage_before_audit_and_replacement(monkeypatch):
    events = []
    storage = {"available": False}

    class FreshProcessConnection(RecordingConnection):
        def execute(self, sql, params=()):
            compact = " ".join(sql.split())
            if "CREATE TABLE IF NOT EXISTS commodity_msp" in compact:
                events.append("msp schema")
            elif compact == "DELETE FROM commodity_msp WHERE year = %s":
                events.append("replacement")
            return super().execute(sql, params)

    connection = FreshProcessConnection()

    def initialize_database():
        events.append("db init")
        storage["available"] = True
        return True

    def initialize_knowledge():
        events.append("knowledge init")
        return True

    def start_audit(*args, **kwargs):
        assert events == ["db init", "knowledge init", "msp schema"]
        events.append("audit")
        return 41

    monkeypatch.setattr(msp.db, "is_available", lambda: storage["available"])
    monkeypatch.setattr(msp.db, "init", initialize_database)
    monkeypatch.setattr(msp.db, "connection", lambda: nullcontext(connection))
    monkeypatch.setattr(msp.knowledge, "init", initialize_knowledge)
    monkeypatch.setattr(msp.knowledge, "start_ingestion", start_audit)

    result = msp.replace_csv(
        str(FIXTURE), "2026-27", "Reviewed MSP", "https://agmarknet.gov.in/msp.csv"
    )

    assert result["aliases"] == 13
    assert events == ["db init", "knowledge init", "msp schema", "audit", "replacement"]


def test_msp_empty_parse_records_failure_without_opening_replacement_transaction(tmp_path, monkeypatch):
    empty = tmp_path / "empty.csv"
    empty.write_text("Group,Commodity,MSP,Unit\nVegetables,Onion,-,Rs/Quintal\n", encoding="utf-8")
    failures = []
    monkeypatch.setattr(msp.db, "is_available", lambda: True)
    monkeypatch.setattr(msp.knowledge, "init", lambda: True)
    monkeypatch.setattr(msp, "init", lambda: True)
    monkeypatch.setattr(msp.knowledge, "record_ingestion_failure", lambda *args: failures.append(args))
    monkeypatch.setattr(msp.db, "connection", lambda: pytest.fail("empty input must not open a transaction"))

    with pytest.raises(ValueError, match="no valid MSP rows"):
        msp.replace_csv(str(empty), "2026-27", "Reviewed MSP", "https://agmarknet.gov.in/msp.csv")

    assert failures and failures[0][:3] == ("msp", "msp:2026-27", "https://agmarknet.gov.in/msp.csv")


def test_msp_does_not_claim_a_failed_audit_when_storage_cannot_initialize(monkeypatch):
    monkeypatch.setattr(msp.db, "is_available", lambda: False)
    monkeypatch.setattr(msp.db, "init", lambda: False)
    monkeypatch.setattr(
        msp.knowledge,
        "record_ingestion_failure",
        lambda *args: pytest.fail("storage initialization failure must not claim an audit"),
    )

    with pytest.raises(RuntimeError, match="could not initialize database"):
        msp.replace_csv(
            str(FIXTURE), "2026-27", "Reviewed MSP", "https://agmarknet.gov.in/msp.csv"
        )


def test_msp_rejects_non_official_source_without_initializing_the_database(monkeypatch):
    failures = []
    monkeypatch.setattr(msp, "init", lambda: pytest.fail("untrusted URL must not initialize the database"))
    monkeypatch.setattr(msp.knowledge, "record_ingestion_failure", lambda *args: failures.append(args))

    with pytest.raises(ValueError, match="official HTTPS"):
        msp.replace_csv(str(FIXTURE), "2026-27", "Reviewed MSP", "http://example.com/msp.csv")

    assert failures and failures[0][:3] == ("msp", "msp:2026-27", "http://example.com/msp.csv")


def test_msp_replacement_keeps_shared_base_aliases_for_distinct_grades(tmp_path, monkeypatch):
    source = tmp_path / "graded.csv"
    source.write_text(
        "Group,Commodity,MSP,Unit\n"
        "Commercial,Cotton (Medium Staple),8267,Rs/quintal\n"
        "Commercial,Cotton (Long Staple),8667,Rs/quintal\n",
        encoding="utf-8",
    )
    connection = RecordingConnection()
    monkeypatch.setattr(msp, "_initialize_storage", lambda: None)
    monkeypatch.setattr(msp.db, "connection", lambda: nullcontext(connection))
    monkeypatch.setattr(msp.knowledge, "start_ingestion", lambda *args, **kwargs: 41)

    msp.replace_csv(
        str(source), "2026-27", "Reviewed MSP", "https://agmarknet.gov.in/msp.csv"
    )

    cotton_rows = [
        params for sql, params in connection.calls
        if "INSERT INTO commodity_msp" in sql and params[0] == "cotton"
    ]
    assert {(params[1], params[2]) for params in cotton_rows} == {
        ("cotton", "medium staple"),
        ("cotton", "long staple"),
    }
    assert all(
        "ON CONFLICT (alias, commodity_id, grade, year)" in sql
        for sql, _ in connection.calls if "INSERT INTO commodity_msp" in sql
    )


def test_msp_unqualified_alias_returns_all_latest_grade_alternatives(monkeypatch):
    class LookupConnection:
        def execute(self, sql, params=()):
            if "SELECT DISTINCT label" in " ".join(sql.split()):
                return RowsResult([
                    ("Cotton (Long Staple)", 8667, "2026-27", "cotton", "long staple"),
                    ("Cotton (Medium Staple)", 8267, "2026-27", "cotton", "medium staple"),
                ])
            return RowsResult([])

    monkeypatch.setattr(msp.db, "is_available", lambda: True)
    monkeypatch.setattr(msp.db, "connection", lambda: nullcontext(LookupConnection()))

    result = msp.for_crop("cotton")

    assert "Cotton (Long Staple)" in result and "Rs 8,667" in result
    assert "Cotton (Medium Staple)" in result and "Rs 8,267" in result
    assert "identify the grade" in result


def test_msp_qualified_alias_returns_only_requested_grade(monkeypatch):
    class LookupConnection:
        def execute(self, sql, params=()):
            if "SELECT DISTINCT label" in " ".join(sql.split()):
                return RowsResult([
                    ("Cotton (Long Staple)", 8667, "2026-27", "cotton", "long staple"),
                ])
            return RowsResult([])

    monkeypatch.setattr(msp.db, "is_available", lambda: True)
    monkeypatch.setattr(msp.db, "connection", lambda: nullcontext(LookupConnection()))

    result = msp.for_crop("cotton long staple")

    assert "Cotton (Long Staple)" in result and "Rs 8,667" in result
    assert "Medium Staple" not in result


def test_cibrc_product_and_waiting_period_safety_rules():
    assert looks_like_product("Acephate 75% SP", ["", "", "", "", ""])
    assert not looks_like_product("For control of bollworm", ["", "", "", "", ""])
    assert valid_waiting_period("21") == "21"
    assert valid_waiting_period("500-1000") is None


def test_cibrc_insert_failure_rolls_back_category_replacement(monkeypatch):
    from tools import load_cibrc

    transaction = FailingTransaction()
    monkeypatch.setattr(load_cibrc.db, "connection", lambda: transaction)
    uses = [{
        "category": "insecticide",
        "product": "Acephate 75% SP",
        "crop": "Cotton",
        "pest": "Bollworm",
        "dose_ai": "500 g",
        "dose_formulation": "667 g",
        "dilution": None,
        "waiting_period": "21",
        "source": "CIB&RC",
        "source_url": "https://ppqs.gov.in/register.pdf",
    }]

    with pytest.raises(RuntimeError, match="insert failed"):
        load_cibrc.replace_category("insecticide", uses, 41)

    assert transaction.rolled_back
    assert transaction.calls == [
        ("DELETE FROM pesticide_uses WHERE category = %s", ("insecticide",)),
    ]


def test_cibrc_download_validates_part_before_replacing_existing_pdf(tmp_path, monkeypatch):
    target = tmp_path / "insecticide.pdf"
    target.write_bytes(b"%PDF-old")
    received = {}

    class Response:
        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            yield b"%PDF-new"

    def request(url, **kwargs):
        received["url"] = url
        received.update(kwargs)
        return Response()

    monkeypatch.setattr(fetch_cibrc.requests, "get", request)

    assert fetch_cibrc.download_file("https://ppqs.gov.in/register.pdf", target, 12) == 8
    assert target.read_bytes() == b"%PDF-new"
    assert received == {
        "url": "https://ppqs.gov.in/register.pdf",
        "headers": {"User-Agent": fetch_cibrc.UA},
        "timeout": 12,
        "stream": True,
    }


def test_cibrc_fetch_script_runs_as_a_direct_entrypoint():
    backend = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "tools/fetch_cibrc.py", "--help"],
        cwd=backend,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_cibrc_review_rejects_missing_declared_artifacts(tmp_path):
    (tmp_path / "insecticide.pdf").write_bytes(b"%PDF-only-one")

    with pytest.raises(ValueError, match="missing CIB&RC artifacts"):
        load_cibrc.review_artifacts(tmp_path)


def test_cibrc_review_rejects_unexpected_pdf_before_parsing(tmp_path):
    (tmp_path / "unexpected.pdf").write_bytes(b"%PDF-untrusted")

    with pytest.raises(ValueError, match="unexpected CIB&RC artifacts"):
        load_cibrc.review_artifacts(tmp_path)


def test_cibrc_review_rejects_hash_mismatch_before_parsing(tmp_path):
    for category in fetch_cibrc.FILES:
        (tmp_path / f"{category}.pdf").write_bytes(b"%PDF-wrong")

    with pytest.raises(ValueError, match="hash mismatch"):
        load_cibrc.review_artifacts(tmp_path)


def test_cibrc_fetch_partial_failure_preserves_retained_set(tmp_path, monkeypatch):
    out = tmp_path / "cibrc"
    out.mkdir()
    for category in fetch_cibrc.FILES:
        (out / f"{category}.pdf").write_bytes(f"%PDF-old-{category}".encode())
    calls = []

    def partial_download(url, target, timeout):
        calls.append(url)
        if len(calls) == 1:
            target.write_bytes(b"%PDF-new")
            return 8
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(fetch_cibrc, "download_file", partial_download)

    assert fetch_cibrc.main(["--out", str(out)]) == 1
    assert (out / "insecticide.pdf").read_bytes() == b"%PDF-old-insecticide"
    assert len(calls) == len(fetch_cibrc.FILES)


def test_cibrc_dry_run_is_nonzero_when_any_category_parses_empty(tmp_path, monkeypatch):
    reviewed = [
        (
            SimpleNamespace(
                category=category,
                sha256="a" * 64,
                source_url="https://ppqs.gov.in/a.pdf",
                as_on="31.03.2026",
            ),
            tmp_path / f"{category}.pdf",
        )
        for category in fetch_cibrc.FILES
    ]
    parsed = []
    monkeypatch.setattr(load_cibrc, "verified_artifacts", lambda folder: reviewed)

    def parse_pdf(path, category, **kwargs):
        parsed.append(category)
        return [] if category == "pgr" else [{"category": category}]

    monkeypatch.setattr(load_cibrc, "parse_pdf", parse_pdf)

    assert load_cibrc.main(["--folder", str(tmp_path), "--dry-run"]) == 1
    assert "pgr" in parsed


def test_cibrc_batch_replacement_rolls_back_all_categories(monkeypatch):
    transaction = FailingTransaction()
    monkeypatch.setattr(load_cibrc.db, "connection", lambda: transaction)
    reviewed = [
        (SimpleNamespace(category=category), [{"category": category}])
        for category in fetch_cibrc.FILES
    ]
    run_ids = {category: index for index, category in enumerate(fetch_cibrc.FILES, 1)}

    with pytest.raises(RuntimeError, match="insert failed"):
        load_cibrc.replace_all_categories(reviewed, run_ids)

    assert transaction.rolled_back


def test_cibrc_batch_replacement_rejects_incomplete_category_set(monkeypatch):
    monkeypatch.setattr(
        load_cibrc.db,
        "connection",
        lambda: pytest.fail("incomplete set must fail before opening a transaction"),
    )

    with pytest.raises(ValueError, match="complete six-category"):
        load_cibrc.replace_all_categories(
            [(SimpleNamespace(category="insecticide"), [{"category": "insecticide"}])],
            {"insecticide": 41},
        )
