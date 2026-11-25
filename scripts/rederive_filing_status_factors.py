#!/usr/bin/env python3
"""Re-derive the filing-status weight factors in ``units.constructor.FILING_STATUS_WEIGHT_FACTORS``.

The factors were set (commit 661ca38, 2026-06) so the units' filing-status mix on the 2024 1-year PUMS matches
the DOTAX TY2022 shares (``scripts/diagnose_filing_status.py``: "post-fix mix now matches DOTAX within 0.1pp").
The recipe is a ratio, with MFJ held at 1.0:

    factor[s] = (DOTAX[s] / DOTAX[MFJ]) / (natural[s] / natural[MFJ])

where ``natural`` is the weighted count of ALL units of the status (non-filing units included, as the diagnosis
counts them) with every factor set to 1.0. This script builds the units under a given claimant assignment with
neutral factors and prints the factors the recipe gives, plus the shares the CURRENT factors give, so the recipe can
be checked against the committed values on the construction they were set on.

    uv run python scripts/rederive_filing_status_factors.py [--assignment legacy|parent_first] [--parent-link sfn]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(REPO / "scripts")]

from diagnose_filing_status import DOTAX_FS_COUNTS, STATUSES, _load_pums  # noqa: E402

PUMS_1YR = REPO / "packages" / "data" / "raw" / "pums_2024_1yr"


def natural_counts(persons: pd.DataFrame, households: pd.DataFrame, assignment: str, link: str):
    """Weighted units by filing status with neutral factors, and with the committed factors."""
    from tax_modeler.base import build_raw_units
    from tax_modeler.units import constructor

    committed = dict(constructor.FILING_STATUS_WEIGHT_FACTORS)
    constructor.FILING_STATUS_WEIGHT_FACTORS.update({k: 1.0 for k in committed})
    try:
        u = build_raw_units(persons.copy(), households.copy(), claimant_assignment=assignment, parent_link=link)
    finally:
        constructor.FILING_STATUS_WEIGHT_FACTORS.update(committed)
    w = u["weight"].to_numpy(float)
    fs = u["filing_status"].astype(str).to_numpy()
    nat = {s: float(w[fs == s].sum()) for s in STATUSES}
    return nat, committed


def derive(nat: dict[str, float]) -> dict[str, float]:
    m = "married_filing_jointly"
    return {s: (DOTAX_FS_COUNTS[s] / DOTAX_FS_COUNTS[m]) / (nat[s] / nat[m]) for s in STATUSES}


def shares(counts: dict[str, float]) -> dict[str, float]:
    t = sum(counts.values())
    return {s: counts[s] / t for s in STATUSES}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--assignment", default="parent_first", choices=["legacy", "parent_first"])
    ap.add_argument("--parent-link", default="sfn")
    ap.add_argument("--pums-dir", type=Path, default=PUMS_1YR)
    ap.add_argument("--json", type=Path, help="write the result here")
    args = ap.parse_args()

    persons, households = _load_pums("pums", args.pums_dir)
    from tax_modeler.loaders.pums_loader import PUMSDataLoader
    loader = PUMSDataLoader(data_dir=args.pums_dir)
    persons, households = loader._coalesce_puma(persons), loader._coalesce_puma(households)
    nat, committed = natural_counts(persons, households, args.assignment, args.parent_link)
    new = derive(nat)
    cur_counts = {s: nat[s] * committed[s] for s in STATUSES}
    dot = shares(DOTAX_FS_COUNTS)
    print(f"assignment={args.assignment} parent_link={args.parent_link}")
    print(f"{'status':<26}{'natural n':>12}{'natural %':>11}{'DOTAX %':>9}{'committed f':>13}{'share w/ committed':>20}"
          f"{'re-derived f':>14}")
    ns, cs = shares(nat), shares(cur_counts)
    for s in STATUSES:
        print(f"{s:<26}{nat[s]:>12,.0f}{ns[s]:>10.1%}{dot[s]:>9.1%}{committed[s]:>13.2f}{cs[s]:>19.1%}{new[s]:>14.3f}")
    if args.json:
        args.json.write_text(json.dumps({"assignment": args.assignment, "parent_link": args.parent_link,
                                         "natural": nat, "committed": committed, "rederived": new}, indent=1))


if __name__ == "__main__":
    np.seterr(all="ignore")
    main()
