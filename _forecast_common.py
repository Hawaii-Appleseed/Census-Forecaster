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


def fingerprint(*parts) -> str:
    """A sha256 key for a cached step output: every file under each workspace
    package's ``src`` (``tax_modeler`` and the ``census_forecaster``,
    ``pums_estimator`` and ``common`` code it calls), the Python, numpy and
    pandas versions, and *parts* — Paths (a file's bytes, a directory's
    files), bytes, or anything ``json.dumps`` takes. Hashing whole packages,
    not the modules a step imports, means any model change misses every
    cache: slower, never stale. (Until 2026-10-29 only ``tax_modeler`` was
    hashed, so a change in the packages it imports kept a stale cache.)
    """
    import hashlib
    import json

    import numpy as np
    import pandas as pd

    h = hashlib.sha256()

    def add_path(p: Path) -> None:
        files = sorted(f for f in p.rglob("*") if f.is_file() and "__pycache__" not in f.parts) \
            if p.is_dir() else [p]
        for f in files:
            h.update(str(f.relative_to(REPO) if f.is_relative_to(REPO) else f).encode())
            h.update(f.read_bytes())

    for src in sorted((REPO / "packages").glob("*/src")):
        add_path(src)
    h.update(f"{sys.version}|numpy {np.__version__}|pandas {pd.__version__}".encode())
    for part in parts:
        if isinstance(part, Path):
            add_path(part)
        elif isinstance(part, bytes):
            h.update(part)
        else:
            h.update(json.dumps(part, sort_keys=True, default=str).encode())
    return h.hexdigest()


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
