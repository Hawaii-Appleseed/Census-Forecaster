"""The bundled prices panel: load/save + return/momentum/vol transforms.

Schema of ``data/markets/prices_panel.json`` (mirrors ``bls_panel``):

::

    {
      "version": 1,
      "fetch_date": "YYYY-MM-DD",
      "start_year": 2005,
      "n_series": 13,
      "series": {"SPY": [{"year": 2005, "period": "M01",
                          "adj_close": 88.41, "volume": ...}, ...]},
      "provenance": {"SPY": "stooq", ...},
      "limitations": [...]
    }

Transforms operate on log adjusted closes. All are pure functions of the
loaded panel — no network.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import census_forecaster

from .client import MonthlyBar

_DATA_DIR = Path(census_forecaster.__file__).resolve().parent / "data" / "markets"
PRICES_PANEL_PATH = _DATA_DIR / "prices_panel.json"
MANIFEST_PATH = _DATA_DIR / "manifest.json"


def _period(month: int) -> str:
    return f"M{month:02d}"


def _month_index(year: int, month: int) -> int:
    """Months since year 0 — arithmetic-friendly month key."""
    return year * 12 + (month - 1)


@dataclass
class PricesPanel:
    """In-memory prices panel keyed by canonical symbol."""
    series: dict[str, list[MonthlyBar]]
    provenance: dict[str, str] = field(default_factory=dict)
    fetch_date: str = ""
    start_year: int = 2005

    # ----- (de)serialisation -----

    @classmethod
    def from_payload(cls, payload: dict) -> "PricesPanel":
        series: dict[str, list[MonthlyBar]] = {}
        for symbol, rows in payload.get("series", {}).items():
            bars = []
            for row in rows:
                period = row.get("period", "")
                month = int(period[1:]) if period.startswith("M") else 0
                if not 1 <= month <= 12:
                    continue
                bars.append(MonthlyBar(
                    year=int(row["year"]), month=month,
                    adj_close=float(row["adj_close"]),
                    volume=row.get("volume"),
                ))
            bars.sort(key=lambda b: (b.year, b.month))
            series[symbol] = bars
        return cls(
            series=series,
            provenance=dict(payload.get("provenance", {})),
            fetch_date=payload.get("fetch_date", ""),
            start_year=int(payload.get("start_year", 2005)),
        )

    def to_payload(self, *, limitations: Optional[list[str]] = None) -> dict:
        return {
            "version": 1,
            "fetch_date": self.fetch_date,
            "start_year": self.start_year,
            "n_series": len(self.series),
            "series": {
                symbol: [
                    {"year": b.year, "period": _period(b.month),
                     "adj_close": b.adj_close, "volume": b.volume}
                    for b in bars
                ]
                for symbol, bars in sorted(self.series.items())
            },
            "provenance": dict(sorted(self.provenance.items())),
            "limitations": limitations or [
                "Adjusted closes mix adjustment conventions by provenance: "
                "yfinance adjusts for splits AND dividends; Stooq adjusts "
                "for splits only. Log-return transforms are unaffected by "
                "level conventions except across dividend dates.",
                "The incomplete current calendar month is dropped at fetch "
                "time.",
                "Prices embed market-wide sentiment; see the market-signal "
                "screen limitations before treating any series as causal.",
            ],
        }

    # ----- transforms -----

    def symbols(self) -> list[str]:
        return sorted(self.series.keys())

    def bars(self, symbol: str) -> list[MonthlyBar]:
        try:
            return self.series[symbol]
        except KeyError:
            raise KeyError(f"symbol not in panel: {symbol!r}") from None

    def log_returns(self, symbol: str) -> list[tuple[int, int, float]]:
        """Consecutive-month log returns as (year, month, r) tuples.

        Gap months (missing prints) do NOT chain: a return is emitted
        only between calendar-adjacent bars.
        """
        bars = self.bars(symbol)
        out: list[tuple[int, int, float]] = []
        for prev, cur in zip(bars, bars[1:]):
            if _month_index(cur.year, cur.month) - _month_index(prev.year, prev.month) != 1:
                continue
            out.append((cur.year, cur.month,
                        math.log(cur.adj_close / prev.adj_close)))
        return out

    def momentum(
        self, symbol: str, months: int,
        as_of: Optional[tuple[int, int]] = None,
    ) -> Optional[float]:
        """Log price change over ``months`` months ending at ``as_of``.

        ``as_of`` is (year, month); defaults to the latest bar. Returns
        None when either endpoint is missing (no interpolation).
        """
        bars = self.bars(symbol)
        if not bars:
            return None
        by_index = {_month_index(b.year, b.month): b for b in bars}
        if as_of is None:
            end_idx = _month_index(bars[-1].year, bars[-1].month)
        else:
            end_idx = _month_index(*as_of)
        start_idx = end_idx - months
        end_bar = by_index.get(end_idx)
        start_bar = by_index.get(start_idx)
        if end_bar is None or start_bar is None:
            return None
        return math.log(end_bar.adj_close / start_bar.adj_close)

    def annualized_vol(
        self, symbol: str, window: int = 36,
        as_of: Optional[tuple[int, int]] = None,
    ) -> Optional[float]:
        """Annualised σ of monthly log returns over the trailing window.

        Sample standard deviation × √12. Returns None with fewer than
        12 in-window returns (too noisy to report).
        """
        returns = self.log_returns(symbol)
        if as_of is not None:
            cutoff = _month_index(*as_of)
            returns = [r for r in returns
                       if _month_index(r[0], r[1]) <= cutoff]
        tail = [r for _, _, r in returns[-window:]]
        n = len(tail)
        if n < 12:
            return None
        mean = sum(tail) / n
        var = sum((x - mean) ** 2 for x in tail) / (n - 1)
        return math.sqrt(var) * math.sqrt(12.0)

    def to_projection_points(self, symbol: str) -> list[dict]:
        """Bars as ``[{year, period, value}]`` — the shape
        ``bls.projection.project_forward_full`` consumes (Phase 2)."""
        return [
            {"year": b.year, "period": _period(b.month), "value": b.adj_close}
            for b in self.bars(symbol)
        ]


def load_prices_panel(path: Optional[Path] = None) -> PricesPanel:
    """Load the bundled (or a specified) prices panel JSON."""
    p = path or PRICES_PANEL_PATH
    if not p.exists():
        raise FileNotFoundError(
            f"prices panel not found at {p}; run "
            "`python -m lab.scripts.refresh_market_panel` first"
        )
    with open(p) as f:
        return PricesPanel.from_payload(json.load(f))


__all__ = [
    "PricesPanel",
    "load_prices_panel",
    "PRICES_PANEL_PATH",
    "MANIFEST_PATH",
]
