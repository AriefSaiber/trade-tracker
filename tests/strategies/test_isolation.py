"""Enforces the hard rule: strategy code must not import from execution,
risk, or portfolio packages (CLAUDE.md §3). CI-level guard."""
from __future__ import annotations

import ast
from pathlib import Path

STRATEGIES_DIR = Path(__file__).resolve().parents[2] / "backend" / "strategies"
FORBIDDEN = ("backend.execution", "backend.risk", "backend.portfolio")


def iter_imports(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module


def test_strategies_do_not_import_execution_risk_or_portfolio():
    violations: list[str] = []
    for py in STRATEGIES_DIR.rglob("*.py"):
        for module in iter_imports(py):
            if any(module.startswith(f) for f in FORBIDDEN):
                violations.append(f"{py.name}: imports {module}")
    assert not violations, f"strategy isolation violated: {violations}"


def test_no_lookahead_shift_in_strategies():
    """No .shift(-1) (future data) anywhere in strategy code."""
    offenders = [
        py.name for py in STRATEGIES_DIR.rglob("*.py")
        if ".shift(-" in py.read_text(encoding="utf-8")
    ]
    assert not offenders, f"look-ahead bias risk: {offenders}"
