# census-state-revenue

Every Hawaiʻi state tax as one ledger: DOTAX's collections by tax line and
fiscal year, with each line tiered by how far it can be modelled.

```python
from state_revenue import load_ledger

ledger = load_ledger()
ledger[2025].total                        # 11,456,335 (thousands)
ledger.series("general_excise")           # {2004: 1_900_377, ..., 2025: 4_630_396}
ledger.coverage(2025)                     # {'structural': 0.295, 'base_rate': ..., 'trend': ...}
load_ledger("general_fund")[2025].total   # 9,974,161

from state_revenue.baseline import build_baseline
b = build_baseline("all")                 # every line FY2026-FY2033, each with its source
b.total(2027), b.values[2027]["general_excise"].source     # (..., 'cor_scaled')
```

- `lines.py`: the tax lines, their tiers (`structural`, `base_rate`, `trend`)
  and the code that models each, if any.
- `ledger.py`: the bundled collections and the general fund's part, FY2004 on.
- `cor.py`: the Council on Revenues' line-item forecasts, every meeting since 2014.
- `forecast.py`: a line's forecast from its own history (damped trend or level).
- `backtest.py`: that forecast against the Council's, and alone on the lines it doesn't itemise.
- `general_excise.py`: the general excise tax by final purchaser (DBEDT's input-output
  table), and scorers for rate changes by activity and for exemptions (residents and
  visitors); `python -m state_revenue.general_excise`.
- `excise.py`: scorers for the transient accommodations tax, fuel and the barrel tax,
  cigarettes, the rental vehicle surcharge, and any line proportionally;
  `python -m state_revenue.excise`.
- `unemployment.py`: unemployment contributions as the rate schedule in force times
  taxable payroll, on the Department of Labor's series; `python -m state_revenue.unemployment`.
- `income_timing.py`: how a tax year's individual income tax splits across fiscal years,
  from DOTAX's monthly collections; `python -m state_revenue.income_timing`.
- `baseline.py`: every line by fiscal year, from the Council where it itemises a line, the model
  elsewhere.
- `annual_report.py`: reads the "Tax Collections" table from an annual report.
- `scripts/refresh_annual_collections.py`: fetches the reports and rewrites
  `data/annual_collections.json` (needs `pdftotext`).
- `scripts/refresh_get_inputs.py`: fetches the input-output table, HTA's visitor
  spending by category and DOTAX's GET exemptions claimed into `data/`.
- `scripts/refresh_excise_bases.py`: fetches DOTAX's calendar-year fuel and tobacco
  summaries and its transient accommodations base into `data/excise_bases.json`.

Every refresh script but `refresh_ui` (monthly data) takes `--if-new` (do nothing unless the source posted a report the
bundled file lacks); the monthly `refresh-data` workflow runs them that way.
- `scripts/refresh_ui.py`: fetches DOL's unemployment insurance files and DLIR's rate
  schedules into `data/ui_hawaii.json`.
- `scripts/refresh_cor_forecasts.py`: fetches the Council's attachments and rewrites
  `data/cor_line_forecasts.json`.

Scope, tiers and phases: `STATE_REVENUE_SCOPE.md` at the repository root.
