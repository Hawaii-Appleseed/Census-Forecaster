"""
Per-filer distributional analysis: Act 46 vs SB 3125 CD1.

Computes bracket tax change and REEC credit savings impact per filer,
bins into income quintiles (weight-cumulative, all filing statuses combined)
and fixed income brackets, and returns summary DataFrames.

Credit distribution methodology:
  REEC savings fall on individual filers in two ways:
    1. Ineligible-by-AGI filers (AGI > $175K single / $350K MFJ under CD1)
       lose their entire REEC claim — allocated by DOTAX TY2023 Table A-5
       ineligible claim amounts per AGI bin.
    2. Eligible filers face a pro-rata cap reduction when total eligible
       claims exceed $40M — allocated by their eligible claim weight.
  Corporate / Other REEC, CGEC, and TCRA savings are not allocable to
  individual filers and are reported as an unallocated corporate component.
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
from typing import Optional

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
    falls back to scenario SD only. There is no longer a ``deduction_col``
    override — that parameter previously caused silent bugs by forcing both
    baseline and bill scenarios to share a pre-computed effective-deduction
    column, hiding scenario-specific SD changes (e.g. HB 2306 ORIG SD freeze).

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
    np.ndarray of shape (n,) — per-filer net liability (≥ 0).
    """
    from tax_modeler.adjustments.hawaii_credits import HawaiiTaxCredits

    n        = len(tax_units)
    incomes  = tax_units["income"].to_numpy(dtype=float)
    statuses = tax_units["filing_status"].to_numpy()

    raw_exemp = (
        tax_units["num_exemptions"].to_numpy(dtype=float)
        if "num_exemptions" in tax_units.columns
        else np.ones(n, dtype=float)
    )
    nan_exemp = np.isnan(raw_exemp)
    num_exemptions = np.where(nan_exemp, 1.0, raw_exemp)

    raw_dep = (
        tax_units["num_dependents"].to_numpy(dtype=float)
        if "num_dependents" in tax_units.columns
        else np.zeros(n, dtype=float)
    )
    nan_dep = np.isnan(raw_dep)
    num_dependents = np.where(nan_dep, 0, raw_dep).astype(int)

    # Effective deduction = max(scenario_SD_per_fs, itemized).
    # The raw itemized column is populated upstream by calculate_hawaii_tax
    # (when deduction_params was supplied during projection). Itemizers keep
    # their itemized amount under any scenario; only non-itemizers see the
    # scenario-specific SD. This is essential for SD-changing bills like
    # HB 2306 ORIG, where applying the SD difference to itemizers would
    # overstate the bill's revenue impact.
    sd_per_filer = np.empty(n, dtype=float)
    for fs in np.unique(statuses):
        sd_per_filer[statuses == fs] = calc.get_standard_deduction(
            config.standard_deduction_year, fs
        )
    if "hi_itemized_deduction" in tax_units.columns:
        itemized = (
            tax_units["hi_itemized_deduction"]
            .fillna(0.0).to_numpy(dtype=float)
        )
        deductions = np.maximum(sd_per_filer, itemized)
    else:
        deductions = sd_per_filer

    exemption_amount = num_exemptions * config.personal_exemption
    taxable_full = np.maximum(0.0, incomes - deductions - exemption_amount)

    cg_shares = (
        tax_units["synthetic_cg_share"].fillna(0.0).to_numpy(dtype=float)
        if "synthetic_cg_share" in tax_units.columns
        else np.zeros(n, dtype=float)
    )
    cg_income   = np.maximum(0.0, incomes * cg_shares)
    has_cg      = cg_income > 0
    ordinary    = np.maximum(0.0, incomes - cg_income)
    taxable_ord = np.maximum(0.0, ordinary - deductions - exemption_amount)

    tax_full = calc._vectorized_bracket_tax(taxable_full, statuses, config)
    if has_cg.any():
        tax_ord     = calc._vectorized_bracket_tax(taxable_ord, statuses, config)
        cg_uncapped = np.maximum(0.0, tax_full - tax_ord)
        cg_capped   = np.minimum(cg_uncapped, cg_income * calc.HAWAII_CG_CAP_RATE)
        pre_credit  = np.where(has_cg, tax_ord + cg_capped, tax_full)
    else:
        pre_credit = tax_full

    pre_credit[nan_exemp] = 0.0

    credit_calc = HawaiiTaxCredits(year=config.year)
    credit_scen = getattr(config, "credit_scenario", None)
    credits_arr = np.zeros(n, dtype=float)
    skip = nan_exemp | nan_dep
    for i in range(n):
        if skip[i]:
            continue
        result = credit_calc.calculate_total_credits(
            agi=float(incomes[i]),
            filing_status=str(statuses[i]),
            num_dependents=int(num_dependents[i]),
            tax_before_credits=float(pre_credit[i]),
            credit_scenario=credit_scen,
        )
        credits_arr[i] = result.get("total_credits", 0.0)

    return np.maximum(0.0, pre_credit - credits_arr)


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


def distribute_reec_loss_to_filers(
    tax_units: pd.DataFrame,
    total_ind_savings_m: float,
    ineligible_m: float,
    eligible_cap_reduction_m: float,
) -> np.ndarray:
    """Distribute individual REEC credit loss to filers; return per-filer amounts ($).

    Distribution uses DOTAX TY2023 AGI bin claim weights. Each filer receives
    a share proportional to their bin's REEC claim weight, divided by the
    weighted filer count in that bin.

    Parameters
    ----------
    tax_units:
        DataFrame with 'income' and 'weight' columns.
    total_ind_savings_m:
        Total individual-borne REEC savings ($M). Verified = ineligible + cap_reduction.
    ineligible_m:
        Portion from AGI-limit exclusion ($M). Allocated to above-threshold filers.
    eligible_cap_reduction_m:
        Portion from pro-rata cap ($M). Allocated to below-threshold filers.
    """
    n        = len(tax_units)
    incomes  = tax_units["income"].to_numpy(dtype=float)
    weights  = tax_units["weight"].to_numpy(dtype=float)
    statuses = tax_units["filing_status"].to_numpy()

    if total_ind_savings_m <= 0:
        return np.zeros(n, dtype=float)

    # AGI thresholds for eligibility under CD1:
    #   MFJ → $350K, all others → $175K
    agi_threshold = np.where(
        np.isin(statuses, ["married_filing_jointly", "qualifying_widow"]),
        350_000.0,
        175_000.0,
    )
    is_ineligible = incomes > agi_threshold
    is_eligible   = ~is_ineligible

    bin_idx = _agi_bin_index(incomes)

    # --- Ineligible loss allocation (above-AGI-limit filers) ----------------
    # Ineligible DOTAX bin claim fractions: bins 0-4 are 100% eligible, so
    # ineligible claims come only from bins 4 ($100K-$200K, 2.8% ineligible)
    # and bin 5 ($200K+, 43.9% ineligible).
    inelig_bin_weights = np.array([
        b[1] * (1.0 - b[2]) for b in _REEC_BINS  # total_$M × ineligible_share
    ])
    inelig_total_weight = inelig_bin_weights.sum()

    inelig_loss = np.zeros(n, dtype=float)
    if inelig_total_weight > 0 and ineligible_m > 0:
        # Pre-compute per-bin weighted sum of ineligible filers (vectorized)
        inelig_bin_w_sum = np.array([
            weights[(bin_idx == b) & is_ineligible].sum()
            for b in range(len(_REEC_BINS))
        ])
        # Each ineligible filer gets (bin_claim_weight / total_weight) × total_$M / bin_pop_weight
        per_bin_rate = np.where(
            inelig_bin_w_sum > 0,
            (inelig_bin_weights / inelig_total_weight) * ineligible_m * 1e6 / np.maximum(inelig_bin_w_sum, 1e-9),
            0.0,
        )
        inelig_loss = np.where(is_ineligible, per_bin_rate[bin_idx], 0.0)

    # --- Eligible cap-reduction allocation (below-AGI-limit filers) ---------
    elig_bin_weights = np.array([
        b[1] * b[2] for b in _REEC_BINS  # total_$M × eligible_share
    ])
    elig_total_weight = elig_bin_weights.sum()

    elig_loss = np.zeros(n, dtype=float)
    if elig_total_weight > 0 and eligible_cap_reduction_m > 0:
        # Pre-compute per-bin weighted sum of eligible filers (vectorized)
        elig_bin_w_sum = np.array([
            weights[(bin_idx == b) & is_eligible].sum()
            for b in range(len(_REEC_BINS))
        ])
        per_bin_rate_e = np.where(
            elig_bin_w_sum > 0,
            (elig_bin_weights / elig_total_weight) * eligible_cap_reduction_m * 1e6 / np.maximum(elig_bin_w_sum, 1e-9),
            0.0,
        )
        elig_loss = np.where(is_eligible, per_bin_rate_e[bin_idx], 0.0)

    return inelig_loss + elig_loss


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
        "hh_id": (
            projected["hh_id"].values if "hh_id" in projected.columns
            else np.arange(len(projected))
        ),
    })

    # ── REEC credit loss distribution ─────────────────────────────────────────
    if scenario_params is not None:
        year = baseline_cfg.year
        total_ind_m, inelig_m, cap_red_m = reec_individual_credit_loss(
            target_year=year,
            reec_demand_scenario=scenario_params["reec"],
            reec_effective_claim_share=scenario_params["reec_eff_share"],
            corp_subject_to_agi_limit=scenario_params.get("corp_agi_limit", False),
            cgec_annual_growth=scenario_params.get("cgec_growth", 0.030),
            reec_carryforward_utilization_m=scenario_params.get("reec_cf_m", 0.0),
        )
        loss_arr = distribute_reec_loss_to_filers(
            projected, total_ind_m, inelig_m, cap_red_m
        )
        pu["credit_loss"] = loss_arr

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
        )
        .reset_index()
    )

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
            "pct_pay_more":              hw[g["total_change"] > 0].sum() / hw_sum * 100,
            "pct_pay_less":              hw[g["total_change"] < 0].sum() / hw_sum * 100,
            "pct_no_change":             hw[g["total_change"] == 0].sum() / hw_sum * 100,
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
            "pct_pay_more":        w[g["total_change"] > 0].sum() / w.sum() * 100,
            "pct_pay_less":        w[g["total_change"] < 0].sum() / w.sum() * 100,
            "pct_no_change":       w[g["total_change"] == 0].sum() / w.sum() * 100,
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
