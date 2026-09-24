# Census Forecaster

[![tests](https://github.com/Hawaii-Appleseed/Census-Forecaster/actions/workflows/tests.yml/badge.svg)](https://github.com/Hawaii-Appleseed/Census-Forecaster/actions/workflows/tests.yml)

Forecast current-period values from older U.S. Census ACS estimates and BLS time series, with calibrated uncertainty.

## Why

ACS data lags. The 2024 5-year ACS won't release until late 2025; researchers using "current" rents in 2026 are pulling 2018–2022 averages. BLS rent CPI samples each unit once every six months, so even the "latest" Honolulu print is ~12 months behind market. Most projects either:

1. Ignore the lag (silently flat-line),
2. Naïvely linearly extrapolate (no uncertainty quantification, fragile to single-print noise), or
3. Build something custom that doesn't propagate ACS margin-of-error properly.

This package does it once, well: damped trend + AR(1) ensemble + macro anchor for ACS, recency-weighted + damped + capped projection for BLS, with **empirically-calibrated** 90% prediction intervals on both surfaces.

## Quick start

### Forecast an ACS estimate forward to a target year

```python
from census_forecaster import (
    AcsObservation,
    project_acs_ensemble,
)

# Honolulu County median household income, 2014-2023 (5-year ACS)
obs = [
    AcsObservation(estimate=72133, moe=1862, year=2014, vintage="5y",
                   geoid="15003", indicator="B19013_001E"),
    AcsObservation(estimate=74460, moe=2105, year=2015, vintage="5y",
                   geoid="15003", indicator="B19013_001E"),
    # ... more years
    AcsObservation(estimate=99814, moe=2330, year=2023, vintage="5y",
                   geoid="15003", indicator="B19013_001E"),
]

forecast = project_acs_ensemble(obs, target_year=2026)
print(f"2026 income: ${forecast.point:,.0f}")
print(f"90% CI: [${forecast.ci90_low:,.0f}, ${forecast.ci90_high:,.0f}]")
print(f"Method: {forecast.method}")
print(f"Sample SE: {forecast.se_sample:,.0f} | Forecast SE: {forecast.se_forecast:,.0f}")
```

### Forecast a BLS CPI series forward to a target month

```python
from datetime import date
from census_forecaster.bls import fetch_cpi_data, project_forward_full

# Fetch Honolulu all-items CPI
data = fetch_cpi_data(["CUURS49ASA0"], 2018, 2026)

# Project to April 2026
proj = project_forward_full(data["CUURS49ASA0"], date(2026, 4, 1))
print(f"Projected CPI: {proj.value:.2f}")
print(f"Implied annual rate: {((1 + proj.monthly_rate) ** 12 - 1) * 100:+.2f}%")
print(f"Cap fired: {proj.cap_fired}")
print(f"Forecast SE (log): {proj.forecast_se_log:.4f}")
```

### Compute a CPI ratio with full diagnostic surface

For uprating older nominal-dollar values to a target month:

```python
from datetime import date
from census_forecaster.bls import compute_cpi_ratio

result = compute_cpi_ratio(
    data,
    series_id="CUURS49ASA0",
    baseline_date=date(2020, 1, 1),
    target_date=date(2026, 4, 1),
)

# Apply to a 2020 dollar value:
old_rent = 1850
projected_rent = old_rent * result["ratio"]
print(f"Today's rent equivalent: ${projected_rent:,.0f}")
print(f"Method: {result['method']}")
print(f"90% CI on ratio: [{result['ratio_ci90_low']:.4f}, {result['ratio_ci90_high']:.4f}]")
```

### Use ACS + BLS together (macro-anchored ensemble)

```python
forecast = project_acs_ensemble(
    obs,
    target_year=2026,
    macro_annual_rate=0.034,  # e.g. BLS Honolulu all-items CPI YoY
    macro_weight=0.30,
)
```

The `0.30` weight blends the BLS-derived growth rate against the ACS trend ensemble — the same 70/30 pattern Cleveland Fed uses for blended-rent nowcasts.

## What's inside

| Subpackage | Purpose |
|---|---|
| `census_forecaster.acs` | ACS forecasting: damped trend + AR(1) + ensemble + macro anchor |
| `census_forecaster.bls` | BLS CPI forecasting: recency-weighted + damped + capped, with calibrated 90% CIs |
| `census_forecaster.backtest` | Walk-forward harness for tuning constants and quoting realised MAPE |
| `census_forecaster.moe` | Census MOE → SE conversions and propagation (sum/ratio/proportion) |

## Policy analyses (Hawaiʻi)

The `tax_modeler` package builds on the forecaster to score Hawaiʻi tax/benefit
proposals. Self-contained analyses with their own entry guides:

| Analysis | Start here |
|---|---|
| **RxKids Hawaiʻi** — cost of a prenatal/infant cash program | **[`RXKIDS_GUIDE.md`](RXKIDS_GUIDE.md)** → [`RXKIDS_METHODOLOGY.md`](RXKIDS_METHODOLOGY.md) |
| SB 3125 CD1 — EITC/CTC reform | [`SB3125_CD1_FORECAST.md`](SB3125_CD1_FORECAST.md) |
| Capital-gains rate options on Act 24 brackets | [`forecast_cg_rate_options.py`](forecast_cg_rate_options.py) · published on the estimates site |

### Estimates site

Published estimates are served from `site/` by GitHub Pages
(`.github/workflows/pages.yml`, which deploys the directory as-is). To publish
or refresh one after a model run:

```bash
python forecast_cg_rate_options.py          # writes runs/cg_rate_options/ (gitignored)
python scripts/build_site.py --import-runs  # copies outputs to site/data/, regenerates pages
```

Commit `site/` with the change. Pages are generated only from the committed
`site/data/`, so prose, charts and downloadable CSVs always agree, and
`tests/site/` fails if the committed pages are stale. Adding an estimate means
an `Estimate` entry and a page builder in `scripts/build_site.py`.

## Design principles

* **MOE-aware variance.** ACS publishes 90% margins of error; this package converts at the boundary (Z=1.645) and propagates correctly through the projection.
* **Empirical calibration over literature defaults.** Both `EMPIRICAL_SE_INFLATOR=1.30` (ACS) and `_PROJ_SE_INFLATOR=1.50` (CPI) were derived from walk-forward evidence, not theory alone.
* **Compound rates, never arithmetic.** `(1+r)^h`, never `1 + r·h`. Geometric throughout.
* **Damped trends.** Gardner-McKenzie (1985) damping (`phi=0.85/yr` for ACS, `phi=0.92/mo` for CPI) prevents the most common short-horizon failure: treating a noisy single-print spike as a permanent slope.
* **Per-period rate caps.** Annual `±10%/yr` for ACS; monthly `±0.0189/mo` (~±25%/yr) for CPI. Defense-in-depth against compounding failure.
* **Honest method tags.** Every output carries `method`, `notes`, `cap_fired`, etc. so downstream consumers can tell `exact` from `interpolated` from `projected`.
* **Backwards-compatible APIs.** New diagnostic fields default to `None`; existing callers reading `point` / `ratio` are unaffected.

## Realised performance (April 2026 walk-forward on Hawaii panel)

ACS 2-year horizon, 96 folds (4 counties × 4 indicators × 6 anchors):

| Method | MAPE | CI90 coverage |
|---|---:|---:|
| Carry-forward | 8.91% | 29% |
| Linear OLS (log) | 7.69% | 81% |
| Damped log-trend | 7.62% | 90% |
| AR(1) on log-diffs | 6.98% | 88% |
| **Ensemble (default)** | **6.75%** | **88.5%** |

BLS CPI projection, 945 evaluations (5 series × 63 anchors × 3 horizons):

| Series | MAPE | CI90 coverage (κ=1.50) |
|---|---:|---:|
| Rent | 0.47% | ≥90% |
| Food at home | 1.64% | ≥85% |
| All items | 1.11% | ≥85% |
| Fruits/veg | 0.99% | ≥85% |
| Gasoline | 10.04% | ≥85% (high-volatility floor) |

Re-derive these numbers for your own region with:

```bash
# ACS calibration (uses your own ACS panel data)
python -c "from census_forecaster.backtest import run_acs_backtest; ..."
```

## Caveats

1. **BLS revisions silently bias backtest accuracy upward.** The public API exposes only the latest-revised series; the harness uses revised values at T even though they would not have been live then. Treat reported MAPE as a *lower bound* on true live error.
2. **Calibration is regime-dependent.** The default `EMPIRICAL_SE_INFLATOR=1.30` and `_PROJ_SE_INFLATOR=1.50` were calibrated on Hawaii data spanning the 2020-2025 inflation cycle. Re-calibrate for your own region — and recheck after any major regime change.
3. **Generalisation is unproven outside Hawaii.** The 119 ACS unit tests + 32 CPI unit tests exercise the math, but the empirical calibration was on a single state. Use the backtest harness to verify your own panel before trusting the defaults.
4. **Not all ACS indicators behave the same.** Dollar-denominated indicators (income, rent, value) work well with log-space damped trends. Counts and proportions need different handling — see `acs/projection.py` design notes.

## Installation

```bash
pip install git+https://github.com/Hawaii-Appleseed/Census-Forecaster.git@main
```

Or for development:

```bash
git clone https://github.com/Hawaii-Appleseed/Census-Forecaster.git
cd Census-Forecaster
pip install -e .[dev]
pytest tests/
```

## License

MIT. See `LICENSE`.

## Citation

If you use this package in research, please cite:

```
@software{census_forecaster_2026,
  author = {Tom Katsumi},
  title  = {Census Forecaster: Calibrated forecasts of current-period values from older ACS and BLS data},
  year   = {2026},
  url    = {https://github.com/Hawaii-Appleseed/Census-Forecaster},
}
```

## See also

* [Hawaii Cost-of-Living Tracker](https://github.com/Hawaii-Appleseed/Cost-of-Living-Tracker) — primary consumer of this package; shows real-world integration patterns.
* Hyndman, R. & Athanasopoulos, G. (2018). *Forecasting: Principles and Practice* (2nd ed.). — modern damped-trend treatment.
* Cleveland Fed WP 22-38r ("New-Tenant Repeat Rent Inflation") — academic basis for the 70/30 blend pattern.
* Wilson, T. et al. (2021). "Methods for Small Area Population Forecasts." — small-area discipline (fixed smoothing constants, etc.).
