"""Download IRS SOI state data (Historic Table 2), TY2012-2023, and write the Hawaii and
US income-component totals ``calibration.hawaii_differentials`` estimates on.

Output: packages/tax_modeler/src/tax_modeler/data/calibration/soi_state_components_hi_us.json
(the JSON is committed, the CSVs are not). Rows STATE in {HI, US}, AGI_STUB = 0 (all returns);
amounts are $000 as published, converted to $M here.

URL names differ by year (checked 2026-10-11 on
https://www.irs.gov/statistics/soi-tax-stats-historic-table-2 and its 2010-2014 / 2015-2019
sub-pages): TY2012, 2014-2017 ``<yy>in54cmcsv.csv``; TY2013 only ``13in54cmcsv.xlsx``;
TY2018 ``18in55cmagi.csv``; TY2019-2023 ``<yy>in55cmcsv.csv``. The brief's TY2012-2018
``in55cmcsv`` names 404.

    uv run python scripts/fetch_soi_state_components.py
"""
from __future__ import annotations

import datetime as dt
import io
import json
import sys
import urllib.request
from pathlib import Path

import pandas as pd

BASE = "https://www.irs.gov/pub/irs-soi/"
FILES = {
    2012: "12in54cmcsv.csv", 2013: "13in54cmcsv.xlsx", 2014: "14in54cmcsv.csv",
    2015: "15in54cmcsv.csv", 2016: "16in54cmcsv.csv", 2017: "17in54cmcsv.csv",
    2018: "18in55cmagi.csv", 2019: "19in55cmcsv.csv", 2020: "20in55cmcsv.csv",
    2021: "21in55cmcsv.csv", 2022: "22in55cmcsv.csv", 2023: "23in55cmcsv.csv",
}
COLUMNS = {  # SOI column -> engine-facing label ($000 as published)
    "A00200": "wages", "A00300": "interest", "A00600": "dividends", "A00900": "business",
    "A01000": "net_capital_gain", "A01700": "pensions_annuities", "A02500": "taxable_social_security",
}
OUT = (Path(__file__).resolve().parents[1] / "packages" / "tax_modeler" / "src" / "tax_modeler"
       / "data" / "calibration" / "soi_state_components_hi_us.json")


def _download(name: str) -> bytes:
    req = urllib.request.Request(BASE + name, headers={"User-Agent": "Mozilla/5.0 (census-forecaster)"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def _parse(name: str, raw: bytes) -> pd.DataFrame:
    if name.endswith(".xlsx"):  # TY2013: header row sits below a title block
        df = pd.read_excel(io.BytesIO(raw), dtype=str, header=None)
        hdr = next(i for i in range(10) if str(df.iloc[i, 0]).strip().upper() == "STATE")
        df.columns = df.iloc[hdr]
        df = df.iloc[hdr + 1:]
    else:
        df = pd.read_csv(io.BytesIO(raw), dtype=str)
    df.columns = [str(c).strip().upper() for c in df.columns]
    df["STATE"] = df["STATE"].astype(str).str.strip()
    return df[pd.to_numeric(df["AGI_STUB"], errors="coerce") == 0]


def _pensions_ira(r) -> float:
    """Taxable IRA distributions + pensions and annuities ($000). TY2018's CSV prints only the
    combined A01750 (no A01400 / A01700); every other year A01400 + A01700 (checked: they
    agree to the dollar against the TY2018 state workbook's combined row)."""
    if "A01750" in r.index and "A01700" not in r.index:
        return _num(r["A01750"])
    return _num(r["A01400"]) + _num(r["A01700"])


def _num(v) -> float:
    return float(str(v).replace(",", "").strip())


def main() -> int:
    out = {"source": "IRS SOI Historic Table 2 (state data), all returns (AGI_STUB=0)",
           "units": "$ millions (published in $000)",
           "retrieved": dt.date.today().isoformat(), "urls": {}, "columns": COLUMNS, "years": {}}
    for ty, name in FILES.items():
        df = _parse(name, _download(name))
        out["urls"][str(ty)] = BASE + name
        row: dict[str, dict[str, float]] = {}
        for geo in ("HI", "US"):
            sub = df[df["STATE"] == geo]
            if len(sub) == 1:
                r = sub.iloc[0]
                row[geo] = {lab: _num(r[c]) / 1000.0 for c, lab in COLUMNS.items() if c in r.index}
                row[geo]["pensions_ira"] = _pensions_ira(r) / 1000.0
            elif geo == "US":  # no US row: sum the states
                st = df[~df["STATE"].isin(["US", "HI"]) | (df["STATE"] == "HI")]
                row[geo] = {lab: sum(_num(v) for v in st[c]) / 1000.0 for c, lab in COLUMNS.items() if c in st.columns}
            else:
                raise SystemExit(f"TY{ty}: no HI row in {name}")
        out["years"][str(ty)] = row
        print(f"TY{ty} {name}: HI wages ${row['HI']['wages']:,.0f}M  US wages ${row['US']['wages']:,.0f}M", file=sys.stderr)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1) + "\n")
    print(f"wrote {OUT}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
