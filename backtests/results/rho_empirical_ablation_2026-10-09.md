# Empirical member-correlation ablation (2026-10-09)

Anchors 2014–2022, h=1..5, ML member on. Arm A = v1 literal ρ; Arm B = `enable_empirical_rho=True` (folds + blend at empirical ρ); Arm C = arm-A folds blended at arm-B ρ (blend-level effect only). Metrics on the bias+κ-corrected ensemble synthesised from the fold cache (`compare_ml_ablation` recipe + level-anchor fusion).

## Estimated correlations (tuning folds, h-marginalised; short/long in parentheses)

| Indicator | damped_log_trend|ar1_log_diff (lit 0.7) | trend_ensemble|ml_trend (lit 0.7) | target_ensemble|multi_anchor (lit 0.5) | rate_blend|level_anchor (lit 0.5) | anchor_sources (lit 0.6) |
|---|---:|---:|---:|---:|---:|
| B01002_001E | 0.69 n=2963 (s 0.85/l 0.64) | 0.83 n=2977 (s 0.87/l 0.80) | — | — | — |
| B19013_001E | 0.81 n=2963 (s 0.90/l 0.80) | 0.83 n=2977 (s 0.85/l 0.80) | 0.85 n=2977 (s 0.86/l 0.84) | — | 0.91 n=2984 (s 0.95/l 0.89) |
| B20002_001E | 0.82 n=2963 (s 0.89/l 0.78) | 0.87 n=2977 (s 0.87/l 0.84) | 0.90 n=2977 (s 0.88/l 0.89) | — | — |
| B25058_001E | 0.83 n=2963 (s 0.90/l 0.82) | 0.87 n=2977 (s 0.88/l 0.86) | 0.82 n=2977 (s 0.85/l 0.79) | — | 0.71 n=1371 (s 0.78/l 0.64) |
| B25064_001E | 0.84 n=2963 (s 0.91/l 0.83) | 0.87 n=2977 (s 0.88/l 0.86) | 0.82 n=2977 (s 0.84/l 0.80) | — | 0.69 n=1371 (s 0.76/l 0.62) |
| B25071_001E | 0.90 n=2963 (s 0.95/l 0.87) | 0.84 n=2977 (s 0.89/l 0.80) | — | — | — |
| B25077_001E | 0.71 n=2963 (s 0.81/l 0.68) | 0.86 n=2977 (s 0.88/l 0.82) | 0.76 n=2977 (s 0.83/l 0.71) | — | 0.64 n=2975 (s 0.80/l 0.55) |
| S1501_C02_014E | 0.65 n=2963 (s 0.82/l 0.60) | 0.75 n=2977 (s 0.83/l 0.71) | — | — | — |
| S1501_C02_015E | 0.86 n=2963 (s 0.93/l 0.84) | 0.83 n=2977 (s 0.89/l 0.80) | — | — | — |
| S1701_C03_001E | 0.92 n=2963 (s 0.95/l 0.90) | 0.85 n=2977 (s 0.90/l 0.83) | 0.78 n=2977 (s 0.88/l 0.79) | 0.85 n=2984 (s 0.89/l 0.86) | — |
| S2301_C04_001E | 0.95 n=2901 (s 0.95/l 0.95) | 0.95 n=2892 (s 0.95/l 0.94) | — | 0.49 n=2904 (s 0.40/l 0.58) | — |
| homeownership_rate | 0.81 n=2963 (s 0.89/l 0.77) | 0.82 n=2977 (s 0.86/l 0.80) | — | — | — |
| in_migration_rate | 0.91 n=2958 (s 0.95/l 0.90) | 0.84 n=2972 (s 0.89/l 0.81) | — | — | — |
| pct_professional | 0.85 n=2944 (s 0.93/l 0.80) | 0.81 n=2944 (s 0.85/l 0.76) | — | — | — |
| pct_service_occupations | 0.88 n=2944 (s 0.94/l 0.83) | 0.83 n=2944 (s 0.87/l 0.79) | — | — | — |
| vacancy_rate | 0.93 n=2963 (s 0.95/l 0.91) | 0.88 n=2977 (s 0.92/l 0.86) | — | — | — |

## Ensemble: literal (A) vs empirical (B)

| Indicator | RMSE A | RMSE B | Δ (rel) | Cov90 A | Cov90 B | n folds |
|---|---:|---:|---:|---:|---:|---:|
| B01002_001E | 1.89% | 1.89% | -0.00% | 88.6% | 89.7% | 2977 |
| B19013_001E | 6.82% | 6.79% | -0.51% | 87.8% | 90.2% | 2984 |
| B20002_001E | 6.89% | 6.88% | -0.06% | 87.2% | 91.5% | 2984 |
| B25058_001E | 7.45% | 7.50% | +0.72% | 87.6% | 89.5% | 2984 |
| B25064_001E | 7.30% | 7.29% | -0.05% | 85.8% | 91.0% | 2984 |
| B25071_001E | 9.50% | 9.59% | +0.98% | 88.5% | 88.7% | 2977 |
| B25077_001E | 8.94% | 8.93% | -0.18% | 86.0% | 88.4% | 2984 |
| S1501_C02_014E | 1.92% | 1.94% | +1.03% | 90.5% | 89.9% | 2977 |
| S1501_C02_015E | 8.47% | 8.45% | -0.28% | 87.9% | 88.9% | 2977 |
| S1701_C03_001E | 17.71% | 17.55% | -0.93% | 82.7% | 88.5% | 2984 |
| S2301_C04_001E | 36.69% | 36.67% | -0.05% | 90.4% | 92.2% | 2904 |
| homeownership_rate | 4.32% | 4.31% | -0.13% | 87.8% | 89.1% | 2977 |
| in_migration_rate | 22.68% | 22.64% | -0.15% | 91.1% | 90.4% | 2972 |
| pct_professional | 7.42% | 7.54% | +1.57% | 88.7% | 87.4% | 2951 |
| pct_service_occupations | 13.53% | 13.51% | -0.14% | 89.7% | 91.3% | 2951 |
| vacancy_rate | 27.02% | 27.00% | -0.05% | 90.9% | 92.2% | 2977 |

Pooled ensemble RMSE: A 14.97% → B 14.95% (-0.10%); C 14.96%.

## Ensemble: literal (A) vs blend-only empirical (C)

| Indicator | RMSE A | RMSE C | Δ (rel) | Cov90 A | Cov90 C | n folds |
|---|---:|---:|---:|---:|---:|---:|
| B01002_001E | 1.89% | 1.89% | +0.00% | 88.6% | 89.6% | 2977 |
| B19013_001E | 6.82% | 6.82% | -0.04% | 87.8% | 91.4% | 2984 |
| B20002_001E | 6.89% | 6.89% | +0.04% | 87.2% | 91.2% | 2984 |
| B25058_001E | 7.45% | 7.45% | +0.02% | 87.6% | 91.0% | 2984 |
| B25064_001E | 7.30% | 7.30% | +0.02% | 85.8% | 90.2% | 2984 |
| B25071_001E | 9.50% | 9.50% | +0.00% | 88.5% | 89.7% | 2977 |
| B25077_001E | 8.94% | 8.93% | -0.09% | 86.0% | 89.0% | 2984 |
| S1501_C02_014E | 1.92% | 1.92% | +0.00% | 90.5% | 90.9% | 2977 |
| S1501_C02_015E | 8.47% | 8.47% | +0.00% | 87.9% | 88.8% | 2977 |
| S1701_C03_001E | 17.71% | 17.57% | -0.81% | 82.7% | 88.1% | 2984 |
| S2301_C04_001E | 36.69% | 36.69% | +0.00% | 90.4% | 91.8% | 2904 |
| homeownership_rate | 4.32% | 4.32% | +0.00% | 87.8% | 88.8% | 2977 |
| in_migration_rate | 22.68% | 22.68% | +0.00% | 91.1% | 91.9% | 2972 |
| pct_professional | 7.42% | 7.42% | +0.00% | 88.7% | 89.4% | 2951 |
| pct_service_occupations | 13.53% | 13.53% | +0.00% | 89.7% | 90.7% | 2951 |
| vacancy_rate | 27.02% | 27.02% | +0.00% | 90.9% | 91.8% | 2977 |

## Coverage by horizon bucket

| h_bucket | Cov90 A | Cov90 B | Cov90 C | n |
|---|---:|---:|---:|---:|
| short | 88.1% | 90.6% | 90.5% | 22400 |
| long | 88.3% | 89.4% | 90.0% | 25144 |

## Member coverage (post-κ), A vs B

| Member | Cov90 A | Cov90 B | n |
|---|---:|---:|---:|
| level_anchor | 90.1% | 90.1% | 5898 |
| mean_reversion | 91.7% | 91.7% | 2914 |
| ml_trend | 91.7% | 91.7% | 47476 |
| multi_anchor | 91.9% | 91.3% | 17904 |
| trend_ensemble | 91.3% | 91.0% | 47502 |

## Ship gate verdict

* RMSE gate (pooled ensemble RMSE does not worsen): **PASS** (pooled Δ -0.10%; 0/16 indicators improve ≥2%)
* Coverage gate (CI90 ∈ [85%, 95%] for every indicator): **PASS**
* Regression gate (no indicator regresses > 2% RMSE): **PASS** (0 regressed)

## **Verdict: SHIP** — flip `enable_empirical_rho` default on.