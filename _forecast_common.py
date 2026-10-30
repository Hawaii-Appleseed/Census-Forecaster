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
from contextlib import contextmanager
from pathlib import Path

REPO = Path(__file__).resolve().parent

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


# The narrow cache key (fingerprint(code=...)); CF_NARROW_FINGERPRINT=0 hashes the whole packages.
NARROW_FINGERPRINT = os.environ.get("CF_NARROW_FINGERPRINT", "1") != "0"


def _workspace_modules() -> dict[str, Path]:
    """Every importable workspace module, by dotted name: the packages under
    ``packages/*/src``, the root scripts and ``scripts/*.py`` (the steps put
    both directories on ``sys.path``)."""
    mods: dict[str, Path] = {}
    for f in sorted(REPO.glob("*.py")) + sorted((REPO / "scripts").glob("*.py")):
        mods.setdefault(f.stem, f.resolve())
    for src in sorted((REPO / "packages").glob("*/src")):
        for f in sorted(src.rglob("*.py")):
            parts = f.relative_to(src).with_suffix("").parts
            mods[".".join(parts[:-1] if parts[-1] == "__init__" else parts)] = f.resolve()
    return mods


def code_closure(*entries: str | Path) -> list[Path] | None:
    """The source files of every workspace module *entries* (dotted module
    names, or script Paths) can import, or None if one of them imports code
    in a way this cannot follow.

    It reads the source, it does not run it, and errs toward too much: an
    import anywhere in a file counts (inside a function, under
    ``TYPE_CHECKING``), as do the ``__init__`` of each package on the way and,
    for ``from a import b``, both ``a`` and ``a.b``. ``importlib.import_module``
    or ``__import__`` with a non-literal name, ``spec_from_file_location``,
    ``runpy`` and ``exec`` make it give up. Code that reaches a step some other
    way — the methods of an object the caller passes in, a class a pickle
    names — is the caller's to list in *entries*; :func:`closure_guard`
    catches what is imported while the step runs.
    """
    import ast

    mods = _workspace_modules()
    by_file = {f: m for m, f in mods.items()}

    def resolve(name: str) -> str | None:
        while name:
            if name in mods:
                return name
            name = name.rpartition(".")[0]
        return None

    def imports(name: str) -> set[str] | None:
        f = mods[name]
        tree = ast.parse(f.read_bytes(), filename=str(f))
        package = name if f.name == "__init__.py" else name.rpartition(".")[0]
        out: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                out.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    up = package.split(".")[:len(package.split(".")) - (node.level - 1)]
                    base = ".".join(up + ([base] if base else []))
                out.add(base)
                out.update(f"{base}.{a.name}" for a in node.names)
            elif isinstance(node, ast.Call):
                fn = node.func
                called = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
                if called in ("import_module", "__import__"):
                    arg = node.args[0] if node.args else None
                    if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
                        return None
                    out.add(arg.value)
                elif called in ("spec_from_file_location", "run_path", "run_module", "exec"):
                    return None
        return out

    todo = []
    for e in entries:
        if isinstance(e, Path):
            name = by_file.get(e.resolve())
            if name is None:
                raise ValueError(f"{e} is not a workspace module")
        else:
            name = resolve(e)
            if name != e:
                raise ValueError(f"{e} is not a workspace module")
        todo.append(name)
    seen: set[str] = set()
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        parts = name.split(".")
        todo.extend(p for i in range(1, len(parts)) if (p := resolve(".".join(parts[:i]))))
        found = imports(name)
        if found is None:
            return None
        todo.extend(r for i in found if (r := resolve(i)) and r not in seen)
    return sorted(mods[m] for m in seen)


def _is_workspace_file(f: Path) -> bool:
    return (f.parent in (REPO, REPO / "scripts")
            or any(f.is_relative_to(src) for src in (REPO / "packages").glob("*/src")))


@contextmanager
def closure_guard(code: tuple):
    """Around a cached step's computation (a cache miss): fail if it imports
    a workspace module that ``code_closure(*code)`` left out, since a change
    there would not have changed the key. Off with the narrow key
    (``CF_NARROW_FINGERPRINT=0``); a module loaded before the step began is
    not seen, so ``scripts/check_code_closure.py`` runs each step in a fresh
    process."""
    files = code_closure(*code) if NARROW_FINGERPRINT else None
    before = set(sys.modules)
    yield
    if files is None:
        return
    allowed = set(files)
    missed = sorted(
        f"{name} ({f.relative_to(REPO)})"
        for name in set(sys.modules) - before - {"__main__", "__mp_main__"}  # the step's script
        if (path := getattr(sys.modules[name], "__file__", None))
        and _is_workspace_file(f := Path(path).resolve()) and f not in allowed
    )
    if missed:
        raise RuntimeError("the cache key's code closure missed modules this step imported: "
                           + ", ".join(missed) + ". Add them to its entries.")


def fingerprint(*parts, code: tuple | None = None) -> str:
    """A sha256 key for a cached step output: the model code, the Python,
    numpy and pandas versions, and *parts* — Paths (a file's bytes, a
    directory's files), bytes, or anything ``json.dumps`` takes.

    The model code is every file under each workspace package's ``src``
    (``tax_modeler`` and the ``census_forecaster``, ``pums_estimator`` and
    ``common`` code it calls) and ``config``: any model change misses every
    cache, slower but never stale. (Until 2026-10-29 only ``tax_modeler``
    was hashed, so a change in the packages it imports kept a stale cache;
    until the narrow key, ``packages/*/config`` was left out.)

    Given *code* (the step's entry modules, see :func:`code_closure`), the
    Python files are only those *code* can
    import, so an edit elsewhere keeps the cache; every non-Python file
    under ``src`` and ``config`` is still hashed. If the closure cannot be
    traced, or ``CF_NARROW_FINGERPRINT=0``, it hashes the whole packages
    as above.
    """
    import hashlib
    import json

    import numpy as np
    import pandas as pd

    h = hashlib.sha256()

    def add_file(f: Path) -> None:
        h.update(str(f.relative_to(REPO) if f.is_relative_to(REPO) else f).encode())
        h.update(f.read_bytes())

    def add_path(p: Path) -> None:
        files = sorted(f for f in p.rglob("*") if f.is_file() and "__pycache__" not in f.parts) \
            if p.is_dir() else [p]
        for f in files:
            add_file(f)

    closure = code_closure(*code) if NARROW_FINGERPRINT and code else None
    trees = sorted([*(REPO / "packages").glob("*/src"), *(REPO / "packages").glob("*/config")])
    if closure is None:
        for tree in trees:
            add_path(tree)
    else:
        h.update(b"narrow")
        for f in closure:
            add_file(f)
        for tree in trees:
            for f in sorted(f for f in tree.rglob("*") if f.is_file() and f.suffix not in (".py", ".pyc")
                            and "__pycache__" not in f.parts):
                add_file(f)
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
