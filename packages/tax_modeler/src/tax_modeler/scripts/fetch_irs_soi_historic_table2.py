#!/usr/bin/env python3
"""Fetch IRS SOI Historic Table 2 (state-level individual returns) for Hawaii.

Pulls the per-tax-year XLSX files from ``https://www.irs.gov/pub/irs-soi/``,
extracts the state-total EITC return count + dollar amount, and writes a
multi-year panel CSV at
``packages/tax_modeler/src/tax_modeler/data/external/irs_soi_hi_eitc_panel.csv``.

The panel feeds the empirical α calibration for ``eitc_poverty_alpha`` in
:mod:`tax_modeler.calibration.eitc_alpha_calibration`.

Source URL pattern
------------------
The IRS publishes Historic Table 2 (Table 2.  Individual Income and Tax
Data, by State and Size of Adjusted Gross Income) at

    https://www.irs.gov/pub/irs-soi/{YY}in12hi.xlsx

where ``YY`` is the two-digit tax year (e.g., ``22`` → TY 2022). The
``hi`` suffix is Hawaii's state code. Files for prior years are linked
from https://www.irs.gov/statistics/soi-tax-stats-historic-table-2.

Schema of the extracted CSV
---------------------------

================  ===========================================================
column            description
================  ===========================================================
``year``          tax year (integer, 4-digit)
``eitc_returns``  number of Hawaii returns claiming EITC (refundable +
                  non-refundable combined; row labeled "Earned income
                  credit:  [10] Number" in the source XLSX)
``eitc_dollars_  EITC dollar amount, in thousands of dollars (matching
thousands``       the row labeled "Earned income credit:  [10] Amount"
                  in the source XLSX)
================  ===========================================================

Usage
-----

  python -m tax_modeler.scripts.fetch_irs_soi_historic_table2 \\
      --years 2018-2022 --out packages/.../data/external/irs_soi_hi_eitc_panel.csv

Refreshing
~~~~~~~~~~

When the IRS publishes a new tax year (typically late autumn for the
prior calendar year), re-run with ``--years <previous>-<new>`` and
commit the updated CSV.
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

import pandas as pd

LOG = logging.getLogger("fetch_irs_soi_historic_table2")

_IRS_SOI_BASE_URL = "https://www.irs.gov/pub/irs-soi"
_DEFAULT_OUT_PATH = (
    Path(__file__).resolve().parents[1]
    / "data" / "external" / "irs_soi_hi_eitc_panel.csv"
)


def _fetch_one_year(year: int, cache_dir: Path) -> Path:
    """Download the IRS SOI Hawaii XLSX for one tax year, returning the local path."""
    import urllib.request
    yy = year % 100
    url = f"{_IRS_SOI_BASE_URL}/{yy:02d}in12hi.xlsx"
    cache_dir.mkdir(parents=True, exist_ok=True)
    out = cache_dir / f"soi_hi_{yy:02d}.xlsx"
    if out.exists() and out.stat().st_size > 1000:
        LOG.info("Cache hit: %s (%d bytes)", out, out.stat().st_size)
        return out
    LOG.info("Fetching %s", url)
    urllib.request.urlretrieve(url, out)
    LOG.info("Wrote %d bytes to %s", out.stat().st_size, out)
    return out


def _extract_eitc_row(xlsx_path: Path) -> tuple[float, float | None]:
    """Return ``(eitc_returns, eitc_dollars_thousands)`` from one IRS XLSX.

    Walks the first sheet looking for the rows labeled "Earned income
    credit:  [N] Number" and "Earned income credit:  [N] Amount" (the
    footnote ``N`` varies year-to-year). The state-total is in column
    1; subsequent columns are AGI-band breakdowns and are ignored.
    """
    df = pd.read_excel(xlsx_path, sheet_name=0, header=None)
    count_val: float | None = None
    amt_val: float | None = None
    for r in range(len(df)):
        v = df.iloc[r, 0]
        if pd.isna(v):
            continue
        text = str(v).strip()
        # Match the *top-line* EITC row (skip the by-children-count rows).
        # The top row starts with "Earned income credit:" and contains
        # either "Number" or "Amount" (typically also a footnote like [10]).
        if not text.startswith("Earned income credit:"):
            continue
        if "qualifying" in text.lower():
            continue  # skip by-children-count breakdown rows
        if "Number" in text and count_val is None:
            count_val = float(df.iloc[r, 1])
        elif "Amount" in text and amt_val is None:
            amt_val = float(df.iloc[r, 1])
    if count_val is None:
        raise ValueError(
            f"Could not locate the top-line EITC Number row in {xlsx_path}"
        )
    return count_val, amt_val


def fetch_panel(
    years: list[int],
    *,
    cache_dir: Path | None = None,
) -> pd.DataFrame:
    """Build the multi-year HI EITC panel.

    Parameters
    ----------
    years:
        List of tax years to include (typically ``range(2018, 2023)``
        once 2018→2022 SOI publications are available).
    cache_dir:
        Where to stash the downloaded XLSX files. Defaults to a
        ``.irs_soi_cache`` subdir of the current working directory.
        Files are re-used on subsequent runs to avoid re-downloads.

    Returns
    -------
    DataFrame with columns ``year``, ``eitc_returns``,
    ``eitc_dollars_thousands``, sorted by year ascending.
    """
    cache_dir = cache_dir or Path.cwd() / ".irs_soi_cache"
    rows: list[dict] = []
    for year in sorted(years):
        try:
            xlsx = _fetch_one_year(year, cache_dir)
            count, amount = _extract_eitc_row(xlsx)
            rows.append({
                "year": year,
                "eitc_returns": count,
                "eitc_dollars_thousands": amount,
            })
        except Exception as exc:  # noqa: BLE001 — best-effort per-year
            LOG.error("Skipping TY %d: %s", year, exc)
    if not rows:
        raise RuntimeError(
            "fetch_panel: no years successfully extracted. Check the IRS "
            "URL pattern and your network connection."
        )
    return pd.DataFrame(rows).sort_values("year").reset_index(drop=True)


def _parse_year_range(spec: str) -> list[int]:
    """Parse ``"2018-2022"`` → ``[2018, 2019, 2020, 2021, 2022]``."""
    if "-" in spec:
        lo, hi = spec.split("-", 1)
        return list(range(int(lo), int(hi) + 1))
    return [int(spec)]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--years", type=str, default="2018-2022",
                   help="Range like 2018-2022 (default) or single year.")
    p.add_argument("--out", type=Path, default=_DEFAULT_OUT_PATH,
                   help="Destination CSV path (default: bundled location).")
    p.add_argument("--cache-dir", type=Path, default=None,
                   help="Cache directory for downloaded XLSX files "
                        "(default: ./.irs_soi_cache).")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    years = _parse_year_range(args.years)
    panel = fetch_panel(years, cache_dir=args.cache_dir)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    panel.to_csv(args.out, index=False)
    LOG.info("Wrote %d rows to %s", len(panel), args.out)
    print(panel.to_string(index=False))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
