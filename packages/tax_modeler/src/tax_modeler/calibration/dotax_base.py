"""DOTAX *Hawaiʻi Individual Income Tax Statistics* anchors, by tax year.

The calibration chain anchors on DOTAX's resident-return tables: filer counts
and tax before credits by Hawaiʻi AGI class (Table A-8), filing status
(Table 4), AGI by class (Table A-1) and net long-term gains (Table 21). They
were hand-typed in five modules (``forward_targets``, ``simultaneous_calibrator``,
``forward_agi_targets``, ``cg_anchor``, ``scenarios.top_income_synthesis``) and
three older orchestrators, all on TY2022, which is how a new edition went
unnoticed. This module is the one home; the others import from it.

Vintages
--------
2023  The TY2023 edition (published October 2026), read from
      ``data/calibration/dotax_indinc_2023.json``, which
      ``scripts/parse_dotax_indinc.py`` writes from the PDF. Every figure is a
      printed table value or a documented derivation from one (below).
2022  The constants that were hand-typed here before, verbatim, so a run can be
      reproduced on the old base (``build_targets(..., base_year=2022)``).
      Caveat: the filer counts are wrong in six classes. Checked against the
      TY2022 edition's own Table A-8 (``indinc/archive/2022indinc.pdf``), the
      hand-typed counts differ at $30K-$40K (58,135 against 59,827), $200K-$300K
      (19,015 / 18,937), $300K-$400K (5,729 / 6,076), $400K-$500K (2,856 /
      2,926), $500K-$750K (2,549 / 2,991) and $750K-$1M (1,004 / 1,134), and
      total 618,423 against A-8's 621,026 without the Loss class. The tax column
      and the $1M+ row (1,824 / $663M) match. ``cg_anchor``'s own A-8 classes
      were right. The values stay as typed so the old base reproduces.

Derivations for 2023
--------------------
* Filer targets: Table A-8's 15 classes from $0 up, without the Loss class
  (the 2022 table did the same: 621,026 against 635,117 returns; the hand-typed
  618,423 was wrong).
* Tax targets: A-8 tax before credits, $M as printed.
* **The $1M+ class is the exception: it is set from a multi-year window, not the
  base edition.** Its tax swings too much for one year to anchor it: $413M, $415M,
  $799M, $663M, $441M in TY2019-2023, with no trend (+0.2 points of share a year,
  standard error 1.6) and no year-to-year persistence (lag-1 autocorrelation -0.07),
  so the mean of more years is the better estimate of its expected level and one year
  carries the whole series' spread (about $131M of the class's tax). ``TOP_CLASS_WINDOW``
  (TY2019-2023) sets the class's returns and tax to the window's mean share of all
  resident returns and of resident tax before credits, applied to the base edition's
  printed totals: 1,633 returns and $562.3M for TY2023, against 1,704 and $441M as
  printed. Shares, not dollar levels, because TY2019-20 were smaller-tax years and an
  average of nominal levels understates TY2023's scale. The series is
  ``data/calibration/dotax_a8_top_class.json`` (``scripts/parse_dotax_top_class.py``).
  The class table, its total, the filer and status totals and the AGI split all follow.
  Set ``DOTAX_TOP_CLASS_WINDOW=none`` for the edition as printed, or ``YYYY-YYYY`` for
  another window; the environment is how the spawned scenario workers see it.
* Filing status: Table 4's resident counts for single, joint, head of household
  and separate, scaled to the filer total with largest-remainder rounding.
  Qualifying widow(er)s (188) are left out of the scaling, as in 2022.
* AGI by class: Table A-1's taxable-return AGI for the eleven classes below
  $400K. A-1 stops at "$400,000 and over" ($9,319M); the four classes inside it
  split that total in proportion to A-8's tax before credits divided by its
  effective rate on Hawaiʻi AGI (rates are printed to 0.1 point, so each implied
  AGI is good to about 1%; the implied sum is within 0.2% of A-1's).
"""
from __future__ import annotations

import json
import os
from functools import cache
from pathlib import Path

import numpy as np

BASE_YEAR = 2023
"""Newest DOTAX edition on file; the default base year for forward targets."""

_HAND_TYPED_YEAR = 2022   # the one vintage kept as literals; later years come from the JSON


def _parse_window(text: str) -> tuple[int, int] | None:
    """'2019-2023' -> (2019, 2023); 'none' or '' -> None (the edition as printed)."""
    text = text.strip().lower()
    if text in ("", "none"):
        return None
    start, _, end = text.partition("-")
    window = (int(start), int(end or start))
    if window[0] > window[1]:
        raise ValueError(f"DOTAX_TOP_CLASS_WINDOW {text!r}: start after end")
    return window


TOP_CLASS_WINDOW: tuple[int, int] | None = _parse_window(
    os.environ.get("DOTAX_TOP_CLASS_WINDOW", "2019-2023"))
"""Years whose mean share sets the $1M+ class at ``BASE_YEAR`` (module doc); None is as printed."""

_DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "calibration"

Bracket = tuple[float, float]

# The 15 nominal brackets the calibration rakes to (A-8's classes without Loss).
AGI_BRACKETS: tuple[Bracket, ...] = (
    (0, 10_000), (10_000, 20_000), (20_000, 30_000), (30_000, 40_000),
    (40_000, 50_000), (50_000, 75_000), (75_000, 100_000), (100_000, 150_000),
    (150_000, 200_000), (200_000, 300_000), (300_000, 400_000),
    (400_000, 500_000), (500_000, 750_000), (750_000, 1_000_000),
    (1_000_000, np.inf),
)

STATUSES = ("single", "married_filing_jointly", "head_of_household",
            "married_filing_separately")
_STATUS_LABEL = {"single": "Single", "married_filing_jointly": "Married Filing Jointly",
                 "head_of_household": "Head of Household",
                 "married_filing_separately": "Married Filing Separately"}

# ── TY2022: the previously hand-typed constants, verbatim ────────────────────
_TY2022_FILERS: dict[Bracket, int] = dict(zip(AGI_BRACKETS, (
    115_285, 64_160, 57_835, 58_135, 53_555, 91_459, 54_976, 62_065, 27_976,
    19_015, 5_729, 2_856, 2_549, 1_004, 1_824), strict=False))
_TY2022_TAX_M: dict[Bracket, float] = dict(zip(AGI_BRACKETS, (
    3.0, 21.0, 51.0, 92.0, 116.0, 293.0, 261.0, 438.0, 294.0, 310.0, 153.0,
    101.0, 149.0, 85.0, 663.0), strict=False))
_TY2022_STATUS: dict[str, int] = {
    "single": 326_470, "married_filing_jointly": 210_724,
    "head_of_household": 65_638, "married_filing_separately": 15_591}
_TY2022_AGI_M: dict[Bracket, float] = dict(zip(AGI_BRACKETS, (
    240.0, 836.0, 1_377.0, 2_050.0, 2_375.0, 5_567.0, 4_744.0, 7_525.0, 4_796.0,
    4_517.0, 2_079.0, 1_277.0, 1_560.0, 868.0, 7_444.0), strict=False))
# cg_anchor's TY2022 A-8 classes (see the caveat above on the $400K+ counts).
_TY2022_CG_CLASS_RETURNS: dict[str, int] = {
    "100_150": 62_065, "150_200": 27_976, "200_300": 18_937, "300_400": 6_076,
    "400_1m": 2_926 + 2_991 + 1_134, "1mp": 1_824}
_TY2022_TOTAL_RETURNS = 635_117   # all resident returns, Loss class included


@cache
def load_indinc(year: int) -> dict:
    """The parsed edition for ``year`` (``dotax_indinc_<year>.json``)."""
    path = _DATA_DIR / f"dotax_indinc_{year}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"No parsed DOTAX edition for TY{year}: {path.name}. Download the PDF "
            f"(files.hawaii.gov/tax/stats/stats/indinc/{year}indinc.pdf) and run "
            f"scripts/parse_dotax_indinc.py."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _key(row: dict) -> Bracket:
    hi = np.inf if row["agi_hi"] is None else int(row["agi_hi"])
    return (int(row["agi_lo"]), hi)


@cache
def load_top_class_series() -> dict:
    """The $1M+ class and all-resident totals from Table A-8, by edition
    (``dotax_a8_top_class.json``, from ``scripts/parse_dotax_top_class.py``)."""
    path = _DATA_DIR / "dotax_a8_top_class.json"
    if not path.exists():
        raise FileNotFoundError(
            f"No {path.name}. Run scripts/parse_dotax_top_class.py on the editions "
            f"(files.hawaii.gov/tax/stats/stats/indinc/archive/<year>indinc.pdf)."
        )
    return json.loads(path.read_text(encoding="utf-8"))["years"]


def _printed_top(year: int) -> dict:
    return next(r for r in load_indinc(year)["table_a8_resident_liability"]["classes"]
                if r["agi_lo"] == 1_000_000)


def top_class_anchor(year: int = BASE_YEAR) -> tuple[int, float] | None:
    """The $1M+ class's ``(returns, tax before credits $M)`` from ``TOP_CLASS_WINDOW``.

    The window's mean share of all resident returns and of resident tax before
    credits, applied to the base edition's printed totals. None when the window is
    off or ``year`` is not the base year (older editions are used as printed).
    """
    if TOP_CLASS_WINDOW is None or year != BASE_YEAR:
        return None
    series = load_top_class_series()
    years = range(TOP_CLASS_WINDOW[0], TOP_CLASS_WINDOW[1] + 1)
    missing = [y for y in years if str(y) not in series]
    if missing:
        raise ValueError(f"no $1M+ class series for TY{missing} in dotax_a8_top_class.json")
    rows = [series[str(y)] for y in years]
    total = load_indinc(year)["table_a8_resident_liability"]["total"]
    returns_share = sum(r["returns"] / r["total_returns"] for r in rows) / len(rows)
    tax_share = sum(r["tax_before_M"] / r["total_tax_before_M"] for r in rows) / len(rows)
    return round(returns_share * total["returns"]), round(tax_share * total["tax_before_M"], 1)


def _a8_rows(year: int) -> list[dict]:
    """A-8 classes from $0 up (the Loss class has no lower bound), with the $1M+ class
    set from ``TOP_CLASS_WINDOW`` at the base year."""
    rows = [r for r in load_indinc(year)["table_a8_resident_liability"]["classes"]
            if r["agi_lo"] is not None]
    anchor = top_class_anchor(year)
    if anchor is None:
        return rows
    return [dict(r, returns=anchor[0], tax_before_M=anchor[1]) if r["agi_lo"] == 1_000_000 else r
            for r in rows]


def filer_targets(year: int = BASE_YEAR) -> dict[Bracket, int]:
    """Resident filers by AGI bracket (Loss class excluded)."""
    if year == _HAND_TYPED_YEAR:
        return dict(_TY2022_FILERS)
    return {_key(r): int(r["returns"]) for r in _a8_rows(year)}


def tax_targets_M(year: int = BASE_YEAR) -> dict[Bracket, float]:
    """Resident tax liability before credits by AGI bracket, $M."""
    if year == _HAND_TYPED_YEAR:
        return dict(_TY2022_TAX_M)
    return {_key(r): float(r["tax_before_M"]) for r in _a8_rows(year)}


def total_returns(year: int = BASE_YEAR) -> int:
    """All resident returns, Loss class included (the cg_anchor rank base)."""
    if year == _HAND_TYPED_YEAR:
        return _TY2022_TOTAL_RETURNS
    printed = int(load_indinc(year)["table_a8_resident_liability"]["total"]["returns"])
    anchor = top_class_anchor(year)
    return printed if anchor is None else printed + anchor[0] - _printed_top(year)["returns"]


def _largest_remainder(shares: dict[str, float], total: int) -> dict[str, int]:
    raw = {k: v * total for k, v in shares.items()}
    out = {k: int(np.floor(v)) for k, v in raw.items()}
    for k in sorted(raw, key=lambda k: raw[k] - out[k], reverse=True)[: total - sum(out.values())]:
        out[k] += 1
    return out


def status_targets(year: int = BASE_YEAR) -> dict[str, int]:
    """Resident filers by filing status, summing to ``sum(filer_targets(year))``."""
    if year == _HAND_TYPED_YEAR:
        return dict(_TY2022_STATUS)
    counts = load_indinc(year)["table_4_filing_status"]["resident"]
    raw = {s: counts[_STATUS_LABEL[s]] for s in STATUSES}
    denom = sum(raw.values())
    return _largest_remainder({s: n / denom for s, n in raw.items()},
                              sum(filer_targets(year).values()))


def agi_targets_M(year: int = BASE_YEAR) -> dict[Bracket, float]:
    """Resident taxable-return AGI by bracket, $M (derivation in the module doc)."""
    if year == _HAND_TYPED_YEAR:
        return dict(_TY2022_AGI_M)
    d = load_indinc(year)
    taxable = d["table_a1_resident_taxable"]["taxable_classes"]
    out = {_key(r): float(r["agi_M"]) for r in taxable if r["agi_lo"] < 400_000}
    top_total = next(float(r["agi_M"]) for r in taxable if r["agi_lo"] == 400_000)
    top = [r for r in _a8_rows(year) if r["agi_lo"] >= 400_000]
    implied = {_key(r): r["tax_before_M"] / (r["etr_agi_before_pct"] / 100.0) for r in top}
    scale = top_total / sum(implied.values())
    out.update({b: v * scale for b, v in implied.items()})
    return {b: out[b] for b in AGI_BRACKETS}


# ── Table 21 and the A-8 classes the capital-gains anchor ranks on ───────────
CG_CLASSES = ("lt100", "100_150", "150_200", "200_300", "300_400", "400p")


def nltcg_M(year: int, *, nonresident: bool = False) -> list[float]:
    """Net long-term gains by the six anchor classes, $M (Table 21).

    ``lt100`` folds the seven classes below $100K; nonresidents' composite
    returns join the top class, as in the earlier editions' series.
    """
    t = load_indinc(year)["table_21_nltcg"]
    col = "nonres_amount_M" if nonresident else "res_amount_M"
    by_lo = {r["agi_lo"]: r[col] for r in t["classes"]}
    out = [sum(v for lo, v in by_lo.items() if lo < 100_000),
           by_lo[100_000], by_lo[150_000], by_lo[200_000], by_lo[300_000], by_lo[400_000]]
    if nonresident:
        out[-1] += t["composite"]["amount_M"]
    return [round(v, 3) for v in out]


def cg_class_returns(year: int = BASE_YEAR) -> dict[str, int]:
    """A-8 resident returns by the capital-gains anchor's classes."""
    if year == _HAND_TYPED_YEAR:
        return dict(_TY2022_CG_CLASS_RETURNS)
    f = filer_targets(year)
    return {
        "100_150": f[(100_000, 150_000)], "150_200": f[(150_000, 200_000)],
        "200_300": f[(200_000, 300_000)], "300_400": f[(300_000, 400_000)],
        "400_1m": f[(400_000, 500_000)] + f[(500_000, 750_000)] + f[(750_000, 1_000_000)],
        "1mp": f[(1_000_000, np.inf)],
    }


# ── The Earned Income Tax Credit Report (Act 107), by tax year ───────────────
@cache
def load_eitc_report(year: int) -> dict:
    """DOTAX's *Earned Income Tax Credit Report* for ``year``
    (``dotax_eitc_<year>.json``, from ``scripts/parse_dotax_eitc_report.py``).

    The newest year here is the best anchor for the state EITC: it has claims
    and dollars by federal AGI range, residency and filing status, where the
    all-credits report (``Tax Credits Claimed``) has one line per credit.
    """
    path = _DATA_DIR / f"dotax_eitc_{year}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"No parsed EITC report for TY{year}: {path.name}. Download the PDF "
            f"(files.hawaii.gov/tax/stats/stats/act107_2017/act107_earnedincome_txcredit_{year}.pdf) "
            f"and run scripts/parse_dotax_eitc_report.py."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def eitc_new_credit(year: int) -> dict[str, float]:
    """Credit claimed *for* the tax year, all filers: ``{"claims", "dollars_M"}``.

    The new credit, not the amount applied: applied adds legacy nonrefundable
    carryforwards (TY2018-2022) that the 40% rate does not touch and Act 25
    (2025) ends. TY2024: 78,399 claims, $76.981M.
    """
    t = load_eitc_report(year)["table_1_by_federal_agi"]["total"]["new"]
    return {"claims": t["claims"], "dollars_M": t["dollars"] / 1e6}

