# Data-source test: IRS/Census EITC participation rate (Hawaii)

Tested 2026-11-24 on branch `data/eitc-participation-rate`. Read-only: no factor, default, published number, `site/` or `runs/` file changed.
Script: `analyses/eitc_participation_check.py` (run from the repo root). Data: `docs/data_source_tests/data/irs_eitc_participation_hi_national.csv`.

## Sources (both re-fetched with curl on 2026-11-24, HTTP 200, no 403)

- Participation rate by state, TY2014-TY2022 (page last reviewed 19-Aug-2026):
  https://www.eitc.irs.gov/eitc-central/participation-rate/eitc-participation-rate-by-states (redirects to
  https://www.irs.gov/tax-professionals/eitc-central/eitc-participation-rate-by-state).
  Method per the page: ACS records modelled for EITC eligibility (denominator) vs IRS payment records (numerator); estimated by Census CES with IRS. Page footnote: the CPS-based national TY2022 figure is 78%.
- IRS EITC statistics by state, TY2019-TY2024 (returns processed, dollars):
  https://www.irs.gov/tax-professionals/eitc-central/statistics-for-tax-returns-with-the-earned-income-tax-credit-eitc
- Repo inputs: `data/calibration/dotax_eitc_2024.json` (DOTAX report, TY2024), `data/external/irs_soi_hi_eitc_panel.csv`, METHODOLOGY.md "Take-up imputation"/"Take-up propensity".

## What was pulled (checked against the live pages; the CSV saved by the earlier agent matched every value)

| TY | HI rate | US rate | HI IRS returns (K) | HI IRS $M |
|---|---|---|---|---|
| 2014 | 82.5% | 79.4% | - | - |
| 2015 | 80.9% | 79.2% | - | - |
| 2016 | 81.7% | 78.6% | - | - |
| 2017 | 77.8% | 77.8% | - | - |
| 2018 | 77.8% | 78.1% | - | - |
| 2019 | 83.6% | 79.3% | 94 | 203 |
| 2020 | 76.8% | 76.3% | 90 | 192 |
| 2021 | 81.6% | 80.8% | 113 | 208 |
| 2022 | 83.3% | 80.8% | 82 | 181 |
| 2023 | not published | not published | 82 | 196 |
| 2024 | not published | not published | 81.8 | 206.2 |

HI mean 2014-22 80.7%, sd 2.6 pts (n=9). 2021 is inflated by the one-year ARPA childless expansion (113K returns). The newest published rate is TY2022; none exists for the model's calibration year (TY2024). The IRS "processed" counts (82K for 2022) differ from the repo's SOI panel (84,970) and the 84,010 in METHODOLOGY.md: different vintages/tabulations.

## The test (run, TY2022 and TY2024, cached chain tax units `data/artifacts/working_family_units.pkl`)

Eligible = weighted units with federal `eitc_amount > 0`. "Raw" = before the chain's by-children IRS reweight (`_apply_eitc_reweight`); "reweighted" = as the chain uses them.

TY2022 (rate 83.3%, SOI claims 84,970 = 26,900 / 27,820 / 18,460 / 11,790 for 0/1/2/3+ kids):
- IRS-implied eligible = 84,970 / 0.833 = 102,005.
- Model eligible raw 68,620 ($128.4M): 32.7% short. Reweighted 86,918 ($177.0M): 14.8% short (15,087 units).
- Model-implied participation (claims / model eligible): raw 123.8% (impossible), reweighted 97.8%. By kids, raw: 0 kids 93.2%, 1 kid 172.7%, 2 kids 136.2%, 3+ 116.6%; reweighted 1/2/3+ are 100.0% by construction (the reweight targets the claim counts, so it cannot also test participation).
- Shortfall at 83.3% by bucket (raw): 0 kids 3,445; 1 kid 17,288; 2 kids 8,606; 3+ 4,045. The shortfall is concentrated in families with children.

TY2024 (HI rate not published; the 2022 rate is used as a stand-in, so this row is an assumption): claims 81,800; model eligible raw 83,063 (participation 98.5%), reweighted 92,247 (88.7%). Eligible dollars implied at 83.3%: $247.5M vs model $172.1M raw / $196.9M reweighted.

Fitted factor: `takeup_factors` fits one uniform state-EITC factor to DOTAX $76.981M (TY2024). Published manifest `runs/working_family_credits/manifest.json`: takeup_eitc 0.9809 (read from the file, not recomputed). The script's re-derivation (DOTAX $ / (0.4 x eligible federal $)) gives 0.978 on reweighted units and 1.118 on raw units. So the factor reads about 0.98, not 0.83, because the reweight has already inflated eligibles to the claim counts. (The 0.4 state-rate in the script is an approximation of the Act 163 schedule; the manifest is the authority.)

## Decomposition

If the IRS rate is right, true take-up is about 83% and the model's eligible population is too small: by about 15% after the reweight (33% before it). A factor near 1 therefore mixes about 0.83 of true take-up with an eligible-count error of roughly +20% (1/0.83) that the reweight and the dollar fit absorb. The direction is the same in every TY2022 bucket: the model undercounts eligible units, it does not overcount. Because the factor is fitted to DOTAX dollars, the credit dollars are matched whichever story holds; only the split between take-up and eligibility changes.

Childless share check: IRS/SOI TY2022 childless share of claimants 26,900/84,970 = 31.7% (the repo's benchmark). Model childless share of eligible units, TY2022: 28,848/86,918 = 33.2% reweighted (42.0% raw); TY2024 34,177/92,247 = 37.0%. This run does not reproduce the earlier 31.5% (a different measure; I did not trace its script). It agrees in direction only: childless units are not reweighted and the model's childless count is near the IRS one (93% participation), so the missing eligible units sit mostly in families with children. At a uniform 83.3% the childless bucket would also be short by about 3.4K.

## Limits

- The Census rate's denominator is an ACS-modelled eligible count with its own eligibility rules, and its numerator includes erroneous claims; the model's eligibility (PUMS tax units, poverty scaling alpha 0.5) differs. A gap between them is not purely model error.
- Hawaii's 9-year series is noisy (sd 2.6 pts, range 76.8-83.6%); 2022 sits near the top. At the 80.7% mean the implied eligible count is about 105K.
- No rate for TY2023/TY2024; the TY2024 line uses a stand-in.
- No uncertainty interval was on the page; the technical-paper PDF was not fetched.
- The reweight uses the same claims being compared, so reweighted participation is circular.
- The IRS statistics page's counts by number of children were not pulled; by-kids claims come from the repo's SOI figures.

## Verdict

Reject as a second calibration target; keep as a diagnostic. The rate cannot fit a take-up factor because the model's eligible set is itself calibrated to IRS claims, so participation and eligible-count error are confounded; the published rate also has a different denominator, a noisy Hawaii series and no TY2024 value. What it does tell us: true take-up is near 83%, and the 0.98 factor is not a take-up rate. Anything that reads the factor as "98% of eligibles claim" (propensity work, take-up sensitivity) should be told so. Using it as a target would first need an eligible count independent of IRS claims.
