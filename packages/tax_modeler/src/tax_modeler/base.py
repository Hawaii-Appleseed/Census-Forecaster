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

import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd

from tax_modeler.calibration.dotax_base import (
    BASE_YEAR as BASE_TAX_YEAR,  # the DOTAX Table A8 calibration anchor
)

logger = logging.getLogger(__name__)


def build_raw_units(
    person_df: pd.DataFrame, hh_df: pd.DataFrame, *, parallel: bool = False,
    claimant_assignment: str = "parent_first", parent_link: str = "sfn",
) -> pd.DataFrame:
    """Uncalibrated tax units from PUMS persons + households.

    ``use_soi_calibration=False``: weights are rake-calibrated later, in
    :func:`build_calibrated_base`. ``parallel=False`` is production's setting
    (the units are identical either way; the parallel path only trades memory
    for speed). ``claimant_assignment="legacy"`` reproduces the construction before 2026-11-25 (children
    claimed only by the householder or spouse); ``parent_link`` is the parent-finding policy
    (``tax_modeler.units.claimants.PARENT_LINKS``).
    """
    from tax_modeler.units.constructor import TaxUnitConstructor

    constructor = TaxUnitConstructor(
        person_df, hh_df, use_soi_calibration=False, progress_bar=False,
        claimant_assignment=claimant_assignment, parent_link=parent_link,
    )
    return constructor.create_rule_based_units(parallel=parallel)


#: Set to ``1`` to load a tax-unit cache that cannot be shown to come from the current construction code instead of
#: rebuilding it (about two and a half minutes on the mini): for quick iteration, never for figures that are published.
ALLOW_STALE_CACHE_ENV = "CF_ALLOW_STALE_CACHE"


def load_or_build_raw_units(
    cache_file: Path | str,
    data_dir: Path | str,
    *,
    state: str = "15",
    pums_type: str = "5yr",
    built_by: str = "tax_modeler.base",
    log: Callable[..., None] = print,
) -> pd.DataFrame:
    """The raw tax-unit cache, rebuilt from PUMS in ``data_dir`` when it is missing or not current.

    A cache is current when its sidecar's code key is that of the code that builds it
    (:func:`tax_modeler.artifacts.cache_status`). One that is not is rebuilt, and the log says whether the units
    came out the same as the cache they replace. It is loaded as it is, with a warning, only when
    ``CF_ALLOW_STALE_CACHE=1`` or when PUMS is not there to rebuild from. A build with neither cache nor PUMS
    raises ``MissingDataError`` with download guidance. Builds hold a lock beside the cache, so two processes
    do not build it at once, and the parquet is replaced in one step. The returned frame is always the cache as
    read back from disk (object columns as ``str``), built or not, so a run gives the same units either way.
    """
    import os

    from tax_modeler.artifacts import cache_status
    from tax_modeler.errors import MissingDataError

    cache_file = Path(cache_file)
    if cache_file.exists():
        reason, _ = cache_status(cache_file)
        if reason is None:
            return _read_units_cache(cache_file, log)
        if os.environ.get(ALLOW_STALE_CACHE_ENV) == "1":
            _warn(f"WARNING: {reason}; loading it anyway ({ALLOW_STALE_CACHE_ENV}=1). Do not publish from this run.")
            return _read_units_cache(cache_file, log)
        log(f"{reason}: rebuilding it.", flush=True)
    try:
        return _build_units_cache(cache_file, Path(data_dir), state=state, pums_type=pums_type,
                                  built_by=built_by, log=log)
    except MissingDataError:
        if not cache_file.exists():
            raise
        _warn(f"WARNING: {cache_status(cache_file)[0]}, and there is no PUMS in {data_dir} to rebuild it from; "
              "loading it anyway. Do not publish from this run.")
        return _read_units_cache(cache_file, log)


def _warn(msg: str) -> None:
    print(msg, flush=True)
    logger.warning(msg)


def _read_units_cache(cache_file: Path, log: Callable[..., None]) -> pd.DataFrame:
    log(f"Loading cached units from {cache_file}...", flush=True)
    units = pd.read_parquet(cache_file)
    log(f"  {len(units):,} units loaded from cache", flush=True)
    return units


def _build_units_cache(cache_file: Path, data_dir: Path, *, state: str, pums_type: str, built_by: str,
                       log: Callable[..., None]) -> pd.DataFrame:
    import fcntl

    from tax_modeler.artifacts import cache_status, write_cache_sidecar
    from tax_modeler.loaders.pums_loader import PUMSDataLoader

    cache_file.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_file.with_suffix(".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        # Another process may have rebuilt it while this one waited.
        if cache_file.exists() and cache_status(cache_file)[0] is None:
            return _read_units_cache(cache_file, log)

        t0 = time.perf_counter()
        log("Loading PUMS...", flush=True)
        person_df, hh_df = PUMSDataLoader(data_dir=data_dir).load_data(state=state, pums_type=pums_type)
        log(f"  {len(person_df):,} persons, {len(hh_df):,} households in {time.perf_counter()-t0:.1f}s",
            flush=True)

        t0 = time.perf_counter()
        log("Constructing tax units...", flush=True)
        units = build_raw_units(person_df, hh_df)
        log(f"  {len(units):,} units in {time.perf_counter()-t0:.1f}s", flush=True)

        safe_units = units.copy()
        for col in safe_units.columns:
            if safe_units[col].dtype == object:
                safe_units[col] = safe_units[col].astype(str)
        tmp = cache_file.with_suffix(".tmp.parquet")
        safe_units.to_parquet(tmp, index=False)
        built = pd.read_parquet(tmp)
        if cache_file.exists():
            log(f"  {_compare_units(pd.read_parquet(cache_file), built)}", flush=True)
        tmp.replace(cache_file)
        write_cache_sidecar(cache_file, extra={
            "built_by": built_by,
            "pums": "HI 5yr 2020-24",
            "n_units": int(len(built)),
        })
        log(f"  Units cached to {cache_file}", flush=True)
        return built


def _compare_units(old: pd.DataFrame, new: pd.DataFrame) -> str:
    """One line on how the rebuilt units differ from the cache they replace."""
    if old.equals(new):
        return "rebuilt units are identical to the cache they replace"
    parts = []
    if len(old) != len(new):
        parts.append(f"{len(old):,} -> {len(new):,} units")
    added, dropped = sorted(set(new.columns) - set(old.columns)), sorted(set(old.columns) - set(new.columns))
    if added:
        parts.append(f"new columns {added}")
    if dropped:
        parts.append(f"dropped columns {dropped}")
    if len(old) == len(new):
        changed = [c for c in old.columns if c in new.columns and not old[c].equals(new[c])]
        if changed:
            parts.append(f"{len(changed)} columns changed: {changed[:12]}{' ...' if len(changed) > 12 else ''}")
    return "REBUILT UNITS DIFFER from the cache they replace: " + "; ".join(parts or ["dtypes or order"])


def build_calibrated_base(
    units: pd.DataFrame,
    *,
    deduction_params: dict[str, Any] | None = None,
    tax_year: int = BASE_TAX_YEAR,
    skip_calibration: bool = False,
    log: Callable[..., None] = print,
) -> tuple[pd.DataFrame, dict[str, Any]]:
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
    deduction_params: dict[str, Any],
    tax_year: int = BASE_TAX_YEAR,
    *,
    pareto_alpha: float = 1.5,
) -> dict[str, float]:
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
) -> tuple[pd.DataFrame, float]:
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
    ded_params: dict[str, Any],
    cal_tax_year: int,
    redistribute: bool = True,
) -> tuple[pd.DataFrame, float]:
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
