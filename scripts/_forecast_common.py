"""Shared preamble for the chain/ and analyses/ forecast scripts.

Consolidates the constants, CLI parsing, and cache-loading blocks that
were copy-pasted across the SB 3125 / HB 2306 script family. Scripts
only — package code must not import this module (the cache key lives in
``common.fingerprint``, which package code may import).
"""
from __future__ import annotations

import logging
import os
import sys
import warnings
from pathlib import Path

from common.fingerprint import REPO, fingerprint

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

# Manifested run outputs (Phase 1, docs/archive/DASHBOARD_PIPELINE_SCOPE.md).
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


POPULATION_DIR = ARTIFACT_DIR / "populations"


def population_store():
    """The chain's shared populations for the current calibrated base
    (``tax_modeler.scenarios.population_store``), keyed by the model code and
    the calibrated-base pickle, which step 1 rewrites on every run."""
    from tax_modeler.scenarios.population_store import PopulationStore

    return PopulationStore(POPULATION_DIR, fingerprint(CALIBRATED_PKL))


def silence_noise() -> None:
    """Suppress library warnings/log spam in analytical script output."""
    warnings.filterwarnings("ignore")
    logging.disable(logging.WARNING)


def parse_cd_args(description: str | None = None):
    """Standard ``--cd {1,2}`` CLI shared by the SB 3125 scripts."""
    import argparse

    p = argparse.ArgumentParser(description=description)
    p.add_argument(
        "--cd", choices=["1", "2"], default="1",
        help="Conference draft to model: 1=CD1 (default), 2=CD2",
    )
    return p.parse_args()


def load_cached_units(cd: str = "1", built_by: str = "_forecast_common.load_cached_units"):
    """The raw tax-unit cache, built from PUMS if it is missing.

    Thin wrapper over ``tax_modeler.base.load_or_build_raw_units``; exits with
    guidance when the cache and the PUMS files are both missing.
    """
    from tax_modeler.base import load_or_build_raw_units
    from tax_modeler.errors import MissingDataError

    try:
        return load_or_build_raw_units(CACHE_FILE, DATA_DIR, built_by=built_by)
    except MissingDataError as e:
        print(f"ERROR: tax-unit cache not found at {CACHE_FILE} and PUMS is missing.", flush=True)
        print(str(e), flush=True)
        print(f"Place the PUMS files, or copy the cache over, then re-run (cd={cd}).", flush=True)
        sys.exit(1)
