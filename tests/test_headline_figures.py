"""The model on this checkout reproduces the published figures, byte for byte.

``site/data/`` holds what the estimates site publishes, copied from ``runs/`` by ``scripts/build_site.py
--import-runs``. Nothing else checked that the code still produces it: the chain's tests check exit codes, and the
site test checks that the committed HTML matches the committed data. This test runs the Act 24 forecast (chain step 1),
the comparison with pre-Act 46 law that reads it (step 5) and the working-family credits step into a temporary runs
directory (``CF_RUNS_DIR``) and compares every file the site publishes from them.

A change that moves a published number therefore fails here until the site is republished in the same branch
(``scripts/publish.py``, then commit ``site/``), which is the repo's shipping order anyway.

It needs the local inputs (PUMS, the tax-unit cache, the calibrated base), which are not committed, so it is skipped
on CI; ``tests/conftest.py`` allows that skip and no other. About a minute on warm caches; ``-m "not headline"``
leaves it out of a quick run.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SITE = REPO / "site" / "data"
PUMS_1YR = REPO / "packages" / "data" / "raw" / "pums_2024_1yr" / "psam_h15.parquet"
UNIT_CACHE = REPO / "data" / "artifacts" / "tax_units_cache.parquet"
PUMS_5YR = REPO / "packages" / "data" / "raw" / "pums_2020_2024"

STEPS = (
    ("chain/forecast_sb3125_enhanced.py", "--cd", "2"),
    ("chain/forecast_act24_vs_pre_act46.py",),
    ("chain/forecast_working_family_credits.py",),
)
# (published file under site/data, the run file it is copied from), as scripts/build_site.py maps them
PUBLISHED = (
    ("act-24/fiscal_by_scenario.csv", "sb3125_cd2_enhanced/enhanced.csv"),
    ("act-24/distribution_quintile.csv", "sb3125_cd2_enhanced/quintile.csv"),
    ("act-24/distribution_income_class.csv", "sb3125_cd2_enhanced/bracket.csv"),
    ("act-24/vs_pre_act46.csv", "act24_vs_pre_act46/decomposition.csv"),
    *((f"working-family-credits/{f}", f"working_family_credits/{f}") for f in (
        "revenue_by_year.csv", "calibration.csv", "eitc_check_ty2024.csv", "distribution_ty2028.csv",
        "by_family_type_ty2028.csv", "by_credit_ty2028.csv", "poverty_ty2028.csv", "sampling_error_ty2028.csv")),
)

pytestmark = [
    pytest.mark.headline,
    pytest.mark.skipif(not PUMS_1YR.exists() or not (UNIT_CACHE.exists() or PUMS_5YR.exists()),
                       reason="needs the local PUMS and tax-unit cache, which are not committed"),
]


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    out = tmp_path_factory.mktemp("runs")
    env = {**os.environ, "CF_RUNS_DIR": str(out)}
    env.setdefault("CF_MAX_WORKERS", "2")
    for step in STEPS:
        done = subprocess.run([sys.executable, *step], cwd=REPO, env=env, capture_output=True, text=True)
        assert done.returncode == 0, f"{' '.join(step)} failed:\n{done.stdout[-3000:]}\n{done.stderr[-3000:]}"
    return out


@pytest.mark.parametrize(("published", "made"), PUBLISHED, ids=[p for p, _ in PUBLISHED])
def test_the_model_reproduces_the_published_file(runs, published, made):
    site, run = (SITE / published).read_bytes(), (runs / made).read_bytes()
    if site != run:
        import difflib

        diff = "".join(list(difflib.unified_diff(site.decode().splitlines(True), run.decode().splitlines(True),
                                                 f"site/data/{published}", f"model:{made}", n=0))[:40])
        pytest.fail(f"site/data/{published} is not what the model makes now. If the change is intended, "
                    f"republish (scripts/publish.py) and commit site/.\n{diff}")
