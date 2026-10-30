"""Cross-check simulated benefit aggregates against administrative caseload.

Phase 6 validation. Rather than calling out to PolicyEngine US's API
(which requires authentication, network access, and brings up pinning
problems), this module provides a deterministic, runnable cross-check
against the bundled Hawaii DHS / SSA administrative caseload data.

Two checks per program:

  1. **Headcount agreement** — simulated count of receiving units should
     match the administrative recipient count within tolerance. After
     :func:`tax_modeler.calibrate_benefits` runs, this should be tight
     (±5%); without calibration, it just diagnoses the take-up gap.
  2. **Dollar-flow agreement** — simulated total $ should match
     admin-published total $. Looser tolerance (±20% by default) since
     per-unit benefit calculations involve approximations.

Returns a typed :class:`ValidationReport` rather than raising — callers
decide what to do with failures (warn, log, fail tests, etc.).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

import pandas as pd
from tax_modeler.calibration.admin_caseload import AdminCaseload, CaseloadTarget


@dataclass(frozen=True)
class ProgramCheck:
    """One-line summary of one program × admin-target check."""

    program: str
    year: int
    admin_count: float
    sim_count: float
    count_gap_pct: float
    admin_dollars_millions: float
    sim_dollars_millions: float
    dollar_gap_pct: float
    count_passes: bool
    dollar_passes: bool

    @property
    def passes(self) -> bool:
        return self.count_passes and self.dollar_passes


@dataclass
class ValidationReport:
    """Aggregate result of :func:`validate_against_admin_caseload`."""

    year: int
    checks: list[ProgramCheck] = field(default_factory=list)
    count_tolerance_pct: float = 0.05
    dollar_tolerance_pct: float = 0.20

    @property
    def passes(self) -> bool:
        return all(c.passes for c in self.checks)

    def summary(self) -> str:
        lines = [
            f"Validation @ {self.year} "
            f"(count tol ±{self.count_tolerance_pct:.0%}, "
            f"$ tol ±{self.dollar_tolerance_pct:.0%})",
            f"  {'PROGRAM':<20s} {'COUNT':>20s} {'$M':>22s}  STATUS",
        ]
        for c in self.checks:
            count_part = (
                f"{c.sim_count:>9,.0f} / {c.admin_count:>7,.0f} "
                f"({c.count_gap_pct:+5.1%})"
            )
            dollar_part = (
                f"{c.sim_dollars_millions:>8,.1f} / "
                f"{c.admin_dollars_millions:>8,.1f} "
                f"({c.dollar_gap_pct:+5.1%})"
            )
            status = "PASS" if c.passes else "FAIL"
            lines.append(f"  {c.program:<20s} {count_part:>20s} {dollar_part:>22s}  {status}")
        return "\n".join(lines)


_PROGRAM_TO_COLUMNS: Mapping[str, tuple[str, str]] = {
    # program -> (benefit dollar column, "receives" indicator column or None)
    "snap": ("snap_amount", ""),
    "ssi": ("ssi_amount", ""),
    "ssi_hi_supplement": ("ssi_hi_amount", ""),
}


def validate_against_admin_caseload(
    units: pd.DataFrame,
    *,
    caseload: AdminCaseload,
    year: int,
    programs: tuple[str, ...] = ("snap", "ssi", "ssi_hi_supplement"),
    count_tolerance_pct: float = 0.05,
    dollar_tolerance_pct: float = 0.20,
    weight_col: str = "weight",
) -> ValidationReport:
    """Compute per-program agreement vs administrative caseload.

    Parameters
    ----------
    units:
        Unit DataFrame with the benefit ``*_amount`` columns populated.
        Run the benefit modules + (optionally) ``calibrate_benefits``
        before calling this for a meaningful check.
    caseload:
        :class:`AdminCaseload` with targets for ``year``.
    year:
        Target year — must exist in the caseload table for every
        program in ``programs``.
    programs:
        Programs to check. Defaults to the three Phase 4 calibration
        targets.
    count_tolerance_pct, dollar_tolerance_pct:
        Pass/fail bands. Defaults are ±5% on count, ±20% on $.

    Returns
    -------
    ValidationReport
        Use ``.passes`` for a single bool, ``.summary()`` for human-
        readable output, or ``.checks`` for typed per-program details.
    """
    report = ValidationReport(
        year=year,
        count_tolerance_pct=count_tolerance_pct,
        dollar_tolerance_pct=dollar_tolerance_pct,
    )
    weights = units.get(weight_col, pd.Series(1.0, index=units.index))
    for program in programs:
        target: CaseloadTarget = caseload.target(program, year)
        benefit_col, _ = _PROGRAM_TO_COLUMNS.get(program, (f"{program}_amount", ""))
        if benefit_col not in units.columns:
            # Treat missing columns as fail-with-zero rather than raising.
            sim_count = 0.0
            sim_dollars_m = 0.0
        else:
            receiving = units[benefit_col].fillna(0) > 0
            sim_count = float(weights[receiving].sum())
            sim_dollars_m = float(
                (units[benefit_col].fillna(0) * weights).sum() / 1e6
            )

        count_gap = (
            (sim_count - target.count) / target.count if target.count > 0 else 0.0
        )
        dollar_gap = (
            (sim_dollars_m - target.annual_dollars_millions)
            / target.annual_dollars_millions
            if target.annual_dollars_millions > 0
            else 0.0
        )
        report.checks.append(ProgramCheck(
            program=program,
            year=year,
            admin_count=target.count,
            sim_count=sim_count,
            count_gap_pct=count_gap,
            admin_dollars_millions=target.annual_dollars_millions,
            sim_dollars_millions=sim_dollars_m,
            dollar_gap_pct=dollar_gap,
            count_passes=abs(count_gap) <= count_tolerance_pct,
            dollar_passes=abs(dollar_gap) <= dollar_tolerance_pct,
        ))
    return report


__all__ = [
    "ProgramCheck",
    "ValidationReport",
    "validate_against_admin_caseload",
]
