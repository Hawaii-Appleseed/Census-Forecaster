# RETITC Report — How the Pipeline Works

A plain-English walkthrough of every step, from raw data to the numbers on the page.

---

## What the report is about

Hawaii's §235-12.5 **Renewable Energy Technologies Income Tax Credit (RETITC)** lets residents and businesses claim a tax credit for solar, wind, and other clean-energy installations. In Tax Year 2023 the state gave up roughly **$100M** in revenue through this credit.

SB 3125 — signed **May 21, 2026** and now **Act 24, SLH 2026** — makes three changes: a **$40M annual cap** on new certifications, an **income (AGI) limit** that cuts off high earners, and a **sunset date** that ends new credits after a few years. The report estimates how much the state saves, who bears the burden, and how uncertain those estimates are.

> **What is retroactive.** Act 24 §9(1) makes §1 retroactive to TY2026 —
> *except* the §235-12.5(a) amendments, which start TY2027. So for TY2026 only
> the **$40M cap + HSEO certification + sunset** apply retroactively; the
> **AGI limit does not** (DOTAX Announcement 2026-06: "effective for tax year
> 2027"). The model encodes this as `REEC_AGI_LIMIT_FIRST_YEAR = 2027`.
>
> **Executive Order 26-02 — signed June 8, 2026** (announced June 12). Note the
> two dates: **June 8 is the signing; May 21, 2026 is the eligibility cutoff
> written into the EO.** A CY2026 system is exempt **from the $40M aggregate cap
> — and nothing else** — if completed before May 21, 2026, or if the taxpayer
> shows HSEO and DOTAX it **"reasonably relied"** on the credit when investing
> resources before that date. The 35% rate, per-system caps, certification, and
> the 2030 phase-out all still apply. **DOTAX TIR 2026-02 (July 31, 2026)**
> defines "investment of resources" as a payment made or cost incurred before
> 5/21/2026 (Treas. Reg. §1.461-1(a)(1)–(2)); reliance is then *presumed*.
>
> **Pipeline effect: none for the default model.** Step 6's carryforward
> simulation already treats the TY2026 vintage as uncapped under
> `interpretation="B"`. Note though that TIR 2026-02 describes the cap as
> operating on *claims made in 2027 for systems placed in service in 2026* —
> **interpretation A semantics** — with the EO then carving most of that cohort
> back out. Effective treatment is A-with-a-large-carve-out, close to B for the
> grandfathered majority. `interpretation="A"` (LOW scenario) is now
> over-optimistic on State savings. See the "Executive Order 26-02" section of
> `SB3125_CD1_FORECAST.md`.
>
> ⚠️ **No official estimate of the grandfathered credit dollars exists** — the
> cap was added in conference committee with no fiscal note, and no post-EO COR
> re-estimate has been published (the May 21, 2026 GF forecast predates the EO
> and omits Act 24; **September 3, 2026** is the first meeting to take it up).
> The public **$436M** figure (HSEA, 265 commercial projects) is *project cost,
> not credit value* — **do not convert it at 35%**; per-system caps put the
> blended effective rate nearer **17–25%**. A top-down estimate anchored on
> normal program scale (~$100M/yr) puts the grandfathered pool at **≈$85M
> (range $65M–$100M)**, i.e. **≈$45M–$60M more than a strict cap would have
> allowed**, one-time in FY2027–28. That figure is *derived, not sourced*.

---

## Step 1 — Start from real DOTAX data

The model anchors everything to **Tax Year 2023 actuals** published by the Hawaii Department of Taxation (DOTAX):

| Claimant type | TY2023 claims |
|---|---|
| Individual / residential | $58.3M |
| Corporate / commercial | $38.6M |
| Other (trusts, exempt orgs) | $3.2M |
| **Total** | **$100.1M** |

Individual claims are further broken out by **AGI bracket** (six bins from <$10K to $200K+), which is what makes the income-distribution analysis on page 5 possible.

---

## Step 2 — Project baseline demand forward

Without the bill, how much would the RETITC cost the state in future years?

The model grows TY2023 claims forward separately for each claimant type:

**Individual/residential claims** grow with:
- **Hawaii nominal income growth** — see Step 2A below for a full explanation of how this is forecasted.
- **OBBBA demand-decay factor** — see Step 3 below.

**Corporate/commercial claims** grow with:
- **Hawaii business-sector growth** — a fixed 3%/yr assumption (calibrated downward from GDP; Hawaii business investment grows slower than the macro aggregate).
- **§48E taper** — the federal commercial solar credit (§48E) expires 12/31/2027. After that, commercial solar project financing gets more expensive and demand falls 15–28% over the following years.

---

## Step 2A — How income growth is forecasted

This is the engine underneath all the individual-side RETITC projections. The model needs to know: if Hawaiians claimed $58M in RETITC in TY2023, how much would they claim in TY2027 if nothing changed — just because incomes grew and more people could afford solar?

The answer comes from projecting **Hawaii median household income** forward using a multi-method statistical ensemble built on Census Bureau survey data.

### The raw data: ACS B19013

The Census Bureau publishes annual surveys of household income at the county level through the **American Community Survey (ACS)**. The specific number used here is called **B19013** — median household income. For Hawaii, the model uses Honolulu County (which holds 70% of the state's population) as the proxy, because:

- You can't correctly average medians across counties (medians don't add up that way mathematically)
- Honolulu has the largest sample size and therefore the most reliable estimates
- Its income trends are representative of the state overall

The ACS panel bundled in this repo has B19013 observations for Honolulu going back to roughly 2010, each with an associated margin of error (the range within which the true number probably falls, published by the Census Bureau alongside every estimate).

### The ensemble: three forecasting methods combined

Rather than relying on a single method, the model runs two independent forecasting approaches and blends them. This is standard practice in forecasting — no single model is best in all situations, and their errors tend to partially cancel when combined.

**Method 1 — Damped trend**

This is exponential smoothing: the model gives more weight to recent observations than older ones, extrapolates the trend forward, but applies a "damping factor" (φ, phi) that gradually pulls the trend toward zero. Without damping, a trend that was strong in 2022 would be projected forward indefinitely into the future, which is almost never realistic. The damping factor used here (φ = 0.85 per year for annual series) means that by five years out, the trend has faded to about 44% of its original strength (0.85⁵ ≈ 0.44).

Think of it like a ship slowing down after the engine is cut — it keeps moving in the same direction but gradually decelerates.

**Method 2 — Multi-anchor**

This method anchors the income forecast to external macroeconomic indicators that are published more frequently than the ACS and may have more predictive power. For Hawaii, the anchors include:

- BEA per-capita personal income (published quarterly by the Bureau of Economic Analysis)
- Honolulu metro regional price parity (BEA)
- QCEW wages (BLS Quarterly Census of Employment and Wages — what employers actually pay)
- Honolulu CPI (consumer price inflation)
- National PCE deflator (a broader measure of price changes)

Each anchor is given a weight based on how well it has historically predicted ACS income. BEA per-capita income gets the highest weight (~25%) for Hawaii, followed by QCEW wages (~23%), CPI (~19%), PCE (~19%), and metro RPP (~13%).

**Combining the two methods**

The two forecasts are blended using a technique called **Bates-Granger combination**: each method's forecast is weighted inversely to its uncertainty — the more uncertain a method's prediction, the less weight it gets in the final number. The result is a single point estimate (the blended forecast) plus a combined uncertainty range.

### Calibration: making sure the uncertainty ranges are honest

A forecast is only useful if its stated uncertainty is accurate. If the model says "we're 90% confident income will be between X and Y," that range should actually contain the true value 90% of the time — not 60%, not 99%.

The model was back-tested on a panel of 147 U.S. counties across 15 years. For each county and year, the model was trained on historical data up to some cutoff, then its forecast was compared to what actually happened. Two corrections were applied:

1. **Geometric bias correction**: The model systematically under-predicted during the post-COVID inflation surge (2020–2022) because nobody's historical data predicted that spike. The correction shifts dollar-series forecasts up ~7–9% to account for this.

2. **SE inflation (κ factor)**: The model's built-in uncertainty ranges were too narrow — they said 90% but only hit ~80% empirically. A per-cell multiplier κ is calibrated (separately for small vs. large counties, and for short vs. long forecast horizons) until the stated 90% confidence interval actually contains the true value 90% of the time in backtesting.

The result of calibration: for median income (B19013), the calibrated forecast achieves about 90.5% empirical coverage on held-out test years.

### From real growth to nominal growth

The ACS ensemble produces a **real** forecast — that is, inflation-adjusted (purchasing-power-adjusted). But RETITC claims are reported in **nominal** dollars (today's dollars, not adjusted for inflation). A $100 credit claimed in 2027 is worth $100, period.

So the model converts real growth to nominal:

```
nominal growth factor = real growth factor × CPI ratio
```

The CPI used is the **Honolulu Urban All-Items CPI** (BLS series CUURS49ASA0), a bimonthly series covering Hawaii's cost of living. Past the most recently published data point, it is projected forward with the same damped-trend approach, converging toward a long-run rate of ~2.5%/yr.

The final number — say, 1.12 for TY2027 — means that nominal incomes are expected to be 12% higher than in TY2023. The model multiplies TY2023 RETITC claims by this factor to get the TY2027 baseline before any cap or other adjustment.

### Fallback

If the forecasting machinery is unavailable for any reason (missing data, import error), the model falls back to a flat **4%/yr compound growth** assumption. This is documented in the code and visible in log output when it triggers.

### Corporate claims: a simpler approach

Corporate RETITC is not projected with the ACS ensemble. Business investment doesn't track median household income closely — it responds to different economic drivers (interest rates, tax incentives, capital availability). The model uses a flat **3%/yr compound growth** assumption for corporate claims, calibrated downward from Hawaii nominal GDP growth based on observed commercial solar investment trends.

---

## Step 3 — Apply three OBBBA demand scenarios

The federal **One Big Beautiful Budget Act (OBBBA, July 2025)** terminated the federal residential solar credit (§25D) effective 12/31/2025. Without that federal credit, some people who would have installed solar won't — which means fewer RETITC claims even before the bill's cap kicks in.

The model runs three scenarios for how badly residential demand falls:

| Scenario | What it assumes | Key factor |
|---|---|---|
| **Pre-OBBBA** | Federal termination has no effect on Hawaii demand | 1.0× every year |
| **OBBBA Mid** (default) | SEIA forecast, Hawaii-tempered. Hawaii's $0.42/kWh electricity prices mean solar still pencils without the federal credit. 2025 sees a pull-forward (+10%), then a trough in 2026–27 (−15 to −17%), then partial recovery. | 2026: 0.85×, 2027: 0.83× |
| **OBBBA Severe** | SEIA national figures applied directly — assumes Hawaii tracks the mainland more closely. Demand stays depressed. | 2026: 0.81×, 2027: 0.79× |

These factors multiply the projected residential RETITC claims. Corporate/commercial claims use their own §48E taper (separate from the residential §25D scenarios).

---

## Step 4 — Apply the AGI income limit

The bill bars individual filers with income above **$175,000** (single/HoH) or **$350,000** (joint) from claiming the RETITC starting in TY2027.

Using PUMS tax-unit microdata, the model calculates what share of dollars in each AGI bracket fall below the threshold:

- Below $100K → 100% eligible (everyone is under the limit)
- $100K–$200K → ~97% eligible (almost everyone is still under)
- $200K+ → ~56% eligible (many joint filers are under $350K, but most high earners are cut off)

Across all brackets, about **80% of individual RETITC dollars** survive the AGI screen. The other 20% is revenue the state saves by making those filers ineligible.

Corporations are **not subject to the AGI limit** under the default assumption (the statutory language refers to "adjusted gross income," which doesn't straightforwardly apply to corporations).

---

## Step 5 — Apply the $40M cap and pro-rata allocation

For TY2027–2029, total new RETITC certifications are capped at **$40M per year** across all eligible claimants. From TY2030 on, no new credits are certified at all (the sunset).

> **Why 2029 and not 2030.** Act 24 is internally inconsistent here:
> §235-12.5(c)(4)–(5) reads as a cap through CY2030 with $0 "beginning January 1,
> 2031," while §235-12.5(p) sunsets the whole section for taxable years beginning
> after **12/31/2029**. The model resolves this in favor of (p)
> (`REEC_SUNSET_LAST_VINTAGE = 2029`, TY2030+ certifications zero), which is also
> how **DOTAX Announcement 2026-06** resolves it ($0 beginning January 1, 2030).
> Treating TY2030 as a $40M cap year instead materially understates savings —
> that was the May 14, 2026 vintage-model correction, worth ~+$50M. The
> resolution is administratively settled but has not been issued as a TIR.

### How pro-rata works

The cap is administered through the certification process (§235-12.5(h)). *(Agency note: the statute and this pipeline's older text say DBEDT; sources contemporaneous with Act 24 attribute certification to the **Hawaiʻi State Energy Office (HSEO)**. The cap mechanics are identical either way — no number depends on this.)* When aggregate eligible demand in a given year exceeds $40M, every eligible filer receives a **proportional share** of the cap rather than their full credit. The pro-rata factor is simply:

```
pro-rata factor = $40M cap ÷ total eligible demand
```

For example: if projected eligible demand in TY2027 is $80M (individual + corporate combined), the pro-rata factor is 0.50 — every filer gets 50 cents on the dollar. Individual and corporate claims are scaled by the same factor.

The state savings in a cap year is therefore:

```
Savings = total eligible demand − $40M
         (the amount that gets rationed away)
```

Plus any demand excluded entirely by the AGI screen (those filers get nothing, not a pro-rated share).

### Why filers have limited incentive to time their claims

Under §235-12.5(h), the certification is tied to the installation — you can't claim the credit before the system is installed. This means filers can't strategically delay or accelerate their claim to avoid a bad pro-rata year the way they could with a first-come-first-served cap. The expected value of installing is still (credit amount × pro-rata factor), so the rational response for most filers is to install anyway and accept the haircut, not to wait for a better year. This is the key reason the model treats the cap as approximately static (no timing shifts).

### CD2: behavioral demand suppression

CD1 assumes the pro-rata haircut has **no effect on how many systems get installed** — every eligible filer proceeds regardless of the pro-rata factor (elasticity = 0).

CD2 adds a mild behavioral response. If the pro-rata factor is low enough, some marginal projects — the ones where the economics are borderline even with the full credit — will no longer pencil out, and those installations won't happen. The model captures this with a **suppression factor**:

```
suppression factor = pro-rata factor ^ elasticity
```

With CD2's elasticity of 0.3:

| Pro-rata factor | Suppression factor | Meaning |
|---|---|---|
| 1.00 (cap not binding) | 1.00 | No change |
| 0.75 | 0.92 | 8% fewer installations |
| 0.50 | 0.79 | 21% fewer installations |
| 0.40 | 0.72 | 28% fewer installations |

The suppression factor is applied to each filer's nominal demand **before** the pro-rata step. If the suppressed total still exceeds $40M, the remaining excess is rationed pro-rata. If suppression alone brings total demand below $40M, no further pro-rata is needed and every remaining filer gets their full (suppressed) credit.

The net effect in CD2: the cap binds somewhat less tightly because demand has partially self-corrected, which means slightly lower state savings compared to CD1 in cap years — but those savings are arguably more durable (the credit stock available for carryforward is also smaller).

---

## Step 6 — Track the carryforward pool

This is the trickiest part. The RETITC is **nonrefundable for most filers** (about 77% of individual claims, 11% of corporate claims). Nonrefundable credits can only offset actual tax owed — any excess carries forward indefinitely until used up.

This means:

1. Credits earned **before the cap** (TY2010–2026) are sitting in a carryforward pool and will keep getting used **even during the cap years**.
2. The $40M cap only limits **new certifications**, not drawdown of the old stock.
3. So the state's actual revenue cost in 2027–2030 = (capped new credits) + (drawdown of the pre-cap carryforward stock).

The model simulates this pool year by year starting from TY2010, building up the stock from historical actuals, and then tracking how it drains as filers use it. This is why the "RETITC cost — under SB 3125" in the table can be substantially above $40M in early years.

The **utilization rate** (default 65%) is the share of nonrefundable stock that gets used in any given year; the rest stays in the pool for the next year. See Step 6A below for how this number is determined.

---

## Step 6A — How the carryforward utilization rate is determined

This is one of the model's key judgment calls, and it's worth explaining carefully.

### First: refundable vs. nonrefundable (from real data)

Not all RETITC credits are equally immediate in their revenue impact. DOTAX publishes a refundable/nonrefundable breakout directly from TY2023 filings:

| Claimant type | Refundable | Nonrefundable |
|---|---|---|
| Individual | $13.4M (23%) | $44.9M (77%) |
| Corporate + Other | $37.3M (89%) | ~$4.5M (11%) |

**Refundable credits** are paid out in the year they're claimed — the state forgoes that exact amount of revenue immediately. **Nonrefundable credits** can only offset tax owed; anything left over carries forward. The state only loses the revenue when those carryforward credits are actually used against a future tax bill.

Why is corporate RETITC so overwhelmingly refundable? Large commercial solar projects are typically financed through tax-equity partnerships. Under §235-12.5(g), those investors can elect refundability — at a 30% reduction in credit value — because a guaranteed immediate payout is worth more to them than waiting years to use credits against uncertain future tax liability.

### The utilization rate: how fast does the nonrefundable stock drain?

The utilization rate answers: of all the nonrefundable RETITC stock available in a given year (both new nonrefundable credits just earned plus whatever is left over from prior years), what fraction actually gets used against tax liability this year?

**The literature benchmark is about 0.80** — meaning roughly 20% of available nonrefundable credits go unused in any given year (they carry forward). This figure comes from studies of federal nonrefundable tax credits generally.

**This model uses 0.65**, which is more conservative (slower drawdown) than the literature midpoint. The rationale is specific to RETITC:

- RETITC credits are **large relative to individual tax liability**. A typical residential solar system in Hawaii generates a $10,000–$20,000 credit. Many filers — even fairly high-income ones — don't have $15,000 in annual Hawaii state tax liability. It takes several years to use up a credit that big, meaning a smaller fraction of the pool drains each year.
- Hawaii's income tax rates are lower at the individual level than the federal rate, so the tax bill available to absorb the credit is smaller.
- The RETITC has accumulated a large stock over 15+ years. A pool that has been building since 2009 contains old vintages from filers who have since moved, retired, or had reduced income — credits that may drain slowly or not at all.

The honest caveat: **0.65 is a modeled assumption, not a number observed directly from data**. DOTAX does not publish year-over-year carryforward utilization rates. The model is transparent about this uncertainty by running a sensitivity sweep of 0.55–0.75 to show how much the results move.

### How the simulation uses it

Each year in the carryforward simulation:

```
nonrefundable stock available = prior year end stock + new nonrefundable certs this year
usage this year               = utilization rate × nonrefundable stock available
end stock (carried forward)   = (1 − utilization rate) × nonrefundable stock available
```

So with a 0.65 utilization rate, 65% of the available nonrefundable pool is used each year and 35% carries over. After five years with no new credits entering the pool, the remaining stock is approximately 0.35⁵ ≈ 5% of the original — essentially exhausted.

Refundable credits are handled separately: they are fully counted as revenue cost in the year they are certified (no utilization rate applied, because the state owes that money upon certification regardless).

### The blended effective rate

Combining the refundable/nonrefundable split with the utilization rate gives the overall "effective claim share" per pool — the fraction of claimed credits that actually hits state revenue in the current year:

| Pool | Calculation | Effective rate |
|---|---|---|
| Individual | 23% × 100% + 77% × 65% | ~73% |
| Corporate | 89% × 100% + 11% × 65% | ~96% |

Corporate RETITC is nearly fully immediate (96% effective) because it's dominated by refundable elections. Individual RETITC is more deferred (73% effective) because most individual claims are nonrefundable and take multiple years to exhaust.

---

## Step 7 — Compute savings

For each year and scenario:

```
State savings = Baseline cost − Bill cost
```

Where:
- **Baseline cost** = what the state would spend without the bill (refundable credits paid out + nonrefundable stock drawn down)
- **Bill cost** = same calculation, but with AGI screen, $40M cap, and sunset applied

Savings are the revenue the state **does not give up** because of the bill.

---

## Step 8 — Distribute the burden across income groups (page 5)

Who bears the cost of losing the credit? The model maps individual RETITC losses to **five income quintiles** using the DOTAX AGI bin data as a bridge.

Each AGI bin's losses are allocated across quintiles by how much of the bin's income range overlaps with each quintile's income range (fractional overlap, not a hard assignment). Quintile income breaks are anchored to **TY2027** by inflating the 2026 census estimates by one year of nominal income growth.

**Important scope note:** This analysis covers only **individual filers** (58% of total RETITC). Corporate and trust/estate filers (~42%) are not allocated to resident household quintiles — their incidence falls on shareholders and ratepayers in ways that don't map cleanly to the household income distribution.

---

## Step 9 — Build the sensitivity band (page 6)

The headline numbers are the **OBBBA Mid central estimates**. Around them, the chart shows a shaded band representing the range of plausible outcomes.

The band captures two sources of uncertainty:

1. **Demand uncertainty** — min/max across all three OBBBA demand scenarios (Pre-OBBBA, Mid, Severe) for each year.
2. **Behavioral/utilization uncertainty** — a small sweep of the OBBBA Mid scenario across different values of the utilization rate (55%–75%) and behavioral elasticity (0.0–0.6).

The band edges are the minimum and maximum savings across all those combinations. It is **not** a confidence interval in the statistical sense — it's a structured sensitivity range showing how the answer moves as key assumptions vary.

What the band does **not** capture: pure timing shifts (filers pre-purchasing equipment before the cap year) and general-equilibrium effects (e.g., lower RETITC claims → lower solar adoption → changes in electricity prices).

---

## Step 10 — Score the other two credits

The bill also changes two smaller credits. The model scores them with simpler calculations:

**Capital Goods Excise Tax Credit (CGEC, §235-110.7):**
- TY2023 baseline: $34.6M
- The bill sunsets this credit after TY2027
- State savings starting TY2028 = projected CGEC baseline for that year
- A pull-forward haircut (85%–95%) reduces the TY2028–2030 baseline to account for businesses accelerating capital purchases into 2027 to claim the credit before it disappears

**Tax Credit for Research Activities (TCRA, §235-110.91):**
- TY2023 baseline: $7M
- The baseline (Act 261) already repeals this credit on 1/1/2030
- The bill accelerates repeal to 1/1/2029 — one year earlier
- State savings = TY2029 only (just the one-year acceleration)

---

## What the model does NOT capture

- **Pure timing shifts** — filers who pull installations forward into pre-cap years to get the full credit; this is a real phenomenon but hard to quantify without behavioral data
- **Corporate incidence** — the $42M corporate RETITC has real-world incidence on shareholders and electricity ratepayers, but this is not allocated to household quintiles
- **General equilibrium** — lower RETITC demand → less solar installed → effects on electricity rates, construction employment, etc.
- **Carryforward expiration** — the model assumes carryforward stock eventually gets used at the utilization rate; in practice some may expire (taxpayers move, die, or have no future tax liability)

---

## Data sources

| Data | Source |
|---|---|
| TY2023 RETITC actuals by claimant type | DOTAX *Tax Credits Claimed by Hawaiʻi Taxpayers — Tax Year 2023* (Dec 2025), Table A-1 |
| TY2023 individual claims by AGI bracket | DOTAX Table A-5 |
| TY2018–2022 historical RETITC actuals | `dotax_reec_historical.csv` (from DOTAX annual publications) |
| Hawaii income growth | ACS median household income forecast × Honolulu CPI series (repo's `income_forecast.py`) |
| OBBBA demand decay | SEIA *U.S. Solar Market Insight* (post-OBBBA edition), Hawaii-tempered |
| AGI eligibility shares | Hawaii PUMS calibrated tax units (`/tmp/tax_units_cache.parquet`) |
| Household income quintiles | ACS 5-year estimates, TY2027-anchored via nominal income growth |

---

## Frequently asked questions

### "What is a tax credit? How is it different from a deduction?"

A **tax deduction** reduces the income you're taxed on. A **tax credit** reduces the actual tax bill itself — dollar for dollar. If you owe $5,000 in state taxes and you have a $3,000 RETITC credit, you now owe only $2,000. Credits are generally much more valuable than deductions of the same size.

### "Why does the state give people money for putting solar on their roof?"

It doesn't write a check — it just collects less tax. The idea is that the state wants more solar installed (cleaner energy, less dependence on imported oil) and is willing to forgo some tax revenue to incentivize it. The RETITC has existed since 2009 and has helped Hawaii reach the highest rooftop solar penetration rate in the country (~35% of homes).

### "What does 'the state gives up $100M in revenue' mean? Where does that money go?"

It means $100M in taxes that would otherwise flow to the state treasury never arrives — it stays in the pockets of whoever claimed the credit. The state didn't spend $100M; it just didn't collect it. From a budget standpoint the effect is the same: $100M less to spend on schools, roads, etc.

### "What is AGI?"

AGI stands for **Adjusted Gross Income** — essentially your total income minus a handful of deductions (retirement contributions, student loan interest, etc.), before you take the standard deduction. It's the number at the bottom of the first page of your federal tax return. The bill uses AGI as the income test because it's a consistent, already-audited number the state can verify.

### "Why does it matter whether a credit is refundable or not?"

A **nonrefundable** credit can only reduce your tax bill to zero — it can't create a refund. If you owe $1,000 in taxes and have a $3,000 RETITC credit, you zero out your bill and the remaining $2,000 of credit carries forward to next year. A **refundable** credit can go below zero — the state sends you a check for the difference. Corporate solar investors often elect refundability (at a 30% reduction in credit value) because it's more predictable than waiting years to use it.

This matters for the model because nonrefundable credits don't cost the state money the moment they're earned — the state only loses revenue when those credits actually get used to offset a future tax bill.

### "What is the carryforward pool? Why is it such a big deal?"

Think of it as a bank account of unspent credits. Every year, people and businesses earn RETITC credits but can't fully use them (because the credit exceeds their tax bill). The excess goes into their "carryforward account" and can be used in future years — indefinitely, until it's gone.

Here's why it matters for the bill: the $40M annual cap only limits **new** credits being earned each year. It does nothing to the carryforward bank account that already exists from 2009–2026. So in 2027, when the cap kicks in, the state will still be paying out old credits from that pool on top of the newly capped $40M. That's why the report's "RETITC cost under the bill" can still be well above $40M in the early years.

### "What is OBBBA? Why does a federal law affect a state tax credit?"

OBBBA (One Big Beautiful Budget Act) is a federal law passed in July 2025 that terminated the federal residential solar tax credit (Section 25D) effective December 31, 2025. The federal credit was worth 30% of the system cost — a big deal. Without it, installing solar is more expensive, so fewer people will do it. Fewer installations means fewer RETITC claims, which means the state's baseline exposure (without any bill) is already lower than it would have been. The OBBBA scenarios in the model capture the range of how much Hawaii demand actually falls.

### "Why doesn't Hawaii solar just disappear entirely without the federal credit?"

Two reasons. First, Hawaii has the highest electricity prices in the country (~$0.42/kWh, about triple the mainland average). Solar already pencils out on pure economics even without a federal subsidy — you save so much on your electric bill that the payback period is short. Second, commercial solar projects often use a different federal credit (§48E, which stays intact through 2027) rather than the residential §25D, so a large chunk of the commercial market is unaffected in the near term.

### "What does 'pro-rata' mean in plain English?"

It means everyone in the same boat gets an equal haircut. Imagine 10 people each want $8M in RETITC credits ($80M total) but the cap is $40M. Rather than give the first five people full credits and the last five nothing (first-come-first-served), the state gives everyone 50 cents on the dollar — each person gets $4M. That's pro-rata: the cap is shared proportionally across all eligible claimants.

### "What is the difference between CD1 and CD2?"

Both are versions of SB 3125 — CD1 is Conference Draft 1, CD2 is Conference Draft 2. They have the same basic structure (cap, income limit, sunset) but differ in a few technical details about how the cap is interpreted and whether filers are assumed to change their behavior in response to the pro-rata haircut. CD2 also applies a dynamic adjustment to the refundable/nonrefundable split under the assumption that after the AGI screen removes high-income filers, the remaining pool is more likely to elect the refundable option.

### "What is DOTAX? What is DBEDT? What is SEIA?"

- **DOTAX** — Hawaii Department of Taxation. They publish annual reports on how much of each tax credit was claimed and by whom. The TY2023 data that anchors this entire model comes from their December 2025 publication.
- **DBEDT** — Department of Business, Economic Development, and Tourism. They administer the RETITC certification process — you apply to them for certification before you can claim the credit on your taxes.
- **SEIA** — Solar Energy Industries Association. The national solar industry trade group. They publish regular market forecasts for U.S. solar demand, which the model uses (Hawaii-adjusted) for the OBBBA demand scenarios.

### "What is PUMS?"

PUMS stands for **Public Use Microdata Sample** — it's the Census Bureau's release of anonymized individual survey responses from the American Community Survey. Instead of just published summary tables, researchers get the actual (anonymized) record for each household: their income, housing situation, family size, etc. This model uses Hawaii PUMS data to build simulated tax units and figure out what share of RETITC claimants in each income bracket fall above or below the AGI threshold.

### "What is a 'baseline'?"

The baseline is simply **what happens if the bill doesn't pass** — the counterfactual. Every savings figure in the report is the difference between the baseline (no bill) and the scenario (bill passes). If the baseline RETITC cost in TY2027 is $90M and the bill brings it to $55M, the savings is $35M.

### "What does 'vintage' mean?"

In this context, vintage just means the **year a credit was earned**. A "TY2025 vintage" credit is one earned in Tax Year 2025. This language is useful because the $40M cap applies only to new vintages (TY2027–2029) — old vintages from prior years continue to be used regardless of the cap.

### "Why are corporate filers left out of the income distribution chart?"

The income distribution chart is trying to answer: "which Hawaii households bear the cost of losing the RETITC?" Corporations aren't households. When a corporation loses a tax credit, the cost is ultimately borne by its shareholders (who may or may not be Hawaii residents) and potentially its customers through higher prices. There's no clean way to assign that to a household income quintile without making a lot of additional assumptions, so the chart is explicitly scoped to individual filers only and labeled accordingly.
