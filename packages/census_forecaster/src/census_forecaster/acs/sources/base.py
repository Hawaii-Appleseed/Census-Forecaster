"""Anchor source loaders and registry.

A "source" wraps a JSON file under `data/anchors/` and exposes:

* `load_series(end_year)` → ordered list of (year, value) limited to
  observations whose *publication year* is ≤ end_year (so back-tests
  using a hidden-data anchor year T can never accidentally peek at
  values that hadn't been published yet).
* `annual_log_rates(end_year)` → list of (year, log_rate, weight) where
  log_rate is the YoY log-difference and weight is the recency-weighted
  smoother weight used in `projection._recency_weighted_initial_trend`.
* `smoothed_annual_rate(end_year)` → the recency-weighted geometric
  mean of YoY log rates as the forward-anchor for compound projection.

The "publication year" handling is the key difference from a naive
"slice the dict at year T" — see `publication_lag_years` per source.
For example HUD FMR FY2024 was *published* in mid-2023 based on 2021
ACS data; if we're back-testing as of anchor year 2021, we cannot use
the FY2024 FMR (it didn't exist yet). The lag accounts for this.

The loaders carry no internal state and are safe to call concurrently;
the JSON files themselves are read once per call (small, <2KB each).
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# (anchor name, last_refresh) pairs already warned about in this process.
_STALE_WARNED: set[tuple[str, str]] = set()

_ANCHOR_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "anchors"


@dataclass(frozen=True)
class AnnualRate:
    """A single year's YoY log-rate observation from a source.

    `log_rate` is annualised log-difference: ln(value_t / value_{t-gap}) / gap.
    For an annual series gap=1; for irregular gaps (e.g. semiannual data
    averaged to year-of-publication) we still annualise so all rates are
    comparable.
    """
    year: int
    log_rate: float
    se_log_rate: float


@dataclass(frozen=True)
class AnchorSource:
    """Wrapper around an embedded historical series.

    Attributes
    ----------
    name : str           — short stable identifier (e.g. "cpi_honolulu_allitems")
    data : dict          — parsed JSON (loaded eagerly at construction)
    publication_lag_years : int
        How many years a value lags reality. Most series here lag 0
        (we list calendar-year values published the following year);
        HUD FMR is special because its *fiscal* year is offset (FY2024
        FMR comes out mid-2023 based on 2021 ACS).
    indicator_affinity : tuple of ACS table-cell IDs the source can
        anchor. Used to filter sources when selecting macro anchors
        for a given ACS indicator.
    rate_se_floor : float
        Per-source minimum standard error on YoY log-rate (to prevent
        an unreasonably tight ensemble weight from a single low-noise
        anchor with a few atypical years). Documented in METHODOLOGY.md.
    geography : str
        Geographic scope of the underlying series. One of:
        - "any"     : `values_by_year` applied uniformly to any geoid (legacy).
        - "national": single national series (PCE, etc.).
        - "state"   : state-level series applied to all counties in scope.
        - "county"  : per-county series; data lives under
                      `values_by_geoid_year`. When the requested geoid
                      is missing, falls back to `values_by_year` (which
                      typically holds a state-level aggregate) if present.
    """
    name: str
    path: Path
    publication_lag_years: int
    indicator_affinity: tuple[str, ...]
    rate_se_floor: float
    _data: dict
    geography: str = "any"
    anchor_type: str = "rate"
    level_se_floor: float = 0.0

    @classmethod
    def from_file(
        cls,
        path: Path,
        publication_lag_years: int,
        indicator_affinity: tuple[str, ...],
        rate_se_floor: float = 0.005,
        geography: str = "any",
        anchor_type: str = "rate",
        level_se_floor: float = 0.0,
    ) -> "AnchorSource":
        with open(path) as f:
            data = json.load(f)
        # Stale-data warning: if `last_refresh` (YYYY-MM) is more than 6
        # months old, surface it in stderr. Doesn't error — bundled data
        # is still usable; the warning prompts the maintainer to run
        # `python -m census_forecaster.scripts.refresh_bea_anchors`
        # (for BEA-sourced files) when convenient. The CI auto-refresh
        # workflow eliminates this in normal operation.
        cls._maybe_warn_stale(path.stem, data.get("last_refresh"))
        return cls(
            name=path.stem,
            path=path,
            publication_lag_years=publication_lag_years,
            indicator_affinity=indicator_affinity,
            rate_se_floor=rate_se_floor,
            _data=data,
            geography=geography,
            anchor_type=anchor_type,
            level_se_floor=level_se_floor,
        )

    @staticmethod
    def _maybe_warn_stale(name: str, last_refresh: str | None) -> None:
        """Emit a stderr warning if `last_refresh` is older than 6 months.

        Once per anchor per process: every scenario year and every forecast
        reloads the registry, and unthrottled this printed the same four lines
        153,000 times in one CI refresh (a 108 MB log in which the warnings that
        mattered were unfindable).
        """
        if not last_refresh or not isinstance(last_refresh, str):
            return
        if (name, last_refresh) in _STALE_WARNED:
            return
        try:
            year_str, month_str = last_refresh.split("-", 1)
            year = int(year_str)
            month = int(month_str)
        except ValueError:
            return
        # Compute calendar months between last_refresh and "now"
        from datetime import date
        today = date.today()
        delta_months = (today.year - year) * 12 + (today.month - month)
        if delta_months > 6:
            import sys as _sys
            _STALE_WARNED.add((name, last_refresh))
            print(
                f"[anchor:{name}] last_refresh {last_refresh} is "
                f"{delta_months} months old (>6); consider running the "
                f"refresh script to pick up newer data.",
                file=_sys.stderr,
            )

    @property
    def metadata(self) -> dict:
        return {
            "name": self.name,
            "source": self._data.get("source"),
            "series_id": self._data.get("series_id"),
            "title": self._data.get("title"),
            "frequency": self._data.get("frequency"),
            "units": self._data.get("units"),
            "limitations": self._data.get("limitations", []),
            "publication_lag_years": self.publication_lag_years,
            "indicator_affinity": list(self.indicator_affinity),
            "geography": self.geography,
        }

    @property
    def covered_geoids(self) -> tuple[str, ...]:
        """Set of geoids with per-county data, or empty tuple for non-county sources."""
        if self.geography != "county":
            return ()
        by_geoid = self._data.get("values_by_geoid_year") or {}
        return tuple(sorted(by_geoid.keys()))

    def _values_dict_for(self, geoid: Optional[str]) -> dict:
        """Pick the appropriate {year: value} dict for the requested geoid.

        For geography == "county" we look up `values_by_geoid_year[geoid]`;
        if that geoid is missing we transparently fall back to
        `values_by_year` (which by convention holds a state-level
        aggregate for hybrid sources). For all other geographies we
        always return `values_by_year`.

        A missing/empty result is normal — the caller treats it as
        "no data visible at this geoid/year" and falls back gracefully.
        """
        if self.geography == "county" and geoid is not None:
            by_geoid = self._data.get("values_by_geoid_year") or {}
            geo_block = by_geoid.get(geoid) or by_geoid.get(str(geoid))
            if geo_block:
                return geo_block
        return self._data.get("values_by_year") or {}

    def load_series(
        self,
        end_year: Optional[int] = None,
        geoid: Optional[str] = None,
    ) -> list[tuple[int, float]]:
        """Return [(year, value), ...] sorted ascending, limited by visibility.

        With end_year=T, a value for year Y is included only if it would
        have been *published* on or before T — i.e. Y + publication_lag_years
        ≤ T. This is the no-peeking rule for hidden-data back-tests.

        For county-level sources, `geoid` selects the per-county series;
        non-county sources ignore the parameter for backwards-compat.
        """
        items: list[tuple[int, float]] = []
        values = self._values_dict_for(geoid)
        for k, v in values.items():
            try:
                yr = int(k)
            except ValueError:
                continue
            if v is None or not isinstance(v, (int, float)) or not math.isfinite(v):
                continue
            if v <= 0:
                continue
            if end_year is not None and (yr + self.publication_lag_years) > end_year:
                continue
            items.append((yr, float(v)))
        items.sort(key=lambda x: x[0])
        return items

    def annual_log_rates(
        self,
        end_year: Optional[int] = None,
        geoid: Optional[str] = None,
    ) -> list[AnnualRate]:
        """YoY log-rates with empirical residual SE.

        SE is the dispersion of pairwise log-rates within the visible
        window. Floored at `rate_se_floor` so a perfectly smooth admin
        series doesn't get unreasonably high weight in the ensemble.
        """
        series = self.load_series(end_year, geoid=geoid)
        if len(series) < 2:
            return []
        rates: list[tuple[int, float]] = []
        for i in range(1, len(series)):
            (y0, v0), (y1, v1) = series[i - 1], series[i]
            gap = y1 - y0
            if gap <= 0 or v0 <= 0 or v1 <= 0:
                continue
            rates.append((y1, math.log(v1 / v0) / gap))
        if not rates:
            return []
        # Empirical SD of YoY log-rates as the residual SE; floor it.
        rs = [r for _, r in rates]
        mean_r = sum(rs) / len(rs)
        if len(rs) >= 2:
            var = sum((r - mean_r) ** 2 for r in rs) / (len(rs) - 1)
            sd = math.sqrt(var)
        else:
            sd = abs(rs[0])
        sd = max(sd, self.rate_se_floor)
        return [AnnualRate(year=y, log_rate=r, se_log_rate=sd) for y, r in rates]

    def latest_level(
        self,
        end_year: Optional[int] = None,
        geoid: Optional[str] = None,
    ) -> Optional[float]:
        """Return the most recently visible observed level value.

        Unlike ``load_series``, does not filter out ``v <= 0`` — a 0%
        rate is valid for level-type anchors (e.g. 0% unemployment).
        Negative values are still excluded as they indicate bad data.
        Only meaningful when ``anchor_type == "level"``.
        """
        values = self._values_dict_for(geoid)
        best_year: Optional[int] = None
        best_val: Optional[float] = None
        for k, v in values.items():
            try:
                yr = int(k)
            except ValueError:
                continue
            if v is None or not isinstance(v, (int, float)) or not math.isfinite(v):
                continue
            if v < 0:
                continue
            if end_year is not None and (yr + self.publication_lag_years) > end_year:
                continue
            if best_year is None or yr > best_year:
                best_year = yr
                best_val = float(v)
        return best_val

    def smoothed_annual_rate(
        self,
        end_year: Optional[int] = None,
        geoid: Optional[str] = None,
    ) -> Optional[AnnualRate]:
        """Recency-weighted geometric mean of YoY log-rates.

        Mirrors `projection._recency_weighted_initial_trend` so the
        smoother is applied identically to both ACS-internal and external
        anchor series. Most-recent rate weight 1.0, prior 0.5, prior^2
        0.25, half-life one year (per pair).

        SE on the smoothed rate accounts for the fact that the weighted
        mean is more stable than a single pair: var = Σ w_i² σ² / (Σ w_i)²
        with σ ≈ residual SD of the YoY series.
        """
        rates = self.annual_log_rates(end_year, geoid=geoid)
        if not rates:
            return None
        n = len(rates)
        if n == 1:
            return rates[0]
        weights = [0.5 ** (n - 1 - i) for i in range(n)]
        wsum = sum(weights)
        smoothed = sum(rates[i].log_rate * weights[i] for i in range(n)) / wsum
        sigma = rates[-1].se_log_rate  # all rates share the same SD by construction
        # Variance of weighted mean of (assumed) iid draws.
        var_smoothed = sum(w * w for w in weights) * sigma * sigma / (wsum * wsum)
        # Floor: cannot beat a single observation's SE.
        se_smoothed = max(math.sqrt(var_smoothed), self.rate_se_floor)
        return AnnualRate(year=rates[-1].year, log_rate=smoothed, se_log_rate=se_smoothed)


# -----------------------------------------------------------------------------
# Source registry — which anchors are appropriate for which ACS indicators
# -----------------------------------------------------------------------------
#
# `indicator_affinity` is the set of ACS table-cell IDs each source is
# admissible to anchor. Conservative by construction: rent CPI anchors
# only the rent indicators; FHFA HPI anchors only home-value; QCEW wages
# and PCE/CPI broad inflation can anchor income.

_REGISTRY_SPEC = [
    # (filename, publication_lag_years, indicator_affinity, rate_se_floor, geography, anchor_type, level_se_floor)
    # CPI Honolulu broad inflation: anchors income (used as nominal
    # purchasing-power-equivalent macro rate). publication_lag=0:
    # H1+H2 averages are out by Dec of the year.
    ("cpi_honolulu_allitems.json", 0,
     ("B19013_001E",), 0.005, "any", "rate", 0.0),
    # CPI Honolulu rent — anchors gross/contract rent.
    ("cpi_honolulu_rent.json", 0,
     ("B25058_001E", "B25064_001E"), 0.005, "any", "rate", 0.0),
    # PCE deflator (national) — anchors income as alternate macro proxy.
    ("pce_deflator.json", 0,
     ("B19013_001E",), 0.005, "national", "rate", 0.0),
    # QCEW HI wages — anchors income, and (added 2026-08-21) worker
    # earnings: B20002 is median earnings for workers 16+, conceptually
    # the closest ACS cell to QCEW's payroll-tax wage base — arguably a
    # tighter match than B19013 household income, which QCEW anchored
    # from the start. Gate PASSED on the calibration panel (2,977 folds,
    # anchors 2014-2022): blended RMSE 9.12% vs trend-only 10.47%
    # (−12.9% relative) at Bates-Granger weight 0.571; the anchor member
    # alone runs 6.91% vs trend 7.97%. Raw blend coverage 95.6% — the
    # slight over-coverage is what the κ machinery deflates, exactly as
    # it already does for B19013's multi_anchor (κ ≈ 0.715).
    ("qcew_hawaii_wages.json", 1,  # final annual averages release ~Aug of year+1
     ("B19013_001E", "B20002_001E"), 0.005, "state", "rate", 0.0),
    # HUD FMR Honolulu — *validation* anchor for rent (lags 2y so its
    # back-test weight will be small; primarily used for checks).
    ("hud_fmr_honolulu.json", 2,
     ("B25058_001E", "B25064_001E"), 0.010, "any", "rate", 0.0),
    # FHFA HPI Hawaii — anchors home-value.
    ("fred_hi_hpi.json", 0,  # Q4 release within Q1 of year+1
     ("B25077_001E",), 0.005, "state", "rate", 0.0),
    # ----- BEA anchors (added v0.3) -----
    # BEA per-capita personal income, Hawaii state. Year-Y data is
    # finalised in the April Y+1 release; treat as 1-year-lagged so the
    # back-test honours the no-peeking discipline.
    ("bea_hi_percapita_income.json", 1,
     ("B19013_001E", "S1701_C03_001E"), 0.006, "state", "rate", 0.0),
    # BEA Regional Price Parity, Honolulu metro all-items. Annual
    # release ~May of year+1.
    ("bea_honolulu_rpp_all.json", 1,
     ("B19013_001E",), 0.005, "any", "rate", 0.0),
    # BEA Regional Price Parity, Hawaii state services-rents. Anchors
    # rent indicators alongside CPI Honolulu rent and HUD FMR.
    ("bea_hi_rpp_housing.json", 1,
     ("B25058_001E", "B25064_001E"), 0.006, "state", "rate", 0.0),
    # ----- County-level anchors (added v0.3 Phase A) -----
    # Zillow ZHVI — county-level home value index, monthly cadence
    # aggregated to annual (Dec value or annual mean depending on the
    # refresh script). Publication lag ~1 month; rounded up to 1 year
    # for the back-test discipline so a 2024 forecast made in Jan 2025
    # cannot peek at the Jan 2025 ZHVI release.
    ("zillow_zhvi.json", 0,
     ("B25077_001E",), 0.005, "county", "rate", 0.0),
    # Zillow ZORI — county-level rent index. Same cadence/lag treatment.
    ("zillow_zori.json", 0,
     ("B25058_001E", "B25064_001E"), 0.010, "county", "rate", 0.0),
    # BLS LAUS — county-level unemployment rate.
    # Registered as a LEVEL anchor (not a log-rate anchor): LAUS is directly
    # the county unemployment rate in percentage points and predicts ACS S2301
    # at h=1 as a level observation (not a growth rate). Log-rate registration
    # was tried and failed (increased RMSE); the level-anchor path bypasses the
    # log-growth-rate arithmetic entirely.
    # level_se_floor=1.5: empirical RMSE of LAUS vs ACS S2301 at county level
    # is typically 0.5–2.0 pp; 1.5 is a conservative floor before κ calibration.
    ("bls_laus.json", 0,
     ("S2301_C04_001E",), 0.005, "county", "level", 1.5),
    # Census SAIPE — county-level annual poverty rate.
    # Registered as a LEVEL anchor: SAIPE is a model-based estimate of the
    # county poverty rate (%) and predicts ACS S1701 directly at h=1.
    # Log-rate registration was tried and failed (+1.18pp RMSE regression).
    # level_se_floor=2.0: empirical RMSE of SAIPE vs ACS S1701 at county level
    # is typically 1.0–3.0 pp; 2.0 is a conservative floor before κ calibration.
    ("saipe_poverty.json", 0,
     ("S1701_C03_001E",), 0.010, "county", "level", 2.0),
    # ----- Market-signals module (July 2026, Phase 3) -----
    # National unemployment (CPS LNS14000000) as a RATE anchor on S2301
    # was tried and REJECTED — do not re-add without a variance-aware
    # treatment. Full evidence in
    # backtests/results/market_ml_ablation_2026-07-14.md:
    #   * Full window (anchors 2014-2022): blended S2301 RMSE improves
    #     -1.2% absolute — BUT coverage drops 91.7% → 88.2% (the anchor
    #     makes intervals overconfident) and the gain concentrates in the
    #     2020-2021 shock years.
    #   * Recent window (anchors 2021-2022 only): RMSE REGRESSES +2.7%
    #     absolute (gate: 2%) — on the anchors most like the future, the
    #     member (standalone RMSE 0.47) dilutes the trend it joins (0.40).
    #   * Structural: unemployment-rate YoY log-changes blow the
    #     rate-anchor band invariant, |log(9.3/6.3)| = 0.47 in 2009 vs
    #     0.30 (test_rates_in_reasonable_band). The rate machinery was
    #     built for price/income indexes; recession swings in a *rate*
    #     series are too violent for its SE assumptions — which is the
    #     mechanism behind the coverage loss above.
    # National unemployment DOES feed the forecaster — but as an ML
    # leading-indicator FEATURE, not an anchor: the "unemp" entry of the
    # NATIONAL_SERIES registry (acs/ml_features.py, col_policy level_diff2 →
    # natl_unemp_lvl/chg1/chg2 columns, fetched by refresh_national_macro).
    # The feature path dodges every failure above: raw % + pp changes (no
    # log-space SE), only the change transfers (no level offset), and the
    # tree learns when to trust it. See METHODOLOGY.md §Market signals and
    # compare_natl_unemp_ablation.py.
]


def _unpack_spec(spec: tuple) -> tuple:
    """Unpack a registry spec tuple into (fname, lag, affinity, floor, geo, atype, lfloor).

    Supports both the old 5-element format and the new 7-element format
    for backwards compatibility with any external code that registered
    custom sources via the 5-element convention.
    """
    if len(spec) == 7:
        return spec
    if len(spec) == 5:
        fname, lag, affinity, floor, geo = spec
        return (fname, lag, affinity, floor, geo, "rate", 0.0)
    raise ValueError(f"Unexpected registry spec length {len(spec)}: {spec!r}")


def load_source(name_or_filename: str) -> AnchorSource:
    """Load one anchor source by name (filename stem or filename)."""
    if not name_or_filename.endswith(".json"):
        name_or_filename = name_or_filename + ".json"
    path = _ANCHOR_DIR / name_or_filename
    spec = next(
        (s for s in _REGISTRY_SPEC if s[0] == name_or_filename),
        None,
    )
    if spec is None:
        raise KeyError(f"No anchor spec registered for {name_or_filename!r}")
    _, lag, affinity, floor, geography, anchor_type, level_se_floor = _unpack_spec(spec)
    return AnchorSource.from_file(
        path=path,
        publication_lag_years=lag,
        indicator_affinity=affinity,
        rate_se_floor=floor,
        geography=geography,
        anchor_type=anchor_type,
        level_se_floor=level_se_floor,
    )


def available_sources(indicator: Optional[str] = None) -> list[AnchorSource]:
    """All registered sources (optionally filtered to those that can anchor `indicator`)."""
    sources: list[AnchorSource] = []
    for spec in _REGISTRY_SPEC:
        fname, lag, affinity, floor, geography, anchor_type, level_se_floor = _unpack_spec(spec)
        path = _ANCHOR_DIR / fname
        if not path.exists():
            continue
        try:
            src = AnchorSource.from_file(
                path=path,
                publication_lag_years=lag,
                indicator_affinity=affinity,
                rate_se_floor=floor,
                geography=geography,
                anchor_type=anchor_type,
                level_se_floor=level_se_floor,
            )
        except (OSError, json.JSONDecodeError):
            continue
        if indicator is None or indicator in src.indicator_affinity:
            sources.append(src)
    return sources


SOURCE_REGISTRY = _REGISTRY_SPEC  # exposed for documentation/test discovery
