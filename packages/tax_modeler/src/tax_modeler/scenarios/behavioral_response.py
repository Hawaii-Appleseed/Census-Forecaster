"""Behavioral response module for SB 3125 CD1's new 13% top bracket.

Static-scoring forecasts overstate the revenue gain from a top-bracket rate
hike because high earners respond on multiple margins:

  1. **Taxable-income elasticity (ETI)** — They shift compensation
     (deferred comp, fringe benefits), realize fewer capital gains
     ("lock-in"), restructure pass-through income, and shelter via
     deductions / charitable giving. Standard ETI for top earners
     ranges 0.20-0.50 (Saez/Slemrod/Giertz 2012; Hawaii has limited
     state-level evidence).

  2. **Migration / domicile change** — They move to states with lower
     or no income tax. The US evidence puts the effect of a state
     top-rate increase on its millionaire population at roughly 0.1% to
     0.5% per percentage point: Young & Varner (2011), whose New Jersey
     semi-elasticities are in *percent* per point; Young, Varner, Lurie &
     Prisinzano (2016); Cohen, Lai & Steindel (2015); Rauh & Shyu (2024),
     an extra 0.8% of California's top bracket leaving once after a
     3-point rise. Until September 27, 2026 this module used 0.05-0.15
     as a *share* per point (10-30% of $1M+ filers for Act 24's 2
     points), about a hundred times the evidence; see
     BEHAVIORAL_ACCOUNTING_REVIEW.md.

  3. **Pass-through entity (PTE) election** — Hawaii's PTE election
     under HRS §235-110.93 lets pass-through businesses pay Hawaii
     tax at the entity level (currently 11%, the top individual
     rate) and SALT-deduct it federally. SB 3125 CD1's 13% individual
     rate creates a 2pp incentive to shift income through PTE. Most
     S-corps and partnerships with $1M+ owners would elect.

  4. **Capital-gains realization** — When the alternative tax rate on net
     long-term capital gains (HRS §235-51(f)) rises, filers realize fewer
     gains: gains fall by exp(−cg_beta × the rise in the state marginal
     rate on gains), the capital-gains page's response
     (``forecast_cg_rate_options.score_option``). Only systems scored with
     the statutory alternative tax whose gains rates differ use it, so it
     never moves Act 24 against Act 46 (both 7.25%). See
     ``apply_realization_response``.

This module applies these responses on top of the static
microsimulation, and ``score_with_response`` scores them. The
counterfactual is the baseline with nobody responding (the responses are
to the scenario's rate increases), so only the scenario is scored again
on the responded population: a filer who moves away costs their whole
tax, and income reported away costs the scenario's rate on it. Until
September 27, 2026 both systems were re-scored on the responded
population, which charged a migrant only the rate increase.

Behavioral scenarios (``BehavioralParams``; the revenue scenario LOW uses
``high``, MID ``mid``, HIGH ``low``). PTE capture is 0 in all three (Act 58):
    low       — eti=0.15, migration_elast=0.001,  cg_beta=1.6   (weak response)
    mid       — eti=0.40, migration_elast=0.0025, cg_beta=2.0
    high      — eti=0.60, migration_elast=0.01,   cg_beta=2.6   (strong response)

References:
  - Saez, Slemrod, Giertz (2012) "The Elasticity of Taxable Income with
    Respect to Marginal Tax Rates" J. Econ. Lit.
  - Young, Varner (2011) "Millionaire Migration and State Taxation of Top
    Incomes: Evidence from a Natural Experiment" National Tax Journal.
  - Young, Varner, Lurie, Prisinzano (2016) "Millionaire Migration and
    Taxation of the Elite: Evidence from Administrative Data" American
    Sociological Review.
  - Cohen, Lai, Steindel (2015) "A Replication of 'Millionaire Migration
    and State Taxation of Top Incomes'" Public Finance Review.
  - Rauh, Shyu (2024) "Behavioral Responses to State Income Taxation of
    High Earners: Evidence from California" AEJ: Economic Policy.
  - Hawaii DOTAX "Tax Credits Claimed by Hawaiʻi Taxpayers — Tax Year
    2023" (Dec 2025) for PTE base.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Mapping, Optional, Tuple
import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Bill parameters
# ---------------------------------------------------------------------------
# SB 3125 CD1 new top bracket thresholds (taxable income)
SB3125_TOP_THRESHOLDS = {
    "married_filing_jointly":      1_000_000,
    "qualifying_widow":            1_000_000,
    "head_of_household":             750_000,
    "single":                        500_000,
    "married_filing_separately":     500_000,
}
# Top marginal rate under each system
ACT46_TOP_RATE = 0.11      # current top rate under Act 46
SB3125_CD1_TOP_RATE = 0.13 # new top rate under bill

# Hawaii PTE election rate (HRS §235-110.93). FIXED at 9% per Act 50
# (TY 2024+); does NOT auto-track the top individual rate. SB 3125 CD1
# does not amend §235-110.93, so PTE rate stays at 9%.
# This creates a 4pp arbitrage gap (13% individual vs 9% PTE) under the
# bill — a much stronger incentive to elect than the 2pp gap I had
# initially assumed (when I mistakenly thought PTE rate was 11%).
PTE_RATE = 0.09


# ---------------------------------------------------------------------------
# Behavioral parameter scenarios
# ---------------------------------------------------------------------------
@dataclass
class BehavioralParams:
    """Behavioral elasticity parameters.

    NOTE on PTE capture: Hawaii Act 58 (SLH 2025, eff. TY2025) requires PTE
    members to add back their share of entity-level PTE tax to Hawaii taxable
    income before claiming the §235-110.93 credit. This makes a PTE election
    *more* expensive for Hawaii state purposes than paying individually at any
    rate ≥ 9%. Under SB 3125 CD1's 13% top bracket, a $1M filer pays ~$11,700
    MORE Hawaii tax by electing PTE (vs paying individually). Existing PTE
    elections are driven entirely by the federal SALT deduction (entity-level
    state tax bypasses the $10K SALT cap), which is unaffected by SB 3125.
    Therefore SB 3125 creates ZERO incremental incentive for new PTE elections
    on Hawaii state grounds, and ``pte_capture`` is set to 0 across scenarios.
    """
    eti: float                 # Taxable income elasticity (Saez/Slemrod/Giertz range: 0.15-0.50)
    migration_elast: float     # Share of $1M+ filers who leave per pp of top-rate increase, at full phase-in
    pte_capture: float         # Share of $1M+ pass-through income that elects PTE due to bill (Act 58: =0)
    migration_phase_in_years: int = 5  # Years to fully realize migration response
    # Capital-gains realization semi-elasticity: gains fall by exp(-cg_beta x
    # the rise in the marginal rate on gains). The capital-gains page's
    # 0.5-0.8 long-run elasticity range over the ~31% combined top rate
    # (forecast_cg_rate_options.py): 1.6 / 2.0 (its BETA) / 2.6.
    cg_beta: float = 0.0

    @classmethod
    def low(cls) -> "BehavioralParams":
        # Weak response: weak ETI; migration at the low end of the US
        # evidence (Young & Varner 2011: ~0.04% per effective pp a year).
        # PTE=0 (Act 58). Realization at the low end (elasticity ~0.5).
        return cls(eti=0.15, migration_elast=0.001, pte_capture=0.0, cg_beta=1.6)

    @classmethod
    def mid(cls) -> "BehavioralParams":
        # MID ETI within Saez/Slemrod/Giertz. Migration near the middle of
        # the US state-tax studies, rebased to the statutory top rate (Act 24
        # raises $1M+ filers' average rate ~0.9 pp for its 2 pp): Rauh/Shyu
        # 2024, Cohen/Lai/Steindel 2015, Young et al. 2016 (~0.002-0.005).
        # PTE=0 (Act 58 addback eliminates Hawaii arbitrage). Realization:
        # the capital-gains page's BETA (elasticity ~0.6).
        return cls(eti=0.40, migration_elast=0.0025, pte_capture=0.0, cg_beta=2.0)

    @classmethod
    def high(cls) -> "BehavioralParams":
        # Strong response. Migration at the upper end: New Jersey filers
        # earning all their income in-state (Young & Varner 2011, not
        # significant) and Young et al.'s flow cumulated over five years.
        # PTE=0 (Act 58). Realization at the high end (elasticity ~0.8).
        return cls(eti=0.60, migration_elast=0.01, pte_capture=0.0, cg_beta=2.6)

    @classmethod
    def static(cls) -> "BehavioralParams":
        """All zeros — replicates the original static-scoring estimate."""
        return cls(eti=0.0, migration_elast=0.0, pte_capture=0.0, cg_beta=0.0)

    @classmethod
    def named(cls, name: str) -> "BehavioralParams":
        m = {"low": cls.low, "mid": cls.mid, "high": cls.high, "static": cls.static}
        if name not in m:
            raise ValueError(f"Unknown behavioral scenario {name!r}; "
                             f"valid: {list(m.keys())}")
        return m[name]()


# ---------------------------------------------------------------------------
# ETI: taxable-income response to marginal rate change
# ---------------------------------------------------------------------------
#
# Standard form: %ΔTI = ETI × %Δ(1-MTR)
#     new_TI / old_TI = ((1 - scenario_mtr) / (1 - baseline_mtr)) ** eti
#
# Each filer's actual marginal rates under baseline and scenario are looked
# up from the bracket schedules (not assumed to be 11% → 13%). This matters
# because SB 3125 CD1 raises rates across $350K–$1M MFJ (and equivalents)
# in addition to creating the new 13% bracket above $1M; mid-tier filers
# face smaller-but-real rate changes that the prior threshold-based
# implementation ignored.


def _per_filer_marginal_rate(
    df: pd.DataFrame,
    *,
    config,
    calculator,
    income_col: str,
    fs_col: str,
    exemption_count_col: Optional[str],
) -> np.ndarray:
    """Vectorized marginal-rate lookup per filer under ``config``.

    Computes ``taxable = max(0, income − effective_deduction − exemption)``
    then locates each filer's bracket via ``searchsorted`` on bracket
    floors. Rates are read from the bracket CSV (stored as percentages,
    e.g. 11.0); converted to decimals (0.11). Returns an ``ndarray`` of
    decimal rates aligned with ``df``.

    Effective deduction is ``max(filing-status SD, hi_itemized_deduction)``
    when the ``hi_itemized_deduction`` column is present (a NaN raises, as in
    the revenue scorers). Exemptions come from ``exemption_count_col`` when
    given, else the same count the revenue scorers use (``exemption_counts``:
    a ``num_exemptions`` column, or filer + spouse + dependents).

    Filers with zero taxable income return MTR = 0.
    """
    from tax_modeler.config.tax_system_config import itemized_deductions
    from tax_modeler.liability.hawaii import exemption_counts

    incomes = df[income_col].to_numpy(dtype=float)
    statuses = df[fs_col].to_numpy()
    n = len(df)

    sd_per_filer = np.empty(n, dtype=float)
    for fs in np.unique(statuses):
        sd_per_filer[statuses == fs] = calculator.standard_deduction_for(config, fs)
    itemized = itemized_deductions(df)
    deductions = sd_per_filer if itemized is None else np.maximum(sd_per_filer, itemized)

    if exemption_count_col and exemption_count_col in df.columns:
        exemption_count = df[exemption_count_col].fillna(1).to_numpy(dtype=int)
    else:
        exemption_count = np.nan_to_num(exemption_counts(df), nan=1.0)
    exemption_amount = exemption_count * config.personal_exemption

    taxable = np.maximum(0.0, incomes - deductions - exemption_amount)

    mtrs = np.zeros(n, dtype=float)
    for fs in np.unique(statuses):
        brackets = calculator.brackets_for(config, fs)
        boundaries = brackets["income_min"].to_numpy(dtype=float)
        rates = brackets["rate_decimal"].to_numpy(dtype=float)
        mask = statuses == fs
        # searchsorted(side='right') returns idx with
        # boundaries[idx-1] <= taxable < boundaries[idx]; the bracket
        # the filer is in has rate rates[idx-1].
        idx = np.searchsorted(boundaries, taxable[mask], side="right") - 1
        idx = np.clip(idx, 0, len(boundaries) - 1)
        mtrs[mask] = rates[idx]

    mtrs[taxable <= 0] = 0.0
    return mtrs


def apply_eti_response(
    df: pd.DataFrame,
    params: BehavioralParams,
    *,
    baseline_cfg,
    scenario_cfg,
    calculator,
    income_col: str = "income",
    fs_col: str = "filing_status",
    exemption_count_col: Optional[str] = None,
    inplace: bool = False,
) -> pd.DataFrame:
    """Apply per-filer ETI shrinkage based on actual baseline → scenario MTR.

    For each filer whose marginal rate increases, multiply income by
    ``((1 − scen_mtr) / (1 − base_mtr)) ** eti``. Filers facing no rate
    change (or a rate cut) receive no income adjustment — the ETI
    literature is asymmetric and rate cuts have weaker, less established
    behavioral feedback than rate increases.

    Parameters
    ----------
    baseline_cfg, scenario_cfg : TaxSystemConfig
        The two systems whose bracket schedules define each filer's
        marginal rate. The bracket schedule is looked up via
        ``calculator.brackets_for(config, status)``.
    calculator : TaxCalculator
        Provides bracket lookup and standard-deduction lookup.

    Notes
    -----
    Effective deduction per filer is automatically computed as
    ``max(filing-status SD, hi_itemized_deduction)`` when the
    ``hi_itemized_deduction`` column is present. The former
    ``deduction_col`` parameter has been removed.
    """
    if params.eti <= 0:
        return df if inplace else df.copy()

    out = df if inplace else df.copy()
    out[income_col] = out[income_col].astype(float)

    base_mtr = _per_filer_marginal_rate(
        out, config=baseline_cfg, calculator=calculator,
        income_col=income_col, fs_col=fs_col,
        exemption_count_col=exemption_count_col,
    )
    scen_mtr = _per_filer_marginal_rate(
        out, config=scenario_cfg, calculator=calculator,
        income_col=income_col, fs_col=fs_col,
        exemption_count_col=exemption_count_col,
    )

    factor = np.ones(len(out), dtype=float)
    rate_up = (scen_mtr > base_mtr) & (base_mtr < 1.0) & (scen_mtr < 1.0)
    factor[rate_up] = (
        (1.0 - scen_mtr[rate_up]) / (1.0 - base_mtr[rate_up])
    ) ** params.eti

    out[income_col] = out[income_col].to_numpy() * factor
    out["_eti_factor"] = factor
    out["_eti_base_mtr"] = base_mtr
    out["_eti_scen_mtr"] = scen_mtr
    return out


# ---------------------------------------------------------------------------
# Migration: weight-reduction for top filers leaving Hawaii
# ---------------------------------------------------------------------------

_FRAME_STATUSES = ("single", "married_filing_jointly", "head_of_household",
                   "married_filing_separately", "qualifying_widow")
MIGRATION_FULL_TIER = 1_000_000   # AGI where the top-1% migration estimates apply


def top_rate_changes(baseline_cfg, scenario_cfg, calculator) -> Dict[str, Tuple[float, float, float]]:
    """Per filing status: (change in the top statutory rate in points,
    scenario's top-bracket floor, baseline's top-bracket floor). Used to
    generalize the migration and PTE responses beyond SB 3125's 11% -> 13%
    and $1M/$750K/$500K; for Act 24 vs Act 46 the first two are exactly those
    constants."""
    out = {}
    for fs in _FRAME_STATUSES:
        base = calculator.brackets_for(baseline_cfg, fs)
        scen = calculator.brackets_for(scenario_cfg, fs)
        out[fs] = (
            100.0 * (float(scen["rate_decimal"].iloc[-1]) - float(base["rate_decimal"].iloc[-1])),
            float(scen["income_min"].iloc[-1]),
            float(base["income_min"].iloc[-1]),
        )
    return out


def _phased_rate_change(path: Mapping[int, Mapping[str, Tuple[float, ...]]], fs: str,
                        target_year: int, phase_in_years: int) -> float:
    """Top-rate change (points) in effect for migration in ``target_year``:
    each year's increment phases in from the year it takes effect, so a
    rise that starts in 2029 is in its first year in 2029 whenever the plan
    itself starts. ``path`` maps each scored year to ``top_rate_changes``;
    years before a plan starts have a change of 0. For a change that is
    constant from the first year this is change x phase, the constant form."""
    total, prev = 0.0, 0.0
    for y in sorted(y for y in path if y <= target_year):
        pp = path[y][fs][0]
        total += (pp - prev) * min(1.0, (target_year - y + 1) / phase_in_years)
        prev = pp
    return total


def apply_migration_response(
    df: pd.DataFrame,
    params: BehavioralParams,
    *,
    target_year: int,
    bill_effective_year: int = 2027,
    income_col: str = "income",
    fs_col: str = "filing_status",
    weight_col: str = "weight",
    inplace: bool = False,
    baseline_cfg=None,
    scenario_cfg=None,
    calculator=None,
    top_rate_path: Optional[Mapping[int, Mapping[str, Tuple[float, ...]]]] = None,
) -> pd.DataFrame:
    """Reduce weights of $1M+ filers per migration elasticity, phased in.

    ``migration_elast`` is the share of $1M+ filers who leave per
    percentage point of top-rate increase, at full phase-in. SB 3125 CD1
    raises the top from 11% to 13% (2pp), so the long-run out-migration
    share is 2 × migration_elast (MID: 0.5%). We phase this in linearly
    over `migration_phase_in_years` from `bill_effective_year`.

    Migration applies to filers above the *MFJ* threshold ($1M) since
    that's where the literature's top-1% estimates apply. Lower thresholds
    (HoH $750K, Single $500K) mostly capture upper-middle earners with
    weaker migration response — we apply a discounted rate (50%) for
    those between the lower threshold and $1M.

    With ``baseline_cfg``/``scenario_cfg``/``calculator`` the response is
    derived from the two schedules (the tax simulator's specs): each filing
    status's change in the top statutory rate, full response at AGI at or
    above max($1M, the scenario's top-bracket floor), half from the higher of
    the scenario's and the baseline's top-bracket floors to $1M when that is
    lower (so a top bracket moved down to middle incomes does not apply a
    top-1% elasticity to them). Only increases move anyone (as with ETI), and
    no group loses more than all of its weight. For Act 24 vs Act 46 this is
    exactly the constant form below. Without them, the SB 3125 constants are
    used.

    ``top_rate_path`` ({year: top_rate_changes}) phases each year's change
    in from the year it takes effect, instead of phasing the current change
    from ``bill_effective_year`` (see ``_phased_rate_change``).
    """
    if params.migration_elast <= 0:
        return df if inplace else df.copy()

    out = df if inplace else df.copy()
    out[weight_col] = out[weight_col].astype(float)
    out["_migration_factor"] = 1.0

    # Phase-in: linear from year 1 of effect to year N
    years_since_effect = max(0, target_year - bill_effective_year)
    phase_frac = min(1.0, (years_since_effect + 1) / params.migration_phase_in_years)

    if baseline_cfg is None or scenario_cfg is None or calculator is None:
        # SB 3125 constants: 11% -> 13% for every status, its thresholds.
        rate_change_pp = (SB3125_CD1_TOP_RATE - ACT46_TOP_RATE) * 100.0  # 2.0
        changes = {fs: (rate_change_pp, SB3125_TOP_THRESHOLDS.get(fs, MIGRATION_FULL_TIER), 0.0)
                   for fs in _FRAME_STATUSES}
    else:
        changes = top_rate_changes(baseline_cfg, scenario_cfg, calculator)

    income = out[income_col].to_numpy(dtype=float)
    status = out[fs_col].to_numpy()
    factor = np.ones(len(out))
    for fs, (rate_change_pp, floor, base_floor) in changes.items():
        if top_rate_path is not None:
            phased_pp = _phased_rate_change(top_rate_path, fs, target_year,
                                            params.migration_phase_in_years)
        else:
            phased_pp = rate_change_pp * phase_frac if rate_change_pp > 0 else 0.0
        if phased_pp <= 0:
            continue
        # e.g. MID 0.0025 × 2.0 = 0.005 long run; × 0.6 phase-in = 0.003
        realised_loss = params.migration_elast * phased_pp
        is_fs = status == fs
        # Top tier: full migration loss (never more than everyone)
        factor[is_fs & (income >= max(MIGRATION_FULL_TIER, floor))] = max(0.0, 1.0 - realised_loss)
        # Upper tier (e.g. single $500K-$1M): half the migration response
        lower = max(floor, base_floor)
        if lower < MIGRATION_FULL_TIER:
            factor[is_fs & (income >= lower) & (income < MIGRATION_FULL_TIER)] = (
                max(0.0, 1.0 - realised_loss * 0.5))

    moved = factor != 1.0
    out.loc[moved, weight_col] = out.loc[moved, weight_col] * factor[moved]
    out.loc[moved, "_migration_factor"] = factor[moved]
    return out


# ---------------------------------------------------------------------------
# Capital-gains realization: fewer gains realized at a higher gains rate
# ---------------------------------------------------------------------------

def apply_realization_response(
    df: pd.DataFrame,
    pre: pd.DataFrame,
    params: BehavioralParams,
    *,
    baseline_cfg,
    scenario_cfg,
    calculator,
    income_col: str = "income",
    fs_col: str = "filing_status",
    cg_col: str = "synthetic_cg_share",
    inplace: bool = False,
) -> pd.DataFrame:
    """Shrink realized capital gains when the alternative tax rate rises.

    The capital-gains page's response (``forecast_cg_rate_options.score_option``):
    with ``m`` each filer's bracket rate under the scenario's schedule at
    *pre-response* taxable income (``pre``, the frame before ETI and
    migration), the marginal rate on gains under alternative rate ``c`` is
    ``τ(c) = min(m, c)`` (``m`` with no alternative tax), and

        d    = max(0, τ(c_scenario) − τ(c_baseline))
        g    = income × share            (``df``: after ETI, so gains
                                          already shrink with income)
        kept = g × exp(−cg_beta × d)
        income′ = income − (g − kept),  share′ = kept / income′

    Both τ terms use the scenario's schedule, so a plan that changes only
    the brackets gets no realization response (ETI and migration price it),
    and a plan that changes only the gains rate gets exactly the page's.
    Only rows with ``d > 0`` and gains change; elsewhere ``g / income`` can
    differ from the share by a rounding error, which would move a plan with
    no rise. A cut gets no response (``d`` is clipped at 0), as the other
    channels give cuts none.

    A no-op unless ``cg_beta > 0``, both systems use the statutory
    alternative tax (``cg_alt_tax == "statute"``) and their rates differ:
    Act 24 against Act 46 (both 7.25%) is never moved by this.
    """
    c0, c1 = baseline_cfg.capital_gains_rate_pct, scenario_cfg.capital_gains_rate_pct
    if (params.cg_beta <= 0 or baseline_cfg.cg_alt_tax != "statute"
            or scenario_cfg.cg_alt_tax != "statute" or c0 == c1 or cg_col not in df.columns):
        return df if inplace else df.copy()

    out = df if inplace else df.copy()
    m1 = _per_filer_marginal_rate(
        pre, config=scenario_cfg, calculator=calculator,
        income_col=income_col, fs_col=fs_col, exemption_count_col=None,
    )

    def gains_rate(cfg) -> np.ndarray:
        cap = cfg.cg_alt_rate
        return m1 if cap is None else np.minimum(m1, cap)

    d = np.maximum(0.0, gains_rate(scenario_cfg) - gains_rate(baseline_cfg))
    income = out[income_col].to_numpy(dtype=float).copy()
    share = out[cg_col].to_numpy(dtype=float).copy()
    hit = (d > 0) & (share > 0)
    if hit.any():
        y = income[hit]
        g = y * share[hit]
        kept = g * np.exp(-params.cg_beta * d[hit])
        income[hit] = y - (g - kept)
        share[hit] = kept / income[hit]
        out[income_col] = income
        out[cg_col] = share
    return out


# ---------------------------------------------------------------------------
# PTE election shift: revenue moves from individual to PTE form
# ---------------------------------------------------------------------------

def estimate_pte_election_shift_M(
    df: pd.DataFrame,
    params: BehavioralParams,
    *,
    income_col: str = "income",
    fs_col: str = "filing_status",
    weight_col: str = "weight",
    baseline_cfg=None,
    scenario_cfg=None,
    calculator=None,
) -> Dict[str, float]:
    """Estimate revenue *reduction* from PTE election under SB 3125 CD1.

    With the two configs, the top-bracket floors and the gap to the PTE rate
    come from the scenario's schedule instead of SB 3125's constants, for
    filing statuses whose top rate rises (as with migration, a plan that
    does not raise the top rate creates no new incentive). Every scenario
    sets ``pte_capture`` to 0 (Act 58 addback), so this is zero.

    Mechanism: Pass-through owners with income above the new 13% threshold
    have a 4pp incentive (13% individual → 9% PTE) to elect the PTE. We assume:

      - Share of $1M+ **ordinary** income that is pass-through-eligible: 40%
        (national IRS SOI 2022: pass-through is ~35-50% of top-1% income;
        Hawaii skews slightly lower due to wage-heavy economy).
      - Of eligible pass-through income, `pte_capture` share elects.
      - Revenue lost = (captured income above threshold) × (13% − 9%)
        = captured income × 0.04

    Capital gains are excluded from the election pool for two reasons:
      1. CG income is not pass-through "business" income eligible for
         entity-level election under HRS §235-110.93.
      2. Even where K-1 capital gains could theoretically be elected,
         the Hawaii CG cap (7.25%, HRS §235-51(f)) is *below* the PTE rate
         (9%), so rational filers would not elect PTE for CG income —
         it would raise their tax on that income.

    Threshold comparison uses total income (correct — that determines
    bracket placement), but excess is computed on ordinary income only.

    Returns:
      pte_eligible_income_$M:    ordinary pass-through income above threshold
      pte_elected_income_$M:     income that actually elects ( × pte_capture)
      pte_revenue_loss_$M:       revenue moving from individual to PTE form
    """
    if params.pte_capture <= 0:
        return {
            "pte_eligible_income_$M": 0.0,
            "pte_elected_income_$M":  0.0,
            "pte_revenue_loss_$M":    0.0,
        }

    PASS_THROUGH_SHARE = 0.20  # Hawaii IRS SOI 2022: partnership/S-corp = 12.6% of total income for $200K+ filers (~15% of ordinary income); national 0.40 overstates Hawaii's wage-heavy high-income mix
    if baseline_cfg is None or scenario_cfg is None or calculator is None:
        thresholds = SB3125_TOP_THRESHOLDS
        differential = {fs: SB3125_CD1_TOP_RATE - PTE_RATE for fs in thresholds}  # 0.04
    else:
        thresholds, differential = {}, {}
        for fs, (pp, floor, _) in top_rate_changes(baseline_cfg, scenario_cfg, calculator).items():
            if pp <= 0:          # no rise in the top rate, no new reason to elect
                continue
            scen = calculator.brackets_for(scenario_cfg, fs)
            thresholds[fs] = floor
            differential[fs] = max(0.0, float(scen["rate_decimal"].iloc[-1]) - PTE_RATE)

    # Ordinary income = total income minus capital gains.
    # synthetic_cg_share is set for $1M+ synthetic filers; base PUMS units
    # default to 0 (no CG separation available from ACS).
    if "synthetic_cg_share" in df.columns:
        cg_share = df["synthetic_cg_share"].fillna(0.0)
        ordinary_income = df[income_col] * (1.0 - cg_share)
    else:
        ordinary_income = df[income_col]

    excess_income_total = 0.0
    loss_total = 0.0
    for fs, threshold in thresholds.items():
        # Threshold check: use total income (bracket placement)
        mask = (df[fs_col] == fs) & (df[income_col] > threshold)
        if not mask.any():
            continue
        # Excess: ordinary income above threshold only
        excess_ordinary = (ordinary_income.loc[mask] - threshold).clip(lower=0)
        excess = float((excess_ordinary * df.loc[mask, weight_col]).sum())
        excess_income_total += excess
        loss_total += excess * differential[fs]

    pte_eligible = excess_income_total * PASS_THROUGH_SHARE
    pte_elected = pte_eligible * params.pte_capture
    pte_loss_dollars = loss_total * PASS_THROUGH_SHARE * params.pte_capture

    return {
        "pte_eligible_income_$M": pte_eligible / 1e6,
        "pte_elected_income_$M":  pte_elected / 1e6,
        "pte_revenue_loss_$M":    pte_loss_dollars / 1e6,
    }


# ---------------------------------------------------------------------------
# Top-income growth premium
# ---------------------------------------------------------------------------
# B19013 (median household income) under-projects top-earner growth.
#
# Empirical basis (IRS SOI Annual Bulletin, 2012–2019 pre-policy window):
#   National: $500K+ AGI bracket averaged ~3.0%/yr real growth vs ~1.2%/yr
#   for the median — a structural differential of ~1.8pp/yr.
#   Hawaii haircut: ~0.5pp discount for outmigration pressure (high-earner
#   departure suppresses local top-income growth relative to national trend).
#   → MID calibration: 1.8 – 0.5 = +1.3pp/yr above median.
#
# Scenario bounds (symmetric ±1.0pp around MID):
#   LOW  +0.3%/yr  — strong outmigration, weaker top-income growth
#   MID  +1.3%/yr  — empirically anchored (IRS SOI differential – Hawaii haircut)
#   HIGH +2.3%/yr  — Hawaii top-income growth returns toward national rates
#
# Source: Piketty-Saez-Zucman (PSZ) "Distributional National Accounts"
#   (top-1% 1979-2019: +2.3pp); IRS SOI 2012–2019 national bracket data
#   ($500K+ vs median: +1.8pp); Young & Varner Hawaii outmigration estimates.
#
# Base year 2024: the PUMS panel is the ACS 2020-2024 5-year vintage,
# inflation-adjusted to 2024 dollars, and the county B19013 projector
# anchors on the most recent 1-year ACS observation (2024). The premium
# represents the top-vs-median growth differential layered on top of the
# county-anchored projection, so it must compound from the same anchor.
TOP_INCOME_PREMIUM_BASE_YEAR = 2024
TOP_INCOME_PREMIUM_THRESHOLD = 500_000   # apply above this AGI
TOP_INCOME_PREMIUM_RATE = 0.013          # MID: 1.3pp/yr (IRS SOI empirical)


def apply_top_income_growth_premium(
    df: pd.DataFrame,
    *,
    target_year: int,
    base_year: int = TOP_INCOME_PREMIUM_BASE_YEAR,
    annual_premium: float = TOP_INCOME_PREMIUM_RATE,
    threshold: float = TOP_INCOME_PREMIUM_THRESHOLD,
    income_col: str = "income",
    inplace: bool = False,
) -> pd.DataFrame:
    """Scale top-earner incomes by a compounding premium over median growth.

    The base projection uses county-level B19013 (median household income),
    which under-projects top earners. This function applies a separate
    multiplier to filers above ``threshold`` to reflect the historical
    top-1% / top-0.1% income growth differential over median.

    Empirical basis: IRS SOI 2012–2019 national bracket data shows $500K+
    AGI growing ~1.8pp/yr faster than median. A 0.5pp Hawaii outmigration
    haircut (Young & Varner) yields the MID calibration of +1.3pp/yr.
    LOW/HIGH are ±1.0pp symmetric bounds: +0.3% and +2.3%.

    Multiplier = (1 + annual_premium) ** (target_year - base_year)
    For MID 1.3pp/yr from 2024 to 2027: 1.013^3 = 1.040 (+4.0%)
    """
    if annual_premium == 0 or target_year <= base_year:
        return df if inplace else df.copy()
    years = target_year - base_year
    multiplier = (1.0 + annual_premium) ** years
    out = df if inplace else df.copy()
    # Ensure income column accepts float multiplications without dtype-upcast errors
    out[income_col] = out[income_col].astype(float)
    mask = out[income_col] >= threshold
    out.loc[mask, income_col] = out.loc[mask, income_col] * multiplier
    out["_top_income_premium"] = 1.0
    out.loc[mask, "_top_income_premium"] = multiplier

    # Premium changed AGI; downstream tax columns are now stale on the
    # affected rows. Clear them so any consumer reading them without
    # first calling _compute_base_tax() gets NaN (loud failure) instead
    # of a silently wrong pre-premium value.
    _STALE_COLS = (
        "hi_tax_liability", "hi_taxable_income", "hi_tax_before_credits",
        "hi_low_income_credit", "hi_standard_deduction",
        "hi_itemized_deduction", "hi_cg_cap_savings", "hi_state_tax",
    )
    for col in _STALE_COLS:
        if col in out.columns:
            out.loc[mask, col] = float("nan")

    return out


# ---------------------------------------------------------------------------
# Itemized-deduction adjustment for top filers
# ---------------------------------------------------------------------------
# Top earners itemize at near-100% rates (IRS SOI: ~99% of $1M+ filers
# itemize federally). They typically have $50K-$200K of itemized deductions
# (Hawaii: federal income tax paid + property tax + mortgage interest +
# charitable). My synthesis applies the standard deduction ($16K MFJ for
# 2027), which OVERSTATES the 13%-bracket taxable base.
#
# We approximate the effect by reducing reported income by an "itemized
# premium" — the additional deduction beyond the standard deduction —
# for filers above the relevant top-bracket threshold. This shrinks
# their 13%-bracket-taxable income proportionally.
#
# Defaults: $80K MFJ premium above $1M, $40K Single/HoH above $500K-750K.
# These approximate the median itemized-vs-standard differential for
# Hawaii top filers based on IRS SOI Hawaii Table 2 (2022).
ITEMIZED_PREMIUM_DEFAULTS = {
    "married_filing_jointly":     80_000.0,
    "qualifying_widow":           80_000.0,
    "head_of_household":          50_000.0,
    "single":                     40_000.0,
    "married_filing_separately":  40_000.0,
}


def apply_itemized_deduction_adjustment(
    df: pd.DataFrame,
    *,
    premium_by_status: Optional[Dict[str, float]] = None,
    threshold_by_status: Optional[Dict[str, float]] = None,
    income_col: str = "income",
    fs_col: str = "filing_status",
    inplace: bool = False,
) -> pd.DataFrame:
    """Reduce reported income of top filers by an implied itemized premium.

    The microsim's tax calculator applies the *standard deduction* to
    every filer to derive taxable income. Top earners actually itemize
    much larger deductions; their taxable income is therefore lower
    than the calculator estimates. To compensate, we subtract the
    "itemized premium" (itemized − standard) from `income` for filers
    above the bill's threshold, so that downstream tax computation
    arrives at a reasonable taxable income.

    This applies symmetrically to baseline AND scenario, so the bill's
    rate cuts on the lower brackets are correctly priced too.
    """
    premiums = premium_by_status or ITEMIZED_PREMIUM_DEFAULTS
    thresholds = threshold_by_status or SB3125_TOP_THRESHOLDS
    out = df if inplace else df.copy()
    out[income_col] = out[income_col].astype(float)
    out["_itemized_premium"] = 0.0
    for fs, prem in premiums.items():
        thr = thresholds.get(fs, 1_000_000.0)
        mask = (out[fs_col] == fs) & (out[income_col] > thr)
        if not mask.any():
            continue
        out.loc[mask, income_col] = (out.loc[mask, income_col] - prem).clip(lower=0)
        out.loc[mask, "_itemized_premium"] = prem
    return out


# ---------------------------------------------------------------------------
# Combined behavioral response
# ---------------------------------------------------------------------------

def apply_behavioral_response(
    df: pd.DataFrame,
    params: BehavioralParams,
    *,
    target_year: int,
    baseline_cfg,
    scenario_cfg,
    calculator,
    bill_effective_year: int = 2027,
    income_col: str = "income",
    fs_col: str = "filing_status",
    weight_col: str = "weight",
    top_rate_path: Optional[Mapping[int, Mapping[str, Tuple[float, ...]]]] = None,
) -> Tuple[pd.DataFrame, Dict[str, float]]:
    """Apply ETI, migration and capital-gains realization adjustments, in
    that order, and report the PTE shift.

    Returns (adjusted_df, diagnostics_dict).

    The PTE shift is reported separately because it represents revenue
    that moves to a different tax base (the PTE entity-level tax), not
    revenue that disappears. The caller subtracts it from the bracket
    delta as a correction.

    ``baseline_cfg``, ``scenario_cfg``, and ``calculator`` are required so
    that ETI shrinkage can be applied based on each filer's actual
    marginal-rate change rather than a hard-coded 11% → 13% assumption.

    Effective deduction per filer is automatically computed as
    ``max(filing-status SD, hi_itemized_deduction)`` when the
    ``hi_itemized_deduction`` column is present. The former
    ``deduction_col`` parameter has been removed.
    """
    out = df.copy()
    out = apply_eti_response(
        out, params,
        baseline_cfg=baseline_cfg, scenario_cfg=scenario_cfg, calculator=calculator,
        income_col=income_col, fs_col=fs_col,
        inplace=True,
    )
    out = apply_migration_response(
        out, params, target_year=target_year,
        bill_effective_year=bill_effective_year,
        income_col=income_col, fs_col=fs_col, weight_col=weight_col, inplace=True,
        baseline_cfg=baseline_cfg, scenario_cfg=scenario_cfg, calculator=calculator,
        top_rate_path=top_rate_path,
    )
    # Realization last, so ETI and migration see the same inputs whatever
    # the gains rate does; its marginal rate is read on the frame before any
    # response (df).
    out = apply_realization_response(
        out, df, params,
        baseline_cfg=baseline_cfg, scenario_cfg=scenario_cfg, calculator=calculator,
        income_col=income_col, fs_col=fs_col, inplace=True,
    )

    # PTE shift estimated on the post-response income base
    # (so we don't double-count income that already left)
    pte = estimate_pte_election_shift_M(
        out, params, income_col=income_col, fs_col=fs_col, weight_col=weight_col,
        baseline_cfg=baseline_cfg, scenario_cfg=scenario_cfg, calculator=calculator,
    )

    # Report top-bracket diagnostics
    mask_top = out[income_col] >= 1_000_000
    diag = {
        "scenario_eti":              params.eti,
        "scenario_migration_elast":  params.migration_elast,
        "scenario_pte_capture":      params.pte_capture,
        "filers_1m_post_response":   float(out.loc[mask_top, weight_col].sum()),
        "income_1m_post_response_$M": float(
            (out.loc[mask_top, income_col] * out.loc[mask_top, weight_col]).sum()
        ) / 1e6,
        **pte,
    }
    return out, diag


def top_rate_path(baseline_for, scenario_for, years, calculator) -> Dict[int, Dict[str, Tuple[float, float, float]]]:
    """``top_rate_changes`` for each of ``years`` ({year: {status: ...}}),
    for ``apply_migration_response``'s ``top_rate_path``: each change in the
    top rate phases in from the year it takes effect. ``years`` must start
    no later than the first year the scenario differs from the baseline."""
    return {y: top_rate_changes(baseline_for(y), scenario_for(y), calculator) for y in years}


def score_with_response(
    df: pd.DataFrame,
    params: BehavioralParams,
    *,
    target_year: int,
    baseline_cfg,
    scenario_cfg,
    calculator,
    top_rate_path: Optional[Mapping[int, Mapping[str, Tuple[float, ...]]]] = None,
    bill_effective_year: int = 2027,
) -> Tuple[Dict[str, float], pd.DataFrame, Dict[str, float]]:
    """Revenue under both systems, static and after the behavioral response.

    The counterfactual is the baseline with nobody responding: the responses
    are to the scenario's rate increases, so after them only the scenario is
    scored again::

        static     = Σ w T_scen(y)   − Σ w T_base(y)
        behavioral = Σ w′ T_scen(y′) − Σ w T_base(y) − PTE shift

    A filer who moves away costs their whole tax, and income reported away
    costs the scenario's rate on it (t1·dy). Until September 27, 2026 the
    Act 24 pipeline and the tax simulator re-scored both systems on the
    responded population, Σ w′(T_scen − T_base)(y′), which charged a migrant
    only the rate increase (BEHAVIORAL_ACCOUNTING_REVIEW.md).

    The one implementation for ``forecast_sb3125_enhanced.py`` and the tax
    simulator's reference scorer (``tax_modeler.simulator.score``), which
    ``site/assets/simulator/kernel.js`` mirrors.

    Returns ``(revenue, adjusted, diag)``. ``revenue`` holds, in $M,
    ``baseline_$M``, ``reform_$M``, ``static_$M``, ``reform_post_$M`` (the
    scenario on the responded population) and ``behavioral_$M``;
    ``adjusted`` is the responded population and ``diag`` the
    ``apply_behavioral_response`` diagnostics.
    """
    from tax_modeler.config.tax_system_config import compare_systems

    static = compare_systems(df, baseline_cfg, scenario_cfg, calculator=calculator)
    baseline = float(static.iloc[0]["revenue_millions"])
    adjusted, diag = apply_behavioral_response(
        df, params, target_year=target_year, baseline_cfg=baseline_cfg,
        scenario_cfg=scenario_cfg, calculator=calculator,
        bill_effective_year=bill_effective_year, top_rate_path=top_rate_path,
    )
    reform_post = float(calculator.calculate_revenue_vectorized(
        adjusted, scenario_cfg)["total_revenue_millions"])
    revenue = {
        "baseline_$M": baseline,
        "reform_$M": float(static.iloc[1]["revenue_millions"]),
        "static_$M": float(static.iloc[2]["revenue_millions"]),
        "reform_post_$M": reform_post,
        "behavioral_$M": reform_post - baseline - diag["pte_revenue_loss_$M"],
    }
    return revenue, adjusted, diag
