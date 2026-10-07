"""The bounded repair loop and the campaign runner.

For every (task, policy, repeat):
  attempt 1: policy's first model gets the buggy program + visible tests
             -> candidate -> visible tests. Pass => stop early (no 2nd paid call).
  attempt 2: policy's second model gets the ORIGINAL program + previous candidate
             + normalised VISIBLE-test feedback. Its candidate is final.
  grading:   final candidate runs on hidden tests in a fresh process.
             Hidden results never flow back into any prompt or routing decision.
"""
from __future__ import annotations

import hashlib
import json
import random
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .candidate import build_candidate
from .config import Config
from .models import FakeClient, InfraError, make_client
from .prompts import PROMPT_VERSION, build_messages, format_feedback
from .sandbox import run_suite
from .tasks import list_tasks, load_labels, load_private, load_public


def _h(text: str | None) -> str | None:
    return hashlib.sha256(text.encode()).hexdigest()[:16] if text is not None else None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    """Append-only JSONL logs + per-run artifacts. Never rewrites old records."""

    def __init__(self, root: Path):
        self.root = root
        (root / "artifacts").mkdir(parents=True, exist_ok=True)
        self.attempts = root / "attempts.jsonl"
        self.runs = root / "runs.jsonl"

    def append(self, path: Path, rec: dict):
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def completed_keys(self) -> set[str]:
        """Runs that finished with a valid (non-infra) result are never re-run or re-billed."""
        if not self.runs.exists():
            return set()
        keys = set()
        for line in self.runs.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            if r["final_status"] != "infra_error":
                keys.add(r["run_key"])
        return keys

    def spent_usd(self) -> float:
        """Real money only: local models carry a list-price ESTIMATE that is never billed."""
        if not self.attempts.exists():
            return 0.0
        recs = (json.loads(l) for l in self.attempts.read_text(encoding="utf-8").splitlines())
        return sum(r.get("cost_usd") or 0.0 for r in recs if r.get("hosting") == "api")

    def artifact(self, run_id: str, name: str, content: str):
        d = self.root / "artifacts" / run_id
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(content or "", encoding="utf-8")


def _call_with_retries(client, messages, retries: int, seed: int):
    """Transport retries only. A bad answer is NOT retried (that would cherry-pick)."""
    last = None
    for i in range(retries + 1):
        try:
            return client.generate(messages, seed=seed), i
        except InfraError as exc:
            last = exc
            time.sleep(2 ** i)
    raise InfraError(f"after {retries} retries: {last}")


def run_one(cfg: Config, clients: dict, store: Store, task_id: str, policy: str, repeat: int) -> dict:
    task = load_public(cfg.data_dir, task_id)
    run_id = uuid.uuid4().hex[:12]
    run_key = f"{task_id}|{policy}|{repeat}"
    seed = cfg.seed + repeat   # each repeat is a fresh sample; same seed for every policy within a repeat
    t_start = time.perf_counter()
    feedback, prev_code, cand, pub = None, None, None, None
    api_s, test_s, calls, cost, models_used = 0.0, 0.0, 0, 0.0, []
    infra = None

    for attempt in (1, 2):
        mkey = cfg.policies[policy][attempt - 1]
        spec, client = cfg.models[mkey], clients[mkey]
        if isinstance(client, FakeClient):
            client.current_task = task_id

        # Budget guard: worst case for this call = generous input estimate + max output.
        if cfg.budget_usd > 0 and spec.hosting == "api" and (spec.usd_per_1m_input or spec.usd_per_1m_output):
            worst = (4000 * spec.usd_per_1m_input + spec.max_tokens * spec.usd_per_1m_output) / 1e6
            if store.spent_usd() + worst > cfg.budget_usd:
                raise BudgetExhausted(f"budget {cfg.budget_usd} USD would be exceeded")

        messages = build_messages(task, prev_code, feedback)
        prompt_text = "\n\n".join(f"[{m['role']}]\n{m['content']}" for m in messages)
        try:
            resp, n_retries = _call_with_retries(client, messages, cfg.transport_retries, seed)
        except InfraError as exc:
            infra = str(exc)
            store.append(store.attempts, dict(run_id=run_id, run_key=run_key, task_id=task_id, policy=policy,
                                              repeat=repeat, attempt=attempt, model_key=mkey, model=spec.model,
                                              hosting=spec.hosting, timestamp_utc=_now(), error_type="infra", error=infra,
                                              cost_usd=0.0))
            break

        calls += 1
        models_used.append(mkey)
        api_s += resp.latency_s
        cost += resp.cost_usd
        cand = build_candidate(resp.text, task.func_name, task.buggy_code)
        if cand.valid:
            pub = run_suite(cand.code, task.func_name, task.public_cases, cfg.test_timeout_s)
            test_s += pub.seconds
            public_status = "pass" if pub.passed else ("timeout" if pub.any_timeout else "fail")
            if pub.infra_error:
                infra = "test runner failed to start"
        else:
            pub, public_status = None, "invalid"

        store.artifact(run_id, f"attempt{attempt}_prompt.txt", prompt_text)
        store.artifact(run_id, f"attempt{attempt}_response.txt", resp.text)
        if cand.code:
            store.artifact(run_id, f"attempt{attempt}_candidate.py", cand.code)
        store.append(store.attempts, dict(
            run_id=run_id, run_key=run_key, campaign=cfg.campaign, task_id=task_id, policy=policy,
            repeat=repeat, attempt=attempt, model_key=mkey, model=spec.model, model_id_reported=resp.model_id,
            hosting=spec.hosting, timestamp_utc=_now(), prompt_version=PROMPT_VERSION,
            seed=seed, temperature=spec.temperature,
            prompt_hash=_h(prompt_text), response_hash=_h(resp.text), candidate_hash=_h(cand.code),
            input_tokens=resp.input_tokens, output_tokens=resp.output_tokens, latency_s=round(resp.latency_s, 3),
            cost_usd=resp.cost_usd, transport_retries=n_retries, candidate_valid=cand.valid,
            invalid_reason=cand.reason, lines_changed=cand.lines_changed,
            lines_changed_raw=cand.lines_changed_raw, public_status=public_status,
            public_pass=pub.n_pass if pub else 0, public_total=len(task.public_cases)))

        if infra or public_status == "pass":
            break
        # Feedback for the next attempt: visible tests only, same format for every policy.
        feedback = format_feedback(task, pub) if pub else f"Invalid response: {cand.reason}"
        prev_code = cand.code

    # ---- grading on hidden tests (never fed back) ----
    hidden_pass, hidden_n, hidden_total = None, None, None
    if infra:
        status = "infra_error"
    elif cand is None or not cand.valid:
        status = "invalid_output"
    else:
        priv = load_private(cfg.data_dir, task_id)
        hid = run_suite(cand.code, task.func_name, priv.hidden_cases, cfg.test_timeout_s)
        test_s += hid.seconds
        hidden_pass, hidden_n, hidden_total = hid.passed, hid.n_pass, hid.n
        if hid.infra_error:
            status = "infra_error"
        elif pub.passed and hid.passed:
            status = "resolved"
        elif pub.any_timeout or hid.any_timeout:
            status = "candidate_timeout"
        else:
            status = "unresolved_tests"

    rec = dict(
        run_id=run_id, run_key=run_key, campaign=cfg.campaign, task_id=task_id, policy=policy, repeat=repeat,
        **load_labels(cfg.data_dir, task_id), final_status=status, resolved=status == "resolved",
        final_public_pass=bool(pub and pub.passed), hidden_pass=hidden_pass,
        hidden_cases_passed=hidden_n, hidden_cases_total=hidden_total,
        false_acceptance=bool(pub and pub.passed and hidden_pass is False),
        total_calls=calls, models_used=models_used, escalated=len(set(models_used)) > 1,
        total_cost_usd=cost, api_s=round(api_s, 3), test_s=round(test_s, 3),
        end_to_end_s=round(time.perf_counter() - t_start, 3), final_candidate_hash=_h(cand.code if cand else None),
        infra_error=infra, timestamp_utc=_now())
    store.append(store.runs, rec)
    return rec


class BudgetExhausted(Exception):
    pass


def run_campaign(cfg: Config) -> Path:
    out = cfg.results_dir / cfg.campaign
    store = Store(out)
    (out / "config_used.json").write_text(json.dumps(
        {**cfg.raw, "prompt_version": PROMPT_VERSION, "started_utc": _now(),
         "models_resolved": {k: asdict(v) for k, v in cfg.models.items()}}, indent=1, default=str), encoding="utf-8")
    clients = {k: make_client(spec, cfg.data_dir) for k, spec in cfg.models.items()}
    task_ids = cfg.tasks or list_tasks(cfg.data_dir, cfg.split)
    done = store.completed_keys()
    rng = random.Random(cfg.seed)

    jobs = []
    for repeat in range(cfg.repeats):
        for t in task_ids:
            order = list(cfg.policies)
            rng.shuffle(order)   # interleave policies so drift/load doesn't favour one
            jobs += [(t, p, repeat) for p in order]

    todo = [j for j in jobs if f"{j[0]}|{j[1]}|{j[2]}" not in done]
    print(f"campaign={cfg.campaign} tasks={len(task_ids)} policies={list(cfg.policies)} "
          f"repeats={cfg.repeats} -> {len(jobs)} runs ({len(jobs) - len(todo)} already done)")
    for i, (t, p, r) in enumerate(todo, 1):
        try:
            rec = run_one(cfg, clients, store, t, p, r)
        except BudgetExhausted as exc:
            print(f"STOP: {exc}. Completed results are kept; campaign is INCOMPLETE.")
            break
        print(f"[{i}/{len(todo)}] {t:28s} {p:12s} r{r} -> {rec['final_status']:17s} "
              f"calls={rec['total_calls']} cost=${rec['total_cost_usd']:.4f} {rec['end_to_end_s']:.1f}s")
    return out
