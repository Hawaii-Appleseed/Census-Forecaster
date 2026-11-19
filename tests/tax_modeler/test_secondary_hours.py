"""Married couples get the SPM childcare-expense subtraction.

`compute_childcare_expense_for_units` gives a joint filer $0 unless the frame says the second
filer works (`secondary_hours_worked`); the poverty pipeline's frame never carried the column, so
every married-couple unit had $0 of childcare expense (and one earner in the work-expense match).
`attach_secondary_hours` supplies it from the spouse's PUMS WKHP, and `_load_units` calls it.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pandas as pd
import pytest
from tax_modeler.benefits.childcare_expense import compute_childcare_expense_for_units
from tax_modeler.pipeline import attach_secondary_hours

REPO = Path(__file__).resolve().parents[2]


def _persons():
    return pd.DataFrame({"SERIALNO": ["A", "A", "B", "B", "C"], "SPORDER": [1, 2, 1, 2, 1],
                         "WKHP": [40.0, 35.0, 40.0, None, 20.0]})


def _units():
    return pd.DataFrame({
        "filing_status": ["married_filing_jointly", "married_filing_jointly", "head_of_household"],
        "primary_filer_id": ["A_1", "B_1", "C_1"], "secondary_filer_id": ["A_2", "B_2", "0"],
        "total_cash_income": [60_000.0] * 3, "earned_income": [60_000.0] * 3,
        "num_qualifying_children": [2, 2, 2]})


def test_attach_reads_the_spouses_hours():
    out = attach_secondary_hours(_units(), _persons())
    assert out["secondary_hours_worked"].tolist() == [35.0, 0.0, 0.0]


def test_attach_is_idempotent_and_does_not_mutate():
    u = _units()
    once = attach_secondary_hours(u, _persons())
    assert "secondary_hours_worked" not in u.columns
    assert attach_secondary_hours(once, _persons())["secondary_hours_worked"].tolist() == [35.0, 0.0, 0.0]


def test_a_two_earner_couple_gets_childcare_expense_only_with_the_column():
    """Fails without the fix: the frame had no secondary hours, so the couple's expense was $0."""
    without = compute_childcare_expense_for_units(_units())["childcare_expense_amount"].to_numpy()
    assert without[0] == 0.0 and without[1] == 0.0                       # the bug: couples always $0
    with_hours = compute_childcare_expense_for_units(
        attach_secondary_hours(_units(), _persons()))["childcare_expense_amount"].to_numpy()
    assert with_hours[0] > 0.0                                           # both spouses work
    assert with_hours[1] == 0.0                                          # one earner: the SPM rule gives none
    assert with_hours[2] == without[2] > 0.0                             # single parents are unaffected


def test_the_poverty_pipeline_loads_units_through_the_helper():
    tree = ast.parse((REPO / "scripts" / "poverty_impact_report.py").read_text())
    load = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_load_units")
    called = {n.func.id for n in ast.walk(load) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "attach_secondary_hours" in called


def test_attach_refuses_persons_without_hours():
    with pytest.raises(KeyError, match="WKHP"):
        attach_secondary_hours(_units(), _persons().drop(columns="WKHP"))
