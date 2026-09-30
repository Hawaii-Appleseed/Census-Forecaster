"""Tests for cbo_aging: per-component CBO Outlook filer aging."""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tax_modeler.calibration.cbo_aging import (
    CBO_COMPONENTS,
    DEFAULT_HAWAII_FACTORS,
    CBOComponentRates,
    _component_amounts_for_filer,
    age_filers_with_components,
    load_cbo_rates,
    transfers_outside_income,
)


@pytest.fixture(scope="module")
def cbo_csv_present(repo_data_dir: Path) -> Path:
    csv = repo_data_dir / "cbo" / "cbo_components_2025-01.csv"
    if not csv.exists():
        pytest.skip(f"CBO rates CSV not present at {csv}")
    return csv


@pytest.fixture(scope="module")
def cbo_rates(cbo_csv_present: Path) -> CBOComponentRates:
    return load_cbo_rates(vintage="2025-01", csv_path=cbo_csv_present)


def test_load_cbo_rates_has_all_components(cbo_rates: CBOComponentRates):
    expected_components = {"wages", "proprietors", "business", "capital_gains",
                           "dividends", "interest", "retirement", "other"}
    assert set(cbo_rates.factors.keys()) == expected_components


def test_cbo_base_year_factor_is_one(cbo_rates: CBOComponentRates):
    for component, years in cbo_rates.factors.items():
        assert abs(years[2022] - 1.0) < 1e-6, (
            f"{component} 2022 factor should be 1.0, got {years[2022]}"
        )


def test_cbo_factors_monotonically_increase(cbo_rates: CBOComponentRates):
    """Each component's growth factor should grow year-over-year (no negative)."""
    for component, years in cbo_rates.factors.items():
        ys = sorted(years.keys())
        for i in range(1, len(ys)):
            assert years[ys[i]] >= years[ys[i - 1]], (
                f"{component}: {ys[i - 1]}={years[ys[i - 1]]:.3f} > "
                f"{ys[i]}={years[ys[i]]:.3f}"
            )


def test_capital_gains_grows_faster_than_wages(cbo_rates: CBOComponentRates):
    """The CG-vs-wages differential is the whole reason for component aging."""
    cg_2027 = cbo_rates.factor("capital_gains", 2027)
    wages_2027 = cbo_rates.factor("wages", 2027)
    assert cg_2027 > wages_2027, (
        f"CG should grow faster than wages by 2027: CG={cg_2027:.3f}, "
        f"wages={wages_2027:.3f}"
    )


def _make_units(n: int = 200) -> pd.DataFrame:
    """Units whose ``income`` is built the way ``units.income`` builds it.

    ``income`` = WAGP + SEMP + INTP + DIV + RETP + OIP + 85% of Social Security
    (``primary_ssp``), with a 2% capital-gains slice. ``primary_ssp_full`` is
    the whole benefit; SSI and public assistance are in total cash income but
    not in ``income``. (This fixture used to count the full benefit inside
    ``income`` and carry no SSI or PAP, which is why the decomposition bug
    that inflated income by 1.5% at zero growth never showed.)
    """
    rng = np.random.default_rng(7)
    inc = rng.uniform(50_000, 250_000, n)
    return pd.DataFrame({
        "income": inc,
        "agi": inc,
        "weight": np.full(n, 50.0),
        "filing_status": rng.choice(
            ["single", "married_filing_jointly", "head_of_household"], n,
        ),
        "primary_wagp": inc * 0.69,
        "primary_oip": inc * 0.01,
        "primary_intp": inc * 0.05,
        "primary_div": inc * 0.03,
        "primary_retp": inc * 0.05,
        "primary_ssp": inc * 0.05,
        "primary_ssp_full": inc * 0.05 / 0.85,
        "primary_semp": inc * 0.10,
        "primary_ssip": np.where(rng.random(n) < 0.15, 4_000.0, 0.0),
        "primary_pap": np.where(rng.random(n) < 0.10, 2_500.0, 0.0),
        "synthetic_cg_share": np.full(n, 0.02),
    })


def test_age_filers_grows_aggregate_income(cbo_rates: CBOComponentRates):
    units = _make_units()
    pre = (units["income"] * units["weight"]).sum()
    # Test pure CBO aging without HI calibration — sanity-check the engine,
    # not the HI factor magnitudes (those have a separate test).
    aged = age_filers_with_components(
        units, target_year=2027, base_year=2022, cbo_rates=cbo_rates,
        hawaii_factors={c: 1.0 for c in CBO_COMPONENTS},
    )
    post = (aged["income"] * aged["weight"]).sum()
    # 5-year aging at CBO national rates (~4-5% wages, ~5% CG) should grow
    # aggregate by 22-30% on a wage-heavy filer mix.
    growth = post / pre - 1
    assert 0.20 < growth < 0.32, (
        f"5-year pure-CBO growth {growth:.1%} outside 20-32% sanity band"
    )


def test_per_component_columns_emitted(cbo_rates: CBOComponentRates):
    units = _make_units()
    aged = age_filers_with_components(units, target_year=2027, cbo_rates=cbo_rates)
    for comp in ("wages", "capital_gains", "business", "dividends",
                 "interest", "retirement", "other"):
        col = f"cbo_aged_{comp}"
        assert col in aged.columns, f"missing {col}"


def test_hawaii_factors_reduce_growth(cbo_rates: CBOComponentRates):
    units = _make_units()
    no_hi = age_filers_with_components(
        units, target_year=2027, cbo_rates=cbo_rates,
        hawaii_factors={c: 1.0 for c in CBO_COMPONENTS},
    )
    with_hi = age_filers_with_components(
        units, target_year=2027, cbo_rates=cbo_rates,
        hawaii_factors=DEFAULT_HAWAII_FACTORS,
    )
    no_hi_total = (no_hi["income"] * no_hi["weight"]).sum()
    with_hi_total = (with_hi["income"] * with_hi["weight"]).sum()
    # HI factors are mostly < 1, so HI-calibrated aging should produce less growth
    assert with_hi_total < no_hi_total, (
        f"HI calibration should reduce growth: no_hi={no_hi_total:.0f}, "
        f"with_hi={with_hi_total:.0f}"
    )


def test_base_year_rebases_cumulative_factors(cbo_rates: CBOComponentRates):
    """Regression (audit F1/R0): incomes already in a later dollar-year must be
    aged FROM that year. The cumulative-from-2022 CBO factors are re-based to
    base_year, so aging 2024-dollar income to 2027 grows ~3 years, not 5 — and
    strictly less than aging from 2022. Growing from 2022 double-counts the
    2022->2024 growth (~10% overstatement).
    """
    units = _make_units()
    hi_off = {c: 1.0 for c in CBO_COMPONENTS}
    from_2022 = age_filers_with_components(
        units, target_year=2027, base_year=2022, cbo_rates=cbo_rates,
        hawaii_factors=hi_off,
    )
    from_2024 = age_filers_with_components(
        units, target_year=2027, base_year=2024, cbo_rates=cbo_rates,
        hawaii_factors=hi_off,
    )
    t2022 = (from_2022["income"] * from_2022["weight"]).sum()
    t2024 = (from_2024["income"] * from_2024["weight"]).sum()
    assert t2024 < t2022, "2024-based aging must be smaller than 2022-based"
    # Wages: 2024->2027 factor = factor(2027)/factor(2024) = 1.252/1.100 = 1.138.
    wages_2027 = cbo_rates.factor("wages", 2027)
    wages_2024 = cbo_rates.factor("wages", 2024)
    expected = wages_2027 / wages_2024
    # _make_units is 70% wages; the aggregate ratio is dominated by but not equal
    # to the wage factor, so just bound the per-wage re-basing directly:
    one = pd.DataFrame({"income": [100_000.0], "agi": [100_000.0], "weight": [1.0],
                        "filing_status": ["single"], "primary_wagp": [100_000.0]})
    aged = age_filers_with_components(
        one, target_year=2027, base_year=2024, cbo_rates=cbo_rates,
        hawaii_factors=hi_off,
    )
    np.testing.assert_allclose(aged["income"].iloc[0] / 100_000.0, expected, rtol=1e-6)


def test_round_trip_to_base_year_preserves_income(cbo_rates: CBOComponentRates):
    units = _make_units()
    pre = (units["income"] * units["weight"]).sum()
    aged = age_filers_with_components(
        units, target_year=2022, base_year=2022, cbo_rates=cbo_rates,
        hawaii_factors={c: 1.0 for c in CBO_COMPONENTS},
    )
    post = (aged["income"] * aged["weight"]).sum()
    # Exact, not "close": the components sum to income (see the zero-growth
    # tests below), so a base-year round trip cannot move it.
    assert post == pytest.approx(pre, rel=1e-12)


def test_synthetic_cg_share_refreshed_after_aging(cbo_rates: CBOComponentRates):
    """After component aging, synthetic_cg_share must reflect post-aged composition.

    A filer starting at 60% CG / 40% wages will have a different CG share after
    CBO aging because CG and wages grow at different rates. The share should
    shift by more than 0.5pp over 9 years (2022→2031).
    """
    # One $5M filer: 60% CG, 40% wages; no PUMS columns so decomposition
    # falls back to synthetic_cg_share and synthetic_wages_share for CG/wages.
    df = pd.DataFrame({
        "income": [5_000_000.0],
        "agi": [5_000_000.0],
        "weight": [1.0],
        "filing_status": ["married_filing_jointly"],
        "synthetic_cg_share": [0.60],
        # No primary_wagp etc. — forces synthetic-share path.
    })
    aged = age_filers_with_components(
        df, target_year=2031, base_year=2022, cbo_rates=cbo_rates,
        hawaii_factors={c: 1.0 for c in CBO_COMPONENTS},
    )
    new_share = float(aged["synthetic_cg_share"].iloc[0])
    # CG and wages grow at different CBO rates → share must have moved.
    assert abs(new_share - 0.60) > 0.005, (
        f"synthetic_cg_share should change after 9-yr differential CBO aging, "
        f"but moved only {abs(new_share - 0.60):.4f} (pre=0.60, post={new_share:.4f})"
    )
    # Share must remain a valid fraction.
    assert 0.0 <= new_share <= 1.0, f"share out of range: {new_share}"


# ---------------------------------------------------------------------------
# The decomposition sums to ``income``
#
# Regression (2026-09-29 pipeline audit): retirement was RETP + 100% of Social
# Security + SSI + public assistance, while unit ``income`` holds 85% of
# Social Security and neither of the others. ``other`` is floored at zero, so
# the excess was never netted out and aging to the base year raised weighted
# income by ~1.5% ($61,659M -> $62,613M on the tax-unit cache, +2.0% on the
# calibrated base) and taxed SSI and TANF recipients in Hawaii.
# ---------------------------------------------------------------------------

_HI_OFF = {c: 1.0 for c in CBO_COMPONENTS}


def _synthetic_units() -> pd.DataFrame:
    """Top-income rows: no PUMS columns, components come from the shares."""
    return pd.DataFrame({
        "income": [5_000_000.0, 2_000_000.0],
        "agi": [5_000_000.0, 2_000_000.0],
        "weight": [1.0, 2.0],
        "filing_status": ["married_filing_jointly", "single"],
        "synthetic_cg_share": [0.60, 0.30],
        "synthetic_wages_share": [0.25, 0.50],
        "synthetic_business_share": [0.10, 0.10],
    })


def _only_the_85_percent_columns(df: pd.DataFrame) -> pd.DataFrame:
    return df.drop(columns=["primary_ssp_full"])


def _only_the_full_benefit_columns(df: pd.DataFrame) -> pd.DataFrame:
    return df.drop(columns=["primary_ssp"])


@pytest.mark.parametrize("build", [
    _make_units,
    lambda: _only_the_85_percent_columns(_make_units()),
    lambda: _only_the_full_benefit_columns(_make_units()),
    _synthetic_units,
], ids=["both-ss-columns", "ssp-only", "ssp_full-only", "synthetic-rows"])
def test_components_sum_to_income(build):
    units = build()
    total = sum(_component_amounts_for_filer(units).values())
    np.testing.assert_allclose(total, units["income"], rtol=1e-12)


@pytest.mark.parametrize("hawaii_factors", [_HI_OFF, DEFAULT_HAWAII_FACTORS],
                         ids=["national", "hawaii-calibrated"])
@pytest.mark.parametrize("build", [
    _make_units,
    lambda: _only_the_85_percent_columns(_make_units()),
    lambda: _only_the_full_benefit_columns(_make_units()),
    _synthetic_units,
], ids=["both-ss-columns", "ssp-only", "ssp_full-only", "synthetic-rows"])
def test_aging_to_the_base_year_leaves_every_units_income_unchanged(
        cbo_rates: CBOComponentRates, build, hawaii_factors):
    units = build()
    aged = age_filers_with_components(
        units, target_year=2024, base_year=2024, cbo_rates=cbo_rates,
        hawaii_factors=hawaii_factors,
    )
    np.testing.assert_allclose(aged["income"], units["income"], rtol=1e-12)


def test_ssi_and_public_assistance_never_enter_income(cbo_rates: CBOComponentRates):
    """A unit on SSI and TANF only has no ``income``; aging must not invent any,
    in the base year or after growth."""
    units = pd.DataFrame({
        "income": [0.0], "agi": [0.0], "weight": [1.0],
        "filing_status": ["single"],
        "primary_ssip": [9_000.0], "primary_pap": [3_000.0],
    })
    for target in (2024, 2029):
        aged = age_filers_with_components(
            units, target_year=target, base_year=2024, cbo_rates=cbo_rates,
            hawaii_factors=_HI_OFF,
        )
        assert aged["income"].iloc[0] == 0.0


def test_retirement_is_retp_plus_the_85_percent_of_social_security():
    df = pd.DataFrame({
        "income": [40_000.0],
        "primary_retp": [12_000.0], "secondary_retp": [1_000.0],
        "primary_ssp": [8_500.0], "primary_ssp_full": [10_000.0],
        "primary_ssip": [3_000.0], "primary_pap": [500.0],
    })
    ret = _component_amounts_for_filer(df)["retirement"].iloc[0]
    assert ret == pytest.approx(12_000.0 + 1_000.0 + 8_500.0)


def test_social_security_grows_on_the_85_percent_income_counts(
        cbo_rates: CBOComponentRates):
    """A retiree with only Social Security: income is 85% of the benefit and
    grows at the retirement rate on that 85%, not on the whole benefit."""
    units = pd.DataFrame({
        "income": [8_500.0], "agi": [8_500.0], "weight": [1.0],
        "filing_status": ["single"],
        "primary_ssp": [8_500.0], "primary_ssp_full": [10_000.0],
    })
    aged = age_filers_with_components(
        units, target_year=2027, base_year=2024, cbo_rates=cbo_rates,
        hawaii_factors=_HI_OFF,
    )
    f = cbo_rates.factor("retirement", 2027) / cbo_rates.factor("retirement", 2024)
    assert aged["income"].iloc[0] == pytest.approx(8_500.0 * f)


def test_transfers_outside_income_are_untaxed_ss_ssi_and_pap():
    df = pd.DataFrame({
        "primary_ssp": [8_500.0], "primary_ssp_full": [10_000.0],
        "primary_ssip": [3_000.0], "secondary_pap": [500.0],
    })
    want = 1_500.0 + 3_000.0 + 500.0
    assert transfers_outside_income(df).iloc[0] == pytest.approx(want)
    # Either Social Security column set alone recovers the same split.
    assert transfers_outside_income(
        df.drop(columns="primary_ssp")).iloc[0] == pytest.approx(want)
    assert transfers_outside_income(
        df.drop(columns="primary_ssp_full")).iloc[0] == pytest.approx(want)
    # ...and a frame with none of the columns has none.
    assert transfers_outside_income(pd.DataFrame({"income": [1.0]})).iloc[0] == 0.0


def test_a_decomposition_that_exceeds_income_is_flagged(
        cbo_rates: CBOComponentRates, caplog):
    """The ``other`` floor hides a component sum above income; say so."""
    bad = pd.DataFrame({
        "income": [50_000.0], "agi": [50_000.0], "weight": [1.0],
        "filing_status": ["single"], "primary_wagp": [60_000.0],
    })
    with caplog.at_level(logging.WARNING, logger="tax_modeler.calibration.cbo_aging"):
        age_filers_with_components(
            bad, target_year=2024, base_year=2024, cbo_rates=cbo_rates,
            hawaii_factors=_HI_OFF,
        )
    assert "aging will move income even at zero growth" in caplog.text

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="tax_modeler.calibration.cbo_aging"):
        age_filers_with_components(
            _make_units(), target_year=2024, base_year=2024, cbo_rates=cbo_rates,
            hawaii_factors=_HI_OFF,
        )
    assert "aging will move income" not in caplog.text
