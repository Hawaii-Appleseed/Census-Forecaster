"""Anti-poverty impact of federal CTC, federal EITC, and Hawaii state EITC.

Computes Hawaii SPM poverty rates *with* the relevant credits (baseline)
and re-computes them under one or more counterfactual scenarios:

  Removal scenarios (the credit is set to $0):
    * ``no_eitc``       — federal EITC removed
    * ``no_ctc``        — federal CTC refundable portion removed
    * ``no_hi_eitc``    — Hawaii state EITC removed
    * ``no_credits``    — all three credits removed

  Partial-reduction scenarios:
    * ``hi_eitc_revert_20`` — Hawaii state EITC rate cut from 40% (Act
      163, 2023) back to 20% of federal — i.e. ``hi_eitc_amount
      × 0.5``.
    * ``hi_eitc_revert_20_behavioral`` — same static cut plus the
      extensive-margin single-mother LFP exit response per Meyer &
      Rosenbaum (2001). Requires the ``lfp_behavioral_resource_loss``
      column precomputed via
      :func:`tax_modeler.scenarios.eitc_labor_response.apply_hi_eitc_lfp_response`
      before SPM-unit aggregation.
    * ``act163_sunset`` — Act 163 (2023) expires after TY2027: the Hawaii
      EITC falls from 40% to 20% of federal and the food/excise credit
      returns to its pre-2023 table. Subtracts the precomputed
      ``act163_sunset_loss`` column (dollars per tax unit, take-up
      adjusted; see ``forecast_working_family_credits.py``).

  Expansion / new-credit scenarios:
    * ``expanded_ctc_2021`` — federal CTC replaced with the ARPA 2021
      design (see :mod:`tax_modeler.credits.arpa_ctc`).
    * ``hi_eitc_100pct`` — Hawaii state EITC rate raised from 40% to
      100% of federal (i.e. ``hi_eitc_amount × 2.5``).
    * ``hi_ctc_<NNN>`` — Hawaii enacts a state CTC at $NNN per
      qualifying child, fully refundable, no AGI phase-out. Default
      scenario set ships three policy points: ``hi_ctc_300``,
      ``hi_ctc_650`` (the SB 3125 CD1 amount), and ``hi_ctc_1000``.
      Any positive integer dollar amount works (e.g. ``hi_ctc_500``
      for amendment analysis) — the per-child amount is parsed from
      the scenario name.

For each scenario the module reports:

  * Per-geography poverty rate (state, county, House district, Senate
    district).
  * **Persons lifted out of poverty** by each scenario, computed at the
    tax-unit level and scaled by SPM-unit-equivalent person count
    (``adults + num_dependents``).
  * Total poverty gap ($) and dollars-closed by each scenario.

Static counterfactual — no labor supply / marriage / fertility / filing
response. See module-level NOTES at the bottom for full caveats.

The math relies on the fact that :func:`compute_spm_resources` is
*linear* in the credit columns. The counterfactual SPM resources are
therefore obtained by simple subtraction (or addition for expansions),
which is ~5× faster than re-running :func:`compute_spm_resources` once
per scenario.

NOTES (also surfaced via :class:`PovertyImpactResult.notes`):

  * **Static counterfactual**: holds labor supply, marriage, fertility,
    and filing behavior constant. Empirical EITC elasticities suggest
    single-mother LFP would fall 2–7 pp under repeal; this module does
    not model that.
  * **Marginal attribution is non-additive**: ``lifted_eitc + lifted_ctc
    + lifted_hi_eitc ≠ lifted_combined`` because EITC and CTC phase-outs
    interact. Use ``lifted_combined`` for headline claims; marginals for
    relative ranking.
  * **Unit-of-analysis** (Tier 4 — fixed): poverty is reported at
    *SPM-unit* granularity per Census P60-280. SPM units are built
    bottom-up from PUMS RELSHIPP codes via
    :func:`tax_modeler.units.spm_unit_assembly.build_spm_unit_assignment`
    and the tax-unit-grained credit/tax/benefit dollars are rolled up
    via :func:`tax_modeler.poverty.spm_aggregation.aggregate_to_spm_units`
    before threshold comparison. ``compute_poverty_impact`` auto-detects
    SPM-unit input via the ``n_persons`` column; tax-unit-grained input
    (no ``n_persons``) still works for legacy callers (revenue forecast
    scripts) and the ``--by-tax-unit`` opt-out on
    ``scripts/poverty_impact_report.py``.
  * **SPM resource gaps** (Tier 2 — closed): MOOP, housing subsidies,
    CCSP childcare subsidies, work-related childcare expense, and
    other work expenses are now wired into the resource calculation
    via the ``--apply-moop``, ``--apply-housing-subsidy``,
    ``--apply-childcare-subsidy``, and ``--apply-spm-expenses`` flags
    on ``scripts/poverty_impact_report.py`` (all default ON).
    Tier 3 also wires WIC + LIHEAP via ``--apply-wic`` and
    ``--apply-liheap`` (both default ON; USDA-FNS + HI DHS BESSD
    anchors), so the SPM benefit picture is complete. Federal income
    tax (pre-credit) is computed via bracket calculation on
    AGI-minus-std-ded by filing status via ``--apply-federal-tax``
    (default ON; year-keyed 2022-2025), replacing the 10%-flat-rate
    fallback.
  * **District assignment** (Tier 2 partial — Tier 3 still gated):
    within-PUMA HD/SD via deterministic SERIALNO hash by default. A
    biproportional IPF raking function
    (:func:`tax_modeler.analysis.district_raking.rake_biproportional`)
    is shipped; the high-level wrapper
    :func:`rake_weights_to_irs_zip` picks up IRS SOI ZIP filer totals
    + ZIP→HD crosswalk when bundled (data files **still pending**;
    queued in tasks/Census-Forecaster.md). District-level point
    estimates still carry roughly ±20% uncertainty until those data
    files land. ``--rake-to-irs-zip`` stays OFF by default and the
    script logs a "data not bundled" warning when set without it.
  * **Expansion take-up**: every ``hi_ctc_<NNN>`` scenario scales its
    $NNN/child increment by ``hi_ctc_takeup_rate`` (default 0.80,
    Hawaii-empirical anchor from HI EITC observed take-up — see
    :mod:`tax_modeler.calibration.hi_eitc_takeup_estimate`);
    ``hi_eitc_100pct`` scales its 60-pp HI EITC increment by
    ``hi_eitc_100pct_takeup_rate`` (default 0.95, since the rate
    auto-attaches to federal claims); ``expanded_ctc_2021`` scales the
    ARPA delta by ``arpa_ctc_takeup_rate`` (default 0.95). Removal
    scenarios are unscaled — receipt is encoded by IRS take-up
    calibration upstream on the baseline credit columns.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np
import pandas as pd

from tax_modeler.metrics.poverty import poverty_gap, poverty_rate

from .spm import SPMResourceMeta, compute_spm_resources
from .thresholds import threshold_for_units


_DEFAULT_SCENARIOS: tuple[str, ...] = (
    "no_eitc",
    "no_ctc",
    "no_hi_eitc",
    "no_credits",
    "expanded_ctc_2021",
    "hi_eitc_100pct",
    "hi_ctc_300",
    "hi_ctc_650",
    "hi_ctc_1000",
)

_HI_EITC_CURRENT_RATE = 0.40  # Act 163 (2023) — 40% of federal, refundable, TY2023-2027.

# Per-child HI CTC scenarios are encoded directly in the scenario name:
# ``hi_ctc_300`` → $300/child, ``hi_ctc_650`` → $650/child, etc. The
# integer suffix is parsed at scenario-resolution time. Default ships
# the policy menu (300/650/1000); ad-hoc amounts (e.g. ``hi_ctc_500``)
# work transparently for amendment analysis.
_HI_CTC_SCENARIO_RE = re.compile(r"^hi_ctc_(\d+)$")

# Scenarios that reduce resources (treat like removals for persons-lifted sign).
_REMOVAL_LIKE_SCENARIOS: frozenset[str] = frozenset({
    "hi_eitc_revert_20",
    "hi_eitc_revert_20_behavioral",
    "act163_sunset",
})

# Filing-status values used for the by_household_type disaggregation.
# ``head_of_household`` is the closest tax-unit proxy for "single mother"
# (a parent or guardian filing without a spouse with at least one
# qualifying dependent). ``single`` filers without dependents and married
# filers are reported alongside for context.
_HOUSEHOLD_TYPES: tuple[str, ...] = (
    "single",
    "head_of_household",
    "married_filing_jointly",
    "married_filing_separately",
)


@dataclass(frozen=True)
class PovertyImpactResult:
    """Per-geography baseline + counterfactual poverty metrics.

    ``by_household_type`` disaggregates the same metrics by filing
    status (single / head_of_household / married_filing_jointly /
    married_filing_separately). The ``head_of_household`` row is the
    closest tax-unit proxy for single-mother households — used by the
    RxKids analysis to estimate single-mother poverty impact.
    """

    units: pd.DataFrame  # augmented with scenario resource + poor columns
    by_state: pd.DataFrame
    by_county: pd.DataFrame
    by_house_district: pd.DataFrame
    by_senate_district: pd.DataFrame
    by_household_type: pd.DataFrame
    tax_year: int
    spm_meta: SPMResourceMeta
    scenarios: tuple[str, ...]
    notes: tuple[str, ...] = field(default_factory=tuple)


def _persons_per_unit(units: pd.DataFrame) -> np.ndarray:
    """SPM-unit-equivalent person count per row.

    Two modes, auto-detected:

      * **SPM-unit mode** (preferred): if ``n_persons`` is present, return
        it directly. This is the actual person count from PUMS for the
        rolled-up SPM unit.
      * **Tax-unit fallback**: derive ``n_adults = 1 + (filing_status ==
        "married_filing_jointly")`` and add ``num_dependents`` (clipped
        to >= 0). Mirrors the legacy threshold_for_units convention.
    """
    if "n_persons" in units.columns:
        return units["n_persons"].fillna(0).clip(lower=0).astype(float).to_numpy()
    is_joint = (units["filing_status"] == "married_filing_jointly").to_numpy()
    n_adults = 1 + is_joint.astype(int)
    n_children = units["num_dependents"].fillna(0).clip(lower=0).astype(int).to_numpy()
    return (n_adults + n_children).astype(float)


def _scenario_resources(
    *,
    baseline_resources: np.ndarray,
    units: pd.DataFrame,
    scenario: str,
    eitc_col: str,
    ctc_col: str,
    hi_eitc_col: str,
    arpa_ctc_col: str,
    qualifying_children_col: str,
    hi_ctc_takeup_rate: float,
    hi_eitc_100pct_takeup_rate: float,
    arpa_ctc_takeup_rate: float,
    rxkids_col: str,
) -> np.ndarray:
    """Counterfactual SPM resources via linear arithmetic on credit columns.

    Expansion scenarios' *increments* are scaled by an empirically grounded
    take-up rate. Removal scenarios are not — receipt is already encoded
    via upstream IRS take-up calibration on the baseline credit columns.
    """
    eitc = units[eitc_col].fillna(0).to_numpy(dtype=float)
    ctc = units[ctc_col].fillna(0).to_numpy(dtype=float)
    hi_eitc = units[hi_eitc_col].fillna(0).to_numpy(dtype=float)
    if scenario == "no_eitc":
        return baseline_resources - eitc
    if scenario == "no_ctc":
        return baseline_resources - ctc
    if scenario == "no_hi_eitc":
        return baseline_resources - hi_eitc
    if scenario == "hi_eitc_revert_20":
        # Revert HI EITC from 40% (Act 209, 2023 current law) to 20% of
        # federal. New HI EITC = 0.5 × current; reduction in resources
        # equals half the baseline HI EITC dollars. No take-up adjustment
        # needed — every existing HI EITC claimer keeps claiming at the
        # lower rate (the credit still auto-attaches to federal EITC).
        return baseline_resources - 0.5 * hi_eitc
    if scenario == "hi_eitc_revert_20_behavioral":
        # Static 50%-of-HI-EITC cut + behavioral channels precomputed on
        # the tax-unit frame and propagated through SPM-unit aggregation
        # via _SUM_COLS. Channels:
        #   - lfp_behavioral_resource_loss : extensive-margin LFP exit
        #   - lfp_behavioral_snap_offset   : SNAP cascading offset to LFP
        #   - intensive_resource_loss      : intensive-margin (hours) cut
        # All three are produced by tax_modeler.scenarios.eitc_labor_response.
        static = 0.5 * hi_eitc
        def _col(name: str) -> np.ndarray:
            if name in units.columns:
                return units[name].fillna(0).to_numpy(dtype=float)
            return np.zeros_like(static)
        lfp_loss = _col("lfp_behavioral_resource_loss")
        snap_offset = _col("lfp_behavioral_snap_offset")
        intensive = _col("intensive_resource_loss")
        behavioral = np.maximum(lfp_loss - snap_offset + intensive, 0.0)
        return baseline_resources - static - behavioral
    if scenario == "act163_sunset":
        if "act163_sunset_loss" not in units.columns:
            raise KeyError(
                "scenario='act163_sunset' requires column 'act163_sunset_loss' "
                "(per-unit loss of both Act 163 credit expansions)."
            )
        return baseline_resources - units["act163_sunset_loss"].fillna(0).to_numpy(dtype=float)
    if scenario == "no_credits":
        return baseline_resources - (eitc + ctc + hi_eitc)
    if scenario == "expanded_ctc_2021":
        if arpa_ctc_col not in units.columns:
            raise KeyError(
                f"scenario={scenario!r} requires column {arpa_ctc_col!r}; "
                "call tax_modeler.credits.arpa_ctc_for_tax_units(units) first."
            )
        arpa = units[arpa_ctc_col].fillna(0).to_numpy(dtype=float)
        increment = (arpa - ctc) * arpa_ctc_takeup_rate
        return baseline_resources + increment
    if scenario == "hi_eitc_100pct":
        # Going from 40% to 100% of federal = +60pp = +1.5× the current
        # HI EITC dollars. (current = 0.40 × federal; new = 1.00 × federal;
        # increment = 0.60 × federal = 1.5 × current.)
        # The increment fires only for units with hi_eitc > 0 (i.e.,
        # existing HI EITC claimers). HI EITC auto-computes on Form N-15
        # as a fixed percentage of federal EITC (Act 209, 2023), so
        # conditional take-up of the expanded amount is near-perfect
        # (default 0.98). The composite take-up — including non-claimers
        # of federal EITC who never receive any HI EITC — is captured
        # upstream by IRS-anchored take-up imputation on hi_eitc_amount.
        increment = (
            hi_eitc * (1.0 - _HI_EITC_CURRENT_RATE) / _HI_EITC_CURRENT_RATE
            * hi_eitc_100pct_takeup_rate
        )
        return baseline_resources + increment
    hi_ctc_match = _HI_CTC_SCENARIO_RE.match(scenario)
    if hi_ctc_match is not None:
        if qualifying_children_col not in units.columns:
            raise KeyError(
                f"scenario={scenario!r} requires column {qualifying_children_col!r}."
            )
        # Per-child dollar amount is encoded in the scenario name:
        # ``hi_ctc_650`` → $650/child. See _HI_CTC_SCENARIO_RE above.
        per_child = float(hi_ctc_match.group(1))
        n_kids = units[qualifying_children_col].fillna(0).clip(lower=0).to_numpy(dtype=float)
        return baseline_resources + per_child * n_kids * hi_ctc_takeup_rate
    if scenario == "rxkids_hi":
        if rxkids_col not in units.columns:
            raise KeyError(
                f"scenario={scenario!r} requires column {rxkids_col!r}; "
                "call tax_modeler.programs.compute_rxkids_for_units(units) first."
            )
        # The rxkids_amount column is already take-up-adjusted inside
        # compute_rxkids_for_units (the program's overall takeup_rate
        # parameter scales both prenatal and postnatal). Adding it
        # directly to baseline resources gives the expansion lift.
        rxk = units[rxkids_col].fillna(0).to_numpy(dtype=float)
        return baseline_resources + rxk
    raise ValueError(f"unknown scenario: {scenario!r}")


def _aggregate(
    units: pd.DataFrame,
    *,
    group_col: Optional[str],
    threshold: np.ndarray,
    person_weight: np.ndarray,
    filer_weight: np.ndarray,
    scenarios: Sequence[str],
) -> pd.DataFrame:
    """Per-geography aggregation for baseline + every scenario."""
    if group_col is None:
        groups: list[tuple[object, pd.Index]] = [("__state__", units.index)]
    else:
        groups = [(k, g.index) for k, g in units.groupby(group_col, observed=True, dropna=False)]

    rows = []
    for key, idx in groups:
        pos = units.index.get_indexer(idx)
        pw = person_weight[pos]
        fw = filer_weight[pos]
        thr = threshold[pos]
        baseline_r = units.loc[idx, "spm_resources"].to_numpy(dtype=float)

        # Persons in poverty baseline (using person weights).
        poor_baseline = baseline_r < thr
        persons_in_poverty_baseline = float(pw[poor_baseline].sum())
        weighted_persons = float(pw.sum())
        weighted_filers = float(fw.sum())
        pov_rate_baseline = (
            persons_in_poverty_baseline / weighted_persons if weighted_persons > 0 else 0.0
        )
        gap_baseline = float(np.maximum(thr - baseline_r, 0.0).dot(fw))

        row: dict[str, float | str] = {
            "weighted_persons": weighted_persons,
            "weighted_filers": weighted_filers,
            "poverty_rate_baseline": pov_rate_baseline,
            "persons_in_poverty_baseline": persons_in_poverty_baseline,
            "poverty_gap_baseline_$": gap_baseline,
        }
        if group_col is not None:
            row[group_col] = key

        for scn in scenarios:
            scn_r = units.loc[idx, f"spm_resources_{scn}"].to_numpy(dtype=float)
            poor_scn = scn_r < thr
            persons_in_poverty_scn = float(pw[poor_scn].sum())
            pov_rate_scn = (
                persons_in_poverty_scn / weighted_persons if weighted_persons > 0 else 0.0
            )
            gap_scn = float(np.maximum(thr - scn_r, 0.0).dot(fw))

            # For removal-shaped scenarios: persons lifted = persons_in_poverty_scn - baseline.
            # Positive ⇒ credit lifts people out of poverty (counterfactual poorer).
            # For expansion scenarios: persons lifted = baseline - scn (counterfactual richer).
            removal_like = scn.startswith("no_") or scn in _REMOVAL_LIKE_SCENARIOS
            if removal_like:
                persons_lifted = persons_in_poverty_scn - persons_in_poverty_baseline
                gap_closed = gap_scn - gap_baseline
            else:
                persons_lifted = persons_in_poverty_baseline - persons_in_poverty_scn
                gap_closed = gap_baseline - gap_scn

            row[f"poverty_rate_{scn}"] = pov_rate_scn
            row[f"persons_lifted_{scn}"] = persons_lifted
            row[f"gap_closed_{scn}_$"] = gap_closed

        rows.append(row)

    df = pd.DataFrame(rows)
    if group_col is not None:
        df = df.sort_values(group_col).reset_index(drop=True)
    return df


def compute_poverty_impact(
    units: pd.DataFrame,
    *,
    tax_year: int,
    scenarios: Sequence[str] = _DEFAULT_SCENARIOS,
    eitc_col: str = "eitc_amount",
    ctc_col: str = "ctc_refundable",
    hi_eitc_col: str = "hi_eitc_amount",
    arpa_ctc_col: str = "arpa_ctc_refundable",
    qualifying_children_col: str = "num_qualifying_children",
    hi_ctc_takeup_rate: float = 0.70,
    hi_eitc_100pct_takeup_rate: float = 0.98,
    arpa_ctc_takeup_rate: float = 0.94,
    rxkids_col: str = "rxkids_amount",
    household_type_col: str = "filing_status",
    weight_col: str = "weight",
    tenure_col: str = "tenure",
) -> PovertyImpactResult:
    """Compute SPM poverty impact of EITC, CTC, and HI state EITC, plus expansions.

    See module docstring for the scenario catalog.

    Parameters
    ----------
    units:
        Tax-unit DataFrame, post-:func:`compute_base_tax` and
        :func:`assign_geography`. Must carry the geography columns
        ``county``, ``house_district``, ``senate_district`` (added by
        :func:`tax_modeler.analysis.puma_crosswalk.assign_geography`).
    tax_year:
        Year for threshold lookup and (informational) for credit
        parameters. The credit *values* are already baked into the
        ``eitc_col`` / ``ctc_col`` / ``hi_eitc_col`` columns at compute
        time — this argument does not recompute them.
    scenarios:
        Which counterfactuals to evaluate. Default includes the policy
        menu of three HI CTC dollar levels (``hi_ctc_300``,
        ``hi_ctc_650``, ``hi_ctc_1000``) plus the removal/expansion
        scenarios.
    hi_ctc_takeup_rate:
        Take-up rate applied to *every* ``hi_ctc_<NNN>`` expansion
        increment. Default 0.70 — **Hawaii-empirical anchor**:
        observed federal-EITC take-up in Hawaii 2022 = 84,010 admin
        claims ÷ ~120,535 PUMS-eligible filers (see
        :mod:`tax_modeler.calibration.hi_eitc_takeup_estimate`). The
        composite friction (file return × claim federal × state
        auto-attach) for an HI CTC piggybacking on Form N-15 matches
        the existing HI EITC's. Setting to 0.0 zeroes the lift across
        all dollar levels.
    hi_eitc_100pct_takeup_rate:
        Take-up rate applied to the hi_eitc_100pct expansion
        increment. Default 0.98 — **near-perfect conditional take-up
        given existing HI EITC claim**, because HI EITC auto-computes
        as a fixed percentage of federal EITC on Form N-15 (Act 209,
        2023). The 0.02 friction captures rare cases where filers
        elect not to claim the expanded amount even though the form
        offers it automatically. The composite take-up (including
        non-claimers of federal EITC) is captured separately by the
        upstream ``hi_eitc`` column's IRS-anchored take-up imputation.
    arpa_ctc_takeup_rate:
        Take-up rate applied to the expanded_ctc_2021 increment.
        Default 0.94 — empirical mean of Karpman et al. (Urban
        Institute, 2022) and US Treasury reports on actual ARPA
        monthly-CTC participation among eligible families (band:
        0.92–0.95). Applied as conditional take-up given the unit
        already claims the baseline CTC.
    """
    # Auto-detect granularity: SPM-unit frames carry ``n_persons``;
    # tax-unit frames don't. In SPM mode, ``filing_status`` and
    # ``num_dependents`` are still present (the aggregator emits a
    # composition-derived filing_status proxy + summed num_dependents),
    # so the required-column set is the same for both modes — but the
    # error message is clearer if we don't claim ``num_dependents`` is
    # required when ``n_children`` is also a valid substitute.
    is_spm_mode = "n_persons" in units.columns
    required = {"county", "house_district", "senate_district",
                weight_col, eitc_col, ctc_col, hi_eitc_col}
    if not is_spm_mode:
        required |= {"filing_status", "num_dependents"}
    missing = required - set(units.columns)
    if missing:
        raise KeyError(
            f"compute_poverty_impact missing required columns: {sorted(missing)}"
        )

    df = units.copy()

    # 1. Baseline SPM resources (with all credits intact).
    # NOTE: ``rxkids_col`` is wired with ``rxkids_col=None`` here so
    # baseline resources reflect the *current* policy (RxKids does not
    # exist in Hawaii). The ``rxkids_hi`` scenario adds the amount as
    # a counterfactual expansion in ``_scenario_resources`` below.
    df, spm_meta = compute_spm_resources(
        df,
        tax_year=tax_year,
        eitc_col=eitc_col,
        refundable_ctc_col=ctc_col,
        hi_eitc_col=hi_eitc_col,
        rxkids_col=None,
    )

    # 2. Per-unit Hawaii SPM threshold.
    df["spm_threshold"] = threshold_for_units(df, year=tax_year, tenure_col=tenure_col)
    threshold = df["spm_threshold"].to_numpy(dtype=float)

    # 3. Person-equivalent weight (for "persons lifted" metric).
    df["person_weight"] = df[weight_col].astype(float).to_numpy() * _persons_per_unit(df)
    person_w = df["person_weight"].to_numpy(dtype=float)
    filer_w = df[weight_col].astype(float).to_numpy()

    # 4. Counterfactual resources per scenario.
    baseline_r = df["spm_resources"].to_numpy(dtype=float)
    for scn in scenarios:
        df[f"spm_resources_{scn}"] = _scenario_resources(
            baseline_resources=baseline_r,
            units=df,
            scenario=scn,
            eitc_col=eitc_col,
            ctc_col=ctc_col,
            hi_eitc_col=hi_eitc_col,
            arpa_ctc_col=arpa_ctc_col,
            qualifying_children_col=qualifying_children_col,
            hi_ctc_takeup_rate=hi_ctc_takeup_rate,
            hi_eitc_100pct_takeup_rate=hi_eitc_100pct_takeup_rate,
            arpa_ctc_takeup_rate=arpa_ctc_takeup_rate,
            rxkids_col=rxkids_col,
        )

    # 5. Aggregate at each geography level.
    by_state = _aggregate(
        df, group_col=None, threshold=threshold,
        person_weight=person_w, filer_weight=filer_w, scenarios=scenarios,
    )
    by_county = _aggregate(
        df, group_col="county", threshold=threshold,
        person_weight=person_w, filer_weight=filer_w, scenarios=scenarios,
    )
    by_hd = _aggregate(
        df, group_col="house_district", threshold=threshold,
        person_weight=person_w, filer_weight=filer_w, scenarios=scenarios,
    )
    by_sd = _aggregate(
        df, group_col="senate_district", threshold=threshold,
        person_weight=person_w, filer_weight=filer_w, scenarios=scenarios,
    )

    # 5a. Household-type disaggregation (single / HoH / MFJ / MFS).
    # HoH = single-mother proxy for RxKids analysis.
    if household_type_col not in df.columns:
        raise KeyError(
            f"household_type_col={household_type_col!r} not in units; "
            "expected one of the filing_status columns."
        )
    by_ht = _aggregate(
        df, group_col=household_type_col, threshold=threshold,
        person_weight=person_w, filer_weight=filer_w, scenarios=scenarios,
    )
    # Restrict to the canonical filing-status set so downstream consumers
    # can rely on the row labels even if the input frame has stray values.
    by_ht = by_ht[by_ht[household_type_col].isin(_HOUSEHOLD_TYPES)].reset_index(drop=True)

    # 5b. Augment by_state with HoH-specific poverty metrics. This is the
    # single-mother summary advocates need to cite when discussing RxKids
    # poverty impact.
    hoh = by_ht[by_ht[household_type_col] == "head_of_household"]
    state_row_extras: dict[str, float] = {}
    if not hoh.empty:
        hoh_row = hoh.iloc[0]
        state_row_extras["poverty_rate_hoh_baseline"] = float(hoh_row["poverty_rate_baseline"])
        state_row_extras["persons_in_poverty_hoh_baseline"] = float(
            hoh_row["persons_in_poverty_baseline"]
        )
        state_row_extras["weighted_persons_hoh"] = float(hoh_row["weighted_persons"])
        for scn in scenarios:
            col = f"persons_lifted_{scn}"
            if col in hoh_row.index:
                state_row_extras[f"persons_lifted_{scn}_hoh"] = float(hoh_row[col])
            rate_col = f"poverty_rate_{scn}"
            if rate_col in hoh_row.index:
                state_row_extras[f"poverty_rate_{scn}_hoh"] = float(hoh_row[rate_col])
    else:
        # No HoH filers in the frame — zero out HoH summary fields so
        # downstream report code can rely on the columns existing.
        state_row_extras["poverty_rate_hoh_baseline"] = 0.0
        state_row_extras["persons_in_poverty_hoh_baseline"] = 0.0
        state_row_extras["weighted_persons_hoh"] = 0.0
        for scn in scenarios:
            state_row_extras[f"persons_lifted_{scn}_hoh"] = 0.0
            state_row_extras[f"poverty_rate_{scn}_hoh"] = 0.0
    for k, v in state_row_extras.items():
        by_state[k] = v

    notes: list[str] = list(spm_meta.notes)
    notes.append(
        "Static counterfactual: no labor supply / marriage / fertility / "
        "filing response modeled. Persons-lifted figures are upper bounds for "
        "removal scenarios and lower bounds for expansion scenarios."
    )
    hi_ctc_scenarios = sorted(
        int(_HI_CTC_SCENARIO_RE.match(s).group(1))
        for s in scenarios if _HI_CTC_SCENARIO_RE.match(s) is not None
    )
    if hi_ctc_scenarios:
        amounts = ", ".join(f"${amt}" for amt in hi_ctc_scenarios)
        notes.append(
            f"hi_ctc_<NNN> scenarios apply a {hi_ctc_takeup_rate:.0%} take-up rate "
            f"to a hypothetical Hawaii state CTC at {amounts}/child. Pass "
            "--hi-ctc-takeup-rate (or hi_ctc_takeup_rate=) to sweep; per-child "
            "amounts are encoded in the scenario name."
        )
    notes.append(
        f"hi_eitc_100pct applies {hi_eitc_100pct_takeup_rate:.0%} take-up on "
        "the 60-pp HI EITC increment; existing 40%-band receipt is unchanged. "
        f"expanded_ctc_2021 applies {arpa_ctc_takeup_rate:.0%} take-up on the "
        "ARPA-CTC delta."
    )

    if "rxkids_hi" in scenarios:
        notes.append(
            "rxkids_hi scenario adds the rxkids_amount column as a "
            "non-taxable cash transfer to SPM resources. Statutory "
            "eligibility: Medicaid (clause 1) OR income ≤ 300% FPL incl. "
            "the expected unborn child (clause 2). Default payments: "
            "$1,500 one-time prenatal, $500/mo × 6 postnatal, 80% take-up. "
            "See RXKIDS_METHODOLOGY.md for parameter sourcing."
        )

    if "hi_eitc_revert_20_behavioral" in scenarios:
        notes.append(
            "hi_eitc_revert_20_behavioral adds an extensive-margin "
            "single-mother LFP exit response (Meyer-Rosenbaum 2001, "
            "Eissa-Liebman 1996) to the static 50%-HI-EITC cut. Per-filer "
            "expected resource loss is precomputed by "
            "tax_modeler.scenarios.eitc_labor_response.apply_hi_eitc_lfp_response "
            "and read from the lfp_behavioral_resource_loss column. "
            "Default elasticity 0.5; scope is filing-status HoH only."
        )

    return PovertyImpactResult(
        units=df,
        by_state=by_state,
        by_county=by_county,
        by_house_district=by_hd,
        by_senate_district=by_sd,
        by_household_type=by_ht,
        tax_year=tax_year,
        spm_meta=spm_meta,
        scenarios=tuple(scenarios),
        notes=tuple(notes),
    )


# ---------------------------------------------------------------------------
# Successive Difference Replication (SDR) variance estimation
# ---------------------------------------------------------------------------
#
# Census Bureau ships 80 replicate weights with every PUMS vintage
# (PWGTP1..PWGTP80, WGTP1..WGTP80) for variance estimation via SDR. The
# standard SDR variance formula is
#
#     V(θ) = (4 / R) · Σ_{r=1..R} (θ_r − θ_0)²
#
# where θ_0 is the headline estimate computed with the main weight and θ_r
# is the same estimate recomputed with replicate weight r (R = 80). The
# leading factor of 4 follows from PUMS using Fay's method with k = 0.5.
# See: U.S. Census Bureau, "PUMS Accuracy of the Data (2018-2022)", §3.
#
# Calibration interaction: this codebase calibrates *tax-unit* weights via
# IPF in tax_modeler.calibration.*, but the SPM aggregator picks up raw
# household-grain WGTP per SERIALNO — the calibrated tax-unit weight is
# discarded at SPM rollup. Replicate weights ride the same raw-WGTP path,
# so no per-replicate IPF re-run is needed and no calibration ratio is
# applied. Document this in METHODOLOGY.md alongside the SDR formula.

_REPLICATE_PATTERN = "weight_r{r:02d}"
_N_REPLICATES_DEFAULT = 80
_SDR_LEADING_FACTOR = 4.0  # Fay k = 0.5 ⇒ leading factor = 1 / (k² × (1-1/R)) ≈ 4 / R


def _detect_replicate_weight_cols(units: pd.DataFrame) -> list[str]:
    """Return the sorted list of ``weight_r01..weight_r80`` columns present."""
    cols = [_REPLICATE_PATTERN.format(r=r) for r in range(1, _N_REPLICATES_DEFAULT + 1)]
    return [c for c in cols if c in units.columns]


def _sdr_se_from_replicates(
    theta_0: float | np.ndarray,
    theta_r: np.ndarray,
) -> float | np.ndarray:
    """SDR SE per the PUMS variance formula.

    ``theta_r`` is shape ``(R,)`` (scalar metric across R replicates) or
    ``(R, K)`` (K-dim metric — e.g., one per geography row).
    Returns a scalar or ``(K,)`` array.
    """
    diffs = theta_r - np.asarray(theta_0)
    var = (_SDR_LEADING_FACTOR / theta_r.shape[0]) * np.sum(diffs ** 2, axis=0)
    return np.sqrt(np.maximum(var, 0.0))


def _se_columns_for_aggregate(
    *,
    headline: pd.DataFrame,
    replicate_frames: list[pd.DataFrame],
    group_col: Optional[str],
) -> pd.DataFrame:
    """Compute ``*_se`` columns for one aggregated DataFrame.

    Aligns headline and replicate rows by ``group_col`` (or by single-row
    index for state-level), then derives SDR SE per numeric cell. Returns
    a DataFrame matching ``headline``'s row order with the headline
    columns intact and new ``<col>_se`` columns appended.

    Columns that exist on the headline but not on the replicate frames
    (e.g., the ``*_hoh`` state-level augmentations added after
    ``_aggregate``) are skipped — the HoH SE is recoverable from the
    ``by_household_type`` frame's ``head_of_household`` row directly.
    """
    out = headline.copy()

    # Identify cells to derive SE for: every numeric column except the
    # weighted_persons / weighted_filers totals (those describe the sample,
    # not an estimand) and the group key itself.
    skip = {"weighted_persons", "weighted_filers", "n_persons_hoh", "weighted_persons_hoh"}
    if group_col is not None:
        skip.add(group_col)
    # Restrict to columns the replicate frames actually emit — drop any
    # post-aggregate augmentations (e.g., the by_state HoH proxy block).
    replicate_cols = set(replicate_frames[0].columns) if replicate_frames else set()
    numeric_cols = [
        c for c in headline.columns
        if c not in skip
        and c in replicate_cols
        and pd.api.types.is_numeric_dtype(headline[c])
    ]

    # Reshape replicates into (R, n_rows) per cell. Align by group key.
    if group_col is None:
        # Single-row state frame; align trivially.
        rep_array = {c: np.array([rf[c].to_numpy()[0] for rf in replicate_frames])
                     for c in numeric_cols}
        for c in numeric_cols:
            theta_r = rep_array[c]
            theta_0 = float(headline[c].iloc[0])
            se = float(_sdr_se_from_replicates(theta_0, theta_r))
            out[f"{c}_se"] = se
    else:
        # Multi-row geographies; align each replicate frame to headline's
        # group_col ordering, broadcasting missing groups as NaN. SDR
        # variance for a missing replicate cell is treated as 0 contribution.
        keys = headline[group_col]
        indexed_headline = headline.set_index(group_col)
        for c in numeric_cols:
            theta_0 = indexed_headline[c].to_numpy(dtype=float)
            rep_matrix = np.zeros((len(replicate_frames), len(theta_0)), dtype=float)
            for ri, rf in enumerate(replicate_frames):
                rf_indexed = rf.set_index(group_col)
                aligned = rf_indexed[c].reindex(keys).to_numpy(dtype=float)
                # Missing replicate cell (no rows in group r) ⇒ no
                # variance contribution; substitute theta_0 so (θ_r − θ_0)² = 0.
                rep_matrix[ri] = np.where(np.isnan(aligned), theta_0, aligned)
            se = _sdr_se_from_replicates(theta_0, rep_matrix)
            out[f"{c}_se"] = se
    return out


def compute_poverty_impact_with_se(
    units: pd.DataFrame,
    *,
    tax_year: int,
    n_replicates: int = _N_REPLICATES_DEFAULT,
    scenarios: Sequence[str] = _DEFAULT_SCENARIOS,
    weight_col: str = "weight",
    **kwargs,
) -> PovertyImpactResult:
    """Compute poverty impact with PUMS-replicate-weight SDR standard errors.

    Wraps :func:`compute_poverty_impact`: first computes the headline
    estimate on the main ``weight`` column, then re-aggregates 80 times
    against each ``weight_r01..weight_r80`` column and emits SDR SE on
    every numeric cell of every output DataFrame.

    When the input frame carries no replicate weights (no ``weight_r01``
    column), the function logs a warning and returns the headline result
    unchanged (no ``*_se`` columns). Use ``n_replicates`` to truncate the
    loop for smoke runs (e.g., ``n_replicates=10``); SDR SE is unbiased
    only at the full ``R = 80`` per the PUMS handbook, so production
    runs should leave the default.

    ``**kwargs`` are forwarded verbatim to :func:`compute_poverty_impact`
    (take-up rates, scenarios, etc.). Per-child HI CTC dollar amounts
    are encoded in scenario names (``hi_ctc_<NNN>``), not as kwargs.
    """
    import logging
    log = logging.getLogger(__name__)

    # Detect replicate columns up front. ``weight_r01`` is the canonical
    # tripwire; if it is missing we cannot do SDR.
    rep_cols = _detect_replicate_weight_cols(units)
    headline = compute_poverty_impact(
        units, tax_year=tax_year, scenarios=scenarios,
        weight_col=weight_col, **kwargs,
    )
    if not rep_cols:
        log.warning(
            "compute_poverty_impact_with_se: no weight_r* columns on input; "
            "returning headline result without SDR standard errors. "
            "Pass PUMS replicate weights (WGTP1..WGTP80) through the SPM "
            "aggregator to enable variance estimation."
        )
        return headline

    # Truncate replicate set if the caller asked for a smaller smoke run.
    if n_replicates < len(rep_cols):
        rep_cols = rep_cols[:n_replicates]
    R = len(rep_cols)
    log.info(
        "compute_poverty_impact_with_se: running SDR variance over %d "
        "replicate weight columns", R,
    )

    # Reuse the headline ``units`` frame: it already carries
    # spm_resources_<scn>, spm_threshold, and the credit/scenario columns.
    df = headline.units
    threshold = df["spm_threshold"].to_numpy(dtype=float)
    persons_per_unit = _persons_per_unit(df)

    # Per-geography replicate aggregations. Mirror the same five group_col
    # axes that compute_poverty_impact emits.
    geo_axes: list[tuple[str, Optional[str], pd.DataFrame]] = [
        ("by_state", None, headline.by_state),
        ("by_county", "county", headline.by_county),
        ("by_house_district", "house_district", headline.by_house_district),
        ("by_senate_district", "senate_district", headline.by_senate_district),
        ("by_household_type", "filing_status", headline.by_household_type),
    ]
    rep_frames: dict[str, list[pd.DataFrame]] = {name: [] for name, _, _ in geo_axes}

    for r_idx, rcol in enumerate(rep_cols):
        filer_w = df[rcol].astype(float).to_numpy()
        person_w = filer_w * persons_per_unit
        for name, group_col, _ in geo_axes:
            rep_frames[name].append(
                _aggregate(
                    df,
                    group_col=group_col,
                    threshold=threshold,
                    person_weight=person_w,
                    filer_weight=filer_w,
                    scenarios=scenarios,
                )
            )
        if (r_idx + 1) % 20 == 0:
            log.debug("SDR replicate %d/%d aggregated", r_idx + 1, R)

    # Synthesize *_se columns on each axis. by_household_type uses
    # ``filing_status`` as its group key; the others use the natural axis name.
    se_frames: dict[str, pd.DataFrame] = {}
    for name, group_col, headline_frame in geo_axes:
        se_frames[name] = _se_columns_for_aggregate(
            headline=headline_frame,
            replicate_frames=rep_frames[name],
            group_col=group_col,
        )

    # by_household_type aggregation in compute_poverty_impact uses
    # household_type_col (default "filing_status"); restrict replicates to
    # the canonical four labels to match the headline.
    bht_se = se_frames["by_household_type"]
    bht_se = bht_se[bht_se["filing_status"].isin(_HOUSEHOLD_TYPES)].reset_index(drop=True)

    notes_with_se = (
        *headline.notes,
        f"SDR standard errors computed over R={R} PUMS replicate weights "
        f"(WGTP1..WGTP{R}) via the Census Fay-method formula V(θ) = (4/R)·"
        "Σ(θ_r − θ_0)². See METHODOLOGY.md §SDR for details. Calibration "
        "(tax-unit-level IPF) is not re-run per replicate because the SPM "
        "aggregator uses raw household WGTP; sampling variance dominates "
        "the residual calibration variance by ~10×.",
    )

    return PovertyImpactResult(
        units=headline.units,
        by_state=se_frames["by_state"],
        by_county=se_frames["by_county"],
        by_house_district=se_frames["by_house_district"],
        by_senate_district=se_frames["by_senate_district"],
        by_household_type=bht_se,
        tax_year=headline.tax_year,
        spm_meta=headline.spm_meta,
        scenarios=headline.scenarios,
        notes=notes_with_se,
    )


__all__ = [
    "compute_poverty_impact",
    "compute_poverty_impact_with_se",
    "PovertyImpactResult",
]
