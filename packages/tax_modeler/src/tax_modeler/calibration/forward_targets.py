"""Year-forward DOTAX-shaped calibration targets for Hawaii income tax microsim.

Constructs filer-count, tax, and filing-status targets for any TY in 2025-2031
from three inputs:
    1. DOTAX baseline (filer counts, tax, filing status): the TY2023 edition,
       ``dotax_base.BASE_YEAR``
    2. Hawaii Council on Revenues IIT projection (FY27 anchor + back-cast)
    3. Top-bracket differential growth assumption (1pp/yr above general)

Used by ``year_recalibrator.project_and_recalibrate`` to layer ITEP-style
year-by-year IRS target matching on top of the existing B19013 + top-income
premium projection. The forward targets re-anchor projected incomes each year
so total revenue tracks COR and top-bracket distribution tracks IRS empirical
patterns (top 1% growing 2-3pp/yr above median).

Methodology
-----------
For target year Y:

* **Aggregate tax target T_Y**: lookup in COR projections (default
  ``DEFAULT_COR_IIT_PROJECTIONS_M``). Years outside the table are
  extrapolated at 2.5% nominal growth from the nearest anchor.

* **Filer migration**: apply piecewise growth ``g_b(Y)`` per bracket:
    - Below $200K: ``g_low = 1.025^(Y-base)``
    - At/above $200K: ``g_high = g_low * 1.010^(Y-base)``  (top differential)
  Build a CDF over the baseline brackets, scale cut-points by ``g_b``, then
  re-bucket onto the fixed 15-bracket nominal schema. Total filer count is
  grown at ``population_growth_rate`` (631,125 in the TY2023 baseline).

* **Tax targets**: per-filer effective rate from the baseline (``r_b = Tax_b / N_b``)
  applied to the *forward* counts, with a small ``rate_drift`` uplift for
  bracket-creep into higher marginal rates. Then uniformly scaled so the sum
  exactly equals T_Y (consistency with COR).

* **Filing status**: baseline totals scaled by ``total_Y / baseline total`` (≈1.0).
"""
from __future__ import annotations

import logging
import warnings
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import numpy as np

from tax_modeler.calibration import dotax_base

logger = logging.getLogger(__name__)

# COR Hawaii Individual Income Tax projections, loaded from the bundled file
# that ``census_forecaster.scripts.refresh_cor_iit`` keeps on the newest
# General Fund meeting (the monthly refresh-data workflow runs it). This used
# to be a second hand-typed copy, left on the March 10, 2026 vintage when
# ``scenarios.quintile_analysis`` moved to the file, so a new COR meeting never
# reached the forward targets (and the statute-vs-COR wedge was computed
# against a vintage two meetings old).
#
# YEAR CONVENTION: keys are TAX years (TY). COR publishes FISCAL years
# (Jul-Jun); the mapping is FY(n+1) = TY(n) per DOTAX fiscal-note convention
# — e.g. the FY 2026 collections forecast anchors TY 2025 liability. All
# model outputs are labeled in TAX years; convert to FY (= TY+1) when
# comparing against COR/fiscal-note tables.
#
# The literal below is a LAST-RESORT fallback for an installation whose data
# file is missing; it is the March 10, 2026 vintage
# (files.hawaii.gov/tax/useful/cor/2026gf03-10_attach_1.pdf). Do not hand-edit
# it to refresh — run the script.
_COR_FALLBACK_M: Dict[int, float] = {
    2025: 2_986.920,   # FY 2026
    2026: 2_900.330,   # FY 2027
    2027: 2_825.329,   # FY 2028
    2028: 2_815.274,   # FY 2029
    2029: 2_749.758,   # FY 2030
    2030: 2_851.075,   # FY 2031
    2031: 2_944.872,   # FY 2032
}


def _load_cor_projections() -> Dict[int, float]:
    try:
        from census_forecaster.cor import load_cor_iit_projections

        return load_cor_iit_projections(by="tax_year")
    except Exception:  # noqa: BLE001 - never let a data-file problem break calibration
        warnings.warn(
            "bundled COR projections unavailable; falling back to the "
            "hardcoded March 10, 2026 vintage. Run "
            "`python -m census_forecaster.scripts.refresh_cor_iit` to restore.",
            RuntimeWarning,
            stacklevel=2,
        )
        return dict(_COR_FALLBACK_M)


DEFAULT_COR_IIT_PROJECTIONS_M: Dict[int, float] = _load_cor_projections()

# The DOTAX baseline (Table A-8 filer counts and tax, Table 4 filing status) comes
# from ``dotax_base``, which holds one copy for every module that anchors on it.
# ``build_targets(base_year=...)`` picks the edition.

@dataclass(frozen=True)
class ForwardTargets:
    """Year-parameterized DOTAX-shaped calibration targets.

    Attributes
    ----------
    year:
        Target tax year (e.g. 2027).
    filer_targets:
        ``{(agi_lo, agi_hi): filer_count}`` for the 15 nominal AGI brackets.
    tax_targets:
        ``{(agi_lo, agi_hi): tax_M}`` summing to the COR aggregate for ``year``.
    status_targets:
        ``{filing_status: count}`` totals.
    aggregate_tax_M:
        Convenience: sum of ``tax_targets.values()``.
    agi_mass_targets:
        ``{(agi_lo, agi_hi): aggregate_AGI_M}`` per bracket. Used by
        ``simultaneous_calibrator.phase1_reweight`` to anchor stage-2
        AGI mass per bracket to forward distributional targets (CBO/TPC
        standard reweighting). ``None`` disables AGI-mass margin.
    tier_agi_targets:
        ``{(tier_lo, tier_hi): aggregate_AGI_M}`` for the 5 SOI Table 1.4
        tiers within $1M+. Refines the within-$1M+ distribution so high
        income mobility is reflected (Auten/Gee/Turner 2013).
    statutory_tax_M:
        Filled in by ``project_and_recalibrate`` AFTER the Phase 1 rake:
        the statutory (recomputed) baseline tax aggregate before Phase 2
        scales ``hi_state_tax`` to the COR target. ``None`` until then.
    statute_vs_cor_wedge:
        ``statutory_tax_M / aggregate_tax_M``. The calibration residual the
        Phase 2 multipliers absorb. Scenario scripts re-score statutorily and
        so report STATUTORY levels, not COR-anchored ones — this wedge is the
        gap between the two and should be reported alongside every level.
    calibration_report:
        Filled in by ``project_and_recalibrate``: a
        ``calibration.report.CalibrationReport`` for this year's re-anchoring
        (Phase 1 convergence, tier-rake clamp events, residuals against the
        forward targets, weight dispersion, the wedge above).
    """
    year: int
    filer_targets: Dict[Tuple[float, float], int]
    tax_targets:   Dict[Tuple[float, float], float]
    status_targets: Dict[str, int]
    aggregate_tax_M: float
    agi_mass_targets: Optional[Dict[Tuple[float, float], float]] = None
    tier_agi_targets: Optional[Dict[Tuple[float, float], float]] = None
    statutory_tax_M: Optional[float] = None
    statute_vs_cor_wedge: Optional[float] = None
    calibration_report: Optional[Any] = None


def _back_cast_cor(year: int, cor_projections_M: Dict[int, float]) -> float:
    """Return the COR aggregate tax target for *year*.

    For years already in the table, return the value directly. For years
    before the earliest entry, back-cast at 2.5% nominal annual growth from
    the earliest anchor. For years beyond the table, forward-extrapolate at 2.5%.
    """
    if year in cor_projections_M:
        return cor_projections_M[year]
    earliest = min(cor_projections_M)
    if year > max(cor_projections_M):
        # Forward extrapolation also at 2.5% (rare; COR usually publishes 5+ yrs)
        anchor = cor_projections_M[max(cor_projections_M)]
        return anchor * (1.025 ** (year - max(cor_projections_M)))
    # Back-cast: anchor / 1.025^(anchor_year - year)
    anchor = cor_projections_M[earliest]
    return anchor / (1.025 ** (earliest - year))


def _migrate_filer_counts(
    base_counts: Dict[Tuple[float, float], int],
    g_low: float,
    g_high: float,
    threshold: float = 200_000.0,
    *,
    empirical_density: Optional[Dict[Tuple[float, float], "np.ndarray"]] = None,
) -> Dict[Tuple[float, float], int]:
    """Migrate baseline filer counts to a forward year via bracket-shift.

    Each baseline bracket ``(lo, hi)`` is treated as having ``count`` filers
    distributed according to either:
      - a uniform density (legacy default) — assumes filers are spread evenly
        across the bracket, which over-projects upward migration because real
        bracket density is bottom-loaded (Pareto-like), OR
      - an ``empirical_density`` histogram from PUMS (preferred) — uses the
        actual within-bracket sub-bin counts to model migration realistically.

    The chosen density is scaled by ``g_low`` (below threshold) or
    ``g_high`` (at/above threshold), then re-bucketed onto the fixed nominal
    boundaries. Total mass is preserved.

    Parameters
    ----------
    empirical_density:
        ``{(bracket_lo, bracket_hi): np.array of normalized sub-bin densities}``
        keyed by DOTAX bracket. Each array represents within-bracket density
        across N=20 sub-bins of equal width. If provided, used in place of
        uniform density assumption.
    """
    nominal_brackets = sorted(base_counts.keys())
    forward: Dict[Tuple[float, float], float] = {b: 0.0 for b in nominal_brackets}

    for (lo, hi), count in base_counts.items():
        g = g_low if lo < threshold else g_high
        if hi == np.inf:
            # Open-ended top bracket: treat as point mass at scaled lower edge.
            new_lo = lo * g
            for (nlo, nhi) in nominal_brackets:
                if nlo <= new_lo < nhi:
                    forward[(nlo, nhi)] += count
                    break
            else:
                forward[nominal_brackets[-1]] += count
            continue

        # Build sub-bin densities: empirical (PUMS) or uniform (legacy).
        n_subbins = 20
        if empirical_density is not None and (lo, hi) in empirical_density:
            sub_density = empirical_density[(lo, hi)]
            # Ensure normalization (sum of sub_density = total bracket count)
            d_sum = float(sub_density.sum())
            if d_sum > 0:
                sub_density = sub_density * (count / d_sum)
            else:
                sub_density = np.full(n_subbins, count / n_subbins)
        else:
            sub_density = np.full(n_subbins, count / n_subbins)

        # Each sub-bin spans [lo + i*step, lo + (i+1)*step]; after growth it
        # becomes [(lo+i*step)*g, (lo+(i+1)*step)*g]. Re-bucket onto nominal.
        step = (hi - lo) / n_subbins
        for i, sub_count in enumerate(sub_density):
            sub_lo_new = (lo + i * step) * g
            sub_hi_new = (lo + (i + 1) * step) * g
            sub_width = sub_hi_new - sub_lo_new
            if sub_width <= 0 or sub_count <= 0:
                continue
            density_per_dollar = sub_count / sub_width
            for (nlo, nhi) in nominal_brackets:
                if nhi == np.inf:
                    overlap_lo = max(sub_lo_new, nlo)
                    overlap_hi = sub_hi_new
                else:
                    overlap_lo = max(sub_lo_new, nlo)
                    overlap_hi = min(sub_hi_new, nhi)
                if overlap_hi > overlap_lo:
                    forward[(nlo, nhi)] += density_per_dollar * (overlap_hi - overlap_lo)

    return {b: int(round(v)) for b, v in forward.items()}


def empirical_density_from_pums(
    units: "pd.DataFrame",
    base_counts: Dict[Tuple[float, float], int],
    n_subbins: int = 20,
    income_col: str = "income",
    weight_col: str = "weight",
) -> Dict[Tuple[float, float], "np.ndarray"]:
    """Build within-bracket density histograms from PUMS-projected units.

    For each DOTAX bracket, computes a normalized density vector (length
    ``n_subbins``) reflecting where filers fall within the bracket. Used by
    ``_migrate_filer_counts`` to replace the unrealistic uniform assumption.
    """
    incomes = units[income_col].to_numpy(dtype=float)
    weights = units[weight_col].to_numpy(dtype=float)

    out: Dict[Tuple[float, float], "np.ndarray"] = {}
    for (lo, hi) in base_counts.keys():
        if hi == np.inf:
            # Top bracket — use point mass at lo, no sub-bin distribution
            out[(lo, hi)] = np.zeros(n_subbins)
            continue
        mask = (incomes >= lo) & (incomes < hi)
        if not mask.any():
            out[(lo, hi)] = np.full(n_subbins, 1.0 / n_subbins)
            continue
        sub_edges = np.linspace(lo, hi, n_subbins + 1)
        hist = np.zeros(n_subbins)
        for i in range(n_subbins):
            sm = mask & (incomes >= sub_edges[i]) & (incomes < sub_edges[i + 1])
            hist[i] = float(weights[sm].sum())
        total = hist.sum()
        if total > 0:
            hist = hist / total
        else:
            hist = np.full(n_subbins, 1.0 / n_subbins)
        out[(lo, hi)] = hist
    return out


def build_targets(
    year: int,
    *,
    cor_projections_M: Optional[Dict[int, float]] = None,
    low_growth: float = 0.025,
    top_differential: float = 0.010,
    rate_drift: float = 0.005,
    population_growth_rate: float = 0.005,
    base_counts: Optional[Dict[Tuple[float, float], int]] = None,
    base_tax: Optional[Dict[Tuple[float, float], float]] = None,
    base_status: Optional[Dict[str, int]] = None,
    base_year: int = dotax_base.BASE_YEAR,
    include_agi_targets: bool = True,
    cbo_vintage: str = "2025-01",
    hawaii_factors: Optional[Dict[str, float]] = None,
    empirical_density: Optional[Dict[Tuple[float, float], "np.ndarray"]] = None,
) -> ForwardTargets:
    """Construct ``ForwardTargets`` for the given year.

    Parameters
    ----------
    year:
        Target tax year (2022..2031 supported).
    cor_projections_M:
        Override for the COR projection table. Default uses
        ``DEFAULT_COR_IIT_PROJECTIONS_M``.
    low_growth:
        Annual income growth factor applied to brackets below $200K.
    top_differential:
        Extra annual growth for brackets at/above $200K (compounded with
        ``low_growth``). Captures top-1% empirical premium.
    rate_drift:
        Per-year effective-rate uplift reflecting bracket-creep into higher
        marginal rates as nominal incomes rise.
    population_growth_rate:
        Annual filer-count growth rate, applied uniformly across brackets
        AFTER bracket migration. Default 0.005 (0.5%/yr) is between DBEDT's
        ~0.34%/yr total population projection and ~0.6%/yr civilian labor
        force projection (DBEDT 2050 series, Apr 2024). Tax filers correlate
        most closely with the labor force. Set to 0.0 to hold filer count
        constant (legacy behavior).
    base_counts, base_tax, base_status:
        Override the DOTAX baseline (mainly for testing the round-trip).
    base_year:
        DOTAX edition the baseline comes from (``dotax_base``); default the
        newest on file, TY2023. ``2022`` reproduces the earlier base.
    """
    cor = dict(cor_projections_M or DEFAULT_COR_IIT_PROJECTIONS_M)
    bc = dict(base_counts or dotax_base.filer_targets(base_year))
    bt = dict(base_tax    or dotax_base.tax_targets_M(base_year))
    bs = dict(base_status or dotax_base.status_targets(base_year))

    years = year - base_year
    g_low  = (1 + low_growth) ** years
    g_high = g_low * (1 + top_differential) ** years
    drift  = 1 + rate_drift * years

    # ── 1. Filer counts: bracket migration ────────────────────────────────────
    forward_counts = _migrate_filer_counts(
        bc, g_low, g_high, empirical_density=empirical_density,
    )

    # Apply population growth uniformly across brackets (after migration).
    # Source: DBEDT 2050 series (Apr 2024); 0.5%/yr default ≈ civilian labor
    # force projection. Aggregate tax target stays anchored to COR via the
    # uniform rescale below, so this only affects the WEIGHT distribution
    # (more middle-bracket filer mass) not total revenue.
    pop_factor = (1 + population_growth_rate) ** years
    forward_counts = {b: int(round(c * pop_factor)) for b, c in forward_counts.items()}

    # ── 2. Tax targets: per-filer effective rate × new counts × drift ─────────
    raw_tax: Dict[Tuple[float, float], float] = {}
    for bracket, base_n in bc.items():
        base_t = bt[bracket]
        eff_rate = base_t / base_n if base_n > 0 else 0.0
        new_n = forward_counts.get(bracket, 0)
        raw_tax[bracket] = eff_rate * new_n * drift

    # Scale uniformly so sum matches COR aggregate
    aggregate = _back_cast_cor(year, cor)
    raw_sum = sum(raw_tax.values())
    if raw_sum > 0:
        scale = aggregate / raw_sum
    else:
        scale = 1.0
    forward_tax = {b: v * scale for b, v in raw_tax.items()}

    # ── 3. Filing status: scale baseline totals by total filer growth ─────────
    base_total = sum(bc.values())
    new_total = sum(forward_counts.values())
    status_scale = new_total / base_total if base_total > 0 else 1.0
    forward_status = {s: int(round(v * status_scale)) for s, v in bs.items()}

    # ── 4. Forward AGI mass per bracket (stage-2 reweighting target) ────────
    agi_mass_targets: Optional[Dict[Tuple[float, float], float]] = None
    tier_agi_targets: Optional[Dict[Tuple[float, float], float]] = None
    if include_agi_targets:
        try:
            from tax_modeler.calibration.forward_agi_targets import build_agi_targets
            # Use the forward-projected $1M+ count to allocate SOI tier filers
            fwd_1m_count = int(forward_counts.get((1_000_000, np.inf), bc[(1_000_000, np.inf)]))
            agi = build_agi_targets(
                year,
                cbo_vintage=cbo_vintage,
                hawaii_factors=hawaii_factors,
                base_year=base_year,
                soi_tier_total_filers=fwd_1m_count,
            )
            agi_mass_targets = agi.bracket_agi_targets
            tier_agi_targets = agi.tier_agi_targets if agi.tier_agi_targets else None
        except Exception as exc:
            logger.warning("Forward AGI targets unavailable — proceeding without: %s", exc)

    return ForwardTargets(
        year=year,
        filer_targets=forward_counts,
        tax_targets=forward_tax,
        status_targets=forward_status,
        aggregate_tax_M=aggregate,
        agi_mass_targets=agi_mass_targets,
        tier_agi_targets=tier_agi_targets,
    )


def summarize(target: ForwardTargets) -> str:
    """One-line summary for logging."""
    n = sum(target.filer_targets.values())
    top = sum(v for (lo, _), v in target.filer_targets.items() if lo >= 500_000)
    return (
        f"ForwardTargets[{target.year}]: {n:,} filers (≥$500K: {top:,}), "
        f"${target.aggregate_tax_M:,.0f}M aggregate tax"
    )
