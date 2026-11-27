"""The baseline: every state tax line, by fiscal year, from the best source.

Which source a line takes follows the backtest against the Council on
Revenues (``state_revenue.backtest``, ``STATE_REVENUE_SCOPE.md``):

``cor``
    The general fund's part of a line the Council itemises: its latest line
    items. The Council knows enacted law (the Act 46 income tax steps, how the
    transient accommodations tax is split), which a forecast from history
    cannot; fiscal notes score against these figures.
``cor_scaled``
    All funds, a line the Council itemises: its general fund figure times the
    line's all-funds-to-general-fund ratio in the last actual year (current
    law's split between funds held).
``rides_on``
    The county surcharge: the general excise baseline times the surcharge's
    ratio to it in the last actual year (it is levied on the same base).
``model``
    Everything else (fuel, vehicle taxes, unemployment contributions, the
    environmental response tax, fees): ``state_revenue.forecast`` on the
    line's own history.

``build_baseline(source="model")`` forecasts every line from history alone,
the check on the Council and the fallback past its last year.

    python -m state_revenue.baseline            # all funds, latest COR meeting
    python -m state_revenue.baseline --fund general_fund --source model
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date
from typing import Literal

from state_revenue.cor import CorForecast, load_cor_forecasts
from state_revenue.forecast import forecast_line, long_run_growth
from state_revenue.ledger import Ledger, load_ledger

Source = Literal["cor", "cor_scaled", "rides_on", "model"]

#: Lines levied on another line's base: forecast as a share of it.
RIDES_ON = {"county_surcharge": "general_excise"}
#: COR keys that are not ledger lines.
COR_ONLY = ("miscellaneous", "total")


@dataclass(frozen=True)
class Value:
    value: float          # thousands of dollars
    source: Source


@dataclass(frozen=True)
class Baseline:
    fund: str
    origin: int                       # last actual fiscal year
    cor_meeting: date | None
    values: dict[int, dict[str, Value]]

    @property
    def fiscal_years(self) -> list[int]:
        return sorted(self.values)

    def total(self, fy: int) -> float:
        return sum(v.value for v in self.values[fy].values())

    def line(self, key: str) -> dict[int, float]:
        return {fy: row[key].value for fy, row in sorted(self.values.items()) if key in row}


def _cor_years(cor: CorForecast, origin: int) -> dict[int, dict[str, int]]:
    """The Council's figures for years after ``origin``: preliminary BASE years, then estimates."""
    out = {fy: row for fy, row in cor.base.items() if fy > origin}
    out.update(cor.estimates)
    return out


def build_baseline(fund: str = "all", *, source: Literal["best", "model"] = "best",
                   cor: CorForecast | None = None, ledger: Ledger | None = None,
                   general_fund: Ledger | None = None) -> Baseline:
    """Baseline for ``fund`` ("all" or "general_fund") through the Council's last year."""
    ledger = ledger or load_ledger(fund)
    gf = general_fund or load_ledger("general_fund")
    cor = cor or load_cor_forecasts()[-1]
    origin = max(ledger.years)
    cor_years = _cor_years(cor, origin)
    years = sorted(cor_years)
    horizon = years[-1] - origin
    last = ledger[origin]

    total_growth = long_run_growth({fy: float(y.total) for fy, y in ledger.years.items()}) or 0.0
    model: dict[str, dict[int, float]] = {}
    for key in last.lines:
        history = {fy: float(v) for fy, v in ledger.series(key).items()}
        model[key] = forecast_line(history, horizon, fallback_growth=total_growth).values

    values: dict[int, dict[str, Value]] = {fy: {} for fy in years}
    for key in last.lines:
        if key in RIDES_ON:
            continue
        cor_line = source == "best" and key in cor_years[years[0]] and key not in COR_ONLY
        for fy in years:
            if not cor_line:
                values[fy][key] = Value(model[key][fy], "model")
            elif fund == "general_fund":
                values[fy][key] = Value(float(cor_years[fy][key]), "cor")
            else:
                gf_last = gf[origin].lines.get(key)
                if not gf_last:
                    values[fy][key] = Value(model[key][fy], "model")
                else:
                    ratio = last.lines[key] / gf_last
                    values[fy][key] = Value(cor_years[fy][key] * ratio, "cor_scaled")
    for key, host in RIDES_ON.items():
        if key in last.lines and host in last.lines:
            ratio = last.lines[key] / last.lines[host]
            for fy in years:
                values[fy][key] = Value(values[fy][host].value * ratio, "rides_on")
    if fund == "general_fund" and source == "best":
        # The Council's catch-all row stands in for the small lines it folds in.
        folded = [k for k in last.lines if k not in cor_years[years[0]]]
        for fy in years:
            for k in folded:
                values[fy].pop(k, None)
            values[fy]["miscellaneous"] = Value(float(cor_years[fy]["miscellaneous"]), "cor")
    return Baseline(fund=fund, origin=origin, cor_meeting=cor.meeting if source == "best" else None,
                    values=values)


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Print the state revenue baseline.")
    ap.add_argument("--fund", choices=("all", "general_fund"), default="all")
    ap.add_argument("--source", choices=("best", "model"), default="best")
    args = ap.parse_args(argv)
    b = build_baseline(args.fund, source=args.source)
    keys = sorted({k for row in b.values.values() for k in row},
                  key=lambda k: -b.values[b.fiscal_years[0]][k].value)
    print(f"Baseline, {args.fund}, source {args.source}, last actual FY{b.origin}, "
          f"COR meeting {b.cor_meeting}. $ millions.\n")
    print("| Line | Source | " + " | ".join(f"FY{fy}" for fy in b.fiscal_years) + " |")
    print("|---|---|" + "---|" * len(b.fiscal_years))
    for k in keys:
        src = b.values[b.fiscal_years[0]][k].source
        print(f"| {k} | {src} | " + " | ".join(f"{b.values[fy][k].value / 1e3:,.1f}"
                                              for fy in b.fiscal_years) + " |")
    print("| **total** | | " + " | ".join(f"**{b.total(fy) / 1e3:,.1f}**" for fy in b.fiscal_years) + " |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
