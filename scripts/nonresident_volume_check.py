"""How nonresidents' capital gains have tracked high-end property sales, and
what 2024-2026 sales say about the level (``tax_modeler.calibration.nonresident_cg``).

Usage:
  python scripts/nonresident_volume_check.py

Prints, by calendar year, Maui's priced, taxed sales (all and $1M+, from
scripts/conveyance/maui_sales_extract.py's cached county sales file; read
only) beside DOTAX's nonresident gains (Table 21, composites out) for
TY2018-2023, their ratio, and each year's $1M+ volume over 2023's: TY2023's
gains scaled by it are what the later years imply. Maui is the one county whose sale
prices are on file; the check is a sign of the direction, not a forecast.
The latest year is partial: the file is cumulative to its date, and deeds
reach it as they are recorded.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts" / "conveyance"))


def maui_volume() -> pd.DataFrame:
    spec = importlib.util.spec_from_file_location("maui_sales_extract",
                                                  REPO / "scripts" / "conveyance" / "maui_sales_extract.py")
    mx = importlib.util.module_from_spec(spec)
    sys.modules["maui_sales_extract"] = mx
    spec.loader.exec_module(mx)
    _raw, _units, _texts, rows, par = mx.inputs(False)
    s = mx.sales(mx.documents(rows), par, last_fy=mx.LAST_FY + 1)
    s["year"] = pd.to_datetime(s["date"]).dt.year
    return (s[s["year"] >= 2016].groupby("year")["price"]
            .agg(sales="size", volume_B=lambda x: x.sum() / 1e9, volume_1m_B=lambda x: x[x >= 1e6].sum() / 1e9))


def main() -> int:
    from tax_modeler.calibration import nonresident_cg

    v = maui_volume()
    v["nonres_gains_M"] = pd.Series({y: nonresident_cg.nonresident_gains_M(y) for y in nonresident_cg.YEARS})
    v["gains_per_1m_sale"] = v["nonres_gains_M"] / 1e3 / v["volume_1m_B"]
    v["volume_1m_vs_2023"] = v["volume_1m_B"] / v.loc[2023, "volume_1m_B"]
    v["implied_gains_M"] = v.loc[2023, "nonres_gains_M"] * v["volume_1m_vs_2023"]
    pd.set_option("display.width", 160)
    print(v.round(3).to_string())
    r = v.loc[2019:2023, "gains_per_1m_sale"]
    print(f"\nNonresident gains per $ of Maui $1M+ sales, TY2019-2023: {r.min():.3f}-{r.max():.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
