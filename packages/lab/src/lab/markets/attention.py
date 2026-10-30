"""EXPERIMENTAL — Google Trends attention/demand terms for the causal screen.

Search interest is the demand-side complement to the ticker universe:
prices embed expectations, search embeds *intent* (booking a flight,
shopping for a house or a PV system), with zero publication lag. Two
mechanisms map onto this pipeline:

1. **Demand-proxy channel** (the one this module pre-registers): terms
   whose search volume plausibly leads a Hawaii monthly target, screened
   exactly like tickers (pre-registration → BH-FDR → 2020-robustness →
   forecaster ablation). A 2026-07 probe of four terms found
   ``flights to hawaii`` / ``hawaii vacation`` → HI unemployment with
   Granger p ≈ 2e-5 .. 2e-4 (n≈153) **when 2020 is excluded** — the
   inverse of the ticker pattern, where signals *died* on 2020
   exclusion: the COVID collapse masks rather than manufactures this
   relationship. Correlation signs are unstable across terms, so
   predictive content ≠ interpretable mechanism; the ablation gate does
   the deciding, as always.
2. **Attention→volatility channel** (tracker only, not implemented):
   Da, Engelberg & Gao (2011) — retail search attention predicts
   short-horizon volatility. Could condition the tracker's band
   multiplier. Direct ticker-SVI ("BOH stock") was considered and
   REJECTED: search volume for Hawaii microcaps is too thin to survive
   Trends' privacy thresholding.

Status: NOT wired into ``run_market_screen``, no bundled data, no CI
refresh. Blockers before promotion, beyond the usual screen gates:

* **Unofficial endpoint.** There is no stable public API; the fetcher
  drives the same widget endpoints the Trends UI uses (cookie → explore
  → token → multiline). It works keylessly today and breaks whenever
  Google changes the dance — treat every fetch as best-effort.
* **Sampling + normalization.** Values are 0-100 normalized per request
  window over a *sample* of searches: refetching the same query yields
  slightly different histories, and the whole history rescales whenever
  the in-window maximum shifts. Determinism requires a pinned window,
  averaging several fetches, and storing raw fetch provenance — the
  repo's usual byte-stable bundles are not achievable.
* **Bimonthly CPI targets cannot be Granger-tested.** The screen's
  all-lags-present rule needs monthly targets; the genuine Urban Hawaii
  CPI (bimonthly) fails it. (The old XLE→CPI screen results existed
  only because the mislabelled series was secretly monthly Los
  Angeles.) CPI-directed terms are xcorr-descriptive only.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional

# Month-key convention matches screen.month_index (year*12 + month).


@dataclass(frozen=True)
class TermSpec:
    """One pre-registered search term with its Hawaii-lead hypothesis."""
    term: str
    geo: str                 # 'US' (mainland intent) or 'US-HI' (local intent)
    hypothesis: str
    monthly_targets: tuple[str, ...]   # keys into screen.MONTHLY_TARGETS


# Pre-registered — the multiple-testing budget is capped here, exactly as
# in universe.TICKERS. Add terms by editing this tuple in a reviewed
# commit, never ad hoc at fetch time.
TERMS: tuple[TermSpec, ...] = (
    TermSpec(
        "flights to hawaii", "US",
        "Mainland flight-search intent leads visitor arrivals, which lead "
        "tourism employment.",
        ("HI_UNEMPLOYMENT",),
    ),
    TermSpec(
        "hawaii vacation", "US",
        "Broader trip-planning intent; same tourism-employment channel, "
        "earlier in the funnel than flight search.",
        ("HI_UNEMPLOYMENT",),
    ),
    TermSpec(
        "homes for sale hawaii", "US",
        "Mainland buyer interest leads transaction volume and measured "
        "prices/rents.",
        ("HONOLULU_ZHVI", "HONOLULU_ZORI"),
    ),
    TermSpec(
        "solar panels", "US-HI",
        "Local PV shopping intent leads REEC-creditable installations; "
        "CPI affinity is descriptive-only (bimonthly target).",
        (),
    ),
)

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
_BASE = "https://trends.google.com"
# Pinned request window: identical on every fetch so normalization is at
# least window-stable (see module docstring for what it cannot fix).
DEFAULT_TIME_WINDOW = "2010-01-01 2026-07-01"


def _get(url: str, cookie: Optional[str]) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": _UA, **({"Cookie": cookie} if cookie else {})})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", "replace")


def _session_cookie() -> Optional[str]:
    req = urllib.request.Request(_BASE + "/", headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        for header, value in resp.headers.items():
            if header.lower() == "set-cookie" and "NID" in value:
                return value.split(";")[0]
    return None


_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def fetch_term(
    spec: TermSpec,
    *,
    time_window: str = DEFAULT_TIME_WINDOW,
    cookie: Optional[str] = None,
    pause_s: float = 2.0,
) -> dict[int, float]:
    """Fetch one term's monthly interest as {month_index: value}.

    Best-effort against unofficial endpoints — raises URLError/KeyError
    on breakage; callers should treat failures as 'no data today', never
    retry-loop.
    """
    if cookie is None:
        cookie = _session_cookie()
    payload = {"comparisonItem": [{"keyword": spec.term, "geo": spec.geo,
                                   "time": time_window}],
               "category": 0, "property": ""}
    body = _get(_BASE + "/trends/api/explore?hl=en-US&tz=600&req="
                + urllib.parse.quote(json.dumps(payload)), cookie)
    widgets = json.loads(body.split("\n", 1)[1])["widgets"]
    ts = next(w for w in widgets if w["id"] == "TIMESERIES")
    time.sleep(pause_s)
    body2 = _get(_BASE + "/trends/api/widgetdata/multiline?hl=en-US&tz=600&req="
                 + urllib.parse.quote(json.dumps(ts["request"]))
                 + "&token=" + ts["token"], cookie)
    points = json.loads(body2.split("\n", 1)[1])["default"]["timelineData"]
    out: dict[int, float] = {}
    for p in points:
        if not p.get("value"):
            continue
        mon_name, _, year = p["formattedTime"].partition(" ")
        out[int(year) * 12 + _MONTHS.index(mon_name) + 1] = float(p["value"][0])
    return out


def fetch_all(
    *,
    time_window: str = DEFAULT_TIME_WINDOW,
    pause_s: float = 2.0,
) -> dict[str, dict[int, float]]:
    """Fetch every pre-registered term. Keys are '<term>|<geo>'."""
    cookie = _session_cookie()
    out: dict[str, dict[int, float]] = {}
    for spec in TERMS:
        out[f"{spec.term}|{spec.geo}"] = fetch_term(
            spec, time_window=time_window, cookie=cookie, pause_s=pause_s)
        time.sleep(pause_s)
    return out


__all__ = ["TermSpec", "TERMS", "fetch_term", "fetch_all",
           "DEFAULT_TIME_WINDOW"]
