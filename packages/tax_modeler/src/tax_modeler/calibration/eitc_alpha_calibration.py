"""Empirical calibration of the EITC poverty-elasticity parameter α.

Fits the α exponent in ``scale_eitc_for_poverty`` (see
:mod:`tax_modeler.adjustments.eitc_poverty_scaling`) from a Hawaii
historical panel of three series, all observable:

  * **Observed EITC growth**  — annual Hawaii EITC return counts from
    IRS SOI Historic Table 2 (TY 2018–2022), bundled at
    ``data/external/irs_soi_hi_eitc_panel.csv``.
  * **B19013 growth**         — annual ACS median household income for
    Hawaii counties, read from the bundled ACS 1-year panel.
  * **S1701 growth**          — annual ACS poverty rate (all-ages) for
    Hawaii counties, same panel.

Model
-----

The EITC scaling adjustment is

    EITC_amount_final = EITC_amount × poverty_rate_factor ** α

applied after EITC has already been recomputed on B19013-scaled income.
In log-form across year-pairs the implied prediction is

    log(EITC[t+1] / EITC[t]) ≈ log(B19013[t+1] / B19013[t])
                              + α × log(S1701[t+1] / S1701[t])

A single-parameter OLS fit gives α from the residual log relationship
between observed EITC growth and the B19013-anchored prediction.

Honolulu County (GEOID 15003) is used as the state proxy because it
contains ~70% of Hawaii's population and ~70% of its EITC filers; using
a county-level series rather than a population-weighted state aggregate
keeps the fit simple and reproducible.

Calibration artifact
--------------------

The fit emits a JSON artifact at
``data/calibration/eitc_poverty_alpha.json`` carrying ``α``, ``rmse``,
``n_year_pairs``, the data vintages used, and the per-pair residuals.
Downstream consumers can read either the JSON or use the fitted value
baked into ``scale_eitc_for_poverty``'s default.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

LOG = logging.getLogger(__name__)

_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_PANEL_PATH = (
    _PACKAGE_ROOT / "data" / "external" / "irs_soi_hi_eitc_panel.csv"
)
_DEFAULT_ARTIFACT_PATH = (
    _PACKAGE_ROOT / "data" / "calibration" / "eitc_poverty_alpha.json"
)
_HONOLULU_GEOID = "15003"


@dataclass(frozen=True)
class AlphaCalibration:
    """Result of an empirical α fit."""

    alpha: float
    rmse: float
    n_year_pairs: int
    year_pairs: tuple[tuple[int, int], ...]
    residuals: tuple[float, ...]
    geoid_used: str
    vintage: str
    irs_panel_years: tuple[int, ...]
    notes: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "alpha": self.alpha,
            "rmse": self.rmse,
            "n_year_pairs": self.n_year_pairs,
            "year_pairs": [list(p) for p in self.year_pairs],
            "residuals": list(self.residuals),
            "geoid_used": self.geoid_used,
            "vintage": self.vintage,
            "irs_panel_years": list(self.irs_panel_years),
            "notes": list(self.notes),
        }


def _load_acs_series(
    panel_path: Path, *, indicator: str, geoid: str, vintage: str = "1y",
) -> pd.Series:
    """Return ``year → estimate`` for one indicator+geography from the bundled panel."""
    with open(panel_path) as f:
        panel = json.load(f)
    obs = pd.DataFrame(panel["observations"])
    filt = (
        (obs["indicator"] == indicator)
        & (obs["geoid"] == geoid)
        & (obs["vintage"] == vintage)
    )
    sub = obs.loc[filt].sort_values("year")
    if sub.empty:
        raise ValueError(
            f"No ACS observations for indicator={indicator} geoid={geoid} "
            f"vintage={vintage} in {panel_path}"
        )
    return sub.set_index("year")["estimate"].astype(float)


def _load_irs_panel(panel_path: Path) -> pd.DataFrame:
    """Load the IRS SOI HI EITC panel; require ``year``, ``eitc_returns``."""
    df = pd.read_csv(panel_path)
    required = {"year", "eitc_returns"}
    missing = required - set(df.columns)
    if missing:
        raise KeyError(
            f"IRS panel at {panel_path} missing columns {sorted(missing)}"
        )
    return df.sort_values("year").reset_index(drop=True)


def _build_year_pairs(
    *,
    irs: pd.DataFrame,
    b19013: pd.Series,
    s1701: pd.Series,
) -> list[dict]:
    """Build per-year-pair growth factors for years present in all 3 series."""
    common = sorted(
        set(int(y) for y in irs["year"])
        & set(int(y) for y in b19013.index)
        & set(int(y) for y in s1701.index)
    )
    pairs: list[dict] = []
    irs_by_year = dict(zip(irs["year"].astype(int), irs["eitc_returns"], strict=False))
    for i in range(len(common) - 1):
        y0, y1 = common[i], common[i + 1]
        if y1 - y0 != 1:
            continue  # only consecutive pairs
        eitc_factor = irs_by_year[y1] / irs_by_year[y0]
        b_factor = float(b19013[y1]) / float(b19013[y0])
        p_factor = float(s1701[y1]) / float(s1701[y0])
        if eitc_factor <= 0 or b_factor <= 0 or p_factor <= 0:
            continue
        pairs.append({
            "year_pair": (y0, y1),
            "eitc_factor": eitc_factor,
            "b19013_factor": b_factor,
            "poverty_factor": p_factor,
        })
    return pairs


def calibrate_eitc_poverty_alpha(
    *,
    irs_panel_path: Path | None = None,
    acs_panel_path: Path | None = None,
    geoid: str = _HONOLULU_GEOID,
    vintage: str = "1y",
    exclude_pairs: Sequence[tuple[int, int]] = (
        (2019, 2020),  # COVID income shock (EITC drops because earned
                       # income falls, not because eligibility expands —
                       # violates the model's "poverty up → EITC up" frame)
        (2020, 2021),  # ARPA expansion (Rescue Plan, TY 2021 only)
        (2021, 2022),  # ARPA expiration shock
    ),
) -> AlphaCalibration:
    """Fit α from the bundled IRS + ACS panels.

    Parameters
    ----------
    irs_panel_path:
        Path to the IRS SOI HI EITC panel CSV. Defaults to the bundled
        ``data/external/irs_soi_hi_eitc_panel.csv``.
    acs_panel_path:
        Path to the ``census_forecaster`` ACS panel JSON. When ``None``,
        the function imports
        ``census_forecaster.scripts.build_calibration_panel`` to locate
        the bundled artifact path.
    geoid:
        County GEOID used as the Hawaii state proxy. Default is
        Honolulu (15003) which carries the bulk of state EITC filers.
    vintage:
        ACS vintage ("1y" or "5y"). The bundled panel ships 1-year
        estimates.
    exclude_pairs:
        Year-pairs to drop from the fit. Default excludes the 2020→2021
        pair because the one-time ARPA EITC expansion (Rescue Plan,
        TY 2021 only) dominated the signal — including it biases α
        toward zero. Pass ``()`` to keep all pairs.

    Returns
    -------
    :class:`AlphaCalibration` with the fitted α, RMSE, and per-pair
    residuals. Use ``.alpha`` to get the headline value.

    Notes
    -----
    Fit is OLS in log-space on a constrained model:
        log(EITC_growth) = log(B19013_growth) + α · log(poverty_growth) + ε
    Equivalent to single-variable regression of
        y = log(EITC_growth / B19013_growth)  vs  x = log(poverty_growth)
    with α as the slope (no intercept — the B19013 factor already
    captures the headline income response).
    """
    irs_path = irs_panel_path or _DEFAULT_PANEL_PATH
    if acs_panel_path is None:
        # _PACKAGE_ROOT = packages/tax_modeler/src/tax_modeler/
        # ../.. = packages/tax_modeler/src/
        # ../../.. = packages/tax_modeler/
        # ../../../.. = packages/
        acs_panel_path = (
            _PACKAGE_ROOT.parent.parent.parent
            / "census_forecaster" / "src" / "census_forecaster"
            / "data" / "calibration_panel" / "acs_panel.json"
        )
        if not acs_panel_path.exists():
            raise FileNotFoundError(
                f"ACS panel not found at {acs_panel_path}. Pass "
                "acs_panel_path= explicitly or rebuild it via "
                "census_forecaster.scripts.build_calibration_panel."
            )

    irs = _load_irs_panel(irs_path)
    b19013 = _load_acs_series(
        acs_panel_path, indicator="B19013_001E", geoid=geoid, vintage=vintage,
    )
    s1701 = _load_acs_series(
        acs_panel_path, indicator="S1701_C03_001E", geoid=geoid, vintage=vintage,
    )

    pairs = _build_year_pairs(irs=irs, b19013=b19013, s1701=s1701)
    if exclude_pairs:
        exclude_set = {tuple(p) for p in exclude_pairs}
        pairs = [p for p in pairs if p["year_pair"] not in exclude_set]
    if len(pairs) < 2:
        raise RuntimeError(
            f"Need ≥ 2 year-pairs to fit α; got {len(pairs)}. "
            "Check that the IRS and ACS panels share at least 3 "
            "consecutive years."
        )

    # log-space OLS via the closed-form single-variable regression with
    # no intercept (the B19013 factor is the model's anchor for the
    # headline income response):
    #     y = log(eitc_factor / b19013_factor)
    #     x = log(poverty_factor)
    #     α = Σ(x·y) / Σ(x²)
    y = np.log(np.array([p["eitc_factor"] / p["b19013_factor"] for p in pairs]))
    x = np.log(np.array([p["poverty_factor"] for p in pairs]))
    if np.sum(x ** 2) == 0:
        raise RuntimeError(
            "All poverty_factor values equal 1.0 — α is unidentified. "
            "Need at least one year-pair where the poverty rate changed."
        )
    alpha = float(np.sum(x * y) / np.sum(x ** 2))

    # Residuals in log-EITC-growth units (the modeled prediction is
    # log(b19013_factor) + α · log(poverty_factor); residual is observed
    # log(eitc_factor) − that).
    predicted_log_eitc = (
        np.log(np.array([p["b19013_factor"] for p in pairs])) + alpha * x
    )
    observed_log_eitc = np.log(np.array([p["eitc_factor"] for p in pairs]))
    residuals = observed_log_eitc - predicted_log_eitc
    rmse = float(np.sqrt(np.mean(residuals ** 2)))

    notes = [
        f"OLS log-space fit, no intercept (B19013 factor is the anchor). "
        f"n_year_pairs={len(pairs)}; "
        f"excluded_pairs={list(exclude_pairs) if exclude_pairs else []}.",
        "ARPA 2021 pair excluded by default — the one-time Rescue Plan "
        "EITC expansion (TY 2021 only) dominates the signal and biases "
        "α toward zero if included.",
        f"Geography: GEOID {geoid} (Honolulu County) as Hawaii state "
        f"proxy. ACS vintage: {vintage}.",
    ]
    return AlphaCalibration(
        alpha=alpha,
        rmse=rmse,
        n_year_pairs=len(pairs),
        year_pairs=tuple(p["year_pair"] for p in pairs),
        residuals=tuple(float(r) for r in residuals),
        geoid_used=geoid,
        vintage=vintage,
        irs_panel_years=tuple(sorted(int(y) for y in irs["year"])),
        notes=tuple(notes),
    )


def write_calibration_artifact(
    cal: AlphaCalibration,
    *,
    out_path: Path | None = None,
) -> Path:
    """Write the JSON artifact at ``data/calibration/eitc_poverty_alpha.json``."""
    out_path = out_path or _DEFAULT_ARTIFACT_PATH
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(cal.to_dict(), indent=2))
    LOG.info("Wrote α calibration artifact to %s (α=%.4f, rmse=%.4f)",
             out_path, cal.alpha, cal.rmse)
    return out_path


__all__ = [
    "AlphaCalibration",
    "calibrate_eitc_poverty_alpha",
    "write_calibration_artifact",
]
