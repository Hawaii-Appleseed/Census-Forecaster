"""Phase 9 end-to-end validation report.

Runs the three Phase 9 improvements on the bundled synthetic + real
CPS slice and prints a one-page summary suitable for a fiscal note's
methodology appendix.

Usage::

    python -m lab.validation.phase9_validation

Reports:
  1. **Real CPS donor matching**: weighted-mean MOOP, childcare,
     work-expense imputed onto the synthetic recipient frame.
  2. **Multi-marginal projection**: pre vs post shares for senior,
     HH-size, disability dimensions.
  3. **Per-PUMA poverty rate**: spread across PUMAs in the simulated
     baseline frame (single-PUMA on the synthetic fixture).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[5]


def _load_taxed_units():
    fixture_dir = REPO_ROOT / "tests" / "tax_modeler" / "fixtures"
    persons = pd.read_parquet(fixture_dir / "synthetic_pums_persons.parquet")
    households = pd.read_parquet(fixture_dir / "synthetic_pums_households.parquet")

    from tax_modeler import (
        compute_base_tax,
        enrich_for_benefits,
        enrich_for_credits,
    )
    from tax_modeler.units.constructor import TaxUnitConstructor

    ctor = TaxUnitConstructor(
        persons.copy(), households.copy(),
        use_soi_calibration=False, progress_bar=False,
    )
    units = ctor.create_rule_based_units(parallel=False)
    units = enrich_for_credits(units)
    units = enrich_for_benefits(units, persons, households)
    units = compute_base_tax(units)
    return units


def _check_donor_matching(units):
    from tax_modeler import (
        impute_childcare_expense,
        impute_moop,
        impute_work_expense,
    )
    out = impute_moop(units)
    out = impute_childcare_expense(out)
    out = impute_work_expense(out)
    w = out["weight"].fillna(0)

    def _wmean(col):
        return float((out[col] * w).sum() / w.sum())

    print("\n[1] Real CPS donor-matching imputation")
    print("    Weighted-mean per recipient:")
    print(f"      moop_amount:              ${_wmean('moop_amount'):>10,.0f}/yr")
    print(f"      childcare_expense_amount: ${_wmean('childcare_expense_amount'):>10,.0f}/yr")
    print(f"      work_expense_amount:      ${_wmean('work_expense_amount'):>10,.0f}/yr")
    return out


def _check_multi_marginal_projection(units):
    from tax_modeler import (
        project_demographics_forward,
    )
    out, result = project_demographics_forward(
        units, target_year=2040,
        dimensions=["senior", "hh_size", "disability"],
    )
    print("\n[2] Multi-marginal demographic projection (target_year=2040)")
    for dim, info in result.per_dim_results.items():
        target = info.get("target_shares", {})
        post = info.get("post_shares", {})
        skipped = info.get("skipped_empty_cells", [])
        print(f"    {dim}:")
        for cell in target:
            t = target[cell]
            p = post.get(cell, 0.0)
            print(f"      {cell:<12} target {t:>5.3f}  post {p:>5.3f}")
        if skipped:
            print(f"      skipped (empty cells): {skipped}")
    print(f"    Total weight preserved: "
          f"{out['weight'].sum():.1f} vs baseline {units['weight'].sum():.1f}")


def _check_puma_stratification(units):
    from tax_modeler import (
        compute_spm_resources,
        poverty_by_geography,
        threshold_for_units,
    )
    resources, _ = compute_spm_resources(units)
    thr = threshold_for_units(resources, year=2024)
    geo = poverty_by_geography(
        resources, resources_col="spm_resources",
        threshold=thr, geo_col="PUMA",
    )
    print("\n[3] Per-PUMA poverty rate (synthetic fixture has 1 PUMA)")
    print(geo[["count_weighted", "poverty_rate", "mean_resources_$"]].round(4).to_string())


def main(argv: list[str] | None = None) -> int:
    print("=" * 70)
    print("Phase 9 validation report — tax_modeler")
    print("=" * 70)
    units = _load_taxed_units()
    print(f"  Loaded {len(units):,} tax units, "
          f"{units['weight'].sum():,.0f} weighted filers")

    units_with_donors = _check_donor_matching(units)
    _check_multi_marginal_projection(units_with_donors)
    _check_puma_stratification(units_with_donors)
    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
