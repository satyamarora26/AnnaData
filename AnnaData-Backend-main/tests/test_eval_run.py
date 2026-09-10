import importlib.util
from pathlib import Path


def _eval_module():
    path = Path(__file__).resolve().parents[1] / "eval" / "run.py"
    spec = importlib.util.spec_from_file_location("eval_run", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_latency_summary_reports_mean_and_maximum():
    run = _eval_module()

    assert run.format_latency_summary([0.101, 0.205, 0.304]) == (
        "latency: mean=0.20s, max=0.30s across 3 case(s)"
    )


def test_latency_summary_handles_no_completed_cases():
    run = _eval_module()

    assert run.format_latency_summary([]) == "latency: no case timings recorded"
