"""Exploratory: implied take-up of the HI renters credit vs DOTAX claims (see docs/data_source_tests/renters-credit-anchor.md)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import poverty_impact_report as pir
from tax_modeler.credits.hi_renters import compute_hi_renters_for_units

PUMS = REPO / "packages/data/raw/pums"
units, persons = pir._load_units(PUMS, False)
out = {}
for ty in (2022, 2023):
    u = pir._build_units_for_tax_year(units, ty, project=(ty != 2022), use_cbo_aging=True)
    full = compute_hi_renters_for_units(u, overrides={"takeup_pct": 1.0})
    w = full["weight"] if "weight" in full else full["WGTP"]
    amt = full["hi_renters_amount"].to_numpy()
    elig = amt > 0
    renter = (full["tenure"] == "renter").to_numpy() if "tenure" in full else np.ones(len(full), bool)
    ww = np.asarray(w, float)
    res = {"weight_col": "weight" if "weight" in full else "WGTP"}
    for name, m in (("income_only", elig), ("income_and_renter", elig & renter)):
        res[name] = {"units": float(ww[m].sum()), "dollars": float((ww * amt)[m].sum()),
                     "avg_credit": float((ww * amt)[m].sum() / ww[m].sum())}
    out[ty] = res
    print(ty, json.dumps(res, indent=1), flush=True)
Path("/tmp/rc_model.json").write_text(json.dumps(out, indent=1))
