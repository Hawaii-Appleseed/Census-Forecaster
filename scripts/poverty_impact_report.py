#!/usr/bin/env python3
"""Hawaii anti-poverty impact of EITC, CTC, and HI state EITC by geography.

Emits four CSVs per run at state, county, House district, and Senate
district granularity, comparing baseline SPM poverty rates against
seven counterfactuals (four removal, three expansion/new-credit).

    by_state.csv             — single-row state totals
    by_county.csv            — 4 Hawaii counties
    by_house_district.csv    — up to 51 HD rows
    by_senate_district.csv   — up to 25 SD rows

Scenarios:
  Removal — what would poverty look like without each credit?
    no_eitc, no_ctc, no_hi_eitc, no_credits
  Expansion — how much additional poverty reduction from policy changes?
    expanded_ctc_2021 (ARPA-style fully-refundable CTC),
    hi_eitc_100pct    (HI state EITC raised from 40% → 100% of federal),
    hi_ctc_300        (new HI state CTC at $300/qualifying-child, refundable),
    hi_ctc_650        (the SB 3125 CD1 amount, $650/qualifying-child),
    hi_ctc_1000       (high-end HI state CTC at $1000/qualifying-child)

Examples
--------
    # TY 2024 baseline + all seven scenarios on real PUMS, with SNAP
    python scripts/poverty_impact_report.py --tax-year 2024 --apply-snap \\
        --pums-data-dir packages/data/raw/pums \\
        --out reports/poverty_impact_2024/

    # TY 2025 projection
    python scripts/poverty_impact_report.py --tax-year 2025 --apply-snap \\
        --pums-data-dir packages/data/raw/pums \\
        --out reports/poverty_impact_2025/

    # The default scenario set already runs hi_ctc_300, hi_ctc_650, and
    # hi_ctc_1000 side-by-side. To run only a subset:
    python scripts/poverty_impact_report.py --tax-year 2024 --apply-snap \\
        --scenarios hi_ctc_1000 \\
        --out reports/poverty_impact_2024_hi_ctc_1000/

Notes
-----
* SNAP imputation is recommended for poverty-impact runs (without it,
  low-income units' SPM resources are understated by ~$500M/yr in
  Hawaii, biasing baseline poverty rates upward).
* See the module-level docstring of ``tax_modeler.poverty.impact`` for
  the full list of methodological caveats (static counterfactual,
  marginal-attribution non-additivity, unit-of-analysis bias).
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
import textwrap
from pathlib import Path
from typing import Optional

import pandas as pd

LOG = logging.getLogger("poverty_impact_report")

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE_DIR = REPO_ROOT / "tests" / "tax_modeler" / "fixtures"


# ---------------------------------------------------------------------------
# Pipeline glue (mirrors scripts/eitc_ctc_geo_report.py; intentionally
# duplicated — scripts/ has no shared helpers module)
# ---------------------------------------------------------------------------


def _replicate_weight_cols(prefix: str = "WGTP", n: int = 80) -> list[str]:
    """ACS replicate-weight column names, ``WGTP1`` .. ``WGTP80`` by default."""
    return [f"{prefix}{i}" for i in range(1, n + 1)]


def _load_units(
    pums_data_dir: Optional[Path],
    use_fixture: bool,
    *,
    with_replicate_weights: bool = False,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build enriched, taxed tax units with HI EITC + geography assigned.

    Returns ``(units, persons)``. The ``persons`` frame is returned so
    downstream SPM-unit assembly can read RELSHIPP, AGEP, and WGTP
    directly from PUMS.

    When ``with_replicate_weights`` is set, the 80 ACS household replicate
    weights (``WGTP1`` .. ``WGTP80``) are also broadcast from households
    onto persons so SDR sampling-variance can be computed downstream.
    """
    from tax_modeler.pipeline import enrich_for_credits
    from tax_modeler.units.constructor import TaxUnitConstructor
    from tax_modeler.analysis.puma_crosswalk import assign_geography

    if use_fixture or pums_data_dir is None:
        persons_path = DEFAULT_FIXTURE_DIR / "synthetic_pums_persons.parquet"
        households_path = DEFAULT_FIXTURE_DIR / "synthetic_pums_households.parquet"
        if not persons_path.exists() or not households_path.exists():
            raise FileNotFoundError(
                f"Synthetic PUMS fixture missing at {DEFAULT_FIXTURE_DIR}."
            )
        persons = pd.read_parquet(persons_path)
        households = pd.read_parquet(households_path)
        LOG.info("Loaded synthetic PUMS fixture: %d persons, %d households",
                 len(persons), len(households))
    else:
        for ext in ("parquet", "csv"):
            p = pums_data_dir / f"psam_p15.{ext}"
            h = pums_data_dir / f"psam_h15.{ext}"
            if p.exists() and h.exists():
                reader = pd.read_parquet if ext == "parquet" else pd.read_csv
                persons = reader(p)
                households = reader(h)
                LOG.info("Loaded PUMS from %s (%s): %d persons, %d hhs",
                         pums_data_dir, ext, len(persons), len(households))
                break
        else:
            raise FileNotFoundError(
                f"No psam_p15.{{parquet,csv}} found in {pums_data_dir}"
            )
        from tax_modeler.loaders.pums_loader import PUMSDataLoader
        _coalesce = PUMSDataLoader(data_dir=pums_data_dir)._coalesce_puma
        households = _coalesce(households)
        persons = _coalesce(persons)

    # can pick up the household weight without re-joining downstream. The
    # 80 replicate weights (WGTP1..WGTP80) are broadcast alongside only when
    # ``with_replicate_weights`` is set, so SDR variance estimation downstream
    # sees them on persons.
    broadcast_cols = []
    if "WGTP" not in persons.columns and "WGTP" in households.columns:
        broadcast_cols.append("WGTP")
    if with_replicate_weights:
        rep_cols = [
            c for c in _replicate_weight_cols()
            if c in households.columns and c not in persons.columns
        ]
        if not rep_cols:
            raise FileNotFoundError(
                "--replicate-se requested but no WGTP1..80 replicate-weight "
                "columns are present on the household frame. Re-export PUMS "
                "with replicate weights (psam_h15 must carry WGTP1..WGTP80)."
            )
        broadcast_cols.extend(rep_cols)
    if broadcast_cols:
        persons = persons.merge(
            households[["SERIALNO", *broadcast_cols]].drop_duplicates(subset=["SERIALNO"]),
            how="left", on="SERIALNO",
        )
        LOG.info(
            "Broadcast %d weight column(s) from households onto persons "
            "(replicate weights present: %s)",
            len(broadcast_cols),
            any(c.startswith("WGTP") and c != "WGTP" for c in broadcast_cols),
        )

    ctor = TaxUnitConstructor(
        persons.copy(), households.copy(),
        use_soi_calibration=False, progress_bar=False,
    )
    units = ctor.create_rule_based_units(parallel=False)
    units = enrich_for_credits(units)
    units = assign_geography(units)
    return units, persons


def _build_units_for_tax_year(
    units: pd.DataFrame,
    tax_year: int,
    project: bool,
    *,
    eitc_poverty_alpha: float = 0.5,
    use_cbo_aging: bool = False,
) -> pd.DataFrame:
    """Compute base tax (federal CTC/EITC) for a given year.

    HI EITC is intentionally NOT computed here — it is computed after the
    IRS-anchored credit take-up imputation, so that filers who do not take
    up the federal EITC also have zero HI EITC (HI EITC is a fixed
    percentage of the federal credit).

    ``eitc_poverty_alpha`` is the elasticity exponent passed through to
    :func:`scale_eitc_for_poverty` inside :func:`project_tax_units_forward`.
    Only meaningful when ``project=True``.

    When ``project`` is True, income is aged to ``tax_year``. With
    ``use_cbo_aging=True`` the aging uses CBO per-component growth (the same
    engine the revenue path uses), so a reform's revenue score and its poverty
    score are computed on one coherent aged income distribution; otherwise the
    legacy county-scalar B19013 growth is used. Geography is preserved either
    way. ``use_cbo_aging`` is a no-op when ``project`` is False (TY2022 base,
    no aging).
    """
    from tax_modeler.pipeline import compute_base_tax

    if project:
        from tax_modeler.projection.tax_unit_projector import project_tax_units_forward
        out = project_tax_units_forward(
            units, target_year=tax_year,
            eitc_poverty_alpha=eitc_poverty_alpha,
            use_cbo_aging=use_cbo_aging,
        )
    else:
        out = compute_base_tax(units, tax_year=tax_year)
    return out


def _apply_hi_eitc(units: pd.DataFrame, *, tax_year: int) -> pd.DataFrame:
    """Compute HI state EITC after federal-credit take-up has been applied.

    HI EITC is a fixed percentage of the federal EITC. Computing it before
    take-up would attach the full state credit to filers who never claimed
    the federal credit — overstating HI EITC outlays and the
    persons-lifted-by-HI-EITC scenario count. Calling this after
    _apply_credit_takeup ensures non-claimants receive $0 HI EITC.
    """
    from tax_modeler.credits.hi_eitc import compute_hi_eitc_for_units
    return compute_hi_eitc_for_units(units, tax_year=tax_year)


def _apply_hi_renters(units: pd.DataFrame) -> pd.DataFrame:
    """Compute the HI Low-Income Household Renters Credit (HRS §235-55.7).

    Tiered refundable credit ($50 / $100 / $150) for renting filers with AGI
    below status-specific ceilings ($30K single / $40K MFJ / $35K HoH /
    $20K MFS). The credit's take-up rate is currently a 0.30 placeholder
    (no DOTAX caseload bundled — TODO in credits/hi_renters.py); the
    formula smooths to a deterministic 0.30 × full-credit per eligible
    unit. Aggregate disbursement matches an explicit Bernoulli model.

    Once this column rides on the tax-unit frame, ``compute_spm_resources``
    picks it up automatically via the ``hi_renters_col="hi_renters_amount"``
    kwarg in poverty/spm.py.
    """
    from tax_modeler.credits.hi_renters import compute_hi_renters_for_units
    LOG.info("Computing HI Low-Income Household Renters Credit")
    return compute_hi_renters_for_units(units)


def _apply_snap(units: pd.DataFrame, *, tax_year: int) -> pd.DataFrame:
    """Compute SNAP benefits then take-up-adjust against admin caseload anchor."""
    from tax_modeler.benefits.snap import compute_snap_for_units
    from tax_modeler.calibration.admin_caseload import AdminCaseload
    from tax_modeler.calibration.takeup_imputation import impute_takeup

    LOG.info("Computing SNAP benefits + take-up imputation for TY %d", tax_year)
    out = compute_snap_for_units(units)
    try:
        target = AdminCaseload.load().target("snap", tax_year)
    except Exception as exc:
        # Fall back to the most-recent available SNAP anchor (typically
        # FY2024, post-COVID emergency-allotments). Skipping take-up
        # entirely leaves SNAP at simulated eligibility, biasing the
        # baseline rate downward — the fallback is preferable.
        LOG.warning(
            "SNAP admin caseload target unavailable for year=%d (%s); "
            "falling back to TY2024 USDA-FNS anchor for the take-up step.",
            tax_year, exc,
        )
        target = AdminCaseload.load().target("snap", 2024)
    out = impute_takeup(
        out, target=target, benefit_col="snap_amount", score_col="income",
        ascending=True, weight_col="weight",
    )
    return out


def _apply_housing_subsidy(units: pd.DataFrame, *, tax_year: int) -> pd.DataFrame:
    """Impute Section-8/public-housing benefit, then take-up-impute against HUD anchor."""
    from tax_modeler.benefits.housing import compute_housing_for_units
    from tax_modeler.calibration.admin_caseload import AdminCaseload
    from tax_modeler.calibration.takeup_imputation import impute_takeup

    LOG.info("Computing housing-subsidy benefits + take-up imputation for TY %d", tax_year)
    out = compute_housing_for_units(units, tax_year=tax_year)
    try:
        target = AdminCaseload.load().target("housing", tax_year)
    except Exception as exc:
        LOG.warning(
            "Housing admin caseload target unavailable for year=%d (%s); "
            "falling back to TY2022 HUD anchor for the take-up step.",
            tax_year, exc,
        )
        target = AdminCaseload.load().target("housing", 2022)
    out = impute_takeup(
        out, target=target, benefit_col="housing_subsidy_amount",
        score_col="income", ascending=True, weight_col="weight",
    )
    return out


def _apply_childcare_subsidy(units: pd.DataFrame, *, tax_year: int) -> pd.DataFrame:
    """Impute CCSP benefit, then take-up-impute against HI DHS anchor."""
    from tax_modeler.benefits.childcare import compute_childcare_for_units
    from tax_modeler.calibration.admin_caseload import AdminCaseload
    from tax_modeler.calibration.takeup_imputation import impute_takeup

    LOG.info("Computing CCSP childcare-subsidy benefits + take-up imputation for TY %d", tax_year)
    out = compute_childcare_for_units(units, tax_year=tax_year)
    try:
        target = AdminCaseload.load().target("childcare_subsidy", tax_year)
    except Exception as exc:
        LOG.warning(
            "Childcare-subsidy admin caseload target unavailable for year=%d (%s); "
            "falling back to TY2022 HI DHS anchor for the take-up step.",
            tax_year, exc,
        )
        target = AdminCaseload.load().target("childcare_subsidy", 2022)
    out = impute_takeup(
        out, target=target, benefit_col="childcare_amount",
        score_col="income", ascending=True, weight_col="weight",
    )
    return out


def _apply_wic(units: pd.DataFrame, *, tax_year: int) -> pd.DataFrame:
    """Impute WIC food-package value, then take-up-impute against USDA-FNS anchor."""
    from tax_modeler.benefits.wic import compute_wic_for_units
    from tax_modeler.calibration.admin_caseload import AdminCaseload
    from tax_modeler.calibration.takeup_imputation import impute_takeup

    LOG.info("Computing WIC benefits + take-up imputation for TY %d", tax_year)
    out = compute_wic_for_units(units, tax_year=tax_year)
    try:
        target = AdminCaseload.load().target("wic", tax_year)
    except Exception as exc:
        LOG.warning(
            "WIC admin caseload target unavailable for year=%d (%s); falling "
            "back to TY2022 USDA-FNS anchor for the take-up step.", tax_year, exc,
        )
        target = AdminCaseload.load().target("wic", 2022)
    out = impute_takeup(
        out, target=target, benefit_col="wic_amount", score_col="income",
        ascending=True, weight_col="weight",
    )
    return out


def _apply_liheap(units: pd.DataFrame, *, tax_year: int) -> pd.DataFrame:
    """Impute LIHEAP cooling assistance, then take-up-impute against HI DHS anchor."""
    from tax_modeler.benefits.liheap import compute_liheap_for_units
    from tax_modeler.calibration.admin_caseload import AdminCaseload
    from tax_modeler.calibration.takeup_imputation import impute_takeup

    LOG.info("Computing LIHEAP benefits + take-up imputation for TY %d", tax_year)
    out = compute_liheap_for_units(units, tax_year=tax_year)
    try:
        target = AdminCaseload.load().target("liheap", tax_year)
    except Exception as exc:
        LOG.warning(
            "LIHEAP admin caseload target unavailable for year=%d (%s); "
            "falling back to TY2022 HI DHS anchor.", tax_year, exc,
        )
        target = AdminCaseload.load().target("liheap", 2022)
    out = impute_takeup(
        out, target=target, benefit_col="liheap_amount", score_col="income",
        ascending=True, weight_col="weight",
    )
    return out


def _apply_arpa_ctc(units: pd.DataFrame) -> pd.DataFrame:
    from tax_modeler.credits.arpa_ctc import arpa_ctc_for_tax_units
    return arpa_ctc_for_tax_units(units)


def _apply_eitc_reweight(units: pd.DataFrame) -> pd.DataFrame:
    """Lever 3a: up-weight EITC-eligible units in short with-children buckets.

    The microsimulation under-produces EITC-eligible 1-/2-qualifying-child tax
    units relative to IRS SOI Hawaii (~0.74 / ~0.77 of the by-children targets);
    a take-up truncation can cap over-produced buckets but cannot fill short
    ones. This reweight lifts the short buckets to their IRS targets *before*
    ``_apply_credit_takeup``, so the stratified take-up then trims each bucket to
    its IRS share and the EITC childless-claimer share lands at IRS Hawaii's
    31.7% (vs ~36% with take-up alone).

    Surgical: only EITC-eligible units in short buckets are up-weighted, no
    compensating down-weights. Measured drift (TY2025 PUMS): ~+1.5% tax-unit
    weight, ~+0.2% HI net revenue — all on low-income filers. The SPM-unit
    weight used for poverty accounting is the household WGTP, re-derived
    independent of the tax-unit ``weight`` this edits.
    """
    from tax_modeler.calibration.eitc_reweight import (
        reweight_eitc_eligibles_by_children,
    )
    out, info = reweight_eitc_eligibles_by_children(units)
    before, after = info["total_weight_before"], info["total_weight_after"]
    factors = {k: round(v["factor"], 3) for k, v in info["buckets"].items()}
    LOG.info(
        "EITC by-children reweight: up-weighted buckets %s; tax-unit weight "
        "%.0f -> %.0f (%+.2f%%)",
        factors or "none (no short bucket)", before, after,
        (100.0 * (after - before) / before) if before else 0.0,
    )
    return out


def _apply_credit_takeup(
    units: pd.DataFrame,
    *,
    tax_year: int,
    programs: tuple[str, ...] = ("eitc", "actc"),
) -> pd.DataFrame:
    """Rank-and-truncate EITC/ACTC eligibles to IRS SOI take-up targets.

    Without this, ``eitc_amount`` and ``ctc_refundable`` reflect *eligibility*
    (~105k EITC-eligible filers in Hawaii), not *receipt* (~84k claimed per
    IRS SOI). Baseline poverty lifts for the no_eitc / no_ctc scenarios are
    overstated by ~25% / ~15% respectively when this step is skipped.
    """
    from tax_modeler.pipeline import apply_credit_takeup
    from tax_modeler.errors import ConfigError, MissingDataError
    LOG.info(
        "Applying IRS-anchored take-up imputation for %s at year=%d",
        ",".join(programs), tax_year,
    )
    try:
        return apply_credit_takeup(units, year=tax_year, programs=programs)
    except (ConfigError, MissingDataError) as exc:
        # No caseload row for this projection year (EITC only has TY2022).
        # Apply the 2022 take-up COUNT anchor as a constant behavioral rate,
        # but DO NOT rescale EITC dollars to the 2022 nominal target — that
        # would refreeze income-aged EITC back to 2022 dollars and nullify the
        # aging (audit F2). Per-claimant amounts keep their projection-year
        # (aged) values; only the claimant *count* is anchored.
        LOG.warning(
            "apply_credit_takeup: no caseload target for year=%d (%s). Falling "
            "back to the TY2022 take-up COUNT anchor with scale_eitc_dollars=False "
            "so income-aged EITC dollars are preserved (NOT re-pegged to 2022 $).",
            tax_year, exc,
        )
        return apply_credit_takeup(
            units, year=2022, programs=programs, scale_eitc_dollars=False
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: Optional[list] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--tax-year", type=int, required=True,
                   help="Tax year (2022, 2023, 2024, or 2025).")
    p.add_argument("--out", type=Path, required=True,
                   help="Output directory.")
    p.add_argument("--pums-data-dir", type=Path, default=None,
                   help="Directory with psam_h15.{parquet,csv} + psam_p15.*. "
                        "Defaults to the synthetic fixture.")
    p.add_argument("--use-fixture", action="store_true",
                   help="Force use of the synthetic PUMS fixture.")
    p.add_argument("--cbo-aging", action=argparse.BooleanOptionalAction, default=True,
                   help="(Default ON.) When projecting to a future tax year, "
                        "age income with CBO per-component growth — the same "
                        "engine the revenue path uses — instead of the legacy "
                        "county-scalar B19013 growth. Puts the poverty/"
                        "distributional results on the same aged income "
                        "distribution as the revenue forecast (coherence). "
                        "Trades county growth differentiation for income-type "
                        "differentiation; geography is preserved. Pass "
                        "--no-cbo-aging for the legacy county-scalar path. "
                        "No-op for --tax-year 2022 (base year, no aging).")
    p.add_argument("--apply-snap", action="store_true", default=True,
                   help="(Default ON.) Compute + take-up-impute SNAP before "
                        "the SPM computation. Use --no-apply-snap to disable.")
    p.add_argument("--no-apply-snap", dest="apply_snap", action="store_false",
                   help=argparse.SUPPRESS)
    p.add_argument("--apply-credit-takeup", action="store_true", default=True,
                   help="(Default ON.) Rank-and-truncate EITC/ACTC eligibles "
                        "to the IRS SOI take-up target so baseline reflects "
                        "*receipt*, not *eligibility*. Use "
                        "--no-apply-credit-takeup to disable.")
    p.add_argument("--no-apply-credit-takeup", dest="apply_credit_takeup",
                   action="store_false", help=argparse.SUPPRESS)
    p.add_argument("--reweight-eitc-by-children", action="store_true", default=True,
                   help="(Default ON.) Lever 3a: up-weight EITC-eligible units "
                        "in short with-children buckets (1-/2-kid) to IRS "
                        "by-children targets before take-up, so the EITC "
                        "childless-claimer share matches IRS Hawaii (31.7%%) "
                        "rather than the ~36%% the take-up truncation alone "
                        "yields. Adds ~1.5%% tax-unit weight on low-income "
                        "filers (~+0.2%% HI net revenue). Use "
                        "--no-reweight-eitc-by-children to disable.")
    p.add_argument("--no-reweight-eitc-by-children", dest="reweight_eitc_by_children",
                   action="store_false", help=argparse.SUPPRESS)
    p.add_argument("--apply-moop", action="store_true", default=True,
                   help="(Default ON.) Impute MOOP (medical out-of-pocket) "
                        "from CPS ASEC Hawaii donors before SPM resource "
                        "calculation. Use --no-apply-moop to disable.")
    p.add_argument("--no-apply-moop", dest="apply_moop", action="store_false",
                   help=argparse.SUPPRESS)
    p.add_argument("--apply-housing-subsidy", action="store_true", default=True,
                   help="(Default ON.) Impute Section-8/public-housing subsidy "
                        "via HUD-anchored take-up. Use --no-apply-housing-subsidy "
                        "to disable.")
    p.add_argument("--no-apply-housing-subsidy", dest="apply_housing_subsidy",
                   action="store_false", help=argparse.SUPPRESS)
    p.add_argument("--apply-childcare-subsidy", action="store_true", default=True,
                   help="(Default ON.) Impute Hawaii CCSP childcare subsidy via "
                        "HI DHS-anchored take-up. Use --no-apply-childcare-subsidy "
                        "to disable.")
    p.add_argument("--no-apply-childcare-subsidy", dest="apply_childcare_subsidy",
                   action="store_false", help=argparse.SUPPRESS)
    p.add_argument("--apply-spm-expenses", action="store_true", default=True,
                   help="(Default ON.) Impute work-related childcare expense "
                        "and other work expenses from CPS ASEC Hawaii donors. "
                        "Use --no-apply-spm-expenses to disable.")
    p.add_argument("--no-apply-spm-expenses", dest="apply_spm_expenses",
                   action="store_false", help=argparse.SUPPRESS)
    p.add_argument("--apply-wic", action="store_true", default=True,
                   help="(Default ON.) Impute WIC food-package value via "
                        "USDA-FNS-anchored take-up. Use --no-apply-wic to disable.")
    p.add_argument("--no-apply-wic", dest="apply_wic", action="store_false",
                   help=argparse.SUPPRESS)
    p.add_argument("--apply-liheap", action="store_true", default=True,
                   help="(Default ON.) Impute LIHEAP cooling-assistance value "
                        "via HI DHS-anchored take-up. Use --no-apply-liheap to disable.")
    p.add_argument("--no-apply-liheap", dest="apply_liheap", action="store_false",
                   help=argparse.SUPPRESS)
    p.add_argument("--apply-school-lunch", action="store_true", default=True,
                   help="(Default ON.) Impute free/reduced-price NSLP school-lunch "
                        "value (USDA Hawaii free-meal reimbursement × 180 school "
                        "days × eligible children). Use --no-apply-school-lunch "
                        "to disable.")
    p.add_argument("--no-apply-school-lunch", dest="apply_school_lunch",
                   action="store_false", help=argparse.SUPPRESS)
    p.add_argument("--apply-rxkids", action="store_true", default=False,
                   help="(Default OFF; this is a hypothetical program.) "
                        "Compute the modeled Hawaii RxKids cash prescription "
                        "amount per filing unit. Requires the 'rxkids_hi' "
                        "scenario to surface poverty-lift estimates.")
    p.add_argument("--by-tax-unit", action="store_true", default=False,
                   help="(Default OFF; SPM-unit aggregation is the default.) "
                        "Opt out of SPM-unit aggregation and report poverty at "
                        "tax-unit granularity. Useful only for backward-compat "
                        "checks. Persons-in-poverty will be overstated by "
                        "~10 percentage points relative to Census published "
                        "Hawaii SPM under this flag.")
    p.add_argument("--rake-to-irs-zip", action="store_true", default=False,
                   help="(Default OFF.) Rake unit weights so per-HD totals "
                        "match IRS SOI ZIP filer counts, holding PUMA "
                        "marginals fixed. Tightens district-level uncertainty.")
    p.add_argument("--carry-calibration-ratio", action=argparse.BooleanOptionalAction,
                   default=False,
                   help="(Default OFF.) Weight each SPM unit by WGTP times the mean "
                        "calibration ratio of its tax units (calibrated weight / "
                        "PUMS weight: the filing-status factors and the EITC "
                        "by-children reweight), and scale the replicate weights "
                        "by the same ratio, so poverty runs on the revenue path's "
                        "weight basis instead of raw WGTP. Opt-in: it moves the "
                        "TY2024 baseline SPM rate by more than 0.5pp (see "
                        "SB3125_CD1_FORECAST.md, October 9, 2026).")
    p.add_argument("--replicate-se", action="store_true", default=False,
                   help="(Default OFF.) Compute ACS successive-difference-"
                        "replication (SDR) sampling standard errors + 90%% "
                        "confidence intervals for the headline poverty "
                        "statistics, using the 80 household replicate weights "
                        "(WGTP1..80). Writes by_state_se.csv + by_county_se.csv "
                        "and prints CIs in the summary. Requires SPM-unit mode "
                        "(incompatible with --by-tax-unit) and a PUMS export "
                        "carrying the replicate weights. Captures SAMPLING "
                        "variance only — not model/imputation uncertainty.")
    p.add_argument("--hi-ctc-takeup-rate", type=float, default=0.70,
                   help="Take-up rate applied to every hi_ctc_<NNN> "
                        "expansion increment. Default 0.70 (Hawaii-empirical "
                        "anchor: observed federal-EITC take-up in HI 2022 "
                        "= 84,010 admin claims ÷ ~120,535 PUMS-eligible "
                        "filers; see tax_modeler.calibration."
                        "hi_eitc_takeup_estimate).")
    p.add_argument("--hi-eitc-100pct-takeup-rate", type=float, default=0.98,
                   help="Take-up rate applied to the hi_eitc_100pct "
                        "expansion *increment*. Default 0.98 — HI EITC "
                        "auto-computes as fixed percentage of federal on "
                        "Form N-15 (Act 209, 2023), so conditional take-up "
                        "given existing claim is near-perfect.")
    p.add_argument("--arpa-ctc-takeup-rate", type=float, default=0.94,
                   help="Take-up rate applied to the expanded_ctc_2021 "
                        "(ARPA) increment. Default 0.94 — empirical mean "
                        "of Karpman et al. (Urban, 2022) and Treasury "
                        "estimates of actual ARPA monthly-CTC participation "
                        "(band: 0.92-0.95).")
    p.add_argument("--scenarios", type=str, default=None,
                   help="Comma-separated subset of "
                        "no_eitc,no_ctc,no_hi_eitc,no_credits,"
                        "expanded_ctc_2021,hi_eitc_100pct,"
                        "hi_ctc_300,hi_ctc_650,hi_ctc_1000. "
                        "Any positive integer in hi_ctc_<N> works "
                        "(e.g. hi_ctc_500 for amendment analysis). "
                        "Default = the full menu.")
    p.add_argument("--replicate-weights", action="store_true", default=False,
                   help="(Default OFF.) Compute SDR sampling standard errors "
                        "on every persons_lifted_* and poverty_rate_* cell "
                        "using PUMS replicate weights (WGTP1..WGTP80). Adds "
                        "a *_se sibling column to every numeric output cell.")
    p.add_argument("--n-replicates", type=int, default=80,
                   help="Number of PUMS replicates to use when "
                        "--replicate-weights is set. Default 80 (full PUMS); "
                        "lower values (e.g. 10) for smoke testing. Census "
                        "Fay-method SDR is unbiased only at R=80.")
    p.add_argument("--merge-sweep", type=Path, default=None,
                   help="Path to a sweep summary CSV produced by "
                        "scripts/poverty_impact_sweep.py. When provided, "
                        "the per-parameter min/max columns are merged into "
                        "the headline CSVs as <col>_param_min/<col>_param_max.")
    p.add_argument("--eitc-poverty-alpha", type=float, default=0.5,
                   help="Elasticity exponent for the forward-projection "
                        "S1701 poverty-rate scaling of EITC amounts. "
                        "Default 0.5 (conservative half-elasticity). Only "
                        "active when projection is invoked (tax_year != "
                        "PUMS construction year).")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


# ---------------------------------------------------------------------------
# Advocacy narrative — Hawaiʻi Appleseed register
# ---------------------------------------------------------------------------
#
# After each scenario's numeric line, ``_print_summary`` emits a 2–3 sentence
# narrative in the published Hawaiʻi Appleseed voice (see hiappleseed.org —
# "Helping Hawaiʻi's Families" and "A Fairer Tax Code for a Thriving
# Hawaiʻi"). Vocabulary anchors absorbed from those publications:
#
#   * Aspirational frame: families "deserve to thrive without worrying
#     whether they'll be able to make ends meet."
#   * Action verbs: "lift up", "lift out of poverty", "keep above the
#     line."
#   * Working-family centering: "ʻohana balancing two or three jobs to
#     meet the highest cost of living in the country."
#   * Hawaiian-language signifiers for human dignity: keiki, ʻohana,
#     kūpuna — used where they naturally fit, not decoratively.
#   * Macro-to-household bridge: connect dollar figures to lived
#     experience (housed vs. unhoused, debt, educational mobility).
#
# Narratives are original to this codebase — no sentence is copied
# verbatim from any Appleseed publication. The register is matched, not
# the wording. CSV outputs are unaffected; this is stdout polish only.

_NARRATIVE_TEMPLATES: dict[str, str] = {
    "no_eitc": (
        "  The federal Earned Income Tax Credit currently keeps about "
        "{n:,} of our neighbors{u} above the poverty line — many of them "
        "keiki in working families who would otherwise struggle to make "
        "ends meet. The credit's strength is that it anchors support to "
        "earned income: the harder a family works, the more relief it "
        "receives."
    ),
    "no_ctc": (
        "  The federal Child Tax Credit lifts roughly {n:,} keiki and "
        "their caregivers{u} above the poverty line in Hawaiʻi each "
        "year. For ʻohana balancing two or three jobs to meet the "
        "highest cost of living in the country, that credit is often "
        "the difference between staying housed and falling into debt."
    ),
    "no_hi_eitc": (
        "  Hawaiʻi's state EITC — 40% of the federal credit and fully "
        "refundable since Act 209 (2023) — lifts about {n:,} working "
        "residents{u} above the poverty line. Because it auto-attaches "
        "to the federal claim, every dollar of federal EITC delivered "
        "to a Hawaiʻi household triggers another 40 cents of state "
        "relief at exactly the income levels where families struggle "
        "most."
    ),
    "no_credits": (
        "  Together, the federal EITC, federal CTC, and Hawaiʻi's state "
        "EITC keep approximately {n:,} of our neighbors{u} out of "
        "poverty — a population larger than the city of Hilo. Stripping "
        "all three would push tens of thousands of working families and "
        "their keiki below the line in a single tax year."
    ),
    "expanded_ctc_2021": (
        "  Restoring the 2021 ARPA-style Child Tax Credit — fully "
        "refundable, paid monthly, no minimum earnings requirement — "
        "would lift an additional {n:,} keiki and parents{u} above the "
        "poverty line. The 2021 expansion produced the largest "
        "single-year drop in U.S. child poverty on record before it "
        "lapsed; bringing it back means choosing whether families have "
        "to qualify for help, or whether help finds them."
    ),
    "hi_eitc_100pct": (
        "  Doubling Hawaiʻi's state EITC to match 100% of the federal "
        "credit would lift an additional {n:,} residents{u} above the "
        "poverty line. Because the credit already auto-computes on Form "
        "N-15, the expansion reaches existing claimants automatically — "
        "no new paperwork, no take-up gap, just a larger check for "
        "ʻohana already filing."
    ),
}

# Template used for hi_ctc_<NNN> scenarios (dynamic dollar amount).
_HI_CTC_NARRATIVE = (
    "  A new Hawaiʻi state Child Tax Credit set at ${amt}/keiki — "
    "fully refundable, no minimum earnings test — would lift {n:,} "
    "additional residents{u} above the poverty line.{sb_note} At this "
    "funding level, the credit channels relief to families bearing "
    "the highest cost-of-living burden anywhere in the country, with "
    "the deepest impact on households with multiple keiki."
)


def _format_uncertainty(*, se: Optional[float], param_min: Optional[float],
                       param_max: Optional[float]) -> str:
    """Render the uncertainty clause appended to the persons-count.

    Inline in the running prose: ``" (sampling 90% CI ±X,XXX; assumption
    band: A,AAA-B,BBB)"``. Returns ``""`` if neither SE nor a non-trivial
    param range is supplied.
    """
    parts: list[str] = []
    if se is not None and se > 0:
        moe = int(round(1.645 * float(se)))
        if moe > 0:
            parts.append(f"sampling 90% CI ±{moe:,}")
    if (
        param_min is not None and param_max is not None
        and not (param_min == 0 and param_max == 0)
        and param_max > param_min
    ):
        lo = int(round(float(param_min)))
        hi = int(round(float(param_max)))
        parts.append(f"assumption band: {lo:,}-{hi:,}")
    if not parts:
        return ""
    return " (" + "; ".join(parts) + ")"


def _narrative_for_scenario(
    scn: str, persons_lifted: float, *,
    se: Optional[float] = None,
    param_min: Optional[float] = None,
    param_max: Optional[float] = None,
) -> str:
    """Return a 2-3 sentence Appleseed-register narrative for one scenario.

    Returns an empty string if the scenario has no template (callers can
    fall through silently) or if persons_lifted < 1 (no human-impact
    story to tell). All formatting is plain ASCII + a few Hawaiian-
    language signifiers (keiki, ʻohana, kūpuna) where natural.
    """
    n = max(0, int(round(persons_lifted)))
    if n < 1:
        return ""
    u = _format_uncertainty(se=se, param_min=param_min, param_max=param_max)

    template = _NARRATIVE_TEMPLATES.get(scn)
    if template is not None:
        return template.format(n=n, u=u)

    # Dynamic hi_ctc_<NNN> handler.
    m = re.match(r"^hi_ctc_(\d+)$", scn)
    if m is not None:
        amt = m.group(1)
        sb_note = " This is the level proposed in SB 3125 CD1." if amt == "650" else ""
        return _HI_CTC_NARRATIVE.format(n=n, u=u, amt=amt, sb_note=sb_note)
    return ""


def tidy_by_state(
    by_state: pd.DataFrame,
    scenarios: tuple[str, ...],
    *,
    tax_year: int,
) -> pd.DataFrame:
    """Melt the single-row wide ``by_state`` frame into tidy long form.

    Wide columns encode scenario (``poverty_rate_no_eitc``), population
    (``..._hoh``) and units (``..._$``) in their names, which makes every
    new scenario a schema change. The long form is one row per
    (tax_year, scenario, population, metric) — new scenarios become rows.
    Emitted ADDITIVELY next to by_state.csv (Phase 1,
    DASHBOARD_PIPELINE_SCOPE.md); the wide file stays during transition.
    """
    row = by_state.iloc[0]
    # Longest-first so e.g. `_no_hi_eitc` wins over any shorter overlap.
    scen_tokens = sorted(scenarios, key=len, reverse=True)

    records = []
    for col in by_state.columns:
        base = col
        population = "all"
        if "_hoh" in base:
            population = "head_of_household"
            base = base.replace("_hoh", "")
        if "_baseline" in base:
            scenario = "baseline"
            metric = base.replace("_baseline", "")
        else:
            for scn in scen_tokens:
                if f"_{scn}" in base:
                    scenario = scn
                    metric = base.replace(f"_{scn}", "")
                    break
            else:
                scenario = "baseline"   # weighted_persons, weighted_filers, …
                metric = base
        records.append({
            "tax_year": tax_year,
            "scenario": scenario,
            "population": population,
            "metric": metric,
            "value": float(row[col]),
        })
    return pd.DataFrame(records)


def _print_summary(
    *,
    tax_year: int,
    by_state: pd.DataFrame,
    scenarios: tuple[str, ...],
    sdr_state: Optional[pd.DataFrame] = None,
) -> None:
    s = by_state.iloc[0]
    se = sdr_state.iloc[0] if sdr_state is not None and not sdr_state.empty else None

    def _ci(metric: str, scale: float = 1.0, unit: str = "") -> str:
        """Format ``[low, high] (±moe)`` for a metric, or '' if no SDR."""
        if se is None:
            return ""
        lo = se.get(f"{metric}_ci90_low")
        hi = se.get(f"{metric}_ci90_high")
        std = se.get(f"{metric}_se")
        if lo is None or hi is None or std is None:
            return ""
        return (f"   90% CI [{unit}{lo * scale:,.1f}, {unit}{hi * scale:,.1f}]"
                f" (±{unit}{1.645 * std * scale:,.1f})")

    print()
    print(f"=== TY {tax_year} Hawaii poverty impact ===")
    rate = s["poverty_rate_baseline"]
    persons_total = s["weighted_persons"]
    persons_poor = s["persons_in_poverty_baseline"]
    rate_ci = ""
    if se is not None:
        lo = se.get("poverty_rate_baseline_ci90_low")
        hi = se.get("poverty_rate_baseline_ci90_high")
        if lo is not None and hi is not None:
            rate_ci = f"   90% CI [{lo * 100:.2f}%, {hi * 100:.2f}%]"
    print(f"  Baseline SPM poverty rate         : {rate * 100:>14.2f}%{rate_ci}")
    print(f"  Persons in poverty (baseline)     : {persons_poor:>15,.0f}"
          f"{_ci('persons_in_poverty_baseline')}")
    print(f"  Weighted persons (state)          : {persons_total:>15,.0f}")
    if se is not None:
        print("  (CIs reflect ACS sampling variance only — SDR, R=80; "
              "they EXCLUDE model/imputation/take-up/aging uncertainty.)")
    print()
    # Baseline narrative — sets the human-impact frame before scenarios run.
    poor_int = int(round(float(persons_poor)))
    rate_pct = float(rate) * 100
    baseline_narrative = (
        f"In Hawaiʻi today, roughly {poor_int:,} of our neighbors — about "
        f"{rate_pct:.1f}% of the state — are unable to make ends meet at the "
        f"highest cost of living in the country. The figures below trace "
        f"how each tax credit pulls working ʻohana back toward stability, "
        f"and how proposed expansions could deepen that protection."
    )
    print(textwrap.fill(
        baseline_narrative, width=76,
        initial_indent="    ", subsequent_indent="    ",
    ))
    print()
    label_map = {
        "no_eitc":           "Persons lifted by EITC",
        "no_ctc":            "Persons lifted by CTC",
        "no_hi_eitc":        "Persons lifted by HI EITC",
        "no_credits":        "Persons lifted by ALL three credits (joint)",
        "expanded_ctc_2021": "Additional lift if ARPA-style CTC restored",
        "hi_eitc_100pct":    "Additional lift if HI EITC → 100% of federal",
        "hi_eitc_revert_20": "Additional poverty if HI EITC reverts to 20% of federal",
        "hi_ctc_650":        "Additional lift if HI enacts $650/child CTC",
        "rxkids_hi":         "Additional lift if HI enacts RxKids cash program",
    }
    _hi_ctc_re = re.compile(r"^hi_ctc_(\d+)$")
    for scn in scenarios:
        col = f"persons_lifted_{scn}"
        if col not in s.index:
            continue
        m = _hi_ctc_re.match(scn)
        if m is not None:
            label = f"Additional lift if HI enacts ${m.group(1)}/child CTC"
        else:
            label = label_map.get(scn, scn)
        sign = "+" if not scn.startswith("no_") else ""
        print(f"  {label:<48} : {sign}{s[col]:>14,.0f}{_ci(col)}")
        # Appleseed-register narrative (skipped silently if no template
        # or zero lift). SE / param_range columns are pulled inline so the
        # narrative reflects whatever uncertainty data is present on the row.
        narrative = _narrative_for_scenario(
            scn, float(s[col]),
            se=float(s[f"{col}_se"]) if f"{col}_se" in s.index else None,
            param_min=float(s[f"{col}_param_min"]) if f"{col}_param_min" in s.index else None,
            param_max=float(s[f"{col}_param_max"]) if f"{col}_param_max" in s.index else None,
        )
        if narrative:
            # Wrap to 76 cols so the prose reads as a paragraph indented
            # under the numeric line.
            wrapped = textwrap.fill(
                narrative.strip(), width=76,
                initial_indent="    ", subsequent_indent="    ",
            )
            print(wrapped)
            print()
    print()
    gap_baseline_m = s["poverty_gap_baseline_$"] / 1e6
    print(f"  Baseline poverty gap              : ${gap_baseline_m:>14,.1f}M"
          f"{_ci('poverty_gap_baseline_$', scale=1e-6, unit='$')}")
    for scn in scenarios:
        col = f"gap_closed_{scn}_$"
        if col not in s.index:
            continue
        label = (
            label_map.get(scn, scn)
            .replace("Persons lifted by", "Gap closed by")
            .replace("Additional lift", "Additional gap closed")
            .replace("Additional poverty", "Additional poverty gap")
        )
        sign = "+" if not scn.startswith("no_") else ""
        print(f"  {label:<48} : {sign}${s[col] / 1e6:>13,.1f}M"
              f"{_ci(col, scale=1e-6, unit='$')}")
    print()


def main(argv: Optional[list] = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.tax_year not in {2022, 2023, 2024, 2025}:
        LOG.error("--tax-year must be one of {2022, 2023, 2024, 2025}; got %d", args.tax_year)
        return 2
    if args.replicate_se and args.by_tax_unit:
        LOG.error(
            "--replicate-se requires SPM-unit mode; it is incompatible with "
            "--by-tax-unit. Drop --by-tax-unit to compute SDR sampling SEs."
        )
        return 2
    args.out.mkdir(parents=True, exist_ok=True)

    # 1. Build base units + load persons frame (needed for SPM unit assembly).
    base_units, persons = _load_units(
        args.pums_data_dir, args.use_fixture,
        with_replicate_weights=args.replicate_se,
    )

    # 1b. Attach SPM unit IDs to both frames using PUMS RELSHIPP/AGEP per
    #     Census P60-280. Done early so the spm_unit_id column rides
    #     through all subsequent enrichment/projection steps unchanged.
    from tax_modeler.pipeline import enrich_with_spm_unit_id
    base_units, persons = enrich_with_spm_unit_id(base_units, persons)
    n_assigned = int(base_units["spm_unit_id"].notna().sum())
    LOG.info("Attached spm_unit_id: %d / %d tax units in SPM accounting",
             n_assigned, len(base_units))

    # 2. Compute tax + credits for the requested year.
    PUMS_CONSTRUCTION_YEAR = 2022
    project_required = args.tax_year != PUMS_CONSTRUCTION_YEAR
    units = _build_units_for_tax_year(
        base_units, args.tax_year, project=project_required,
        eitc_poverty_alpha=args.eitc_poverty_alpha,
        use_cbo_aging=args.cbo_aging,
    )

    # 2b. Lever 3a: EITC by-children reweight. Up-weights EITC-eligible units in
    #     short with-children buckets to IRS by-children targets *before*
    #     take-up, so the stratified truncation in step 3 yields the IRS
    #     childless-claimer share (31.7%) instead of ~36%. Tax-unit weight only;
    #     SPM poverty (WGTP-weighted) sees only the indirect benefit-take-up
    #     selection shift, not a direct weight change.
    if args.reweight_eitc_by_children:
        units = _apply_eitc_reweight(units)

    # 3. IRS-anchored take-up: rank-and-truncate EITC/ACTC eligibles to
    #    actual SOI claim counts. Must run *before* ARPA CTC counterfactual
    #    column is computed so the ARPA column reflects parameter changes
    #    only and not take-up calibration of the baseline.
    if args.apply_credit_takeup:
        units = _apply_credit_takeup(units, tax_year=args.tax_year)

    # 3b. HI state EITC — computed *after* federal take-up so that
    #     non-claimants of the federal credit also receive $0 HI EITC.
    #     (HI EITC is a fixed percentage of the federal credit.)
    units = _apply_hi_eitc(units, tax_year=args.tax_year)

    # 3c. HI Low-Income Household Renters Credit (HRS §235-55.7). Small
    #     refundable credit for renters under the AGI ceilings; populates
    #     hi_renters_amount which compute_spm_resources picks up as a
    #     positive SPM resource. Take-up at unanchored 0.30 placeholder
    #     (see credits/hi_renters.py TODO).
    units = _apply_hi_renters(units)

    # 4. ARPA CTC counterfactual column (required for expanded_ctc_2021 scenario).
    units = _apply_arpa_ctc(units)

    # 5. SNAP wiring (default ON for poverty-impact reports).
    if args.apply_snap:
        units = _apply_snap(units, tax_year=args.tax_year)

    # 5b. Federal income tax liability — replaces the flat-10% fallback in
    #     compute_spm_resources. Bracket-based estimator gates on federal
    #     standard deduction so low-income filers correctly owe $0 federal
    #     tax. Without this fix, a flat 10% × money_income subtracted phantom
    #     tax for filers below the SD, biasing the SPM rate several pp high.
    from tax_modeler.liability.federal import compute_federal_income_tax_for_units
    LOG.info("Computing federal income tax liability for TY %d", args.tax_year)
    units = compute_federal_income_tax_for_units(units, tax_year=args.tax_year)

    # 6. MOOP imputation from CPS ASEC Hawaii donors. Required to bring the
    #    baseline SPM rate in line with Census-published Hawaii SPM (~10-12%);
    #    without it the baseline rate runs ~10pp high.
    if args.apply_moop:
        from tax_modeler.benefits.moop import compute_moop_for_units
        units = compute_moop_for_units(units)

    # 6a. Housing subsidy (Section 8 / public housing) — HUD-anchored take-up.
    if args.apply_housing_subsidy:
        units = _apply_housing_subsidy(units, tax_year=args.tax_year)

    # 6b. Childcare subsidy (CCSP) — HI DHS-anchored take-up.
    if args.apply_childcare_subsidy:
        units = _apply_childcare_subsidy(units, tax_year=args.tax_year)

    # 6b1. WIC food-package value — USDA-FNS-anchored take-up.
    if args.apply_wic:
        units = _apply_wic(units, tax_year=args.tax_year)

    # 6b2. LIHEAP cooling-assistance — HI DHS-anchored take-up.
    if args.apply_liheap:
        units = _apply_liheap(units, tax_year=args.tax_year)

    # 6b3. NSLP free/reduced-price school lunch — Census SPM treats this as a
    #      cash-equivalent resource (USDA federal reimbursement × school days
    #      × eligible children).
    if args.apply_school_lunch:
        from tax_modeler.benefits.school_lunch import compute_school_lunch_for_units
        LOG.info("Computing NSLP free/reduced school-lunch values for TY %d", args.tax_year)
        units = compute_school_lunch_for_units(units, tax_year=args.tax_year)

    # 6c. SPM work-side expenses: work-related childcare + other work expenses,
    #     imputed from CPS ASEC Hawaii donors. SPM subtracts both from resources.
    if args.apply_spm_expenses:
        from tax_modeler.benefits.childcare_expense import (
            compute_childcare_expense_for_units,
        )
        from tax_modeler.benefits.work_expense import compute_work_expense_for_units
        units = compute_childcare_expense_for_units(units)
        units = compute_work_expense_for_units(units)

    # 6c2. RxKids Hawaiʻi (hypothetical program). Adds rxkids_amount column
    #      used by the rxkids_hi expansion scenario in compute_poverty_impact.
    #      Statutory eligibility is Medicaid (clause 1) OR ≤300% FPL incl.
    #      unborn child (clause 2), so we compute the Medicaid receives flag
    #      FIRST on the finalized income basis. medicaid_receives is not in
    #      _SUM_COLS and Medicaid dollars are excluded from SPM resources, so
    #      this does not affect the poverty rollup — it only gates RxKids.
    if args.apply_rxkids:
        from tax_modeler.benefits.medicaid_hi_quest import compute_medicaid_for_units
        from tax_modeler.programs import compute_rxkids_for_units
        LOG.info("Computing RxKids Hawaiʻi benefit amounts for TY %d", args.tax_year)
        units = compute_medicaid_for_units(units, tax_year=args.tax_year)
        units = compute_rxkids_for_units(units, tax_year=args.tax_year)

    # 6d. SPM-unit aggregation (default): roll the tax-unit-grained
    #     credit/tax/benefit dollars up to SPM-unit granularity using the
    #     RELSHIPP-based assignment computed at step 1b. Census P60-280
    #     specifies the SPM unit (resource-sharing group within a
    #     household) as the unit of poverty analysis.
    if args.by_tax_unit:
        LOG.warning(
            "--by-tax-unit set; reporting poverty at tax-unit granularity. "
            "Persons-in-poverty will be overstated by ~10 percentage points "
            "vs Census-published Hawaii SPM. Used only for backward-compat "
            "diagnostics."
        )
        poverty_frame = units
    else:
        from tax_modeler.poverty.spm_aggregation import aggregate_to_spm_units
        rep_cols = (
            [c for c in _replicate_weight_cols() if c in persons.columns]
            if args.replicate_se else None
        )
        ratio_col = None
        if args.carry_calibration_ratio:
            from tax_modeler.poverty.weight_basis import (
                CALIBRATION_RATIO_COL, attach_calibration_ratio,
            )
            units = attach_calibration_ratio(units)
            ratio_col = CALIBRATION_RATIO_COL
            LOG.info(
                "Carrying the tax-unit calibration ratio into the SPM weights "
                "(mean %.4f, weighted by tax-unit weight %.4f)",
                float(units[ratio_col].mean()),
                float((units[ratio_col] * units["weight"]).sum() / units["weight"].sum()),
            )
        poverty_frame = aggregate_to_spm_units(
            units, persons, replicate_weight_cols=rep_cols,
            calibration_ratio_col=ratio_col,
        )
        LOG.info(
            "Aggregated %d tax units -> %d SPM units (P60-280 RELSHIPP rules); "
            "weight basis: %s.",
            len(units), len(poverty_frame),
            "WGTP x tax-unit calibration ratio" if ratio_col else "raw WGTP",
        )

    # 6e. District raking to IRS SOI ZIP filer/AGI totals. Holds per-PUMA sum(weight)
    #     fixed; reshapes within-PUMA HD assignment to match IRS district targets.
    if args.rake_to_irs_zip:
        from tax_modeler.analysis.district_raking import rake_weights_to_irs_zip
        from tax_modeler.errors import MissingDataError
        try:
            units = rake_weights_to_irs_zip(units, tax_year=args.tax_year)
        except MissingDataError as exc:
            LOG.error(
                "--rake-to-irs-zip requested but supporting data is not "
                "bundled: %s. Falling back to deterministic SERIALNO-hash "
                "assignment (district point estimates carry ~±20%% uncertainty).",
                exc,
            )
    else:
        LOG.warning(
            "--rake-to-irs-zip not enabled; within-PUMA HD/SD assignment "
            "uses deterministic SERIALNO hash (district point estimates "
            "carry ~±20%% uncertainty). IRS SOI ZIP data + ZIP→HD "
            "crosswalk are not bundled with this Tier 3 ship — flag stays "
            "default OFF until the data lands."
        )

    # 7. Resolve scenarios.
    from tax_modeler.poverty.impact import (
        _DEFAULT_SCENARIOS,
        compute_poverty_impact,
        compute_poverty_impact_with_se,
    )

    if args.scenarios:
        scenarios = tuple(s.strip() for s in args.scenarios.split(",") if s.strip())
    else:
        scenarios = _DEFAULT_SCENARIOS

    # 8. Compute impact. ``poverty_frame`` is either the SPM-unit-grained
    #    rollup (default) or the tax-unit frame (--by-tax-unit). The
    #    compute_poverty_impact function auto-detects via the n_persons
    #    column emitted by the aggregator.
    impact_kwargs = dict(
        tax_year=args.tax_year, scenarios=scenarios,
        hi_ctc_takeup_rate=args.hi_ctc_takeup_rate,
        hi_eitc_100pct_takeup_rate=args.hi_eitc_100pct_takeup_rate,
        arpa_ctc_takeup_rate=args.arpa_ctc_takeup_rate,
    )
    if args.replicate_weights:
        result = compute_poverty_impact_with_se(
            poverty_frame, n_replicates=args.n_replicates, **impact_kwargs,
        )
    else:
        result = compute_poverty_impact(poverty_frame, **impact_kwargs)

    # 8b. Merge in sweep param-range columns if a sweep summary was supplied.
    by_state_out = result.by_state
    by_county_out = result.by_county
    by_hd_out = result.by_house_district
    by_sd_out = result.by_senate_district
    by_ht_out = result.by_household_type
    if args.merge_sweep is not None:
        from tax_modeler.poverty.sweep_merge import merge_sweep_param_ranges
        try:
            (
                by_state_out, by_county_out, by_hd_out,
                by_sd_out, by_ht_out,
            ) = merge_sweep_param_ranges(
                sweep_csv=args.merge_sweep,
                by_state=by_state_out,
                by_county=by_county_out,
                by_house_district=by_hd_out,
                by_senate_district=by_sd_out,
                by_household_type=by_ht_out,
            )
            LOG.info("Merged sweep param ranges from %s", args.merge_sweep)
        except FileNotFoundError as exc:
            LOG.warning(
                "--merge-sweep target %s not found (%s); writing headline "
                "CSVs without *_param_min/*_param_max columns.",
                args.merge_sweep, exc,
            )

    # 9. Write CSVs.
    by_state_out.to_csv(args.out / "by_state.csv", index=False)
    by_county_out.to_csv(args.out / "by_county.csv", index=False)
    by_hd_out.to_csv(args.out / "by_house_district.csv", index=False)
    by_sd_out.to_csv(args.out / "by_senate_district.csv", index=False)
    by_ht_out.to_csv(args.out / "by_household_type.csv", index=False)
    tidy_by_state(by_state_out, result.scenarios, tax_year=args.tax_year).to_csv(
        args.out / "by_state_long.csv", index=False,
    )
    LOG.info("Wrote 6 poverty-impact CSVs to %s", args.out)

    # 9b. SDR sampling standard errors + 90% CIs (ACS replicate weights).
    #     State + county only — district point estimates already carry the
    #     deterministic-hash ~±20% caveat, so a sampling-only SE there would
    #     understate true uncertainty and mislead.
    sdr_state = None
    if args.replicate_se:
        from tax_modeler.poverty.uncertainty import compute_poverty_sdr
        rep_cols = [c for c in _replicate_weight_cols() if c in result.units.columns]
        sdr_state = compute_poverty_sdr(
            result.units, replicate_weight_cols=rep_cols,
            scenarios=result.scenarios, group_col=None,
        )
        sdr_county = compute_poverty_sdr(
            result.units, replicate_weight_cols=rep_cols,
            scenarios=result.scenarios, group_col="county",
        )
        sdr_state.to_csv(args.out / "by_state_se.csv", index=False)
        sdr_county.to_csv(args.out / "by_county_se.csv", index=False)
        LOG.info(
            "Wrote SDR sampling SE/CI CSVs (R=%d replicate weights) to %s",
            len(rep_cols), args.out,
        )

    # 10. Summary block.
    _print_summary(
        tax_year=args.tax_year, by_state=result.by_state,
        scenarios=result.scenarios, sdr_state=sdr_state,
    )

    if result.notes:
        print("Notes:")
        for n in result.notes:
            print(f"  - {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
