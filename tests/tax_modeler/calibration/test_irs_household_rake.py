"""Household-level raking of tax-unit weights to IRS return counts."""
import numpy as np
import pandas as pd
import pytest
from tax_modeler.calibration import irs_household_rake as rake
from tax_modeler.errors import CalibrationError

BANDS = [(1, 10_000), (10_000, 25_000), (25_000, 50_000)]


def _frame(seed=0, n_hh=300):
    rng = np.random.default_rng(seed)
    rows = []
    for h in range(n_hh):
        wgtp = float(rng.integers(20, 200))
        for _ in range(rng.integers(1, 4)):
            rows.append({"hh_id": f"H{h}", "federal_agi": float(rng.choice([-500, 5_000, 15_000, 40_000, 90_000])),
                         "weight": wgtp * rng.uniform(0.7, 1.3), "hh_weight": wgtp})
    return pd.DataFrame(rows)


def _margins(u, w):
    a = u["federal_agi"].to_numpy()
    return np.array([w[(a >= lo) & (a < hi)].sum() for lo, hi in BANDS])


def _targets(u, scale=(1.2, 0.8, 1.1)):
    m = _margins(u, u["weight"].to_numpy())
    return {b: m[i] * scale[i] for i, b in enumerate(BANDS)}


def test_margins_hit_and_household_integrity():
    u = _frame()
    t = _targets(u)
    f, info = rake.household_factors(u, u["weight"], targets=t)
    out = rake.apply_household_factors(u, f)
    got = _margins(out, out["weight"].to_numpy())
    assert np.allclose(got, list(t.values()), rtol=1e-8)
    assert info["max_abs_rel_error"] < 1e-8
    assert (out.groupby("hh_id")["irs_rake_factor"].nunique() == 1).all()
    assert (f > 0).all() and info["factor_min"] > 0
    assert info["ess_after"] > 0 and info["ess_before"] > 0


def test_untargeted_households_keep_one_and_hh_weight_untouched():
    u = _frame()
    f, _ = rake.household_factors(u, u["weight"], targets=_targets(u))
    out = rake.apply_household_factors(u, f)
    a = u["federal_agi"].to_numpy()
    in_any = pd.Series(((a >= 1) & (a < 50_000)), index=u.index).groupby(u["hh_id"]).any()
    assert (f[~in_any.reindex(f.index)] == 1.0).all()
    assert (f[in_any.reindex(f.index)] != 1.0).any()
    assert out["hh_weight"].equals(u["hh_weight"])
    assert not np.shares_memory(out["weight"].to_numpy(), u["weight"].to_numpy())


def test_already_on_target_gives_unit_factors():
    u = _frame()
    f, info = rake.household_factors(u, u["weight"], targets=_targets(u, (1, 1, 1)))
    assert np.allclose(f.to_numpy(), 1.0) and info["iterations"] == 0


def test_non_convergence_raises():
    u = _frame()
    with pytest.raises(CalibrationError):
        rake.household_factors(u, u["weight"], targets=_targets(u, (50.0, 0.001, 30.0)), max_iter=2)


def test_irs_targets_are_the_seven_classes():
    t = rake.irs_targets()
    assert len(t) == 7 and min(lo for lo, _ in t) == 1 and max(hi for _, hi in t) == 500_000
