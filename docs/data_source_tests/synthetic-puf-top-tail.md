# Data-source test: public national tax microdata for the Hawaii top tail

Branch `data/synthetic-puf-top-tail`. Exploratory; nothing here changes a published number, a default or the synthesis. Run 2026-11-24 on the Mac mini.

## Question

Hawaii PUMS has no capital gains or itemized deductions and top-codes income, so `synthesize_top_filers` (Pareto alpha = 1.5) builds the $1M+ tail and the result is calibrated to DOTAX Table A-8 and IRS SOI class totals. The shape inside the class (how gains, dividends, business income and deductions are spread among filers above $200K / $500K / $1M) is assumed. Is there public, non-restricted national microdata that could supply that shape?

## What is obtainable

| Source | Obtainable? | Detail |
|---|---|---|
| PolicyEngine Enhanced CPS 2024 (`enhanced_cps_2024.h5`) | Yes, 107.8 MB, no login | Hugging Face repo `policyengine/policyengine-us-data`, repo commit `21280dca5995e978d706740a8a4b9b7860cfd7b6`, file sha256 `0a6b961ad363a421bde99f2c8e5d8f20370bcba45fd303050537a25bdd805b14`. Tax year 2024, 55,264 tax units, 41,314 households. Fetched with curl from the public `resolve/main` path; no 403/429. |
| PSL Tax-Data Model (TMD), GitHub `PSLmodels/tax-microdata-benchmarking` | No | Its README says the TMD files are built only by a user who holds a licence for the 2015 IRS PUF (`puf_2015.csv`, `demographics_2015.csv`). The repo's `tmd/storage/input` and `output` folders hold only README files; the repo has no releases and no data assets. Sub-national weight files are built from the same restricted national file. Not pulled, stopped on this route. |

Licence: the Hugging Face repo carries no licence file or card (the model card is empty; `README.md` does not exist on the Hub). The GitHub code repo's `pyproject.toml` points at a `LICENSE` file that is not in the root listing the API returned, and the API reports no licence. So the data licence could not be established from official sources. Treat as unknown: fine for a local analytical test, confirm with PolicyEngine before any redistribution or quoting of record-level values. TMD: the code repo has no licence file either (API: none); the data is restricted by the IRS PUF licence in any case.

Pulled and deleted: the one 108 MB file (kept under `/tmp`, outside the repo, removed at the end), a throwaway venv at `/tmp` with h5py/pandas (removed), small JSON/README fetches. Nothing was added to the project's lockfile or venv.

## The test

Scripts are in `docs/data_source_tests/synthetic_puf_top_tail/`:

- `ecps_units.py` builds tax units from the h5 (person arrays summed by `person_tax_unit_id`; unit weight = its household's `household_weight`).
- `ecps_tail_shape.py` computes counts, Pareto tail index and income composition above $200K/$500K/$1M.
- `hawaii_build_units.py` and `hawaii_tail_shape.py` load the cached calibrated base read-only with `scripts/_forecast_common.open_calibrated(shared=False)` (no population store written), run `b.units(alpha=1.5, top_premium=0.01)` (the chain's synthesis plus tail calibration, `tail_k = 1.351`), and compute the same statistics on the Hawaii units.
- `soi_tail_shape.py` does the same on the bundled national SOI Table 1.4 (TY2022) and Hawaii SOI Table 2 (TY2022).

Tail index three ways: slope of weighted N(>x) between thresholds, Hill (weighted MLE above a threshold), and the mean-ratio m/(m - t).

The ECPS file stores inputs only, not AGI. "AGI proxy" = wages + pass-through (partnership/S-corp, self-employment, farm, rental, estate) + capital gains (long, short, non-Sch-D) + dividends + taxable interest + taxable pensions/IRA/401k + unemployment/alimony/SALT refund. Taxable Social Security and above-the-line adjustments are not included. Itemized components are the stored inputs: mortgage interest, real estate taxes, charitable cash and non-cash, investment interest, other medical (before the AGI floor). State income tax is not stored, so SALT is incomplete.

## Result 1: the ECPS file cannot supply a top-tail shape

The released file's weights do not carry its PUF-imputed half. Verified by run:

- 41,314 households: the first 20,657 are original CPS records, the last 20,657 are exact clones (same household size and ages, paired by position). All clone households have `household_weight = 0` (clone weight 0.0 of 161.3M). Only 9,343 originals have positive weight. The file's own `enhanced_cps_2024.clone_diagnostics.json` reports clone household weight share of about 2e-5 percent, so this is the release design, not a bug in my read.
- The clones hold the imputed capital gains: $2.18T of capital gains sits on zero-weight clone units. The weighted view holds $0.31T.
- Weighted aggregates against bundled national SOI Table 1.4 (TY2022; ECPS is TY2024, so only gross mismatches count):

| Item | ECPS 2024 weighted | SOI 2022 |
|---|---|---|
| Wages | $12.4T | $9.7T |
| Capital gains | $0.31T | $1.24T |
| Dividends | $0.34T | $0.41T |
| Taxable interest | $0.21T | $0.13T |
| Pensions/IRA | $0.63T | $2.41T |
| Pass-through | $1.24T | $1.53T |

- Giving each clone its paired original's weight (a re-weighting I tried only as a diagnostic, not a PolicyEngine product) is far worse: wages $318T, dividends $207T. The clone values are not a usable population.
- `miscellaneous_income` carries $9.3T weighted in the released weights, with single records above $800M; it is outside my AGI proxy, but it shows the file's top tail contains outliers that PolicyEngine's own simulation presumably handles (not checked).

Weighted ECPS tail, as released (AGI proxy):

| Threshold | Weighted units | Unweighted | SOI 2022 returns |
|---|---|---|---|
| >= $200K | 16.9M | 19,137 | 12.5M |
| >= $500K | 2.84M | 15,031 | 2.48M |
| >= $1M | 0.53M | 12,808 | 0.80M |

Composition of the AGI proxy, ECPS as released:

| Class | Wages | Pass-through | Cap gains | Dividends | Interest |
|---|---|---|---|---|---|
| $200K-$500K | 84% | 4% | 4% | 3% | 2% |
| $500K-$1M | 71% | 16% | 3% | 4% | 2% |
| $1M+ | 6% | 84% | 0.1% | 0.0% | 0.1% |

Capital gains at 0.1% of income above $1M, against 31.9% in SOI Table 1.4, is the failure. Tail index (slope): 1.94 ($200-500K), 2.43 ($500K-1M), 1.22 ($1-2M), 1.06 ($1-5M), 0.84 ($2-10M); Hill above $1M 1.12 and mean-ratio 1.20. Indices below 1.5 above $1M with an infinite-mean look are not credible as a population shape. Itemized components above $1M came out above 100% of the proxy for charitable (outlier records), so they are not reported as shares.

Not tested: whether another ECPS release, the 2023/2022 CPS-based files, `small_enhanced_cps_2024.h5` or the state files (e.g. the Hawaii file) re-weight the clones. Only the one file listed above was pulled, to stay under the size limit; the repo also lists `pu2023.csv` (3.7 GB, over the size limit; contents not inspected), not pulled.

## Result 2: fallback comparison, model vs national SOI Table 1.4 and Hawaii SOI Table 2

Because no usable record file was obtainable, I did what the task specifies: compared the model's synthesized Hawaii tail with national SOI Table 1.4 (TY2022, bundled) and the Hawaii SOI Table 2 totals (TY2022, bundled), by run. Model = cached calibrated base after `units(alpha=1.5, top_premium=0.01)`. The $1M+ class is 24 synthetic rows (6 income tiers x 4 filing statuses); existing PUMS $1M+ rows get weight 0. Model weights are DOTAX resident filers, dollars are TY2023 tail-calibrated and aged to the PUMS dollar year; the SOI Hawaii counts are all returns filed from a Hawaii address, so levels are not exactly comparable, but the shape statistics are.

Tail index (slope of weighted N(>x)):

| Range | Model | Hawaii SOI T2 | National SOI 1.4 |
|---|---|---|---|
| $200K -> $500K | 2.02 | 2.03 | 1.77 |
| $500K -> $1M | 1.97 | 1.81 | 1.62 |
| $1M -> $2M | 1.50 (by construction) | n/a | 1.44 |
| $1M -> $5M | 1.50 | n/a | 1.38 |
| $2M -> $10M | 1.50 | n/a | 1.33 |

Hawaii mean-ratio alpha above $1M from Table 2 (mean AGI $3.50M on 1,970 returns): 1.40. Model: mean AGI above $1M is $4.18M (1.5 and the tail's 1.35x tail calibration scale, dollar-year aging), mean-ratio alpha about 1.31. Counts: model 40,784 / 6,418 / 1,633 above $200K / $500K / $1M; Hawaii SOI T2 44,360 / 6,890 / 1,970. (The 1,633 is about 4% under the TY2023 DOTAX target of 1,704 named in the module docstring; I did not chase that.)

Income composition:

| Class | Model: ordinary-labelled | Model: gains/investment | Hawaii SOI T2 wages/AGI | National wages | National cap gains | National pass-through | National dividends+interest |
|---|---|---|---|---|---|---|---|
| $200K-$500K | wages 65%, SE 7%, retirement 10% | interest+div+rent 14% | 57% | 69.5% | 5.1% | 9.1% | 3.6% |
| $500K-$1M | wages 64%, SE 16%, retirement 3% | 15% | 41% | 58.0% | 10.8% | 18.4% | 5.3% |
| $1M+ | wages 68.5%, SE 0% | 31.5% (capital gains) | 21% | 27.6% | 31.9% | 26.4% | 8.5% |

By national tier above $1M: capital gains 16.4% ($1-2M), 23.3% ($2-5M), 30.3% ($5-10M), 47.6% ($10M+). Model: 20.9%, 25%, 30%, 44.7%, 47%, 49.4% across its six tiers.

Itemized/effective deduction as a share of AGI:

| Class | Model | National SOI 1.4 (all returns, includes SALT) |
|---|---|---|
| $200K-$500K | about 18% | 4.7% |
| $500K-$1M | about 15% | 4.9% |
| $1M+ | about 9.7% (8.4%-11.9% across tiers) | 5.8% |

## Where the model's shape differs materially

1. Pass-through, dividends and interest are not modelled at $1M+. The synthetic rows put all non-gain income in `primary_wagp` and gains in `primary_intp`. National SOI has wages 28%, pass-through 26%, dividends+interest 8.5% above $1M; Hawaii SOI Table 2 wages are only 21% of AGI above $1M (41% at $500K-$1M), against 68.5% implied by the model's earned-income column and 64% in the PUMS-based $500K-$1M. The Hawaii capital-gains rate cap applies to gains but not to the other pieces, so lumping them as "wages" matters only when a reform touches gains versus ordinary income. For a Hawaii reform that only moves rates and brackets on ordinary-rate income it does not matter, because the tail is calibrated to DOTAX tax.
2. Gains at the lower end of $1M+ are above national: 20.9% in the $1-2M tier against 16.4% national. The Hawaii DOTAX Table 21 baseline (20.9%, residents at $400K+) is the reason, so this is an explicit Hawaii choice, not a defect.
3. The tail index: the model's Pareto 1.5 is a fair single number between national 1.44 ($1-2M) and 1.33 ($2-10M); Hawaii's own mean-ratio 1.40 is a little lower. The national index falls as income rises (1.77, 1.62, 1.44, 1.38, 1.33) while the model has a hard step from about 2.0 to 1.5 at $1M. Below $1M the PUMS-based model is close to Hawaii SOI (2.02/1.97 vs 2.03/1.81).
4. Deductions: the model's effective deduction is 1.7x national at $1M+ and 3.7x at $200-500K per dollar of AGI. National figures are per all returns, include SALT (Hawaii excludes state income tax), and Hawaii mortgage interest is heavy, so a gap is expected, but the size is untested and is a candidate for a Hawaii-specific check (DOTAX itemized tables), not for a national import.

DOTAX class totals (Table A-8: 1,704 resident returns and $441M tax before credits above $1M, TY2023) enter only as the calibration target; the model reproduces them by construction (count within 4%, tax by `tail_k`), so they carry no shape information beyond the average of about $259K tax per return.

## Is a national shape defensible for Hawaii?

Partly. The Hawaii SOI Table 2 totals are the binding constraint. Hawaii's wage share above $1M (21%) is already close to the national 27.6%, not to the model's 68.5%, which says the national composition (large pass-through and gains, small wage share) describes Hawaii's top class better than the model's two-bucket split does. The tail index also lies inside the national range. Hawaii has a distinctive feature the national shape cannot give: gains at $400K+ run at 20.9% of income (DOTAX Table 21), below the national tier average of 31.9% at $1M+, which the repo already addresses with a 0.95 adjustment in `soi_top_anchor`. Hawaii SOI Table 2 itself is only published for 10 AGI classes with wages, so it cannot validate the finer split (dividends, pass-through, gains); a national shape would still be unchecked on those.

## What adopting a national shape would touch

Already in the repo: `calibration/soi_top_anchor.py` (`synthesize_top_filers_from_soi`, `SoiAnchor` in `projection/options.py`) re-synthesizes the $1M+ filers from national SOI Table 1.4 tier counts and per-tier shares for wages, gains, business, dividends and interest, with the 0.95 Hawaii gains adjustment. It is the national-shape path in code form. It still assigns the shares to two columns today (the ECPS test would have replaced that input, not the structure). Adopting any national composition would touch: `scenarios/top_income_synthesis.py` (column mapping in Override 3), `adjustments/ultra_high_income_synthesizer_v2.py` (`SYNTHETIC_CG_SHARES`, tier counts), the tail calibration `calibrate_synthetic_tail_to_tax_target` (`tail_k` would move), `calibration/cg_anchor.py` and the capital-gains chain step (gain shares), then every published number that depends on the $1M+ tail: the Act 24 revenue steps, the vs-FY26 and pre-Act-46 runs, the simulator golden fixtures, the site pages and the headline-figure test. None of that was run or changed here.

## Limits

- ECPS test used one file, one AGI proxy (no taxable Social Security, no adjustments), SALT absent, TY2024 vs TY2022 SOI. The ECPS conclusion is about this release's weights, not the method.
- Hawaii SOI Table 2 gives only AGI, wages and a few credits by class in the bundled JSON; composition checks beyond wages rely on national Table 1.4.
- National `itemized_M` is the bundled parse of Table 1.4 (total 0.67T across classes, plausible), not re-checked against the IRS workbook.
- The model's 24 tail rows make Hill estimates meaningless above $1M (discrete tiers); only slopes and mean-ratios are quoted.
- Licence of the ECPS file not established.

## Verdict

Reject ECPS (this release) as a top-tail shape source. TMD is not obtainable without the IRS PUF. The fallback comparison against national SOI Table 1.4 and Hawaii Table 2 says: keep alpha = 1.5, but the unmodelled pass-through/dividend split and the deduction level are the two shape assumptions that differ materially; the first could be addressed with the existing national-SOI tier shares (the `SoiAnchor` path), the second needs Hawaii data, not national.

Worth a retry if a PolicyEngine release gives clone households non-zero weight, or if PSL publishes a no-PUF TMD.
