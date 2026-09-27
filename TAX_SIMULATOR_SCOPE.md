# Tax Simulator (user-defined changes, income tax first) — Scope

**Status (2026-09-26): scoped; Phase 0 done.** The three engine defects
below were fixed and Act 24 republished in PR #24 (middle-scenario five-year
gain vs Act 46 $870.1M → $738.9M; see `SB3125_CD1_FORECAST.md`,
"Scoring-path fixes"). Where this document quotes the pre-fix engine
($80.99M static, $75.44M after response for TY2027) it says so; the fixed
engine gives $58.4M and $53.8M. Phases 1–5 not started. Goal: a page on the estimates
site where anyone can change Hawaiʻi's income tax brackets, rates, standard
deduction and personal exemption and see right away what the change raises or
costs in tax years 2027–2031, and who pays more or less. The numbers come from
the same model and population as the Act 24 page. It runs parallel to
`CONVEYANCE_TAX_SCOPE.md`, which took one bill from scope to page. This
document takes the model itself to the page. The same machinery can take
other taxes later, conveyance first ([last section](#later-other-taxes)).
Working name: "tax simulator". The name is still open (decision 6).

## Bottom line

- **Feasible as a static page, with no server.** The slow part of an estimate
  is building the population's top tail (23 s) and projecting it (17 s per tax
  year), and none of it depends on the policy. Scoring a new schedule against
  the projected population takes 0.5 s in Python. The TY2027 population is
  40,014 records and 0.5 MB compressed, and all five years come to about
  1.5 MB.
- **Checked, not assumed.** A throwaway spike tested this. A JavaScript script
  of about 110 lines read only an exported TY2027 population and the two
  schedules, and imported no Python. It reproduced the Python pipeline's
  Act 24-vs-Act 46 numbers to within 1e-11 $M:
  - Act 46 revenue: $2,643.39M
  - Act 24 revenue: $2,724.38M
  - Static change: +$80.99M
  - After behavioral response: +$75.44M

  These were the published middle-scenario row at the time, from the
  pre-fix engine. One full scoring took about 7 ms in Node. The parity check
  must be re-run against the fixed engine's published figures ($58.4M static,
  $53.8M after response) in Phase 3.
- **Three engine defects had to be fixed first. Done in PR #24.** The largest
  was that the Act 24 scoring path dropped the itemized deduction of every
  filer above $500K. With all three fixed, TY2027's bracket gain is $58.4M
  static and $53.8M after response, down from $80.99M and $75.44M, and the
  five-year gain against Act 46 is $738.9M, down from $870.1M. See Phase 0.
- **Effort: about 10–13 days for v1,** now that Phase 0 is done. v1 covers
  brackets, rates, standard deduction and exemption; revenue by year with a
  range; who pays; and a household calculator.

## What users can change, and what they get

| Control | When | Notes |
|---|---|---|
| Bracket thresholds and rates, per filing status | v1 | Add or remove brackets. A "head of household 1.5×, joint 2× single" link is on by default, the pattern Act 46 and Act 24 both follow |
| Standard deduction | v1 | The current-law schedule (it rises through 2031) or a flat amount by filing status |
| Personal exemption | v1 | Only after Phase 0 defect 3 is fixed |
| First tax year | v1 | 2027–2031. The user's schedule replaces current law from that year on, including Act 24's scheduled 2029 step |
| Behavioral assumptions | v1 | Static, or the Act 24 page's low, middle and high scenarios. They pair taxable-income elasticity (ETI) 0.60 / 0.40 / 0.15 and migration elasticity 0.15 / 0.10 / 0.05 with that page's top-tail and top-income-growth assumptions |
| Capital gains alternative rate (7.25%) | v2 | Needs the capital-gains page's DOTAX-anchored gains base and its statutory alternative tax |
| Credits (food/excise, EITC share of federal, dependent care) | v3 | Needs the working-family credits machinery (take-up calibrated to DOTAX claims) |

Outputs are all measured **against current law (Act 24)**, and every number
says so. The Act 24 page scores against Act 46, a different baseline (see
CLAUDE.md, "Know which baseline a number uses").

- Revenue change for each tax year 2027–2031 and the five-year total, shown
  three ways: static, with the middle behavioral response, and the low–high
  range.
- Who pays: the average change per household by fifth of household income,
  the share paying more or less, and the same by AGI class. These are static,
  like the Act 24 page's tables.
- A household calculator: filing status, income and dependents give the tax
  under current law and under the user's schedule.
- The user's schedule against current law, as a step chart of marginal rates.
- A share link and downloads: the scenario as YAML the Python pipeline accepts
  (below), and the results as CSV. Both are stamped with the model version.

## What exists, and what was measured

**Engine.**
- `TaxCalculator` and `compare_systems` (`config/tax_system_config.py`) do the
  tax math.
- `scenarios/behavioral_response.py` applies the behavioral response.
- `scenarios/quintile_analysis.py` builds the distribution tables.
- `forecast_sb3125_enhanced.py --cd 2` is the Act 24 pipeline that publishes
  `site/data/act-24/`.

Reforms are YAML (`reforms/*.yaml`, `reform/reform.py`). `run_scenario.py`
lists and dispatches the registry scenarios.

**Timings.** Measured on `data/artifacts/sb3125_calibrated_base.pkl` (git
`b70ca4e`), middle scenario, TY2027:

| Step | Time | Depends on the policy? |
|---|---:|---|
| Load the calibrated base (39,990 records, 140 columns) | 1.4 s | no |
| Synthesize the $1M+ tail, impute gains, rescale the tail | 22.8 s | no |
| Project to TY2027 and apply the top-income premium | 16.5 s | no |
| `compare_systems`, two schedules | 0.53 s | yes |
| Behavioral response, then score again | 0.58 s | yes |

**Why a static page can work.**
- Projection changes incomes, never weights. TY2027 and TY2031 weights are
  identical.
- The low and high top-tail variants (Pareto α 1.7 and 1.4) differ from the
  middle one (α 1.5) only in the 24 synthetic records. The other 39,990 are
  identical.
- From year to year, only AGI, the itemized deduction and household-fifth
  membership change.
- Size: one year's 8 columns are 1.04 MB raw and 0.50 MB gzipped. Each further
  year adds 0.22 MB gzipped for AGI and the itemized deduction.

**Hard-coded to SB 3125, so it must be generalized.**
- `apply_migration_response` hard-codes a 2-point rise in the top rate (11% to
  13%) and SB 3125's thresholds ($1M, $750K, $500K).
- `estimate_pte_election_shift_M` hard-codes 13% against 9%. Its
  `pte_capture` is 0 in every scenario because of Act 58's addback, so the
  simulator drops it and says so.
- `apply_behavioral_response(bill_effective_year=2027)` fixes the start year.
- `TaxCalculator.HAWAII_CG_CAP_RATE` is a class constant.
- Tax systems are named registry entries, and their bracket rows are tagged in
  `data/raw/hawaii_tax_brackets_master_all.csv`. `Reform.from_dict` accepts
  only a registered `tax_system` name, so a schedule cannot be passed inline.

## Phase 0 — engine defects (done, PR #24)

Each was found while scoping and checked on the TY2027 middle-scenario
population. All three sit in the scoring path the simulator will reuse. All
three were fixed and Act 24 republished in PR #24; `SB3125_CD1_FORECAST.md`
("Scoring-path fixes") has the final figures. The numbers below are the
measurements that motivated the fixes, each taken with only that defect
corrected, so they do not add up to the final figures.

**1. Itemized deductions dropped above $500K.**
- `apply_top_income_growth_premium` blanks `hi_itemized_deduction` and the
  other `hi_*` columns on every record it scales. This is on purpose, a "loud
  failure" pinned by `tests/tax_modeler/test_premium_clears_tax.py`.
- `forecast_sb3125_enhanced.py` then scores without recomputing tax.
  `calculate_revenue_vectorized` and `per_unit_tax` fill the blanks with 0.
- So all 682 records at $500K+ (about 7,100 filers) are scored with the
  standard deduction only.

Restoring their itemized deductions changes the results. The deductions
average about $220K and were recomputed with the projector's own target-year
parameters.

| TY2027, middle scenario | As published | Itemized restored |
|---|---:|---:|
| Act 46 revenue | $2,643.4M | $2,488.1M |
| Act 24 gain, static | $80.99M | $58.10M |
| Act 24 gain, after response | $75.44M | $53.49M |

This looks unintended, for two reasons:
- The script's own docstring (item 5) says top filers keep their
  projection-time itemized deductions.
- `project_and_recalibrate` (which the capital-gains page uses) and
  `forecast_act24_vs_pre_act46.py` both recompute tax after the premium.

`forecast_sb3125_static_quintile.py` used the same pattern. The fix re-ran
the Act 24 page and updated `SB3125_CD1_FORECAST.md`; the five-year headline
fell from $870.1M to $738.9M.

**2. `per_unit_tax` ignores credits.**
- It reads `result.get("total_credits")`, but
  `HawaiiTaxCredits.calculate_total_credits` returns the key `"total"`.
- So these are before credits, against the function's docstring:
  - the distribution tables' tax levels;
  - the COR scale factor on the distribution path;
  - the levels in `forecast_act24_vs_pre_act46.py`.

  Example: TY2027 Act 46 revenue is $2,798.6M, against $2,643.4M net.
- Differences between two schedules barely move: credits differ by $0.9M
  between Act 24 and Act 46.

**3. One personal exemption per return.**
- `compare_systems` and `per_unit_tax` read a `num_exemptions` column that the
  projected frame does not have. So every return gets one exemption.
- `liability/hawaii.py` counts filer, spouse and dependents: a weighted
  average of 1.88.
- Act 24's TY2027 gain moves only from $80.99M to $81.29M.
- An exemption control would be badly off, though. Doubling the exemption to
  $2,400 costs $34.6M as scored, against $63.8M with the statutory count.
- Smaller, in the same area: the renters-credit table's key is misspelled
  `married_filing_separate`, so separate filers fall back to the $30K default.

## Architecture

**Recommendation: a static page with a JavaScript scoring kernel,
parity-tested against Python.**

```
forecast_sb3125_enhanced.py --cd 2      existing; after Phase 0 it also writes
   └─ runs/income_tax_population/       per-year arrays, manifest, golden results
scripts/build_site.py --import-runs     existing; copies to
   └─ site/data/tax-simulator/<model version>/
site/tax-simulator/index.html           generated shell; without JS it shows current
                                        law and a table of the presets' results
site/assets/simulator/kernel.js         pure scoring, ~300 lines, no dependencies
site/assets/simulator/app.js            the UI; runs the kernel in a Web Worker
tests                                   pytest writes golden results for ~20 specs from
                                        the real pipeline; `node --test` checks kernel.js
```

| Option | For | Against |
|---|---|---|
| **Static JS kernel** (recommended) | No server, cost or uptime to manage. Instant (~7 ms per scoring). User input never leaves the browser. Deploys through the existing Pages workflow | The scoring math exists in two languages. Mitigated: the kernel is small, and CI fails on any drift. It covers bracket tax, deductions, exemptions, the capital-gains cap, the nonrefundable-credit limit, the ETI and migration responses, and aggregation |
| Pyodide (Python in the browser) | One implementation | Roughly 10–25 MB to download (the Python runtime, numpy and pandas) and a start of several seconds, poor on phones. It would still need a slim, dependency-free kernel, the same work as the JS port |
| Hosted API (Cloud Run, Fly, etc.) | One implementation. Can run anything, including the credit overlay and the capital-gains anchoring | Appleseed runs no servers. Cost, operations, abuse limits, and the risk of an outage on a hearing day. Slow even with the same precomputed population: about 1 s per year and scenario today (mostly a per-record Python credit loop), so about 15 s for a full request until that loop is vectorized |
| Precomputed grid of options | No new math | Handles one or two controls. "Change the brackets" is combinatorial |

The public site is deliberately free of JavaScript (see the `scripts/build_site.py`
docstring), so this needs a scoped exception (decision 2):
- First-party code on this one page only.
- No CDN libraries, so "no external requests beyond Google Fonts" still holds.
- Without JavaScript, the page shows current law and a table of the presets'
  precomputed results, so it still says something.

## The policy spec

One JSON/YAML schema serves the page, the share link and the Python pipeline.
It extends `reforms/*.yaml` with an inline block:

```yaml
name: top_14_over_1m
label: "14% above $1 million (joint)"
first_year: 2028                # current law applies before this tax year
income_tax:
  brackets:                     # replaces current law from first_year, incl. Act 24's 2029 step
    single: [[0, 1.4], [14400, 2.5], [19200, 5.0], [24000, 6.4], [36000, 6.8], [48000, 7.2],
             [125000, 7.6], [175000, 8.25], [225000, 9.0], [275000, 10.0], [325000, 11.0],
             [500000, 14.0]]
    head_of_household: {scale: single, factor: 1.5}
    married_filing_jointly: {scale: single, factor: 2.0}
  standard_deduction: current_law   # or {single: 9000, head_of_household: 13500, married_filing_jointly: 18000}
  personal_exemption: current_law   # or dollars per exemption
behavior: [low, mid, high, static]
model_version: "2026-10-xx+<git sha>"
```

Python side:
- `TaxSystemConfig` gains optional inline `brackets` and `standard_deduction`.
- `TaxCalculator.get_brackets` and `get_standard_deduction` return them when
  present. Every reform-scoring path goes through these two lookups, including
  the ETI marginal-rate lookup. The liability engine (`liability/hawaii.py`)
  keeps its own schedules, but it only builds the baseline.
- `Reform.from_dict` builds a factory from the block.
- A `forecast_custom.py --spec` runner scores a spec against Act 24 with the
  full pipeline (low, middle and high; distribution), and `run_scenario.py`
  gets a matching option.

That gives staff a command-line path to any schedule within minutes, before
any page exists.

## The scoring population

The Act 24 run writes it, so the simulator and the Act 24 page always share a
population and a model version.

| Field | Varies by | Type |
|---|---|---|
| Weight; household weight | record | float32 |
| Filing status; dependents; household index | record | uint8, uint8, uint16 |
| Capital-gains share of income | record | float32 |
| AGI before the top-income premium; itemized deduction; household fifth | record × year | float32, float32, uint8 |
| Synthetic top-tail records for α = 1.4, 1.5, 1.7 | 24 records × variant × year | float32 |
| Current law (schedules, standard deductions and exemption by year); food/excise tables | — | JSON |
| Golden results for the presets | — | JSON |

**Size.**
- About 1.5 MB as a gzipped binary decoded in the browser
  (`DecompressionStream`). We compress it ourselves because GitHub Pages does
  not compress binary files.
- About 3 MB as gzipped CSV.

**Disclosure.** Every input is public-use (ACS PUMS) or a published aggregate
(IRS SOI, DOTAX). `SERIALNO` and other identifiers are dropped anyway.

**Not shipped in v1:**
- The 80 replicate weights, which would give sampling error but add 6–13 MB.
- The recession path.

## The kernel

It runs for each year and scenario, about 15 scorings in all. Each takes about
7 ms in Node, with a budget of about 50 ms on a phone. For each scoring:

1. **Top-income premium.** Apply the scenario's premium to records at $500K+:
   1.0% a year for the middle scenario, 0.3% for low, 2.3% for high,
   compounding from 2024.
2. **Tax under current law and under the spec.**
   - Deduction: the greater of the standard deduction and the itemized
     deduction.
   - Exemptions: the statutory count.
   - Bracket tax, with the 7.25% cap on the capital-gains share.
   - Credits held at current law, with nonrefundable credits limited to tax.
3. **Behavioral response.**
   - ETI: each record whose marginal rate rises has its income multiplied by
     ((1 − t₁)/(1 − t₀))^ε.
   - Migration, generalized from the Act 24 form (decision 4).
   - Then score again.
4. **Aggregate.**
   - Revenue by year.
   - Static distribution by household fifth and AGI class. As in
     `generate_quintile_report`: sum by household, use the filer weight for
     dollars and the household weight for counts.

**Parity.** After Phase 0, a pytest writes golden outputs from the real
pipeline for about 20 specs: the presets plus random valid schedules. A
`node --test` step in `.github/workflows/tests.yml` must then match:
- $0.01M on totals;
- one cent on each record's tax.

This is a separate CI step rather than a pytest that skips when Node is
missing, since CLAUDE.md allows no new skips. One golden case is
the committed Act 24 page itself. With the baseline set to Act 46 and the spec
to Act 24, the kernel must reproduce the middle-scenario static and
after-response rows of `site/data/act-24/fiscal_by_scenario.csv`. In the
spike, it already did.

## The page

```
┌───────────────────────────────────────────────────────────────────────────┐
│ Try it yourself · Income tax                                               │
│ Start from: [Current law (Act 24) ▾]   Presets: Act 46 · SD1 · HB 2306 ·…  │
├──────────────────────────────────────┬────────────────────────────────────┤
│ 1 Brackets   [Single][Head][Joint]   │ Change vs current law (Act 24)     │
│   From        Rate                   │  +$__M in TY2028 (range $__–$__M)  │
│   $0          1.40%                  │  +$__M over 2028–2031              │
│   $14,400     2.50%                  │  [columns by year: static | with   │
│   …                                  │   response, low–high whiskers]     │
│   $500,000   13.00% → [14.00]%       │ Who pays                           │
│   [+ add bracket]                    │  [bars by fifth · % paying more]   │
│   ☑ Head 1.5×, joint 2× single        │ A household like yours             │
│ 2 Standard deduction [current law ▾] │  [status][income][children] → $±   │
│ 3 Personal exemption [$1,200]        │ Your rates vs current (step chart) │
│ 4 First tax year     [2028 ▾]        │ [Copy link] [Scenario .yaml] [CSV] │
│ 5 Behavior (•) middle ( ) low ( ) hi │                                    │
└──────────────────────────────────────┴────────────────────────────────────┘
```

- **Two input modes.** Quick mode has sliders for the top rate, the top
  threshold and the standard deduction. Full mode has an editable schedule
  table for each filing status, with rows that can be added or removed.
- **Presets:**
  - current law;
  - Act 46's schedule;
  - SB 3125 SD1;
  - HB 2306 HD1;
  - an illustrative 1-point surcharge above $1M (joint), which is just one
    more bracket.

  The first two let users check the tool against the Act 24 page. SD1 and
  HB 2306 HD1 are bills already defined in the model's registry. One caution:
  the Act 46 preset reproduces the Act 24 page's static figure with the sign
  reversed, but not its after-response figure. Scored against Act 24, Act 46
  is a rate cut, and the model gives rate cuts no behavioral response.
- **Live results.** Results update as the user types (with a short delay). The
  headline sits in an aria-live region so screen readers announce it, and
  every chart has a table. The styling matches the existing pages: CSS bars
  and the brand tokens in `site.css`.
- **Share links.** The link carries the spec and the model version in the URL
  fragment. An old link loads its own model version while that version is
  still hosted. Otherwise it warns that the numbers have been updated since.
- **Credit.** The page is credited to Hawaiʻi Appleseed, like every page on
  the site.
- **Home page.** Add a "Try it yourself" section beside "Current policy" and
  "Proposed policy". `tests/site/test_site_build.py` currently counts every
  `site/*/index.html` as an estimate, so it needs updating.

## Guardrails

- **Validate input.** Thresholds must rise from $0, and rates must be 0–25%.
  Warn above 15%, or when rates fall as income rises.
- **Say when the response is extrapolated.** The behavioral assumptions were
  set for top-rate changes of a few points (11% to 13%).
- **The top tail is thin.** 70 records stand for the roughly 2,300 returns
  above $1M. Always show the low–high range, and don't headline a single
  number for changes that touch only $1M+.
- **Rate cuts get no behavioral response,** because the ETI module is
  asymmetric by design. The page says so whenever a spec cuts rates.
- **State the limits.** Distribution is static. Credits stay at current law.
  Capital gains keep the 7.25% cap on the model's own gains base. The
  capital-gains page found that base puts about 30% too much at the top, so v1
  slightly understates what top-rate increases raise. v2 fixes this.
- **Label every result:** "Hawaiʻi Appleseed model estimate against current law
  (Act 24); not a Department of Taxation fiscal note", plus the model version.

## Phases and effort

| Phase | Work | Days |
|---|---|---:|
| 0 | Fix the three defects; re-run Act 24; update `SB3125_CD1_FORECAST.md` and the page (done, PR #24) | — |
| 1 | Inline schedules in `TaxSystemConfig` and `Reform`; generalize migration, the PTE shift and the effective year; `forecast_custom.py --spec` scoring against Act 24 | 2 |
| 2 | Population export from the Act 24 run; import into `site/data/`; manifest; size budget | 1 |
| 3 | `kernel.js`; golden-fixture generator; `node --test` in CI | 2 |
| 4 | The page: editor, presets, results, distribution, household calculator, share and download, no-JS fallback, mobile, accessibility | 4–6 |
| 5 | Methodology section, QA against the published pages, staff review | 1–2 |
| **Total remaining** | | **10–13** |

Phase 1 is useful alone: it lets staff score any schedule from the command
line.

## Risks

- **Parity drift between Python and JavaScript.** The golden fixtures change
  whenever the model does, and CI fails until `kernel.js` matches. The kernel
  either keeps up with the model or the page does not deploy.
- **Numbers move between model runs.** The same spec gives different results
  after a re-run. The data paths and links are versioned for this reason.
- **A single number gets quoted out of context.** The page shows the range by
  default and carries the guardrail text above.
- **Scope creep.** Credits and capital gains could delay v1. They are v2 and
  v3.

## Decisions needed

1. ~~Fix Phase 0 and republish Act 24 before the simulator, or build against
   the current numbers?~~ Settled: fixed and republished first (PR #24).
2. May the site add first-party JavaScript, on this page only?
3. Behavioral response to rate cuts: keep none, as the model does today and as
   the published numbers assume, or make it symmetric?
4. How should migration generalize? The options:
   - scale by the change in the top statutory rate, keeping today's tiers:
     full response at $1M+ AGI, half between the new top threshold and $1M.
     This reproduces Act 24 exactly;
   - scale by each filer's own change in marginal rate. This also responds to
     increases below the top.
5. v1 controls as listed, with capital gains in v2 and credits in v3?
6. The page's name, and where it sits on the home page.
7. Confirm the current-law personal exemption before the page shows it as
   current law. The model uses $1,200 for TY2025 on (`liability/hawaii.py`
   says Act 46 raised it from $1,144). Check against HRS §235-54.

## Later: other taxes

Each follows the same pattern: published base data, a block in the spec, a
kernel, parity tests and a panel on the page.

- **Conveyance (next).**
  - The schedules are already data (`conveyance.py`: `MarginalSchedule` and the
    cliff tables).
  - The base is band aggregates already on the site (`county_bands.csv`,
    `maui_by_band.csv`).
  - The published estimate (`forecast_conveyance_sb3028.py`, PR #25) is no
    longer a single ε applied to static tax. Its `Behavior` model has
    separate channels: the lasting sales response (ε 6, with a first-year
    bump and curvature), a developer ramp over five years, part of the tax
    passed into prices, buyers shifting to the owner-occupant rates, and
    sales of companies in place of homes. The range comes from a 1,000-draw
    simulation over those priors and each county's sales by price.
  - The kernel therefore sums count × tax(price) by price band, buyer
    category and county, applies those channels, then the §247-7
    disposition. It must reproduce the page's central estimate
    (`site/data/conveyance-tax/revenue_by_year.csv`) as a golden case.
  - The range is the hard part. Either run the simulation in the browser
    (1,000 draws over the band aggregates, likely fast enough in a Worker),
    or show the central estimate live and scale the published 5–95% band,
    and say so. Decide before building.
  - About 5–7 days once the framework exists, more than the earlier 3–4,
    because of the behavior channels and the simulation.
- **Capital-gains rate.** Use the DOTAX-anchored gains base and the statutory
  alternative tax from `forecast_cg_rate_options.py`.
- **Credits.** Food/excise tables, the EITC share of federal, and dependent
  care, using take-up from the working-family credits pipeline.
- **General excise tax.** There is no microdata for it, so it needs its own
  scope.
