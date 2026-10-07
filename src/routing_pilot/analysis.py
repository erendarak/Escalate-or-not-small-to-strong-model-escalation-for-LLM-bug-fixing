"""Rebuild every reported number from the raw JSONL logs (no hand-typed results)."""
from __future__ import annotations

import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path


def _load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def summarise(campaign_dir: Path, reference_policy: str = "strong_only") -> str:
    runs = _load(campaign_dir / "runs.jsonl")
    attempts = _load(campaign_dir / "attempts.jsonl")
    # Keep the latest valid record per run_key; infra-error records stay in the raw log but not in the table.
    latest = {}
    for r in runs:
        if r["final_status"] != "infra_error":
            latest[r["run_key"]] = r
    valid = list(latest.values())
    n_infra = sum(r["final_status"] == "infra_error" for r in runs)
    tokens = defaultdict(lambda: [0, 0])
    keep_ids = {r["run_id"] for r in valid}
    for a in attempts:
        if a.get("run_id") in keep_ids:
            tokens[a["policy"]][0] += a.get("input_tokens") or 0
            tokens[a["policy"]][1] += a.get("output_tokens") or 0

    by_pol = defaultdict(list)
    for r in valid:
        by_pol[r["policy"]].append(r)

    rows = {}
    for pol, rs in by_pol.items():
        per_task = defaultdict(list)
        for r in rs:
            per_task[r["task_id"]].append(r["resolved"])
        rate = statistics.mean(statistics.mean(v) for v in per_task.values())  # task-averaged
        solved = sum(r["resolved"] for r in rs)
        cost = sum(r["total_cost_usd"] for r in rs)
        pub_pass = [r for r in rs if r["final_public_pass"]]
        two_model = len(set(m for r in rs for m in r["models_used"])) > 1
        rows[pol] = dict(
            policy=pol, runs=len(rs), tasks=len(per_task), resolution_rate=rate, solved=solved,
            total_cost_usd=cost, cost_per_solved=(cost / solved) if solved else float("inf"),
            median_e2e_s=statistics.median(r["end_to_end_s"] for r in rs),
            median_api_s=statistics.median(r["api_s"] for r in rs),
            mean_calls=statistics.mean(r["total_calls"] for r in rs),
            escalation_rate=(statistics.mean(r["escalated"] for r in rs) if two_model else None),
            false_acceptance=(sum(r["false_acceptance"] for r in pub_pass) / len(pub_pass)) if pub_pass else None,
            input_tokens=tokens[pol][0], output_tokens=tokens[pol][1],
            statuses=dict(sorted(_count(r["final_status"] for r in rs).items())),
        )

    ref = rows.get(reference_policy)
    lines = [f"# Results: {campaign_dir.name}", "",
             f"Valid runs: {len(valid)} | infra-error records excluded: {n_infra} | reference policy: {reference_policy}", "",
             "| Policy | Resolution (task-avg) | Δ vs ref (pp) | Solved/Runs | Total cost $ | Cost/solved $ | "
             "Cost Δ vs ref | Median e2e s | Median API s | Mean calls | Escalation | False accept. | Tokens in/out |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for pol, x in rows.items():
        d_pp = f"{100 * (x['resolution_rate'] - ref['resolution_rate']):+.1f}" if ref else "–"
        c_red = ("–" if not ref or ref["total_cost_usd"] == 0 else
                 f"{100 * (x['total_cost_usd'] - ref['total_cost_usd']) / ref['total_cost_usd']:+.0f}%")
        cps = "∞ (0 solved)" if x["cost_per_solved"] == float("inf") else f"{x['cost_per_solved']:.4f}"
        esc = "n/a" if x["escalation_rate"] is None else f"{100 * x['escalation_rate']:.0f}%"
        fa = "n/a" if x["false_acceptance"] is None else f"{100 * x['false_acceptance']:.0f}%"
        lines.append(f"| {pol} | {100 * x['resolution_rate']:.1f}% | {d_pp} | {x['solved']}/{x['runs']} | "
                     f"{x['total_cost_usd']:.4f} | {cps} | {c_red} | {x['median_e2e_s']:.1f} | {x['median_api_s']:.1f} | "
                     f"{x['mean_calls']:.2f} | "
                     f"{esc} | {fa} | {x['input_tokens']}/{x['output_tokens']} |")
    lines += ["", "Status counts:"] + [f"- {p}: {x['statuses']}" for p, x in rows.items()]
    lines += ["", "Cost basis per model:"] + _cost_basis(campaign_dir)
    lines += ["", "Notes: resolution = valid output AND all visible AND all hidden tests pass. "
              "False acceptance = passed visible tests but failed hidden tests, among runs whose final "
              "candidate passed visible tests. Costs of local models are standardized list-price estimates "
              "(see config comments for source/date), not money spent; tokens and latency are measured."]
    text = "\n".join(lines)
    (campaign_dir / "summary.md").write_text(text, encoding="utf-8")

    # per-task matrix for error analysis
    tasks = sorted({r["task_id"] for r in valid})
    with (campaign_dir / "per_task.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["task_id"] + list(rows))
        for t in tasks:
            w.writerow([t] + [f"{sum(r['resolved'] for r in by_pol[p] if r['task_id'] == t)}/"
                              f"{sum(1 for r in by_pol[p] if r['task_id'] == t)}" for p in rows])
    return text


def _cost_basis(campaign_dir: Path) -> list[str]:
    cfg_path = campaign_dir / "config_used.json"
    if not cfg_path.exists():
        return ["- unknown (config_used.json missing)"]
    models = json.loads(cfg_path.read_text(encoding="utf-8")).get("models_resolved", {})
    out = []
    for key, m in models.items():
        basis = "billed API price" if m.get("hosting") == "api" else "list-price estimate, runs locally"
        out.append(f"- {key} = {m.get('model')}: ${m.get('usd_per_1m_input', 0)}/1M in, "
                   f"${m.get('usd_per_1m_output', 0)}/1M out ({basis})")
    return out


def _count(it):
    d = defaultdict(int)
    for x in it:
        d[x] += 1
    return d
