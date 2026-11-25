# Data-source test: IRS SOI county data x ACS S1701 panel for `eitc_poverty_alpha`

Branch `data/soi-county-panel`. Exploratory only: no published number, stored value, `site/` or `runs/` file changed.

## Question

`eitc_poverty_alpha` (0.5) is a literature half-elasticity, not estimated
(`adjustments/eitc_poverty_scaling.py`; METHODOLOGY.md "What's still uncalibrated"). The
earlier Hawaii-only fit (Honolulu proxy, 2 year-pairs) gave 0.71 and was called unidentified.
Can a multi-county panel identify it?

## Data

- **IRS SOI "County Data", AGI-class files**, `https://www.irs.gov/pub/irs-soi/{yy}incyallagi.csv`
  (HTTP 200 for 2011 and 2013-2023; 14-36 MB each). Columns used: `N1` (returns), `A00100` (AGI),
  `N59660`/`A59660` (EITC returns / amount, $000), summed over the 8 AGI classes per county.
  - **2012 not obtained**: `12incyallagi.csv` returns a 404 HTML page (also `12incyall.csv`). 2011 has no neighbouring year, so it contributes no pair.
  - 2024 file (`24incyall.csv`): 404, not published as of 2026-11-24.
- Panel extract: `data/external/irs_soi_county_eitc_panel.csv` (612 KB, 8,550 rows: 12 tax years x 90 counties x 8 AGI classes; 2011, 2013-2023). I did not fetch it; the prior agent did. **Verified this session**: I re-downloaded the IRS files for 2011, 2013 and 2023 and compared the extract row by row on N1, A00100, N59660, A59660: max absolute difference 0 (630/720/720 county-class rows matched). Other years were not re-checked.
- **ACS**: bundled `calibration_panel/acs_panel.json` (1-year vintage; S1701_C03_001E poverty rate and B19013_001E median household income, with MOEs; 90 counties incl. all four Hawaii counties; 2010-2019 and 2021-2025; no 2020 1-year exists). Read from the repo, not re-fetched from the Census API. The SOI extract uses the same 90 counties.
- Joined on (year, county FIPS): ACS year t with SOI tax year t.

## Test

Per spec: `log(EITC[t+1]/EITC[t]) - log(B19013[t+1]/B19013[t]) = alpha * log(pov[t+1]/pov[t]) + e`, pooled over
consecutive pairs, no intercept, pairs 2019-20, 2020-21, 2021-22 dropped. Pairs used: 2013-14 through 2018-19 (6) and
2022-23 (1); 87-89 counties each, 613 rows. SEs are cluster-robust (CR1) by county (by year-pair for Hawaii-only runs: 7 clusters, so those SEs are unreliable). Script: `scripts/soi_county_alpha_panel.py`; output verbatim:

```
                                               spec  alpha    se     lo    hi   n  clusters
      [eitc_n] all, pooled, no intercept (the spec)  0.156 0.034  0.089 0.223 613        89
                       [eitc_n] all, + year-pair FE  0.083 0.031  0.022 0.144 613        89
                          [eitc_n] all, + county FE  0.083 0.033  0.018 0.147 613        89
                   [eitc_n] all, + county & year FE  0.078 0.032  0.015 0.141 613        89
                  [eitc_n] all, spec, EITC-weighted  0.456 0.054  0.350 0.562 613        89
  [eitc_n] all, spec, precision-weighted (1/se_x^2)  0.462 0.054  0.355 0.569 613        89
     [eitc_n] all, spec, x_se<0.10 (large counties)  0.509 0.045  0.421 0.598 313        47
             [eitc_n] x_se<0.10, + county & year FE  0.134 0.032  0.071 0.196 313        47
                   [eitc_n] Hawaii 4 counties, spec -0.002 0.099 -0.196 0.193  28         7
              [eitc_n] Hawaii 4 counties, + year FE -0.009 0.113 -0.231 0.212  28         7
             [eitc_n] Honolulu only (existing spec)  0.973 0.204  0.574 1.373   7         7
         [eitc_n] all, spec, excluding 2022-23 pair  0.167 0.041  0.087 0.247 524        88
                    [eitc_n] all, 2022-23 pair only  0.086 0.043  0.003 0.169  89        89
    [eitc_amt] all, pooled, no intercept (the spec)  0.150 0.035  0.082 0.218 613        89
                     [eitc_amt] all, + year-pair FE  0.081 0.031  0.020 0.141 613        89
                        [eitc_amt] all, + county FE  0.100 0.035  0.031 0.168 613        89
                 [eitc_amt] all, + county & year FE  0.074 0.032  0.011 0.136 613        89
                [eitc_amt] all, spec, EITC-weighted  0.460 0.058  0.345 0.574 613        89
[eitc_amt] all, spec, precision-weighted (1/se_x^2)  0.460 0.057  0.348 0.573 613        89
   [eitc_amt] all, spec, x_se<0.10 (large counties)  0.510 0.044  0.423 0.597 313        47
           [eitc_amt] x_se<0.10, + county & year FE  0.141 0.031  0.080 0.203 313        47
                 [eitc_amt] Hawaii 4 counties, spec  0.011 0.092 -0.169 0.191  28         7
            [eitc_amt] Hawaii 4 counties, + year FE -0.012 0.111 -0.230 0.207  28         7
           [eitc_amt] Honolulu only (existing spec)  0.742 0.375  0.007 1.478   7         7
       [eitc_amt] all, spec, excluding 2022-23 pair  0.165 0.041  0.084 0.246 524        88
                  [eitc_amt] all, 2022-23 pair only  0.059 0.059 -0.057 0.175  89        89

pairs per period: {2013: 87, 2014: 87, 2015: 87, 2016: 87, 2017: 88, 2018: 88, 2022: 89}
var(x)=0.02544  mean noise var(x_se^2)=0.01894  -> reliability ~ 0.26
var(y)=0.00439  corr(x,y)=0.210

Hawaii pair rows:
 geoid  period  eitc_n0  eitc_n1  pov0  pov1      x  x_se      y
15001    2013  18860.0  18700.0  19.5  17.9 -0.086 0.138 -0.095
15001    2014  18700.0  18630.0  17.9  18.8  0.049 0.131 -0.132
15001    2015  18630.0  17980.0  18.8  15.4 -0.199 0.136  0.039
15001    2016  17980.0  17600.0  15.4  15.0 -0.026 0.133 -0.053
15001    2017  17600.0  17090.0  15.0  16.2  0.077 0.132 -0.030
15001    2018  17090.0  17780.0  16.2  13.2 -0.205 0.149 -0.113
15001    2022  15610.0  15640.0  15.1  17.5  0.148 0.136 -0.025
15003    2013  76640.0  75280.0   9.4   9.7  0.031 0.068 -0.035
15003    2014  75280.0  72550.0   9.7   9.2 -0.053 0.064 -0.072
15003    2015  72550.0  67710.0   9.2   8.5 -0.079 0.079 -0.110
15003    2016  67710.0  64950.0   8.5   8.4 -0.012 0.078 -0.051
15003    2017  64950.0  62540.0   8.4   7.8 -0.074 0.070 -0.076
15003    2018  62540.0  63030.0   7.8   8.1  0.038 0.081 -0.028
15003    2022  54310.0  53870.0   9.7   9.0 -0.075 0.074 -0.074
15007    2013   6260.0   6140.0  10.1  11.9  0.164 0.205 -0.094
15007    2014   6140.0   6020.0  11.9  10.2 -0.154 0.204 -0.193
15007    2015   6020.0   5790.0  10.2   6.1 -0.514 0.202  0.039
15007    2016   5790.0   5600.0   6.1   9.5  0.443 0.196 -0.195
15007    2017   5600.0   5250.0   9.5   6.4 -0.395 0.226 -0.029
15007    2018   5250.0   5450.0   6.4   8.6  0.295 0.295  0.024
15007    2022   4630.0   4540.0   6.6   7.6  0.141 0.300 -0.186
15009    2013  14250.0  13910.0   9.5  13.2  0.329 0.151 -0.046
15009    2014  13910.0  13510.0  13.2   9.7 -0.308 0.162 -0.080
15009    2015  13510.0  12820.0   9.7   8.3 -0.156 0.183 -0.077
15009    2016  12820.0  12410.0   8.3   9.7  0.156 0.175 -0.137
15009    2017  12410.0  12050.0   9.7   7.1 -0.312 0.174 -0.062
15009    2018  12050.0  12550.0   7.1  11.9  0.516 0.187  0.067
15009    2022  10440.0  10460.0   8.2   8.8  0.071 0.175  0.126

per-period cross-county slope:
  2013->2014: alpha=0.14 se=0.06 n=87
  2014->2015: alpha=0.38 se=0.07 n=87
  2015->2016: alpha=0.18 se=0.06 n=87
  2016->2017: alpha=0.01 se=0.10 n=87
  2017->2018: alpha=0.18 se=0.08 n=88
  2018->2019: alpha=0.17 se=0.04 n=88
  2022->2023: alpha=0.09 se=0.04 n=89
large counties (x_se<0.10): n=313 var(x)=0.00422 noise=0.00338 reliability~0.20
```

## Reading the numbers

- **Spec as written (all counties, unweighted): alpha = 0.16 (SE 0.03; 95% CI 0.09-0.22).** With year-pair and/or county fixed effects: 0.08 (SE 0.03; CI 0.02-0.14). The amount outcome gives the same (0.15; 0.07-0.10 with FE).
- **Weighted** (by baseline EITC, or 1/se_x^2) or restricted to large counties (ACS x_se<0.10): **0.46-0.51** (SE 0.05). Adding county+year FE to the large-county sample drops it back to 0.13. The estimate moves between 0.08 and 0.51 on weighting and intercept choices alone; the no-intercept spec is not robust (mean y is negative because EITC counts fell through most of the window, and an omitted common trend loads on x).
- **Hawaii, 4 counties:** -0.00 (SE 0.10) with or without year FE, 28 pair-rows, 7 clusters. **Honolulu only (the existing spec): 0.97 (counts) / 0.74 (amount)**, n=7 pairs, SE 0.20-0.38. The earlier 0.71 is within noise; the other three counties pull the Hawaii pool to zero.
- **Measurement error dominates.** Var(x)=0.0254 but mean ACS sampling-noise variance (from MOEs) is 0.0189: reliability ~0.26 (0.20 among large counties). Unweighted runs are heavily attenuated. A 1/reliability correction would multiply the FE estimates several-fold, but it is unreliable: B19013 noise enters y with opposite sign and is negatively correlated with poverty noise, which biases alpha upward. The two biases oppose; I did not net them.
- Per-period cross-county slopes are all positive (0.01-0.38; 2022-23: 0.09): a stable positive within-year association, small relative to 0.5.
- corr(x, y) = 0.21.

## Does the data identify alpha?

Only weakly, and not as a causal elasticity.
1. The panel shows a positive association clearly different from zero (about 0.1-0.2 unweighted, ~0.5 weighted) between EITC growth net of median growth and poverty-rate growth across counties. That is not the response of a county's EITC to a poverty change inside the forecasting model.
2. The regressor is mostly sampling noise (reliability 0.2-0.3), and results swing with weighting and the no-intercept restriction.
3. Hawaii-specific identification is nil: 4 counties, 7 pairs each, log-poverty SEs of 0.07-0.30.
4. EITC counts also move with federal law (ARPA 2021, expiry 2022) and filing behavior; 2022-23 still carries post-ARPA adjustment.

## Verdict: adopt with caveats (as evidence, not as a replacement value)

- Do not replace 0.5 with a point estimate from this panel. Defensible reading: alpha in roughly [0.1, 0.5]; 0.5 is the top of that range (matches the weighted and large-county fits, 0.46-0.51); unweighted and FE fits are well below it.
- The panel does not support alpha > 0.5 and shows the Honolulu-only 0.71 is not meaningful.
- Worth keeping as infrastructure: a 90-county x 7-pair SOI/ACS panel the existing spec runs on. If a sensitivity band is used, 0.1 / 0.3 / 0.5 is better supported than 0.3 / 0.5 / 0.7.
- Not done: errors-in-variables correction (needs an instrument for poverty, e.g. a lagged measure), IRS 2012 (unobtainable at the usual URL), TY2024 (unpublished), pre-2011 years, an intercept-included precision-weighted variant.

## Verified by run vs taken from documents

- Run this session: SOI extract equality check against fresh IRS downloads (2011, 2013, 2023); every regression number above.
- Taken from repo documents: the spec, the earlier 0.71 result, the ACS panel's provenance (contents inspected; not re-fetched).
