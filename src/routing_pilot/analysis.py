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
    lines += ["", "Resolution by bug_family (task-averaged; descriptive only, 1-8 tasks per family):", ""]
    lines += _family_table(by_pol, list(rows))
    lines += ["", "Status counts:"] + [f"- {p}: {x['statuses']}" for p, x in rows.items()]
    lines += ["", "Cost basis per model:"] + _cost_basis(campaign_dir)
    lines += _break_even(campaign_dir, valid, attempts)
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


def _family_table(by_pol: dict, policies: list[str]) -> list[str]:
    fam_tasks = defaultdict(set)
    for rs in by_pol.values():
        for r in rs:
            fam_tasks[r.get("bug_family") or "unlabelled"].add(r["task_id"])
    out = ["| Family | Tasks | " + " | ".join(policies) + " |", "|---|---|" + "---|" * len(policies)]
    for fam in sorted(fam_tasks):
        cells = []
        for pol in policies:
            per_task = defaultdict(list)
            for r in by_pol[pol]:
                if (r.get("bug_family") or "unlabelled") == fam:
                    per_task[r["task_id"]].append(r["resolved"])
            cells.append(f"{100 * statistics.mean(statistics.mean(v) for v in per_task.values()):.0f}%"
                         if per_task else "–")
        out.append(f"| {fam} | {len(fam_tasks[fam])} | " + " | ".join(cells) + " |")
    return out


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


# Price ratios (C_strong / C_small) marked on the break-even plot for comparison. ILLUSTRATIVE
# round numbers for a frontier model behind a small one, not prices of specific models; the
# eval_3tier campaign replaces them with its configured, sourced prices.
ILLUSTRATIVE_RATIOS = (10, 30, 100)


def break_even(campaign_dir: Path, valid: list[dict], attempts: list[dict]) -> list[dict]:
    """Pre-registered break-even analysis for every two-model policy (e.g. escalate).

    p     = share of the policy's runs whose FIRST call (small model) passed the visible tests,
            per task, then averaged over tasks (same weighting as resolution).
    C_x   = mean measured cost of one first-attempt call to model x (all policies, all tasks).
    Rule  : escalation saves money when C_strong / C_small > 1 / p  (i.e. p * C_strong > C_small).
            Simplification: ignores that the second prompt is longer and that the reference
            policy may also make a second call.
    """
    cfg_path = campaign_dir / "config_used.json"
    if not cfg_path.exists():
        return []
    policies = json.loads(cfg_path.read_text(encoding="utf-8")).get("policies", {})
    keep = {r["run_id"] for r in valid}
    first = [a for a in attempts if a.get("run_id") in keep and a.get("attempt") == 1 and "error_type" not in a]
    call_cost = defaultdict(list)
    for a in first:
        call_cost[a["model_key"]].append(a.get("cost_usd") or 0.0)
    out = []
    for pol, (m1, m2) in policies.items():
        if m1 == m2 or not call_cost[m1] or not call_cost[m2]:
            continue
        per_task = defaultdict(list)
        for a in first:
            if a["policy"] == pol:
                per_task[a["task_id"]].append(a["public_status"] == "pass")
        if not per_task:
            continue
        p_task = {t: statistics.mean(v) for t, v in sorted(per_task.items())}
        c1, c2 = statistics.mean(call_cost[m1]), statistics.mean(call_cost[m2])
        out.append(dict(policy=pol, small=m1, strong=m2, p=statistics.mean(p_task.values()), p_task=p_task,
                        c_small=c1, c_strong=c2, ratio=(c2 / c1) if c1 else float("inf")))
    return out


def _break_even(campaign_dir: Path, valid: list[dict], attempts: list[dict]) -> list[str]:
    lines = []
    for b in break_even(campaign_dir, valid, attempts):
        p, r = b["p"], b["ratio"]
        need_r = (1 / p) if p > 0 else float("inf")
        saves = p * b["c_strong"] > b["c_small"]
        svg = f"break_even_{b['policy']}.svg"
        (campaign_dir / svg).write_text(_break_even_svg(b), encoding="utf-8")
        with (campaign_dir / f"break_even_{b['policy']}.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["task_id", "p_small_first_try"])
            w.writerows([t, f"{v:.3f}"] for t, v in b["p_task"].items())
        n0 = sum(v == 0 for v in b["p_task"].values())
        lines += ["", f"Break-even ({b['policy']}: {b['small']} -> {b['strong']}; pre-registered, see docs/experiment_protocol.md):",
                  f"- p_small_first_try (task-avg, visible pass on call 1) = {p:.3f}; tasks with p = 0: {n0}/{len(b['p_task'])}",
                  f"- mean cost per first call: C_{b['small']} = ${b['c_small']:.6f}, C_{b['strong']} = ${b['c_strong']:.6f} "
                  f"-> measured ratio C_{b['strong']}/C_{b['small']} = {r:.2f}",
                  f"- escalation saves money iff ratio > 1/p = {need_r:.2f} -> "
                  f"{'SAVES' if saves else 'does NOT save'} at the measured ratio",
                  "- at illustrative frontier/small price ratios (equal tokens per call assumed): " + ", ".join(
                      f"{x}x -> {'saves' if x * p > 1 else 'no saving'}" for x in ILLUSTRATIVE_RATIOS),
                  f"- plot: {svg}; per-task p: break_even_{b['policy']}.csv"]
    return lines


def _break_even_svg(b: dict) -> str:
    """Cost ratio (log y) vs p, with the break-even curve ratio = 1/p. Plain SVG, no plotting dependency."""
    import math
    W, H, L, R, T, B = 640, 420, 70, 150, 40, 55
    y_lo, y_hi = 0.5, 1000.0
    X = lambda p: L + p * (W - L - R)
    Y = lambda v: T + (math.log10(y_hi) - math.log10(min(max(v, y_lo), y_hi))) / (math.log10(y_hi) - math.log10(y_lo)) * (H - T - B)
    e = []
    add = e.append
    add(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" font-family="sans-serif" font-size="11">')
    add(f'<rect width="{W}" height="{H}" fill="white"/>')
    add(f'<text x="{W / 2}" y="20" text-anchor="middle" font-size="13">Break-even: {b["policy"]} ({b["small"]} -&gt; {b["strong"]})</text>')
    for v in (1, 10, 100, 1000):
        add(f'<line x1="{L}" x2="{W - R}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" stroke="#eee"/>'
            f'<text x="{L - 6}" y="{Y(v) + 4:.1f}" text-anchor="end">{v}</text>')
    for p in (0, 0.25, 0.5, 0.75, 1):
        add(f'<line x1="{X(p):.1f}" x2="{X(p):.1f}" y1="{T}" y2="{H - B}" stroke="#eee"/>'
            f'<text x="{X(p):.1f}" y="{H - B + 16}" text-anchor="middle">{p}</text>')
    add(f'<rect x="{L}" y="{T}" width="{W - L - R}" height="{H - T - B}" fill="none" stroke="#333"/>')
    pts = " ".join(f"{X(p):.1f},{Y(1 / p):.1f}" for p in [i / 200 for i in range(1, 201)] if 1 / p <= y_hi)
    add(f'<polyline points="{pts}" fill="none" stroke="black" stroke-width="2"/>')
    add(f'<text x="{X(0.06):.1f}" y="{Y(300):.1f}">escalation saves (above curve)</text>')
    add(f'<text x="{X(0.55):.1f}" y="{Y(0.7):.1f}">no saving (below curve)</text>')
    for x in ILLUSTRATIVE_RATIOS:
        add(f'<line x1="{L}" x2="{W - R}" y1="{Y(x):.1f}" y2="{Y(x):.1f}" stroke="#888" stroke-dasharray="4 3"/>'
            f'<text x="{W - R + 6}" y="{Y(x) + 4:.1f}" fill="#555">{x}x illustrative</text>')
    add(f'<line x1="{L}" x2="{W - R}" y1="{Y(b["ratio"]):.1f}" y2="{Y(b["ratio"]):.1f}" stroke="#d62728" stroke-width="2"/>'
        f'<text x="{W - R + 6}" y="{Y(b["ratio"]) + 4:.1f}" fill="#d62728">measured {b["ratio"]:.2f}x</text>')
    add(f'<line x1="{X(b["p"]):.1f}" x2="{X(b["p"]):.1f}" y1="{T}" y2="{H - B}" stroke="#1f77b4" stroke-width="2"/>'
        f'<text x="{X(b["p"]) + 4:.1f}" y="{T + 12}" fill="#1f77b4">p = {b["p"]:.2f}</text>')
    for v in b["p_task"].values():   # rug: one tick per task
        add(f'<line x1="{X(v):.1f}" x2="{X(v):.1f}" y1="{H - B - 8}" y2="{H - B}" stroke="#1f77b4"/>')
    add(f'<circle cx="{X(b["p"]):.1f}" cy="{Y(b["ratio"]):.1f}" r="5" fill="#d62728"/>')
    add(f'<text x="{(L + W - R) / 2}" y="{H - 15}" text-anchor="middle">p = small model passes visible tests on first try (ticks: per task)</text>')
    add(f'<text x="18" y="{(T + H - B) / 2}" text-anchor="middle" transform="rotate(-90 18 {(T + H - B) / 2})">'
        'cost ratio C_strong / C_small (log)</text>')
    add('</svg>')
    return "\n".join(e)


def _count(it):
    d = defaultdict(int)
    for x in it:
        d[x] += 1
    return d
