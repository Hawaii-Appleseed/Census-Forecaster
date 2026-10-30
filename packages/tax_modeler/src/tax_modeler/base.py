"""The production population build: PUMS -> raw tax units -> calibrated base -> $1M+ tail.

Every chain script used to hand-assemble these stages; this module is the one
place that owns the sequence, so a change to it (deduction basis, calibration
year, the order of the steps) reaches every consumer. Stages:

1. :func:`build_raw_units` / :func:`load_or_build_raw_units` -- PUMS persons and
   households to uncalibrated tax units (cached as parquet + a provenance sidecar).
2. :func:`build_calibrated_base` -- enrich, score on the canonical itemized
   deduction params at TY2023, rake to DOTAX, score again. This is what
   ``save_calibrated_base`` pickles for the rest of the chain.
   :func:`validate_calibrated_base` returns the DOTAX tie-out numbers.
3. :func:`build_tail_units` -- the calibrated base plus the synthetic $1M+ tail
   (optionally with the $500K-$1M redistribution), tail calibrated to DOTAX's
   tax. ``scenarios.act24_population.build_units`` shares the same core and adds
   aging.

``pipeline.run_pipeline`` builds its base through :func:`build_calibrated_base`
too, so it matches production.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable, Dict, Tuple

import pandas as pd

BASE_TAX_YEAR = 2023  # the DOTAX Table A8 calibration anchor


def build_raw_units(
    person_df: pd.DataFrame, hh_df: pd.DataFrame, *, parallel: bool = False,
) -> pd.DataFrame:
    """Uncalibrated tax units from PUMS persons + households.

    ``use_soi_calibration=False``: weights are rake-calibrated later, in
    :func:`build_calibrated_base`. ``parallel=False`` is production's setting
    (the units are identical either way; the parallel path only trades memory
    for speed).
    """
    from tax_modeler.units.constructor import TaxUnitConstructor

    constructor = TaxUnitConstructor(
        person_df, hh_df, use_soi_calibration=False, progress_bar=False,
    )
    return constructor.create_rule_based_units(parallel=parallel)


def load_or_build_raw_units(
    cache_file: Path | str,
    data_dir: Path | str,
    *,
    state: str = "15",
    pums_type: str = "5yr",
    built_by: str = "tax_modeler.base",
    log: Callable[..., None] = print,
) -> pd.DataFrame:
    """The raw tax-unit cache, built from PUMS in ``data_dir`` when missing.

    A cache hit is checked against its sidecar (``check_cache_sidecar`` warns if
    it predates the current code). A miss loads PUMS (``MissingDataError`` with
    download guidance if it is absent), constructs units, and writes the parquet
    (object columns as ``str``, which parquet needs) plus the sidecar, recording
    ``built_by``. The returned frame is what the cache would load, except on a
    build, where it is the in-memory units (object columns intact).
    """
    from tax_modeler.artifacts import check_cache_sidecar, write_cache_sidecar
    from tax_modeler.loaders.pums_loader import PUMSDataLoader

    cache_file = Path(cache_file)
    if cache_file.exists():
        log(f"Loading cached units from {cache_file}...", flush=True)
        check_cache_sidecar(cache_file)
        units = pd.read_parquet(cache_file)
        log(f"  {len(units):,} units loaded from cache", flush=True)
        return units

    t0 = time.perf_counter()
    log("Loading PUMS...", flush=True)
    person_df, hh_df = PUMSDataLoader(data_dir=Path(data_dir)).load_data(state=state, pums_type=pums_type)
    log(f"  {len(person_df):,} persons, {len(hh_df):,} households in {time.perf_counter()-t0:.1f}s", flush=True)

    t0 = time.perf_counter()
    log("Constructing tax units...", flush=True)
    units = build_raw_units(person_df, hh_df)
    log(f"  {len(units):,} units in {time.perf_counter()-t0:.1f}s", flush=True)

    safe_units = units.copy()
    for col in safe_units.columns:
        if safe_units[col].dtype == object:
            safe_units[col] = safe_units[col].astype(str)
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    safe_units.to_parquet(cache_file, index=False)
    write_cache_sidecar(cache_file, extra={
        "built_by": built_by,
        "pums": "HI 5yr 2020-24",
        "n_units": int(len(safe_units)),
    })
    log(f"  Units cached to {cache_file}", flush=True)
    return units


def build_calibrated_base(
    units: pd.DataFrame,
    *,
    deduction_params: Dict[str, Any] | None = None,
    tax_year: int = BASE_TAX_YEAR,
    skip_calibration: bool = False,
    log: Callable[..., None] = print,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Raw units -> the calibrated base every chain step starts from.

    enrich -> base tax on ``deduction_params`` at ``tax_year`` -> IPF rake to the
    DOTAX benchmarks -> base tax again (so ``hi_tax_liability`` reflects the final
    weights, itemized deductions on). ``deduction_params=None`` loads the
    canonical set (``artifacts.load_canonical_deduction_params``); score on the
    same params everywhere downstream or the tail and the projection disagree.
    ``skip_calibration`` returns the enriched, scored units without the rake and
    the second scoring (for debugging construction in isolation).

    Returns ``(base, deduction_params)``; save both with ``save_calibrated_base``.
    """
    from tax_modeler.artifacts import load_canonical_deduction_params
    from tax_modeler.pipeline import calibrate, compute_base_tax, enrich_for_credits

    if deduction_params is None:
        deduction_params = load_canonical_deduction_params()
        log("  Loaded itemized-deduction params for calibration", flush=True)

    units = enrich_for_credits(units)
    units = compute_base_tax(units, deduction_params=deduction_params, tax_year=tax_year)
    if skip_calibration:
        return units, deduction_params
    base = calibrate(units)
    base = compute_base_tax(base, deduction_params=deduction_params, tax_year=tax_year)
    return base, deduction_params


def validate_calibrated_base(
    base: pd.DataFrame,
    deduction_params: Dict[str, Any],
    tax_year: int = BASE_TAX_YEAR,
    *,
    pareto_alpha: float = 1.5,
) -> Dict[str, float]:
    """Tie the base out to DOTAX after the $1M+ synthesis (diagnostic only).

    The IPF rake cannot reach the $1M+ bin (1.5x bin cap), so the calibration
    target is met after synthesis: synthesize the tail at ``pareto_alpha``,
    calibrate it to DOTAX's tax (no aging, like the targets), and total the
    weighted tax. Returns ``hi_tax_liability_M`` and ``hi_state_tax_M`` (net of
    credits) with the two DOTAX targets they should be near
    (``dotax_before_credits_M``, ``dotax_after_credits_M``) and ``dotax_base_year``.
    """
    from tax_modeler.calibration import dotax_base
    from tax_modeler.pipeline import compute_base_tax
    from tax_modeler.scenarios.top_income_synthesis import (
        calibrate_synthetic_tail_to_tax_target,
        synthesize_top_filers,
    )

    def score(u: pd.DataFrame) -> pd.DataFrame:
        return compute_base_tax(u, deduction_params=deduction_params, tax_year=tax_year)

    # The target the calibration rakes to (the $1M+ class on its window), not the printed total.
    before_m = sum(dotax_base.tax_targets_M().values())
    after_m = dotax_base.load_indinc(dotax_base.BASE_YEAR)[
        "table_a8_resident_liability"]["total"]["tax_after_M"]
    scored, _ = calibrate_synthetic_tail_to_tax_target(
        score(synthesize_top_filers(base, pareto_alpha=pareto_alpha)), score=score)
    return {
        "hi_tax_liability_M": float((scored["hi_tax_liability"] * scored["weight"]).sum() / 1e6),
        "hi_state_tax_M": float((scored["hi_state_tax"] * scored["weight"]).sum() / 1e6),
        "dotax_before_credits_M": float(before_m),
        "dotax_after_credits_M": float(after_m),
        "dotax_base_year": int(dotax_base.BASE_YEAR),
    }


def synthesize_calibrated_tail(
    base: pd.DataFrame,
    *,
    alpha: float,
    score: Callable[[pd.DataFrame], pd.DataFrame],
    enrich: bool = True,
    redistribute: bool = False,
) -> Tuple[pd.DataFrame, float]:
    """The calibrated base with its $1M+ tail synthesized and calibrated; ``(units, tail_k)``.

    [``redistribute_mid_high_incomes``] -> ``synthesize_top_filers`` ->
    [``enrich_for_credits``] -> ``impute_capital_gains_from_soi`` ->
    ``calibrate_synthetic_tail_to_tax_target`` under ``score``. The shared core of
    :func:`build_tail_units` and ``scenarios.act24_population.build_units`` (which
    then ages the tail); it does not age.
    """
    from tax_modeler.calibration.cg_imputation import impute_capital_gains_from_soi
    from tax_modeler.pipeline import enrich_for_credits
    from tax_modeler.scenarios.top_income_synthesis import (
        calibrate_synthetic_tail_to_tax_target,
        redistribute_mid_high_incomes,
        synthesize_top_filers,
    )

    units = base
    if redistribute:
        units = redistribute_mid_high_incomes(units, pareto_alpha=alpha)
    units = synthesize_top_filers(units, pareto_alpha=alpha)
    if enrich:
        units = enrich_for_credits(units)
    units = impute_capital_gains_from_soi(units)    # CG rate cap for $100K-$1M filers
    return calibrate_synthetic_tail_to_tax_target(score(units), score=score)


def build_tail_units(
    base: pd.DataFrame,
    *,
    alpha: float,
    ded_params: Dict[str, Any],
    cal_tax_year: int,
    redistribute: bool = True,
) -> Tuple[pd.DataFrame, float]:
    """The unaged $1M+ population the vs-FY26 and capital-gains steps project from.

    The $500K-$1M income redistribution (``redistribute``), the Pareto tail at
    ``alpha``, enrichment, capital-gains imputation, and the tail calibrated to
    DOTAX's tax, re-scored on ``ded_params`` / ``cal_tax_year`` (the basis the
    base was calibrated under, or ``tail_k`` is wrong). The tail is not aged:
    ``project_and_recalibrate``'s SOI anchor replaces every $1M+ unit with SOI
    tiers aged from TY2022, so its level is moot there. Returns ``(units, tail_k)``.
    """
    from tax_modeler.pipeline import compute_base_tax

    def score(u: pd.DataFrame) -> pd.DataFrame:
        return compute_base_tax(u, deduction_params=ded_params, tax_year=cal_tax_year)

    return synthesize_calibrated_tail(
        base, alpha=alpha, score=score, enrich=True, redistribute=redistribute)
