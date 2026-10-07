"""Runs ONE test case against ONE candidate program and prints a JSON verdict.

Invoked as a separate process (python -I harness.py ...) by sandbox.py so that
infinite loops, crashes or recursion errors in model-written code cannot take
down the experiment runner. Uses only the standard library on purpose.

Usage: python -I harness.py <candidate.py> <function_name> <cases.json> <case_index>
"""
import importlib.util
import inspect
import json
import sys


def _normalise(value):
    """Make outputs comparable with JSON-decoded expectations.

    Generators -> lists, tuples -> lists (JSON has no tuples), recursively.
    """
    if inspect.isgenerator(value) or isinstance(value, (map, filter, zip)):
        value = list(value)
    if isinstance(value, (list, tuple)):
        return [_normalise(v) for v in value]
    if isinstance(value, dict):
        return {k: _normalise(v) for k, v in value.items()}
    return value


def _short(value, limit=300):
    text = repr(value)
    return text if len(text) <= limit else text[:limit] + "...<truncated>"


def main():
    candidate_path, func_name, cases_path, idx = sys.argv[1:5]
    with open(cases_path, encoding="utf-8") as f:
        case = json.load(f)[int(idx)]
    args, expected, tol = case["input"], case["expected"], case.get("abs_tol")

    try:
        spec = importlib.util.spec_from_file_location("candidate", candidate_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        func = getattr(module, func_name)
    except Exception as exc:  # syntax/import errors are the candidate's fault
        print(json.dumps({"status": "error", "error": f"load: {type(exc).__name__}: {exc}"[:500]}))
        return

    try:
        got = _normalise(func(*args))
    except RecursionError:
        print(json.dumps({"status": "error", "error": "RecursionError: maximum recursion depth exceeded"}))
        return
    except Exception as exc:
        print(json.dumps({"status": "error", "error": f"{type(exc).__name__}: {exc}"[:500]}))
        return

    if tol is not None and isinstance(got, (int, float)) and not isinstance(got, bool):
        ok = abs(got - expected) <= tol
    else:
        ok = got == expected
    print(json.dumps({"status": "pass" if ok else "fail", "got": _short(got)}))


if __name__ == "__main__":
    main()
