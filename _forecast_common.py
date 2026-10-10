"""Shared preamble for the root-level forecast_*.py scripts.

Consolidates the constants, CLI parsing, and cache-loading blocks that
were copy-pasted across the SB 3125 / HB 2306 script family. Root
scripts only — package code must not import this module.
"""
from __future__ import annotations

import logging
import os
import sys
import warnings
from pathlib import Path

REPO = Path(__file__).parent

# Raw PUMS location (only needed when rebuilding the tax-unit cache).
#
# pums_2020_2024/ is the 2020-24 5yr vintage the cache is built from,
# vendored 2026-08-03 from ~/ctc-and-eitc/data/raw/pums (byte-identical
# copies; gitignored, disk-only). The sibling pums/ dir holds the OLDER
# 2018-2022 vintage and is kept for the census_forecaster loaders that
# still read it — do not point this default there.
DATA_DIR = Path(
    os.environ.get("HAWAII_PUMS_DIR")
    or REPO / "packages" / "data" / "raw" / "pums_2020_2024"
)

# Versioned artifacts live in-repo (gitignored) — /tmp caches had no
# invalidation and silently served stale bases across code changes.
ARTIFACT_DIR = REPO / "data" / "artifacts"
CACHE_FILE = ARTIFACT_DIR / "tax_units_cache.parquet"
CALIBRATED_PKL = ARTIFACT_DIR / "sb3125_calibrated_base.pkl"

# Manifested run outputs (Phase 1, DASHBOARD_PIPELINE_SCOPE.md).
# One directory per script invocation family, each with a manifest.json
# written via tax_modeler.runs.write_run_manifest.
RUNS_DIR = REPO / "runs"

TARGET_YEARS = [2027, 2028, 2029, 2030, 2031]


def cache_provenance() -> dict | None:
    """Contents of the tax-unit cache sidecar, for run-manifest inputs."""
    import json

    sidecar = CACHE_FILE.with_suffix("").with_suffix(".meta.json")
    if not sidecar.exists():
        return None
    with open(sidecar) as f:
        return json.load(f)


CALIBRATION_REPORT_NAME = "calibration_report.json"


def assemble_calibration_report(
    base_meta: dict | None,
    year_reports: dict | None = None,
    *,
    run_dir: Path | None = None,
    print_report: bool = True,
):
    """The run's calibration report: the base rake's (embedded in the
    calibrated-base artifact by forecast_sb3125_enhanced.py) with each
    projection year's re-anchoring report as a child.

    Prints the markdown summary (cells go to the JSON, not the console) and,
    when ``run_dir`` is given, writes ``calibration_report.json`` next to the
    run's manifest. Returns the report.
    """
    from tax_modeler.calibration.report import CalibrationReport

    embedded = (base_meta or {}).get("calibration_report")
    if embedded:
        report = CalibrationReport.from_dict(embedded)
    else:
        report = CalibrationReport(
            stage="base_ipf_rake (not recorded)", converged=True, iterations=0,
            max_iterations=0, tolerance=float("nan"),
            warnings=["The calibrated-base artifact predates the calibration report; "
                      "re-run forecast_sb3125_enhanced.py to embed it."],
        )
    for yr in sorted(year_reports or {}):
        child = year_reports[yr]
        if child is not None:
            report.children.append(child)
    if print_report:
        print("\n" + report.to_markdown(max_cells=0), flush=True)
        if report.any_nonconverged():
            print("\n!! A calibration stage did not converge — see the report above.", flush=True)
    if run_dir is not None:
        path = report.to_json(Path(run_dir) / CALIBRATION_REPORT_NAME)
        print(f"Saved calibration report: {path}", flush=True)
    return report


def silence_noise() -> None:
    """Suppress library warnings/log spam in analytical script output."""
    warnings.filterwarnings("ignore")
    logging.disable(logging.WARNING)


def add_replicate_se_arg(p) -> None:
    """``--replicate-se`` / ``--no-replicate-se`` (default ON): SDR sampling SE
    and 90% CI on the revenue aggregates from the 80 PUMS replicate weights
    (rake once, score replicates; ``tax_modeler.uncertainty.replicates``)."""
    import argparse

    p.add_argument(
        "--replicate-se", action=argparse.BooleanOptionalAction, default=True,
        help="(Default ON.) Emit SDR sampling standard errors and 90%% CIs on the "
             "revenue aggregates from the 80 PUMS replicate weights, with the "
             "calibration ratio applied to each replicate (rake once, score "
             "replicates). Sampling variance only: not the anchors, aging, "
             "behavioral parameters or the credit overlay. Needs a tax-unit "
             "cache built with replicate weights (forecast_sb3125.py).",
    )


def parse_cd_args(description: str | None = None, *, replicate_se: bool = False):
    """Standard ``--cd {1,2}`` CLI shared by the SB 3125 scripts; with
    ``replicate_se`` the ``--replicate-se`` flag (``add_replicate_se_arg``)."""
    import argparse

    p = argparse.ArgumentParser(description=description)
    p.add_argument(
        "--cd", choices=["1", "2"], default="1",
        help="Conference draft to model: 1=CD1 (default), 2=CD2",
    )
    if replicate_se:
        add_replicate_se_arg(p)
    return p.parse_args()


def load_cached_units(cd: str = "1"):
    """Load the tax-unit cache, exiting with guidance if it is missing.

    Returns the raw cached units DataFrame (sidecar-verified).
    """
    import pandas as pd

    from tax_modeler.artifacts import check_cache_sidecar

    if not CACHE_FILE.exists():
        print(f"ERROR: tax-unit cache not found at {CACHE_FILE}", flush=True)
        print(f"Run forecast_sb3125.py --cd {cd} first to populate the cache.", flush=True)
        sys.exit(1)

    print(f"Loading cached units from {CACHE_FILE}...", flush=True)
    check_cache_sidecar(CACHE_FILE)
    units = pd.read_parquet(CACHE_FILE)
    print(f"  {len(units):,} units loaded", flush=True)
    return units
