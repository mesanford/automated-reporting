"""Safe formula evaluator for CustomKpi.

User formulas are restricted to:
- Identifiers from `ALLOWED_VARIABLES` (the scorecard keys we ship).
- Numeric literals.
- Binary operators `+`, `-`, `*`, `/`.
- Unary minus.
- Parentheses.

Anything else (attribute access, function calls, subscripts, names not
in the allowlist) raises `FormulaError`. We parse with `ast.parse(mode='eval')`
and walk the tree manually rather than passing the source to `eval`.
This is the same defense pattern used for spreadsheet formula languages
and is robust against the usual `__class__.__bases__` exploit chain.
"""
from __future__ import annotations

import ast
from typing import Dict, Tuple


ALLOWED_VARIABLES = {
    "totalSpend", "totalImpressions", "totalClicks", "totalConversions",
    "totalRevenue",
    "blendedCPA", "blendedCTR", "blendedCVR", "blendedCPC", "blendedCPM",
    "blendedROAS",
}


class FormulaError(ValueError):
    """Anything wrong with a user formula."""


_BINOPS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: (a / b) if b else 0.0,  # divide-by-zero → 0, sensible for analytics
}


def _walk(node: ast.AST, variables: Dict[str, float]) -> float:
    if isinstance(node, ast.Expression):
        return _walk(node.body, variables)

    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return float(node.value)
        raise FormulaError(f"Unsupported literal: {node.value!r}")

    if isinstance(node, ast.Name):
        if node.id not in ALLOWED_VARIABLES:
            raise FormulaError(f"Unknown variable: {node.id}")
        v = variables.get(node.id)
        if v is None:
            return 0.0
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    if isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type not in _BINOPS:
            raise FormulaError(f"Unsupported operator: {op_type.__name__}")
        return _BINOPS[op_type](_walk(node.left, variables), _walk(node.right, variables))

    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_walk(node.operand, variables)

    raise FormulaError(f"Disallowed syntax: {type(node).__name__}")


def validate(formula: str) -> None:
    """Raise FormulaError if anything's wrong; otherwise return None."""
    if not (formula or "").strip():
        raise FormulaError("Formula cannot be empty.")
    try:
        tree = ast.parse(formula, mode="eval")
    except SyntaxError as exc:
        raise FormulaError(f"Parse error: {exc.msg}")
    # Walk once with all-zero inputs to surface bad identifiers / ops.
    _walk(tree, {k: 0.0 for k in ALLOWED_VARIABLES})


def evaluate(formula: str, scorecards: Dict[str, float]) -> Tuple[float, str | None]:
    """Run `formula` against `scorecards`. Returns (value, error_message).

    On any FormulaError, returns (0.0, msg). Callers can decide whether
    to surface — the dashboard displays the error inline so users
    can correct their formula without leaving the page.
    """
    try:
        tree = ast.parse(formula, mode="eval")
        return _walk(tree, scorecards), None
    except FormulaError as exc:
        return 0.0, str(exc)
    except Exception as exc:  # noqa: BLE001
        return 0.0, f"Evaluation error: {exc}"
