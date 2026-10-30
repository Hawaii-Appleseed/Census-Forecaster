"""BEA API client (Regional dataset).

The BEA web service uses a single REST/JSON endpoint for all datasets:

    https://apps.bea.gov/api/data?UserID=<KEY>&method=<M>&datasetname=Regional&...

The "Regional" dataset covers state/metro personal income, employment,
GDP, and Regional Price Parities (RPP). For Census Forecaster we use
three series:

* `SAINC1 LineCode=3, GeoFips=15000`   — Hawaii per-capita personal income
* `MARPP  LineCode=1, GeoFips=46520`   — Urban Honolulu metro RPP all-items
* `SARPP  LineCode=3, GeoFips=15000`   — Hawaii state RPP services: rents

Caching mirrors `census_forecaster.acs.client.AcsClient`: a single
JSON file, atomic writes, offline-mode raise. The cache key includes
table, line, geo, and the requested year range so concurrent fetches
to different parameter sets don't collide.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BEA_API_URL = "https://apps.bea.gov/api/data"

# 1-second sleep between successive requests is well below the BEA
# rate limit (~100 calls/min) but defends against the API's burst-
# detection that triggers a 1-hour timeout if you exceed an undocumented
# threshold inside any 60-second window.
_DEFAULT_SLEEP_SECONDS: float = 1.0


class BeaApiError(RuntimeError):
    """Raised on a BEA API error response."""


def _default_cache_path() -> Path:
    base = os.environ.get("CENSUS_FORECASTER_CACHE_DIR")
    if base:
        return Path(base) / "bea.json"
    return Path.home() / ".cache" / "census-forecaster" / "bea.json"


DEFAULT_CACHE_PATH = _default_cache_path()


class BeaClient:
    """BEA Regional API client with file-backed JSON cache.

    Usage::

        client = BeaClient(api_key="<KEY>")
        rows = client.fetch_regional("SAINC1", line_code=3,
                                     geo_fips="15000", year="2010-2024")
        # rows: list of {"year": int, "value": float}
    """

    def __init__(
        self,
        api_key: str | None = None,
        cache_path: Path = DEFAULT_CACHE_PATH,
        offline: bool = False,
        sleep_seconds: float = _DEFAULT_SLEEP_SECONDS,
    ) -> None:
        self.api_key = api_key or os.environ.get("BEA_API_KEY", "")
        self.cache_path = Path(cache_path)
        self.offline = offline
        self.sleep_seconds = sleep_seconds
        self._last_call_at: float = 0.0
        self._cache: dict = self._load_cache()

    # ----- cache plumbing -----

    def _load_cache(self) -> dict:
        if not self.cache_path.exists():
            return {}
        try:
            with open(self.cache_path) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[bea] cache load failed ({exc}); starting fresh",
                  file=sys.stderr)
            return {}

    def _save_cache(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_path.with_suffix(self.cache_path.suffix + ".tmp")
        with open(tmp, "w") as f:
            json.dump(self._cache, f, indent=1, sort_keys=True)
        tmp.replace(self.cache_path)

    @staticmethod
    def _cache_key(
        table_name: str, line_code: int | str,
        geo_fips: str, year: str,
    ) -> str:
        return f"Regional|{table_name}|{line_code}|{geo_fips}|{year}"

    # ----- HTTP -----

    def _fetch_url(self, params: dict) -> dict:
        if self.offline:
            raise RuntimeError(
                "offline mode: refusing BEA network call",
            )
        # Throttle between calls
        elapsed = time.monotonic() - self._last_call_at
        if elapsed < self.sleep_seconds:
            time.sleep(self.sleep_seconds - elapsed)
        url = f"{BEA_API_URL}?{urllib.parse.urlencode(params)}"
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                body = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise BeaApiError(f"BEA HTTP {exc.code}: {exc.reason}") from exc
        finally:
            self._last_call_at = time.monotonic()
        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            raise BeaApiError(f"BEA returned non-JSON: {body[:200]}") from exc
        return data

    # ----- public API -----

    def fetch_regional(
        self,
        table_name: str,
        line_code: int | str,
        geo_fips: str,
        year: str = "ALL",
    ) -> list[dict]:
        """Fetch a Regional table series from BEA.

        Parameters
        ----------
        table_name : e.g. "SAINC1", "MARPP", "SARPP"
        line_code  : LineCode within the table (e.g. 3 for SAINC1 per-capita
                      personal income, 1 for MARPP all-items)
        geo_fips   : 5-digit state FIPS or MSA code
                      (e.g. "15000" Hawaii, "46520" Urban Honolulu MSA)
        year       : "ALL", or "YYYY", or "YYYY,YYYY,...", or "YYYY-YYYY"

        Returns
        -------
        list of {"year": int, "value": float} sorted ascending by year.
        Drops rows where DataValue can't be parsed as a float (BEA
        sentinels like "(D)" for suppressed disclosure are filtered out).
        """
        if not self.api_key:
            raise BeaApiError(
                "BEA API key is required. Pass `api_key=...` or set "
                "BEA_API_KEY in the environment."
            )
        key = self._cache_key(table_name, line_code, geo_fips, year)
        if key in self._cache:
            return self._cache[key]

        params = {
            "UserID": self.api_key,
            "method": "GetData",
            "datasetname": "Regional",
            "TableName": table_name,
            "LineCode": str(line_code),
            "GeoFips": geo_fips,
            "Year": year,
            "ResultFormat": "json",
        }
        payload = self._fetch_url(params)

        results = payload.get("BEAAPI", {}).get("Results", {})
        if "Error" in results:
            err = results["Error"]
            raise BeaApiError(f"BEA API error: {err}")

        data = results.get("Data", [])
        out: list[dict] = []
        for row in data:
            try:
                year_int = int(row.get("TimePeriod", ""))
            except (TypeError, ValueError):
                continue
            raw_value = row.get("DataValue", "")
            if not raw_value or raw_value in ("(D)", "(NA)", "(L)"):
                continue
            # BEA returns numeric values as comma-separated strings
            try:
                value = float(str(raw_value).replace(",", ""))
            except ValueError:
                continue
            out.append({"year": year_int, "value": value})

        out.sort(key=lambda r: r["year"])
        self._cache[key] = out
        self._save_cache()
        return out

    def get_parameter_values(
        self,
        target_parameter: str,
        table_name: str | None = None,
    ) -> list[dict]:
        """Fetch the valid values for a parameter (used to verify LineCodes).

        Useful as a `--verify` step before running the refresh script
        against a re-shuffled BEA schema.
        """
        if not self.api_key:
            raise BeaApiError("BEA API key is required.")
        params = {
            "UserID": self.api_key,
            "method": "GetParameterValuesFiltered",
            "datasetname": "Regional",
            "TargetParameter": target_parameter,
            "ResultFormat": "json",
        }
        if table_name is not None:
            params["TableName"] = table_name
        payload = self._fetch_url(params)
        results = payload.get("BEAAPI", {}).get("Results", {})
        return results.get("ParamValue", [])


__all__ = [
    "BEA_API_URL",
    "BeaClient",
    "BeaApiError",
    "DEFAULT_CACHE_PATH",
]
