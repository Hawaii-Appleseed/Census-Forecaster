"""Walk-forward backtest harnesses.

Modules
-------
* `acs` — pseudo-out-of-sample evaluation of ACS projection methods.
  For each anchor year T, project forward h years, compare to actual
  T+h ACS print. Metrics: MAPE, bias, CI90 coverage.
* `cpi` — same pattern for BLS CPI series at monthly/bimonthly cadence.
  Sweeps a grid of (phi, cap, window) cells; supports `--calibrate`
  mode for the SE inflator κ.
"""
from .acs import (
    DEFAULT_METHODS as ACS_DEFAULT_METHODS,
)
from .acs import (
    BacktestRow as AcsBacktestRow,
)
from .acs import (
    BacktestSummary as AcsBacktestSummary,
)
from .acs import (
    project_carry_forward,
    project_linear_log,
    truncate_to_anchor,
)
from .acs import (
    run_backtest as run_acs_backtest,
)

__all__ = [
    "AcsBacktestRow",
    "AcsBacktestSummary",
    "ACS_DEFAULT_METHODS",
    "project_carry_forward",
    "project_linear_log",
    "truncate_to_anchor",
    "run_acs_backtest",
]
