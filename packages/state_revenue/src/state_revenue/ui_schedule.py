"""Which unemployment contribution rate schedule the statute puts in force.

HRS §383-68(c): before December 31 the schedule for the next calendar year is
set by the ratio of the *current reserve fund* to the *adequate reserve fund*
(§383-63):

- current reserve fund: the trust fund's assets on November 30;
- adequate reserve fund: the highest benefit cost rate of the ten years
  ending November 30, times total remuneration in the four quarters ending
  June 30 (for calendar years 2023-2030 the benefit cost rates of June 2020
  to August 2021 are left out);
- benefit cost rate: net benefits paid in twelve months over total
  remuneration in the last four completed quarters ending at least five
  months before the twelve months end.

Ratio above 1.69: schedule A; 1.30-1.69 B; 1.00-1.29 C; 0.80-0.99 D;
0.60-0.79 E; 0.40-0.59 F; 0.20-0.39 G; below 0.20 H. Calendar years 2021 and
2022 were set to D by the statute itself.

Computed from the Department of Labor's monthly trust fund report and annual
payroll, the rule reproduces every schedule DLIR announced for 2020 and
2023-2026 (``check``). Two approximations: remuneration for four quarters
ending June 30 is the mean of the two calendar years it straddles (DOL's
file is by calendar year), and the balance carried into December stands for
the assets on November 30.

``projected_schedules`` runs the rule forward: the fund month by month
(contributions from the schedule model, benefits at the latest twelve months'
benefit cost rate, interest at the latest twelve months' rate), then the
ratio each November.
"""
from __future__ import annotations

import statistics
from functools import lru_cache

from state_revenue.forecast import forecast_line
from state_revenue.unemployment import UIData, _months, load_ui

#: Lower bound of each schedule's ratio band, highest first (the ratio is
#: rounded to hundredths; A needs MORE than 1.69).
BANDS = (("A", 1.70), ("B", 1.30), ("C", 1.00), ("D", 0.80), ("E", 0.60), ("F", 0.40), ("G", 0.20))
#: Schedules the statute fixed regardless of the ratio (§383-68(c)).
STATUTORY = {2021: "D", 2022: "D"}
#: Benefit cost rates left out of the adequate reserve fund for calendar years 2023-2030.
EXCLUDED = frozenset(_months("2020-06", "2021-08"))
EXCLUDED_FOR = range(2023, 2031)


def schedule_for_ratio(ratio: float) -> str:
    r = round(ratio, 2)
    for letter, floor in BANDS:
        if r >= floor:
            return letter
    return "H"


def _back(end: str, n: int) -> list[str]:
    """The ``n`` months ending with ``end``."""
    y, m = int(end[:4]), int(end[5:])
    m -= n - 1
    while m <= 0:
        y, m = y - 1, m + 12
    return _months(f"{y:04d}-{m:02d}", end)


class _Fund:
    """The data with a projection appended (payroll by year, monthly flows)."""

    def __init__(self, ui: UIData):
        self.ui = ui
        self.payroll = dict(ui.total_payroll)
        self.benefits = dict(ui.benefits)
        self.balance = dict(ui.balance)

    def remuneration(self, year: int) -> float:
        """Total remuneration in the four quarters ending June 30 of ``year``."""
        self._extend_payroll(year)
        return (self.payroll[year - 1] + self.payroll[year]) / 2

    def _extend_payroll(self, year: int) -> None:
        last = max(self.ui.total_payroll)
        if year > max(self.payroll):
            history = {y: v for y, v in self.ui.total_payroll.items()}
            self.payroll.update(forecast_line(history, year - last, method="damped").values)

    def benefit_cost_rate(self, end: str) -> float | None:
        """Twelve months of benefits ending ``end`` over the remuneration of the
        four quarters ending at least five months earlier."""
        months = _back(end, 12)
        if any(m not in self.benefits for m in months):
            return None
        y, m = int(end[:4]), int(end[5:])
        year = y if m >= 11 else y - 1            # June 30 of y is five months before November 30
        if year - 1 not in self.payroll and year - 1 < min(self.payroll):
            return None
        return sum(self.benefits[k] for k in months) / self.remuneration(year)

    def adequate(self, year: int) -> tuple[float, str]:
        """The adequate reserve fund computed November 30 of ``year`` and the
        month ending the twelve months with the highest benefit cost rate."""
        skip = EXCLUDED if year + 1 in EXCLUDED_FOR else frozenset()
        rates = {}
        for end in _back(f"{year}-11", 120 - 11):         # periods wholly inside the ten years
            if end in skip:
                continue
            rate = self.benefit_cost_rate(end)
            if rate is not None:
                rates[end] = rate
        top = max(rates, key=rates.get)
        return rates[top] * self.remuneration(year), top

    def ratio(self, year: int) -> float:
        return self.balance[f"{year}-12"] / self.adequate(year)[0]


def reserve_ratio(year: int, ui: UIData | None = None) -> dict:
    """The computation made November 30 of ``year`` (for calendar ``year + 1``),
    from reported data only."""
    fund = _Fund(ui or load_ui())
    adequate, top = fund.adequate(year)
    current = fund.balance[f"{year}-12"]
    ratio = current / adequate
    return {"for_year": year + 1, "current": current, "adequate": adequate, "top_period_end": top,
            "ratio": ratio, "schedule": STATUTORY.get(year + 1, schedule_for_ratio(ratio))}


def check(ui: UIData | None = None) -> list[dict]:
    """The rule against every schedule DLIR announced that the data can reach."""
    ui = ui or load_ui()
    out = []
    for year, announced in sorted(ui.schedules.items()):
        if f"{year - 1}-12" not in ui.balance:
            continue
        r = reserve_ratio(year - 1, ui)
        out.append({**r, "announced": announced, "match": r["schedule"] == announced,
                    "statutory": year in STATUTORY})
    return out


def _month_shares(ui: UIData, through: int, years: int = 5) -> dict[int, float]:
    """Each calendar month's share of a rate year's receipts (February to the
    next January), averaged over recent complete rate years."""
    acc: dict[int, list[float]] = {m: [] for m in range(1, 13)}
    done = 0
    for year in range(through, through - 12, -1):
        total = ui.rate_year(year)
        if not total:
            continue
        for ym in _months(f"{year}-02", f"{year + 1}-01"):
            acc[int(ym[5:])].append(ui.monthly[ym] / total)
        done += 1
        if done == years:
            break
    return {m: statistics.mean(v) for m, v in acc.items()}


@lru_cache(maxsize=4)
def _project(through_year: int) -> tuple[dict[int, str], dict[int, float]]:
    from state_revenue.unemployment import effective_rates, forecast_rate_year

    ui = load_ui()
    fund = _Fund(ui)
    schedules = dict(ui.schedules)
    ratios: dict[int, float] = {}
    last_month = max(ui.monthly)
    last_rate_year = max(effective_rates(ui))
    shares = _month_shares(ui, last_rate_year)
    recent = _back(last_month, 12)
    bcr = sum(ui.benefits[m] for m in recent) / fund.remuneration(int(last_month[:4]))
    interest = sum(ui.interest[m] for m in recent) / sum(ui.balance[m] for m in recent)
    receipts: dict[int, float] = {}

    def rate_year_receipts(year: int) -> float:
        if year not in receipts:
            receipts[year] = forecast_rate_year(year, ui, schedule=schedules[year])
        return receipts[year]

    month = last_month
    first_unannounced = max(ui.schedules) + 1
    for year in range(first_unannounced, through_year + 1):
        # roll the fund forward to the balance carried into December of year - 1
        target = f"{year - 1}-12"
        while f"{int(month[:4]) + (month[5:] == '12')}-{(int(month[5:]) % 12) + 1:02d}" <= target:
            y, m = int(month[:4]), int(month[5:])
            rate_year = y - 1 if m == 1 else y
            flow = ui.monthly.get(month)
            if flow is None:
                flow = shares[m] * rate_year_receipts(rate_year)
            paid = ui.benefits.get(month)
            if paid is None:
                paid = bcr * fund.remuneration(y) / 12
                fund.benefits[month] = paid
            earned = ui.interest.get(month, interest * fund.balance[month])
            nxt = f"{y + (m == 12)}-{(m % 12) + 1:02d}"
            if nxt not in fund.balance:
                fund.balance[nxt] = fund.balance[month] + flow + earned - paid
            month = nxt
        ratios[year] = fund.ratio(year - 1)
        schedules[year] = STATUTORY.get(year, schedule_for_ratio(ratios[year]))
    return schedules, ratios


def projected_schedules(through_year: int, ui: UIData | None = None) -> dict[int, str]:
    """Announced schedules, then the statute's rule on the projected fund, by calendar year."""
    return dict(_project(through_year)[0])


def projected_ratios(through_year: int) -> dict[int, float]:
    """The projected reserve ratio behind each unannounced year's schedule."""
    return dict(_project(through_year)[1])
