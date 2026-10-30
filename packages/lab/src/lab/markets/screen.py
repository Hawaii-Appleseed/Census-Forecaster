"""Lead-lag / Granger causal screen: market series → Hawaii trends.

Statistical honesty, up front
-----------------------------
* **Granger causality is NOT causation.** A significant F-test says the
  ticker's past adds predictive content for the target beyond the
  target's own past — nothing more. Confounders (e.g. national rates
  driving both) survive this screen; only the Phase-3 forecaster
  ablation decides whether a signal earns weight.
* **Pre-registration.** Only the ticker→target pairs declared in
  ``HYPOTHESIS_PAIRS`` (derived from ``universe.py``) are tested; the
  Benjamini–Hochberg FDR correction is applied across ALL (pair ×
  transform × lag) tests actually run.
* **Cadence.** Hypothesis tests run at monthly cadence only. Annual
  pairs (ACS-anchor targets, n≈10–15) are reported as descriptive
  lead-lag correlations, clearly labelled "no test".
* **Regime sensitivity.** The 2020 COVID shock dominates naive
  correlations; ``run_screen(exclude_2020=True)`` re-runs the screen
  with 2020 months removed and the report shows both.

Implementation is numpy + ``scipy.stats.f`` (no statsmodels — it is not
a census_forecaster dependency and the restricted-vs-unrestricted OLS
F-test is ~30 lines).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

import numpy as np
from scipy import stats

# ---------------------------------------------------------------------------
# Monthly target definitions
# ---------------------------------------------------------------------------
# Keys into the macro_monthly.json / bls_panel series, with the transform
# that makes each approximately stationary:
#   "diff"     — first difference (unemployment rates, percentage points)
#   "log_diff" — log first difference (price indexes)
MONTHLY_TARGETS: dict[str, tuple[str, str]] = {
    # target key            (source series id,          transform)
    "US_UNEMPLOYMENT":      ("LNS14000000",             "diff"),
    "HI_UNEMPLOYMENT":      ("LASST150000000000003",    "diff"),
    "HONOLULU_ZHVI":        ("ZHVI_HONOLULU_MONTHLY",   "log_diff"),
    "HONOLULU_ZORI":        ("ZORI_HONOLULU_MONTHLY",   "log_diff"),
    # Urban Hawaii CPI, the GENUINE Hawaii series (bimonthly, 2017+).
    # Was CUURS49ASA0 until 2026-08-05 — which the 2026-07-27 identity
    # audit proved is Los Angeles, not Honolulu (METHODOLOGY §5). The
    # production tax_modeler path was corrected then; this screen was
    # missed, so every historical XLE/MATX → "HONOLULU_CPI" pass
    # actually described LA inflation. Corrected here.
    #
    # Consequence, and it is the honest one: this series is bimonthly,
    # so granger_f_test's all-lags-present rule finds too few aligned
    # rows and returns None rather than a p-value. CPI-directed
    # hypotheses are therefore xcorr-descriptive only — exactly what
    # METHODOLOGY's "Known limitations" already stated. HI_ELECTRICITY
    # below is the monthly Hawaii price proxy that restores testability.
    "HONOLULU_CPI":         ("CUURS49FSA0",             "log_diff"),  # bimonthly
    # EIA state retail electricity price — genuine MONTHLY Hawaii-specific
    # price series (2001+, ~3-month lag). Hawaii imports ~80% of its
    # energy, so this measures the imported-energy → local-price channel
    # directly instead of inferring it from an energy-equity proxy.
    "HI_ELECTRICITY":       ("EIA_HI_ELEC_ALL",         "log_diff"),
    # Monthly visitor arrivals, statewide. The middle link of the JETS
    # hypothesis, which previously had to be assumed: "airline prices
    # embed forward bookings → arrivals → tourism employment".
    #
    # Source is the DBEDT MEI series (1990-01 → current month, ~5-week
    # lag) rather than the HTA historical workbook (ends at its final
    # year, 2024): the two agree to within rounding on all 420 overlap
    # months (max rel diff 0.0004%, verified 2026-08-05), so
    # HTA_VISITORS_* stays bundled as the archival cross-check while
    # this target gets the current-through-today series.
    "HI_VISITORS":          ("DBEDT_ARRIVALS_STATEWIDE", "log_diff"),
    # Honolulu single-family median RESALE price (DBEDT, 1990+). A
    # model-free house-price target: it is the median of prices actually
    # recorded on closed transactions, with no estimator in the loop.
    #
    # That property is why it exists here. HONOLULU_ZHVI is Zillow's
    # index, computed from Zestimates, and Zillow documents on-market
    # data — list price and days on market among it — as Zestimate
    # inputs. Screening listing-derived predictors (RDC_* below) against
    # ZHVI therefore risks the HIPHCI defect in diluted form: partly
    # asking whether a number predicts something built from it. The
    # dilution is real (ZHVI values every home, and ~97% of them are not
    # listed in any given month, so their Zestimates carry no current
    # on-market signal) — this is nothing like HIPHCI's arithmetic
    # circularity — but "diluted" is not "absent". This target is the
    # uncontaminated control: a listing indicator that leads BOTH series
    # is telling us about housing, not about Zillow's model.
    #
    # Noisier than ZHVI by construction (a raw median over ~200-300
    # sales/month moves with the composition of what sold, which is
    # exactly the smoothing ZHVI exists to provide), so treat it as the
    # robustness check and ZHVI as the operative target, not vice versa.
    "HONOLULU_SF_MEDIAN":   ("DBEDT_SF_MEDIAN_HONOLULU", "log_diff"),
}

#: Hawaii monthly PREDICTORS (screen name → macro_monthly.json series id).
#: Same merge path as NATIONAL_PREDICTORS, but Hawaii-specific rather
#: than national.
HAWAII_PREDICTORS: dict[str, str] = {
    # Tourism-demand channel that plausibly leads local labour-market
    # slack. DBEDT MEI series — see the HI_VISITORS note above.
    "HI_VISITORS_ARRIVALS": "DBEDT_ARRIVALS_STATEWIDE",
    # DOL ETA-539 weekly initial claims, aggregated to monthly means.
    # Administrative head-counts of new UI filings — they precede the
    # household-survey unemployment measurement mechanically (a filing
    # happens at separation; LAUS measures the stock of unemployed
    # later), and are the fastest labour signal in the panel (~11-day
    # lag at the weekly grain).
    "HI_UI_CLAIMS": "DOL_HI_INITIAL_CLAIMS",
    # BLS CES total nonfarm payrolls, Hawaii (SMS15000000000000001).
    # Establishment survey — the hiring-side complement to LAUS's
    # household survey; payroll changes plausibly lead measured
    # unemployment.
    "HI_PAYROLLS": "SMS15000000000000001",
    # Philadelphia Fed Hawaii coincident index — a composite of payrolls,
    # unemployment, manufacturing hours and deflated wages, i.e. a
    # purpose-built "state of the Hawaii economy" number. The only
    # composite in the panel; everything else is a single raw series.
    "HI_COINCIDENT": "HIPHCI",
    # BTS T-100 enplanements FROM Honolulu — airline operations data,
    # independent of the survey-based DBEDT/HTA visitor counts, so it
    # tests the tourism channel from the supply side.
    "HI_AIR_PAX": "BTS_HNL_PASSENGERS",
    # Statewide visitor SPENDING (DBEDT MEI). Arrivals count heads;
    # this counts dollars, which is the quantity tourism-dependent
    # employment and tax receipts actually track.
    "HI_VISITOR_SPEND": "DBEDT_VISITOR_SPEND_STATEWIDE",
    # Tourism-exposed payrolls: accommodation + food services. The
    # employment link the JETS→arrivals→jobs chain has been arguing
    # about, now measured directly rather than via aggregate slack.
    "HI_JOBS_ACCOM": "DBEDT_JOBS_ACCOM_STATEWIDE",
    # Housing transaction VOLUME (single-family resales). Turnover
    # typically leads price: sales dry up before prices roll over.
    "HI_SF_SALES": "DBEDT_SF_SALES_STATEWIDE",
    # Housing permits as UNIT COUNTS (FRED/Census), distinct from
    # DBEDT's permit VALUE in dollars.
    "HI_PERMIT_UNITS": "HIBPPRIV",
    # --- Realtor.com listing-side indicators (2026-08-06 intake) ---
    # Everything else in the housing panel is measured at or after
    # closing, which in Hawaii is 30-60 days after the price was agreed.
    # These three are measured while the home is still on the market, so
    # they are the panel's only candidates for genuinely LEADING the
    # price series rather than re-describing it.
    #
    # Median days on market. Duration, not a price: when demand softens
    # homes sit longer before going under contract, and that shows up
    # before any closed-sale statistic moves.
    "HI_DOM": "RDC_DOM_HONOLULU",
    # Share of active listings that cut their asking price — direct
    # measurement of seller capitulation. Arguably the cleanest leading
    # indicator in the file: a price cut is a decision, recorded the day
    # it happens, whereas a closed-sale index only learns about it once
    # the discounted sale settles months later.
    "HI_PRICE_CUTS": "RDC_PRICE_CUTS_HONOLULU",
    # Pending / active listings. A tightness ratio, scale-free (so it is
    # not dragged around by Oahu's market size) and forward-looking:
    # pending contracts are next month's and the month after's closings.
    "HI_PENDING_RATIO": "RDC_PENDING_RATIO_HONOLULU",
    # Census BFS business applications, Hawaii, SA (2004-07+). A filing
    # precedes hiring, leasing and equipment purchase, so formations
    # lead labour demand rather than recording it. The only route to
    # this signal: Hawaii DCCA publishes registrations exclusively as
    # HTML search forms — no CSV, no API, no series (verified
    # 2026-08-06).
    "HI_BIZ_APPS": "BABATOTALSAHI",
    # --- domestic / international split of arrivals (2026-08-07) ---
    # HI_VISITORS_ARRIVALS above averages two markets that behave
    # nothing alike. International (Japan above all) collapsed after
    # 2020 and has recovered on a completely different path from
    # domestic travel, and it is the higher-spend, hotel-concentrated
    # segment — so the tourism→employment signal, if it exists, should
    # be stronger here than in the blended total. Splitting it is the
    # cheapest available test of that.
    "HI_VISITORS_INTL": "DBEDT_ARRIVALS_INTL_STATEWIDE",
    "HI_VISITORS_DOM": "DBEDT_ARRIVALS_DOM_STATEWIDE",
    # Professional & business services payrolls. Registered out of the
    # 13 new sector series because it is the one with a mechanism the
    # others lack: the sector contains TEMPORARY HELP SERVICES, which
    # employers expand before committing to permanent hires and cut
    # first when demand turns — the conventional leading edge of labour
    # demand. The rest (retail, health, government, manufacturing...)
    # are bundled for descriptive use but not screened; without a
    # written mechanism they would only spend testing budget.
    "HI_JOBS_PROF": "DBEDT_JOBS_PROF_STATEWIDE",
    #
    # NOT registered as predictors, deliberately:
    #
    # RDC_LIST_PRICE_* / RDC_LIST_PPSF_* — asking prices. Against ZHVI
    # this is the sharpest form of the Zestimate-input problem described
    # in the HONOLULU_SF_MEDIAN note (list price is an acknowledged
    # Zestimate input), and against HONOLULU_SF_MEDIAN it is close to
    # tautological: the median list price and the median sale price of
    # the same market in the same month are two measurements of one
    # number, separated mostly by the negotiating discount. Bundled in
    # macro_monthly for descriptive and nowcast use; not screened.
    #
    # RDC_PRICE_HIKES_* — price_increased_share hits exactly 0.0 in 40
    # county-months, which log_diff drops, leaving a gapped series. The
    # informative direction in a softening market is cuts, not hikes.
    #
    # RDC_ACTIVE_* / RDC_NEW_LISTINGS_* / RDC_PENDING_* (levels) —
    # inventory and flow counts. Held back purely on multiple-testing
    # budget: RDC_PENDING_RATIO already carries the supply/demand
    # balance in scale-free form, and DBEDT_SF_INVENTORY_HONOLULU covers
    # the stock. Available in the bundle if a written mechanism appears.
}

# National-macro monthly PREDICTORS (predictor_name → macro_monthly.json
# series id). These are national Census/BLS/FRED series that plausibly LEAD
# Hawaii trends; run_market_screen merges them into the predictor dict
# alongside the tickers. CAVEAT (documented in the report limitations):
# run_screen applies log_return to whatever it is handed, so for rate-level
# series (mortgage, 10yr, LFPR) the "log_return" is a rough proxy for a
# percentage-point change — acceptable for a predictive-precedence screen,
# never used as a forecast input.
NATIONAL_PREDICTORS: dict[str, str] = {
    "US_AHE":        "CES0500000003",         # avg hourly earnings (wages)
    "US_LFPR":       "LNS11300000",           # labour-force participation
    "US_EMPPOP":     "LNS12300000",           # employment-population ratio
    "US_JOLTS":      "JTS000000000000000JOR", # job openings rate
    "US_MORTGAGE30": "MORTGAGE30US",          # 30-yr mortgage rate (FRED)
    "US_DGS10":      "DGS10",                 # 10-yr Treasury yield (FRED)
    # US Gulf Coast kerosene-type jet fuel spot, $/gal (1990-04+).
    # Fetched by refresh_hawaii_indicators.py rather than
    # refresh_national_macro.py — the latter is driven by
    # acs.ml_features.NATIONAL_SERIES, so registering it there would
    # inject an ungated channel into the ACS feature panel.
    "US_JETFUEL":    "MJFUELUSGULF",
}

# Pre-registered predictor → monthly-target pairs. Tickers map ACS-cell
# affinities (universe.py) to a monthly counterpart; national predictors
# map to the labour-market / housing channel they lead.
HYPOTHESIS_PAIRS: tuple[tuple[str, str], ...] = (
    ("SPY", "HI_UNEMPLOYMENT"),
    ("QQQ", "HI_UNEMPLOYMENT"),
    ("VTI", "HI_UNEMPLOYMENT"),
    ("XLF", "HI_UNEMPLOYMENT"),
    ("JETS", "HI_UNEMPLOYMENT"),
    ("JETS", "US_UNEMPLOYMENT"),
    # Leg 1 of the JETS hypothesis, now testable directly rather than
    # inferred: do airline equity prices lead actual Hawaii arrivals?
    ("JETS", "HI_VISITORS"),
    # Leg 2: do arrivals lead local labour-market slack? Together these
    # test the mechanism the JETS→HI_UNEMPLOYMENT pair only assumed.
    ("HI_VISITORS_ARRIVALS", "HI_UNEMPLOYMENT"),
    # Administrative UI filings → measured unemployment (see
    # HAWAII_PREDICTORS notes for the mechanisms).
    ("HI_UI_CLAIMS", "HI_UNEMPLOYMENT"),
    # Establishment hiring → household-survey unemployment.
    ("HI_PAYROLLS", "HI_UNEMPLOYMENT"),
    # --- 2026-08-06 intake: Hawaii-specific monthly indicators ---
    # Deliberately a SHORT list. 51 new series landed in macro_monthly,
    # but the multiple-testing budget is the scarce resource here (BH-FDR
    # is applied across every pair x transform x lag actually run), so
    # only hypotheses with a written mechanism get registered — the same
    # discipline universe.py imposes on tickers.
    #
    # Air traffic → visitor arrivals: airline capacity/enplanements are
    # booked weeks ahead, so supply-side operations should precede the
    # survey-measured arrival counts.
    ("HI_AIR_PAX", "HI_VISITORS"),
    # Visitor SPENDING → tourism-exposed employment. Spending is what
    # actually funds those payrolls; heads-through-the-door need not.
    ("HI_VISITOR_SPEND", "HI_UNEMPLOYMENT"),
    # Housing turnover → prices. Volume conventionally leads price in
    # residential real estate; this tests it on Hawaii data.
    ("HI_SF_SALES", "HONOLULU_ZHVI"),
    # Permit UNITS → home values: new supply pipeline vs price.
    ("HI_PERMIT_UNITS", "HONOLULU_ZHVI"),
    # --- 2026-08-06 intake: listing-side leading indicators ---
    # Tested against HONOLULU_SF_MEDIAN, the model-free recorded-sale
    # target, NOT against ZHVI — see that target's note. All three ask
    # the same question from different angles: does what happens to a
    # home while it is still listed tell us where closed prices go next?
    ("HI_DOM", "HONOLULU_SF_MEDIAN"),           # homes sit longer → prices soften
    ("HI_PRICE_CUTS", "HONOLULU_SF_MEDIAN"),    # sellers capitulate → prices soften
    ("HI_PENDING_RATIO", "HONOLULU_SF_MEDIAN"), # tightness → prices firm
    # ...and days-on-market additionally against the operative ZHVI
    # target, because ZHVI is what the annual forecaster actually
    # consumes. Registered as ONE pair rather than three so the
    # contaminated target does not dominate the testing budget; read any
    # pass here against its HONOLULU_SF_MEDIAN counterpart above, which
    # is the uncontaminated version of the same question.
    #
    # OUTCOME (2026-08-06), and it is why the control was built: this
    # pair PASSES (p=0.0056 at lag 3, 2020 excluded) while the same
    # predictor against recorded sale prices does NOT (p=0.567). The
    # cross-correlation profiles say why. Against SF_MEDIAN, DOM peaks
    # at lead 0 (-0.29) and collapses to +0.02 by lead 1 — coincident
    # with the market, no predictive content. Against ZHVI it peaks at
    # lead 1 and decays smoothly, which is what a shared input smeared
    # through a smoothed index looks like.
    #
    # "SF_MEDIAN is just noisier" does not explain it: HI_PRICE_CUTS
    # cleared BH against that same noisy target, so the target has power
    # to detect an effect of this size. DOM's apparent lead on ZHVI is
    # therefore treated as an artifact of ZHVI's construction, not
    # evidence about housing. Kept registered — the contrast is the
    # informative part and deleting it would hide the finding — but this
    # signal MUST NOT be promoted into a feature channel (signals.py
    # CHANNELS) without first reproducing it on a model-free target.
    ("HI_DOM", "HONOLULU_ZHVI"),
    # --- 2026-08-06 intake: fuel cost + business formation ---
    # Jet fuel -> arrivals. Fuel is ~25-30% of airline operating cost,
    # and EVERY visitor to Hawaii arrives on a 2,500+ mile flight, so
    # the state's exposure to fuel is structural in a way no mainland
    # destination's is: when fuel spikes, long-haul leisure capacity is
    # what gets cut. Registered ALONGSIDE the existing JETS->HI_VISITORS
    # pair rather than instead of it, because they are not the same
    # quantity: JETS is a diversified airline EQUITY index dominated by
    # domestic short-haul (and prices in hedging, balance sheets and
    # regulatory risk), while this is the raw input COST. If the fuel
    # channel is real, this should be the cleaner expression of it.
    #
    # OUTCOME (2026-08-07): NULL, and doubly so.
    # (a) It clears BH at all three lags on the full sample (p=0.0103 /
    #     0.0138 / 0.0009) and then fails ALL THREE with 2020 excluded
    #     (p=0.244 / 0.219 / 0.537). Textbook one-event artifact: 2020
    #     collapsed jet fuel and Hawaii arrivals in the same quarter, so
    #     any estimator sees a relationship that is really one shock.
    # (b) The SIGN is wrong for the mechanism regardless of the p-value.
    #     The hypothesis predicts NEGATIVE (fuel up -> long-haul
    #     capacity cut -> fewer arrivals); the data give +0.204 at lead
    #     0 on the full sample and +0.179 at lead 9 without 2020. A
    #     positive relationship is what the global demand cycle produces
    #     — a strong world economy lifts both fuel prices and travel —
    #     so even the full-sample "pass" is not evidence for the stated
    #     channel. Sign checks are cheap and would have caught this
    #     before the F-test did.
    # Kept registered: the null is informative and the mechanism is a
    # natural thing for a future reader to re-propose.
    ("US_JETFUEL", "HI_VISITORS"),
    # Business formation -> labour-market slack. An application is filed
    # before anyone is hired, a lease signed or equipment bought, so
    # formations lead employment rather than recording it.
    #
    # Not circular: BFS counts IRS EIN applications, an administrative
    # filing series with no input from the household survey the
    # unemployment rate is computed from — unlike HIPHCI, which is
    # built from that rate (see the note below).
    #
    # OUTCOME (2026-08-07): NULL. Same shape as US_JETFUEL above —
    # clears BH at all three lags on the full sample (p=0.0045 / 0.0334
    # / 0.0186), fails all three with 2020 excluded (p=0.421 / 0.540 /
    # 0.608). Unlike the fuel pair the SIGN is right throughout
    # (-0.147 at lead 1: more formations, less slack), so the mechanism
    # is not contradicted — it is simply not detectable outside the
    # pandemic, when formations and unemployment moved together because
    # one shock moved everything.
    #
    # Worth a retest later rather than deletion: the series is SA and
    # starts 2004-07, so excluding 2020 leaves ~236 usable months —
    # ample n. The constraint is effect size, not sample size, and
    # Hawaii's formation rate runs below its population share (0.30% of
    # US applications vs 0.43% of population), so the signal may simply
    # be small here.
    ("HI_BIZ_APPS", "HI_UNEMPLOYMENT"),
    # --- 2026-08-07: does the tourism signal live in one segment? ---
    # Both segments against labour slack, so the blended
    # HI_VISITORS_ARRIVALS -> HI_UNEMPLOYMENT pair above can be read as
    # a decomposition rather than a single averaged verdict.
    ("HI_VISITORS_INTL", "HI_UNEMPLOYMENT"),
    ("HI_VISITORS_DOM", "HI_UNEMPLOYMENT"),
    # Temporary-help-bearing payrolls -> labour slack.
    #
    # THIS SITS IN TENSION WITH AN EARLIER DECISION and the tension is
    # deliberate rather than overlooked. ("HI_JOBS_ACCOM",
    # "HI_UNEMPLOYMENT") was withdrawn below as circular, on the
    # grounds that "accommodation payrolls are a component of the
    # employment level the unemployment rate is computed against". On
    # re-examination that reasoning does not hold: payroll counts come
    # from the ESTABLISHMENT survey (CES, which DBEDT republishes)
    # while the unemployment rate comes from the HOUSEHOLD survey
    # (LAUS/CPS). Different instruments, different universes — an
    # establishment job count is not an input to the household rate.
    # The repo already relies on this: ("HI_PAYROLLS",
    # "HI_UNEMPLOYMENT") registers TOTAL CES nonfarm against the same
    # target and has never been questioned, and a single sector shares
    # strictly less with the target than the total does.
    #
    # So this pair is registered, and the HI_JOBS_ACCOM withdrawal is
    # left standing rather than quietly reversed — re-registering a
    # pair that was withdrawn on the record is a call for a human to
    # make, not something to slip into an unrelated commit.
    ("HI_JOBS_PROF", "HI_UNEMPLOYMENT"),
    #
    # NOT REGISTERED — ("HI_VISITORS_INTL", "HI_VISITORS") and
    # ("HI_VISITORS_DOM", "HI_VISITORS"): ARITHMETICALLY CIRCULAR, and
    # more starkly than HIPHCI ever was. HIPHCI at least blends four
    # inputs; here the target IS the sum of the two predictors.
    # Verified on the data rather than assumed — across all five
    # geographies and 438 months, DOM + INTL reconstructs the published
    # total to within one visitor (worst case 1 of 345,075). Asking
    # whether a term predicts its own sum is not a hypothesis.
    #
    # NOT REGISTERED, deliberately:
    #
    # ("HI_COINCIDENT", "HI_UNEMPLOYMENT") — CIRCULAR. The Philadelphia
    # Fed builds HIPHCI from four inputs, and the state unemployment RATE
    # is one of them, so "does HIPHCI predict unemployment" is partly
    # asking whether a number predicts its own ingredient. A trial run on
    # 2026-08-06 duly returned r=-0.934 at lag 0 with a 2020-robust flag
    # — a result that looks spectacular and means almost nothing. HIPHCI
    # stays bundled in macro_monthly for descriptive/nowcast use; if it
    # is ever screened, the target must be something outside its
    # construction (e.g. HONOLULU_ZHVI or HI_VISITORS).
    #
    # ("HI_JOBS_ACCOM", "HI_UNEMPLOYMENT") — same defect, milder:
    # accommodation payrolls are a component of the employment level the
    # unemployment rate is computed against. Zero BH passes on the trial
    # run anyway.
    ("XLRE", "HONOLULU_ZHVI"),
    ("XLRE", "HONOLULU_ZORI"),
    ("VNQ", "HONOLULU_ZHVI"),
    ("VNQ", "HONOLULU_ZORI"),
    ("XLE", "HONOLULU_CPI"),
    # Same imported-energy hypothesis as XLE→HONOLULU_CPI, but against a
    # genuine MONTHLY Hawaii price. The CPI pair stays registered for the
    # descriptive xcorr; this is the one that can actually be Granger-tested.
    ("XLE", "HI_ELECTRICITY"),
    ("BOH", "HI_UNEMPLOYMENT"),
    ("FHB", "HI_UNEMPLOYMENT"),
    ("HE", "HI_UNEMPLOYMENT"),
    ("MATX", "HONOLULU_CPI"),
    # Ocean-freight costs feed imported fuel + delivered goods; the
    # electricity price is the monthly-cadence expression of that channel.
    ("MATX", "HI_ELECTRICITY"),
    ("MATX", "HONOLULU_ZHVI"),
    # Hawaiian Electric — registered as a same-channel check and it does
    # NOT pass (zero BH passes, 2026-08-05). Kept registered because the
    # null is informative, not because it was expected to work: HE's
    # equity price tracks regulatory and wildfire-liability risk (the
    # 2023 Maui fire crash), while its tariff is PUC-set from fuel costs
    # — so stock and tariff are driven by different things and the miss
    # is economically coherent. The channel's evidence rests on XLE
    # (p≈6e-14, 3-month lead), not on this pair.
    ("HE", "HI_ELECTRICITY"),
    # --- national-macro predictors (Phase 2) ---
    ("US_MORTGAGE30", "HONOLULU_ZHVI"),   # rates → home values (inverse, lagged)
    ("US_MORTGAGE30", "HONOLULU_ZORI"),   # rates → rents
    ("US_DGS10", "HONOLULU_ZHVI"),        # long rates → home values
    ("US_JOLTS", "US_UNEMPLOYMENT"),      # openings lead national unemployment
    ("US_JOLTS", "HI_UNEMPLOYMENT"),      # ...and local
    ("US_AHE", "HI_UNEMPLOYMENT"),        # national wages vs local slack
    ("US_LFPR", "HI_UNEMPLOYMENT"),       # participation vs local slack
    ("US_EMPPOP", "HI_UNEMPLOYMENT"),
)

# ---------------------------------------------------------------------------
# Expected sign of each hypothesis
# ---------------------------------------------------------------------------
# A Granger F-test is direction-blind: it asks whether the predictor's
# past adds predictive content, not whether it moves the target the way
# the mechanism says. Those come apart in practice. On 2026-08-07,
# US_JETFUEL -> HI_VISITORS cleared BH at all three lags with r=+0.204
# while its written mechanism (fuel up -> long-haul capacity cut ->
# fewer arrivals) predicts a NEGATIVE relationship. The "pass" was
# never evidence for the stated channel — a strong world economy simply
# lifts fuel prices and travel together. A sign check is cheaper than
# an F-test and catches that class of error first, so it now runs
# automatically instead of depending on someone remembering.
#
# +1 / -1 = the sign the mechanism predicts.
# Deliberately ABSENT = genuinely two-sided; see _AMBIGUOUS_SIGNS below
# for why each one is omitted. Absent is not "unknown", it is a claim
# that no directional prediction is defensible, and it exempts the pair
# from the check rather than failing it.
EXPECTED_SIGN: dict[tuple[str, str], int] = {
    # Equity/activity up -> labour-market slack down.
    ("SPY", "HI_UNEMPLOYMENT"): -1,
    ("QQQ", "HI_UNEMPLOYMENT"): -1,
    ("VTI", "HI_UNEMPLOYMENT"): -1,
    ("XLF", "HI_UNEMPLOYMENT"): -1,
    ("JETS", "HI_UNEMPLOYMENT"): -1,
    ("JETS", "US_UNEMPLOYMENT"): -1,
    ("BOH", "HI_UNEMPLOYMENT"): -1,
    ("FHB", "HI_UNEMPLOYMENT"): -1,
    ("HE", "HI_UNEMPLOYMENT"): -1,
    # Airline equity up -> more capacity/bookings -> more arrivals.
    ("JETS", "HI_VISITORS"): +1,
    ("HI_AIR_PAX", "HI_VISITORS"): +1,
    # Tourism inflow up -> slack down.
    ("HI_VISITORS_ARRIVALS", "HI_UNEMPLOYMENT"): -1,
    ("HI_VISITORS_INTL", "HI_UNEMPLOYMENT"): -1,
    ("HI_VISITORS_DOM", "HI_UNEMPLOYMENT"): -1,
    ("HI_JOBS_PROF", "HI_UNEMPLOYMENT"): -1,
    ("HI_VISITOR_SPEND", "HI_UNEMPLOYMENT"): -1,
    # Labour-market mechanics.
    ("HI_UI_CLAIMS", "HI_UNEMPLOYMENT"): +1,   # more filings -> more slack
    ("HI_PAYROLLS", "HI_UNEMPLOYMENT"): -1,
    ("HI_BIZ_APPS", "HI_UNEMPLOYMENT"): -1,    # formations -> hiring
    ("US_JOLTS", "US_UNEMPLOYMENT"): -1,
    ("US_JOLTS", "HI_UNEMPLOYMENT"): -1,
    ("US_AHE", "HI_UNEMPLOYMENT"): -1,         # wage pressure = tight market
    ("US_EMPPOP", "HI_UNEMPLOYMENT"): -1,
    # Real-estate equity -> local housing.
    ("XLRE", "HONOLULU_ZHVI"): +1,
    ("XLRE", "HONOLULU_ZORI"): +1,
    ("VNQ", "HONOLULU_ZHVI"): +1,
    ("VNQ", "HONOLULU_ZORI"): +1,
    ("MATX", "HONOLULU_ZHVI"): +1,             # local activity proxy
    # Rates up -> home values down.
    ("US_MORTGAGE30", "HONOLULU_ZHVI"): -1,
    ("US_DGS10", "HONOLULU_ZHVI"): -1,
    # Imported-energy / freight cost -> local prices up.
    ("XLE", "HONOLULU_CPI"): +1,
    ("XLE", "HI_ELECTRICITY"): +1,
    ("MATX", "HONOLULU_CPI"): +1,
    ("MATX", "HI_ELECTRICITY"): +1,
    ("HE", "HI_ELECTRICITY"): +1,
    # Listing-market tightness -> price direction.
    ("HI_DOM", "HONOLULU_SF_MEDIAN"): -1,      # homes sit longer -> softer
    ("HI_DOM", "HONOLULU_ZHVI"): -1,
    ("HI_PRICE_CUTS", "HONOLULU_SF_MEDIAN"): -1,
    ("HI_PENDING_RATIO", "HONOLULU_SF_MEDIAN"): +1,
    ("HI_SF_SALES", "HONOLULU_ZHVI"): +1,      # volume leads price up
    # Fuel cost -> long-haul capacity -> arrivals. The pair that
    # motivated this table; it violates its own prediction.
    ("US_JETFUEL", "HI_VISITORS"): -1,
}

#: Pairs with NO directional prediction, and why. Kept as an explicit
#: table so "missing from EXPECTED_SIGN" is always a decision on the
#: record rather than an oversight — ``assert_sign_coverage`` enforces
#: that every registered pair appears in exactly one of the two.
_AMBIGUOUS_SIGNS: dict[tuple[str, str], str] = {
    ("HI_PERMIT_UNITS", "HONOLULU_ZHVI"):
        "Permits are procyclical (authorised when demand is strong, "
        "pushing the correlation positive) but also add supply "
        "(pushing it negative). Which dominates depends on the lag and "
        "on how binding Oahu's construction constraints are — no "
        "honest single prediction.",
    ("US_MORTGAGE30", "HONOLULU_ZORI"):
        "Higher mortgage rates price households out of buying and into "
        "renting (rents up), while also signalling a tightening cycle "
        "that cools rents. Standard housing economics gives both.",
    ("US_LFPR", "HI_UNEMPLOYMENT"):
        "Rising participation mechanically ADDS job-seekers to the "
        "denominator (measured unemployment up) while also indicating "
        "a labour market strong enough to draw people in (down).",
}


def expected_sign(predictor: str, target: str) -> Optional[int]:
    """+1/-1 if the mechanism predicts a direction, else None."""
    return EXPECTED_SIGN.get((predictor, target))


def mean_lead_corr(xcorrs: Sequence[LeadCorr], lags: int) -> Optional[float]:
    """Mean correlation over leads 1..``lags`` — the window the Granger
    test at that lag actually uses.

    Lead 0 is excluded deliberately: a contemporaneous correlation is
    not prediction, and including it lets same-month co-movement decide
    the direction of a claim about leading.
    """
    rs = [c.r for c in xcorrs if 1 <= c.lead <= lags]
    return sum(rs) / len(rs) if rs else None


def sign_materiality(nobs: Optional[int]) -> float:
    """|mean r| below this is not a direction, it is noise around zero.

    ``1/sqrt(n)`` — approximately one standard error of a correlation
    coefficient under the null, so it tightens as the sample grows
    (~0.05 at n=400, ~0.10 at n=100) instead of imposing one arbitrary
    cutoff on samples of very different sizes.
    """
    if not nobs or nobs <= 1:
        return float("inf")      # nothing to judge -> never material
    return 1.0 / math.sqrt(nobs)


def sign_matches(predictor: str, target: str,
                 xcorrs: Sequence[LeadCorr], lags: int,
                 nobs: Optional[int] = None) -> Optional[bool]:
    """Does the correlation run the way the mechanism predicts?

    Returns ``None`` — meaning "no directional verdict", NOT "passed" —
    when the pair makes no directional claim, when the row carries no
    Granger test (``lags == 0``: the descriptive mom12 rows have no
    test for a sign to contradict), or when |mean r| falls below
    ``sign_materiality``. That last case matters: the first version of
    this check had no floor and duly reported a mean r of +0.009 as
    "contradicting" its mechanism. A correlation that small is noise
    around zero; calling it a contradiction buries the two or three
    real violations in a wall of false ones, which is how a check stops
    being read.

    Judged on ``mean_lead_corr`` over leads 1..lags rather than on the
    peak-|r| lead. The first implementation of this check used the peak
    and was wrong, in a way its own output exposed on 2026-08-07: it
    flagged HI_PRICE_CUTS -> HONOLULU_SF_MEDIAN as a violation because
    the largest |r| across leads 0-18 sits at lead 12 (+0.322) — a
    12-month offset, which is what seasonality manufactures between two
    non-seasonally-adjusted series — while the mechanism's own horizon,
    lead 3, reads -0.272 with the predicted sign, and lead 3 is where
    the BH-passing test lives. Scoring a direction at a lead the test
    never used, and letting a seasonal echo outvote the signal, made
    the check worse than useless: it accused the one finding that had
    been verified most carefully.
    """
    want = EXPECTED_SIGN.get((predictor, target))
    if want is None or not lags:
        return None
    m = mean_lead_corr(xcorrs, lags)
    if m is None or abs(m) < sign_materiality(nobs):
        return None
    return (m > 0) == (want > 0)


def assert_sign_coverage(
    pairs: Sequence[tuple[str, str]] = HYPOTHESIS_PAIRS,
) -> None:
    """Every registered pair must declare a sign or declare ambiguity.

    Guards the failure mode this table exists to prevent: a new pair
    added without a directional claim silently skips the check and
    looks like it passed one.
    """
    missing = [p for p in pairs
               if p not in EXPECTED_SIGN and p not in _AMBIGUOUS_SIGNS]
    if missing:
        raise ValueError(
            "pairs missing a directional claim — add to EXPECTED_SIGN, or "
            f"to _AMBIGUOUS_SIGNS with a reason: {sorted(missing)}")
    both = [p for p in pairs
            if p in EXPECTED_SIGN and p in _AMBIGUOUS_SIGNS]
    if both:
        raise ValueError(f"pairs in BOTH sign tables: {sorted(both)}")


# Ticker transforms screened. mom12 is only used for cross-correlation
# (its 12-month overlap induces autocorrelation that invalidates the
# Granger F-test's iid-residual assumption).
TICKER_TRANSFORMS = ("log_return", "mom12")
GRANGER_TRANSFORMS = ("log_return",)


# ---------------------------------------------------------------------------
# Result containers
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LeadCorr:
    lead: int      # months x leads y
    r: float
    n: int


@dataclass(frozen=True)
class GrangerResult:
    f_stat: float
    p_value: float
    nobs: int
    lags: int


@dataclass(frozen=True)
class ScreenCandidate:
    """One tested (ticker, transform, target, lag) hypothesis."""
    ticker: str
    transform: str
    target: str
    lags: int
    granger: Optional[GrangerResult]
    best_xcorr: Optional[LeadCorr]
    bh_pass: bool = False
    note: str = ""
    #: True/False if the mean correlation over leads 1..lags runs the
    #: way the mechanism predicts; None when the pair makes no
    #: directional claim (EXPECTED_SIGN / _AMBIGUOUS_SIGNS) or the row
    #: carries no Granger test. False means the hypothesis as WRITTEN
    #: is contradicted even if bh_pass is True.
    sign_ok: Optional[bool] = None
    #: The mean lead-1..lags correlation that sign_ok was judged on,
    #: kept so the report can show what was actually scored.
    sign_stat: Optional[float] = None


@dataclass
class ScreenReport:
    candidates: list[ScreenCandidate] = field(default_factory=list)
    annual_descriptive: list[dict] = field(default_factory=list)
    q_fdr: float = 0.10
    exclude_2020: bool = False
    n_tests: int = 0
    #: How many months an explicit exclude_months list removed (0 for
    #: the default runs). Reported so a reader can tell a disaster-
    #: controlled run from a plain one.
    excluded_months: int = 0


# ---------------------------------------------------------------------------
# Series plumbing — everything is {month_index: value}
# ---------------------------------------------------------------------------

def month_index(year: int, month: int) -> int:
    return year * 12 + (month - 1)


def series_to_monthly_dict(rows: Sequence[dict]) -> dict[int, float]:
    """``[{year, period:"Mxx", value}]`` → ``{month_index: value}``."""
    out: dict[int, float] = {}
    for row in rows:
        period = row.get("period", "")
        if not (isinstance(period, str) and period.startswith("M")):
            continue
        m = int(period[1:])
        if not 1 <= m <= 12:
            continue
        out[month_index(int(row["year"]), m)] = float(row["value"])
    return out


def transform_series(values: dict[int, float], how: str,
                     *, span: int = 1) -> dict[int, float]:
    """Stationarising transforms on a month-indexed dict.

    ``diff``/``log_diff`` difference against the *nearest previous
    available* print within 3 months, scaled to a per-month rate — this
    is what makes bimonthly CPI usable on the monthly grid.
    ``mom12`` is the trailing 12-month log change (exact endpoints only).
    """
    out: dict[int, float] = {}
    if how in ("diff", "log_diff"):
        keys = sorted(values)
        for prev_k, cur_k in zip(keys, keys[1:]):
            gap = cur_k - prev_k
            if gap > 3:
                continue
            if how == "diff":
                out[cur_k] = (values[cur_k] - values[prev_k]) / gap
            else:
                if values[prev_k] <= 0 or values[cur_k] <= 0:
                    continue
                out[cur_k] = math.log(values[cur_k] / values[prev_k]) / gap
    elif how == "mom12":
        for k, v in values.items():
            prev = values.get(k - 12)
            if prev and prev > 0 and v > 0:
                out[k] = math.log(v / prev)
    elif how == "log_return":
        return transform_series(values, "log_diff")
    else:
        raise ValueError(f"unknown transform: {how!r}")
    return out


def _drop_2020(values: dict[int, float]) -> dict[int, float]:
    return {k: v for k, v in values.items()
            if not (month_index(2020, 1) <= k <= month_index(2020, 12))}


def months_to_indices(months: Iterable[str]) -> set[int]:
    """``["2023-08", ...]`` → month-index set (bad entries ignored)."""
    out: set[int] = set()
    for m in months:
        text = str(m)
        if len(text) < 7:
            continue
        try:
            year, month = int(text[:4]), int(text[5:7])
        except (TypeError, ValueError):
            continue
        if not 1 <= month <= 12:
            continue          # "2023-13" is junk, not December-plus-one
        out.add(month_index(year, month))
    return out


def _drop_months(values: dict[int, float],
                 drop: set[int]) -> dict[int, float]:
    """Remove specific months — the disaster-control analogue of
    ``_drop_2020``. See scripts/refresh_fema_disasters.py for where the
    month list comes from and why it is filtered before it gets here."""
    return {k: v for k, v in values.items() if k not in drop}


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def cross_correlation_lead(
    x: dict[int, float],
    y: dict[int, float],
    *,
    max_lead: int = 18,
    min_n: int = 24,
) -> list[LeadCorr]:
    """Pearson r of (x lagged by ``lead`` months, y) for lead = 0..max_lead."""
    out: list[LeadCorr] = []
    for lead in range(max_lead + 1):
        pairs = [(x[m - lead], y[m]) for m in y if (m - lead) in x]
        if len(pairs) < min_n:
            continue
        xa = np.array([p[0] for p in pairs])
        ya = np.array([p[1] for p in pairs])
        if xa.std() == 0 or ya.std() == 0:
            continue
        r = float(np.corrcoef(xa, ya)[0, 1])
        out.append(LeadCorr(lead=lead, r=r, n=len(pairs)))
    return out


def granger_f_test(
    y: dict[int, float],
    x: dict[int, float],
    *,
    lags: int,
    min_nobs_per_param: int = 8,
) -> Optional[GrangerResult]:
    """Restricted-vs-unrestricted OLS Granger F-test.

    H0: coefficients on x's lags are jointly zero in
    ``y_t ~ const + y_{t-1..t-p} + x_{t-1..t-p}``.

    Rows require ALL 2p+1 aligned months present (no interpolation).
    Returns None when nobs < min_nobs_per_param × (2p+1) — below that
    the F distribution's asymptotics are untrustworthy, so we refuse to
    report a p-value rather than report a fragile one.
    """
    rows_y, rows_ylag, rows_xlag = [], [], []
    for m in sorted(y):
        ylags = [y.get(m - i) for i in range(1, lags + 1)]
        xlags = [x.get(m - i) for i in range(1, lags + 1)]
        if None in ylags or None in xlags:
            continue
        rows_y.append(y[m])
        rows_ylag.append(ylags)
        rows_xlag.append(xlags)

    nobs = len(rows_y)
    n_params_u = 2 * lags + 1
    if nobs < min_nobs_per_param * n_params_u:
        return None

    yv = np.asarray(rows_y)
    ylag = np.asarray(rows_ylag)
    xlag = np.asarray(rows_xlag)
    const = np.ones((nobs, 1))

    X_r = np.hstack([const, ylag])
    X_u = np.hstack([const, ylag, xlag])

    def _rss(X: np.ndarray) -> float:
        beta, *_ = np.linalg.lstsq(X, yv, rcond=None)
        resid = yv - X @ beta
        return float(resid @ resid)

    rss_r, rss_u = _rss(X_r), _rss(X_u)
    df_num = lags
    df_den = nobs - n_params_u
    if df_den <= 0 or rss_u <= 0:
        return None
    f_stat = ((rss_r - rss_u) / df_num) / (rss_u / df_den)
    f_stat = max(f_stat, 0.0)
    p_value = float(stats.f.sf(f_stat, df_num, df_den))
    return GrangerResult(f_stat=float(f_stat), p_value=p_value,
                         nobs=nobs, lags=lags)


def benjamini_hochberg(pvals: Sequence[float], q: float = 0.10) -> list[bool]:
    """BH step-up FDR: True where the hypothesis is rejected at level q."""
    n = len(pvals)
    if n == 0:
        return []
    order = sorted(range(n), key=lambda i: pvals[i])
    threshold_rank = 0
    for rank, idx in enumerate(order, start=1):
        if pvals[idx] <= q * rank / n:
            threshold_rank = rank
    passed = [False] * n
    for rank, idx in enumerate(order, start=1):
        if rank <= threshold_rank:
            passed[idx] = True
    return passed


# ---------------------------------------------------------------------------
# The screen
# ---------------------------------------------------------------------------

def run_screen(
    ticker_series: dict[str, dict[int, float]],
    target_series: dict[str, dict[int, float]],
    *,
    pairs: Sequence[tuple[str, str]] = HYPOTHESIS_PAIRS,
    lags: tuple[int, ...] = (3, 6, 12),
    max_lead: int = 18,
    q: float = 0.10,
    exclude_2020: bool = False,
    exclude_months: Optional[Iterable[str]] = None,
) -> ScreenReport:
    """Run the pre-registered screen.

    ``ticker_series``: {symbol: {month_index: adj_close}} (raw levels).
    ``target_series``: {target_key: {month_index: raw value}} keyed by
    ``MONTHLY_TARGETS`` source ids resolved to target keys upstream.
    """
    report = ScreenReport(q_fdr=q, exclude_2020=exclude_2020)
    dropped = months_to_indices(exclude_months or ())
    report.excluded_months = len(dropped)

    def _prep(values: dict[int, float]) -> dict[int, float]:
        out = _drop_2020(values) if exclude_2020 else values
        return _drop_months(out, dropped) if dropped else out

    # Pre-transform everything once.
    xformed_tickers: dict[tuple[str, str], dict[int, float]] = {}
    for sym, levels in ticker_series.items():
        lv = _prep(levels)
        for tf in TICKER_TRANSFORMS:
            xformed_tickers[(sym, tf)] = transform_series(lv, tf)

    xformed_targets: dict[str, dict[int, float]] = {}
    for key, (_, tf) in MONTHLY_TARGETS.items():
        raw = target_series.get(key)
        if not raw:
            continue
        xformed_targets[key] = transform_series(_prep(raw), tf)

    granger_ps: list[float] = []
    granger_slots: list[int] = []

    for sym, target in pairs:
        tgt = xformed_targets.get(target)
        if tgt is None:
            report.candidates.append(ScreenCandidate(
                ticker=sym, transform="-", target=target, lags=0,
                granger=None, best_xcorr=None,
                note="target series unavailable"))
            continue
        for tf in TICKER_TRANSFORMS:
            src = xformed_tickers.get((sym, tf))
            if not src:
                continue
            xcorrs = cross_correlation_lead(src, tgt, max_lead=max_lead)
            best = max(xcorrs, key=lambda c: abs(c.r)) if xcorrs else None
            if tf in GRANGER_TRANSFORMS:
                for p in lags:
                    g = granger_f_test(tgt, src, lags=p)
                    cand = ScreenCandidate(
                        ticker=sym, transform=tf, target=target, lags=p,
                        granger=g, best_xcorr=best,
                        sign_ok=sign_matches(sym, target, xcorrs, p,
                                             g.nobs if g else None),
                        sign_stat=mean_lead_corr(xcorrs, p),
                        note="" if g else "insufficient aligned months",
                    )
                    report.candidates.append(cand)
                    if g is not None:
                        granger_ps.append(g.p_value)
                        granger_slots.append(len(report.candidates) - 1)
            else:
                report.candidates.append(ScreenCandidate(
                    ticker=sym, transform=tf, target=target, lags=0,
                    granger=None, best_xcorr=best,
                    note="descriptive xcorr only (overlapping-window "
                         "transform; no Granger test)",
                ))

    # BH across every Granger test actually run.
    report.n_tests = len(granger_ps)
    for slot, passed in zip(granger_slots,
                            benjamini_hochberg(granger_ps, q=q)):
        c = report.candidates[slot]
        report.candidates[slot] = ScreenCandidate(
            ticker=c.ticker, transform=c.transform, target=c.target,
            lags=c.lags, granger=c.granger, best_xcorr=c.best_xcorr,
            bh_pass=passed, note=c.note, sign_ok=c.sign_ok,
            sign_stat=c.sign_stat,
        )
    return report


def annual_descriptive_leads(
    ticker_annual: dict[str, dict[int, float]],
    target_annual: dict[str, dict[int, float]],
    pairs: Sequence[tuple[str, str]],
    *,
    max_lead_years: int = 3,
    min_n: int = 8,
) -> list[dict]:
    """Descriptive-only annual lead-lag correlations (n≈10–15 — NO test).

    Inputs are {name: {year: value}} in already-stationary form (log
    changes). Output rows are labelled untested; they exist to give the
    Phase-3 signal derivation a sanity anchor, not to make claims.
    """
    out: list[dict] = []
    for sym, target in pairs:
        x = ticker_annual.get(sym)
        y = target_annual.get(target)
        if not x or not y:
            continue
        for lead in range(max_lead_years + 1):
            common = [(x[yr - lead], y[yr]) for yr in y if (yr - lead) in x]
            if len(common) < min_n:
                continue
            xa = np.array([p[0] for p in common])
            ya = np.array([p[1] for p in common])
            if xa.std() == 0 or ya.std() == 0:
                continue
            out.append({
                "ticker": sym, "target": target, "lead_years": lead,
                "r": round(float(np.corrcoef(xa, ya)[0, 1]), 3),
                "n": len(common),
                "note": "descriptive only; n too small for inference",
            })
    return out


__all__ = [
    "MONTHLY_TARGETS",
    "HYPOTHESIS_PAIRS",
    "LeadCorr",
    "GrangerResult",
    "ScreenCandidate",
    "ScreenReport",
    "month_index",
    "series_to_monthly_dict",
    "transform_series",
    "cross_correlation_lead",
    "granger_f_test",
    "benjamini_hochberg",
    "run_screen",
    "annual_descriptive_leads",
]
