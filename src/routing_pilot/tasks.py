"""Task loading. Public and private material live in SEPARATE directories.

data/public/<task_id>/   buggy.py, public_cases.json, meta.json   -> may reach the model
data/private/<task_id>/  reference.py, hidden_cases.json          -> evaluator only

Only `load_public` is ever used to build prompts. `load_private` is used by the
evaluator after the final candidate is fixed.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PublicTask:
    task_id: str
    func_name: str
    split: str          # dev | eval
    buggy_code: str
    public_cases: list


@dataclass(frozen=True)
class PrivateTask:
    task_id: str
    reference_code: str
    hidden_cases: list


def _read_json(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def list_tasks(data_dir: Path, split: str | None = None) -> list[str]:
    ids = []
    for d in sorted((data_dir / "public").iterdir()):
        if d.is_dir() and (split is None or _read_json(d / "meta.json")["split"] == split):
            ids.append(d.name)
    return ids


def load_labels(data_dir: Path, task_id: str) -> dict:
    """Bug labels for ANALYSIS only (never passed to prompts; tests check this)."""
    meta = _read_json(data_dir / "public" / task_id / "meta.json")
    return {k: meta.get(k) for k in ("bug_type", "bug_family")}


def load_public(data_dir: Path, task_id: str) -> PublicTask:
    d = data_dir / "public" / task_id
    meta = _read_json(d / "meta.json")
    return PublicTask(task_id=task_id, func_name=meta["func_name"], split=meta["split"],
                      buggy_code=(d / "buggy.py").read_text(encoding="utf-8"),
                      public_cases=_read_json(d / "public_cases.json"))


def load_private(data_dir: Path, task_id: str) -> PrivateTask:
    d = data_dir / "private" / task_id
    return PrivateTask(task_id=task_id,
                       reference_code=(d / "reference.py").read_text(encoding="utf-8"),
                       hidden_cases=_read_json(d / "hidden_cases.json"))
