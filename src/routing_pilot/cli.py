"""Command line entry point.

    python -m routing_pilot.cli check  --config configs/dev_local.yaml   # are the models reachable?
    python -m routing_pilot.cli run    --config configs/dev_fake.yaml    # run (resumes automatically)
    python -m routing_pilot.cli analyse --campaign dev_fake               # rebuild summary.md from logs
"""
from __future__ import annotations

import argparse
import sys

from .analysis import summarise
from .config import ROOT, load_config
from .experiment import run_campaign
from .models import make_client


def main():
    sys.stdout.reconfigure(encoding="utf-8")   # Windows consoles default to cp125x; summary has Δ
    ap = argparse.ArgumentParser(prog="routing_pilot")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "check"):
        p = sub.add_parser(name)
        p.add_argument("--config", required=True)
    a = sub.add_parser("analyse")
    a.add_argument("--campaign", required=True)
    a.add_argument("--reference", default="strong_only")
    args = ap.parse_args()

    if args.cmd == "run":
        cfg = load_config(args.config)
        out = run_campaign(cfg)
        print()
        print(summarise(out))
    elif args.cmd == "check":
        cfg = load_config(args.config)
        for key, spec in cfg.models.items():
            if spec.kind == "fake":
                print(f"{key}: fake ({spec.model}) OK")
                continue
            try:
                r = make_client(spec).generate([{"role": "user", "content": "Reply with the word OK."}])
                print(f"{key}: {spec.model} -> {r.text.strip()[:40]!r} in {r.latency_s:.1f}s, "
                      f"tokens in/out {r.input_tokens}/{r.output_tokens}")
            except Exception as exc:
                print(f"{key}: {spec.model} FAILED -> {exc}")
    elif args.cmd == "analyse":
        print(summarise(ROOT / "results" / args.campaign, args.reference))


if __name__ == "__main__":
    main()
