# State UI-claims feature ablation — 2026-08-22

Panel: 1440 series; anchors [2014, 2015, 2016, 2017, 2018, 2019, 2020, 2021, 2022]; horizons [1, 2, 3, 4, 5]. Both arms carry the shipped county, market and national-macro channels; the only difference is the 4 `ui_claims_*` columns (51 states). Gates: no RMSE regression > 2% absolute panel-wide or on HI-restricted S2301_C04_001E; CI90 coverage in [85%, 95%]; partial-dependence sign must match the screen mechanism.

## Panel-wide — ensemble_with_ml, baseline (A) vs +ui_claims (B)

| indicator | RMSE A | RMSE B | ΔRMSE | coverage A → B | flag |
|---|---|---|---|---|---|
| B01002_001E | 0.0187 | 0.0189 | +0.0002 | 88.68% → 88.65% |  |
| B19013_001E | 0.0681 | 0.0680 | -0.0000 | 86.46% → 87.06% |  |
| B20002_001E | 0.0677 | 0.0679 | +0.0002 | 85.32% → 86.39% |  |
| B25058_001E | 0.0741 | 0.0744 | +0.0003 | 86.66% → 86.66% |  |
| B25064_001E | 0.0723 | 0.0723 | -0.0001 | 87.97% → 87.60% |  |
| B25071_001E | 0.0956 | 0.0950 | -0.0007 | 88.24% → 88.51% |  |
| B25077_001E | 0.0881 | 0.0882 | +0.0001 | 86.03% → 85.99% |  |
| S1501_C02_014E | 0.0192 | 0.0192 | -0.0000 | 89.96% → 90.49% |  |
| S1501_C02_015E | 0.0844 | 0.0847 | +0.0003 | 88.38% → 87.91% |  |
| S1701_C03_001E | 0.1974 | 0.1971 | -0.0003 | 85.52% → 85.09% |  |
| S2301_C04_001E | 0.3687 | 0.3669 | -0.0018 | 90.53% → 90.43% |  |
| homeownership_rate | 0.0429 | 0.0432 | +0.0003 | 87.24% → 87.77% |  |
| in_migration_rate | 0.2278 | 0.2268 | -0.0011 | 89.60% → 91.08% |  |
| pct_professional | 0.0744 | 0.0742 | -0.0002 | 88.89% → 88.75% |  |
| pct_service_occupations | 0.1351 | 0.1353 | +0.0002 | 89.60% → 89.73% |  |
| vacancy_rate | 0.2692 | 0.2702 | +0.0010 | 90.80% → 90.90% |  |

## Hawaii-restricted (4 counties) — the cell the screen found

Gated on S2301_C04_001E only — that is the pair the screen found. The other rows are context: with 4 counties they are too thin to gate on, and coverage flags here are marked `pre-existing` when arm A was already outside the band for reasons that predate this channel.

| indicator | RMSE A | RMSE B | ΔRMSE | coverage A → B | flag |
|---|---|---|---|---|---|
| B01002_001E | 0.0207 | 0.0207 | +0.0000 | 88.97% → 88.97% |  |
| B19013_001E | 0.0824 | 0.0822 | -0.0002 | 85.29% → 84.56% | **NEWLY OUT OF BAND** |
| B20002_001E | 0.0895 | 0.0896 | +0.0001 | 80.88% → 83.82% | pre-existing coverage |
| B25058_001E | 0.0870 | 0.0879 | +0.0009 | 77.94% → 76.47% | pre-existing coverage |
| B25064_001E | 0.0821 | 0.0816 | -0.0006 | 86.76% → 86.76% |  |
| B25071_001E | 0.0903 | 0.0882 | -0.0021 | 83.82% → 87.50% |  |
| B25077_001E | 0.0687 | 0.0698 | +0.0011 | 87.50% → 88.24% |  |
| S1501_C02_014E | 0.0128 | 0.0128 | -0.0000 | 94.12% → 94.12% |  |
| S1501_C02_015E | 0.0835 | 0.0840 | +0.0005 | 88.24% → 86.76% |  |
| S1701_C03_001E | 0.2472 | 0.2457 | -0.0015 | 76.47% → 74.26% | pre-existing coverage |
| S2301_C04_001E | 0.4670 | 0.4647 | -0.0024 | 84.85% → 85.61% |  |
| homeownership_rate | 0.0438 | 0.0440 | +0.0002 | 83.09% → 85.29% |  |
| in_migration_rate | 0.1712 | 0.1725 | +0.0014 | 84.56% → 87.50% |  |
| pct_professional | 0.0865 | 0.0870 | +0.0005 | 84.56% → 83.82% | pre-existing coverage |
| pct_service_occupations | 0.1467 | 0.1477 | +0.0010 | 82.35% → 82.35% | pre-existing coverage |
| vacancy_rate | 0.1404 | 0.1514 | +0.0110 | 87.50% → 85.29% |  |

## Honolulu County (15003) MAPE — the standing CLAUDE.md gate

`ensemble_with_ml`, 544 folds. Reported as an A-vs-B delta: the 6.76% baseline in CLAUDE.md has no script in this repo that emits it, so its definition is not recoverable and the absolute level below is not claimed to be comparable to it. The delta on identical folds is what the gate is actually asking.

| baseline | +ui_claims | Δ |
|---:|---:|---:|
| 5.73% | 5.84% | +0.105pp |

## ui_claims_* permutation importance

- **S2301_C04_001E** (3238 rows):
    - ui_claims_log_lag0: +0.00535 ± 0.00067
    - ui_claims_chg1: +0.00256 ± 0.00013
    - ui_claims_chg2: +0.00468 ± 0.00090
    - ui_claims_rel3: +0.01505 ± 0.00137
- **S1701_C03_001E** (3312 rows):
    - ui_claims_log_lag0: +0.00857 ± 0.00055
    - ui_claims_chg1: +0.00442 ± 0.00041
    - ui_claims_chg2: +0.01010 ± 0.00120
    - ui_claims_rel3: +0.00267 ± 0.00015
- **B19013_001E** (3312 rows):
    - ui_claims_log_lag0: +0.01030 ± 0.00079
    - ui_claims_chg1: +0.00188 ± 0.00010
    - ui_claims_chg2: +0.00284 ± 0.00039
    - ui_claims_rel3: +0.00580 ± 0.00049

## Sign check — does the channel move the way its mechanism says?

Partial dependence of predicted S2301_C04_001E log-growth on `ui_claims_rel3` (claims vs the state's own 3-yr baseline):

| -0.40 | -0.35 | -0.30 | -0.25 | -0.20 | -0.15 | -0.10 | -0.05 | -0.00 |
|---|---|---|---|---|---|---|---|---|
| -0.0477 | -0.0462 | -0.0048 | +0.0006 | +0.0076 | +0.0121 | +0.0116 | +0.0132 | +0.0144 |

Net slope (high − low): **+0.0621** log-growth. Mechanism predicts positive (claims up → unemployment up); materiality floor ±0.001.

## Verdict: **GATE PASSED** — no RMSE regression panel-wide or on the Hawaii-restricted S2301 cell, coverage in band, and the fitted channel moves with its stated mechanism.
