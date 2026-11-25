"""Exploratory: per-recipient candidate for the CBO 2026-02 component growth.

Usage: python scripts/build_cbo_per_person.py CENSUS_np2023_d1_mid.csv
Writes data/tax_modeler/cbo/cbo_components_2026-02-per-person.csv (NOT a default)
and data/tax_modeler/cbo/denominators_2026-02.csv. See docs/data_source_tests/cbo-per-person-denominators.md.
"""
from __future__ import annotations

import sys
from pathlib import Path

import openpyxl
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
CBO = REPO / "data" / "tax_modeler" / "cbo"
DL = REPO / "runs" / "cbo_downloads"
YEARS = list(range(2022, 2031))


def sheet3():
    wb = openpyxl.load_workbook(DL / "51138-2026-02-Revenue.xlsx", read_only=True, data_only=True)
    ws = next(w for w in wb if w.title.startswith("3."))
    rows = [[c for c in r if c is not None] for r in ws.iter_rows(values_only=True)]
    years = next(r for r in rows if r and r[0] == "Calendar year")[1:]
    out = {}
    for r in rows:
        if r and isinstance(r[0], str) and len(r) == len(years) + 1:
            out[r[0].strip()] = dict(zip(map(int, years), map(float, r[1:])))
    return out


def econ_levels():
    """Levels incl. 2022, backed out of CBO's own annual % change (2023 is the first column)."""
    wb = openpyxl.load_workbook(DL / "51135-2026-02-Economic-Projections.xlsx", read_only=True, data_only=True)
    rows = list(wb["2. Calendar Year"].iter_rows(values_only=True))
    hdr = next(r for r in rows if len(r) > 2 and r[1] == "Units")
    yrs = [int(y) for y in hdr[2:] if y not in (None, "")]

    def get(i):  # row i = level, i+1 = pct change
        lv = dict(zip(yrs, (float(v) for v in rows[i][2:] if v not in (None, ""))))
        lv[2022] = lv[2023] / (1 + float(rows[i + 1][2]) / 100)
        return lv
    lab = {str(r[0]): i for i, r in enumerate(rows) if r[0]}
    return {
        "nonfarm_employment_M": get(lab["Employment, total nonfarm (establishment survey)"]),
        "civ_pop_16plus_M": get(73),            # Census Through 2020 Plus CBO Projection
        "household_employment_M": get(89),
    }


def census_pop(path):
    d = pd.read_csv(path)
    d = d[(d.SEX == 0) & (d.ORIGIN == 0) & (d.RACE == 0)].set_index("YEAR")
    cols = lambda a, b: [f"POP_{i}" for i in range(a, b + 1)]  # noqa: E731
    s65 = d[cols(65, 100)].sum(axis=1) if "POP_100" in d else d[[c for c in d.columns if c.startswith("POP_") and int(c[4:]) >= 65]].sum(axis=1)
    s1864 = d[cols(18, 64)].sum(axis=1)
    return {"pop65plus_M": (s65 / 1e6).to_dict(), "pop18_64_M": (s1864 / 1e6).to_dict()}


def main(census):
    s3 = sheet3()
    raw = pd.read_csv(CBO / "cbo_components_2026-02.csv")
    live = pd.read_csv(CBO / "cbo_components_2025-01-cg2026-02.csv")
    den = {**econ_levels(), **census_pop(census)}
    den["returns_M"] = s3["Number of returns (millions)l"]
    den["taxable_pensions_SS_B"] = {y: s3["Taxable pensions and annuities and IRA distributions"][y] + s3["Taxable Social Security benefits"][y] for y in s3["Total income"]}
    den["taxable_SS_B"] = s3["Taxable Social Security benefits"]
    dd = pd.DataFrame({k: {y: v[y] for y in YEARS} for k, v in den.items()})
    dd.index.name = "year"
    dd.round(4).to_csv(CBO / "denominators_2026-02.csv")
    idx = dd / dd.loc[2022]

    def g(comp, df):
        x = df[df.component == comp].set_index("year").growth_factor_from_2022
        return x
    ratio = lambda num, d: (num / num.loc[2022]) / idx[d]  # noqa: E731
    tot = lambda k: pd.Series({y: s3[k][y] for y in YEARS})  # noqa: E731
    cand = {
        "wages": ratio(tot("Salaries and wages"), "nonfarm_employment_M"),
        "interest": ratio(tot("Taxable interest and ordinary dividends (excludes qualified dividends)"), "returns_M"),
        "dividends": ratio(tot("Qualified dividends"), "returns_M"),
        "capital_gains": g("capital_gains", raw).loc[YEARS],           # kept national
        "proprietors": g("proprietors", raw).loc[YEARS] / idx["returns_M"],
        "retirement": ratio(pd.Series(den["taxable_pensions_SS_B"]).loc[YEARS], "pop65plus_M"),
        "other": ratio(tot("Total income"), "returns_M"),
    }
    cand["business"] = cand["proprietors"]
    notes = {
        "wages": "CBO salaries and wages / total nonfarm employment (per worker)",
        "interest": "CBO taxable interest+ordinary dividends / CBO number of returns (per return)",
        "dividends": "CBO qualified dividends / CBO number of returns (per return)",
        "capital_gains": "CBO capital gain or loss, national (unchanged)",
        "proprietors": "2026-02 proprietors' growth / CBO number of returns (per return)",
        "business": "alias of proprietors",
        "retirement": "CBO taxable pensions+IRA+Social Security / Census 2023 projections population 65+ (per older person)",
        "other": "CBO total income / CBO number of returns (per return)",
    }
    rows = []
    for c, s in cand.items():
        for y in YEARS:
            prev = s.loc[y - 1] if y > 2022 else None
            rows.append({"component": c, "year": y, "growth_factor_from_2022": round(float(s.loc[y]), 6),
                         "annual_cagr_pct": round(100 * (float(s.loc[y]) / float(prev) - 1), 3) if prev is not None else 0.0,
                         "source_note": notes[c]})
    out = pd.DataFrame(rows)
    out.to_csv(CBO / "cbo_components_2026-02-per-person.csv", index=False)
    # comparison table
    cmp_rows = []
    for c in cand:
        for y in (2025, 2027, 2030):
            cmp_rows.append({"component": c, "year": y,
                             "live_2025-01-cg": round(float(g(c, live).loc[y]), 3),
                             "raw_2026-02": round(float(g(c, raw).loc[y]), 3),
                             "per_person": round(float(cand[c].loc[y]), 3)})
    cmp = pd.DataFrame(cmp_rows)
    cmp.to_csv(CBO / "per_person_comparison_2026-02.csv", index=False)
    print(idx.round(3).to_string())
    print(cmp.to_string())


if __name__ == "__main__":
    main(sys.argv[1])
