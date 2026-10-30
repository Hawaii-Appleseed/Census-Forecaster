"""Recalibrate the DOTAX concentration model against new Table 12A data.

For each AGI bracket in DOTAX Table 12A, this script finds the
``rep_income`` — the gross income level at which
``hawaii_tax_weighted(rep_income, tax_year)`` equals the bracket's
``avg_tax_before_credits`` — by solving via ``scipy.optimize.brentq``.

The resulting ``rep_income`` values encode:
* The actual income distribution within each bracket (right-skewed)
* Itemized deduction heterogeneity (high earners itemize more)
* Filing-status mix within each bracket (via the weighted tax function)

Usage
-----
Re-calibrate with a new DOTAX Table 12A JSON file::

    python -m tax_modeler.scripts.recalibrate_concentration \\
        --input dotax_2023_table12a.json \\
        --tax-year 2023 \\
        --dotax-median 91000

Re-verify the existing 2022 calibration (no input file)::

    python -m tax_modeler.scripts.recalibrate_concentration \\
        --tax-year 2022

Input file format (JSON)
------------------------
A JSON array of bracket dicts::

    [
      {"agi_min": 0,       "agi_max": 10000,  "n_returns": 129376, "avg_tax_before_credits": 21},
      {"agi_min": 10000,   "agi_max": 20000,  "n_returns": 64160,  "avg_tax_before_credits": 333},
      ...
      {"agi_min": 400000,  "agi_max": null,   "n_returns": 8875,   "avg_tax_before_credits": 112416}
    ]

``agi_max`` may be ``null`` or ``"inf"`` to indicate the top open bracket.

A CSV with the same four column names is also accepted (detected by ``.csv``
file extension).

Output
------
Prints a Python code block ready to paste into
``tax_modeler/projection/revenue_concentration.py`` as the new
``DOTAX_YYYY_BRACKETS`` constant, followed by a verification table showing
how closely the calibrated model reproduces each bracket's
``avg_tax_before_credits``.

Exit code 0 on success; 1 if any bracket fails to converge within tolerance.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import sys
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Maximum allowed relative error between calibrated tax and DOTAX target.
_CONVERGENCE_TOL = 1e-8       # brentq tolerance (absolute, in $)
_VERIFICATION_TOL = 0.0005    # 0.05% max relative error per bracket before warning


# ---------------------------------------------------------------------------
# Default bracket data (DOTAX Table 12A, TY 2022)
# ---------------------------------------------------------------------------

@dataclass
class _RawBracket:
    agi_min: float
    agi_max: float          # math.inf for top bracket
    n_returns: int
    avg_tax_before_credits: float


_DOTAX_2022_RAW: list[_RawBracket] = [
    _RawBracket(       0,   10_000, 129_376,      21),
    _RawBracket(  10_000,   20_000,  64_160,     333),
    _RawBracket(  20_000,   30_000,  57_835,     889),
    _RawBracket(  30_000,   40_000,  59_827,   1_533),
    _RawBracket(  40_000,   50_000,  53_555,   2_166),
    _RawBracket(  50_000,   75_000,  91_459,   3_201),
    _RawBracket(  75_000,  100_000,  54_976,   4_751),
    _RawBracket( 100_000,  150_000,  62_065,   7_055),
    _RawBracket( 150_000,  200_000,  27_976,  10_496),
    _RawBracket( 200_000,  300_000,  18_937,  16_391),
    _RawBracket( 300_000,  400_000,   6_076,  25_119),
    _RawBracket( 400_000, math.inf,   8_875, 112_416),
]

# DOTAX 2022 median household income (ACS Honolulu B19013)
_DOTAX_2022_MEDIAN = 83_737.0


# ---------------------------------------------------------------------------
# brentq calibration helpers
# ---------------------------------------------------------------------------

def _search_bounds(bracket: _RawBracket) -> tuple[float, float]:
    """Return (lower, upper) brentq search bounds for one bracket.

    The bounds are chosen so that:
      hawaii_tax_weighted(lower) < avg_tax_before_credits < hawaii_tax_weighted(upper)
    which guarantees a sign change and thus convergence.

    Note: ``rep_income`` may fall below ``agi_min`` because it encodes
    within-bracket income skew (most filers cluster near the bracket floor)
    and itemized deductions that reduce effective taxable income.  For
    example, the $75k-$100k bracket has rep_income ≈ $73k because
    ``hawaii_tax_weighted($75k)`` already exceeds the bracket's average tax.
    Therefore, the lower bound is always 1.0, not ``agi_min``.
    """
    lo = 1.0
    if bracket.agi_max == math.inf:
        hi = max(bracket.agi_min * 100, 10_000_000)
    else:
        hi = max(bracket.agi_max * 10, bracket.agi_min * 50, bracket.agi_max + 50_000)
    return lo, hi


def _calibrate_bracket(
    bracket: _RawBracket,
    tax_year: int,
    tol: float = _CONVERGENCE_TOL,
) -> tuple[float, float]:
    """Solve rep_income for one bracket via brentq.

    Returns
    -------
    (rep_income, residual_pct)
        ``residual_pct`` is ``|calibrated_tax / avg_tax - 1| * 100``.
    """
    from scipy.optimize import brentq

    from tax_modeler.projection.hawaii_tax_real import hawaii_tax_weighted

    target = bracket.avg_tax_before_credits
    lo, hi = _search_bounds(bracket)

    # Verify sign condition; widen bounds if necessary.
    f_lo = hawaii_tax_weighted(lo, tax_year=tax_year) - target
    f_hi = hawaii_tax_weighted(hi, tax_year=tax_year) - target

    # Edge case: zero or near-zero avg_tax bracket (lowest income filers)
    if target <= 0:
        logger.warning(
            "Bracket [%.0f, %.0f]: avg_tax=%.2f ≤ 0; using lower bound as rep_income",
            bracket.agi_min, bracket.agi_max, target,
        )
        return lo, 0.0

    # Expand hi if f_hi is still negative
    multiplier = 2
    while f_hi < 0 and multiplier <= 1024:
        hi *= multiplier
        f_hi = hawaii_tax_weighted(hi, tax_year=tax_year) - target
        multiplier *= 2

    if f_lo >= 0 or f_hi <= 0:
        raise RuntimeError(
            f"No sign change for bracket [{bracket.agi_min:.0f}, {bracket.agi_max}]: "
            f"f(lo={lo:.0f})={f_lo:.2f}  f(hi={hi:.0f})={f_hi:.2f}"
        )

    rep_income = brentq(
        lambda x: hawaii_tax_weighted(x, tax_year=tax_year) - target,
        lo, hi,
        xtol=tol, rtol=tol,
        full_output=False,
    )

    calibrated_tax = hawaii_tax_weighted(rep_income, tax_year=tax_year)
    residual_pct = abs(calibrated_tax / target - 1) * 100 if target > 0 else 0.0
    return rep_income, residual_pct


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def _load_brackets(path: str) -> list[_RawBracket]:
    """Load bracket data from a JSON or CSV file."""
    if path.lower().endswith(".csv"):
        return _load_csv(path)
    return _load_json(path)


def _parse_agi_max(raw) -> float:
    if raw is None or raw == "" or str(raw).lower() in ("inf", "null", "none", "infinity"):
        return math.inf
    return float(raw)


def _load_json(path: str) -> list[_RawBracket]:
    with open(path) as f:
        rows = json.load(f)
    brackets = []
    for row in rows:
        brackets.append(_RawBracket(
            agi_min=float(row["agi_min"]),
            agi_max=_parse_agi_max(row.get("agi_max")),
            n_returns=int(row["n_returns"]),
            avg_tax_before_credits=float(row["avg_tax_before_credits"]),
        ))
    return brackets


def _load_csv(path: str) -> list[_RawBracket]:
    brackets = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            brackets.append(_RawBracket(
                agi_min=float(row["agi_min"]),
                agi_max=_parse_agi_max(row.get("agi_max")),
                n_returns=int(row["n_returns"]),
                avg_tax_before_credits=float(row["avg_tax_before_credits"]),
            ))
    return brackets


# ---------------------------------------------------------------------------
# Code generation
# ---------------------------------------------------------------------------

def _agi_max_repr(agi_max: float) -> str:
    if math.isinf(agi_max):
        return "math.inf"
    return f"{int(agi_max):_}"


def _render_python(
    brackets: list[_RawBracket],
    rep_incomes: list[float],
    tax_year: int,
    dotax_median: float,
) -> str:
    """Return the Python constant block to paste into revenue_concentration.py."""
    lines = [
        f"# Calibrated {tax_year} representative incomes — computed via scipy.optimize.brentq",
        f"# on hawaii_tax_weighted(tax_year={tax_year}), verified against DOTAX totals.",
        f"DOTAX_{tax_year}_BRACKETS: tuple[BracketEntry, ...] = (",
    ]
    for b, ri in zip(brackets, rep_incomes):
        lo = f"{int(b.agi_min):>10_}"
        hi = f"{_agi_max_repr(b.agi_max):>10}"
        nr = f"{b.n_returns:>7_}"
        at = f"{b.avg_tax_before_credits:>8,.0f}"
        ri_str = f"{ri:>14_.2f}"
        lines.append(f"    BracketEntry({lo}, {hi}, {nr}, {at}, {ri_str}),")
    lines.append(")")
    lines.append("")
    lines.append(f"# Base year for calibration (TY{tax_year} ACS Honolulu B19013)")
    lines.append(f"DOTAX_{tax_year}_MEDIAN_INCOME: float = {dotax_median:,.1f}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(
    brackets: list[_RawBracket],
    tax_year: int,
    dotax_median: float,
    verbose: bool = True,
) -> tuple[list[float], bool]:
    """Calibrate all brackets.  Returns (rep_incomes, success)."""
    rep_incomes: list[float] = []
    any_failed = False

    if verbose:
        print(f"\nCalibrating {len(brackets)} brackets  "
              f"[tax_year={tax_year}, median=${dotax_median:,.0f}]")
        print(f"{'Bracket':>22}  {'n_returns':>9}  {'avg_tax':>9}  "
              f"{'rep_income':>14}  {'residual%':>10}  status")
        print("-" * 85)

    for b in brackets:
        lo_str = f"${b.agi_min/1000:.0f}k" if b.agi_min > 0 else "$0"
        hi_str = f"${b.agi_max/1000:.0f}k" if not math.isinf(b.agi_max) else "∞"
        label = f"{lo_str}–{hi_str}"

        try:
            rep_income, residual_pct = _calibrate_bracket(b, tax_year)
            rep_incomes.append(rep_income)

            status = "✓"
            if residual_pct > _VERIFICATION_TOL * 100:
                status = "WARN"
                any_failed = True
                logger.warning(
                    "Bracket %s: residual %.4f%% exceeds %.2f%% threshold",
                    label, residual_pct, _VERIFICATION_TOL * 100,
                )
            if verbose:
                print(f"  {label:>20}  {b.n_returns:>9,}  {b.avg_tax_before_credits:>9,.0f}  "
                      f"{rep_income:>14,.2f}  {residual_pct:>10.6f}  {status}")
        except Exception as exc:
            logger.error("Bracket %s failed: %s", label, exc)
            rep_incomes.append(float("nan"))
            any_failed = True
            if verbose:
                print(f"  {label:>20}  {b.n_returns:>9,}  "
                      f"{b.avg_tax_before_credits:>9,.0f}  "
                      f"{'FAILED':>14}  {'—':>10}  ✗ {exc}")

    if verbose:
        _print_verification(brackets, rep_incomes, tax_year, dotax_median)

    return rep_incomes, not any_failed


def _print_verification(
    brackets: list[_RawBracket],
    rep_incomes: list[float],
    tax_year: int,
    dotax_median: float,
) -> None:
    """Print aggregate verification against DOTAX totals."""
    from tax_modeler.projection.hawaii_tax_real import hawaii_tax_weighted

    total_returns = sum(b.n_returns for b in brackets)
    dotax_total_rev = sum(b.n_returns * b.avg_tax_before_credits for b in brackets)
    dotax_avg = dotax_total_rev / total_returns if total_returns else 0.0

    calibrated_total = 0.0
    for b, ri in zip(brackets, rep_incomes):
        if not math.isnan(ri):
            calibrated_total += b.n_returns * hawaii_tax_weighted(ri, tax_year=tax_year)

    calibrated_avg = calibrated_total / total_returns if total_returns else 0.0
    total_err_pct = abs(calibrated_avg / dotax_avg - 1) * 100 if dotax_avg else 0.0

    print(f"\n{'─' * 85}")
    print(f"  DOTAX total revenue (weighted):  ${dotax_total_rev/1e6:>10.2f}M  "
          f"(${dotax_avg:>8.2f}/filer)")
    print(f"  Calibrated model:                ${calibrated_total/1e6:>10.2f}M  "
          f"(${calibrated_avg:>8.2f}/filer)")
    print(f"  Total error:                      {total_err_pct:>9.4f}%  "
          f"({'✓ PASS' if total_err_pct < 0.05 else '✗ FAIL'})")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Recalibrate DOTAX concentration model via brentq.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--input", "-i",
        metavar="FILE",
        help="JSON or CSV file with DOTAX Table 12A bracket data.  "
             "Omit to use the built-in 2022 DOTAX data.",
    )
    parser.add_argument(
        "--tax-year", "-y",
        type=int,
        default=2022,
        metavar="YEAR",
        help="Tax year for hawaii_tax_weighted() bracket schedule (default: 2022).",
    )
    parser.add_argument(
        "--dotax-median",
        type=float,
        default=None,
        metavar="INCOME",
        help="ACS Honolulu B19013 median for the calibration year.  "
             "Defaults to $83,737 for TY2022; required for other years.",
    )
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Suppress the per-bracket table; print only the Python code block.",
    )
    parser.add_argument(
        "--no-code",
        action="store_true",
        help="Suppress the Python code block; print only the verification table.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

    # Load bracket data
    if args.input:
        try:
            brackets = _load_brackets(args.input)
        except Exception as exc:
            print(f"ERROR: could not load {args.input!r}: {exc}", file=sys.stderr)
            return 1
    else:
        brackets = _DOTAX_2022_RAW
        if args.tax_year != 2022:
            print(
                f"INFO: No --input provided; using built-in 2022 DOTAX bracket data "
                f"with --tax-year={args.tax_year}.",
                file=sys.stderr,
            )

    # Resolve median income
    dotax_median = args.dotax_median
    if dotax_median is None:
        if args.tax_year == 2022:
            dotax_median = _DOTAX_2022_MEDIAN
        else:
            print(
                "ERROR: --dotax-median is required when --tax-year != 2022.",
                file=sys.stderr,
            )
            return 1

    # Calibrate
    rep_incomes, success = run(
        brackets, args.tax_year, dotax_median, verbose=not args.quiet
    )

    # Emit Python code block
    if not args.no_code:
        code = _render_python(brackets, rep_incomes, args.tax_year, dotax_median)
        print("\n" + "─" * 85)
        print("# ── Paste into tax_modeler/projection/revenue_concentration.py ──────────")
        print(code)
        print("─" * 85)

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
