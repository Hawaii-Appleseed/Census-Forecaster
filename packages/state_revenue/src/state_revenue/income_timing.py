"""When a tax year's individual income tax reaches the state: fiscal-year cash.

The income tax model scores a change in tax-year liability. The ledger is
fiscal-year cash (July to June). The repository's convention, FY(n+1) = TY(n),
puts all of a tax year's change in the next fiscal year. That is right for a
change taxpayers settle on their returns and wrong for one that reaches
withholding, half of which is collected in the tax year's first six months.

DOTAX's monthly collections (``census_forecaster``'s bundle, every month from
July 2013) split the tax into withholding, estimated payments, payments with
returns and refunds. For a tax year TY:

- **Withholding** on TY wages is collected January to December of TY: the
  January-June part is fiscal year TY, the rest fiscal year TY+1.
- **Estimated payments** for TY are due in April, June and September of TY and
  January of TY+1. February-June collections are fiscal year TY (the January
  payment belongs to the year before), July-January fiscal year TY+1.
- **Payments with returns and refunds** for TY come when returns are filed,
  January to June of TY+1 mostly: fiscal year TY+1. (Extension payments and
  late refunds run into fiscal year TY+2; they are counted with TY+1 here.)

``fiscal_shares`` turns those into the share of a tax-year change that lands
in fiscal year TY, by how the change reaches taxpayers:

``"withholding"``
    DOTAX changes the withholding tables with the law (it did for Act 46's
    first step): the change follows each channel's own timing, weighted by the
    channel's share of gross payments.
``"filing"``
    The tables do not change, or the change is a credit claimed on the
    return: all of it in fiscal year TY+1. The repository's convention.
"""
from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from typing import Literal

Channel = Literal["withholding", "filing"]

#: Tax years whose deadlines were moved (2020 returns and estimates to July
#: 2020; 2021 followed a distorted base). Left out of the averages.
SKIP_YEARS = frozenset({2020, 2021})


@lru_cache(maxsize=1)
def _monthly() -> dict[str, dict[str, float]]:
    path = files("census_forecaster") / "data" / "dotax_monthly" / "collections.json"
    return json.loads(path.read_text())["monthly"]


def _sum(key: str, first: str, last: str) -> float | None:
    months = _monthly()
    y, m = int(first[:4]), int(first[5:])
    total = 0.0
    while f"{y:04d}-{m:02d}" <= last:
        row = months.get(f"{y:04d}-{m:02d}")
        if row is None or key not in row:
            return None
        total += row[key]
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return total


@dataclass(frozen=True)
class TaxYearCash:
    tax_year: int
    withholding_early: float      # January-June of the tax year
    withholding_late: float       # July-December
    estimates_early: float        # February-June
    estimates_late: float         # July of the tax year to the next January
    returns: float                # payments with returns, the next calendar year
    refunds: float                # negative, the next calendar year

    @property
    def gross(self) -> float:
        return (self.withholding_early + self.withholding_late + self.estimates_early
                + self.estimates_late + self.returns)

    @property
    def net(self) -> float:
        return self.gross + self.refunds


def tax_year_cash(tax_year: int) -> TaxYearCash | None:
    """A tax year's collections by channel and half, dollars; None if a month is missing."""
    ty, nxt = tax_year, tax_year + 1
    parts = (
        _sum("ind_wh", f"{ty}-01", f"{ty}-06"), _sum("ind_wh", f"{ty}-07", f"{ty}-12"),
        _sum("ind_est", f"{ty}-02", f"{ty}-06"), _sum("ind_est", f"{ty}-07", f"{nxt}-01"),
        _sum("ind_ret", f"{nxt}-01", f"{nxt}-12"), _sum("ind_refunds", f"{nxt}-01", f"{nxt}-12"),
    )
    if any(p is None for p in parts):
        return None
    return TaxYearCash(tax_year, *parts)


def complete_years() -> list[TaxYearCash]:
    months = _monthly()
    first, last = int(min(months)[:4]) + 1, int(max(months)[:4])
    return [c for y in range(first, last) if y not in SKIP_YEARS and (c := tax_year_cash(y))]


@dataclass(frozen=True)
class Timing:
    years: tuple[int, ...]
    withholding_share: float          # of gross payments
    estimates_share: float
    returns_share: float
    withholding_early: float          # share of withholding collected January-June
    estimates_early: float            # share of estimated payments collected February-June


@lru_cache(maxsize=1)
def timing() -> Timing:
    """Mean shares over the complete tax years in the bundle (:data:`SKIP_YEARS` out)."""
    cs = complete_years()
    if not cs:
        raise ValueError("no complete tax year in the monthly collections bundle")
    mean = statistics.mean
    return Timing(
        years=tuple(c.tax_year for c in cs),
        withholding_share=mean((c.withholding_early + c.withholding_late) / c.gross for c in cs),
        estimates_share=mean((c.estimates_early + c.estimates_late) / c.gross for c in cs),
        returns_share=mean(c.returns / c.gross for c in cs),
        withholding_early=mean(c.withholding_early / (c.withholding_early + c.withholding_late) for c in cs),
        estimates_early=mean(c.estimates_early / (c.estimates_early + c.estimates_late) for c in cs),
    )


def fiscal_shares(channel: Channel = "withholding") -> tuple[float, float]:
    """``(share in fiscal year TY, share in fiscal year TY+1)`` of a change in
    tax year TY's liability."""
    if channel == "filing":
        return 0.0, 1.0
    if channel != "withholding":
        raise ValueError(f"channel must be 'withholding' or 'filing', got {channel!r}")
    t = timing()
    early = t.withholding_share * t.withholding_early + t.estimates_share * t.estimates_early
    return early, 1.0 - early


def to_fiscal_years(by_tax_year: dict[int, float], channel: Channel = "withholding") -> dict[int, float]:
    """Spread tax-year changes (any unit) over fiscal years."""
    early, late = fiscal_shares(channel)
    out: dict[int, float] = {}
    for ty, value in by_tax_year.items():
        out[ty] = out.get(ty, 0.0) + value * early
        out[ty + 1] = out.get(ty + 1, 0.0) + value * late
    return dict(sorted(out.items()))


def main(argv: list[str] | None = None) -> int:
    t = timing()
    print("Individual income tax by tax year and channel ($ millions):\n")
    print("| Tax year | Withholding Jan-Jun | Jul-Dec | Estimates Feb-Jun | Jul-Jan | With returns "
          "| Refunds | Net |\n|---|---|---|---|---|---|---|---|")
    months = _monthly()
    for y in range(int(min(months)[:4]) + 1, int(max(months)[:4])):
        c = tax_year_cash(y)
        if c:
            mark = " *" if y in SKIP_YEARS else ""
            print(f"| {y}{mark} | {c.withholding_early / 1e6:,.0f} | {c.withholding_late / 1e6:,.0f} "
                  f"| {c.estimates_early / 1e6:,.0f} | {c.estimates_late / 1e6:,.0f} "
                  f"| {c.returns / 1e6:,.0f} | {c.refunds / 1e6:,.0f} | {c.net / 1e6:,.0f} |")
    print(f"\n\\* left out of the averages. Averages over {t.years[0]}-{t.years[-1]}:")
    print(f"withholding {t.withholding_share:.1%} of gross payments ({t.withholding_early:.1%} of it "
          f"January-June), estimates {t.estimates_share:.1%} ({t.estimates_early:.1%} February-June), "
          f"payments with returns {t.returns_share:.1%}.")
    early, late = fiscal_shares("withholding")
    print(f"\nA change that reaches withholding: {early:.1%} in fiscal year TY, {late:.1%} in TY+1.")
    print("A change settled on returns: 0% and 100% (the FY(n+1) = TY(n) convention).")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
