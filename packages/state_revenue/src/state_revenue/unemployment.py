"""Unemployment insurance contributions: schedule times taxable payroll.

The ledger's "employment security contributions" line cannot be forecast
from its own history, for two reasons this module works around.

1. **The rate is set by rule.** Each calendar year DLIR puts one of eight
   contribution rate schedules (A, lowest, to H) in force, by the trust
   fund's balance against its adequate reserve (HRS §383-68). Contributions
   in a rate year are close to a constant per schedule times taxable payroll:
   about 1.0-1.2% under C, 1.3-1.5% under D, 2.5% under F (2020-2025).
2. **DOTAX's line changed coverage.** Against the Department of Labor's count
   of net contributions deposited (ETA 2112), DOTAX's line was 50-70% of it
   through FY2023, 87% in FY2024 and 107% in FY2025; month by month they
   agree within $1-2M since late 2024. So the line's tripling from FY2022 to
   FY2024 is partly a change in what DOTAX counted, and the DOL series is
   the one to model.

Receipts for a rate year arrive with the quarterly reports: April, July,
October and the next January. "Rate-year receipts" are February of the year
through the next January. A fiscal year takes the July-January part of one
rate year and the February-June part of the next.

The forecast: rate-year receipts = the effective rate of the schedule in
force (the latest rate year under the same schedule; for a schedule not yet
seen, the last year's rate moved by the ratio of the schedules' new-employer
rates) times taxable payroll (ETA 204, grown by its own damped trend). A year whose schedule DLIR has not
announced keeps the last announced one. Months DOL has already reported are
used as they are.
"""
from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from functools import lru_cache

from state_revenue.forecast import forecast_line
from state_revenue.general_excise import DATA
from state_revenue.ledger import load_ledger

SHARE_YEARS = 5          # rate years the February-June share is averaged over


@dataclass(frozen=True)
class UIData:
    monthly: dict[str, float]                 # "YYYY-MM" -> net contributions, dollars
    balance: dict[str, float]                 # trust fund balance forward
    taxable_payroll: dict[int, float]         # calendar year -> dollars
    schedules: dict[int, str]                 # calendar year -> "A".."H"
    new_employer_rate: dict[int, float]       # calendar year -> the schedule's new-employer rate

    def window(self, first: str, last: str) -> float | None:
        """Contributions from month ``first`` to ``last`` inclusive; None if any is missing."""
        months = _months(first, last)
        if any(m not in self.monthly for m in months):
            return None
        return sum(self.monthly[m] for m in months)

    def rate_year(self, year: int) -> float | None:
        """Receipts for a rate year: February through the next January."""
        return self.window(f"{year}-02", f"{year + 1}-01")

    def fiscal_year(self, fy: int) -> float | None:
        return self.window(f"{fy - 1}-07", f"{fy}-06")


def _months(first: str, last: str) -> list[str]:
    y, m = int(first[:4]), int(first[5:])
    out = []
    while f"{y:04d}-{m:02d}" <= last:
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


@lru_cache(maxsize=1)
def load_ui() -> UIData:
    d = json.loads((DATA / "ui_hawaii.json").read_text())
    return UIData(
        monthly={k: v["contributions"] for k, v in d["monthly"].items()},
        balance={k: v["trust_fund_balance"] for k, v in d["monthly"].items()},
        taxable_payroll={int(y): v["taxable_payroll"] for y, v in d["payroll"].items()},
        schedules={int(y): v["schedule"] for y, v in d["schedules"].items()},
        new_employer_rate={int(y): v["new_employer_rate"] for y, v in d["schedules"].items()},
    )


def effective_rates(ui: UIData | None = None, *, through: int | None = None) -> dict[int, float]:
    """Rate-year receipts over that year's taxable payroll, for complete rate
    years (through ``through`` if given)."""
    ui = ui or load_ui()
    out = {}
    for year, payroll in sorted(ui.taxable_payroll.items()):
        receipts = ui.rate_year(year)
        if receipts is not None and (through is None or year <= through):
            out[year] = receipts / payroll
    return out


def schedule_rate(schedule: str, ui: UIData | None = None, *, through: int | None = None) -> float:
    """The effective rate of the latest complete rate year under ``schedule``
    (through ``through``). KeyError if the schedule has not been seen."""
    ui = ui or load_ui()
    rates = effective_rates(ui, through=through)
    years = [y for y in rates if ui.schedules.get(y) == schedule]
    if not years:
        raise KeyError(f"no complete rate year under schedule {schedule}")
    return rates[max(years)]


def _early_share(ui: UIData, through: int) -> float:
    """February-June receipts as a share of the rate year's, recent years' mean."""
    shares = []
    for year in range(through, through - 12, -1):
        whole, early = ui.rate_year(year), ui.window(f"{year}-02", f"{year}-06")
        if whole and early is not None:
            shares.append(early / whole)
        if len(shares) == SHARE_YEARS:
            break
    return statistics.mean(shares)


def forecast_rate_year(year: int, ui: UIData | None = None, *, known_through: int | None = None,
                       schedule: str | None = None) -> float:
    """Receipts for a rate year, dollars. ``known_through`` is the last
    complete rate year the forecast may use (default: the latest in the data)."""
    ui = ui or load_ui()
    rates = effective_rates(ui, through=known_through)
    last = max(rates)
    if year <= last:
        return ui.rate_year(year)
    in_force = max(y for y in ui.schedules if y <= year)           # unannounced years keep the last
    schedule = schedule or ui.schedules[in_force]
    try:
        rate = schedule_rate(schedule, ui, through=last)
    except KeyError:
        # A schedule with no complete year yet: the last year's effective rate,
        # moved by the ratio of the two schedules' new-employer rates.
        rate = rates[last]
        if last in ui.new_employer_rate and schedule == ui.schedules[in_force]:
            rate *= ui.new_employer_rate[in_force] / ui.new_employer_rate[last]
    history = {y: v for y, v in ui.taxable_payroll.items() if y <= last}
    payroll = forecast_line(history, year - last, method="damped").values[year]
    return rate * payroll


def forecast_fiscal_year(fy: int, ui: UIData | None = None, *,
                         known_through: int | None = None) -> float:
    """Net contributions deposited in a fiscal year (DOL basis), dollars:
    reported months as they are, the rest from the rate-year forecasts."""
    ui = ui or load_ui()
    if known_through is None:
        actual = ui.fiscal_year(fy)
        if actual is not None:
            return actual
    last = known_through if known_through is not None else max(effective_rates(ui))
    share = _early_share(ui, last)
    total = 0.0
    # July-January belongs to rate year fy-1, February-June to rate year fy.
    for year, first, final, part in ((fy - 1, f"{fy - 1}-07", f"{fy}-01", 1 - share),
                                     (fy, f"{fy}-02", f"{fy}-06", share)):
        reported = ui.window(first, final) if known_through is None else None
        if reported is not None:
            total += reported
        else:
            total += part * forecast_rate_year(year, ui, known_through=last)
    return total


def dotax_ratio(ui: UIData | None = None) -> tuple[int, float]:
    """DOTAX's line over DOL's net contributions in the last fiscal year both
    report: what turns a DOL-basis forecast into the ledger's line."""
    ui = ui or load_ui()
    ledger = load_ledger("all")
    for fy in sorted(ledger.years, reverse=True):
        dol = ui.fiscal_year(fy)
        if dol and "unemployment" in ledger[fy].lines:
            return fy, ledger[fy].lines["unemployment"] * 1e3 / dol
    raise ValueError("no fiscal year in both the ledger and the DOL series")


def ledger_line(fy: int, ui: UIData | None = None) -> float:
    """The ledger's unemployment line for a fiscal year, $ thousands: the DOL-basis
    forecast times DOTAX's ratio to it in the last year both report."""
    return forecast_fiscal_year(fy, ui) * dotax_ratio(ui)[1] / 1e3


def backtest(ui: UIData | None = None) -> list[dict]:
    """Rate years with an announced schedule, each forecast from the years
    before it (schedule known, as DLIR announces it before the year starts)."""
    ui = ui or load_ui()
    out = []
    for year in sorted(ui.schedules):
        actual = ui.rate_year(year)
        if actual is None or year - 1 not in effective_rates(ui):
            continue
        seen = [y for y in ui.schedules if y < year and ui.schedules[y] == ui.schedules[year]]
        fc = forecast_rate_year(year, ui, known_through=year - 1)
        out.append({"rate_year": year, "schedule": ui.schedules[year], "actual": actual,
                    "forecast": fc, "error": fc / actual - 1, "schedule_seen_before": bool(seen)})
    return out


def main(argv: list[str] | None = None) -> int:
    ui = load_ui()
    ledger = load_ledger("all")
    print("Rate years (receipts February to the next January, $ millions):\n")
    print("| Rate year | Schedule | Receipts | Taxable payroll ($B) | Effective rate |\n|---|---|---|---|---|")
    for y, r in effective_rates(ui).items():
        if y >= 2016:
            print(f"| {y} | {ui.schedules.get(y, '')} | {ui.rate_year(y) / 1e6:,.1f} "
                  f"| {ui.taxable_payroll[y] / 1e9:.2f} | {r:.2%} |")
    print("\nFiscal years: DOL's net contributions and DOTAX's line ($ millions):\n")
    print("| FY | DOL | DOTAX | DOTAX / DOL |\n|---|---|---|---|")
    for fy in range(2016, max(ledger.years) + 1):
        dol, dotax = ui.fiscal_year(fy) / 1e6, ledger[fy].lines["unemployment"] / 1e3
        print(f"| {fy} | {dol:,.1f} | {dotax:,.1f} | {dotax / dol:.2f} |")
    print("\nBacktest, each rate year from the years before it:\n")
    print("| Rate year | Schedule | Actual | Forecast | Error | Schedule seen before |\n|---|---|---|---|---|---|")
    for b in backtest(ui):
        print(f"| {b['rate_year']} | {b['schedule']} | {b['actual'] / 1e6:,.1f} | {b['forecast'] / 1e6:,.1f} "
              f"| {b['error']:+.1%} | {'yes' if b['schedule_seen_before'] else 'no'} |")
    fy0 = max(ledger.years) + 1
    print(f"\nForecast, DOL basis and as the ledger's line (ratio {dotax_ratio(ui)[1]:.2f}), $ millions:\n")
    print("| FY | DOL basis | Ledger line |\n|---|---|---|")
    for fy in range(fy0, fy0 + 4):
        print(f"| {fy} | {forecast_fiscal_year(fy, ui) / 1e6:,.1f} | {ledger_line(fy, ui) / 1e3:,.1f} |")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
