"""Every chain step that catches an exception at its entry point must still exit non-zero.

scripts/rerun_models.py and scripts/publish.py judge a step by its exit code alone. Until
2026-11-13 chain/forecast_sb3125_vs_fy26base.py caught ``Exception``, printed the traceback
and exited 0, so a failed run was reported "ok" and its previous outputs stayed in runs/.
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
_spec = importlib.util.spec_from_file_location("rerun_models", REPO / "scripts" / "rerun_models.py")
rerun_models = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rerun_models)

STEPS = sorted({cmd[0] for stage in rerun_models.STAGES for cmd in stage})


def _main_block(tree: ast.Module) -> ast.If | None:
    for node in tree.body:
        if (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
                and getattr(node.test.left, "id", None) == "__name__"):
            return node
    return None


def _exits(handler: ast.ExceptHandler) -> bool:
    for node in ast.walk(handler):
        if isinstance(node, ast.Raise):
            return True
        if isinstance(node, ast.Call):
            name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
            if name in ("exit", "SystemExit", "_exit") and node.args:
                return True
    return False


def _handlers(tree: ast.Module) -> list[ast.ExceptHandler]:
    """The except clauses that guard the entry point: in the ``__main__`` block, and in a
    top-level function that block calls (``main``, ``run``)."""
    block = _main_block(tree)
    if block is None:
        return []
    called = {n.func.id for n in ast.walk(block)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    roots = [block, *(f for f in tree.body if isinstance(f, ast.FunctionDef) and f.name in called)]
    found = []
    for root in roots:
        for node in ast.walk(root):
            if isinstance(node, ast.Try):
                found += [h for h in node.handlers
                          if h.type is None or getattr(h.type, "id", None) in ("Exception",
                                                                                "BaseException")]
    return found


@pytest.mark.parametrize("step", STEPS)
def test_a_caught_failure_still_exits_non_zero(step):
    tree = ast.parse((REPO / step).read_text())
    swallowed = [h.lineno for h in _handlers(tree)
                 if not _exits(h) and "a diagnostic must not stop the chain"
                 not in ast.get_source_segment((REPO / step).read_text(), h)]
    assert not swallowed, f"{step} catches Exception without exiting non-zero at lines {swallowed}"
