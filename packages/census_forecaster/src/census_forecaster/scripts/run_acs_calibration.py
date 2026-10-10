"""Regenerate the bundled ACS v3 stratified calibration from the panel.

Reads the bundled calibration panel
(`data/calibration_panel/{acs_panel,county_population_2020,manifest}.json`)
and runs the v3 stratified calibration (population-stratified, multi-horizon,
bias-corrected), writing the result to
`data/anchors/calibration.json`.

Intended to be called:
  * By the CI auto-refresh workflow after the panel is built / already present.
  * Locally after `build_calibration_panel.py` to regenerate calibration.json.

Does **not** require a Census API key — it reads the already-bundled panel.
If the panel is absent, it exits with a clear remediation message.

Usage
-----
    python -m census_forecaster.scripts.run_acs_calibration
    python -m census_forecaster.scripts.run_acs_calibration --anchor-start 2014 --anchor-end 2022
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Regenerate ACS v3 stratified calibration from the bundled panel.",
    )
    parser.add_argument(
        "--anchor-start", type=int, default=2014,
        help="First anchor year for walk-forward folds (default 2014).",
    )
    parser.add_argument(
        "--anchor-end", type=int, default=2023,
        help="Last anchor year for walk-forward folds (default 2023). "
             "Anchors whose targets run past the panel's truth keep only the "
             "horizons that have truth (reported in fold_truth_availability).",
    )
    parser.add_argument(
        "--horizons", type=str, default="1,2,3,4,5",
        help="Comma-separated forecast horizons in years (default 1,2,3,4,5).",
    )
    parser.add_argument(
        "--n-threshold", type=int, default=20,
        help="Minimum folds for a cell to receive calibration (default 20).",
    )
    parser.add_argument(
        "--include-ml", action="store_true", default=False,
        help="Include ml_trend ensemble member in calibration (adds ~5 min HGB training).",
    )
    parser.add_argument(
        "--include-kalman", action="store_true", default=False,
        help="Include kalman_state_space ensemble member in calibration.",
    )
    parser.add_argument(
        "--include-conformal", action="store_true", default=False,
        help=(
            "Split anchor years into tuning/calibration/evaluation and compute "
            "per-stratum split-conformal quantiles (schema_version 4)."
        ),
    )
    parser.add_argument(
        "--enable-phi", action="store_true", default=False,
        help=(
            "Apply v4 per-cell phi: project Pass 2 trend folds at the "
            "calibrated phi and mark the payload phi_enabled so the ensemble "
            "applies it. Off by default — the v4 phi ablation failed its "
            "acceptance bar (METHODOLOGY.md §v4); phi records are emitted as "
            "diagnostics either way."
        ),
    )
    parser.add_argument(
        "--as-of-mode", choices=["instant", "publication"], default="instant",
        help=(
            "How to truncate training data per fold. "
            "'instant' (default): include all observations with effective_year ≤ anchor_year. "
            "'publication': additionally filter by publication_date ≤ acs_1y_release_date(anchor_year), "
            "simulating the information set available when the anchor-year ACS was released."
        ),
    )
    parser.add_argument(
        "--out", type=Path, default=None,
        help="Output path for calibration.json. Default: data/anchors/calibration.json.",
    )
    args = parser.parse_args(argv)

    # Import here so import errors surface cleanly.
    from .load_calibration_panel import load_panel, PanelMissingError
    from ..acs.calibration import run_stratified_calibration, write_calibration
    from ..acs.anchors import DEFAULT_CALIBRATION_PATH

    # --- Load panel ---
    try:
        print("[1/3] Loading calibration panel ...", file=sys.stderr)
        series_by_key, populations, manifest = load_panel()
    except PanelMissingError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    n_series = len(series_by_key)
    n_obs = sum(len(v) for v in series_by_key.values())
    n_counties = len(populations)
    print(
        f"      {n_series} (geoid, indicator) series, "
        f"{n_obs} observations, {n_counties} counties",
        file=sys.stderr,
    )

    # --- Run calibration ---
    anchor_years = list(range(args.anchor_start, args.anchor_end + 1))
    horizons = [int(h.strip()) for h in args.horizons.split(",") if h.strip()]
    print(
        f"[2/3] Running v3 stratified calibration "
        f"(anchors {anchor_years[0]}-{anchor_years[-1]}, "
        f"h={horizons[0]}..{horizons[-1]}) ...",
        file=sys.stderr,
    )

    payload = run_stratified_calibration(
        series_by_key=series_by_key,
        anchor_years=anchor_years,
        horizons=tuple(horizons),
        populations=populations,
        n_threshold=args.n_threshold,
        as_of_mode=args.as_of_mode,
        include_ml=args.include_ml,
        include_kalman=args.include_kalman,
        include_conformal=args.include_conformal,
        enable_phi=args.enable_phi,
    )

    sr = payload.get("strata_records", {})
    n_kappa = len(sr.get("se_inflator", []))
    n_bias  = len(sr.get("bias", []))
    print(f"      {n_kappa} κ strata records, {n_bias} bias records", file=sys.stderr)

    # Which horizons the split anchors actually had truth for. Anchors
    # within max(h) of the panel's last truth year have partial horizons;
    # the generator tabulates them rather than dropping folds silently.
    fta = payload.get("fold_truth_availability", {})
    if fta:
        print(
            f"      fold split: tuning {fta.get('tuning_anchors')}, "
            f"calibration {fta.get('calibration_anchors')} "
            f"(h with truth: {fta.get('calibration_horizons')}), "
            f"evaluation {fta.get('evaluation_anchors')} "
            f"(h with truth: {fta.get('evaluation_horizons')})",
            file=sys.stderr,
        )

    # Coverage summary (marginalised)
    for ind, methods in sorted(
        payload.get("ci90_coverage_by_indicator_method", {}).items()
    ):
        for meth, cov in sorted(methods.items()):
            print(f"      {ind}/{meth}: CI90 coverage = {cov:.1%}", file=sys.stderr)

    # --- Write ---
    out_path: Path = args.out or DEFAULT_CALIBRATION_PATH
    print(f"[3/3] Writing {out_path} ...", file=sys.stderr)
    write_calibration(payload, out_path)
    size_kb = out_path.stat().st_size // 1024
    print(f"Done. {out_path} written ({size_kb} KB)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
