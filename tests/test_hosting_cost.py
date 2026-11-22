"""Arithmetic of the hosting cost model (synthetic numbers, not real prices)."""
import importlib.util
import csv

import pytest
import yaml

from routing_pilot.config import ROOT

spec = importlib.util.spec_from_file_location("hosting_cost", ROOT / "scripts" / "hosting_cost.py")
hc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hc)


@pytest.fixture
def cfg(tmp_path):
    s = tmp_path / "summary.csv"
    s.write_text("target,model,concurrency,median_aggregate_total_tok_s\nd,m,4,1000\n")
    return {
        "campaign": "c",
        "throughput": {"summary_csv": str(s), "target": "d", "model": "m", "concurrency": 4},
        "workload": {"tokens_per_call_in": 300, "tokens_per_call_out": 100, "mix_source": "test",
                     "monthly_tokens": [1e6, 1e10]},
        "assumptions": {"busy_only_utilisation": 0.5},
        "api": [{"name": "api", "usd_per_1m_input": 0.2, "usd_per_1m_output": 0.6}],
        "cloud_gpu": [{"name": "gpu", "usd_per_hour": 1.0}],
        "own_gpu": {"name": "own", "hardware_usd": 1200, "lifetime_months": 12, "busy_watts": 200,
                    "idle_watts": 0, "usd_per_kwh": 0.5},
    }


def test_costs_and_break_even(cfg, tmp_path):
    tp = hc.load_throughput(cfg)
    assert tp["total_tok_s"] == 1000
    assert hc.blended_usd_per_1m(cfg["api"][0], cfg["workload"]) == pytest.approx(0.3)   # (300*.2+100*.6)/400
    rows = {(r["option"], r["monthly_tokens"]): r for r in hc.monthly_costs(cfg, 1000)}
    assert rows[("api", 1e6)]["usd_per_month"] == 0.3
    assert rows[("gpu (always on)", 1e6)]["usd_per_month"] == 730.0
    # 1e10 tokens at 1000 tok/s = 2,777.8 busy hours -> 4 GPUs always on (one GPU carries 2.628e9)
    assert rows[("gpu (always on)", 1e10)]["gpus"] == 4
    assert rows[("gpu (busy hours only)", 1e10)]["usd_per_month"] == pytest.approx(1e10 / 1000 / 3600 / 0.5, abs=0.01)
    # own GPU, 1e6 tokens: 100 $/month hardware + 1000 s at 200 W = 0.0556 kWh * 0.5
    assert rows[("own", 1e6)]["usd_per_month"] == pytest.approx(100.03, abs=0.01)
    be = {(b["gpu_option"], b["api_option"]): b for b in hc.break_even(cfg, 1000)}
    assert be[("gpu (always on)", "api")]["break_even_tokens_per_month"] == pytest.approx(730 / 0.3 * 1e6, rel=1e-6)
    assert be[("gpu (always on)", "api")]["reachable_on_one_gpu"] is True


def test_missing_benchmark_is_an_error_not_an_assumption(cfg, tmp_path):
    cfg["throughput"]["summary_csv"] = str(tmp_path / "nope.csv")
    with pytest.raises(SystemExit):
        hc.load_throughput(cfg)


def test_end_to_end_writes_outputs(cfg, tmp_path):
    p = tmp_path / "h.yaml"
    p.write_text(yaml.safe_dump(cfg))
    hc.main(["--config", str(p), "--results-dir", str(tmp_path)])
    assert (tmp_path / "c" / "cost_vs_volume.svg").read_text().startswith("<svg")
    assert len(list(csv.DictReader((tmp_path / "c" / "costs.csv").open()))) == 2 * 4
