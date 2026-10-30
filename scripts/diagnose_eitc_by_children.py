#!/usr/bin/env python3
"""Decompose model EITC claimers by qualifying-child count vs IRS SOI Hawaii.

Read-only. Builds tax units (no SOI calibration), enriches, computes base
tax (federal EITC), then buckets EITC claimers by ``num_qualifying_children``
into 0 / 1 / 2 / 3+ and compares the *weighted* per-stratum counts to the
verified IRS SOI Historic Table 2 Hawaii TY2022 targets.

Why this exists
---------------
The headline metric "childless EITC claimer share" was being compared to a
~21% benchmark, but IRS SOI Historic Table 2 (Hawaii, 22in12hi.xlsx) puts the
actual HI childless share at **31.7%**. The pooled share is therefore the
wrong lens: it conflates a (small) childless over-count with a (large)
with-children under-count. This script exposes the per-stratum gap so we know
*which* strata the calibration/reweighting has to move, and by how much.

IRS SOI Historic Table 2, Hawaii, TY2022 (filed 2023)
  source: https://www.irs.gov/pub/irs-soi/22in12hi.xlsx
  field:  "Earned income credit with {no,one,two,three or more}
           qualifying children: Number of returns"
  (state counts rounded to nearest 10; buckets sum to the EITC total)

Usage
-----
    uv run python scripts/diagnose_eitc_by_children.py --source pums
    uv run python scripts/diagnose_eitc_by_children.py --source fixture
    uv run python scripts/diagnose_eitc_by_children.py --source pums --apply-takeup
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = REPO_ROOT / "tests" / "tax_modeler" / "fixtures"
DEFAULT_PUMS_DIR = REPO_ROOT / "packages" / "data" / "raw" / "pums_2024_1yr"

# IRS SOI Historic Table 2, Hawaii TY2022 — EITC returns by qualifying children.
# Keys are the 0/1/2/3+ stratum; "3+" folds 3 and 4+ together.
IRS_HI_TY2022 = {
    "0": 26_900,
    "1": 27_820,
    "2": 18_460,
    "3+": 11_790,
}
IRS_HI_TY2022_TOTAL = sum(IRS_HI_TY2022.values())  # 84,970


def _load_pums(source: str, pums_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    if source == "fixture":
        persons = pd.read_parquet(FIXTURE_DIR / "synthetic_pums_persons.parquet")
        households = pd.read_parquet(FIXTURE_DIR / "synthetic_pums_households.parquet")
        return persons, households

    for ext in ("parquet", "csv"):
        p = pums_dir / f"psam_p15.{ext}"
        h = pums_dir / f"psam_h15.{ext}"
        if p.exists() and h.exists():
            reader = pd.read_parquet if ext == "parquet" else pd.read_csv
            return reader(p), reader(h)
    raise FileNotFoundError(
        f"No psam_p15/psam_h15 (.parquet/.csv) found in {pums_dir}"
    )


def _weight(df: pd.DataFrame) -> np.ndarray:
    for col in ("weight", "weight_calibrated", "WGTP"):
        if col in df.columns:
            return df[col].fillna(0).to_numpy(dtype=float)
    return np.ones(len(df), dtype=float)


def _build_units(
    persons: pd.DataFrame, households: pd.DataFrame, tax_year: int, apply_takeup: bool
) -> pd.DataFrame:
    from tax_modeler.pipeline import compute_base_tax, enrich_for_credits
    from tax_modeler.units.constructor import TaxUnitConstructor

    ctor = TaxUnitConstructor(
        persons.copy(), households.copy(),
        use_soi_calibration=False, progress_bar=False,
    )
    units = ctor.create_rule_based_units(parallel=False)
    units = enrich_for_credits(units)
    # We only consume federal EITC here; Hawaii liability is irrelevant, so
    # use SD-only (NO_ITEMIZING) which TY>=2024 requires explicitly.
    ded = None
    if tax_year >= 2024:
        from tax_modeler.liability.hawaii import NO_ITEMIZING
        ded = NO_ITEMIZING
    units = compute_base_tax(units, deduction_params=ded, tax_year=tax_year)
    if apply_takeup:
        from tax_modeler.pipeline import apply_credit_takeup
        units = apply_credit_takeup(units, year=2022, programs=("eitc",))
    return units


def _build_units_production(
    persons: pd.DataFrame, households: pd.DataFrame, pums_dir: Path, tax_year: int,
    reweight_by_children: bool = False,
) -> pd.DataFrame:
    """Reproduce the production forecast build sequence through EITC take-up.

    Mirrors analyses/forecast_hi_eitc_revert_20.py: load -> project/base-tax ->
    IRS-anchored credit take-up -> HI EITC. Unlike _build_units this applies
    the pooled IRS take-up truncation + dollar scaling exactly as the real
    forecast does, so the by-children mix here is the *production* mix, not a
    clean-room eligibility snapshot. tax_year != 2022 triggers forward
    projection (income grown to the target year).

    ``reweight_by_children`` toggles lever 3a — the surgical EITC by-children
    reweight applied *between* base-tax and take-up, so short with-children
    eligible buckets reach their IRS targets before truncation."""
    import poverty_impact_report as pir

    base_units, _persons = pir._load_units(pums_dir, use_fixture=False)
    project_required = tax_year != 2022
    units = pir._build_units_for_tax_year(base_units, tax_year, project=project_required)
    if reweight_by_children:
        from tax_modeler.calibration.eitc_reweight import (
            reweight_eitc_eligibles_by_children,
        )
        units, info = reweight_eitc_eligibles_by_children(units)
        _print_reweight_info(info)
    units = pir._apply_credit_takeup(units, tax_year=tax_year)
    units = pir._apply_hi_eitc(units, tax_year=tax_year)
    return units


def _print_reweight_info(info: dict) -> None:
    """Pretty-print the lever-3a reweight diagnostics (per-bucket + drift)."""
    before = info["total_weight_before"]
    after = info["total_weight_after"]
    drift = after - before
    print("=" * 72)
    print("LEVER 3a — EITC by-children reweight (eligible pool, pre-take-up)")
    print("=" * 72)
    print(f"  {'kids':<5} {'n units':>8} {'wt before':>12} {'wt after':>12} "
          f"{'factor':>8} {'IRS target':>12}")
    print("  " + "-" * 62)
    for bucket in sorted(info["buckets"]):
        b = info["buckets"][bucket]
        label = "3+" if bucket >= 3 else str(bucket)
        print(f"  {label:<5} {b['n_units']:>8,} {b['before']:>12,.0f} "
              f"{b['after']:>12,.0f} {b['factor']:>8.3f} {b['target']:>12,.0f}")
    if not info["buckets"]:
        print("  (no bucket was below target; nothing up-weighted)")
    print("  " + "-" * 62)
    print(f"  total tax-unit weight: {before:,.0f} -> {after:,.0f}  "
          f"({drift:+,.0f}, {(100*drift/before if before else 0):+.2f}%)")
    print()


def _bucket(nqc: int) -> str:
    if nqc <= 0:
        return "0"
    if nqc == 1:
        return "1"
    if nqc == 2:
        return "2"
    return "3+"


def _report(units: pd.DataFrame, scale_to_irs_total: bool) -> None:
    w = _weight(units)
    claimers = units["eitc_amount"].fillna(0).to_numpy() > 0
    nqc = (
        units.get("num_qualifying_children", pd.Series(0, index=units.index))
        .fillna(0)
        .astype(int)
        .to_numpy()
    )

    buckets = ["0", "1", "2", "3+"]
    model_w = {b: 0.0 for b in buckets}
    model_n = {b: 0 for b in buckets}
    for is_claimer, n, wt in zip(claimers, nqc, w, strict=False):
        if not is_claimer:
            continue
        b = _bucket(int(n))
        model_w[b] += float(wt)
        model_n[b] += 1

    total_model_w = sum(model_w.values())
    total_model_n = sum(model_n.values())

    # Optional: rescale the model to the IRS grand total so the comparison
    # isolates the *mix* (share) from the *level* (the ~84k vs ~68k undercount).
    scale = (IRS_HI_TY2022_TOTAL / total_model_w) if (scale_to_irs_total and total_model_w) else 1.0

    print("=" * 72)
    print("EITC CLAIMERS BY QUALIFYING-CHILD COUNT  (weighted)")
    print("=" * 72)
    print(f"  model total EITC claimers (weighted):   {total_model_w:,.0f}")
    print(f"  model total EITC claimers (unweighted): {total_model_n:,}")
    print(f"  IRS SOI HI TY2022 total:                {IRS_HI_TY2022_TOTAL:,}")
    if total_model_w:
        gap = total_model_w - IRS_HI_TY2022_TOTAL
        print(f"  level gap (model - IRS):                {gap:+,.0f}  "
              f"({100 * gap / IRS_HI_TY2022_TOTAL:+.1f}%)")
    if scale_to_irs_total:
        print(f"  [mix view: model rescaled ×{scale:.3f} to IRS total]")
    print()

    hdr = f"  {'kids':<5} {'model wt':>12} {'model %':>9} {'IRS wt':>10} {'IRS %':>8} {'gap (m-IRS)':>14} {'ratio':>7}"
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for b in buckets:
        m_w = model_w[b] * scale
        m_share = (m_w / (total_model_w * scale)) if total_model_w else 0.0
        irs = IRS_HI_TY2022[b]
        irs_share = irs / IRS_HI_TY2022_TOTAL
        gap = m_w - irs
        ratio = (m_w / irs) if irs else float("nan")
        print(f"  {b:<5} {m_w:>12,.0f} {100*m_share:>8.1f}% {irs:>10,} "
              f"{100*irs_share:>7.1f}% {gap:>+14,.0f} {ratio:>7.2f}")

    print()
    # Childless-share headline both ways.
    childless_share = (model_w["0"] / total_model_w) if total_model_w else 0.0
    irs_childless_share = IRS_HI_TY2022["0"] / IRS_HI_TY2022_TOTAL
    with_children_model = total_model_w - model_w["0"]
    with_children_irs = IRS_HI_TY2022_TOTAL - IRS_HI_TY2022["0"]
    print("  HEADLINE")
    print(f"    childless share:      model {100*childless_share:.1f}%   "
          f"vs IRS HI {100*irs_childless_share:.1f}%")
    print(f"    childless count:      model {model_w['0']:,.0f}   "
          f"vs IRS HI {IRS_HI_TY2022['0']:,}   "
          f"(gap {model_w['0'] - IRS_HI_TY2022['0']:+,.0f})")
    print(f"    with-children count:  model {with_children_model:,.0f}   "
          f"vs IRS HI {with_children_irs:,}   "
          f"(gap {with_children_model - with_children_irs:+,.0f})")

    _generation_vs_eligibility(units, w, nqc)


def _generation_vs_eligibility(units: pd.DataFrame, w: np.ndarray, nqc: np.ndarray) -> None:
    """For each child-count stratum, separate 'unit not generated' from 'unit
    generated but income-disqualified'. Decides whether the with-children
    shortfall is a family-structure/generation problem or an income problem."""
    claimers = units["eitc_amount"].fillna(0).to_numpy() > 0
    inc_col = "earned_income" if "earned_income" in units.columns else (
        "income" if "income" in units.columns else None
    )
    inc = (
        units[inc_col].fillna(0).to_numpy() if inc_col
        else np.zeros(len(units))
    )

    print()
    print("=" * 72)
    print("GENERATION vs ELIGIBILITY  (all tax units with N qualifying children)")
    print("=" * 72)
    print(f"  income column used: {inc_col}")
    print(f"  {'kids':<5} {'all units wt':>13} {'eitc wt':>11} {'claim rate':>11} "
          f"{'med inc (all)':>14} {'med inc (non-clm)':>18}")
    print("  " + "-" * 76)
    for b, lo, hi in [("0", 0, 0), ("1", 1, 1), ("2", 2, 2), ("3+", 3, 99)]:
        sel = (nqc >= lo) & (nqc <= hi)
        all_w = float(w[sel].sum())
        clm = sel & claimers
        clm_w = float(w[clm].sum())
        non = sel & ~claimers
        rate = (clm_w / all_w) if all_w else 0.0
        med_all = float(np.median(inc[sel])) if sel.any() else 0.0
        med_non = float(np.median(inc[non])) if non.any() else 0.0
        print(f"  {b:<5} {all_w:>13,.0f} {clm_w:>11,.0f} {100*rate:>10.1f}% "
              f"{med_all:>14,.0f} {med_non:>18,.0f}")
    print()
    print("  Reading: if 'all units wt' for the 1/2-kid rows already falls short of")
    print("  IRS EITC counts, the families aren't generated (structure problem). If")
    print("  'all units' is ample but 'claim rate' is low with high non-claimer")
    print("  income, they're generated-but-income-disqualified (income problem).")

    if "filing_status" not in units.columns:
        return
    print()
    print("=" * 72)
    print("FILING-STATUS COMPOSITION of 1-2 child units  (the income lever)")
    print("=" * 72)
    fs = units["filing_status"].astype(str).to_numpy()
    print(f"  {'kids':<5} {'status':<26} {'all wt':>12} {'eitc wt':>11} "
          f"{'claim%':>8} {'med inc(all)':>13}")
    print("  " + "-" * 78)
    for b, lo, hi in [("1", 1, 1), ("2", 2, 2)]:
        sel_kids = (nqc >= lo) & (nqc <= hi)
        for status in ("single", "head_of_household", "married_filing_jointly",
                       "married_filing_separately"):
            sel = sel_kids & (fs == status)
            if not sel.any():
                continue
            all_w = float(w[sel].sum())
            clm_w = float(w[sel & claimers].sum())
            rate = (clm_w / all_w) if all_w else 0.0
            med = float(np.median(inc[sel]))
            print(f"  {b:<5} {status:<26} {all_w:>12,.0f} {clm_w:>11,.0f} "
                  f"{100*rate:>7.1f}% {med:>13,.0f}")
        print("  " + "-" * 78)


def _native_single_parent_structure(
    persons: pd.DataFrame, households: pd.DataFrame
) -> None:
    """Count PUMS-native single-parent-with-kids households and their earned
    income, independent of the tax-unit constructor. Same input data as the
    model, so a divergence isolates a constructor defect; agreement means the
    residual is real-world income near the ceiling, not a structure bug.

    Single parent := household with >=1 own child under 18 (RELSHIPP
    25/26/27 = bio/adopted/step), a householder (RELSHIPP 20), and NO
    spouse present (no RELSHIPP 21 or 23).
    Earned income := householder (WAGP+SEMP) x ADJINC factor (matches the
    rule-based path: apply_2026_growth=False).
    """
    p = persons.copy()
    rel = p["RELSHIPP"].astype(int)

    spouse_serials = set(p.loc[rel.isin([21, 23]), "SERIALNO"].unique())
    head = p.loc[rel == 20].copy()

    own_child = p.loc[rel.isin([25, 26, 27]) & (p["AGEP"].astype(float) < 18)]
    child_counts = own_child.groupby("SERIALNO").size()

    def _adjinc(row) -> float:
        raw = float(row.get("ADJINC", 1.0) or 1.0)
        return raw / 1_000_000 if raw > 2 else raw

    head = head.set_index("SERIALNO")
    wgtp = (
        households.set_index("SERIALNO")["WGTP"].astype(float)
        if "WGTP" in households.columns else pd.Series(1.0, index=head.index)
    )

    rows = []
    for serial, n_kids in child_counts.items():
        if serial in spouse_serials or serial not in head.index:
            continue
        h = head.loc[serial]
        if isinstance(h, pd.DataFrame):
            h = h.iloc[0]
        earned = (float(h.get("WAGP", 0) or 0) + float(h.get("SEMP", 0) or 0)) * _adjinc(h)
        w = float(wgtp.get(serial, 1.0))
        b = "1" if n_kids == 1 else ("2" if n_kids == 2 else "3+")
        rows.append((b, earned, w))

    print()
    print("=" * 72)
    print("PUMS-NATIVE single-parent households  (no constructor)")
    print("=" * 72)
    if not rows:
        print("  (none found)")
        return
    df = pd.DataFrame(rows, columns=["kids", "earned", "w"])
    print(f"  {'kids':<5} {'native wt':>12} {'med earned':>12}   "
          f"(model HoH-with-kids for reference)")
    print("  " + "-" * 60)
    for b in ("1", "2", "3+"):
        sub = df[df["kids"] == b]
        if sub.empty:
            continue
        wt = float(sub["w"].sum())
        # weighted-ish median via repeat-by-rounded-weight is overkill; report
        # the unweighted median of the stratum (income dist is what matters).
        med = float(sub["earned"].median())
        print(f"  {b:<5} {wt:>12,.0f} {med:>12,.0f}")
    print()
    print("  Compare 'native wt' to model HoH 1-kid=48,801 / 2-kid=29,129 and")
    print("  'med earned' to model HoH medians ~$50,762 / $56,854. Agreement ⇒")
    print("  structure is faithful and incomes are real (residual is coverage/")
    print("  definitional vs IRS); shortfall ⇒ constructor drops single parents.")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--source", choices=["fixture", "pums"], default="pums")
    ap.add_argument("--pums-dir", type=Path, default=DEFAULT_PUMS_DIR)
    ap.add_argument("--tax-year", type=int, default=2022,
                    help="EITC parameter year (default 2022 to match IRS target).")
    ap.add_argument("--apply-takeup", action="store_true",
                    help="Apply IRS-anchored pooled EITC take-up before bucketing.")
    ap.add_argument("--production", action="store_true",
                    help="Reproduce the production forecast build (load -> "
                         "project/base-tax -> IRS take-up -> HI EITC) and bucket "
                         "its claimers. tax_year!=2022 projects income forward.")
    ap.add_argument("--reweight-eitc-by-children", action="store_true",
                    help="(Production builds only.) Apply lever 3a — up-weight "
                         "EITC-eligible units in short with-children buckets to "
                         "their IRS targets before take-up truncation. Reports "
                         "the per-bucket factors and total population drift.")
    ap.add_argument("--scale-to-irs-total", action="store_true",
                    help="Rescale model to IRS grand total to isolate the mix from the level gap.")
    args = ap.parse_args()

    persons, households = _load_pums(args.source, args.pums_dir)
    n_households = (
        households["SERIALNO"].nunique() if "SERIALNO" in households.columns else len(households)
    )
    print(f"Source: {args.source}  |  persons: {len(persons):,}  "
          f"households: {n_households:,}  EITC param year: {args.tax_year}")
    if args.source == "pums" and not (args.production and args.tax_year != 2022):
        print("(income held at PUMS vintage; apply_2026_growth=False in rule-based path)")
    print()

    if args.production:
        print("(PRODUCTION build: load -> project/base-tax -> IRS take-up -> HI EITC)")
        if args.tax_year != 2022:
            print(f"(income PROJECTED forward to TY{args.tax_year})")
        print()
        units = _build_units_production(
            persons, households, args.pums_dir, args.tax_year,
            reweight_by_children=args.reweight_eitc_by_children,
        )
    else:
        units = _build_units(persons, households, args.tax_year, args.apply_takeup)
        if args.apply_takeup:
            print("(post pooled IRS take-up: EITC truncated to SOI Hawaii ~84k claim target)\n")
    _report(units, args.scale_to_irs_total)
    _native_single_parent_structure(persons, households)


if __name__ == "__main__":
    main()
