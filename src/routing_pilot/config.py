"""Experiment configuration (YAML) -> typed objects."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .models import ModelSpec

ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Config:
    campaign: str
    split: str                      # dev | eval
    repeats: int
    seed: int
    models: dict[str, ModelSpec]
    policies: dict[str, list[str]]  # policy name -> [model key for attempt 1, attempt 2]
    test_timeout_s: float
    budget_usd: float
    transport_retries: int
    data_dir: Path
    results_dir: Path
    tasks: list[str] | None = None  # optional explicit subset
    feedback_only_policies: tuple = ()  # hand-off without the previous code (exploratory escalate_clean)
    raw: dict | None = None


def load_config(path: str | Path) -> Config:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    models = {k: ModelSpec(key=k, **v) for k, v in raw["models"].items()}
    policies = raw["policies"]
    feedback_only = tuple(raw.get("feedback_only_policies") or ())
    if set(feedback_only) - set(policies):
        raise ValueError(f"feedback_only_policies not in policies: {set(feedback_only) - set(policies)}")
    for name, seq in policies.items():
        if len(seq) != 2:
            raise ValueError(f"policy {name}: exactly 2 attempts required (fairness rule)")
        for m in seq:
            if m not in models:
                raise ValueError(f"policy {name} uses unknown model '{m}'")
    return Config(
        campaign=raw["campaign"], split=raw.get("split", "dev"), repeats=raw.get("repeats", 1),
        seed=raw.get("seed", 0), models=models, policies=policies,
        test_timeout_s=raw.get("test_timeout_s", 3.0), budget_usd=raw.get("budget_usd", 0.0),
        transport_retries=raw.get("transport_retries", 2),
        data_dir=ROOT / raw.get("data_dir", "data"), results_dir=ROOT / raw.get("results_dir", "results"),
        tasks=raw.get("tasks"), feedback_only_policies=feedback_only, raw=raw,
    )
