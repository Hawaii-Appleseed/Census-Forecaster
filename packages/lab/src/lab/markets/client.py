"""Monthly price-history fetcher (yfinance → Yahoo chart API → Stooq).

Three free, keyless sources, tried in order:

* **yfinance** — unofficial Yahoo Finance wrapper. Best-quality adjusted
  closes (splits + dividends) but breaks periodically when Yahoo changes
  its endpoints. Optional dependency (``markets`` extra); imported lazily.
* **Yahoo chart API** — the same upstream yfinance wraps
  (``query1.finance.yahoo.com/v8/finance/chart``), called directly via
  stdlib urllib. Covers the common yfinance failure mode (package/parsing
  breakage) without adding a dependency; shares Yahoo's availability.
* **Stooq** — public CSV endpoint ``https://stooq.com/q/d/l/?s={sym}&i=m``
  (monthly OHLCV, split-adjusted). As of 2026-07 Stooq fronts this with a
  JavaScript proof-of-work challenge that blocks headless clients, so it
  rarely succeeds — kept as a last resort in case the challenge is lifted.

Caching mirrors ``bls/client.py``'s daily-snapshot approach rather than
``bea/client.py``'s permanent cache: market data must pick up new months
on each monthly refresh, so cache files are keyed by symbol *and* fetch
date (``{symbol}_{YYYY-MM-DD}.json``). Same-day re-runs are free; the
next day refetches.

The current (incomplete) calendar month is always dropped — the refresh
workflow runs on the 5th, and a 3-trading-day "month" would distort
momentum and volatility transforms.
"""
from __future__ import annotations

import csv
import io
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

STOOQ_URL_TMPL = "https://stooq.com/q/d/l/?s={symbol}&i=m"
YAHOO_CHART_URL_TMPL = (
    "https://query1.finance.yahoo.com/v8/finance/chart/"
    "{symbol}?interval=1mo&range=max"
)


class MarketDataError(RuntimeError):
    """Raised when a symbol's history cannot be fetched from any source."""


@dataclass(frozen=True)
class MonthlyBar:
    """One month-end observation for one symbol."""
    year: int
    month: int          # 1-12
    adj_close: float
    volume: Optional[float] = None


def _default_cache_dir() -> Path:
    base = os.environ.get("CENSUS_FORECASTER_CACHE_DIR")
    if base:
        return Path(base) / "markets"
    return Path.home() / ".cache" / "census-forecaster" / "markets"


def _fetch_url(url: str, retries: int = 3, backoff: float = 2.0,
               timeout: float = 30.0) -> bytes:
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "census-forecaster/1.0"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as exc:  # noqa: BLE001 — retry any transport error
            last_exc = exc
            if attempt < retries - 1:
                time.sleep(backoff * (attempt + 1))
    raise MarketDataError(f"fetch failed after {retries} tries: {url}") from last_exc


def _parse_stooq_csv(raw: bytes, start_year: int) -> list[MonthlyBar]:
    """Parse Stooq monthly CSV (Date,Open,High,Low,Close,Volume) to bars.

    The Date column is the last trading day of each month (YYYY-MM-DD).
    Stooq returns ``Close`` split-adjusted. Rows before ``start_year``
    and rows with unparseable Close are dropped.
    """
    text = raw.decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    bars: list[MonthlyBar] = []
    for row in reader:
        d = (row.get("Date") or "").strip()
        if len(d) < 7:
            continue
        try:
            year, month = int(d[:4]), int(d[5:7])
            close = float(row["Close"])
        except (KeyError, TypeError, ValueError):
            continue
        if year < start_year or close <= 0:
            continue
        vol_raw = (row.get("Volume") or "").strip()
        try:
            volume: Optional[float] = float(vol_raw) if vol_raw else None
        except ValueError:
            volume = None
        bars.append(MonthlyBar(year=year, month=month,
                               adj_close=close, volume=volume))
    bars.sort(key=lambda b: (b.year, b.month))
    return bars


def _fetch_stooq(stooq_symbol: str, start_year: int) -> list[MonthlyBar]:
    url = STOOQ_URL_TMPL.format(symbol=stooq_symbol)
    raw = _fetch_url(url)
    bars = _parse_stooq_csv(raw, start_year)
    if not bars:
        # Stooq returns "No data" (or an empty body) for unknown symbols
        # with HTTP 200, so an empty parse IS the failure signal.
        raise MarketDataError(
            f"stooq returned no parseable rows for {stooq_symbol!r}"
        )
    return bars


def _parse_yahoo_chart(payload: dict, start_year: int) -> list[MonthlyBar]:
    """Parse a Yahoo v8 chart JSON payload to monthly bars.

    Uses ``indicators.adjclose`` (splits + dividends adjusted) when
    present, else raw ``quote.close``. Timestamps are epoch seconds at
    the month's first trading session; converting in UTC keeps the
    year/month stable (sessions open mid-day local time).
    """
    from datetime import datetime, timezone

    try:
        result = payload["chart"]["result"][0]
        timestamps = result["timestamp"]
        quote = result["indicators"]["quote"][0]
    except (KeyError, IndexError, TypeError) as exc:
        err = (payload.get("chart") or {}).get("error")
        raise MarketDataError(f"yahoo chart payload malformed: {err or exc}")
    adj = (result["indicators"].get("adjclose") or [{}])[0].get("adjclose")
    closes = adj if adj else quote.get("close", [])
    volumes = quote.get("volume", [])

    bars: list[MonthlyBar] = []
    seen: set[tuple[int, int]] = set()
    for i, ts in enumerate(timestamps):
        close = closes[i] if i < len(closes) else None
        if close is None or close != close or close <= 0:
            continue
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        if dt.year < start_year:
            continue
        key = (dt.year, dt.month)
        if key in seen:  # Yahoo appends a live duplicate of the last month
            continue
        seen.add(key)
        vol = volumes[i] if i < len(volumes) else None
        bars.append(MonthlyBar(
            year=dt.year, month=dt.month, adj_close=float(close),
            volume=float(vol) if vol is not None else None,
        ))
    bars.sort(key=lambda b: (b.year, b.month))
    return bars


def _fetch_yahoo_chart(symbol: str, start_year: int) -> list[MonthlyBar]:
    """Fetch via Yahoo's v8 chart endpoint (stdlib only)."""
    url = YAHOO_CHART_URL_TMPL.format(symbol=urllib.parse.quote(symbol))
    raw = _fetch_url(url)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MarketDataError(
            f"yahoo chart returned non-JSON for {symbol!r}"
        ) from exc
    bars = _parse_yahoo_chart(payload, start_year)
    if not bars:
        raise MarketDataError(f"yahoo chart returned no rows for {symbol!r}")
    return bars


def _fetch_yfinance(symbol: str, start_year: int) -> list[MonthlyBar]:
    """Fetch via yfinance (optional dependency; ImportError propagates)."""
    import yfinance as yf  # deferred: only needed on this path

    hist = yf.Ticker(symbol).history(
        start=f"{start_year}-01-01", interval="1mo", auto_adjust=True,
    )
    bars: list[MonthlyBar] = []
    for ts, row in hist.iterrows():
        close = float(row.get("Close", float("nan")))
        if close != close or close <= 0:  # NaN or nonsense
            continue
        vol = row.get("Volume")
        volume = float(vol) if vol == vol and vol is not None else None
        bars.append(MonthlyBar(year=int(ts.year), month=int(ts.month),
                               adj_close=close, volume=volume))
    if not bars:
        raise MarketDataError(f"yfinance returned no rows for {symbol!r}")
    bars.sort(key=lambda b: (b.year, b.month))
    return bars


def _drop_incomplete_month(bars: list[MonthlyBar],
                           today: Optional[date] = None) -> list[MonthlyBar]:
    t = today or date.today()
    return [b for b in bars if (b.year, b.month) < (t.year, t.month)]


# ----- cache plumbing (daily snapshots, mirrors bls/client.py) -----

def _cache_path(cache_dir: Path, symbol: str,
                today: Optional[date] = None) -> Path:
    t = today or date.today()
    return cache_dir / f"{symbol}_{t.isoformat()}.json"


def _load_cached(path: Path) -> Optional[tuple[list[MonthlyBar], str]]:
    if not path.exists():
        return None
    try:
        with open(path) as f:
            payload = json.load(f)
        bars = [MonthlyBar(**b) for b in payload["bars"]]
        return bars, payload["provenance"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        print(f"[markets] cache load failed ({exc}); refetching",
              file=sys.stderr)
        return None


def _save_cached(path: Path, bars: list[MonthlyBar], provenance: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w") as f:
        json.dump(
            {"provenance": provenance,
             "bars": [b.__dict__ for b in bars]},
            f,
        )
    tmp.replace(path)


# ----- public API -----

def fetch_monthly_history(
    symbol: str,
    stooq_symbol: Optional[str] = None,
    *,
    start_year: int = 2005,
    cache_dir: Optional[Path] = None,
    offline: bool = False,
    source: str = "auto",
    today: Optional[date] = None,
) -> tuple[list[MonthlyBar], str]:
    """Fetch a symbol's monthly adjusted-close history.

    Parameters
    ----------
    symbol       : canonical symbol, e.g. "SPY".
    stooq_symbol : Stooq form (e.g. "spy.us"); defaults to
                   ``symbol.lower() + ".us"``.
    source       : "auto" (yfinance → yahoo_chart → stooq), or one of
                   "yfinance" / "yahoo_chart" / "stooq" to force a path.
    offline      : refuse network; serve today's cache or raise.
    today        : injectable clock for tests / determinism.

    Returns
    -------
    (bars, provenance) — bars sorted ascending, incomplete current month
    dropped; provenance is "yfinance", "yahoo_chart", or "stooq".
    """
    fetchers = {
        "yfinance": lambda: _fetch_yfinance(symbol, start_year),
        "yahoo_chart": lambda: _fetch_yahoo_chart(symbol, start_year),
        "stooq": lambda: _fetch_stooq(
            stooq_symbol or (symbol.lower() + ".us"), start_year),
    }
    if source == "auto":
        chain = ("yfinance", "yahoo_chart", "stooq")
    elif source in fetchers:
        chain = (source,)
    else:
        raise ValueError(f"unknown source: {source!r}")

    cdir = cache_dir or _default_cache_dir()
    cpath = _cache_path(cdir, symbol, today)

    cached = _load_cached(cpath)
    if cached is not None:
        return cached
    if offline:
        raise MarketDataError(
            f"offline mode: no cached history for {symbol} at {cpath}"
        )

    bars: Optional[list[MonthlyBar]] = None
    provenance = ""
    errors: list[str] = []

    for name in chain:
        try:
            bars = fetchers[name]()
            provenance = name
            break
        except Exception as exc:  # noqa: BLE001 — incl. ImportError
            errors.append(f"{name}: {exc}")

    if bars is None:
        raise MarketDataError(
            f"all sources failed for {symbol}: {'; '.join(errors)}"
        )

    bars = _drop_incomplete_month(bars, today)
    _save_cached(cpath, bars, provenance)
    return bars, provenance


__all__ = [
    "MonthlyBar",
    "MarketDataError",
    "STOOQ_URL_TMPL",
    "YAHOO_CHART_URL_TMPL",
    "fetch_monthly_history",
]
