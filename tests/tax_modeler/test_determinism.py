"""The PUMS pipeline gives the same answer on every run.

Regression for two sources of run-to-run drift found in the working-family
credits analysis (SPM poverty counts ranged 3,527-3,912 persons for identical
inputs):

1. adjustments/hawaii_credits.py drew unseeded ``np.random.random()`` for the
   renewable-energy and renters credits inside the base state-tax calculation.
2. TaxUnitConstructor iterated sets of person-ID strings, whose order follows
   PYTHONHASHSEED; when two adults could claim the same child, whichever came
   first won, so tax units changed between processes.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from tax_modeler.adjustments.hawaii_credits import HawaiiTaxCredits

REPO = Path(__file__).resolve().parents[2]


def test_legacy_credits_are_expected_values():
    c = HawaiiTaxCredits(year=2025)
    assert {c.renewable_energy_credit(80_000, "single") for _ in range(50)} == {0.02 * 1500}
    assert {c.low_income_renters_credit(12_000, "single") for _ in range(50)} == {0.30 * 150}
    assert c.renewable_energy_credit(40_000, "single") == 0
    assert c.low_income_renters_credit(90_000, "single") == 0


PUMS = REPO / "packages" / "data" / "raw" / "pums_2024_1yr"
# Households whose tax units changed with PYTHONHASHSEED before the fix: each
# has two adults who could claim the same child (e.g. grandparent, parent and
# grandchild), so the claimant depended on set iteration order.
CONFLICT_HOUSEHOLDS = ["2024HU0094871", "2024HU0718720", "2024HU0766159",
                       "2024HU0880938", "2024HU1249349"]

_BUILD_UNITS = """
import hashlib, sys, pandas as pd
from tax_modeler.units.constructor import TaxUnitConstructor
ids = sys.argv[2].split(",")
p = pd.read_parquet(sys.argv[1] + "/psam_p15.parquet")
h = pd.read_parquet(sys.argv[1] + "/psam_h15.parquet")
p, h = p[p["SERIALNO"].isin(ids)], h[h["SERIALNO"].isin(ids)]
u = TaxUnitConstructor(p, h, use_soi_calibration=False, progress_bar=False).create_rule_based_units(parallel=False)
u = u.assign(deps=u["dependents"].map(lambda d: ",".join(sorted(map(str, d)))))
cols = ["filer_id", "filing_status", "num_dependents", "deps"]
print(hashlib.sha256(u[cols].sort_values("filer_id").to_csv(index=False).encode()).hexdigest())
"""


@pytest.mark.skipif(not (PUMS / "psam_p15.parquet").exists(), reason="2024 PUMS not present")
def test_tax_units_do_not_depend_on_hash_seed():
    digests = set()
    for seed in ("0", "1", "2", "3"):
        out = subprocess.run(
            [sys.executable, "-c", _BUILD_UNITS, str(PUMS), ",".join(CONFLICT_HOUSEHOLDS)],
            env={**os.environ, "PYTHONHASHSEED": seed},
            capture_output=True, text=True, check=True,
        )
        digests.add(out.stdout.strip().splitlines()[-1])
    assert len(digests) == 1
