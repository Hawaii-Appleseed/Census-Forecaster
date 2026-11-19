"""The working-family credits page's household tables
(forecast_working_family_credits.distribution / by_family_type).

Regression for the September 29, 2026 pipeline audit: each household's
summed loss was multiplied by the filer weight of its first tax unit, so the
table totals were not the revenue table's (sum over units of weight x loss)
wherever a household's units carry different filer weights.
"""
from __future__ import annotations

import ast
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "chain/forecast_working_family_credits.py"


@pytest.fixture(scope="module")
def page():
    """The script's table functions, read without importing it (importing
    turns warnings off for the whole test process)."""
    tree = ast.parse(SCRIPT.read_text())
    wanted = [n for n in tree.body if isinstance(n, ast.FunctionDef)
              and n.name in ("_household_frame", "distribution", "by_family_type", "by_credit", "_summarize")]
    ns: dict = {"np": np, "pd": pd}
    exec(compile(ast.Module(body=wanted, type_ignores=[]), str(SCRIPT), "exec"), ns)
    return ns


def _units() -> pd.DataFrame:
    rng = np.random.default_rng(5)
    rows = []
    for h in range(120):
        hw = float(rng.integers(5, 40))
        for k in range(1 + h % 3):
            rows.append({"hh_id": f"H{h:03d}", "hh_weight": hw,
                         "weight": 2 + 0.5 * h if k == 0 else float(rng.uniform(1, 60)),
                         "total_cash_income": 9_000 * 1.03 ** h,
                         "act163_sunset_loss": float(rng.choice([0.0, rng.uniform(10, 900)])),
                         "_claim_prob": float(rng.uniform(0, 1)),
                         "num_dependents": int(rng.integers(0, 3)),
                         "filing_status": ["single", "married_filing_jointly", "head_of_household"][(h + k) % 3]})
    return pd.DataFrame(rows)


@pytest.mark.parametrize("table", ["distribution", "by_family_type"])
def test_totals_are_the_revenue_tables(page, table):
    u = _units()
    out = page[table](u)
    want = (u["act163_sunset_loss"] * u["weight"]).sum() / 1e6
    assert out["total_loss_$M"].sum() == pytest.approx(want, rel=1e-12)
    np.testing.assert_allclose(out["avg_loss_per_household"] * out["households"],
                               out["total_loss_$M"] * 1e6, rtol=1e-12)


def test_by_credit_rows_add_up_to_the_total(page):
    """Each credit's household table uses the same household rule and weights; the three losses sum to the total's."""
    u = _units()
    rng = np.random.default_rng(9)
    n = len(u)
    cuts = rng.dirichlet(np.ones(3), size=n)
    for i, k in enumerate(("eitc", "food", "cdcc")):
        u[f"_loss_{k}"] = u["act163_sunset_loss"] * cuts[:, i]
        u[f"_q_{k}"] = np.where(u[f"_loss_{k}"] > 0, rng.uniform(0, 1, n), 0.0)
    out = page["by_credit"](u).set_index("credit")
    parts = out.loc[["Earned income tax credit", "Food/excise credit", "Child and dependent care credit"], "total_loss_$M"]
    assert parts.sum() == pytest.approx(out.loc["All three", "total_loss_$M"], rel=1e-12)
    assert out.loc["All three", "total_loss_$M"] == pytest.approx((u["act163_sunset_loss"] * u["weight"]).sum() / 1e6, rel=1e-12)
