"""Monthly cost of serving a 7B-class model: own GPU vs rented cloud GPU vs per-token API.

Added November 2026, after the original study. Extends the break-even idea from the study
(when does one option become cheaper than another) from model routing to hosting.

    python scripts/hosting_cost.py --config configs/hosting_options.yaml

Inputs, all from files so every number can be traced:
  * prices, sources and access dates: configs/hosting_options.yaml
  * throughput: the measured serving benchmark (results/<bench>/summary.csv)
  * workload token mix: measured median tokens per call in the study (stated in the config)
Outputs in results/<campaign>/: costs.csv, break_even.csv, summary.md, cost_vs_volume.svg.

Cost models (per month, 730 hours):
  api            volume x blended per-token price (input/output mix from the workload)
  gpu_always_on  enough GPUs, each rented 730 h, to carry the volume at the measured throughput
  gpu_busy_only  only the GPU-hours actually needed at the measured throughput, divided by an
                 assumed utilisation (an idealised scale-to-zero service; cold starts ignored)
  own_gpu        hardware amortised over its life + electricity (busy at full power, idle otherwise)
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
HOURS_PER_MONTH = 730


def load_throughput(cfg: dict) -> dict:
    t = cfg["throughput"]
    path = ROOT / t["summary_csv"]
    if not path.exists():
        raise SystemExit(f"{path} not found: run scripts/serving_bench.py first (no throughput is assumed).")
    for row in csv.DictReader(path.open(encoding="utf-8")):
        if (row["target"], row["model"], int(row["concurrency"])) == (t["target"], t["model"], t["concurrency"]):
            return {"total_tok_s": float(row["median_aggregate_total_tok_s"]), "source": str(t["summary_csv"]),
                    "row": f'{t["target"]} {t["model"]} at concurrency {t["concurrency"]}'}
    raise SystemExit(f"no row for {t} in {path}")


def resolve_measured_power(cfg: dict) -> None:
    """own_gpu busy/idle watts given as "measured" are read from the benchmark's environment.json."""
    g, t = cfg.get("own_gpu"), cfg["throughput"]
    if not g or "measured" not in (g.get("busy_watts"), g.get("idle_watts")):
        return
    env = json.loads((ROOT / t["environment_json"]).read_text(encoding="utf-8"))
    v = env["vram"][f'{t["target"]} {t["model"]}']
    for key, field in (("busy_watts", "gpu_power_mean_under_load_w"), ("idle_watts", "gpu_power_idle_w")):
        if g[key] == "measured":
            if v.get(field) is None:
                raise SystemExit(f"{field} not recorded in {t['environment_json']}; set own_gpu.{key} by hand")
            g[key] = v[field]
            g[f"{key}_source"] = f"{field} in {t['environment_json']}"


def blended_usd_per_1m(opt: dict, w: dict) -> float:
    i, o = w["tokens_per_call_in"], w["tokens_per_call_out"]
    return (i * opt["usd_per_1m_input"] + o * opt["usd_per_1m_output"]) / (i + o)


def monthly_costs(cfg: dict, tok_s: float) -> list[dict]:
    w = cfg["workload"]
    rows = []
    cap_per_gpu = tok_s * 3600 * HOURS_PER_MONTH            # tokens one GPU can serve in a month at 100% busy
    for vol in w["monthly_tokens"]:
        vol = float(vol)
        busy_h = vol / tok_s / 3600
        for o in cfg.get("api", []):
            rows.append({"option": o["name"], "kind": "api", "monthly_tokens": vol,
                         "usd_per_month": vol / 1e6 * blended_usd_per_1m(o, w), "gpus": 0})
        for o in cfg.get("cloud_gpu", []):
            n = max(1, math.ceil(vol / cap_per_gpu))
            rows.append({"option": f'{o["name"]} (always on)', "kind": "gpu_always_on", "monthly_tokens": vol,
                         "usd_per_month": n * HOURS_PER_MONTH * o["usd_per_hour"], "gpus": n})
            util = cfg["assumptions"]["busy_only_utilisation"]
            rows.append({"option": f'{o["name"]} (busy hours only)', "kind": "gpu_busy_only", "monthly_tokens": vol,
                         "usd_per_month": busy_h / util * o["usd_per_hour"], "gpus": None})
        if "own_gpu" in cfg:
            g = cfg["own_gpu"]
            n = max(1, math.ceil(vol / cap_per_gpu))
            capex = n * g["hardware_usd"] / g["lifetime_months"]
            idle_h = max(0.0, n * HOURS_PER_MONTH - busy_h)
            kwh = (busy_h * g["busy_watts"] + idle_h * g["idle_watts"]) / 1000
            rows.append({"option": g["name"], "kind": "own_gpu", "monthly_tokens": vol,
                         "usd_per_month": capex + kwh * g["usd_per_kwh"], "gpus": n})
    for r in rows:
        r["usd_per_1m_tokens"] = r["usd_per_month"] / (r["monthly_tokens"] / 1e6)
        r["usd_per_month"] = round(r["usd_per_month"], 2)
        r["usd_per_1m_tokens"] = round(r["usd_per_1m_tokens"], 4)
    return rows


def break_even(cfg: dict, tok_s: float) -> list[dict]:
    """Monthly volume above which one always-on GPU (or the own GPU) is cheaper than each API."""
    w, out = cfg["workload"], []
    cap = tok_s * 3600 * HOURS_PER_MONTH
    fixed = {f'{o["name"]} (always on)': HOURS_PER_MONTH * o["usd_per_hour"] for o in cfg.get("cloud_gpu", [])}
    if "own_gpu" in cfg:
        g = cfg["own_gpu"]
        fixed[g["name"]] = g["hardware_usd"] / g["lifetime_months"] + HOURS_PER_MONTH * g["idle_watts"] / 1000 * g["usd_per_kwh"]
    for gpu, usd in fixed.items():
        for a in cfg.get("api", []):
            v = usd / blended_usd_per_1m(a, w) * 1e6
            out.append({"gpu_option": gpu, "api_option": a["name"], "gpu_fixed_usd_per_month": round(usd, 2),
                        "break_even_tokens_per_month": round(v), "one_gpu_capacity_tokens_per_month": round(cap),
                        "reachable_on_one_gpu": v <= cap})
    return out


def svg(rows: list[dict]) -> str:
    W, H, L, R, T, B = 900, 420, 70, 330, 30, 50
    series = {}
    for r in rows:
        series.setdefault(r["option"], []).append((r["monthly_tokens"], r["usd_per_month"]))
    xs = sorted({r["monthly_tokens"] for r in rows})
    ys = [max(r["usd_per_month"], 0.01) for r in rows]
    lx0, lx1 = math.log10(xs[0]), math.log10(xs[-1])
    ly0, ly1 = math.floor(math.log10(min(ys))), math.ceil(math.log10(max(ys)))
    X = lambda v: L + (math.log10(v) - lx0) / max(lx1 - lx0, 1e-9) * (W - L - R)
    Y = lambda v: T + (1 - (math.log10(max(v, 0.01)) - ly0) / max(ly1 - ly0, 1e-9)) * (H - T - B)
    colors = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf"]
    dash = {"api": "", "gpu_always_on": "6,3", "gpu_busy_only": "2,3", "own_gpu": "10,3,2,3"}
    kind = {r["option"]: r["kind"] for r in rows}
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" font-family="sans-serif" font-size="11">',
         f'<rect width="{W}" height="{H}" fill="white"/>',
         f'<text x="{L}" y="18" font-size="13">Monthly cost vs volume, 7B-class model (log-log)</text>',
         f'<line x1="{L}" y1="{H - B}" x2="{W - R}" y2="{H - B}" stroke="black"/>',
         f'<line x1="{L}" y1="{T}" x2="{L}" y2="{H - B}" stroke="black"/>',
         f'<text x="{(L + W - R) / 2}" y="{H - 12}" text-anchor="middle">tokens per month (input + output)</text>',
         f'<text x="16" y="{(T + H - B) / 2}" text-anchor="middle" transform="rotate(-90 16 {(T + H - B) / 2})">USD per month</text>']
    for v in xs:
        o.append(f'<text x="{X(v):.1f}" y="{H - B + 16}" text-anchor="middle">{v:.0e}</text>')
    for e in range(ly0, ly1 + 1):
        o.append(f'<line x1="{L}" y1="{Y(10 ** e):.1f}" x2="{W - R}" y2="{Y(10 ** e):.1f}" stroke="#ddd"/>')
        o.append(f'<text x="{L - 6}" y="{Y(10 ** e) + 4:.1f}" text-anchor="end">${10 ** e:g}</text>')
    for i, (name, pts) in enumerate(series.items()):
        c, d = colors[i % len(colors)], dash[kind[name]]
        o.append(f'<polyline points="{" ".join(f"{X(x):.1f},{Y(y):.1f}" for x, y in sorted(pts))}" fill="none" '
                 f'stroke="{c}" stroke-width="2" stroke-dasharray="{d}"/>')
        ly = T + 8 + i * 17
        o.append(f'<line x1="{W - R + 10}" y1="{ly}" x2="{W - R + 34}" y2="{ly}" stroke="{c}" stroke-width="2" stroke-dasharray="{d}"/>')
        o.append(f'<text x="{W - R + 40}" y="{ly + 4}">{name}</text>')
    o.append("</svg>")
    return "\n".join(o)


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
        wr.writeheader()
        wr.writerows(rows)


def summary_md(cfg, tp, rows, be) -> str:
    w = cfg["workload"]
    vols = sorted({r["monthly_tokens"] for r in rows})
    opts = list(dict.fromkeys(r["option"] for r in rows))
    get = {(r["option"], r["monthly_tokens"]): r for r in rows}
    head = "| Option | " + " | ".join(f"{v:.0e} tok/month" for v in vols) + " |"
    lines = [f"# Hosting cost model: {cfg['campaign']}", "",
             "Added November 2026, after the original study. Generated by scripts/hosting_cost.py from "
             "configs/hosting_options.yaml; prices, sources and access dates are in that file.", "",
             f"- Throughput: {tp['total_tok_s']} total tokens/s ({tp['row']}, measured, {tp['source']})",
             f"- Workload mix: {w['tokens_per_call_in']} input + {w['tokens_per_call_out']} output tokens per call "
             f"({w['mix_source']})",
             *([f"- Own GPU: ${cfg['own_gpu']['hardware_usd']} over {cfg['own_gpu']['lifetime_months']} months (assumption), "
                f"{cfg['own_gpu']['busy_watts']} W busy / {cfg['own_gpu']['idle_watts']} W idle (GPU board only), "
                f"${cfg['own_gpu']['usd_per_kwh']}/kWh"] if "own_gpu" in cfg else []),
             f"- Busy-hours-only utilisation assumption: {cfg['assumptions']['busy_only_utilisation']:.0%}", "",
             "Monthly cost in USD (USD per 1M tokens in brackets):", "", head,
             "|---|" + "---|" * len(vols)]
    for opt in opts:
        cells = []
        for v in vols:
            r = get[(opt, v)]
            g = f", {r['gpus']} GPU" if r["gpus"] and r["gpus"] > 1 else ""
            cells.append(f"${r['usd_per_month']:,.2f} (${r['usd_per_1m_tokens']:.3f}{g})")
        lines.append(f"| {opt} | " + " | ".join(cells) + " |")
    lines += ["", "Break-even: monthly volume above which a GPU's fixed monthly cost is lower than the API bill.", "",
              "| GPU option | API option | GPU fixed $/month | Break-even tokens/month | One GPU can serve | Reachable on one GPU |",
              "|---|---|---|---|---|---|"]
    for b in be:
        lines.append(f"| {b['gpu_option']} | {b['api_option']} | ${b['gpu_fixed_usd_per_month']:,.2f} | "
                     f"{b['break_even_tokens_per_month']:.2e} | {b['one_gpu_capacity_tokens_per_month']:.2e} | "
                     f"{'yes' if b['reachable_on_one_gpu'] else 'no'} |")
    lines += ["", "![Cost vs volume](cost_vs_volume.svg)", ""]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--config", default=str(ROOT / "configs" / "hosting_options.yaml"))
    ap.add_argument("--results-dir", default=str(ROOT / "results"))
    args = ap.parse_args(argv)
    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    tp = load_throughput(cfg)
    resolve_measured_power(cfg)
    rows = monthly_costs(cfg, tp["total_tok_s"])
    be = break_even(cfg, tp["total_tok_s"])
    out = Path(args.results_dir) / cfg["campaign"]
    out.mkdir(parents=True, exist_ok=True)
    write_csv(out / "costs.csv", rows)
    write_csv(out / "break_even.csv", be)
    (out / "cost_vs_volume.svg").write_text(svg(rows) + "\n", encoding="utf-8")
    md = summary_md(cfg, tp, rows, be)
    (out / "summary.md").write_text(md, encoding="utf-8")
    print(md)


if __name__ == "__main__":
    main()
