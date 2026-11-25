"""DOTAX "Tax Credits Claimed by Hawaii Taxpayers": the Low-Income Household Renters credit (HRS 235-55.7).

All-taxpayer claim counts and dollars, Table A-1, TY2018-2023, from
``data/raw/dotax_renters_credit_claims.csv`` (``scripts/fetch_dotax_renters_credit.py``). The poverty
pipeline fits the credit's take-up to these dollars (``credits.hi_renters.fit_renters_takeup``).
"""
from __future__ import annotations

import csv
from functools import cache
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "data" / "raw" / "dotax_renters_credit_claims.csv"


@cache
def _all_rows() -> dict[int, tuple[int, float]]:
    out: dict[int, tuple[int, float]] = {}
    with _PATH.open() as f:
        for r in csv.DictReader(f):
            if r["income_class"] == "ALL" and r["claims"]:
                out[int(r["year"])] = (int(float(r["claims"])), float(r["amount_$K"]))
    return out


def years() -> list[int]:
    return sorted(_all_rows())


def latest_year() -> int:
    return years()[-1]


def claims(year: int) -> int:
    return _all_rows()[year][0]


def dollars_M(year: int) -> float:
    """Dollars claimed, in millions, as the Department prints them."""
    return _all_rows()[year][1] / 1000
