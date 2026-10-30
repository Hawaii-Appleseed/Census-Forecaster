"""SB 3028 HD2 next to HB 2049 HD3's rates, year by year, on the conveyance
model (analyses/forecast_conveyance_sb3028.py), for the conveyance page's comparison.

Usage:
  python scripts/conveyance/bill_comparison.py

SB 3028 HD1 left every rate and fund share blank; HB 2049 HD3 (passed by the
House on March 10, 2026) carries the House's rates in HD2's marginal form,
steeper from $600,000. Each bill is scored the way the page scores HD2: Maui
sale by sale, the other counties from their homes, static and with the
central behavioral response, for the model's target years. Current law is the
model's (DOTAX collections grown with Maui's current-law tax). Collections
are split among the funds by HD2's allocation (5% / $10M land conservation,
20% / $40M rental housing, 30% / $60M Hawaiian home lands, 20% / $40M
transit-oriented infrastructure, the rest to the general fund) for both
bills, so the comparison isolates the rates.

Writes runs/conveyance_sb3028_bills/: bill_comparison.csv (fy, bill, case,
state_now_$M, state_change_$M, total_$M, one column per fund) and
manifest.json.
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "analyses"))

import forecast_conveyance_sb3028 as f  # noqa: E402

OUT = REPO / "runs" / "conveyance_sb3028_bills"
COMPARED = ("SB3028 HD2", "HB2049 HD3")


def main() -> int:
    warnings.filterwarnings("ignore")
    sales = f.load_sales()
    rows = f.calibrated_rows()
    out = []
    for fy in f.TARGET_YEARS:
        now = f.state_current_law(sales, fy)
        for bill in COMPARED:
            for case, bh in f.CASES.items():
                comp = f.components(sales, fy, bh, bill)
                sm = f.stock_method(fy, bh, bill=bill, rows=rows)
                change = float(f.by_county_change(comp, sm).sum())
                funds = f.disposition(now + change, f.DISPOSITION_HD2)
                out.append({"fy": fy, "bill": bill, "case": case, "state_now_$M": now,
                            "state_change_$M": change, "total_$M": now + change, **funds})
    OUT.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(out)
    df.to_csv(OUT / "bill_comparison.csv", index=False)

    from tax_modeler.runs import write_run_manifest
    write_run_manifest(OUT, script="scripts/conveyance/bill_comparison.py",
                       params={"bills": list(COMPARED), "years": list(f.TARGET_YEARS),
                               "cases": list(f.CASES), "disposition": "SB3028 HD2"},
                       outputs=["bill_comparison.csv"])
    piv = df.pivot_table(index="fy", columns=["bill", "case"], values="state_change_$M")
    print("Change from current law, $M\n" + piv.round(1).to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
