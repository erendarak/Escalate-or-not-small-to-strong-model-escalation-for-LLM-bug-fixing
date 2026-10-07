"""Build the task set from a local QuixBugs checkout (MIT licence, J. Koppel).

    git clone https://github.com/jkoppel/QuixBugs.git ../QuixBugs
    python scripts/prepare_tasks.py --quixbugs ../QuixBugs

For each of the 31 Python programs that have JSON test cases:
  * buggy.py        = QuixBugs buggy program (its docstring is the specification)
  * reference.py    = QuixBugs correct program (private)
  * public cases    = up to 3 cases shown to the model, at least one of which the bug fails
  * hidden cases    = all remaining cases, never shown to the model
Then the acceptance gate is applied and a dev/eval split is drawn with a fixed seed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from routing_pilot.sandbox import run_suite  # noqa: E402

# Cases the original QuixBugs suite itself skips because they take minutes.
SLOW = {
    "knapsack": lambda inp: inp[0] == 6404180,
    "levenshtein": lambda inp: inp[0] == "amanaplanacanalpanama",
}
TOLERANCE = {"sqrt": lambda inp: inp[-1]}  # sqrt(x, epsilon): compare within epsilon


def load_cases(qb: Path, name: str) -> list[dict]:
    cases = []
    for line in (qb / "json_testcases" / f"{name}.json").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        inp, exp = json.loads(line)
        if not isinstance(inp, list):  # a few files store a single argument unwrapped
            inp = [inp]
        if name in SLOW and SLOW[name](inp):
            continue
        case = {"input": inp, "expected": exp}
        if name in TOLERANCE:
            case["abs_tol"] = TOLERANCE[name](inp)
        cases.append(case)
    return cases


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quixbugs", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=ROOT / "data")
    ap.add_argument("--n-dev", type=int, default=6)
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--max-public", type=int, default=3)
    args = ap.parse_args()

    qb = args.quixbugs
    names = sorted(p.stem for p in (qb / "json_testcases").glob("*.json"))
    accepted, report = [], []

    for name in names:
        buggy = (qb / "python_programs" / f"{name}.py").read_text(encoding="utf-8")
        ref = (qb / "correct_python_programs" / f"{name}.py").read_text(encoding="utf-8")
        cases = load_cases(qb, name)

        ref_res = run_suite(ref, name, cases)
        bug_res = run_suite(buggy, name, cases)
        failing = [r.index for r in bug_res.results if r.status != "pass"]
        passing = [r.index for r in bug_res.results if r.status == "pass"]

        # Public = first failing case + first passing cases (so the model also sees normal behaviour)
        n_pub = min(args.max_public, max(1, len(cases) // 3))
        pub_idx = (failing[:1] + passing)[:n_pub]
        if len(pub_idx) < n_pub:
            pub_idx += [i for i in failing[1:] if i not in pub_idx][: n_pub - len(pub_idx)]
        pub_idx = sorted(pub_idx)
        hid_idx = [i for i in range(len(cases)) if i not in pub_idx]

        gate = {
            "reference_passes_all": ref_res.passed,
            "buggy_fails_a_public_case": any(i in failing for i in pub_idx),
            "buggy_fails_a_hidden_case": any(i in failing for i in hid_idx),
            "at_least_2_hidden_cases": len(hid_idx) >= 2,
        }
        ok = gate["reference_passes_all"] and gate["buggy_fails_a_public_case"] and gate["at_least_2_hidden_cases"]
        report.append({"task": name, "n_cases": len(cases), "n_public": len(pub_idx),
                       "n_hidden": len(hid_idx), "accepted": ok, **gate})
        status = "OK " if ok else "REJ"
        print(f"{status} {name:28s} cases={len(cases):2d} pub={len(pub_idx)} hid={len(hid_idx)} "
              f"ref_ok={ref_res.passed} bug_fails={len(failing)} hidden_bug_fail={gate['buggy_fails_a_hidden_case']}")
        if ok:
            accepted.append((name, buggy, ref, [cases[i] for i in pub_idx], [cases[i] for i in hid_idx]))

    rng = random.Random(args.seed)
    order = [a[0] for a in accepted]
    rng.shuffle(order)
    dev = set(order[: args.n_dev])

    for sub in ("public", "private"):
        shutil.rmtree(args.out / sub, ignore_errors=True)
    manifest = []
    for name, buggy, ref, pub, hid in accepted:
        split = "dev" if name in dev else "eval"
        pd, vd = args.out / "public" / name, args.out / "private" / name
        pd.mkdir(parents=True); vd.mkdir(parents=True)
        (pd / "buggy.py").write_text(buggy, encoding="utf-8")
        (pd / "public_cases.json").write_text(json.dumps(pub, indent=1), encoding="utf-8")
        (pd / "meta.json").write_text(json.dumps({"task_id": name, "func_name": name, "split": split,
                                                  "source": "QuixBugs"}, indent=1), encoding="utf-8")
        (vd / "reference.py").write_text(ref, encoding="utf-8")
        (vd / "hidden_cases.json").write_text(json.dumps(hid, indent=1), encoding="utf-8")
        manifest.append({"task_id": name, "split": split, "buggy_sha": sha(buggy), "reference_sha": sha(ref),
                         "n_public": len(pub), "n_hidden": len(hid)})

    (args.out / "manifest.json").write_text(json.dumps({"seed": args.seed, "tasks": manifest}, indent=1), encoding="utf-8")
    (args.out / "gate_report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    n_dev = sum(m["split"] == "dev" for m in manifest)
    print(f"\nAccepted {len(accepted)}/{len(names)} tasks -> dev={n_dev}, eval={len(manifest) - n_dev}")


if __name__ == "__main__":
    main()
