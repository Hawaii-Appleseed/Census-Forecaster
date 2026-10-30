# tax_modeler — Claude rules

## Package purpose

Hawaiʻi tax microsimulation: loads PUMS households, constructs tax units, computes federal + state tax liability, applies credits, and projects poverty impact under policy reforms.

## Key entry points

| File | Purpose |
|------|---------|
| `src/tax_modeler/base.py` | The production population build every chain script calls: `load_or_build_raw_units()` (PUMS → cached raw tax units), `build_calibrated_base()` (enrich → base tax on the canonical deduction params at TY2023 → rake → re-score), `validate_calibrated_base()` (DOTAX tie-out numbers), `build_tail_units()` (calibrated base + $1M+ tail, the vs-FY26 / capital-gains variant) |
| `src/tax_modeler/agreement.py` | `compare_populations(base, poverty)`: how the revenue base (5-year PUMS, raked to DOTAX) and the poverty population (1-year PUMS, Census weights) differ on units by AGI band and filing status, children and EITC eligibility, in per cent and in the poverty sample's standard errors, with DOTAX's and the IRS's figures alongside (the IRS Table 2 returns, wages and average wage by AGI class). Read-only; run by `chain/forecast_working_family_credits.py` (writes `population_agreement.csv`, unpublished) and `scripts/check_population_agreement.py` |
| `src/tax_modeler/calibration/irs_soi_hi_table2.py` | IRS SOI Table 2 for Hawaii (every federal return from a Hawaii address, by AGI class; returns, wages, AGI), TY2022 and TY2023, from `data/calibration/irs_soi_hi_table2.json` (`scripts/parse_irs_soi_hi_table2.py`, SHA-256 and printed-total checks). The benchmark for the poverty population; the Department's resident-return tables (`dotax_base`) are the benchmark for the revenue base |
| `src/tax_modeler/calibration/dotax_credits_claimed.py` | DOTAX "Tax Credits Claimed" Tables A-1 and A-2 for the earned income credit and the food and excise credit (dollars and claims), TY2022 and TY2023, from `data/calibration/dotax_credits_claimed.json` (`scripts/parse_dotax_credits_claimed.py`, SHA-256 and ALL-equals-Individuals checks). The working-family step fits and checks against it; it used to be typed into that script |
| `src/tax_modeler/pipeline.py` | `run_pipeline()` — top-level orchestration for revenue forecasts; builds its base through `base.build_calibrated_base`, so it matches production. Also the stage primitives (`enrich_for_credits`, `compute_base_tax`, `calibrate`) |
| `src/tax_modeler/poverty/impact.py` | `compute_poverty_impact()` — SPM poverty rate computation |
| `src/tax_modeler/units/constructor.py` | `TaxUnitConstructor` — builds tax units from PUMS persons |
| `src/tax_modeler/units/spm_unit_assembly.py` | `build_spm_unit_assignment()` — Census P60-280 SPM-unit assembly |
| `src/tax_modeler/poverty/spm_aggregation.py` | `aggregate_to_spm_units()` — rolls tax-unit results to SPM granularity |
| `src/tax_modeler/config/tax_system_config.py` | `TaxSystemRegistry` — bill variant registry, `compare_systems()` |

## Submodule map

| Submodule | Contents |
|-----------|---------|
| `poverty/` | SPM resource computation, thresholds, poverty impact; SPM-unit aggregation |
| `credits/` | EITC, CTC, HI-EITC, CDCC, RxKids credit calculators |
| `units/` | Tax-unit constructor, SPM-unit assembly, filing status inference |
| `liability/` | Federal + Hawaiʻi income tax calculation |
| `calibration/` | IPF weight calibration to DOTAX filer totals |
| `projection/` | Forward-year tax-unit projection (CBO/SOI/ACS-forward targets) |
| `scenarios/` | Per-bill credit overlay definitions (SB3125 CD1/CD2, HB2306) |
| `loaders/` | PUMS data loader, CEX loader, benefit imputation |

## Critical architectural rule

**Revenue forecasts stay tax-unit-grained. Only the poverty-impact pipeline uses SPM-unit aggregation.** Tax credits are correctly computed at tax-unit granularity (returns are filed per tax unit). SPM aggregation is applied only in `scripts/poverty_impact_report.py` after all credit math is done.

## Test markers

```bash
# Fast, no external data (always run)
pytest tests/ -m smoke

# Requires ~83 MB DOTAX SOI tables
pytest tests/ -m requires_dotax_raw

# Requires ~639 MB IRS national SOI files
pytest tests/ -m requires_irs_external
```

## Do not touch without reading first

- `calibration/ipf_orchestrator.py` — complex multi-margin IPF; calibrated weights hit DOTAX filer counts
- `projection/income_forecast.py` — CBO/BLS-anchored forward projection; φ parameters are empirically calibrated
