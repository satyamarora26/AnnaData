import importlib.util
import json
import socket
from pathlib import Path

import pytest


def _benchmark_module():
    path = Path(__file__).resolve().parents[1] / "eval" / "benchmark.py"
    spec = importlib.util.spec_from_file_location("eval_benchmark", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_offline_benchmark_runs_real_functions_without_network(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("network access is forbidden")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    benchmark = _benchmark_module()
    output = tmp_path / "offline.json"
    assert benchmark.main(["--iterations", "2", "--output", str(output)]) == 0
    report = json.loads(output.read_text())
    assert report["mode"] == "offline_guard_ranking"
    assert report["unique_case_count"] == 8
    assert report["iterations"] == 2
    assert report["summary"]["attempted"] == 16
    assert report["summary"]["passed"] == 16
    assert report["summary"]["latency"]["sample_count"] == 16
    assert {r["iteration"] for r in report["cases"]} == {1, 2}
    assert {r["category"] for r in report["cases"]} == {"guard", "ranking"}
    assert all(r["latency_seconds"] >= 0 for r in report["cases"])


def test_benchmark_reports_guard_regression_and_ranking_error(monkeypatch, tmp_path):
    import knowledge
    import output_guards

    benchmark = _benchmark_module()
    monkeypatch.setattr(output_guards, "scrub", lambda answer, gathered: (answer, False))

    def broken_ranking(*args, **kwargs):
        raise RuntimeError("synthetic ranking error")

    monkeypatch.setattr(knowledge, "rank_passages", broken_ranking)
    output = tmp_path / "offline.json"
    assert benchmark.main(["--output", str(output)]) == 1
    report = json.loads(output.read_text())
    assert report["summary"]["failed"] == 2
    assert report["summary"]["errored"] == 4
    assert report["summary"]["passed"] == 2
    assert all(r["error"]["type"] == "RuntimeError" for r in report["cases"] if r["status"] == "error")


def test_benchmark_rejects_zero_iterations():
    with pytest.raises(SystemExit) as error:
        _benchmark_module().main(["--iterations", "0"])
    assert error.value.code == 2
