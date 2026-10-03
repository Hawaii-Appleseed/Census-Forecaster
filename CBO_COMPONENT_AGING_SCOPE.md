# CBO Component Aging — Scope

## Motivation

The SOI direct anchor (just shipped) closes the *static* composition gap with ITEP — at $1M+, our wages/CG/business shares now match administrative data per tier. That moved HB 2306 TY2027 from $324M → $331M (Q5: $198M → $206M).

The remaining $36M gap to ITEP's $367M is a *dynamic* problem: we age forward by uniformly scaling AGI (~3.5%/yr above $200K), while ITEP ages each income component at its own CBO-projected growth rate, then re-tots. Capital gains compound faster than wages in CBO projections; the $1M+ tier (where CG is 30-50% of AGI) ends up with materially more income than uniform aging predicts.

Component aging is the most rigorous methodology and is what ITEP, JCT, and CBO itself use for projection. This scope adds it as a third aging mode alongside the existing uniform-growth path.

## The fundamental tension: aggregate anchor vs component aging

Component aging produces an aggregate revenue number that *falls out* of the per-filer math — it is not constrained to match COR's published forecast. ITEP doesn't anchor to a state-level aggregate; they run their model and report whatever it produces.

We anchor to COR for two reasons: (1) the legislature uses COR forecasts, so our Act 46 baseline must match, and (2) COR knows Hawaii-specific labor market conditions that CBO national projections miss (HI wage growth has trailed national for a decade).

**Resolution:** make the COR anchor optional and configurable. Three modes after this work:

| Mode | Aggregate behavior | Use case |
|---|---|---|
| `cor_anchor` (current default) | Hard-anchor to COR aggregate | Fiscal notes, Act 46 baseline |
| `cbo_aged` (new) | Aggregate falls out of components | Bill-specific revenue (HB 2306) where top-bracket dynamics dominate |
| `cbo_aged_hi_calibrated` (new, default for new bills) | Component-aged with Hawaii calibration factors; soft-anchor to COR via post-hoc multiplier (1-3% adjustment max) | Best of both; matches ITEP methodology + respects Hawaii institutional forecast |

## Data needed

### CBO Budget and Economic Outlook (annual + interim)

Source: https://www.cbo.gov/topics/budget/economic-projections

**Specific data:**
- Table 2-3 (or current naming) — Components of Income (Calendar Years), 10-year horizon
- Per-component nominal CAGR for 2024-2034 (TY2024-2031 covers our forecast window)

**Components we need:**

| CBO line item | Maps to PUMS / SOI field | TY2024-2027 illustrative CAGR |
|---|---|---|
| Wages and salaries | `primary_wagp + secondary_wagp` | ~4.5% |
| Proprietor income (nonfarm) | `primary_semp` (Sch C / partnership / S-corp passthrough) | ~5.0% |
| Rental income | imputed from SOI rental_royalty | ~3.5% |
| Personal interest income | `primary_intp` | ~4.0% |
| Personal dividend income | `primary_div` | ~5.5% |
| Capital gains realizations | `synthetic_cg_share × income` (synthesis); SOI bin imputation (mid-tier) | ~7-9% |
| Pension and retirement | `primary_retp + secondary_retp` | ~4.0% |
| Social security benefits | 85% of the benefit, as `income` counts it (`primary_ssp + secondary_ssp`), in the retirement bucket; SSI, public assistance and the untaxed 15% are outside `income` (implemented 2026-09-30; originally `primary_ssp_full`, which inflated income) | ~4.0% |

CBO publishes nominal levels per year, so growth rates derive directly. Interim updates (e.g. CBO Jan 2026 Outlook) refine the forecast — we'd cache the most recent vintage and re-run.

### Hawaii calibration factors (validation step)

CBO is national. Hawaii has documented divergence:

| Component | Hawaii vs national factor | Source |
|---|---|---|
| Wages | ~0.85 of national CAGR | DBEDT historical wage data; HI tourism-heavy economy lags |
| Capital gains | ~1.00 (no adjustment) | HI top filers have national equity exposure |
| Business income | ~0.90 | HI business growth tracks tourism — slightly slower |
| Dividends/interest | ~1.00 | National financial markets |
| Retirement | ~1.00 | SS benefits indexed federally |

Default factors derived from a TY2014-TY2022 HI/national QCEW + DOTAX A8 backtest (work item 5 below). Tunable per-component.

## Implementation

### 1. CBO data fetcher

**New module:** `packages/tax_modeler/src/tax_modeler/calibration/cbo_aging.py`

```python
@dataclass(frozen=True)
class CBOComponentRates:
    """Per-component nominal annual growth rates from CBO Outlook."""
    vintage: str                      # e.g. "2026-01" for Jan 2026 Outlook
    base_year: int                    # CBO's projection base
    rates: dict[str, dict[int, float]]  # {component: {year: cumulative_growth_factor}}

def load_cbo_rates(vintage: str = "2026-01") -> CBOComponentRates: ...
```

**Data file:** `data/tax_modeler/cbo/cbo_components_2026-01.csv`

```csv
component,year,growth_factor_from_2022
wages,2023,1.043
wages,2024,1.089
wages,2025,1.135
...
capital_gains,2027,1.310
capital_gains,2028,1.385
```

Manual fetch from CBO Outlook XLSX; parser extracts the relevant lines. Store as a versioned CSV per Outlook vintage so we can A/B different forecasts. Unlike SOI Table 1.4, CBO publishes interactive workbooks with non-trivial structure — recommend a one-time manual extraction with documented row/column references rather than a generic parser. Re-extract annually.

### 2. Income decomposition extension

Currently we have:
- `primary_wagp`, `primary_intp`, `primary_div`, `primary_retp`, `primary_ssp_full` — set per-filer from PUMS for sub-$1M filers
- `synthetic_cg_share` — set per-filer for $100K-$1M (via `cg_imputation.py`) and $1M+ (via SOI synthesizer)
- `synthetic_wages_share`, `synthetic_business_share` — set on $1M+ synthetic rows by `soi_top_anchor.py` (just shipped)

What's missing:
- `synthetic_business_share` for $200K-$1M filers (PUMS has primary_semp but mid-tier filers don't have full Pareto-style decomposition)
- `synthetic_dividends_share`, `synthetic_interest_share`, `synthetic_retirement_share` for $200K-$1M

**New module:** extend `cg_imputation.py` → `income_composition_imputation.py`

Apply the SOI Table 1.4 per-tier shares (already loaded for $1M+ work) to $200K-$500K and $500K-$1M tiers. SOI Table 1.4 has these tiers explicitly, so it's a one-extra-tier extraction from the existing CSV cache.

```python
def impute_income_composition_from_soi(
    df: pd.DataFrame,
    soi_year: int = 2022,
) -> pd.DataFrame:
    """Set synthetic_*_share for $200K-$1M filers using SOI Table 1.4.
    
    Below $200K: use PUMS-actual decomposition (primary_wagp etc.). The SOI
    Hawaii state file (Table 2) has share data for sub-$200K bins from the
    existing cg_imputation pipeline — extend the same approach to wages,
    business, etc.
    """
```

### 3. Component aging engine

**New module:** `packages/tax_modeler/src/tax_modeler/calibration/component_aging.py`

```python
def age_filers_with_components(
    df: pd.DataFrame,
    target_year: int,
    *,
    base_year: int = 2022,
    cbo_rates: CBOComponentRates | None = None,
    hawaii_factors: dict[str, float] | None = None,  # default HAWAII_CALIBRATION
    soft_anchor_to_cor: bool = True,
    cor_year_target_M: float | None = None,
) -> pd.DataFrame:
    """Age each filer's income components separately, then re-tot to AGI.
    
    For each filer:
        new_AGI = sum_c [
            (income * share_c) * cbo_growth_c * hawaii_factor_c
        ]
    where shares sum to 1 across components (wages, CG, business, dividends,
    interest, retirement, other).
    
    For sub-$200K filers, shares come from PUMS-actual (primary_wagp etc.).
    For $200K+, shares come from SOI Table 1.4 imputation (work item 2).
    For $1M+ synthetic rows, shares are already set by soi_top_anchor.
    
    soft_anchor_to_cor:
        If True, after aging, scale all incomes by a uniform factor so that
        aggregate Hawaii tax (computed via _compute_base_tax) lands near
        the COR projection for target_year. The uniform factor is constrained
        to [0.97, 1.03] — beyond that, fail loudly because component aging
        + Hawaii calibration should already be close to COR. This keeps the
        bracket-distribution shape intact while respecting the COR anchor.
    """
```

### 4. Wire into year_recalibrator

**Modify:** `year_recalibrator.py`

Add a fourth flag mode and reorder Step 1 (currently `project_tax_units_forward` does all aging at once via county B19013 medians):

```python
def project_and_recalibrate(
    ...,
    use_cbo_aging: bool = False,
    cbo_vintage: str = "2026-01",
    cbo_soft_anchor_to_cor: bool = True,
    hawaii_calibration: dict[str, float] | None = None,
    ...,
):
    if use_cbo_aging:
        # Replaces the standard project_tax_units_forward county growth.
        # SOI anchor (Step 5b) and Phase 1 (Step 3) still run — they don't
        # change with aging method.
        units_aged = age_filers_with_components(
            units, target_year=target_year,
            cbo_rates=load_cbo_rates(cbo_vintage),
            hawaii_factors=hawaii_calibration,
            soft_anchor_to_cor=cbo_soft_anchor_to_cor,
            cor_year_target_M=forward.aggregate_tax_M,
        )
        projected = units_aged
    else:
        projected = project_tax_units_forward(
            units, target_year=target_year, base_year=base_year, method=method
        )
    ...
```

The SOI top anchor (Step 5b) remains in the chain — component aging affects how each filer's *own* AGI grows, but the $1M+ filer count target still comes from DOTAX A8 forward projection, and the per-tier composition still anchors on SOI Table 1.4. Component aging applies to the synthetic $1M+ rows like any other rows.

### 5. Hawaii calibration backtest

**New script:** `backtests/cbo_aging_hawaii_factors.py`

For TY2014-TY2022 (the 8-year window where we have both DOTAX A8 actuals and CBO/BEA national components):
1. Run component aging with `hawaii_factors=None` (defaults to 1.0 — pure CBO national).
2. Compare predicted DOTAX bracket totals to actuals.
3. Per-component, fit a multiplicative HI factor that minimizes RMSE across brackets.
4. Cross-validate by holding out one year at a time.

Output: `data/tax_modeler/cbo/hawaii_calibration_factors.json` with per-component HI factors and confidence intervals. Used as default `hawaii_calibration` arg.

### 6. Validation

**New tests:** `tests/tax_modeler/test_component_aging.py`

| Check | Threshold |
|---|---|
| Pure CBO aging (no HI factors) of TY2022 → TY2022 round-trip equals base | exact |
| Aggregate aged AGI grows monotonically year-over-year | strict |
| Hawaii-calibrated aging applied to TY2014 base reproduces TY2022 DOTAX A8 bracket totals | within 5% per bracket |
| Component shares per tier preserved within 2pp after aging | within 2pp |
| Soft anchor to COR converges within 0.97-1.03 multiplier | strict |
| HB 2306 TY2027: SOI anchor + component aging produces $345-365M | within range |

### 7. Acceptance criteria — HB 2306 re-run

| Metric | Pareto (legacy) | SOI anchor (just shipped) | + CBO aging (this scope) | ITEP |
|---|---|---|---|---|
| TY2027 total | $324M | $331M | **$345-365M** | $367M |
| Q5 burden | $198M | $206M | **$215-225M** | $225M |
| Top 1% income growth (TY22→TY27) | ~19% (uniform) | ~19% (uniform) | **~28-32%** | ~30% |
| $1M+ avg CG share (TY27) | 33% (national-95%) | 33% (SOI tier-weighted) | 36-38% (CG grew faster) | not published |

If this lands in the $345-365M band, we're within $5-15M of ITEP — the residual being institutional differences (TCI vs AGI definition, COR-soft-anchor multiplier, ITEP filing-status assumptions).

## Effort estimate

| Step | Time | Risk |
|---|---|---|
| 1. CBO data fetcher (manual extraction + parser) | 2h | low — one-time per vintage |
| 2. Income composition imputation extension | 3-4h | medium — need to handle PUMS-actual + SOI-imputed mix consistently |
| 3. Component aging engine | 4-6h | medium — per-filer math; need to handle negative components (losses) and edge cases |
| 4. Year_recalibrator wiring | 1h | low |
| 5. Hawaii calibration backtest | 4-6h | high — depends on data availability; may need DOTAX A8 multi-year |
| 6. Tests | 2-3h | low |
| 7. HB 2306 re-run + diagnostics + writeup | 2h | low |
| **Total** | **18-24h** | |

The Hawaii calibration backtest is the highest-risk and highest-value piece. Without it, we're applying CBO national rates to Hawaii — which probably overstates wage growth. With it, we have empirical anchors per component.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| CBO national CG growth rate doesn't apply to Hawaii filers | Default Hawaii CG factor 1.00 (top filers have national exposure); keep tunable |
| Component aging breaks aggregate revenue match with COR | Soft anchor with [0.97, 1.03] multiplier; fail loudly if exceeded — signals model drift |
| Income decomposition for $200K-$500K is approximate (SOI tier averages) | Bracket is small enough ($310M HI tax in TY2022, ~10% of total); errors here have limited fiscal impact |
| CBO Outlook revisions change forecasts mid-cycle | Versioned CSV per vintage; can A/B different vintages; document which vintage was used in any fiscal note |
| Negative components (loss carryovers, business losses) confuse aging | Components aged in net-of-loss form (matches CBO definitions); use absolute value with sign preserved |
| Backtest may show poor fit if HI-vs-national divergence is non-stationary | Report fit quality alongside coefficients; if fit is poor, flag uniform aging as the more honest baseline |

## Open questions

1. **CBO Outlook publication cadence vs forecast horizon.** CBO publishes a full Outlook in Jan/Feb and an interim update in summer. For TY2027 forecast in mid-2026, we'd use the Jan 2026 Outlook. Need to document the lag and refresh process.

2. **Should component aging replace or supplement `project_tax_units_forward`?** The current B19013 county-median path is geographic (Honolulu vs Maui growth differentials). Component aging is income-source-specific. They answer different questions. Cleanest: component aging *replaces* the income-growth piece, but keeps county-specific regional adjustments via a small geographic multiplier.

3. **TCI vs AGI growth.** ITEP uses TCI, we use AGI for tax calc. CBO publishes both. For component aging, we'd grow AGI components (since that's what tax calc uses). But for quintile classification we use TCI — should the social-transfer components (SS, public assistance) age separately for TCI? Probably yes, but it's a refinement that affects only Q1-Q3 binning, not HB 2306 revenue.

4. **Year-by-year vs single-step aging.** Current uniform model compounds 5×. CBO publishes per-year levels — we can age TY22→TY27 in one shot or 5 single-year steps. Mathematically equivalent if growth factors are multiplicative; pragmatically use single-step for simplicity.

## Sequence

If you want to ship in stages:

1. **Phase A (1 day):** Fetch CBO Jan 2026 Outlook, build the rate cache CSV, write `load_cbo_rates`. No behavioral change yet — just data plumbing.
2. **Phase B (1 day):** Implement `age_filers_with_components` with `hawaii_factors=None` (pure CBO). Wire into year_recalibrator with `use_cbo_aging` flag, default off. Re-run HB 2306 with flag on; observe likely overshoot to $370-385M (no HI calibration yet).
3. **Phase C (1 day):** Hawaii calibration backtest. Fit factors. Re-run HB 2306; expect $345-365M as scoped.
4. **Phase D (0.5 day):** Tests, writeup, flip the HB 2306 forecast to use_cbo_aging by default.

Phases A+B alone are valuable — they demonstrate the magnitude of the methodology shift before investing in HI calibration. If CBO-aged-no-HI-factor produces $385M, we know the structural gap is real; if it produces $340M, the residual is something else and component aging won't close it.
