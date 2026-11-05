"""The working-family step's per-replicate IRS rake (replicate ratios and weight columns)."""
from __future__ import annotations

import ast
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from tax_modeler.calibration import irs_household_rake as rake

SCRIPT = Path(__file__).resolve().parents[2] / "chain/forecast_working_family_credits.py"
BANDS = [(1, 10_000), (10_000, 25_000), (25_000, 50_000)]


@pytest.fixture(scope="module")
def fns():
    tree = ast.parse(SCRIPT.read_text())
    wanted = [n for n in tree.body if isinstance(n, ast.FunctionDef)
              and n.name in ("_replicate_ratios", "_apply_rake_to_replicates")]
    ns: dict = {"np": np, "pd": pd, "IrsRake": object}
    exec(compile(ast.Module(body=wanted, type_ignores=[]), str(SCRIPT), "exec"), ns)
    return ns


def _setup():
    rng = np.random.default_rng(1)
    rows, hh = [], []
    for h in range(200):
        w = float(rng.integers(30, 150))
        hh.append({"SERIALNO": f"H{h}", "WGTP": w, **{f"WGTP{i}": w * rng.uniform(0.6, 1.4) for i in range(1, 81)}})
        for _ in range(rng.integers(1, 4)):
            rows.append({"hh_id": f"H{h}", "federal_agi": float(rng.choice([-5, 5e3, 15e3, 40e3, 90e3])),
                         "weight": w * rng.uniform(0.8, 1.2)})
    return pd.DataFrame(rows), pd.DataFrame(hh)


def test_replicate_rake_fixes_class_counts_and_without_it_ratios_are_unchanged(fns):
    u, households = _setup()
    base = fns["_replicate_ratios"](u, households)
    targets = {b: 0.0 for b in BANDS}
    a = u["federal_agi"].to_numpy()
    for b in BANDS:
        targets[b] = 1.1 * u["weight"][(a >= b[0]) & (a < b[1])].sum()
    g, _ = rake.household_factors(u, u["weight"], targets=targets)
    reps = pd.DataFrame({f"r{r + 1:02d}": rake.household_factors(u, u["weight"] * base[r], targets=targets)[0]
                         for r in range(80)})
    raked = rake.apply_household_factors(u, g)
    plain = fns["_replicate_ratios"](raked, households)
    np.testing.assert_allclose(plain, base)                       # no rake_replicates: ratios as before
    ratio = fns["_replicate_ratios"](raked, households, reps)
    w = raked["weight"].to_numpy()
    for r in range(80):
        got = [(w * ratio[r])[(a >= lo) & (a < hi)].sum() for lo, hi in BANDS]
        np.testing.assert_allclose(got, list(targets.values()), rtol=1e-7)


def test_weight_columns_carry_each_replicates_own_factor(fns):
    u, _ = _setup()
    g = pd.Series(1.0 + 0.1 * np.arange(200) / 200, index=pd.Index([f"H{h}" for h in range(200)], name="hh_id"))
    reps = pd.DataFrame({f"weight_r{i:02d}": g.to_numpy() * (1 + 0.01 * i) for i in range(1, 81)}, index=g.index)
    u = rake.apply_household_factors(u, g)
    for i in range(1, 81):
        u[f"weight_r{i:02d}"] = u["weight"]

    class R:
        unit_replicates = reps
    out = fns["_apply_rake_to_replicates"](u, R())
    np.testing.assert_allclose(out["weight_r05"], u["weight"] * 1.05)
    assert fns["_apply_rake_to_replicates"](u, None) is u
