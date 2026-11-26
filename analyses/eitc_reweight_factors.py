#!/usr/bin/env python3
"""The EITC by-children reweight ("lever 3a") factors the working-family step finds on the current tax-unit code.

The factors are not constants: ``poverty_impact_report._apply_eitc_reweight`` finds them every run, as the ratio of
each short qualifying-child bucket's IRS Hawaii target (``takeup_imputation.EITC_RETURNS_BY_CHILDREN_HI_TY2022``)
to the weighted count of the units in it that are EITC-eligible, after the IRS household rake. This prints them
for the years the working-family step reports (2024 and 2028), and the eligible count by bucket before and after
the IRS rake, so the same script run on the old and the new construction gives the old and new factors.

    CF_MAX_WORKERS=2 uv run python analyses/eitc_reweight_factors.py --out /tmp/eitc_reweight_new.json
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import warnings
from pathlib import Path

import numpy as np

warnings.filterwarnings("ignore")
REPO = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(REPO / "scripts")]


def load_step():
    spec = importlib.util.spec_from_file_location("wfc_step", REPO / "chain" / "forecast_working_family_credits.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules["wfc_step"] = m
    spec.loader.exec_module(m)
    return m


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--years", type=int, nargs="+", default=[2024, 2028])
    args = ap.parse_args()

    import poverty_impact_report as pir
    from tax_modeler.calibration import irs_household_rake as rk
    from tax_modeler.calibration.eitc_reweight import reweight_eitc_eligibles_by_children
    from tax_modeler.calibration.takeup_imputation import EITC_RETURNS_BY_CHILDREN_HI_TY2022

    step = load_step()
    base, _ = step.load_population()
    rake = step.irs_rake(base, per_replicate=False)
    out: dict = {"targets": {str(k): v for k, v in EITC_RETURNS_BY_CHILDREN_HI_TY2022.items()},
                 "irs_rake": {k: (float(v) if isinstance(v, (int, float, np.floating)) else v)
                              for k, v in rake.info.items()}, "years": {}}
    for yr in args.years:
        aged = pir._build_units_for_tax_year(base, yr, project=True, use_cbo_aging=True)
        raked = rk.apply_household_factors(aged, rake.factors)
        w0 = raked["weight"].to_numpy(float)
        elig = raked["eitc_amount"].fillna(0).to_numpy(float) > 0
        k = np.minimum(raked["eitc_qualifying_children"].fillna(0).to_numpy(int), 3)
        before = {str(b): float(w0[elig & (k == b)].sum()) for b in range(4)}
        _, info = reweight_eitc_eligibles_by_children(raked)
        out["years"][str(yr)] = {
            "eligible_by_children_after_irs_rake": before,
            "reweight_factors": {str(b): float(v["factor"]) for b, v in info["buckets"].items()},
            "total_weight_before": float(info["total_weight_before"]),
            "total_weight_after": float(info["total_weight_after"]),
            "total_weight_change_pct": float(100 * (info["total_weight_after"] / info["total_weight_before"] - 1)),
        }
        print(yr, json.dumps(out["years"][str(yr)]))
    args.out.write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
