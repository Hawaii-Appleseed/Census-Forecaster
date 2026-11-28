"""Fetch Hawaiʻi's unemployment insurance tax data into ``data/ui_hawaii.json``.

Three public sources, no keys:

- US Department of Labor, ETA 2112 (UI Financial Transaction Summary),
  ``ar2112.csv``: by state and month. Kept for Hawaiʻi: net UI contributions
  (line 11, column ``c9``) and the trust fund account's balance forward (line
  1, ``c3``). Column meanings are DOL's data map (ET Handbook 402, "ar2112").
- ETA 204 (Experience Rating Report), ``ar204.csv``: by state and rate year.
  Kept: total and taxable payroll of taxable accounts (line 101, ``c7`` and
  ``c8``) for the 12 months ending ``c5``.
- Hawaiʻi DLIR, "Tax Rate Schedule and Weekly Benefit Amount": the
  contribution rate schedule (A to H) in force each calendar year, the taxable
  wage base, and the new-employer and maximum rates.

    python -m state_revenue.scripts.refresh_ui --cache /tmp/ui
"""
from __future__ import annotations

import argparse
import csv
import html
import io
import json
import re
import sys
import tempfile
from datetime import date
from pathlib import Path

from state_revenue.scripts.refresh_annual_collections import _fetch

DOL = "https://oui.doleta.gov/unemploy/csv"
DLIR_PAGE = "https://labor.hawaii.gov/ui/tax-rate-schedule-and-weekly-benefit-amount/"
OUT = Path(__file__).resolve().parent.parent / "data" / "ui_hawaii.json"
STATE = "HI"


def parse_2112(text: str, state: str = STATE) -> dict[str, dict[str, float]]:
    """``{"YYYY-MM": {"contributions", "trust_fund_balance"}}``, dollars."""
    out = {}
    for row in csv.DictReader(io.StringIO(text)):
        if row["st"] != state:
            continue
        out[row["rptdate"][:7]] = {
            "contributions": float(row["c9"] or 0),
            "trust_fund_balance": float(row["c3"] or 0),
        }
    if not out:
        raise ValueError(f"ETA 2112: no rows for {state}")
    return dict(sorted(out.items()))


def parse_204(text: str, state: str = STATE) -> dict[int, dict[str, float]]:
    """``{payroll year: {"total_payroll", "taxable_payroll"}}``, dollars: the 12
    months ending December 31 of the year (column ``c5``)."""
    out = {}
    for row in csv.DictReader(io.StringIO(text)):
        if row["st"] != state or not row["c5"] or not row["c8"]:
            continue
        if not row["c5"].endswith("12-31"):
            continue
        out[int(row["c5"][:4])] = {"total_payroll": float(row["c7"]),
                                   "taxable_payroll": float(row["c8"])}
    if not out:
        raise ValueError(f"ETA 204: no rows for {state}")
    return dict(sorted(out.items()))


def parse_dlir(page: str) -> dict[int, dict]:
    """``{calendar year: {"schedule", "wage_base", "new_employer_rate", "max_rate"}}``."""
    body = re.sub(r"<script.*?</script>|<style.*?</style>", "", page, flags=re.S)
    cells = [c.strip() for c in html.unescape(re.sub(r"<[^>]+>", "|", body)).split("|") if c.strip()]

    def row(label: str, n: int) -> list[str]:
        i = next((k for k, c in enumerate(cells) if c.lower().startswith(label)), None)
        if i is None:
            raise ValueError(f"DLIR page: no row {label!r}")
        return cells[i + 1: i + 1 + n]

    i = next((k for k, c in enumerate(cells) if c.lower() == "calendar year"), None)
    if i is None:
        raise ValueError("DLIR page: no 'Calendar Year' row")
    years = []
    for c in cells[i + 1:]:
        if not re.fullmatch(r"20\d\d", c):
            break
        years.append(int(c))
    n = len(years)
    schedule = row("contribution rate schedule", n)
    wage = row("taxable wage base", n)
    new = row("tax rate for new employers", n)
    top = row("maximum tax rate", n)
    out = {}
    for k, y in enumerate(years):
        if not re.fullmatch(r"[A-H]", schedule[k]):
            raise ValueError(f"DLIR page: schedule {schedule[k]!r} for {y}")
        out[y] = {"schedule": schedule[k],
                  "wage_base": float(wage[k].replace("$", "").replace(",", "")),
                  "new_employer_rate": round(float(new[k].rstrip("%")) / 100, 6),
                  "max_rate": round(float(top[k].rstrip("%")) / 100, 6)}
    return dict(sorted(out.items()))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cache", type=Path, help="keep downloads here (reused if present)")
    args = ap.parse_args(argv)
    cache = args.cache or Path(tempfile.mkdtemp(prefix="ui_"))
    cache.mkdir(parents=True, exist_ok=True)

    def get(url: str, name: str) -> str:
        f = cache / name
        if not f.exists():
            f.write_bytes(_fetch(url))
        return f.read_text(errors="replace")

    monthly = parse_2112(get(f"{DOL}/ar2112.csv", "ar2112.csv"))
    payroll = parse_204(get(f"{DOL}/ar204.csv", "ar204.csv"))
    schedules = parse_dlir(get(DLIR_PAGE, "dlir_schedule.html"))
    # The DLIR page shows about seven years; earlier ones already bundled are kept.
    if OUT.exists():
        old = {int(y): v for y, v in json.loads(OUT.read_text()).get("schedules", {}).items()}
        schedules = dict(sorted({**old, **schedules}.items()))
    payload = {
        "source": "US Department of Labor ETA 2112 (net UI contributions, trust fund balance) and "
                  f"ETA 204 (payroll of taxable accounts), {DOL}/; Hawaii DLIR contribution rate "
                  f"schedules, {DLIR_PAGE}",
        "fetch_date": date.today().isoformat(),
        "units": "dollars; monthly by month of deposit; payroll for the calendar year",
        "monthly": monthly,
        "payroll": {str(y): v for y, v in payroll.items()},
        "schedules": {str(y): v for y, v in schedules.items()},
    }
    OUT.write_text(json.dumps(payload, separators=(",", ":")) + "\n")
    print(f"[ui] months {min(monthly)} to {max(monthly)}, payroll {min(payroll)}-{max(payroll)}, "
          f"schedules {min(schedules)}-{max(schedules)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
