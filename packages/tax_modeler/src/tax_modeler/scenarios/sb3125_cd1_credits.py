"""Static-scoring overlay for SB 3125 CD1 credit changes.

Three credit changes in the bill have material fiscal impact but are not
captured by the individual-income-tax microsimulation:

  1. §235-12.5 Renewable Energy Technologies Income Tax Credit
     - Capped at $40M aggregate per year for TY 2027-2030
     - Set to $0 from TY 2031 onward
     - Adds AGI eligibility limits ($175K single/HoH/MFS, $350K MFJ)
       Per §235-12.5(a) as amended: "$175,000 if filing as an individual,
       or $350,000 if filing jointly". HoH and MFS treated as "individual"
       (single category) per user direction.

  2. §235-110.7 Capital Goods Excise Tax Credit
     - Sunsets effective Dec 31, 2027 (applies TY 2028+)

  3. §235-110.91 Tax Credit for Research Activities (via Act 261 amendment)
     - Bill accelerates Part II of Act 261 from 1/1/2030 to 1/1/2029
     - Net effect: credit eliminated 1 year earlier (TY 2029 instead of 2030)
     - Bill impact = TY 2029 only; TY 2030+ baseline already has it sunset

DOTAX TY2023 baselines (in $M, from "Tax Credits Claimed by Hawaiʻi
Taxpayers — Tax Year 2023", Dec 2025):

  Renewable Energy Tax Credit (Table A-1, line 1854; A-5, line 2067):
    Total: $100.1M
    By taxpayer type:
      Individuals:   $58.3M  (subject to AGI limit)
      Corporations:  $38.6M  (NOT subject to AGI limit)
      Other (other):  $3.2M  (financial corp, fiduciaries, exempt orgs)
    Individual claims by AGI bin:
      <$10K:      $4.7M  (100% eligible — below threshold)
      $10-30K:    $2.5M  (100% eligible)
      $30-60K:    $3.1M  (100% eligible)
      $60-100K:   $5.8M  (100% eligible)
      $100-200K: $16.2M  (~97.2% eligible per PUMS — most below $175K)
      $200K+:    $26.0M  (~56.1% eligible per PUMS — MFJ at $200-350K still in)
    Eligibility shares derived from Hawaii calibrated tax units (cached at
    /tmp/tax_units_cache.parquet, weighted-filer counts).

  Capital Goods Excise Tax Credit (Table A-1, line 1862):
    Total: $34.6M

  Tax Credit for Research Activities (Table A-1, line 1857):
    Total: $7.0M  ($1.0M individuals + $6.0M corporations)

Growth assumption: Hawaii nominal income growth from
``get_hawaii_real_growth_factor(2023, year) * cpi_honolulu_factor``,
matching the methodology used for tax unit projections.

This overlay does NOT model:
  - Behavioral response to the cap (pro-rata allocation under §235-12.5(h)
    means filers have minimal incentive to time claims — appropriate to
    treat as static)
  - Carryover dynamics (carry-forwards from prior years post-cap)
  - Non-individual REEC claimants other than corporate (~$3.2M "other"
    treated as not subject to AGI limits but counted in cap)
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Dict
import logging

logger = logging.getLogger(__name__)

BASE_YEAR = 2023

# ---------------------------------------------------------------------------
# DOTAX TY2023 baselines (in $ millions)
# ---------------------------------------------------------------------------

# §235-12.5 Renewable Energy Tax Credit (REEC)
REEC_INDIVIDUAL_TOTAL_M = 58.293
REEC_CORPORATE_TOTAL_M  = 38.565
REEC_OTHER_TOTAL_M      =  3.217   # financial corp, fiduciaries, exempt orgs (not subject to AGI limit)
# Sanity: 58.293 + 38.565 + 3.217 = $100.075M, matches DOTAX Table A-1 line 1854.

# Refundable / nonrefundable split (DOTAX "Tax Credits Claimed by Hawaiʻi
# Taxpayers — Tax Year 2023", refund-flag breakout). Refundable claims
# offset state revenue dollar-for-dollar; nonrefundable claims that exceed
# tax liability carry forward and don't reduce current-year revenue, so
# only ~80% of nonrefundable dollars effectively offset revenue.
#
# Individual:        $13.4M refundable, $44.9M nonrefundable  (~23% / ~77%)
# Corporate + Other: $37.3M refundable, ~$4.5M nonrefundable  (~89% / ~11%)
#
# Corporate REEC is overwhelmingly refundable because most commercial-scale
# claims are PV / wind / OTEC investments financed via tax-equity partners
# (refundable by election under §235-12.5(g)). Applying a single
# nonrefundable utilization haircut to both pools (as a previous version
# did) understated the effective dollar value of corporate REEC by ~15%.
REEC_REFUNDABLE_SHARE_INDIVIDUAL    = 13.4 / REEC_INDIVIDUAL_TOTAL_M
REEC_NONREFUNDABLE_SHARE_INDIVIDUAL = 1.0 - REEC_REFUNDABLE_SHARE_INDIVIDUAL
_corp_plus_other_total              = REEC_CORPORATE_TOTAL_M + REEC_OTHER_TOTAL_M
REEC_REFUNDABLE_SHARE_CORPORATE     = 37.3 / _corp_plus_other_total
REEC_NONREFUNDABLE_SHARE_CORPORATE  = 1.0 - REEC_REFUNDABLE_SHARE_CORPORATE

# Individual REEC claims by AGI bin (DOTAX Table A-5, line 2067)
# Each entry: (bin_label, claim_$M, eligible_share_after_AGI_limit)
# Eligibility shares derived from Hawaii calibrated tax units (PUMS):
# fraction of weighted filers in each bin with AGI <= threshold
# ($175K single/HoH/MFS, $350K MFJ).
REEC_INDIVIDUAL_BY_AGI_BIN = [
    ("<$10K",        4.731, 1.000),
    ("$10K-$30K",    2.522, 1.000),
    ("$30K-$60K",    3.121, 1.000),
    ("$60K-$100K",   5.752, 1.000),
    ("$100K-$200K", 16.150, 0.972),
    ("$200K+",      26.018, 0.561),
]
# Sanity: sum of claims = $58.294M (matches REEC_INDIVIDUAL_TOTAL_M to rounding)

# §235-110.7 Capital Goods Excise Tax Credit (CGEC)
CGEC_TOTAL_M = 34.608

# §235-110.91 Tax Credit for Research Activities (TCRA)
TCRA_TOTAL_M = 7.034

# ---------------------------------------------------------------------------
# Bill provisions
# ---------------------------------------------------------------------------
REEC_CAP_2027_2030_M = 40.0
REEC_CAP_2031_PLUS_M =  0.0
CGEC_SUNSET_YEAR     = 2027   # Sunsets after Dec 31, 2027 -> applies TY 2028+
TCRA_BILL_REPEAL_YEAR = 2029  # Bill: Act 261 Part II takes effect 1/1/2029
TCRA_ACT46_REPEAL_YEAR = 2030 # Baseline (Act 46): repeal was 1/1/2030

# Bill effective dates (HI SB 3125 CD2, Section 9):
#   - Section 1 (REEC amendment) applies retroactively to TY beginning after 12/31/2025
#   - §235-12.5(a) AGI limit applies only to TY beginning after 12/31/2026
#   - §235-12.5(p) sunset: no new credits for TY beginning after 12/31/2029
#   - §235-12.5(c) cap of $40M applies to calendar years 2027-2030 only
REEC_BILL_RETROACTIVE_YEAR = 2026          # Section 1 retroactive to TY2026
REEC_AGI_LIMIT_FIRST_YEAR  = 2027          # AGI limits start TY2027
REEC_SUNSET_LAST_VINTAGE   = 2029          # Last TY for new credits

# Carryforward stock simulation parameters
# §235-12.5(j) carryforward "until exhausted" — no time limit.
# REEC effective from 2009; we simulate from 2010 onward (stock converges to
# steady state within ~5-6 years given util=0.65).
REEC_SIM_START_YEAR        = 2010
REEC_BACKCAST_GROWTH       = 0.03   # 3%/yr nominal industry growth for pre-2023 backcast

# ---------------------------------------------------------------------------
# REEC demand decay scenarios (post-OBBBA federal Section 25D termination)
# ---------------------------------------------------------------------------
# OBBBA (PL 119-21, July 2025) terminated the federal residential solar
# credit (Section 25D) effective 12/31/2025. SEIA forecasts US residential
# solar -19% in 2026, commercial -13%; then modest recovery as state credits
# become more decisive at the margin.
#
# Hawaii context tempers SEIA's national figures:
#   - Hawaii has the highest electricity prices in the US ($0.42/kWh) -> solar
#     economics survive without federal credit at meaningful levels.
#   - Large lease/PPA share (Sunrun, etc.) uses Section 48E (intact through
#     12/31/2027) rather than Section 25D, so a portion is unaffected.
#   - 2025 saw a +37% pull-forward in HECO interconnection applications
#     (Jul-Dec 2025) -> some 2026-2027 demand was already pulled into 2025.
#
# Three scenarios, applied as a multiplicative factor to the projected REEC
# baseline (residential + commercial combined):
REEC_DEMAND_SCENARIOS: dict[str, dict[int, float]] = {
    # No federal-credit adjustment (assumes pre-OBBBA demand persists).
    # Conservative for revenue (overstates baseline -> overstates cap savings).
    "pre_obbba": {y: 1.00 for y in range(2024, 2032)},

    # SEIA-anchored, Hawaii-tempered. Recommended default.
    # 2025 +10% pull-forward; 2026-27 trough at -15 to -17% (vs SEIA national
    # -19%, Hawaii buffered by $0.42/kWh electricity prices and §48E lease
    # share). Gradual recovery as state credit becomes more decisive at
    # the margin, but no return to pre-OBBBA — Hawaii rooftop saturation
    # (~35%, highest in US) caps long-run demand below 2024 levels.
    "obbba_mid": {2024: 1.00, 2025: 1.10, 2026: 0.85, 2027: 0.83,
                  2028: 0.85, 2029: 0.87, 2030: 0.90, 2031: 0.85},

    # SEIA national figures applied directly + extended decay.
    # Severe case: Hawaii REEC declines as solar economics deteriorate.
    "obbba_severe": {2024: 1.00, 2025: 1.10, 2026: 0.81, 2027: 0.79,
                     2028: 0.80, 2029: 0.85, 2030: 0.90, 2031: 0.95},

    # Federal §25D safe-harbor overhang variant (OBBBA Mid base + safe-harbor
    # demand boost in 2026-2027).
    #
    # IRS Notice 2013-29 (and successor guidance) allows residential solar
    # filers to lock in the §25D credit at the point of "commencement of
    # construction" — a 5% safe-harbor payment suffices — with up to 4 years
    # to complete installation. The +37% surge in HECO interconnection
    # applications in H2 2025 (representing projects that commenced
    # construction before the 12/31/2025 §25D termination deadline) creates
    # a backlog of installations completing in 2026-2027 that is NOT captured
    # in the OBBBA Mid trough factors (0.85 / 0.83).
    #
    # This scenario superimposes that safe-harbor backlog:
    #   2026: 1.05  — above pre-OBBBA baseline; H2 2025 applications
    #                 (~+37% HECO surge) completing installation in early 2026,
    #                 net of the share already counted in the 2025 pull-forward.
    #   2027: 0.97  — tail of safe-harbor completions (larger / more complex
    #                 systems); still near pre-OBBBA levels before fading.
    #   2028+:       reverts to OBBBA Mid trajectory; safe-harbor backlog
    #                exhausted by this point.
    #
    # SB 3125 retroactivity: confirmed (Section 9(1)) to apply to taxable
    # years beginning after 12/31/2025. Under Interpretation A, TY2026
    # certifications (filed in CY2027) fall under the $40M cap.  The elevated
    # 2026 demand therefore binds the cap more tightly → lower pro-rata factor
    # → higher state savings, but more filers pro-rated.
    "safe_harbor_mid": {2024: 1.00, 2025: 1.10, 2026: 1.05, 2027: 0.97,
                        2028: 0.85, 2029: 0.87, 2030: 0.90, 2031: 0.85},
}
DEFAULT_REEC_DEMAND_SCENARIO = "obbba_mid"


# ---------------------------------------------------------------------------
# DOTAX historical actuals (TY2018-2022) — replaces 3%/yr synthetic backcast
# ---------------------------------------------------------------------------
# Loaded once at module import from
#   packages/tax_modeler/src/tax_modeler/data/raw/dotax_reec_historical.csv
# produced by scripts/fetch_dotax_credits_historical.py.
#
# Cells suppressed by DOTAX (disclosure protection on small populations) are
# stored as 0 in the CSV. The `corporate_plus_other_$M` figure is derived as
# `all_total - individual_total`, which captures the full non-individual pool
# including suppressed financial-corp values.
_DOTAX_HISTORICAL: dict[int, dict[str, float]] = {}


def _load_dotax_historical() -> dict[int, dict[str, float]]:
    """Load DOTAX TY2018-2022 actuals into a year-keyed dict. Idempotent."""
    global _DOTAX_HISTORICAL
    if _DOTAX_HISTORICAL:
        return _DOTAX_HISTORICAL
    import csv
    from pathlib import Path
    csv_path = (
        Path(__file__).resolve().parent.parent
        / "data" / "raw" / "dotax_reec_historical.csv"
    )
    if not csv_path.exists():
        return _DOTAX_HISTORICAL
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                year = int(row["year"])
            except (KeyError, ValueError):
                continue
            ind_total = float(row.get("individual_total_$M", 0) or 0)
            all_total = float(row.get("all_total_$M", 0) or 0)
            corp_plus_other = max(0.0, all_total - ind_total)
            _DOTAX_HISTORICAL[year] = {
                "all_total_$M":         all_total,
                "individual_total_$M":  ind_total,
                "corp_plus_other_$M":   corp_plus_other,
                "individual_<$10K_$M":  float(row.get("individual_<$10K_$M", 0) or 0),
                "individual_$10K-$30K_$M": float(row.get("individual_$10K-$30K_$M", 0) or 0),
                "individual_$30K-$60K_$M": float(row.get("individual_$30K-$60K_$M", 0) or 0),
                "individual_$60K-$100K_$M": float(row.get("individual_$60K-$100K_$M", 0) or 0),
                "individual_$100K-$200K_$M": float(row.get("individual_$100K-$200K_$M", 0) or 0),
                "individual_$200K+_$M": float(row.get("individual_$200K+_$M", 0) or 0),
            }
    return _DOTAX_HISTORICAL


_load_dotax_historical()


# ---------------------------------------------------------------------------
# Growth factors
# ---------------------------------------------------------------------------

# Multiplicative scale on the income-linked (individual) growth factor.
# 1.0 in normal operation; set only via `_scaled_growth()` during the
# CI evaluation passes of `compute_credit_overlay`, where it shifts the
# Hawaii nominal growth to its 90% bounds. Applied inside
# `_hawaii_nominal_growth` — after the cached real-factor lookup, so the
# lru caches always hold unscaled values. Corporate growth is a fixed-rate
# assumption, not income-forecast-derived, and is deliberately unscaled.
_growth_scale: float = 1.0


@contextmanager
def _scaled_growth(scale: float):
    """Temporarily scale `_hawaii_nominal_growth` output (CI passes only)."""
    global _growth_scale
    prev = _growth_scale
    _growth_scale = scale
    try:
        yield
    finally:
        _growth_scale = prev


def _hawaii_nominal_growth(target_year: int) -> float:
    """Cumulative Hawaii nominal income growth from BASE_YEAR (2023) to target_year.

    nominal = real(B19013, CPI-deflated) * Honolulu CPI growth.

    Falls back to flat 4%/yr if the bundled forecast machinery is unavailable.
    """
    try:
        from tax_modeler.projection.income_forecast import (
            get_hawaii_real_growth_factor, _load_cpi_honolulu_series, _project_cpi,
        )
        real = get_hawaii_real_growth_factor(BASE_YEAR, target_year)
        if real is None:
            raise ValueError("real growth factor unavailable")
        cpi = _load_cpi_honolulu_series()
        cpi_factor = _project_cpi(cpi, target_year) / _project_cpi(cpi, BASE_YEAR)
        return real * cpi_factor * _growth_scale
    except Exception as e:
        years = target_year - BASE_YEAR
        fallback = (1.04) ** years
        logger.warning(
            "Hawaii nominal growth unavailable (%s); falling back to 4%%/yr -> factor=%.4f",
            e, fallback,
        )
        return fallback * _growth_scale


def _hawaii_corporate_growth(target_year: int, annual_rate: float = 0.030) -> float:
    """Cumulative Hawaii nominal *business sector* growth from BASE_YEAR.

    CGEC and corporate REEC are business-investment / capital-asset
    credits, NOT individual-income credits. Using median household income
    growth for them systematically misprices the projection.

    Default annual_rate is 3% (recalibrated downward from 5% after
    feedback that real Hawaii business investment growth is closer to
    the 3-4% range, not the 5% nominal GDP). Caller can override.

    Falls back to flat 3%/yr if no bundled GDP series is available.
    """
    years = target_year - BASE_YEAR
    return (1.0 + annual_rate) ** years


# ---------------------------------------------------------------------------
# Credit overlay
# ---------------------------------------------------------------------------


def _reec_eligible_individual_M() -> float:
    """REEC individual-side eligible demand (TY2023 $) after AGI limits.

    Sums DOTAX TY2023 individual claims weighted by per-bin AGI eligibility
    share (from PUMS). Assumes within-bin uniformity (filer-share ≈ dollar-share).
    """
    return sum(claim * elig for _, claim, elig in REEC_INDIVIDUAL_BY_AGI_BIN)


# AGI thresholds in §235-12.5(a) — NOT indexed to inflation, so real value erodes
# as nominal incomes grow.
REEC_AGI_THRESHOLD_INDIVIDUAL = 175_000.0   # single / HoH / MFS
REEC_AGI_THRESHOLD_JOINT      = 350_000.0   # MFJ / surviving spouse


def compute_dynamic_agi_eligibility_share(
    projected_units,
    *,
    income_col: str = "income",
    weight_col: str = "weight",
    filing_status_col: str = "filing_status",
) -> float:
    """Recompute aggregate individual REEC AGI-eligibility share from a
    projected-tax-units DataFrame.

    Weights each AGI bin by the TY2023 DOTAX dollar shares in
    `REEC_INDIVIDUAL_BY_AGI_BIN` (so REEC claimants' income distribution
    is approximated by the historical claim distribution), then within each
    bin computes the population-weighted fraction of filers whose AGI is
    within the §235-12.5(a) thresholds ($175K single/HoH/MFS, $350K MFJ).

    As nominal income grows year-over-year, more filers cross above the
    unindexed thresholds — eligibility share declines.
    """
    import pandas as pd  # local import keeps the module import-light

    bin_edges = [0, 10_000, 30_000, 60_000, 100_000, 200_000, float("inf")]
    bin_labels = ["<$10K", "$10K-$30K", "$30K-$60K", "$60K-$100K",
                  "$100K-$200K", "$200K+"]

    df = projected_units
    inc = df[income_col]
    wt = df[weight_col]
    fs = df[filing_status_col].astype(str)

    # Eligibility flag per row
    joint_thresh = (fs.str.contains("joint", case=False)) | (
        fs.str.contains("surviving", case=False)
    )
    elig_mask = (
        (joint_thresh & (inc <= REEC_AGI_THRESHOLD_JOINT))
        | (~joint_thresh & (inc <= REEC_AGI_THRESHOLD_INDIVIDUAL))
    )

    eligible_dollars = 0.0
    total_dollars    = 0.0
    for i, (label, claim_M, _ty23_elig) in enumerate(REEC_INDIVIDUAL_BY_AGI_BIN):
        if label != bin_labels[i]:
            # Defensive: bins should align by index
            continue
        in_bin = (inc >= bin_edges[i]) & (inc < bin_edges[i + 1])
        bin_w = wt[in_bin].sum()
        if bin_w <= 0:
            continue
        bin_elig = wt[in_bin & elig_mask].sum() / bin_w
        eligible_dollars += claim_M * bin_elig
        total_dollars    += claim_M

    if total_dollars <= 0:
        return _reec_eligible_individual_M() / REEC_INDIVIDUAL_TOTAL_M
    return eligible_dollars / total_dollars


def _reec_demand_factor(target_year: int, scenario: str) -> float:
    """Return the residential REEC demand-decay multiplier at target_year.

    Encodes post-OBBBA federal Section 25D termination effects per the
    selected scenario. See REEC_DEMAND_SCENARIOS for definitions.

    Applies to the **individual / residential** portion of REEC claims.
    Corporate / commercial claims use :func:`_reec_demand_factor_corporate`.
    """
    if scenario not in REEC_DEMAND_SCENARIOS:
        raise ValueError(
            f"Unknown REEC demand scenario {scenario!r}; "
            f"valid options: {list(REEC_DEMAND_SCENARIOS.keys())}"
        )
    table = REEC_DEMAND_SCENARIOS[scenario]
    if target_year in table:
        return table[target_year]
    # Out-of-range: clamp to nearest endpoint
    if target_year < min(table):
        return table[min(table)]
    return table[max(table)]


_CORPORATE_48E_TAPER: dict[int, float] = {
    # §48E (commercial solar/wind ITC) active through 12/31/2027.
    # Post-expiration, Hawaii commercial solar loses the federal tax-equity
    # financing complement. Projects using Sunrun-style lease/PPA structures
    # financed via §48E cannot use that credit for new projects after 2027.
    # Hawaii's $0.42/kWh electricity prices sustain some commercial solar
    # economics, so demand doesn't collapse — but project finance reprices
    # and a meaningful share of marginal commercial projects no longer pencil.
    # Taper: -15% in first year post-expiry, converging to -28% by 2031.
    2027: 1.00,
    2028: 0.85,
    2029: 0.80,
    2030: 0.75,
    2031: 0.72,
}


def _reec_demand_factor_corporate(target_year: int) -> float:
    """Return the commercial REEC demand-decay multiplier at target_year.

    Through 2027: §48E (commercial solar/wind ITC) is active → factor = 1.0.
    Post-2027: §48E expires 12/31/2027; commercial project finance reprices,
    depressing new REEC-eligible commercial installations by 15–28% over the
    following years. See ``_CORPORATE_48E_TAPER`` for the year-by-year schedule.
    """
    if target_year in _CORPORATE_48E_TAPER:
        return _CORPORATE_48E_TAPER[target_year]
    if target_year < min(_CORPORATE_48E_TAPER):
        return 1.0
    return _CORPORATE_48E_TAPER[max(_CORPORATE_48E_TAPER)]


def _effective_claim_share_individual(nonrefundable_utilization: float) -> float:
    """Aggregate effective-revenue share for individual REEC claims.

    Refundable claims offset revenue 1:1; nonrefundable claims offset at
    ``nonrefundable_utilization`` (the literature midpoint is ~0.80, since
    ~20% of nonrefundable dollars carry forward or expire). This function
    blends the two using the DOTAX 2023 refundable share (~23% individual).
    """
    return (
        REEC_REFUNDABLE_SHARE_INDIVIDUAL * 1.0
        + REEC_NONREFUNDABLE_SHARE_INDIVIDUAL * nonrefundable_utilization
    )


def _effective_claim_share_corporate(nonrefundable_utilization: float) -> float:
    """Aggregate effective-revenue share for corporate / other REEC claims.

    Mirrors :func:`_effective_claim_share_individual` but uses the
    corporate refundable share (~89%), so a 0.80 nonrefundable
    utilization rate yields an aggregate effective share near 0.98 —
    materially higher than 0.80, because corporate REEC is dominated by
    refundable PV tax-equity claims.
    """
    return (
        REEC_REFUNDABLE_SHARE_CORPORATE * 1.0
        + REEC_NONREFUNDABLE_SHARE_CORPORATE * nonrefundable_utilization
    )


def _reec_baseline_M(
    target_year: int,
    demand_scenario: str = DEFAULT_REEC_DEMAND_SCENARIO,
    corp_subject_to_agi_limit: bool = False,
    effective_claim_share: float = 1.0,
    cgec_annual_growth: float = 0.030,
) -> Dict[str, float]:
    """REEC projected demand for target_year, with eligibility breakdown.

    Applies Hawaii nominal income growth and OBBBA demand-decay factors
    for the chosen scenario. Individual / residential claims grow with
    median income and decay per the §25D-termination scenarios;
    corporate / commercial claims grow with Hawaii business-sector
    nominal output and use the §48E-extension factor (no decay through
    forecast horizon).

    Parameters
    ----------
    effective_claim_share : float
        **Nonrefundable utilization rate** — the share of nonrefundable
        REEC claims that actually offset current-year tax (the rest
        carry forward or expire). Refundable claims always offset 100%.
        The aggregate effective share is computed per pool from the
        DOTAX 2023 refundable / nonrefundable split: individual REEC is
        ~23% refundable, corporate REEC is ~89% refundable, so a 0.80
        nonrefundable utilization rate yields ~85% effective for
        individual and ~98% effective for corporate. Default 1.0 makes
        the function backward-compatible (no utilization haircut).
    corp_subject_to_agi_limit : bool
        Whether corporate REEC claims are subject to the AGI limit. The
        bill text in §235-12.5(a) says "the taxpayer's adjusted gross
        income does not exceed $175,000 if filing as an individual, or
        $350,000 if filing jointly." Whether "AGI" applies coherently to
        a corporation is contested. Default False (treat corp claims as
        eligible regardless of AGI). Set True for a conservative scenario
        — assumes corporations get treated like high-income individuals
        and most ($200K+ bin = 56.1% eligible) claims are accepted.
    """
    g_individual = _hawaii_nominal_growth(target_year)
    g_corporate  = _hawaii_corporate_growth(target_year, annual_rate=cgec_annual_growth)
    d_individual = _reec_demand_factor(target_year, demand_scenario)
    d_corporate  = _reec_demand_factor_corporate(target_year)

    # Effective-revenue share differs by pool because the
    # refundable/nonrefundable mix differs (corporate is ~89% refundable,
    # individual is ~23% refundable). See module-level constants.
    eff_share_ind  = _effective_claim_share_individual(effective_claim_share)
    eff_share_corp = _effective_claim_share_corporate(effective_claim_share)

    combined_ind  = g_individual * d_individual * eff_share_ind
    combined_corp = g_corporate * d_corporate * eff_share_corp

    individual_eligible_2023   = _reec_eligible_individual_M()
    individual_ineligible_2023 = REEC_INDIVIDUAL_TOTAL_M - individual_eligible_2023

    # Corporate eligibility: by default, no AGI limit (full claim).
    # If corp_subject_to_agi_limit=True, treat corp claims as if they
    # were filers in the $200K+ AGI bin (56.1% eligible per PUMS).
    if corp_subject_to_agi_limit:
        TOP_AGI_BIN_ELIGIBLE_SHARE = REEC_INDIVIDUAL_BY_AGI_BIN[-1][2]  # 0.561
        corp_eligible_2023  = REEC_CORPORATE_TOTAL_M * TOP_AGI_BIN_ELIGIBLE_SHARE
        other_eligible_2023 = REEC_OTHER_TOTAL_M    * TOP_AGI_BIN_ELIGIBLE_SHARE
    else:
        corp_eligible_2023  = REEC_CORPORATE_TOTAL_M
        other_eligible_2023 = REEC_OTHER_TOTAL_M

    return {
        "growth_individual":      g_individual,
        "growth_corporate":        g_corporate,
        "demand_factor":           d_individual,
        "demand_factor_corporate": d_corporate,
        "effective_share_individual": eff_share_ind,
        "effective_share_corporate":  eff_share_corp,
        "individual_eligible":   individual_eligible_2023 * combined_ind,
        "individual_ineligible": individual_ineligible_2023 * combined_ind,
        "corporate":             REEC_CORPORATE_TOTAL_M * combined_corp,
        "corporate_eligible":    corp_eligible_2023 * combined_corp,
        "other":                 REEC_OTHER_TOTAL_M * combined_corp,
        "other_eligible":        other_eligible_2023 * combined_corp,
        "total_baseline":        (REEC_INDIVIDUAL_TOTAL_M * combined_ind
                                  + (REEC_CORPORATE_TOTAL_M + REEC_OTHER_TOTAL_M) * combined_corp),
        "total_eligible":        (individual_eligible_2023 * combined_ind
                                  + (corp_eligible_2023 + other_eligible_2023) * combined_corp),
    }


# ---------------------------------------------------------------------------
# Vintage carryforward simulation
# ---------------------------------------------------------------------------
# Background: §235-12.5(j) preserves "until exhausted" carryforward. The bill
# does not void pre-2027 vintages. The $40M cap (§235-12.5(c)) applies only to
# DBEDT certifications in cap years, not to utilization of credits certified
# in prior years (subsection (h) and the certification process in (f)-(g)
# confirm: the cap is on annual certification of NEW credits).
#
# Therefore state revenue cost in 2027-2030 = capped new credits + drawdown
# from all pre-cap vintage stock. The static overlay above misses the
# pre-existing stock channel. This vintage simulation captures it.
#
# The simulation tracks individual and corporate nonrefundable stock
# separately because they have different growth (income vs business),
# different demand decay (§25D vs §48E), and different effective claim
# shares.


def _historical_reec_individual(year: int, demand_scenario: str) -> float:
    """Total individual REEC claims ($M) for vintage `year`.

    Priority order:
        1. TY2023 — hardcoded DOTAX actual ($58.293M, Dec 2025 publication)
        2. TY2018-2022 — DOTAX actuals loaded from
           `dotax_reec_historical.csv`
        3. Pre-2018 — backcast at REEC_BACKCAST_GROWTH (3%/yr) from TY2018
           actual (or TY2023 if CSV unavailable)
        4. Post-2023 — projected via Hawaii nominal income growth × OBBBA
           demand factor for residential §25D channel
    """
    base = REEC_INDIVIDUAL_TOTAL_M  # TY2023 baseline $58.293M
    if year == 2023:
        return base
    if year in _DOTAX_HISTORICAL:
        return _DOTAX_HISTORICAL[year]["individual_total_$M"]
    if year < 2023:
        # Pre-2018 (or fallback if CSV missing): backcast from earliest anchor
        earliest_actual_year = min(_DOTAX_HISTORICAL) if _DOTAX_HISTORICAL else 2023
        anchor = (_DOTAX_HISTORICAL[earliest_actual_year]["individual_total_$M"]
                  if _DOTAX_HISTORICAL else base)
        return anchor / (1.0 + REEC_BACKCAST_GROWTH) ** (earliest_actual_year - year)
    g = _hawaii_nominal_growth(year)
    d = _reec_demand_factor(year, demand_scenario)
    return base * g * d


def _historical_reec_corporate(year: int, cgec_annual_growth: float) -> float:
    """Total corporate + other REEC claims ($M) for vintage `year`.

    Corporate REEC is volatile year-over-year (large commercial PV
    tax-equity transactions create lumpy claims). For TY2018-2022 we use
    `all_total - individual_total` from DOTAX, which captures both the
    reported corporate column and any disclosure-suppressed financial-corp
    rows that the disclosure-protected DOTAX corporate column omits.
    For TY2023, retain the hardcoded $41.8M (corporate $38.565M + other $3.217M)
    from the December 2025 publication.
    """
    base = REEC_CORPORATE_TOTAL_M + REEC_OTHER_TOTAL_M  # ~$41.8M
    if year == 2023:
        return base
    if year in _DOTAX_HISTORICAL:
        return _DOTAX_HISTORICAL[year]["corp_plus_other_$M"]
    if year < 2023:
        earliest_actual_year = min(_DOTAX_HISTORICAL) if _DOTAX_HISTORICAL else 2023
        anchor = (_DOTAX_HISTORICAL[earliest_actual_year]["corp_plus_other_$M"]
                  if _DOTAX_HISTORICAL else base)
        return anchor / (1.0 + REEC_BACKCAST_GROWTH) ** (earliest_actual_year - year)
    g = _hawaii_corporate_growth(year, annual_rate=cgec_annual_growth)
    d = _reec_demand_factor_corporate(year)
    return base * g * d


def _refundable_share_individual_for_eligibility(
    eligibility_share: float,
) -> float:
    """Estimate the refundable share of the individual REEC pool *after* the
    §235-12.5(a) AGI screen.

    The TY2023 hardcoded share (~23% refundable) is computed across the
    *whole* claimant pool. The AGI screen removes high-income / high-tax-
    liability filers — the very claimants who use nonrefundable credits
    efficiently. The remaining pool skews lower-income; under §235-12.5(l)
    filers below $20K single / $40K MFJ qualify automatically for
    refundability, and under §235-12.5(k) any filer can elect 30%-reduced
    refundability. Lower-income filers have less tax to offset → both
    elections become more attractive.

    Piecewise-linear estimate anchored to:
        eligibility_share=1.00 → 0.23 (full population, TY2023 actual)
        eligibility_share=0.80 → ~0.30
        eligibility_share=0.70 → ~0.35
        eligibility_share=0.50 → ~0.45
    Extrapolated linearly outside [0.50, 1.00]; clamped to [0.20, 0.60].
    """
    # Slope: (0.45 - 0.23) / (0.50 - 1.00) = -0.44 per unit
    base = 0.23 + 0.44 * (1.0 - eligibility_share)
    return max(0.20, min(0.60, base))


def _certified_credits_for_vintage(
    year: int,
    *,
    cap_enabled: bool,
    demand_scenario: str,
    cgec_annual_growth: float,
    corp_subject_to_agi_limit: bool,
    agi_eligibility_share: float | None = None,
    pro_rata_elasticity: float = 0.0,
    interpretation: str = "B",
) -> tuple[float, float, dict]:
    """Return (individual_certified, corporate_certified, diag) ($M) for vintage year.

    ``diag`` carries year-level diagnostics (nominal vs suppressed demand,
    binding ratio, eligibility share used).

    Under baseline (cap_enabled=False): full demand is certified.
    Under bill (cap_enabled=True):
      - TY < REEC_BILL_RETROACTIVE_YEAR (2026): full demand (bill not yet effective)
      - TY = 2026: interpretation "B" (default) keeps full demand (cap starts
        TY2027 by TY-mapping); interpretation "A" applies the $40M cap (since
        CY2027 cap = TY2026 certifications). Neither variant applies AGI
        (Section 9(1) defers AGI to TY2027+).
      - TY in [2027, REEC_SUNSET_LAST_VINTAGE] (2027-2029): AGI filter
        applied, $40M cap.
      - TY > REEC_SUNSET_LAST_VINTAGE: 0 (subsection (p) sunset overrides
        subsection (c)(4) for TY2030).

    Parameters
    ----------
    agi_eligibility_share : float | None
        Override for the individual REEC AGI-eligibility share. ``None``
        falls back to the static TY2023 PUMS-derived share
        (~0.796). Use with
        :func:`compute_dynamic_agi_eligibility_share` for per-year recompute.
    pro_rata_elasticity : float
        Demand elasticity to the pro-rata cap factor; 0.0 disables
        suppression (default). Positive values reduce certified demand
        when the cap binds.
    interpretation : "A" | "B"
        Whether CY in §235-12.5(c) maps to TY of certification (A) or TY of
        install (B, default). Affects only the TY2026 vintage.
    """
    ind_total = _historical_reec_individual(year, demand_scenario)
    corp_total = _historical_reec_corporate(year, cgec_annual_growth)

    diag = {
        "ind_total_nominal":  ind_total,
        "corp_total_nominal": corp_total,
        "eligibility_share":  1.0,
        "pro_rata_factor":    1.0,
        "suppression_factor": 1.0,
    }

    if not cap_enabled:
        return ind_total, corp_total, diag

    # Bill scenario
    if year > REEC_SUNSET_LAST_VINTAGE:
        return 0.0, 0.0, diag  # subsection (p) sunset

    if year < REEC_BILL_RETROACTIVE_YEAR:
        # Pre-2026: bill not yet effective
        return ind_total, corp_total, diag

    # TY2026 + TY2027-2029: certification-process and possibly cap apply
    apply_agi = year >= REEC_AGI_LIMIT_FIRST_YEAR
    apply_cap = (
        year >= REEC_AGI_LIMIT_FIRST_YEAR  # TY2027+ always capped
        or (year == REEC_BILL_RETROACTIVE_YEAR and interpretation == "A")
    )

    if apply_agi:
        if agi_eligibility_share is None:
            ind_elig_share = _reec_eligible_individual_M() / REEC_INDIVIDUAL_TOTAL_M
        else:
            ind_elig_share = float(agi_eligibility_share)
        ind_eligible = ind_total * ind_elig_share
        diag["eligibility_share"] = ind_elig_share
    else:
        ind_eligible = ind_total

    if apply_agi and corp_subject_to_agi_limit:
        corp_eligible = corp_total * REEC_INDIVIDUAL_BY_AGI_BIN[-1][2]
    else:
        corp_eligible = corp_total

    if not apply_cap:
        return ind_eligible, corp_eligible, diag

    total_eligible = ind_eligible + corp_eligible
    if total_eligible <= REEC_CAP_2027_2030_M:
        return ind_eligible, corp_eligible, diag

    # Pro-rata allocation per §235-12.5(h). Optionally apply endogenous
    # demand suppression: a rational filer at install time discounts the
    # nominal credit value by the binding ratio, and the marginal install
    # falls if expected value drops too far.
    pro_rata_nominal = REEC_CAP_2027_2030_M / total_eligible
    if pro_rata_elasticity > 0.0 and pro_rata_nominal < 1.0:
        suppression = pro_rata_nominal ** pro_rata_elasticity
        ind_eligible_supp = ind_eligible * suppression
        corp_eligible_supp = corp_eligible * suppression
        total_supp = ind_eligible_supp + corp_eligible_supp
        if total_supp <= REEC_CAP_2027_2030_M:
            diag["pro_rata_factor"] = 1.0
            diag["suppression_factor"] = suppression
            return ind_eligible_supp, corp_eligible_supp, diag
        pro_rata_final = REEC_CAP_2027_2030_M / total_supp
        diag["pro_rata_factor"] = pro_rata_final
        diag["suppression_factor"] = suppression
        return ind_eligible_supp * pro_rata_final, corp_eligible_supp * pro_rata_final, diag

    diag["pro_rata_factor"] = pro_rata_nominal
    return ind_eligible * pro_rata_nominal, corp_eligible * pro_rata_nominal, diag


def simulate_reec_state_cost_path(
    target_years: list[int],
    *,
    cap_enabled: bool,
    util_rate_individual: float,
    util_rate_corporate: float,
    demand_scenario: str,
    cgec_annual_growth: float,
    corp_subject_to_agi_limit: bool,
    agi_eligibility_by_year: dict[int, float] | None = None,
    pro_rata_elasticity: float = 0.0,
    interpretation: str = "B",
    dynamic_refundable_share: bool = False,
) -> dict[int, dict[str, float]]:
    """Simulate refundable + nonref-usage state cost path year-by-year.

    Returns dict {year: {certified, refundable, nonref_usage, total,
    end_stock_ind, end_stock_corp, ind_certified, corp_certified,
    eligibility_share, pro_rata_factor, suppression_factor}}.

    Stock dynamics (per pool, ind / corp):
        usage(t)       = util × (stock(t-1) + nonref_certs(t))
        end_stock(t)   = (1 - util) × (stock(t-1) + nonref_certs(t))
    Refundable certs are paid in full in year of certification.

    Parameters
    ----------
    agi_eligibility_by_year : dict[int, float] | None
        Per-year override for the individual AGI eligibility share. Keys
        should cover at least the AGI-relevant vintages (TY2027 onward).
        Missing years fall back to the static TY2023 share.
    pro_rata_elasticity, interpretation
        Passed through to :func:`_certified_credits_for_vintage`.
    dynamic_refundable_share : bool
        If True, recompute the individual refundable share each cert year
        as a function of the realized AGI-eligibility share (post-screen
        pool skews lower-income → higher refundable election rate).
        Default False (uses TY2023 hardcoded 23%).
    """
    if not target_years:
        return {}

    stock_ind = 0.0
    stock_corp = 0.0
    out: dict[int, dict[str, float]] = {}

    end_year = max(target_years)
    for y in range(REEC_SIM_START_YEAR, end_year + 1):
        elig_override = (
            agi_eligibility_by_year.get(y) if agi_eligibility_by_year else None
        )
        ind_cert, corp_cert, diag = _certified_credits_for_vintage(
            y,
            cap_enabled=cap_enabled,
            demand_scenario=demand_scenario,
            cgec_annual_growth=cgec_annual_growth,
            corp_subject_to_agi_limit=corp_subject_to_agi_limit,
            agi_eligibility_share=elig_override,
            pro_rata_elasticity=pro_rata_elasticity,
            interpretation=interpretation,
        )

        if dynamic_refundable_share and cap_enabled and y >= REEC_AGI_LIMIT_FIRST_YEAR:
            ind_ref_share = _refundable_share_individual_for_eligibility(
                diag["eligibility_share"]
            )
        else:
            ind_ref_share = REEC_REFUNDABLE_SHARE_INDIVIDUAL
        ind_nonref_share = 1.0 - ind_ref_share

        ind_ref      = ind_cert  * ind_ref_share
        ind_nonref   = ind_cert  * ind_nonref_share
        corp_ref     = corp_cert * REEC_REFUNDABLE_SHARE_CORPORATE
        corp_nonref  = corp_cert * REEC_NONREFUNDABLE_SHARE_CORPORATE

        ind_usage  = util_rate_individual * (stock_ind  + ind_nonref)
        corp_usage = util_rate_corporate  * (stock_corp + corp_nonref)

        stock_ind  = (1 - util_rate_individual) * (stock_ind  + ind_nonref)
        stock_corp = (1 - util_rate_corporate)  * (stock_corp + corp_nonref)

        if y in target_years:
            out[y] = {
                "ind_certified":  ind_cert,
                "corp_certified": corp_cert,
                "certified":     ind_cert + corp_cert,
                "refundable":    ind_ref + corp_ref,
                "nonref_usage":  ind_usage + corp_usage,
                "end_stock_ind":  stock_ind,
                "end_stock_corp": stock_corp,
                "total":         ind_ref + corp_ref + ind_usage + corp_usage,
                "eligibility_share":  diag["eligibility_share"],
                "pro_rata_factor":    diag["pro_rata_factor"],
                "suppression_factor": diag["suppression_factor"],
                "ind_ref_share":      ind_ref_share,
            }

    return out


def compute_credit_overlay(
    target_year: int,
    reec_demand_scenario: str = DEFAULT_REEC_DEMAND_SCENARIO,
    corp_subject_to_agi_limit: bool = False,
    reec_effective_claim_share: float = 1.0,
    cgec_annual_growth: float = 0.030,
    reec_carryforward_utilization_m: float = 0.0,
    model_carryforward_pool: bool = False,
    agi_eligibility_by_year: dict[int, float] | None = None,
    pro_rata_elasticity: float = 0.0,
    interpretation: str = "B",
    dynamic_refundable_share: bool = False,
    _ci_pass: bool = False,
) -> Dict[str, float]:
    """Compute SB 3125 CD1 credit-cap fiscal impact for ``target_year``.

    Positive values = revenue gained by the State.

    Parameters
    ----------
    target_year : int
        Tax year to score, e.g. 2027.
    reec_demand_scenario : str
        One of REEC_DEMAND_SCENARIOS keys. Default 'obbba_mid' applies
        Hawaii-tempered SEIA decay to REEC demand. Use 'pre_obbba' to
        match the pre-research baseline.
    corp_subject_to_agi_limit : bool
        Whether REEC corporate claims fall under the AGI limit (see
        ``_reec_baseline_M`` docstring). Default False.
    reec_carryforward_utilization_m : float
        TY2031 carryforward utilization ($M) under the static overlay
        (legacy path; ignored when ``model_carryforward_pool=True``).
        Default 0.0.
    model_carryforward_pool : bool
        If True, replace the static REEC savings calc with a vintage-level
        simulation that tracks nonrefundable stock from TY2010 onward and
        applies §235-12.5(j) "until exhausted" carryforward dynamics.
        Bill scenario: AGI filter from TY2027, $40M cap on TY2027-2029
        certifications, sunset for TY2030+ vintages. Baseline scenario:
        no filter, no cap. Reported savings = baseline state cost −
        scenario state cost, where state cost = refundable + nonref-stock
        usage in target_year. Pre-2027 stock drawdown is identical in
        both scenarios and cancels; the differential captures (a) ineligible
        demand permanently lost in cap years and (b) reduced future
        drawdown from capped vintages. Default False (legacy behavior).

    Returns a dict with the breakdown described in the module docstring.
    """
    if target_year < BASE_YEAR:
        raise ValueError(f"target_year must be >= {BASE_YEAR}, got {target_year}")

    growth_individual = _hawaii_nominal_growth(target_year)
    growth_corporate  = _hawaii_corporate_growth(target_year, annual_rate=cgec_annual_growth)

    # ---- Renewable Energy Tax Credit ------------------------------------
    reec = _reec_baseline_M(
        target_year, demand_scenario=reec_demand_scenario,
        corp_subject_to_agi_limit=corp_subject_to_agi_limit,
        effective_claim_share=reec_effective_claim_share,
        cgec_annual_growth=cgec_annual_growth,
    )

    cf_keys: Dict[str, float] = {}
    if model_carryforward_pool:
        # Vintage simulation: derive util rates per pool from the same
        # refundable/nonrefundable split logic as the static overlay.
        util_ind  = _effective_claim_share_individual(reec_effective_claim_share)
        util_corp = _effective_claim_share_corporate(reec_effective_claim_share)

        # The util_rate in the simulation is the *nonrefundable* utilization
        # rate (the rate at which stock is drawn down). Refundable claims
        # are paid in full in the year of certification (handled separately
        # inside the simulator). So pass the nonrefundable utilization rate
        # directly, not the blended effective share.
        sim_target_years = [target_year]
        sim_baseline = simulate_reec_state_cost_path(
            sim_target_years,
            cap_enabled=False,
            util_rate_individual=reec_effective_claim_share,
            util_rate_corporate=reec_effective_claim_share,
            demand_scenario=reec_demand_scenario,
            cgec_annual_growth=cgec_annual_growth,
            corp_subject_to_agi_limit=corp_subject_to_agi_limit,
            agi_eligibility_by_year=None,  # baseline never applies AGI filter
            pro_rata_elasticity=0.0,        # baseline has no cap, no suppression
            interpretation=interpretation,
            dynamic_refundable_share=False,
        )[target_year]
        sim_bill = simulate_reec_state_cost_path(
            sim_target_years,
            cap_enabled=True,
            util_rate_individual=reec_effective_claim_share,
            util_rate_corporate=reec_effective_claim_share,
            demand_scenario=reec_demand_scenario,
            cgec_annual_growth=cgec_annual_growth,
            corp_subject_to_agi_limit=corp_subject_to_agi_limit,
            agi_eligibility_by_year=agi_eligibility_by_year,
            pro_rata_elasticity=pro_rata_elasticity,
            interpretation=interpretation,
            dynamic_refundable_share=dynamic_refundable_share,
        )[target_year]

        reec_after_bill = sim_bill["total"]
        reec_baseline_cost = sim_baseline["total"]
        reec_savings = max(0.0, reec_baseline_cost - reec_after_bill)

        cf_keys = {
            "reec_baseline_state_cost_$M":  round(reec_baseline_cost, 2),
            "reec_scenario_state_cost_$M":  round(reec_after_bill, 2),
            "reec_scenario_new_certs_$M":   round(sim_bill["certified"], 2),
            "reec_scenario_refundable_$M":  round(sim_bill["refundable"], 2),
            "reec_scenario_nonref_usage_$M": round(sim_bill["nonref_usage"], 2),
            "reec_baseline_new_certs_$M":   round(sim_baseline["certified"], 2),
            "reec_baseline_refundable_$M":  round(sim_baseline["refundable"], 2),
            "reec_baseline_nonref_usage_$M": round(sim_baseline["nonref_usage"], 2),
            "reec_scenario_end_stock_$M":   round(
                sim_bill["end_stock_ind"] + sim_bill["end_stock_corp"], 2),
            "reec_baseline_end_stock_$M":   round(
                sim_baseline["end_stock_ind"] + sim_baseline["end_stock_corp"], 2),
            # Round-2 diagnostics
            "reec_eligibility_share":        round(sim_bill["eligibility_share"], 4),
            "reec_pro_rata_factor":          round(sim_bill["pro_rata_factor"], 4),
            "reec_suppression_factor":       round(sim_bill["suppression_factor"], 4),
            "reec_ind_refundable_share":     round(sim_bill["ind_ref_share"], 4),
            "reec_interpretation":           interpretation,
        }
    elif 2027 <= target_year <= 2030:
        # Cap binds on the eligible portion. Total revenue gain =
        # ineligible-by-AGI demand (filtered out entirely) +
        # excess of eligible demand over the cap.
        reec_after_bill = min(reec["total_eligible"], REEC_CAP_2027_2030_M)
        reec_savings = max(0.0, reec["total_baseline"] - reec_after_bill)
    elif target_year >= 2031:
        # Legacy path: new claims = $0; carryforward proxy via parameter.
        reec_after_bill = min(reec_carryforward_utilization_m, reec["total_baseline"])
        reec_savings = max(0.0, reec["total_baseline"] - reec_after_bill)
    else:
        reec_after_bill = reec["total_baseline"]
        reec_savings = max(0.0, reec["total_baseline"] - reec_after_bill)

    # ---- Capital Goods Excise Tax Credit --------------------------------
    # CGEC is mostly corporate / business-investment.
    # Apply effective_claim_share for nonrefundable utilization realism +
    # a year-varying pull-forward haircut: businesses accelerate capital
    # purchases into 2026-2027 to claim before sunset, depressing the
    # post-sunset baseline most heavily in the immediate aftermath and
    # fading as normal investment cadence resumes.
    if target_year <= CGEC_SUNSET_YEAR:
        PULL_FORWARD_HAIRCUT = 1.0
    elif target_year == CGEC_SUNSET_YEAR + 1:   # 2028
        PULL_FORWARD_HAIRCUT = 0.85
    elif target_year == CGEC_SUNSET_YEAR + 2:   # 2029
        PULL_FORWARD_HAIRCUT = 0.90
    else:                                        # 2030+
        PULL_FORWARD_HAIRCUT = 0.95
    cgec_baseline = (CGEC_TOTAL_M * growth_corporate
                     * reec_effective_claim_share * PULL_FORWARD_HAIRCUT)
    cgec_savings = cgec_baseline if target_year > CGEC_SUNSET_YEAR else 0.0

    # ---- Research Activities Credit -------------------------------------
    # TCRA baseline is $7M, $1M individual + $6M corporate. Use a
    # weighted growth factor: 1/7 individual + 6/7 corporate.
    tcra_growth = (1.0 * growth_individual + 6.0 * growth_corporate) / 7.0
    tcra_baseline = TCRA_TOTAL_M * tcra_growth
    # Bill accelerates repeal by 1 year. Difference applies in TY 2029 only.
    if target_year == TCRA_BILL_REPEAL_YEAR:  # 2029: bill repeals, baseline keeps
        tcra_savings = tcra_baseline
    else:
        tcra_savings = 0.0   # 2027-2028: both keep; 2030+: both repealed

    total_credit_savings = reec_savings + cgec_savings + tcra_savings

    result = {
        "growth_individual":     round(growth_individual, 4),
        "growth_corporate":      round(growth_corporate, 4),
        "reec_demand_factor":    round(reec["demand_factor"], 4),
        "reec_demand_scenario":  reec_demand_scenario,
        "corp_subject_to_agi_limit": corp_subject_to_agi_limit,
        "model_carryforward_pool":   model_carryforward_pool,
        # REEC breakdown
        "reec_individual_eligible_$M":   round(reec["individual_eligible"], 2),
        "reec_individual_ineligible_$M": round(reec["individual_ineligible"], 2),
        "reec_corporate_$M":             round(reec["corporate"], 2),
        "reec_corporate_eligible_$M":    round(reec["corporate_eligible"], 2),
        "reec_other_$M":                 round(reec["other"], 2),
        "reec_other_eligible_$M":        round(reec["other_eligible"], 2),
        "reec_baseline_$M":              round(reec["total_baseline"], 2),
        "reec_eligible_$M":              round(reec["total_eligible"], 2),
        "reec_after_bill_$M":            round(reec_after_bill, 2),
        "reec_carryforward_utilization_$M": round(min(reec_carryforward_utilization_m, reec["total_baseline"]), 2),
        "reec_savings_$M":               round(reec_savings, 2),
        # CGEC
        "cgec_baseline_$M":              round(cgec_baseline, 2),
        "cgec_savings_$M":               round(cgec_savings, 2),
        # TCRA
        "tcra_baseline_$M":              round(tcra_baseline, 2),
        "tcra_savings_$M":               round(tcra_savings, 2),
        # Totals
        "total_credit_savings_$M":       round(total_credit_savings, 2),
    }
    result.update(cf_keys)

    # ---- 90% CI on the income-linked channel (κ-calibrated) -------------
    # Rerun the full overlay with `_hawaii_nominal_growth` scaled to the
    # real-growth CI90 bounds from `get_hawaii_real_growth_detail` (ACS
    # forecast SE ⊕ κ-rescaled BLS CPI projection SE). The scale rides
    # through every growth call site — static overlay and vintage
    # simulation alike. Held at point: corporate growth (fixed-rate
    # assumption) and the annual-anchor re-inflate leg (no SE surface;
    # its error is also positively correlated with the deflator leg, so
    # treating it as independent would misstate the band in both
    # directions). The band therefore reflects the deflate-leg CPI and
    # ACS-forecast uncertainty only.
    if not _ci_pass:
        try:
            from tax_modeler.projection.income_forecast import (
                get_hawaii_real_growth_detail,
            )
            detail = get_hawaii_real_growth_detail(BASE_YEAR, target_year)
        except Exception as e:
            logger.debug("growth detail unavailable for CI pass: %s", e)
            detail = None
        if (detail is not None and detail.se_log_real
                and detail.real_ci90_low is not None
                and detail.real_ci90_high is not None
                and detail.real_factor > 0):
            rerun_kwargs = dict(
                reec_demand_scenario=reec_demand_scenario,
                corp_subject_to_agi_limit=corp_subject_to_agi_limit,
                reec_effective_claim_share=reec_effective_claim_share,
                cgec_annual_growth=cgec_annual_growth,
                reec_carryforward_utilization_m=reec_carryforward_utilization_m,
                model_carryforward_pool=model_carryforward_pool,
                agi_eligibility_by_year=agi_eligibility_by_year,
                pro_rata_elasticity=pro_rata_elasticity,
                interpretation=interpretation,
                dynamic_refundable_share=dynamic_refundable_share,
                _ci_pass=True,
            )
            bounds = {}
            for tag, bound in (("low", detail.real_ci90_low),
                               ("high", detail.real_ci90_high)):
                with _scaled_growth(bound / detail.real_factor):
                    bounds[tag] = compute_credit_overlay(target_year, **rerun_kwargs)
            # Savings are monotone in growth, but order defensively.
            sav = sorted((bounds["low"]["reec_savings_$M"],
                          bounds["high"]["reec_savings_$M"]))
            tot = sorted((bounds["low"]["total_credit_savings_$M"],
                          bounds["high"]["total_credit_savings_$M"]))
            result.update({
                "growth_individual_se_log":   round(detail.se_log_real, 6),
                "growth_individual_ci90_low":  round(
                    result["growth_individual"] * detail.real_ci90_low / detail.real_factor, 4),
                "growth_individual_ci90_high": round(
                    result["growth_individual"] * detail.real_ci90_high / detail.real_factor, 4),
                "reec_savings_ci90_low_$M":   sav[0],
                "reec_savings_ci90_high_$M":  sav[1],
                "total_credit_savings_ci90_low_$M":  tot[0],
                "total_credit_savings_ci90_high_$M": tot[1],
            })
    return result
