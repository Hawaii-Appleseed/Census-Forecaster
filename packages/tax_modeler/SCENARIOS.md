# Authoring scenarios

This guide shows how to (1) add a new Hawaii bill scenario to the existing
registry, and (2) extend the package to a new state.

## Part 1 — Adding a new Hawaii bill

A "scenario" is a `TaxSystemConfig` describing brackets, standard
deduction, and personal exemption for a target year.
`TaxSystemRegistry` exposes one factory per supported bill.

### Step 1: register the system

`packages/tax_modeler/src/tax_modeler/config/tax_system_config.py` already
contains factories like `get_act46_system(year)`, `get_sb3125_cd1_system(year)`,
`get_hb2306_orig_system(year)`.  Add a sibling with the same shape:

```python
@classmethod
def get_my_bill_system(cls, year: int) -> TaxSystemConfig:
    """My Bill (HB XXXX): brief description of bracket changes."""
    return TaxSystemConfig(
        name=f"my_bill_{year}",
        year=year,
        bracket_year=year,                 # selects bracket CSV vintage
        standard_deduction_year=year,      # selects SD CSV vintage
        personal_exemption=cls.PERSONAL_EXEMPTIONS.get(year, 1200),
        description="HB XXXX: increases top bracket to 13% for income > $1M",
    )
```

If your bill introduces brackets that don't exist in any current CSV,
add a new column to `data/tax_tables/hawaii_2022/hawaii_tax_brackets_master_all.csv`
and reference it via `bracket_year`.

### Step 2: optionally add credit overlays

Bill-specific credit changes (REEC caps, CGEC sunsets, TCRA
acceleration, etc.) live in `tax_modeler/scenarios/`.  Use
`scenarios/sb3125_cd1_credits.py` as a template.  Each module exposes a
`compute_credit_overlay(year, **kwargs)` that returns a dict of credit
deltas.

### Step 3: compare against a baseline

```python
from tax_modeler import TaxCalculator, TaxSystemRegistry, compare_systems

calc = TaxCalculator()
baseline = TaxSystemRegistry.get_act46_system(2027)
my_bill  = TaxSystemRegistry.get_my_bill_system(2027)

cmp = compare_systems(projected_units, baseline, my_bill, calculator=calc)
print(cmp)
```

`compare_systems` returns a DataFrame with revenue under both systems
and the delta.

### Step 4: write a forecast script

Mirror `forecast_sb3125_cd1.py` at the repo root.  After this refactor,
those scripts no longer need `sys.path.insert` hacks — they just import
from the installed `tax_modeler` package.

## Part 2 — Extending to a new state

The package is currently Hawaii-only but the heavy hardcodes are
collected behind `tax_modeler.config.state_config.StateConfig`.  Adding a
new state is roughly:

### Step 1: define the state config

Add a sibling of `HAWAII` in
`packages/tax_modeler/src/tax_modeler/config/state_config.py`:

```python
OREGON = StateConfig(
    name="Oregon",
    state_fips="41",
    default_geoid="41051",            # Multnomah County (Portland) as state-proxy
    cg_cap_rate=0.0,                  # Oregon has no CG cap; skip cap math
    pease_threshold_single=...,       # ORS-specific thresholds
    pease_threshold_mfs=...,
    personal_exemption_year_inflection=...,
    tax_table_dir=_PACKAGE_ROOT / "data" / "tax_tables" / "oregon_2022",
    available_bracket_years=(2020, 2024, 2026),
)
```

### Step 2: bundle the state's tax tables

Drop bracket and standard-deduction CSVs into the directory pointed at by
`tax_table_dir`.  Schema must match Hawaii's:

- `<state>_tax_brackets_master_all.csv` — one column per bracket-year vintage.
- `<state>_standard_deductions_by_year.csv` — SDs by filing status × year.

### Step 3: add a state-specific liability path (if needed)

Hawaii's bracket-walk is generic enough that any progressive-bracket state
can reuse it.  States with extra wrinkles (e.g. an alternative minimum
tax, a kicker rebate) need a sibling of `liability/hawaii.py` that
implements the wrinkle and routes through `liability/__init__.py`.

The Hawaii capital-gains cap (HRS §235-16, 7.25%) is already
parameterized via `state_config.cg_cap_rate` — set to `0.0` for states
without a CG cap and the cap branch is skipped.

### Step 4: add a state-specific calibration target loader

`year_recalibrator.project_and_recalibrate` takes a calibration-target
loader.  Today it defaults to Hawaii's COR projections.  An Oregon
plug-in would supply Oregon Department of Revenue projections via the
same callable interface.

### Step 5: add bill-specific scenarios

Create `tax_modeler/scenarios/oregon/` for Oregon bills, mirroring the
existing Hawaii scenario modules.

### What does *not* need to change

- `TaxCalculator` — generic bracket math, parameterized by config.
- `TaxUnitConstructor` — works on any state's PUMS as long as the schema
  matches Census ACS.
- `RevenueEstimator` — pure aggregation, state-agnostic.
- IPF rake calibration internals — accept any target dictionary.
- Federal EITC / CTC — federal law, no state customization.

## Smoke tests

Every scenario should be exercised by at least one smoke test on the
synthetic fixture.  Add a test under `tests/tax_modeler/smoke/` that
calls your scenario through `compare_systems` or `run_pipeline` and
asserts the revenue delta has the expected sign.  See
`tests/tax_modeler/smoke/test_e2e_smoke.py` for the pattern.

## Part 3 — Modeling benefit reforms

The Phase 1-6 policy-impact extension adds a `Reform` DSL that handles
both tax-rate AND benefit changes through one machinery.  Use it
whenever the question is *"how does X change household income or
poverty?"* rather than *"how does X change state revenue?"*

### The Reform DSL

```python
from tax_modeler import Reform, apply_reform

reform = Reform(
    name="snap_minus_10pct",
    benefit_overrides={"snap": {"max_benefit_pct": 0.90}},
)
result = apply_reform(taxed_units, reform, year=2026)

print(result.benefit_flow_deltas_millions)  # {"snap": -X.XX}
counterfactual = result.counterfactual_units
```

### Programs the DSL recognizes

| Program key            | Module                                               | Reform knobs (multipliers default 1.0)                          |
|------------------------|------------------------------------------------------|------------------------------------------------------------------|
| `snap`                 | `benefits.snap`                                      | `max_benefit_pct`, `gross_income_limit_factor`, `net_income_limit_factor` |
| `ssi`                  | `benefits.ssi`                                       | `federal_payment_pct`, `income_threshold_factor`                |
| `ssi_hi_supplement`    | `benefits.ssi_hi_supplement`                         | `monthly_amount`, `coverage_pct`                                |
| `eitc`                 | `credits.eitc` (federal)                             | `amount_pct` (multiplier on existing column)                   |
| `ctc`                  | `credits.ctc` (federal)                              | `amount_pct`                                                    |
| `hi_eitc`              | `credits.hi_eitc`                                    | `rate_of_federal`, `refundable`, `amount_pct`                  |
| `hi_food_excise`       | `credits.hi_food_excise`                             | `single`, `joint` (AGI tables), `amount_pct`, `income_threshold_factor` |
| `hi_renters`           | `credits.hi_renters`                                 | `takeup_pct`, `amount_pct`, `income_threshold_factor`          |
| `aca_ptc`              | `benefits.aca_ptc`                                   | `credit_pct`, `benchmark_premium_pct`, `income_threshold_factor` |
| `medicaid`             | `benefits.medicaid_hi_quest`                         | `adult_pmpm_pct`, `child_pmpm_pct`, `aged_pmpm_pct`            |
| `wic`                  | `benefits.wic`                                       | `package_value_pct`, `income_threshold_factor`                 |
| `liheap`               | `benefits.liheap`                                    | `benefit_amount_pct`, `income_threshold_factor`                |
| `childcare`            | `benefits.childcare`                                 | `per_child_subsidy_pct`, `income_threshold_factor`             |
| `housing`              | `benefits.housing`                                   | `payment_standard_pct`, `tenant_rent_pct`                      |

Tax-rate reforms (e.g. SB 3125 CD1) plug in via `tax_system_factory`:

```python
from tax_modeler import TaxSystemRegistry

reform = Reform(
    name="combined",
    tax_system_factory=TaxSystemRegistry.get_sb3125_cd1_system,
    benefit_overrides={"snap": {"max_benefit_pct": 0.90}},
)
```

### Computing the poverty / distribution impact

`apply_reform` only computes the comparison; downstream you decide what
to compute on the resulting frames:

```python
from tax_modeler import (
    compute_spm_resources, hawaii_spm_threshold, poverty_rate, decile_summary,
)

base_resources, _ = compute_spm_resources(units)
cf_resources, _   = compute_spm_resources(result.counterfactual_units)
threshold = hawaii_spm_threshold(2024)

base_rate = poverty_rate(base_resources["spm_resources"], threshold, base_resources["weight"])
cf_rate   = poverty_rate(cf_resources["spm_resources"],   threshold, cf_resources["weight"])
print(f"Poverty: {base_rate*100:.2f}% → {cf_rate*100:.2f}%")

deciles = decile_summary(cf_resources["spm_resources"], cf_resources["weight"])
print(deciles)
```

### TRIM3-style baseline calibration

Without correction, ACS-derived simulated take-up systematically
overcounts SNAP/SSI eligibility relative to administrative caseload.
Calibrate baseline before applying reforms:

```python
from tax_modeler import AdminCaseload, calibrate_benefits

caseload = AdminCaseload.load()
units = calibrate_benefits(units, caseload=caseload, year=2024)
# Per TRIM3 convention, apply_reform recomputes from eligibility on the
# counterfactual path — the imputed take-up flag affects baseline only.
```

### Validation

`tax_modeler.validation.validate_against_admin_caseload` produces a
typed report comparing simulated vs administrative aggregates for SNAP,
SSI, and the HI SSI supplement:

```python
from tax_modeler.validation import validate_against_admin_caseload

report = validate_against_admin_caseload(units, caseload=caseload, year=2024)
print(report.summary())
assert report.passes  # within ±5% count, ±20% dollars
```

### End-to-end forecast scripts

Three runnable demos at the repo root chain the full stack:

* `forecast_snap_10pct_cut.py`     — SNAP cut → poverty/income impact
* `forecast_eitc_doubled.py`       — federal + HI EITC expansion
* `forecast_combined_reform.py`    — tax cut + benefit cut combined

Each runs in <2s on the bundled synthetic fixture and accepts CLI flags
(`--cut-pct`, `--year`, `--out`, etc.) for parameter sweeps.

### Geographic stratification (`--by-puma`)

Each `forecast_*.py` script accepts an opt-in `--by-puma` flag that
produces a per-PUMA breakdown alongside the headline state-level table:

```
$ python forecast_snap_10pct_cut.py --by-puma --no-takeup-imputation

[state-level summary printed as usual]

Per-PUMA breakdown saved: /tmp/forecast_snap_10pct_cut_by_puma.csv
       count_weighted  poverty_rate_baseline  poverty_rate_cut  delta_pp
00100          5453.0                  0.171             0.187     +1.56
```

PUMAs with weighted count below `--puma-min-count` (default 100) are
flagged with a `~` prefix to signal small-sample instability per Census
SAE guidance. With Hawaii's 12 PUMAs, full PUMS-level runs typically
produce 50K+ filers per PUMA so suppression rarely fires; the synthetic
fixture has all units in one PUMA so the table collapses to one row.

### Real CPS-ASEC donor matching (Phase 9)

Phase 9 ships a real Hawaii CPS-ASEC slice at
`packages/tax_modeler/src/tax_modeler/data/cps_donor/cps_asec_hawaii.parquet`
(~2,575 person-records) used by `impute_moop()`,
`impute_childcare_expense()`, and `impute_work_expense()` for SPM
expense imputation. To regenerate from a fresh Census release:

```bash
python scripts/build_cps_asec_slice.py --year 2024
```

If the bundled parquet is missing (fresh checkout, partial install),
the donor matchers fall back to a synthetic donor CSV so smoke tests
still run in CI.

### Multi-marginal demographic projection

`project_demographics_forward(units, target_year=N, dimensions=[...])`
rakes weights across multiple dimensions for long-horizon simulations:

```python
from tax_modeler import project_demographics_forward, hawaii_demographic_targets

# Project to 2040 demographics (HI 65+ share, HH-size dist, disability prevalence)
units_2040, result = project_demographics_forward(
    units, target_year=2040,
    dimensions=["senior", "hh_size", "disability"],
)
print(result.per_dim_results)  # post-rake shares per dimension
```

When `dimensions` is omitted, the function preserves the original
single-dim (senior-only) behavior for backwards compatibility.

### Reforms as YAML files (Phase 11)

`Reform` instances serialize to YAML for reproducibility, sharing,
and audit trails. Example checked-in spec at `reforms/snap_minus_10pct.yaml`:

```yaml
name: snap_minus_10pct
benefit_overrides:
  snap:
    max_benefit_pct: 0.90
metadata:
  created: 2026-05-09
  author: dtomkatsu
  description: 10% reduction in maximum SNAP allotment
  citation: HI Senate Ways & Means request, May 2026
  data_vintage:
    cps_asec: 2024
    dotax_dhs_caseload: 2024
```

Round-trip through Python:

```python
from tax_modeler import Reform

reform = Reform.from_yaml("reforms/snap_minus_10pct.yaml")
result = apply_reform(units, reform, year=2027)

# After modifying...
reform.to_yaml("reforms/snap_minus_15pct.yaml")
```

For tax-rate reforms, use the `tax_system` key (string identifier from
`TAX_SYSTEM_FACTORY_REGISTRY`):

```yaml
name: sb3125_cd1
tax_system: sb3125_cd1
metadata:
  bill_status: introduced
  citation: HI SB 3125 CD1, conference draft (2026)
```

Combined tax-rate + benefit-overrides specs in one YAML are supported
(see `reforms/sb3125_cd1_with_safety_net_cuts.yaml`).

### Composing reforms

`Reform.compose(*reforms, name=...)` merges multiple reforms with
last-write-wins semantics on conflicting program keys:

```python
snap_cut = Reform.from_yaml("reforms/snap_minus_10pct.yaml")
eitc_doubled = Reform.from_yaml("reforms/eitc_doubled.yaml")
sb3125 = Reform.from_yaml("reforms/sb3125_cd1.yaml")

# All three together
combined = Reform.compose(
    sb3125, snap_cut, eitc_doubled,
    name="full_2027_progressive_package",
    metadata={"author": "fiscal_team", "scenario_version": "v3"},
)
```

The composed reform's `metadata["composed_from"]` automatically lists
the source reform names for provenance.

### Custom tax-system factories

If you've added a HI bill not in `TaxSystemRegistry`, register the
factory at runtime so YAML reform specs can reference it by name:

```python
from tax_modeler import register_tax_system, TaxSystemConfig

def get_my_bill_2028(year: int) -> TaxSystemConfig:
    return TaxSystemConfig(name=f"my_bill_{year}", year=year, ...)

register_tax_system("my_bill_2028", get_my_bill_2028)

# Now `tax_system: my_bill_2028` in any YAML spec resolves to it.
```

### Typed scenario-parameter dataclasses

For sensitivity sweeps and parameter sets, use typed bundles
(`ForecastScenario`, `BehavioralParams`, `TopIncomeParams`,
`MacroScenario`) instead of loose tuples or dicts:

```python
from tax_modeler import ForecastScenario

for s in [ForecastScenario.low(), ForecastScenario.mid(), ForecastScenario.high()]:
    print(s.label, s.top.pareto_alpha, s.behavior.eti, s.macro.reec_demand_scenario)
```

### Smoke-test pattern for new reforms

Phase 6 conventions live under `tests/tax_modeler/smoke/policy/`:

* Test the *qualitative* shape (sign of the delta, monotonicity), not
  exact dollar amounts — synthetic fixture is too small for tight bands.
* Skip cleanly when the fixture has no eligible units for the program.
* Compute SPM resources and assert ordering rather than absolute rates.
* See `test_snap_cut_smoke.py`, `test_eitc_expansion_smoke.py`,
  `test_combined_reform_smoke.py` for the pattern.
