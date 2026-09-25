#!/usr/bin/env python3
"""CBPP-comparable EITC / CTC report by state, county, House and Senate district.

Produces four CSVs per run, mirroring the geographic granularity in CBPP
table 367 (https://apps.cbpp.org/program_participation/#table/367/eitc-and-ctc-claims):

    by_state.csv             — single-row state totals
    by_county.csv            — 4 Hawaii counties
    by_house_district.csv    — 51 HD rows
    by_senate_district.csv   — 25 SD rows  (matches CBPP's chamber)

When ``--compare-cbpp`` is set, additionally emits ``cbpp_vs_modeler_2022.csv``
diffing modeled vs. CBPP-published values per Senate district (the chamber
CBPP publishes for state legislative districts).

Examples
--------
    # TY 2025 projection
    python scripts/eitc_ctc_geo_report.py --tax-year 2025 \\
        --out reports/eitc_ctc_2025/

    # TY 2022 backtest with CBPP comparison
    python scripts/eitc_ctc_geo_report.py --tax-year 2022 --compare-cbpp \\
        --out reports/eitc_ctc_2022_backtest/

Notes
-----
* The script defaults to using the in-repo synthetic PUMS fixture so it
  runs out of the box on a fresh clone. Pass ``--pums-data-dir`` to point
  at the real Hawaii PUMS for production runs.
* PUMA→HD/SD assignment uses a deterministic SERIALNO hash; PUMA-level
  signal is preserved but within-PUMA variance is uniform.  See "Out of
  scope" in EITC_CTC_GEO_PLAN.md for a note on raking to IRS ZIP data.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Optional

import pandas as pd

LOG = logging.getLogger("eitc_ctc_geo_report")

# Repo-relative paths only (per CLAUDE.md).
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE_DIR = REPO_ROOT / "tests" / "tax_modeler" / "fixtures"
DEFAULT_CBPP_CACHE = (
    REPO_ROOT
    / "packages" / "tax_modeler" / "src" / "tax_modeler" / "data" / "raw"
    / "cbpp_table367_ty2022.csv"
)


# ---------------------------------------------------------------------------
# Pipeline glue
# ---------------------------------------------------------------------------

def _load_units(
    pums_data_dir: Optional[Path],
    use_fixture: bool,
) -> pd.DataFrame:
    """Build enriched, taxed tax units with HI EITC + geography assigned.

    Returns a DataFrame ready for projection or direct estimation.
    """
    from tax_modeler.pipeline import enrich_for_credits, compute_base_tax
    from tax_modeler.units.constructor import TaxUnitConstructor
    from tax_modeler.analysis.puma_crosswalk import assign_geography
    from tax_modeler.credits.hi_eitc import compute_hi_eitc_for_units

    if use_fixture or pums_data_dir is None:
        persons_path = DEFAULT_FIXTURE_DIR / "synthetic_pums_persons.parquet"
        households_path = DEFAULT_FIXTURE_DIR / "synthetic_pums_households.parquet"
        if not persons_path.exists() or not households_path.exists():
            raise FileNotFoundError(
                f"Synthetic PUMS fixture missing at {DEFAULT_FIXTURE_DIR}. "
                "Pass --pums-data-dir for real data, or rebuild via "
                "tests/tax_modeler/fixtures/_build_fixture.py"
            )
        persons = pd.read_parquet(persons_path)
        households = pd.read_parquet(households_path)
        LOG.info("Loaded synthetic PUMS fixture: %d persons, %d households",
                 len(persons), len(households))
    else:
        # Real Hawaii PUMS: parquet preferred, fall back to CSV.
        for ext in ("parquet", "csv"):
            p = pums_data_dir / f"psam_p15.{ext}"
            h = pums_data_dir / f"psam_h15.{ext}"
            if p.exists() and h.exists():
                reader = pd.read_parquet if ext == "parquet" else pd.read_csv
                persons = reader(p)
                households = reader(h)
                LOG.info("Loaded PUMS from %s (%s): %d persons, %d hhs",
                         pums_data_dir, ext, len(persons), len(households))
                break
        else:
            raise FileNotFoundError(
                f"No psam_p15.{{parquet,csv}} found in {pums_data_dir}"
            )
        from tax_modeler.loaders.pums_loader import PUMSDataLoader
        _coalesce = PUMSDataLoader(data_dir=pums_data_dir)._coalesce_puma
        households = _coalesce(households)
        persons = _coalesce(persons)

    ctor = TaxUnitConstructor(
        persons.copy(), households.copy(),
        use_soi_calibration=False, progress_bar=False,
    )
    units = ctor.create_rule_based_units(parallel=False)
    units = enrich_for_credits(units)
    units = assign_geography(units)
    return units


def _build_units_for_tax_year(
    units: pd.DataFrame,
    tax_year: int,
    project: bool,
) -> pd.DataFrame:
    """Compute base tax + HI EITC for a given year.

    For ``tax_year == base_year`` (the construction vintage), runs
    compute_base_tax directly.  Otherwise, projects forward via
    project_tax_units_forward, which rebuilds Hawaii tax + federal credits
    using year-specific parameters.
    """
    from tax_modeler.pipeline import compute_base_tax
    from tax_modeler.credits.hi_eitc import compute_hi_eitc_for_units

    if project:
        from tax_modeler.projection.tax_unit_projector import project_tax_units_forward
        # The projector rebuilds hi_*, ctc_*, eitc_* columns internally for
        # the target year; supply units pre-enriched (no need for compute_base_tax).
        out = project_tax_units_forward(units, target_year=tax_year)
    else:
        # Direct compute at the construction year.
        out = compute_base_tax(units, tax_year=tax_year)

    # HI EITC depends on federal eitc_amount; recompute either way, at the
    # tax year's law (20% non-refundable through TY2022; 40% refundable
    # TY2023-2027; 20% refundable from TY2028). Without the year it used the
    # 40% default, so the TY2022 backtest overstated the state EITC ~3x.
    out = compute_hi_eitc_for_units(out, tax_year=tax_year)
    return out


# ---------------------------------------------------------------------------
# Geographic rollups
# ---------------------------------------------------------------------------

# CBPP-comparable column set for every geography level.
_REPORT_COLUMNS = [
    "weighted_filers",
    "filers_receiving_eitc",
    "total_eitc_dollars",
    "filers_receiving_ctc",
    "total_ctc_dollars",
    "refundable_ctc_dollars",
    "hi_eitc_dollars",
]


def _aggregate(df: pd.DataFrame, group_col: Optional[str]) -> pd.DataFrame:
    """Population-weighted aggregation with CBPP-comparable columns."""
    if group_col is None:
        groups = [("__state__", df)]
    else:
        # groupby(dropna=False) on a key with NaN values builds an internal
        # categorical grouper whose group index includes NaN, which raises
        # "Categorical categories cannot be null" on the pandas resolved for
        # Python 3.10 (3.12's pandas tolerates it). Cast off categorical and
        # fill NaN with a sentinel so there is no null group key on any version
        # (previously-missing rows aggregate under "Unknown", same totals).
        gcol = df[group_col]
        if isinstance(gcol.dtype, pd.CategoricalDtype):
            gcol = gcol.astype(object)
        gcol = gcol.fillna("Unknown")
        groups = list(df.groupby(gcol, observed=True))

    rows = []
    for key, g in groups:
        w = g["weight"].astype(float)
        eitc_eligible = g.get("eitc_eligible", g["eitc_amount"] > 0).astype(bool)
        ctc_eligible = g["ctc_total"] > 0
        row = {
            "weighted_filers":         float(w.sum()),
            "filers_receiving_eitc":   float(w[eitc_eligible].sum()),
            "total_eitc_dollars":      float((g["eitc_amount"] * w).sum()),
            "filers_receiving_ctc":    float(w[ctc_eligible].sum()),
            "total_ctc_dollars":       float((g["ctc_total"] * w).sum()),
            "refundable_ctc_dollars":  float((g["ctc_refundable"] * w).sum()),
            "hi_eitc_dollars":         float((g.get("hi_eitc_amount", 0.0) * w).sum()),
        }
        if group_col is not None:
            row = {group_col: key, **row}
        rows.append(row)

    out = pd.DataFrame(rows, columns=([group_col] if group_col else []) + _REPORT_COLUMNS)
    if group_col is not None:
        # Filter null/zero district codes (HD/SD = 0 → unmapped PUMA).
        if group_col in {"house_district", "senate_district"}:
            out = out[(out[group_col].notna()) & (out[group_col] != 0)]
        out = out.sort_values(group_col).reset_index(drop=True)
    return out


# ---------------------------------------------------------------------------
# CBPP table 367 fetch + compare
# ---------------------------------------------------------------------------

def _try_fetch_cbpp_table367(cache_path: Path) -> Optional[pd.DataFrame]:
    """Attempt to download CBPP table 367 senate-district CSV.

    On failure (network down, schema change), falls back to the cached
    copy at ``cache_path``.  Returns None when neither is available.
    """
    if cache_path.exists():
        LOG.info("Using cached CBPP table 367 at %s", cache_path)
        try:
            return pd.read_csv(cache_path)
        except Exception as exc:  # noqa: BLE001
            LOG.warning("Cached CBPP table 367 unreadable: %s", exc)

    url = "https://apps.cbpp.org/program_participation/data/getSpreadsheetByID&table_id=367"
    try:
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = resp.read()
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(data)
        LOG.info("Downloaded CBPP table 367 → %s", cache_path)
        return pd.read_csv(cache_path)
    except Exception as exc:  # noqa: BLE001
        LOG.warning("CBPP fetch failed (%s); no comparison possible.", exc)
        return None


def _compare_against_cbpp(
    senate_df: pd.DataFrame,
    cbpp_df: pd.DataFrame,
) -> pd.DataFrame:
    """Build a side-by-side delta table for senate districts.

    CBPP's table-367 spreadsheet has shifted column names across vintages;
    this routine accepts a few common headers and normalises to a fixed
    schema before computing percentage deltas.
    """
    # Heuristically locate columns on the CBPP frame.
    def _find(cands, df):
        for c in cands:
            for actual in df.columns:
                if c.lower() in str(actual).lower():
                    return actual
        return None

    sd_col = _find(["senate", "district", "sld"], cbpp_df)
    eitc_n = _find(["eitc returns", "n eitc", "filers receiving eitc"], cbpp_df)
    eitc_d = _find(["eitc dollars", "eitc amount", "total eitc"], cbpp_df)
    ctc_d  = _find(["ctc dollars", "ctc amount", "total ctc"], cbpp_df)

    if sd_col is None:
        LOG.warning("Could not identify CBPP senate-district column; comparison skipped.")
        return pd.DataFrame()

    cbpp = cbpp_df.rename(columns={sd_col: "senate_district"}).copy()
    cbpp["senate_district"] = pd.to_numeric(cbpp["senate_district"], errors="coerce")
    cbpp = cbpp.dropna(subset=["senate_district"])
    cbpp["senate_district"] = cbpp["senate_district"].astype(int)

    keep = ["senate_district"]
    if eitc_n is not None:
        cbpp = cbpp.rename(columns={eitc_n: "cbpp_eitc_returns"})
        keep.append("cbpp_eitc_returns")
    if eitc_d is not None:
        cbpp = cbpp.rename(columns={eitc_d: "cbpp_eitc_dollars"})
        keep.append("cbpp_eitc_dollars")
    if ctc_d is not None:
        cbpp = cbpp.rename(columns={ctc_d: "cbpp_ctc_dollars"})
        keep.append("cbpp_ctc_dollars")

    merged = senate_df.merge(cbpp[keep], on="senate_district", how="outer")

    def _pct_delta(model: str, ref: str, label: str) -> None:
        if ref in merged.columns:
            merged[label] = (merged[model] - merged[ref]) / merged[ref].replace(0, pd.NA) * 100

    _pct_delta("filers_receiving_eitc",  "cbpp_eitc_returns",  "delta_pct_eitc_returns")
    _pct_delta("total_eitc_dollars",     "cbpp_eitc_dollars",  "delta_pct_eitc_dollars")
    _pct_delta("total_ctc_dollars",      "cbpp_ctc_dollars",   "delta_pct_ctc_dollars")
    return merged


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv: Optional[list] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--tax-year", type=int, required=True,
                   help="Tax year (2022, 2023, 2024, or 2025).")
    p.add_argument("--out", type=Path, required=True,
                   help="Output directory (created if absent). Repo-relative paths recommended.")
    p.add_argument("--compare-cbpp", action="store_true",
                   help="Fetch CBPP table 367 (TY 2022) and emit cbpp_vs_modeler_2022.csv.")
    p.add_argument("--pums-data-dir", type=Path, default=None,
                   help="Directory containing psam_h15.{parquet,csv} and psam_p15.{...}. "
                        "Defaults to the in-repo synthetic fixture.")
    p.add_argument("--use-fixture", action="store_true",
                   help="Force use of the synthetic PUMS fixture even if --pums-data-dir is set.")
    p.add_argument("--apply-takeup", action="store_true",
                   help="Apply IRS-anchored take-up imputation for EITC + ACTC (default off).")
    p.add_argument("--takeup-year", type=int, default=2022,
                   help="IRS caseload year for take-up benchmark (default 2022).")
    p.add_argument("--reweight-eitc-by-children", action=argparse.BooleanOptionalAction, default=None,
                   help="Lift short 1-/2-child EITC buckets to IRS Hawaii counts before take-up "
                        "(default: on for real PUMS, off for the fixture).")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv: Optional[list] = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.tax_year not in {2022, 2023, 2024, 2025}:
        LOG.error("--tax-year must be one of {2022, 2023, 2024, 2025}; got %d", args.tax_year)
        return 2

    args.out.mkdir(parents=True, exist_ok=True)

    # 1. Build base units (PUMS → enriched + geography assigned).
    base_units = _load_units(args.pums_data_dir, args.use_fixture)

    # 2. Compute year-specific tax + credits. The PUMS construction year is
    #    fixed; if the requested year differs, project forward.
    PUMS_CONSTRUCTION_YEAR = 2022
    project_required = args.tax_year != PUMS_CONSTRUCTION_YEAR
    units = _build_units_for_tax_year(
        base_units, tax_year=args.tax_year, project=project_required,
    )

    # 3. Lift the short 1-/2-child EITC buckets to IRS Hawaii counts, as the
    #    poverty pipeline does by default (scripts/poverty_impact_report.py
    #    _apply_eitc_reweight). Without it real-PUMS runs produce ~62k EITC
    #    claimants against IRS's 84k (TY2022). Off for the synthetic fixture,
    #    whose few thousand weighted filers cannot be scaled to state counts.
    reweight = (args.reweight_eitc_by_children if args.reweight_eitc_by_children is not None
                else (args.pums_data_dir is not None and not args.use_fixture))
    if reweight:
        from tax_modeler.calibration.eitc_reweight import reweight_eitc_eligibles_by_children
        units, info = reweight_eitc_eligibles_by_children(units)
        LOG.info("EITC by-children reweight: tax-unit weight %.0f -> %.0f",
                 info["total_weight_before"], info["total_weight_after"])

    # 4. Optional take-up imputation.
    if args.apply_takeup:
        from tax_modeler.pipeline import apply_credit_takeup
        units = apply_credit_takeup(units, year=args.takeup_year, programs=("eitc", "actc"))

    # 5. Aggregate at four geography levels.
    by_state = _aggregate(units, group_col=None)
    by_county = _aggregate(units, group_col="county")
    by_hd = _aggregate(units, group_col="house_district")
    by_sd = _aggregate(units, group_col="senate_district")

    # 5. Emit CSVs.
    by_state.to_csv(args.out / "by_state.csv", index=False)
    by_county.to_csv(args.out / "by_county.csv", index=False)
    by_hd.to_csv(args.out / "by_house_district.csv", index=False)
    by_sd.to_csv(args.out / "by_senate_district.csv", index=False)
    LOG.info("Wrote 4 geography CSVs to %s", args.out)

    # 6. Top-level summary to stdout.
    s = by_state.iloc[0]
    print()
    print(f"=== TY {args.tax_year} state totals ===")
    print(f"  Weighted filers          : {s['weighted_filers']:>15,.0f}")
    print(f"  Filers receiving EITC    : {s['filers_receiving_eitc']:>15,.0f}")
    print(f"  Total EITC ($M)          : {s['total_eitc_dollars']/1e6:>15,.1f}")
    print(f"  HI state EITC ($M)       : {s['hi_eitc_dollars']/1e6:>15,.1f}")
    print(f"  Filers receiving CTC     : {s['filers_receiving_ctc']:>15,.0f}")
    print(f"  Total CTC ($M)           : {s['total_ctc_dollars']/1e6:>15,.1f}")
    print(f"  Refundable CTC ($M)      : {s['refundable_ctc_dollars']/1e6:>15,.1f}")

    # 7. CBPP comparison (TY 2022 vintage only).
    if args.compare_cbpp:
        if args.tax_year != 2022:
            LOG.warning("--compare-cbpp uses CBPP table 367 (TY 2022); proceeding "
                        "with comparison vs. requested TY %d output anyway.",
                        args.tax_year)
        cbpp = _try_fetch_cbpp_table367(DEFAULT_CBPP_CACHE)
        if cbpp is None or cbpp.empty:
            LOG.warning("No CBPP data available; skipping cbpp_vs_modeler_2022.csv")
        else:
            cmp_df = _compare_against_cbpp(by_sd, cbpp)
            cmp_path = args.out / "cbpp_vs_modeler_2022.csv"
            cmp_df.to_csv(cmp_path, index=False)
            LOG.info("Wrote CBPP comparison to %s", cmp_path)

            # Flag senate districts where any delta exceeds ±15%.
            for col in ("delta_pct_eitc_returns", "delta_pct_eitc_dollars",
                        "delta_pct_ctc_dollars"):
                if col not in cmp_df.columns:
                    continue
                bad = cmp_df.loc[cmp_df[col].abs() > 15, ["senate_district", col]]
                if not bad.empty:
                    print(f"\n[!] Senate districts with |{col}| > 15%:")
                    print(bad.to_string(index=False))

    return 0


if __name__ == "__main__":
    sys.exit(main())
