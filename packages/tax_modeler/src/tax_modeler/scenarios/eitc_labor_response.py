"""Single-mother LFP behavioral response to a Hawaii state EITC rate cut.

Extensive-margin labor-supply response: when the combined federal + Hawaii
EITC value falls, some single-mother heads-of-household exit the labor
force. This module computes the *expected* per-filer resource loss from
that response and writes it to a column that the
``hi_eitc_revert_20_behavioral`` scenario in
:mod:`tax_modeler.poverty.impact` consumes.

The implementation operates on the tax-unit frame **before** SPM-unit
aggregation, so the loss propagates cleanly through
:func:`tax_modeler.poverty.spm_aggregation.aggregate_to_spm_units` (the
``lfp_behavioral_resource_loss`` column is summed across tax units in
each SPM unit; see ``_SUM_COLS`` in that module).

Method
------
For each HoH filer with ``earned_income > 0`` and ``hi_eitc_amount > 0``:

  p_exit  = elasticity × |log((1 + hi_rate_scenario) / (1 + hi_rate_baseline))|

  loss_if_exit = earned_income            # cash earnings lost
               + eitc_amount              # federal EITC zeros on exit
               + 0.5 × hi_eitc_amount     # remaining HI EITC at 20% rate
               - FICA_RATE × earned_income # payroll tax no longer paid
               - federal_tax_liability    # federal income tax saved

  expected_loss = p_exit × loss_if_exit

The 50%-of-HI-EITC static term is **already** subtracted by the
``hi_eitc_revert_20`` branch in ``_scenario_resources``. The behavioral
term we write here additionally captures the resource loss for the
fraction of HoH filers who exit the LFP entirely.

Elasticity
----------
Default ``elasticity = 0.5`` is the Meyer & Rosenbaum (2001, QJE)
midpoint for single-mother labor-force-participation response to EITC
value. Literature range: 0.3–0.7 (Eissa & Liebman 1996 QJE, Hoynes &
Patel 2018 AEJ Applied). With the default 40%→20% revert, p_exit ≈
0.077 (~7.7% of affected HoH filers).

Caveats
-------
* **Scope** is filing-status HoH (single-mother proxy). Includes single
  fathers (~10% of HoH per HI ACS S1101) — overstates response by ~10%.
* **Linear approximation** of FICA + federal-tax savings using baseline
  values. Most affected single-mother filers fall below the federal
  standard deduction, so ``federal_tax_liability ≈ 0`` already.
* **Excludes** intensive-margin (hours), marriage, and fertility
  responses, and SNAP/UI uptake from new non-workers. The first three
  are smaller in the literature; ignoring SNAP/UI uptake produces a
  slight upward bias on post-response poverty (a marginal LFP exiter
  often qualifies for SNAP that partially replaces the lost EITC +
  earnings).

References
----------
* Meyer, B. D. & Rosenbaum, D. T. (2001). "Welfare, the Earned Income
  Tax Credit, and the Labor Supply of Single Mothers." Quarterly Journal
  of Economics 116(3): 1063–1114.
* Eissa, N. & Liebman, J. B. (1996). "Labor Supply Response to the
  Earned Income Tax Credit." Quarterly Journal of Economics 111(2):
  605–637.
* Hoynes, H. & Patel, A. J. (2018). "Effective Policy for Reducing
  Poverty and Inequality? The Earned Income Tax Credit and the
  Distribution of Income." American Economic Journal: Applied Economics
  10(4): 174–212.
"""
from __future__ import annotations

import logging
import math

import numpy as np
import pandas as pd

from tax_modeler.poverty.spm import _EMPLOYEE_FICA_RATE

logger = logging.getLogger(__name__)


LFP_LOSS_COLUMN = "lfp_behavioral_resource_loss"
LFP_EXIT_PROB_COLUMN = "_lfp_exit_prob"
LFP_SNAP_OFFSET_COLUMN = "lfp_behavioral_snap_offset"
INTENSIVE_LOSS_COLUMN = "intensive_resource_loss"


def apply_hi_eitc_lfp_response(
    df: pd.DataFrame,
    *,
    elasticity: float = 0.5,
    hi_rate_baseline: float = 0.40,
    hi_rate_scenario: float = 0.20,
    earned_income_col: str = "earned_income",
    eitc_col: str = "eitc_amount",
    hi_eitc_col: str = "hi_eitc_amount",
    federal_tax_col: str = "federal_tax_liability",
    filing_status_col: str = "filing_status",
    weight_col: str = "weight",
    inplace: bool = False,
) -> tuple[pd.DataFrame, dict]:
    """Compute expected per-filer SPM-resource loss from LFP exit response.

    Writes ``lfp_behavioral_resource_loss`` (and the diagnostic
    ``_lfp_exit_prob``) to the returned frame. Non-affected filers get
    zero. See module docstring for the formula.

    Parameters
    ----------
    df:
        Tax-unit frame after federal EITC, HI EITC, federal income tax,
        and SPM-input enrichment. Must carry ``earned_income``,
        ``eitc_amount``, ``hi_eitc_amount``, ``filing_status``, and
        ``weight``. ``federal_tax_liability`` is optional (treated as 0
        if missing).
    elasticity:
        Single-mother LFP elasticity wrt EITC value. Default 0.5
        (Meyer-Rosenbaum midpoint). Setting to 0 produces all-zero loss.
    hi_rate_baseline, hi_rate_scenario:
        HI state EITC rate as fraction of federal — current law (0.40)
        and scenario (0.20). Used in the Δlog(EITC) factor.
    inplace:
        Modify ``df`` in place if True. Default False.

    Returns
    -------
    (df, diagnostics):
        df has the two new columns; diagnostics summarizes affected
        population and aggregate loss.
    """
    out = df if inplace else df.copy()

    required = [earned_income_col, eitc_col, hi_eitc_col, filing_status_col, weight_col]
    missing = [c for c in required if c not in out.columns]
    if missing:
        raise KeyError(
            f"apply_hi_eitc_lfp_response: missing required columns {missing}"
        )

    if elasticity <= 0:
        out[LFP_LOSS_COLUMN] = 0.0
        out[LFP_EXIT_PROB_COLUMN] = 0.0
        return out, {
            "elasticity": float(elasticity),
            "affected_filers_weighted": 0.0,
            "avg_exit_prob": 0.0,
            "expected_lfp_exits_weighted": 0.0,
            "aggregate_lost_earnings_$M": 0.0,
            "aggregate_resource_loss_$M": 0.0,
        }

    # Δlog of combined federal + HI EITC. Combined value = federal × (1 + hi_rate).
    # Federal piece cancels in the log ratio.
    delta_log_eitc = abs(math.log((1.0 + hi_rate_scenario) / (1.0 + hi_rate_baseline)))
    p_exit = elasticity * delta_log_eitc  # per-filer exit probability (same for all)

    earned = out[earned_income_col].fillna(0).to_numpy(dtype=float)
    fed_eitc = out[eitc_col].fillna(0).to_numpy(dtype=float)
    hi_eitc = out[hi_eitc_col].fillna(0).to_numpy(dtype=float)
    fs = out[filing_status_col].to_numpy()
    weight = out[weight_col].fillna(0).to_numpy(dtype=float)
    if federal_tax_col in out.columns:
        fed_tax = out[federal_tax_col].fillna(0).to_numpy(dtype=float)
    else:
        fed_tax = np.zeros_like(earned)

    affected = (fs == "head_of_household") & (earned > 0) & (hi_eitc > 0)

    loss_if_exit = (
        earned
        + fed_eitc
        + 0.5 * hi_eitc
        - _EMPLOYEE_FICA_RATE * earned
        - fed_tax
    )
    # Clamp loss to non-negative — a filer whose baseline federal tax
    # exceeds earnings+credits (unlikely for affected HoH with EITC) would
    # otherwise show a "loss" that's actually a gain. Defensive.
    loss_if_exit = np.maximum(loss_if_exit, 0.0)

    expected_loss = np.where(affected, p_exit * loss_if_exit, 0.0)
    exit_prob_col = np.where(affected, p_exit, 0.0)

    out[LFP_LOSS_COLUMN] = expected_loss
    out[LFP_EXIT_PROB_COLUMN] = exit_prob_col

    affected_w = float(weight[affected].sum())
    diagnostics = {
        "elasticity": float(elasticity),
        "delta_log_eitc": float(delta_log_eitc),
        "p_exit": float(p_exit),
        "affected_filers_weighted": affected_w,
        "avg_exit_prob": float(p_exit) if affected_w > 0 else 0.0,
        "expected_lfp_exits_weighted": float(affected_w * p_exit),
        "aggregate_lost_earnings_$M": float((weight * affected * earned).sum() * p_exit / 1e6),
        "aggregate_resource_loss_$M": float((weight * expected_loss).sum() / 1e6),
    }
    logger.info(
        "apply_hi_eitc_lfp_response: η=%.2f, Δlog(EITC)=%.4f, p_exit=%.4f, "
        "affected_weighted=%.0f, expected_exits=%.0f, resource_loss=$%.2fM",
        elasticity, delta_log_eitc, p_exit, affected_w,
        affected_w * p_exit, diagnostics["aggregate_resource_loss_$M"],
    )
    return out, diagnostics


def compute_snap_offset_for_lfp_exit(
    units: pd.DataFrame,
    *,
    exit_prob_col: str = LFP_EXIT_PROB_COLUMN,
    earned_income_col: str = "earned_income",
    income_col: str = "income",
    snap_amount_col: str = "snap_amount",
    weight_col: str = "weight",
    out_col: str = LFP_SNAP_OFFSET_COLUMN,
    inplace: bool = False,
) -> tuple[pd.DataFrame, dict]:
    """Expected SNAP-benefit gain per affected filer from LFP exit.

    Recomputes SNAP on a single counterfactual frame where every filer
    with ``exit_prob > 0`` has ``earned_income = 0`` (and ``income``
    reduced by their original earned_income). The per-row SNAP delta
    multiplied by ``exit_prob`` is the expected offset to the LFP
    resource loss.

    Approximation
    -------------
    When multiple affected filers share a household, the counterfactual
    zeros all of them simultaneously; the SNAP delta then reflects the
    full household earnings drop rather than one filer's. For HoH
    single-mother units (the target population here) this is fine —
    they are overwhelmingly single-tax-unit households. The
    approximation slightly over-states the offset for the rare
    multi-affected-filer household.

    Parameters
    ----------
    units:
        Tax-unit frame after ``apply_hi_eitc_lfp_response`` and after
        ``compute_snap_for_units`` (so ``exit_prob_col`` and
        ``snap_amount_col`` are present).
    inplace:
        Modify ``units`` in place if True. Default False.

    Returns
    -------
    (df, diagnostics):
        ``df`` carries the new ``out_col``; diagnostics summarize the
        aggregate offset and its share of the raw LFP loss.
    """
    from tax_modeler.benefits.snap import compute_snap_for_units

    out = units if inplace else units.copy()

    for col in (exit_prob_col, earned_income_col, snap_amount_col, weight_col):
        if col not in out.columns:
            raise KeyError(
                f"compute_snap_offset_for_lfp_exit: missing required column {col!r}"
            )

    exit_prob = out[exit_prob_col].fillna(0).to_numpy(dtype=float)
    weight = out[weight_col].fillna(0).to_numpy(dtype=float)
    affected_mask = exit_prob > 0

    if not affected_mask.any():
        out[out_col] = 0.0
        return out, {
            "affected_filers_weighted": 0.0,
            "aggregate_snap_offset_$M": 0.0,
            "avg_snap_gain_per_exit": 0.0,
        }

    cf = out.copy()
    earned_orig = cf[earned_income_col].fillna(0).to_numpy(dtype=float)
    if income_col in cf.columns:
        income_orig = cf[income_col].fillna(0).to_numpy(dtype=float)
    else:
        income_orig = earned_orig.copy()

    cf_earned = earned_orig.copy()
    cf_income = income_orig.copy()
    cf_earned[affected_mask] = 0.0
    cf_income[affected_mask] = np.maximum(income_orig[affected_mask] - earned_orig[affected_mask], 0.0)
    cf[earned_income_col] = cf_earned
    if income_col in cf.columns:
        cf[income_col] = cf_income

    cf = compute_snap_for_units(cf, out_col="_cf_snap_amount")
    snap_baseline = out[snap_amount_col].fillna(0).to_numpy(dtype=float)
    snap_cf = cf["_cf_snap_amount"].fillna(0).to_numpy(dtype=float)
    snap_delta = np.maximum(snap_cf - snap_baseline, 0.0)

    offset = np.where(affected_mask, exit_prob * snap_delta, 0.0)
    out[out_col] = offset

    affected_w = float(weight[affected_mask].sum())
    expected_exits_w = float((weight * exit_prob).sum())
    aggregate_offset_m = float((weight * offset).sum() / 1e6)
    avg_gain_per_exit = (
        float((weight * snap_delta * affected_mask).sum() / max(affected_w, 1.0))
        if affected_w > 0 else 0.0
    )
    diagnostics = {
        "affected_filers_weighted": affected_w,
        "expected_exits_weighted": expected_exits_w,
        "aggregate_snap_offset_$M": aggregate_offset_m,
        "avg_snap_gain_per_exit": avg_gain_per_exit,
    }
    logger.info(
        "compute_snap_offset_for_lfp_exit: affected_weighted=%.0f, "
        "aggregate_offset=$%.2fM, avg_gain_per_exit=$%.0f",
        affected_w, aggregate_offset_m, avg_gain_per_exit,
    )
    return out, diagnostics


def apply_hi_eitc_intensive_response(
    df: pd.DataFrame,
    *,
    elasticity_phase_in: float = 0.15,
    hi_rate_baseline: float = 0.40,
    hi_rate_scenario: float = 0.20,
    tax_year: int = 2025,
    earned_income_col: str = "earned_income",
    hi_eitc_col: str = "hi_eitc_amount",
    filing_status_col: str = "filing_status",
    num_kids_col: str = "num_qualifying_children",
    weight_col: str = "weight",
    exit_prob_col: str = LFP_EXIT_PROB_COLUMN,
    out_col: str = INTENSIVE_LOSS_COLUMN,
    inplace: bool = False,
) -> tuple[pd.DataFrame, dict]:
    """Intensive-margin (hours) hours response to HI EITC cut, phase-in region only.

    The extensive-margin response in :func:`apply_hi_eitc_lfp_response`
    captures full LFP exits. This function adds the *hours* reduction
    for HoH filers who remain in the labor force but reduce hours because
    the EITC subsidy on their marginal dollar of earnings has fallen.

    Scope is restricted to **phase-in workers**: only there does the EITC
    increase the after-subsidy wage, so cutting the subsidy cleanly
    reduces hours via the substitution effect. Plateau workers get no
    marginal-wage change. Phase-out workers face an opposing substitution
    effect (lower phaseout → higher net wage) that is approximately
    cancelled by the income effect (lower total income → less leisure
    demanded) — net zero in the literature, so we set ε = 0 there for
    parsimony.

    Algorithm
    ---------
    For each HoH phase-in filer (per :func:`classify_eitc_region`):

      Δlog(wage) = log((1 + ϕ × r_scn) / (1 + ϕ × r_base))     # negative
      Δh        = ε_phase_in × Δlog(wage)
      loss/$    = 1 + ϕ + ϕ × r_scn − FICA                     # per dollar of earnings
      intensive_loss = (1 − p_exit) × |Δh| × earned_income × loss/$

    where ϕ is the federal EITC phase-in rate for the filer's
    num_qualifying_children (0: 0.0765, 1: 0.34, 2: 0.40, 3+: 0.45).

    Method references: Eissa & Hoynes (2004 JPubE) — intensive-margin
    elasticity 0.10–0.30 for EITC phase-in workers; Saez (2010) — small
    plateau response; Chetty et al. (2013) — small income-elastic
    response in phase-out.

    Returns
    -------
    (df, diagnostics)
    """
    from tax_modeler.credits.eitc import classify_eitc_region, eitc_parameters_for_year

    out = df if inplace else df.copy()

    required = [earned_income_col, hi_eitc_col, filing_status_col, num_kids_col, weight_col]
    missing = [c for c in required if c not in out.columns]
    if missing:
        raise KeyError(
            f"apply_hi_eitc_intensive_response: missing required columns {missing}"
        )

    if elasticity_phase_in <= 0:
        out[out_col] = 0.0
        return out, {
            "elasticity_phase_in": float(elasticity_phase_in),
            "affected_filers_weighted": 0.0,
            "aggregate_resource_loss_$M": 0.0,
        }

    earned = out[earned_income_col].fillna(0).to_numpy(dtype=float)
    hi_eitc = out[hi_eitc_col].fillna(0).to_numpy(dtype=float)
    fs = out[filing_status_col].to_numpy()
    n_kids = np.clip(out[num_kids_col].fillna(0).to_numpy(dtype=int), 0, 3)
    weight = out[weight_col].fillna(0).to_numpy(dtype=float)
    if exit_prob_col in out.columns:
        p_exit = out[exit_prob_col].fillna(0).to_numpy(dtype=float)
    else:
        p_exit = np.zeros_like(earned)

    region = classify_eitc_region(earned, n_kids, fs, tax_year=tax_year)
    is_phase_in = region == "phase_in"
    affected = is_phase_in & (fs == "head_of_household") & (earned > 0) & (hi_eitc > 0)

    # Per-row phase-in rate ϕ from EITC parameters.
    params = eitc_parameters_for_year(tax_year)
    phi_by_k = {k: params.by_children[k].phase_in_rate for k in (0, 1, 2, 3)}
    phi = np.array([phi_by_k[k] for k in n_kids], dtype=float)

    # Δlog(wage). The 1 + 0.20*ϕ vs 1 + 0.40*ϕ change is per-row because ϕ varies.
    with np.errstate(divide="ignore", invalid="ignore"):
        wage_scn = 1.0 + phi * hi_rate_scenario
        wage_base = 1.0 + phi * hi_rate_baseline
        delta_log_wage = np.log(wage_scn / wage_base)
    abs_delta_h = elasticity_phase_in * np.abs(delta_log_wage)

    # Loss per marginal dollar of earnings (resource accounting):
    #   earnings $1 lost + federal EITC subsidy ϕ × $1 lost + HI EITC subsidy
    #   ϕ × r_scn × $1 lost − FICA saved 0.0765
    loss_per_dollar = 1.0 + phi + phi * hi_rate_scenario - _EMPLOYEE_FICA_RATE

    intensive_loss = np.where(
        affected,
        (1.0 - p_exit) * abs_delta_h * earned * loss_per_dollar,
        0.0,
    )
    out[out_col] = intensive_loss

    affected_w = float(weight[affected].sum())
    diagnostics = {
        "elasticity_phase_in": float(elasticity_phase_in),
        "affected_filers_weighted": affected_w,
        "avg_abs_hours_change_pct": (
            float((weight * abs_delta_h * affected).sum() / max(affected_w, 1.0) * 100)
            if affected_w > 0 else 0.0
        ),
        "aggregate_resource_loss_$M": float((weight * intensive_loss).sum() / 1e6),
    }
    logger.info(
        "apply_hi_eitc_intensive_response: ε=%.2f (phase-in only), affected=%.0f, "
        "avg|Δh|=%.2f%%, resource_loss=$%.2fM",
        elasticity_phase_in, affected_w,
        diagnostics["avg_abs_hours_change_pct"],
        diagnostics["aggregate_resource_loss_$M"],
    )
    return out, diagnostics
