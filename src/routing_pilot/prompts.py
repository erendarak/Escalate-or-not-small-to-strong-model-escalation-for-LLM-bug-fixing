"""Prompt construction. Identical for every model and policy (no model-specific hints).

Only PublicTask data and PUBLIC test feedback can enter a prompt. There is no
code path from PrivateTask into this module; tests/test_infra.py checks it.
"""
from __future__ import annotations

import json

from .sandbox import SuiteResult
from .tasks import PublicTask

PROMPT_VERSION = "repair_v1"

SYSTEM = (
    "You are an expert Python developer fixing a bug in a small program. "
    "The docstring describes the intended behaviour. The bug is small (usually one line). "
    "Keep the function name and signature. Do not add imports. Do not print anything. "
    "Reply with the COMPLETE corrected file in a single ```python code block and nothing else."
)


def _fmt_case(c: dict) -> str:
    return f"{c['func']}(*{json.dumps(c['input'])}) should return {json.dumps(c['expected'])}"


def format_feedback(task: PublicTask, result: SuiteResult, max_items: int = 5) -> str:
    """Normalised, bounded feedback from PUBLIC tests only."""
    lines = []
    for r in result.results:
        if r.status == "pass":
            continue
        call = f"{task.func_name}(*{json.dumps(r.input)})"
        if r.status == "fail":
            lines.append(f"- {call}: expected {json.dumps(r.expected)}, got {r.got}")
        elif r.status == "timeout":
            lines.append(f"- {call}: TIMEOUT (possible infinite loop)")
        else:
            lines.append(f"- {call}: raised {r.error}")
        if len(lines) >= max_items:
            break
    return f"{result.n_pass}/{result.n} visible tests passed. Failures:\n" + "\n".join(lines)


def build_messages(task: PublicTask, previous_code: str | None = None, feedback: str | None = None,
                   show_previous_code: bool = True) -> list[dict]:
    """show_previous_code=False (exploratory escalate_clean hand-off): feedback only, no previous code."""
    examples = "\n".join(
        _fmt_case({"func": task.func_name, **c}) for c in task.public_cases)
    user = (f"The function `{task.func_name}` below has a bug.\n\n"
            f"```python\n{task.buggy_code.rstrip()}\n```\n\n"
            f"Visible tests (there are more hidden tests, so fix the real bug, do not special-case inputs):\n"
            f"{examples}\n")
    if feedback is not None:
        if previous_code and show_previous_code:
            user += (f"\nA previous attempt produced this code, which is still wrong:\n"
                     f"```python\n{previous_code.rstrip()}\n```\n")
        elif previous_code:
            user += "\nA previous attempt is still wrong.\n"
        else:
            user += "\nA previous attempt did not produce usable code.\n"
        user += (f"Result of the previous attempt:\n{feedback}\n"
                 f"\nWrite a new corrected version of the ORIGINAL program.\n")
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]
