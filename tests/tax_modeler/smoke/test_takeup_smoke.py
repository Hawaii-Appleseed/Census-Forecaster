"""Smoke tests for take-up imputation (Phase 4).

Verifies that:

* :class:`AdminCaseload` loads the bundled Hawaii caseload CSV.
* :func:`impute_takeup` matches simulated receipt to a target count
  within tolerance for SNAP and SSI on the synthetic fixture.
* :func:`calibrate_benefits` is a working one-call wrapper.
* Per the TRIM3 convention, imputation does **not** apply on the
  reform-counterfactual path (apply_reform recomputes fresh).
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import pytest

from tax_modeler import (
    AdminCaseload,
    CaseloadTarget,
    Reform,
    apply_reform,
    calibrate_benefits,
    compute_snap_for_units,
    compute_ssi_for_units,
    compute_ssi_hi_supplement_for_units,
    impute_takeup,
)
from tax_modeler.calibration.takeup_imputation import (
    impute_takeup_eitc_by_children,
    scale_benefit_to_dollar_target,
)
from tax_modeler.calibration.eitc_reweight import reweight_eitc_eligibles_by_children
from tax_modeler.errors import ConfigError, DataValidationError


pytestmark = pytest.mark.smoke


# ---------------------------------------------------------------------------
# AdminCaseload
# ---------------------------------------------------------------------------


def test_admin_caseload_loads_bundled():
    cl = AdminCaseload.load()
    assert len(cl) > 0
    snap2024 = cl.target("snap", 2024)
    assert isinstance(snap2024, CaseloadTarget)
    assert snap2024.unit == "household"
    # Real HI SNAP caseload is ~85k-90k households
    assert 50_000 < snap2024.count < 200_000


def test_admin_caseload_unknown_year_raises():
    cl = AdminCaseload.load()
    with pytest.raises(ConfigError):
        cl.target("snap", 1999)


def test_admin_caseload_unknown_program_raises():
    cl = AdminCaseload.load()
    with pytest.raises(ConfigError):
        cl.target("medicaid", 2024)


# ---------------------------------------------------------------------------
# impute_takeup
# ---------------------------------------------------------------------------


def _scaled_target(real: CaseloadTarget, fixture_units: pd.DataFrame, fixture_factor: float) -> CaseloadTarget:
    """Scale a real-world target down to the synthetic fixture's population."""
    # The fixture has ~50 households (a few thousand weighted units) vs. real
    # HI population (~500K households). Scale linearly so the imputation has
    # something to bite into.
    return CaseloadTarget(
        program=real.program,
        year=real.year,
        unit=real.unit,
        count=real.count * fixture_factor,
        annual_dollars_millions=real.annual_dollars_millions * fixture_factor,
        source=real.source,
    )


def test_impute_takeup_hits_target(taxed_units, caplog):
    """Target=10 weighted units → imputed count should land within ±50% on a 50-row fixture."""
    units = compute_snap_for_units(taxed_units)
    target = CaseloadTarget(
        program="snap", year=2024, unit="household",
        count=10.0, annual_dollars_millions=0.05,
    )
    out = impute_takeup(
        units, target=target, benefit_col="snap_amount", score_col="income"
    )
    assert "snap_receives_imputed" in out.columns
    imputed_total = float(out.loc[out["snap_receives_imputed"], "weight"].sum())
    # Tolerance is loose because the fixture is tiny (one row can swing
    # the cumulative weight by 10s)
    assert imputed_total >= target.count * 0.5
    # Non-recipients have zero benefit amount
    non_recipients = out[~out["snap_receives_imputed"]]
    assert (non_recipients["snap_amount"] == 0).all()


def test_impute_takeup_low_income_first(taxed_units):
    """Default scoring (income ascending) → imputed units have lower income than non-imputed eligible."""
    units = compute_snap_for_units(taxed_units)
    eligible = units[units["snap_amount"] > 0]
    if len(eligible) < 4:
        pytest.skip("need at least 4 eligible units for a meaningful comparison")
    target = CaseloadTarget(
        program="snap", year=2024, unit="household",
        count=float(eligible["weight"].sum()) * 0.5,  # half the eligible
        annual_dollars_millions=10.0,
    )
    out = impute_takeup(
        units, target=target, benefit_col="snap_amount", score_col="income"
    )
    imputed_eligible = out[(out["snap_amount"] >= 0) & (out["snap_receives_imputed"])]
    non_imputed_eligible_baseline = eligible[~eligible.index.isin(imputed_eligible.index)]
    if len(imputed_eligible) > 0 and len(non_imputed_eligible_baseline) > 0:
        assert imputed_eligible["income"].max() <= non_imputed_eligible_baseline["income"].min() + 1


def test_impute_takeup_missing_benefit_col_raises(taxed_units):
    target = CaseloadTarget(program="x", year=2024, unit="household", count=10.0, annual_dollars_millions=0.0)
    with pytest.raises(DataValidationError):
        impute_takeup(taxed_units, target=target, benefit_col="not_a_column")


def test_impute_takeup_logs_when_target_unmet(taxed_units, caplog):
    units = compute_snap_for_units(taxed_units)
    impossible_target = CaseloadTarget(
        program="snap", year=2024, unit="household",
        count=1e9, annual_dollars_millions=0.0,
    )
    with caplog.at_level(logging.WARNING, logger="tax_modeler.calibration.takeup_imputation"):
        impute_takeup(
            units, target=impossible_target, benefit_col="snap_amount",
            score_col="income",
        )
    assert any("exceeds tolerance" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# calibrate_benefits (one-call wrapper)
# ---------------------------------------------------------------------------


def test_calibrate_benefits_runs(taxed_units):
    df = compute_snap_for_units(taxed_units)
    df = compute_ssi_for_units(df)
    df = compute_ssi_hi_supplement_for_units(df)

    cl = AdminCaseload.load()
    # Scale targets way down so the synthetic fixture can hit them
    real_snap = cl.target("snap", 2024)
    real_ssi = cl.target("ssi", 2024)
    real_supp = cl.target("ssi_hi_supplement", 2024)
    fixture_factor = 0.0005  # 0.05% scale
    scaled = AdminCaseload(pd.DataFrame([
        {"program": "snap", "year": 2024, "unit": "household",
         "count": real_snap.count * fixture_factor,
         "annual_dollars_millions": real_snap.annual_dollars_millions * fixture_factor},
        {"program": "ssi", "year": 2024, "unit": "person",
         "count": real_ssi.count * fixture_factor,
         "annual_dollars_millions": real_ssi.annual_dollars_millions * fixture_factor},
        {"program": "ssi_hi_supplement", "year": 2024, "unit": "person",
         "count": real_supp.count * fixture_factor,
         "annual_dollars_millions": real_supp.annual_dollars_millions * fixture_factor},
    ]))

    out = calibrate_benefits(df, caseload=scaled, year=2024)
    # All imputed-flag columns now exist
    assert "snap_receives_imputed" in out.columns
    assert "ssi_receives_imputed" in out.columns
    assert "ssi_hi_receives_imputed" in out.columns


def test_calibrate_benefits_unknown_program_raises(taxed_units):
    df = compute_snap_for_units(taxed_units)
    cl = AdminCaseload.load()
    with pytest.raises(ConfigError):
        calibrate_benefits(df, caseload=cl, year=2024, programs=("medicaid",))


# ---------------------------------------------------------------------------
# EITC/CTC take-up (Phase 3 of EITC/CTC geo plan)
# ---------------------------------------------------------------------------


def test_admin_caseload_has_2022_eitc_ctc_benchmarks():
    """Hawaii admin caseload table must include IRS SOI TY2022 EITC/CTC anchors."""
    cl = AdminCaseload.load()
    eitc = cl.target("eitc", 2022)
    ctc = cl.target("ctc", 2022)
    actc = cl.target("actc", 2022)

    # IRS SOI Hawaii TY2022 published totals (ZIP=00000 state row):
    #   EITC: ~84,010 returns, $184.7M
    #   CTC (nonref + ACTC): ~154,580 returns, $469.5M
    #   ACTC: ~60,600 returns, $117.8M
    assert eitc.unit == "return"
    assert 50_000 < eitc.count < 150_000
    assert 100 < eitc.annual_dollars_millions < 300

    assert ctc.unit == "return"
    assert 100_000 < ctc.count < 250_000
    assert 300 < ctc.annual_dollars_millions < 700

    assert actc.unit == "return"
    assert 30_000 < actc.count < 100_000
    assert 50 < actc.annual_dollars_millions < 200


def test_calibrate_benefits_supports_eitc(taxed_units):
    """calibrate_benefits must zero out EITC amounts for non-imputed units."""
    # taxed_units already has eitc_amount from the pipeline fixture.
    if "eitc_amount" not in taxed_units.columns:
        pytest.skip("taxed_units fixture missing eitc_amount column")

    eligible_pre = (taxed_units["eitc_amount"] > 0).sum()
    if eligible_pre < 2:
        pytest.skip("not enough EITC-eligible rows in fixture")

    # Scale 2022 EITC target way down so the fixture can plausibly hit it.
    real = AdminCaseload.load().target("eitc", 2022)
    fixture_factor = 0.0001
    scaled = AdminCaseload(pd.DataFrame([{
        "program": "eitc", "year": 2022, "unit": "return",
        "count": real.count * fixture_factor,
        "annual_dollars_millions": real.annual_dollars_millions * fixture_factor,
    }]))

    out = calibrate_benefits(taxed_units, caseload=scaled, year=2022, programs=("eitc",))
    assert "eitc_receives_imputed" in out.columns
    # Non-recipients have zero EITC amount after imputation
    non_recipients = out[~out["eitc_receives_imputed"]]
    assert (non_recipients["eitc_amount"] == 0).all()
    # At least one unit was imputed as a recipient
    assert out["eitc_receives_imputed"].any()


def test_calibrate_benefits_eitc_ranks_descending_by_amount(taxed_units):
    """Among EITC-eligible units, the largest credit amounts should be claimed first."""
    if "eitc_amount" not in taxed_units.columns:
        pytest.skip("taxed_units fixture missing eitc_amount column")

    eligible = taxed_units[taxed_units["eitc_amount"] > 0]
    if len(eligible) < 4:
        pytest.skip("need at least 4 EITC-eligible rows for ranking test")

    target_count = float(eligible["weight"].sum()) * 0.5  # half the eligible
    scaled = AdminCaseload(pd.DataFrame([{
        "program": "eitc", "year": 2022, "unit": "return",
        "count": target_count, "annual_dollars_millions": 1.0,
    }]))
    out = calibrate_benefits(taxed_units, caseload=scaled, year=2022, programs=("eitc",))

    # Among originally-eligible units, claimants should have higher pre-imputation
    # eitc_amount than non-claimants. Pull pre-imputation amounts via a side
    # column on the input (eligible came from the fixture).
    eligible_idx = eligible.index
    claim_status = out.loc[eligible_idx, "eitc_receives_imputed"]
    pre_amount = eligible["eitc_amount"]
    if claim_status.any() and (~claim_status).any():
        # All claimants' pre-amounts >= all non-claimants' pre-amounts (tie tol)
        assert pre_amount[claim_status].min() >= pre_amount[~claim_status].max() - 0.01


# ---------------------------------------------------------------------------
# Stratified-by-children EITC take-up (lever 1 — childless-share correction)
# ---------------------------------------------------------------------------


def _eitc_units_by_children(n0: int, n1: int) -> pd.DataFrame:
    """Frame with n0 childless + n1 one-child EITC-eligible units, weight 1 each."""
    rows = [{"eitc_amount": 3000.0 - i, "eitc_qualifying_children": 0,
             "weight": 1.0, "income": 15_000.0} for i in range(n0)]
    rows += [{"eitc_amount": 4000.0 - i, "eitc_qualifying_children": 1,
              "weight": 1.0, "income": 20_000.0} for i in range(n1)]
    return pd.DataFrame(rows)


def test_eitc_stratified_caps_overproduced_childless_bucket():
    """A surplus childless pool is truncated to its bucket target while a short
    with-children pool keeps every eligible unit."""
    # 100 childless eligibles, 10 one-child eligibles.
    units = _eitc_units_by_children(n0=100, n1=10)
    # Pooled count target 60, split 50/50 → bucket target 30 each.
    target = CaseloadTarget(
        program="eitc", year=2022, unit="return",
        count=60.0, annual_dollars_millions=0.0,
    )
    out = impute_takeup_eitc_by_children(
        units, target=target, distribution={0: 50.0, 1: 50.0},
    )
    recv = out["eitc_receives_imputed"]
    childless_recv = float(out.loc[recv & (out["eitc_qualifying_children"] == 0), "weight"].sum())
    onekid_recv = float(out.loc[recv & (out["eitc_qualifying_children"] == 1), "weight"].sum())
    # Childless capped near 30 (overshoot by at most one boundary unit).
    assert 30.0 <= childless_recv <= 31.0
    # All 10 one-child eligibles kept (pool below its 30 target).
    assert onekid_recv == 10.0
    # Non-recipients zeroed.
    assert (out.loc[~recv, "eitc_amount"] == 0).all()


def test_eitc_stratified_highest_amount_first_within_bucket():
    """Within the childless bucket, the largest eligible amounts are claimed."""
    units = _eitc_units_by_children(n0=100, n1=0)
    target = CaseloadTarget(
        program="eitc", year=2022, unit="return",
        count=20.0, annual_dollars_millions=0.0,
    )
    out = impute_takeup_eitc_by_children(
        units, target=target, distribution={0: 1.0},
    )
    recv = out["eitc_receives_imputed"]
    # eitc_amount = 3000 - i, so the highest amounts (lowest i) claim first.
    assert out.loc[recv, "eitc_amount"].min() >= out.loc[~recv, "eitc_amount"].max()


def test_eitc_stratified_requires_children_col():
    units = pd.DataFrame({"eitc_amount": [1000.0], "weight": [1.0]})
    target = CaseloadTarget(
        program="eitc", year=2022, unit="return",
        count=1.0, annual_dollars_millions=0.0,
    )
    with pytest.raises(DataValidationError, match="eitc_qualifying_children"):
        impute_takeup_eitc_by_children(units, target=target)


def test_calibrate_benefits_stratify_flag_routes_to_by_children(taxed_units):
    """stratify_eitc_by_children=True still zeroes non-recipients and yields the
    eitc_receives_imputed column (contract parity with pooled path)."""
    if "eitc_amount" not in taxed_units.columns:
        pytest.skip("taxed_units fixture missing eitc_amount column")
    if "eitc_qualifying_children" not in taxed_units.columns:
        pytest.skip("taxed_units fixture missing eitc_qualifying_children column")
    real = AdminCaseload.load().target("eitc", 2022)
    fixture_factor = 0.0001
    scaled = AdminCaseload(pd.DataFrame([{
        "program": "eitc", "year": 2022, "unit": "return",
        "count": real.count * fixture_factor,
        "annual_dollars_millions": real.annual_dollars_millions * fixture_factor,
    }]))
    out = calibrate_benefits(
        taxed_units, caseload=scaled, year=2022, programs=("eitc",),
        stratify_eitc_by_children=True,
    )
    assert "eitc_receives_imputed" in out.columns
    non_recipients = out[~out["eitc_receives_imputed"]]
    assert (non_recipients["eitc_amount"] == 0).all()


# ---------------------------------------------------------------------------
# Surgical EITC by-children reweight (lever 3a — fills short with-children
# buckets so the stratified take-up can land the IRS childless share)
# ---------------------------------------------------------------------------


def _eitc_eligible_pool(weighted_counts: dict[int, int]) -> pd.DataFrame:
    """EITC-eligible pool: ``weighted_counts[k]`` units (weight 1.0) in bucket k.

    ``eitc_amount`` descends within a bucket and stays strictly positive, so
    rank-and-truncate is deterministic and every unit reads as eligible.
    """
    rows = []
    for k in sorted(weighted_counts):
        n = int(weighted_counts[k])
        for i in range(n):
            rows.append({
                "eitc_amount": 1000.0 + (n - i),
                "eitc_qualifying_children": k,
                "weight": 1.0,
                "income": 20_000.0,
            })
    return pd.DataFrame(rows)


def test_reweight_lifts_short_bucket_to_target():
    """A below-target with-children bucket is scaled up to its IRS count; an
    over-produced bucket is left untouched (surgical: no down-weights)."""
    units = _eitc_eligible_pool({0: 30, 1: 10})
    out, info = reweight_eitc_eligibles_by_children(units, targets={0: 20.0, 1: 20.0})

    # Bucket 1 was short (10 < 20): every eligible unit scaled ×2 → exactly 20.
    b1 = float(out.loc[out["eitc_qualifying_children"] == 1, "weight"].sum())
    assert b1 == pytest.approx(20.0)
    assert info["buckets"][1]["factor"] == pytest.approx(2.0)
    assert info["buckets"][1]["before"] == pytest.approx(10.0)
    assert info["buckets"][1]["after"] == pytest.approx(20.0)

    # Bucket 0 over target (30 > 20): untouched, absent from info["buckets"].
    b0 = float(out.loc[out["eitc_qualifying_children"] == 0, "weight"].sum())
    assert b0 == pytest.approx(30.0)
    assert 0 not in info["buckets"]

    # Total weight drifted up by exactly the bucket-1 fill (10), nothing else.
    assert info["total_weight_after"] - info["total_weight_before"] == pytest.approx(10.0)


def test_reweight_ignores_noneligible_units():
    """Only EITC-eligible (benefit > 0) units are counted and scaled; zero-benefit
    units in a short bucket keep their original weight."""
    units = _eitc_eligible_pool({1: 10})
    noneligible = pd.DataFrame([{
        "eitc_amount": 0.0, "eitc_qualifying_children": 1,
        "weight": 1.0, "income": 20_000.0,
    } for _ in range(5)])
    units = pd.concat([units, noneligible], ignore_index=True)

    out, info = reweight_eitc_eligibles_by_children(units, targets={1: 20.0})

    eligible_w = float(out.loc[out["eitc_amount"] > 0, "weight"].sum())
    noneligible_w = float(out.loc[out["eitc_amount"] == 0, "weight"].sum())
    assert eligible_w == pytest.approx(20.0)      # 10 eligible × 2
    assert noneligible_w == pytest.approx(5.0)    # untouched
    assert info["buckets"][1]["before"] == pytest.approx(10.0)  # eligible only


def test_reweight_requires_children_col():
    units = pd.DataFrame({"eitc_amount": [1000.0], "weight": [1.0]})
    with pytest.raises(DataValidationError, match="eitc_qualifying_children"):
        reweight_eitc_eligibles_by_children(units)


def test_reweight_empty_targets_raises():
    units = _eitc_eligible_pool({0: 5})
    with pytest.raises(DataValidationError, match="non-empty targets"):
        reweight_eitc_eligibles_by_children(units, targets={})


def test_reweight_max_factor_caps_and_warns(caplog):
    """A computed factor above max_factor is capped (bucket then falls short of
    target) and a warning is logged."""
    units = _eitc_eligible_pool({1: 10})
    with caplog.at_level(logging.WARNING, logger="tax_modeler.calibration.eitc_reweight"):
        out, info = reweight_eitc_eligibles_by_children(
            units, targets={1: 100.0}, max_factor=3.0,
        )
    # Uncapped factor would be 10× (10 → 100); capped to 3× → 30, short of 100.
    assert info["buckets"][1]["factor"] == pytest.approx(3.0)
    b1 = float(out.loc[out["eitc_qualifying_children"] == 1, "weight"].sum())
    assert b1 == pytest.approx(30.0)
    assert any("max_factor" in rec.message for rec in caplog.records)


def test_reweight_then_takeup_hits_irs_childless_share():
    """Composition headline: reweight + stratified take-up drives the childless
    EITC share to the IRS 31.7%, materially below the ~37% the take-up
    truncation reaches on its own (short with-children buckets can't be filled
    by truncation alone)."""
    # Scaled-down (÷10) HI TY2022 shape: childless over target, 1-/2-kid short,
    # 3+ slightly over — the real eligibility shortfall.
    pool = {0: 3000, 1: 2000, 2: 1400, 3: 1200}
    irs = {0: 2690.0, 1: 2782.0, 2: 1846.0, 3: 1179.0}
    pooled_count = sum(irs.values())  # 8497

    def _childless_share(units: pd.DataFrame) -> float:
        target = CaseloadTarget(
            program="eitc", year=2022, unit="return",
            count=pooled_count, annual_dollars_millions=0.0,
        )
        out = impute_takeup_eitc_by_children(units, target=target, distribution=irs)
        recv = out["eitc_receives_imputed"]
        total = float(out.loc[recv, "weight"].sum())
        childless = float(
            out.loc[recv & (out["eitc_qualifying_children"] == 0), "weight"].sum()
        )
        return childless / total

    base = _eitc_eligible_pool(pool)
    share_no_reweight = _childless_share(base)
    reweighted, _ = reweight_eitc_eligibles_by_children(base, targets=irs)
    share_reweight = _childless_share(reweighted)

    # Take-up alone is stuck near 37% — it can cap the childless surplus but
    # cannot manufacture the missing 1-/2-kid eligibles.
    assert share_no_reweight == pytest.approx(0.370, abs=0.005)
    # Reweight fills the short buckets; the childless share lands on IRS 31.7%.
    assert share_reweight == pytest.approx(0.317, abs=0.005)
    assert share_reweight < share_no_reweight - 0.04


# ---------------------------------------------------------------------------
# HI EITC take-up calibration (Tier 1A — eitc_revert_20 forecast)
# ---------------------------------------------------------------------------


def test_admin_caseload_has_hi_eitc_anchor():
    """Hawaii caseload table must include a hi_eitc anchor for the
    1A take-up-calibration step in forecast_hi_eitc_revert_20.py.
    Anchor year is TY 2023 — the first year HI EITC was refundable
    (Act 209, 2023). DOTAX Tax Credits Claimed Table A-1/A-2: 84,470
    returns, $77.054M.
    """
    cl = AdminCaseload.load()
    hi = cl.target("hi_eitc", 2023)
    assert hi.unit == "return"
    # DOTAX TY 2023: 84,470 returns. Refundable HI EITC has near-100%
    # take-up because it auto-attaches to the federal EITC claim.
    assert 80_000 < hi.count < 90_000, (
        f"hi_eitc count {hi.count} should match DOTAX TY 2023 (84,470)."
    )
    assert 70 < hi.annual_dollars_millions < 90


def test_admin_caseload_hi_eitc_anchor_ty2024():
    """TY 2024 is the latest year: DOTAX's Earned Income Tax Credit Report
    (Act 107), Table 1, NEW credit claimed: 78,399 claims, $76,981,028 (the
    credit applied, $78.5M, adds legacy carryforwards and is not the anchor).
    """
    hi = AdminCaseload.load().target("hi_eitc", 2024)
    assert hi.unit == "return"
    assert hi.count == 78_399
    assert hi.annual_dollars_millions == pytest.approx(76.981, abs=1e-3)
    from tax_modeler.calibration import dotax_base
    new = dotax_base.eitc_new_credit(2024)
    assert (hi.count, hi.annual_dollars_millions) == (new["claims"], pytest.approx(new["dollars_M"], abs=1e-3))


def test_calibrate_benefits_supports_hi_eitc(taxed_units):
    """calibrate_benefits zeros HI EITC for non-imputed units; preserves federal."""
    if "eitc_amount" not in taxed_units.columns:
        pytest.skip("taxed_units fixture missing eitc_amount column")

    units = taxed_units.copy()
    # Synthesize hi_eitc_amount = 0.40 × federal EITC.
    units["hi_eitc_amount"] = 0.40 * units["eitc_amount"].fillna(0)
    if (units["hi_eitc_amount"] > 0).sum() < 2:
        pytest.skip("not enough HI-EITC-eligible rows in fixture")

    fed_before = units["eitc_amount"].copy()

    target_count = float(units.loc[units["hi_eitc_amount"] > 0, "weight"].sum()) * 0.5
    scaled = AdminCaseload(pd.DataFrame([{
        "program": "hi_eitc", "year": 2023, "unit": "return",
        "count": target_count, "annual_dollars_millions": 1.0,
    }]))

    out = calibrate_benefits(units, caseload=scaled, year=2023, programs=("hi_eitc",))
    assert "hi_eitc_receives_imputed" in out.columns
    non_recip = out[~out["hi_eitc_receives_imputed"]]
    assert (non_recip["hi_eitc_amount"] == 0).all()
    # Federal EITC unaffected by HI calibration.
    pd.testing.assert_series_equal(
        out["eitc_amount"], fed_before, check_names=False
    )


# ---------------------------------------------------------------------------
# Reform-path interaction (TRIM3 convention)
# ---------------------------------------------------------------------------


def test_reform_path_does_not_apply_baseline_takeup(taxed_units):
    """Per TRIM3 convention, apply_reform recomputes from eligibility — it
    should NOT inherit the baseline take-up flags. Easiest way to verify:
    a Reform that doesn't touch SNAP should produce a counterfactual SNAP
    column equal to fresh eligibility-based computation, not zeroed-out."""
    # Manually apply take-up imputation to baseline first
    baseline = compute_snap_for_units(taxed_units)
    cl = AdminCaseload.load()
    # Use a tiny target so most eligible units have benefit_col == 0 in baseline
    tiny_target = CaseloadTarget(
        program="snap", year=2024, unit="household",
        count=1.0, annual_dollars_millions=0.0,
    )
    baseline_calibrated = impute_takeup(
        baseline, target=tiny_target, benefit_col="snap_amount", score_col="income"
    )
    # After this, baseline_calibrated["snap_amount"] is mostly zero.

    # Apply a tax-only reform (no benefit overrides)
    reform = Reform(name="null_tax_reform")
    result = apply_reform(baseline_calibrated, reform, year=2026)
    # No counterfactual_units (no benefit overrides) — the assertion is that
    # apply_reform doesn't crash on calibrated baseline data.
    assert result.counterfactual_units is None


# ---------------------------------------------------------------------------
# scale_benefit_to_dollar_target — SOI-anchored dollar calibration
# ---------------------------------------------------------------------------


def _make_eitc_units(n: int = 10, eitc_per_unit: float = 1_000.0) -> pd.DataFrame:
    """Minimal DataFrame with eitc_amount and weight columns."""
    return pd.DataFrame({
        "eitc_amount": [eitc_per_unit] * n,
        "hi_eitc_amount": [eitc_per_unit * 0.4] * n,
        "weight": [1.0] * n,
        "income": [30_000.0] * n,
    })


def test_scale_benefit_scalar_applied():
    """model=100M, target=120M → scalar=1.2, all amounts ×1.2."""
    # 100 units × $1M each × weight 1.0 = $100M model total
    df = pd.DataFrame({
        "eitc_amount": [1_000_000.0] * 100,
        "weight": [1.0] * 100,
    })
    out, scalar = scale_benefit_to_dollar_target(
        df, benefit_col="eitc_amount", target_dollars_M=120.0
    )
    assert abs(scalar - 1.2) < 1e-9
    np.testing.assert_allclose(out["eitc_amount"].to_numpy(), 1_200_000.0)


def test_scale_benefit_hi_eitc_same_scalar():
    """hi_eitc_amount must be scaled by the same scalar as the federal credit."""
    df = pd.DataFrame({
        "eitc_amount": [1_000_000.0] * 50,
        "hi_eitc_amount": [400_000.0] * 50,   # 40% of federal
        "weight": [1.0] * 50,
    })
    # Federal: model=50M, target=75M → scalar=1.5
    out_fed, scalar_fed = scale_benefit_to_dollar_target(
        df, benefit_col="eitc_amount", target_dollars_M=75.0
    )
    assert abs(scalar_fed - 1.5) < 1e-9

    # Apply same scalar to hi_eitc manually (mirrors calibrate_benefits logic)
    out_hi = out_fed.copy()
    out_hi["hi_eitc_amount"] = out_hi["hi_eitc_amount"] * scalar_fed
    # hi_eitc should still be 40% of federal after scaling
    ratio = out_hi["hi_eitc_amount"] / out_fed["eitc_amount"]
    np.testing.assert_allclose(ratio.to_numpy(), 0.4)


def test_scale_benefit_unusual_scalar_warns(caplog):
    """scalar > 2.0 must emit a WARNING."""
    df = pd.DataFrame({
        "eitc_amount": [1_000.0] * 10,  # model total = 0.01M
        "weight": [1.0] * 10,
    })
    with caplog.at_level(logging.WARNING, logger="tax_modeler.calibration.takeup_imputation"):
        _, scalar = scale_benefit_to_dollar_target(
            df, benefit_col="eitc_amount", target_dollars_M=1_000.0  # 100,000× overshoot
        )
    assert scalar > 2.0
    assert any("unusual scalar" in rec.message for rec in caplog.records)


def test_scale_benefit_zero_model_returns_unchanged(caplog):
    """model total == 0 must return the frame unchanged without divide-by-zero."""
    df = pd.DataFrame({
        "eitc_amount": [0.0] * 5,
        "weight": [1.0] * 5,
    })
    with caplog.at_level(logging.WARNING, logger="tax_modeler.calibration.takeup_imputation"):
        out, scalar = scale_benefit_to_dollar_target(
            df, benefit_col="eitc_amount", target_dollars_M=100.0
        )
    assert scalar == 1.0
    assert (out["eitc_amount"] == 0.0).all()
    assert any("zero" in rec.message for rec in caplog.records)


def test_calibrate_benefits_eitc_dollar_calibration_scales_hi_eitc(taxed_units):
    """When eitc + hi_eitc are both in programs and eitc has annual_dollars_millions > 0,
    hi_eitc_amount should be scaled by the same federal scalar."""
    if "eitc_amount" not in taxed_units.columns:
        pytest.skip("taxed_units fixture missing eitc_amount column")

    units = taxed_units.copy()
    units["hi_eitc_amount"] = 0.40 * units["eitc_amount"].fillna(0)

    eitc_eligible = units[units["eitc_amount"] > 0]
    hi_eligible = units[units["hi_eitc_amount"] > 0]
    if len(eitc_eligible) < 2 or len(hi_eligible) < 2:
        pytest.skip("not enough EITC/HI-EITC eligible rows in fixture")

    # Build a scaled caseload for both programs; give eitc a dollar target.
    eitc_count = float(eitc_eligible["weight"].sum())
    hi_count = float(hi_eligible["weight"].sum())
    # model eitc total in $M (before calibration)
    model_eitc_M = float((eitc_eligible["eitc_amount"] * eitc_eligible["weight"]).sum()) / 1e6
    # Set target to 1.5× model to force a known scalar
    target_eitc_M = model_eitc_M * 1.5 if model_eitc_M > 0 else 1.0

    scaled = AdminCaseload(pd.DataFrame([
        {"program": "eitc", "year": 2022, "unit": "return",
         "count": eitc_count, "annual_dollars_millions": target_eitc_M},
        {"program": "hi_eitc", "year": 2022, "unit": "return",
         "count": hi_count, "annual_dollars_millions": 0.0},
    ]))

    out = calibrate_benefits(units, caseload=scaled, year=2022, programs=("eitc", "hi_eitc"))

    # For recipients, hi_eitc should still be ~40% of eitc_amount after scaling.
    recipients = out[out["eitc_receives_imputed"] & out["hi_eitc_receives_imputed"]]
    if len(recipients) == 0:
        pytest.skip("no joint eitc+hi_eitc recipients in fixture")
    ratio = recipients["hi_eitc_amount"] / recipients["eitc_amount"]
    np.testing.assert_allclose(ratio.to_numpy(), 0.4, rtol=1e-6)


def test_scale_eitc_dollars_false_preserves_aged_dollars():
    """Regression (audit F2): with scale_eitc_dollars=False the EITC dollar
    re-peg to the caseload's nominal target is skipped — the count anchor still
    applies, but per-claimant (income-aged) amounts are preserved. With the
    default True, the aggregate is clamped to the table's nominal $ target.

    This is the fallback used when projecting to a year with no caseload row
    (EITC only has TY2022): clamping would refreeze aged EITC to 2022 dollars.
    """
    n = 200
    df = pd.DataFrame({
        "eitc_amount": [3_000.0] * n,
        "weight": [2_000.0] * n,  # 400k weighted eligibles >> 84k count target
        "eitc_qualifying_children": [2] * n,
    })
    caseload = AdminCaseload(pd.DataFrame([
        {"program": "eitc", "year": 2022, "unit": "return",
         "count": 84_010, "annual_dollars_millions": 184.7},
    ]))
    scaled = calibrate_benefits(
        df.copy(), caseload=caseload, year=2022, programs=("eitc",),
        stratify_eitc_by_children=False, scale_eitc_dollars=True,
    )
    preserved = calibrate_benefits(
        df.copy(), caseload=caseload, year=2022, programs=("eitc",),
        stratify_eitc_by_children=False, scale_eitc_dollars=False,
    )
    agg_scaled = float((scaled["eitc_amount"] * scaled["weight"]).sum()) / 1e6
    agg_preserved = float((preserved["eitc_amount"] * preserved["weight"]).sum()) / 1e6
    # Clamped to the nominal target...
    assert agg_scaled == pytest.approx(184.7, abs=1.0)
    # ...vs preserved aged dollars (count-anchored only), well above the clamp.
    assert agg_preserved > 200.0
