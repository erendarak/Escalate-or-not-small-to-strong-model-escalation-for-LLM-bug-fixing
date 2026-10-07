"""Executes test cases against a candidate program, one subprocess per case.

This is process isolation, not a security sandbox. candidate.py additionally
rejects code that imports os/subprocess/etc. before it ever gets here.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

HARNESS = Path(__file__).with_name("harness.py")


@dataclass
class CaseResult:
    index: int
    status: str  # pass | fail | error | timeout | infra_error
    input: list
    expected: object
    got: str | None = None
    error: str | None = None


@dataclass
class SuiteResult:
    results: list[CaseResult] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def n(self) -> int:
        return len(self.results)

    @property
    def n_pass(self) -> int:
        return sum(r.status == "pass" for r in self.results)

    @property
    def passed(self) -> bool:
        # An empty suite is NEVER a pass (guards against "0 tests collected").
        return self.n > 0 and self.n_pass == self.n

    @property
    def infra_error(self) -> bool:
        return any(r.status == "infra_error" for r in self.results)

    @property
    def any_timeout(self) -> bool:
        return any(r.status == "timeout" for r in self.results)


def run_suite(code: str, func_name: str, cases: list[dict], timeout_s: float = 3.0) -> SuiteResult:
    start = time.perf_counter()
    suite = SuiteResult()
    with tempfile.TemporaryDirectory(prefix="rp_") as tmp:
        tmp = Path(tmp)
        cand = tmp / "candidate.py"
        cand.write_text(code, encoding="utf-8")
        cases_file = tmp / "cases.json"
        cases_file.write_text(json.dumps(cases), encoding="utf-8")
        for i, case in enumerate(cases):
            base = dict(index=i, input=case["input"], expected=case["expected"])
            try:
                proc = subprocess.run(
                    [sys.executable, "-I", str(HARNESS), str(cand), func_name, str(cases_file), str(i)],
                    cwd=tmp, capture_output=True, text=True, timeout=timeout_s,
                )
            except subprocess.TimeoutExpired:
                suite.results.append(CaseResult(status="timeout", error=f"timed out after {timeout_s}s", **base))
                continue
            except OSError as exc:  # could not even start python -> our problem, not the model's
                suite.results.append(CaseResult(status="infra_error", error=str(exc), **base))
                continue
            lines = [l for l in proc.stdout.strip().splitlines() if l.startswith("{")]
            if not lines:
                # Candidate printed nothing parseable or crashed the interpreter (e.g. sys.exit in code).
                err = (proc.stderr or proc.stdout or "no output").strip()[-500:]
                suite.results.append(CaseResult(status="error", error=err, **base))
                continue
            verdict = json.loads(lines[-1])
            suite.results.append(CaseResult(status=verdict["status"], got=verdict.get("got"),
                                            error=verdict.get("error"), **base))
    suite.seconds = time.perf_counter() - start
    return suite
