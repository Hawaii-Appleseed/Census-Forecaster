"""Shared fixtures for the tax_modeler test suite.

Two location helpers cover the two data-storage strategies in the
monorepo:

* :func:`bundled_data_dir` returns the path to data shipped *inside*
  the wheel (``packages/tax_modeler/src/tax_modeler/data/``) — small
  JSONs like tax brackets, deduction policy, etc.

* :func:`repo_data_dir` returns the path to data living at the
  monorepo root (``data/tax_modeler/``) — checked-in crosswalks and
  small IRS SOI tables, plus the gitignored ``raw/``, ``external/``,
  ``processed/`` directories that get fetched on demand.

Tests that depend on the gitignored heavy data should skip when the
relevant subdirectory is missing — see :func:`requires_dotax_raw` and
:func:`requires_irs_external` for the standard skip-marker fixtures.
"""
from __future__ import annotations

from importlib.resources import files
from pathlib import Path

import pandas as pd
import pytest


# -----------------------------------------------------------------------------
# Path resolvers
# -----------------------------------------------------------------------------

@pytest.fixture(scope="session")
def bundled_data_dir() -> Path:
    """Path to data files shipped inside the tax_modeler wheel."""
    return Path(str(files("tax_modeler") / "data"))


@pytest.fixture(scope="session")
def repo_data_dir() -> Path:
    """Path to data files at the monorepo root (``data/tax_modeler``).

    Resolves relative to this conftest's location:
    ``tests/tax_modeler/conftest.py`` -> ``../..`` is the repo root.
    """
    return Path(__file__).resolve().parents[2] / "data" / "tax_modeler"


# -----------------------------------------------------------------------------
# Skip-when-heavy-data-missing markers
# -----------------------------------------------------------------------------

def _has_files(d: Path) -> bool:
    return d.exists() and any(d.iterdir())


@pytest.fixture(scope="session")
def requires_dotax_raw(repo_data_dir: Path) -> Path:
    """Skips the test if ``data/tax_modeler/raw/`` is empty.

    Tests that need the 83 MB DOTAX SOI raw tables should declare a
    dependency on this fixture; the test is skipped on a fresh clone
    (where the raw data hasn't been fetched) and runs after the user
    runs ``python -m tax_modeler.scripts.fetch_dotax_soi``.
    """
    raw = repo_data_dir / "raw"
    if not _has_files(raw):
        pytest.skip(
            f"DOTAX raw data not present at {raw}. "
            "Run `python -m tax_modeler.scripts.fetch_dotax_soi` to populate."
        )
    return raw


@pytest.fixture(scope="session")
def requires_irs_external(repo_data_dir: Path) -> Path:
    """Skips the test if ``data/tax_modeler/external/`` is empty.

    For the 639 MB IRS national SOI Excel files. Same fetch-on-demand
    contract as :func:`requires_dotax_raw`.
    """
    ext = repo_data_dir / "external"
    if not _has_files(ext):
        pytest.skip(
            f"IRS SOI external data not present at {ext}. "
            "Run `python -m tax_modeler.scripts.fetch_irs_soi` to populate."
        )
    return ext


# -----------------------------------------------------------------------------
# Domain fixtures (ported from the original ctc-and-eitc tests/conftest.py)
# -----------------------------------------------------------------------------

@pytest.fixture(scope="session")
def sample_household_data() -> pd.DataFrame:
    """Synthetic person-level rows for two small households.

    Household 1: married couple, both 30s, ~$95k combined income.
    Household 2: single parent with one young child.

    Columns mirror the PUMS person-record schema (``SERIALNO``,
    ``SPORDER``, ``RELSHIPP``, ``SEX``, ``AGE``, ``MAR``, ``PINCP``,
    ``ADJINC``).
    """
    data = [
        # Household 1: married couple
        {"SERIALNO": "1", "SPORDER": "1", "RELSHIPP": "20", "SEX": "1",
         "AGE": "35", "MAR": "1", "PINCP": "50000", "ADJINC": "1000000"},
        {"SERIALNO": "1", "SPORDER": "2", "RELSHIPP": "20", "SEX": "2",
         "AGE": "32", "MAR": "1", "PINCP": "45000", "ADJINC": "1000000"},
        # Household 2: single parent with one child
        {"SERIALNO": "2", "SPORDER": "1", "RELSHIPP": "20", "SEX": "2",
         "AGE": "30", "MAR": "5", "PINCP": "40000", "ADJINC": "1000000"},
        {"SERIALNO": "2", "SPORDER": "2", "RELSHIPP": "25", "SEX": "1",
         "AGE": "8", "MAR": "6", "PINCP": "0", "ADJINC": "1000000"},
    ]
    return pd.DataFrame(data)


# -----------------------------------------------------------------------------
# The capital-gains page's scorer (forecast_cg_rate_options.py)
# -----------------------------------------------------------------------------

@pytest.fixture(scope="session")
def cg_page():
    """The capital-gains page's ``Scorer`` class, ``distribution`` function
    (with its ``_GROUPS``), ``BETA`` and ``CAP_CURRENT``, read from
    ``forecast_cg_rate_options.py`` without importing it: importing the
    script turns warnings and logging off for the whole test process."""
    import ast
    import types

    import numpy as np

    path = Path(__file__).resolve().parents[2] / "forecast_cg_rate_options.py"
    tree = ast.parse(path.read_text())
    wanted = [n for n in tree.body
              if (isinstance(n, ast.ClassDef) and n.name == "Scorer")
              or (isinstance(n, ast.FunctionDef) and n.name == "distribution")
              or (isinstance(n, ast.Assign)
                  and any(isinstance(t, ast.Name) and t.id in ("BETA", "CAP_CURRENT", "_GROUPS")
                          for t in n.targets))]
    ns: dict = {"np": np, "pd": pd}
    exec(compile(ast.Module(body=wanted, type_ignores=[]), str(path), "exec"), ns)
    return types.SimpleNamespace(Scorer=ns["Scorer"], BETA=ns["BETA"],
                                 CAP_CURRENT=ns["CAP_CURRENT"],
                                 distribution=ns["distribution"], GROUPS=ns["_GROUPS"])
