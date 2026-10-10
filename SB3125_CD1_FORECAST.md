# SB 3125 (enacted as Act 24, SLH 2026) — Hawaii Income Tax Fiscal Impact Forecast
## Tax Years 2027–2031

> **Naming:** SB 3125 was signed by Gov. Josh Green on **May 21, 2026** and is
> now **Act 24, SLH 2026**. "SB 3125 CD1/CD2" throughout this document refer to
> the conference drafts as modeled; **CD2 is the enrolled text**, so CD2 results
> are the ones to cite as "Act 24." CD1 is retained for continuity — its bracket
> schedule is identical to CD2 and only the REEC credit model differs.

**Last updated:** October 9, 2026
**Analyst:** Hawaii Appleseed Center for Law and Economic Justice
**Model version:** CD2 vintage carryforward model + Round-2 REEC refinements (May 14, 2026), on the corrected Hawaii CPI basis (July 30, 2026).

> **Maintenance note:** This document must be updated whenever forecast methodology changes — including parameter recalibration, new behavioral channels, tax treatment corrections, or data source changes. Update the relevant section(s) and the Results table before committing.

---

## Calibration report and scenario parameter audit — October 9, 2026 (no result changes)

**No number in this document changes.** This entry records hygiene from a forecast
audit: the calibration now *reports* what it does, and four stale comments are
corrected. Every target, cap, clamp, tolerance and scenario parameter is as it was.

### What the calibration report exposes

`tax_modeler.calibration.report.CalibrationReport` (new) is returned by
`calibrate_via_rake` / `apply_ipf_calibration_via_rake(return_report=True)` and
`pipeline.calibrate(return_report=True)`, attached to the calibrated frame's
`attrs["calibration_report"]`, embedded in the calibrated-base artifact's metadata by
`forecast_sb3125_enhanced.py`, and filled per projection year by
`year_recalibrator.project_and_recalibrate` (`ForwardTargets.calibration_report`).
The four headline scripts (`forecast_sb3125_enhanced.py`, `forecast_sb3125_vs_fy26base.py`,
`forecast_cg_rate_options.py`, `forecast_working_family_credits.py`) print it and write
`calibration_report.json` next to their `manifest.json`. It records:

- **Convergence** — mode (`tolerance`, `fixed_point`, `max_iterations`), iterations, final
  max relative deviation. Non-convergence is in the report (and its `warnings`), not only
  in a log line.
- **Per-margin residuals** — target vs achieved for every cell of every margin, with the
  margin's **source year**, and whether the cell counts toward convergence.
- **Clip/cap and drop events** — the damped tax-total step's per-bin cap (code default
  5.0×; its docstring said 1.15× — corrected), the SOI AGI step's cap (code default 1.10×;
  docstring said 2.0× — corrected), the $1M+ tier rake's [0.5, 2.0] clamp, and every bin
  the 1.25× feasibility rule drops from the tax margin.
- **Weight dispersion** before and after — Kish design effect, effective sample size,
  max/mean and min/mean weight ratios, share of weight on the top 1% of units.
- **Statute-vs-COR wedge** per projection year.

**What it shows on the TY2023 base rake (HI 5-year 2020–24 cache, 39,996 units):**
the joint IPF stops at a *compromise fixed point* after 9 of 30 outer iterations and
is logged as converged, with a final max relative deviation of **0.188** against the 1%
tolerance. Filer counts are within 1% in only 2 of 14 counted brackets — $150–200K is
+18.8%, $500–750K +15.9%, $100–150K +15.9%; MFJ is +10.2%. Tax totals hit the $200K–$1M
bins exactly and sit at the +10% cap in $75K–$200K; three bins ($0–30K) are dropped
as infeasible. The SOI AGI step clips 18 times (all $100–200K, asked ×1.115, applied
×1.10). Weights: Kish deff 2.02 → 2.14, ESS 19,774 → 18,663, max/mean 18.3 → 19.7,
top-1% share 6.8% → 7.3%. The report now says so where before only a log line did.

**Per projection year (MID path, `project_and_recalibrate` with the SOI anchor and CBO
aging):** Phase 1 converges in 3–4 of 100 iterations (max dev 0.002 vs 0.005); the tier
rake's [0.5, 2.0] clamp never binds; the statute-vs-COR wedge is **0.871 in TY2027**
(statutory $2,674M vs COR $3,071M) and **1.053 in TY2031** ($3,423M vs $3,252M); weight
dispersion grows with the horizon (ESS 18,203 → 17,337 in TY2027, → 16,081 in TY2031;
max/mean 43.8 → 46.1 → 67.5; the SOI-tier synthesis zeroes 101–188 units). Forward
filer counts miss in the $500K–$1M brackets (TY2031: $500–750K −18.8%, $750K–$1M +32.2%),
which the synthesis-then-no-rerake design leaves as is.

### SOI/DOTAX year mismatch (made explicit; not changed)

`calibration/irs_soi_state_targets.py` supplies **TY2022** AGI margins
(`SOI_TAX_YEAR`), raked jointly with DOTAX **TY2023** counts, status and tax
(`dotax_base.BASE_YEAR`). The targets are used as published, with no growth factor
bridging the year. The report records each margin's source year and the orchestrator
warns once per process when they differ. The module docstring says how to close the gap
(TY2023 SOI extract + bump `SOI_TAX_YEAR`).

### Scenario parameter audit — October 9, 2026

`forecast_sb3125_enhanced.py` comments corrected to match code; values unchanged:

- The header's ETI line said 0.15 / 0.25 / 0.40; `BehavioralParams` uses **0.15 (HIGH
  scenario, weak response) / 0.40 (MID) / 0.60 (LOW, strong)**.
- RECESSION's comment said `migr=0.10, pte=0.70`; MID behavioral is **migr=0.0025,
  pte=0** (Act 58).
- **RECESSION carries `top_premium=0.013` against MID's `0.010`** while being described
  as "MID behavioral params". This is the pre-May-2026 MID value that was not lowered with
  MID's (noted under §8b on September 29, 2026 as the reason RECESSION outgrows MID in
  2030–31). It is undocumented as a deliberate choice. **Flagged, not changed:** the
  comment now says so, and the RECESSION rows in the results tables carry it.
- Not changed either: the §8a scenario table below lists top premium +1.3%/yr (MID),
  REEC nonrefundable utilization 65/80/100% and CGEC growth 2/3/4%/yr, where the script
  has 0.3/1.0/2.3%, 50/65/80% and 1/1.5/2.5%. The script is the source of truth; the
  table is stale and should be reconciled in a results-bearing entry.

---

## DOTAX TY2023 base and the TY2024 EITC report — October 8, 2026 (supersedes the Act 24 tables below)

DOTAX published *Hawaiʻi Individual Income Tax Statistics, Tax Year 2023*
(October 2026, `files.hawaii.gov/tax/stats/stats/indinc/2023indinc.pdf`) and its
*Earned Income Tax Credit Report, Tax Year 2024* (December 2025,
`.../act107_2017/act107_earnedincome_txcredit_2024.pdf`). The model had anchored
on the TY2022 edition of the first; this rebases it on TY2023, anchors the $1M+ class
on a TY2019–2023 window rather than one edition (below), and anchors the state EITC
on the second. Every table in this section is a full rerun
(`forecast_sb3125_enhanced.py --cd 2`, `forecast_act24_vs_pre_act46.py`,
`forecast_sb3125_vs_fy26base.py --cd 2`, `forecast_cg_rate_options.py`,
`scripts/build_simulator_population.py`, `forecast_working_family_credits.py`). The
suite passes at 2,735 / 2 skipped (was 2,701 / 2), the simulator's JS tests at
138 / 138, and the simulator build re-derives the Act 24 run and the
capital-gains page on the new base.

**The headline moves, and one number drives it.** MID's five-year total falls
**$810.4M → $687.8M (−15%)** and its static bracket gain **$443.2M → $309.6M
(−30%)**. The $1M+ class owed **$441M on 1,704 returns in TY2023 against $662.6M on
1,824 in TY2022**, and the Act 24 bracket gain is the small net of a top-bracket gain
and middle-bracket cuts, so a smaller top tail moves it a long way. On TY2023 alone the
fall would be −31% (MID $555.6M). **The class is anchored on a TY2019–2023 window
instead: 1,633 returns and $562.3M of tax before credits**, the window's mean share of
resident returns and of resident tax on TY2023's printed totals. Its tax is $413M,
$415M, $799M, $663M and $441M in those five editions, with no trend (+0.2 points of its
share a year, standard error 1.6) and no year-to-year persistence (lag-1 autocorrelation
−0.07), so the mean of more years is the better estimate of its expected level. One
year's anchor carries the whole series' spread, about ±$141M on MID's five-year total,
and the five-year mean about ±$63M, if the five years are draws around a stable mean,
which five points cannot confirm (*Sensitivity* below). The tail's scale factor k is
1.3514 (MID) against 1.4210 on the TY2022 base and 1.0410 on TY2023 alone, and the Act 46
baseline is $2,514.2M in TY2027 ($2,523.0M published; $2,363.7M on TY2023 alone).
**The $1M+ class is almost all of the move.** Setting only it back to its TY2022
values, with every other anchor on TY2023, gives MID $795.0M, so it accounts for
$239.4M of the $254.8M drop from the published figure to TY2023 alone (94%) and all the
other TY2023 anchors together for $15.4M. The response is close to linear, **about
$1.08M of five-year gain per $1M of the class's tax**; the line predicted $686.4M for
the window and the run gave $687.8M.

**What changed in the code.** `calibration/dotax_base.py` is now the one home for
the DOTAX anchors (it replaces five hand-typed copies and two older
orchestrators). It reads `data/calibration/dotax_indinc_2023.json` and
`dotax_eitc_2024.json`, which `scripts/parse_dotax_indinc.py` and
`scripts/parse_dotax_eitc_report.py` write from the PDFs, each table checked
against its own printed total. The TY2022 constants are kept, so
`build_targets(..., base_year=2022)` reproduces the earlier base. Rebased:
`forward_targets` (counts, tax, filing status; growth now from 2023),
`forward_agi_targets`, `simultaneous_calibrator`, both IPF orchestrators,
`year_recalibrator`, `scenarios/top_income_synthesis` (the $1M+ and $500K–$1M
targets, and the tail's aging now 2023 → 2024), and `calibration/cg_anchor` (Table
21 gains, class returns, `cg_growth` from the base year). The CBO table is
relative to 2022, so `CBOComponentRates.growth(component, year, from_year)` takes
the ratio; the SOI tier averages still age from their own year, 2022.

**The $1M+ class's anchor.** `dotax_base.TOP_CLASS_WINDOW` (TY2019–2023) sets the
class's returns and tax at the base year to the window's mean share of all resident
returns and of resident tax before credits, on TY2023's printed totals: 1,633 returns
and $562.3M, against 1,704 and $441M as printed. Shares, not levels, because TY2019–20
were smaller-tax years. Every accessor reads the same rows, so the class table, its
total, the filer and filing-status totals, the class ranks and the AGI split follow.
The series is `data/calibration/dotax_a8_top_class.json`, which
`scripts/parse_dotax_top_class.py` writes from the five editions' Table A-8 (TY2019–2022
from `indinc/archive/`), each checked against its printed totals.
`DOTAX_TOP_CLASS_WINDOW=none` gives the edition as printed and `YYYY-YYYY` another
window; the environment is how the spawned scenario workers see it.

| Anchor (DOTAX, residents) | TY2022 | TY2023 |
|---|---:|---:|
| Returns, all classes (A-8) | 635,117 | 644,631 |
| Filers, Loss class excluded | 621,026 (the model carried 618,423) | 631,054 (631,125 as printed) |
| Tax before credits | $3,029M | $2,960M |
| $1M+ returns / tax before credits | 1,824 / $662.6M | **1,633 / $562.3M** (TY2019–2023 window; 1,704 / $441M as printed) |
| $400K+ returns (A-8) | 8,875 | 8,934 |
| $400K+ taxable-return AGI (A-1) | $11,149M | $9,319M |
| Net long-term gains, $400K+ class (Table 21) | $2,210M | $2,041M |
| Net long-term gains, all classes | $2,995M | $2,646M |
| Filing status: single / joint / head / separate | 335,198 / 216,358 / 67,393 / 16,007 | 341,973 / 216,407 / 69,345 / 16,718 |
| Nonresident liability before credits | $300M | $283M |

The TY2022 column was checked against the TY2022 edition's own Table A-8
(`indinc/archive/2022indinc.pdf`; the filing-status and nonresident rows are the 2022
figures the TY2023 report prints). **The old hand-typed TY2022 table was wrong in six
classes.** `forward_targets` carried 8,233 returns at $400K+ against A-8's 8,875, while
`cg_anchor` carried A-8's own classes, which were right. The hand-typed counts differ at
$30K–$40K (58,135 against 59,827), $200K–$300K (19,015 / 18,937), $300K–$400K (5,729 /
6,076), $400K–$500K (2,856 / 2,926), $500K–$750K (2,549 / 2,991) and $750K–$1M (1,004 /
1,134): 2,603 returns in all, 618,423 against A-8's 621,026 without the Loss class. The
tax column and the $1M+ row (1,824 / $663M) were right. The September 30 tables
therefore rest on slightly wrong filer counts; the TY2023 targets use A-8 as printed.
The $400K+ AGI is split among its four classes by A-8's tax before credits over
its effective rate on Hawaiʻi AGI, scaled to A-1's $9,319M (`dotax_base` docstring).

**CD2 vs Act 46 baseline, post-behavioral ($M)** (the September 30 figures in
brackets):

| Tax Year | LOW | **MID** | HIGH | RECESSION |
|---|--:|--:|--:|--:|
| 2027 | $81.3M [$105.1M] | **$88.7M [$111.1M]** | $117.5M [$144.9M] | $86.0M [$108.2M] |
| 2028 | $97.2M [$121.1M] | **$117.8M [$140.9M]** | $152.4M [$180.3M] | $116.5M [$139.9M] |
| 2029 | $109.2M [$132.3M] | **$133.7M [$158.8M]** | $175.7M [$203.7M] | $134.4M [$160.1M] |
| 2030 | $136.5M [$159.2M] | **$169.3M [$194.9M]** | $214.6M [$245.5M] | $171.5M [$198.1M] |
| 2031 | $147.8M [$170.0M] | **$178.4M [$204.6M]** | $230.5M [$262.4M] | $182.0M [$209.1M] |
| **5-year total** | **$572.1M [$687.6M]** | **$687.8M [$810.4M]** | **$890.6M [$1,036.9M]** | **$690.4M [$815.3M]** |

The credit overlay is unchanged ($456.0M over five years, MID): it reads DOTAX's
*Tax Credits Claimed*, not this report. RECESSION still runs above MID in 2030–31,
as before.

**MID by component ($M):**

| Tax Year | Act 46 baseline | Static bracket | Behavioral response | Bracket (post-behav.) | Credit total | **Total** |
|---|--:|--:|--:|--:|--:|--:|
| 2027 | $2,514.2M | $50.7M | −$11.1M | $39.7M | $49.0M | **$88.7M** |
| 2028 | $2,679.8M | $56.0M | −$12.8M | $43.3M | $74.5M | **$117.8M** |
| 2029 | $2,511.8M | $61.2M | −$16.0M | $45.2M | $88.5M | **$133.7M** |
| 2030 | $2,623.0M | $67.5M | −$18.0M | $49.5M | $119.8M | **$169.3M** |
| 2031 | $2,698.9M | $74.2M | −$20.0M | $54.2M | $124.2M | **$178.4M** |
| **5-year** | | **$309.6M** | **−$77.8M** | **$231.8M** | **$456.0M** | **$687.8M** |

Was: static $443.2M, behavioral −$89.2M, bracket $354.1M, total $810.4M. The tail is
calibrated to $562.3M in place of $663M: k is 1.6627 / 1.3514 / 1.1740 / 1.3514
(LOW / MID / HIGH / RECESSION) where it was 1.7743 / 1.4210 / 1.2343 (and
RECESSION's MID value), aged ×1.0232 / 1.0303 / 1.0436 / 1.0334 from 2023 to 2024
(B19013 105,205 / 103,131 = 1.0201, times (1 + premium)). The calibrated PUMS base
holds 634 weighted $1M+ filers before synthesis against the 1,633 target.

**Who pays (MID TY2027).** The top fifth carries the change (the September 30
figures in brackets):

| Fifth | Avg. total | Total $M | % paying more |
|---|---:|---:|---:|
| Q1 (bottom 20%) | **+5 [+6]** | +0.45 [+0.57] | 1.3% [1.3%] |
| Q2 | **−39 [−37]** | −3.87 [−3.69] | 2.4% [2.4%] |
| Q3 | **−45 [−41]** | −4.48 [−4.10] | 2.9% [2.9%] |
| Q4 | **−41 [−36]** | −4.09 [−3.58] | 3.9% [3.9%] |
| Q5 (top 20%) | **+892 [+1,126]** | +88.15 [+111.26] | 23.5% [21.8%] |

**Act 24 against pre-Act-46 (2017) law, five years ($M)**
(`forecast_act24_vs_pre_act46.py`; column C ties to the page's static change):

| Tax Year | A: Act 46 banked ≤2026 | B: Act 46 remaining | C: Act 24 increment | **TOTAL vs pre-Act-46** | memo: vs frozen 2026 |
|---|---:|---:|---:|---:|---:|
| 2027 | −571.5 | −246.6 | +50.7 | **−767.4** | −195.9 |
| 2028 | −580.0 | −266.7 | +56.0 | **−790.8** | −210.7 |
| 2029 | −585.3 | −525.3 | +61.2 | **−1,049.4** | −464.1 |
| 2030 | −593.6 | −549.4 | +67.5 | **−1,075.5** | −481.9 |
| 2031 | −598.4 | −580.3 | +74.2 | **−1,104.6** | −506.1 |
| **5-year** | **−2,928.9** | **−2,168.3** | **+309.6** | **−4,787.6** | **−1,858.7** |

Was: A −$2,758.8M, B −$2,032.5M, C +$443.3M, total −$4,348.0M. Act 46's cost is
$170M (A) and $136M (B) larger over five years on this base, from the other anchors,
not the $1M+ class (see *Sensitivity* below).

**Act 24 against Act 46 frozen at TY2026 (the ITEP frame), CD2, $M**
(`forecast_sb3125_vs_fy26base.py --cd 2`; this path replaces the Pareto tail with
SOI tiers sized to the forward $1M+ count and tax, so it moves through the
forward targets, not the tail calibration):

| TY | Bracket | SD expansion | Total | ITEP | Gap |
|---|---:|---:|---:|---:|---:|
| 2027 | −167.8 | 0.0 | −167.8 | −227.0 | +59.2 |
| 2028 | −156.1 | −16.0 | −172.2 | −258.0 | +85.8 |
| 2029 | −379.1 | −15.3 | −394.4 | −534.0 | +139.6 |
| 2030 | −365.9 | −31.0 | −396.8 | −563.0 | +166.2 |
| 2031 | −344.2 | −64.4 | −408.6 | −622.0 | +213.4 |
| **5-yr** | **−1,413.1** | **−126.7** | **−1,539.8** | **−2,204.0** | **+664.2** |

Was −$1,429.2M and a gap of +$774.8M (about 35% below ITEP; now about 30%). This
frame moves the other way from the one above (more revenue foregone, −$110.6M over
five years); the $1M+ anchor is a small part of it, within about ±$20M across every
anchor tried (*Sensitivity* below). The statute-vs-COR wedge, a diagnostic, barely
moves: 0.871 / 0.954 / 0.975 / 1.009 / 1.053 (was 0.883 / 0.960 / 0.985 / 1.016 / 1.058).

**Capital-gains page.** The base is DOTAX's TY2023 resident gains ($2,646M
against $2,995M) grown from 2023, so TY2027's anchored gains fall $4,111M →
$3,375M (−18%). Under Act 24 the ordinary-rate option's static
revenue goes $169.8M → $146.1M in 2027 and $214.7M → $182.7M in 2031; the 9% cap
option $57.8M → $49.1M and $72.1M → $60.8M. The nonresident add-on keeps its "TY2022 level"
column on TY2022 nonresident gains (the spike year, grown from 2022); the "typical
year" column now pools TY2018–2023 and rides the TY2023 resident base.
`TOP_SHARE_1M` stays 0.80: the same arithmetic on TY2023 gives about 76% of Table
21's $400K+ gains (81% net of the $400K–$1M classes), inside the 70–80% range the
page already shows (`cg_anchor._TOP_SHARE_NOTE`). The $1M+ window moves this page by at
most $0.15M a year (0.1%): its base is Table 21's gains, not the class's tax.

**State EITC anchored on the TY2024 report** (`forecast_working_family_credits.py`).
The EITC report gives the credit by federal AGI range, residency and filing status
for TY2024: 78,399 *new* claims worth $76,981,028; the credit *applied*,
$78,514,304 on 80,279 claims, adds legacy nonrefundable carryforwards (TY2018–2022)
that the 40% rate does not touch and Act 25 (2025) ends, so the **new credit is the
anchor**. The factor now fits TY2024 dollars on the TY2024 population (0.981; was
1.025 fitted on TY2023's $77.054M against a base that ran $1.9M short). The
food/excise factor still fits TY2023 (0.7455), its credit's latest report.

| TY | EITC loss | Food/excise loss | **Total** |
|---|---:|---:|---:|
| 2028 | $42.8M [$44.8M] | $33.7M | **$76.5M [$78.4M]** |
| 2029 | $43.5M [$45.5M] | $32.2M | **$75.7M [$77.7M]** |
| 2030 | $44.8M [$46.9M] | $31.6M | **$76.5M [$78.5M]** |
| 2031 | $47.4M [$49.6M] | $30.8M | **$78.2M [$80.4M]** |
| **TY2028–31** | **$178.5M [$186.8M]** | **$128.3M** | **$306.8M [$315.1M]** |

Poverty (TY2028) is unchanged at 2,127 more people (521 children), the gap $16.0M
(was $16.2M). Checks against the report (`eitc_check_ty2024.csv`; page Tables 5–7):
dollars by federal AGI range are within about 11% in each of the four ranges under
$55,000 (the model puts $1.4M at $55,000+ against $0.8M); by filing status the
model gives heads of household 51.5% of dollars against 55.5%, **married couples
41.8% against 28.9%, and single filers 6.6% against 15.3%**. Claims run 5.5% high
(82,672 modeled against 78,399, all filers, with the model residents-only, whose
share of claims is 94.6%). The total is fitted, so the composition gap moves losses
between family types, not the statewide figure; a by-range or by-status reweight
would be the fix and is not done. Out of sample, TY2023 is $73.7M against Tables
A-1's $77.1M and the EITC report's $75.6M applied (the two DOTAX reports differ by
2% for the same year). `forecast_hi_eitc_revert_20.py` and the admin-caseload table
carry the same anchor (`hi_eitc`, 2024: 78,399 claims, $76.981M).

**Not changed, and what to know.**
- **Unit construction still targets TY2022 filing-status shares**
  (`units/constructor._calculate_hybrid_weight`, `status/irs_based.calibrate_to_soi_totals`;
  hand-tuned factors). The shares moved under a point (single 52.8% → 53.0%, joint
  34.1% → 33.6%, head 10.6% → 10.8%), and the forecast path re-rakes to the
  TY2023 status targets afterwards. The unit cache (built August 3) was not rebuilt.
- `calibration/nonresident_uplift.py` (no callers) has its anchors on TY2023 but its
  bracket uplift factors were derived on TY2022's Hawaiʻi-AGI table and not
  re-derived (TY2023 prints nonresident liability by worldwide AGI only).
- The REEC/CGEC credit overlay and every table that reads *Tax Credits Claimed* are
  unchanged: this report holds no credit-type detail.
- `deductions/parsers.py` still reads TY2022 raw CSVs (not present in this checkout).
- **Every run and the simulator build are on `493ef76`**, the commit that holds the
  window, so the manifests and the simulator's endnote cite it. The working-family
  figures did not move with the window (that model reads none of these anchors); they
  reproduce the earlier run exactly.

**Sensitivity: the $1M+ anchor (October 8–9, 2026).** The $1M+ class (Table A-8's top
row: returns and tax before credits) is the anchor Act 24 leans on, so this reruns
Act 24 with only that row changed and everything else on TY2023: the gains base, the
filer and filing-status targets, the class ranks. The row, as each edition prints it
(TY2019–2022 from `files.hawaii.gov/tax/stats/stats/indinc/archive/<year>indinc.pdf`,
parsed by `scripts/parse_dotax_top_class.py` into `dotax_a8_top_class.json`):

| TY | $1M+ returns | Tax before credits | All resident tax | $1M+ share |
|---|---:|---:|---:|---:|
| 2019 | 1,163 | $413M | $2,462M | 16.8% |
| 2020 | 1,387 | $415M | $2,578M | 16.1% |
| 2021 | 2,086 | $799M | $3,155M | 25.3% |
| 2022 | 1,824 | $663M | $3,029M | 21.9% |
| 2023 | 1,704 | $441M | $2,960M | 14.9% |

The variants. Those marked *level* were run first, on the edition as printed, as simple
averages of the levels (returns and tax each); the window is what the tables above use:

| $1M+ class | Returns | Tax before credits |
|---|---:|---:|
| TY2023 alone, as printed | 1,704 | $441.0M |
| 5-year average, TY2019–2023 (*level*) | 1,633 | $546.2M |
| 2-year average, TY2022–2023 (*level*) | 1,764 | $552.0M |
| **5-year window, share-normalized (the tables above)** | **1,633** | **$562.3M** |
| 3-year average, TY2021–2023 (*level*) | 1,871 | $634.3M |
| TY2022, the rest TY2023 | 1,824 | $663.0M |

`scripts/sensitivity/sitecustomize.py` rewrites that row as `dotax_base` reads it (the
*level* variants and TY2022), in every process including the scenario workers (7
processes per variant across the three runs, confirmed); its docstring has the
commands, one scratch copy of the scripts per variant because they write fixed paths.
The window itself needs no override: `DOTAX_TOP_CLASS_WINDOW=2021-2023` is the
share-normalized 3-year window. In the TY2022 variant the tail's scale factors come out
at 1.7743 / 1.4210 / 1.2343, the values this document recorded on the TY2022 base.
LOW / MID / HIGH / RECESSION for the others: window 1.6627 / 1.3514 / 1.1740 / 1.3514;
5-year level 1.6193 / 1.3157 / 1.1429 / 1.3157; 2-year 1.5243 / 1.2374 / 1.0750 /
1.2374; 3-year 1.6393 / 1.3322 / 1.1573 / 1.3322.

**Act 24 vs Act 46, five-year total impact ($M):**

| $1M+ anchor | LOW | **MID** | HIGH | RECESSION |
|---|--:|--:|--:|--:|
| TY2022 on every anchor (published September 30) | 687.6 | 810.4 | 1,036.9 | 815.3 |
| TY2022 for the $1M+ class, rest TY2023 | 673.0 | 795.0 | 1,015.3 | 797.9 |
| 3-year average, TY2021–2023 (*level*) | 633.0 | 763.2 | 980.9 | 763.6 |
| **5-year window, share-normalized (the tables above)** | **572.1** | **687.8** | **890.6** | **690.4** |
| 2-year average, TY2022–2023 (*level*) | 554.9 | 675.0 | 877.1 | 677.6 |
| 5-year average, TY2019–2023 (*level*) | 555.7 | 670.8 | 870.8 | 673.3 |
| TY2023 alone, as printed | 449.5 | 555.6 | 733.9 | 557.8 |

Static bracket gain, five years, MID: $443.2M published, $428.0M (TY2022 class), $393.3M
(3-year), **$309.6M** (window), $296.1M (2-year), $290.9M (5-year level), $165.0M (TY2023
alone). MID by year (total / static bracket, $M):

| Tax Year | TY2022 class | 3-year (*level*) | **Window** | TY2023 alone |
|---|---:|---:|---:|---:|
| 2027 | 108.7 / 72.5 | 101.7 / 65.0 | **88.7 / 50.7** | 62.6 / 22.7 |
| 2028 | 136.2 / 76.4 | 131.3 / 71.1 | **117.8 / 56.0** | 90.8 / 26.9 |
| 2029 | 155.8 / 85.5 | 151.0 / 80.1 | **133.7 / 61.2** | 108.1 / 33.1 |
| 2030 | 192.2 / 92.9 | 184.8 / 84.8 | **169.3 / 67.5** | 142.9 / 38.3 |
| 2031 | 202.1 / 100.7 | 194.4 / 92.3 | **178.4 / 74.2** | 151.1 / 43.9 |

The response is close to linear in the class's tax: the line through the two ends (MID
$555.6M at $441.0M, $795.0M at $663.0M) is **$1.078M of five-year gain per $1M**. It
predicted $669.0M for the 5-year level average (run: $670.8M), $675.3M for the 2-year
($675.0M), $764.1M for the 3-year ($763.2M) and $686.4M for the window before it was
run ($687.8M). The Act 46 baseline (MID, TY2027) is $2,363.7M (TY2023 alone), $2,492.9M
(5-year level), $2,510.0M (2-year), $2,514.2M (window), $2,606.8M (3-year) and
$2,645.2M (TY2022 class), against $2,523.0M published.

**Why a window, and why shares.** The class's share of resident tax is 16.8%, 16.1%,
25.3%, 21.9%, 14.9% (mean 19.0%, standard deviation 4.4 points, about $131M of the
class's tax on TY2023's $2,960M). There is no trend (+0.2 points a year, standard error
1.6) and no persistence (lag-1 autocorrelation −0.07). If the five years are draws
around a stable mean, a single year's anchor carries that whole spread (about ±$141M on
MID's five-year total at $1.08M per $1M), the 5-year mean 1/√5 of it (±$63M) and the
3-year mean 1/√3 (±$82M). A one-step-ahead backtest on the only two years with three
prior years (2022–23) has mean absolute errors of 5.2, 4.9 and 4.3 share points for last
year, a 2-year and a 3-year mean: longer windows slightly better, on two test points.
The assumption cannot be tested on five observations, and a persistent shift behind
TY2023's low would make the window overstate the class; TY2024's statistics (late
2027) are what would show it. A 3-year window gives TY2021's $799M a third of the
weight and the 5-year window a fifth. Shares, because TY2019–20 were smaller-tax years
(resident tax $2.46–2.58B against $2.96B in TY2023), so an average of nominal levels
understates TY2023's scale: the 5-year level average is $546.2M, the share-normalized
window $562.3M. Against the published $810.4M the window gives $687.8M (−15%), and
against TY2023 alone (+24%).

**The other two frames do not depend on the class the same way** (five years, $M):

| | Published | TY2022 class | 3-year (*level*) | **Window** | TY2023 alone |
|---|--:|--:|--:|--:|--:|
| Act 24 vs Act 46 frozen at TY2026 (ITEP frame) | −1,429.2 | −1,506.6 | −1,498.4 | **−1,539.8** | −1,527.4 |
| Gap to ITEP | +774.8 | +697.4 | +705.6 | **+664.2** | +676.6 |
| Pre-Act-46 (2017) law: A + B, Act 46's cost | −4,791.3 | −5,112.4 | −5,115.9 | **−5,097.2** | −5,101.9 |
| Pre-Act-46: C, Act 24 increment | +443.3 | +427.9 | +393.3 | **+309.6** | +164.9 |
| Pre-Act-46: total | −4,348.0 | −4,684.4 | −4,722.6 | **−4,787.6** | −4,936.9 |

On the Act 24 increment the class accounts for $239.4M of the $254.8M drop from the
published figure to TY2023 alone (94%); every other TY2023 anchor together, $15.4M, a
figure that includes the correction of the old hand-typed counts above. In the frozen
frame the class accounts for $20.8M of the $98.2M move to TY2023 alone (21%) and the
other anchors for $77.4M (79%); the window sits within about ±$20M of every other
anchor there. In Act 46's cost it barely matters (A + B: −$321.1M from the other
anchors, +$10.5M from the class, −$310.6M in all). So the $1M+ anchor matters for the
Act 24 increment, and the other TY2023 anchors, the class counts and tax below $1M, for
how much Act 46 costs and for the ITEP-frame gap. The decomposition is one path
(published → the rest at TY2022's class → the class); taken in the other order it would
differ by the interaction, which was not measured.

**What it does not cover.** The *level* variants were run before the window existed, on
the edition as printed. The capital-gains page and the simulator were not rerun under
any variant but the window. The class's tax also shifts how Table A-1's $400K+ AGI is
split among its four classes (a side effect; `year_recalibrator` rakes the $1M+ tiers,
not the bracket-level AGI targets, by default), and the gains base stays on TY2023. It
varies one row: it does not average the other classes, the gains base or the
filing-status targets. It does not say which year is right.

---

## Council on Revenues, September 3, 2026 vintage — October 5, 2026 (review only; no model result changes)

COR met on **September 3, 2026** (letter dated September 9). The monthly
refresh picked the meeting up on September 23 (`cor_iit_projections.json`,
issue #13), so the COR-scaled columns in this document's tables were already on
this vintage. This section is the human review that issue asked for: what COR
changed, what its letter now says about Acts 46 and 24, and whether the FY→TY
mapping still holds. **The review itself moves no headline table.**

**What COR changed.** It raised its General Fund growth forecast for FY2027
from 1.0% to 2.5% and for FY2028 from 1.9% to 3.0%, revised FY2029-FY2032, and
added FY2033 (3.7%). It cited slow tourism recovery, inflation, historical
growth trends, federal policy changes, the war in the Middle East, and the
multi-year phase-in of the income tax cuts.

| Fiscal year | Growth, May 21 → Sept 3 | General Fund tax revenue, May 21 → Sept 3 ($M) |
|---|---|---:|
| 2027 | 1.0% → 2.5% | 9,822.1 → 10,037.9 |
| 2028 | 1.9% → 3.0% | 10,008.7 → 10,339.1 |
| 2029 | 2.5% → 3.0% | 10,258.9 → 10,649.2 |
| 2030 | 1.8% → 1.7% | 10,443.6 → 10,830.3 |
| 2031 | 3.1% → 3.7% | 10,767.3 → 11,231.0 |
| 2032 | 3.4% → 3.3% | 11,133.4 → 11,601.6 |
| 2033 | new: 3.7% | new: 12,030.9 |

**The individual income tax line this model uses** (DOTAX attachment 1, $M by
tax year, TY(n) = FY(n+1); the table in *COR projections are now
auto-refreshed* is the March 10 → May 21 move):

| TY | May 21 | Sept 3 | Δ | % |
|---|---:|---:|---:|---:|
| 2025 (preliminary) | 3,139.079 | 3,120.844 | −18.235 | −0.6% |
| 2026 | 2,923.065 | 3,050.119 | +127.054 | +4.3% |
| 2027 | 2,874.134 | 3,070.958 | +196.824 | +6.8% |
| 2028 | 2,872.305 | 3,086.701 | +214.396 | +7.5% |
| 2029 | 2,780.166 | 3,002.744 | +222.578 | +8.0% |
| 2030 | 2,881.860 | 3,138.371 | +256.511 | +8.9% |
| 2031 | 2,971.795 | 3,252.054 | +280.259 | +9.4% |
| 2032 | none | 3,421.492 | new | |

**The FY→TY mapping still holds.** The attachment's span moved one year
(FY2024-FY2032 → FY2025-FY2033). The parser read all nine columns, and the
bundled values match the attachment's Individual Income Tax row exactly, so
`FY(n+1) = TY(n)` is unchanged: TY2027 is FY2028. The new FY2033 column is
TY2032, past this model's TY2027-TY2031 horizon.

**Act 46: COR's estimate did not change.** The letter's General Fund loss
figures ($596.6M in FY2026 through $1,453.2M in FY2032) are numerically
identical to the May 21 letter's, so the Act 46 cross-check (the model 20.7%
below over five years, in the September 29 section) and the *Practical
guidance* under the ITEP reconciliation stand.

**Act 24: COR's first estimate.** The May 21 letter predates the act. The
September 9 letter adjusts the forecast for it, as DOTAX's estimate of the
whole act: the bracket changes (second bracket 3.2% → 2.5%, third 5.5% → 5%, a
new 13% top bracket) and its credit changes (the RETITC, Capital Goods Excise,
Renewable Fuels Production, High Technology Business Investment, Research
Activities and Technology Infrastructure Renovation credits). The estimated
gain to the General Fund is $145.2M in FY2028, $222.7M in FY2029, $233.5M in
FY2030, $284.7M in FY2031, $297.3M in FY2032 and $308.9M in FY2033. Against
this model (CD2, post-behavioral bracket change plus the credit overlay, $M; TY
n is FY n+1):

| TY (FY) | DOTAX | LOW | MID | HIGH | MID as % of DOTAX |
|---|---:|---:|---:|---:|---:|
| 2027 (2028) | 145.2 | 105.1 | 111.1 | 144.9 | 76.5% |
| 2028 (2029) | 222.7 | 121.1 | 140.9 | 180.3 | 63.3% |
| 2029 (2030) | 233.5 | 132.3 | 158.8 | 203.7 | 68.0% |
| 2030 (2031) | 284.7 | 159.2 | 194.9 | 245.5 | 68.5% |
| 2031 (2032) | 297.3 | 170.0 | 204.6 | 262.4 | 68.8% |
| **5-year** | **1,183.4** | **687.6** | **810.4** | **1,036.9** | **68.5%** |

DOTAX's figure is above MID in every year and above HIGH in every year but the
first (TY2027: HIGH $144.9M against $145.2M). The shape agrees (DOTAX's
roughly doubles from FY2028 to FY2032; MID rises 1.8×). Two known differences
could account for the level gap, and the letter alone cannot separate them: the
model's levels run 13–18% below COR (*Baseline Validation*, updated below), and
DOTAX's figure includes three credit provisions the overlay does not score (the
Renewable Fuels Production, High Technology Business Investment and Technology
Infrastructure Renovation credits; the overlay covers REEC, CGEC and TCRA).
Treat DOTAX's estimate as an upper reference for MID, not a calibration target.

**Where the model uses the vintage.** `cor_scale_factor_for_year` feeds only
the parallel diagnostic columns (`bracket_delta_cor_scaled_$M`, `*_cor_$M`).
The published columns were produced after the September 23 refresh and already
carry this vintage: the TY2027 factor is 1.217 (COR $3,071.0M over the MID Act
46 baseline of $2,522.9M), up from 1.139 on the May 21 vintage, and TY2031 is
1.185 (was 1.083). Nothing in the headline tables uses it.

**A second, stale copy of the vintage, now fixed.** `calibration/forward_targets.py`
kept its own hand-typed COR dict, left on the **March 10** vintage when
`scenarios/quintile_analysis.py` moved to the bundled file, and the guard test
covered only the other copy. A new COR meeting therefore never reached the
forward targets, and the *statute-vs-COR wedge* the recalibrator logs was
computed against a vintage two meetings old. It now loads from the bundled file
(the literal is a missing-file fallback) and `test_cor_iit.py` pins it. **No
published table depends on it**: the frozen-baseline table and the
capital-gains page rerun identically (the $1M+ synthesis only logs its COR tax
target, and the Phase 2 tax multipliers are not used by the scenario scripts,
which re-score statutorily). What changes is the diagnostic, statutory tax over
the COR target for TY2027–TY2031: 0.960 / 1.052 / 1.076 / 1.119 / 1.168 →
0.883 / 0.960 / 0.985 / 1.016 / 1.058.

**Data drift since the published run.** The tables in this document come from
the October 1 runs. Rerun on the October 5 refreshed anchors (BEA per-capita
income, LAUS, the regenerated calibration), the Act 24 bracket columns are
identical and the credit overlay moves by at most $0.11M a year, taking MID's
five-year total from $810.4M to $810.1M (−0.04%). That is the monthly refresh
feeding REEC growth, not the COR work; the published figures are unchanged.

**The October 6 anchor replacement also reaches these tables, by one path.**
Replacing the PCE, HPI and QCEW anchors (METHODOLOGY.md, "Annual ACS anchors")
moved the ensemble's B25077 home-value forecast, whose growth factor scales the
mortgage-interest tiers of the itemized deduction; the income-growth path is
unaffected, and an earlier version of that section said the tax model as a whole
was. Rerun end to end on the commits either side of it (CD2, all four
scenarios), the Act 46 baseline moves by up to $2.0M a year (0.07%; MID +$0.6M in
TY2027, −$2.0M in TY2031) and the Act 24 bracket and total-impact columns by at
most $0.05M, so 10 of the 60 headline cells differ by 0.1 in the first decimal.
MID's five-year static gain goes from $443.25M to $443.22M and its total impact
from $810.10M to $810.15M. The tables above were not regenerated for it. The
later HUD rent-anchor correction moves nothing the tax model reads (its
home-value and poverty factors are bit-identical).

---

## CBO Social Security aging — September 30, 2026 (supersedes the frozen-baseline table below; its Act 24 figures were superseded October 8, 2026)

The September 29 pipeline audit confirmed a defect in the per-component CBO
aging (`age_filers_with_components`) that `forecast_sb3125_vs_fy26base.py`,
`forecast_cg_rate_options.py`, `forecast_working_family_credits.py` and
`forecast_bill_quintile.py` use through `use_cbo_aging=True`. It is fixed. The
code change is one commit on top of the published build, and the Act 24 files
came back byte-identical, so every difference below comes from it.

**The defect.** The aging splits each unit's `income` into CBO components,
grows each at its own rate and rewrites `income` as the sum. The retirement
component held RETP plus *all* of Social Security, SSI and public assistance,
but `income` holds 85% of Social Security and neither of the others, and the
residual `other` component is floored at zero, so the excess was never netted
out. Aging to the base year with **no growth** raised weighted income 1.55% on
the tax-unit cache ($61,659M → $62,613M) and 2.04% on the calibrated base
($45,343M → $46,268M). 12,328 of 39,990 units moved, every one with Social
Security, SSI or public assistance, and units living on SSI or TANF alone, with
no income, were handed income and taxed on it. The test fixture counted full
Social Security inside `income` and carried no SSI or public assistance, which
hid it.

**The fix.** Retirement is now RETP plus the 85% of Social Security `income`
counts, so the components sum to `income` and a base-year round trip is exact
(asserted in `test_cbo_aging.py`; a warning fires if a decomposition ever sums
more than 0.1% away from `income`). SPM money income still ages every dollar of
total cash income: the untaxed 15% of Social Security, SSI and public
assistance, which `income` does not hold, are aged separately at the retirement
rate, where before they aged by sitting in the retirement bucket. The one
change to money income is that OIP now ages for units where the old floor
swallowed it (+$13M weighted on the TY2028 PUMS, +0.02%).

**What moved.**

| Output | Before | After |
|---|---|---|
| Act 24 vs Act 46, vs pre-Act-46, fifths and income classes (CD2) | | **unchanged** (identical files): they project through `project_units` (county growth and the top-income premium), not CBO aging |
| Tax simulator population | | unchanged (only the `model_version` hash and provenance stamps) |
| Act 24 vs Act 46 frozen at TY2026, 5-year total | −$1,435.6M | −$1,429.2M |
| Gap to ITEP over five years | +$768.4M | +$774.8M (still about 35% below) |
| Working-family credits, revenue loss from reverting, TY2028–2031 | $314.2M | $315.1M |
| People pushed below the poverty line, TY2028 (children) | 2,036 (555) | 2,127 (521) |
| Poverty rate, renewed / expired law, TY2028 | 8.46% / 8.61% | 8.52% / 8.68% |
| Poverty gap increase, TY2028 | $16.4M | $16.2M |
| Capital-gains options page, static / behavioral revenue by year | | at most $1.3M / $1.0M a year (0.9%) |

The head count is still the fragile poverty number (it moved 4.5% here; the
gap moved −1.1%).

**Act 24 against Act 46 frozen at TY2026 (the ITEP frame), CD2, $M**
(`forecast_sb3125_vs_fy26base.py --cd 2`):

| TY | Bracket | SD expansion | Total | ITEP | Gap |
|---|---:|---:|---:|---:|---:|
| 2027 | −150.3 | 0.0 | −150.3 | −227.0 | +76.7 |
| 2028 | −139.0 | −15.6 | −154.6 | −258.0 | +103.4 |
| 2029 | −354.0 | −15.0 | −369.0 | −534.0 | +165.0 |
| 2030 | −342.2 | −30.4 | −372.6 | −563.0 | +190.4 |
| 2031 | −319.7 | −63.0 | −382.7 | −622.0 | +239.3 |
| **5-yr** | **−1,305.2** | **−124.0** | **−1,429.2** | **−2,204.0** | **+774.8** |

Not rerun: `forecast_bill_quintile.py` (HB 2306) and the poverty and EITC/CTC
report products that default to CBO aging (`scripts/poverty_impact_report.py`).
They age through the same function, so they will move by the same order,
fractions of a percent, when next run.

---

## Pipeline-audit fixes — September 29, 2026 (supersedes the tables below; its Act 24 figures were superseded October 8, 2026)

A review of the forecasting pipeline (September 29, 2026) found four errors
that moved this document's Act 24 figures. All four are fixed, and every
table in this section is a full rerun (`forecast_sb3125_enhanced.py --cd 2`,
`forecast_act24_vs_pre_act46.py`, `forecast_sb3125_vs_fy26base.py --cd 2`).
Unmodified code at `134a15f` reproduces the September 28 tables exactly, so
every change below comes from these fixes.

**What changed.**
- **The $1M+ tail is calibrated exactly, then aged to 2024 dollars (Steps
  6a–6b).** The tail's tax target, $663M, is DOTAX's TY2022 figure, but the
  projection grows every unit from 2024, so the tail never received
  2022–2024 growth; its incomes are now multiplied by Honolulu B19013
  2024/2022 (105,205 / 96,580) × (1 + premium)² before projection. The
  one-step rescale to that target also overshot it by 2.0–3.6% (tax is not
  proportional to income even above $1M), so it now iterates to within 0.1%.
  The two pull in opposite directions: on MID TY2027's static bracket change,
  the exact calibration alone is −$5.6M and the aging alone +$16.6M, together
  $64.5M → $75.0M.
- **Quintile tables use the household weight for the fifths and each unit's
  own weight for dollars (Section 9).** The fifths were cut on the filer weight
  of each household's first tax unit while households were counted with WGTP,
  so the published "fifths" held 16.4–23.5% of households; and each
  household's summed change was multiplied by that one filer weight, so the
  quintile Act 46 total ($2,652.7M in TY2027) did not match the fiscal total
  ($2,465.2M). Both are fixed; the quintile totals now equal the fiscal and
  income-class totals exactly.
- **The frozen-TY2026 baseline is scored on target-year itemized
  deductions** (`forecast_sb3125_vs_fy26base.py`). It re-scored the frozen
  law with TY2026 mortgage-interest tiers, an economic input rather than
  statute, which put a spurious −$346.3M over five years into its "SD
  expansion" row (TY2027 showed −$32.4M where the statute gives exactly 0).
- **The REEC growth CI uses the calibrated anchor SE.** The ACS ensemble
  built the multi-anchor SE with a different horizon convention from the
  calibration that fits its κ, so intervals were too narrow at long horizons
  (METHODOLOGY.md §4, *Anchor SE convention*). Points are unchanged.

`calibration.json` was also regenerated (the monthly refresh had deleted four
national-macro ML features on September 23). That changes only the ML
member's records, which the Act 24 path does not use (`use_ml=False`).

**CD2 vs Act 46 baseline, post-behavioral ($M)** (the September 28 figures in
brackets):

| Tax Year | LOW | **MID** | HIGH | RECESSION |
|----------|----:|--------:|-----:|----------:|
| 2027 | $105.1M [$94.8M] | **$111.1M [$101.5M]** | $144.9M [$126.5M] | $108.2M [$98.1M] |
| 2028 | $121.1M [$110.7M] | **$140.9M [$128.6M]** | $180.3M [$163.5M] | $139.9M [$127.0M] |
| 2029 | $132.3M [$121.8M] | **$158.8M [$146.0M]** | $203.7M [$186.0M] | $160.1M [$146.5M] |
| 2030 | $159.2M [$148.7M] | **$194.9M [$181.8M]** | $245.5M [$224.3M] | $198.1M [$183.9M] |
| 2031 | $170.0M [$159.5M] | **$204.6M [$191.0M]** | $262.4M [$240.1M] | $209.1M [$194.4M] |
| **5-year total** | **$687.6M [$635.4M]** | **$810.4M [$748.9M]** | **$1,036.9M [$940.4M]** | **$815.3M [$749.9M]** |

MID 5-year: **$748.9M → $810.4M (+$61.5M, +8.2%)**. The whole move is in the
bracket change; the credit overlay is unchanged. RECESSION still runs above
MID in 2030–31: it carries a 1.3%/yr top premium against MID's 1.0% (a
leftover from the May change that lowered MID's), which also gives its tail a
slightly larger aging factor. That is not changed here.

**MID by component ($M):**

| Tax Year | Act 46 baseline | Static bracket | Behavioral response | Bracket (post-behav.) | Credit total | **Total** |
|----------|----------------:|---------------:|--------------------:|----------------------:|-------------:|----------:|
| 2027 | $2,522.9M | $75.0M | −$13.0M | $62.0M | $49.1M | **$111.1M** |
| 2028 | $2,689.1M | $81.3M | −$14.8M | $66.4M | $74.5M | **$140.9M** |
| 2029 | $2,552.0M | $88.4M | −$18.2M | $70.2M | $88.6M | **$158.8M** |
| 2030 | $2,664.9M | $95.5M | −$20.4M | $75.1M | $119.9M | **$194.9M** |
| 2031 | $2,744.8M | $103.1M | −$22.7M | $80.4M | $124.3M | **$204.6M** |
| **5-year** | | **$443.2M** | **−$89.2M** | **$354.1M** | **$456.3M** | **$810.4M** |

Was: static $376.0M, behavioral −$83.4M, bracket $292.6M, total $748.9M. The
Act 46 baseline rises by the aged tail's tax (TY2027 $2,465.2M → $2,522.9M).

**Who pays (MID TY2027).** By income class only the $1M+ row moves: $91.08M
→ $101.55M in total, $39,303 → $43,823 per household. By fifth of households
(the published figures in brackets):

| Fifth | Households | Avg. rate change | Avg. credit change | **Avg. total** | Total $M | % paying less | % paying more |
|---|---:|---:|---:|---:|---:|---:|---:|
| Q1 (bottom 20%) | 98,676 [81,005] | −8 | +14 | **+6 [+9]** | +0.57 [+0.74] | 25.1% | 1.3% |
| Q2 | 98,877 [89,419] | −54 | +17 | **−37 [−29]** | −3.69 [−2.63] | 87.1% | 2.4% |
| Q3 | 98,778 [100,606] | −77 | +36 | **−41 [−46]** | −4.10 [−4.66] | 96.7% | 2.9% |
| Q4 | 98,905 [106,687] | −92 | +56 | **−36 [−40]** | −3.58 [−4.28] | 96.1% | 3.9% |
| Q5 (top 20%) | 98,811 [116,330] | +991 | +134 | **+1,126 [+905]** | +111.26 [+105.26] | 78.2% | 21.8% |

The top fifth's total rises with the aged tail (+$6.0M) while the weighting
fix alone lowers it (−$4.5M). The credit borne by households falls from
$27.1M to $25.4M in TY2027 (per claimant $1,746 → $1,637), because the old
totals counted it at the first unit's weight.

**Act 24 against pre-Act-46 (2017) law, five years ($M)**
(`forecast_act24_vs_pre_act46.py`). Column C follows the page's new static
change; the Act 46 columns barely move:

| Tax Year | A: Act 46 banked ≤2026 | B: Act 46 remaining | C: Act 24 increment | **TOTAL vs pre-Act-46** | memo: vs frozen 2026 | COR (A+B) | % below COR |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2027 | −539.0 | −230.5 | +75.0 | **−694.5** | −155.5 | −922.7 | 16.6% |
| 2028 | −546.7 | −250.0 | +81.3 | **−715.4** | −168.7 | −1,052.6 | 24.3% |
| 2029 | −551.3 | −491.9 | +88.4 | **−954.9** | −403.6 | −1,262.3 | 17.4% |
| 2030 | −558.8 | −515.0 | +95.5 | **−978.3** | −419.5 | −1,347.5 | 20.3% |
| 2031 | −563.0 | −545.0 | +103.1 | **−1,004.9** | −441.9 | −1,453.2 | 23.8% |
| **5-year** | **−2,758.8** | **−2,032.5** | **+443.3** | **−4,348.0** | **−1,589.2** | **−6,038.3** | **20.7%** |

By 2031 Act 46 costs $1,108.0M a year on this model's static estimate and
Act 24 recovers $103.1M of it, about 9% (was $88.1M, about 8%).

**Act 24 against Act 46 frozen at TY2026 (the ITEP frame), CD2, $M**
(`forecast_sb3125_vs_fy26base.py --cd 2`; its own population, on which the
SOI anchor replaces the Pareto tail, so only the frozen-baseline fix moves
it):

| TY | Bracket | SD expansion | Total | ITEP | Gap |
|---|---:|---:|---:|---:|---:|
| 2027 | −151.0 | 0.0 | −151.0 | −227.0 | +76.0 |
| 2028 | −139.8 | −15.7 | −155.6 | −258.0 | +102.4 |
| 2029 | −355.5 | −15.1 | −370.6 | −534.0 | +163.4 |
| 2030 | −343.7 | −30.6 | −374.2 | −563.0 | +188.8 |
| 2031 | −320.8 | −63.4 | −384.2 | −622.0 | +237.8 |
| **5-yr** | **−1,310.8** | **−124.8** | **−1,435.6** | **−2,204.0** | **+768.4** |

Was: SD expansion −$471.1M, total −$1,781.9M, gap +$422.1M (~19% below ITEP;
now ~35% below). These equal the pre-September-28 `memo_vs_frozen_2026`
column of `forecast_act24_vs_pre_act46.py`, which scored the same population
on target-year deductions. Whether ITEP's own vs-frozen figure holds itemized
deductions at TY2026 levels is worth checking before comparing the two.

*Superseded September 30, 2026: the CBO aging fix moves this table to −$1,429.2M
over five years (section above).*

**REEC growth CI90** (the table under *Prediction-interval plumbing*,
recomputed with the MID credit knobs, 0.65 effective claim share and 1.5%
CGEC growth). Points are unchanged; the band now uses the calibrated anchor
SE:

| Tax year | REEC savings $M | CI90 before | CI90 now |
|---|---:|---|---|
| 2027 | 43.47 | [41.23, 45.84] | [40.87, 46.24] |
| 2031 | 83.02 | [79.76, 86.52] | [77.42, 89.33] |

The CPI deflator leg of this band is still extrapolated past its calibrated
horizon (h > 12 months), so the band remains a lower bound.

---

## Act 24 re-based on the DOTAX-anchored capital-gains base — September 28, 2026 (superseded by the section above)

This script, `forecast_act24_vs_pre_act46.py` and the tax simulator now score
capital gains the same way: each DOTAX Hawaiʻi AGI class holds DOTAX's
resident net long-term gains eligible for the alternative tax, and the tax is
the statutory alternative tax of HRS §235-51(f). Until now this script scored
the model's own gains shares with the stacked shortcut of Step 10a, so the
simulator and the Act 24 page gave different figures for Act 24 itself (MID
TY2027 after response: $52.4M against $46.4M). They now agree exactly.

**What changed.**
- `scenarios/act24_population.mid_gains_factors` anchors the MID population
  once per tax year (`calibration.cg_anchor.anchor_factors`) and returns the
  scale factor per DOTAX class; every scenario's projected frame is rescaled
  with MID's factors (`apply_anchor_factors`), so DOTAX fixes how gains are
  spread across classes while LOW and HIGH keep their own top incomes. The
  factors are recorded in the run manifest.
- Both systems are scored with `cg_alt_tax="statute"`
  (`act24_population.statute`) in the revenue loop and the MID distribution
  pass.
- The simulator build's reproduction check scores the same way and also
  checks that its own factors equal the ones this run recorded.
- `forecast_act24_vs_pre_act46.py` now builds the same population as this
  script (`build_units` + `project_units`) instead of
  `redistribute_mid_high_incomes` + `project_and_recalibrate`. Its Act 24
  column is therefore exactly this script's static bracket change, which it
  checks every year; it was about 70% above it. Its `memo_vs_frozen_2026`
  column no longer reproduces `forecast_sb3125_vs_fy26base.py`, which still
  scores the other population and the model's own gains.

**Why the numbers move.** The formula is almost irrelevant: on the model's
own gains the statute moves MID TY2027's Act 46 baseline by $0.006M and its
static gain not at all. The base is what moves them. Against DOTAX the
model's own gains hold about 17% too much above $1M and too little between
$300K and $1M (by a factor of 2.0 to 2.8). Gains are taxed at no more than
7.25%, so gains shield income from a bracket increase: moving gains down out
of the $1M+ class exposes more of its income to Act 24's higher rates, and
moving gains up into the $300K–$1M classes shields more of theirs.

**CD2 vs Act 46 baseline, post-behavioral ($M)** (the September 27 figures in
brackets):

| Tax Year | LOW | **MID** | HIGH | RECESSION |
|----------|----:|--------:|-----:|----------:|
| 2027 | $94.8M [$89.9M] | **$101.5M [$95.4M]** | $126.5M [$119.4M] | $98.1M [$92.4M] |
| 2028 | $110.7M [$105.7M] | **$128.6M [$122.5M]** | $163.5M [$156.5M] | $127.0M [$120.9M] |
| 2029 | $121.8M [$119.3M] | **$146.0M [$142.3M]** | $186.0M [$181.2M] | $146.5M [$142.7M] |
| 2030 | $148.7M [$146.6M] | **$181.8M [$178.2M]** | $224.3M [$220.3M] | $183.9M [$180.1M] |
| 2031 | $159.5M [$158.0M] | **$191.0M [$188.0M]** | $240.1M [$237.1M] | $194.4M [$191.6M] |
| **5-year total** | **$635.4M [$619.5M]** | **$748.9M [$726.4M]** | **$940.4M [$914.5M]** | **$749.9M [$727.7M]** |

MID 5-year: **$726.4M → $748.9M (+$22.5M, +3.1%)**. LOW +$15.9M, HIGH
+$25.9M, RECESSION +$22.2M. The whole of each move is in the bracket change;
the credit overlay and the migration diagnostics are unchanged.

**MID by component ($M):**

| Tax Year | Act 46 baseline | Static bracket | Behavioral response | Bracket (post-behav.) | Credit total | **Total** |
|----------|----------------:|---------------:|--------------------:|----------------------:|-------------:|----------:|
| 2027 | $2,465.2M | $64.5M | −$12.1M | $52.4M | $49.1M | **$101.5M** |
| 2028 | $2,615.5M | $67.9M | −$13.8M | $54.1M | $74.5M | **$128.6M** |
| 2029 | $2,475.4M | $74.5M | −$17.0M | $57.4M | $88.6M | **$146.0M** |
| 2030 | $2,585.2M | $81.0M | −$19.1M | $61.9M | $119.9M | **$181.8M** |
| 2031 | $2,661.9M | $88.1M | −$21.3M | $66.8M | $124.3M | **$191.0M** |
| **5-year** | | **$376.0M** | **−$83.4M** | **$292.6M** | **$456.3M** | **$748.9M** |

Was: static $353.3M, behavioral −$83.2M, bracket $270.1M, total $726.4M.

**Who pays (MID TY2027, total change by income class).** The change is
concentrated and runs both ways, for the reason above:

| Income class | Before | Now | Share paying more |
|---|---:|---:|---|
| $1M+ | $82.58M | $91.08M | 100% → 100% |
| $500K–$1M | $7.84M | $6.19M | 96.1% → 75.7% |
| $350K–$500K | $3.39M | $2.85M | 35.4% → 34.6% |
| $175K–$350K | $3.27M | $3.15M | 9.9% → 9.5% |
| Under $175K | unchanged | unchanged | unchanged |

By fifth of households only the top fifth moves (+$5.46M in TY2027).

**Act 24 against pre-Act-46 (2017) law, five years ($M)** (`forecast_act24_vs_pre_act46.py`;
the published figures in brackets). The Act 24 column falls to this
script's own static change; the Act 46 columns barely move:

| Tax Year | A: Act 46 banked ≤2026 | B: Act 46 remaining | C: Act 24 increment | **TOTAL vs pre-Act-46** | memo: vs frozen 2026 | COR (A+B) | % below COR |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2027 | −539.0 | −230.5 | +64.6 | **−704.9** | −166.0 | −922.7 | 16.6% |
| 2028 | −546.7 | −250.0 | +67.9 | **−728.7** | −182.0 | −1,052.6 | 24.3% |
| 2029 | −551.3 | −491.9 | +74.5 | **−968.8** | −417.4 | −1,262.3 | 17.4% |
| 2030 | −558.8 | −515.0 | +81.0 | **−992.7** | −433.9 | −1,347.5 | 20.3% |
| 2031 | −563.0 | −544.9 | +88.1 | **−1,019.9** | −456.8 | −1,453.2 | 23.8% |
| **5-year** | **−2,758.8** | **−2,032.2** | **+376.0** | **−4,415.0** | **−1,656.2** | **−6,038.3** | **20.7%** |

Published before this change: A −2,770.2, B −2,040.1, C **+604.5**, total
−4,205.8, memo −1,435.6. By 2031 Act 46 costs $1,107.9M a year on this
model's static estimate and Act 24 recovers $88.1M of it, about 8% (was
$170.7M, about 15%, which did not match this document's own $85.1M static
for the same comparison). Against COR's own Act 46 estimate the model now
runs **16.6–24.3% below** (5-year 20.7%; was 17.5–24.8%, 5-year 20.3%). The
memo column moves more than A and B because it is Act 24 against frozen
2026, so it carries the whole change in C.

---

## Tax simulator v2: capital gains — September 27, 2026 (superseded by the section above)

The tax simulator now lets users change the rate of the alternative tax on
net long-term capital gains (`TAX_SIMULATOR_SCOPE.md`, "v2: capital gains").
The Act 24 pipeline was unchanged that day; it was re-based on the same
capital-gains treatment the next day, so the figures this section gives for
the difference between the two no longer stand.

**Why nothing here moves.** This script, `forecast_act24_vs_pre_act46.py`
and `forecast_sb3125_vs_fy26base.py` score the registry systems, which keep
the stacked shortcut of Step 10a on the model's own gains base.
- `TaxSystemConfig` gains `cg_alt_tax`: `"stacked"` (the default, Step 10a)
  or `"statute"`, HRS §235-51(f) as written for schedules whose rates rise
  with income (`liability/cg_alternative.py`), with
  `capital_gains_rate_pct`; where a rate falls back below the gains rate,
  the statute's income "taxed at a rate below" it is read as the floor of the
  first bracket whose rate reaches it (`cap_floor`), and the simulator page
  says so. The shortcut accepts only 7.25%.
- `BehavioralParams.cg_beta` (2.6 / 2.0 / 1.6 for LOW / MID / HIGH; 0
  static) drives a new `apply_realization_response`, which
  `apply_behavioral_response` calls after migration. It does nothing unless
  both systems use the statute and their gains rates differ, so it never
  runs here.
- The DOTAX anchoring moved from `forecast_cg_rate_options.py` to
  `tax_modeler.calibration.cg_anchor` without change; that page's CSVs
  rebuild byte for byte.
- The simulator build still checks that scoring Act 24 against Act 46 on its
  population, with the registry systems and the model's gains base,
  reproduces every published LOW/MID/HIGH revenue row and the MID
  distribution tables. It passes unchanged: on the model's gains base, the
  registry systems score bitwise as they did before this change.

**The simulator's capital gains now differ from this script's.** It scores
the capital-gains page's DOTAX-anchored base (80 percent of the $400K+
class's gains to $1M+ filers) with the statute. DOTAX anchors MID: the
scale factor per DOTAX class and year makes MID's class totals DOTAX's.
LOW and HIGH keep MID's factors, applied to their own classes (their own
income ranks) and their own model gains, so their gains move with their top
incomes (TY2027: $3,832.7M / $4,110.7M / $4,382.0M in LOW / MID / HIGH).
DOTAX corrects how the model spreads gains across income classes, while
LOW and HIGH express uncertainty about top incomes (tail shape and growth
premium), and gains should move with that. On the model's own base the
statute alone moves almost nothing: MID TY2027, the Act 46 baseline goes from
$2,456.4259M to $2,456.4200M and Act 24's static gain stays at $58.3642M.
The base moves it. The model's base holds about 17% too much at $1M+ and
too little between $300K and $1M (by a factor of 2.0 to 2.8) against DOTAX
in MID TY2027 (`meta.cg_anchor` in `site/data/tax-simulator/population.json`).
Scored the simulator's way, Act 24's bracket change against Act 46 is
(this document's figures at the time in brackets; the section above re-based
this script on the same treatment the next day, so the figures on the left
are now this document's too, and the comparison file that recorded the gap
is gone):

| $M | LOW | **MID** | HIGH |
|---|---:|---:|---:|
| TY2027 static | 65.32 [60.26] | **64.55 [58.36]** | 65.88 [58.75] |
| TY2027 post-behavioral | 45.10 [40.23] | **52.42 [46.37]** | 61.08 [54.01] |
| 2027–2031 static | 376.72 [359.69] | **376.03 [353.32]** | 414.50 [388.47] |
| 2027–2031 post-behavioral | 218.28 [202.35] | **292.60 [270.12]** | 379.93 [354.05] |

The first v2 build pinned every scenario to DOTAX's totals, which pushed
all of a scenario's extra top income into ordinary income and stretched
the scenario pattern: five-year static, 341.84 / 376.03 / 453.55, ratios
to MID of 0.91 / 1 / 1.21 against this document's 1.02 / 1 / 1.10. With
MID's factors, as in the table, the ratios are 1.00 / 1 / 1.10.

The yearly post-behavioral gap runs from +$1.53M to +$4.94M in LOW, +$3.04M
to +$6.11M in MID and +$3.02M to +$7.07M in HIGH: the simulator is above
this document in every scenario and year. The simulator's page states it.
Re-basing this estimate on the anchored base was done the next day; see
"Act 24 re-based on the DOTAX-anchored capital-gains base" above, which
supersedes this section's comparison.

**Corrections to this document.** The capital-gains cap is HRS §235-51(f),
not §235-16; Step 10a and the other citations below now say so. Step 10a
also said that PUMS units below $1M carry no gains share; in fact
`calibration/cg_imputation.py` gives units from $100K to $1M one.

---

## Behavioral accounting and migration elasticity — September 27, 2026 (supersedes the tables below)

Two corrections to the behavioral response, made together.
`BEHAVIORAL_ACCOUNTING_REVIEW.md` has the analysis, sources and arithmetic.

1. **Accounting.** After the response, the script scored Act 46 as well as
   SB 3125 on the responded population: change = Σ w′(T₂₄ − T₄₆)(y′). The
   standard counterfactual scores Act 46 on the population as it is:
   change = Σ w′·T₂₄(y′) − Σ w·T₄₆(y).
   - **What the old form did.** It charged a filer who moved away only the
     rate increase on their income, about a tenth of their tax, and charged
     income reported away only (t₁ − t₀)·Δy.
   - **Where it came from.** The form arrived with the first behavioral
     commit (`0894417`, May 2) with no stated rationale.
   - **Now.** `behavioral_response.score_with_response` does the scoring for
     this script and for the tax simulator, whose `kernel.js` mirrors it.
2. **Migration elasticity.** The share of $1M+ filers who leave per point
   of top-rate increase goes from 0.15 / 0.10 / 0.05 to **0.01 / 0.0025 /
   0.001** (LOW / MID / HIGH). The old values read Young & Varner's percent
   semi-elasticities as shares; section 5b has the evidence.

Changing only the accounting would have taken the MID five-year total to
$125.1M and LOW to −$215.0M, because the oversized elasticity would then
cost each migrant's whole tax. With both corrections:

**CD2 vs Act 46 baseline, post-behavioral ($M):**

| Tax Year | LOW | **MID** | HIGH | RECESSION |
|----------|----:|--------:|-----:|----------:|
| 2027 | $89.9M | **$95.4M** | $119.4M | $92.4M |
| 2028 | $105.7M | **$122.5M** | $156.5M | $120.9M |
| 2029 | $119.3M | **$142.3M** | $181.2M | $142.7M |
| 2030 | $146.6M | **$178.2M** | $220.3M | $180.1M |
| 2031 | $158.0M | **$188.0M** | $237.1M | $191.6M |
| **5-year total** | **$619.5M** | **$726.4M** | **$914.5M** | **$727.7M** |

MID 5-year: **$738.9M → $726.4M (−$12.5M, −1.7%)**. LOW −$51.1M (−7.6%),
HIGH +$2.7M, RECESSION −$11.8M.

**MID by component ($M):**

| Tax Year | Act 46 baseline | Static bracket | Behavioral response | Bracket (post-behav.) | Credit total | **Total** |
|----------|----------------:|---------------:|--------------------:|----------------------:|-------------:|----------:|
| 2027 | $2,456.4M | $58.4M | −$12.0M | $46.4M | $49.1M | **$95.4M** |
| 2028 | $2,607.2M | $61.7M | −$13.7M | $48.0M | $74.5M | **$122.5M** |
| 2029 | $2,465.0M | $70.7M | −$17.0M | $53.7M | $88.6M | **$142.3M** |
| 2030 | $2,575.3M | $77.5M | −$19.1M | $58.3M | $119.9M | **$178.2M** |
| 2031 | $2,652.8M | $85.1M | −$21.4M | $63.7M | $124.3M | **$188.0M** |
| **5-year** | | **$353.3M** | **−$83.2M** | **$270.1M** | **$456.3M** | **$726.4M** |

Was: behavioral −$70.7M, bracket $282.6M (the September 25 table below).

**Where the response comes from (MID, five years).** Channels were scored
separately on the simulator population, which reproduces this script:

| | ETI alone | Migration alone | Interaction | Total |
|---|---:|---:|---:|---:|
| Now | −$67.8M | −$15.6M | +$0.2M | −$83.2M |
| Was | −$8.2M | −$63.5M | +$1.0M | −$70.7M |

The response is now mostly ETI, which is at full strength from 2027, while
migration phased in over five years. So TY2027 falls ($102.8M → $95.4M)
and TY2031 rises ($184.5M → $188.0M). In MID TY2031 there are 2,953 filers
above $1M before any response and 2,860 after ETI (93 fall below $1M by
reporting less income). Migration then removes 14 of them (0.5%) where it
used to remove 572 (20%); `filers_1m_post_response` is 2,846, was 2,288.

`eti_response_$M` keeps its name but now means SB 3125 revenue lost to the
whole response (ETI and migration), against its static revenue.

**Unchanged:**
- static bracket figures, credits and the Act 46 baseline;
- every distribution table, which is static;
- the vs-pre-Act-46 and FY26-base tables;
- the capital-gains options, whose scorer already used the standard
  counterfactual and has no migration channel.

The tax simulator's presets moved too; see `TAX_SIMULATOR_SCOPE.md`.

**Still open, not changed here:**
- The top-income growth premium carries a "Hawaiʻi outmigration haircut"
  (0.8 pp a year in the script, 0.5 pp in section 8a), a second channel for
  high earners leaving that may overlap with migration.
- The ETI values sit far below Rauh & Shyu's estimate for California's top
  earners (section 5a).

---

## Tax simulator groundwork — September 26, 2026 (results tables unchanged)

Changes made for the tax simulator (`TAX_SIMULATOR_SCOPE.md`). A rerun of
`forecast_sb3125_enhanced.py --cd 2` after them reproduces the published
`enhanced.csv`, `quintile.csv` and `bracket.csv` byte for byte, so every table
below stands.

- **Migration and PTE responses read the two schedules.**
  `apply_migration_response` and `estimate_pte_election_shift_M` hard-coded
  SB 3125's 11% → 13% top rate and its $1M / $750K / $500K thresholds. Given
  the baseline and scenario configs (as `apply_behavioral_response` now passes
  them), they derive each filing status's change in the top statutory rate
  and the scenario's top-bracket floor instead: full migration response at AGI
  of at least max($1M, that floor), half from the floor to $1M when it is
  lower, and none for a rate cut (the same asymmetry as the ETI). For Act 24
  vs Act 46 this is exactly the old constants (2 points; $1M, $750K, $500K).
  Three limits keep the derived form sensible for plans other than Act 24,
  and none of them binds for Act 24:
  - the half tier starts no lower than the baseline's own top-bracket floor,
    so moving the top bracket down to middle incomes does not apply the
    top-1% elasticity to them;
  - no group loses more than all of its weight (a top rate above about 20%
    in the low scenario would otherwise give negative weights);
  - given each year's change (`top_rate_path`, which the simulator passes),
    every rise phases in from the year it takes effect, so a rise that starts
    in 2029 is in its first year in 2029 however the plan is written. A
    change that holds from the first year phases in exactly as before.
  The PTE shift likewise applies only where the top rate rises.
- **Brackets and deductions through two lookups.** Every scorer now reads
  `TaxCalculator.brackets_for(config, status)` and
  `standard_deduction_for(config, status)`, which also serve inline
  schedules (user-defined systems). Rates reach the scorers as an explicit
  `rate_decimal`: the old "a rate above 1 is a percent" guess would have read
  a 0.5% bracket as 50%. No existing schedule has a rate at or below 1%.
- **One population builder.** The synthesis and projection steps moved to
  `tax_modeler.scenarios.act24_population`, shared by this script and the
  simulator's scoring population.
- **Distribution shares can ignore noise.** `generate_quintile_report` takes
  `no_change_tolerance` (default 0, as this script uses); the simulator passes
  half a cent, so a plan that changes no one's tax does not show people
  paying more from floating-point rounding.
- **Correction** to the section below: the COR TY2027 projection is
  $3,071.0M, not $2,874M; see the corrected sentence there.

---

## Scoring-path fixes — September 25, 2026 (post-behavioral tables superseded above)

Three defects in the reform-scoring path, found while scoping a tax simulator
(`TAX_SIMULATOR_SCOPE.md`). A pre-fix rerun on `main` reproduced the published
Act 24 and capital-gains files byte for byte, and the calibrated base is
unchanged (all 140 columns identical), so every change below is the fixes.

1. **Itemized deductions dropped above $500K.** `apply_top_income_growth_premium`
   blanks the `hi_*` tax columns of the units it rescales, so a stale value
   cannot be used. `forecast_sb3125_enhanced.py` then scored without
   recomputing, and `TaxCalculator` / `per_unit_tax` filled the blank itemized
   deduction with 0: all 682 records at $500K+ (about 7,100 filers, itemizing
   about $220K on average) were scored on the standard deduction alone,
   against item 5 of the script's own docstring. New
   `tax_unit_projector.refresh_stale_hawaii_tax` re-scores every unit whose
   income changed after projection, with the projector's per-county
   deduction params; it runs after the premium and after the RECESSION
   shock, which rescales every filer without refreshing tax. The scorers now
   raise on a NaN itemized deduction (`tax_system_config.itemized_deductions`)
   instead of zero-filling it. `project_and_recalibrate` and
   `forecast_act24_vs_pre_act46.py` already recomputed tax and were not
   affected; `forecast_sb3125_static_quintile.py` uses no premium.
2. **`per_unit_tax` ignored credits.** It read a `"total_credits"` key that
   `HawaiiTaxCredits` never returns, so every distributional table and the
   quintile-path COR factor were scored before credits (TY2027 Act 46:
   $2,798.6M before credits vs $2,643.4M net). It is now
   `TaxCalculator.unit_liabilities(...)["net"]`, the same per-unit tax
   `compare_systems` sums, and it is no longer floored at zero: the bottom
   fifth's average Act 46 tax is now a net refund (−$318 in TY2027) from the
   refundable food/excise and renters credits. Revenue totals are unaffected.
3. **One personal exemption per return.** Without a `num_exemptions` column
   (absent on every projected frame) the scorers, the ETI marginal-rate
   lookup and the capital-gains `Scorer` gave each return one exemption;
   they now count filer, spouse on a joint return and dependents (weighted
   mean 1.88), as `liability/hawaii.py` always did. Small for Act 24
   (TY2027 static +$0.3M); a doubled exemption had scored at $34.6M instead
   of $63.8M. Also fixed: the renters credit's misspelled
   `married_filing_separate` key, and the static-quintile script's missing
   spouse exemption.

Nearly all of the Act 24 change is defect 1 (TY2027 MID static: −$22.9M from
the deductions, +$0.3M from exemptions). Credit savings are unchanged.

**CD2 vs Act 46 baseline, post-behavioral ($M):**

| Tax Year | LOW | **MID** | HIGH | RECESSION |
|----------|----:|--------:|-----:|----------:|
| 2027 | $102.9M | **$102.8M** | $122.0M | $99.6M |
| 2028 | $117.7M | **$128.0M** | $158.0M | $126.3M |
| 2029 | $129.9M | **$145.3M** | $180.9M | $145.7M |
| 2030 | $155.4M | **$178.2M** | $218.2M | $180.1M |
| 2031 | $164.7M | **$184.5M** | $232.8M | $187.8M |
| **5-year total** | **$670.6M** | **$738.9M** | **$911.9M** | **$739.5M** |

MID 5-year: **$870.1M → $738.9M (−$131.2M, −15.1%)**; LOW −$116.2M, HIGH
−$148.0M, RECESSION −$131.1M.

**MID by component ($M):**

| Tax Year | Act 46 baseline | Static bracket | ETI/migration | Bracket (post-behav.) | Credit total | **Total** |
|----------|----------------:|---------------:|--------------:|----------------------:|-------------:|----------:|
| 2027 | $2,456.4M | $58.4M | −$4.6M | $53.8M | $49.1M | **$102.8M** |
| 2028 | $2,607.2M | $61.7M | −$8.2M | $53.5M | $74.5M | **$128.0M** |
| 2029 | $2,465.0M | $70.7M | −$13.9M | $56.7M | $88.6M | **$145.3M** |
| 2030 | $2,575.3M | $77.5M | −$19.1M | $58.4M | $119.9M | **$178.2M** |
| 2031 | $2,652.8M | $85.1M | −$24.9M | $60.2M | $124.3M | **$184.5M** |
| **5-year** | | **$353.3M** | **−$70.7M** | **$282.6M** | **$456.3M** | **$738.9M** |

Was: static $501.4M, ETI −$87.6M, bracket $413.8M. The Act 46 baseline falls
$187M in TY2027 ($2,643.4M → $2,456.4M), widening the gap to COR's TY2027
projection ($3,071.0M) from 13.9% to 20.0%. *(Corrected September 26, 2026:
this sentence first cited $2,874M, the stale fallback table in
`quintile_analysis.py`, and a gap of 8% to 15%.)*

**Distribution (TY2027, MID).** Households paying less: 78.0% → 76.1%. The
second fifth's share falls from 88% to 79%: where a nonrefundable credit
already covers a household's tax, a rate cut no longer lowers what it owes,
and the pre-credit tables counted it as a winner. Households above $1M:
+$44,037 → +$35,636; $500K–$1M: +$2,384 → +$1,632 (96% pay more, was 100%).
Section 10's tables are updated in place.

**Act 24 vs pre-Act-46 law** (`forecast_act24_vs_pre_act46.py`, static):
Act 46's cost by 2031 (A+B) falls from $1,174.2M to $1,129.7M, mostly
defect 2 (households whose nonrefundable credits already cover their tax gain
nothing from Act 46's cuts); Act 24's increment is $170.7M (15%). Against
COR's own Act 46 estimate the model now runs **17–25% below** (was 14–22%;
5-year 20.3%, was 17.2%). The reconciliation section's figures below are
superseded by:

| Tax Year | A: Act 46 banked ≤2026 | B: Act 46 remaining | C: Act 24 increment | **TOTAL vs pre-Act-46** | memo: vs frozen 2026 | COR (A+B) | % below COR |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2027 | −533.1 | −227.8 | +76.8 | **−684.1** | −151.0 | −922.7 | 17.5% |
| 2028 | −544.0 | −247.8 | +92.2 | **−699.6** | −155.6 | −1,052.6 | 24.8% |
| 2029 | −553.4 | −492.0 | +121.4 | **−924.0** | −370.6 | −1,262.3 | 17.2% |
| 2030 | −564.8 | −517.6 | +143.3 | **−939.1** | −374.2 | −1,347.5 | 19.7% |
| 2031 | −574.8 | −554.9 | +170.7 | **−958.9** | −384.2 | −1,453.2 | 22.3% |
| **5-year Σ** | **−2,770.2** | **−2,040.1** | **+604.5** | **−4,205.8** | **−1,435.6** | **−6,038.3** | **20.3%** |

Against ITEP's ~$1.4B, the 2031 A+B of −$1,129.7M is ~19% below (was ~16%).

**CD2 vs FY2026-frozen baseline** (`forecast_sb3125_vs_fy26base.py --cd 2`;
supersedes the August 3 table in Section 10; pre-fix `main` gave −$1,862.7M).
*Superseded September 29, 2026: this table scored the frozen baseline on
TY2026 itemized deductions; the corrected table (5-year −$1,435.6M) is in the
section at the top.*

| Tax Year | Bracket effect | SD expansion | **Total** | ITEP estimate | Gap |
|----------|---------------:|-------------:|----------:|---------------:|-----:|
| 2027 | −$151.0M | −$32.4M | **−$183.4M** | −$227.0M | +$43.6M |
| 2028 | −$139.8M | −$64.7M | **−$204.5M** | −$258.0M | +$53.5M |
| 2029 | −$355.5M | −$82.4M | **−$437.9M** | −$534.0M | +$96.1M |
| 2030 | −$343.7M | −$118.3M | **−$462.0M** | −$563.0M | +$101.0M |
| 2031 | −$320.8M | −$173.3M | **−$494.1M** | −$622.0M | +$127.9M |
| **5-year Σ** | **−$1,310.8M** | **−$471.1M** | **−$1,781.9M** | **−$2,204.0M** | **+$422.1M** |

`forecast_act24_vs_pre_act46.py`'s tie-out constants now carry these totals.

**CD2 sensitivity** (`forecast_sb3125_sensitivity.py --cd 2`, static;
supersedes the August 3 table): MID 5-year $694.9M → **$697.2M** (LOW
$664.8M, HIGH $796.7M); the script applies no premium, so only the exemption
count moves it (+$0.4M to +$0.6M a year).

**Capital-gains options** (`forecast_cg_rate_options.py`): exemption count
only; no figure moves more than $0.21M (TY2027 Act 24, central: 9% cap
$47.47M → $47.41M, ordinary rates $127.91M → $127.82M).

---

## Statutory food/excise credit in the tax calculator — September 24, 2026 (superseded above)

The refundable food/excise tax credit (HRS §235-55.85) was modeled three
different ways, none matching the statute: a flat $110 per exemption below
$20K/$30K AGI in `liability/hawaii.py` (netted into `hi_tax_liability` as
`hi_low_income_credit`), and a $110 linear phase-out over $30K-$50K single /
$50K-$70K joint in both `adjustments/hawaii_credits.py` (inside
`TaxCalculator`) and `credits/hi_food_excise.py`. All three now use the
statutory tables in `credits/hi_food_excise`: Act 163's (up to $220 per
exemption, to $40K single / $60K joint) for TY2023-2027 and the prior table
(up to $110, to $30K / $50K) from TY2028, when Act 163 is repealed.

**Levels move; the Act 24 delta barely does.** The credit is refundable and
identical under both laws, so it cancels in the difference. The Act 46
baseline rises $76M over TY2027-2031 (MID; the statutory table from TY2028
pays less than the old approximation). The delta moves **−$0.7M over five
years** (at most $0.2M a year), through the synthetic $1M+ tail, whose weights
are rescaled to a tax target that includes the credit. Capital-gains options:
at most $0.02M per figure.

| Tax Year | LOW | **MID** | HIGH | RECESSION |
|----------|----:|--------:|-----:|----------:|
| 2027 | $124.9M | **$124.5M** | $146.0M | $120.5M |
| 2028 | $139.6M | **$152.6M** | $183.6M | $150.4M |
| 2029 | $154.3M | **$173.0M** | $210.7M | $173.4M |
| 2030 | $179.6M | **$206.7M** | $252.0M | $209.1M |
| 2031 | $188.4M | **$213.3M** | $267.7M | $217.2M |
| **5-year total** | **$786.8M** | **$870.1M** | **$1,059.8M** | **$870.6M** |

**MID by component ($M):**

| Tax Year | REEC savings | CGEC savings | Credit total | Bracket (post-behav.) | **Total** |
|----------|-------------:|-------------:|-------------:|------------------------:|----------:|
| 2027 | $49.1M | $0.0M | $49.1M | $75.4M | **$124.5M** |
| 2028 | $53.9M | $20.6M | $74.5M | $78.0M | **$152.6M** |
| 2029 | $58.6M | $22.1M | $88.6M | $84.4M | **$173.0M** |
| 2030 | $96.2M | $23.7M | $119.9M | $86.9M | **$206.7M** |
| 2031 | $100.2M | $24.1M | $124.3M | $89.0M | **$213.3M** |

The Section 10 distribution tables are updated in place; quintile boundaries
shift slightly with the rescaled tail (Q1 78,773 → 81,005 households).

---

## Deterministic legacy credits — September 24, 2026 (superseded above)

`adjustments/hawaii_credits.py` drew unseeded `np.random.random()` for two small
legacy credits inside `TaxCalculator`'s per-filer credit loop: a 2% "claims the
renewable energy credit" draw ($1,500-$3,500) and a 30% "is a renter" draw for
the renters credit ($50-$150). Results were nonetheless stable run to run,
because `analysis/puma_imputation.py` reseeds NumPy's global generator per unit
before these draws — so every published number embedded one arbitrary draw.
Both are now expected values (2% × amount, 30% × amount), matching the
deterministic take-up smoothing in `credits/hi_renters.py`. No other method or
parameter changed.

The credits enter the Act 46 baseline and Act 24 alike, but they are
nonrefundable and capped by each system's liability, and they shift the
marginal rates the ETI response is computed from, so the delta moves:

| Tax Year | LOW | **MID** | HIGH | RECESSION |
|----------|----:|--------:|-----:|----------:|
| 2027 | $125.0M | **$124.6M** | $146.0M | $120.6M |
| 2028 | $139.7M | **$152.6M** | $183.6M | $150.5M |
| 2029 | $154.4M | **$173.2M** | $210.8M | $173.6M |
| 2030 | $179.8M | **$206.9M** | $252.2M | $209.2M |
| 2031 | $188.6M | **$213.5M** | $267.9M | $217.4M |
| **5-year total** | **$787.5M** | **$870.8M** | **$1,060.5M** | **$871.3M** |

**MID by component ($M):**

| Tax Year | REEC savings | CGEC savings | Credit total | Bracket (post-behav.) | **Total** |
|----------|-------------:|-------------:|-------------:|------------------------:|----------:|
| 2027 | $49.1M | $0.0M | $49.1M | $75.5M | **$124.6M** |
| 2028 | $53.9M | $20.6M | $74.5M | $78.1M | **$152.6M** |
| 2029 | $58.6M | $22.1M | $88.6M | $84.6M | **$173.2M** |
| 2030 | $96.2M | $23.7M | $119.9M | $87.0M | **$206.9M** |
| 2031 | $100.2M | $24.1M | $124.3M | $89.2M | **$213.5M** |

MID 5-year: **$876.0M → $870.8M (−$5.3M, −0.6%)**, all in the bracket channel
(credit overlay unchanged to the cent). The static bracket delta rises $3.2M
over five years ($498.8M → $502.1M) while the ETI/migration offset grows
$8.5M ($−79.1M → $−87.6M). LOW −$0.6M, HIGH −$5.5M, RECESSION −$4.7M. The Act
46 baseline level moves by −$2.4M to +$0.6M a year. Unchanged: the distributional tables (quintile and AGI class,
bit-identical) and every capital-gains-options output
(`forecast_cg_rate_options.py`, rerun and bit-identical).

---

## CD2 rerun for the estimates site — September 24, 2026

`forecast_sb3125_enhanced.py --cd 2` was rerun so its distributional tables
could be published on the estimates site (`site/act-24/`). No methodology or
parameter changed; the rerun picks up the monthly data refresh of September 23.
**Superseded by "Deterministic legacy credits" above** (MID $870.8M); this table superseded the August 3 CD2 table in Section 10:

**CD2 vs Act 46 baseline, post-behavioral ($M):**

| Tax Year | LOW | **MID** | HIGH | RECESSION |
|----------|----:|--------:|-----:|----------:|
| 2027 | $125.0M | **$127.4M** | $148.8M | $121.2M |
| 2028 | $139.5M | **$152.4M** | $184.4M | $151.8M |
| 2029 | $155.7M | **$173.8M** | $212.2M | $174.3M |
| 2030 | $179.6M | **$208.3M** | $252.0M | $210.5M |
| 2031 | $188.3M | **$214.2M** | $268.6M | $218.1M |
| **5-year total** | **$788.1M** | **$876.0M** | **$1,066.0M** | **$876.0M** |

**MID by component ($M):**

| Tax Year | REEC savings | CGEC savings | Credit total | Bracket (post-behav.) | **Total** |
|----------|-------------:|-------------:|-------------:|------------------------:|----------:|
| 2027 | $49.1M | $0.0M | $49.1M | $78.3M | **$127.4M** |
| 2028 | $53.9M | $20.6M | $74.5M | $77.9M | **$152.4M** |
| 2029 | $58.6M | $22.1M | $88.6M | $85.2M | **$173.8M** |
| 2030 | $96.2M | $23.7M | $119.9M | $88.5M | **$208.3M** |
| 2031 | $100.2M | $24.1M | $124.3M | $89.9M | **$214.2M** |

MID moves −$1.0M over five years (−$0.1M to −$0.3M a year), all in the REEC
baseline, which scales with the nominal-income path the refresh nudged. The
bracket delta is unchanged to ±$0.03M.

**Distributional outputs now persist with the run.** The quintile and
AGI-class tables were written only to `/tmp`; they now also land in
`runs/sb3125_cd2_enhanced/quintile.csv` and `bracket.csv`.

**Credit attribution fixed (same day, later).** Two caveats found while
publishing these tables have been resolved in
`tax_modeler.scenarios.quintile_analysis`:

1. **The credit loss was spread, not incident.** The old
   `distribute_reec_loss_to_filers` gave every filer in an AGI bin an equal
   share of that bin's expected REEC loss, so the `pct_pay_more` /
   `pct_pay_less` columns counted households with no solar claim as "paying
   more" (100% of households under $10K, 85% of Q1). **Replaced by
   `attribute_credit_loss`**: each tax unit gets its AGI class's DOTAX TY2023
   claim probability (Table A-6 claims / Table 2 returns: REEC 0.38% under
   $10K to 3.59% at $200K+; CGEC 0.18% to 2.01%) and, if it claims, its
   class's average claim (A-5 / A-6) times the share the bill takes away (all
   of it for AGI-ineligible claimants and after the sunset; 1 − the retained
   pro-rata × suppression share for eligible claimants in cap years), scaled
   to the year's individual-return savings. Averages use the expected loss;
   pay-more / pay-less shares treat each household as a claimant with
   probability q = 1 − Π(1 − q_unit), so non-claimants are never counted as
   paying more. Claim data: `scripts/fetch_dotax_credit_claims.py` →
   `tax_modeler/data/raw/dotax_credit_claims_by_agi.csv` (TY2018-2023).
2. **The distributional module scored credits with the CD1 static overlay.**
   It now takes the individual-pool REEC savings straight from the overlay it
   is handed: the CD2 vintage simulation reports `reec_individual_savings_$M`
   (individual refundable + stock usage, baseline − bill) and
   `reec_eligible_retained_share`; the CD1 static path falls back to
   `reec_individual_credit_loss`. CGEC's individual share
   (`cgec_individual_savings_$M`, 27.0% per DOTAX TY2023 Table A-1) is now
   attributed too. Corporate / fiduciary REEC and CGEC, and TCRA (~$1M on
   individual returns, mostly suppressed), are not distributed.

Fiscal totals are unchanged (bit-identical `enhanced.csv`). Individual-return
credit loss attributed to households: $27.2M of TY2027's $49.1M
credit savings, $76.6M of TY2031's $124.3M. Timing approximation:
in years after capped vintages, reduced carryforward drawdown from earlier
certificates is attributed to that year's would-be claimants. The updated
table is in Section 10 ("Distributional Impact — TY 2027 (MID)").

---

## ACS engine changes — August 21, 2026 (results tables unchanged)

Three changes landed in the `census_forecaster` ACS engine; their effect on
this document's numbers is negligible-to-none, so the Section 10 tables
stand:

1. **v4 per-cell φ no longer applies by default.** The bundled calibration
   had drifted into shipping φ records that production applied while every
   κ/bias record was fit at φ=0.85; application is now gated behind an
   explicit `phi_enabled` payload flag (off), restoring the configuration
   the φ ablation validated. Effect on the income path (B19013 Honolulu
   trend member, TY2026 anchor): point moves **+0.03%** — an order of
   magnitude below this forecast's ±1pp scenario granularity.
2. **Conformal-primary CIs** replaced the max(κ, conformal) floor in the
   ACS engine. `RealGrowthDetail` and the credit-overlay bands read
   `se_total`, which the conformal policy does not touch — **no effect**
   on any band in this document. (`tax_modeler.revenue`'s own
   `apply_revenue_conformal_floor` is unchanged.)
3. **`use_ml` defaults to True in `project_ensemble_multi`** after its
   ship-gate ablation passed (see METHODOLOGY §"Why opt-in"). The income
   and ACS-supplement forecast call sites in `tax_modeler` pass
   `use_ml=False` explicitly, so **all Act 24 numbers remain on the
   classical ensemble**. Revisiting that pin is a deliberate future
   decision that would require regenerating Section 10.

---

## Executive Order 26-02 — REEC transition relief (added August 19, 2026)

**What is retroactive, precisely.** Act 24 §9(1) makes §1 retroactive to taxable
years beginning after 12/31/2025 (TY2026) — **"provided that the amendments to
section 235-12.5(a) shall apply to taxable years beginning after December 31,
2026."** Subsection (a) is where the $175K/$350K AGI test lives. So for TY2026
the retroactive reach is the **$40M aggregate cap + HSEO certification
requirement + sunset only** — *not* the AGI screen, which starts TY2027.
DOTAX Announcement 2026-06 confirms the AGI limits are "effective for tax year
2027." The model already encodes exactly this
(`REEC_AGI_LIMIT_FIRST_YEAR = 2027`).

**The EO.** Gov. Green signed **Executive Order 26-02 on June 8, 2026**
(announced June 12; approved as to form by AG Anne Lopez). Note the two dates
are different things: **June 8 is the signing date; May 21, 2026 is the
eligibility cutoff written into the EO.** A system placed in service in CY2026
"shall not be subject to the annual $40,000,000 aggregate cap" if it was either
**completed before May 21, 2026**, or the taxpayer can show to HSEO and DOTAX
that it **"reasonably relied"** on the credit when it invested resources in
financing, planning, designing, permitting or installing the system before that
date. Stated rationale: "the retroactivity of the RETITC annual cap presents a
risk of litigation."

**Scope is narrow — the cap only.** The EO exempts qualifying systems from the
**$40M aggregate cap and nothing else**. The 35% rate, all per-system caps
($5,000 single-family PV / $350 per unit multi-family / $500,000 commercial),
the HSEO certification process, and the 2030 phase-out all still apply.

**DOTAX implementing guidance: TIR No. 2026-02 (July 31, 2026).** Defines
"investment of resources" as a payment made or cost incurred before 5/21/2026
per Treas. Reg. §1.461-1(a)(1)–(2); "reasonable reliance" is **presumed** once
that showing is made. Critically, the TIR confirms the cap operates on **claims
made in 2027 for systems placed in service in 2026** — which is
**interpretation A semantics** (see "5. TY2026 retroactive cap interpretation"
below). Certification mechanics are still "to be provided later"; applications
are due ~March 1, 2027 with certification by May 31, 2027.

**What this means for the A-vs-B choice.** The picture is more nuanced than the
model's binary flag:

- **DOTAX's official reading is interpretation A** — the CY2027 cap binds TY2026
  installations.
- **EO 26-02 then carves most of that cohort back out of the cap.** With the
  cutoff at May 21 and solar lead times running months, the large majority of
  the CY2026 cohort qualifies.
- So the correct effective treatment is **neither pure A nor pure B**: it is A
  with a large carve-out, which lands close to B for the grandfathered majority
  and at A for post-May-21 systems.

**MID/HIGH/RECESSION (interpretation B) remain the right ones to cite** — they
approximate the post-EO outcome for the bulk of the cohort. **LOW
(interpretation A) is now clearly over-optimistic** about State savings: it caps
a TY2026 vintage the EO largely exempts. The A-vs-B spread at that vintage is
$98.5M of certifications (A: $40.0M capped vs B: $98.5M full), worth roughly
**+$9-11M of cumulative TY2027-2031 savings** under A.

**Size of the grandfathered pool — derived, not sourced.** No official or
third-party estimate of the **credit-dollar** value exists. The $40M cap and
certification regime were added in *conference committee* (CD1/CD2, floor
amendments 5/6/2026) — they appear in neither SD1 nor HD1, no one testified on
them, and Conference Committee Report 114-26 contains **no dollar figures at
all**. There is therefore no published fiscal note, and HSEO/DOTAX cannot know
the qualifying total until the 2027 certification round runs.

The only quantified public figures are **project cost, not credit value**:
Hawaii Solar Energy Association reported (Civil Beat, May 29 and June 12, 2026)
that the eight largest solar firms had **265 commercial projects with $436M of
private capital** committed for 2026 — explicitly a sample, excluding
residential. *(Earlier text in this repo said "$400M"; the sourced figure is
$436M.)*

A top-down estimate anchored on program scale is more defensible than
converting that figure:

| Quantity | Estimate |
|---|---|
| Normal annual RETITC | **~$100M** (DOTAX TY2023 actual $100.1M; 6-yr mean $86.1M) |
| Grandfathered share of CY2026 cohort | 75-95% (May 21 cutoff, multi-month lead times) |
| **Grandfathered pool** | **≈ $85M (range $65M-$100M)** |
| Cohort outcome without the EO | $40M (cap) |
| **EO's incremental cost vs a strict cap** | **≈ $45M-$60M, one-time, FY2027-28** |

> ⚠️ **Do not convert $436M at 35%.** The per-system caps make the blended
> effective rate roughly **17-25%**, not 35% — a $30,000 residential system
> yields $5,000 (16.7%), and multi-family is capped at $350/unit (Civil Beat's
> lead example, Waiau Gardens Kai, is a 114-unit complex → $39,900, not
> $500,000). A bottom-up conversion implies the commercial sample alone would
> exceed the largest annual total the program has ever paid, which is not
> credible.

**Effect on this forecast's headline numbers: none.** The differential already
treats TY2026 as uncapped under interpretation B in both the baseline and
scenario paths, so the grandfathered stock largely cancels. The estimate above
matters for *level* questions (what FY2027-28 collections will look like), not
for the Act 24-vs-baseline *delta* this document scores.

**No official post-EO revenue re-estimate exists yet.** The Council on Revenues'
last General Fund forecast (**May 21, 2026**) predates the EO by three weeks and
does **not** account for Act 24 — its own list of incorporated law changes names
Acts 58/96 (2025) and Acts 46/47 (2024) only. The **September 3, 2026** COR
meeting is the first with "major tax law changes in 2026" on the agenda; that
will be the first official figure incorporating Act 24 and EO 26-02.

**Administering agency.** Act 24 REEC certification runs through the **Hawaiʻi
State Energy Office (HSEO)**, not DBEDT as older text in this document and in
`RETITC_REPORT_METHODOLOGY.md` states. No number depends on this.

**⚠️ Statutory inconsistency in Act 24, unresolved.** §235-12.5(c)(5) says
allowable credits are "$0 beginning January 1, **2031**," while §235-12.5(p)
sunsets the section after TY**2029**. DOTAX Announcement 2026-06 resolves it as
$0 beginning January 1, 2030 — consistent with (p) and with this model
(`REEC_SUNSET_LAST_VINTAGE = 2029`, TY2030+ certifications zero). The
resolution appears only in the announcement, not in a TIR, so it is
administratively settled but not formally so.

---

## COR projections are now auto-refreshed (added August 19, 2026)

**What was wrong.** `DEFAULT_COR_IIT_PROJECTIONS_M` in
`tax_modeler/scenarios/quintile_analysis.py` was a hand-transcribed dict pinned
to the **March 10, 2026** COR meeting. COR had since met on **May 21, 2026**,
so every COR-scaled figure was running on a stale vintage — with nothing in the
repo to signal it. COR meets on no fixed cadence (~4-5 times a year), which
makes "remember to check" the wrong mechanism.

**What replaced it.** `census_forecaster.scripts.refresh_cor_iit` scrapes the
COR meeting page for the newest General Fund **Attachment 1** — the DOTAX Tax
Research & Planning line-item table, which is where the IIT row lives (the COR
letter itself forecasts only *total* General Fund revenue) — parses the
Individual Income Tax row, and writes
`census_forecaster/data/cor/cor_iit_projections.json`. The constant now loads
from that file via `census_forecaster.cor.load_cor_iit_projections()`.

It runs as a step in the monthly **`refresh-data`** workflow, so a new COR
meeting propagates on the next cron run with no human action. Failure modes are
deliberately asymmetric: a network error warns and keeps the committed data,
while a **layout change fails the step loudly** rather than writing a
misaligned dict — a wrong scale factor is worse than a stale one. The parser
refuses to zip a row whose number count doesn't match the FY header.

**Vintage change (March 10 → May 21, 2026), $M by tax year:**

| TY | Mar 10 | May 21 | Δ |
|---|---:|---:|---:|
| 2025 | 2,986.920 | 3,139.079 | +152.159 |
| 2026 | 2,900.330 | 2,923.065 | +22.735 |
| 2027 | 2,825.329 | 2,874.134 | +48.805 |
| 2028 | 2,815.274 | 2,872.305 | +57.031 |
| 2029 | 2,749.758 | 2,780.166 | +30.408 |
| 2030 | 2,851.075 | 2,881.860 | +30.785 |
| 2031 | 2,944.872 | 2,971.795 | +26.923 |

The large TY2025 revision is COR's FY2026 growth upgrade (−4.5% → −2.5%);
FY2028-FY2032 growth rates were left unchanged from March, so the out-year
moves are the DOTAX line-item reconciliation only.

**Blast radius: none to the headline tables.** `cor_scale_factor_for_year` feeds
only the *parallel* diagnostic columns — `bracket_delta_cor_scaled_$M` and the
`*_cor_$M` quintile columns — which sit alongside the unscaled figures rather
than replacing them. Every fiscal-impact number in Section 10 is computed from
the unscaled bracket delta and is unaffected. The scale factor itself rises
~1.7% at TY2027 (1.2613 → 1.2831 against a $2.24B microsim baseline).

*The next vintage change (May 21 → September 3, 2026) is tabulated in the
October 5, 2026 section at the top of this document.*

---

## Reconciliation to ITEP's ~$1.4B Act 46 figure (added August 19, 2026)

> **Figures superseded September 25, 2026** by "Scoring-path fixes" at the top
> of this document: the pre-Act-46 table, the COR gap (now 17–25%) and the ITEP
> gap (now ~19%) moved when per-unit tax began netting credits and counting
> every exemption. The reasoning below is unchanged.

**The problem.** Section 10's "CD2 vs FY2026-frozen baseline" table reports a
5-year total of **−$1,854.5M** (2031: −$514.3M). ITEP's widely-cited estimate
for Act 46 is roughly **$1.4B in *annual* General Fund loss** near full phase-in
in 2031. These look irreconcilable. They are not — they use different baselines.

**Why.** `forecast_sb3125_vs_fy26base.py` freezes the baseline at **Act 46 law
as of TY2026** — TY2025 brackets plus TY2026 standard deductions. Act 46 phases
in the standard deduction in **2024, 2026, 2028, 2030 and 2031** and widens
brackets in **2025, 2027 and 2029**. So a TY2026-frozen baseline has *already
banked* the two largest SD steps:

| Tax year | Joint standard deduction | |
|---|---:|---|
| 2018 (pre-Act 46) | $4,400 | |
| 2024 | $8,800 | Act 46 step 1 |
| **2026 — baseline freezes here** | **$16,000** | Act 46 step 2 |
| 2028 | $18,000 | |
| 2030 | $20,000 | |
| 2031 | $24,000 | |

The joint SD had already risen **3.6×** before that baseline begins. The
vs-frozen table therefore measures only the *remaining tail* of Act 46 plus
Act 24 — not the cost of Hawaii's income tax cuts.

Note also that the "ITEP estimate" column in the Section 10 vs-frozen table
(−$227M … −$622M) is from **ITEP's SB 3125 analysis**
(`26.05.06 HI SB 3125 CD Analysis.xlsx`), a different product from ITEP's
Act 46 estimate. The two should not be compared to each other.

**The true pre-Act-46 frame.** `forecast_act24_vs_pre_act46.py` scores four
legal regimes on identical projected incomes and decomposes the total. Hawaii
does not index brackets or the standard deduction, so the pre-Act-46
counterfactual is the 2018 schedule held at **nominal** values — which is
exactly why the measured cost grows so steeply.

**Static bracket + SD + personal-exemption effect vs pre-Act-46 (2017) law ($M):**

| Tax Year | A: Act 46 banked ≤2026 | B: Act 46 remaining | C: Act 24 increment | **TOTAL vs pre-Act-46** | memo: vs frozen 2026 |
|---|---:|---:|---:|---:|---:|
| 2027 | −558.3 | −235.8 | +75.6 | **−718.5** | −160.2 |
| 2028 | −568.9 | −258.2 | +91.2 | **−735.9** | −167.0 |
| 2029 | −578.1 | −510.0 | +120.4 | **−967.7** | −389.6 |
| 2030 | −589.0 | −538.0 | +142.4 | **−984.6** | −395.6 |
| 2031 | −598.6 | −579.7 | +170.0 | **−1,008.3** | −409.7 |
| **5-year Σ** | **−2,892.9** | **−2,121.7** | **+599.5** | **−4,415.0** | **−1,522.2** |

*Negative = revenue foregone. A+B+C = total. Static: no ETI/migration response,
no credit overlay. The memo column differs from the −$1,854.5M published
vs-frozen total because it scores the frozen baseline on target-year itemized
deduction scales rather than TY2026 scales; the script's separate tie-out column
reproduces the published −$1,854.5M **exactly ($0.0M difference in all five
years)**, which is what validates the pre-Act-46 column. (The TY2026 scales
were the defect, not the memo column: corrected September 29, 2026, see the
dated section at the top.)*

**The reconciliation.** Act 46's own full annual cost in 2031 is column A + B:

> **−$598.6M − $579.7M = −$1,178.3M/yr by 2031**

against ITEP's ~$1.4B. That is **~16% below ITEP** — the *same* systematic gap
this model already documents against ITEP on the vs-frozen comparison ("~15.9%
below ITEP", Section 10), attributed to non-resident filers and PTE
pass-through attribution that a PUMS-based microsim does not capture. The two
estimates are consistent once the baseline is aligned; there was never a
substantive disagreement, only a baseline mismatch.

**Third check — COR publishes its own Act 46 estimate.** The May 21, 2026 COR
General Fund letter states DOTAX's estimated loss from Act 46 directly: "$596.6
million in FY 2026, $740.1 million in FY 2027, $922.7 million in FY 2028,
$1,052.6 million in FY 2029, $1,262.3 million in FY 2030, $1,347.5 million in
FY 2031, and **$1,453.2 million in FY 2032**." That is the *official State*
figure, and it independently corroborates ITEP's ~$1.4B (the September 9, 2026 letter
restates the identical figures). Against our column
A+B (FY→TY shifted):

| Tax Year | This model (A+B) | COR official | Gap | % below |
|---|---:|---:|---:|---:|
| 2027 | −794.1 | −922.7 | +128.6 | 13.9% |
| 2028 | −827.1 | −1,052.6 | +225.5 | 21.4% |
| 2029 | −1,088.1 | −1,262.3 | +174.2 | 13.8% |
| 2030 | −1,127.0 | −1,347.5 | +220.5 | 16.4% |
| 2031 | −1,178.3 | −1,453.2 | +274.9 | 18.9% |
| **5-year Σ** | **−5,014.6** | **−6,038.3** | **+1,023.7** | **17.0%** |

The model tracks COR at a **stable 14-21% shortfall with no trend**, which is
the signature of a *coverage* difference (non-residents, PTE, withholding-only
filers — all absent from PUMS) rather than a modelling error, which would
typically widen or oscillate. Three independent estimates — ITEP, COR, and this
microsim — now agree on the shape of Act 46's cost and differ only by that
known constant.

> **Practical guidance.** When a headline number is wanted for *Act 46's* cost,
> cite **COR's own** figure ($1.45B by FY2032) — it is official, it is the
> State's own accounting, and this model does not attempt to beat it on level.
> Use this model for the *incremental* Act 24 effect and the distributional
> split, where the coverage gap largely cancels between baseline and scenario.

**Total cost including Act 24 credit savings.** The credit overlay
(REEC/CGEC/TCRA) is an **Act 24** effect; Act 46 did not amend those credits, so
it can be added to the pre-Act-46 bracket/SD total — but as a **separately
labelled line**, because the overlay is scored against immediately-pre-Act-24
credit law, not 2017 credit law (Act 261's TCRA repeal, OBBBA's §25D
termination and other post-2017 changes sit inside the credit baseline).

| Tax Year | Bracket+SD+PE vs pre-Act-46 | Act 24 credit savings (REEC yr-1 paused) | **Net** |
|---|---:|---:|---:|
| 2027 | −718.5 | 0.0 | **−718.5** |
| 2028 | −735.9 | +67.4 | **−668.5** |
| 2029 | −967.7 | +86.2 | **−881.5** |
| 2030 | −984.6 | +119.2 | **−865.4** |
| 2031 | −1,008.3 | +124.2 | **−884.1** |
| **5-year Σ** | **−4,415.0** | **+397.1** | **−4,017.9** |

With the **unpaused** (production) credit overlay of +$457.3M, the 5-year net is
**−$3,957.7M**. Both figures exclude behavioral response.

### REEC first-year-pause sensitivity

Modeled as the $40M cap **and** the AGI screen deferred one year
(`REEC_AGI_LIMIT_FIRST_YEAR = 2028`), i.e. TY2027 treated like TY2026: full
demand certified, no cap, no screen. Sunset (last new-cert vintage TY2029)
unchanged. Production MID credit parameters, reproduced to a clean tie-out
against `runs/sb3125_cd2_enhanced/enhanced.csv` (max deviation $0.07M).

| Tax Year | Production | REEC yr-1 paused | Δ |
|---|---:|---:|---:|
| 2027 | 49.2 | 0.0 | −49.2 |
| 2028 | 74.7 | 67.4 | −7.3 |
| 2029 | 88.8 | 86.2 | −2.6 |
| 2030 | 120.1 | 119.2 | −0.9 |
| 2031 | 124.5 | 124.2 | −0.3 |
| **5-year Σ** | **457.3** | **397.1** | **−60.2** |

**The pause costs $60.2M, not $49.2M.** Zeroing TY2027 removes that year's
savings *and* pushes an uncapped TY2027 vintage into the §235-12.5(j)
carryforward pool, which draws down against TY2028-2031 liability and suppresses
savings in every later year. Simple subtraction of the first year understates
the effect by ~$11M.

> **Scope note.** This sensitivity is a **counterfactual**, not a scored
> policy. EO 26-02 grandfathers **TY2026**, which the model already treats as
> uncapped under interpretation B — it does *not* pause TY2027. No source has
> been found for a TY2027 REEC pause. Cite the production numbers as Act 24;
> cite this table only when explicitly modeling a deferred cap start.

---

## Hawaii CPI series correction — July 30, 2026

**Data-integrity correction. This changes every nominal-income-dependent
number in the forecast.** The BLS series the model used as its Hawaii
price deflator was not a Hawaii series.

### What was wrong

`CUURS49ASA0`, labelled "Urban Honolulu, HI" throughout the repo and used
as `_HONOLULU_BLS_SERIES_ID` in `projection/income_forecast.py`, resolves
in the BLS API catalog to **Los Angeles-Long Beach-Anaheim, CA**. Five of
the eleven area labels in the CPI panel were shifted, and no Hawaii series
was present at all. The cheap tell: BLS publishes the all-items index
monthly for exactly four areas (U.S. average, New York, Chicago, Los
Angeles), so a "Honolulu" series returning 12 prints a year is
mislabelled — the panel's four monthly series were precisely that set.

Two further defects surfaced in the same audit:

1. The annual anchor files `cpi_honolulu_allitems.json` and
   `cpi_honolulu_rent.json` declared BLS series IDs (`CUUSA426SA0`,
   `CUUSA426SEHA`) that **do not exist**. The all-items file's values
   converged to Los Angeles by 2024, consistent with its having been
   extended forward using the mislabelled series.
2. The trend smoother in `bls/projection.py` weighted the newest print
   pair at ~50% of total weight and read consecutive-month changes of
   these NSA series as trend. A jackknife (drop the newest print) moved
   the implied annual rate by ~4 pp on both series — Urban Hawaii read
   9.86%/yr and Los Angeles 0.75%/yr against actual ~3.4%/yr CAGRs.

### What changed

| Component | Before | After |
|---|---|---|
| Hawaii deflator series | `CUURS49ASA0` (Los Angeles) | **`CUURS49FSA0` (Urban Hawaii)** |
| CPI panel | 11 areas / 55 series, 5 labels wrong | 12 areas / 60 series, audited vs API catalog |
| All-items anchor | `CUUSA426SA0` (nonexistent) | `CUUSS49FSA0`, 2017–2025 observed |
| Rent anchor | `CUUSA426SEHA` (nonexistent) | `CUUSS49FSEHA`, 2017–2025 observed |
| Trend estimator | pairwise, ~50% weight on newest print | YoY log changes + time-decay (8.3-mo half-life) |
| v3 κ / bias | computed, not passed to production | passed through `_bls_cpi_ratio` |

BLS publishes Urban Hawaii only from 2017, so anchor years 2010–2016 are
**rate-chained**: the legacy files' year-over-year rates applied backward
to the 2017 Urban Hawaii level. Those rates could not be re-verified
against a live series and are flagged in each file's `limitations`.

### Forecast impact

Los Angeles ran ~0.34 pp/yr hotter than Urban Hawaii over 2018–2025 (CAGR
3.687% vs 3.350%), but the naive CAGR gap understates the effect: the old
smoother extrapolated each series' short-run momentum, which diverged
sharply. Corrected Hawaii nominal income growth from the TY2023 base:

| Tax year | real | CPI factor | **nominal** | implied ann. |
|---|---:|---:|---:|---:|
| 2027 | 1.0374 | 1.1147 | **1.1564** | 3.70% |
| 2028 | 1.0698 | 1.1345 | **1.2137** | 3.95% |
| 2029 | 1.1063 | 1.1516 | **1.2740** | 4.12% |
| 2030 | 1.1451 | 1.1663 | **1.3355** | 4.22% |
| 2031 | 1.1856 | 1.1789 | **1.3977** | 4.27% |

Credit overlay on the corrected basis (`reec_effective_claim_share=0.65`,
`cgec_annual_growth=0.015`):

| Tax year | REEC baseline $M | REEC savings $M | CGEC savings $M |
|---|---:|---:|---:|
| 2027 | 83.55 | **43.55** | 0.00 |
| 2029 | 82.37 | 42.37 | 22.14 |
| 2031 | 83.20 | 83.20 | 24.07 |

**TY2027 REEC savings move $41.8M → $43.55M (+$1.75M, +4.2%).** The chain
is: Hawaii CPI runs hotter than Los Angeles → higher projected nominal
income → higher REEC baseline → more savings against the fixed $40M cap.
The v3 bias correction contributes ~+1.0% of the deflator on top of the
series swap. Results tables elsewhere in this document that predate
July 30, 2026 were computed on the Los Angeles basis and are superseded
by this section pending a full re-run.

### Prediction-interval plumbing — July 30, 2026 (supersedes the earlier "known limitation")

The κ-calibrated uncertainty now reaches the credit-overlay outputs.
`RealGrowthDetail` (in `projection/income_forecast.py`) carries the full
decomposition — ACS-forecast SE and κ-rescaled BLS CPI projection SE,
combined under an independence assumption
(`se_log_real = sqrt(se_nom² + se_inf²)`) — and `compute_credit_overlay`
reruns itself at the real-growth CI90 bounds via a scoped scale on
`_hawaii_nominal_growth`, emitting
`reec_savings_ci90_low/high_$M`, `total_credit_savings_ci90_low/high_$M`,
and `growth_individual_ci90_low/high`.

At TY2027 the decomposition is se_nom=0.033, se_inf=0.023 (κ=3.0) →
se_real=0.041: **the calibrated CPI leg contributes ~33% of the
real-growth variance** and would have been invisible under the legacy
κ=1.50-and-discarded path.

| Tax year | REEC savings $M | CI90 |
|---|---:|---|
| 2027 | 43.55 | [41.29, 45.94] |
| 2031 | 83.20 | [79.92, 86.71] |

Held at point inside the band, by design: corporate growth (a fixed-rate
assumption, not income-forecast-derived) and the annual-anchor
re-inflate leg (no SE surface; its error is positively correlated with
the deflate leg, so an independence treatment would misstate the band).
The band therefore covers the ACS-forecast and BLS-deflator channels
only, and is a lower bound on total forecast uncertainty. The
microsimulation income-aging path (`apply_income_growth`) remains
point-based.

### Empirical REEC demand check — DPP solar permits (July 30, 2026)

First empirical read on the *projected* REEC demand vintages. Honolulu
DPP building permits (data.honolulu.gov `4vab-c87q`, aggregated by the
new `refresh_dpp_solar` fetcher into a bundled JSON; diagnostics in
`scenarios/reec_demand_validation.py`) provide residential solar permit
counts and declared values through 2025-06-30 — past the last DOTAX
claim actual (TY2022) and into the vintages the model projects as
`base_2023 × g(y) × d(y)`.

| Year | window | empirical (value) | empirical (count) | model g×d (all scenarios) |
|---|---|---:|---:|---:|
| 2024 | full-year | **0.843** | **0.798** | 1.059 |
| 2025 | H1/H1 | 0.744 | 0.824 | 1.077–1.185 |

**The TY2024 vintage looks ~20-25% overstated.** Every scenario pins
d(2024)=1.00, so modeled 2024 demand is pure income growth (×1.059) —
while actual residential solar permits *fell* ~16% by value and ~20% by
count. This is pre-OBBBA data (the federal §25D termination hits 2026+),
so it cannot be explained by the decay the scenarios model; it suggests
Hawaiʻi rooftop saturation was already binding harder than the
"pre-decay demand persists" assumption. If permits translate ~1:1 to
claims, the REEC baseline for the cap years is high, and **cap savings
are overstated** — indicatively, a 20% lower TY2027 baseline (~$67M vs
$83.5M) would cut static-overlay savings from ~$43.5M to ~$27M.

Read the 2025 row cautiously: the §25D pull-forward story (+37% HECO
surge) is specifically an **H2-2025** phenomenon, and the permit window
ends June 30, 2025 — the H1/H1 ratio structurally cannot see it. The
next data refresh decides whether the 2025 d=1.10 assumption held.

Caveats (full list in the bundle's `limitations`): declared permit value
≠ credit basis (ratios only); Oʻahu only; permits lead claims by months;
observed publication lag of the portal is ~12 months despite frequent
row-updates. **No forecast input has been changed** — this is a
diagnostic; re-anchoring the demand scenarios on permit actuals is a
scenario-design decision pending the TY2024 DOTAX claim actuals.

### Revenue ground-truth check — DOTAX monthly collections (July 31, 2026)

New intake: DOTAX "State Tax Collections and Distribution" monthly XLSX
reports (`refresh_dotax_collections` fetcher; the bundle is an
*accumulating archive* because DOTAX keeps only ~one fiscal year of XLSX
online — never truncate it to one fetch). 13 series including individual
withholding, GE&Use, county surcharge, TAT, corporate income, and the
General Fund total; diagnostics in `projection/dotax_validation.py`
compare same-window year-over-year sums (never single months — deposit
timing is spiky) against the model's implied annual nominal growth.

First read (windows ending Dec 2025 / Apr 2026):

| series | window | YoY |
|---|---|---:|
| Individual withholding | Jul–Dec 2025 | **−11.5%** |
| GE & Use (collec) | Jul–Dec 2025 | +6.5% |
| GE allocated (fresher report) | Jul 2025–Apr 2026 | +2.7% |
| TAT | Jul–Dec 2025 | +2.0% |
| Model implied annual nominal | 2025 / 2026 | +1.7% / −1.0% |

Interpretation: the withholding collapse is dominated by the **Act 46
bracket cuts phasing in from TY2025** — it measures wages *plus policy*
and cannot be read as wage decline. The rate-stable GE gauges show
nominal activity growing ~3–6%/yr, which sits *above* the model's
implied 2025–2026 nominal income path (+1.7%, then −1.0%). Tension, not
contradiction — GE measures gross receipts, the model measures median
household income — but the model's implied 2026 dip is worth watching
as more GE months accumulate. Diagnostic only; no forecast input
changed.

### Files changed

- `packages/census_forecaster/src/census_forecaster/bls/panel.py` — area labels corrected, `S49F` Urban Hawaii added, cadence audit documented.
- `packages/census_forecaster/src/census_forecaster/bls/projection.py` — YoY + time-decay trend estimator, pairwise fallback below 4 YoY observations.
- `packages/census_forecaster/src/census_forecaster/backtest/cpi.py` — `BACKTEST_SERIES` to the real Hawaii IDs; κ=1.50 provenance noted as an LA panel.
- `packages/census_forecaster/src/census_forecaster/data/bls_panel/` — panel refetched (60 series, 9,333 obs).
- `packages/census_forecaster/src/census_forecaster/data/anchors/cpi_honolulu_{allitems,rent}.json` — rebuilt from genuine Urban Hawaii series.
- `packages/census_forecaster/src/census_forecaster/data/anchors/bls_calibration.json` — v3 re-derived (42,908 folds, 421 strata cells).
- `packages/tax_modeler/src/tax_modeler/projection/income_forecast.py` — deflator series corrected; v3 calibration passed through.
- `METHODOLOGY.md` — §5 of the BLS v3 section documents both the identity correction and the estimator change.
- `backtests/results/bls_v3_calibration_2026-07-30.md` — new calibration report.
- Tests: 6 new YoY-smoother tests; the TY2027 REEC sanity band re-centred 41.8 → 44.0 with provenance.

---

## SOI-anchored EITC dollar calibration — May 27, 2026

**Methodology addition:** Post-take-up proportional scaling to match IRS SOI HI TY2022 aggregate EITC dollars ($184.7M).

After count-based take-up imputation (which marks non-recipients to zero), the model may still understate the aggregate EITC dollar total due to ACS wage underreporting and filer mix-shift. A new `scale_benefit_to_dollar_target()` step now applies a proportional scalar (`target_dollars_M / model_dollars_M`) to `eitc_amount` for all imputed recipients. The HI EITC (`hi_eitc_amount`, 40% of federal) is scaled by the same factor to preserve the 40% rate.

**Guard rails:** The scalar is logged at WARNING level when it falls outside [0.5, 2.0] — values outside this range indicate a data mismatch rather than a calibration adjustment. A zero model total skips scaling entirely to prevent divide-by-zero.

**Files changed:**
- `packages/tax_modeler/src/tax_modeler/calibration/takeup_imputation.py` — added `scale_benefit_to_dollar_target()` function and integrated it into `calibrate_benefits()` for the 'eitc' and 'hi_eitc' programs.
- `packages/tax_modeler/src/tax_modeler/calibration/__init__.py` — exported `scale_benefit_to_dollar_target`.
- `packages/tax_modeler/src/tax_modeler/__init__.py` — exported `scale_benefit_to_dollar_target`.
- `tests/tax_modeler/smoke/test_takeup_smoke.py` — 5 new tests covering scalar application, HI-EITC proportionality, unusual-scalar warning, zero-model guard, and calibrate_benefits integration.
- `tests/tax_modeler/poverty/test_impact.py` — updated `test_credit_takeup_reduces_baseline_lift` to use count-only caseload (annual_dollars_millions=0) so the zeroing-behavior invariant is tested independently of dollar scaling.

**Forecast impact:** Closes the remaining dollar gap between the model and IRS SOI HI TY2022 actuals after the filer-age fix. The scalar applied to the full PUMS population should be ≈1.0–1.3 (small upward adjustment). SB 3125 CD1 bracket and REEC/CGEC/TCRA computations are not affected; this calibration applies to the baseline EITC receipt column only.

---

## EITC age-eligibility correction — May 27, 2026

**Tax treatment correction:** Enforced IRC §32(c)(1)(A)(ii) age 25–64 requirement
for childless filers in `calculate_eitc()`.

Prior behavior allowed childless filers of any age to receive the federal EITC,
which overstated EITC receipts by ~$10.6M vs IRS SOI Hawaii TY2022 actuals
($184.7M actual). Affected filers: 28,084 under-25 claimants (~$9.9M) and 3,147
over-64 claimants (~$0.7M).

**Files changed:**
- `packages/tax_modeler/src/tax_modeler/credits/eitc.py` — added age guard after
  qualifying-child count; defaults to age 40 (eligible) when `primary_agep` is
  absent to avoid penalizing incomplete records.
- `tests/tax_modeler/credits/test_eitc.py` — 6 new tests covering boundary ages
  (24/25/64/65), parent exemption, and missing-age default.

**Forecast impact:** Reduces modeled EITC base slightly. Does not affect the SB
3125 CD1 income-tax bracket or REEC/CGEC/TCRA credit computations directly, but
improves accuracy of the baseline tax-unit income distribution used in Step 5
calibration.

---

## CD2 REEC model — Round 2 refinements (May 14, 2026, late)

Following the May 14 vintage-carryforward correction, five additional
refinements were folded into the REEC fiscal model. Each is independently
toggle-able via parameters on `compute_credit_overlay`; default values
preserve backward compatibility.

### 1. DOTAX TY2018-2022 historical actuals

Replaced the 3%/yr synthetic backcast with measured DOTAX
"Tax Credits Claimed" actuals for TY2018-2022:

| Vintage | Individual ($M) | Corporate+Other ($M) | All ($M) |
|---------|----------------:|---------------------:|---------:|
| TY2018  | $34.21M | $36.29M | $70.50M |
| TY2019  | $44.02M | $16.29M | $60.31M |
| TY2020  | $46.59M | $66.03M | $112.61M |
| TY2021  | $51.45M | $15.86M | $67.32M |
| TY2022  | $55.02M | $50.74M | $105.76M |
| TY2023  | $58.29M | $41.78M | $100.07M |

Individual REEC grew ~10%/yr 2018→2023 — substantially faster than the
3%/yr synthetic backcast. Corporate is volatile (commercial PV
tax-equity transaction timing) and uses `all_total - individual_total`
to capture the disclosure-protected financial-corp pool. Loader is
`scripts/fetch_dotax_credits_historical.py` (one-shot fetch);
parsed CSV at
`packages/tax_modeler/src/tax_modeler/data/raw/dotax_reec_historical.csv`.

### 2. Dynamic AGI eligibility recomputation per year

§235-12.5(a) thresholds ($175K single/HoH/MFS, $350K MFJ) are
unindexed. New helper `compute_dynamic_agi_eligibility_share(projected_units)`
computes the aggregate eligibility share each forecast year from the
projected tax-unit AGI distribution, weighted by TY2023 DOTAX REEC
dollar shares per bin. Replaces the frozen TY2023 PUMS-derived 0.796.
The enhanced forecast calls it for each TY 2027-2031 and passes a
year-specific override.

### 3. Pro-rata demand suppression (endogenous)

§235-12.5(h)'s pro-rata cap allocation discounts the expected value of
each filer's credit when the cap binds. New `pro_rata_elasticity`
parameter applies suppression factor `s = pro_rata^η` to demand. Scenario
bands: LOW η=0.5, MID η=0.3, HIGH η=0.15. Disabled (η=0) for backward
compat.

### 4. Refundable share adjustment for AGI-screened pool

AGI filter removes high-income / high-tax-liability filers; the remaining
pool skews lower-income. Per §235-12.5(k) (30% reduced refundable) and
§235-12.5(l) (auto-refundable for low-AGI), lower-income filers elect
refundability more. New
`_refundable_share_individual_for_eligibility(eligibility_share)` returns
a piecewise estimate (0.23 at 1.0 eligibility → 0.45 at 0.5).

### 5. TY2026 retroactive cap interpretation (A vs B)

Section 9(1) makes Section 1 retroactive to TY2026. §235-12.5(c)(1) caps
CY2027 certifications at $40M. Under interpretation A (CY of certification =
TY of install + 1), CY2027 cap binds TY2026 installations. Under
interpretation B (CY = TY, default), TY2026 is uncapped. New
`interpretation: "A" | "B"` parameter. LOW uses A (conservative for the
State); MID/HIGH/RECESSION use B.

### Round-2 impact on MID 5-year cumulative

| Channel | After May 14 vintage | After Round 2 | Δ |
|---------|---------------------:|--------------:|--:|
| Total fiscal impact | $781.8M | $781.9M | +$0.1M |
| Credit total | $441.9M | $442.0M | +$0.1M |
| Bracket delta | $339.9M | $339.9M | unchanged |

MID barely moves: TY2027 income hasn't grown enough from TY2023 to
materially shift dynamic eligibility (0.796 → ~0.795); pro-rata suppression
reduces both new certs and future drawdown ~symmetrically; the refundable
share shift moves the timing of state cost but not the total. **LOW
shifts +$11M (to $703.7M) because interpretation A caps the TY2026 vintage,
reducing the pre-2027 stock entering the simulation window.**

The bigger value of Round 2 is **diagnostic transparency** rather than
headline fiscal impact: per-year eligibility share, pro-rata factor,
suppression factor, refundable share, and interpretation flag are now
reported per forecast year, enabling clean sensitivity sweeps.

### Files changed

- `packages/tax_modeler/src/tax_modeler/scenarios/sb3125_cd1_credits.py` —
  DOTAX loader, `compute_dynamic_agi_eligibility_share`,
  `_refundable_share_individual_for_eligibility`,
  `_certified_credits_for_vintage` extended, `simulate_reec_state_cost_path`
  extended, `compute_credit_overlay` gains four optional params
- `scripts/fetch_dotax_credits_historical.py` — DOTAX XLSX fetcher/parser
- `packages/tax_modeler/src/tax_modeler/data/raw/dotax_reec_historical.csv`
- `forecast_sb3125_cd2_enhanced.py` — scenario knobs + per-year AGI
  eligibility computation
- `tests/tax_modeler/scenarios/test_sb3125_cd1_credits_v2.py` — 16 new tests

---

## CD2 vintage carryforward correction — May 14, 2026

After reviewing the enrolled CD2 text, the REEC fiscal model was extended to
track nonrefundable credit stock by vintage year. Three findings from the
bill review drove the change:

1. **§235-12.5(j) preserves "until exhausted" carryforward** with no time
   limit and no AGI re-test. The bill amendments do not void or restrict
   credits certified before TY2027 — they remain usable indefinitely
   against future tax liability.
2. **§235-12.5(h) cap applies to DBEDT certifications**, not to utilization
   of previously-certified credits. The $40M aggregate cap therefore does
   not constrain drawdown of pre-2027 vintage stock.
3. **§235-12.5(p) sunset prevails over §235-12.5(c)(4)**: the section
   "shall not apply to taxable years beginning after December 31, 2029."
   TY2030 has no new certifications even though (c)(4) lists a $40M cap
   for CY2030. The legacy static-overlay model treated TY2030 as a cap
   year ($40M payout), materially understating savings.

**Modeling approach.** A vintage simulation tracks individual and corporate
nonrefundable stock from TY2010 forward using the dynamic
`stock_t = (1-u)·(stock_{t-1} + nonref_certs_t)` with `usage_t = u·(stock_{t-1} + nonref_certs_t)`,
where `u = reec_effective_claim_share`. Pre-2023 vintages are back-cast at 3%/yr nominal growth; TY2024–2026 use the model's
nominal income growth × OBBBA demand decay. The simulator is run twice per
target year — once with `cap_enabled=False` (baseline: no cap, no AGI filter,
no sunset) and once with `cap_enabled=True` (bill: AGI filter for TY2027+,
$40M cap for TY2027–2029, zero for TY2030+). State cost in target year =
refundable payments certified that year + nonref stock drawdown. Savings =
baseline state cost − scenario state cost. Pre-2027 stock drawdown is
identical under both paths and cancels in the differential; what remains is
(a) ineligible-by-AGI demand permanently lost in cap years and (b) reduced
future drawdown from capped 2027–2029 vintages.

**Impact on MID results (5-year cumulative, 2027–2031):**

| Channel | Legacy static | Vintage simulation | Δ |
|---------|--------------:|-------------------:|--:|
| REEC savings | $236.3M | $343.3M | +$107.0M |
| Total credit | $334.6M | $441.9M | +$107.3M |
| Total fiscal impact | $674.5M | $781.8M | +$107.3M |

The TY2030 row carries most of the correction (+$50M from properly applying
the (p) sunset instead of treating TY2030 as a $40M cap year).

**Files changed:**
- `packages/tax_modeler/src/tax_modeler/scenarios/sb3125_cd1_credits.py` — added `simulate_reec_state_cost_path`, `_historical_reec_individual/corporate`, `_certified_credits_for_vintage`; added `model_carryforward_pool` flag to `compute_credit_overlay` (default `False` for backward compatibility).
- `forecast_sb3125_cd2_enhanced.py` — now calls overlay with `model_carryforward_pool=True`.

**Interpretation note.** The model uses interpretation B (CY in §235-12.5(c)
maps directly to TY of installation). Interpretation A (CY = TY of
certification = TY of install + 1) produces the same qualitative results —
TY2026–2029 installations are capped at $40M each, TY2030+ blocked by (p).
The difference is bookkeeping only.

---

## CD2 update — May 11, 2026

SB 3125 CD2 has been received and a new `sb3125_cd2` scenario has been
added to the model.

**Key finding:** The brackets loaded in the CSV under the `sb3125_cd1` tag
already reflected the CD2-vintage bracket values (2.50%/5.00% mid rates,
13% at $1M+ MFJ/$750K+ HoH/$500K+ Single). The CD1-labeled brackets in
the codebase were CD2 brackets — so **running `forecast_sb3125_cd2.py`
produces identical fiscal-impact numbers to the existing CD1 forecast**.

The `sb3125_cd2` tag in the CSV and `get_sb3125_cd2_system()` in the
registry are now the authoritative labels going forward.

**What CD2 adds vs the "CD1" that was modeled:**

Credit provisions — all already reflected in `sb3125_cd1_credits.py`
(reused by `forecast_sb3125_cd2.py`):
- §235-12.5 REEC: AGI limits $175K single / $350K MFJ, $40M cap 2027–2030, sunset after Dec 31, 2029
- §235-110.7 CGEC: sunset after Dec 31, 2027
- Act 261 §5 TCRA: repeal accelerated to Jan 1, 2029

New repeals in CD2 **not yet modeled** (treated as $0):
- §235-110.51 Technology Infrastructure Renovation Tax Credit: repealed TY 2028+ — no TY2023 DOTAX line data available; estimated near-zero
- §235-110.9 High Technology Business Investment Tax Credit: repealed TY 2029+ — credit was carryforward-only by TY2023; estimated near-zero

**New files added:**
- `reforms/sb3125_cd2.yaml`
- `forecast_sb3125_cd2.py` (output: `/tmp/sb3125_cd2_fiscal_impact_2027_2031.csv`)

---

## Methodology revisions — May 2026

The following corrections were merged after the May 2026 review and supersede
the corresponding sections below; results in Section 10 will refresh on the
next forecast run.

1. **Per-filer ETI based on actual MTR change.** The old ETI step
   applied a flat ``((1 − 0.13)/(1 − 0.11)) ** eti`` factor to every
   filer above the new 13% threshold. SB 3125 CD1 raises rates across
   the entire $350K–$1M MFJ range (and equivalents for HoH/Single), not
   just at $1M+, so the old approach attributed *zero* behavioral
   response to those mid-tier rate increases — overstating the static
   bracket gain in the upper-middle band. The new
   :func:`apply_eti_response` looks up each filer's marginal rate under
   both Act 46 and SB 3125 CD1 (via vectorized bracket-boundary
   ``searchsorted``) and applies the ETI factor based on the actual
   per-filer net-of-tax change. Filers facing no rate change get
   factor 1.0; filers facing a rate cut also get 1.0 (asymmetric
   treatment, matching the literature).

2. **Effective deduction plumbed into the bracket comparison.**
   ``project_tax_units_forward`` already computes each filer's
   effective deduction (the larger of standard and Pease-limited
   itemized) and stores it as ``hi_standard_deduction``. Previously
   ``compare_systems`` ignored that column and applied the standard
   deduction on its own, which forced a separate
   ``apply_itemized_deduction_adjustment`` step that subtracted a
   flat $40K–$80K from each top filer's income as a stand-in.
   ``compare_systems`` now accepts a ``deduction_col`` parameter; the
   enhanced forecast passes ``"hi_standard_deduction"`` for both
   baseline and scenario, so the bracket comparison uses the same
   per-filer deduction as the projection step. The per-scenario
   ``itemized_adj`` flag has been retired.

3. **REEC: residential vs commercial demand decay.** OBBBA terminated
   §25D (residential) but extended §48E (commercial) through
   12/31/2027. The old code applied the §25D-based demand decay
   (`obbba_mid`, `obbba_severe`) to *both* individual and corporate
   REEC pools, understating corporate baseline demand by ~10–20%.
   The new ``_reec_demand_factor_corporate`` returns 1.0 for all
   forecast years; only individual / residential REEC sees the §25D
   decay.

4. **REEC: refundable / nonrefundable utilization split.** The old
   ``effective_claim_share`` parameter was applied uniformly to all
   REEC dollars. But refundable claims offset state revenue 1:1; only
   nonrefundable claims have utilization concerns (carry-forward,
   expiration). DOTAX 2023 actuals: individual REEC is ~23%
   refundable, corporate REEC is ~89% refundable. The parameter has
   been re-interpreted as a *nonrefundable utilization rate*; the
   aggregate effective share is computed per pool. At the MID setting
   of 0.80, individual aggregate share is ~85% and corporate is ~98%
   (vs the previous 80% for both).

5. **Top-income growth premium base-year alignment.** Previously
   ``TOP_INCOME_PREMIUM_BASE_YEAR = 2023`` while the PUMS panel and
   B19013 county projections anchor on 2024 (the most recent ACS
   1-year vintage in the bundled panel). Compounding from 2023 added
   one extra year of premium on top of the projection. Base year is
   now 2024; the MID multiplier for TY 2027 falls from
   ``1.013⁴ = 1.053`` (+5.3%) to ``1.013³ = 1.040`` (+4.0%).

---

## Table of Contents

1. [Overview](#1-overview)
2. [Bill Summary](#2-bill-summary)
3. [Act 46 vs SB 3125 CD1 — Bracket-by-Bracket Comparison](#3-act-46-vs-sb-3125-cd1--bracket-by-bracket-comparison)
4. [Data Sources](#4-data-sources)
5. [Methodology](#5-methodology)
6. [Behavioral Response](#6-behavioral-response)
7. [Credit Overlay](#7-credit-overlay)
8. [Scenario Design](#8-scenario-design)
9. [Distributional Analysis](#9-distributional-analysis)
10. [Results](#10-results)
11. [Caveats and Limitations](#11-caveats-and-limitations)
12. [Scripts and File Map](#12-scripts-and-file-map)
13. [Software and Packages](#13-software-and-packages)

---

## 1. Overview

This forecast estimates the year-by-year fiscal impact to the State of Hawaii from enacting **SB 3125 CD1** (2026 conference draft) relative to the current-law baseline of **Act 46 (2024)** for tax years 2027 through 2031.

The model uses a **bottom-up microsimulation** approach: it constructs roughly 43,000 representative Hawaii income tax units from the U.S. Census Bureau's Public Use Microdata Sample, calibrates them against DOTAX administrative data, projects them forward year by year using county-level income growth forecasts, and computes each unit's tax liability under both the baseline and the proposed law. The total fiscal impact is the population-weighted difference, adjusted for behavioral responses and a separate static-scoring credit overlay.

**MID scenario 5-year result: $673.0M** (May 7, 2026 re-run incorporating population growth, COR March 2026 projections, and updated calibration. PTE shift is currently zero in this run — see Section 8a. Credit overlay revised down to $334.6M from earlier $503.2M estimate due to updated REEC and CGEC baseline projections.)

---

## 2. Bill Summary

SB 3125 CD1 changes Hawaii's individual income tax in three ways:

### 2a. §235-51 Bracket Changes (Effective TY 2027 and TY 2029)

The bill strikes Act 46's scheduled bracket phase-ins and replaces them with two new schedules:

| Change | Act 46 Rate | SB 3125 CD1 Rate | Who It Affects |
|--------|-------------|------------------|----------------|
| Second bracket cut | 3.20% | 2.50% | Income above ~$28,800 (MFJ) / $14,400 (Single) |
| Third bracket cut | 5.50% | 5.00% | Income above ~$38,400 (MFJ) / $19,200 (Single) |
| New top bracket | 11% (max) | **13%** | Income above $1,000,000 (MFJ) / $750,000 (HoH) / $500,000 (Single) |

The 1.40% bottom bracket rate is **unchanged** in both systems — Act 46 and SB 3125 CD1 use an identical 1.40% rate and identical bracket width in all modeled years. Act 46's phase-in widens the 1.40% bracket threshold (from $19,200 to $38,400 MFJ by 2029); SB 3125 CD1 preserves these same expanded thresholds.

A second set of bracket schedules takes effect for TY 2029 (per bill text "after December 31, 2028"), further widening the lower brackets. Both schedules are modeled explicitly.

### 2b. §235-12.5 Renewable Energy Technologies Income Tax Credit (REEC)

- Caps aggregate annual claims at **$40M for TY 2027–2030**
- Eliminates the credit entirely **from TY 2031** (cap → $0)
- Adds AGI eligibility limits: $175K single / $350K joint

DOTAX TY2023 baseline: $99.6M in total REEC claims ($58.3M individual + $38.6M corporate + $3.2M other).

### 2c. §235-110.7 Capital Goods Excise Tax Credit (CGEC)

- Sunsets effective December 31, 2027 — applies from **TY 2028** onward
- DOTAX TY2023 baseline: $34.6M in total CGEC claims (rising trend from $29.3M in 2021)

### 2d. §235-110.91 Tax Credit for Research Activities (TCRA)

- Accelerated wind-down relative to Act 46 baseline
- One-time acceleration effect modeled for **TY 2029** only (~$8–9M)

---

## 3. Act 46 vs SB 3125 CD1 — Bracket-by-Bracket Comparison

The tables below show every bracket for each filing status and each effective period. Cells marked **bold** differ between the two systems. A "—" means no bracket exists at that threshold under that system.

> **How to read these tables:** Each row is a bracket that begins at the listed income threshold and runs to the next row's threshold. The rate shown applies to income within that range only (Hawaii uses a progressive structure). The 1.40% bottom bracket is identical in both systems across all years — Act 46's phase-ins widen it without changing the rate.

---

### Married Filing Jointly / Qualifying Surviving Spouse

#### TY 2027–2028

| Income Threshold | Act 46 Rate | SB 3125 CD1 Rate | Change |
|-----------------|------------|-----------------|--------|
| $0 | 1.40% | 1.40% | — |
| $28,800 | 3.20% | **2.50%** | **−0.70 pp** |
| $38,400 | 5.50% | **5.00%** | **−0.50 pp** |
| $48,000 | 6.40% | 6.40% | — |
| $72,000 | 6.80% | 6.80% | — |
| $96,000 | 7.20% | 7.20% | — |
| $250,000 | 7.60% | 7.60% | — |
| $350,000 | 7.90% | **8.25%** | **+0.35 pp** |
| $450,000 | 8.25% | **9.00%** | **+0.75 pp** |
| $550,000 | 9.00% | **10.00%** | **+1.00 pp** |
| $650,000 | 10.00% | **11.00%** | **+1.00 pp** |
| $800,000 | 11.00% | 11.00%¹ | — |
| $1,000,000 | — | **13.00%** | **New bracket** |

¹ Under SB 3125 CD1, the 11% bracket runs from $650K to $1M (a narrower range than Act 46's $800K–∞). The 13% bracket then applies above $1M.

#### TY 2029–2031

| Income Threshold | Act 46 Rate | SB 3125 CD1 Rate | Change |
|-----------------|------------|-----------------|--------|
| $0 | 1.40% | 1.40% | — |
| $38,400 | 3.20% | **2.50%** | **−0.70 pp** |
| $48,000 | 5.50% | **5.00%** | **−0.50 pp** |
| $72,000 | 6.40% | 6.40% | — |
| $96,000 | 6.80% | 6.80% | — |
| $250,000 | 7.20% | 7.20% | — |
| $350,000 | 7.60% | **8.25%** | **+0.65 pp** |
| $450,000 | 7.90% | **9.00%** | **+1.10 pp** |
| $550,000 | 8.25% | **10.00%** | **+1.75 pp** |
| $650,000 | 9.00% | **11.00%** | **+2.00 pp** |
| $800,000 | 10.00% | — | *(absorbed into 11% bracket)* |
| $950,000 | 11.00% | 11.00%¹ | — |
| $1,000,000 | — | **13.00%** | **New bracket** |

---

### Head of Household

#### TY 2027–2028

| Income Threshold | Act 46 Rate | SB 3125 CD1 Rate | Change |
|-----------------|------------|-----------------|--------|
| $0 | 1.40% | 1.40% | — |
| $21,600 | 3.20% | **2.50%** | **−0.70 pp** |
| $28,800 | 5.50% | **5.00%** | **−0.50 pp** |
| $36,000 | 6.40% | 6.40% | — |
| $54,000 | 6.80% | 6.80% | — |
| $72,000 | 7.20% | 7.20% | — |
| $187,500 | 7.60% | 7.60% | — |
| $262,500 | 7.90% | **8.25%** | **+0.35 pp** |
| $337,500 | 8.25% | **9.00%** | **+0.75 pp** |
| $412,500 | 9.00% | **10.00%** | **+1.00 pp** |
| $487,500 | 10.00% | **11.00%** | **+1.00 pp** |
| $600,000 | 11.00% | 11.00%¹ | — |
| $750,000 | — | **13.00%** | **New bracket** |

¹ SB 3125 CD1 narrows the 11% bracket to $487,500–$750,000; above $750,000 the 13% rate applies.

#### TY 2029–2031

| Income Threshold | Act 46 Rate | SB 3125 CD1 Rate | Change |
|-----------------|------------|-----------------|--------|
| $0 | 1.40% | 1.40% | — |
| $28,800 | 3.20% | **2.50%** | **−0.70 pp** |
| $36,000 | 5.50% | **5.00%** | **−0.50 pp** |
| $54,000 | 6.40% | 6.40% | — |
| $72,000 | 6.80% | 6.80% | — |
| $187,500 | 7.20% | 7.20% | — |
| $262,500 | 7.60% | **8.25%** | **+0.65 pp** |
| $337,500 | 7.90% | **9.00%** | **+1.10 pp** |
| $412,500 | 8.25% | **10.00%** | **+1.75 pp** |
| $487,500 | 9.00% | **11.00%** | **+2.00 pp** |
| $600,000 | 10.00% | — | *(absorbed into 11% bracket)* |
| $712,500 | 11.00% | 11.00%¹ | — |
| $750,000 | — | **13.00%** | **New bracket** |

---

### Single / Married Filing Separately

#### TY 2027–2028

| Income Threshold | Act 46 Rate | SB 3125 CD1 Rate | Change |
|-----------------|------------|-----------------|--------|
| $0 | 1.40% | 1.40% | — |
| $14,400 | 3.20% | **2.50%** | **−0.70 pp** |
| $19,200 | 5.50% | **5.00%** | **−0.50 pp** |
| $24,000 | 6.40% | 6.40% | — |
| $36,000 | 6.80% | 6.80% | — |
| $48,000 | 7.20% | 7.20% | — |
| $125,000 | 7.60% | 7.60% | — |
| $175,000 | 7.90% | **8.25%** | **+0.35 pp** |
| $225,000 | 8.25% | **9.00%** | **+0.75 pp** |
| $275,000 | 9.00% | **10.00%** | **+1.00 pp** |
| $325,000 | 10.00% | **11.00%** | **+1.00 pp** |
| $400,000 | 11.00% | 11.00%¹ | — |
| $500,000 | — | **13.00%** | **New bracket** |

¹ SB 3125 CD1 narrows the 11% bracket to $325,000–$500,000; above $500,000 the 13% rate applies.

#### TY 2029–2031

| Income Threshold | Act 46 Rate | SB 3125 CD1 Rate | Change |
|-----------------|------------|-----------------|--------|
| $0 | 1.40% | 1.40% | — |
| $19,200 | 3.20% | **2.50%** | **−0.70 pp** |
| $24,000 | 5.50% | **5.00%** | **−0.50 pp** |
| $36,000 | 6.40% | 6.40% | — |
| $48,000 | 6.80% | 6.80% | — |
| $125,000 | 7.20% | 7.20% | — |
| $175,000 | 7.60% | **8.25%** | **+0.65 pp** |
| $225,000 | 7.90% | **9.00%** | **+1.10 pp** |
| $275,000 | 8.25% | **10.00%** | **+1.75 pp** |
| $325,000 | 9.00% | **11.00%** | **+2.00 pp** |
| $400,000 | 10.00% | — | *(absorbed into 11% bracket)* |
| $475,000 | 11.00% | 11.00%¹ | — |
| $500,000 | — | **13.00%** | **New bracket** |

---

### Summary: What Changes and for Whom

| Income Band (MFJ example) | Net Rate Change TY2027 | Net Rate Change TY2029 | Effect |
|--------------------------|------------------------|------------------------|--------|
| $0 – $28,800 | None | None | Unchanged |
| $28,800 – $38,400 | **−0.70 pp** (3.20→2.50) | **−0.70 pp** (3.20→2.50) | Tax cut |
| $38,400 – $48,000 | **−0.50 pp** (5.50→5.00) | **−0.50 pp** (5.50→5.00) | Tax cut |
| $48,000 – $250,000 | None | None | Unchanged |
| $250,000 – $350,000 | None | None | Unchanged |
| $350,000 – $450,000 | **+0.35 pp** (7.90→8.25) | **+0.65 pp** (7.60→8.25) | Tax increase |
| $450,000 – $550,000 | **+0.75 pp** (8.25→9.00) | **+1.10 pp** (7.90→9.00) | Tax increase |
| $550,000 – $650,000 | **+1.00 pp** (9.00→10.00) | **+1.75 pp** (8.25→10.00) | Tax increase |
| $650,000 – $1,000,000 | **+1.00 pp** (10.00→11.00) | **+2.00 pp** (9.00→11.00) | Tax increase |
| Above $1,000,000 | **+2.00 pp** (11.00→13.00) | **+2.00 pp** (11.00→13.00) | Tax increase |

> **Key observation:** The bill is not simply "low rates cut, high rates raised." Rates in the $350K–$1M range are also raised — by up to 2 pp under the TY2029 schedule. This means a meaningful portion of the revenue gain comes from earners well below the $1M threshold. The upper-middle bracket increases grow larger over time because Act 46 progressively lowers those rates in 2027 and 2029, widening the gap that SB 3125 CD1 then closes upward.

---

## 4. Data Sources

### Microdata
| Source | Description | Use |
|--------|-------------|-----|
| **ACS 5-Year PUMS 2020–2024** | U.S. Census Bureau, Hawaii (State FIPS 15) — `psam_p15.csv` / `psam_h15.csv` | Base population of tax units |
| **DOTAX Hawaiʻi Individual Income Tax Statistics, TY2023** (Tables A-8, A-1, 4, 21, 17A; `calibration/dotax_base.py`) | Resident filer count and tax by AGI class, AGI, filing status, net long-term gains, nonresident liability | Calibration and forward targets, and top-income synthesis: the $1M+ class (Table A-8's top row) is set from a TY2019–2023 window, 1,633 returns above $1M AGI owing $562.3M (1,704 and $441M as printed), a TY2023-scale level aged to the PUMS dollar year before projection (Step 6b). Was TY2022 (1,824 returns, $662.6M) until October 8, 2026 |
| **DOTAX Earned Income Tax Credit Report, TY2024** (Act 107) | State EITC claims and dollars by federal AGI range, residency, filing status | Take-up anchor for the working-family credits page (78,399 new claims, $76.981M) |
| **DOTAX "Tax Credits Claimed by Hawaiʻi Taxpayers — Tax Year 2023"** | Table A-1 (REEC aggregate), Table A-5 (REEC by AGI bin), line 1490 (CGEC) | Credit overlay baseline values |

### Administrative Benchmarks
| Source | Description | Use |
|--------|-------------|-----|
| **DOTAX TY2023 aggregate statistics** | Revenue by filing status and AGI bracket | IPF calibration targets |
| **Hawaii Council on Revenues (COR)** — newest General Fund meeting, auto-refreshed into `data/cor/cor_iit_projections.json` (currently **September 3, 2026**) | Individual income tax line-item projections by fiscal year, from the DOTAX Tax Research & Planning attachment | Baseline validation + `cor_scale_factor_for_year` diagnostic columns |
| **COR / DOTAX Act 46 fiscal estimate** (May 21 and September 9, 2026 letters, identical) | Act 46 General Fund loss: $740.1M FY2027 rising to $1,453.2M FY2032 | Official cross-check on the pre-Act-46 reconciliation |
| **COR / DOTAX Act 24 fiscal estimate** (September 9, 2026 letter) | Act 24 gain to the General Fund: $145.2M FY2028 rising to $308.9M FY2033 | Upper reference for MID (October 5, 2026 section) |

### Legal / Statutory
| Source | Description |
|--------|-------------|
| **Act 46, SLH 2024** | Current-law baseline bracket schedules and phase-in dates |
| **SB 3125 CD1 (2026 conference draft)** | Bill text — bracket schedules, credit cap amounts, effective dates |
| **Act 50, SLH 2024** | Hawaii Pass-Through Entity (PTE) tax — rate fixed at **9%** for TY2024+, does not auto-track individual rate |

### Economic Projections
| Source | Description | Use |
|--------|-------------|-----|
| **ACS B19013 (Median Household Income by County)** | Census Bureau, bundled ACS panel | County-level income growth rates for TY projection |
| **BLS CPI-U `CUURS49FSA0` (Urban Hawaii, all items)** | BLS, bundled CPI panel; bimonthly, published from 2017 | Hawaii price deflator for nominal↔real income conversion. **Corrected July 30, 2026** — previously `CUURS49ASA0`, which is Los Angeles. |
| **SEIA U.S. Solar Market Insight (2025)** | National residential solar demand forecasts | REEC demand decay scenarios post-OBBBA |
| **Honolulu DPP building permits** | data.honolulu.gov `4vab-c87q`, bundled solar-permit aggregates (1999→2025-06) | Empirical validation of REEC demand scenarios (diagnostic only; ~12-month publication lag) |
| **DOTAX monthly collections** | files.hawaii.gov/tax/stats/monthly `{YYYYMM}collec.xlsx` + `{YYYYMM}ge.xlsx`, bundled accumulating archive (2024-07→) | Revenue ground-truth validation: withholding (wages+policy), GE&Use (rate-stable activity), TAT (tourism). Diagnostic only |

### Academic Literature (Behavioral Response)
| Citation | Use |
|----------|-----|
| Saez, Slemrod & Giertz (2012), "The Elasticity of Taxable Income with Respect to Marginal Tax Rates," *Journal of Economic Literature* | ETI range: 0.15–0.60 |
| Young & Varner (2011), "Millionaire Migration and State Taxation of Top Incomes: Evidence from a Natural Experiment," *National Tax Journal* | New Jersey's 2004 rise: semi-elasticities below 0.1 **percent** of millionaires per effective pp a year |
| Young, Varner, Lurie & Prisinzano (2016), "Millionaire Migration and Taxation of the Elite," *American Sociological Review* | Millionaires migrate at 2.4% a year; a 1 pp rise moves about 0.26% of a state's millionaires a year |
| Cohen, Lai & Steindel (2015), replication of Young & Varner (2011), *Public Finance Review* | About 80 extra out-migrants over $500K a year after New Jersey's 2.6 pp rise |
| Rauh & Shyu (2024), "Behavioral Responses to State Income Taxation of High Earners: Evidence from California," *AEJ: Economic Policy* | Prop. 30's +3 pp: an extra 0.8% of the top bracket left, once; stayers' ETI 2.5–3.2 on the combined rate |
| IRS SOI migration data — Hawaii high-income outmigration trends | Migration response calibration |

---

## 5. Methodology

### Step 1 — Load PUMS

**Script:** `forecast_sb3125_cd1.py` → `PUMSDataLoader`  
**File:** `packages/tax_modeler/src/tax_modeler/loaders/pums_loader.py`

Loads Hawaii person and household records from the ACS 5-year PUMS CSV files (`psam_p15.csv`, `psam_h15.csv`). The loader handles cross-vintage column renaming (e.g., `STATE`→`ST`) and batches reads to manage memory. Income variables are adjusted using ADJINC (1.222017 for the 2020–2024 vintage, representing the 5-year inflation adjustment to 2024 dollars).

### Step 2 — Construct Tax Units

**Class:** `TaxUnitConstructor`  
**File:** `packages/tax_modeler/src/tax_modeler/units/constructor.py`

Assembles person and household PUMS records into income tax filing units using rule-based logic:

- **Head of household:** Single filer with qualifying dependent(s)
- **Married filing jointly:** Married couple in same household
- **Single / married filing separately:** All other configurations

Each unit inherits the household weight (`WGTP`) as its initial statistical weight representing the number of real Hawaii filers it stands in for. Income components (wages `WAGP`, self-employment `SEMP`, interest `INTP`, retirement `RETP`, Social Security `SSP`, etc.) are summed across the primary filer and spouse. Results are cached at `/tmp/tax_units_cache.parquet` to avoid the ~3-minute rebuild on repeated runs (~43,300 units before top-income synthesis).

### Step 3 — Enrich for Credits

**Function:** `_enrich_for_credits()`  
**File:** `packages/tax_modeler/src/tax_modeler/pipeline.py`

Adds credit-eligibility fields needed for the REEC AGI eligibility test (Hawaii §235-12.5 AGI limits: $175K single / $350K joint). Derives `agi_approx` from the income components and flags units as eligible or ineligible for the credit overlay.

### Step 4 — Compute Base Tax

**Function:** `_compute_base_tax()`  
**File:** `packages/tax_modeler/src/tax_modeler/pipeline.py`

Runs `TaxCalculator.calculate_tax()` on each unit using the current-law bracket schedule (Act 46, TY2025 parameters). Produces `base_tax_liability`, `marginal_rate`, and `effective_rate` columns used downstream for calibration and ETI calculations.

### Step 5 — IPF Rake Calibration

**Function:** `apply_ipf_calibration_via_rake()`  
**File:** `packages/tax_modeler/src/tax_modeler/calibration.py`

Iterative Proportional Fitting (IPF) adjusts unit weights so the PUMS-derived totals match DOTAX administrative benchmarks across multiple dimensions simultaneously (filing status × AGI bracket). Weight cap: 1.5× original weight per unit to prevent extreme upweighting of a single record. This step corrects the known PUMS undercount of higher-income filers within the range reachable by reweighting alone.

### Step 6 — Top-Income Synthesis (Pareto)

**Function:** `synthesize_top_filers()`  
**File:** `packages/tax_modeler/src/tax_modeler/scenarios/top_income_synthesis.py`

**Why this step exists:** The IPF rake can close small gaps but cannot close the 2.6× gap at $1M+. The ACS PUMS contains only 634 weighted filers above $1M (after calibration) vs. the target of 1,633 (the TY2019–2023 window on Table A-8; 1,704 as printed for TY2023, and 1,824 on the TY2022 target, against ~342 measured then). This undercount would make the 13% top bracket appear almost invisible in the model.

**How it works:** Generates synthetic tax units with incomes drawn from a Pareto distribution with shape parameter α (default α = 1.5, calibrated to match the IRS SOI 2022 Hawaii tail shape). These units are given realistic filing-status mixes (drawn from the DOTAX TY2023 $1M+ filer population: ~65% MFJ, ~25% Single, ~8% HoH, ~2% MFS) and are added to the calibrated dataset. Base tax is recomputed for all units after synthesis.

**Validation target:** 1,633 weighted $1M+ filers with $562.3M in aggregate tax (the TY2019–2023 window on DOTAX Table A-8: the mean share of resident returns and tax, on TY2023's totals; TY2023 as printed is 1,704 and $441M, TY2022 was 1,824 and $662.6M). The synthesis hits 100% of the filer count target by construction.

### Step 6a — Synthetic Tail Tax-Target Calibration

**Function:** `calibrate_synthetic_tail_to_tax_target()` (first step: `rescale_synthetic_tail_to_tax_target()`)  
**File:** `packages/tax_modeler/src/tax_modeler/scenarios/top_income_synthesis.py`

**Why this step exists:** The Pareto conditional-mean income formula slightly underestimates income concentration above ~$10M — the very top of the tail — causing the raw synthesis to recover only ~88% of the DOTAX tax benchmark (measured on the TY2022 target, $663M). A 12% shortfall in the baseline tax at $1M+ directly translates to a ~12% undercount of marginal revenue from the 13% bracket, approximately $14–17M per year.

**How it works:** After synthesis and a first `compute_base_tax()`, the synthetic rows' income columns (`income`, `agi`, `synthetic_total_income`, `earned_income`, `investment_income`, `primary_wagp`, `primary_intp`) are scaled by a uniform factor k and re-scored until the tail's Hawaii tax is within 0.1% of the target ($562.3M). The first step is proportional, k = target / actual; later steps are secant updates on k. A single proportional step does not land on the target, because tax is not proportional to income even above $1M: the deduction, the lower brackets and the §235-51(f) alternative tax on gains make it roughly a·k − b. The single step used until September 29, 2026 overshot to $681.9M / $686.9M / $676.3M (LOW / MID / HIGH), 2.0–3.6% above the target; the iteration converges in two or three re-scores.

**Per-scenario k values:**

| Scenario | Pareto α | tail_k | Tail tax after calibration (TY2023 level) |
|----------|----------|--------|--------------------------------------------|
| LOW      | 1.7      | 1.6627 | 100.0% of $562.3M |
| MID      | 1.5      | 1.3514 | 100.0% |
| HIGH     | 1.4      | 1.1740 | 100.0% |

RECESSION uses α = 1.5, so its k is MID's. k is larger for LOW (α=1.7, thinner tail → lower initial tax capture) and smaller for HIGH (α=1.4, fatter tail). The §235-51(f) alternative tax raises k: capping gains at 7.25% lowers the tail's tax, so a larger scale is needed to reach the target. (On the TY2022 target, before October 8, 2026: 1.7743 / 1.4210 / 1.2343; on TY2023's printed $441M alone: 1.2605 / 1.0410 / 0.9046. Before September 29, 2026, with the single step: 1.8202 / 1.4685 / 1.2573; earlier still 1.5745 / 1.2793 / 1.1001.)

### Step 6b — Age the Tail to the PUMS Dollar Year

**Function:** `age_synthetic_tail()` (factor: `synthetic_tail_aging_factor()`)  
**File:** `packages/tax_modeler/src/tax_modeler/scenarios/top_income_synthesis.py`

The $562.3M target is at TY2023 scale, but every PUMS unit is in 2024 dollars (the 2020–2024 5-year file), and Step 7 grows every unit from 2024: the county B19013 factor from the projector's 2024 anchor, and the Step 8 premium from its 2024 base year. So after calibration the tail's incomes are multiplied by

```
g = B19013_Honolulu(2024) / B19013_Honolulu(2023) × (1 + premium) = 105,205 / 103,131 × (1 + p)
```

and re-scored. The B19013 levels are the observed 1-year ACS values from the same bundled panel the projector anchors on (read, not typed), and p is the scenario's own top-income premium: g = 1.0232 (LOW), 1.0303 (MID), 1.0436 (HIGH), 1.0334 (RECESSION) (on the TY2022 target it was 1.0958 / 1.1112 / 1.1400 / 1.1178, over two years). The premium applies to 2023–2024 as well because the model observes only median income in those years; each scenario's premium is its assumption about how top incomes grew relative to it. Until September 29, 2026 the tail entered the projection at its base-year level and never received the growth to 2024, while the same pipeline aged DOTAX's capital gains from their own year (`cg_anchor.cg_growth`).

The scripts that project with `project_and_recalibrate(use_soi_anchor=True)` (`forecast_sb3125_vs_fy26base.py`, `forecast_cg_rate_options.py`, `forecast_bill_quintile.py`) use the converging calibration but not the aging: the SOI anchor zeroes every unit above $1M, the Pareto rows included, and replaces them with SOI tier rows aged from SOI's TY2022, so the Pareto tail's starting level does not reach their results (checked: identical TY2027 output for the old, calibrated, and calibrated-and-aged tail). `forecast_sb3125_static_quintile.py` projects like Step 7 and is aged with p = 0.

### Step 7 — Project to Target Year

**Function:** `project_tax_units_forward()`  
**File:** `packages/tax_modeler/src/tax_modeler/projection/tax_unit_projector.py`

Scales each unit's income from the PUMS base year to each target year (2027–2031) using **county-specific income growth factors** derived from the ACS B19013 (Median Household Income) panel:

```
growth_factor(county, year) = projected_B19013(county, year) / anchor_B19013(county, base_year)
```

Hawaii's four counties (Honolulu, Maui, Hawaii, Kauai) each get their own growth trajectory. Kalawao County (~90 residents) is redirected to the Maui forecast. The `method="ensemble"` option uses the census_forecaster's ensemble projector for point estimates with 90% confidence intervals. All units in a given county receive the same multiplicative income scaling (proportional growth).

### Step 8 — Top-Income Growth Premium (MID/HIGH scenarios)

**Function:** `apply_top_income_growth_premium()`
**File:** `packages/tax_modeler/src/tax_modeler/scenarios/behavioral_response.py`

Applies an additional annual growth premium to units with income above $500K, on top of the county-level B19013 scaling. This corrects for the known divergence between top-1% income growth and median-anchored projections.

**Empirical calibration (IRS SOI 2012–2019):** The $500K+ AGI bracket nationally averaged ~3.0%/yr real growth vs. ~1.2%/yr for the median — a structural differential of ~1.8pp/yr. A 0.5pp Hawaii outmigration haircut (Young & Varner 2016 top-1% migration elasticity applied to Hawaii's geography) yields the MID anchor of **+1.3%/yr**. LOW and HIGH are symmetric ±1.0pp bounds:

| Scenario | Premium | Rationale |
|----------|---------|-----------|
| LOW | +0.3%/yr | Strong outmigration suppresses Hawaii top-income growth to near-median |
| **MID** | **+1.3%/yr** | IRS SOI 1.8pp national differential minus 0.5pp Hawaii haircut |
| HIGH | +2.3%/yr | Hawaii top-income growth converges toward national rates |

Formula: `multiplier = (1 + annual_premium)^(target_year − 2024)`. The base
year is **2024** to match the PUMS panel anchor (the bundled ACS 5-year
2020–2024 vintage is inflation-adjusted to 2024 dollars, and the
county B19013 projector anchors on the most recent 1-year ACS observation
— also 2024). For MID at TY 2027: 1.013³ = 1.040 (+4.0% vs. the
county-median-anchored projection). The previous version used base
year 2023, which compounded one extra year of premium on top of the
projection.

### Step 9 — Effective Deduction (per-filer, plumbed through comparison)

**File:** `packages/tax_modeler/src/tax_modeler/projection/tax_unit_projector.py`,
`packages/tax_modeler/src/tax_modeler/liability/hawaii.py`,
`packages/tax_modeler/src/tax_modeler/config/tax_system_config.py`

`project_tax_units_forward` computes each filer's effective deduction —
the larger of the year-appropriate standard deduction and the
expected-value itemized deduction (mortgage interest with homeownership
probability + tier-banded charitable + medical expenses + real-estate
taxes, with Hawaii's Pease limitation applied) — and stores it as
`hi_standard_deduction`. The enhanced forecast passes that column to
`compare_systems` via the new `deduction_col` parameter, so the
bracket comparison applies each filer's actual effective deduction
under both Act 46 and SB 3125 CD1.

This replaces the prior `apply_itemized_deduction_adjustment` step,
which subtracted a flat dollar amount ($40–$80K depending on filing
status) from each top filer's income to approximate the same effect.
The flat-amount approach was crude (real itemized deductions scale
with AGI — federal income tax paid alone is ~$300K+ at $1M AGI) and
required a per-scenario `itemized_adj` flag (with HIGH turning it off
entirely, which inflated HIGH's bracket gain by overstating the
taxable base). Both have been retired.

### Step 10 — Per-Unit Tax Calculation

**Class:** `TaxCalculator`  
**File:** `packages/tax_modeler/src/tax_modeler/config/tax_system_config.py`

For each projected unit, `TaxCalculator.calculate_tax()` computes tax liability under a given `TaxSystemConfig`. It applies the bracket schedule (looked up from the master CSV by year and scenario tag), computes taxable income after standard deduction and personal exemptions, applies brackets progressively, and returns tax liability along with marginal rate and effective rate.

Two configs are constructed for each year:

- `TaxSystemRegistry.get_act46_system(year)` — baseline Act 46 brackets
- `TaxSystemRegistry.get_sb3125_cd1_system(year)` — SB 3125 CD1 brackets (scenario tag `sb3125_cd1`, bracket_year 2027 for TY 2027–2028, bracket_year 2029 for TY 2029+)

The delta (SB 3125 CD1 minus Act 46) is the static bracket change before behavioral corrections.

### Step 10a — Capital Gains Cap (HRS §235-51(f))

**Implementation:** `TaxCalculator.calculate_tax()` (both `tax_system_config.py` and `liability/hawaii.py`)

HRS §235-51(f) caps the tax rate on net long-term capital gains at **7.25%** of the gain. This is applied in the model using the "stack" method:

```
ordinary_tax      = brackets applied to (total_income − cg_income)
cg_tax_uncapped   = total_bracket_tax − ordinary_tax
cg_tax_capped     = min(cg_tax_uncapped, cg_income × 7.25%)
final_tax         = ordinary_tax + cg_tax_capped
```

**SB 3125 CD1 does not change the §235-51(f) cap.** It remains 7.25% under both Act 46 and SB 3125 CD1. This means the 13% new bracket applies only to the *ordinary income* portion of $1M+ filer AGI — the CG portion is already taxed at a flat 7.25% and sees zero bracket delta from the bill.

**Data source for CG share:** Synthetic $1M+ filers carry a `synthetic_cg_share` column (derived from IRS SOI Hawaiʻi high-income composition data). ACS does not capture realized capital gains, so PUMS units from $100K to $1M get SOI-based shares (`calibration/cg_imputation.py`) and units below $100K get none. *(Corrected September 27, 2026: this said every PUMS unit below $1M had a share of 0.)*

**The statute's own formula** (`liability/cg_alternative.py`, `TaxSystemConfig.cg_alt_tax = "statute"`) is the lower of the regular tax and the bracket tax on the greater of taxable income less the gain and the taxable income taxed below 7.25%, plus 7.25% of the rest. The stack method above is never below it and differs only for filers whose taxable income other than gains is below the point where the brackets reach 7.25% and whose total taxable income is above it. This script and every registry system keep the stack method; the tax simulator uses the statute on a DOTAX-anchored gains base (see "Tax simulator v2: capital gains — September 27, 2026").

**Model impact:** Correctly applying the CG cap reduces the simulated baseline tax on synthetic filers, which in turn requires a larger tail_k in Step 6a to reach the target. The cap also reduces the static bracket delta — income that was previously over-taxed at bracket rates is correctly taxed at 7.25%, leaving less incremental revenue when the 13% bracket applies only to the ordinary component.

### Step 11 — Bracket Schedules (Master CSV)

**File:** `packages/tax_modeler/src/tax_modeler/data/raw/hawaii_tax_brackets_master_all.csv`

Contains all bracket schedules as rows with columns: `income_min`, `income_max`, `rate`, `base_tax`, `base_income`, `year`, `filing_status`, `scenario`.

| Year | Scenario tag | Description |
|------|-------------|-------------|
| 2018 | *(blank)* | Pre-Act 46 baseline |
| 2025 | *(blank)* | Act 46 first phase-in |
| 2027 | *(blank)* | Act 46 second phase-in (baseline for TY2027–2028) |
| 2029 | *(blank)* | Act 46 third phase-in (baseline for TY2029+) |
| 2027 | `sb3125_cd1` | SB 3125 CD1 TY2027–2028 schedules |
| 2029 | `sb3125_cd1` | SB 3125 CD1 TY2029+ schedules |

The `_bracket_year()` lookup selects the largest available vintage year ≤ the target year, so adding 2027 and 2029 entries automatically covers all five forecast years.

---

## 6. Behavioral Response

**File:** `packages/tax_modeler/src/tax_modeler/scenarios/behavioral_response.py`

Three behavioral channels are modeled, applied to the projected population: ETI to every filer whose marginal rate rises, migration to filers at or above the top-bracket floor (in full at $1M+), and the PTE election (off: capture is 0). Since September 27, 2026 the revenue after the response is scored against Act 46 **with nobody responding** (`score_with_response`): SB 3125 is scored on the responded population and Act 46 on the population as it is, so a filer who moves away costs their whole Hawaii tax and income reported away costs SB 3125's rate on it. Before, both laws were re-scored on the responded population, which charged a migrant only the rate increase (see the dated section at the top).

### 5a. Taxable Income Elasticity (ETI) — per-filer MTR change

High-income filers reduce their *reported* taxable income in response to a higher marginal rate — through increased charitable giving, deferred compensation, additional retirement contributions, and accelerated deductions. The standard form is:

```
%ΔTaxable Income = ETI × %Δ(1 − MTR)
```

The model computes each filer's marginal rate under both Act 46
and SB 3125 CD1 (vectorized bracket-boundary lookup on
``income − effective_deduction − exemption``) and applies the ETI
factor based on the actual per-filer rate change:

```
factor = ((1 − scen_mtr) / (1 − base_mtr)) ** ETI
```

This correctly captures the rate increases that SB 3125 CD1 applies
across $350K–$1M MFJ (and equivalents) as well as the new 13% bracket
above $1M. The previous implementation hard-coded ``base_mtr = 11%``
and ``scen_mtr = 13%`` for filers above the new top threshold and
applied no ETI at all to mid-tier filers, even though those filers
also face rate increases under the bill. Filers facing no rate change
(or a rate cut) get factor 1.0 — the literature is asymmetric and
rate cuts have weaker, less-established income-shrinkage feedback.

**Source:** Saez, Slemrod & Giertz (2012). Rauh & Shyu (2024) estimate
a much larger ETI for California's top earners (2.5–3.2 against the
combined net-of-tax rate); the model's values are within the general
literature, not calibrated to that study.

| Scenario | ETI | 5-year ETI offset alone, CD2 (September 27, 2026) |
|----------|-----|----------------------|
| LOW (high behavioral) | 0.60 | −$97.7M |
| **MID** | **0.40** | **−$67.8M** |
| HIGH (low behavioral) | 0.15 | −$27.7M |

The offset is SB 3125's full rate on the income reported away (t₁·Δy).
Until September 27, 2026 it was scored as the rate increase alone,
(t₁ − t₀)·Δy: MID −$8.2M over five years.

### 5b. Migration Response

Some high earners leave Hawaii when rates rise. `migration_elast` is the
share of $1M+ filers who leave per percentage point of top-rate increase,
at full phase-in; filers between the top-bracket floor ($500K single,
$750K head of household) and $1M lose half that. It is phased in linearly
over 5 years from the year each rise takes effect (migration is slow —
people don't leave overnight), and no group loses more than all of its
weight. Each filer who leaves costs their whole Hawaii tax.

The US evidence on state top rates puts the loss at roughly 0.1% to 0.5%
of millionaires per point, once rebased to the statutory rate (Act 24
raises $1M+ filers' average rate about 0.9 points for its 2): Young &
Varner (2011), Cohen, Lai & Steindel (2015), Young, Varner, Lurie &
Prisinzano (2016), Rauh & Shyu (2024). The values below span it, with the
strong-response end on the least certain estimates (New Jersey filers
earning all their income in-state, not significant; Young et al.'s flow
cumulated over five years).

| Scenario | Migration elasticity | $1M+ filers lost, 2 pp, full phase-in |
|----------|---------------------|--------|
| LOW (strong response) | 0.01 | 2% |
| **MID** | **0.0025** | **0.5%** |
| HIGH (weak response) | 0.001 | 0.2% |

**Corrected September 27, 2026.** Until then the values were 0.15 / 0.10 /
0.05 (10–30% of $1M+ filers), attributed to "Young & Varner (2016)" at
"~0.10–0.15 per percentage-point", with "a 50% discount" for Hawaiʻi's
geography. Young & Varner's semi-elasticities are in *percent* per point,
so that reading was about a hundred times too large, and no discount was
ever applied in the code (its only 0.5 is the half tier above).
`BEHAVIORAL_ACCOUNTING_REVIEW.md` has the sources and the arithmetic.

### 5c. Pass-Through Entity (PTE) Election — Dominant Behavioral Offset

*Off since May 5, 2026: capture is 0 in every scenario, because Act 58's
addback removes the Hawaiʻi incentive to elect (see `BehavioralParams`).
The design below is kept for reference.*

**This is the largest single behavioral offset in the model.**

Hawaii's Act 50 (SLH 2024) created a PTE tax at a fixed rate of **9%** for TY2024 and beyond. Critically, this rate does **not** automatically track the individual income tax rate. When SB 3125 CD1 raises the individual top rate to 13%, pass-through business owners (S-corps, partnerships, LLCs) face a **4-percentage-point arbitrage**: paying at the entity level (9%) vs. the individual level (13%).

A modeled share of eligible $1M+ pass-through filers elect PTE treatment, shifting their income out of the individual return and into the entity-level PTE return. This reduces individual income tax revenue by:

```
PTE revenue loss = eligible_ordinary_income × rate_differential × pte_capture
                 = (ordinary pass-through income above threshold) × 4% × capture_rate
```

**Capital gains are excluded from the PTE election pool** for two independent reasons:
1. CG income is not pass-through business income and is ineligible for HRS §235-110.93 election by statute.
2. Even if theoretically eligible, electing PTE on CG income would be irrational: the PTE rate (9%) exceeds the §235-51(f) CG cap (7.25%), so any rational filer would pay the cap rate rather than elect PTE.

In practice, the model uses `synthetic_cg_share` to compute `ordinary_income = total_income × (1 − cg_share)`, and the PTE excess is computed on ordinary income only. This reduces the PTE offset by approximately 50% relative to using total income, consistent with the high CG share (~50%) of $1M+ filer income.

| Scenario | PTE capture rate | 5-year revenue loss |
|----------|-----------------|---------------------|
| LOW | 90% | ~−$194M |
| **MID** | **70%** | **~−$185M** |
| HIGH | 40% | ~−$147M |

**Source:** Hawaii HRS §235-110.93 (Act 50, 2024); PTE rate confirmed at 9% via legislative history. CG ineligibility confirmed by statute (§235-110.93 applies to "qualified net income" of the entity, not to individual capital gains).

---

## 7. Credit Overlay

**File:** `packages/tax_modeler/src/tax_modeler/scenarios/sb3125_cd1_credits.py`  
**Function:** `compute_credit_overlay(target_year, reec_demand_scenario, ...)`

The REEC, CGEC, and TCRA credit changes are **aggregate static-scoring overlays** — they cannot be attributed to individual PUMS filers because credit claim data only exists at aggregate level from DOTAX reports. They are computed separately and added to the bracket microsimulation result.

### 6a. REEC — Renewable Energy Technologies Income Tax Credit

**DOTAX TY2023 baseline:** $99.6M total claims
- Individual refundable: $13.4M (~23% of $58.3M individual)
- Individual nonrefundable: $44.9M (~77%)
- Corporate refundable: $37.3M (~89% of $41.8M corporate + other)
- Corporate nonrefundable: ~$4.5M (~11%)

**Effective claim share — refundable / nonrefundable split.** Refundable
claims offset state revenue 1:1; nonrefundable claims that exceed
current-year tax liability carry forward and don't immediately reduce
revenue. The ``reec_effective_claim_share`` parameter is the
**nonrefundable utilization rate** (literature midpoint ~0.80); the
aggregate effective share is computed per pool from the refundable mix:

| Pool | Refundable share | Effective at 0.80 nonrefundable | Effective at 1.00 |
|------|------------------|--------------------------------|--------------------|
| Individual | ~23% | ~85% | 100% |
| Corporate + Other | ~89% | ~98% | 100% |

A previous version applied a single ``effective_claim_share`` (e.g.
0.80) to both pools, which understated the dollar value of corporate
REEC by ~15%.

**OBBBA demand impact — residential vs commercial.** OBBBA (PL 119-21,
Jul 2025) terminated **§25D (residential)** but extended **§48E
(commercial)** through 12/31/2027. Hawaii residential and commercial
REEC see different decays:

- *Individual / residential REEC* uses the §25D decay scenario:
  - `pre_obbba` — no demand decay (upper bound)
  - `obbba_mid` — −10% in 2026 (Hawaii-tempered; SEIA national −19% discounted for Hawaii's lease-heavy market), gradual recovery
  - `obbba_severe` — −19% in 2026 (SEIA national figures applied directly), slow recovery
  - `safe_harbor_mid` — OBBBA Mid base + §25D safe-harbor overhang. IRS
    Notice 2013-29 allows filers to lock in §25D at commencement of
    construction (5% safe-harbor payment) with up to 4 years to complete.
    HECO recorded a +37% surge in H2 2025 interconnection applications;
    that backlog completes installation in 2026-2027. Factors: 2026 = 1.05
    (above pre-OBBBA baseline), 2027 = 0.97, reverting to OBBBA Mid from
    2028. Under confirmed Interpretation A retroactivity (SB 3125 §9(1),
    TY beginning after 12/31/2025), this elevated 2026-2027 demand binds
    the $40M cap more tightly → lower pro-rata factor → higher state
    savings. Used in the REEC report's sensitivity band upper bound.
- *Corporate / commercial REEC* uses ``_reec_demand_factor_corporate``
  which returns **1.0** for all forecast years (no §48E impact through
  2027; tapering schedule is downstream of the forecast horizon).

The previous version applied the residential decay factor to both
pools, biasing total revenue gain down.

**Interpretation A (retroactivity, May 2026):** SB 3125 §9(1) confirmed to
apply retroactively to taxable years beginning after 12/31/2025 (TY2026),
with only the §235-12.5(a) AGI-limit amendments deferred to TY2027+. The
model now uses `interpretation="A"` for all scenarios: TY2026 certifications
(filed in CY2027) fall under the $40M cap. AGI limit still does not apply to
TY2026. Net effect: less nonrefundable stock enters the carryforward pool from
TY2026 → lower state RETITC cost in TY2027-2031 → ~+$9M cumulative savings
vs prior Interpretation B baseline.

**Cap impact logic:**
- TY2027–2030: `cap_savings = max(0, projected_baseline − $40M cap)`
- TY2031+: `cap_savings = full projected_baseline` (cap → $0)

**MID scenario REEC savings (pre-fix):** $42M (2027) → $104M (2031),
total ~$307M over 5 years. Post-fix savings will be higher because
corporate baseline no longer decays and corporate aggregate effective
share rises from ~80% to ~98%. Awaiting re-run.

### 6b. CGEC — Capital Goods Excise Tax Credit

**DOTAX TY2023 baseline:** $34.6M (rising trend from $29.3M in 2021)  
**Growth rate (MID: 3%/yr):** Calibrated to Hawaii business investment growth, not statewide nominal GDP.  
**Pull-forward haircut (10%):** Capital goods purchases are partially accelerated into TY2027 before the sunset, reducing the post-2027 base modestly.

**Sunset impact logic:**
- TY2027: $0 (credit still active)
- TY2028+: full projected baseline (all claims go away)

**MID scenario CGEC savings:** ~$29M/yr starting TY2028, total ~$121M over 5 years.

### 6c. TCRA — Tax Credit for Research Activities

One-time acceleration effect modeled for **TY2029 only**:
- MID: ~$8.4M  
- HIGH: ~$8.8M  

---

## 8. Scenario Design

Four integrated scenarios: three behavioral sensitivity scenarios (no recession macro) plus one recession scenario using MID behavioral parameters.

### 8a. Behavioral Scenarios (No Recession Macro)

| Parameter | LOW | **MID (Recommended)** | HIGH |
|-----------|-----|----------------------|------|
| Pareto α (top-income concentration) | 1.7 (lighter tail) | **1.5** | 1.4 (heavier tail) |
| REEC residential demand scenario | obbba_severe | **obbba_mid** | pre_obbba |
| REEC commercial demand factor | 1.0 (no §48E impact) | **1.0** | 1.0 |
| ETI | 0.60 | **0.40** | 0.15 |
| Migration elasticity (share of $1M+ filers per pp) | 0.01 | **0.0025** | 0.001 |
| PTE capture rate | 0 | **0** | 0 |
| Top-income growth premium | +0.3%/yr | **+1.3%/yr** | +2.3%/yr |
| REEC nonrefundable utilization | 65% | **80%** | 100% |
| CGEC annual growth | 2%/yr | **3%/yr** | 4%/yr |
| Corporate AGI limit on REEC | Yes | **No** | No |
| Per-filer effective deduction in compare_systems | Yes | **Yes** | Yes |
| Macro shock | None | **None** | None |

**Calibration anchor:** The model anchors to DOTAX's baseline tax figure for $1M+ filers ($562.3M, the TY2019–2023 window; it was $663M, TY2022, when the following comparison was made). The MID result then ($629.6M) was ~7.5% below the official ~$680M estimate due to two corrections applied after the official score was produced: (1) §235-51(f) CG cap (7.25%) properly applied to synthetic $1M+ filers — reduces the bracket-delta contribution of capital gains income; (2) PTE election pool excludes CG income per statute and economic rationality (9% PTE > 7.25% CG cap) — reduces the PTE offset, which in turn reduces the net bracket gain. LOW reflects maximum plausible behavioral response (strong ETI, 90% PTE shift, severe OBBBA solar decay). HIGH reflects minimal behavioral response and optimistic demand assumptions.

### 8b. Recession Scenario

**Script:** `forecast_sb3125_cd1_enhanced.py` (RECESSION entry in SCENARIOS list)  
**New file:** `packages/tax_modeler/src/tax_modeler/scenarios/macro_scenarios.py`

Models a **mild-to-moderate recession with trough in 2027 and gradual recovery through 2031** — consistent with elevated current recession odds (25–50% 12-month, >50% five-year historically) and empirical recession patterns from 2001 and 2008. Uses MID behavioral parameters; behavioral-macro interaction effects (e.g., higher PTE takeup in recession, slower migration) are not modeled (conservative simplification).

**Methodology — cumulative deviation from baseline.** The shock values are CUMULATIVE deviations from the baseline trajectory at each year, not year-on-year deltas. This correctly models persistent recession damage: by year 2 the income gap is smaller than year 1 (recovery underway), but income is still below trend. The previous year-on-year interpretation incorrectly produced a "rebound year" with income *above* baseline, which is not how recessions recover.

**Macro shock parameters (applied after `project_tax_units_forward()` and top-income premium, before behavioral response):**

| Year | All-filer cumulative gap to baseline | Top-income additional gap (≥$200K) | Total top-income gap |
|------|-------------------------------------:|----------------------------------:|---------------------:|
| 2027 | −2.0% | −1.5% | **−3.5%** |
| 2028 | −1.5% | −1.0% | **−2.5%** |
| 2029 | −1.0% | −0.3% | **−1.3%** |
| 2030 | −0.5% | 0.0% | **−0.5%** |
| 2031 |  0.0% | 0.0% | **0.0%** (full recovery) |

The top-income extra gap captures capital gains realization collapse and pass-through business income cyclicality (historical precedent: 2008–09 saw 40–60% CG declines and 6–12% peak-to-trough top-1% income drops, with full recovery within 4 years).

**Results (May 7, 2026 re-run):** RECESSION and MID are within $1.5M over 5 years; RECESSION is slightly above MID in 2030–2031. ⚠️ *This is unexpected — recession should depress revenue, not raise it. Likely an artifact of COR scaling or income-projection interaction; needs investigation before citing.* **Explained September 29, 2026:** RECESSION's top premium is 1.3%/yr against MID's 1.0% — the May change that lowered MID's premium left RECESSION's at the old value — and the macro shock is 0 by TY2031, so RECESSION outgrows MID in the last years. With MID's premium it falls below MID every year through 2030 and equals it in 2031. Not yet changed in the script.

| Year | MID | RECESSION | Δ vs MID |
|------|----:|----------:|---------:|
| 2027 | $101.2M | $97.5M | −$3.7M |
| 2028 | $122.0M | $120.6M | −$1.4M |
| 2029 | $139.7M | $138.8M | −$0.9M |
| 2030 | $137.2M | $141.0M | +$3.8M |
| 2031 | $173.0M | $176.5M | +$3.5M |
| **5yr** | **$673.0M** | **$674.5M** | **+$1.5M** |

**Why the effect is moderate in absolute terms:** This forecast measures the *delta* between Act 46 and SB 3125 CD1, not absolute state revenue. Both tax systems face the same income shock, so the delta is partially protected — only the marginal rate × marginal income above thresholds differs between them.

1. **Bracket delta** (~$338M of $673M MID): small changes expected in recession as income shocks affect both systems equally
2. **Credit overlay** (~$335M of $673M MID): unchanged — REEC/CGEC claim levels don't depend on individual filer income

**Note on absolute revenue impact:** Absolute state individual income tax revenue would drop substantially more in a recession (likely 5–10% peak-to-trough on a ~$3B base). That is a property of any income tax under any rate schedule, not specific to SB 3125 CD1.

---

## 9. Distributional Analysis

**Script:** `forecast_sb3125_enhanced.py --cd 2` (distributional pass, MID)  
**Output:** `runs/sb3125_cd2_enhanced/quintile.csv` and `bracket.csv` (also `/tmp/sb3125_cd2_*_mid_2027_2031.csv`)

Methodology follows CBO/Tax Policy Center standard distributional analysis:

- **Households ranked by household total cash income** and cut into **fifths of households by the PUMS household weight (WGTP)**, so each fifth holds a fifth of households (not equal income spans); dollar totals sum each tax unit's calibrated filer weight × its change, so the fifths add up to the fiscal totals. (Until September 29, 2026 the fifths were cut on each household's first-unit filer weight and its summed change was multiplied by that one weight; see the dated section at the top.)
- **Static incidence scoring**: per-unit tax is computed at each filer's projected income before ETI/migration adjustments — reflects who bears the statutory burden before behavioral avoidance
- **Bracket change plus attributed credit loss** (September 24, 2026): the individual-return share of REEC and CGEC savings is assigned to imputed claimant households at DOTAX TY2023 claim rates by AGI class (`quintile_analysis.attribute_credit_loss`; see the note at the top of this document). Corporate credit savings and TCRA are not distributed. Pay-more / pay-less shares count only claimants as bearing credit losses.
- **MID scenario only**

Key finding: **Q5 (top 20%, income $102K+) bears more than 100% of the aggregate bracket revenue gain**, with Q1–Q4 receiving modest net benefits from the lower middle rates. The 13% bracket is the dominant force; the middle rate cuts (3.20%→2.50%, 5.50%→5.00%) offset approximately 15% of the Q5 gain.

Note: Q1 filers (avg income ~$3K) are **completely unaffected** because their gross income is below Hawaii's standard deduction + personal exemption threshold — they have zero taxable income and no rate applies.

---

## 10. Results

### Annual Fiscal Impact by Scenario ($M, vs. Act 46 baseline)

*Superseded: the current Act 24 (CD2) results are in the dated sections at
the top, most recently "Pipeline-audit fixes — September 29, 2026". The August 3 CD1 run below predates the
scoring-path fixes, both behavioral corrections and the capital-gains
re-basing.*

**Updated August 3, 2026** — full re-run of `forecast_sb3125_enhanced.py --cd 1` (static credit overlay) on the corrected Hawaii CPI basis (see "Hawaii CPI series correction," July 30, 2026, above) and the wired-through v3 κ calibration. Every number below supersedes the May 7, 2026 run, which was computed on the mislabelled Los Angeles CPI series. PTE shift is still $0 in this run (unresolved — see the Section 5c note; not re-investigated as part of this rerun). Results are post-behavioral (ETI/migration only).

| Tax Year | LOW | **MID** | HIGH | RECESSION |
|----------|----:|--------:|-----:|----------:|
| 2027 | $109.2M | **$121.9M** | $145.6M | $115.7M |
| 2028 | $119.7M | **$139.2M** | $174.6M | $138.7M |
| 2029 | $133.7M | **$157.6M** | $200.9M | $158.1M |
| 2030 | $126.0M | **$156.8M** | $203.8M | $159.0M |
| 2031 | $163.3M | **$191.2M** | $248.0M | $195.1M |
| **5-year total** | **$651.8M** | **$766.7M** | **$972.9M** | **$766.6M** |

*Positive = net revenue gain for the State. Includes bracket microsim + credit overlay + behavioral response.*

The CPI correction raises every scenario's 5-year total by roughly $90-105M (MID: $673.0M → $766.7M, +13.9%) — the corrected Hawaii nominal-income path runs hotter than the Los Angeles path it replaced, which raises both the bracket base and the REEC demand baseline (REEC scales with nominal income; see Section 6a).

**RECESSION scenario note (unchanged from the prior run):** See Section 8b. ⚠️ RECESSION is still slightly above MID in 2030–2031 ($159.0M vs $156.8M in 2030; $195.1M vs $191.2M in 2031) — the same unexpected ordering flagged in the May 7 run persists after the CPI fix, confirming it is not a CPI-series artifact. Cause found September 29, 2026 (RECESSION's 1.3%/yr top premium vs MID's 1.0%; see Section 8b).

### MID Scenario Decomposition

| Channel | 5-year Total |
|---------|-------------:|
| Static bracket gain (13% top bracket + middle cuts) | +$498.9M |
| ETI / migration behavioral offset | −$79.1M |
| PTE election shift | $0 ⚠️ |
| **Post-behavioral bracket delta** | **+$419.8M** |
| REEC savings (static cap overlay) | +$248.5M |
| CGEC savings (sunset) | +$90.5M |
| TCRA savings (acceleration) | +$7.9M |
| **Credit overlay total** | **+$346.9M** |
| **Total MID** | **+$766.7M** |

*⚠️ PTE shift is still $0 — carried over from the May 7 run, unresolved (see Section 5c). Static bracket gain rose $87.3M and REEC savings rose $12.2M vs. the May 7 run, both driven by the corrected (hotter) Hawaii nominal-income path; CGEC and TCRA are unchanged (their growth path — Section 6b/6c — is a separate 3%/yr assumption, not derived from `_hawaii_nominal_growth`).*

Year-by-year (MID, $M):

| Tax Year | Static bracket | ETI/migration | Post-behav. bracket | REEC | CGEC | TCRA | Credit total | **Total** |
|----------|---------------:|---------------:|---------------------:|-----:|-----:|-----:|-------------:|----------:|
| 2027 | 81.6 | −3.2 | 78.3 | 43.5 | 0.0 | 0.0 | 43.5 | **121.9** |
| 2028 | 87.0 | −9.2 | 77.9 | 40.8 | 20.6 | 0.0 | 61.3 | **139.2** |
| 2029 | 101.1 | −15.9 | 85.2 | 42.4 | 22.1 | 7.9 | 72.4 | **157.6** |
| 2030 | 111.0 | −22.6 | 88.5 | 44.7 | 23.7 | 0.0 | 68.4 | **156.8** |
| 2031 | 118.2 | −28.3 | 89.9 | 77.2 | 24.1 | 0.0 | 101.3 | **191.2** |

### Distributional Impact — TY 2027 (MID)

**Updated September 25, 2026** — CD2 (enacted Act 24), after the
scoring-path fixes (top of this document): top filers keep their itemized
deductions, per-household tax is net of credits, and every exemption counts.
Credit losses are attributed to imputed claimants (Section 9). Supersedes the
August 3 table, which was CD1's static credit overlay spread evenly across
every filer in each AGI bin; its pay-more / pay-less shares (e.g. 85.1% of Q1
"paying more") were an artifact of that spread.

| Quintile | Households | Bracket Δ ($M) | Credit loss ($M) | Total Δ ($M) | Avg/HH bracket | Avg/HH credit loss | Avg/HH total | % claimants | % pay more | % pay less |
|----------|-----------:|---------------:|-----------------:|-------------:|---------------:|-------------------:|-------------:|------------:|-----------:|-----------:|
| Q1 (bottom 20%) | 81,005 | −$0.4M | +$1.1M | +$0.7M | −$5 | +$14 | +$9 | 0.9% | 1.0% | 17.7% |
| Q2 | 89,419 | −$4.0M | +$1.3M | −$2.6M | −$44 | +$15 | −$29 | 1.5% | 2.5% | 79.2% |
| Q3 | 100,606 | −$7.6M | +$3.0M | −$4.7M | −$76 | +$29 | −$46 | 2.5% | 2.5% | 96.2% |
| Q4 | 106,687 | −$9.6M | +$5.4M | −$4.3M | −$90 | +$50 | −$40 | 3.6% | 3.6% | 96.4% |
| Q5 (top 20%) | 116,330 | +$83.5M | +$16.3M | +$99.8M | +$717 | +$141 | +$858 | 6.1% | 21.5% | 78.5% |

**TY 2031 (MID)** — no new REEC certifications after TY2029, so every would-be
claimant loses the full credit:

| Quintile | Households | Bracket Δ ($M) | Credit loss ($M) | Total Δ ($M) | Avg/HH bracket | Avg/HH credit loss | Avg/HH total | % claimants | % pay more | % pay less |
|----------|-----------:|---------------:|-----------------:|-------------:|---------------:|-------------------:|-------------:|------------:|-----------:|-----------:|
| Q1 (bottom 20%) | 81,005 | −$0.2M | +$3.1M | +$2.9M | −$3 | +$39 | +$36 | 0.9% | 0.9% | 9.5% |
| Q2 | 89,419 | −$4.0M | +$4.1M | +$0.1M | −$44 | +$45 | +$1 | 1.6% | 1.6% | 66.5% |
| Q3 | 100,606 | −$10.9M | +$10.2M | −$0.7M | −$108 | +$101 | −$7 | 2.8% | 2.8% | 94.7% |
| Q4 | 106,687 | −$14.5M | +$17.2M | +$2.7M | −$136 | +$161 | +$25 | 3.9% | 3.9% | 96.0% |
| Q5 (top 20%) | 116,330 | +$124.2M | +$41.9M | +$166.0M | +$1,067 | +$360 | +$1,427 | 6.5% | 25.3% | 74.7% |

*Negative Δ = household pays less. "Credit loss" is the individual-return REEC/CGEC savings attributed to households, in expectation (claim probability × loss if claiming); "% claimants" is the share of households imputed to claim REEC or CGEC. Static incidence: before ETI/migration response. Household counts use PUMS WGTP; $M totals use the calibrated filer weight. (Superseded September 29, 2026: until then the fifths were cut on each household's first-unit filer weight and totals multiplied a household's summed change by that one weight; see the dated section at the top.)*

---

### SB 3125 CD2 Results — August 3, 2026 (post CPI-correction rerun)

> **Superseded** by the September 24, 2026 sections at the top of this document (current MID: $870.1M).

Full re-run of `forecast_sb3125_enhanced.py --cd 2` (vintage carryforward
model) on the corrected Hawaii CPI basis. Supersedes the May 14, 2026
table below, which was computed on the mislabelled Los Angeles CPI series.

**CD2 vs Act 46 baseline, post-behavioral, post-Round-2 ($M):**

| Tax Year | LOW | **MID** | HIGH | RECESSION |
|----------|----:|--------:|-----:|----------:|
| 2027 | $125.0M | **$127.5M** | $149.0M | $121.3M |
| 2028 | $139.6M | **$152.6M** | $184.6M | $152.0M |
| 2029 | $155.9M | **$174.0M** | $212.4M | $174.5M |
| 2030 | $179.8M | **$208.6M** | $252.3M | $210.8M |
| 2031 | $188.6M | **$214.4M** | $268.9M | $218.3M |
| **5-year total** | **$788.9M** | **$877.0M** | **$1,067.2M** | **$876.9M** |

MID rises $95.1M vs. the May 14 run ($781.9M → $877.0M, +12.2%) — the
same corrected-CPI mechanism as the CD1 rerun above (hotter Hawaii
nominal-income path → higher REEC baseline), amplified here because the
vintage carryforward model's REEC savings ($358.9M 5yr, vs. CD1's static
$248.5M) scale more directly with the nominal-income-driven baseline.

**MID scenario, vintage-model REEC diagnostics by year:**

| Tax Year | REEC savings | CGEC savings | Credit total | Bracket (post-behav.) | **Total** |
|----------|-------------:|-------------:|-------------:|------------------------:|----------:|
| 2027 | $49.2M | $0.0M | $49.2M | $78.3M | **$127.5M** |
| 2028 | $54.1M | $20.6M | $74.7M | $77.9M | **$152.6M** |
| 2029 | $58.8M | $22.1M | $88.8M | $85.2M | **$174.0M** |
| 2030 | $96.4M | $23.7M | $120.1M | $88.5M | **$208.6M** |
| 2031 | $100.4M | $24.1M | $124.5M | $89.9M | **$214.4M** |

The TY2030 jump (REEC savings $58.8M → $96.4M) is the same
§235-12.5(p) sunset driver documented in the May 14 finding below — it
is a discrete step in the vintage simulation's cap/sunset logic, not a
CPI artifact, and persists after the correction.

**Credit savings breakdown, MID scenario ($M) — legacy static overlay (CD1) vs. vintage carryforward (CD2):**

| Year | Legacy static (CD1) | Vintage model (CD2) | Δ |
|------|---------------------:|----------------------:|--:|
| 2027 | $43.5M | $49.2M | +$5.6M |
| 2028 | $61.3M | $74.7M | +$13.3M |
| 2029 | $72.4M | $88.8M | +$16.4M |
| 2030 | $68.4M | $120.1M | +$51.8M |
| 2031 | $101.3M | $124.5M | +$23.3M |

*Bracket-change impact (identical across CD1/CD2 — see the May 11, 2026
finding below) is unaffected by which REEC model is used; only the
credit-overlay column differs.*

---

### SB 3125 CD2 Results — May 14, 2026 (vintage carryforward correction)

**Superseded by the August 3, 2026 rerun above** (same methodology, corrected
CPI basis). Retained for historical reference.

**Key finding (revised May 14):** The previous static credit overlay
understated REEC savings by ~$107M over 5 years because it (a) treated
TY2030 as a $40M cap year rather than applying the §235-12.5(p) sunset,
and (b) did not track pre-existing carryforward stock drawdown. The
vintage simulation corrects both. Numbers in the tables below reflect the
new vintage-pool model.

**CD2 vs Act 46 baseline, post-behavioral, post-Round-2 ($M):**

| Tax Year | LOW | **MID** | HIGH | RECESSION |
|----------|----:|--------:|-----:|----------:|
| 2027 | $108.8M | **$108.3M** | $129.1M | $102.7M |
| 2028 | $122.3M | **$135.2M** | $164.7M | $133.3M |
| 2029 | $137.0M | **$155.8M** | $191.8M | $153.2M |
| 2030 | $164.2M | **$187.9M** | $231.2M | $192.1M |
| 2031 | $171.5M | **$194.7M** | $244.7M | $198.2M |
| **5-year total** | **$703.7M** | **$781.9M** | **$961.4M** | **$779.5M** |

*LOW uses interpretation A (TY2026 cap binds) + pro-rata η=0.5; other scenarios
use interpretation B + scenario-band η. All scenarios use DOTAX
TY2018-2022 actuals, dynamic AGI eligibility, and dynamic refundable share.*

**Pre-Round-2 results (May 14 vintage carryforward only, for comparison):**

| Tax Year | LOW | **MID** | HIGH | RECESSION |
|----------|----:|--------:|-----:|----------:|
| 2027 | $103.8M | **$108.8M** | $129.4M | $103.2M |
| 2028 | $119.8M | **$135.4M** | $164.7M | $133.5M |
| 2029 | $135.8M | **$155.9M** | $191.8M | $153.3M |
| 2030 | $162.3M | **$187.3M** | $230.8M | $191.4M |
| 2031 | $170.6M | **$194.4M** | $244.6M | $198.0M |
| **5-year total** | **$692.4M** | **$781.8M** | **$961.3M** | **$779.4M** |

**Credit savings breakdown, MID scenario ($M) — illustrating vintage carryforward correction:**

*"Vintage model" column is pre-Round-2; Round-2 final MID numbers are in the table above ($47.3M, $72.0M, $85.5M, $116.5M, $120.6M).*

| Year | Legacy static | Vintage model (pre-R2) | TY2030 driver |
|------|--------------:|-----------------------:|---------------|
| 2027 | $41.8M | $47.9M | Pre-2027 stock + ineligibles |
| 2028 | $59.2M | $72.2M | Higher baseline (steady-state cost) |
| 2029 | $69.8M | $85.6M | Higher baseline |
| 2030 | $65.5M | **$115.8M** | **§235-12.5(p) sunset (was treated as $40M cap)** |
| 2031 | $98.3M | $120.4M | Includes 2027–2029 vintage drawdown |

---

### SB 3125 CD2 Results — May 11, 2026 (legacy static overlay, pre-correction)

**Note:** Superseded by May 14 vintage-corrected results above. Retained for
historical reference.

**Key finding:** The bracket values loaded under the `sb3125_cd1` label in the CSV already reflected CD2-vintage numbers (2.50%/5.00% mid rates, 13% at $1M+ MFJ). Therefore, the CD2 forecast produces **identical fiscal-impact numbers to the CD1 forecast above**. The difference is in labeling and authoritative tag going forward.

**CD2 vs Act 46 baseline, all scenarios, final post-behavioral numbers ($M):**

| Tax Year | LOW | **MID** | HIGH | RECESSION |
|----------|----:|--------:|-----:|----------:|
| 2027 | $93.4M | **$102.8M** | $125.7M | $97.2M |
| 2028 | $103.0M | **$122.4M** | $155.0M | $120.4M |
| 2029 | $115.6M | **$140.1M** | $180.8M | $137.5M |
| 2030 | $111.3M | **$136.9M** | $183.2M | $141.1M |
| 2031 | $147.4M | **$172.4M** | $224.4M | $175.9M |
| **5-year total** | **$570.7M** | **$674.5M** | **$869.2M** | **$672.1M** |

**CD2 sensitivity (static scoring, no behavioral response, $M) — updated August 3, 2026:**

Re-run of `forecast_sb3125_sensitivity.py --cd 2`, which always uses the
static (non-vintage) credit overlay regardless of `--cd` — this table
measures Pareto-α × REEC-scenario sensitivity in isolation, not the
vintage carryforward model. Supersedes the values below it.

| Tax Year | LOW | **MID** | HIGH |
|----------|----:|--------:|-----:|
| 2027 | $79.3M | **$88.1M** | $108.5M |
| 2028 | $111.7M | **$121.5M** | $141.4M |
| 2029 | $136.4M | **$144.5M** | $163.8M |
| 2030 | $141.8M | **$148.6M** | $166.3M |
| 2031 | $192.3M | **$191.1M** | $213.6M |
| **5-year total** | **$661.5M** | **$693.8M** | **$793.6M** |

*Superseded (pre-CPI-correction) values, retained for reference:*

| Tax Year | LOW | **MID** | HIGH |
|----------|----:|--------:|-----:|
| 2027 | $79.3M | **$88.0M** | $107.9M |
| 2028 | $114.1M | **$123.8M** | $143.1M |
| 2029 | $139.0M | **$147.0M** | $165.8M |
| 2030 | $141.8M | **$148.5M** | $165.8M |
| 2031 | $193.0M | **$192.3M** | $214.0M |
| **5-year total** | **$667.2M** | **$699.6M** | **$796.7M** |

*The near-unchanged totals (5yr MID $699.6M → $693.8M, essentially flat)
confirm this table's methodology is insensitive to the CPI correction —
expected, since REEC demand here scales off a fixed 3%/yr backcast growth
rate for out-of-range years and the scenario-level REEC baselines rather
than `_hawaii_nominal_growth` directly; the modest per-year shifts are
noise from the underlying microsim rerun, not a CPI effect.*

**CD2 vs FY2026-frozen baseline (ITEP-comparable, $M) — updated August 3, 2026** *(superseded; see the September 29, 2026 section at the top)*:

*Answers: "What does CD2 cost vs if nothing had been enacted after 2026?" Negative = revenue lost. Re-run of `forecast_sb3125_vs_fy26base.py --cd 2` on the corrected CPI basis.*

| Tax Year | Bracket effect | SD expansion | **Total** | ITEP estimate | Gap |
|----------|---------------:|-------------:|----------:|---------------:|-----:|
| 2027 | −$160.2M | −$31.7M | **−$191.9M** | −$227.0M | +$35.1M |
| 2028 | −$148.9M | −$65.6M | **−$214.5M** | −$258.0M | +$43.5M |
| 2029 | −$372.0M | −$82.3M | **−$454.3M** | −$534.0M | +$79.7M |
| 2030 | −$360.2M | −$119.3M | **−$479.5M** | −$563.0M | +$83.5M |
| 2031 | −$336.7M | −$177.6M | **−$514.3M** | −$622.0M | +$107.7M |
| **5-year Σ** | **−$1,378.1M** | **−$476.4M** | **−$1,854.5M** | **−$2,204.0M** | **+$349.5M** |

Our microsim now runs ~15.9% below ITEP on the vs-frozen baseline (was
~12% pre-correction) — the corrected, hotter Hawaii nominal-income path
narrows our bracket-effect losses, widening the gap to ITEP's estimate.
Likely sources unchanged from the prior note: ITEP includes non-residents
and PTE pass-through attribution not in the microsim; their dynamic
inflation assumptions may also differ over the 5-year window — the CPI
correction narrows one plausible source of the original gap (our CPI
assumption running cooler than reality) while the gap widening suggests
the remaining sources (PTE, non-resident) are doing more of the work
than previously apparent.

*Superseded (pre-CPI-correction) values, retained for reference:*

| Tax Year | Bracket effect | SD expansion | Total | ITEP estimate | Gap |
|----------|---------------:|-------------:|------:|---------------:|-----:|
| 2027 | −$171.4M | −$32.4M | −$203.7M | −$227.0M | +$23.3M |
| 2028 | −$161.6M | −$67.2M | −$228.9M | −$258.0M | +$29.1M |
| 2029 | −$387.6M | −$85.3M | −$472.9M | −$534.0M | +$61.1M |
| 2030 | −$375.5M | −$123.1M | −$498.6M | −$563.0M | +$64.4M |
| 2031 | −$353.6M | −$182.9M | −$536.5M | −$622.0M | +$85.5M |
| **5-year Σ** | **−$1,449.7M** | **−$491.0M** | **−$1,940.6M** | **−$2,204.0M** | **+$263.4M** |

**MID scenario quintile breakdown, TY 2027, CD2 vs Act 46 — updated August 3, 2026:**

The current pipeline's distributional module produces **identical**
household-based quintile figures for CD1 and CD2 (bracket structure is
shared between the two conference drafts; only the credit-overlay model
differs, and this table is bracket-only). See the CD1 "Distributional
Impact" table in Section 10 above for the updated household-based
breakdown with the same figures — the filer-based, income-range-labelled
table below is retained as-is because the current pipeline does not
reproduce those income-range boundaries (see the Section 10 note); it is
not being actively re-derived.

| Quintile | Income Range | Avg Income | N filers | Bracket Delta | Avg $/filer |
|----------|---------------|-----------|----------|--------------|-------------|
| Q1 (Bottom 20%) | −$18.6K – $11.2K | $2,961 | 124,948 | $0.0M | $0 |
| Q2 | $11.2K – $31.3K | $19,834 | 124,505 | −$0.8M | −$6 |
| Q3 | $31.3K – $56.0K | $42,628 | 125,514 | −$5.5M | −$44 |
| Q4 | $56.0K – $103.5K | $76,346 | 125,006 | −$10.5M | −$84 |
| Q5 (Top 20%) | $103.5K – $211.6M | $253,959 | 124,994 | **+$82.7M** | **+$662** |

*Not re-derived this rerun — retained from the May 11 run pending a
quintile script re-run with income-range output.*

### Baseline Validation

**Updated October 5, 2026** (COR vintage of September 3; model after the
September 29–30 fixes). The MID Act 46 baseline, which now includes the
calibrated and aged $1M+ tail, against COR's individual income tax line ($M;
TY n = FY n+1):

| TY | Model | COR | Gap | % below COR |
|---|---:|---:|---:|---:|
| 2027 | 2,522.9 | 3,071.0 | 548.0 | 17.8% |
| 2028 | 2,689.1 | 3,086.7 | 397.6 | 12.9% |
| 2029 | 2,552.0 | 3,002.7 | 450.8 | 15.0% |
| 2030 | 2,664.9 | 3,138.4 | 473.5 | 15.1% |
| 2031 | 2,744.8 | 3,252.1 | 507.2 | 15.6% |

The gap is 13–18% with no trend, the coverage difference described below.
The August 19 figures that follow (about $2.24B against $2.87B, 22%) are
superseded: both sides moved, the model baseline with the tail fixes and COR
with its September 3 revision (+6.8% at TY2027).

**Updated August 19, 2026** (COR vintage refresh): the microsim TY2027 Act 46
baseline is approximately $2.24B against COR's **$2.87B** projection for TY2027
— a gap of roughly **$634M (22%)**.

*Two corrections to the prior text, which read "roughly $810M (27%) below the
COR's $3.05B FY2027 projection."* First, the $3.05B came from COR's **September
2025** forecast; the current bundled vintage is **May 21, 2026** (auto-refreshed
— see the COR section above), where the comparable figure is $2,874.134M.
Second, that sentence compared a **TY**-labelled microsim baseline against an
**FY**-labelled COR column. Under this repo's stated `FY(n+1) = TY(n)`
convention, TY2027 maps to **FY2028**, which is the $2,874.134M used here. The
gap is **expected and not a calibration error**:

- Pass-through entity (PTE) tax revenue (~$124M+): taxed at entity level, not on individual returns
- Non-resident withholding: captured in DOTAX but not in PUMS-based microsim
- Audit, penalty, and late-filing assessments
- Rounding and timing differences between tax year (TY) and fiscal year (FY)

The **bracket delta** (SB 3125 CD1 minus Act 46) is robust to this level-shift — both systems are subject to the same coverage gaps, so the difference cancels them out.

---

## 11. Caveats and Limitations

1. **PUMS income underreporting at the top.** The ACS PUMS understates income for very high earners even after Pareto synthesis. The Pareto approximation underweights income concentration above ~$10M. The raw synthesis recovers only ~65–80% of the IRS SOI $663M tax target when the §235-51(f) CG cap is correctly applied (lower than the pre-cap estimate because CG income is taxed at 7.25% rather than bracket rates, reducing simulated baseline tax). The gap is closed by the post-synthesis uniform tail scaling step (Step 6a), which brings the tax target ratio to 100.0% (tail_k: LOW=1.774, MID=1.421, HIGH=1.234), then ages the tail from the TY2022 target to 2024 dollars before projection (Step 6b).

2. **Static credit overlay.** REEC and CGEC are scored as aggregate static overlays. The model does not simulate individual solar adoption behavior or capital investment timing at the filer level.

3. **No general equilibrium effects.** The model does not simulate wage effects, employment changes, or business investment responses beyond the three modeled behavioral channels.

4. **PTE capture rate uncertainty.** The 4pp arbitrage (13% individual vs. 9% PTE) creates a strong incentive for restructuring. The MID scenario's 70% capture rate is a judgment call — actual takeup depends on legal/accounting costs and awareness. The pass-through share (0.20) is now calibrated to Hawaii IRS SOI 2022 data ($200K+ filers: 12.6% of total income as partnership/S-corp income, ~15% of ordinary income), replacing the prior national top-1% figure of 0.40. Remaining uncertainty: the IRS SOI does not break out $1M+ filers separately; the $1M+ sub-population may have a modestly higher pass-through share than the full $200K+ bracket.

5. **OBBBA demand uncertainty.** Federal repeal of Section 25D in 2025 creates genuine uncertainty for Hawaii solar demand 2026–2031. The three REEC scenarios bracket the plausible range from SEIA forecasts.

6. **No tax avoidance timing effects.** High-income filers may accelerate income recognition into 2026 (before the bill takes effect) or defer it to 2028 if they anticipate the 2029 bracket change. These timing effects are not modeled.

7. **Behavioral response is phased in linearly.** Migration and PTE election are assumed to phase in gradually. In practice, some response may occur immediately (legal restructuring) while other responses may be permanent (migration).

---

## 10a. Margin of error on the poverty-impact pipeline (added May 2026)

> **September 29, 2026:** projected-year SPM runs of this pipeline
> (`poverty_impact_report.py` for tax years other than 2024,
> `forecast_hi_eitc_revert_20.py`, `forecast_working_family_credits.py`) now
> age money income with the tax inputs (`spm_money_income`) instead of
> reading 2024-dollar `total_cash_income` against target-year taxes and
> credits; see METHODOLOGY.md, *Projected-year SPM poverty*. Act 24 figures
> do not move: `total_cash_income` itself is unchanged.

The poverty-impact tables emitted by `scripts/poverty_impact_report.py`
(separate from the SB 3125 fiscal-impact tables in Section 10) now
carry two new families of uncertainty columns when invoked with the
appropriate flags:

* `<col>_se` — PUMS sampling SE via 80-replicate Successive Difference
  Replication. Activated by `--replicate-weights`.
* `<col>_param_min` / `<col>_param_max` / `<col>_param_median` —
  one-at-a-time parameter sweep across the four behavioral-uncertainty
  parameters (take-up rates + forward-projection α elasticity).
  Activated by merging `scripts/poverty_impact_sweep.py` output via
  `--merge-sweep`.

The two bands are reported **separately, not combined in quadrature**,
so reviewers can see whether uncertainty is dominated by sampling or by
policy assumptions. See `METHODOLOGY.md` § *Margin of error on the
poverty-impact pipeline* for the SDR formula, the empirical anchors
behind each sweep band, and the rationale for not collapsing the two
sources into one headline interval.

### Narrowed parameter band on HI CTC (2026-Q2)

A first-cut sweep produced a ±37 % parameter range on
`persons_lifted_hi_ctc_650`, too wide to be useful for stakeholders.
Decomposing the range showed two distinct drivers:

* `hi_ctc_per_child` band ($300 → $1000) → ±38 % contribution.
  This is a **policy-design counterfactual** ("what if the bill had
  said $300?"), not behavioral uncertainty given the bill as drafted.
* `hi_ctc_takeup` band (0.60 → 0.95, literature pool) → ±22 %.
  Wide because it conflated first-year and steady-state estimates.

The per-child axis was moved out of the sweep entirely and into the
named scenario set: the default scenario menu now ships
`hi_ctc_300`, `hi_ctc_650`, and `hi_ctc_1000` side-by-side, each with
its own SE and parameter range. The take-up band was replaced with a
Hawaii-empirical anchor: observed federal-EITC take-up in HI 2022 =
84,010 admin claims ÷ ~120,535 PUMS-eligible filers ≈ **0.70** with
±5 pp judgment band. The estimator lives at
`tax_modeler.calibration.hi_eitc_takeup_estimate`.

**Result on the headline cell:** parameter range on
`persons_lifted_hi_ctc_650` tightens from ±37 % → **~±10 %**. Quotable:
> EITC lifts ~19,000 persons in Hawaiʻi (SDR 90 % CI ±3,200; parameter
> range ±10 %). A $650/child state CTC would lift an additional ~5,260
> (SDR ±1,640; parameter range ~4,700–5,800). A $1,000/child variant
> would lift ~8,000.

The headline SB 3125 fiscal-impact numbers in Section 10 are unaffected
by this change — only the auxiliary poverty-impact tables ship the new
columns.

---

## 12. Scripts and File Map

### Forecast Scripts (repo root)

#### SB 3125 CD1

| Script | Purpose | Output |
|--------|---------|--------|
| `forecast_sb3125_cd1.py` | Original forecast with decile snapshot | `/tmp/sb3125_cd1_fiscal_impact_2027_2031.csv` |
| `forecast_sb3125_cd1_sensitivity.py` | Sensitivity across Pareto α × REEC scenarios (pre-behavioral) | `/tmp/sb3125_cd1_sensitivity_2027_2031.csv` |
| `forecast_sb3125_cd1_enhanced.py` | **Primary forecast** — 3 behavioral scenarios + RECESSION macro scenario | `/tmp/sb3125_cd1_enhanced_2027_2031.csv` |
| `forecast_sb3125_cd1_quintile.py` | Distributional analysis by income quintile (MID) | `/tmp/sb3125_cd1_quintile_2027_2031.csv` |

#### SB 3125 CD2 (May 2026)

| Script | Purpose | Output |
|--------|---------|--------|
| `forecast_sb3125_cd2.py` | Static forecast vs Act 46 baseline | `/tmp/sb3125_cd2_fiscal_impact_2027_2031.csv` |
| `forecast_sb3125_cd2_sensitivity.py` | Sensitivity across Pareto α × REEC scenarios (static, no behavioral) | `/tmp/sb3125_cd2_sensitivity_2027_2031.csv` |
| `forecast_sb3125_cd2_enhanced.py` | **Primary CD2 forecast** — 4 scenarios (LOW/MID/HIGH/RECESSION) with behavioral response | `/tmp/sb3125_cd2_enhanced_2027_2031.csv` |
| `forecast_sb3125_cd2_quintile.py` | Distributional quintile analysis (bracket only, all 5 years) | `/tmp/sb3125_cd2_quintile_2027_2031.csv` |
| `forecast_sb3125_vs_fy26base.py --cd 2` | ITEP-comparable: CD2 vs FY2026-frozen baseline | `runs/sb3125_cd2_fy26base/` |

#### Baseline reconciliation

| Script | Purpose | Output |
|--------|---------|--------|
| `forecast_act24_vs_pre_act46.py` | Act 24 vs **pre-Act-46 (2017) law** — the frame comparable to ITEP's ~$1.4B/yr Act 46 figure. Decomposes into Act 46 banked ≤2026 / Act 46 remaining / Act 24 increment, and carries a tie-out column (`tieout_act24_page`) checking that column C equals the Act 24 page's static bracket change. | `runs/act24_vs_pre_act46/decomposition.csv` |

#### DOTAX scorecard

| Script | Purpose | Output |
|--------|---------|--------|
| `scripts/dotax_scorecard.py --base-edition-year N` | Scores the calibrated base raked to DOTAX edition N (`scripts/build_calibrated_base.py`, or the artifact `forecast_sb3125_enhanced.py` writes) against edition N+1: returns, AGI and tax before credits by AGI class, filing status, the $1M+ class with its count / per-filer split, and the growth the dials implied. The TY2022-anchored model (`48761b8`) against TY2023 is `DOTAX_SCORECARD.md`: $1M+ tax +73% (MID), everything below $1M −3.5%. | `reports/dotax_scorecard/TY<N+1>/` |

### Key Package Files

| File | Purpose |
|------|---------|
| `packages/tax_modeler/src/tax_modeler/loaders/pums_loader.py` | PUMS CSV loader |
| `packages/tax_modeler/src/tax_modeler/units/constructor.py` | Tax unit construction from person/household records |
| `packages/tax_modeler/src/tax_modeler/pipeline.py` | Pipeline orchestration (`_enrich_for_credits`, `_compute_base_tax`, `_calibrate`) |
| `packages/tax_modeler/src/tax_modeler/calibration.py` | IPF rake calibration |
| `packages/tax_modeler/src/tax_modeler/config/tax_system_config.py` | `TaxCalculator`, `TaxSystemConfig`, `TaxSystemRegistry`, `compare_systems()` |
| `packages/tax_modeler/src/tax_modeler/data/raw/hawaii_tax_brackets_master_all.csv` | All bracket schedules (Act 46 and SB 3125 CD1) |
| `packages/tax_modeler/src/tax_modeler/projection/tax_unit_projector.py` | County-level income projection |
| `packages/tax_modeler/src/tax_modeler/scenarios/top_income_synthesis.py` | Pareto $1M+ filer synthesis |
| `packages/tax_modeler/src/tax_modeler/scenarios/behavioral_response.py` | ETI, migration, PTE election, itemized adjustment |
| `packages/tax_modeler/src/tax_modeler/scenarios/macro_scenarios.py` | Macro recession shock (`apply_macro_recession_shock`) |
| `packages/tax_modeler/src/tax_modeler/scenarios/sb3125_cd1_credits.py` | REEC/CGEC/TCRA credit overlay |

### Output Files

#### CD1 Outputs

| File | Description |
|------|-------------|
| `/tmp/sb3125_cd1_fiscal_impact_2027_2031.csv` | Static base forecast vs Act 46 |
| `/tmp/sb3125_cd1_enhanced_2027_2031.csv` | Final calibrated forecast (all 4 scenarios, post-behavioral) |
| `/tmp/sb3125_cd1_quintile_2027_2031.csv` | Quintile distributional results (MID, all 5 years) |
| `/tmp/sb3125_cd1_sensitivity_2027_2031.csv` | Sensitivity range (LOW/MID/HIGH, static scoring) |

#### CD2 Outputs (May 11, 2026)

| File | Description |
|------|-------------|
| `/tmp/sb3125_cd2_fiscal_impact_2027_2031.csv` | Static base forecast vs Act 46 |
| `/tmp/sb3125_cd2_enhanced_2027_2031.csv` | Enhanced forecast (all 4 scenarios, post-behavioral) |
| `/tmp/sb3125_cd2_quintile_2027_2031.csv` | Per-unit quintile analysis (bracket only, TY2027–2031) |
| `/tmp/sb3125_cd2_sensitivity_2027_2031.csv` | Sensitivity range (LOW/MID/HIGH, static scoring) |
| `/tmp/cd2_vs_fy26base_bracket_mid_2027_2031.csv` | ITEP-comparable bracket-only vs frozen baseline |
| `/tmp/cd2_vs_fy26base_quintile_mid_2027_2031.csv` | ITEP-comparable quintile breakdown |
| `/tmp/sb3125_cd2_decile_TY2027.csv` | Decile snapshot from base static forecast |

#### Cache

| File | Description |
|------|-------------|
| `/tmp/tax_units_cache.parquet` | Calibrated base-year tax units (pre-synthesis); rebuilds in ~3 min if deleted |
| `/tmp/sb3125_calibrated_base.pkl` | Calibrated units saved after top-income synthesis (used by enhanced scripts for state-level analysis) |

---

## 13. Software and Packages

### Language and Runtime
- **Python 3.12** via `uv` (Astral) package manager
- All scripts run from the repo root as: `uv run python <script>.py`

### External Python Libraries

| Library | Version | Use |
|---------|---------|-----|
| `pandas` | ≥2.0 | Data manipulation, groupby aggregation, CSV I/O |
| `numpy` | ≥1.24 | Numerical computations, Pareto distribution draws |
| `pyarrow` | ≥14.0 | Parquet cache I/O |
| `scipy` | ≥1.11 | Statistical distributions (Pareto synthesis) |

### Internal Packages (this repo)

| Package | Path | Role |
|---------|------|------|
| `tax_modeler` | `packages/tax_modeler/src/` | Core tax calculation, bracket schedules, pipeline, projection, scenarios |
| `census_forecaster` | `packages/census_forecaster/src/` | ACS B19013 ensemble projector for county income growth |
| `pums_estimator` | `packages/pums_estimator/src/` | PUMS control totals and crosswalk utilities |
| `common` | `packages/common/src/` | Shared utilities (logging, config, type helpers) |

### Data Storage
- **PUMS source data:** `/Users/dtomkatsu/ctc-and-eitc/data/raw/pums/` (shared with ctc-and-eitc repo)
- **Tax unit cache:** `/tmp/tax_units_cache.parquet`
- **Forecast outputs:** `/tmp/sb3125_cd1_*.csv`
