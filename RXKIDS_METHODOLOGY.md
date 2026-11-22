# RxKids Hawaiʻi — Methodology

> Companion documentation for `tax_modeler.programs.rxkids_hi`. Documents
> sourcing, parameter calibration, eligibility approximations, and
> known limitations. Required for advocacy use.
>
> **New to this document?** Start with §0 (Plain-language overview). It
> explains, in everyday terms, what the program is, how we count births, and
> where every number comes from. The later sections (§1–§10) are the detailed
> technical reference.

## 0. Plain-language overview

### What RxKids is

RxKids gives cash to families around the time a baby is born — no strings
attached. A pregnant person gets a one-time **$1,500** payment, and after the
baby arrives the family gets **$500 a month for 6 months** ($3,000). It started
in Flint, Michigan in 2024 and has spread to 35+ Michigan communities. This
document models what a similar program would cost and accomplish **in Hawaiʻi**.

### Who would qualify (in this model)

A family qualifies if **either** of these is true:

1. **They already qualify for Hawaiʻi Medicaid (Med-QUEST)**, or
2. **Their income is at or below 300% of the federal poverty line** (about
   $85,000 for a family of three).

This is broader than a Medicaid-only program but narrower than giving cash to
everyone. On the real Hawaiʻi data, roughly **58% of all birth families** clear
this bar (~7,969 of ~13,842 projected 2028 births). **As of 2026-08-18 the
model computes the Medicaid-vs-income split directly from the PUMS microdata
(earlier versions of this document estimated it from an external, national
statistic — see §10) — and at the statutory design, 100% of that eligibility
currently comes from families who already qualify for Medicaid.** The 300%
FPL income test adds **no additional** eligible births on top of what
Medicaid already reaches, because Hawaiʻi's Medicaid children/CHIP pathway
(313% FPL) is wider than the program's own 300% FPL test, and every birth
family has a qualifying dependent. See the real, per-scenario split in §10.

We also price two **universal** designs (cash to every birth family, no income
or Medicaid test) — one paying through 6 months and one through 12 — so the cost
of going universal is on the table next to the statutory design. See the
scenario comparison in §0 (bottom line) and §10.

### How we estimate the cost — the short version

1. **Count the babies.** We find every newborn (a baby under age 1) in the
   Census microdata for Hawaiʻi, then check which of those families would
   qualify under the rules above.
2. **Add up the payments.** Each qualifying birth draws one $1,500 prenatal
   payment and $3,000 in postnatal payments.
3. **Adjust for real-world take-up.** Not everyone who qualifies will sign up.
   We assume **90%** of newborn families enroll and **83%** of pregnant people
   enroll prenatally — conservative next to Flint's observed 98%.
4. **Project to 2028 and scale up to the whole state** using Census population
   weights.

**Bottom line (statutory design):** about **$21 million a year** in cash benefits
(≈$22M including 8% administrative overhead), serving roughly **7,969 births a
year**. The realistic uncertainty range is **$12M–$26M** — driven mostly by how
many families actually enroll, which no Hawaiʻi track record exists to pin down
yet.

**The three priced designs at a glance** (take-up held constant at 0.90 newborn
/ 0.83 prenatal, so only eligibility and duration change):

| Design | Who qualifies | Payments | Cash cost/yr | With 8% admin | Recipients/yr |
|---|---|---|---|---|---|
| **Statutory · 3 mo (default)** | Medicaid OR ≤300% FPL | $1,500 + $500×3 | **~$21M** | ~$22M | **7,969 births** |
| Statutory · 6 mo | Medicaid OR ≤300% FPL | $1,500 + $500×6 | ~$31M | ~$34M | 7,969 births |
| **Universal · 6 mo** | every birth family | $1,500 + $500×6 | **~$52M** | ~$56M | 13,228 births |
| **Universal · 12 mo** | every birth family | $1,500 + $500×12 | **~$88M** | ~$95M | 13,228 births |

Going universal at 6 months adds ~$22M (it reaches ~9,600 more recipients); the
extra 6 months of payments on top adds ~$37M more (the postnatal arm doubles,
the same families). Per-county splits for all three are in
`reports/rxkids_2028/cost_by_county_scenarios.csv`.

### Payment design and what "people affected" means

**Headline design (`statutory_3mo`, default since 2026-08-10):** a one-time
**$1,500** prenatal payment plus **three monthly $500** payments —
**$3,000 per birth**. The prenatal leg was always a single payment
(`prenatal_months=1`); only the postnatal window changed, 6 → 3.

**Shortening the postnatal window is a pure cost lever — it does not reduce
reach.** The same families qualify and enrol; they simply receive fewer
instalments. Births served is *identical* (7,969) under the 3- and 6-month
designs, while cost falls **$31.4M → $20.7M (−34%)**. What changes is depth per
family: average benefit drops from ~$4,280 to **$2,814**.

**"People affected" = BIRTHS SERVED.** Three different numbers could answer that
question and they are not interchangeable — the model now reports births served
first, in the console, workbook, PDF, `cost_by_state.csv` and
`cost_by_county.csv`:

| Metric | 2028 value | What it counts |
|---|---|---|
| **Births served** | **7,969** | **The headline. Births actually drawing benefits.** |
| Eligible families | 7,340 | Families clearing the income/Medicaid test — most have no birth in a given year |
| Recipient-payments | 13,771 | ~2× births: each served birth draws one prenatal + one postnatal payment. **Not people.** |

Births served is derived from the postnatal arm (`infant recipients ÷ postnatal
take-up`), because every served birth draws exactly one postnatal entitlement —
which is what makes it invariant to the length of the payment window. A
regression test pins that invariance.

### How the number of births is calculated — and where the data comes from

This is the single most important input, so here is exactly how it works:

1. **The official birth count comes from the CDC.** The federal government
   publishes the actual number of babies born to Hawaiʻi residents each year in
   its *"Births: Final Data"* reports (National Vital Statistics Reports). The
   anchor figure is **15,535 births in 2022** (report NVSR 73-02), counted by
   the mother's state of residence. The full year-by-year series we use:

   | Year | Hawaiʻi resident births | Basis |
   |---|---|---|
   | 2018 | 16,972 | NVSR final |
   | 2019 | 16,797 | NVSR final |
   | 2020 | 15,785 | NVSR final |
   | 2021 | 15,620 | NVSR final |
   | 2022 | 15,535 | NVSR final |
   | 2023 | 14,808 | NVSR final |
   | 2024 | 14,917 | NVSR final |
   | 2025 | ~14,541 | **DOH nowcast** (see below) |
   | 2026 | ~14,416 | **DOH nowcast**, annualised from 5 months |

   *Sources: CDC NVSR "Births: Final Data" series — 2022 from NVSR 73-02,
   2024 from **NVSR 75-02** (published 9 Jun 2026), Table 5, by place of
   residence. 2025–26 are Hawaiʻi DOH preliminary counts, converted to the
   residence basis (§3).*

   **Four of these values were wrong until 2026-08-07** — 2018 and 2019 by
   ~9% — and were corrected against CDC WONDER (the queryable NVSR natality
   file). The corrected series is a smooth ~12% decline 2018→2024; the old one
   had births *rising* into the pandemic, which is backwards. See §3
   ("NVSR series correction") for what that error had been propping up.

2. **We find those same births inside the Census data, family by family.** The
   model runs on **Census PUMS** (Public Use Microdata Sample — anonymized
   individual records from the American Community Survey, 2018–2022 5-year
   file). Every newborn shows up as a **baby listed at age 0** in a family's
   records. We count those age-0 babies per family. (Twins → 2.) This is what
   lets us tie each birth to a *specific family's income*, so we can test
   whether that family would qualify.

   - *Why age 0 and not "under 6 months"?* A baby under age 1 was born sometime
     in the last ~12 months — that matches the **annual flow** of births, which
     is the right basis for a yearly cost. Using a 6-month snapshot would
     undercount the year's births by about half.

3. **We anchor the count to the CDC.** When we add up all the age-0 babies in
   the Census data (using population weights), we get **~12,755** — about **10%
   below** the CDC's 14,917 for the same year (the ACS sample undercounts
   infants; the raw PUMS age-0 total on this frame is 13,329). So the
   model doesn't trust the raw Census count: it **calibrates** it up to the
   official CDC total, then projects forward (step 4). The final birth count is
   therefore anchored to the vital-statistics figure, not to the raw survey
   count.

   > *Corrected 2026-08-06.* This previously reported ~14,176 and a ~9%
   > undercount. Tracing the discrepancy uncovered a **weighting bug**, now
   > fixed — see "Birth weighting basis" in §3. The number here is the
   > post-fix, person-weighted count.

4. **We project births forward to 2028.** Because the forecast is for 2028, not
   2022, we age the birth count forward — since 2026-08-07 with the repo's
   **Kalman state-space filter**, chosen over the older trend ensemble and then
   empirically calibrated (bias + conformal κ) on a 46-fold back-test (§3). It
   projects **~13,842 births in 2028** (90% range ~12,800–14,900) — a
   **×0.891** adjustment on the 2022 level. (You can turn
   the projection off with `--no-birth-projection` to hold births at the 2022
   level, or drop the nowcast years with `--no-doh-nowcast`.)

So: **CDC tells us how many births there are; Hawaiʻi DOH tells us what has
happened since CDC's last final release; the Census data tells us which
families those births belong to; the trend model carries that forward to 2028.**

> **Earlier method (now replaced).** A previous version estimated births
> indirectly — multiplying each family's *total* dependent count by an average
> birth rate. That overcounted, because it treated older kids, students, and
> adult dependents as if they contributed births, and those larger families
> skew lower-income, which inflated the eligible share. Switching to directly
> counting age-0 babies roughly **halved the headline cost** — the proxy
> overstated births by about 1.7× (full decomposition in §10). The old method is
> still available via `--use-proxy-births` for comparison only.

### Where every other number comes from

| Input | Value | Source |
|---|---|---|
| Annual births | 15,535 (2022) → ~13,842 (2028 proj.) | CDC NVSR finals (via WONDER) + Hawaiʻi DOH nowcast, Kalman-projected + calibrated (§3) |
| Family records / incomes | Hawaiʻi sample | Census PUMS (ACS 2018–2022 5-yr) |
| Payment amounts | $1,500 prenatal; $500/mo × 6 postnatal | RxKids Flint program (rxkids.org) |
| Take-up (enrollment) | 90% newborn / 83% prenatal | RxKids Flint observed 98%/90%, set conservatively |
| 300% FPL income line | ~$85k for family of 3 | HHS federal poverty guidelines (year-aware) |
| Medicaid eligibility | Med-QUEST pathways (138%/196%/313% FPL) | Hawaiʻi Med-QUEST; KFF state health facts |
| Share of births *financed* by Medicaid (context only — not used in the model; unrelated to the eligibility split below) | **34% (5,075/yr, 2024)** | KFF, "Births Financed by Medicaid" (Hawaii-specific; verified 2026-08-19) |
| Share of *RxKids-eligible* births reached via the Medicaid clause (statutory design) | **100%** — computed directly from PUMS (§10) | `forecast_rxkids_2028.py`, `cost_by_scenario.csv` |
| Tax treatment | Non-taxable cash | Flint design; Census SPM rules (P60-280) |
| Admin overhead | 8% | Typical cash-transfer load (5–10%) |

Every one of these is documented in detail in the sections below, and the
forecast can be re-run with any of them overridden (see §10).

---

## 1. Program origin

**RxKids** is a universal prenatal + infant cash prescription program
launched in Flint, Michigan, in **January 2024** and administered by
**Michigan State University** with cash disbursement through
**GiveDirectly**. By mid-2026 it has expanded to **35+ Michigan
communities** including Kalamazoo, Detroit, multiple Wayne County
cities, and 15+ Upper Peninsula counties.

The program is the first community-wide implementation of a
prescription-style universal cash transfer in the perinatal window —
the period of life when child-poverty risk is highest and when
unconditional cash has the strongest measured effect on health,
material hardship, and economic stability outcomes (Hanna et al. 2025,
JAMA Network Open).

### Flint program parameters (as of May 2026)

| Parameter | Value |
|---|---|
| Operator | Michigan State University + GiveDirectly |
| Eligibility | Resident of designated community; minimum 16 weeks pregnant OR legal guardian of child <6 months |
| Income test | **None** (universal) |
| Medicaid test | **None** |
| Prenatal payment | **$1,500 one-time** (after 16 weeks pregnancy) |
| Postnatal payment | **$500/month for 6–12 months** (varies by community) |
| Tax treatment | Non-taxable charitable disbursement (GiveDirectly) |
| Observed take-up | **98%** of eligible newborns (Jan 2024–Sept 2025) |
| Cumulative reach (May 2026) | 10,774 families, $37.76M disbursed |

### Published outcomes evidence base

| Study | Year | Journal | Finding |
|---|---|---|---|
| Hanna et al. | 2025 | JAMA Network Open | ↑ prenatal care engagement |
| Hanna et al. | 2025 | American J. Public Health | ↑ maternal mental health; ↑ economic stability |
| Dickson et al. | 2024 | Family Justice Journal | ↓ child maltreatment via poverty reduction |
| Shaefer et al. | 2024 | The Lancet | Framework for universal child cash benefits |
| RxKids team | 2026 | RxKids Birth Impact brief | Births rose ~10% post-launch in Flint |

No published SPM-based poverty-reduction RCT is available yet — the
program is too new. Best evidence on mechanism: RxKids spending
analysis (2026) finds payments routed entirely to "essential needs —
starting with diapers", with measurable material hardship reductions.

## 2. Hawaii adaptation

This module models a **Hawaii-targeted variant** whose default
eligibility follows the **statutory** two-clause test (see §3): a unit
qualifies if it **either** (1) qualifies for benefits under the State's
Medicaid (Med-QUEST) program, **or** (2) has family income ≤ **300% FPL**
for a family of applicable size *including the expected unborn child(ren)*.
This statutory gate costs **~$32M/yr** (§10). It is substantially broader than a
Medicaid-only (138% FPL) gate and sits below the **modeled universal** designs
(no income/Medicaid test): **~$50M** at 6 months and **~$84M** at 12 months
(§10 scenario set). (An earlier back-of-envelope put fully-universal-12-month at
~$110M; the modeled figure is lower because it uses the 2028 projected birth
cohort and the conservative 0.90 take-up rather than 2022 births at 0.95.)

A Medicaid-adult-only income variant remains achievable by overriding
`income_fpl_cap=1.38`; the universal scenarios set `income_fpl_cap=100.0`
(≈ no income test — 100× FPL is ~$2.5M for a family of three, so every realistic
birth family clears it). See the module docstring and §10 for override recipes.

### Hawaii context

- **Annual live births (2022)**: **15,535** — CDC NVSR 73-02, Table on
  births by state by race/ethnicity. Composition: 3,854 Asian /
  1,486 NHPI / 2,896 White / 326 Black / 2,701 Hispanic.
- **Births financed by Medicaid**: **34%** (5,075 of 14,917 births, 2024)
  — KFF's *Hawaii-specific* "Births Financed by Medicaid" table, sourced
  from CDC WONDER natality records: https://www.kff.org/medicaid/state-indicator/births-financed-by-medicaid/
  (verified 2026-08-19; supersedes an earlier ~40%/~6,200 figure on this
  page — see the correction note in "What was ruled out" below). Hawaii's
  share has sat at 33–35% every year 2018–2024 in KFF's series; it has
  never been near 40%, and the count has never approached 6,200. **This is
  a different concept from RxKids clause-1 eligibility** (whether a family
  qualifies for Med-QUEST on income/family-size, tested on real PUMS data)
  **and is not used anywhere in the cost model** — it is background context
  only. For the model's own Medicaid-vs-income-only eligibility split,
  computed directly from the microdata, see §10.
- **QUEST Integration income limits**: pregnant women 196% FPL,
  children 0-1 308% FPL, children 1-5 308% FPL, adults 138% FPL.
- **Hawaii has no RxKids-equivalent pending legislation** (2025
  session). Closest analogs: WIC HI (~31k participants), Healthy Start
  Hawaii (home visiting), Keiki O Ka Aina (early childhood) — none
  provide unrestricted cash.

### Parameter defaults (`RxKidsHIParams`)

| Parameter | Default | Rationale |
|---|---|---|
| `prenatal_monthly` | $1,500 | Flint's one-time prenatal payment (`prenatal_months=1`). |
| `postnatal_monthly_per_child` | $500 | Flint's postnatal monthly payment. |
| `prenatal_months` | 1 | One-time prenatal payment. |
| `postnatal_months` | 6 | Flint's 6-month postnatal window (lower bound of the 6–12 mo range). |
| `postnatal_age_cutoff` | 1 | Infants in their first year (Flint design). PUMS carries no sub-year age, so age 0 is the closest proxy. |
| `income_fpl_cap` | **3.00** | Statutory clause 2: 300% FPL income test (at family size). |
| `pregnant_fpl_cap` | **1.96** | Clause 1 prenatal Medicaid pregnancy pathway (196% FPL). Subsumed by the 300% income test at default; binds only if the income cap is set below 196%. |
| `takeup_rate` | 0.90 | Anchored by *delivery channel* (clinic-enrolled, sub-Flint) — see "Take-up rate — how it is anchored" below. |
| `is_taxable` | `False` | Match Flint design — charitable disbursement, not IRS-reported. Routes through SPM resources only. |
| `child_under_age_share` | 0.066 | **Proxy-only fallback** (used when no observed birth count is supplied; the forecast default is observed age-0 dependents — see §3). Annual qualifying-birth rate per dependent: Hawaii births 15,535 ÷ ACS dependents 0-17 ≈ 233,000. NOTE the proxy applies this to *all* `num_dependents` (~410k weighted, incl. adult dependents), overstating births ~1.7×; prefer the observed basis. Sensitivity: linear. |

Plus the Medicaid clause, evaluated in `compute_rxkids_for_units` from the
`medicaid_receives` column produced by `compute_medicaid_for_units` (the
caller pre-attaches it).

### Take-up rate — how it is anchored

`takeup_rate` (default **0.90**) is the single most important and least
data-anchored input — it scales the whole estimate linearly. It is set by
analogy, not by an administrative caseload (no Hawaiʻi RxKids exists). The
key principle: **take-up is driven primarily by the *delivery channel*, not
the benefit type.**

- **Tax-delivered** benefits (EITC, CTC) have take-up limited by tax-filing
  friction — non-filers, complexity, awareness. EITC ≈ 78% overall / ~85%
  for families with children; CTC ≈ 90% among filers.
- **Clinically enrolled / "prescribed"** benefits — RxKids' actual design in
  Flint (enroll at a prenatal-care visit, disbursed via GiveDirectly) — clear
  that friction because nearly every pregnant person has prenatal contact.
  Flint observed **98%** of eligible newborns. The same perinatal channel
  drives high take-up in WIC-infants (~90%+) and Medicaid-for-pregnant-women
  (~90%+).
- **Application-based** standalone programs sit lower (WIC pregnant/children
  ~55–65%; SNAP ~82%).

So **EITC/CTC are the right anchor only if Hawaiʻi delivers RxKids as a tax
credit.** For a Flint-style clinic-enrolled cash program, the perinatal
analogs (WIC-infants, Medicaid-pregnant, Flint itself) put steady-state
take-up at **~0.85–0.98**, which is why the 0.90 default is justified — and
why EITC/CTC should be treated as a *floor*, not the central estimate.

Reference scenarios by delivery channel (override with `--takeup-rate`):

| Delivery design | Take-up anchor | Rate |
|---|---|---|
| Refundable tax credit | EITC/CTC families | ~0.85 |
| **Clinic-enrolled, maturing (default)** | Perinatal analogs, sub-Flint | **0.90** |
| Flint-mature (hospital partnership) | Flint observed | 0.98 |
| Standalone application | WIC pregnant/children | ~0.60 |

**Arm-specific take-up.** Mothers enroll prenatally at a lower rate than
newborns are enrolled (Flint: ~90% prenatal vs ~98% of newborns). The model
sets postnatal (newborn) take-up = `takeup_rate` and prenatal take-up =
`takeup_rate × 0.92` (`PRENATAL_TAKEUP_RATIO`). So the **0.90 default →
postnatal 0.90 / prenatal 0.83**, and the Flint scenario (`--takeup-rate
0.98`) → 0.98 / 0.90, recovering Flint's observed arm rates. (The library
default leaves the two arms uniform; the forecast applies the split.)

Three structural notes: (1) take-up here is the **steady-state** rate — the
launch-year ramp (§10) separately models lower year-1 enrollment; (2) the
assumption band sweeps the postnatal rate (and scales prenatal with it), so
this uncertainty is in the reported range; (3) take-up figures are standard
IRS/USDA/CMS/RxKids estimates (see §8 for sourcing).

### Estimated annual cost (statutory eligibility)

The 300%-FPL-OR-Medicaid gate is far more inclusive than the legacy 138%
"Medicaid variant," so the modeled cost (**~$32M/yr**) lands materially above the
old ~$8–15M back-of-envelope and below the modeled universal designs (**~$50M**
at 6 months, **~$84M** at 12 months — §10 scenario set). The authoritative
figures are produced by `forecast_rxkids_2028.py` (see §10), which weights the
per-unit expected benefit by the PUMS household weight on the real PUMS frame.

For reference, an old back-of-envelope **universal Flint-equivalent variant**
(superseded by the modeled universal scenarios in §10):

- 15,535 births × $1,500 prenatal × 0.95 take-up = $22.1M
- 15,535 births × $500/mo × 12 × 0.95 = $88.5M
- **Total: ~$111M/yr** — higher than the modeled ~$84M because it uses 2022
  births (vs the 2028 projected cohort) at 0.95 take-up (vs the conservative
  0.90/0.83 split).

## 3. Statutory eligibility (Medicaid OR 300% FPL incl. unborn)

A unit is eligible if it satisfies **either** clause:

- **Clause 1 — Medicaid.** Any member qualifies for benefits under the
  State's Medicaid (Med-QUEST) program. Implemented by reusing the
  `medicaid_receives` boolean from
  `tax_modeler.benefits.compute_medicaid_for_units` (all categorical
  pathways: 138% adult, 196% pregnant, 313% children, 100% aged). The
  caller pre-attaches this column; if it is absent the module applies
  clause 2 only and logs a warning.
- **Clause 2 — 300% FPL.** `income / FPL(family_size) <= income_fpl_cap`
  (default 3.00), tested at family size. A birth's family includes the
  child either way (the unborn child pre-birth equals the newborn
  dependent in the cross-section), so a single family-size test serves
  both arms — no separate "+unborn" increment is needed. For the prenatal
  arm, clause 1 additionally honors the Medicaid **pregnancy pathway**
  (`pregnant_fpl_cap`, 196% FPL); at the 300% default this is subsumed.

The income test uses the FPL table for the run's `tax_year`
(`benefits/_fpl.py`), so forward-projected incomes are tested against
same-year thresholds (see §7 caveat 4).

### Both arms are birth-driven

Both arms are driven by the same **birth events**. Two bases are supported:

- **Observed (default in `forecast_rxkids_2028.py`).** PUMS *does* carry
  individual ages: the tax-unit constructor records each claimed dependent's
  real age, so an **age-0 dependent is an infant born in the last ~12 months**
  — the annual birth flow, the correct basis for the full per-birth
  entitlement (not a <6-month stock, which understates ~2×). The per-unit
  observed birth count = the number of age-0 dependents (twins → 2). The
  forecast attaches this as `observed_births` and passes it to
  `compute_rxkids_for_units(..., birth_count_col="observed_births")`. The
  weighted observed count (**12,755**, person-weighted) sits ~10% below CDC
  NVSR resident births for the same frame year; the calibration step
  (`_calibrate_births`) scales it up to the vital-statistics total and then
  projects it forward, so the final cohort is anchored to NVSR, not the raw
  survey count.

- **Proxy (library fallback; `--use-proxy-births`).**
  `birth_events = num_dependents × child_under_age_share`. The legacy basis,
  retained for backward comparison. It multiplies an *all-dependents* count
  (older children, students, adult dependents — ~410k weighted) by a rate
  calibrated against a *children-only* denominator (~233k), so it overstates
  total births ~1.7× and, because dependent counts skew toward lower-income
  families, inflates the eligible-birth share. **Prefer the observed basis.**

Either way a first birth shows up as the newborn dependent in the PUMS
cross-section, so the model captures **first AND repeat births across ALL
filing statuses** (married couples included), not just a single/HoH-no-
dependent proxy. The observed basis additionally fixes the proxy's level and
income-distribution biases.

#### Birth-cohort projection (observed basis)

The observed infant count is a *base-year* stock. Because the forecast ages
**incomes** to the target year, the **birth cohort** is aged coherently too:
the weighted observed count is calibrated to a single target via one scalar
factor folding in (1) the PUMS→vital-statistics base correction and (2) the
base-year→target-year trend. The trend comes from running the resident-birth
series (`HI_BIRTHS_BY_YEAR` + DOH nowcasts, by state of residence) through the
package's own damped-trend + AR(1) ensemble (`project_acs_ensemble`, φ=0.85/yr
annual cadence — the same machinery the income forecast uses). For TY2028 the
calibrated Kalman filter projects **~13,842 births** (90% PI ~[12,779, 14,905])
from a 2022 base of 15,535 — a **×0.891 vital-statistics trend**. Combined with the
PUMS→vital base correction (raw weighted **12,755** scaled up to the NVSR
level, since the survey undercounts infants ~10%), the single scalar actually
applied to `observed_births` is **×1.085** (12,755 → 13,842). Pass
`--no-birth-projection` to hold the cohort at the base-year level instead.

#### Birth weighting basis — person weight, not the hybrid unit weight

**Fixed 2026-08-06. This was a real bug and it moved the headline.**

Investigating the drifting raw-birth count (15,419 → 14,176 → 12,339 across
successive refresh notes, never explained) found the cause: `_observed_births`
counted infants per tax unit and then let everything downstream multiply by the
unit's **`weight`** — but that weight is not a demographic weight at all. Per
`_calculate_hybrid_weight` (`units/constructor.py`) it is:

* the **household** weight `WGTP` for any multi-person unit — and every unit
  carrying an infant is multi-person — not the infant's own person weight
  `PWGTP`; then
* multiplied by **DOTAX filing-status calibration factors** that force the
  tax-unit mix onto DOTAX's TY2022 filing-status shares: single 0.82, MFJ 1.00,
  **HoH 1.30**, MFS 1.35.

Both steps are correct for their intended purpose (revenue estimation on a
filer population) and wrong for counting babies. Measured on the 2024 1-year
frame:

| Basis | Weighted age-0 | vs correct |
|---|---|---|
| Raw PUMS age-0, `PWGTP` (**correct**) | 13,329 | — |
| Same infants, `WGTP` substituted | 11,959 | −10.3% |
| × DOTAX filing-status factors (**old code**) | 12,339 | −7.4% |
| Person-weighted, claimed infants (**new code**) | 12,755 | −4.3% † |

† the residual is 4 infants (532 weighted, ~4%) living in households where no
tax unit claims an age-0 dependent — a separate dependent-assignment gap, not a
weighting one. The calibration scalar absorbs it.

**Why the drift.** The factors are periodically re-derived. Commit `661ca38`
(2026-06-11) corrected them after the B2–B5 construction fixes — **HoH fell
1.88 → 1.30**. Since ~19% of infants sit in HoH units, every infant in those
units instantly lost 31% of its weight, mechanically dropping the birth count
with no demographic change whatsoever. Re-running today's frame with the old
factors returns 13,660 (vs 12,339 with current factors), which brackets the
documented 14,176 once the construction changes in the same commit are allowed
for. The birth count was, in effect, a hostage of a tax-administration
calibration.

**Why it mattered — the eligible share was biased, and so was the cost.**
The distortion is not neutral across income: HoH filers are disproportionately
lower-income single parents, so the ×1.30 over-weighted exactly the birth
families most likely to pass a means test. Correcting the basis:

| | Old (unit weight) | Fixed (person weight) | Δ |
|---|---|---|---|
| Eligible share of births | ~60% | **~56%** | **−3.4pp** |
| Statutory 6-mo cost | $33.2M | **$31.3M** | **−5.7%** |
| Universal 6-mo cost | $52.3M | **$53.3M** | **+1.9%** |
| Q1 (poorest fifth) share of benefit | 8.2% | **5.3%** | −2.9pp |

The means-tested design fell while the **universal** design *rose* — the
signature of a low-income tilt being removed rather than a level change. A pure
level error would have moved both the same way; only a distributional error
moves them in opposite directions. That is the confirmation that the eligible
share, not just the count, was wrong.

**The fix.** `_observed_births` now emits an *effective* count,
`Σ PWGTP(age-0 deps) / unit_weight`, so that `observed_births × weight`
reproduces the infants' own person-weighted total exactly. Every downstream
consumer (cost, county rows, quintiles) keeps multiplying by `weight` unchanged
and silently gets the right basis; the raw head-count survives as
`observed_births_n` for QA. This required carrying each dependent's `pwgtp`
through `_build_dependent_details`. Regression tests pin the invariant, the
absent-`pwgtp` fallback, and the constructor field.

> **Generalisation worth heeding.** `weight` on a tax unit is a *filer* weight,
> calibrated to a tax-administration target. Any consumer counting **people**
> (births, children, household members) must weight by `PWGTP`, not by it.
> The RxKids birth driver was the one place this had leaked; other demographic
> consumers should be audited against the same rule.

#### County split — DOH shares, not raw PUMS shares (2026-08-07)

The state birth total is well-anchored (NVSR + DOH nowcast, §3 below). PUMS's
**county allocation** of that total is not anchored to anything, for two
distinct reasons — one a sample-size problem, one structural:

**1. Small samples.** On the 2024 1-year frame, the raw (unweighted) age-0
infant count is Honolulu 81, Hawaii 11, Maui 9, Kauai 6. At n=11 the Poisson
relative SE is ~30% — comfortably large enough to explain what the raw PUMS
split implied for Hawaii County: a **−36.5%** collapse from DOH's actual level
by 2028, when DOH itself shows Hawaii County flat (~1,900–1,950) every year
2018–2025. A larger 5-year frame narrows this (n=65 for Hawaii County) but
doesn't fix problem 2, and wasn't pursued for that reason.

**2. Maui and Kauai are not independently sampled at all.** Hawaiʻi's
2020-vintage PUMA geography combines Maui + Kalawao + Kauai into a single PUMA
(0100) — confirmed present, still combined, in both the 1-year and 5-year
files (Census PUMA boundaries are fixed for the decade; no PUMS vintage
separates them). `PUMA0100Imputer`
(`analysis/puma_imputation.py`) assigns each unit a **probabilistic**
Maui-vs-Kauai label from 2023 population shares (68.9%/31.1%) plus demographic
heuristics (income, family size, housing type) — not real sampled geography.
Any Maui/Kauai split read off raw PUMS shares is therefore an artifact of that
imputer's heuristics, not survey evidence.

**The fix.** Hawaiʻi DOH's monthly county vital-statistics (the same source as
the state-level nowcast, §3) are genuine administrative data, exactly resolved
by county — immune to both problems. `_calibrate_births_by_county`
redistributes the (unchanged) state birth total across counties to match
DOH's **aggregate 2018–2025 county shares** (2026 excluded as a partial year;
shares are flat over the window — no material trend, so a plain aggregate
share, not recency-weighting, is the right estimator):

| County | DOH share (2018–2025) | Raw PUMS-implied share (2028) | Factor applied |
|---|---|---|---|
| Honolulu | 73.4% | 76.8% | ×0.94 |
| Hawaii | 12.3% | 8.8% | **×1.42** |
| Maui | 9.6% | 9.4% | ×1.03 |
| Kauai | 4.7% | 5.1% | ×0.92 |

**This moves the state COST total too — not just `cost_by_county`.** The state
*birth* total is unaffected by construction (`Σ DOH_COUNTY_SHARE == 1`, so
summing the four county targets reproduces the state target exactly — verified
by test). But RxKids eligibility (Medicaid/300% FPL) is tested **per unit**,
and Hawaiʻi County runs poorer than Honolulu — so shifting birth-weighted mass
from an over- to an under-sampled county changes how much of it lands on
already-eligible vs. already-ineligible units. Measured on the real frame:
**statutory cost +2.1% ($31.3M → $32.0M), recipients +2.1% (13,728 → 14,011)**,
with `eligible_families` (the 0/1 test itself) **exactly unchanged** at 7,340 —
confirming the mechanism is reallocation onto already-eligible units, not a
change in who qualifies. `--no-doh-county-shares` restores the raw (small-
sample, partly-imputed) PUMS split for comparison.

#### Data-source audit: DOH vs CDC (2026-08-06)

The birth series is the least externally-anchored input in this model — unlike
income or rent, it has **no entry in the repo's anchor-source registry**
(`acs/sources/base.py`), so it gets no macro anchor, no ML leading-indicator
feature, and no v3 bias/κ calibration. It is a bare series run through two trend
models. That makes the quality of the series itself the whole ballgame. Two
problems were found and fixed:

**Problem 1 — the series was a year stale, and stale in the worst place.**
`HI_BIRTHS_BY_YEAR` ended at 2023 (recorded then as 14,643 — itself wrong, the
true figure is 14,808; see "NVSR series correction" below), the bottom of a
one-year dip. CDC
had since published **NVSR 75-02 (9 Jun 2026), "Births: Final Data for 2024"**,
showing Hawaiʻi **recovered to 14,917**. Projecting from the dip alone
understated the cohort and produced an implausibly wide interval.

**Problem 2 — an 18-month hole with nothing in it.** NVSR final data lags
~18 months (2024 final arrived mid-2026), so even after adding 2024 the model
jumped from a 2024 observation straight to a 2028 target with **four unobserved
years** in between. Hawaiʻi DOH (Office of Health Status Monitoring) publishes
preliminary births **monthly, by county, at ~5 weeks' lag**
(<https://health.hawaii.gov/vitalstatistics/>), which covers exactly that hole.

DOH is *upstream* of CDC — the state registers the certificates and transmits
them to NCHS — so it is not a competing estimate, it is the same data seen
earlier and with less processing. That cuts both ways, and two corrections are
mandatory before DOH can join the NVSR series:

**(a) Occurrence vs. residence — small and stable.**
DOH counts births *occurring* in Hawaiʻi (including to non-residents); NVSR
counts births to Hawaiʻi *residents* (including those occurring out of state).
RxKids eligibility is a residency test, so **NVSR is the correct basis** and DOH
must be converted onto it. Against the *corrected* NVSR series the ratio is
tiny and essentially constant:

| Year | DOH (occurrence) | NVSR (residence) | ratio |
|---|---|---|---|
| 2018 | 17,027 | 16,972 | 1.0032 |
| 2019 | 16,832 | 16,797 | 1.0021 |
| 2020 | 15,811 | 15,785 | 1.0016 |
| 2021 | 15,656 | 15,620 | 1.0023 |
| 2022 | 15,570 | 15,535 | 1.0023 |
| 2023 | 14,851 | 14,808 | 1.0029 |
| 2024 | 14,972 | 14,917 | 1.0037 |

Mean **1.0026**, sd **0.0007**, range 1.0016–1.0037 — one regime, no trend. That
is what an island state 2,400 miles from anywhere should look like: almost
nobody flies in to give birth, almost no resident delivers out of state. All
available years are used; the dispersion still flows into the nowcast MOE rather
than treating the factor as exactly known.

> **Correction (2026-08-07) — a story this document previously told, wrongly.**
> This section used to report the ratio as **1.1054 / 1.0928 in 2018/2019**,
> collapsing to ~1.00, and explained it as a pre-COVID *birth-tourism regime*
> ended by the travel collapse — calibrating on post-2020 years only to avoid
> it. **No such regime existed.** The 2018/2019 ratios were artifacts of the
> four wrong NVSR values (see "NVSR series correction" below); once the series
> is right, every year sits at ~1.003. The lesson worth keeping: a plausible
> mechanism was invented to explain what was really a data error, and the
> mechanism survived review because it sounded reasonable. The physical
> implausibility — a 10% non-resident birth share in the most isolated
> archipelago on earth — should have been the tell.

**(b) Trailing months are structurally incomplete.** Birth certificates register
with a lag, so the newest months of any snapshot are short. In the 2026-07-06
pull, June reads **777** against a ~1,200 run-rate (~65% complete) while May
(1,244) sits on trend. Months within `DOH_MATURATION_MONTHS = 2` of the snapshot
are dropped; a partial year is annualised on the historical share of the
retained months, with the annualisation variance propagated into the MOE.

Each nowcast therefore carries three variance components — Poisson counting
noise, occurrence→residence ratio uncertainty, and (partial years only)
annualisation uncertainty. Resulting points: **2025 ≈ 14,541 ± 199** (all 12
months mature) and **2026 ≈ 14,416 ± 347** (annualised from 5 mature months).

> Note on how much that MOE actually buys. Under the **ensemble** projector it
> bought nothing for the point estimate: `fit_damped_trend` and
> `fit_ar1_log_diff` never read per-observation MOE (they use only the *latest*
> observation's, for the sampling-SE term), so a wide nowcast MOE could widen
> the interval but never move the point. That is one of the reasons the default
> projector is now the **Kalman filter**, which consumes each observation's MOE
> as measurement noise — see "Projector choice" below. Even so, be honest about
> the magnitude: the nowcast MOEs (199/347) sit close to the finals' nominal
> Poisson MOEs (~201), so the down-weighting is real but modest, and it does not
> price the risk that DOH revises a preliminary count.

**Effect on the TY2028 projection**, cumulative across every fix in this
section (all measured on the ensemble projector except the last row, so the
method change is isolated):

| Series / method | 2028 point | 90% PI | PI width |
|---|---|---|---|
| Original (NVSR ≤2023, no nowcast) | 13,632 | [10,502, 16,761] | 6,258 |
| + 2024 NVSR final | 14,453 | [12,736, 16,169] | 3,433 |
| + DOH nowcast | 14,127 | [13,071, 15,183] | 2,112 |
| + corrected NVSR series (2018/19/20/23) | 13,923 | [13,104, 14,743] | 1,639 |
| + Kalman projector (raw) | 14,126 | [12,868, 15,384] | 2,516 |
| **+ bias & κ calibration, 2007-2024 series (current default)** | **13,842** | **[12,779, 14,905]** | **2,126** |

The individual moves matter more than the total, because they largely cancel:
adding 2024 pushes the cohort **up** (+6.0%) by removing the dip-extrapolation;
the nowcasts pull it **down** (−2.3%); correcting the series pulls it **down**
again (−1.4%, the corrected 2018/19 are higher so the fitted decline is
steeper); switching to Kalman pushes it **up** (+1.5%); the empirical bias
correction pulls it back **down** (−2.0%, the filter systematically
under-extrapolates this decline in 46 walk-forward folds). Net across all five:
13,632 → 13,842, **+1.5%**.

**The headline barely moved; the honesty of the interval did.** The last row
*widens* the PI, which is the point — the back-test found the ensemble's 90%
interval covering the truth in only **50%** of folds. A number that was roughly
right by luck was being quoted with an interval that was wrong on purpose.

#### NVSR series correction (2026-08-07)

Four of the seven `HI_BIRTHS_BY_YEAR` values were **wrong**, found by querying
CDC WONDER (dataset D66 "Natality, 2007-2024", Hawaiʻi, grouped by year) — the
same NVSR natality file, queryable:

| Year | was | is (WONDER) | error |
|---|---|---|---|
| 2018 | 15,404 | **16,972** | −9.24% |
| 2019 | 15,403 | **16,797** | −8.30% |
| 2020 | 15,730 | **15,785** | −0.35% |
| 2023 | 14,643 | **14,808** | −1.11% |
| 2021 / 2022 / 2024 | — | unchanged | correct already |

2022 (NVSR 73-02) and 2024 (NVSR 75-02 Table 5) cross-validate against WONDER
exactly, so WONDER is authoritative and the four odd values came from elsewhere.
Two independent tells that the old series was wrong, both visible without any
external source:

* **It was demographically incoherent.** 2018/2019 sat *below* 2020/2021 — i.e.
  births rising into the pandemic, against the national pattern. The corrected
  series is a smooth ~12% decline 2018→2024.
* **It manufactured the fake occurrence/residence regime** described above. A
  data error and the story invented to explain it were propping each other up.

A regression test now pins the series against its WONDER source, and
`scripts/refresh_doh_births.py` reproduces the DOH constants from the live pages
so the next refresh is a re-run rather than a retype.

#### Projector choice — Kalman, on back-test evidence

The cohort is projected with the package's **Kalman state-space filter**
(`BIRTH_PROJECTION_METHOD = "kalman"`), not the damped-trend + AR(1) ensemble
that drives the dollar indicators — and since later on 2026-08-07 the raw
filter output is **empirically calibrated** (bias + conformal κ, below). Both
decisions are back-test results, not preferences: the series was extended to
the full CDC WONDER window (**2007–2024, 18 finals**) precisely so
`scripts/backtest_birth_projection.py` could walk-forward **46 folds** at the
production-relevant horizons (h ≤ 4). Full write-up in
[`backtests/results/birth_projection_2026-08-07.md`](backtests/results/birth_projection_2026-08-07.md).

| method | n | MAPE | bias | RMSE | CI90 coverage |
|---|---|---|---|---|---|
| persistence (floor) | 46 | 5.18% | +5.14% | 1,018 | — |
| ensemble (old default) | 46 | 3.43% | +3.12% | 715 | **69.6%** (40% at h=4) |
| **kalman (default)** | 46 | **3.27%** | **+2.11%** | **658** | 97.8% |

Kalman wins every point metric, and the ensemble's interval genuinely collapses
with horizon — **40% coverage at h=4**, the horizon the 2028 target sits at from
the last final. (An earlier 6-fold version of this table measured that as 50%
pooled; real n confirmed the direction and sharpened the location.)

**Why they differ here.** `project_kalman` consumes each observation's MOE as
measurement noise (R = (se/estimate)²). `fit_damped_trend` / `fit_ar1_log_diff`
ignore per-observation MOE entirely. On a series whose newest points are
*nowcasts carrying deliberately-sized MOEs*, only the Kalman path lets that
uncertainty reach the point estimate.

**Empirical calibration (bias + conformal κ).** At n=46 two miscalibrations
became measurable, and both are now corrected in `_project_births`, in the
repo-canonical order (bias first, κ on bias-corrected residuals):

* **Systematic +2.0% point bias** (`BIRTH_KALMAN_LOG_BIAS = 0.0203`): the
  φ=0.85 damping pulls the growth state toward zero while the real series keeps
  declining, so the filter under-extrapolates the decline — monotone in horizon
  (+0.4% at h=1 → +4.2% at h=4; pooled, per the repo's n≥20 strata threshold).
  Bias-corrected in-sample MAPE: 3.27% → **2.62%**.
* **Over-covering analytical interval** (97.8% vs the 90% target):
  `BIRTH_KALMAN_SE_KAPPA = 0.862`, the split-conformal quantile on
  bias-corrected residuals (same finite-sample convention as
  `acs/conformal.py`), *shrinks* the half-width onto **93.5%** in-sample
  coverage. Before this, the quoted PI was purely analytical — something the
  repo's own discipline forbids.

Re-run the script and re-paste both constants whenever a new NVSR final lands.

**Caveats, stated plainly.** The 46 folds come from one series with overlapping
windows, so the effective sample is smaller than 46. The pooled bias slightly
overcorrects at the production horizon (h=2 measured +1.5%) and undercorrects at
h=4 (+4.2%). The correction encodes "the decline keeps being under-extrapolated"
— if Hawaiʻi fertility genuinely flattens (2024 ticked up), it overshoots
downward by up to ~2%. And the folds score finals only: nowcast-specific risks
(DOH revisions, ratio drift) are priced only via the nowcasts' wider MOEs in the
filter's R. `--birth-projection-method ensemble` restores the old path for
comparison.

**Snapshot discipline.** DOH counts are pinned in-code
(`HI_DOH_BIRTHS_MONTHLY`, `DOH_SNAPSHOT`) rather than live-fetched, matching the
byte-reproducibility rule the anchor bundles follow. On refresh: bump
`DOH_SNAPSHOT`, re-pull the monthly table, and **delete any year NVSR has since
finalised** — NVSR always wins, and `_doh_nowcast_births()` enforces this by
skipping any year already present in `HI_BIRTHS_BY_YEAR`.

**What was ruled out.** Med-QUEST / DHS annual reports were examined as a source
for the Medicaid-financed-birth share (§2) and do **not** carry one: the CMS
MCPAR template reports perinatal *quality rates* (timeliness of prenatal care,
postpartum care completion, contraceptive-care measures) with no births
denominator, and the monthly enrollment reports give a point-in-time
"04-Pregnant Women" caseload (~2,500/mo), which is a stock, not a birth flow.

> **Correction (2026-08-19).** This section previously claimed the
> Medicaid-financed-birth share "rests on KFF's national-average inference"
> and named CDC NVSR's payment-source-for-delivery table as "the best
> unexploited upgrade" — implying no Hawaii-specific source existed. Both
> claims were wrong. KFF publishes a **Hawaii-specific** "Births Financed by
> Medicaid" table (state-by-state, not a national figure applied to every
> state), sourced from CDC WONDER natality records — i.e. essentially the
> NVSR-derived upgrade this section called "unexploited" was already sitting
> in KFF's own site. Read directly (not summarized) 2026-08-19: Hawaii's
> share was **34.0% in 2024** (5,075 births), and has run 33–35% every year
> 2018–2024 — never near the ~40%/~6,200 figure this document previously
> carried, which appears to have been either stale or from a different,
> unidentified methodology. §2 now cites the verified KFF figure directly.
> The lesson worth keeping, matching this document's other correction notes:
> a plausible-sounding gap in the data ("no Hawaii-specific source exists")
> went unchecked because the wrong number didn't look obviously wrong — it
> survived being read as a summary rather than opened as a primary table.

Each eligible birth draws one prenatal and one postnatal payment:

- **Prenatal** (`rxkids_prenatal_amount`): `birth_events × prenatal_monthly
  × prenatal_months × takeup_rate`, gated on `num_dependents > 0` AND
  (clause 1 incl. pregnancy pathway OR clause 2).
- **Postnatal** (`rxkids_postnatal_amount`): `birth_events ×
  postnatal_monthly_per_child × postnatal_months × takeup_rate`, gated on
  `num_dependents > 0` AND (clause 1 OR clause 2).

Both are **probabilistic** per unit, not deterministic; the weighted state
total recovers the right population-level expectation. At a uniform take-up
the prenatal arm is exactly **half** the postnatal arm (the $1,500 vs $3,000
per-birth payment ratio); with the default arm-specific take-up (§2) it is a
bit less than half (lower prenatal enrollment).

The combined `rxkids_amount = rxkids_prenatal_amount +
rxkids_postnatal_amount` is what feeds SPM resources; the two subtotals
let the cost report split program outlay by arm.

## 4. SPM resource accounting

RxKids cash is modeled as **non-taxable** (`is_taxable=False`), matching
the Flint program structure. This means:

1. `rxkids_amount` is **NOT added to** `total_cash_income` →
   no interaction with AGI / EITC / CTC phase-outs.
2. `rxkids_amount` **IS added to** `spm_resources` via the
   `rxkids_col` argument of `compute_spm_resources`.

The baseline `compute_poverty_impact` run passes `rxkids_col=None` so
baseline poverty reflects current Hawaii policy (no RxKids). The
`rxkids_hi` scenario adds `rxkids_amount` to baseline resources as
an expansion counterfactual.

If a Hawaii legislative proposal structured the payment differently
(e.g. as a refundable tax credit flowing through Form N-11), the
modeling would need to route `rxkids_amount` through AGI and recompute
EITC / CTC interactions — currently not supported.

## 5. Single-mother poverty disaggregation

`compute_poverty_impact` now returns a `by_household_type` frame
disaggregating poverty metrics by `filing_status`:

- `single` (no dependents)
- `head_of_household` (closest tax-unit proxy for single mother — an
  unmarried adult filing with at least one qualifying dependent)
- `married_filing_jointly`
- `married_filing_separately`

The `head_of_household` row is the **single-mother proxy** used for
RxKids analysis. The `by_state` frame is augmented with:

- `poverty_rate_hoh_baseline` — baseline HoH SPM rate
- `persons_in_poverty_hoh_baseline`
- `weighted_persons_hoh`
- `persons_lifted_{scenario}_hoh` — HoH-specific lift for each scenario
- `poverty_rate_{scenario}_hoh` — HoH-specific scenario rate

These let advocates cite the **single-mother-specific** poverty
reduction directly, which is the natural framing for a perinatal
cash-transfer program.

### Limitations of the HoH proxy

- HoH is a tax-filing status, not a household composition. Some HoH
  filers are not mothers (e.g. unmarried fathers, adult children
  caring for parents).
- Married mothers in low-income MFJ households also benefit but are
  not captured in the HoH row.
- Cohabiting unmarried mothers may file as `single` rather than HoH
  depending on dependent-claiming arrangements.
- As of Tier 4 (F-14), the poverty pipeline defaults to SPM-unit
  granularity per Census P60-280, so cohabiting partners and shared
  households are already pooled correctly. Single-mother attribution
  is the `single`/`head_of_household` subset of the SPM filing-status
  proxy (single adult with children).

## 6. Comparison to Flint outcomes

Projected Hawaii impact under default Medicaid-targeted parameters:

| Metric | Flint (observed) | HI projected (this model) |
|---|---|---|
| Take-up | 98% | 0.90 postnatal / 0.83 prenatal (default) |
| Avg disbursement / recipient | ~$3,505 (rolling) | ~$2,280 (prenatal+postnatal mix) |
| Reach | 10,774 families (cumulative) | **7,969 births served/yr** (13,771 recipient-payments: ~6,598 prenatal + ~7,172 postnatal) |
| Annual cost | ~$25-30M (single-city) | ~$32M benefit / ~$35M w/ admin (statutory 300%-FPL-OR-Medicaid); ~$50M (universal·6mo) / ~$84M (universal·12mo) |
| Persons lifted out of poverty | Not yet published | Reported per `--apply-rxkids` run |

The Hawaii model matches Flint's per-payment amounts ($1,500 one-time
prenatal, $500/mo postnatal); the difference from Flint is the eligibility
gate (statutory Medicaid-OR-300%-FPL vs Flint's universal no-test design) and
the conservative 0.90 take-up. Advocates can override parameters to model the
Flint-equivalent universal program — see the override recipe in the module
docstring.

## 7. Limitations & caveats

1. **Pregnancy not directly observable on PUMS** — the model uses **observed
   births** (age-0 dependents) as the common driver of both arms, calibrated
   to vital statistics and projected forward (§3). Each observed infant stands
   for one birth that had a prenatal period, so both arms are right in level
   and distribution. Unit-level amounts are still expectations (the prenatal
   arm is the observed infant standing in for its own pregnancy), not literal
   payments. The legacy `num_dependents × child_under_age_share` proxy remains
   available via `--use-proxy-births` but overstates births (§3).
2. **Birth basis** — postnatal (and prenatal) counts use age-0 dependents, an
   infant stock that ≈ the annual birth flow (an infant <1yr was born in the
   last ~12 months). This is the correct entitlement basis; a narrower
   <6-month stock would understate the postnatal arm ~2×. Twins are counted
   correctly (two age-0 dependents → two payments).
   Sensitivity: linear.
3. **No labor supply / fertility response modeled** — static
   counterfactual. The literature suggests unconditional cash
   transfers slightly reduce maternal labor supply (small effect,
   ~1-3 pp) but plausibly increase fertility on the margin. Neither
   is modeled.
4. **FPL is year-aware (`benefits/_fpl.py`)** — published 2024 and 2025
   HHS Hawaii tables, with CPI-projected forward years 2026–2028 (~2.3%/yr
   off the 2025 base, per CBO Jan 2025). `compute_rxkids_for_units` and
   `compute_medicaid_for_units` take a `tax_year` and test eligibility
   against that year's table, so a 2028 projection ages incomes AND
   thresholds coherently. The 2026–2028 inflator is an estimate; replace
   with a real HHS table when published.
5. **No admin-caseload anchor** — because RxKids is hypothetical for
   Hawaii, there's no IRS or DHS caseload to take-up-calibrate
   against (unlike SNAP, WIC, EITC). Take-up is set via the
   `takeup_rate` parameter; sensitivity sweep is the natural
   robustness check.
6. **HoH proxy ≠ single mother** — see Section 5 limitations.
7. **Modeled as charitable cash (non-taxable)** — if implemented as
   a refundable tax credit instead, the EITC/CTC phase-out
   interaction is not modeled.

## 8. Research findings (data collected 2026-05-16)

Compiled from `https://rxkids.org` (main page, /about, /impact/dashboard,
/impact/research/rx-kids-publications/), CDC NVSR 73-02 (2022 final
natality), Census P60-280 (SPM Technical Documentation), KFF state
health facts, and Hawaii DOH vital statistics index.

Key facts feeding this methodology:

- RxKids is **universal, residency-based, no income test, no Medicaid
  test** in its actual Flint implementation. Hawaii model defaults
  to a means-tested variant for cost reasons but supports universal
  modeling via parameter override.
- **98% take-up** of eligible newborns Jan 2024–Sept 2025 — universal
  design + hospital partnership are the take-up drivers.
- Hawaii **15,535 annual births (2022)**; Medicaid-financed share is
  **34% (2024)** → ~5,075 Medicaid births/yr — KFF's Hawaii-specific table,
  corrected 2026-08-19 from an earlier ~40%/~6,200 figure this document
  carried (see §3, "What was ruled out"). CDC NVSR resident-births series (by state of
  residence), used for the birth-cohort projection (`HI_BIRTHS_BY_YEAR`):
  2018 16,972 · 2019 16,797 · 2020 15,785 · 2021 15,620 · 2022 15,535 ·
  2023 14,808 · 2024 14,917 (a smooth ~12% decline; corrected against CDC
  WONDER on 2026-08-07 — see §3).
- Hawaii Medicaid adult expansion threshold **138% FPL** — one of the
  Med-QUEST pathways feeding the clause-1 `medicaid_receives` test. The
  default `income_fpl_cap` is **3.00** (statutory clause 2), not 1.38.
- Modeling cash as **non-taxable resource** (added to SPM resources,
  not money income) matches Flint program structure and is the
  Census-recommended treatment for charitable cash disbursements
  (Census P60-280 §III).

### Updated literature review (data collected 2026-06)

A second, more thorough pass through the RxKids evidence base
(`rxkids.org/about`, `/impact`, `/dashboard`, `/research/publications`):

**Program design (validates the model's parameters):**
- Payments: **$1,500 prenatal + $500/mo for 6 *or* 12 months** — the
  duration is set per community by funds raised. (Confirms our payment
  amounts and the modeled 6-vs-12-month option.)
- **Universal, residency-based, no income test** in Michigan. (Confirms
  Michigan offers no income-eligibility-grain precedent — the Hawaiʻi
  income test is the bill's own design.)

**Take-up (empirical anchor for `takeup_rate`):**
- **98% of eligible newborns** enrolled (Jan 2024–Sept 2025); **>90% of
  mothers enroll prenatally** — "far exceeds WIC and SNAP." This is the
  direct evidence for the delivery-channel anchoring (§2) and shows the
  0.90 default is *conservative* vs Flint. It also distinguishes the arms:
  **postnatal ~0.98, prenatal ~0.90** (a possible model refinement).
- Dashboard (May 2026): **$41.77M disbursed, 11,751 families, 8,936 births**
  (~$3,553/family *to date* — a mid-stream snapshot, not the lifetime
  per-birth entitlement, so consistent with our ~$4,500 6-month per-birth).

**Fertility response (sources the +10% scenario):**
- **"Births rise nearly 10% following launch"** — *Rx Kids Flint Birth
  Report (2026)*. This is the basis for `--fertility-response 0.10`.
  **Caveat:** Flint's rise may blend a conception response with in-migration
  of pregnant residents into eligible areas; Hawaiʻi (island geography)
  would see far less migration, so the transferable fertility effect is
  uncertain — keep it an explicit, off-by-default scenario.

**Health/economic outcomes (enable a gross-vs-net framing):**
- *Lancet Public Health (2026)*, Richterman & Thirumurthy: **18% fewer
  preterm births, 27% fewer low-birthweight births.**
- *JAMA Network Open (2025)*, Hanna et al.: improved adequate prenatal care.
- *JAMA Pediatrics (2026)*, Agarwal et al.: ~**32% fewer infant-maltreatment
  investigations.**
- *Upjohn Institute (2025)*, Bartik et al.: **$0.60–$3.00 returned per $1**
  to the state economy.
- These are **offsets/benefits, not cost inputs** — the gross program cost
  (~$32M benefit / ~$35M appropriation, statutory eligibility) is partly offset
  by downstream health savings and economic return. A Hawaiʻi-scaled net-cost
  estimate is a natural extension (not yet built).

Cached research notes: `/tmp/rxkids-research.md` (session-scoped).

## 9. Verification

Tests at `tests/tax_modeler/programs/test_rxkids.py` cover:

- Clause 2: income ≤ 300% FPL eligible; > 300% (no Medicaid) excluded
- Clause 1: Medicaid receipt qualifies a unit above 300% FPL (proves OR)
- Birth-driven arms: a married (MFJ) family with a child draws a prenatal
  payment (previously excluded); a childless filer draws nothing
- Prenatal arm = exactly half the postnatal arm (per-birth payment ratio)
- Medicaid pregnancy pathway (196% FPL) binds the prenatal arm when the
  income cap is set below 196%
- Family-grain income test (household income/size, not per tax unit)
- Missing `medicaid_receives` column ⇒ clause-2-only fallback + warning
- High-income units excluded
- Postnatal amount scales linearly with `num_dependents`
- Non-taxable treatment: SPM resources increase by exactly
  `rxkids_amount`, while `total_cash_income` is unchanged
- Monotone take-up: higher `takeup_rate` ⇒ proportionally larger amount
- HoH baseline poverty rate strictly decreases under `rxkids_hi`
  scenario on calibrated synthetic cohort
- At least some persons lifted under realistic synthetic frames
- Backwards compatibility: runs without `rxkids_amount` column produce
  identical baselines and credit-removal results
- Override validation: unknown parameter keys raise `ConfigError`
- Range validation: out-of-range `takeup_rate` raises `ConfigError`
- Factory parity: `hawaii_rxkids_parameters()` matches documented defaults

Full suite: `.venv/bin/python -m pytest tests/tax_modeler/programs/ -v`

Integration run:
```bash
.venv/bin/python scripts/poverty_impact_report.py \
    --tax-year 2024 --apply-snap --apply-credit-takeup --apply-moop \
    --apply-housing-subsidy --apply-childcare-subsidy \
    --apply-spm-expenses --apply-rxkids \
    --scenarios rxkids_hi,no_eitc,no_ctc,hi_ctc_650 \
    --pums-data-dir packages/data/raw/pums \
    --out reports/rxkids_impact_2024/
```

Output CSVs (5):
`by_state.csv`, `by_county.csv`, `by_house_district.csv`,
`by_senate_district.csv`, `by_household_type.csv`.

Key columns on `by_state.csv` for RxKids analysis:
- `persons_lifted_rxkids_hi` — state total lift
- `persons_lifted_rxkids_hi_hoh` — HoH (single-mother proxy) lift
- `poverty_rate_hoh_baseline`, `poverty_rate_rxkids_hi_hoh`

## 10. Cost of implementation (2028)

`forecast_rxkids_2028.py` (repo root) estimates the **annual fiscal
outlay** of the program — distinct from the poverty *lift* scored by
`poverty_impact_report.py`.

### Cost identity

Program cost = the weighted sum of the expected RxKids benefit:

```
cost = Σ_SPM  rxkids_amount × weight
```

at **SPM-unit grain**, using the **WGTP-derived household weight** — the
correct fiscal weight. (The tax-unit `weight` is edited by the EITC
by-children reweight lever in the revenue path; the SPM-grain weight from
`aggregate_to_spm_units` is independent of that and is the right basis for
a fiscal total.) The prenatal/postnatal subtotals
(`rxkids_prenatal_amount`, `rxkids_postnatal_amount`) are summed the same
way and reported separately.

### Forward projection

1. Load PUMS with replicate weights; attach SPM unit IDs.
2. `project_tax_units_forward(target_year=2028)` ages incomes (WGTP weights
   carried forward unchanged).
3. FPL thresholds are taken at `tax_year=2028` (§7 caveat 4), so income and
   thresholds are on one coherent basis.
4. Compute `medicaid_receives` (clause 1) then the RxKids benefit, and
   aggregate to SPM units.

### Headline (real PUMS, TY2028)

This headline is the **statutory 6-month** design (the first scenario in the
§10 scenario set below); the two universal designs follow.

| | Value |
|---|---|
| **Steady-state annual cost (benefit)** | **~$32M** (prenatal ~$10M + postnatal ~$22M) |
| Administrative load (8%) | +~$2.6M |
| **Appropriation total (benefit + admin)** | **~$35M** |
| Sampling 90% CI | ~$29M–$36M |
| **Assumption band (joint corners)** | **~$19M–$41M** |
| **Births served / year (people affected)** | **7,969** |
| Recipient-payments / year | ~13,771 (≈6,598 prenatal + 7,172 postnatal; ~2 per birth) |
| Avg benefit per recipient | ~$2,280 |
| First fiscal year (launch, 12-mo ramp) | ~$13M (41% of steady) |
| Optional +6-month postnatal (statutory) | +~$22M (12-month-design total ~$54M) |

> **Methodology revision (2026-06).** The birth driver changed from the
> ``num_dependents × child_under_age_share`` **proxy** to **observed** age-0
> dependents (PUMS), calibrated to CDC NVSR resident births and projected to
> 2028 with the repo's damped-trend ensemble. This roughly halves the headline
> (proxy ~$55M → observed ~$32M). Decomposition: the proxy multiplied an
> *all-dependents* count (~410k weighted, incl. older children, students, adult
> dependents) by a rate calibrated against a *children-only* denominator
> (~233k), overstating total births ~1.7× and — because dependents skew toward
> lower-income families — inflating the eligible-birth share. Switching to
> observed births at the base-year level gives **~$37M** (−33% from the proxy);
> the 2028 birth-cohort projection (×0.909 vital-stats trend, see below) takes
> it to **~$31M**. The admin line is separate (the headline is benefit dollars).

> **Baseline refresh (2026-06-22).** All figures in this doc were regenerated on
> the current `tax_modeler` pipeline. The statutory headline moved from the
> previously-published ~$29M to **~$32M** (recipients ~12,700 → ~14,100, eligible
> share ~54% → ~60%) — this is **not** a parameter or methodology change here but
> a consequence of upstream pipeline updates pulled into the package (tax-unit
> construction, dependent assignment, income projection, Medicaid/credits). The
> birth *target* (13,632) is unchanged; the raw PUMS-observed age-0 count fell
> 15,419 → 14,176, so the calibration now scales up more (×0.962 vs ×0.884). The
> same refresh also added the universal scenario set (above).

> **Birth-series refresh (2026-08-06).** Taken alone, the data refresh moved the
> statutory headline **~$32M → ~$33M** (before the weighting fix below, which
> then took it to ~$31M). This *is* a data change, not
> a parameter change: the CDC series gained its **2024 final** (NVSR 75-02,
> 14,917 — a recovery from the 2023 dip the old series ended on), and Hawaiʻi
> **DOH preliminary counts now nowcast 2025–26**, closing the ~18-month NVSR
> publication hole. The 2028 birth target rose **13,632 → 14,127 (+3.6%)** and
> its 90% PI narrowed by **66%** (width 6,258 → 2,112). Full audit, including
> the time-varying occurrence→residence correction, in §3.
>
> **Birth weighting fix (same date).** Tracing the unexplained drift in the raw
> PUMS age-0 count (the 14,176 recorded above) found a genuine bug: infants were
> weighted by the tax unit's DOTAX-calibrated hybrid weight instead of their own
> `PWGTP`. Because that calibration up-weights HoH units (×1.30) — i.e.
> lower-income single parents — it inflated the **eligible share ~60% → ~56%**
> and the statutory headline **$33.2M → $31.3M (−5.7%)**, while the *universal*
> design rose slightly. Full decomposition in §3 ("Birth weighting basis").
> Net of both changes on the same day, the statutory headline moves
> **~$32M → ~$31M**.

> **County-split fix (2026-08-07).** `cost_by_county` was driven by ACS PUMS's
> own county allocation of the birth total — unreliable for two reasons (§3,
> "County split"): small samples (Hawaii County n=11 infants) and Maui/Kauai
> not being independently sampled at all (combined PUMA, split only by a
> demographic-heuristic imputer). Recalibrated to Hawaiʻi DOH's aggregate
> county vital-statistics shares instead. This is **not** just a
> `cost_by_county` cosmetic fix — eligibility is unit-specific and correlated
> with county (Hawaiʻi County runs poorer than Honolulu), so correcting the
> allocation moved the **state** total too: statutory **$31.3M → $32.0M
> (+2.1%)**, recipients **13,728 → 14,011 (+2.1%)**, with `eligible_families`
> exactly unchanged at 7,340 (confirming the mechanism is reallocation onto
> already-eligible units, not a change in who qualifies). `--no-doh-county-shares`
> restores the raw PUMS split.

> **Headline design change (2026-08-10).** The default scenario is now
> **`statutory_3mo`**: $1,500 one-time prenatal + **3** × $500 monthly =
> **$3,000 per birth**, down from the 6-month/$4,500 design. Cost falls
> **$31.4M → $20.7M (−34%)** with **no change in reach** — births served is
> 7,969 either way, because the same families qualify and enrol and only the
> number of instalments differs. Average benefit per family drops $4,280 →
> $2,814. The old 6-month design remains priced as `statutory_6mo`.
>
> The reach metric also changed: **"people affected" now means BIRTHS SERVED**,
> reported first in the console, workbook, PDF and both CSVs. It had been easy
> to quote *recipient-payments* (13,771) as people, which double-counts —
> each served birth draws one prenatal and one postnatal payment. See
> "Payment design and what people affected means" in §0.

> **Series correction + projector switch (2026-08-07, later same day).** An
> audit of the projection mechanism found the **NVSR series itself was wrong**
> in four of seven years (2018/2019 by ~9%), corrected against CDC WONDER — and
> with it went the "pre-COVID birth-tourism regime" this document had used to
> justify a post-2020-only ratio calibration. That regime was an artifact of the
> bad data; the true occurrence/residence ratio is ~1.003 in every year. The
> same audit back-tested the projector for the first time and found the trend
> ensemble's 90% interval covering the truth in only **50%** of folds, so the
> default moved to the **Kalman filter** (MAPE 3.28% → 2.34%, bias −2.5% →
> +0.6%). County shares also moved from DOH *occurrence* to CDC WONDER
> *residence*, the basis eligibility actually turns on.
>
> Net effect on the statutory headline: **~$32.0M → ~$32.1M** — i.e. almost
> nothing. That is the honest summary: the point estimate was roughly right by
> luck, while the interval around it was too narrow by half and the county
> detail was on the wrong basis. Full detail in §3.

> **Calibration round (2026-08-07, third pass).** The series was extended to
> the full CDC WONDER window (2007–2024, 18 finals), giving the walk-forward
> back-test **46 folds** — enough to calibrate, not just gate. It measured a
> systematic **+2.0% high bias** in the Kalman point (the damping
> under-extrapolates this persistently-declining series) and an over-covering
> analytical interval (97.8%). Both are now corrected in production
> (`BIRTH_KALMAN_LOG_BIAS`, `BIRTH_KALMAN_SE_KAPPA`; bias first, κ on
> corrected residuals — the repo-canonical order). The 2028 cohort moved
> **14,126 → 13,842** and the statutory headline **~$32.1M → ~$31.4M**. A
> latent crash in `kalman/project.py`'s default `end_year` (float into
> `range()`) was also fixed, with a follow-up queued to re-harmonize the
> Housing-Affordability-Tracker cherry-pick. Full detail in §3 and
> `backtests/results/birth_projection_2026-08-07.md`.

Take-up is **arm-specific** (see §2): postnatal/newborn **0.90**, prenatal
**0.83** (0.92 × postnatal, from Flint's ~90% prenatal vs ~98% newborn). So
postnatal is a bit *more* than 2× prenatal — the 2× per-birth payment ratio
($3,000 vs $1,500) plus the lower prenatal take-up. (Single-rate runs, e.g.
the library default, keep both arms equal → prenatal exactly half.)

Eligibility is tested on the **MAGI household ≈ the tax unit** (filer +
spouse + tax dependents, the family concept Medicaid uses — 42 CFR 435.603),
on a **MAGI income proxy** (100% of Social Security, per Medicaid/ACA rules).
Because the statute anchors eligibility to Medicaid (clause 1), the 300% FPL
test (clause 2) uses the same MAGI-household grain — NOT the broader SPM
resource-sharing family (which would pool cohabiting partners / separately-
filing relatives whose income MAGI excludes) and NOT the physical household
(which would count unrelated roommates). See §10.

#### Scenario set — eligibility × postnatal duration

`forecast_rxkids_2028.py` prices **three policy designs in one pass** (override
with `--scenarios`). All hold take-up at the run's default (0.90 newborn / 0.83
prenatal) and use the same projected births, so the cost differences isolate the
two policy levers — the **eligibility gate** and the **postnatal duration**:

| Scenario (`key`) | Eligibility | Postnatal | $/birth | Cash cost | Appropriation | Recipients/yr | Assumption band |
|---|---|---|---|---|---|---|---|
| **`statutory_3mo` (default)** | Medicaid OR ≤300% FPL | 3 mo | **$3,000** | **~$21M** | ~$22M | 7,969 births | ~$12M–$26M |
| `statutory_6mo` | Medicaid OR ≤300% FPL | 6 mo | $4,500 | ~$31M | ~$34M | 7,969 births | ~$19M–$40M |
| `universal_6mo` | universal (no test) | 6 mo | $4,500 | **~$52M** | ~$56M | ~22,900 | ~$31M–$66M |
| `universal_12mo` | universal (no test) | 12 mo | $7,500 | **~$88M** | ~$95M | ~22,900 | ~$53M–$112M |

- **Universal eligibility** (`income_fpl_cap=100.0`, ≈ no income/Medicaid test)
  reaches essentially every birth family — 13,228 births served vs 7,969 under
  the statutory gate. Going universal at 6 months adds **~$18M** in cash cost
  (it pulls in the ~7,700 birth families above the statutory gate).
- **The extra 6 months** (`postnatal_months=12`) doubles the postnatal arm and
  leaves the prenatal arm and the recipient count unchanged — it pays the **same**
  universal families for twice as long, adding **~$34M** on top of universal·6mo.
- Per-county splits for all three scenarios are written to
  `cost_by_county_scenarios.csv` (and the workbook's *County × scenario* tab);
  the state comparison is `cost_by_scenario.csv` (and the *By scenario* tab).

#### Sensitivity: take-up + behavioral fertility (statutory design)

The statutory headline is the **conservative** default (90% take-up, no
behavioral response). Two Flint-observed assumptions raise it:

| Sensitivity | Take-up (post/pre) | Fertility | Steady-state cost | Band |
|---|---|---|---|---|
| Conservative (default) | 0.90 / 0.83 | — | **~$31M** | ~$19–40M |
| **Flint-equivalent** | 0.98 / 0.90 | +10% | **~$38M** | ~$23–48M |

`--takeup-rate 0.98 --fertility-response 0.10` produces the Flint scenario —
which sets postnatal take-up to 0.98 and prenatal to 0.98 × 0.92 ≈ 0.90,
recovering Flint's *observed* arm rates exactly. The **fertility response**
(`_apply_fertility`) models the ~10% post-launch birth rise documented in the
*Rx Kids Flint Birth Report (2026)* as a uniform +10% on eligible births
(×1.10 on both arms): ~$31M × (0.98/0.90 take-up lift) × 1.10 ≈ $38M.
It is a real upside risk a static model would miss, but **off by default**:
Flint's rise may blend a conception response with in-migration of pregnant
residents into eligible areas, and Hawaiʻi's island geography would see far
less migration — so the transferable effect is uncertain. (These take-up /
fertility levers compose with any of the three scenario designs above.)

### Eligible base vs recipients

"Eligible families" (~7,340 weighted on the observed basis — families with an
**observed birth** clearing the income/Medicaid test) is NOT the recipient
count, and neither is **births served** (7,969 — the headline reach figure).
Actual **recipient-payments** (prenatal + postnatal) are ~13,771/yr,
recovered by dividing each arm's expected-dollar column by its full
per-recipient payment ($1,500 prenatal, $3,000 postnatal). Report recipients,
not the eligible base. (Under the legacy proxy the eligible base was ~100k —
every family with *any* dependent contributed fractional births; the observed
basis correctly restricts it to families with an actual infant.)

### Coherent arms (no birth-anchor needed)

Both arms are driven by the same birth events (§3), so prenatal expected
pregnancies = postnatal eligible births by construction — each eligible
birth draws one $1,500 prenatal + one $3,000 postnatal payment. (An earlier
model used a flat pregnancy probability over a single/HoH-no-dependent
universe, which overcounted pregnancies ~2× and required a runtime
"birth-anchor" rescale; the unified birth-driven arms remove that machinery.)

### Eligible-birth cross-check

On the **observed** basis the model implies **~7,969 eligible births** (=
~7,172 infant recipients ÷ 0.90 take-up) out of the ~13,842 projected 2028
births = **~58% of births eligible**, on the MAGI-household grain (eligible
*families*, a related but different denominator, is 7,340).

- **Real clause-1-vs-clause-2 split (added 2026-08-18).** Earlier versions of
  this section estimated the Medicaid-vs-income split indirectly, by
  anchoring to KFF's *national* Medicaid-financed-birth rate (~40%) and
  backing out a residual — explicitly flagged at the time as an external,
  non-model-reported approximation ("treat the split as ±a few points, not
  exact"). `forecast_rxkids_2028.py` now computes this directly from the
  actual PUMS microdata instead: for every eligible family it checks whether
  `medicaid_receives` (Hawaiʻi Med-QUEST categorical eligibility, computed on
  that family's real income/family size — §3) alone would have qualified it,
  versus families that clear *only* the 300% FPL income test. Real result, by
  scenario (`cost_by_scenario.csv`, columns `eligible_families_medicaid` /
  `eligible_families_income_only` / `births_served_medicaid` /
  `births_served_income_only` / `cost_medicaid_$` / `cost_income_only_$`):

  | Scenario | Eligible families | via Medicaid | via income-only | Births served | via Medicaid | via income-only |
  |---|---|---|---|---|---|---|
  | **Statutory · 3mo (default)** | 7,340 | **7,340 (100%)** | **0 (0%)** | 7,969 | **7,969 (100%)** | **0 (0%)** |
  | Statutory · 6mo | 7,340 | 7,340 (100%) | 0 (0%) | 7,969 | 7,969 (100%) | 0 (0%) |
  | Universal · 6mo | 11,521 | 7,340 (63.7%) | 4,181 (36.3%) | 13,228 | 7,969 (60.2%) | 5,259 (39.8%) |
  | Universal · 12mo | 11,521 | 7,340 (63.7%) | 4,181 (36.3%) | 13,228 | 7,969 (60.2%) | 5,259 (39.8%) |

  **The statutory (default) result is not an estimate — it is a structural
  fact of the current parameter configuration, not sampling noise.** Hawaiʻi's
  Medicaid children/CHIP pathway caps at **313% FPL**
  (`MedicaidParameters.child_fpl_cap`), which is *wider* than RxKids's own
  statutory income test at **300% FPL** (`RxKidsHIParams.income_fpl_cap`).
  Every birth-driving family has ≥1 dependent (a birth is itself a
  dependent), so the children pathway fires for any family at or below 313%
  FPL with a dependent — a strict superset of the families the 300% income
  clause reaches. Both tests run on the exact same family-size/income basis
  (`_magi`/`_magi_size`, §3), so **any family that clears the statutory 300%
  income test is, by construction, already under 313% FPL and therefore
  already `medicaid_receives = True`.** At the statutory default, clause 2
  currently contributes **zero** additional eligible births beyond clause 1 —
  it only starts doing independent work once the income cap is pushed toward
  universal (the Universal rows above use a ~100× FPL cap, which reaches
  families well outside any Medicaid pathway). A regression test
  (`test_rxkids_statutory_default_is_entirely_medicaid_clause` in
  `tests/tax_modeler/programs/test_rxkids.py`) pins this; re-verify it if
  either FPL cap is ever changed independently of the other.

  This retires the "roughly two-thirds Medicaid" framing this document used
  previously. The real number for the statutory design is both higher (100%,
  not ~67%) and exact rather than approximate — and it is a stronger, simpler
  advocacy line than the estimate it replaces: under the current design,
  **every dollar of the statutory program flows to families the state already
  recognizes as Medicaid-eligible.** The 300% income test isn't currently
  reaching a new population — it's a second, currently-redundant door into
  the same population Med-QUEST already serves. (For the income test to
  meaningfully broaden reach beyond Medicaid, `income_fpl_cap` would need to
  be set *above* 313% FPL, which at its current 300% default it narrowly does
  not.)
- **Why ~58% (vs the proxy's inflated 86%)?** The proxy distributed
  *fractional* births across **all** families with dependents, in proportion
  to dependent count — and larger-dependent families skew lower-income, so the
  proxy over-weighted low-income families and pushed the eligible share to 86%
  (above the ~60–65% Census-family benchmark). The **observed** births
  are the actual joint distribution of new infants × MAGI income; ~58% of them
  clear the gate — right in the ~60–65% Census-family benchmark range. For a
  high-cost, high-median-income state where 300% FPL for a family of 3 is ~$85k,
  ~58% of birth families qualifying is plausible. The shift from 86% to 58% is
  the largest *qualitative* correction from the observed-births change.

### Launch (first fiscal year)

`_first_year_disbursement` models year-1 cash, which is far below steady
state because (a) enrollment ramps from zero to full over `--ramp-months`
(default 12) and (b) the 6-month postnatal caseload takes 6 months to fill.
A monthly simulation sums prenatal payments scaled by enrollment and
postnatal payments scaled by the trailing-window caseload; postnatal
dollars that pay out past the fiscal year are reported as **deferred to the
next FY** (not lost). `--launch-operating-months` < 12 models a mid-year
launch. Ramp speed is the dominant year-1 driver (6/12/18-month sensitivity
is reported).

### Uncertainty

- **Sampling 90% CI** — SDR (Fay method, factor 4.0) over the 80 PUMS
  household replicate weights `weight_r01..weight_r80`, reusing
  `poverty.impact._sdr_se_from_replicates`. Captures ACS sampling error
  only (~±5%) and badly understates total uncertainty.
- **Assumption band (joint corners)** — the all-low and all-high corners of
  `takeup_rate` (swept around the assumed central value) and the **birth
  count** (±25%). In observed mode the ±25% multiplier is applied to the
  `observed_births` column directly (the `child_under_age_share` parameter is
  inert); the ±25% band also comfortably covers the ensemble birth-projection's
  ~±23% 90% PI. Cost is monotone in each, so the joint corners give the true
  outer envelope.
- **Overall MOE — treat as ±25–35%.** The estimate is dominated by
  **specification** uncertainty, not sampling. The birth driver is now
  observed-and-calibrated (a real improvement over the proxy), but take-up
  still has no admin caseload to calibrate against and the birth-cohort
  projection carries a wide PI, so present the headline as a point estimate
  **with the band always beside it**, never as a ±5% figure.

### Caveats specific to the 2028 run

- Federal EITC/CTC parameter tables fall back to TY2025 for years > 2025.
  **Immaterial here** — RxKids is non-taxable and never touches AGI /
  EITC / CTC.
- **Eligibility unit = MAGI household ≈ tax unit.** Medicaid/ACA define the
  household by tax-filing relationships (42 CFR 435.603) — filer + spouse +
  tax dependents — and the statute anchors clause 1 to Medicaid, so the 300%
  FPL test (clause 2) uses the same grain. Each tax unit is tested on its own
  MAGI at its own size. We do **not** pool to the SPM resource-sharing family
  (which would count cohabiting-partner / extended-family income that MAGI
  excludes — over-pooling that understates eligibility ~15%) nor to the
  physical household (which would count unrelated roommates). The income is a
  **MAGI proxy** (`_magi_proxy`): gross income with 100% of Social Security
  counted (the model's `income` counts only the 85% taxable portion), per
  Medicaid/ACA rules. Above-the-line deductions and tax-exempt interest are
  not observable in PUMS, so MAGI ≈ gross + SS add-back (a standard survey-
  based proxy). Residual: PUMS tax units approximate but do not perfectly
  match the MAGI-household composition rules; if the bill defines "family"
  more broadly than MAGI, the SPM-family (resource-sharing) grain is the
  alternative — a material (~15–20%) lever on the headline, not separately
  re-run on the current pipeline.
- The birth cohort is **projected to 2028** with the package's damped-trend
  ensemble off the CDC NVSR resident-birth series (`HI_BIRTHS_BY_YEAR`), so the
  demographic driver is aged coherently with income (×0.891 vital-stats trend;
  the single scalar applied to `observed_births` is ×1.085 once the PUMS→vital
  base correction is folded in — see §3). Update `HI_BIRTHS_BY_YEAR` as new NVSR
  final-data releases land; pass `--no-birth-projection` to hold the cohort at
  the base-year level.
- Program cost is **benefit dollars**; an 8% administrative load (override
  `--admin-load`) is reported as a separate appropriation line. Cash-transfer
  admin loads typically run 5–10%.
- The 2026–2028 FPL inflator is a CPI projection, not a published table.

### Outputs (`reports/rxkids_2028/`)

All `cost_by_state.csv` / `cost_by_county.csv` / quintile / Summary outputs are
the **statutory 6-month** headline (backward-compatible). The scenario panel
adds two cross-scenario files:

- `cost_by_state.csv` — statutory benefit cost, CI, admin load + appropriation
  total, birth driver + calibrated/target births, potential +6mo, launch year,
  recipients.
- `cost_by_county.csv` — per-county statutory cost + expected recipients.
- **`cost_by_scenario.csv`** — state-level comparison of all priced scenarios
  (statutory_3mo / statutory_6mo / universal_6mo / universal_12mo): cost, arms, CI, assumption
  band, admin, appropriation, recipients, avg benefit, first-year.
- **`cost_by_county_scenarios.csv`** — per-county cost + recipients for **every**
  scenario (tidy long format: one row per county × scenario).
- `benefits_by_income_quintile.csv` — benefit received by income quintile
  (statutory).
- `rxkids_2028_cost_and_impact.xlsx` — workbook (Summary / quintile / county /
  **By scenario** / **County × scenario** / assumptions / notes).
- `rxkids_2028_cost_and_impact.pdf` — one-page shareable summary (now includes a
  scenario-comparison table).
- `spm_units.parquet` — SPM-grain frame (statutory).

Run:
```bash
.venv/bin/python forecast_rxkids_2028.py \
    --tax-year 2028 \
    --pums-data-dir packages/data/raw/pums \
    --out reports/rxkids_2028/
```
