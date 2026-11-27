"""Hawaiʻi's state tax collections by line and fiscal year.

Reads ``data/annual_collections.json`` (written by
``state_revenue.scripts.refresh_annual_collections``) and answers the ledger
questions: what each line collected, that the lines add to the total, and how
much of the total each modelling tier covers.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from state_revenue.lines import BY_KEY, HOST, LINES, TIERS, Tier

DATA_PATH = Path(__file__).resolve().parent / "data" / "annual_collections.json"


@dataclass(frozen=True)
class FiscalYear:
    year: int
    lines: dict[str, int]          # thousands of dollars
    total: int                     # the report's printed total
    report: str

    @property
    def residual(self) -> int:
        """Printed total minus the lines (rounding)."""
        return self.total - sum(self.lines.values())

    @property
    def folded(self) -> dict[str, str]:
        """``{line: host}`` for lines this year reports inside another row."""
        return {k: h for k, h in HOST.items() if k not in self.lines and h in self.lines}

    def by_tier(self) -> dict[Tier, int]:
        out: dict[Tier, int] = dict.fromkeys(TIERS, 0)
        for key, v in self.lines.items():
            out[BY_KEY[key].tier] += v
        return out


@dataclass(frozen=True)
class Ledger:
    years: dict[int, FiscalYear]
    fetch_date: str

    @property
    def fiscal_years(self) -> list[int]:
        return sorted(self.years)

    def __getitem__(self, fy: int) -> FiscalYear:
        return self.years[fy]

    def series(self, key: str) -> dict[int, int]:
        """``{fiscal_year: thousands}`` for one line, years it was its own row only
        (see :attr:`FiscalYear.folded` for the years it sits in another)."""
        if key not in BY_KEY:
            raise KeyError(f"unknown line {key!r}; lines are {[line.key for line in LINES]}")
        return {fy: y.lines[key] for fy, y in sorted(self.years.items()) if key in y.lines}

    def coverage(self, fy: int) -> dict[Tier, float]:
        """Share of a year's total in each tier."""
        y = self.years[fy]
        return {tier: v / y.total for tier, v in y.by_tier().items()}


def _from_payload(payload: dict) -> Ledger:
    years = {}
    for fy, e in payload["fiscal_years"].items():
        unknown = set(e["lines"]) - set(BY_KEY)
        if unknown:
            raise ValueError(f"FY{fy}: lines not in state_revenue.lines: {sorted(unknown)}")
        years[int(fy)] = FiscalYear(
            year=int(fy), lines=dict(e["lines"]), total=int(e["total"]), report=e["report"])
    return Ledger(years=years, fetch_date=payload["fetch_date"])


@lru_cache(maxsize=1)
def load_ledger() -> Ledger:
    """The bundled ledger (cached)."""
    return _from_payload(json.loads(DATA_PATH.read_text()))
