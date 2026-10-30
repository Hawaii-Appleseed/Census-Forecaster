"""The Department of Taxation's "Tax Credits Claimed by Hawaiʻi Taxpayers" figures for two credits.

Dollars and claim counts for the earned income credit and the refundable food and excise credit,
from Tables A-1 and A-2 of the Department's workbook for each tax year. The working-family step
fits its food and excise take-up to these dollars and checks both credits against them.

The numbers are in ``data/calibration/dotax_credits_claimed.json``, written by
``scripts/parse_dotax_credits_claimed.py`` from the Department's workbooks with each file's
SHA-256. They were typed into ``chain/forecast_working_family_credits.py`` until then, the same
way the Council on Revenues projections were typed into three places and left on a stale vintage.

Not the same as :mod:`dotax_base`'s ``eitc_new_credit``, which reads the Department's separate
Earned Income Tax Credit Report (TY2024, the credit's new claims) and is the EITC take-up anchor.
Table A-1's TY2023 EITC dollars ($77.05M) differ from that report's applied figure ($75.6M) by about
2 percent, because the two reports count the credit differently.
"""
from __future__ import annotations

import json
from functools import cache
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "data" / "calibration" / "dotax_credits_claimed.json"

#: ``eitc`` is the earned income credit, ``food_excise`` the refundable food and excise credit.
CREDITS = ("eitc", "food_excise")


@cache
def _load() -> dict:
    return json.loads(_PATH.read_text())


def years() -> list[int]:
    """Tax years in the file."""
    return sorted(int(y) for y in _load()["years"])


def _row(credit: str, year: int) -> dict[str, float]:
    if credit not in CREDITS:
        raise KeyError(f"{credit!r} is not one of {CREDITS}")
    try:
        return _load()["years"][str(year)][credit]
    except KeyError:
        raise KeyError(f"no Tax Credits Claimed figures for tax year {year}; the file has {years()}") from None


def dollars_M(credit: str, year: int) -> float:
    """Dollars claimed, in millions, exactly as the Department prints them."""
    return _row(credit, year)["dollars_k"] / 1000


def claims(credit: str, year: int) -> int:
    """Number of credits claimed."""
    return int(_row(credit, year)["claims"])
