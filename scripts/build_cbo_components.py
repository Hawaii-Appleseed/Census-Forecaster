"""Build ``data/tax_modeler/cbo/cbo_components_<vintage>.csv`` from CBO's revenue projections.

Usage:
  python scripts/build_cbo_components.py REVENUE.xlsx ECONOMIC.xlsx YYYY-MM
  python scripts/build_cbo_components.py --capital-gains-only BASE YYYY-MM

The second form writes ``cbo_components_<BASE>-cg<YYYY-MM>.csv``: BASE's file
with its capital_gains rows replaced by those of the YYYY-MM file built by the
first form. The model aged on that mix (2025-01-cg2026-02) from November 24,
2026 until the other components were reviewed on November 25 and the full
2026-02 file, below, became the default.

REVENUE.xlsx is 51138-<YYYY-MM>-Revenue.xlsx and ECONOMIC.xlsx is
51135-<YYYY-MM>-Economic-Projections.xlsx, both from the same Outlook.

The file is CBO's "Revenue Projections, by Category" for a Budget and Economic
Outlook (www.cbo.gov/data/budget-economic-data; cbo.gov refuses scripted
downloads, so save it from a browser). Sheet 3, "Detailed Individual Income
Tax Projections", gives the income reported on tax returns by calendar year,
actual for 2022 and projected after: the same definitions the model ages, so
each component's growth is read from it directly.

  wages          Salaries and wages
  interest       Taxable interest and ordinary dividends (excluding qualified)
  dividends      Qualified dividends
  capital_gains  Capital gain or loss
  proprietors    nonfarm proprietors' income (Economic Projections, calendar year: the
                 national accounts' measure of what the businesses earn), joined to
                 the tax line in 2023, the first year of that table. CBO's "net business
                 income" on returns falls 6% in 2025 as 100% bonus depreciation
                 returns: a federal deduction Hawaiʻi does not take (it decouples
                 from IRC §168(k)), and not a fall in what owners earn
  business       the same (alias)
  retirement     per person: the prior year's CPI-U (Economic Projections, calendar
                 year), the way Social Security's cost-of-living adjustment and most
                 pensions are indexed. CBO's lines are national totals, which also
                 grow with the number of retirees: taxable pensions and IRA
                 distributions +37% from 2024 to 2027, taxable Social Security +43%
                 from 2022 to 2025 (more benefits become taxable as the unindexed
                 thresholds bite). Applied per person, those raised every retiree's
                 income and took 0.4 points off H.R.1's baseline poverty rate.
  other          Total income (CBO's own "all other sources" is a residual of
                 $25-80B that swings by half from year to year, too small and
                 noisy to age anything on)

Writes one row per component and year (2022 = 1.0) with the year's growth and
the CBO line it comes from. The workbook's SHA-256 goes in the README's
vintage table, not here: the CSV's schema is fixed by ``cbo_aging``.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import openpyxl
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
OUT_DIR = REPO / "data" / "tax_modeler" / "cbo"
SHEET_PREFIX = "3."
BASE_YEAR = 2022

# CPI-U annual averages before CBO's table starts (BLS, 1982-84=100).
CPI_U_HISTORY = {2021: 270.970, 2022: 292.655}

LINES = {   # component -> line labels summed (prefix match on CBO's label)
    "wages": ["Salaries and wages"],
    "interest": ["Taxable interest and ordinary dividends"],
    "dividends": ["Qualified dividends"],
    "capital_gains": ["Capital gain or loss"],
    "proprietors": ["Net business income"],
    "business": ["Net business income"],
    "retirement": ["Taxable pensions and annuities"],   # replaced by CPI-U below (per_person_retirement)
    "other": ["Total income"],
}


def read_sheet(path: Path) -> tuple[list[int], dict[str, list[float]]]:
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = next(w for w in wb if w.title.startswith(SHEET_PREFIX))
    rows = [[c for c in r if c is not None] for r in ws.iter_rows(values_only=True)]
    header = next(r for r in rows if r and str(r[0]).strip() == "Calendar year")
    years = [int(y) for y in header[1:]]
    lines = {}
    for r in rows:
        if len(r) == len(years) + 1 and isinstance(r[0], str) and all(isinstance(v, (int, float)) for v in r[1:]):
            lines[r[0].strip()] = [float(v) for v in r[1:]]
    return years, lines


def components(years: list[int], lines: dict[str, list[float]]) -> pd.DataFrame:
    out = []
    for comp, labels in LINES.items():
        level = None
        used = []
        for lab in labels:
            match = [lab] if lab in lines else [k for k in lines if k.startswith(lab)]
            if len(match) != 1:
                raise ValueError(f"{comp}: expected one line starting {lab!r}, found {match}")
            used.append(match[0])
            vals = lines[match[0]]
            level = vals if level is None else [a + b for a, b in zip(level, vals, strict=True)]
        base = level[years.index(BASE_YEAR)]
        for i, (y, v) in enumerate(zip(years, level, strict=True)):
            if y < BASE_YEAR:
                continue
            prev = level[i - 1] if i else None
            out.append({"component": comp, "year": y, "growth_factor_from_2022": round(v / base, 6),
                        "annual_cagr_pct": round(100 * (v / prev - 1), 3) if prev and y > BASE_YEAR else 0.0,
                        "source_note": " + ".join(used)})
    return pd.DataFrame(out)


def econ_line(path: Path, label: str) -> dict[int, float]:
    """One line of the Economic Projections' calendar-year sheet, by year."""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    rows = [[c for c in r if c is not None] for r in wb["2. Calendar Year"].iter_rows(values_only=True)]
    header = next(r for r in rows if len(r) > 2 and r[0] == "Units")
    years = [int(y) for y in header[1:]]
    line = next(r for r in rows if r and str(r[0]).startswith(label))
    return dict(zip(years, (float(v) for v in line[2:]), strict=False))


def cpi_u(path: Path) -> dict[int, float]:
    """CPI-U by calendar year: BLS history, then CBO's projection."""
    return {**CPI_U_HISTORY, **econ_line(path, "Consumer price index, all urban")}


def econ_business(df: pd.DataFrame, path: Path) -> pd.DataFrame:
    """Business income after 2023 grows with nonfarm proprietors' income."""
    nipa = econ_line(path, "Proprietors' income, nonfarm")
    out = df.copy()
    for comp in ("proprietors", "business"):
        m = out["component"] == comp
        f = out.loc[m].set_index("year")["growth_factor_from_2022"]
        join = f[2023]
        new = {y: (f[y] if y <= 2023 else round(join * nipa[y] / nipa[2023], 6)) for y in f.index}
        out.loc[m, "growth_factor_from_2022"] = out.loc[m, "year"].map(new).to_numpy()
        prev = out.loc[m, "growth_factor_from_2022"].shift()
        out.loc[m, "annual_cagr_pct"] = (100 * (out.loc[m, "growth_factor_from_2022"] / prev - 1)).fillna(0).round(3)
        out.loc[m, "source_note"] = "Net business income to 2023, then Proprietors' income, nonfarm (national accounts)"
    return out


def per_person_retirement(df: pd.DataFrame, cpi: dict[int, float]) -> pd.DataFrame:
    """Retirement income per person follows the prior year's CPI-U."""
    out = df.copy()
    m = out["component"] == "retirement"
    out.loc[m, "growth_factor_from_2022"] = [round(cpi[y - 1] / cpi[BASE_YEAR - 1], 6) for y in out.loc[m, "year"]]
    prev = out.loc[m, "growth_factor_from_2022"].shift()
    out.loc[m, "annual_cagr_pct"] = (100 * (out.loc[m, "growth_factor_from_2022"] / prev - 1)).fillna(0).round(3)
    out.loc[m, "source_note"] = "CPI-U, prior year (Social Security COLA and most pensions; per person)"
    return out


def capital_gains_only(base: str, vintage: str) -> Path:
    old = pd.read_csv(OUT_DIR / f"cbo_components_{base}.csv")
    new = pd.read_csv(OUT_DIR / f"cbo_components_{vintage}.csv")
    mix = pd.concat([old[old["component"] != "capital_gains"], new[new["component"] == "capital_gains"]])
    dest = OUT_DIR / f"cbo_components_{base}-cg{vintage}.csv"
    mix.sort_values(["component", "year"]).to_csv(dest, index=False)
    print(f"wrote {dest.relative_to(REPO)}: {base} with capital gains from {vintage}")
    return dest


def main(argv: list[str]) -> int:
    if len(argv) == 4 and argv[1] == "--capital-gains-only":
        capital_gains_only(argv[2], argv[3])
        return 0
    if len(argv) != 4:
        print(__doc__)
        return 2
    rev, econ, vintage = Path(argv[1]), Path(argv[2]), argv[3]
    years, lines = read_sheet(rev)
    df = econ_business(per_person_retirement(components(years, lines), cpi_u(econ)), econ)
    dest = OUT_DIR / f"cbo_components_{vintage}.csv"
    df.to_csv(dest, index=False)
    for path in (rev, econ):
        print(f"source {path.name} sha256 {hashlib.sha256(path.read_bytes()).hexdigest()}")
    print(f"wrote {dest.relative_to(REPO)} ({len(df)} rows, {BASE_YEAR}-{years[-1]})")
    print(df.pivot(index="year", columns="component", values="growth_factor_from_2022").round(3).to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
