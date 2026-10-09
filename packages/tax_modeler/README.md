# tax_modeler

Hawaii income-tax + federal EITC microsimulation, packaged for reuse.

The package builds tax filing units from ACS PUMS microdata, applies state
tax law (brackets, standard deduction, itemized deduction, capital-gains
cap), folds in federal EITC + CTC, calibrates weights against DOTAX
benchmarks, projects forward to a target year, and aggregates revenue by
county / quintile / filing status.

Currently configured for Hawaii.  A `StateConfig` seam is in place so a
future state plug-in is a config-file change, not a refactor — see
[`SCENARIOS.md`](./SCENARIOS.md).

## Install

From the monorepo root:

```bash
# Workspace install (recommended — gets all four packages editable):
uv sync --all-packages --extra dev

# Or just this package, editable:
uv pip install -e packages/tax_modeler
```

After install, `import tax_modeler` works without `sys.path` hacks.

Optional extras:
- `tax-modeler[ui]` — Streamlit demo dependencies
- `tax-modeler[geo]` — geopandas for shapefile-based analysis
- `tax-modeler[cex]` — pyreadstat for CEX micro-data parsing

## Quick start

```python
from tax_modeler import run_pipeline

result = run_pipeline(target_year=2026)
print(result.state_summary["hi_net_tax_revenue"])
print(result.by_county)
```

`run_pipeline` returns a `PipelineResult` with the calibrated base-year
units, projected target-year units, a `RevenueEstimator` instance, and
pre-computed county / quintile / filing-status breakdowns.

If you've already calibrated a base-year DataFrame, skip stages 1–5 by
supplying it directly:

```python
result = run_pipeline(target_year=2026, tax_units_df=my_calibrated_df)
```

## Public API

`tax_modeler.PUBLIC_API` lists the supported surface.  Anything not in
that tuple is internal — fine to import, no compatibility guarantee
across releases.

The most common entry points:

| Symbol                     | Purpose                                          |
| -------------------------- | ------------------------------------------------ |
| `run_pipeline`             | End-to-end run; returns `PipelineResult`.        |
| `enrich_for_credits`       | Stage 3: derive EITC/CTC inputs from PUMS units. |
| `compute_base_tax`         | Stage 4: Hawaii liability + federal credits.     |
| `calibrate`                | Stage 5: IPF rake against DOTAX benchmarks.      |
| `TaxUnitConstructor`       | Build tax units from person + household frames.  |
| `TaxCalculator`            | Bracket-walk computation primitive.              |
| `TaxSystemRegistry`        | Pre-built Hawaii bills (Act 46, SB 3125, …).     |
| `compare_systems`          | Side-by-side revenue comparison.                 |
| `RevenueEstimator`         | Population-weighted aggregation.                 |
| `StateConfig`, `HAWAII`    | State-level constants.                           |

The underscore-prefixed legacy names (`_enrich_for_credits`,
`_compute_base_tax`, `_calibrate`) still work but emit `DeprecationWarning`.

## Errors

Boundary failures raise typed exceptions from `tax_modeler.errors`:

- `MissingDataError` — required PUMS / DOTAX / IRS file absent.  Carries
  the search path and an env-var hint for resolution.
- `DataValidationError` — input DataFrame missing required columns or
  empty.  Lists the missing columns explicitly.
- `ConfigError` — bad year, unknown state, missing parameters.  Includes
  the available values where possible.
- `CalibrationError` — IPF non-convergence or inconsistent targets.

All inherit from `TaxModelerError` and from the appropriate Python builtin
(`FileNotFoundError`, `ValueError`, `RuntimeError`) so legacy `except`
blocks keep catching them.

## Two entry points each for PUMS and projection

The simple "give me a DataFrame / scalar" use cases delegate to the
monorepo's `pums_estimator` / `census_forecaster` packages via thin
adapters.  The full-fat loaders are kept alongside for use cases the
simple adapters don't cover:

| Task                  | Simple adapter (preferred)                                                            | Full-fat loader (advanced)                                                                              |
| --------------------- | ------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| Load PUMS             | `tax_modeler.load_hawaii_pums(year)` (delegates to `pums_estimator.fetch_pums`)       | `tax_modeler.PUMSDataLoader().load_data(...)` — incremental batched loads, vintage column handling      |
| Income growth factor  | `tax_modeler.project_income_growth(history, target_year)`                             | `tax_modeler.EnsembleProjector(...)` — BLS OES wage blending, occupation matching                       |

## Data files

Bundled in the wheel (~25 KB):
- `data/tax_tables/hawaii_2022/*.csv` — bracket and exemption tables
- `data/policy/baseline_2022_deductions.json` — standard/itemized policy baseline
- `data/calibration/dotax_indinc_<year>.json` — the DOTAX *Individual Income
  Tax Statistics* tables the calibration anchors on (A-8 filers and tax by AGI
  class, A-1 AGI, Table 4 filing status, Table 21 capital gains, 17A
  nonresidents), as printed. `calibration/dotax_base.py` reads them; a new
  edition is one command:
  `python scripts/parse_dotax_indinc.py <YEAR>indinc.pdf <YEAR>`
  (PDF from `files.hawaii.gov/tax/stats/stats/indinc/<YEAR>indinc.pdf`), then
  set `dotax_base.BASE_YEAR`. The parser checks every table against its own
  printed total.

Checked into the repo (~2 MB):
- `data/tax_modeler/crosswalks/` — PUMA / district / ZIP geography
- `data/tax_modeler/irs_soi/` — small IRS SOI summary tables

Fetched on demand (gitignored):
- `data/tax_modeler/raw/` — Hawaii DOTAX SOI tables (~83 MB).
  `python -m tax_modeler.scripts.fetch_dotax_soi`
- `data/tax_modeler/external/` — IRS national SOI Excel files (~639 MB).
  `python -m tax_modeler.scripts.fetch_irs_soi`

Tests that depend on `raw/` or `external/` skip automatically when those
directories are empty so a fresh clone has a green smoke suite.

## Tests

Smoke tests run on a synthetic 50-household fixture (no real PUMS / DOTAX
/ IRS data needed) and finish in under 30 seconds:

```bash
uv run pytest -m smoke -v
```

Full suite (some tests require external data):

```bash
uv run pytest tests/tax_modeler -v
```

## Authoring scenarios

See [`SCENARIOS.md`](./SCENARIOS.md) for:

- How to add a new Hawaii bill (concrete, with an SB 3125 CD1 example).
- How to extend to a new state (conceptual, points at `StateConfig`).
- How to model benefit reforms via the Reform DSL.
- How to use the per-PUMA `--by-puma` flag on each forecast script.

## Real-data setup (CPS-ASEC slice)

Phase 9 ships donor-matching imputation against the real Hawaii CPS-ASEC
public-use slice. The bundled parquet at
`src/tax_modeler/data/cps_donor/cps_asec_hawaii.parquet` is committed,
but if you need to regenerate (e.g. after a Census September release):

```bash
python scripts/build_cps_asec_slice.py --year 2024
```

The script downloads the Census public-use file (~140MB), filters to
Hawaii residents via the household FIPS linker, and writes a small
parquet (~130KB). When the real parquet isn't present (fresh checkout,
partial install), donor matchers fall back to a synthetic CSV so smoke
tests still run in CI.

To verify the full Phase 9 stack end-to-end:

```bash
python -m tax_modeler.validation.phase9_validation
```

## EITC / CTC by geography

Federal EITC and CTC totals (plus HI state EITC at 40% of federal) can be
rolled up to state, county, House district, and Senate district via the
report script. Year-keyed credit parameters cover TY 2022–2025 (IRS Rev.
Procs. 2021-45, 2022-38, 2023-34, 2024-40); forward projections clamp to
the most recent published vintage.

```bash
# TY 2025 projection
python scripts/eitc_ctc_geo_report.py --tax-year 2025 \
    --out reports/eitc_ctc_2025/

# TY 2022 backtest, with delta vs. CBPP table 367 by senate district
python scripts/eitc_ctc_geo_report.py --tax-year 2022 --compare-cbpp \
    --out reports/eitc_ctc_2022_backtest/
```

Pass `--apply-takeup` to apply IRS-anchored take-up imputation (zero out
federal EITC + ACTC for non-claimants per the rank-based rule calibrated
to the IRS SOI Hawaii TY 2022 state totals). The opt-out default keeps
raw eligibility numbers intact.

## Provenance

Imported via `git subtree add` on 2026-04-29 from
`/Users/dtomkatsu/ctc-and-eitc` branch `feature/bea-rates-dotax-fix`
(commit `0bea005`).  Run `git log --follow <file>` on any moved file to
see its full pre-import history.
