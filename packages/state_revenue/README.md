# census-state-revenue

Every Hawaiʻi state tax as one ledger: DOTAX's collections by tax line and
fiscal year, with each line tiered by how far it can be modelled.

```python
from state_revenue import load_ledger

ledger = load_ledger()
ledger[2025].total                        # 11,456,335 (thousands)
ledger.series("general_excise")           # {2004: 1_900_377, ..., 2025: 4_630_396}
ledger.coverage(2025)                     # {'structural': 0.295, 'base_rate': ..., 'trend': ...}
```

- `lines.py`: the tax lines, their tiers (`structural`, `base_rate`, `trend`)
  and the code that models each, if any.
- `ledger.py`: the bundled collections, FY2004 on.
- `annual_report.py`: reads the "Tax Collections" table from an annual report.
- `scripts/refresh_annual_collections.py`: fetches the reports and rewrites
  `data/annual_collections.json` (needs `pdftotext`).

Scope, tiers and phases: `STATE_REVENUE_SCOPE.md` at the repository root.
