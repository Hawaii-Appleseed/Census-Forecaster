# Behavioral response scoring: review and proposed fix

**September 27, 2026. Both fixes were applied the same day on
`feat/tax-simulator`: the standard counterfactual, through one helper
(`behavioral_response.score_with_response`) that the Act 24 pipeline and
the simulator share and `kernel.js` mirrors, and the migration elasticities
0.01 / 0.0025 / 0.001. See `SB3125_CD1_FORECAST.md`, "Behavioral accounting
and migration elasticity — September 27, 2026", for the results. Below is the
analysis as written before they were applied.** This covers the Act 24 pipeline (`forecast_sb3125_enhanced.py`,
published at `site/data/act-24/`), the tax simulator's Python reference scorer
(`tax_modeler.simulator.score.score_systems`) and its browser kernel
(`site/assets/simulator/kernel.js`). All figures are five-year sums, tax years
2027 to 2031, in $M, Act 24 against Act 46 unless stated. The figures come from
the simulator population, which reproduces every published LOW/MID/HIGH row to
the cent, and were recomputed by a second, independent route.

## Bottom line

1. **The accounting is wrong.** After the behavioral response, the pipeline
   scores *both* Act 46 and Act 24 on the population that has responded. The
   standard counterfactual scores Act 46 on the population as it is, because
   nobody responds to a law that did not change. As published, a millionaire
   who leaves Hawaiʻi costs only the rate increase on their income, not the
   tax they pay today. No document or commit gives a reason for doing it this
   way.
2. **The migration elasticity is 20 to 100 times the literature.** The model
   removes 0.10 of $1M+ filers per percentage point of top-rate increase
   (MID): 20% of them for Act 24's 2 points. The sources it cites support
   roughly 0.001 to 0.005 per point. The value looks like Young & Varner's
   semi-elasticity, which is in *percent* per point, read as a share.
3. **For Act 24 the two errors nearly cancel.** Fixing both moves the MID
   five-year total from **$738.9M to about $726M** (−1.7%). Fixing only the
   accounting gives **$125M**, because the oversized elasticity then costs the
   migrants' whole tax. The two must change together.
4. **For other plans they do not cancel.** The simulator's 14% top-rate
   preset scores +$195.9M as published, −$144.3M with only the accounting
   fixed, and +$171.5M with both fixed.

## 1. Is the accounting wrong?

Yes. The code computes

    change = Σ w′·T_Act24(y′) − Σ w′·T_Act46(y′)

where y′ and w′ are incomes and weights after the response (ETI shrinks the
reported income of filers whose marginal rate rises; migration cuts the weight
of top filers). Conventional revenue estimates with behavior, the kind JCT,
CBO and Treasury produce, compute

    change = Σ w′·T_Act24(y′) − Σ w·T_Act46(y)

The responses exist only in the Act 24 world: the model gives an ETI factor of
1 and no migration wherever rates do not rise. The difference between the two
is exactly Σ w′·T_Act46(y′) − Σ w·T_Act46(y), the Act 46 revenue the
response destroys, which the code drops. So:

- **Migrants.** Each is charged only T_Act24 − T_Act46, about 10.5% of their
  tax, instead of their whole tax. In TY2031 (MID) about 790 filers leave; they
  pay $199.4M under Act 46, and the published accounting charges $22.9M.
- **ETI.** The offset on shrunk income is (t₁ − t₀)·Δy instead of t₁·Δy.

Evidence:

- **It cannot score a loss.** For a pure rate increase, Σ w′(T₁ − T₀)(y′) ≥ 0
  whatever the elasticity. Even losing every top filer would score their
  contribution as zero rather than minus their tax.
- **The published table is internally inconsistent.** Its `act46_baseline_$M`
  column is the static baseline (Σ w·T₀(y), $2,652.8M in TY2031 MID), but the
  after-response change is measured against Σ w′·T₀(y′) ($2,441.7M), which the
  table never shows.
- **The repo's other scorers do it the standard way:**
  `forecast_cg_rate_options.score_option` (current-law tax on unadjusted
  gains) and `forecast_conveyance_sb3028.score` (current tax on the unresponded
  sales).
- **The PTE channel is accounted correctly.** Income that shifts to the 9% PTE
  tax is netted against its destination base. Nothing like that exists for ETI
  or migration.
- **Rauh & Shyu (2024)**, which section 5a cites, charge each mover their
  whole California tax and lost income at the new rate.

History: the accounting arrived with the first behavioral commit (`0894417`,
2026-05-02), whose comment read "recompute scenario revenue" while the code
re-scored both systems. `46a57ed` shortened the comment to "recompute", and the
code has been copied unchanged into CD2 (`3bfc352`), the merged script
(`e54187d`), the September 25 fixes and the simulator. SB3125_CD1_FORECAST.md
sections 5a and 5b describe the channels but say nothing about how the baseline
is scored. The one related argument in the doc, in section 8b, concerns the
recession shock, which is exogenous and hits both laws alike; it does not carry
over to a response that Act 24 causes.

The strongest defense is that the published number is a valid *diagnostic*:
the mechanical effect of the rate change on the post-response base, or a
tally of what the new rates collect from returns filed after the response. The
page, however, presents it as "more revenue than Act 46 would have raised",
which it is not.

## 2. What should the migration elasticity be?

The code (`BehavioralParams`) sets `migration_elast` to 0.05 / 0.10 / 0.15 and
applies it as the share of $1M+ filers lost per percentage point of top-rate
increase, at full phase-in. Filers between the top-bracket floor and $1M lose
half that. The doc cites "Young & Varner (2016) … ~0.10–0.15 per
percentage-point rate change" and says a 50% Hawaiʻi discount is applied. The
code applies no such discount; its only 0.5 is the half tier. The code's own
comment cites Young & Varner (2011).

What the sources estimate, and the share per *statutory* point it implies for
Hawaiʻi. Act 24 raises the average tax rate of $1M+ filers by only about 0.9
points for its 2-point statutory increase, because of the 7.25% capital-gains
cap. The studies measure average or effective rates, so their estimates are
rebased by that ratio (about 0.45):

| Source | Estimate, as defined there | Implied share per statutory pp |
|---|---|---|
| Young & Varner (2011), *National Tax Journal*: New Jersey's 2004 millionaire tax, +2.6 pp | Semi-elasticities "generally below 0.1", in **percent** of the population per effective pp a year: 0.04 (over $500K), 0.08 (top 0.1%), neither significant | ≈ 0.001–0.002 (over five years) |
| Cohen, Lai & Steindel (2015), *Public Finance Review*: replication of the same | About 80 extra out-migrants over $500K a year | ≈ 0.005 |
| Young, Varner, Lurie & Prisinzano (2016), *American Sociological Review*: all US $1M+ filers, 1999–2011 | Millionaires migrate at 2.4% a year, less than the general population. 1 pp moves about 23 households a year per state (≈ 0.26%). η ≈ 0.1 of the population against the combined federal and state rate | ≈ 0.002 as a one-time move; ≈ 0.01 if the flow persists five years |
| Rauh & Shyu (2024), *AEJ: Economic Policy*: California Prop. 30, +3 pp | An extra 0.8% of the top-bracket base left, once (2013), not repeated | ≈ 0.002–0.003 |
| Agrawal & Foremny (2019), *REStat*: Spain | 0.85, top taxpayers against the net-of-tax rate | ≈ 0.004–0.0075 |
| Kleven, Landais, Muñoz & Stantcheva (2020), *JEP* survey | Domestic top earners across US states ≈ 0.1 against the net-of-tax rate | ≈ 0.001–0.002 |

Moretti & Wilson's (2017) star-scientist estimate of 1.8 is a flow elasticity
of migration odds, not a population elasticity, and for a small, unusually
mobile group; it is an upper bound only.

**Recommendation.** Keep the parameter's form, with values of **0.001 (HIGH
revenue scenario, weak response), 0.0025 (MID) and 0.01 (LOW revenue scenario,
strong response)**. For Act 24's 2 points that is a long-run loss of 0.2%,
0.5% and 2% of $1M+ filers, instead of 10%, 20% and 30%. 0.01 is already
generous: it rests on New Jersey filers whose income is all earned in-state
(not significant) and on the persistent-flow reading of Young et al. 0.02 is
a reasonable stress case. Each 0.001 costs about $6.2M over five years at MID
under the corrected accounting, so the MID result is not sensitive within the
range the literature allows ($711M to $736M for 0.005 to 0.001).

The simulator accepts any rate change. A later refinement could express
migration as an elasticity against the net-of-tax rate, the way the ETI is,
rather than per point. That needs matching changes in `behavioral_response.py`
and `kernel.js`.

## 3. Act 24 under each option

Bracket change after the response (credits are static and unaffected):

| Five years, $M | LOW | MID | HIGH |
|---|---:|---:|---:|
| Static | 359.7 | 353.3 | 388.5 |
| **Published** (both laws on the responded population; elasticity 0.15 / 0.10 / 0.05) | **253.5** | **282.6** | **351.4** |
| Corrected accounting, current elasticity | −632.2 | −331.2 | 22.1 |
| Corrected accounting, literature elasticity (0.01 / 0.0025 / 0.001) | 202.3 | 270.1 | 354.0 |
| Corrected accounting, no migration | 262.0 | 285.5 | 360.8 |

Total impact, the bracket change plus the unchanged credit overlay (417.2 /
456.3 / 560.5):

| Five years, $M | LOW | MID | HIGH |
|---|---:|---:|---:|
| **Published** | **670.6** | **738.9** | **911.9** |
| Corrected accounting, current elasticity | −215.0 | 125.1 | 582.6 |
| Corrected accounting, literature elasticity | 619.5 | 726.4 | 914.5 |
| Corrected accounting, no migration | 679.1 | 741.8 | 921.3 |

RECESSION (published $739.5M) moves to $118.7M with the current elasticity;
with the literature elasticity it would land near MID, but it needs the full
pipeline to compute (the simulator population has no recession path).

Where the published offset came from: of MID's −$70.7M behavioral offset,
−$63.5M is migration and −$8.2M ETI. Section 5a presents it as ETI. With the
corrected accounting the ETI offset alone is −$67.8M.

In the simulator, the 14% top-rate preset (vs Act 24):

| Five years, $M | LOW | MID | HIGH |
|---|---:|---:|---:|
| Static | 216.0 | 210.5 | 223.7 |
| Published method | 193.7 | 195.9 | 216.1 |
| Corrected accounting, current elasticity | −300.0 | −144.3 | 34.2 |
| Corrected accounting, literature elasticity | 140.0 | 171.5 | 207.4 |

The combination of corrected accounting and the current elasticity is also
incoherent in another way. Rate cuts get no response and nobody moves back, so
both enacting Act 24's brackets (−$331.2M against Act 46, MID) and undoing them
(the Act 46 preset, −$356.2M against Act 24) would lose money.

## 4. The fix

**Accounting (three places, one rule).** After the response, score only the
reform again, and subtract the static baseline.

- `forecast_sb3125_enhanced.py`, `run_one_scenario` step 4:

  ```python
  scenario_behav = float(calc.calculate_revenue_vectorized(
      adjusted, scenario_cfg)["total_revenue_millions"])
  diff_behav = scenario_behav - baseline_static      # was compare_systems(adjusted, ...) Difference
  ```

- `tax_modeler/simulator/score.py`, `score_systems`:

  ```python
  reform_post = float(calc.calculate_revenue_vectorized(adj, scen_cfg)["total_revenue_millions"])
  "behavioral_$M": reform_post - float(cmp.iloc[0]["revenue_millions"]) - diag["pte_revenue_loss_$M"],
  ```

- `site/assets/simulator/kernel.js`, `scoreYear`: drop `base2`, return
  `"behavioral_$M": reform2 - base`.

- A test (`tests/tax_modeler/test_simulator.py`) that checks behavioral =
  Σ w′·T_reform(y′) − Σ w·T_baseline(y) computed independently, and that it is
  below the old figure whenever anyone migrates.

The full diff is in Appendix A. It was applied to a scratch copy of the repo
and tested there:

- **Unchanged code.** The unchanged pipeline reproduced every published row.
- **Patched pipeline.** It gave exactly the "current elasticity" figures in
  section 3, including RECESSION (bracket −337.6, total 118.7).
- **Python parity.** The simulator build's Act 24 check passed, so
  `score_systems` matches the patched pipeline row by row.
- **Kernel parity.** 81 of 81 kernel tests pass against regenerated fixtures;
  22 fail against the old ones, as they should.
- **The new Python test.** It passes, and it fails against the old code.
- **Full suite.** 2,446 passed and 3 skipped. The one failure,
  `test_git_sha_mismatch_warns_but_returns_meta`, comes from the copy having no
  `.git`.
- **Population data.** The population binary is byte-identical; only
  `model_version` changes.

In `enhanced.csv` only `eti_response_$M`, `bracket_delta_post_$M`,
`bracket_delta_cor_scaled_$M` and `total_impact_$M` change. The distribution
tables are static and do not.

A review of the diff asked for four more things before it is merged:

- **One helper instead of two copies.** Parity between the pipeline and
  `score_systems` now rests on the same formula written twice, tied together
  only by the simulator build's check, which is not in CI. A shared
  `score_with_response(df, params, ...)` in `behavioral_response.py`, called
  by both (with `top_rate_path` from the pipeline too), would make it
  structural.
- **The meaning of `eti_response_$M`.** It becomes the reform revenue lost to
  the response. At the current elasticity about 90% of that is migration, so
  the column name and the "income shrink" print label mislead. Keep the name
  and document it, or add a migration column.
- **Temporary rises.** Under the standard counterfactual, filers who left
  because of an earlier rise still cost their whole tax after the plan returns
  to current law, until the fall phases in. A 15% top rate for 2027–28 alone
  scores about −$80M to −$90M a year in 2029–31 (MID, current elasticity). That
  is right, but `score_systems`' docstring says the opposite, and it should be
  pinned by a test.
- **Kernel-side tests that don't depend on the golden fixtures.** These catch
  the kernel and the model drifting back together. Appendix B has three:
  - a migrant costs their whole tax;
  - a pure cut scores the same both ways;
  - migrants from a temporary rise stay gone.

  They pass on the patched kernel; the first and third fail on the old one.

**Elasticity.** `BehavioralParams.low/mid/high` in
`scenarios/behavioral_response.py`: `migration_elast` 0.05 / 0.10 / 0.15 →
0.001 / 0.0025 / 0.01. Two tests pin the old values
(`test_phase_in_starts_when_the_top_rate_rises` and
`test_migration_never_takes_more_than_everyone`), as does the kernel test that
expects the `top_rate_30` golden case to lose every $1M+ filer. Those should
test the clamp with explicit parameters instead.

**Order, once approved:**

1. Apply both changes.
2. `python forecast_sb3125_enhanced.py --cd 2` to regenerate the Act 24 run.
3. `python scripts/build_simulator_population.py --from-saved`. Its Act 24 check
   then confirms that the simulator's scorer matches the pipeline under the
   new accounting. It fails, and writes nothing, until step 2 has run. The
   population itself does not change; `model_version` does, so old share links
   will say the numbers moved.
4. `python scripts/build_site.py --import-runs act-24 tax-simulator`. This
   also copies the new golden fixture into `tests/simulator/`.
5. `node --test tests/simulator/*.test.mjs` and the full pytest suite.
6. Documents (the CLAUDE.md rule):
   - `SB3125_CD1_FORECAST.md`: a new dated section, the results tables and
     decomposition, section 4's literature row, 5a (the offset is mostly
     migration), 5b (the elasticity and its citation, and the discount it
     claims), and 8a.
   - `TAX_SIMULATOR_SCOPE.md`: decision 8 and the preset table.
   - The page prose:
     - The Act 24 section of `build_site.py` credits the offset to "reporting
       less income", though most of it is migration, and says "a few" move
       away.
     - With the current elasticity it would also print "$-331 million", and
       its stacked Figure 1 cannot draw negatives. That is one more reason not
       to change the accounting alone.
     - The simulator's copy ("some of the highest earners move away") and its
       rate-cut warning should say that a filer who leaves takes their whole
       tax, and that no one is assumed to move back.

**What republication would change:** the Act 24 page's headline ($738.9M →
about $726M MID; LOW $670.6M → about $620M), its Table 2 and Figure 1, the
home-page card, the simulator's preset table, `presets_results.json` and the
golden fixture.

## Addendum: capital-gains realization (tax simulator v2, September 27, 2026)

v2 of the tax simulator lets a plan change the rate of the alternative tax
on net long-term capital gains, and adds a third behavioral channel for it,
scored with the accounting above (`TAX_SIMULATOR_SCOPE.md`, "v2: capital
gains").

- **Order.** ETI, then migration, then realization, then the PTE estimate:
  `apply_realization_response` in `behavioral_response.py`, called by
  `apply_behavioral_response` and so by `score_with_response`, and mirrored
  in `kernel.js`'s `behave`.
- **Form.** The capital-gains page's (`forecast_cg_rate_options.score_option`).
  With m a filer's bracket rate under the plan at taxable income before any
  response, the rate on gains is τ(c) = min(m, c) under an alternative rate
  c (m when gains are taxed as ordinary income), and d = max(0, τ(c₁) −
  τ(c₀)), both τ on the plan's schedule. Gains after ETI and migration,
  g = y·s, become g·exp(−β·d); income falls by the difference and the share
  is recomputed.
- **β.** 2.6 / 2.0 / 1.6 in the LOW / MID / HIGH revenue scenarios (the
  strong / middle / weak response parameters, `BehavioralParams.cg_beta`),
  0 when static: the capital-gains script's elasticity range of 0.5–0.8
  divided by the combined top rate of about 31%. MID is that page's β of 2.
- **Accounting.** Unchanged: the plan is scored on the responded incomes,
  weights and gains, current law on the population as it is, Σ w′·T₁(y′, s′)
  − Σ w·T₀(y, s).
- **Scope.** It runs only when both systems use the statutory alternative
  tax and their gains rates differ. The Act 24 pipeline's registry systems
  keep the stacked shortcut, and a plan that changes only the brackets
  leaves the gains rate alone, so everything above is unchanged. A cut in
  the gains rate gets no response, and no one migrates because of the gains
  rate.
- **Overlap.** ETI scales income with the gains share held, so it already
  shrinks gains when a filer's bracket rate rises. For a plan that raises
  both the brackets and the gains rate, the two channels partly overlap.
  That is left as it is and documented, not removed.
- **Check.** For the capital-gains page's two options the simulator's path
  matches `score_option` on the same records to 1.1e-14 (relative), and its
  response ratios are within 0.0032 of the page's
  (`site/data/tax-simulator/cg_page_comparison.json`).

v2 also moves the simulator onto a DOTAX-anchored gains base, which moves
the 14% top-rate preset of section 3 from $171.5M to $191.1M (MID, five
years, with both fixes), and LOW from $140.0M to $156.6M and HIGH from
$207.4M to $228.7M. DOTAX anchors MID; LOW and HIGH keep MID's scale
factors, applied to their own income classes and model gains, so their
gains move with their top incomes rather than being pinned to DOTAX's
totals (`TAX_SIMULATOR_SCOPE.md`, v2 decision 3). That comes from the
base, not from this channel: a bracket-only plan never triggers it. For a
plan that changes only the gains rate, the range across scenarios now
comes from top incomes as well as from β: the 9 percent preset's five-year
static gain is $276.4M / $308.0M / $344.2M (LOW / MID / HIGH), against
$306.9M / $308.0M / $309.9M when the first v2 build pinned every scenario
to DOTAX, and $211.9M / $252.6M / $294.7M after the response.

## Also found, not part of this fix

- **ETI.** Section 5a says the ETI is calibrated to Rauh & Shyu (2024), but
  their estimate for top earners is 2.5–3.2 against the combined net-of-tax
  rate, against the model's 0.40 on the state rate alone. The model's values
  are within the general literature (Saez, Slemrod & Giertz), just not that
  paper's.
- **A second migration channel.** The top-income growth premium carries a
  "Hawaiʻi outmigration haircut" (0.8 pp a year in code, 0.5 pp in the doc),
  so baseline out-migration of high earners is already in the projection.
  Worth checking for double counting when the elasticity is set.
- **Citations.** The doc cites Young & Varner (2016) and the code Young & Varner
  (2011); the 2016 paper is Young, Varner, Lurie & Prisinzano. The code cites
  Cohen, Lai & Steindel under a wrong title. The module docstring's scenario
  table (MID ETI 0.25, PTE 0.35) is stale.
- **`dashboard/dist/dashboard.html`** embeds old `eti_response_$M` values
  (MID 2027 −5.55 against today's −4.60), and nothing rebuilds it.

## Decisions

1. Adopt the standard counterfactual in all three scorers. Recommended.
2. Change the migration elasticity **at the same time**. Recommended; the
   accounting fix alone would publish an Act 24 MID of $125M and a LOW of
   −$215M on an elasticity 20 to 100 times the evidence.
3. Whether to revisit the ETI values and the premium's migration haircut now
   or separately.
4. Whether and when to republish the Act 24 page (MID $738.9M → about $726M).

## Appendix A. The accounting patch

Against the working tree of `feat/tax-simulator` as of this review. Apply with
`patch -p1` from the repo root. It changes the accounting only, not the
elasticity (see section 4). *As applied, the Python side instead routes the
pipeline and `score_systems` through one shared helper,
`score_with_response`, as the review of this diff recommended; the
arithmetic is the same.*

```diff
diff -ru a/forecast_sb3125_enhanced.py b/forecast_sb3125_enhanced.py
--- a/forecast_sb3125_enhanced.py	2026-09-26 15:46:29
+++ b/forecast_sb3125_enhanced.py	2026-09-26 15:46:53
@@ -294,16 +294,20 @@
         diff_static = float(cmp_static[cmp_static["system"] == "Difference"].iloc[0]["revenue_millions"])
         baseline_static = float(cmp_static[cmp_static["system"] == baseline_cfg.name].iloc[0]["revenue_millions"])
 
-        # 4) Apply behavioral response (per-filer ETI + migration), recompute
+        # 4) Apply behavioral response (per-filer ETI + migration), then
+        #    re-score Act 24 only. The counterfactual is Act 46 with nobody
+        #    responding (the responses are to Act 24's rate increases), so
+        #    the baseline stays the static one:
+        #        change = sum w' T_act24(y') - sum w T_act46(y).
+        #    A filer who moves away costs their whole Hawaii tax, and income
+        #    reported away costs t1 x dy, not (t1 - t0) x dy.
         adjusted, behav_diag = apply_behavioral_response(
             projected, behav_params, target_year=year,
             baseline_cfg=baseline_cfg, scenario_cfg=scenario_cfg, calculator=calc,
         )
-        cmp_behav = compare_systems(
-            adjusted, baseline_cfg, scenario_cfg,
-            calculator=calc,
-        )
-        diff_behav = float(cmp_behav[cmp_behav["system"] == "Difference"].iloc[0]["revenue_millions"])
+        scenario_behav = float(calc.calculate_revenue_vectorized(
+            adjusted, scenario_cfg)["total_revenue_millions"])
+        diff_behav = scenario_behav - baseline_static
 
         # 5) PTE election shift (revenue moves from individual to PTE form)
         pte_shift = behav_diag["pte_revenue_loss_$M"]
diff -ru a/packages/tax_modeler/src/tax_modeler/simulator/score.py b/packages/tax_modeler/src/tax_modeler/simulator/score.py
--- a/packages/tax_modeler/src/tax_modeler/simulator/score.py	2026-09-26 15:46:29
+++ b/packages/tax_modeler/src/tax_modeler/simulator/score.py	2026-09-26 15:46:53
@@ -53,9 +53,12 @@
     """Revenue for every (scenario, year), static and after the behavioral
     response, plus MID distribution tables for ``distribution_years``.
 
-    Migration phases each change in the top rate in from the year it takes
-    effect (``top_rate_path``), so years in which the plan equals current law
-    add nothing. Money in $M; the distribution tables use
+    After the response, only the reform is scored again: the baseline is
+    current law with nobody responding, so ``behavioral_$M`` is
+    sum w' T_reform(y') - sum w T_baseline(y) (a filer who moves away costs
+    their whole tax). Migration phases each change in the top rate in from
+    the year it takes effect (``top_rate_path``), so years in which the plan
+    equals current law add nothing. Money in $M; the distribution tables use
     ``generate_quintile_report``'s columns with ``act46``/``cd1`` renamed
     ``baseline``/``reform`` (``REPORT_NAMES``)."""
     calc = calculator or TaxCalculator()
@@ -71,13 +74,15 @@
             adj, diag = apply_behavioral_response(
                 df, params, target_year=y, baseline_cfg=base_cfg, scenario_cfg=scen_cfg,
                 calculator=calc, top_rate_path=path)
-            cmp2 = compare_systems(adj, base_cfg, scen_cfg, calculator=calc)
+            reform_post = float(calc.calculate_revenue_vectorized(adj, scen_cfg)["total_revenue_millions"])
             revenue.append({
                 "scenario": s, "tax_year": y,
                 "baseline_$M": float(cmp.iloc[0]["revenue_millions"]),
                 "reform_$M": float(cmp.iloc[1]["revenue_millions"]),
                 "static_$M": float(cmp.iloc[2]["revenue_millions"]),
-                "behavioral_$M": float(cmp2.iloc[2]["revenue_millions"]) - diag["pte_revenue_loss_$M"],
+                # the static baseline: no one responds to current law
+                "behavioral_$M": reform_post - float(cmp.iloc[0]["revenue_millions"])
+                                 - diag["pte_revenue_loss_$M"],
                 "filers_1m_post_response": diag["filers_1m_post_response"],
             })
     distribution = {}
diff -ru a/site/assets/simulator/kernel.js b/site/assets/simulator/kernel.js
--- a/site/assets/simulator/kernel.js	2026-09-26 15:46:29
+++ b/site/assets/simulator/kernel.js	2026-09-26 15:46:53
@@ -265,19 +265,20 @@
 // ---------------------------------------------------------------------------
 
 /** Revenue for one (scenario, year): baseline, reform, static change, and the
- * change after the behavioral response ($M). */
+ * change after the behavioral response ($M). After the response only the
+ * reform is scored again: the baseline is current law with nobody
+ * responding, so a filer who moves away costs their whole tax (score.py). */
 export function scoreYear(pop, year, scenario, baseSys, reformSys, path) {
   const params = pop.meta.scenarios[scenario];
   const u = unitArrays(pop, year, scenario);
   const base = weightedMillions(unitNetTaxes(pop, baseSys, u.agi, u.item, u.cg), u.weight);
   const reform = weightedMillions(unitNetTaxes(pop, reformSys, u.agi, u.item, u.cg), u.weight);
   const adj = behave(pop, baseSys, reformSys, u, params, year, path);
-  const base2 = weightedMillions(unitNetTaxes(pop, baseSys, adj.agi, adj.item, adj.cg), adj.weight);
   const reform2 = weightedMillions(unitNetTaxes(pop, reformSys, adj.agi, adj.item, adj.cg), adj.weight);
   let filers1m = 0;
   for (let i = 0; i < pop.n; i++) if (adj.agi[i] >= 1_000_000) filers1m += adj.weight[i];
   return { scenario, tax_year: year, "baseline_$M": base, "reform_$M": reform,
-    "static_$M": reform - base, "behavioral_$M": reform2 - base2,
+    "static_$M": reform - base, "behavioral_$M": reform2 - base,
     filers_1m_post_response: filers1m };
 }
 
diff -ru a/tests/tax_modeler/test_simulator.py b/tests/tax_modeler/test_simulator.py
--- a/tests/tax_modeler/test_simulator.py	2026-09-26 15:46:29
+++ b/tests/tax_modeler/test_simulator.py	2026-09-26 15:46:53
@@ -482,6 +482,31 @@
         res = score_spec(pop, spec(income_tax={"personal_exemption": 5_000}))
         assert all(r["static_$M"] < 0 for r in res["revenue"])
 
+    def test_the_baseline_has_no_response(self, calc):
+        # A 14% top rate: behavioral = reform tax on the responded population
+        # minus current-law tax on the population as it was.
+        pop = _synthetic_population(years=YEARS)
+        pop.arrays["agi_2029"][:20] = np.linspace(1.2e6, 9e6, 20)   # a top tail to respond
+        vint = [{"from": v["from"], "brackets": {fs: rows[:-1] + [[rows[-1][0], 14.0]]
+                                                  for fs, rows in v["brackets"].items()}}
+                for v in current_law_vintages(calc)]
+        s_ = spec(income_tax={"bracket_vintages": vint})
+        res = score_spec(pop, s_, scenarios=["mid"], years=[2029])
+        r = res["revenue"][0]
+        df = frame_for(pop, 2029, "mid")
+        path = {y: top_rate_changes(s_.baseline_for(y), s_.system_for(y), calc) for y in YEARS}
+        adj, _ = apply_behavioral_response(df, SCENARIOS["mid"].behavioral_params, target_year=2029,
+                                           baseline_cfg=s_.baseline_for(2029),
+                                           scenario_cfg=s_.system_for(2029), calculator=calc,
+                                           top_rate_path=path)
+        net = lambda frame, cfg: float((calc.unit_liabilities(frame, cfg)["net"]  # noqa: E731
+                                        * frame["weight"].to_numpy()).sum()) / 1e6
+        want = net(adj, s_.system_for(2029)) - net(df, s_.baseline_for(2029))
+        assert r["behavioral_$M"] == pytest.approx(want, rel=1e-9, abs=1e-9)
+        # stricter than scoring both systems on the responded population
+        old = net(adj, s_.system_for(2029)) - net(adj, s_.baseline_for(2029))
+        assert r["behavioral_$M"] < old
+
     def test_distribution_names_the_baseline(self):
         pop = _synthetic_population()
         d = score_spec(pop, spec(income_tax={"personal_exemption": 5_000}), distribution_years=[2027])
```

## Appendix B. Proposed kernel tests

`tests/simulator/accounting.test.mjs`, drafted in the review of the patch. It
reads the population only, not the golden fixtures. With the patched kernel all
three pass; with the old kernel the first and third fail. The third encodes the
temporary-rise behavior described in section 4; keep it only if that behavior
is accepted.

```js
// tests/simulator/accounting.test.mjs
// The counterfactual behind "with response": the reform is scored on the
// population after it responds, current law on the population before
// anyone does. change = sum w' T_reform(y') - sum w T_current(y).
// These use only the population, not the golden fixtures, so they would
// catch the kernel and the Python model drifting back to the old accounting
// together (the golden tests cannot: they are regenerated from the model).
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { gunzipSync } from "node:zlib";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  decodePopulation, score, parseSpec, resolveSpec, unitArrays, prepareSystem, unitNetTaxes,
} from "../../site/assets/simulator/kernel.js";

const REPO = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const DATA = process.env.SIM_DATA_DIR || join(REPO, "site/data/tax-simulator");
const meta = JSON.parse(readFileSync(join(DATA, "population.json"), "utf8"));
const blob = gunzipSync(readFileSync(join(DATA, "population.bin.gz")));
const pop = decodePopulation(meta, blob.buffer.slice(blob.byteOffset, blob.byteOffset + blob.byteLength));

const withParams = (over) => ({ ...pop,
  meta: { ...pop.meta, scenarios: { ...pop.meta.scenarios, mid: { ...pop.meta.scenarios.mid, ...over } } } });
const close = (a, b, where, tol = 1e-9) =>
  assert.ok(Math.abs(a - b) <= tol * Math.max(1, Math.abs(b)), `${where}: ${a} vs ${b}`);

test("a filer who moves away costs their whole tax under the plan", () => {
  // Migration only (ETI 0): with response = static - sum w (1 - f) T_reform(y),
  // f from the documented tiers: full at max($1M, the plan's top floor), half
  // from the higher top floor to $1M. Old accounting charged (T_reform - T_current).
  const Y = 2029;
  const top14 = meta.presets.find((p) => p.name === "top_rate_14");
  const systems = resolveSpec(parseSpec(top14, meta), meta);
  const p = withParams({ eti: 0 });
  const r = score(p, systems, { scenarios: ["mid"], years: [Y] }).revenue[0];

  const { migration_elast: e, migration_phase_in_years: n } = meta.scenarios.mid;
  const base = prepareSystem(systems[Y].baseline, meta.food_excise);
  const reform = prepareSystem(systems[Y].reform, meta.food_excise);
  const u = unitArrays(pop, Y, "mid");
  const tR = unitNetTaxes(pop, reform, u.agi, u.item, u.cg);
  const tB = unitNetTaxes(pop, base, u.agi, u.item, u.cg);
  let lostWhole = 0, lostIncrease = 0;
  for (let i = 0; i < pop.n; i++) {
    const b = base.byStatus[pop.arrays.fs[i]], s = reform.byStatus[pop.arrays.fs[i]];
    const pp = 100 * (s.rates.at(-1) - b.rates.at(-1));          // 1 point, from 2027
    const loss = e * pp * Math.min(1, (Y - 2027 + 1) / n);
    const floor = s.floors.at(-1), lower = Math.max(floor, b.floors.at(-1));
    let f = 1;
    if (u.agi[i] >= Math.max(1e6, floor)) f = 1 - loss;
    else if (lower < 1e6 && u.agi[i] >= lower) f = 1 - loss / 2;
    lostWhole += u.weight[i] * (1 - f) * tR[i];
    lostIncrease += u.weight[i] * (1 - f) * (tR[i] - tB[i]);
  }
  assert.ok(lostWhole > 1e6, "someone moves");
  close(r["behavioral_$M"], r["static_$M"] - lostWhole / 1e6, "with response");
  assert.ok(Math.abs(r["behavioral_$M"] - (r["static_$M"] - lostIncrease / 1e6)) > 1, "not the old accounting");
});

test("rate cuts get no response, so a plan that only cuts scores the same both ways", () => {
  const cut = { name: "cut10", first_year: 2027, income_tax: { bracket_vintages: meta.current_law_vintages.map((v) => ({
    from: v.from, brackets: Object.fromEntries(Object.entries(v.brackets).map(([k, rows]) => [k, rows.map(([f, r]) => [f, r * 0.9])])) })) } };
  const res = score(pop, resolveSpec(parseSpec(cut, meta), meta));
  for (const r of res.revenue) {
    assert.ok(r["static_$M"] < 0);
    assert.equal(r["behavioral_$M"], r["static_$M"], `${r.scenario} ${r.tax_year}`);
  }
});

test("migrants from a temporary rise stay gone after the plan returns to current law", () => {
  // 15% top rate in 2027-2028, current law from 2029. From 2029 the plan taxes
  // everyone exactly as current law does (static 0), but part of the 2-point
  // rise's migration has not unwound (the fall phases in too), and those
  // filers' tax is lost. The old accounting scored this as 0.
  // (Keep only if that semantics is the intended one; see the score.py docstring finding.)
  const cl = meta.current_law_vintages;
  const top15 = Object.fromEntries(Object.entries(cl[0].brackets).map(([k, rows]) => [k, [...rows.slice(0, -1), [rows.at(-1)[0], 15]]]));
  const spec = { name: "temp15", first_year: 2027, income_tax: { bracket_vintages: [{ from: 2027, brackets: top15 }, cl[1]] } };
  const res = score(pop, resolveSpec(parseSpec(spec, meta), meta), { scenarios: ["mid"] });
  for (const r of res.revenue.filter((x) => x.tax_year >= 2029)) {
    assert.equal(r["static_$M"], 0, `${r.tax_year} static`);
    assert.ok(r["behavioral_$M"] < -1, `${r.tax_year} with response ${r["behavioral_$M"]}`);
  }
});
```
