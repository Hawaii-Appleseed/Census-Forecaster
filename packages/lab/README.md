# census-lab

Research, validation and ablation tooling. The production chain
(`scripts/rerun_models.py` stages, `poverty_impact_report.py`,
`build_simulator_population.py`, `build_site.py`) does not import anything here.

| Subpackage | Contents |
|---|---|
| `lab.validation` | Benchmark cross-checks and calibration experiments (DOTAX Table 12A, hybrid/AGI/IPF calibration, policy cross-check, phase 9 smoke check) |
| `lab.markets` | Ticker tracker, causal screen, signal derivation, forecast board |
| `lab.scripts` | `compare_*_ablation`, `run_market_screen`, `refresh_market_panel` (run as `python -m lab.scripts.<name>`) |

Data these tools read and write stays under `census_forecaster/data/` (`markets/`,
`leading_indicators/`): the monthly `refresh_*` scripts and the ACS ML features use it too.

Install with the rest of the workspace:

```bash
uv sync --all-packages            # add --extra markets for yfinance
```
