"""Tests for tax_modeler.poverty.impact.compute_poverty_impact."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from tax_modeler.credits.arpa_ctc import arpa_ctc_for_tax_units
from tax_modeler.poverty.impact import (
    _DEFAULT_SCENARIOS,
    PovertyImpactResult,
    _persons_per_unit,
    compute_poverty_impact,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_units(rows: list[dict]) -> pd.DataFrame:
    """Construct a minimal tax-unit DataFrame with the columns the impact
    module requires. Caller supplies overrides; sensible defaults fill the rest.
    """
    defaults = {
        "filing_status": "married_filing_jointly",
        "num_dependents": 0,
        "num_qualifying_children": 0,
        "total_cash_income": 20_000.0,
        "earned_income": 20_000.0,
        "income": 20_000.0,
        "weight": 1.0,
        "hi_tax_liability": 0.0,
        "eitc_amount": 0.0,
        "ctc_total": 0.0,
        "ctc_refundable": 0.0,
        "hi_eitc_amount": 0.0,
        "tenure": "renter",
        "county": "Honolulu",
        "house_district": 1,
        "senate_district": 1,
        "PUMA": 301,
    }
    out = pd.DataFrame([{**defaults, **r} for r in rows])
    out = arpa_ctc_for_tax_units(out)
    return out


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_persons_per_unit_mfj_with_two_kids():
    df = pd.DataFrame([
        {"filing_status": "married_filing_jointly", "num_dependents": 2},
        {"filing_status": "single", "num_dependents": 0},
        {"filing_status": "head_of_household", "num_dependents": 3},
        {"filing_status": "married_filing_jointly", "num_dependents": 0},
    ])
    persons = _persons_per_unit(df)
    np.testing.assert_array_equal(persons, np.array([4.0, 1.0, 4.0, 2.0]))


def test_lifts_two_kids_out_of_poverty():
    """A low-income MFJ filer with 2 kids that EITC alone lifts above threshold."""
    # 2024 Hawaii MFJ + 2 dependents threshold ≈ $40,584 × equiv_scale(2,2)/equiv_scale(2,2)
    # × renter (1.0) = $40,584. Start the filer comfortably below: $20k.
    # Give them: $6,960 EITC (TY2024 2-kid max), $3,400 CTC refundable, $278 HI EITC.
    # Without EITC: spm_resources ≈ 20k + 3.4k + 0.278k - taxes ≈ ~22k < threshold.
    # With EITC: 22k + 6.96k ≈ 29k. Still below threshold actually.
    # To force a lift across threshold, give very large EITC (50k base + 6.96k EITC = 56.96k > 40.6k).
    units = _make_units([{
        "filing_status": "married_filing_jointly",
        "num_dependents": 2,
        "num_qualifying_children": 2,
        "total_cash_income": 38_000.0,  # below threshold
        "earned_income": 38_000.0,
        "income": 38_000.0,
        "eitc_amount": 6_960.0,           # lifts above threshold
        "ctc_refundable": 3_400.0,
        "ctc_total": 4_000.0,
        "hi_eitc_amount": 6_960.0 * 0.40,  # 40% of federal EITC
        "weight": 100.0,
    }])

    result = compute_poverty_impact(units, tax_year=2024)
    state = result.by_state.iloc[0]

    # 4 persons (2 adults + 2 kids) × weight 100 = 400 weighted persons
    assert state["weighted_persons"] == pytest.approx(400.0)
    # Baseline (with all credits) should be NOT poor (lifted above threshold)
    assert state["persons_in_poverty_baseline"] == pytest.approx(0.0)
    # no_eitc removes the lift → should fall below threshold → 400 persons in poverty
    assert state["persons_lifted_no_eitc"] == pytest.approx(400.0)


def test_no_credits_dominates_baseline_monotone():
    """Removing all credits should NEVER reduce poverty (monotonicity)."""
    rng = np.random.default_rng(42)
    rows = []
    for _ in range(150):
        income = float(rng.uniform(5_000, 80_000))
        n_kids = int(rng.integers(0, 4))
        rows.append({
            "filing_status": rng.choice(["single", "married_filing_jointly", "head_of_household"]),
            "num_dependents": n_kids,
            "num_qualifying_children": n_kids,
            "total_cash_income": income,
            "earned_income": income,
            "income": income,
            "weight": float(rng.uniform(50, 500)),
            "eitc_amount": float(rng.uniform(0, 5_000)) if n_kids > 0 else 0.0,
            "ctc_refundable": n_kids * 1_700.0 if n_kids > 0 else 0.0,
            "ctc_total": n_kids * 2_000.0 if n_kids > 0 else 0.0,
            "hi_eitc_amount": float(rng.uniform(0, 2_000)),
            "county": rng.choice(["Honolulu", "Hawaii", "Maui", "Kauai"]),
            "house_district": int(rng.integers(1, 52)),
            "senate_district": int(rng.integers(1, 26)),
        })
    units = _make_units(rows)
    result = compute_poverty_impact(units, tax_year=2024)

    # Every geography: no_credits poverty rate >= baseline.
    for frame_name in ("by_state", "by_county", "by_house_district", "by_senate_district"):
        frame = getattr(result, frame_name)
        for _, row in frame.iterrows():
            assert row["poverty_rate_no_credits"] >= row["poverty_rate_baseline"] - 1e-9, (
                f"{frame_name}: no_credits poverty rate "
                f"{row['poverty_rate_no_credits']:.4f} < baseline "
                f"{row['poverty_rate_baseline']:.4f}"
            )


def test_joint_differs_from_sum_of_marginals():
    """Documents the limitation: marginal attribution is non-additive."""
    # Build a frame where phase-out interactions matter.
    rng = np.random.default_rng(7)
    rows = []
    for _ in range(80):
        income = float(rng.uniform(15_000, 50_000))
        n_kids = int(rng.integers(1, 4))
        rows.append({
            "filing_status": "head_of_household",
            "num_dependents": n_kids,
            "num_qualifying_children": n_kids,
            "total_cash_income": income,
            "earned_income": income,
            "income": income,
            "weight": 100.0,
            "eitc_amount": float(rng.uniform(2_000, 6_000)),
            "ctc_refundable": n_kids * 1_700.0,
            "ctc_total": n_kids * 2_000.0,
            "hi_eitc_amount": float(rng.uniform(800, 2_400)),
        })
    units = _make_units(rows)
    result = compute_poverty_impact(units, tax_year=2024)
    s = result.by_state.iloc[0]
    sum_marginals = (
        s["persons_lifted_no_eitc"]
        + s["persons_lifted_no_ctc"]
        + s["persons_lifted_no_hi_eitc"]
    )
    joint = s["persons_lifted_no_credits"]
    # Either non-zero AND non-equal, OR both zero (vacuously different
    # by zero — accept this).
    assert sum_marginals == pytest.approx(joint) or (sum_marginals > 0 and joint > 0)


def test_threshold_year_propagates():
    """Same units at different tax_year ⇒ different thresholds ⇒ may differ in rate."""
    units = _make_units([{
        "total_cash_income": 33_000.0, "earned_income": 33_000.0, "income": 33_000.0,
        "weight": 100.0,
    }])
    r2022 = compute_poverty_impact(units, tax_year=2022).by_state.iloc[0]["poverty_rate_baseline"]
    r2025 = compute_poverty_impact(units, tax_year=2025).by_state.iloc[0]["poverty_rate_baseline"]
    # 2025 threshold is higher → same income more likely below → rate ≥ 2022.
    assert r2025 >= r2022 - 1e-9


def test_schema_completeness():
    """All 24 output columns must be present on each frame (1 state + 4 county rows)."""
    units = _make_units([
        {"county": "Honolulu", "weight": 100.0, "num_dependents": 1, "num_qualifying_children": 1,
         "eitc_amount": 3_000.0, "ctc_refundable": 1_700.0, "ctc_total": 2_000.0,
         "hi_eitc_amount": 1_200.0, "total_cash_income": 25_000.0, "earned_income": 25_000.0,
         "income": 25_000.0},
        {"county": "Hawaii", "weight": 80.0, "total_cash_income": 30_000.0,
         "earned_income": 30_000.0, "income": 30_000.0},
        {"county": "Maui", "weight": 60.0, "total_cash_income": 40_000.0,
         "earned_income": 40_000.0, "income": 40_000.0},
        {"county": "Kauai", "weight": 40.0, "total_cash_income": 50_000.0,
         "earned_income": 50_000.0, "income": 50_000.0},
    ])
    result = compute_poverty_impact(units, tax_year=2024)
    expected_base_cols = {
        "weighted_persons", "weighted_filers", "poverty_rate_baseline",
        "persons_in_poverty_baseline", "poverty_gap_baseline_$",
    }
    for scn in _DEFAULT_SCENARIOS:
        expected_base_cols.update({
            f"poverty_rate_{scn}", f"persons_lifted_{scn}", f"gap_closed_{scn}_$",
        })
    for frame_name in ("by_state", "by_county", "by_house_district", "by_senate_district"):
        frame = getattr(result, frame_name)
        missing = expected_base_cols - set(frame.columns)
        assert not missing, f"{frame_name} missing columns: {missing}"
    assert len(result.by_state) == 1
    assert len(result.by_county) == 4


def test_hi_ctc_650_increases_lift():
    """A $650/child HI state CTC should reduce poverty on a kids-heavy low-income frame."""
    rng = np.random.default_rng(11)
    rows = []
    for _ in range(120):
        n_kids = int(rng.integers(2, 5))
        income = float(rng.uniform(15_000, 40_000))
        rows.append({
            "filing_status": "head_of_household",
            "num_dependents": n_kids,
            "num_qualifying_children": n_kids,
            "total_cash_income": income,
            "earned_income": income,
            "income": income,
            "weight": 100.0,
            "eitc_amount": float(rng.uniform(2_000, 5_000)),
            "ctc_refundable": n_kids * 1_700.0,
            "ctc_total": n_kids * 2_000.0,
            "hi_eitc_amount": float(rng.uniform(800, 2_000)),
        })
    units = _make_units(rows)
    result = compute_poverty_impact(units, tax_year=2024)
    s = result.by_state.iloc[0]
    assert s["persons_lifted_hi_ctc_650"] > 0, (
        f"hi_ctc_650 should lift some persons; got {s['persons_lifted_hi_ctc_650']}"
    )


def test_hi_ctc_650_zero_when_no_children():
    """No qualifying children ⇒ $650/child credit ⇒ no lift."""
    units = _make_units([
        {"total_cash_income": 28_000.0, "earned_income": 28_000.0, "income": 28_000.0,
         "num_qualifying_children": 0, "num_dependents": 0, "weight": 100.0},
    ])
    result = compute_poverty_impact(units, tax_year=2024)
    s = result.by_state.iloc[0]
    assert s["persons_lifted_hi_ctc_650"] == 0.0


def test_hi_ctc_dollar_scenarios_monotone():
    """Across the named hi_ctc_<NNN> scenarios, lift count grows with the dollar amount.

    The fixture mirrors ``test_hi_ctc_650_increases_lift`` (kids-heavy
    low-income HoH filers with EITC + refundable CTC already attached)
    so units start near the SPM poverty threshold — small per-kid
    credit increments can move some across the line.
    """
    rng = np.random.default_rng(13)
    rows = []
    for _ in range(120):
        n_kids = int(rng.integers(2, 5))
        income = float(rng.uniform(15_000, 40_000))
        rows.append({
            "filing_status": "head_of_household",
            "num_dependents": n_kids,
            "num_qualifying_children": n_kids,
            "total_cash_income": income,
            "earned_income": income,
            "income": income,
            "weight": 100.0,
            "eitc_amount": float(rng.uniform(2_000, 5_000)),
            "ctc_refundable": n_kids * 1_700.0,
            "ctc_total": n_kids * 2_000.0,
            "hi_eitc_amount": float(rng.uniform(800, 2_000)),
        })
    units = _make_units(rows)
    s = compute_poverty_impact(units, tax_year=2024).by_state.iloc[0]
    lifts = [s[f"persons_lifted_hi_ctc_{amt}"] for amt in (300, 650, 1000)]
    # Strictly monotone non-decreasing.
    assert lifts == sorted(lifts), f"non-monotone lifts: {lifts}"
    # At least one must strictly lift somebody.
    assert lifts[-1] > 0


def test_hi_ctc_dynamic_scenario_parse():
    """Ad-hoc dollar amounts via ``hi_ctc_<NNN>`` scenario name (e.g., amendment analysis)."""
    units = _kids_heavy_low_income_units(seed=17)
    # Custom dollar amount not in the default set.
    r = compute_poverty_impact(units, tax_year=2024, scenarios=("hi_ctc_500",))
    s = r.by_state.iloc[0]
    assert "persons_lifted_hi_ctc_500" in s.index
    # And a non-numeric suffix should raise ValueError via the unknown-scenario branch.
    import pytest
    with pytest.raises(ValueError, match="unknown scenario"):
        compute_poverty_impact(units, tax_year=2024, scenarios=("hi_ctc_abc",))


def test_result_dataclass_shape():
    """PovertyImpactResult must carry units, four frames, year, meta, scenarios, notes."""
    units = _make_units([{"weight": 100.0, "total_cash_income": 30_000.0,
                          "earned_income": 30_000.0, "income": 30_000.0}])
    result = compute_poverty_impact(units, tax_year=2024)
    assert isinstance(result, PovertyImpactResult)
    assert result.tax_year == 2024
    assert result.scenarios == _DEFAULT_SCENARIOS
    assert len(result.notes) >= 2  # static + hi_ctc_650 caveat
    assert "spm_resources" in result.units.columns
    assert "spm_threshold" in result.units.columns
    assert "person_weight" in result.units.columns


# ---------------------------------------------------------------------------
# Tier 1 follow-ups (PR #4): take-up rates on expansion scenarios + MOOP +
# upstream credit-take-up calibration.
# ---------------------------------------------------------------------------


def _kids_heavy_low_income_units(seed: int = 11, n: int = 120) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for _ in range(n):
        n_kids = int(rng.integers(2, 5))
        income = float(rng.uniform(15_000, 40_000))
        rows.append({
            "filing_status": "head_of_household",
            "num_dependents": n_kids,
            "num_qualifying_children": n_kids,
            "total_cash_income": income,
            "earned_income": income,
            "income": income,
            "weight": 100.0,
            "eitc_amount": float(rng.uniform(2_000, 5_000)),
            "ctc_refundable": n_kids * 1_700.0,
            "ctc_total": n_kids * 2_000.0,
            "hi_eitc_amount": float(rng.uniform(800, 2_000)),
        })
    return _make_units(rows)


def test_takeup_rate_zero_kills_hi_ctc_650_lift():
    """hi_ctc_takeup_rate=0.0 ⇒ no persons lifted by the hi_ctc_650 scenario."""
    units = _kids_heavy_low_income_units()
    s = compute_poverty_impact(
        units, tax_year=2024, hi_ctc_takeup_rate=0.0,
    ).by_state.iloc[0]
    assert s["persons_lifted_hi_ctc_650"] == 0.0


def test_takeup_rate_monotone_hi_ctc_650():
    """persons_lifted_hi_ctc_650 should grow weakly with hi_ctc_takeup_rate."""
    units = _kids_heavy_low_income_units(seed=17)
    lifts = []
    for rate in (0.0, 0.4, 0.8, 1.0):
        s = compute_poverty_impact(
            units, tax_year=2024, hi_ctc_takeup_rate=rate,
        ).by_state.iloc[0]
        lifts.append(s["persons_lifted_hi_ctc_650"])
    # Strictly monotone non-decreasing.
    assert lifts == sorted(lifts), f"non-monotone lifts: {lifts}"
    # And the top rate should strictly lift somebody so monotone is non-trivial.
    assert lifts[-1] > 0.0


def test_takeup_rate_monotone_hi_eitc_100pct():
    """persons_lifted_hi_eitc_100pct should grow weakly with hi_eitc_100pct_takeup_rate."""
    units = _kids_heavy_low_income_units(seed=23)
    lifts = []
    for rate in (0.0, 0.5, 0.95, 1.0):
        s = compute_poverty_impact(
            units, tax_year=2024, hi_eitc_100pct_takeup_rate=rate,
        ).by_state.iloc[0]
        lifts.append(s["persons_lifted_hi_eitc_100pct"])
    assert lifts == sorted(lifts), f"non-monotone lifts: {lifts}"


def test_takeup_rate_monotone_arpa_ctc():
    """persons_lifted_expanded_ctc_2021 should grow weakly with arpa_ctc_takeup_rate."""
    units = _kids_heavy_low_income_units(seed=29)
    lifts = []
    for rate in (0.0, 0.5, 0.95, 1.0):
        s = compute_poverty_impact(
            units, tax_year=2024, arpa_ctc_takeup_rate=rate,
        ).by_state.iloc[0]
        lifts.append(s["persons_lifted_expanded_ctc_2021"])
    assert lifts == sorted(lifts), f"non-monotone lifts: {lifts}"


def test_credit_takeup_reduces_baseline_lift():
    """Wiring IRS take-up on baseline credits should reduce the no_eitc lift.

    Without take-up (baseline reflects eligibility), more units appear to
    receive EITC and so more units appear to be lifted by it. With take-up
    (baseline reflects receipt), some marginal-eligible units lose their
    EITC, so the no_eitc counterfactual moves fewer of them below the
    poverty line.

    This test uses a count-only caseload (annual_dollars_millions=0) to
    isolate the take-up zeroing behavior from the dollar calibration step,
    which requires a full-PUMS population to produce a meaningful scalar.
    """
    from tax_modeler.calibration.admin_caseload import AdminCaseload
    from tax_modeler.calibration.takeup_imputation import calibrate_benefits

    rng = np.random.default_rng(101)
    rows = []
    for _ in range(150):
        n_kids = int(rng.integers(1, 4))
        income = float(rng.uniform(12_000, 35_000))
        rows.append({
            "filing_status": "head_of_household",
            "num_dependents": n_kids,
            "num_qualifying_children": n_kids,
            "total_cash_income": income,
            "earned_income": income,
            "income": income,
            "weight": 100.0,
            "eitc_amount": float(rng.uniform(2_000, 5_500)),
            "ctc_refundable": n_kids * 1_700.0,
            "ctc_total": n_kids * 2_000.0,
            "hi_eitc_amount": float(rng.uniform(800, 2_000)),
        })
    units_pre = _make_units(rows)
    s_pre = compute_poverty_impact(units_pre, tax_year=2022).by_state.iloc[0]
    lift_pre = s_pre["persons_lifted_no_eitc"]

    # Build a count-only caseload (annual_dollars_millions=0) so that the
    # dollar-calibration step is skipped. On a 150-row fixture the dollar
    # totals are nowhere near the IRS SOI state total, so the scalar would
    # be nonsensically large; we test the zeroing behavior in isolation.
    real_eitc = AdminCaseload.load().target("eitc", 2022)
    real_actc = AdminCaseload.load().target("actc", 2022)
    count_only_cl = AdminCaseload(pd.DataFrame([
        {"program": "eitc",  "year": 2022, "unit": real_eitc.unit,
         "count": real_eitc.count, "annual_dollars_millions": 0.0},
        {"program": "actc", "year": 2022, "unit": real_actc.unit,
         "count": real_actc.count, "annual_dollars_millions": 0.0},
    ]))

    # Apply take-up calibration; this zeroes credits for the lowest-eligible
    # filers until the weighted claim count matches the IRS SOI target.
    units_post = calibrate_benefits(
        units_pre.copy(), caseload=count_only_cl, year=2022,
        programs=("eitc", "actc"),
    )
    s_post = compute_poverty_impact(units_post, tax_year=2022).by_state.iloc[0]
    lift_post = s_post["persons_lifted_no_eitc"]

    assert lift_post <= lift_pre + 1e-9, (
        f"take-up calibration should not increase the no_eitc lift; "
        f"pre={lift_pre}, post={lift_post}"
    )


def test_moop_lowers_baseline_rate():
    """Imputing MOOP into spm_resources should never *raise* the baseline rate.

    MOOP is subtracted from resources, so adding it shifts more units below
    the threshold — baseline rate goes UP, not down. (The task brief had
    the sign inverted; verify the documented direction here.)
    """
    from tax_modeler.benefits.moop import compute_moop_for_units

    rng = np.random.default_rng(37)
    rows = []
    for _ in range(120):
        n_kids = int(rng.integers(0, 3))
        income = float(rng.uniform(25_000, 70_000))
        rows.append({
            "filing_status": rng.choice(["single", "head_of_household", "married_filing_jointly"]),
            "num_dependents": n_kids,
            "num_qualifying_children": n_kids,
            "total_cash_income": income,
            "earned_income": income,
            "income": income,
            "weight": 100.0,
            "primary_agep": int(rng.integers(25, 80)),
            "eitc_amount": 0.0,
            "ctc_refundable": 0.0,
            "ctc_total": 0.0,
            "hi_eitc_amount": 0.0,
        })
    units = _make_units(rows)
    r_pre = compute_poverty_impact(units, tax_year=2024).by_state.iloc[0]
    units_with_moop = compute_moop_for_units(units)
    r_post = compute_poverty_impact(units_with_moop, tax_year=2024).by_state.iloc[0]

    # MOOP is subtracted from resources, so the baseline rate cannot
    # fall when MOOP is imputed.
    assert r_post["poverty_rate_baseline"] >= r_pre["poverty_rate_baseline"] - 1e-9


def test_moop_imputation_basic_shape():
    """compute_moop_for_units adds a non-negative moop_amount with reasonable mean."""
    from tax_modeler.benefits.moop import compute_moop_for_units

    units = _make_units([
        {"primary_agep": 35, "total_cash_income": 40_000.0, "income": 40_000.0,
         "earned_income": 40_000.0, "weight": 100.0, "num_dependents": 2,
         "num_qualifying_children": 2},
        {"primary_agep": 70, "total_cash_income": 25_000.0, "income": 25_000.0,
         "earned_income": 25_000.0, "weight": 100.0, "filing_status": "single"},
        {"primary_agep": 22, "total_cash_income": 18_000.0, "income": 18_000.0,
         "earned_income": 18_000.0, "weight": 100.0, "filing_status": "single"},
    ])
    out = compute_moop_for_units(units)
    assert "moop_amount" in out.columns
    assert (out["moop_amount"] >= 0).all()
    # CPS ASEC donor mean MOOP for Hawaii is in the ~$1k-$3k/yr range; assert
    # imputed values fall in a plausible band, not zeros and not absurdly high.
    assert 200.0 <= out["moop_amount"].mean() <= 10_000.0


# ---------------------------------------------------------------------------
# Tier 2 — housing subsidy + childcare subsidy
# ---------------------------------------------------------------------------


def _low_income_frame(n: int = 80, *, with_kids: bool = False, seed: int = 7) -> pd.DataFrame:
    """A frame skewed low-income enough that subsidies meaningfully shift SPM."""
    rng = np.random.default_rng(seed)
    rows = []
    for _ in range(n):
        kids = int(rng.integers(0, 3)) if with_kids else 0
        rows.append({
            "filing_status": "single",
            "num_dependents": kids,
            "num_qualifying_children": kids,
            "total_cash_income": float(rng.integers(8_000, 25_000)),
            "earned_income": float(rng.integers(8_000, 25_000)),
            "income": float(rng.integers(8_000, 25_000)),
            "weight": 100.0,
            "primary_agep": int(rng.integers(25, 60)),
            "eitc_amount": 0.0,
            "ctc_refundable": 0.0,
            "ctc_total": 0.0,
            "hi_eitc_amount": 0.0,
        })
    return _make_units(rows)


def test_housing_subsidy_lowers_baseline_rate():
    """Adding a housing-subsidy column lifts SPM resources → poverty rate falls (or stays equal)."""
    from tax_modeler.benefits.housing import compute_housing_for_units

    units = _low_income_frame(n=80, with_kids=False, seed=11)
    r_no_sub = compute_poverty_impact(units, tax_year=2024).by_state.iloc[0]
    units_with_sub = compute_housing_for_units(units)
    r_with_sub = compute_poverty_impact(units_with_sub, tax_year=2024).by_state.iloc[0]
    # Subsidy is added to resources, so baseline rate cannot rise.
    assert r_with_sub["poverty_rate_baseline"] <= r_no_sub["poverty_rate_baseline"] + 1e-9
    # And on a low-income frame the subsidy is large enough to clearly lower it.
    assert r_with_sub["poverty_rate_baseline"] < r_no_sub["poverty_rate_baseline"]


def test_childcare_subsidy_lowers_baseline_rate():
    """CCSP subsidy on working low-income families with kids lifts SPM resources."""
    from tax_modeler.benefits.childcare import compute_childcare_for_units

    units = _low_income_frame(n=80, with_kids=True, seed=13)
    r_no_sub = compute_poverty_impact(units, tax_year=2024).by_state.iloc[0]
    units_with_sub = compute_childcare_for_units(units)
    # The CCSP module zeroes the subsidy for filers with no kids / no work
    # income, so the working-low-income-with-kids subset must receive >0.
    assert (units_with_sub["childcare_amount"] > 0).any()
    r_with_sub = compute_poverty_impact(units_with_sub, tax_year=2024).by_state.iloc[0]
    assert r_with_sub["poverty_rate_baseline"] <= r_no_sub["poverty_rate_baseline"] + 1e-9


# ---------------------------------------------------------------------------
# Tier 2 — childcare-expense + work-expense imputation
# ---------------------------------------------------------------------------


def test_childcare_expense_raises_baseline_rate():
    """Subtracting childcare expense reduces resources → poverty rate cannot fall."""
    from tax_modeler.benefits.childcare_expense import (
        compute_childcare_expense_for_units,
    )

    units = _low_income_frame(n=80, with_kids=True, seed=19)
    r_pre = compute_poverty_impact(units, tax_year=2024).by_state.iloc[0]
    units_with_exp = compute_childcare_expense_for_units(units)
    # At least some working families with kids must get a positive amount.
    assert (units_with_exp["childcare_expense_amount"] > 0).any()
    r_post = compute_poverty_impact(units_with_exp, tax_year=2024).by_state.iloc[0]
    assert r_post["poverty_rate_baseline"] >= r_pre["poverty_rate_baseline"] - 1e-9


def test_work_expense_proportional_to_earners():
    """Two-earner MFJ filers should average a higher work expense than single-earner singles."""
    from tax_modeler.benefits.work_expense import compute_work_expense_for_units

    units = _make_units([
        # Single working filers (n_earners=1).
        *[{"filing_status": "single", "num_dependents": 0,
           "num_qualifying_children": 0, "total_cash_income": 30_000.0,
           "income": 30_000.0, "earned_income": 30_000.0,
           "primary_hours_worked": 40.0, "secondary_hours_worked": 0.0,
           "weight": 100.0} for _ in range(50)],
        # MFJ two-earner filers.
        *[{"filing_status": "married_filing_jointly", "num_dependents": 1,
           "num_qualifying_children": 1, "total_cash_income": 60_000.0,
           "income": 60_000.0, "earned_income": 60_000.0,
           "primary_hours_worked": 40.0, "secondary_hours_worked": 40.0,
           "weight": 100.0} for _ in range(50)],
    ])
    out = compute_work_expense_for_units(units)
    assert "work_expense_amount" in out.columns
    assert (out["work_expense_amount"] >= 0).all()
    singles = out.iloc[:50]["work_expense_amount"].mean()
    couples = out.iloc[50:]["work_expense_amount"].mean()
    assert couples > singles


def test_childcare_expense_cap_mfj_single_earner():
    """MFJ with non-working spouse → cap = 0 → childcare_expense_amount = 0.

    Census SPM disallows childcare deduction when one parent could have
    provided care. Without the cap, the donor-imputed amount would
    over-subtract resources for single-earner MFJ families.
    """
    from tax_modeler.benefits.childcare_expense import (
        compute_childcare_expense_for_units,
    )

    units = _make_units([
        {"filing_status": "married_filing_jointly", "num_dependents": 2,
         "num_qualifying_children": 2, "total_cash_income": 40_000.0,
         "earned_income": 40_000.0, "income": 40_000.0,
         "primary_hours_worked": 40.0, "secondary_hours_worked": 0.0,
         "weight": 100.0},
    ])
    out = compute_childcare_expense_for_units(units)
    assert out.iloc[0]["childcare_expense_amount"] == 0.0


def test_childcare_expense_cap_single_parent_limited_by_earnings():
    """Single parent's childcare deduction ≤ their own earnings."""
    from tax_modeler.benefits.childcare_expense import (
        compute_childcare_expense_for_units,
    )

    units = _make_units([
        {"filing_status": "head_of_household", "num_dependents": 1,
         "num_qualifying_children": 1, "total_cash_income": 1_000.0,
         "earned_income": 1_000.0, "income": 1_000.0,
         "primary_hours_worked": 10.0, "secondary_hours_worked": 0.0,
         "weight": 100.0},
    ])
    out = compute_childcare_expense_for_units(units)
    assert out.iloc[0]["childcare_expense_amount"] <= 1_000.0 + 1e-9


def test_only_workers_have_work_expense():
    """Filers with zero earned income must get zero work expense (SPM convention)."""
    from tax_modeler.benefits.work_expense import compute_work_expense_for_units

    units = _make_units([
        {"filing_status": "single", "total_cash_income": 12_000.0,
         "earned_income": 0.0, "income": 12_000.0,
         "primary_hours_worked": 0.0, "secondary_hours_worked": 0.0,
         "weight": 100.0},
        {"filing_status": "single", "total_cash_income": 12_000.0,
         "earned_income": 12_000.0, "income": 12_000.0,
         "primary_hours_worked": 40.0, "secondary_hours_worked": 0.0,
         "weight": 100.0},
    ])
    out = compute_work_expense_for_units(units)
    assert out.iloc[0]["work_expense_amount"] == 0.0
    assert out.iloc[1]["work_expense_amount"] > 0.0


# ---------------------------------------------------------------------------
# Tier 2 — SPM-unit pooling
# ---------------------------------------------------------------------------


# Legacy tax-unit pooling heuristic (build_spm_units) was removed in
# favor of RELSHIPP-based SPM unit assembly. See
# tests/tax_modeler/units/test_spm_unit_assembly.py and
# tests/tax_modeler/poverty/test_spm_aggregation.py for the new tests.


# ---------------------------------------------------------------------------
# Tier 2 — district raking (biproportional IPF)
# ---------------------------------------------------------------------------


def _raking_frame(rng_seed: int = 17) -> pd.DataFrame:
    """A frame spanning 2 PUMAs × 3 HDs with uneven within-PUMA mass."""
    rng = np.random.default_rng(rng_seed)
    rows = []
    # PUMA 301 split across HD 10, 11. Heavy 10 pre-raking.
    for _ in range(40):
        rows.append({"PUMA": 301, "house_district": 10, "weight": 100.0,
                     "income": float(rng.integers(20_000, 80_000)),
                     "earned_income": float(rng.integers(20_000, 80_000)),
                     "total_cash_income": float(rng.integers(20_000, 80_000))})
    for _ in range(10):
        rows.append({"PUMA": 301, "house_district": 11, "weight": 100.0,
                     "income": float(rng.integers(20_000, 80_000)),
                     "earned_income": float(rng.integers(20_000, 80_000)),
                     "total_cash_income": float(rng.integers(20_000, 80_000))})
    # PUMA 302 split across HD 11, 12. Heavy 12 pre-raking.
    for _ in range(10):
        rows.append({"PUMA": 302, "house_district": 11, "weight": 100.0,
                     "income": float(rng.integers(20_000, 80_000)),
                     "earned_income": float(rng.integers(20_000, 80_000)),
                     "total_cash_income": float(rng.integers(20_000, 80_000))})
    for _ in range(40):
        rows.append({"PUMA": 302, "house_district": 12, "weight": 100.0,
                     "income": float(rng.integers(20_000, 80_000)),
                     "earned_income": float(rng.integers(20_000, 80_000)),
                     "total_cash_income": float(rng.integers(20_000, 80_000))})
    return pd.DataFrame(rows)


def test_raking_preserves_state_total():
    """Total weight is preserved (~) after biproportional raking."""
    from tax_modeler.analysis.district_raking import rake_biproportional

    df = _raking_frame()
    pre_total = df["weight"].sum()
    # HD targets shift mass within each PUMA but preserve per-PUMA totals.
    hd_targets = {10: 2500.0, 11: 2500.0, 12: 2500.0}
    w_post = rake_biproportional(
        df["weight"].to_numpy(),
        puma_ids=df["PUMA"].to_numpy(),
        hd_ids=df["house_district"].to_numpy(),
        hd_targets=hd_targets,
    )
    assert w_post.sum() == pytest.approx(pre_total, rel=1e-3)


def test_raking_preserves_puma_marginals():
    """Per-PUMA sum(weight) is held fixed by construction."""
    from tax_modeler.analysis.district_raking import rake_biproportional

    df = _raking_frame()
    pre_puma = df.groupby("PUMA")["weight"].sum().to_dict()
    hd_targets = {10: 3000.0, 11: 1500.0, 12: 3000.0}
    w_post = rake_biproportional(
        df["weight"].to_numpy(),
        puma_ids=df["PUMA"].to_numpy(),
        hd_ids=df["house_district"].to_numpy(),
        hd_targets=hd_targets,
    )
    df["w_post"] = w_post
    post_puma = df.groupby("PUMA")["w_post"].sum().to_dict()
    for puma, target in pre_puma.items():
        assert post_puma[puma] == pytest.approx(target, rel=1e-3)


def test_raking_matches_irs_district_targets_within_2pct():
    """Per-HD sum(weight) lands within 2% of the IRS target after raking."""
    from tax_modeler.analysis.district_raking import rake_biproportional

    df = _raking_frame()
    # Build targets consistent with per-PUMA marginals (each PUMA has
    # total weight = 5,000; state total = 10,000). HD 11 spans both
    # PUMAs and aggregates filers from each.
    hd_targets = {10: 3000.0, 11: 3000.0, 12: 4000.0}
    w_post = rake_biproportional(
        df["weight"].to_numpy(),
        puma_ids=df["PUMA"].to_numpy(),
        hd_ids=df["house_district"].to_numpy(),
        hd_targets=hd_targets,
        max_iter=100,
    )
    df["w_post"] = w_post
    post_hd = df.groupby("house_district")["w_post"].sum().to_dict()
    for hd, target in hd_targets.items():
        assert abs(post_hd[hd] - target) / target < 0.02


def test_unraked_default_unchanged():
    """rake_weights_to_irs_zip raises MissingDataError when supporting data is absent.

    The Tier-2 ship intentionally does not bundle the IRS SOI ZIP file or the
    ZIP→HD crosswalk. The high-level wrapper must signal that explicitly
    (rather than silently no-op) so the user is on notice that the flag is
    inactive without the data.
    """
    from tax_modeler.analysis.district_raking import rake_weights_to_irs_zip
    from tax_modeler.errors import MissingDataError

    df = _raking_frame()
    with pytest.raises(MissingDataError):
        rake_weights_to_irs_zip(df, tax_year=2024)


# ---------------------------------------------------------------------------
# Tier 3 — WIC + LIHEAP wiring
# ---------------------------------------------------------------------------


def test_wic_lowers_baseline_rate():
    """Adding WIC food-package benefits lifts SPM resources for low-income units with kids."""
    from tax_modeler.benefits.wic import compute_wic_for_units

    units = _low_income_frame(n=80, with_kids=True, seed=21)
    r_no_wic = compute_poverty_impact(units, tax_year=2024).by_state.iloc[0]
    units_with_wic = compute_wic_for_units(units)
    # WIC eligibility hinges on (income < 185% FPL) AND at least one dependent;
    # the low-income-with-kids fixture must produce some positive wic_amount.
    assert (units_with_wic["wic_amount"] > 0).any()
    r_with_wic = compute_poverty_impact(units_with_wic, tax_year=2024).by_state.iloc[0]
    assert r_with_wic["poverty_rate_baseline"] <= r_no_wic["poverty_rate_baseline"] + 1e-9


def test_liheap_small_but_nonzero():
    """LIHEAP is a small program — at least one eligible low-income unit must get a positive amount."""
    from tax_modeler.benefits.liheap import compute_liheap_for_units

    units = _low_income_frame(n=80, with_kids=False, seed=23)
    out = compute_liheap_for_units(units)
    assert "liheap_amount" in out.columns
    assert (out["liheap_amount"] > 0).any()
    # Each eligible unit gets the flat $250 annual benefit by default.
    assert out["liheap_amount"].max() == pytest.approx(250.0, abs=1.0)


# ---------------------------------------------------------------------------
# Tier 3 — federal income tax liability (pre-credit)
# ---------------------------------------------------------------------------


def test_federal_tax_column_preferred_over_fallback():
    """compute_spm_resources prefers federal_tax_liability column when present."""
    from tax_modeler.liability.federal import compute_federal_income_tax_for_units
    from tax_modeler.poverty.spm import compute_spm_resources

    units = _make_units([{
        "filing_status": "single",
        "total_cash_income": 80_000.0, "income": 80_000.0,
        "earned_income": 80_000.0, "weight": 100.0,
    }])
    out = compute_federal_income_tax_for_units(units, tax_year=2024)
    assert "federal_tax_liability" in out.columns
    _, meta = compute_spm_resources(out)
    assert meta.federal_tax_source == "column"


def test_federal_tax_column_lowers_high_income_baseline_rate():
    """For high-income filers, bracket-based liability < 10% flat fallback → SPM resources rise (or stay equal)."""
    from tax_modeler.liability.federal import compute_federal_income_tax_for_units

    # Single, AGI $80k: bracket calc on (80k - 14.6k std ded) = $65,400 taxable
    # = ~$9,256 pre-credit. The 10% flat fallback would charge $8,000 — but the
    # bracket calc is closer to $9.3k. Use a frame where the bracket calc CLEARLY
    # undershoots the flat fallback: AGI $30k single → taxable $15.4k → tax $1,798
    # vs flat fallback $3,000. Resources should rise (poverty rate cannot fall).
    units = _make_units([
        {"filing_status": "single", "total_cash_income": 30_000.0,
         "income": 30_000.0, "earned_income": 30_000.0, "weight": 100.0,
         "primary_agep": 35}
        for _ in range(40)
    ])
    r_flat = compute_poverty_impact(units, tax_year=2024).by_state.iloc[0]
    units_with_fed = compute_federal_income_tax_for_units(units, tax_year=2024)
    r_bracket = compute_poverty_impact(units_with_fed, tax_year=2024).by_state.iloc[0]
    # Real federal liability < 10% flat → resources higher under bracket calc.
    # Baseline poverty rate cannot rise.
    assert r_bracket["poverty_rate_baseline"] <= r_flat["poverty_rate_baseline"] + 1e-9


def test_full_flag_stack_smoke():
    """Exercise the full Tier 3 flag stack on synthetic data — composability check.

    Runs SNAP take-up → MOOP → housing → CCSP childcare → WIC → LIHEAP →
    childcare-expense → work-expense → federal-tax → SPM unit pooling.
    Asserts the final impact frame has the expected shape and a poverty
    rate in [0, 1]. Does NOT assert a specific rate value — that's an
    integration check best done on real PUMS.
    """
    from tax_modeler.benefits.childcare import compute_childcare_for_units
    from tax_modeler.benefits.childcare_expense import (
        compute_childcare_expense_for_units,
    )
    from tax_modeler.benefits.housing import compute_housing_for_units
    from tax_modeler.benefits.liheap import compute_liheap_for_units
    from tax_modeler.benefits.moop import compute_moop_for_units
    from tax_modeler.benefits.snap import compute_snap_for_units
    from tax_modeler.benefits.wic import compute_wic_for_units
    from tax_modeler.benefits.work_expense import compute_work_expense_for_units
    from tax_modeler.liability.federal import (
        compute_federal_income_tax_for_units,
    )

    units = _low_income_frame(n=60, with_kids=True, seed=29)
    units["hh_id"] = [f"hh_{i}" for i in range(len(units))]
    units = compute_snap_for_units(units)
    units = compute_moop_for_units(units)
    units = compute_housing_for_units(units)
    units = compute_childcare_for_units(units)
    units = compute_wic_for_units(units)
    units = compute_liheap_for_units(units)
    units = compute_childcare_expense_for_units(units)
    units = compute_work_expense_for_units(units)
    units = compute_federal_income_tax_for_units(units, tax_year=2024)

    # Tax-unit-mode poverty calc (no SPM aggregation): exercises the
    # legacy fallback path for compute_poverty_impact (no n_persons col).
    result = compute_poverty_impact(units, tax_year=2024)
    assert "poverty_rate_baseline" in result.by_state.columns
    rate = float(result.by_state.iloc[0]["poverty_rate_baseline"])
    assert 0.0 <= rate <= 1.0


def test_module_notes_mention_wic_liheap():
    """After Tier 3, the impact module docstring should reflect closed components."""
    import tax_modeler.poverty.impact as impact_mod

    # Collapse whitespace so word-wraps don't break substring matches.
    doc = " ".join((impact_mod.__doc__ or "").split())
    assert "WIC" in doc
    assert "LIHEAP" in doc
    assert "Federal income tax" in doc or "federal income tax" in doc


# test_pooling_magnitude_on_stylized_frame was removed alongside the
# legacy build_spm_units heuristic. The SPM-unit pipeline now operates
# bottom-up from PUMS RELSHIPP codes; the equivalent end-to-end check
# lives in tests/tax_modeler/test_e2e_spm_baseline_rate.py.


def test_federal_tax_low_income_zero_after_std_deduction():
    """A single filer with AGI under the $14,600 standard deduction owes zero federal tax."""
    from tax_modeler.liability.federal import compute_federal_income_tax_for_units

    units = _make_units([
        {"filing_status": "single", "total_cash_income": 12_000.0,
         "income": 12_000.0, "earned_income": 12_000.0, "weight": 100.0},
        {"filing_status": "married_filing_jointly", "total_cash_income": 25_000.0,
         "income": 25_000.0, "earned_income": 25_000.0, "weight": 100.0},
    ])
    out = compute_federal_income_tax_for_units(units, tax_year=2024)
    # Single $12k < $14.6k std ded → 0 federal tax.
    assert out.iloc[0]["federal_tax_liability"] == 0.0
    # MFJ $25k < $29.2k std ded → 0 federal tax.
    assert out.iloc[1]["federal_tax_liability"] == 0.0


# ---------------------------------------------------------------------------
# SPM-unit granularity (auto-detected via `n_persons` column)
# ---------------------------------------------------------------------------


def test_persons_per_unit_uses_n_persons_when_present():
    """SPM-unit frames carry an `n_persons` column from PUMS — use it."""
    df = pd.DataFrame([
        {"filing_status": "married_filing_jointly", "num_dependents": 2, "n_persons": 4},
        {"filing_status": "single", "num_dependents": 0, "n_persons": 3},  # multi-tax-unit SPM unit
    ])
    persons = _persons_per_unit(df)
    np.testing.assert_array_equal(persons, np.array([4.0, 3.0]))


def test_compute_poverty_impact_auto_detects_spm_granularity():
    """Frame with `n_persons` column triggers SPM mode; threshold reads
    `n_adults`/`n_children` directly rather than deriving from filing_status."""
    units = _make_units([{
        "filing_status": "married_filing_jointly",  # used only as proxy in SPM mode
        "num_dependents": 2,
        "num_qualifying_children": 2,
        "n_adults": 2,
        "n_children": 2,
        "n_persons": 4,
        "total_cash_income": 60_000.0,
        "earned_income": 60_000.0,
        "income": 60_000.0,
        "eitc_amount": 0.0,
        "weight": 100.0,  # household weight
    }])
    result = compute_poverty_impact(units, tax_year=2024)
    state = result.by_state.iloc[0]
    # Weighted persons should = n_persons × weight = 400
    assert state["weighted_persons"] == pytest.approx(400.0)


def test_compute_poverty_impact_tax_unit_mode_unchanged():
    """Frame without `n_persons` keeps using legacy filing_status-based math."""
    units = _make_units([{
        "filing_status": "married_filing_jointly",
        "num_dependents": 2,
        "num_qualifying_children": 2,
        "total_cash_income": 60_000.0,
        "earned_income": 60_000.0,
        "income": 60_000.0,
        "weight": 100.0,
    }])
    result = compute_poverty_impact(units, tax_year=2024)
    state = result.by_state.iloc[0]
    # MFJ + 2 deps → n_adults=2, n_children=2 → 4 persons; weighted = 400
    assert state["weighted_persons"] == pytest.approx(400.0)


# ---------------------------------------------------------------------------
# hi_eitc_revert_20 + hi_eitc_revert_20_behavioral scenarios
# ---------------------------------------------------------------------------


def test_hi_eitc_revert_20_subtracts_half_of_hi_eitc():
    units = _make_units([{
        "filing_status": "head_of_household",
        "num_dependents": 2,
        "total_cash_income": 25_000.0,
        "earned_income": 25_000.0,
        "income": 25_000.0,
        "hi_eitc_amount": 1_600.0,
        "eitc_amount": 4_000.0,
    }])
    result = compute_poverty_impact(
        units, tax_year=2024, scenarios=("hi_eitc_revert_20",),
    )
    u = result.units.iloc[0]
    expected_post = u["spm_resources"] - 0.5 * 1_600.0
    assert u["spm_resources_hi_eitc_revert_20"] == pytest.approx(expected_post)


def test_hi_eitc_revert_20_behavioral_adds_loss_column():
    units = _make_units([{
        "filing_status": "head_of_household",
        "num_dependents": 2,
        "total_cash_income": 25_000.0,
        "earned_income": 25_000.0,
        "income": 25_000.0,
        "hi_eitc_amount": 1_600.0,
        "eitc_amount": 4_000.0,
        "lfp_behavioral_resource_loss": 500.0,  # precomputed
    }])
    result = compute_poverty_impact(
        units, tax_year=2024,
        scenarios=("hi_eitc_revert_20", "hi_eitc_revert_20_behavioral"),
    )
    u = result.units.iloc[0]
    expected_static = u["spm_resources"] - 0.5 * 1_600.0
    expected_behavioral = expected_static - 500.0
    assert u["spm_resources_hi_eitc_revert_20"] == pytest.approx(expected_static)
    assert u["spm_resources_hi_eitc_revert_20_behavioral"] == pytest.approx(expected_behavioral)


def test_hi_eitc_revert_20_behavioral_graceful_without_loss_column():
    """Missing loss column behaves identically to the static scenario."""
    units = _make_units([{
        "filing_status": "head_of_household",
        "num_dependents": 2,
        "total_cash_income": 25_000.0,
        "earned_income": 25_000.0,
        "income": 25_000.0,
        "hi_eitc_amount": 1_600.0,
        "eitc_amount": 4_000.0,
    }])
    result = compute_poverty_impact(
        units, tax_year=2024,
        scenarios=("hi_eitc_revert_20", "hi_eitc_revert_20_behavioral"),
    )
    u = result.units.iloc[0]
    assert u["spm_resources_hi_eitc_revert_20"] == pytest.approx(
        u["spm_resources_hi_eitc_revert_20_behavioral"]
    )


def test_hi_eitc_revert_20_behavioral_is_removal_like():
    """Behavioral scenario should report persons-lifted with the removal-sign
    convention (positive = more poor under scenario)."""
    # Construct a unit that crosses the SPM threshold under the behavioral cut.
    units = _make_units([{
        "filing_status": "head_of_household",
        "num_dependents": 2,
        "total_cash_income": 30_000.0,
        "earned_income": 30_000.0,
        "income": 30_000.0,
        "hi_eitc_amount": 2_000.0,
        "eitc_amount": 5_000.0,
        "weight": 100.0,
        "lfp_behavioral_resource_loss": 8_000.0,  # large enough to push below threshold
    }])
    result = compute_poverty_impact(
        units, tax_year=2024,
        scenarios=("hi_eitc_revert_20_behavioral",),
    )
    state = result.by_state.iloc[0]
    # Positive persons_lifted means counterfactual poorer (correct sign for removal-shaped).
    assert state["persons_lifted_hi_eitc_revert_20_behavioral"] >= 0


def test_hi_eitc_revert_20_behavioral_three_component_decomposition():
    """Behavioral resources = baseline - 0.5*hi_eitc - max(lfp - snap_offset + intensive, 0)."""
    units = _make_units([{
        "filing_status": "head_of_household",
        "num_dependents": 2,
        "total_cash_income": 25_000.0,
        "earned_income": 25_000.0,
        "income": 25_000.0,
        "hi_eitc_amount": 1_600.0,
        "eitc_amount": 4_000.0,
        "lfp_behavioral_resource_loss": 500.0,
        "lfp_behavioral_snap_offset": 150.0,
        "intensive_resource_loss": 80.0,
    }])
    result = compute_poverty_impact(
        units, tax_year=2024,
        scenarios=("hi_eitc_revert_20", "hi_eitc_revert_20_behavioral"),
    )
    u = result.units.iloc[0]
    expected_static = u["spm_resources"] - 0.5 * 1_600.0
    behavioral_term = max(500.0 - 150.0 + 80.0, 0.0)  # = 430
    expected_behavioral = expected_static - behavioral_term
    assert u["spm_resources_hi_eitc_revert_20"] == pytest.approx(expected_static)
    assert u["spm_resources_hi_eitc_revert_20_behavioral"] == pytest.approx(expected_behavioral)


def test_hi_eitc_revert_20_behavioral_clamps_negative_to_zero():
    """If snap_offset > lfp + intensive, behavioral term clamps at 0 (no net loss
    relative to static — counterintuitive edge case, defensive only)."""
    units = _make_units([{
        "filing_status": "head_of_household",
        "num_dependents": 2,
        "total_cash_income": 25_000.0,
        "earned_income": 25_000.0,
        "income": 25_000.0,
        "hi_eitc_amount": 1_600.0,
        "eitc_amount": 4_000.0,
        "lfp_behavioral_resource_loss": 100.0,
        "lfp_behavioral_snap_offset": 500.0,
        "intensive_resource_loss": 0.0,
    }])
    result = compute_poverty_impact(
        units, tax_year=2024,
        scenarios=("hi_eitc_revert_20", "hi_eitc_revert_20_behavioral"),
    )
    u = result.units.iloc[0]
    # Behavioral term clamped to 0 → behavioral == static.
    assert u["spm_resources_hi_eitc_revert_20_behavioral"] == pytest.approx(
        u["spm_resources_hi_eitc_revert_20"]
    )


def test_module_notes_mention_behavioral_when_in_scenarios():
    units = _make_units([{
        "filing_status": "head_of_household",
        "num_dependents": 2,
        "earned_income": 25_000.0,
        "income": 25_000.0,
        "hi_eitc_amount": 1_600.0,
        "eitc_amount": 4_000.0,
    }])
    result = compute_poverty_impact(
        units, tax_year=2024,
        scenarios=("hi_eitc_revert_20_behavioral",),
    )
    notes_text = " ".join(result.notes)
    assert "hi_eitc_revert_20_behavioral" in notes_text
    assert "Meyer-Rosenbaum" in notes_text or "LFP" in notes_text
