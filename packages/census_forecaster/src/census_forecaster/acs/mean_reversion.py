"""Mean-reversion model for county unemployment (ACS S2301).

VERDICT (2026-08-21): NULL — do not promote into the ensemble.
--------------------------------------------------------------
Walk-forward on the 90-county calibration panel (anchors 2014-2022,
h 1-5, n=2,937): RMSE 59.0% vs trend_ensemble 36.1% / ml_trend 33.4%,
losing at every horizon; a variant sweep (recency-weighted mean,
last-3-year mean, pure carry+offset) never gets within 20 points of the
incumbents. The county-level ACS S2301 print's variance is dominated by
its own idiosyncratic measurement structure, which only its own history
predicts — LAUS-state-driven designs cannot, however the reversion
target is chosen. Full table and reading: METHODOLOGY.md §"S2301
mean-reversion model — informative null". The model remains wired as
calibration Pass 2e so the null stays reproducible in the fold records.

Why this model was built
------------------------
Unemployment is mean-reverting (3% → 15% → 3% over a recession cycle),
while every trend-family member in this package assumes continuity.
METHODOLOGY.md has documented since April 2026 that S2301's chronic
miscalibration (trend MAPE ≈ 36–40%, raw CI90 coverage ≈ 75–77%, κ
pinned at the 2.60 ceiling) is model misspecification, and prescribes
"a dedicated mean-reversion model (AR(1) toward a long-run mean)".
This module is that model.

Model
-----
Let ``u_t`` be the county's **LAUS** annual-average unemployment rate
(administrative, county-level, far less noisy than the ACS S2301 print)
and ``a_t`` the published ACS S2301 estimate. With anchor year ``T`` and
horizon ``h``:

    point = delta_c + mu_c + phi^h_eff · (u_state − mu_c)

* ``mu_c``   — county long-run mean of LAUS over years ≤ T.
* ``phi``    — pooled AR(1) coefficient, fit by through-origin OLS of
  ``(u_{t+1} − mu_c)`` on ``(u_t − mu_c)`` across ALL counties in the
  bundled LAUS file, years ≤ T (per-county series are far too short to
  fit phi individually; unemployment dynamics are a national business-
  cycle phenomenon, so pooling is the right prior). Clamped [0, 0.95].
* ``u_state``— the latest LAUS value at year ``T − g`` (g ≥ 0 when the
  anchor year is missing); ``h_eff = h + g`` so reversion runs from the
  year the state was actually observed.
* ``delta_c``— county ACS↔LAUS offset, the mean of ``(a_t − u_t)`` over
  paired years ≤ T. ACS S2301 and LAUS measure different universes
  (household-survey share of civilian labor force vs the LAUS model),
  and the gap is systematic per county — this offset is the reason this
  model can beat the raw ``level_anchor`` (which applies LAUS with no
  offset and pays for it: RMSE ≈ 52%).

Uncertainty
-----------
    se_total² = sigma_ar² · Σ_{k=0..h_eff−1} phi^{2k}     (AR(1) h-step)
              + sigma_map² · (1 + 1/n_pairs)              (offset noise)

``sigma_map`` is the SD of the offset residuals ``(a_t − u_t − delta_c)``
— it already contains the ACS sampling noise of the target print, so the
MOE of the latest observation is deliberately NOT added again
(``se_sample = 0``; adding it would double-count). No EMPIRICAL_SE_INFLATOR
is baked in: the κ bisection calibrates whatever raw SE the fold cache
carries, and the fallback κ equals the base inflator so the uncorrected
factor is exactly 1.0.

No-peeking discipline
---------------------
All of ``mu_c``, ``phi``, ``delta_c``, ``sigma_*`` use only years ≤ the
anchor. LAUS year-Y annual averages are final by early Y+1, well before
the year-Y ACS release the backtest's publication mode keys on — the
same treatment the LAUS level anchor already receives (lag 0 in the
source registry).
"""
from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from functools import lru_cache
from pathlib import Path

from common.models import AcsObservation, ForecastPoint

from .projection import effective_year

METHOD_NAME = "mean_reversion"

# Indicator this model is specified for. The LAUS mapping and the
# mean-reversion prior are unemployment-specific; do not widen this list
# without a fresh ablation.
SUPPORTED_INDICATORS = frozenset({"S2301_C04_001E"})

_LAUS_PATH = (
    Path(__file__).resolve().parent.parent
    / "data" / "anchors" / "bls_laus.json"
)

_PHI_CLAMP = (0.0, 0.95)
_MIN_LAUS_YEARS = 5      # county history needed for mu_c
_MIN_OFFSET_PAIRS = 3    # ACS/LAUS pairs needed for delta_c
_FALLBACK_MAP_SD = 1.5   # pp; mirrors the level anchor's level_se_floor


@lru_cache(maxsize=1)
def load_laus_values() -> dict[str, dict[int, float]]:
    """Bundled LAUS county unemployment rates: {geoid: {year: rate_pp}}."""
    raw = json.loads(_LAUS_PATH.read_text())
    out: dict[str, dict[int, float]] = {}
    for geoid, by_year in raw.get("values_by_geoid_year", {}).items():
        out[geoid] = {int(y): float(v) for y, v in by_year.items() if v is not None}
    return out


@lru_cache(maxsize=32)
def _pooled_phi_sigma(end_year: int) -> tuple[float, float]:
    """Pooled AR(1) coefficient and residual SD on LAUS deviations, years ≤ end_year."""
    laus = load_laus_values()
    sxy = 0.0
    sxx = 0.0
    pairs: list[tuple[float, float]] = []
    for _geoid, by_year in laus.items():
        years = sorted(y for y in by_year if y <= end_year)
        if len(years) < _MIN_LAUS_YEARS:
            continue
        mu = sum(by_year[y] for y in years) / len(years)
        for y in years:
            if (y + 1) in by_year and (y + 1) <= end_year:
                x = by_year[y] - mu
                z = by_year[y + 1] - mu
                sxy += x * z
                sxx += x * x
                pairs.append((x, z))
    if sxx <= 0 or len(pairs) < 10:
        return 0.7, _FALLBACK_MAP_SD  # conservative prior; effectively unused
    phi = max(_PHI_CLAMP[0], min(_PHI_CLAMP[1], sxy / sxx))
    resid = [z - phi * x for x, z in pairs]
    dof = max(len(resid) - 1, 1)
    sigma = math.sqrt(sum(r * r for r in resid) / dof)
    return phi, sigma


def project_mean_reversion(
    series_observations: Sequence[AcsObservation],
    target_year: int,
    end_year: int | None = None,
    laus_values: Mapping[str, Mapping[int, float]] | None = None,
) -> ForecastPoint | None:
    """AR(1)-toward-mean forecast of ACS S2301 from the LAUS state.

    Returns None when the indicator is unsupported, the county has no
    usable LAUS history at the anchor, or the target is not ahead of the
    anchor — callers treat None exactly like any other unavailable member.
    """
    if not series_observations:
        return None
    latest = series_observations[-1]
    indicator = latest.indicator
    if indicator not in SUPPORTED_INDICATORS:
        return None
    geoid = latest.geoid
    if end_year is None:
        end_year = int(round(effective_year(latest)))
    horizon = target_year - end_year
    if horizon <= 0:
        return None

    laus_all = laus_values if laus_values is not None else load_laus_values()
    by_year = {int(y): float(v) for y, v in (laus_all.get(geoid) or {}).items()}
    years = sorted(y for y in by_year if y <= end_year)
    if len(years) < _MIN_LAUS_YEARS:
        return None

    mu_c = sum(by_year[y] for y in years) / len(years)
    state_year = years[-1]
    u_state = by_year[state_year]
    h_eff = horizon + (end_year - state_year)

    phi, sigma_ar = _pooled_phi_sigma(end_year)

    # County ACS↔LAUS offset on paired training years.
    acs_by_year: dict[int, float] = {}
    for o in series_observations:
        ey = int(round(effective_year(o)))
        if ey <= end_year and o.vintage == "1y" and o.estimate > 0:
            acs_by_year[ey] = o.estimate
    paired = [(acs_by_year[y], by_year[y]) for y in acs_by_year if y in by_year]
    if len(paired) >= _MIN_OFFSET_PAIRS:
        offsets = [a - u for a, u in paired]
        delta_c = sum(offsets) / len(offsets)
        dof = max(len(offsets) - 1, 1)
        sigma_map = math.sqrt(
            sum((d - delta_c) ** 2 for d in offsets) / dof
        )
        # A tiny offset-residual SD on few pairs is luck, not precision.
        sigma_map = max(sigma_map, 0.3)
        n_pairs = len(offsets)
    else:
        delta_c = 0.0
        sigma_map = _FALLBACK_MAP_SD
        n_pairs = 1

    point = delta_c + mu_c + (phi ** h_eff) * (u_state - mu_c)
    point = max(point, 0.1)  # unemployment rate cannot be ≤ 0

    ar_var = (sigma_ar ** 2) * sum(phi ** (2 * k) for k in range(h_eff))
    map_var = (sigma_map ** 2) * (1.0 + 1.0 / n_pairs)
    se_total = math.sqrt(ar_var + map_var)

    z = 1.645
    return ForecastPoint(
        point=point,
        se_total=se_total,
        se_sample=0.0,   # sigma_map already contains ACS sampling noise
        se_forecast=se_total,
        ci90_low=max(point - z * se_total, 0.0),
        ci90_high=point + z * se_total,
        method=METHOD_NAME,
        target_year=target_year,
        geoid=geoid,
        indicator=indicator,
        horizon=horizon,
        notes=(
            f"mean_reversion(phi={phi:.3f}, mu={mu_c:.2f}, delta={delta_c:+.2f}, "
            f"state={u_state:.2f}@{state_year}, h_eff={h_eff}, "
            f"sigma_ar={sigma_ar:.2f}, sigma_map={sigma_map:.2f}, n_pairs={n_pairs})"
        ),
    )
