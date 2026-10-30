"""IRS Statistics of Income Table 2 for Hawaiʻi: returns, wages and AGI by size of AGI.

Every federal return filed from a Hawaiʻi address, in ten AGI classes, from the IRS's
"Individual Income and Tax Data, by State and Size of Adjusted Gross Income". It is the
benchmark for the population of people who live in Hawaiʻi, which is what the survey
counts. The Department of Taxation's resident-return tables (:mod:`dotax_base`) count a
narrower group, the returns of full-year residents, and run 15 to 27 percent below this
table in every class above $50,000: military families and people who moved in or out
during the year file federal returns from a Hawaiʻi address but are not resident returns.
:mod:`tax_modeler.agreement` reads both, so each population is judged against the
benchmark that matches its purpose.

The numbers are in ``data/calibration/irs_soi_hi_table2.json``, written by
``scripts/parse_irs_soi_hi_table2.py`` from the IRS workbooks with each file's SHA-256 and
each class set checked against the printed totals. Money amounts are in thousands of dollars.
:mod:`irs_soi_state_targets` is an older, coarser (six-class) extract of the same publisher's
ZIP-code file and is unrelated to this one.
"""
from __future__ import annotations

import json
from functools import cache
from pathlib import Path

#: The year the Department's tables are anchored on, so the two benchmarks describe the same year.
BASE_YEAR = 2023

_PATH = Path(__file__).resolve().parents[1] / "data" / "calibration" / "irs_soi_hi_table2.json"

#: Values the table carries for each class.
KEYS = ("returns", "joint_returns", "agi_amount_k", "wage_returns", "wage_amount_k")


@cache
def _load() -> dict:
    return json.loads(_PATH.read_text())


def years() -> list[int]:
    """Tax years in the file."""
    return sorted(int(y) for y in _load()["years"])


def bands() -> list[tuple[float, float]]:
    """``(lo, hi)`` for each class, left edge inclusive; ``-inf`` and ``inf`` at the ends."""
    return [(-float("inf") if c["lo"] is None else float(c["lo"]),
             float("inf") if c["hi"] is None else float(c["hi"])) for c in _load()["classes"]]


def values(key: str, year: int = BASE_YEAR) -> list[float]:
    """One value per class, in :func:`bands` order."""
    if key not in KEYS:
        raise KeyError(f"{key!r} is not one of {KEYS}")
    try:
        return list(_load()["years"][str(year)][key])
    except KeyError:
        raise KeyError(f"no IRS Table 2 for tax year {year}; the file has {years()}") from None


def returns(year: int = BASE_YEAR) -> dict[tuple[float, float], float]:
    """Federal returns from Hawaiʻi addresses by AGI class."""
    return dict(zip(bands(), values("returns", year), strict=True))


def wage_returns(year: int = BASE_YEAR) -> dict[tuple[float, float], float]:
    """Returns with salaries and wages, by AGI class."""
    return dict(zip(bands(), values("wage_returns", year), strict=True))


def wages_M(year: int = BASE_YEAR) -> dict[tuple[float, float], float]:
    """Salaries and wages in AGI, $ millions, by AGI class."""
    return {b: v / 1000 for b, v in zip(bands(), values("wage_amount_k", year), strict=True)}


def average_wage(year: int = BASE_YEAR) -> dict[tuple[float, float], float]:
    """Average salaries and wages per return with wages, dollars, by AGI class."""
    n, a = wage_returns(year), wages_M(year)
    return {b: (a[b] * 1e6 / n[b] if n[b] else float("nan")) for b in n}
