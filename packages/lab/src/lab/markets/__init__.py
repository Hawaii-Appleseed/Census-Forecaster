"""Market signals — ETF/stock tracking and Hawaii leading-indicator screening.

Subpackage layout:

* ``universe``  — the pre-registered ticker universe with per-ticker
  hypotheses mapping each symbol to the ACS indicators it plausibly leads.
* ``client``    — monthly price-history fetcher (yfinance primary when
  installed, Stooq CSV fallback via stdlib; no API keys).
* ``panel``     — the bundled prices panel: load/save, log returns,
  momentum, and volatility transforms.
* ``report``    — CLI tracker: per-ticker status table + CSV output.

Phase 2 adds ``trend`` (ticker forecasts) and ``screen`` (lead-lag /
Granger causal screen); Phase 3 adds ``signals`` (annual leading-indicator
derivation feeding ``acs.ml_features``).

This subpackage is NOT part of the Housing-Affordability-Tracker
cherry-pick (which covers only ``acs/projection.py`` and ``kalman/``).
"""
from .universe import TICKERS, TickerSpec

__all__ = ["TICKERS", "TickerSpec"]
