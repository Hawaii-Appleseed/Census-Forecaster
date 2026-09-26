"""
Per-filer distributional analysis: Act 46 vs SB 3125 CD1.

Computes bracket tax change and REEC credit savings impact per filer,
bins into income quintiles (weight-cumulative, all filing statuses combined)
and fixed income brackets, and returns summary DataFrames.

Credit distribution methodology (attribute_credit_loss):
  A credit cut falls on the filers who claim the credit, not on everyone in
  their income class. Each tax unit gets a claim probability for the
  renewable energy technologies credit (REEC) and the capital goods excise
  tax credit (CGEC) equal to its AGI class's DOTAX TY2023 claim rate (Table
  A-6 claims / Table 2 returns, 0.4-3.6% for REEC), and a loss if it claims:
  its class's average claim (A-5 / A-6), times the share the bill takes away
  (all of it for AGI-ineligible claimants and after the sunset; 1 minus the
  retained pro-rata share for eligible claimants in capped years). Losses are
  scaled so they sum to the individual-return savings the credit overlay
  reports for the year.

  Averages and totals use the expected loss (claim probability x loss if
  claiming), which is exact in expectation. Pay-more / pay-less shares treat
  each household as a claimant household with probability q (tax change =
  bracket change + its loss if claiming) and otherwise not (bracket change
  only), so a household that claims nothing is never counted as paying more.

  Corporate / fiduciary REEC and CGEC savings, and TCRA (individual claims
  ~$1M, mostly suppressed in the DOTAX tables), are not attributed to
  households.

  Timing approximation: in years after capped vintages, part of the
  vintage-model loss is reduced carryforward drawdown from earlier capped
  certificates; it is attributed to that year's would-be claimants.
"""

from __future__ import annotations

import csv
import warnings
from functools import cache
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

# DOTAX claim counts / amounts / returns by AGI class, from
# scripts/fetch_dotax_credit_claims.py. The claim-rate year matches _REEC_BINS.
_CLAIMS_CSV = Path(__file__).resolve().parent.parent / "data" / "raw" / "dotax_credit_claims_by_agi.csv"
CLAIM_PROFILE_YEAR = 2023

# DOTAX TY2023 individual REEC by AGI bin (label, total_$M, eligible_share)
# Source: DOTAX "Tax Credits Claimed by Hawaiʻi Taxpayers TY2023", Table A-5
_REEC_BINS = [
    ("<$10K",        4.731, 1.000),
    ("$10K-$30K",    2.522, 1.000),
    ("$30K-$60K",    3.121, 1.000),
    ("$60K-$100K",   5.752, 1.000),
    ("$100K-$200K", 16.150, 0.972),
    ("$200K+",      26.018, 0.561),
]
# Breakpoints (lower bound of each bin in AGI, open-right except last)
_BIN_BREAKPOINTS = [0, 10_000, 30_000, 60_000, 100_000, 200_000]


def _agi_bin_index(agi_array: np.ndarray) -> np.ndarray:
    """Return 0-based bin index for each filer (matches _REEC_BINS order)."""
    idx = np.zeros(len(agi_array), dtype=int)
    for i, bp in enumerate(_BIN_BREAKPOINTS[1:], start=1):
        idx[agi_array >= bp] = i
    return idx


def per_unit_tax(
    tax_units: pd.DataFrame,
    config,
    calc,
) -> np.ndarray:
    """Return per-filer net Hawaii state tax (after credits) under *config*.

    Per-filer effective deduction is always
    ``max(config.standard_deduction_year_SD_per_fs, hi_itemized_deduction)``.
    The raw itemized column is populated by ``calculate_hawaii_tax`` upstream
    (when ``deduction_params`` was supplied during projection) — see
    ``project_tax_units_forward`` and ``year_recalibrator.project_and_recalibrate``.

    Note on per-scenario itemization
    --------------------------------
    The SD-vs-itemize choice is computed per scenario via the ``max`` above
    (correctly handling SD-changing bills). However, the underlying
    ``hi_itemized_deduction`` *amount* is populated once during projection
    using a single set of ``deduction_params`` (typically scaled to the
    target year). For comparisons against frozen-law counterfactuals (e.g.
    "Act 46 frozen at TY2026" baselines), callers should re-populate
    ``hi_itemized_deduction`` per scenario with year-appropriate
    ``scale_deduction_params_for_target_year(year)`` — frozen baselines use
    TY2026 mortgage/SALT/charitable scales, projected scenarios use target-year
    scales. See ``forecast_sb3125_cd1_vs_fy26base.py`` for the pattern.

    If ``hi_itemized_deduction`` is missing (legacy data, pre-Act-46 backtests),
    falls back to scenario SD only; a NaN in it raises (see
    ``tax_system_config.itemized_deductions``). There is no longer a
    ``deduction_col`` override — that parameter previously caused silent bugs
    by forcing both baseline and bill scenarios to share a pre-computed
    effective-deduction column, hiding scenario-specific SD changes (e.g.
    HB 2306 ORIG SD freeze).

    This is ``TaxCalculator.unit_liabilities(...)["net"]``, the same per-unit
    tax ``compare_systems`` sums, so per-unit totals reproduce its revenue.
    Until September 2026 this function kept its own copy of that math, which
    read a ``"total_credits"`` key the credit calculator never returns: every
    distributional table was scored before credits.

    Parameters
    ----------
    tax_units:
        Projected tax units DataFrame.
    config:
        TaxSystemConfig (Act 46, SB 3125 CD1, HB 2306 ORIG, etc.).
    calc:
        TaxCalculator instance.

    Returns
    -------
    np.ndarray of shape (n,) — per-filer net liability after credits;
    negative where refundable credits exceed the tax, as in ``compare_systems``.
    """
    return calc.unit_liabilities(tax_units, config)["net"]


def reec_individual_credit_loss(
    target_year: int,
    reec_demand_scenario: str,
    reec_effective_claim_share: float,
    corp_subject_to_agi_limit: bool,
    cgec_annual_growth: float,
    reec_carryforward_utilization_m: float,
) -> tuple[float, float, float]:
    """Return (total_individual_savings_$M, ineligible_$M, eligible_cap_reduction_$M).

    These are the three components of credit loss borne by individual filers:
    - total_individual_savings: combined individual burden from CD1 REEC changes
    - ineligible: filers above AGI limit lose entire claim
    - eligible_cap_reduction: pro-rata cap reduction for eligible filers
    """
    from tax_modeler.scenarios.sb3125_cd1_credits import (
        _reec_baseline_M, REEC_CAP_2027_2030_M, REEC_CAP_2031_PLUS_M,
    )

    reec = _reec_baseline_M(
        target_year,
        demand_scenario=reec_demand_scenario,
        corp_subject_to_agi_limit=corp_subject_to_agi_limit,
        effective_claim_share=reec_effective_claim_share,
        cgec_annual_growth=cgec_annual_growth,
    )

    ind_ineligible = reec["individual_ineligible"]
    ind_eligible   = reec["individual_eligible"]
    total_eligible = reec["total_eligible"]

    if 2027 <= target_year <= 2030:
        cap = REEC_CAP_2027_2030_M
        cap_excess = max(0.0, total_eligible - cap)
        # Individual share of cap excess = pro-rata by eligible claims
        if total_eligible > 0:
            ind_cap_reduction = (ind_eligible / total_eligible) * cap_excess
        else:
            ind_cap_reduction = 0.0
    elif target_year >= 2031:
        # No new claims allowed; carryforward offsets are corporate-dominated
        cap_excess = 0.0
        ind_cap_reduction = 0.0
        ind_ineligible = 0.0  # AGI limit moot when cap = $0
    else:
        cap_excess = 0.0
        ind_cap_reduction = 0.0
        ind_ineligible = 0.0

    total_ind_savings = ind_ineligible + ind_cap_reduction
    return total_ind_savings, ind_ineligible, ind_cap_reduction


@cache
def _claim_profile(credit: str, year: int = CLAIM_PROFILE_YEAR) -> tuple[np.ndarray, np.ndarray]:
    """(claim rate, average claim in $) per _REEC_BINS AGI class for *credit*.

    Claim rate = individual-return claims (DOTAX Table A-6) / individual
    returns (Table 2); average claim = dollars claimed (A-5) / claims.
    """
    rows = [r for r in csv.DictReader(_CLAIMS_CSV.open())
            if r["credit"] == credit and int(r["year"]) == year]
    if len(rows) != len(_REEC_BINS):
        raise ValueError(f"{_CLAIMS_CSV.name}: expected {len(_REEC_BINS)} {credit} rows for TY{year}")
    claims = np.array([float(r["claims"] or 0) for r in rows])
    amount = np.array([float(r["amount_$K"] or 0) * 1e3 for r in rows])
    returns = np.array([float(r["returns"]) for r in rows])
    rate = claims / returns
    avg = np.divide(amount, claims, out=np.zeros_like(amount), where=claims > 0)
    return rate, avg


def attribute_credit_loss(
    tax_units: pd.DataFrame,
    *,
    reec_individual_m: float,
    reec_retained_share: float,
    cgec_individual_m: float = 0.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Attribute individual-return credit savings to imputed claimants.

    Returns ``(expected_loss, claim_prob)`` per tax unit, in dollars and as a
    probability. ``expected_loss`` sums (with ``weight``) to
    ``reec_individual_m + cgec_individual_m`` million; ``claim_prob`` is the
    chance the unit claims REEC or CGEC. See the module docstring.

    Parameters
    ----------
    reec_individual_m:
        REEC savings on individual returns this year ($M), e.g. the credit
        overlay's ``reec_individual_savings_$M``.
    reec_retained_share:
        Share of an AGI-eligible claimant's credit the bill leaves in place
        (pro-rata cap x demand suppression; 0 once new credits are sunset).
        AGI-ineligible claimants lose everything.
    cgec_individual_m:
        CGEC savings on individual returns this year ($M).
    """
    n = len(tax_units)
    incomes = tax_units["income"].to_numpy(dtype=float)
    weights = tax_units["weight"].to_numpy(dtype=float)
    statuses = tax_units["filing_status"].to_numpy()
    b = _agi_bin_index(incomes)

    # §235-12.5(a) as amended: $350K joint, $175K otherwise.
    threshold = np.where(np.isin(statuses, ["married_filing_jointly", "qualifying_widow"]),
                         350_000.0, 175_000.0)
    lost_share = np.where(incomes > threshold, 1.0, 1.0 - float(reec_retained_share))

    def _scaled(prob, intensity, total_m):
        denom = float((weights * prob * intensity).sum())
        if total_m <= 0 or denom <= 0:
            return np.zeros(n)
        return intensity * (total_m * 1e6 / denom)

    p_r, avg_r = _claim_profile("reec")
    p_c, avg_c = _claim_profile("cgec")
    pr, pc = p_r[b], p_c[b]
    loss_r = _scaled(pr, avg_r[b] * lost_share, reec_individual_m)
    loss_c = _scaled(pc, avg_c[b], cgec_individual_m)

    expected = pr * loss_r + pc * loss_c
    claim_prob = np.where(expected > 0, 1.0 - (1.0 - pr) * (1.0 - pc), 0.0)
    return expected, claim_prob


def _individual_credit_savings(credit_overlay: dict, year: int, scenario_params: dict) -> tuple[float, float, float]:
    """(REEC individual $M, REEC retained share, CGEC individual $M) for *year*.

    The CD2 vintage overlay reports the individual REEC pool directly; the
    CD1 static overlay does not, so its figures come from
    reec_individual_credit_loss.
    """
    cgec_m = float(credit_overlay.get("cgec_individual_savings_$M", 0.0) or 0.0)
    if "reec_individual_savings_$M" in credit_overlay:
        return (float(credit_overlay["reec_individual_savings_$M"]),
                float(credit_overlay["reec_eligible_retained_share"]), cgec_m)
    total_m, _inelig_m, cap_red_m = reec_individual_credit_loss(
        target_year=year,
        reec_demand_scenario=scenario_params["reec"],
        reec_effective_claim_share=scenario_params["reec_eff_share"],
        corp_subject_to_agi_limit=scenario_params.get("corp_agi_limit", False),
        cgec_annual_growth=scenario_params.get("cgec_growth", 0.030),
        reec_carryforward_utilization_m=scenario_params.get("reec_cf_m", 0.0),
    )
    eligible_m = float(credit_overlay.get("reec_individual_eligible_$M", 0.0) or 0.0)
    retained = 1.0 - cap_red_m / eligible_m if eligible_m > 0 else 1.0
    return total_m, retained, cgec_m


def _change_shares(change: np.ndarray, loss: np.ndarray, q: np.ndarray, wts: np.ndarray) -> tuple[float, float, float]:
    """Weighted percent paying more / less / the same, where each unit is a
    credit claimant with probability *q* and its tax change is *change* (the
    bracket change) plus, if it claims, its loss if claiming (*loss* / *q*)."""
    claim_change = change + np.divide(loss, q, out=np.zeros_like(loss), where=q > 0)
    more = q * (claim_change > 0) + (1 - q) * (change > 0)
    less = q * (claim_change < 0) + (1 - q) * (change < 0)
    tot = wts.sum()
    if tot <= 0:
        return 0.0, 0.0, 0.0
    m, l_ = float((wts * more).sum() / tot * 100), float((wts * less).sum() / tot * 100)
    return m, l_, 100.0 - m - l_


def _household_income_table(tax_units: pd.DataFrame) -> pd.DataFrame:
    """Aggregate tax units to households (sum income per hh_id).

    Uses ``total_cash_income`` (ITEP-style TCI) when present, else falls
    back to ``income`` (AGI-like). TCI includes full Social Security,
    SSI, TANF, and imputed employer-side FICA, producing quintile
    boundaries closer to ITEP's Who Pays? methodology.

    Returns a DataFrame indexed by hh_id with columns 'hh_income' and
    'hh_weight' (taken as the first weight in the household — all tax
    units within a household share the same PUMS household weight).
    """
    if "hh_id" not in tax_units.columns:
        raise ValueError("tax_units missing 'hh_id' column required for household aggregation")
    income_col = "total_cash_income" if "total_cash_income" in tax_units.columns else "income"
    g = tax_units[tax_units["weight"] > 0.01].groupby("hh_id", observed=True)
    return pd.DataFrame({
        "hh_income": g[income_col].sum(),
        "hh_weight": g["weight"].first(),
    })


def compute_quintile_breaks(tax_units: pd.DataFrame) -> np.ndarray:
    """Return the four household-income breakpoints (p20/p40/p60/p80).

    Aggregates tax units to households (by hh_id), then computes weighted
    quantiles on household income. This matches the ITEP/CTJ methodology
    for state-level distributional analysis where multiple tax filers in
    the same household are pooled before binning.

    Use the 2026 base population to anchor quintile boundaries so they
    don't drift upward with projected income growth in later years.

    Returns
    -------
    np.ndarray of shape (4,) — household-income thresholds at [p20, p40, p60, p80].
    """
    hh = _household_income_table(tax_units).sort_values("hh_income").reset_index(drop=True)
    cum_w = hh["hh_weight"].cumsum() / hh["hh_weight"].sum()
    breaks = []
    for q in [0.20, 0.40, 0.60, 0.80]:
        idx = (cum_w >= q).idxmax()
        breaks.append(float(hh.loc[idx, "hh_income"]))
    return np.array(breaks)


# Hawaii Council on Revenues (COR) IIT projections, $M.
#
# Loaded from the bundled data file that
# `python -m census_forecaster.scripts.refresh_cor_iit` maintains, so a new COR
# meeting propagates here without anyone re-transcribing a table. COR meets on
# no fixed cadence (~4-5 times a year); this dict previously sat hardcoded on
# the March 10, 2026 vintage while the May 21, 2026 forecast had already
# superseded it.
#
# FY→TY mapping: FY(n+1) = TY(n) per DOTAX fiscal-note convention (applied
# inside the loader).
#
# The literal below is a LAST-RESORT fallback for an installation whose data
# file is missing; it is the May 21, 2026 vintage. Do not hand-edit it to
# refresh — run the script.
_COR_FALLBACK_M = {
    2025: 3_139.079,   # FY 2026
    2026: 2_923.065,   # FY 2027
    2027: 2_874.134,   # FY 2028
    2028: 2_872.305,   # FY 2029
    2029: 2_780.166,   # FY 2030
    2030: 2_881.860,   # FY 2031
    2031: 2_971.795,   # FY 2032
}


def _load_cor_projections() -> dict[int, float]:
    try:
        from census_forecaster.cor import load_cor_iit_projections

        return load_cor_iit_projections(by="tax_year")
    except Exception:  # noqa: BLE001 - never let a data-file problem break scoring
        warnings.warn(
            "bundled COR projections unavailable; falling back to the "
            "hardcoded May 21, 2026 vintage. Run "
            "`python -m census_forecaster.scripts.refresh_cor_iit` to restore.",
            RuntimeWarning,
            stacklevel=2,
        )
        return dict(_COR_FALLBACK_M)


DEFAULT_COR_IIT_PROJECTIONS_M = _load_cor_projections()


def cor_scale_factor_for_year(
    year: int,
    microsim_baseline_M: float,
    cor_projections_M: Optional[dict[int, float]] = None,
) -> float:
    """Return COR scale = (official COR IIT projection) / (microsim Act 46 baseline).

    The microsim under-projects total Hawaii personal income tax by ~30% because
    PUMS-based AGI excludes income components captured by DOTAX (non-AGI
    items, withholding-only filers, etc.). Multiplying impact estimates by
    this factor brings totals into alignment with the official baseline.
    """
    if cor_projections_M is None:
        cor_projections_M = DEFAULT_COR_IIT_PROJECTIONS_M
    if year not in cor_projections_M:
        raise ValueError(f"No COR projection for year {year}")
    if microsim_baseline_M <= 0:
        return 1.0
    return cor_projections_M[year] / microsim_baseline_M


def generate_quintile_report(
    projected: pd.DataFrame,
    baseline_cfg,
    scenario_cfg,
    credit_overlay: dict,
    calc,
    scenario_params: Optional[dict] = None,
    quintile_breaks: Optional[np.ndarray] = None,
    cor_scale_factor: Optional[float] = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Produce per-filer quintile and bracket-breakdown analysis.

    Parameters
    ----------
    projected:
        Tax units projected to the target year.
    baseline_cfg:
        Act 46 TaxSystemConfig for the year.
    scenario_cfg:
        SB 3125 CD1 TaxSystemConfig for the year.
    credit_overlay:
        Output of compute_credit_overlay() — contains reec_savings_$M
        and the sub-components needed for individual credit distribution.
    calc:
        TaxCalculator instance (shared with the scenario runner).
    scenario_params:
        Scenario dict (label, reec, behav, reec_eff_share, …) used to
        call the REEC distribution helper. If None, credit distribution
        is skipped and credit_change is set to 0.
    quintile_breaks:
        Four AGI thresholds [p20, p40, p60, p80] from compute_quintile_breaks().
        Pass the 2026 base-year breaks to anchor quintile boundaries across
        all projection years. If None, breaks are computed from *projected*
        (floating boundaries, not recommended for multi-year comparison).
    cor_scale_factor:
        If provided, output adds COR-scaled columns (``*_cor_$M``,
        ``avg_*_cor``, ``avg_per_hh_*_cor``). Use this when the report
        needs to be compared with ITEP or DOTAX figures that work off the
        official Hawaii Council on Revenues IIT baseline (see
        ``cor_scale_factor_for_year``).

    Returns
    -------
    (quintile_df, bracket_df, perunit_df)
        quintile_df — 5-row quintile summary
        bracket_df  — income bracket breakdown
        perunit_df  — full per-filer detail
    """
    # ── Per-unit bracket tax under both systems ───────────────────────────────
    act46_net = per_unit_tax(projected, baseline_cfg, calc)
    cd1_net   = per_unit_tax(projected, scenario_cfg,  calc)

    # Household-level income for ITEP-style quintile assignment. Each tax
    # unit inherits its household's total income for the purpose of binning,
    # while tax/credit metrics remain per-unit.
    # Use TCI if available (includes full SSP, SSI, PAP, employer FICA).
    _hh_income_col = "total_cash_income" if "total_cash_income" in projected.columns else "income"
    if "hh_id" in projected.columns:
        hh_totals = projected.groupby("hh_id", observed=True)[_hh_income_col].transform("sum")
    else:
        hh_totals = projected[_hh_income_col].copy()

    pu = pd.DataFrame({
        "agi":            projected["income"].values,
        "hh_income":      hh_totals.values,
        "filing_status":  projected["filing_status"].values,
        "weight":         projected["weight"].values,
        # PUMS household weight (WGTP) — used for household counting.
        # Broader than the IPF-raked filer weight; closer to ACS total HH count.
        "hh_weight": (
            projected["hh_weight"].values if "hh_weight" in projected.columns
            else projected["weight"].values
        ),
        "act46_tax":      act46_net,
        "cd1_tax":        cd1_net,
        "bracket_change": cd1_net - act46_net,
        "credit_loss":    np.zeros(len(projected), dtype=float),
        "claim_prob":     np.zeros(len(projected), dtype=float),
        "hh_id": (
            projected["hh_id"].values if "hh_id" in projected.columns
            else np.arange(len(projected))
        ),
    })

    # ── Credit loss, attributed to imputed claimants ──────────────────────────
    if scenario_params is not None:
        reec_m, retained, cgec_m = _individual_credit_savings(
            credit_overlay, baseline_cfg.year, scenario_params)
        loss_arr, q_arr = attribute_credit_loss(
            projected, reec_individual_m=reec_m, reec_retained_share=retained,
            cgec_individual_m=cgec_m,
        )
        pu["credit_loss"] = loss_arr
        pu["claim_prob"] = q_arr

    pu["total_change"] = pu["bracket_change"] + pu["credit_loss"]
    pu = pu[pu["weight"] > 0.01].copy()

    # ── Quintile assignment (binned on HOUSEHOLD income, ITEP-style) ──────────
    pu_sorted = pu.sort_values("hh_income").reset_index(drop=True)
    q_labels = ["Q1 (bottom 20%)", "Q2", "Q3", "Q4", "Q5 (top 20%)"]
    if quintile_breaks is not None:
        # Anchor to 2026 household-income boundaries so quintile membership
        # reflects where a household stood in the base-year distribution,
        # not the projected year. All tax units in the same household share
        # the same quintile assignment.
        p20, p40, p60, p80 = quintile_breaks
        hh_arr = pu_sorted["hh_income"].to_numpy()
        q_codes = np.where(
            hh_arr < p20, 0,
            np.where(hh_arr < p40, 1,
            np.where(hh_arr < p60, 2,
            np.where(hh_arr < p80, 3, 4)))
        )
        pu_sorted["quintile"] = pd.Categorical.from_codes(q_codes, categories=q_labels, ordered=True)
    else:
        # Floating boundaries — derived from household-summed weights.
        # Households are weighted once (not by tax-unit count) for cumulative
        # share, then each tax unit inherits its household's quintile.
        hh_first = pu_sorted.groupby("hh_income", as_index=False).first() if False else pu_sorted
        cum_w = hh_first["weight"].cumsum() / hh_first["weight"].sum()
        pu_sorted["quintile"] = pd.cut(
            cum_w, bins=[0.0, 0.20, 0.40, 0.60, 0.80, 1.01],
            labels=q_labels, include_lowest=True,
        )

    # ── Income bracket assignment ─────────────────────────────────────────────
    hi_breaks = [float("-inf"), 10_000, 30_000, 60_000, 100_000,
                 175_000, 350_000, 500_000, 1_000_000, float("inf")]
    hi_labels = [
        "Under $10K", "$10K–$30K", "$30K–$60K", "$60K–$100K",
        "$100K–$175K", "$175K–$350K", "$350K–$500K",
        "$500K–$1M", "$1M+",
    ]
    pu_sorted["income_bracket"] = pd.cut(
        pu_sorted["agi"], bins=hi_breaks, labels=hi_labels, right=False
    )

    # ── Helper: weighted aggregate ────────────────────────────────────────────
    def _wtd_mean(vals, wts):
        s = wts.sum()
        return float((vals * wts).sum() / s) if s > 0 else 0.0

    pu_sorted["log_no_claim"] = np.log1p(-pu_sorted["claim_prob"].clip(upper=1 - 1e-12))

    # ── Household-level DataFrame (one row per household) ─────────────────────
    # Sum tax changes across all filers in the same household; take the
    # household's quintile and weights from the first filer (shared within HH).
    # Two weights are tracked:
    #   weight    — IPF-raked filer weight, calibrated to DOTAX → used for $M totals
    #   hh_weight — PUMS WGTP, representative of ACS household universe → denominator
    hh_pu = (
        pu_sorted
        .groupby("hh_id", observed=True)
        .agg(
            quintile=("quintile", "first"),
            hh_income=("hh_income", "first"),
            weight=("weight", "first"),
            hh_weight=("hh_weight", "first"),
            act46_tax=("act46_tax", "sum"),
            cd1_tax=("cd1_tax", "sum"),
            bracket_change=("bracket_change", "sum"),
            credit_loss=("credit_loss", "sum"),
            total_change=("total_change", "sum"),
            log_no_claim=("log_no_claim", "sum"),
        )
        .reset_index()
    )
    # A household claims if any of its tax units does.
    hh_pu["claim_prob"] = 1.0 - np.exp(hh_pu.pop("log_no_claim"))

    def _hh_agg(g):
        """Quintile aggregation at the household level.

        fw (filer weight) drives $M totals — calibrated to DOTAX.
        hw (household weight, WGTP) drives counts and per-HH averages —
        representative of the full ACS household universe including
        non-filing households that appear in PUMS but produce no tax units.
        """
        fw = g["weight"]     # calibrated filer weight → revenue totals
        hw = g["hh_weight"]  # PUMS WGTP → household counts / per-HH averages
        hw_sum = hw.sum()
        fw_sum = fw.sum()
        return pd.Series({
            "household_count":           hw_sum,
            "avg_per_hh_act46_tax":      (g["act46_tax"] * fw).sum() / hw_sum,
            "avg_per_hh_cd1_tax":        (g["cd1_tax"] * fw).sum() / hw_sum,
            "avg_per_hh_bracket_change": (g["bracket_change"] * fw).sum() / hw_sum,
            "avg_per_hh_credit_loss":    (g["credit_loss"] * fw).sum() / hw_sum,
            "avg_per_hh_total_change":   (g["total_change"] * fw).sum() / hw_sum,
            "total_act46_$M":            (g["act46_tax"] * fw).sum() / 1e6,
            "total_cd1_$M":              (g["cd1_tax"] * fw).sum() / 1e6,
            "total_bracket_$M":          (g["bracket_change"] * fw).sum() / 1e6,
            "total_credit_loss_$M":      (g["credit_loss"] * fw).sum() / 1e6,
            "total_change_$M":           (g["total_change"] * fw).sum() / 1e6,
            **dict(zip(("pct_pay_more", "pct_pay_less", "pct_no_change"), _change_shares(
                g["bracket_change"].to_numpy(), g["credit_loss"].to_numpy(),
                g["claim_prob"].to_numpy(), hw.to_numpy()), strict=True)),
            "pct_credit_claimant":       (hw * g["claim_prob"]).sum() / hw_sum * 100,
            "avg_credit_loss_per_claimant": (
                (g["credit_loss"] * fw).sum() / (hw * g["claim_prob"]).sum()
                if (hw * g["claim_prob"]).sum() > 0 else 0.0),
        })

    def _agg(g):
        """Bracket aggregation at the filer level."""
        w = g["weight"]
        return pd.Series({
            "agi_low":             g["agi"].min(),
            "agi_high":            g["agi"].max(),
            "agi_median":          _wtd_mean(g["agi"], w),
            "filer_count":         w.sum(),
            "avg_act46_tax":       _wtd_mean(g["act46_tax"], w),
            "avg_cd1_tax":         _wtd_mean(g["cd1_tax"], w),
            "avg_bracket_change":  _wtd_mean(g["bracket_change"], w),
            "avg_credit_loss":     _wtd_mean(g["credit_loss"], w),
            "avg_total_change":    _wtd_mean(g["total_change"], w),
            "total_act46_$M":      (g["act46_tax"] * w).sum() / 1e6,
            "total_cd1_$M":        (g["cd1_tax"] * w).sum() / 1e6,
            "total_bracket_$M":    (g["bracket_change"] * w).sum() / 1e6,
            "total_credit_loss_$M":(g["credit_loss"] * w).sum() / 1e6,
            "total_change_$M":     (g["total_change"] * w).sum() / 1e6,
            **dict(zip(("pct_pay_more", "pct_pay_less", "pct_no_change"), _change_shares(
                g["bracket_change"].to_numpy(), g["credit_loss"].to_numpy(),
                g["claim_prob"].to_numpy(), w.to_numpy()), strict=True)),
            "pct_credit_claimant": (w * g["claim_prob"]).sum() / w.sum() * 100,
            "avg_credit_loss_per_claimant": (
                (g["credit_loss"] * w).sum() / (w * g["claim_prob"]).sum()
                if (w * g["claim_prob"]).sum() > 0 else 0.0),
        })

    # Quintiles: household-level (pct_pay_more = % of households, not filers)
    quintile_df = (
        hh_pu.groupby("quintile", observed=True)
        .apply(_hh_agg, include_groups=False)
        .reset_index()
    )
    # Merge per-filer counts and averages for display/backward compat
    _filer_q = (
        pu_sorted.groupby("quintile", observed=True)
        .apply(lambda g: pd.Series({
            "filer_count":        g["weight"].sum(),
            "avg_total_change":   _wtd_mean(g["total_change"], g["weight"]),
            "avg_bracket_change": _wtd_mean(g["bracket_change"], g["weight"]),
            "avg_credit_loss":    _wtd_mean(g["credit_loss"], g["weight"]),
        }), include_groups=False)
        .reset_index()
    )
    quintile_df = quintile_df.merge(_filer_q, on="quintile", how="left")

    bracket_df = (
        pu_sorted.groupby("income_bracket", observed=True)
        .apply(_agg, include_groups=False)
        .reset_index()
    )

    # Bracket-level: no household decomposition (brackets are filer-level constructs)
    bracket_df["household_count"] = bracket_df["filer_count"]
    bracket_df["avg_per_hh_total_change"]    = bracket_df["avg_total_change"]
    bracket_df["avg_per_hh_bracket_change"]  = bracket_df["avg_bracket_change"]
    bracket_df["avg_per_hh_credit_loss"]     = bracket_df["avg_credit_loss"]

    # ── Optional COR scaling ─────────────────────────────────────────────────
    if cor_scale_factor is not None and cor_scale_factor != 1.0:
        for df_ in (quintile_df, bracket_df):
            df_["cor_scale_factor"]              = cor_scale_factor
            df_["total_change_cor_$M"]           = df_["total_change_$M"]    * cor_scale_factor
            df_["total_bracket_cor_$M"]          = df_["total_bracket_$M"]   * cor_scale_factor
            df_["avg_total_change_cor"]          = df_["avg_total_change"]   * cor_scale_factor
            df_["avg_per_hh_total_change_cor"]   = df_["avg_per_hh_total_change"] * cor_scale_factor
            df_["avg_per_hh_bracket_change_cor"] = df_.get(
                "avg_per_hh_bracket_change", df_["avg_total_change"]
            ) * cor_scale_factor

    return quintile_df, bracket_df, pu_sorted
