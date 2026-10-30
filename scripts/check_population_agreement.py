"""Compare the poverty population with the revenue base on what both measure.

    python scripts/check_population_agreement.py            # print the summary
    python scripts/check_population_agreement.py --full     # every measure
    python scripts/check_population_agreement.py --csv out.csv

Needs the calibrated base (``chain/forecast_sb3125_enhanced.py --cd 2`` writes it)
and the poverty population, which it loads from the working-family step's cache or
builds (about 40 s) if that cache is stale. Changes nothing; see
``tax_modeler.agreement`` for what is compared and how to read the gaps.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "chain"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--full", action="store_true", help="print every measure, not the summary")
    ap.add_argument("--csv", type=Path, help="also write the full table here")
    args = ap.parse_args()

    import forecast_working_family_credits as wfc
    import pandas as pd

    units, _ = wfc.load_population()
    table = wfc.population_agreement(units)
    if table is None:
        return 1
    if args.full:
        with pd.option_context("display.width", 220, "display.max_rows", 200,
                               "display.float_format", "{:,.1f}".format):
            print(table.drop(columns="tail_band").to_string(index=False))
    if args.csv:
        table.to_csv(args.csv, index=False)
        print(f"wrote {args.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
