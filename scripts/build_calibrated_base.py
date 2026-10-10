"""Build the tax-unit cache and the calibrated base, nothing else.

The same steps as forecast_sb3125.py (the cache) and forecast_sb3125_enhanced.py
(enrich, base tax, IPF rake, re-score, ``save_calibrated_base``), without the
2027-2031 projection, so a base can be built for ``scripts/dotax_scorecard.py``
in a few minutes. Run from the repo root under ``uv run``, with
``HAWAII_PUMS_DIR`` set if the raw PUMS lives elsewhere::

    uv run python scripts/build_calibrated_base.py
    uv run python scripts/dotax_scorecard.py --base-edition-year 2023

Writes ``data/artifacts/tax_units_cache.parquet`` (reused when present) and
``data/artifacts/sb3125_calibrated_base.pkl``.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _forecast_common import ARTIFACT_DIR, CACHE_FILE, DATA_DIR, silence_noise  # noqa: E402

silence_noise()
from tax_modeler.artifacts import (  # noqa: E402
    check_cache_sidecar,
    load_canonical_deduction_params,
    save_calibrated_base,
    write_cache_sidecar,
)
from tax_modeler.pipeline import calibrate, compute_base_tax, enrich_for_credits  # noqa: E402


def main() -> int:
    t0 = time.perf_counter()
    if CACHE_FILE.exists():
        check_cache_sidecar(CACHE_FILE)
        units = pd.read_parquet(CACHE_FILE)
        print(f"cache loaded: {len(units):,} units", flush=True)
    else:
        from tax_modeler.loaders.pums_loader import PUMSDataLoader
        from tax_modeler.units.constructor import TaxUnitConstructor
        print(f"Loading PUMS from {DATA_DIR}", flush=True)
        loader = PUMSDataLoader(data_dir=DATA_DIR)
        person_df, hh_df = loader.load_data(state="15", pums_type="5yr")
        print(f"  {len(person_df):,} persons, {len(hh_df):,} hh ({time.perf_counter()-t0:.0f}s)", flush=True)
        constructor = TaxUnitConstructor(person_df, hh_df, use_soi_calibration=False, progress_bar=False)
        units = constructor.create_rule_based_units(parallel=False)
        print(f"  {len(units):,} units ({time.perf_counter()-t0:.0f}s)", flush=True)
        safe = units.copy()
        for col in safe.columns:
            if safe[col].dtype == object:
                safe[col] = safe[col].astype(str)
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        safe.to_parquet(CACHE_FILE, index=False)
        write_cache_sidecar(CACHE_FILE, extra={"built_by": "forecast_sb3125.py",
                                               "pums": "HI 5yr 2020-24", "n_units": int(len(safe))})
        units = pd.read_parquet(CACHE_FILE)

    cal = load_canonical_deduction_params()
    units = enrich_for_credits(units)
    units = compute_base_tax(units, deduction_params=cal, tax_year=2023)
    base = calibrate(units)
    base = compute_base_tax(base, deduction_params=cal, tax_year=2023)
    print(f"calibrated: {base['weight'].sum():,.0f} filers, "
          f"${(base['hi_tax_liability'] * base['weight']).sum() / 1e6:,.0f}M hi_tax_liability "
          f"({time.perf_counter()-t0:.0f}s)", flush=True)
    out = ARTIFACT_DIR / "sb3125_calibrated_base.pkl"
    save_calibrated_base(base, out, deduction_params=cal, tax_year=2023,
                         extra_meta={"built_by": "build_calibrated_base.py", "cd": "2"})
    print(f"saved {out} ({time.perf_counter()-t0:.0f}s)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
