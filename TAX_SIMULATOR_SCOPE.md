# Tax Simulator (user-defined changes, income tax first) — Scope

**Status (2026-09-27): v1 (Phases 0–5) built and published (PR #28); v2,
the capital gains rate, built and published (PR #29).** The simulator is
live at `site/tax-simulator/`. See [As built](#as-built-september-26-2026)
for what shipped, where it departs from this scope, and the decisions
taken, and [v2](#v2-capital-gains-september-27-2026) for the capital gains
rate. v2 scores capital gains on the capital-gains page's DOTAX-anchored
base with the statutory alternative tax; on September 28 the Act 24 page was
re-based the same way, so the two agree again on Act 24 itself (middle
scenario, TY2027, after response: $52.4M; the Act 24 page was $46.4M and its
five-year gain against Act 46 went from $726.4M to $748.9M — see
`SB3125_CD1_FORECAST.md`, "Act 24 re-based on the DOTAX-anchored
capital-gains base"). The three engine defects below were fixed and Act 24 republished
in PR #24 (middle-scenario five-year gain vs Act 46 $870.1M → $738.9M; see
`SB3125_CD1_FORECAST.md`, "Scoring-path fixes"). On September 27 the
behavioral response was rescored for the simulator and the Act 24 pipeline
alike (standard counterfactual, literature-consistent migration elasticity;
`BEHAVIORAL_ACCOUNTING_REVIEW.md`, decision 8). That moved the Act 24 page's
TY2027 bracket gain after response from $53.8M to $46.4M (static stays
$58.4M) and its five-year gain from $738.9M to $726.4M. Where this document
quotes the pre-fix engine ($80.99M static, $75.44M after response for
TY2027) it says so. The rest of this document is the scope as written
before the build. Goal: a page on the estimates
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
  pre-fix engine. One full scoring took about 7 ms in Node. The built kernel
  is parity-tested against the fixed engine instead (see As built).
- **Three engine defects had to be fixed first. Done in PR #24.** The largest
  was that the Act 24 scoring path dropped the itemized deduction of every
  filer above $500K. With all three fixed, TY2027's bracket gain is $58.4M
  static and $53.8M after response, down from $80.99M and $75.44M, and the
  five-year gain against Act 46 is $738.9M, down from $870.1M. See Phase 0.
- **Effort, as scoped: about 10–13 days for v1** once Phase 0 was done. v1
  covers brackets, rates, standard deduction and exemption; revenue by year
  with a range; who pays; and a household calculator. v1 and v2 are now
  built (PRs #28 and #29).

## As built (September 26, 2026)

This section is v1, the income tax. [v2](#v2-capital-gains-september-27-2026),
the capital gains rate, follows it and changes some of what it says about
capital gains and the presets' figures.

**What exists.**
- **Engine (Phase 1).** `TaxSystemConfig` takes inline `brackets` and
  `standard_deductions`. `TaxCalculator.brackets_for` and
  `standard_deduction_for` are the single lookups every scoring path uses,
  including the ETI marginal rate and `forecast_cg_rate_options.py`. The spec
  lives in `reform/income_tax_spec.py` (`IncomeTaxSpec`, `load_spec`), and
  `Reform.from_dict` accepts an `income_tax:` block scored against Act 24.
  Migration and the PTE shift now read the top-rate change from the two
  systems (`top_rate_changes`) instead of Act 24's constants. With Act 24 as
  the reform they give bitwise the same numbers as before, and
  `forecast_sb3125_enhanced.py` still reproduces the published Act 24 files
  byte for byte. It now builds its population through
  `scenarios/act24_population.py`, which the simulator shares.
- **Command line.** `forecast_custom.py --spec <file>` (or
  `run_scenario.py --spec <file>`) scores a YAML or JSON spec for the low,
  middle and high scenarios, 2027–2031, and writes revenue, quintile and
  income-class tables to `runs/custom/<name>/`. Examples:
  `reforms/examples/*.yaml`.
- **Population (Phase 2).** `scripts/build_simulator_population.py` builds
  the scoring population into `runs/tax_simulator/` in about 10 minutes. It
  stops unless the population, scored for Act 24 against Act 46, reproduces
  the published Act 24 run (all three scenarios' revenue at the published
  two decimals of $M, and the middle distribution to 1e-9). It also writes the web files,
  the golden fixture and the presets' results. `scripts/build_site.py
  --import-runs tax-simulator` copies them into `site/data/tax-simulator/` and
  `tests/simulator/golden.json.gz`.
- **Kernel (Phase 3).** `site/assets/simulator/kernel.js` runs in a module Web
  Worker (`worker.js`). `tests/simulator/kernel.test.mjs` checks it against
  the golden fixture: 29 plans (Act 24 vs Act 46, the presets, edge cases and
  random schedules) for revenue in every scenario and year, the distribution
  tables, a sample of records' tax before and after credits, 480 calculator
  households, spec resolution, 55 invalid specs the model rejects and a set
  of unusual valid ones it accepts. CI runs `node --test tests/simulator/*.test.mjs` in the
  smoke job.
- **Page (Phase 4).** `site/tax-simulator/`, "Tax Simulator", in a new "Try it
  yourself" section on the home page. Editor state and share links are in
  `plan.js`, which `tests/simulator/plan.test.mjs` tests in Node; the DOM is in
  `app.js`. Without JavaScript the page shows the presets' precomputed results.

**Where it departs from the scope.**
- **Size: 2.35 MB gzipped, not 1.5 MB.** Money is stored as float64, not
  float32, so the kernel matches Python exactly. The low and high scenarios
  are stored as overrides of the middle one: only the records whose AGI,
  itemized deduction, weight or gains share differ.
- **The population ships projected values.** The kernel does not apply the
  top-income premium itself. The build projects each scenario and year in
  Python, which keeps one implementation of the projection.
- **Parity is tighter than planned:** 1e-6 relative on every $M total and
  1e-9 on each record's tax, not $0.01M and one cent.
- **Speed.** A full scoring (3 scenarios × 5 years, two passes each for the
  behavioral response, plus one year's distribution) takes about a quarter
  of a second, in Node and in the browser (the first scoring after the page
  loads is slower while the engine warms up). The spike's 7 ms was one
  scoring.
- **Controls.** Quick changes are number fields for the top rate and where it
  starts, not sliders. The editor keeps current law's two bracket periods
  (2027–28 and 2029 on); from a first year of 2029 or later only one applies.
  All three behavioral scenarios are always shown, with static beside them,
  rather than chosen with a radio button.
- **Presets:** current law, Act 46's brackets, SB 3125 SD1, HB 2306 HD1, and
  "top rate 14% (was 13%)", which is the scope's 1-point surcharge above $1M
  joint.
- **Downloads** are the spec as JSON (which `forecast_custom.py` accepts)
  and the results as CSV, not YAML.
- **One data version is hosted.** Data sit in `site/data/tax-simulator/`
  rather than a folder per model version. The model version is the
  calibrated base's date plus a hash of the population, its metadata and
  `kernel.js`, so any rebuild that could change a result changes it. A link
  made under an earlier version still opens, with a notice that the numbers
  have been updated since.
- **The range spans all three scenarios.** The scenarios are named for their
  effect on Act 24's gain. For a rate cut such as the Act 46 preset, the
  middle figure falls outside low-to-high, so the page shows the lowest and
  highest of the three.
- **Not in share links:** the distribution year and the calculator household.

**Decisions taken** (numbered as in [Decisions needed](#decisions-needed);
each was the recommended option):
1. Phase 0 was fixed and Act 24 republished first (PR #24).
2. First-party JavaScript on this page only. No CDN libraries; the rest of the
   site stays free of JavaScript.
3. Rate cuts get no behavioral response, as the model already does. The page
   warns whenever a plan lowers the top rate.
4. Migration keeps Act 24's tiers and scales with the change in the top
   statutory rate. Rate cuts get none. Three refinements, none of which
   moves Act 24's numbers: each rise phases in from the year it takes effect
   (not from the plan's first year); the half tier starts no lower than
   current law's top-bracket floor; and no group loses more than all of its
   weight.
5. v1 controls as listed; capital gains in v2, credits in v3.
6. Name "Tax Simulator", slug `tax-simulator`, in a "Try it yourself"
   section of the home page.
7. **Still open:** the $1,200 personal exemption is not yet checked against
   HRS §235-54. The page shows it as current law.
8. **Resolved September 27, for this page and the Act 24 page together.**
   The after-response figures had scored current law on the population
   after it responded, so a filer who moved away cost only the rate increase
   on their income. And the migration elasticity removed 20% of $1M+ filers
   for a 2-point rise, about a hundred times the evidence. Both changed at
   once (`BEHAVIORAL_ACCOUNTING_REVIEW.md`):
   - the plan is now scored on the responded population and current law on
     the population as it is (`score_with_response`, mirrored in
     `kernel.js`);
   - the migration elasticity is 0.01 / 0.0025 / 0.001 per point for the
     low / middle / high scenarios, where it was 0.15 / 0.10 / 0.05.

   Changing only the accounting would have made Act 24 lose money in the low
   scenario.

**Numbers for the presets in v1** (change vs Act 24, $M, with response; as
rescored on September 27, decision 8, with the September 26 figures in
brackets; [v2](#v2-capital-gains-september-27-2026) moves them again):

| Preset | TY2027, middle | 2027–2031, middle | 2027–2031, low / high |
|---|---:|---:|---:|
| Act 46's brackets | −58.6 [−58.4] | −356.2 [−353.7] | −364.1 / −389.5 [−360.2 / −388.6] |
| SB 3125 SD1 | −43.2 [−42.9] | +440.7 [+460.1] | +417.9 / +429.1 [+447.0 / +436.4] |
| HB 2306 HD1 | +223.3 [+230.4] | +1,830.1 [+1,883.6] | +1,787.4 / +1,866.2 [+1,867.7 / +1,886.2] |
| Top rate 14% | +32.1 [+36.9] | +171.5 [+195.9] | +140.0 / +207.4 [+193.7 / +216.1] |

In v1 the Act 46 row's static figure was the Act 24 page's static gain with
the sign reversed (−$58.36M in 2027). Since v2 it is Act 24's static gain
scored the simulator's way, reversed, which is no longer the page's figure
(−$64.55M in 2027). Its with-response figure differs from the Act 24
page's, because scored this way round it is mostly a rate cut (decision 3):
the response comes only from the lower brackets whose rates rise. Most of
the September 27 change in these rows is the ETI's fuller accounting (the
plan's rate on income reported away, not just the increase); migration
matters only in the 14% row.

### v2: capital gains (September 27, 2026)

Built on `feat/tax-simulator-cg-v2`; not yet merged. The user can now set
the rate of the alternative tax on net long-term capital gains.

**What exists.**
- **Spec.** `income_tax.capital_gains_rate` takes `"current_law"` (the
  default when the key is absent), a rate in percent from 0 to below 100, or
  `"ordinary"`, which drops the alternative tax so gains are taxed at the
  bracket rates. One rate applies from `first_year` on. A present `null`,
  other strings, booleans, lists and objects are rejected, with the same
  messages in Python and `kernel.js`. The spec's `changes_anything` and the
  kernel's `resolveSpec` count the key, so a plan that changes only the gains
  rate is not scored as current law. Links without the key read and write
  as before (with the usual notice that the numbers have been updated). New presets `cg_9` and `cg_ordinary` (the capital-gains page's
  two options) and examples `reforms/examples/cg_9.yaml` and
  `cg_ordinary.yaml`. `forecast_custom.py` scores the same base as the page.
- **Gains base.** The simulator scores the capital-gains page's
  DOTAX-anchored base. Each class's resident net long-term gains eligible for
  the alternative tax (DOTAX Table 21, TY2022) are grown to the tax year with
  the Hawaiʻi-adjusted CBO capital-gains factor. Classes map onto the
  population by weighted income rank, and in the middle scenario 80
  percent of the $400K+ class goes to $1M+ filers. The model's own shares
  set the split within each class. **DOTAX anchors the middle scenario; the low and high scenarios
  keep its factors.** Each class's scale factor k (DOTAX's gains over the
  model's, per class and year) comes from the middle scenario, and the low
  and high scenarios apply the same k to their own classes (their own
  income ranks) and their own model gains. DOTAX corrects how the model
  spreads gains across income classes; the low and high scenarios express
  uncertainty about top incomes (the tail's shape and the top-income growth
  premium), and gains should move with that. So only the middle scenario
  holds DOTAX's totals: in TY2027 the low / middle / high scenarios have
  $3,832.7M / $4,110.7M / $4,382.0M of gains (93 / 100 / 107 percent of
  DOTAX's; 92 to 111 percent over 2027–2031), with 77.5 / 80.0 / 81.9
  percent of the $400K+ class's at $1M+. The first v2 build pinned every
  scenario to DOTAX's totals; see the departures below for why that
  changed. The anchoring code moved unchanged from
  `forecast_cg_rate_options.py` to `tax_modeler.calibration.cg_anchor`; the
  page's CSVs rebuild byte for byte. `simulator/gains.py`
  (`ensure_gains_anchor`) anchors the saved population in 0.3 s in every
  build mode, so the population was not rebuilt. It stores each record's
  class (`cgcls_<year>`, with low and high overrides) and the scale factors
  k per class and year (`meta.cg_anchor`, listed for each scenario so the
  kernel reads them the same way; the low and high lists equal the middle
  one's). A record's gains share is min(1, model share × k of its class),
  after the low and high tail overrides. The rank-based class under $100K
  gets no gains, as on the page.
- **Tax.** The alternative tax of HRS §235-51(f) as the statute defines it
  for schedules whose rates rise with income (`liability/cg_alternative.py`):
  the lower of the regular tax and the bracket tax on the greater of taxable
  income less gains and the taxable income taxed below the gains rate, plus
  the gains rate on the rest. The model takes that income as the floor of the
  first bracket whose rate reaches the gains rate (`cap_floor`, `kernel.js`
  `capFloor`), recomputed for each filing status's schedule in force each
  year. With rates that never fall, the brackets below the gains rate all lie
  under that floor, so the two are the same, as for current law, every preset
  and every bill scored. Where a rate falls back below the gains rate after
  one reaches it, the literal sum of the income in below-rate brackets can
  differ (more or less tax: `test_cg_alternative.py` has two hand cases), and
  the literal reading is no cleaner, since (1) then taxes that sum from zero
  through the higher brackets. The model keeps the first-bracket reading, and
  the page says so: a `falling_cg` warning (`plan.js`) for such plans, and the
  Capital Gains section.
  `TaxSystemConfig.cg_alt_tax` is `"statute"` for every spec system
  (`current_law_system`) and stays `"stacked"`, the old
  min(bracket tax on gains, 7.25% × gains) shortcut, for every registry
  system. The shortcut accepts no rate but 7.25%. A rate at or above every
  bracket rate taxes gains as ordinary income; 0 leaves them untaxed.
- **Behavior.** A realization response runs after ETI and migration and
  before the PTE estimate (`apply_realization_response`, mirrored in
  `kernel.js`'s `behave`). A filer's gains are multiplied by exp(−β·d), where
  d is the rise in their marginal rate on gains, with both rates read from
  the plan's schedule at income before any response. β
  (`BehavioralParams.cg_beta`) is 2.6 / 2.0 / 1.6 in the low / middle / high
  revenue scenarios and 0 in static. It runs only when both systems use the
  statute and their gains rates differ, so a plan that changes only the
  brackets scores exactly as it would without it. A cut in the gains rate
  gets no response, the gains rate moves no one away, and the accounting is
  the standard counterfactual as before.
- **Data.** Web format 2: `population.bin.gz` grows from 2,349,070 to
  2,401,280 bytes. `decodePopulation` refuses any other format,
  `unitArrays` throws without the anchor, and `prepareSystem` throws on a
  system without a gains rate; nothing falls back to the model's base. A
  full scoring takes the same time as before (top rate 14%: median 110 ms
  in Node against v1's 113 ms).
- **Page.** A "Capital gains" fieldset right after Quick changes: current
  law, a different rate (0 to 99.99, step 0.25), or ordinary income. When a
  plan changes both the income tax and the gains rate, Table 1 adds a middle
  scenario row, "Of which, capital gains rate": the plan less the same plan
  at current law's gains rate, interaction included (the middle scenario's
  five years scored once more); the CSV has it by year. The rate chart draws the gains rate as a dotted line,
  the household calculator takes gains in dollars, and four warnings cover a
  rate that never binds (`cg_inert`), a cut (`cg_cut`), a rise beyond
  taxing gains as ordinary income under current law (`cg_large`) and the
  response to any rise (`cg_response`). A fifth (`falling_cg`) names the
  model's reading of the statute when a rate falls back below the gains rate
  (current law's, or the plan's) after one reaches it (see **Tax** above). A new "Capital Gains" section of the
  methodology states the base, the statute, β, what is left out and the gap
  to the Act 24 page, all read from the committed data.
- **Checks on every build** (`scripts/build_simulator_population.py`, all
  three modes):
  - `check_reproduces_act24`, still exact. Since the Act 24 page was
    re-based (September 28) it scores the anchored base with the statute,
    the way that page does, and also checks that the page's run recorded
    the same scale factors per class and year.
  - `check_gains_anchor`: in the middle scenario every class total, every
    year, equals DOTAX's TY2022 gains grown with `cg_growth`, and the
    totals and class targets equal the capital-gains page's `nltcg_M` and
    `anchor_check.csv`, at a relative 1e-9. The low and high scenarios' k
    equal the middle scenario's exactly, and each of their class totals
    equals that k times their own model gains in the class, with the
    classes recomputed from their income ranks (1e-9). In every scenario
    no share is capped at 1 (the largest anchored share is 0.43) and no
    gains sit on income of zero or less.
  - `check_cg_page_parity`, written to `cg_page_comparison.json`: for the
    page's two options, middle scenario, 2027–2031, the simulator's path
    matches the page's `score_option` method on the same records (1.1e-14
    relative), its before-credit levels are 0.935–0.966 of the page's
    published figures (bound 0.90–1.02), and its response ratios are within
    0.0032 of the page's (bound 0.005). The levels cannot match: the page
    projects its own population (`project_and_recalibrate`, with its own
    $1M+ tail).
- **Tests.** The golden fixture grows from 29 plans to 45, 480 households to
  800, 55 invalid specs to 64 and 6 unusual valid ones to 11, with a
  record-by-record check after the response (`top14_cg9`, middle scenario,
  TY2031). `tests/simulator/capital_gains.test.mjs` checks the committed
  data against the capital-gains page's CSVs and that the low and high
  scenarios keep the middle scenario's factors,
  `tests/site/test_tax_simulator_version.py` recomputes `model_version` from
  the committed data and `kernel.js`, and `tests/tax_modeler/` adds
  `test_cg_alternative.py` and `test_cg_anchor.py`. The Node suite runs 136
  tests (84 in v1).

**Where it departs from the scope and the v2 design.**
- **The Act 24 page was not moved to the new base** (decision 1 below), so
  "the same model and population as the Act 24 page" now holds for
  everything except capital gains. Scored the simulator's way, Act 24's
  bracket gain against Act 46 is, in $M:

  | Scenario | TY2027 static | TY2027 after response | 2027–2031 after response | Yearly gap after response |
  |---|---:|---:|---:|---:|
  | Low | 65.32 [60.26] | 45.10 [40.23] | 218.28 [202.35] | +1.53 to +4.94 |
  | Middle | 64.55 [58.36] | 52.42 [46.37] | 292.60 [270.12] | +3.04 to +6.11 |
  | High | 65.88 [58.75] | 61.08 [54.01] | 379.93 [354.05] | +3.02 to +7.07 |

  The Act 24 page's figures are in brackets. The statute alone moves almost
  nothing: in the middle scenario, TY2027, on the model's base, it lowers
  the Act 46 baseline from $2,456.4259M to $2,456.4200M and leaves Act 24's
  static gain at $58.3642M (`calculate_revenue_vectorized` on
  `frame_for(pop, 2027, "mid", gains="model")`, stacked against statute).
  That is still enough to break the build check's two-decimal match, one
  reason the registry systems keep the shortcut. The base moves the gain,
  to $64.55M. The gap is measured on every build and stated on the page.
- **The low and high scenarios keep the middle scenario's gains factors**
  instead of each being pinned to DOTAX's totals, as the first v2 build
  did (decision 3). Pinning pushed all of a scenario's extra top income
  into ordinary income. Act 24 against Act 46, five years static, then ran
  $341.8M / $376.0M / $453.5M (low / middle / high), 0.91 / 1 / 1.21 times
  the middle scenario, where the Act 24 page runs $359.7M / $353.3M /
  $388.5M, or 1.02 / 1 / 1.10. With the middle scenario's factors it is
  $376.7M / $376.0M / $414.5M, or 1.00 / 1 / 1.10, the page's pattern. A
  plan that changes only the gains rate also got almost no range: the
  9 percent rate's TY2027 static gain was $55.6M / $55.7M / $55.8M, and is
  now $50.7M / $55.7M / $60.6M, a range from top incomes as well as from
  β. Every low and high figure in this section uses the new rule; no
  middle-scenario figure changed.
- **The gains material is its own page section,** not a longer caveat, and
  adds the comparison with the capital-gains page: the simulator's figures
  for that page's two options run 3 to 7 percent below it.
- **The "of which" row shows five-year totals** for the middle scenario
  only, with dashes under low and high.
- **The web addition is 52 KB gzipped,** not the 43 KB the design
  estimated, and anchoring takes 0.3 s, not 3 s.
- **Known inconsistency, left as the page has it.** The $1M+ tail is scaled
  to DOTAX's $663M tax target on the model's own gains, and anchoring then
  changes those filers' gains. The capital-gains page does the same, which
  keeps the two comparable; fixing it belongs with the tail rescale itself,
  not with the base.

**Decisions taken** (each the recommended option):
1. ~~The Act 24 page stays on the model's own gains base~~, so that
   `forecast_sb3125_enhanced.py`, `forecast_act24_vs_pre_act46.py`, the Act
   24 page and the build's exact check were unchanged by v2. **Reversed on
   September 28, 2026:** both scripts now score the anchored base with the
   statute, so one capital-gains treatment serves every page. The registry
   systems still default to the stacked shortcut; the pipelines wrap them
   with `act24_population.statute`. `forecast_sb3125_vs_fy26base.py` has not
   moved.
2. β by scenario: 2.6 / 2.0 / 1.6 for the low / middle / high revenue
   scenarios, the capital-gains script's elasticity range of 0.5–0.8 divided
   by the combined top rate of about 31%. The middle value is that page's
   β of 2.
3. **80 percent top share; DOTAX anchors MID; LOW and HIGH keep MID's
   factors.** In the middle scenario, 80 percent of the $400K+ class's gains
   go to $1M+ filers (the capital-gains page's central assumption); the
   share is not paired with the scenarios, because a heavier or lighter top
   moves bracket plans and gains plans in opposite directions. The low and
   high scenarios apply the middle scenario's scale factors to their own
   classes and model gains, so their gains move with their top incomes
   instead of being pinned to DOTAX's totals (why: the departures above).
4. ETI works on gains as in v1: it scales income with the gains share held,
   so gains shrink with income. When a plan raises both the brackets and
   the gains rate, that shrinkage and the realization response partly
   overlap. The overlap is documented, not removed.
5. The statute is HRS §235-51(f), as the capital-gains page cites it. The
   old §235-16 citations in the code and in `SB3125_CD1_FORECAST.md` were
   corrected.
6. Nonresidents are excluded, as on the capital-gains page. The page cites
   that page's TY2027 add-on after response: $10.8M (typical year) or
   $23.7M (TY2022 levels) for a 9% rate, $28.7M or $64.0M for ordinary
   income (`site/data/capital-gains/nonresident_addon.csv`).
7. No response to a cut in the gains rate and no migration from it
   (decisions 3 and 4 above carry over).
8. The spec key as above; the web format version is 2.

**Numbers for the presets in v2** (change vs Act 24, $M, with response; v1
in brackets):

| Preset | TY2027, middle | 2027–2031, middle | 2027–2031, low / high |
|---|---:|---:|---:|
| Act 46's brackets | −64.8 [−58.6] | −378.9 [−356.2] | −381.1 / −415.5 [−364.1 / −389.5] |
| SB 3125 SD1 | −51.2 [−43.2] | +398.9 [+440.7] | +380.7 / +384.5 [+417.9 / +429.1] |
| HB 2306 HD1 | +216.1 [+223.3] | +1,790.0 [+1,830.1] | +1,749.6 / +1,824.6 [+1,787.4 / +1,866.2] |
| Top rate 14% | +36.0 [+32.1] | +191.1 [+171.5] | +156.6 / +228.7 [+140.0 / +207.4] |
| Capital gains rate 9% | +45.7 | +252.6 | +211.9 / +294.7 |
| Capital gains as ordinary income | +123.5 | +683.5 | +549.6 / +824.5 |

Static, middle scenario, 2027–2031: Act 46's brackets −$353.3M → −$376.0M,
the 14% top rate +$210.5M → +$230.6M. The base, not the formula, moved the
v1 rows: it puts less of the gains at $1M+ (so more of those filers' income
meets the top rate) and more between $300K and $1M. The low and high
scenarios keep the middle scenario's factors, so the same correction moves
all three by similar proportions: five years static, the 14 percent rate
goes from $216.0M / $210.5M / $223.7M (low / middle / high) to $233.8M /
$230.6M / $245.2M, 8 / 10 / 10 percent more, and Act 46's brackets from
−$359.7M / −$353.3M / −$388.5M to −$376.7M / −$376.0M / −$414.5M, 5 / 6 / 7
percent larger. Combining plans: the 14% top rate with a 9% gains rate
raises $538.6M static and $442.4M after response over five years in the
middle scenario, of which the gains rate is $308.0M and $251.3M.

## What users can change, and what they get

| Control | When | Notes |
|---|---|---|
| Bracket thresholds and rates, per filing status | v1 | Add or remove brackets. A "head of household 1.5×, joint 2× single" link is on by default, the pattern Act 46 and Act 24 both follow |
| Standard deduction | v1 | The current-law schedule (it rises through 2031) or a flat amount by filing status |
| Personal exemption | v1 | Only after Phase 0 defect 3 is fixed |
| First tax year | v1 | 2027–2031. The user's schedule replaces current law from that year on, including Act 24's scheduled 2029 step |
| Behavioral assumptions | v1 | Static, or the Act 24 page's low, middle and high scenarios. They pair taxable-income elasticity (ETI) 0.60 / 0.40 / 0.15 and migration elasticity 0.01 / 0.0025 / 0.001 (0.15 / 0.10 / 0.05 when this was scoped; see decision 8) with that page's top-tail and top-income-growth assumptions. v2 adds a capital-gains realization response, β 2.6 / 2.0 / 1.6 |
| Capital gains alternative rate (7.25%) | v2, built | Current law, a different rate, or taxed as ordinary income. Uses the capital-gains page's DOTAX-anchored gains base and the statutory alternative tax (HRS §235-51(f)); see [v2](#v2-capital-gains-september-27-2026) |
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
   - Bracket tax, with the 7.25% cap on the capital-gains share. (v1 used
     the model's gains base and the stacked shortcut; v2 uses the
     DOTAX-anchored base and the statutory alternative tax.)
   - Credits held at current law, with nonrefundable credits limited to tax.
3. **Behavioral response.**
   - ETI: each record whose marginal rate rises has its income multiplied by
     ((1 − t₁)/(1 − t₀))^ε.
   - Migration, generalized from the Act 24 form (decision 4).
   - Then score the plan again. Current law is not re-scored: nobody
     responds to it, so a filer who moves away costs their whole tax.
4. **Aggregate.**
   - Revenue by year.
   - Static distribution by household fifth and AGI class. As in
     `generate_quintile_report`: fifths of households by the household
     weight; dollars are each unit's change × its own filer weight, summed;
     the household weight counts households.

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
  (Since v2 the static figure no longer matches either, because the
  simulator's capital gains differ from the Act 24 page's; see
  [v2](#v2-capital-gains-september-27-2026).)
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

- **Validate input.** Thresholds must start at $0 and rise, and rates, the
  capital gains rate included, must be at least 0 and below 100% (as built;
  this scope first said 0–25%). Warn above 15%, when the top rate rises more
  than 3 points or falls, or when rates fall as income rises. v2 adds five
  warnings for the gains rate (see [v2](#v2-capital-gains-september-27-2026)).
  A plan's bracket period may start after the last scored year (both
  parsers accept any later year); the warnings clamp such a year to the
  scored years when they look up current law, and it is never scored.
- **Say when the response is extrapolated.** The behavioral assumptions were
  set for top-rate changes of a few points (11% to 13%), and the gains
  response for the capital-gains page's two options.
- **The top tail is thin.** 49 records (middle scenario, TY2027) stand for
  the roughly 2,300 returns above $1M; this scope first said 70. Always show
  the low–high range, and don't headline a single number for changes that
  touch only $1M+. Most capital gains are there too.
- **Rate cuts get no behavioral response,** because the ETI module is
  asymmetric by design. The page says so whenever a spec cuts rates, and
  whenever a cut in the gains rate can lower anyone's tax.
- **State the limits.** Distribution is static. Credits stay at current law.
  Since v2, capital gains are the DOTAX-anchored base with the statutory
  alternative tax. Nonresidents' gains and those of filers under about
  $100K are left out, and the Act 24 page still uses the model's own base.
  v1 kept the 7.25% cap on that base. The capital-gains page found it puts
  about 30% too much at the top, but that holds on that page's own
  population ($400K+: $3,818.9M of model gains against DOTAX's $2,980.6M in
  TY2026, 28% too much). On the simulator's population the $400K+ total is
  about right (3.7% too much in the middle scenario, TY2027) but split
  wrongly: 17% too much at $1M+ (k = 0.854) and too little between $300K and
  $1M, by a factor of 2.0 to 2.8 (k = 2.005 at $400K–$1M and 2.820 at
  $300K–$400K). The low and high scenarios use the same k. So v1
  understated what top-rate increases raise in all three scenarios (the
  14 percent preset, five years after response, low / middle / high:
  $140.0M / $171.5M / $207.4M in v1, $156.6M / $191.1M / $228.7M in v2).
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
| **Total after Phase 0** (all built, PR #28) | | **10–13** |

Phase 1 is useful on its own: `forecast_custom.py --spec` lets staff score
any schedule from the command line.

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
- **Capital-gains rate.** Built in [v2](#v2-capital-gains-september-27-2026):
  the DOTAX-anchored gains base and the statutory alternative tax from
  `forecast_cg_rate_options.py`, shared through `tax_modeler`.
- **Credits.** Food/excise tables, the EITC share of federal, and dependent
  care, using take-up from the working-family credits pipeline.
- **General excise tax.** There is no microdata for it, so it needs its own
  scope.
