"""Backtest the ledger's own forecasts against the Council on Revenues.

One origin a year: the Council's August or September meeting, when the
fiscal year just ended is known and the new one has two months of
collections. At each origin both forecasts are scored on the general fund
lines the Council itemises, at horizon 1 (the fiscal year in progress) and 2
(the next), against the general fund actuals in DOTAX's annual reports
(which equal the Council's own BASE figures for FY2012-FY2024).

The model sees the general fund history through the origin year and nothing
else; the Council also sees enacted law, two months of the new year and the
economic forecasts. The gap between the two is the price of a forecast that
knows only history.

    python -m state_revenue.backtest            # prints the summary tables
"""
from __future__ import annotations

import csv
import statistics
import sys
from collections.abc import Iterable
from dataclasses import asdict, dataclass

from state_revenue.baseline import RIDES_ON
from state_revenue.cor import CorForecast, load_cor_forecasts
from state_revenue.forecast import forecast_line, long_run_growth
from state_revenue.ledger import Ledger, load_ledger

#: The Council's lines scored here (its "miscellaneous" row has no ledger twin).
LINES = (
    "general_excise", "individual_income", "transient_accommodations", "corporate_income",
    "insurance_premiums", "public_service_companies", "tobacco", "liquor", "banks", "estate",
    "conveyance",
)
HORIZONS = (1, 2)
#: Targets the pandemic decided; reported apart.
COVID_YEARS = frozenset({2020, 2021, 2022})


@dataclass(frozen=True)
class Record:
    origin: int                 # last complete fiscal year at the meeting
    meeting: str
    line: str                   # a key of LINES, or "total"
    horizon: int
    target: int
    actual: float
    model: float
    cor: float
    method: str

    @property
    def model_error(self) -> float:
        return self.model / self.actual - 1

    @property
    def cor_error(self) -> float:
        return self.cor / self.actual - 1


def origin_meetings(forecasts: Iterable[CorForecast]) -> dict[int, CorForecast]:
    """The last July-September meeting of each year, by its origin year."""
    out: dict[int, CorForecast] = {}
    for f in forecasts:
        if 7 <= f.meeting.month <= 9:
            out[f.meeting.year] = f
    return out


def _total_growth(ledger: Ledger, origin: int) -> float:
    """Long-run growth of the ledger's total through ``origin``: the fallback
    for lines with short histories."""
    g = long_run_growth({fy: float(y.total) for fy, y in ledger.years.items() if fy <= origin})
    return 0.0 if g is None else g


def run(ledger: Ledger | None = None, forecasts: Iterable[CorForecast] | None = None) -> list[Record]:
    ledger = ledger or load_ledger("general_fund")
    meetings = origin_meetings(forecasts or load_cor_forecasts())
    records: list[Record] = []
    for origin, f in sorted(meetings.items()):
        targets = [origin + h for h in HORIZONS if origin + h in ledger.years]
        if not targets or origin not in ledger.years:
            continue
        totals = {t: 0.0 for t in targets}
        fallback = _total_growth(ledger, origin)
        for key in sorted({k for fy in ledger.years.values() for k in fy.lines}):
            history = {fy: float(v) for fy, v in ledger.series(key).items() if fy <= origin}
            if origin not in history:
                continue
            fc = forecast_line(history, max(HORIZONS), fallback_growth=fallback)
            for t in targets:
                totals[t] += fc.values[t]
                if key not in LINES or key not in ledger[t].lines or t not in f.estimates:
                    continue
                records.append(Record(origin, f.meeting.isoformat(), key, t - origin, t,
                                      float(ledger[t].lines[key]), fc.values[t],
                                      float(f.estimates[t][key]), fc.method))
        for t in targets:
            if t in f.estimates:
                records.append(Record(origin, f.meeting.isoformat(), "total", t - origin, t,
                                      float(ledger[t].total), totals[t],
                                      float(f.estimates[t]["total"]), "sum"))
    return records


@dataclass(frozen=True)
class Summary:
    line: str
    horizon: int
    n: int
    model_mape: float
    cor_mape: float
    model_wins: int             # origins where the model's error was the smaller

    @property
    def ratio(self) -> float:
        return self.model_mape / self.cor_mape if self.cor_mape else float("inf")


def summarize(records: list[Record], *, exclude_covid: bool = True) -> list[Summary]:
    out = []
    for line in (*LINES, "total"):
        for h in HORIZONS:
            rs = [r for r in records if r.line == line and r.horizon == h
                  and not (exclude_covid and r.target in COVID_YEARS)]
            if not rs:
                continue
            out.append(Summary(
                line, h, len(rs),
                statistics.mean(abs(r.model_error) for r in rs),
                statistics.mean(abs(r.cor_error) for r in rs),
                sum(abs(r.model_error) < abs(r.cor_error) for r in rs),
            ))
    return out


#: Lines (all funds) the Council does not itemise, or itemises only the general
#: fund's part of: the model is their only forecast.
MODEL_ONLY_LINES = (
    "county_surcharge", "fuel", "motor_vehicle", "rental_vehicle", "environmental_response",
    "unemployment", "transient_accommodations", "conveyance",
)


@dataclass(frozen=True)
class ModelOnly:
    line: str
    horizon: int
    n: int
    mape: float
    method: str


def run_model_only(ledger: Ledger | None = None, lines: Iterable[str] = MODEL_ONLY_LINES,
                   origins: Iterable[int] = range(2010, 2025), *,
                   exclude_covid: bool = True) -> list[ModelOnly]:
    """Rolling-origin error of the model alone on all-funds lines."""
    ledger = ledger or load_ledger("all")
    out = []
    for key in lines:
        series = {fy: float(v) for fy, v in ledger.series(key).items()}
        errs: dict[int, list[float]] = {h: [] for h in HORIZONS}
        methods = set()
        for origin in origins:
            history = {fy: v for fy, v in series.items() if fy <= origin}
            if origin not in history or len(history) < 4:
                continue
            fallback = _total_growth(ledger, origin)
            if key in RIDES_ON:              # as the baseline does it: a share of the host
                host = {fy: float(v) for fy, v in ledger.series(RIDES_ON[key]).items()
                        if fy <= origin}
                fc = forecast_line(host, max(HORIZONS), fallback_growth=fallback)
                share = history[origin] / host[origin]
                values = {t: v * share for t, v in fc.values.items()}
                methods.add(f"rides_on {RIDES_ON[key]}")
            else:
                fc = forecast_line(history, max(HORIZONS), fallback_growth=fallback)
                values = fc.values
                methods.add(fc.method)
            for h in HORIZONS:
                t = origin + h
                if t in series and not (exclude_covid and t in COVID_YEARS):
                    errs[h].append(abs(values[t] / series[t] - 1))
        for h in HORIZONS:
            if errs[h]:
                out.append(ModelOnly(key, h, len(errs[h]), statistics.mean(errs[h]),
                                     "/".join(sorted(methods))))
    return out


def _table(summaries: list[Summary]) -> str:
    rows = ["| Line | h | n | Model MAPE | COR MAPE | Model / COR | Model closer |",
            "|---|---|---|---|---|---|---|"]
    for s in summaries:
        rows.append(f"| {s.line} | {s.horizon} | {s.n} | {s.model_mape:.1%} | {s.cor_mape:.1%} "
                    f"| {s.ratio:.2f} | {s.model_wins}/{s.n} |")
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Backtest ledger forecasts against COR.")
    ap.add_argument("--csv", help="write every record here")
    args = ap.parse_args(argv)
    records = run()
    if args.csv:
        with open(args.csv, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=[*asdict(records[0]), "model_error", "cor_error"])
            w.writeheader()
            for r in records:
                w.writerow({**asdict(r), "model_error": r.model_error, "cor_error": r.cor_error})
    origins = sorted({r.origin for r in records})
    print(f"Origins {origins[0]}-{origins[-1]} (August/September meetings), "
          f"general fund actuals from DOTAX annual reports.\n")
    print("Excluding FY2020-FY2022 targets:\n")
    print(_table(summarize(records)))
    print("\nAll targets:\n")
    print(_table(summarize(records, exclude_covid=False)))
    print("\nModel alone, all funds, origins FY2010-FY2024, excluding FY2020-FY2022 targets:\n")
    print("| Line | h | n | Model MAPE | Method |\n|---|---|---|---|---|")
    for m in run_model_only():
        print(f"| {m.line} | {m.horizon} | {m.n} | {m.mape:.1%} | {m.method} |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
