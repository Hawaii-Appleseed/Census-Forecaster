# SOI Table 1.4 Direct Anchor for $1M+ Filers — Scope

## Motivation

After fixing the agi/income staleness bug, the honest HB 2306 surcharge estimate is **$324M (Q5: $198M)** vs ITEP's **$367M (Q5: $225M)**. The remaining $43M gap is structural: our $1M+ income composition (CG share, ordinary income share) is Pareto-synthesized with national-ratio CG shares scaled by 95%. ITEP appears to anchor directly on IRS SOI administrative data, which gives the right composition by construction.

This scope replaces Pareto synthesis at $1M+ with a SOI-Table-1.4-anchored direct construction: aggregate filer count, total AGI, wages, capital gains, dividends, interest, business income — all per-tier from IRS published microaggregates.

## What SOI Table 1.4 / 1.6 actually contains

IRS SOI Table 1.4 ("All Returns: Sources of Income, Adjustments, and Tax Items by Size of Adjusted Gross Income") publishes for each AGI bracket:

| Field | Available |
|---|---|
| Number of returns | ✅ |
| Salaries and wages (count × $) | ✅ |
| Taxable interest (count × $) | ✅ |
| Ordinary dividends (count × $) | ✅ |
| Qualified dividends (count × $) | ✅ |
| Net capital gain (count × $) | ✅ |
| Schedule C (business) net income (count × $) | ✅ |
| Partnership/S-corp net income (count × $) | ✅ |
| IRA distributions, pensions, SS, rents, etc. | ✅ |
| Total income, AGI | ✅ |
| Itemized deductions, taxable income | ✅ |
| Income tax before credits, total tax | ✅ |
| Filing status counts (joint/single/HoH/MFS) | ✅ |

**Bracket structure for $1M+:** Table 1.4 publishes
$1.0M–$1.5M, $1.5M–$2M, $2M–$5M, $5M–$10M, $10M+ (sometimes split further).
This is enough granularity to replace our 6-tier Pareto with 5 SOI tiers.

**Caveat — national vs Hawaii:** Table 1.4 is national. Hawaii state Table 2 only goes to "$200,000 or more" as the top bracket. So we need a **Hawaii-from-national** approach:

- **Filer count**: anchor to DOTAX A8 ($1M+ = 1,824 in TY2022; project forward via forward-target rake)
- **Income composition shares** (wages/CG/business/interest): use national Table 1.4 ratios at each $1M+ tier, with optional Hawaii calibration factor
- **Per-filer tax** drops out from `_compute_base_tax` once the composition is right

## Implementation

### 1. Data fetch

**New script:** `packages/tax_modeler/src/tax_modeler/scripts/fetch_irs_soi_table_1_4.py`

```python
# Pulls Table 1.4 XLSX from https://www.irs.gov/statistics/soi-tax-stats-individual-statistical-tables-by-size-of-adjusted-gross-income
# Most recent: 2022 (published Dec 2024). Update annually.
# Output: data/tax_modeler/irs_soi/national_soi_table_1_4_2022.csv
# Schema: agi_lo, agi_hi, n_returns, wages_M, interest_M, ord_div_M,
#         qual_div_M, capgain_M, sch_c_M, partnership_M, ira_dist_M,
#         pensions_M, agi_M, itemized_M, taxable_income_M, tax_before_credits_M
```

**Manual verification step:** Diff the resulting CSV against the published XLSX for the $1M+ rows to catch parsing errors.

### 2. New module: SOI-anchored top synthesizer

**New file:** `packages/tax_modeler/src/tax_modeler/calibration/soi_top_anchor.py`

```python
@dataclass(frozen=True)
class SOITopAnchor:
    """Per-tier composition shares for $1M+ filers from national SOI Table 1.4."""
    tier_lo: float
    tier_hi: float
    n_returns_national: int
    wages_share: float
    capgain_share: float
    ord_div_share: float
    qual_div_share: float
    interest_share: float
    business_share: float       # Sch C + partnership/S-corp
    other_share: float          # residual: pensions, IRA, rents, etc.
    avg_agi: float

def load_soi_top_anchors(year: int = 2022) -> list[SOITopAnchor]:
    """Load $1M+ tiers from cached SOI Table 1.4 CSV."""
    ...

def synthesize_top_filers_from_soi(
    df: pd.DataFrame,
    target_filer_count: int,        # forward-year DOTAX-projected count
    target_tax_M: float,            # forward-year COR-implied $1M+ tax
    soi_anchors: list[SOITopAnchor] | None = None,
    hawaii_capgain_adjustment: float = 0.95,  # DOTAX 21 vs national CG share
    pareto_alpha_for_count_split: float = 1.5,
) -> pd.DataFrame:
    """Replace Pareto synthesis with SOI-composition-anchored synthesis.

    Process:
    1. Drop existing $1M+ weights (same as v2 synthesizer).
    2. Distribute target_filer_count across SOI tiers using each tier's
       national n_returns share (NOT Pareto). This preserves the empirical
       SOI tier shape exactly.
    3. For each tier, build representative filers with:
         - agi = tier.avg_agi
         - wages = avg_agi * wages_share
         - capital_gains = avg_agi * capgain_share * hawaii_capgain_adjustment
         - interest, dividends, business_income — same pattern
         - synthetic_cg_share = capgain_share * hawaii_capgain_adjustment
       Filing-status mix from DOTAX A8 high-income (unchanged).
    4. Tax calibration knob: optional uniform income scale k applied so
       hi_tax_liability totals match target_tax_M after _compute_base_tax.
       (Same pattern as rescale_synthetic_tail_to_tax_target.)
    """
```

### 3. Wire into year_recalibrator

**Modify:** `packages/tax_modeler/src/tax_modeler/calibration/year_recalibrator.py`

Add a third mode flag alongside the existing `resynthesize_top`:

```python
def project_and_recalibrate(
    units, target_year, *,
    use_forward_targets=False,
    resynthesize_top=False,
    use_soi_anchor=False,            # NEW — supersedes resynthesize_top when True
    soi_year=2022,
    hawaii_capgain_adjustment=0.95,
    ...
):
    ...
    if use_soi_anchor:
        # Replaces the resynthesize_top block (Step 5b)
        from tax_modeler.calibration.soi_top_anchor import (
            synthesize_top_filers_from_soi, load_soi_top_anchors,
        )
        anchors = load_soi_top_anchors(year=soi_year)
        fwd_count = int(forward.filer_targets[(1_000_000, np.inf)])
        fwd_tax_M = float(forward.tax_targets[(1_000_000, np.inf)])
        raked = synthesize_top_filers_from_soi(
            raked,
            target_filer_count=fwd_count,
            target_tax_M=fwd_tax_M,
            soi_anchors=anchors,
            hawaii_capgain_adjustment=hawaii_capgain_adjustment,
        )
        raked = _compute_base_tax(raked, tax_year=target_year)
        ...
```

The two flags are **mutually exclusive**: `resynthesize_top` keeps the legacy Pareto path with forward-year sizing; `use_soi_anchor` swaps in SOI-anchored composition. Default both to False (no behavioral change). HB 2306 forecast flips `use_soi_anchor=True`.

### 4. CG imputation interaction

`impute_capital_gains_from_soi` (line 47) computes a $200K-$1M residual share from `(SOI $200K+ total) - (synthesized $1M+ contribution)`. Today it pulls `synth_cg_M` from `synthetic_cg_share * income * weight` for $1M+ rows. With the SOI-anchored synthesis, the per-tier CG shares are different (and more accurate), but the formula still works — just the input numbers change. **No code change needed in `cg_imputation.py`.**

### 5. Validation harness

**New test:** `tests/tax_modeler/test_soi_top_anchor.py`

| Check | Threshold |
|---|---|
| Filer count = DOTAX A8 target | exact |
| Aggregate AGI per tier matches national SOI Table 1.4 within 5% | within 5% |
| Aggregate CG per tier matches national × HI adjustment within 10% | within 10% |
| Filing status mix preserves DOTAX A8 high-income shares | within 2pp per status |
| `_compute_base_tax` produces tax within 2% of target after rescale | within 2% |
| `impute_capital_gains_from_soi` $200K-$1M share differs from current path by <2pp | within 2pp |

### 6. Acceptance criteria — HB 2306 re-run

| Metric | Current (Pareto) | Expected (SOI anchor) | ITEP |
|---|---|---|---|
| HB 2306 TY2027 total | $324M | $340-355M | $367M |
| Q5 burden | $198M | $210-225M | $225M |
| $1M+ filer count | 2,703 (forward target) | 2,703 (unchanged) | ~2,700 |
| $1M+ avg CG share | ~31% (national-95%) | ~33% (SOI tier-weighted, 95%) | not published |
| $1M+ aggregate Hawaii tax | $1,094M | $1,090-1,120M | not directly comparable |

If $324M → $345M, gap to ITEP narrows from $43M to $22M. The remaining ~$22M reflects unrecoverable methodology differences (TCI vs AGI income definition, ITEP's CBO-component aging vs our COR-anchored tax target).

## Effort estimate

| Step | Time |
|---|---|
| 1. SOI Table 1.4 fetcher | 1.5h (includes XLSX parsing, manual diff verification) |
| 2. soi_top_anchor.py module | 2.5h |
| 3. year_recalibrator wiring | 0.5h |
| 4. Tests | 1.5h |
| 5. HB 2306 re-run + diagnostics + writeup | 1h |
| **Total** | **~7h** |

## Risks

| Risk | Mitigation |
|---|---|
| National Table 1.4 composition doesn't match Hawaii's high earners | `hawaii_capgain_adjustment` tunable; DOTAX Table 21 ($400K+ resident-only CG share = 20.9%) provides Hawaii-specific anchor we can extrapolate |
| Filing-status mix at $1M+ in Hawaii ≠ national | DOTAX A8 high-income mix already used (69% MFJ); national Table 1.4 mix is comparable enough that this is a second-order concern |
| Tier boundaries change year to year in published SOI | Cache a snapshot per year; bracket-stable since at least TY2018 |
| Adds dependency on annual SOI release | Fall back to most-recent-available year; SOI publishes with ~2yr lag, fine for 2027+ projection |
| Could introduce regression in non-HB-2306 forecasts | Flag default-off; SB 3125 keeps Pareto path until validated |

## Open question

DOTAX's $1M+ $663M state tax target is resident-only. Our forward `tax_targets[(1M, inf)]` for TY2027 is COR-projected from this baseline. SOI Table 1.4 is filer-of-record (national), which includes Hawaii non-residents with HI-source income on Table 2 only. For composition shares, this distinction doesn't matter much — wages are wages — but for tax calibration, we keep using the COR-derived $1M+ tax target. Document this clearly in the module docstring.
