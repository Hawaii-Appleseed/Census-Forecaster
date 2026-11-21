"""Bounded generalized raking (:mod:`tax_modeler.calibration.bounded_raking`) and the Act 24 constraint set."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from tax_modeler.calibration import apply_ipf_calibration_via_rake, dotax_base
from tax_modeler.calibration.act24_calibration import (
    DEFAULT_BOUNDS,
    FAMILY_EDGES,
    WIDE_BOUNDS,
    calibrate_act24,
    constraints,
)
from tax_modeler.calibration.bounded_raking import calibrate


def _problem(n=2000, seed=1):
    rng = np.random.default_rng(seed)
    w0 = rng.uniform(50, 150, n)
    g = rng.integers(0, 4, n)
    x = rng.lognormal(3, 1, n)
    X = np.column_stack([(g == k) for k in range(4)] + [x]).astype(float)
    return w0, X, g, x


def test_feasible_targets_are_met_exactly_and_ratios_stay_in_bounds():
    w0, X, g, x = _problem()
    target = (w0[:, None] * X).sum(0) * np.array([1.1, 0.9, 1.05, 0.95, 1.0])
    r = calibrate(w0, X, target, names=list("abcde"), bounds=(0.2, 5.0))
    assert r.exact and r.conflicts().empty
    np.testing.assert_allclose((w0 * r.ratios) @ X, target, rtol=1e-6)
    assert r.ratios.min() >= 0.2 - 1e-12 and r.ratios.max() <= 5.0 + 1e-12


def test_an_infeasible_system_is_reported_not_compromised_silently():
    w0, X, g, x = _problem()
    target = (w0[:, None] * X).sum(0)
    target[0] *= 0.9
    target[1] *= 1.0
    # group 0's count and the total of x cannot both hold with ratios inside [0.99, 1.01]
    r = calibrate(w0, X, target, names=list("abcde"), bounds=(0.99, 1.01))
    assert not r.exact
    c = r.conflicts()
    assert len(c) >= 1 and (c["relative_miss"].abs() > r.tol).all()
    assert {"achievable_low", "achievable_high"} <= set(r.table.columns)


def test_a_constraint_no_weights_can_reach_is_screened_and_does_not_hijack_the_solve():
    w0, X, g, x = _problem()
    target = (w0[:, None] * X).sum(0)
    X2 = np.column_stack([X, np.zeros(len(w0))])
    X2[:5, -1] = 1.0
    t2 = np.append(target, -50.0)       # a negative count of five units
    r = calibrate(w0, X2, t2, names=list("abcdef"), bounds=(0.2, 5.0))
    assert "f" in list(r.screened)
    np.testing.assert_allclose((w0 * r.ratios) @ X, target, rtol=1e-4)


def test_effective_sample_size_and_summary():
    w0, X, g, x = _problem()
    r = calibrate(w0, X, (w0[:, None] * X).sum(0) * 1.02, names=list("abcde"))
    assert 0 < r.effective_sample_size(w0) <= len(w0)
    s = r.ratio_summary()
    assert s["min"] <= s["median"] <= s["max"]


def _base(n=4000, seed=3):
    rng = np.random.default_rng(seed)
    agi = np.exp(rng.normal(10.9, 1.1, n))
    status = rng.choice(["single", "married_filing_jointly", "head_of_household"], n, p=[0.55, 0.3, 0.15])
    dep = np.where(status == "single", 0, rng.integers(0, 4, n))
    tb = np.maximum(agi - 10_000, 0) * 0.06
    return pd.DataFrame({"agi": agi, "federal_agi": agi, "filing_status": status, "weight": rng.uniform(100, 250, n),
                         "num_dependents": dep, "hi_tax_before_credits": tb, "hi_state_tax": tb - 150.0})


def test_constraints_have_matching_shapes_and_the_family_margins_are_the_departments():
    df = _base()
    names, X, t = constraints(df)
    assert X.shape == (len(df), len(names)) == (len(df), len(t))
    assert sum(n.startswith("filers ") for n in names) == sum(1 for k in dotax_base.filer_targets() if k[0] < 1e6)
    assert not any(n.startswith("tax after credits") for n in names)
    assert not any(n.startswith("agi ") for n in names)
    assert any(n.startswith("agi ") for n in constraints(df, agi_margin=True)[0])
    assert {"status married_filing_separately"} <= set(names)
    # joint returns by class are targets (without them the solver moved joint filers into separate filing at high AGI)
    joint = sum(v for n, v in zip(names, t, strict=True) if n.startswith("joint returns"))
    assert joint == pytest.approx(next(v for n, v in zip(names, t, strict=True) if n == "status married_filing_jointly"))
    assert any(n.startswith("tax after credits") for n in constraints(df, tax_after=True)[0])
    # head of household targets are brought to Table 4's head-of-household count, scaled as the status targets are
    hoh = sum(v for n, v in zip(names, t, strict=True) if n.startswith("head of household returns"))
    assert hoh == pytest.approx(next(v for n, v in zip(names, t, strict=True) if n == "status head_of_household"))
    dep = sum(v for n, v in zip(names, t, strict=True) if n.startswith("dependent exemptions"))
    assert dep == pytest.approx(sum(dotax_base.family_targets("dependents", FAMILY_EDGES["dependents"]).values()))


def test_the_default_bounds_are_the_documented_ones():
    assert DEFAULT_BOUNDS == (0.2, 5.0) and WIDE_BOUNDS == (0.1, 10.0)


def test_calibrate_act24_attaches_the_account_and_leaves_other_columns_alone():
    df = _base()
    out, res = calibrate_act24(df)
    assert out.attrs["calibration"] is res
    assert out["agi"].equals(df["agi"]) and (out["weight"] > 0).all()
    assert len(res.table) > 50


def test_apply_with_the_default_method_uses_the_bounded_solver_and_legacy_still_works():
    df = _base()
    out = apply_ipf_calibration_via_rake(df)
    assert "calibration" in out.attrs
    old = apply_ipf_calibration_via_rake(df, method="legacy")
    assert "calibration" not in old.attrs
    with pytest.raises(ValueError):
        apply_ipf_calibration_via_rake(df, method="nope")


def test_a_frame_without_the_columns_falls_back_to_the_legacy_rake():
    df = _base().drop(columns=["num_dependents"])
    out = apply_ipf_calibration_via_rake(df)
    assert "calibration" not in out.attrs
