from contextlib import nullcontext
from pathlib import Path

import msp
import pytest
from tools import fetch_cibrc
from tools.load_cibrc import looks_like_product, valid_waiting_period


FIXTURE = Path(__file__).parent / "fixtures" / "msp_sample.csv"


class RecordingConnection:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((" ".join(sql.split()), params))


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
    assert len(upserts) == 5
    assert all("ON CONFLICT (alias, year)" in sql for sql in upserts)
    assert "status = 'completed'" in statements[-1]
    assert result["commodities"] == 2
    assert result["aliases"] == 5
    assert result["year"] == "2026-27"
    assert len(result["content_hash"]) == 64
    assert audit_calls[0][-1] is False


def test_msp_empty_parse_records_failure_without_opening_replacement_transaction(tmp_path, monkeypatch):
    empty = tmp_path / "empty.csv"
    empty.write_text("Group,Commodity,MSP,Unit\nVegetables,Onion,-,Rs/Quintal\n", encoding="utf-8")
    failures = []
    monkeypatch.setattr(msp.knowledge, "record_ingestion_failure", lambda *args: failures.append(args))
    monkeypatch.setattr(msp.db, "connection", lambda: pytest.fail("empty input must not open a transaction"))

    with pytest.raises(ValueError, match="no valid MSP rows"):
        msp.replace_csv(str(empty), "2026-27", "Reviewed MSP", "https://agmarknet.gov.in/msp.csv")

    assert failures and failures[0][:3] == ("msp", "msp:2026-27", "https://agmarknet.gov.in/msp.csv")


def test_msp_rejects_non_official_source_without_initializing_the_database(monkeypatch):
    failures = []
    monkeypatch.setattr(msp, "init", lambda: pytest.fail("untrusted URL must not initialize the database"))
    monkeypatch.setattr(msp.knowledge, "record_ingestion_failure", lambda *args: failures.append(args))

    with pytest.raises(ValueError, match="official HTTPS"):
        msp.replace_csv(str(FIXTURE), "2026-27", "Reviewed MSP", "http://example.com/msp.csv")

    assert failures and failures[0][:3] == ("msp", "msp:2026-27", "http://example.com/msp.csv")


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
