"""ITEP-style year-by-year IRS target matching for projected tax units.

Wraps the existing projection pipeline (county B19013 growth + top-income
premium) with optional year-forward re-anchoring to Hawaii Council on Revenues
aggregate revenue and DOTAX-shaped bracket distribution. Closes the
methodology gap with ITEP, which re-anchors each projected year to IRS/CBO
income totals rather than relying on a single growth rate.

Usage
-----
::

    from tax_modeler.calibration.year_recalibrator import project_and_recalibrate
    projected, forward = project_and_recalibrate(
        units, target_year=2027, use_forward_targets=True,
    )

When ``use_forward_targets=False`` (default) the function is identical to
the legacy chain ``project_tax_units_forward(...) →
apply_top_income_growth_premium(...)``, preserving backward compatibility.

Order of operations (when re-anchoring is enabled)
-------------------------------------------------
1. ``project_tax_units_forward`` — county B19013 growth (unchanged).
2. ``forward_targets.build_targets`` — construct year-Y bracket targets.
3. ``phase1_reweight`` — re-rake weights to forward filer-count targets, so
   the top-bracket *population* matches IRS empirical patterns BEFORE the
   premium scales the AGI of those filers.
4. ``apply_top_income_growth_premium`` — existing premium (unchanged).
5. Tax recompute — caller is responsible for re-running ``_compute_base_tax``
   *if* they need fresh ``hi_tax_liability`` after the premium. The current
   premium implementation does not auto-recompute tax.
6. ``phase2_tax_calibrate`` — re-anchor aggregate Hawaii tax to the COR
   projection for year Y by applying per-bracket tax multipliers.
"""
from __future__ import annotations

import dataclasses
import logging
from typing import Dict, Optional, Tuple

import pandas as pd

from tax_modeler.calibration.forward_targets import (
    DEFAULT_COR_IIT_PROJECTIONS_M,
    ForwardTargets,
    build_targets,
    summarize as _summarize_targets,
)

logger = logging.getLogger(__name__)


def _alias_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Sync 'agi' and 'hi_state_tax' aliases expected by the calibrator.

    The pipeline's canonical names are 'income' and 'hi_tax_liability'.
    The calibrator was originally written against 'agi' and 'hi_state_tax'.

    IMPORTANT: ``agi`` is forced to equal ``income`` (not just created when
    missing). ``project_tax_units_forward`` updates ``income`` to nominal
    TY values but does not touch ``agi`` — leaving stale TY2022 dollars on
    pre-existing rows. The calibrator then brackets on ``agi`` and produces
    counts that don't match the post-projection ``income`` distribution.
    Always-syncing here removes that hidden bug.
    """
    out = df
    if "income" in out.columns:
        out = out.copy()
        out["agi"] = out["income"]
    if "hi_state_tax" not in out.columns and "hi_tax_liability" in out.columns:
        out = out.copy() if out is df else out
        out["hi_state_tax"] = out["hi_tax_liability"]
    return out


def _propagate_tax_changes(df: pd.DataFrame) -> pd.DataFrame:
    """After Phase 2 modifies 'hi_state_tax', mirror back to 'hi_tax_liability'.

    Phase 2 applies a per-bracket multiplier to ``hi_state_tax``. The rest of
    the pipeline (forecast scripts, quintile_analysis) reads
    ``hi_tax_liability``. Mirror the calibrated value back so downstream
    code sees the re-anchored tax.
    """
    if "hi_tax_liability" in df.columns and "hi_state_tax" in df.columns:
        df = df.copy()
        df["hi_tax_liability"] = df["hi_state_tax"]
    return df


def project_and_recalibrate(
    units: pd.DataFrame,
    target_year: int,
    *,
    use_forward_targets: bool = False,
    use_agi_reweighting: bool = True,
    resynthesize_top: bool = False,
    use_soi_anchor: bool = False,
    soi_year: int = 2022,
    hawaii_capgain_adjustment: float = 0.95,
    use_cbo_aging: bool = False,
    cbo_vintage: str = "2025-01",
    cbo_hawaii_factors: Optional[Dict[str, float]] = None,
    apply_top_premium: bool = True,
    top_premium_pct: float = 0.010,
    top_premium_base_year: int = 2024,
    top_premium_threshold: float = 500_000.0,
    cor_projections_M: Optional[Dict[int, float]] = None,
    top_bracket_differential: float = 0.010,
    low_growth: float = 0.025,
    rate_drift: float = 0.005,
    population_growth_rate: float = 0.005,
    method: str = "ensemble",
    base_year: Optional[int] = None,
) -> Tuple[pd.DataFrame, Optional[ForwardTargets]]:
    """Project ``units`` to ``target_year`` with optional ITEP-style re-anchoring.

    Parameters
    ----------
    units:
        Calibrated base tax units (post-synthesis, post-CG-imputation,
        post-base-tax-compute). Must have ``income``, ``filing_status``,
        ``weight``, ``hi_tax_liability`` columns.
    target_year:
        The tax year to project to (e.g. 2027).
    use_forward_targets:
        When True, layers year-forward IRS-style re-anchoring on top of the
        county growth + top-premium projection. When False (default), the
        function is identical to the legacy chain (no behavioural change).
    apply_top_premium:
        Whether to apply ``apply_top_income_growth_premium`` (default True).
        Set False to project on county growth alone.
    top_premium_pct:
        Annual premium for filers ≥ ``top_premium_threshold``. Default 1.0%.
    top_premium_base_year:
        Base year for premium compounding. Default 2024 (most recent ACS).
    top_premium_threshold:
        Minimum AGI for the premium to apply. Default $500K.
    use_soi_anchor:
        When True, replace the Pareto-derived $1M+ resynthesis with a
        direct anchor on national IRS SOI Table 1.4 tiers. Mutually
        exclusive with ``resynthesize_top`` — if both are True,
        ``use_soi_anchor`` wins. Requires ``use_forward_targets=True``.
    soi_year:
        SOI Table 1.4 vintage to anchor on (default 2022, the most recent
        published).
    hawaii_capgain_adjustment:
        Multiplier on national SOI CG share for Hawaii residents
        (default 0.95, matching DOTAX Table 21 evidence).
    use_cbo_aging:
        When True, replace ``project_tax_units_forward``'s uniform
        county-median aging with per-component CBO Outlook aging
        (different growth for wages, CG, business, etc.). Phase 1 rake +
        SOI top anchor + Phase 2 calibration still run downstream.
    cbo_vintage:
        CBO Outlook publication identifier (default ``"2025-01"``).
    cbo_hawaii_factors:
        Per-component Hawaii calibration multipliers on CBO national
        rates. None → use ``cbo_aging.DEFAULT_HAWAII_FACTORS`` (HI wage
        growth ~0.85 of national, CG parity).
    cor_projections_M, top_bracket_differential, low_growth, rate_drift:
        Forwarded to ``forward_targets.build_targets`` when
        ``use_forward_targets`` is True.
    method:
        Projection method passed to ``project_tax_units_forward``
        (``"ensemble"`` by default).
    base_year:
        Optional ACS anchor year for ``project_tax_units_forward``. Pass
        ``None`` to use the projector's default (most recent ACS vintage).

    Returns
    -------
    (projected_df, forward_targets)
        ``forward_targets`` is the ``ForwardTargets`` used for re-anchoring,
        or ``None`` when ``use_forward_targets=False``.
    """
    # Local imports to avoid circular import via the calibration module
    from tax_modeler.projection.tax_unit_projector import project_tax_units_forward
    from tax_modeler.scenarios.behavioral_response import apply_top_income_growth_premium
    from tax_modeler.calibration.simultaneous_calibrator import (
        phase1_reweight, phase2_tax_calibrate,
    )
    from tax_modeler.adjustments.itemized_deductions import (
        scale_deduction_params_for_target_year,
    )

    # Build TY-scaled itemized deduction params once. CBO-aging path bypasses
    # project_tax_units_forward (which normally writes hi_itemized_deduction),
    # so _compute_base_tax must receive these params explicitly to populate
    # the column. Without this, per_unit_tax can't recover scenario-specific
    # max(scenario_SD, itemized) for SD-freezing bills like HB 2306 ORIG —
    # all filers get just the SD, which overstates revenue from SD-freeze
    # by ~$246M cumulative over TY 2027-2031.
    # Honolulu (15003) is the Hawaii state-proxy; resolved via state_config so
    # a future state plug-in supplies its own default_geoid without changing
    # this site.
    from tax_modeler.config.state_config import HAWAII
    _ded_params = scale_deduction_params_for_target_year(
        target_year, state_config=HAWAII
    )

    # ── 1. Income aging — uniform county growth OR CBO component aging ───────
    if use_cbo_aging:
        # Per-component CBO aging replaces the uniform B19013 income growth.
        # Geographic aging (county B19013) is dropped; the SOI-anchored
        # synthesis at Step 5b doesn't need geographic resolution since $1M+
        # filers all get assigned to Honolulu PUMA anyway, and PUMS
        # below-$200K filers get income growth from CBO components rather
        # than county medians.
        from tax_modeler.calibration.cbo_aging import (
            age_filers_with_components, load_cbo_rates,
        )
        from tax_modeler.loaders.pums_loader import PUMS_INCOME_DOLLAR_YEAR
        cbo_rates = load_cbo_rates(vintage=cbo_vintage)
        projected = age_filers_with_components(
            units,
            target_year=target_year,
            # Input incomes are in PUMS dollar-year dollars (2024), not 2022;
            # age_filers re-bases the cumulative CBO factors to this year.
            base_year=PUMS_INCOME_DOLLAR_YEAR,
            cbo_rates=cbo_rates,
            hawaii_factors=cbo_hawaii_factors,
        )
    else:
        projected = project_tax_units_forward(
            units, target_year=target_year, base_year=base_year, method=method
        )

    if not use_forward_targets:
        # Legacy path: just apply the premium and return.
        if apply_top_premium:
            projected = apply_top_income_growth_premium(
                projected,
                target_year=target_year,
                base_year=top_premium_base_year,
                annual_premium=top_premium_pct,
                threshold=top_premium_threshold,
            )
        return projected, None

    # ── 2. Build forward targets for this year ───────────────────────────────
    # Use empirical PUMS within-bracket density to migrate filer counts forward.
    # Real bracket density is bottom-loaded (Pareto-like), so a uniform
    # assumption over-projects upward migration into top brackets (~2× too
    # many $300-400K filers at TY2027 under uniform vs PUMS-derived).
    from tax_modeler.calibration import dotax_base
    from tax_modeler.calibration.forward_targets import empirical_density_from_pums
    pre_aging = units.copy() if "income" in units.columns else _alias_columns(units)
    if "income" not in pre_aging.columns and "agi" in pre_aging.columns:
        pre_aging["income"] = pre_aging["agi"]
    empirical_density = empirical_density_from_pums(
        pre_aging, dotax_base.filer_targets(),
    )
    forward = build_targets(
        target_year,
        cor_projections_M=cor_projections_M,
        low_growth=low_growth,
        top_differential=top_bracket_differential,
        rate_drift=rate_drift,
        population_growth_rate=population_growth_rate,
        include_agi_targets=use_agi_reweighting,
        cbo_vintage=cbo_vintage,
        hawaii_factors=cbo_hawaii_factors,
        empirical_density=empirical_density,
    )
    logger.info("Year recalibrator: %s", _summarize_targets(forward))

    # ── 3. Phase 1: re-rake weights to forward filer-count targets ───────────
    # Done BEFORE the premium so the premium operates on a correctly-sized
    # top-bracket population (the dominant driver of the ITEP gap).
    aliased = _alias_columns(projected)
    pre_top = aliased.loc[aliased["agi"] >= top_premium_threshold, "weight"].sum()
    # Stage-2 reweighting: tier-level (5 SOI tiers within $1M+) only by default,
    # which is the methodologically supported correction (cohort persistence
    # at the top — Auten/Gee/Turner 2013). Per-bracket AGI mass is NOT raked
    # because per-bracket composition assumptions are coarser than the actual
    # PUMS filer income decomposition that cbo_aging.age_filers_with_components
    # produces, so over-zealous bracket reweighting causes false target gaps
    # at sub-$1M brackets (and HB 2306 alignment regresses).
    raked = phase1_reweight(
        aliased,
        filer_targets=forward.filer_targets,
        status_targets=forward.status_targets,
    )
    post_top = raked.loc[raked["agi"] >= top_premium_threshold, "weight"].sum()
    delta_pct = (post_top / pre_top - 1) * 100 if pre_top > 0 else 0.0
    logger.info(
        f"Phase 1 (filer rake) — ≥${top_premium_threshold/1e3:.0f}K filers: "
        f"{pre_top:,.0f} → {post_top:,.0f} ({delta_pct:+.1f}%)"
    )

    # ── 4. Top-income premium (existing function, unchanged) ────────────────
    if apply_top_premium:
        raked = apply_top_income_growth_premium(
            raked,
            target_year=target_year,
            base_year=top_premium_base_year,
            annual_premium=top_premium_pct,
            threshold=top_premium_threshold,
        )
        # Premium scales income and clears the top-row tax columns; mirror to
        # the alias so Phase 2 sees consistent values.
        if "income" in raked.columns:
            raked["agi"] = raked["income"]

    # ── 5. Phase 2: re-anchor aggregate tax to COR projection ───────────────
    # The premium changed AGI; tax must be recomputed from current AGI before
    # the bracket multipliers can target the COR aggregate. Use the existing
    # base-tax recompute helper.
    if apply_top_premium:
        from tax_modeler.pipeline import _compute_base_tax
        raked = _compute_base_tax(
            raked, tax_year=target_year, deduction_params=_ded_params,
        )
        # _compute_base_tax writes to hi_tax_liability; re-alias for Phase 2.
        if "hi_tax_liability" in raked.columns:
            raked["hi_state_tax"] = raked["hi_tax_liability"]

    # ── 5b. Re-synthesize $1M+ at the forward target (ITEP-style anchor) ────
    # Two modes share this slot — both zero existing $1M+ weights, then add
    # fresh donors sized to the forward DOTAX-projected $1M+ count.
    #
    #   - ``use_soi_anchor``: pulls per-tier composition (CG/wages/biz/div)
    #     directly from national SOI Table 1.4. Strictly preferred when SOI
    #     data is available — closer to ITEP's methodology.
    #   - ``resynthesize_top`` (legacy): Pareto α=1.5 with national-95% CG
    #     shares from a hardcoded tier table.
    #
    # Do NOT re-rake afterwards. The high-income filing-status mix (69% MFJ
    # per DOTAX A8 $400K+) conflicts with the *global* status marginal
    # (34% MFJ overall). A second Phase 1 IPF scales MFJ weights down
    # globally — which drops $1M+ count below target and forces Phase 2 to
    # inflate per-filer tax to compensate. Trust the synthesizer's count;
    # let Phase 2 anchor the aggregate.
    if use_soi_anchor or resynthesize_top:
        import numpy as np
        from tax_modeler.pipeline import _compute_base_tax as _compute_base_tax_2

        top = (1_000_000, float(np.inf))
        fwd_1m_count = int(forward.filer_targets.get(top, dotax_base.filer_targets()[top]))
        fwd_1m_tax_M = float(forward.tax_targets.get(top, dotax_base.tax_targets_M()[top]))
        pre_1m = float(raked.loc[raked["agi"] >= 1_000_000, "weight"].sum())

        if use_soi_anchor:
            from tax_modeler.calibration.soi_top_anchor import (
                synthesize_top_filers_from_soi,
            )
            # When CBO aging is on, age the SOI tier averages forward via
            # component-specific growth so the synthesized $1M+ rows reflect
            # TY-of-interest income levels (not TY2022 SOI vintage).
            raked = synthesize_top_filers_from_soi(
                raked,
                target_filer_count=fwd_1m_count,
                target_tax_M=fwd_1m_tax_M,
                soi_year=soi_year,
                hawaii_capgain_adjustment=hawaii_capgain_adjustment,
                target_year=target_year if use_cbo_aging else None,
                cbo_vintage=cbo_vintage if use_cbo_aging else None,
                cbo_hawaii_factors=cbo_hawaii_factors if use_cbo_aging else None,
            )
            mode = "SOI anchor (CBO-aged tiers)" if use_cbo_aging else "SOI anchor"
        else:
            from tax_modeler.scenarios.top_income_synthesis import synthesize_top_filers
            raked = synthesize_top_filers(
                raked,
                target_tax_m=fwd_1m_tax_M,
                total_1m_filers=fwd_1m_count,
            )
            mode = "Pareto resynthesis"

        raked = _compute_base_tax_2(
            raked, tax_year=target_year, deduction_params=_ded_params,
        )
        if "hi_tax_liability" in raked.columns:
            raked["hi_state_tax"] = raked["hi_tax_liability"]
        if "income" in raked.columns:
            raked["agi"] = raked["income"]
        post_1m = float(raked.loc[raked["agi"] >= 1_000_000, "weight"].sum())
        logger.info(
            f"Year-target re-synthesis ({mode}) — $1M+ filers: "
            f"{pre_1m:,.0f} → {post_1m:,.0f} (forward target {fwd_1m_count:,.0f}, "
            f"target tax ${fwd_1m_tax_M:,.0f}M)"
        )

        # ── 5c. Stage-2 tier-mass rake (now that synthetic_tier_lo is set) ──
        # Phase 1 ran before SOI synthesis, so its tier-AGI-mass margin had
        # nothing to operate on. Apply it now: rescale weights within each
        # SOI Table 1.4 tier ($1M-$1.5M, ..., $10M+) to hit the per-tier
        # forward AGI target. Closes the cohort-aging mis-specification at
        # the granularity the user requested (5 SOI tiers).
        if (
            use_agi_reweighting
            and forward.tier_agi_targets
            and "synthetic_tier_lo" in raked.columns
        ):
            import numpy as np
            tier_lo_arr = raked["synthetic_tier_lo"].fillna(-1).to_numpy(dtype=float)
            agi_arr = raked["agi"].to_numpy(dtype=float)
            w_arr = np.array(raked["weight"].to_numpy(dtype=float), copy=True)
            for (lo, _hi), target_M in sorted(forward.tier_agi_targets.items()):
                mask = tier_lo_arr == lo
                if not mask.any():
                    continue
                current_M = float((agi_arr[mask] * w_arr[mask]).sum() / 1e6)
                if current_M > 0 and target_M > 0:
                    factor = target_M / current_M
                    # Cap factor to avoid extreme weight changes
                    factor = float(np.clip(factor, 0.5, 2.0))
                    w_arr[mask] *= factor
                    logger.info(
                        f"  Tier ${lo/1e6:>4.1f}M+: ${current_M:>6.0f}M → "
                        f"${target_M:>6.0f}M (factor {factor:.3f})"
                    )
            raked["weight"] = w_arr
            # Recompute tax after weight changes (per-unit tax unchanged but
            # aggregate moved; phase 2 will re-anchor)
            raked = _compute_base_tax_2(
                raked, tax_year=target_year, deduction_params=_ded_params,
            )
            if "hi_tax_liability" in raked.columns:
                raked["hi_state_tax"] = raked["hi_tax_liability"]

    pre_tax_M = (raked["hi_state_tax"] * raked["weight"]).sum() / 1e6

    # Record the statute-vs-COR wedge on the returned targets. pre_tax_M is
    # the STATUTORY baseline aggregate (post-rake, pre-Phase-2); Phase 2's
    # per-bracket multipliers absorb the gap to the COR target — but scenario
    # scripts re-score statutorily, so their reported LEVELS carry this wedge,
    # not the COR anchor. Callers should report it next to every level.
    forward = dataclasses.replace(
        forward,
        statutory_tax_M=float(pre_tax_M),
        statute_vs_cor_wedge=float(pre_tax_M / forward.aggregate_tax_M),
    )
    if abs(forward.statute_vs_cor_wedge - 1.0) > 0.02:
        logger.warning(
            "Statute-vs-COR wedge TY%d: statutory $%.0fM vs COR $%.0fM "
            "(ratio %.3f). Scenario scripts recompute statutory tax and are "
            "NOT COR-anchored — reported levels carry this wedge.",
            target_year, pre_tax_M, forward.aggregate_tax_M,
            forward.statute_vs_cor_wedge,
        )

    calibrated = phase2_tax_calibrate(
        raked,
        tax_targets=forward.tax_targets,
    )
    post_tax_M = (calibrated["hi_state_tax"] * calibrated["weight"]).sum() / 1e6
    logger.info(
        f"Phase 2 (tax calibration) — total tax: ${pre_tax_M:,.1f}M → "
        f"${post_tax_M:,.1f}M (COR target ${forward.aggregate_tax_M:,.0f}M)"
    )

    # ── 6. Mirror calibrated tax back to canonical column ───────────────────
    final = _propagate_tax_changes(calibrated)
    return final, forward
