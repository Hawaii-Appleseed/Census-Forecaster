"""Biproportional raking of tax-unit weights to IRS SOI district targets.

Currently the within-PUMA assignment of House/Senate districts uses a
deterministic ``SERIALNO`` hash (see :mod:`puma_imputation` /
:mod:`district_imputation`). Each district inside a PUMA gets a uniform
slice of the PUMA's filers — district-level estimates carry roughly
±20% uncertainty because the actual distribution of filers across
districts depends on within-PUMA demographics that the hash ignores.

Biproportional iterative proportional fitting (IPF) raises district
estimates to actual marginal totals while keeping the PUMA marginals
unchanged. Concretely, given:

  * a ``weight`` column on the input tax-unit frame
  * IRS SOI ZIP filer counts mapped to House districts
  * the existing ``PUMA`` and ``house_district`` columns

the algorithm rescales weights so that, simultaneously:

  * ``sum(weight)`` per ``house_district`` matches the IRS target
  * ``sum(weight)`` per ``PUMA`` stays at its pre-raking value (the
    ACS-anchored marginal we trust)
  * the state total ``sum(weight)`` is preserved by construction

Standard biproportional IPF converges in ~20-30 iterations on small
2-D problems. Tolerance: ``max |w_pre - w_post| / state_total < 1e-6``.

Data sources (the script-side wrapper expects bundled paths):

  * IRS SOI ZIP-code data for Hawaii TY2022, columns ``ZIPCODE``,
    ``N1`` (filer count). Source: irs.gov/statistics/soi-tax-stats-
    individual-income-tax-statistics-zip-code-data-soi.
  * ZIP → House district crosswalk. Sourced either from the Hawaii
    Legislature precinct-to-ZIP mapping or built from ArcGIS Hawaii
    district shapefiles + HUD USPS ZIP centroid file. Bundled as
    ``data/crosswalks/hawaii_zip_to_house_district.csv``.

The IRS SOI ZIP data + ZIP→HD crosswalk for Hawaii are **not bundled
with this Tier-2 ship**. When the bundle lands, the high-level wrapper
``rake_weights_to_irs_zip`` will load them automatically. Until then,
the wrapper raises :class:`MissingDataError` with a clear pointer when
``--rake-to-irs-zip`` is set without the data on disk, and users can
call :func:`rake_biproportional` directly with explicit targets.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path

import numpy as np
import pandas as pd

from tax_modeler.errors import MissingDataError

logger = logging.getLogger(__name__)


_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_IRS_ZIP_PATH = (
    _PACKAGE_ROOT / "data" / "raw" / "irs_soi_zip_hawaii_2022.csv"
)
_DEFAULT_ZIP_TO_HD_PATH = (
    _PACKAGE_ROOT / "data" / "crosswalks" / "hawaii_zip_to_house_district.csv"
)


def rake_biproportional(
    weights: np.ndarray,
    *,
    puma_ids: np.ndarray,
    hd_ids: np.ndarray,
    hd_targets: Mapping[int, float],
    max_iter: int = 50,
    tol: float = 1e-6,
) -> np.ndarray:
    """Biproportional IPF on (PUMA × House district) marginals.

    Holds per-PUMA ``sum(weight)`` at its pre-raking value, scales
    weights so per-HD ``sum(weight)`` matches ``hd_targets``. Returns
    the new weight vector (same length as ``weights``).

    Convergence criterion: maximum relative drift of any HD or PUMA
    marginal across an iteration falls below ``tol``. Typical
    convergence on Hawaii-scale problems (~10 PUMAs, ~51 HDs) is 20-30
    iterations.
    """
    if len(weights) != len(puma_ids) or len(weights) != len(hd_ids):
        raise ValueError("weights, puma_ids, hd_ids must be equal length")
    w = np.asarray(weights, dtype=float).copy()
    pumas = np.asarray(puma_ids)
    hds = np.asarray(hd_ids)

    # Pre-raking PUMA marginals (the "fixed" axis).
    puma_targets: dict = {p: float(w[pumas == p].sum()) for p in np.unique(pumas)}
    hd_targets_d: dict = {int(k): float(v) for k, v in hd_targets.items()}

    for it in range(max_iter):
        # Step A: scale weights so per-HD sum matches hd_targets.
        max_drift = 0.0
        for hd, target in hd_targets_d.items():
            mask = hds == hd
            current = float(w[mask].sum())
            if current > 0 and target > 0:
                factor = target / current
                w[mask] *= factor
                max_drift = max(max_drift, abs(factor - 1.0))
            elif target > 0 and current == 0:
                # No mass on this HD pre-raking — cannot scale up.
                logger.warning(
                    "rake_biproportional: HD %s has zero pre-raking mass; target=%.0f unmet",
                    hd, target,
                )

        # Step B: scale weights so per-PUMA sum returns to pre-raking value.
        for puma, target in puma_targets.items():
            mask = pumas == puma
            current = float(w[mask].sum())
            if current > 0 and target > 0:
                factor = target / current
                w[mask] *= factor
                max_drift = max(max_drift, abs(factor - 1.0))

        if max_drift < tol:
            logger.info("rake_biproportional converged in %d iterations", it + 1)
            return w

    logger.warning(
        "rake_biproportional did not converge within %d iterations (max_drift=%.2e)",
        max_iter, max_drift,
    )
    return w


def _load_irs_zip_targets(
    path: Path, tax_year: int,
) -> pd.DataFrame:
    if not path.exists():
        raise MissingDataError(
            "IRS SOI ZIP-code data not bundled. Download Hawaii ZIPs from "
            "https://www.irs.gov/statistics/soi-tax-stats-individual-income-"
            "tax-statistics-zip-code-data-soi and place at "
            f"{path}. Required columns: ZIPCODE, N1 (filer count).",
            path=path,
        )
    df = pd.read_csv(path, dtype={"ZIPCODE": str})
    if "ZIPCODE" not in df.columns or "N1" not in df.columns:
        raise MissingDataError(
            f"IRS SOI ZIP file at {path} missing required ZIPCODE / N1 columns.",
            path=path,
        )
    return df[["ZIPCODE", "N1"]].dropna()


def _load_zip_to_hd_crosswalk(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise MissingDataError(
            "ZIP → House district crosswalk not bundled. Build from Hawaii "
            "Legislature precinct-to-ZIP mapping or ArcGIS district shapefiles + "
            "HUD USPS ZIP centroid, place at "
            f"{path}. Required columns: ZIPCODE, house_district, weight.",
            path=path,
        )
    df = pd.read_csv(path, dtype={"ZIPCODE": str})
    return df


def rake_weights_to_irs_zip(
    units: pd.DataFrame,
    *,
    tax_year: int,
    irs_zip_path: Path | None = None,
    zip_to_hd_path: Path | None = None,
    weight_col: str = "weight",
    puma_col: str = "PUMA",
    hd_col: str = "house_district",
) -> pd.DataFrame:
    """Rake tax-unit weights to IRS SOI ZIP filer totals by House district.

    Loads IRS SOI ZIP data + ZIP→HD crosswalk from the bundled paths
    (overridable), aggregates ZIPs to HD-level filer targets, and calls
    :func:`rake_biproportional` to adjust ``weight`` so per-HD totals
    match the IRS marginals while per-PUMA totals are preserved.

    Both supporting data files are downstream artifacts; this function
    raises :class:`tax_modeler.errors.MissingDataError` with a clear
    download pointer when they're absent. See the module docstring for
    the source URLs.
    """
    irs_path = Path(irs_zip_path) if irs_zip_path else _DEFAULT_IRS_ZIP_PATH
    zip_to_hd_pth = Path(zip_to_hd_path) if zip_to_hd_path else _DEFAULT_ZIP_TO_HD_PATH

    irs = _load_irs_zip_targets(irs_path, tax_year)
    crosswalk = _load_zip_to_hd_crosswalk(zip_to_hd_pth)
    merged = irs.merge(crosswalk, on="ZIPCODE", how="inner")
    # Allow fractional ZIP → HD weights for ZIPs that span district boundaries.
    cwt_col = "weight" if "weight" in crosswalk.columns else None
    if cwt_col:
        merged["N1_allocated"] = merged["N1"] * merged[cwt_col]
    else:
        merged["N1_allocated"] = merged["N1"]
    hd_targets = (
        merged.groupby("house_district")["N1_allocated"].sum().to_dict()
    )

    df = units.copy()
    new_w = rake_biproportional(
        df[weight_col].to_numpy(dtype=float),
        puma_ids=df[puma_col].to_numpy(),
        hd_ids=df[hd_col].to_numpy(),
        hd_targets=hd_targets,
    )
    df[weight_col] = new_w
    return df


__all__ = ["rake_biproportional", "rake_weights_to_irs_zip"]
