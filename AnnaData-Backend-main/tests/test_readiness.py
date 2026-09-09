from contextlib import contextmanager

import app
import db
import Mandi_Price_Tool
import msp
import readiness


def test_snapshot_distinguishes_configuration_from_runtime(monkeypatch):
    monkeypatch.setattr(readiness.config, "DATABASE_URL", "postgresql://configured")
    monkeypatch.setattr(readiness.db, "is_available", lambda: False)
    monkeypatch.setattr(readiness.db, "status", lambda: "connection timed out")
    monkeypatch.setattr(
        readiness.knowledge,
        "counts",
        lambda: {"documents": 0, "pesticide_uses": 0},
    )
    monkeypatch.setattr(readiness.knowledge, "recent_ingestions", lambda limit=5: [])
    state = readiness.snapshot()
    assert state["database"] == {
        "configured": True,
        "ready": False,
        "status": "connection timed out",
    }
    assert state["knowledge"]["ready"] is False


def test_startup_initializes_knowledge_after_database(monkeypatch):
    calls = []
    monkeypatch.setattr(app.startup, "init_earth_engine", lambda: calls.append("earth_engine"))
    monkeypatch.setattr(app.db, "init", lambda: calls.append("db") or True)
    monkeypatch.setattr(app.feedback, "init", lambda: calls.append("feedback") or True)
    monkeypatch.setattr(app.msp, "init", lambda: calls.append("msp") or True)
    monkeypatch.setattr(app.knowledge, "init", lambda: calls.append("knowledge") or True)
    app.on_startup()
    assert calls.index("db") < calls.index("knowledge")
    assert {"db", "knowledge", "msp", "feedback", "earth_engine"} <= set(calls)


def test_health_is_degraded_when_a_configured_provider_is_not_ready(monkeypatch):
    monkeypatch.setattr(app.readiness, "snapshot", lambda: {
        "database": {"configured": True, "ready": False, "status": "timeout"},
        "gemini": {"configured": True, "ready": True},
    })
    assert app.health()["status"] == "degraded"


def test_msp_counts_reads_without_initializing_schema(monkeypatch):
    class Result:
        def fetchone(self):
            return (2,)

    class Connection:
        def execute(self, query):
            if "CREATE TABLE" in query:
                raise AssertionError("readiness must not initialize schemas")
            return Result()

    @contextmanager
    def connection():
        yield Connection()

    monkeypatch.setattr(msp.db, "is_available", lambda: True)
    monkeypatch.setattr(msp.db, "connection", connection)
    assert msp.counts() == {"msp_commodities": 2}


def test_mandi_readiness_error_does_not_expose_request_parameters(monkeypatch):
    monkeypatch.setattr(Mandi_Price_Tool, "GOV_API_KEY", "configured")
    monkeypatch.setattr(
        Mandi_Price_Tool,
        "_fetch",
        lambda state, commodity: (_ for _ in ()).throw(
            RuntimeError("https://example.test?api-key=secret")
        ),
    )
    monkeypatch.setattr(Mandi_Price_Tool, "_record_failure", lambda: None)
    monkeypatch.setattr(Mandi_Price_Tool, "_record_outcome", lambda ok: None)
    Mandi_Price_Tool.get_state_data("Bihar")
    assert Mandi_Price_Tool.last_error() == "RuntimeError: data.gov.in request failed"


def test_mandi_availability_reads_without_initializing_schema(monkeypatch):
    class Result:
        def fetchone(self):
            return None

    class Connection:
        queries = []

        def execute(self, query):
            self.queries.append(query)
            return Result()

    connection = Connection()

    @contextmanager
    def database_connection():
        yield connection

    monkeypatch.setattr(Mandi_Price_Tool, "GOV_API_KEY", "configured")
    monkeypatch.setattr(Mandi_Price_Tool, "_circuit_open", lambda: False)
    monkeypatch.setattr(db, "is_available", lambda: True)
    monkeypatch.setattr(db, "connection", database_connection)
    assert Mandi_Price_Tool.is_available() is True
    assert all("CREATE TABLE" not in query for query in connection.queries)
