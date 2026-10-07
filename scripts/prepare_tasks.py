"""Build the task set from a local QuixBugs checkout (MIT licence, J. Koppel).

    git clone https://github.com/jkoppel/QuixBugs.git ../QuixBugs
    python scripts/prepare_tasks.py --quixbugs ../QuixBugs

For each of the 31 Python programs that have JSON test cases:
  * buggy.py        = QuixBugs buggy program (its docstring is the specification)
  * reference.py    = QuixBugs correct program (private)
  * public cases    = up to 3 cases shown to the model, at least one of which the bug fails
  * hidden cases    = all remaining cases, never shown to the model
Then the acceptance gate is applied and a dev/eval split is drawn with a fixed seed,
stratified by bug_family (one dev task per family, the rest eval).

Bug labels: bug_type_ye is the per-program label from Ye et al., "A Comprehensive
Study of Automatic Program Repair on the QuixBugs Benchmark", JSS 2021,
arXiv:1805.03454, Table 1 (labelled on the Java versions). 22 are used as-is;
9 are adapted to the Python buggy->correct diff (bug_type differs, bug_type_note
says why). bug_family groups the types into 5 coarse families for analysis.
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

# Ye et al. bug type -> coarse family (fixed before any eval run).
FAMILY_OF_YE_TYPE = {
    "Missing '+1'": "boundary", "Missing '-1'": "boundary", "Incorrect array slice": "boundary",
    "Incorrect comparison operator": "operator", "Incorrect logical operator": "operator",
    "Incorrect arithmetic expression": "operator", "Incorrect method called": "operator",
    "Reference to an incorrect variable": "variable", "Expression swap": "variable",
    "Missing boolean expression": "condition", "Missing logic": "condition", "Other code replacement": "condition",
    "Missing function call": "missing_code", "Missing lines with a function call": "missing_code",
    "Wrong constructor call": "missing_code",
}

# Per-program label from Ye et al. Table 1.
YE_TYPE = {
    "bitcount": "Incorrect logical operator", "bucketsort": "Reference to an incorrect variable",
    "find_first_in_sorted": "Incorrect comparison operator", "find_in_sorted": "Missing '+1'",
    "flatten": "Missing function call", "gcd": "Expression swap", "get_factors": "Wrong constructor call",
    "hanoi": "Reference to an incorrect variable", "is_valid_parenthesization": "Other code replacement",
    "kheapsort": "Missing function call", "knapsack": "Incorrect comparison operator",
    "kth": "Reference to an incorrect variable", "lcs_length": "Incorrect array slice",
    "levenshtein": "Missing '+1'", "lis": "Missing logic", "longest_common_subsequence": "Missing function call",
    "max_sublist_sum": "Missing function call", "mergesort": "Incorrect arithmetic expression",
    "next_palindrome": "Missing '-1'", "next_permutation": "Incorrect comparison operator",
    "pascal": "Missing '+1'", "possible_change": "Missing boolean expression", "powerset": "Missing logic",
    "quicksort": "Incorrect comparison operator", "rpn_eval": "Expression swap",
    "shunting_yard": "Missing lines with a function call", "sieve": "Incorrect method called",
    "sqrt": "Incorrect arithmetic expression", "subsequences": "Missing lines with a function call",
    "to_base": "Expression swap", "wrap": "Missing lines with a function call",
}

# Rows where the Java-based label does not fit the Python diff: (bug_type, bug_family, note).
PYTHON_ADAPTED = {
    "lcs_length": ("Incorrect array index", "boundary",
                   "Python fix dp[i-1, j] -> dp[i-1, j-1]; Ye cell also shows a wrapped 'Missing boolean expression'"),
    "flatten": ("Extra function call", "operator", "Python fix removes the call: yield flatten(x) -> yield x"),
    "levenshtein": ("Added '+1'", "boundary", "Python fix removes '1 +'"),
    "kheapsort": ("Missing array slice", "boundary", "Java fix is subList(); Python fix is arr -> arr[k:]"),
    "longest_common_subsequence": ("Missing array slice", "boundary",
                                   "Java fix is substring(); Python fix is b -> b[1:]"),
    "subsequences": ("Incorrect data structure constant", "missing_code", "Python fix return [] -> return [[]]"),
    "get_factors": ("Incorrect data structure constant", "missing_code", "Python fix return [] -> return [n]"),
    "kth": ("Incorrect argument value", "variable", "Python fix passes k - num_lessoreq instead of k"),
    "mergesort": ("Incorrect boundary condition", "boundary", "Python fix len(arr) == 0 -> len(arr) <= 1"),
}


def labels(name: str) -> dict:
    ye = YE_TYPE[name]
    if name in PYTHON_ADAPTED:
        bug_type, family, note = PYTHON_ADAPTED[name]
        return {"bug_type_ye": ye, "bug_type": bug_type, "bug_family": family, "bug_type_note": note}
    return {"bug_type_ye": ye, "bug_type": ye, "bug_family": FAMILY_OF_YE_TYPE[ye]}


def stratified_dev(names: list[str], seed: int, per_family: int) -> set[str]:
    """Pick `per_family` dev tasks from every bug_family with a fixed seed; the rest are eval."""
    rng = random.Random(seed)
    by_family = {}
    for n in sorted(names):
        by_family.setdefault(labels(n)["bug_family"], []).append(n)
    dev = set()
    for family in sorted(by_family):
        dev.update(rng.sample(by_family[family], per_family))
    return dev


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
    ap.add_argument("--dev-per-family", type=int, default=1)
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

    dev = stratified_dev([a[0] for a in accepted], args.seed, args.dev_per_family)

    for sub in ("public", "private"):
        shutil.rmtree(args.out / sub, ignore_errors=True)
    manifest = []
    for name, buggy, ref, pub, hid in accepted:
        split = "dev" if name in dev else "eval"
        pd, vd = args.out / "public" / name, args.out / "private" / name
        pd.mkdir(parents=True); vd.mkdir(parents=True)
        (pd / "buggy.py").write_text(buggy, encoding="utf-8")
        (pd / "public_cases.json").write_text(json.dumps(pub, indent=1), encoding="utf-8")
        # Labels live in meta.json for analysis only; prompts never read them (tests check this).
        (pd / "meta.json").write_text(json.dumps({"task_id": name, "func_name": name, "split": split,
                                                  "source": "QuixBugs", **labels(name)}, indent=1), encoding="utf-8")
        (vd / "reference.py").write_text(ref, encoding="utf-8")
        (vd / "hidden_cases.json").write_text(json.dumps(hid, indent=1), encoding="utf-8")
        manifest.append({"task_id": name, "split": split, "bug_family": labels(name)["bug_family"],
                         "buggy_sha": sha(buggy), "reference_sha": sha(ref),
                         "n_public": len(pub), "n_hidden": len(hid)})

    (args.out / "manifest.json").write_text(json.dumps(
        {"seed": args.seed, "split_method": f"stratified by bug_family, {args.dev_per_family} dev task(s) per family",
         "label_source": "Ye et al., JSS 2021, arXiv:1805.03454, Table 1 (9 rows adapted to Python, see bug_type_note)",
         "tasks": manifest}, indent=1), encoding="utf-8")
    (args.out / "gate_report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    n_dev = sum(m["split"] == "dev" for m in manifest)
    print(f"\nAccepted {len(accepted)}/{len(names)} tasks -> dev={n_dev}, eval={len(manifest) - n_dev}")


if __name__ == "__main__":
    main()
