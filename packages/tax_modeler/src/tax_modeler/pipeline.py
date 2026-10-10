"""End-to-end Hawaii tax microsimulation pipeline.

Chains six stages into a single callable:

  1. **Load** — PUMSDataLoader reads local parquet/CSV files.
  2. **Construct** — TaxUnitConstructor builds tax filing units from person +
     household records.
  3. **Enrich** — Derives earned/investment income and creates credit-ready
     dependent dicts from the TaxUnitConstructor output schema.
  4. **Tax** — calculate_hawaii_tax_for_units computes baseline Hawaii
     liability + credits.
  5. **Calibrate** — apply_ipf_calibration_via_rake adjusts unit weights to
     match DOTAX aggregate benchmarks.
  6. **Project** — project_tax_units_forward scales income to target year,
     recalculates Hawaii tax + federal credits + poverty correction.
  7. **Estimate** — RevenueEstimator wraps the projected units for revenue
     queries.

Quick start (PUMS files required at the default data path)::

    from tax_modeler.pipeline import run_pipeline

    result = run_pipeline(target_year=2026)
    print(result.state_summary["hi_net_tax_revenue"])
    print(result.by_county)

Skip PUMS loading by supplying pre-built calibrated units::

    result = run_pipeline(target_year=2026, tax_units_df=my_calibrated_df)

Approximations
--------------
Without a person-level join, dependent ages are not available from
TaxUnitConstructor alone.  The enrichment step creates synthetic dependent
dicts with ``age=10`` for every counted dependent, so all dependents are
treated as qualifying children under 17.  This slightly overestimates CTC
for non-child dependents (e.g. elderly relatives) but is conservative for
EITC (which has a broader age cap).  Supply a pre-enriched ``tax_units_df``
to bypass this approximation.
"""
from __future__ import annotations

import logging
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

import pandas as pd

from tax_modeler.errors import MissingDataError, validate_units_schema

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class PipelineResult:
    """Output of :func:`run_pipeline`.

    All revenue figures are population-weighted dollar totals.  Use
    ``estimator`` for additional queries not pre-computed here.

    When a ``reform`` is supplied to :func:`run_pipeline`, the
    ``reform_result`` field carries the comparison vs. baseline.
    """

    tax_units: pd.DataFrame          # calibrated base-year units (post-IPF)
    projected_units: pd.DataFrame    # projected target-year units
    estimator: object                # RevenueEstimator instance

    state_summary: Dict              # estimator.state_summary()
    by_county: pd.DataFrame          # estimator.by_county()
    by_filing_status: pd.DataFrame   # estimator.by_filing_status()
    by_income_quintile: pd.DataFrame # estimator.by_income_quintile()

    base_year: int
    target_year: int
    n_units: int                     # number of tax-unit rows
    n_weighted_filers: float         # population-weighted filer count
    elapsed_seconds: float           # wall-clock time for the full run

    timings: Dict[str, float] = field(default_factory=dict)

    # Optional reform comparison (populated when run_pipeline receives a Reform)
    reform_result: Optional[object] = None  # tax_modeler.reform.ReformResult | None


# ---------------------------------------------------------------------------
# Stage helpers
# ---------------------------------------------------------------------------


def _load_pums(data_dir: Path, state: str, pums_type: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Stage 1: load person + household PUMS DataFrames."""
    from tax_modeler.loaders.pums_loader import PUMSDataLoader

    loader = PUMSDataLoader(data_dir=data_dir)
    hh_file = loader._hh_file_path(state, pums_type)
    if not hh_file.exists():
        raise MissingDataError(
            f"PUMS household file not found.\n"
            f"Download the ACS PUMS files for state FIPS {state} and place them at:\n"
            f"  {data_dir}/psam_h{state}.parquet  (or .csv)\n"
            f"  {data_dir}/psam_p{state}.parquet  (or .csv)",
            path=hh_file,
            env_var="HAWAII_PUMS_DIR",
        )
    person_df, hh_df = loader.load_data(state=state, pums_type=pums_type)
    logger.info("PUMS loaded: %d persons, %d households", len(person_df), len(hh_df))
    return person_df, hh_df


def _construct_units(person_df: pd.DataFrame, hh_df: pd.DataFrame) -> pd.DataFrame:
    """Stage 2: build tax filing units."""
    from tax_modeler.units.constructor import TaxUnitConstructor

    constructor = TaxUnitConstructor(
        person_df,
        hh_df,
        use_soi_calibration=False,  # defer calibration to explicit IPF step
        progress_bar=False,
    )
    units = constructor.create_rule_based_units(parallel=True)
    logger.info("Tax units constructed: %d units", len(units))
    return units


def enrich_with_spm_unit_id(
    units: pd.DataFrame,
    persons: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Compute SPM unit IDs from PUMS and attach them to the tax-unit frame.

    Thin glue around
    :func:`tax_modeler.units.spm_unit_assembly.build_spm_unit_assignment`
    and :func:`attach_spm_unit_id_to_tax_units`. Returns
    ``(units_with_spm_id, persons_with_assignment)``. Either frame can
    have ``person_id`` as a column or as the index; both are handled.

    Use as the first step after PUMS load + tax-unit construction so the
    ``spm_unit_id`` column rides through all subsequent enrichment and
    benefit-imputation steps unchanged.
    """
    from tax_modeler.units.spm_unit_assembly import (
        attach_spm_unit_id_to_tax_units,
        build_spm_unit_assignment,
    )

    # If persons has person_id as index, reset for the assignment step so
    # the new spm_unit_id column lands alongside it.
    pers = persons
    if pers.index.name == "person_id":
        pers = pers.reset_index()
    pers_assigned = build_spm_unit_assignment(pers)
    units_with_id = attach_spm_unit_id_to_tax_units(units, pers_assigned)
    return units_with_id, pers_assigned


def enrich_for_credits(df: pd.DataFrame) -> pd.DataFrame:
    """Stage 3: derive credit-calculation inputs from TaxUnitConstructor columns.

    Credit calculations expect ``dependents`` to be a list of dicts with
    ``age``, ``relationship``, ``citizenship``, ``months_in_home`` (plus
    ``school_level`` / ``disabled`` for the EITC qualifying-child test).
    ``TaxUnitConstructor`` now emits these REAL per-dependent records in
    ``dependents_details``; this step uses them when present and only
    synthesizes age-10 placeholders as a FALLBACK (e.g. caller-supplied frames
    or synthesized top-filer rows that lack per-dependent detail).

    Derived columns added:
      - ``earned_income``          wages + self-employment income (skipped if present)
      - ``investment_income``      interest/dividend income (skipped if present)
      - ``total_cash_income``      ITEP-style TCI for quintile binning (always recomputed)
      - ``num_qualifying_children`` EITC-rule count of real dependents, cap 3 (skipped if present)
      - ``dependents``             list of credit-ready dicts (overwrites str list)
      - ``dependents_details``     real per-dependent dicts (or synthetic fallback)
    """
    df = df.copy()

    # Preserve original person-ID list before overwriting
    if "dependents" in df.columns:
        df["dependent_person_ids"] = df["dependents"]

    from tax_modeler.credits.eitc import _count_qualifying_children_eitc

    def _synthetic_dep_dicts(n: int):
        # Fallback ONLY when real per-dependent records are absent (e.g.
        # caller-supplied tax-unit frames, synthesized top-filer rows). Real
        # details come from TaxUnitConstructor via ``dependents_details``.
        return [
            {"age": 10, "relationship": 25, "citizenship": 1, "months_in_home": 12}  # RELSHIPP 25 = own child
        ] * max(0, int(n))

    def _coerce_dep_details(row):
        real = row.get("dependents_details")
        n = int(row.get("num_dependents", 0) or 0)
        is_real_list = (
            isinstance(real, (list, tuple))
            and len(real) == n
            and all(isinstance(x, dict) for x in real)
        )
        return list(real) if is_real_list else _synthetic_dep_dicts(n)

    df["dependents_details"] = df.apply(_coerce_dep_details, axis=1)
    df["dependents"] = df["dependents_details"]

    # Qualifying-children count from real per-dependent EITC rules
    # (age / relationship / citizenship), capped at 3. This replaces a blind
    # count of num_dependents, so attached adult dependents (e.g. an elderly
    # parent) are correctly excluded. Falls back to synthetic placeholders when
    # real details are absent.
    if "num_qualifying_children" not in df.columns:
        df["num_qualifying_children"] = df["dependents_details"].apply(
            lambda deps: min(_count_qualifying_children_eitc(deps), 3)
        ).astype(int)

    def _col(name: str) -> pd.Series:
        return df[name].fillna(0) if name in df.columns else pd.Series(0.0, index=df.index)

    if "earned_income" not in df.columns:
        earned = (
            _col("primary_wagp") + _col("primary_semp")
            + _col("secondary_wagp") + _col("secondary_semp")
        )
        df["earned_income"] = earned.clip(lower=0)

    if "investment_income" not in df.columns:
        invest = _col("primary_intp") + _col("secondary_intp")
        df["investment_income"] = invest.clip(lower=0)

    # Always recompute TCI — cheap, derived, and must be consistent after
    # synthesize_top_filers which concatenates rows that have NaN TCI.
    # ITEP-style Total Cash Income: AGI sources + non-taxable transfers +
    # full Social Security + imputed employer-side FICA.
    # Used for quintile binning only — does NOT change the tax calculation.
    _SS_WAGE_BASE = 168_600   # 2024 Social Security wage base
    employer_fica = (
        _col("primary_wagp").clip(upper=_SS_WAGE_BASE) * 0.0765
        + _col("secondary_wagp").clip(upper=_SS_WAGE_BASE) * 0.0765
    )
    # Use ssp_full if present (new pkl); otherwise un-discount the 85% column.
    if "primary_ssp_full" in df.columns:
        ssp_full = _col("primary_ssp_full") + _col("secondary_ssp_full")
    else:
        ssp_full = _col("primary_ssp") / 0.85 + _col("secondary_ssp") / 0.85
    ssip = _col("primary_ssip") + _col("secondary_ssip")
    pap  = _col("primary_pap")  + _col("secondary_pap")
    retp = _col("primary_retp") + _col("secondary_retp")
    oip  = _col("primary_oip")  + _col("secondary_oip")
    div  = _col("primary_div")  + _col("secondary_div")
    intp = _col("primary_intp") + _col("secondary_intp")
    tci = (
        df["earned_income"]
        + intp
        + div
        + retp
        + ssp_full               # full SSP, not 85%
        + oip
        + ssip                   # SSI benefits (zero for old pkl without primary_ssip)
        + pap                    # public assistance / TANF (zero for old pkl)
        + employer_fica          # imputed employer payroll tax
    )
    df["total_cash_income"] = tci.clip(lower=0)

    # Federal AGI / earned income (W-2 wages, §86 taxable Social Security)
    # for federal credits and federal tax. Hawaii tax keeps using ``income``.
    from tax_modeler.liability.federal import add_federal_income_columns
    df = add_federal_income_columns(df)

    return df


def enrich_for_benefits(
    df: pd.DataFrame,
    person_df: Optional[pd.DataFrame] = None,
    hh_df: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """Pull richer PUMS variables (DIS, HINS*, WKHP, CIT, TEN) onto tax units.

    The base ``enrich_for_credits`` populates the columns needed for
    EITC / CTC / Hawaii tax math. The benefit modules
    (SSI, ACA PTC, childcare, SPM thresholds) need additional PUMS
    variables that aren't aggregated by ``TaxUnitConstructor``. This
    helper does the per-tax-unit aggregation:

      * ``n_disabled``                count of disabled people in the tax unit
      * ``n_employer_insured``        any HINS1==1
      * ``n_medicaid_covered``        any HINS3==1
      * ``n_medicare_covered``        any HINS4==1
      * ``n_uninsured``               HICOV==2 count
      * ``primary_hours_worked``      WKHP for primary filer
      * ``secondary_hours_worked``    WKHP for spouse on MFJ returns
      * ``n_non_citizens``            CIT==5 count
      * ``tenure``                    "renter" / "owner_with_mortgage" /
                                      "owner_no_mortgage" derived from TEN
      * ``hh_received_snap``          HH-level FS column (when available)

    Idempotent — existing columns are skipped. When ``person_df`` /
    ``hh_df`` are not supplied the function is a no-op (returns ``df``
    unchanged), so callers can opt in by passing the frames they have.
    """
    out = df.copy()

    if hh_df is not None and "tenure" not in out.columns and "TEN" in hh_df.columns:
        # PUMS TEN: 1=owner-mortgage, 2=owner-free, 3=renter, 4=no-rent
        ten_map = {1: "owner_with_mortgage", 2: "owner_no_mortgage", 3: "renter", 4: "renter"}
        hh_tenure = hh_df.set_index(hh_df["SERIALNO"].astype(str))["TEN"].map(ten_map)
        out["tenure"] = out["SERIALNO"].astype(str).map(hh_tenure).fillna("renter")
        if "FS" in hh_df.columns:
            hh_fs = hh_df.set_index(hh_df["SERIALNO"].astype(str))["FS"]
            out["hh_received_snap"] = out["SERIALNO"].astype(str).map(hh_fs == 1).fillna(False)

    if person_df is None:
        return out

    # Build a per-SERIALNO aggregator
    p = person_df.copy()
    if "SERIALNO" not in p.columns:
        return out
    p["SERIALNO"] = p["SERIALNO"].astype(str)

    def _agg_by_serial(col: str, predicate) -> pd.Series:
        if col not in p.columns:
            return pd.Series(dtype=float)
        flag = predicate(p[col])
        return flag.groupby(p["SERIALNO"]).sum()

    serial = out["SERIALNO"].astype(str)

    # Counts
    if "n_disabled" not in out.columns:
        out["n_disabled"] = serial.map(_agg_by_serial("DIS", lambda s: s == 1)).fillna(0).astype(int)
    if "n_employer_insured" not in out.columns:
        out["n_employer_insured"] = serial.map(_agg_by_serial("HINS1", lambda s: s == 1)).fillna(0).astype(int)
    if "n_medicaid_covered" not in out.columns:
        out["n_medicaid_covered"] = serial.map(_agg_by_serial("HINS3", lambda s: s == 1)).fillna(0).astype(int)
    if "n_medicare_covered" not in out.columns:
        out["n_medicare_covered"] = serial.map(_agg_by_serial("HINS4", lambda s: s == 1)).fillna(0).astype(int)
    if "n_uninsured" not in out.columns:
        out["n_uninsured"] = serial.map(_agg_by_serial("HICOV", lambda s: s == 2)).fillna(0).astype(int)
    if "n_non_citizens" not in out.columns:
        out["n_non_citizens"] = serial.map(_agg_by_serial("CIT", lambda s: s == 5)).fillna(0).astype(int)

    # Per-filer hours-worked: requires linking via primary_filer_id / secondary_filer_id
    if "WKHP" in p.columns and "primary_filer_id" in out.columns:
        # Build person_id from SERIALNO_SPORDER
        if "person_id" not in p.columns:
            p["person_id"] = p["SERIALNO"].astype(str) + "_" + p["SPORDER"].astype(str)
        wkhp_by_pid = p.set_index("person_id")["WKHP"].fillna(0).astype(float)
        if "primary_hours_worked" not in out.columns:
            out["primary_hours_worked"] = (
                out["primary_filer_id"].astype(str).map(wkhp_by_pid).fillna(0.0)
            )
        if "secondary_hours_worked" not in out.columns:
            out["secondary_hours_worked"] = (
                out.get("secondary_filer_id", pd.Series("0", index=out.index))
                   .astype(str).map(wkhp_by_pid).fillna(0.0)
            )

    return out


def compute_base_tax(
    df: pd.DataFrame,
    deduction_params=None,
    tax_year: int = 2023,
) -> pd.DataFrame:
    """Stage 4: compute baseline Hawaii tax + federal credits.

    Parameters
    ----------
    deduction_params:
        REQUIRED for tax_year ≥ 2024. Pass either scaled params from
        ``scale_deduction_params_for_target_year(year)`` for itemizing-aware
        computation, or ``NO_ITEMIZING`` (from ``tax_modeler.liability.hawaii``)
        to deliberately compute SD-only tax. ``None`` raises ValueError for
        TY ≥ 2024 — silent fallback is what caused several past bugs where
        forecast scripts forgot the parameter and got SD-only tax mixed with
        itemized projections downstream. For tax_year < 2024 (pre-Act-46),
        ``None`` is accepted.
    tax_year:
        Year used to select bracket vintage and standard-deduction values.
        Default 2023 reflects the DOTAX Table A8 calibration anchor.
    """
    from tax_modeler.liability.hawaii import calculate_hawaii_tax_for_units
    from tax_modeler.credits.eitc import calculate_eitc_for_tax_units
    from tax_modeler.projection.tax_unit_projector import _recalculate_ctc

    df = calculate_hawaii_tax_for_units(df, tax_year=tax_year, deduction_params=deduction_params)
    # Year-aware federal credits, on the same rule as project_tax_units_forward:
    # published years use their Rev. Proc. parameters, later years are
    # CPI-extrapolated, earlier years use the earliest published set. (This
    # used to fall back to TY 2023 for every unpublished year, so TY 2027+
    # calls credited 2027+ incomes against TY 2023 parameters.)
    from tax_modeler.credits.eitc import _EITC_PARAMS_BY_YEAR
    supported = sorted(_EITC_PARAMS_BY_YEAR)
    extrapolate = tax_year > supported[-1]
    credit_year = tax_year if (tax_year in _EITC_PARAMS_BY_YEAR or extrapolate) else supported[0]
    df = _recalculate_ctc(df, tax_year=credit_year, extrapolate=extrapolate)
    df = calculate_eitc_for_tax_units(df, tax_year=credit_year, extrapolate=extrapolate)
    # Calibration expects 'agi' and 'hi_state_tax'; Hawaii tax produces 'hi_agi' / 'hi_tax_liability'
    if "hi_agi" in df.columns and "agi" not in df.columns:
        df["agi"] = df["hi_agi"]
    if "hi_tax_liability" in df.columns and "hi_state_tax" not in df.columns:
        df["hi_state_tax"] = df["hi_tax_liability"]
    return df


def apply_credit_takeup(
    df: pd.DataFrame,
    *,
    year: int = 2022,
    programs: tuple[str, ...] = ("eitc", "actc"),
    stratify_eitc_by_children: bool = True,
    scale_eitc_dollars: bool = True,
) -> pd.DataFrame:
    """Apply IRS-anchored take-up imputation to federal EITC/CTC dollars.

    Eligibility ≠ claim. PUMS-derived eligible amounts overstate actual
    IRS take-up; this step ranks eligible filers by their eligible
    credit dollars (descending) and marks the top-N as claimants until
    the weighted count matches the IRS Hawaii state-total target.
    Non-claimants have their credit zeroed.

    Parameters
    ----------
    df:
        Projected (or calibrated) tax units. Must contain ``weight``,
        ``eitc_amount`` (for ``eitc``), and ``ctc_total`` /
        ``ctc_refundable`` (for ``ctc`` / ``actc``).
    year:
        Caseload-table year for the IRS benchmark (default 2022 — the
        latest IRS SOI Hawaii state total).  The take-up *rate* is
        treated as behavioral and constant across forward projection;
        only the target count is fixed at this vintage.
    programs:
        Programs to apply. Default is the federal refundable EITC and
        the refundable portion of CTC (ACTC), since these are the
        primary anti-poverty channels. Pass ``("eitc", "ctc", "actc")``
        for the full set when CBPP comparison covers total CTC.
    stratify_eitc_by_children:
        When True (default), EITC take-up is stratified across 0/1/2/3+
        qualifying-child buckets per the IRS SOI by-children distribution
        instead of a single pooled rank-truncate. The microsimulation
        over-produces childless EITC eligibles relative to IRS Hawaii; a
        pooled target lets them absorb the whole take-up slack, inflating
        the childless-claimer share. Bucketing caps the childless bucket at
        its IRS share. ACTC and any other programs are unaffected.
    scale_eitc_dollars:
        When True (default), after the count take-up the surviving EITC
        dollars are rescaled to the caseload table's *nominal dollar*
        target for ``year`` (and the same scalar is applied to ``hi_eitc``).
        Set False when ``year`` is a fallback vintage that does NOT match
        the projection year — otherwise the rescale refreezes income-aged
        EITC back to the table's (e.g. 2022) nominal dollars, nullifying the
        aging (audit F2). The count anchor still applies; only the dollar
        re-peg is skipped, so per-claimant amounts keep their aged values.

    Notes
    -----
    By default ``run_pipeline`` skips this step (opt-in) so existing
    forecast scripts that benchmark against eligibility totals are not
    silently changed. Pass ``apply_credit_takeup=True`` to ``run_pipeline``
    to enable.
    """
    from tax_modeler.calibration.admin_caseload import AdminCaseload
    from tax_modeler.calibration.takeup_imputation import calibrate_benefits

    caseload = AdminCaseload.load()
    return calibrate_benefits(
        df,
        caseload=caseload,
        year=year,
        programs=programs,
        stratify_eitc_by_children=stratify_eitc_by_children,
        scale_eitc_dollars=scale_eitc_dollars,
    )


def calibrate(df: pd.DataFrame, *, return_report: bool = False):
    """Stage 5: IPF rake calibration against DOTAX aggregate benchmarks.

    With ``return_report=True`` returns ``(calibrated, CalibrationReport)``;
    the report (convergence, per-margin residuals, clip events, weight
    dispersion — ``tax_modeler.calibration.report``) is attached to
    ``calibrated.attrs["calibration_report"]`` as a dict either way. A rake
    that did not converge is recorded there, not only in the log.
    """
    from tax_modeler.calibration import apply_ipf_calibration_via_rake

    calibrated, report = apply_ipf_calibration_via_rake(df, return_report=True)
    logger.info(
        "IPF calibration complete: weight sum %.0f → %.0f (%s, %d iterations, ESS %.0f)",
        df["weight"].sum(),
        calibrated["weight"].sum(),
        "converged" if report.converged else "NOT converged",
        report.iterations,
        report.weights_after.effective_n if report.weights_after else float("nan"),
    )
    if return_report:
        return calibrated, report
    return calibrated


# ---------------------------------------------------------------------------
# Deprecated underscore-prefixed aliases.
#
# The underscore names were the de-facto public API (imported from 5+ external
# scripts).  They are kept as thin shims that emit DeprecationWarning so that
# downstream scripts keep working while migration happens.  Remove in a future
# major release once no callers remain.
# ---------------------------------------------------------------------------


def _enrich_for_credits(df: pd.DataFrame) -> pd.DataFrame:
    warnings.warn(
        "tax_modeler.pipeline._enrich_for_credits is deprecated; "
        "use enrich_for_credits (public name) instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return enrich_for_credits(df)


def _compute_base_tax(
    df: pd.DataFrame,
    deduction_params=None,
    tax_year: int = 2023,
) -> pd.DataFrame:
    warnings.warn(
        "tax_modeler.pipeline._compute_base_tax is deprecated; "
        "use compute_base_tax (public name) instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return compute_base_tax(df, deduction_params=deduction_params, tax_year=tax_year)


def _calibrate(df: pd.DataFrame) -> pd.DataFrame:
    warnings.warn(
        "tax_modeler.pipeline._calibrate is deprecated; "
        "use calibrate (public name) instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return calibrate(df)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_pipeline(
    target_year: int,
    base_year: Optional[int] = None,
    pums_data_dir: Optional[Path] = None,
    state: str = "15",
    pums_type: str = "5yr",
    method: str = "ensemble",
    tax_units_df: Optional[pd.DataFrame] = None,
    skip_calibration: bool = False,
    reform: Optional[object] = None,    # tax_modeler.reform.Reform | None
    apply_credit_takeup: bool = False,
    credit_takeup_year: int = 2022,
) -> PipelineResult:
    """Run the full Hawaii tax microsimulation pipeline.

    Parameters
    ----------
    target_year:
        Year to project forward to (e.g. 2026).
    base_year:
        ACS anchor year for income growth factors.  Defaults to
        ``None`` — census_forecaster picks the most recent panel vintage.
    pums_data_dir:
        Directory containing ``psam_h15.parquet`` and ``psam_p15.parquet``
        (or ``.csv`` equivalents).  Ignored when ``tax_units_df`` is supplied.
        Defaults to the standard monorepo path
        (``<repo_root>/data/raw/pums``).
    state:
        State FIPS code (default ``"15"`` for Hawaii).
    pums_type:
        ``"5yr"`` (default) or ``"1yr"`` — selects the PUMS vintage.
    method:
        census_forecaster projector method: ``"ensemble"`` (default),
        ``"damped_log_trend"``, ``"ar1_log_diff"``, or ``"carry_forward"``.
    tax_units_df:
        Pre-built calibrated base-year tax units.  When provided, stages
        1–5 (PUMS loading, construction, enrichment, tax, calibration) are
        skipped and projection begins immediately from this DataFrame.
        The caller is responsible for ensuring ``hi_tax_liability``,
        ``earned_income``, ``investment_income``, ``dependents``, and
        ``num_qualifying_children`` are present.
    skip_calibration:
        When ``True``, skip the IPF rake step even when building from PUMS.
        Useful for debugging the construction and projection stages in isolation.
    reform:
        Optional :class:`tax_modeler.reform.Reform`. When supplied, the
        comparison between baseline and reform is computed on the projected
        units and attached to the result as ``reform_result``. The baseline
        DataFrames returned in the result are unchanged.
    apply_credit_takeup:
        When ``True``, after projection (and before revenue estimation),
        zero out federal EITC and ACTC dollars for non-claimants per
        rank-based imputation calibrated to the IRS Hawaii state total
        for ``credit_takeup_year``. Default is ``False`` (opt-in) so
        callers receive raw eligibility amounts.
    credit_takeup_year:
        IRS caseload-table year used as the take-up benchmark
        (default 2022 — the latest IRS SOI Hawaii state total).

    Returns
    -------
    PipelineResult
        Contains calibrated base units, projected units, a RevenueEstimator
        instance, and pre-computed summary tables. ``reform_result`` is
        populated when a ``reform`` is supplied.
    """
    from tax_modeler.projection.tax_unit_projector import project_tax_units_forward
    from tax_modeler.revenue.estimator import RevenueEstimator
    # Rebind the module-level function under a different name so it does
    # not collide with the bool parameter ``apply_credit_takeup`` below.
    _apply_credit_takeup = globals()["apply_credit_takeup"]

    wall_start = time.perf_counter()
    timings: Dict[str, float] = {}

    # ------------------------------------------------------------------ #
    # Stages 1–5: build calibrated base-year units (skipped if supplied)  #
    # ------------------------------------------------------------------ #
    if tax_units_df is not None:
        validate_units_schema(tax_units_df)
        logger.info("run_pipeline: using caller-supplied tax_units_df (%d rows)", len(tax_units_df))
        calibrated = tax_units_df
    else:
        _data_dir = pums_data_dir or (
            Path(__file__).resolve().parents[5] / "data" / "raw" / "pums"
        )

        t0 = time.perf_counter()
        person_df, hh_df = _load_pums(_data_dir, state, pums_type)
        timings["load_pums"] = time.perf_counter() - t0

        t0 = time.perf_counter()
        units = _construct_units(person_df, hh_df)
        timings["construct"] = time.perf_counter() - t0

        t0 = time.perf_counter()
        units = enrich_for_credits(units)
        timings["enrich"] = time.perf_counter() - t0

        t0 = time.perf_counter()
        units = compute_base_tax(units)
        timings["base_tax"] = time.perf_counter() - t0

        t0 = time.perf_counter()
        calibrated = calibrate(units) if not skip_calibration else units
        timings["calibrate"] = time.perf_counter() - t0

    # ------------------------------------------------------------------ #
    # Stage 6: project forward                                             #
    # ------------------------------------------------------------------ #
    t0 = time.perf_counter()
    projected = project_tax_units_forward(
        calibrated,
        target_year=target_year,
        base_year=base_year,
        method=method,
    )
    timings["project"] = time.perf_counter() - t0
    logger.info(
        "Projection complete: %d units, median income %.0f → %.0f",
        len(projected),
        projected["income_base_year"].median(),
        projected["income"].median(),
    )

    # ------------------------------------------------------------------ #
    # Stage 6b (optional): IRS take-up imputation for EITC/CTC           #
    # ------------------------------------------------------------------ #
    if apply_credit_takeup:
        t0 = time.perf_counter()
        projected = _apply_credit_takeup(
            projected, year=credit_takeup_year, programs=("eitc", "actc")
        )
        timings["credit_takeup"] = time.perf_counter() - t0
        logger.info(
            "Credit take-up imputation applied (year=%d): EITC + ACTC restricted "
            "to IRS-anchored claimants.",
            credit_takeup_year,
        )

    # ------------------------------------------------------------------ #
    # Stage 7: revenue estimation                                          #
    # ------------------------------------------------------------------ #
    t0 = time.perf_counter()
    estimator = RevenueEstimator(projected)
    state_summary = estimator.state_summary()
    by_county = estimator.by_county()
    by_filing = estimator.by_filing_status()
    by_quintile = estimator.by_income_quintile()
    timings["estimate"] = time.perf_counter() - t0

    # ------------------------------------------------------------------ #
    # Stage 8 (optional): apply a reform and compute the comparison       #
    # ------------------------------------------------------------------ #
    reform_result = None
    if reform is not None:
        from tax_modeler.reform import apply_reform
        t0 = time.perf_counter()
        reform_result = apply_reform(projected, reform, year=target_year)
        timings["reform"] = time.perf_counter() - t0
        logger.info(
            "Reform %s applied: revenue delta = %+.1fM, avg tax delta = %+.0f",
            reform.name,
            reform_result.revenue_delta_millions,
            reform_result.avg_tax_delta,
        )

    elapsed = time.perf_counter() - wall_start

    logger.info(
        "Pipeline complete in %.1fs: %d units, %.0f weighted filers, "
        "hi_net_tax_revenue=%.0f",
        elapsed,
        len(projected),
        state_summary["total_weighted_filers"],
        state_summary["hi_net_tax_revenue"],
    )

    return PipelineResult(
        tax_units=calibrated,
        projected_units=projected,
        estimator=estimator,
        state_summary=state_summary,
        by_county=by_county,
        by_filing_status=by_filing,
        by_income_quintile=by_quintile,
        base_year=base_year or projected["projection_base_year"].iloc[0],
        target_year=target_year,
        n_units=len(projected),
        n_weighted_filers=state_summary["total_weighted_filers"],
        elapsed_seconds=elapsed,
        timings=timings,
        reform_result=reform_result,
    )
