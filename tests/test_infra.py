"""Tests for the evaluation infrastructure itself.

If the evaluator is wrong, every number in the report is wrong, so these run
before any real model is used:  python -m pytest -q
"""
import json
from dataclasses import replace
from pathlib import Path

import pytest

from routing_pilot.candidate import build_candidate
from routing_pilot.config import ROOT, load_config
from routing_pilot.experiment import Store, run_campaign, run_one
from routing_pilot.models import InfraError, ModelResponse, ModelSpec, OpenAICompatClient
from routing_pilot.sandbox import SuiteResult, run_suite
from routing_pilot.tasks import load_private, load_public

TASK = "gcd"


@pytest.fixture
def cfg(tmp_path):
    c = load_config(ROOT / "configs" / "dev_fake.yaml")
    return replace(c, results_dir=tmp_path, campaign="t")


class Scripted:
    """Client returning pre-written responses in order (or raising InfraError)."""
    def __init__(self, *texts):
        self.texts, self.calls, self.seeds = list(texts), 0, []

    def generate(self, messages, seed=None):
        self.calls += 1
        self.seeds.append(seed)
        t = self.texts.pop(0)
        if isinstance(t, Exception):
            raise t
        return ModelResponse(text=t, model_id="scripted", input_tokens=10, output_tokens=10, latency_s=0, cost_usd=0.01)


def code(src):
    return f"```python\n{src}\n```"


def test_reference_passes_and_buggy_fails_for_every_task():
    for d in (ROOT / "data" / "public").iterdir():
        pub, priv = load_public(ROOT / "data", d.name), load_private(ROOT / "data", d.name)
        assert run_suite(priv.reference_code, pub.func_name, pub.public_cases + priv.hidden_cases).passed, d.name
        assert not run_suite(pub.buggy_code, pub.func_name, pub.public_cases).passed, d.name


def test_empty_suite_is_never_a_pass():
    assert not SuiteResult().passed


def test_overfitted_patch_passes_public_but_is_not_resolved(cfg):
    pub = load_public(cfg.data_dir, TASK)
    lookup = {json.dumps(c["input"]): c["expected"] for c in pub.public_cases}
    hack = f"import json\ndef gcd(a, b):\n    return {lookup!r}[json.dumps([a, b])]\n"
    s = Scripted(code(hack))
    rec = run_one(cfg, {"small": s, "strong": s}, Store(cfg.results_dir / "t"), TASK, "strong_only", 0)
    assert rec["final_public_pass"] and rec["hidden_pass"] is False
    assert rec["final_status"] == "unresolved_tests" and rec["false_acceptance"]
    assert s.calls == 1  # public pass => no second call


def test_invalid_output_counts_as_failure_and_triggers_second_attempt(cfg):
    ref = load_private(cfg.data_dir, TASK).reference_code
    small, strong = Scripted("no code here"), Scripted(code(ref))
    rec = run_one(cfg, {"small": small, "strong": strong}, Store(cfg.results_dir / "t"), TASK, "escalate", 0)
    assert rec["final_status"] == "resolved" and rec["escalated"] and rec["total_calls"] == 2


def test_invalid_final_output_stays_in_denominator(cfg):
    s = Scripted("nope", "still nope")
    rec = run_one(cfg, {"small": s, "strong": s}, Store(cfg.results_dir / "t"), TASK, "small_only", 0)
    assert rec["final_status"] == "invalid_output" and rec["resolved"] is False


def test_transport_error_is_infra_not_model_failure(cfg):
    cfg = replace(cfg, transport_retries=0)
    s = Scripted(InfraError("connection refused"))
    rec = run_one(cfg, {"small": s, "strong": s}, Store(cfg.results_dir / "t"), TASK, "small_only", 0)
    assert rec["final_status"] == "infra_error"


def test_forbidden_code_is_rejected():
    for bad in ("import os\ndef gcd(a,b): return os.getcwd()", "def gcd(a,b): return eval('1')",
                "import subprocess\ndef gcd(a,b): pass"):
        assert not build_candidate(code(bad), "gcd", "").valid


def test_no_private_material_in_any_prompt(cfg):
    out = run_campaign(cfg)
    prompts = "\n".join(p.read_text(encoding="utf-8") for p in (out / "artifacts").rglob("*_prompt.txt"))
    assert prompts
    for d in (ROOT / "data" / "public").iterdir():
        pub, priv = load_public(cfg.data_dir, d.name), load_private(cfg.data_dir, d.name)
        if d.name not in prompts:
            continue
        assert priv.reference_code.strip() not in prompts
        public_inputs = {json.dumps(c["input"]) for c in pub.public_cases}
        for c in priv.hidden_cases:
            s = json.dumps(c["input"])
            if s not in public_inputs and len(s) > 6:   # very short inputs like [1] could appear by chance
                assert f"(*{s})" not in prompts, (d.name, s)


def test_resume_does_not_rerun_or_rebill(cfg):
    out = run_campaign(cfg)
    n1 = len((out / "runs.jsonl").read_text().splitlines())
    run_campaign(cfg)
    n2 = len((out / "runs.jsonl").read_text().splitlines())
    assert n1 == n2


def test_each_repeat_gets_its_own_seed(cfg):
    ref = load_private(cfg.data_dir, TASK).reference_code
    store = Store(cfg.results_dir / "t")
    seen = {}
    for repeat in (0, 1, 2):
        small, strong = Scripted("no code"), Scripted(code(ref))
        run_one(cfg, {"small": small, "strong": strong}, store, TASK, "escalate", repeat)
        assert small.seeds == strong.seeds == [cfg.seed + repeat]  # both attempts of a run share the seed
        seen[repeat] = small.seeds[0]
    assert len(set(seen.values())) == 3
    logged = [json.loads(l)["seed"] for l in store.attempts.read_text(encoding="utf-8").splitlines()]
    assert logged == [cfg.seed, cfg.seed, cfg.seed + 1, cfg.seed + 1, cfg.seed + 2, cfg.seed + 2]


def test_openai_client_sends_per_call_seed_and_temperature():
    class Stub:
        def __init__(self):
            self.kwargs = None
            self.chat = self
            self.completions = self

        def create(self, **kwargs):
            self.kwargs = kwargs
            msg = type("M", (), {"content": "ok"})
            return type("R", (), {"choices": [type("C", (), {"message": msg})], "model": "m", "usage": None})

    client = OpenAICompatClient(ModelSpec(key="s", kind="openai_compat", model="m", base_url="http://x/v1",
                                          temperature=0.2, extra={"seed": 999}))
    client.client = stub = Stub()
    client.generate([{"role": "user", "content": "hi"}], seed=7)
    assert stub.kwargs["seed"] == 7 and stub.kwargs["temperature"] == 0.2


def test_budget_counts_only_billed_api_calls(tmp_path):
    store = Store(tmp_path)
    store.append(store.attempts, {"hosting": "local", "cost_usd": 5.0})   # list-price estimate, never billed
    store.append(store.attempts, {"hosting": "api", "cost_usd": 0.25})
    assert store.spent_usd() == 0.25
