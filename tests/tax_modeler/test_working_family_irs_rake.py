"""The working-family step's replicate weights: each unit's own ratio, and the per-replicate IRS rake."""
from __future__ import annotations

import ast
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from tax_modeler.calibration import irs_household_rake as rake

SCRIPT = Path(__file__).resolve().parents[2] / "chain/forecast_working_family_credits.py"
BANDS = [(1, 10_000), (10_000, 25_000), (25_000, 50_000)]
REPS = [f"weight_r{i:02d}" for i in range(1, 81)]


@pytest.fixture(scope="module")
def fns():
    tree = ast.parse(SCRIPT.read_text())
    wanted = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in (
        "unit_replicate_ratios", "_ratios_for", "_replicate_ratios", "_household_ratios", "_with_replicates")]
    ns: dict = {"np": np, "pd": pd, "IrsRake": object}
    exec(compile(ast.Module(body=wanted, type_ignores=[]), str(SCRIPT), "exec"), ns)
    return ns


def _setup():
    """Households of one to three units. A one-person unit is weighted by its person
    weight and carries person replicates that move unlike its household's; the
    others carry the household's."""
    rng = np.random.default_rng(1)
    rows, hh = [], []
    for h in range(200):
        w = float(rng.integers(30, 150))
        hrep = w * rng.uniform(0.6, 1.4, 80)
        hh.append({"SERIALNO": f"H{h}", "WGTP": w, **{f"WGTP{i + 1}": hrep[i] for i in range(80)}})
        for _ in range(rng.integers(1, 4)):
            one_person = rng.random() < 0.4
            pw = w * rng.uniform(0.8, 1.2) if one_person else w
            rep = pw * rng.uniform(0.9, 1.1, 80) if one_person else hrep
            rows.append({"hh_id": f"H{h}", "federal_agi": float(rng.choice([-5, 5e3, 15e3, 40e3, 90e3])),
                         "weight": pw, "hh_weight": w, "one_person": one_person,
                         **dict(zip(REPS, rep, strict=True))})
    return pd.DataFrame(rows), pd.DataFrame(hh)


def test_each_unit_keeps_its_own_ratio_through_later_weight_changes(fns):
    u, households = _setup()
    ratios = fns["unit_replicate_ratios"](u)
    aged = u.copy()
    aged["weight"] = aged["weight"] * np.linspace(0.5, 2.0, len(u))   # ageing rewrites weight, not weight_rNN
    got = fns["_replicate_ratios"](aged, ratios)
    np.testing.assert_allclose(got.T, u[REPS].to_numpy() / u["weight"].to_numpy()[:, None])
    one = u["one_person"].to_numpy()
    hh = fns["_household_ratios"](u, households)
    assert not np.allclose(got[:, one], hh[:, one])                   # one-person units: not the household's
    np.testing.assert_allclose(got[:, ~one], hh[:, ~one])             # the rest: the household's


def test_missing_units_are_an_error(fns):
    u, _ = _setup()
    ratios = fns["unit_replicate_ratios"](u.iloc[:-1])
    with pytest.raises(SystemExit):
        fns["_replicate_ratios"](u, ratios)


def test_replicate_rake_fixes_class_counts_in_every_replicate(fns):
    u, _ = _setup()
    ratios = fns["unit_replicate_ratios"](u)
    base = fns["_replicate_ratios"](u, ratios)
    a = u["federal_agi"].to_numpy()
    targets = {b: 1.1 * u["weight"][(a >= b[0]) & (a < b[1])].sum() for b in BANDS}
    g, _ = rake.household_factors(u, u["weight"], targets=targets)
    reps = pd.DataFrame({f"r{r + 1:02d}": rake.household_factors(u, u["weight"] * base[r], targets=targets)[0]
                         for r in range(80)})
    raked = rake.apply_household_factors(u, g)
    np.testing.assert_allclose(fns["_replicate_ratios"](raked, ratios), base)   # no rake_replicates
    ratio = fns["_replicate_ratios"](raked, ratios, reps)
    w = raked["weight"].to_numpy()
    for r in range(80):
        got = [(w * ratio[r])[(a >= lo) & (a < hi)].sum() for lo, hi in BANDS]
        np.testing.assert_allclose(got, list(targets.values()), rtol=1e-7)


def test_weight_columns_are_rebuilt_from_the_final_weight(fns):
    u, _ = _setup()
    ratios = fns["unit_replicate_ratios"](u)
    g = pd.Series(1.0 + 0.1 * np.arange(200) / 200, index=pd.Index([f"H{h}" for h in range(200)], name="hh_id"))
    reps = pd.DataFrame({f"r{i:02d}": g.to_numpy() * (1 + 0.01 * i) for i in range(1, 81)}, index=g.index)
    raked = rake.apply_household_factors(u, g)
    raked["weight"] = raked["weight"] * 2.0                           # a later reweight

    class R:
        replicates = reps
    out = fns["_with_replicates"](raked, ratios, R())
    expect = raked["weight"] * ratios["weight_r05"] * 1.05
    np.testing.assert_allclose(out["weight_r05"], expect)
    plain = fns["_with_replicates"](raked, ratios, None)
    np.testing.assert_allclose(plain["weight_r05"], raked["weight"] * ratios["weight_r05"])
