"""Turn a model response into a candidate program, or a clear rejection reason.

Design choice: models return the FULL
corrected file in one ```python block instead of a unified diff. Small local
models produce malformed diffs very often, which would measure diff-formatting
skill rather than repair skill. Every model gets the same format, so the
comparison stays fair.
"""
from __future__ import annotations

import ast
import difflib
import re
from dataclasses import dataclass

CODE_BLOCK = re.compile(r"```(?:python|py)?[ \t]*\n(.*?)```", re.DOTALL | re.IGNORECASE)

# Code is executed on your own machine, so refuse anything that touches the OS.
FORBIDDEN_MODULES = {"os", "sys", "subprocess", "shutil", "socket", "pathlib", "importlib",
                     "ctypes", "multiprocessing", "threading", "requests", "urllib", "http", "pickle"}
FORBIDDEN_CALLS = {"open", "exec", "eval", "compile", "__import__", "input", "exit", "quit"}


@dataclass
class Candidate:
    valid: bool
    code: str | None
    reason: str | None = None   # why invalid
    lines_changed: int = 0      # code only: docstrings, comments and formatting ignored
    lines_changed_raw: int = 0  # plain text diff, kept for comparison


def extract_code(text: str) -> str | None:
    blocks = CODE_BLOCK.findall(text or "")
    if not blocks:
        return None
    return max(blocks, key=len).strip() + "\n"   # longest block = the file, not a snippet


def check_safety(tree: ast.AST) -> str | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] in FORBIDDEN_MODULES:
                    return f"forbidden import: {a.name}"
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] in FORBIDDEN_MODULES:
                return f"forbidden import: {node.module}"
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_CALLS:
            return f"forbidden call: {node.func.id}()"
    return None


def _diff_count(a: str, b: str) -> int:
    diff = difflib.unified_diff(a.splitlines(), b.splitlines(), lineterm="", n=0)
    return sum(1 for l in diff if l[:1] in "+-" and not l.startswith(("+++", "---")))


def _is_bare_string(stmt: ast.stmt) -> bool:
    return isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str)


def code_only(tree: ast.AST) -> str:
    """Normalised source: ast.unparse drops comments/formatting; bare string statements
    (docstrings, and the description block QuixBugs places after each function) are removed.

    Models often drop or rewrite that text while fixing one line, which would
    otherwise make a one-line fix look like a 20-line patch.
    """
    for node in ast.walk(tree):
        for name in ("body", "orelse", "finalbody"):
            stmts = getattr(node, name, None)
            if isinstance(stmts, list) and stmts and isinstance(stmts[0], ast.stmt):
                kept = [st for st in stmts if not _is_bare_string(st)]
                setattr(node, name, kept or ([ast.Pass()] if not isinstance(node, ast.Module) else []))
    return ast.unparse(tree)


def code_lines_changed(original_code: str, code: str) -> int:
    return _diff_count(code_only(ast.parse(original_code)), code_only(ast.parse(code)))


def build_candidate(response_text: str, func_name: str, original_code: str) -> Candidate:
    code = extract_code(response_text)
    if code is None:
        return Candidate(False, None, "no ```python code block in response")
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return Candidate(False, code, f"SyntaxError: {exc.msg} (line {exc.lineno})")
    if not any(isinstance(n, ast.FunctionDef) and n.name == func_name for n in tree.body):
        return Candidate(False, code, f"top-level function `{func_name}` not found")
    unsafe = check_safety(tree)
    if unsafe:
        return Candidate(False, code, unsafe)
    return Candidate(True, code, None, code_lines_changed(original_code, code), _diff_count(original_code, code))
