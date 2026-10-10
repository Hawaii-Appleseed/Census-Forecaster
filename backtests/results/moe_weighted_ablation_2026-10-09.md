# MOE-weighted trend/AR(1) fit ablation (2026-10-09)

Anchors 2014–2022, h=1..5, ML member on. Arm A = equal-weight fits; Arm B = `moe_weighted=True` (weights clamp(R̄/R_t) ∈ [0.25, 4], R_t = SE_log²). Metrics on the bias+κ-corrected ensemble synthesised from the fold cache (`compare_ml_ablation` recipe + level-anchor fusion).

## Ensemble by indicator

| Indicator | RMSE A | RMSE B | Δ (rel) | Cov90 A | Cov90 B | n folds |
|---|---:|---:|---:|---:|---:|---:|
| B01002_001E | 1.89% | 1.88% | -0.12% | 88.6% | 88.2% | 2977 |
| B19013_001E | 6.82% | 6.83% | +0.09% | 87.8% | 87.7% | 2984 |
| B20002_001E | 6.89% | 6.86% | -0.39% | 87.2% | 89.6% | 2984 |
| B25058_001E | 7.45% | 7.44% | -0.04% | 87.6% | 87.5% | 2984 |
| B25064_001E | 7.30% | 7.28% | -0.30% | 85.8% | 85.6% | 2984 |
| B25071_001E | 9.50% | 9.47% | -0.31% | 88.5% | 89.0% | 2977 |
| B25077_001E | 8.94% | 8.93% | -0.11% | 86.0% | 85.7% | 2984 |
| S1501_C02_014E | 1.92% | 1.95% | +1.43% | 90.5% | 90.0% | 2977 |
| S1501_C02_015E | 8.47% | 8.53% | +0.66% | 87.9% | 88.4% | 2977 |
| S1701_C03_001E | 17.71% | 17.69% | -0.14% | 82.7% | 82.6% ⚠ | 2984 |
| S2301_C04_001E | 36.69% | 36.46% | -0.63% | 90.4% | 90.7% | 2904 |
| homeownership_rate | 4.32% | 4.32% | +0.00% | 87.8% | 87.8% | 2977 |
| in_migration_rate | 22.68% | 22.68% | +0.00% | 91.1% | 91.1% | 2972 |
| pct_professional | 7.42% | 7.42% | +0.00% | 88.7% | 88.7% | 2951 |
| pct_service_occupations | 13.53% | 13.53% | +0.00% | 89.7% | 89.7% | 2951 |
| vacancy_rate | 27.02% | 27.02% | +0.00% | 90.9% | 90.9% | 2977 |

Pooled ensemble RMSE: A 14.97% → B 14.93% (-0.25%).

## Ensemble by population bucket

| pop_bucket | RMSE A | RMSE B | Δ (rel) | Cov90 A | Cov90 B | n |
|---|---:|---:|---:|---:|---:|---:|
| medium | 18.72% | 18.68% | -0.22% | 88.3% | 88.5% | 21976 |
| large | 13.22% | 13.18% | -0.25% | 88.1% | 88.2% | 1088 |
| xlarge | 10.62% | 10.58% | -0.32% | 88.1% | 88.2% | 24480 |

## trend_ensemble member alone, by population bucket (post-κ)

| pop_bucket | RMSE A | RMSE B | Δ (rel) | Cov90 A | Cov90 B | n |
|---|---:|---:|---:|---:|---:|---:|
| medium | 21.66% | 21.71% | +0.23% | 91.5% | 91.6% | 21934 |
| large | 14.05% | 13.94% | -0.79% | 91.4% | 91.5% | 1088 |
| xlarge | 11.73% | 11.69% | -0.35% | 91.1% | 91.3% | 24480 |

## trend_ensemble member alone, by indicator (post-κ)

| Indicator | RMSE A | RMSE B | Δ (rel) | Cov90 A | Cov90 B | n folds |
|---|---:|---:|---:|---:|---:|---:|
| B01002_001E | 2.25% | 2.20% | -1.92% | 91.0% | 90.3% | 2977 |
| B19013_001E | 8.15% | 8.34% | +2.28% | 90.5% | 90.3% | 2977 |
| B20002_001E | 7.81% | 8.09% | +3.58% | 89.1% | 93.2% | 2977 |
| B25058_001E | 8.63% | 8.61% | -0.26% | 91.9% | 91.4% | 2977 |
| B25064_001E | 8.54% | 8.54% | +0.02% | 92.2% | 91.6% | 2977 |
| B25071_001E | 12.08% | 12.30% | +1.87% | 91.9% | 93.1% | 2977 |
| B25077_001E | 11.22% | 11.12% | -0.89% | 92.4% | 91.9% | 2977 |
| S1501_C02_014E | 2.57% | 2.71% | +5.48% | 91.7% | 90.5% | 2977 |
| S1501_C02_015E | 10.32% | 10.92% | +5.89% | 91.0% | 91.2% | 2977 |
| S1701_C03_001E | 22.83% | 22.74% | -0.38% | 90.7% | 90.7% | 2977 |
| S2301_C04_001E | 38.21% | 38.03% | -0.47% | 91.7% | 92.1% | 2904 |
| homeownership_rate | 5.29% | 5.29% | +0.00% | 88.7% | 88.7% | 2977 |
| in_migration_rate | 28.47% | 28.47% | +0.00% | 92.8% | 92.8% | 2972 |
| pct_professional | 8.81% | 8.81% | +0.00% | 90.9% | 90.9% | 2951 |
| pct_service_occupations | 15.35% | 15.35% | +0.00% | 92.2% | 92.2% | 2951 |
| vacancy_rate | 30.04% | 30.04% | +0.00% | 92.2% | 92.2% | 2977 |

## Ensemble, `small` bucket by indicator

| Indicator × pop | RMSE A | RMSE B | Δ (rel) | Cov90 A | Cov90 B | n folds |
|---|---:|---:|---:|---:|---:|---:|

## Ship gate verdict

* RMSE gate (pooled ensemble RMSE does not worsen): **PASS** (pooled Δ -0.25%; 0/16 indicators improve ≥2%)
* Coverage gate (CI90 ∈ [85%, 95%] for every indicator): **FAIL**
  * S1701_C03_001E: cov=82.6% (target [85, 95])
* Regression gate (no indicator regresses > 2% RMSE): **PASS** (0 regressed)

## **Verdict: HOLD** — `moe_weighted` stays off by default (opt-in flag).