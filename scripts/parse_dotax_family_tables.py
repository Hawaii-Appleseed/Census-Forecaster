"""Parse DOTAX "Individual Income Tax Statistics" tables A-2, A-5 and A-6 (resident returns, by Hawaiʻi AGI class).

The Department of Taxation's yearly workbook

    https://files.hawaii.gov/tax/stats/stats/indinc/{YEAR}indinc_tables.xlsx

carries, for resident returns by Hawaiʻi AGI class:

    A-2 (sheet "A2-1")  returns by filing status: Joint, Single (includes married filing separately), H/H
                        (head of household and qualifying widow(er)); a taxable block (11 classes) and a
                        nontaxable block (Loss, $0-5,000, $5,000-10,000, $10,000 and over)
    A-5 (sheet "A5")    exemptions claimed: returns, regular (self, spouse on a joint return, qualified dependents),
                        dependents (the dependent exemptions within them), age, total; same two blocks
    A-6 (sheet "A6")    returns by number of regular exemptions (one to six or more) and age exemptions, by class

This reads them from a local copy: pass ``YEAR=PATH``. (files.hawaii.gov answers curl's own user agent; it refuses
Python's urllib default, and this script does not pretend to be a browser, so fetch the file with curl first.)

    curl -O https://files.hawaii.gov/tax/stats/stats/indinc/2023indinc_tables.xlsx
    .venv/bin/python scripts/parse_dotax_family_tables.py 2023=2023indinc_tables.xlsx

Writes packages/tax_modeler/src/tax_modeler/data/calibration/dotax_family_<year>.json with the workbook's SHA-256.
Each table is checked against its printed totals (classes add to the TOTAL rows, A-2's three statuses add to its
all-returns total, A-6's class returns equal A-5's and the Department's Table A-8 returns for the same class).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT_DIR = REPO / "packages" / "tax_modeler" / "src" / "tax_modeler" / "data" / "calibration"
URL = "https://files.hawaii.gov/tax/stats/stats/indinc/{year}indinc_tables.xlsx"


def _num(c):
    return None if c is None or c == "" else float(c)


def _class_of(row) -> tuple[str, float | None, float | None] | None:
    """(label, lo, hi) for a class row, ``None`` for anything else. ``Loss`` has no bounds (negative AGI)."""
    a, b, c = (row[0], row[1], row[2]) if len(row) > 2 else (None, None, None)
    if isinstance(b, str) and b.strip() == "Loss":
        return "Loss", None, 0.0
    if isinstance(b, str) and b.strip() == "to under" and a is not None and c is not None:
        return f"{int(a)} to under {int(c)}", float(a), float(c)
    if isinstance(b, str) and b.strip() in ("and", "to under") and isinstance(c, str) and c.strip() == "over" and a is not None:
        return f"{int(a)} and over", float(a), None
    return None


def _blocks(rows, start: int, columns: dict[str, int]) -> dict:
    """Walk the rows of one sheet: the taxable block, the nontaxable block and the printed totals."""
    out = {"taxable": [], "nontaxable": [], "totals": {}}
    block = None
    for r in rows[start:]:
        head = " ".join(str(c) for c in r[:3] if c is not None).strip().upper()
        if head.startswith("TAXABLE") and "RETURN" in head and "TOTAL" not in head:
            block = "taxable"
            continue
        if head.startswith("NONTAXABLE") and "TOTAL" not in head:
            block = "nontaxable"
            continue
        if head.startswith("TOTAL RESIDENT TAXABLE"):
            out["totals"]["taxable"] = {k: _num(r[i]) for k, i in columns.items()}
            continue
        if head.startswith("TOTAL RESIDENT NONTAXA"):
            out["totals"]["nontaxable"] = {k: _num(r[i]) for k, i in columns.items()}
            continue
        if head.startswith("TOTAL - ALL RESIDENT"):
            out["totals"]["resident"] = {k: _num(r[i]) for k, i in columns.items()}
            continue
        cls = _class_of(r)
        if cls is not None and block is not None:
            label, lo, hi = cls
            out[block].append({"class": label, "lo": lo, "hi": hi, **{k: _num(r[i]) for k, i in columns.items()}})
    return out


def _check_sum(rows: list[dict], totals: dict, keys, where: str, tol: float = 5.0) -> None:
    for k in keys:
        s = sum(r[k] for r in rows)
        if abs(s - totals[k]) > tol:
            raise ValueError(f"{where}: {k} classes add to {s:,.0f}, printed total {totals[k]:,.0f}")


def parse_workbook(path: Path) -> dict:
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    sheets = {n: list(wb[n].iter_rows(values_only=True)) for n in ("A2-1", "A5", "A6")}
    a2 = _blocks(sheets["A2-1"], 6, {"joint": 4, "single": 8, "hoh": 12})
    a5 = _blocks(sheets["A5"], 6, {"returns": 4, "regular": 6, "dependents": 8, "age": 10, "total": 12})
    for name, t in (("A-2", a2), ("A-5", a5)):
        if not t["taxable"] or not t["nontaxable"] or set(t["totals"]) != {"taxable", "nontaxable", "resident"}:
            raise ValueError(f"{path.name} {name}: did not find both blocks and their totals")
    _check_sum(a2["taxable"], a2["totals"]["taxable"], ("joint", "single", "hoh"), "A-2 taxable")
    _check_sum(a2["nontaxable"], a2["totals"]["nontaxable"], ("joint", "single", "hoh"), "A-2 nontaxable")
    for k in ("joint", "single", "hoh"):
        if abs(a2["totals"]["taxable"][k] + a2["totals"]["nontaxable"][k] - a2["totals"]["resident"][k]) > 5:
            raise ValueError(f"A-2: taxable plus nontaxable {k} is not the printed resident total")
    _check_sum(a5["taxable"], a5["totals"]["taxable"], ("returns", "regular", "dependents", "age", "total"), "A-5 taxable")
    _check_sum(a5["nontaxable"], a5["totals"]["nontaxable"], ("returns", "regular", "dependents", "age", "total"), "A-5 nontaxable")

    # A-6: one block, resident classes (Loss included) then totals
    cols6 = {"returns": 4, "one": 6, "two": 8, "three": 10, "four": 12, "five": 14, "six_plus": 16, "age_one": 18, "age_two": 20}
    classes6, total6 = [], None
    for r in sheets["A6"][6:]:
        head = " ".join(str(c) for c in r[:3] if c is not None).strip().upper()
        if head.startswith("TOTAL - ALL RESIDENT"):
            total6 = {k: _num(r[i]) for k, i in cols6.items()}
            break
        cls = _class_of(r)
        if cls is not None:
            classes6.append({"class": cls[0], "lo": cls[1], "hi": cls[2], **{k: _num(r[i]) for k, i in cols6.items()}})
    if total6 is None or not classes6:
        raise ValueError(f"{path.name} A-6: no resident classes or total")
    _check_sum(classes6, total6, ("returns", "one", "two", "three", "four", "five", "six_plus"), "A-6")
    # the three tables describe the same returns
    if abs(sum(r["returns"] for r in classes6) - a5["totals"]["resident"]["returns"]) > 5:
        raise ValueError("A-6 returns do not equal A-5's all-resident total")
    if abs(a2["totals"]["resident"]["joint"] + a2["totals"]["resident"]["single"] + a2["totals"]["resident"]["hoh"]
           - a5["totals"]["resident"]["returns"]) > 5:
        raise ValueError("A-2's three statuses do not add to A-5's all-resident total")
    return {"a2_returns_by_status": a2, "a5_exemptions": a5,
            "a6_returns_by_regular_exemptions": {"classes": classes6, "total": total6}}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("editions", nargs="+", help="YEAR=PATH to a downloaded workbook")
    ap.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = ap.parse_args(argv)
    for spec in args.editions:
        year, _, path = spec.partition("=")
        p = Path(path).expanduser()
        parsed = parse_workbook(p)
        doc = {"source": {"title": "Individual Income Tax Statistics, Tables A-2, A-5 and A-6 (resident returns by Hawaiʻi AGI class)",
                          "publisher": "Hawaiʻi Department of Taxation, Office of Tax Research & Planning",
                          "url": URL.format(year=year), "file": p.name, "sha256": sha256(p),
                          "note": "A-2 'Single' includes married filing separately; 'H/H' includes head of household and "
                                  "qualifying widow(er). Regular exemptions = self + spouse (joint return) + qualified dependents. "
                                  "A-6 excludes returns filed by dependents claimed by another taxpayer from its exemption columns "
                                  "(its 'returns' are all resident returns)."},
               "tax_year": int(year), **parsed}
        out = args.out_dir / f"dotax_family_{year}.json"
        out.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
        r = parsed["a2_returns_by_status"]["totals"]["resident"]
        print(f"TY{year}: {sum(r.values()):,.0f} resident returns; H/H {r['hoh']:,.0f}; wrote {out.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
