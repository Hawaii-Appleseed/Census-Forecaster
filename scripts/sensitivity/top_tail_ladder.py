"""The Act 24 MID five-year total under each step of the top-tail changes.

One configuration per process (the gains settings are read at import::

    CG_GROWTH_MODEL=cbo DOTAX_GAINS_WINDOW=none \\
        uv run python scripts/sensitivity/top_tail_ladder.py --aging scalar --drift 0.010 --label step0

Runs ``forecast_sb3125_enhanced.run_one_scenario`` for MID only (CD2, the
DOTAX-anchored gains base, no replicate band) on
``data/artifacts/sb3125_calibrated_base.pkl`` and appends one row to
``runs/top_tail_ladder/ladder.csv``: the five-year static bracket change,
post-response bracket change, credit overlay and total, with the settings.
The published figure (October 9, 2026: $687.8M; $689.8M on the cache with
replicate weights) is step 0: scalar engine, +1.0%/yr premium, CBO x 0.85
gains growth on the TY2023 edition as printed.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--aging", choices=["cbo", "scalar"], default="cbo")
    p.add_argument("--drift", type=float, default=None, help="override MID's top_share_drift")
    p.add_argument("--label", required=True)
    a = p.parse_args()

    from _forecast_common import CALIBRATED_PKL, RUNS_DIR, TARGET_YEARS, silence_noise
    silence_noise()
    import pandas as pd
    from forecast_sb3125_enhanced import SCENARIOS, run_one_scenario
    from tax_modeler.artifacts import load_calibrated_base
    from tax_modeler.calibration import cg_anchor, dotax_base
    from tax_modeler.projection.engine import AgingSpec
    from tax_modeler.scenarios.act24_population import mid_gains_factors

    base, ded, meta = load_calibrated_base(CALIBRATED_PKL)
    cal_year = int(meta.get("tax_year", 2023))
    mid = dict(next(s for s in SCENARIOS if s["label"] == "MID"))
    if a.drift is not None:
        mid["top_share_drift"] = a.drift
    spec = AgingSpec(engine=a.aging, top_share_drift=mid["top_share_drift"])
    gains = mid_gains_factors(base, alpha=mid["alpha"], spec=spec, ded_params=ded,
                              cal_tax_year=cal_year, years=TARGET_YEARS)
    rows = run_one_scenario(base, scenario=mid, target_years=TARGET_YEARS, cd="2",
                            ded_params=ded, cal_tax_year=cal_year, gains_factors=gains,
                            aging=a.aging)
    df = pd.DataFrame(rows)
    out = {
        "label": a.label, "aging": a.aging, "top_share_drift": mid["top_share_drift"],
        "cg_growth_model": cg_anchor.GAINS_GROWTH_MODEL,
        "gains_window": "none" if dotax_base.GAINS_WINDOW is None else "-".join(map(str, dotax_base.GAINS_WINDOW)),
        "static_5yr_$M": round(df["bracket_delta_static_$M"].sum(), 1),
        "post_5yr_$M": round(df["bracket_delta_post_$M"].sum(), 1),
        "credits_5yr_$M": round(df["credit_total_$M"].sum(), 1),
        "total_5yr_$M": round(df["total_impact_$M"].sum(), 1),
        "act46_baseline_2027_$M": round(float(df.loc[df.tax_year == 2027, "act46_baseline_$M"].iloc[0]), 1),
        **{f"total_{y}_$M": round(float(df.loc[df.tax_year == y, "total_impact_$M"].iloc[0]), 1) for y in TARGET_YEARS},
    }
    d = RUNS_DIR / "top_tail_ladder"
    d.mkdir(parents=True, exist_ok=True)
    path = d / "ladder.csv"
    table = pd.concat([pd.read_csv(path), pd.DataFrame([out])]) if path.exists() else pd.DataFrame([out])
    table.to_csv(path, index=False)
    print(pd.DataFrame([out]).T.to_string(header=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
