import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def _eval_module():
    path = Path(__file__).resolve().parents[1] / "eval" / "run.py"
    spec = importlib.util.spec_from_file_location("eval_run", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_latency_summary_reports_mean_and_maximum():
    run = _eval_module()

    assert run.format_latency_summary([0.101, 0.205, 0.304]) == (
        "latency: mean=0.20s, p50=0.20s, p95=0.30s, max=0.30s across 3 case(s)"
    )


def test_latency_summary_handles_no_completed_cases():
    run = _eval_module()

    assert run.format_latency_summary([]) == "latency: no case timings recorded"


@pytest.mark.parametrize("values,p50,p95", [
    ([4, 1, 3, 2], 2, 4),
    (list(range(1, 21)), 10, 19),
    ([0.25], 0.25, 0.25),
    ([], None, None),
])
def test_nearest_rank_percentiles_include_sample_count(values, p50, p95):
    summary = _eval_module().latency_summary(values)
    assert summary["sample_count"] == len(values)
    assert summary["p50_seconds"] == p50
    assert summary["p95_seconds"] == p95
    assert summary["method"] == "nearest_rank"


def _offline_live_runner(monkeypatch, tmp_path, cases):
    import Agent
    import db
    import startup

    run = _eval_module()
    path = tmp_path / "cases.yaml"
    path.write_text(run.yaml.safe_dump(cases), encoding="utf-8")
    monkeypatch.setattr(run, "CASES", path)
    monkeypatch.setattr(db, "init", lambda: None)
    monkeypatch.setattr(db, "close", lambda: None)
    monkeypatch.setattr(startup, "init_earth_engine", lambda: None)

    def answer(query, **kwargs):
        if query == "error":
            raise RuntimeError("synthetic provider error")
        return SimpleNamespace(answer=query)

    monkeypatch.setattr(Agent, "run_agent", answer)
    return run


def test_json_preserves_pass_failure_error_and_all_timings(monkeypatch, tmp_path):
    run = _offline_live_runner(monkeypatch, tmp_path, [
        {"id": "pass", "query": "hello", "expect": {"contains_all": ["hello"]}},
        {"id": "fail", "query": "goodbye", "expect": {"contains_all": ["hello"]}},
        {"id": "error", "query": "error"},
    ])
    ticks = iter([0, 1, 10, 12, 20, 24])
    monkeypatch.setattr(run.time, "perf_counter", lambda: next(ticks))
    output = tmp_path / "nested" / "report.json"

    assert run.main(["--output", str(output)]) == 1
    report = json.loads(output.read_text())
    assert report["mode"] == "live_agent"
    assert report["status"] == "completed"
    assert report["summary"]["passed"] == 1
    assert report["summary"]["failed"] == 1
    assert report["summary"]["errored"] == 1
    assert report["summary"]["latency"]["sample_count"] == 3
    assert report["summary"]["latency"]["p95_seconds"] == 4
    assert report["summary"]["completed_latency"]["sample_count"] == 2
    assert [(r["id"], r["status"], r["latency_seconds"]) for r in report["cases"]] == [
        ("pass", "passed", 1), ("fail", "failed", 2), ("error", "error", 4),
    ]
    assert report["cases"][1]["assertion_failures"] == ["missing required phrase 'hello'"]
    assert report["cases"][2]["error"] == {
        "type": "RuntimeError", "message": "synthetic provider error", "phase": "agent",
    }
    assert len(report["case_source"]["sha256"]) == 64


def test_filtered_report_records_selection(monkeypatch, tmp_path):
    run = _offline_live_runner(monkeypatch, tmp_path, [
        {"id": "skip", "query": "error", "tags": ["slow"]},
        {"id": "keep", "query": "ok", "tags": ["fast"]},
        {"id": "limit", "query": "error", "tags": ["fast"]},
    ])
    output = tmp_path / "report.json"
    assert run.main(["--tag", "fast", "--limit", "1", "--output", str(output)]) == 0
    report = json.loads(output.read_text())
    assert [r["id"] for r in report["cases"]] == ["keep"]
    assert report["selection"] == {"tag": "fast", "id": None, "limit": 1, "delay_seconds": 0}
    assert report["selected_count"] == 1


def test_empty_selection_writes_no_measured_values(monkeypatch, tmp_path):
    run = _offline_live_runner(monkeypatch, tmp_path, [])
    output = tmp_path / "report.json"
    assert run.main(["--output", str(output)]) == 1
    report = json.loads(output.read_text())
    assert report["status"] == "no_cases"
    assert report["summary"]["latency"]["p95_seconds"] is None
    assert report["summary"]["pass_rate"] is None


def test_startup_error_is_saved_and_database_closed(monkeypatch, tmp_path):
    import db
    import startup

    run = _offline_live_runner(monkeypatch, tmp_path, [{"id": "unrun", "query": "ok"}])
    closed = []
    monkeypatch.setattr(db, "close", lambda: closed.append(True))

    def broken_startup():
        raise RuntimeError("synthetic startup failure")

    monkeypatch.setattr(startup, "init_earth_engine", broken_startup)
    output = tmp_path / "report.json"
    assert run.main(["--output", str(output)]) == 1
    report = json.loads(output.read_text())
    assert report["status"] == "error"
    assert report["run_error"]["message"] == "synthetic startup failure"
    assert report["summary"]["attempted"] == 0
    assert closed == [True]


@pytest.mark.parametrize("args", [["--limit", "0"], ["--limit", "-1"], ["--delay", "-1"], ["--delay", "nan"]])
def test_invalid_cli_values_are_rejected(args):
    with pytest.raises(SystemExit) as error:
        _eval_module().main(args)
    assert error.value.code == 2
