"""Core synthetic estimator: PUMS + county controls → SyntheticEstimatePoint.

Workflow
--------
Given a set of PUMS microdata records (statewide or for the relevant PUMAs)
and county-level control totals from census_forecaster, this module estimates
the county-level distribution of an arbitrary PUMS survey variable.

1. Filter PUMS records to those whose PUMA overlaps the target county.
2. Apply PUMA→county allocation factors from the crosswalk to initial weights.
3. Rake the allocated weights to the county controls via IPF
   (:func:`estimation.rake.rake`).
4. Compute the weighted tabulation of the target variable.
5. Estimate uncertainty via the Kish (1965) effective-sample-size formula.

Use case
--------
Hawaii vehicle ownership example:

    >>> from pums_estimator.pums.client import fetch_pums
    >>> from pums_estimator.pums.crosswalk import load_crosswalk, pumas_for_county
    >>> from pums_estimator.estimation.synthetic import estimate_county_distribution
    >>> from pums_estimator.models import CountyControls
    >>>
    >>> records = fetch_pums("15", ["VEH"])          # Hawaii statewide PUMS
    >>> crosswalk = load_crosswalk(vintage_year=2022)
    >>> controls = CountyControls(                   # from census_forecaster outputs
    ...     geoid="15009",                            # Maui County
    ...     population=164000,
    ...     margins={"VEH": {"0": 2000, "1": 20000, "2": 25000, "3+": 8000}},
    ... )
    >>> estimates = estimate_county_distribution(records, controls, "VEH", crosswalk)
    >>> for e in estimates:
    ...     print(e.category, e.estimate, e.se)

Statistical note
----------------
Results are *model-based estimates*.  Uncertainty reflects only the Kish
design-effect approximation from PUMS sampling weights; it does not include
raking model uncertainty or calibration error from the census_forecaster
county controls.  Interpret SEs as lower bounds on true uncertainty.
"""
from __future__ import annotations

import math

from ..models import CountyControls, PumsRecord, SyntheticEstimatePoint
from ..pums.crosswalk import pumas_for_county
from .rake import _categorise, rake


def estimate_county_distribution(
    records: list[PumsRecord],
    controls: CountyControls,
    variable: str,
    crosswalk: dict[str, list[tuple[str, float]]],
    output_mode: str = "count",
) -> list[SyntheticEstimatePoint]:
    """Estimate the distribution of *variable* for a target county.

    Parameters
    ----------
    records : list[PumsRecord]
        PUMS records, typically statewide.  Records outside PUMAs that
        overlap the target county are automatically filtered out.
    controls : CountyControls
        County-level control totals.  ``controls.geoid`` identifies the
        target county.  ``controls.margins`` provides raking constraints.
    variable : str
        PUMS variable name to tabulate (e.g. ``"VEH"``).
    crosswalk : dict
        Output of :func:`pums.crosswalk.load_crosswalk`.
    output_mode : str
        ``"count"`` (default) — estimates are in housing-unit counts.
        ``"proportion"`` — estimates are fractions summing to ≈1.0.

    Returns
    -------
    list[SyntheticEstimatePoint]
        One point per category of *variable*, sorted by category label.
        Returns an empty list if no PUMS records overlap the county or
        no records carry the requested variable.
    """
    # --- Step 1: filter to overlapping PUMAs ---
    puma_alloc: dict[str, float] = {
        puma: alloc
        for puma, alloc in pumas_for_county(controls.geoid, crosswalk)
    }
    filtered = [r for r in records if r.puma in puma_alloc]
    if not filtered:
        return []

    # --- Step 2: apply allocation factors ---
    allocated = [
        PumsRecord(
            serial=r.serial,
            puma=r.puma,
            weight=r.weight * puma_alloc[r.puma],
            variables=r.variables,
        )
        for r in filtered
    ]

    # --- Step 3: rake to county controls ---
    raked_weights = rake(allocated, controls)
    sum_w = sum(raked_weights)
    if sum_w <= 0:
        return []

    # --- Step 4: weighted tabulation ---
    # Use the margin categories (if present) so output labels match input keys.
    # E.g. if margins say "2+", records with VEH=2 and VEH=3 both land in "2+".
    target_cats = controls.margins.get(variable, {})
    category_totals: dict[str, float] = {}
    category_sq_w: dict[str, float] = {}
    for rec, w in zip(allocated, raked_weights, strict=False):
        val = rec.variables.get(variable)
        if val is None:
            continue
        cat = _categorise(val, target_cats) if target_cats else str(int(val))
        category_totals[cat] = category_totals.get(cat, 0.0) + w
        category_sq_w[cat] = category_sq_w.get(cat, 0.0) + w * w

    if not category_totals:
        return []

    # --- Step 5: Kish effective-sample-size SE ---
    # n_eff = (Σw)² / Σw²  → design-effect-adjusted sample size
    sum_w2 = sum(w * w for w in raked_weights)
    n_eff = (sum_w ** 2) / sum_w2 if sum_w2 > 0 else 0.0

    results: list[SyntheticEstimatePoint] = []
    for cat, weighted_total in sorted(category_totals.items()):
        sq_w_cat = category_sq_w.get(cat, 0.0)
        if output_mode == "proportion":
            estimate = weighted_total / sum_w
            # SE of a proportion under Kish approximation
            se_approx = math.sqrt(sq_w_cat) / sum_w if sum_w > 0 else 0.0
        else:
            estimate = weighted_total
            se_approx = math.sqrt(sq_w_cat) if sq_w_cat > 0 else 0.0

        results.append(SyntheticEstimatePoint(
            geoid=controls.geoid,
            variable=variable,
            category=cat,
            estimate=estimate,
            se=se_approx,
            n_pums_records=len(filtered),
            method="ipf_raking",
            notes=f"n_eff={n_eff:.1f}; n_pums_filtered={len(filtered)}",
        ))

    return results
