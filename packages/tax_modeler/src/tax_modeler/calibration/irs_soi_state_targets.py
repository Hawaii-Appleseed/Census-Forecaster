"""IRS SOI Table 2 (state-level) calibration targets for Hawaii.

The IRS Statistics of Income (SOI) Bulletin publishes Hawaii-state aggregates
by AGI class (TY 2022 latest published). Source CSV in this repo is the
ZIP-code SOI extract — state totals are recovered by summing ZIP=0 rows.

DOTAX Table A8 anchors **state tax** (HI Tax Liability) per AGI bin. SOI
adds complementary anchors that DOTAX doesn't publish:

    - Total federal AGI per bin (anchors income magnitudes)
    - Salaries/wages, capital gains, qualified dividends per bin
    - Total itemized deductions per bin
    - Federal income tax before credits per bin

We use these as auxiliary IPF margins with a coarser bin layout
(6 bins vs DOTAX's 15) and reconcile them by aggregating DOTAX targets
to SOI bin boundaries when both are active in the rake.

**Year mismatch (known, deliberate, not adjusted).** These margins are
**TY2022** (``SOI_TAX_YEAR``), the newest state-level SOI on file, while the
DOTAX count, filing-status and tax margins they are raked jointly with are
**TY2023** (``dotax_base.BASE_YEAR``). The SOI AGI totals are used as
published — no growth factor bridges the year — so the rake asks TY2023
counts to carry TY2022 AGI. ``calibrate_via_rake`` records each margin's
source year in its ``CalibrationReport`` and warns once per process when
they differ. Replace ``hawaii_irs_soi_processed.csv`` with the TY2023 extract
and bump ``SOI_TAX_YEAR`` to close the gap.
"""

from __future__ import annotations

import pandas as pd
import numpy as np
from pathlib import Path
from typing import Dict, List, Tuple, Optional
import logging

logger = logging.getLogger(__name__)

# Tax year of the SOI extract on file (see the module docstring on the
# mismatch with DOTAX's TY2023 margins).
SOI_TAX_YEAR = 2022

# IRS SOI Table 2 AGI bins (Hawaii, TY 2022)
SOI_AGI_BINS: List[Tuple[float, float]] = [
    (0,        25_000),
    (25_000,   50_000),
    (50_000,   75_000),
    (75_000,   100_000),
    (100_000,  200_000),
    (200_000,  float("inf")),
]

# SOI bin label as it appears in the source CSV
SOI_BIN_LABELS: Dict[Tuple[float, float], str] = {
    (0,        25_000):       "$1 under $25,000",
    (25_000,   50_000):       "$25,000 under $50,000",
    (50_000,   75_000):       "$50,000 under $75,000",
    (75_000,   100_000):      "$75,000 under $100,000",
    (100_000,  200_000):      "$100,000 under $200,000",
    (200_000,  float("inf")): "$200,000 or more",
}

# Column indexes in hawaii_irs_soi_processed.csv (header at row 3, totals at rows 6-12).
# Most columns appear as (count_with_item, dollar_total) pairs — we want the
# dollar columns. Source money amounts are $thousands.
SOI_COLUMNS: Dict[str, int] = {
    "n_returns":             2,   # Number of returns
    "n_single":              3,   # Number of single returns
    "n_joint":               4,   # Number of joint returns
    "n_hoh":                 5,   # Number of head of household returns
    "agi_thousands":        18,   # Adjusted gross income (AGI) — dollar total
    "wages_thousands":      22,   # Salaries and wages in AGI — dollar total
    "qualified_div_thousands": 30,  # Qualified dividends — dollar total
    "capgain_thousands":    36,   # Net capital gain (less loss) in AGI — dollar total
    "itemized_thousands":   70,   # Total itemized deductions — dollar total
    "fed_tax_thousands":   102,   # Income tax before credits (federal) — dollar total
    "fed_tax_after_thousands": 148,  # Income tax after credits (federal) — dollar total
    "taxable_income_thousands": 100,  # Federal taxable income — dollar total
}


def load_soi_state_targets(
    csv_path: Optional[Path] = None,
) -> pd.DataFrame:
    """Load Hawaii state-level SOI targets by AGI bin.

    Returns a DataFrame indexed by ``(agi_lo, agi_hi)`` tuple with columns:
        n_returns, n_single, n_joint, n_hoh,
        agi_M, wages_M, capgain_M, qualified_div_M,
        itemized_M, fed_tax_M
    All money columns in $millions (converted from $thousands in source CSV).
    """
    if csv_path is None:
        # Default: data dir relative to repo root
        csv_path = (
            Path(__file__).resolve().parents[5]
            / "data" / "tax_modeler" / "irs_soi"
            / "hawaii_irs_soi_processed.csv"
        )
    if not csv_path.exists():
        raise FileNotFoundError(f"SOI processed CSV not found: {csv_path}")

    raw = pd.read_csv(csv_path, low_memory=False, header=None)

    # State-level totals are rows where ZIP code = 0 (rows 6-12).
    # Row 6 is "Total" across all bins; rows 7-12 are per-bin.
    state = raw.iloc[7:13].copy()
    state.columns = [f"c{i}" for i in range(raw.shape[1])]

    # Map bin label → row.
    bin_label_col = state["c1"].astype(str).str.strip()
    rows: Dict[Tuple[float, float], pd.Series] = {}
    for (lo, hi), label in SOI_BIN_LABELS.items():
        match = state[bin_label_col == label]
        if match.empty:
            raise ValueError(f"SOI bin '{label}' not found in state-totals rows")
        rows[(lo, hi)] = match.iloc[0]

    records = []
    for (lo, hi), row in rows.items():
        rec = {"agi_lo": lo, "agi_hi": hi}
        for name, idx in SOI_COLUMNS.items():
            val = row[f"c{idx}"]
            try:
                v = float(val)
            except (TypeError, ValueError):
                v = float("nan")
            # Convert $thousands → $millions
            if name.endswith("_thousands"):
                v = v / 1000.0
                key = name.replace("_thousands", "_M")
            else:
                key = name
            rec[key] = v
        records.append(rec)

    df = pd.DataFrame.from_records(records).set_index(["agi_lo", "agi_hi"])
    return df


def build_soi_calibration_margins(
    soi_df: pd.DataFrame,
) -> Dict[str, Dict[Tuple[float, float], float]]:
    """Return SOI-derived calibration margins keyed by margin name.

    Each margin is a dict ``{(agi_lo, agi_hi): target_value}``:
      - ``soi_n_returns``: filer counts
      - ``soi_agi_M``:     total AGI ($M)
      - ``soi_fed_tax_M``: federal income tax before credits ($M)
      - ``soi_itemized_M``: total itemized deductions ($M)
    """
    margins: Dict[str, Dict[Tuple[float, float], float]] = {
        "soi_n_returns":   {},
        "soi_agi_M":       {},
        "soi_fed_tax_M":   {},
        "soi_itemized_M":  {},
    }
    for (lo, hi), row in soi_df.iterrows():
        margins["soi_n_returns"][(lo, hi)]   = float(row["n_returns"])
        margins["soi_agi_M"][(lo, hi)]       = float(row["agi_M"])
        margins["soi_fed_tax_M"][(lo, hi)]   = float(row["fed_tax_M"])
        margins["soi_itemized_M"][(lo, hi)]  = float(row["itemized_M"])
    return margins


def soi_bin_for_agi(agi: float) -> Tuple[float, float]:
    """Return the (lo, hi) SOI bin tuple for a given AGI."""
    for (lo, hi) in SOI_AGI_BINS:
        if lo <= agi < hi:
            return (lo, hi)
    return SOI_AGI_BINS[-1]
