"""Parse DOTAX's "Tax Credits Claimed by Hawaiʻi Taxpayers", Tables A-1 and A-2, for two credits.

The working-family step (``chain/forecast_working_family_credits.py``) fits its food and excise
take-up to the Department of Taxation's dollars and checks the earned income credit and the
food and excise credit against its dollars and claim counts. Those figures used to be typed
into the script. This reads them from the Department's workbook for each tax year:

    Table A-1   dollar amounts of tax credits claimed, by type of credit and taxpayer ($1,000)
    Table A-2   number of tax credits claimed, by type of credit and taxpayer

Workbooks are at (newest year outside ``archive/`` until the next edition is published):

    https://files.hawaii.gov/tax/stats/stats/credits/{YEAR}credit_tables.xlsx
    https://files.hawaii.gov/tax/stats/stats/credits/archive/{YEAR}credit_tables.xlsx

Usage:
    .venv/bin/python scripts/parse_dotax_credits_claimed.py --download 2022 2023
    .venv/bin/python scripts/parse_dotax_credits_claimed.py 2022=PATH/2022credit_tables.xlsx 2023=PATH/...

Writes packages/tax_modeler/src/tax_modeler/data/calibration/dotax_credits_claimed.json with each
workbook's SHA-256. A year is refused unless both tables have the credit's row once, the dollar
and count cells are numbers (not suppressed or "na"), and the ALL and Individuals columns agree,
as they must for credits only individuals can claim.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "packages" / "tax_modeler" / "src" / "tax_modeler" / "data" / "calibration" / "dotax_credits_claimed.json"
URLS = ("https://files.hawaii.gov/tax/stats/stats/credits/{year}credit_tables.xlsx",
        "https://files.hawaii.gov/tax/stats/stats/credits/archive/{year}credit_tables.xlsx")

#: key -> the row label in the workbook, with the table's own wording.
CREDITS = {
    "eitc": "Earned Income Tax Credit",
    "food_excise": "Refundable Food/Excise Tax Credit",
}


def _label(cell) -> str:
    return " ".join(str(cell or "").split())


def _number(cell, where: str) -> float:
    try:
        return float(cell)
    except (TypeError, ValueError):
        raise ValueError(f"{where}: expected a number, found {cell!r} (suppressed or not available)") from None


def _table(wb, sheet: str, title: str, path: Path) -> dict[str, float]:
    ws = wb[sheet]
    rows = list(ws.iter_rows(values_only=True))
    head = " ".join(_label(r[0]) for r in rows[:4]).upper()
    if title not in head:
        raise ValueError(f"{path.name} {sheet}: title does not say {title!r} ({head!r})")
    header = next((r for r in rows[:8] if _label(r[0]) == "Type of Credit"), None)
    if header is None:
        raise ValueError(f"{path.name} {sheet}: no 'Type of Credit' header row")
    cols = [_label(c) for c in header]
    try:
        i_all, i_ind = cols.index("ALL"), cols.index("Individuals")
    except ValueError:
        raise ValueError(f"{path.name} {sheet}: no ALL and Individuals columns in {cols}") from None
    out: dict[str, float] = {}
    for key, label in CREDITS.items():
        hits = [r for r in rows if _label(r[0]).startswith(label)]
        if len(hits) != 1:
            raise ValueError(f"{path.name} {sheet}: {len(hits)} rows start {label!r}, expected 1")
        every = _number(hits[0][i_all], f"{path.name} {sheet} {label} ALL")
        indiv = _number(hits[0][i_ind], f"{path.name} {sheet} {label} Individuals")
        if abs(every - indiv) > 1e-6:
            raise ValueError(f"{path.name} {sheet} {label}: ALL {every} != Individuals {indiv}")
        out[key] = every
    return out


def parse_workbook(path: Path) -> dict[str, dict[str, float]]:
    """``{credit: {"dollars_k": ..., "claims": ...}}`` from one year's workbook."""
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    dollars = _table(wb, "A-1", "DOLLAR AMOUNTS OF TAX CREDITS CLAIMED", path)
    counts = _table(wb, "A-2", "NUMBER OF TAX CREDITS CLAIMED", path)
    return {k: {"dollars_k": dollars[k], "claims": counts[k]} for k in CREDITS}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _download(year: int, tmp: Path) -> tuple[Path, str]:
    for pattern in URLS:
        url = pattern.format(year=year)
        dest = tmp / f"{year}credit_tables.xlsx"
        try:
            urllib.request.urlretrieve(url, dest)  # noqa: S310 - fixed files.hawaii.gov URLs
            return dest, url
        except urllib.error.HTTPError:
            continue
    raise SystemExit(f"no workbook for tax year {year} at either location")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("editions", nargs="*", help="YEAR=PATH to a downloaded workbook")
    ap.add_argument("--download", nargs="+", type=int, metavar="YEAR")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args(argv)

    tmp = Path(tempfile.mkdtemp(prefix="dotax_credits_"))
    files: dict[int, tuple[Path, str | None]] = {}
    for year in args.download or []:
        files[year] = _download(year, tmp)
    for spec in args.editions:
        year, _, path = spec.partition("=")
        files[int(year)] = (Path(path).expanduser(), None)      # a local copy: where it came from is not known
    if not files:
        ap.error("give YEAR=PATH workbooks or --download YEAR ...")

    years, sources = {}, {}
    for year in sorted(files):
        path, url = files[year]
        years[str(year)] = parse_workbook(path)
        sources[str(year)] = {"file": path.name, "sha256": sha256(path), **({"url": url} if url else {})}
    doc = {
        "source": {
            "title": "Tax Credits Claimed by Hawaiʻi Taxpayers, Tables A-1 and A-2",
            "publisher": "Hawaiʻi Department of Taxation, Office of Tax Research & Planning",
            "files": sources,
            "note": "Dollars are the printed Table A-1 cells, in thousands of dollars; claims are Table A-2. "
                    "Both credits are claimed by individuals only, so the ALL and Individuals columns are equal.",
        },
        "credits": CREDITS,
        "years": years,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
    for y, d in years.items():
        print(f"TY{y}: " + "; ".join(f"{k} ${v['dollars_k'] / 1000:,.3f}M on {v['claims']:,.0f} claims" for k, v in d.items()))
    print(f"wrote {args.out.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
