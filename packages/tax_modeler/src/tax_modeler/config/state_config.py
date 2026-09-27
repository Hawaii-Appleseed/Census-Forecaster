"""State-level configuration seam.

Today every Hawaii hardcode in the package — state FIPS, default GEOID for
projection proxies, the §235-51(f) capital-gains cap, the Pease threshold —
sits inline at its point of use.  That makes the package work fine for
Hawaii but means a future Oregon (or any-state) plug-in would have to
hunt those values down individually.

This module introduces a single :class:`StateConfig` dataclass that holds
every state-level constant the package consults.  The default
:data:`HAWAII` instance reproduces today's behavior exactly — nothing about
the numerical output changes when a function signature gains a
``state_config: StateConfig = HAWAII`` parameter.

When a future state plug-in is added, it lives here as a sibling constant
(e.g. ``OREGON = StateConfig(...)``) and its bill-specific scenarios live
in their own ``scenarios/oregon/`` subpackage.  No core math has to change.

Typical use::

    from tax_modeler.config.state_config import HAWAII, StateConfig
    params = scale_deduction_params_for_target_year(2027, state_config=HAWAII)

"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple


# Repo-relative location of the bundled Hawaii tax tables.
# (resolves to packages/tax_modeler/src/tax_modeler/data/tax_tables/...)
_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
_HAWAII_TAX_TABLE_DIR = _PACKAGE_ROOT / "data" / "tax_tables" / "hawaii_2022"


@dataclass(frozen=True)
class StateConfig:
    """State-level configuration consumed by the pipeline.

    All fields are read-only after construction.  Add a new state by
    instantiating a sibling at module scope (see :data:`HAWAII`).

    Attributes
    ----------
    name:
        Human-readable state name (e.g. ``"Hawaii"``).
    state_fips:
        Two-character FIPS code (e.g. ``"15"`` for Hawaii).  Used by the
        PUMS adapter to filter Census microdata.
    default_geoid:
        County GEOID used as the state proxy for income-growth projections
        when a per-county forecast is not available.  For Hawaii this is
        Honolulu County (``"15003"``) — the dominant population center.
    cg_cap_rate:
        Top marginal rate applied to net long-term capital gains under any
        state-level cap (0.0725 for Hawaii under HRS §235-51(f)).  Set to
        ``0.0`` for states without a CG cap; the liability code will then
        skip the cap calculation.
    pease_threshold_single:
        AGI threshold above which itemized deductions begin to phase out
        (state-specific Pease equivalent).  Hawaii: $166,800.
    pease_threshold_mfs:
        Same threshold for the married-filing-separately filing status.
        Hawaii: $83,400.
    personal_exemption_year_inflection:
        Year where the personal exemption schedule structurally changes
        (Hawaii: 2018, when Act 46 phase-in begins).
    tax_table_dir:
        Directory containing the state's bundled bracket / standard-deduction
        CSVs.
    available_bracket_years:
        Years for which a bracket CSV exists in :attr:`tax_table_dir`.
        Used by validators to raise :class:`ConfigError` early.
    spm_thresholds_csv:
        Optional path to a CSV of Hawaii-elevated SPM thresholds keyed by
        (year, n_adults, n_children, tenure). When ``None``, the
        :mod:`tax_modeler.poverty.thresholds` module falls back to its
        inline approximate table — fine for scaffolding, replace with a
        real Census-derived CSV when promoting poverty analysis to
        production.
    admin_caseload_csv:
        Optional path to the Hawaii DHS / SSA administrative caseload
        CSV used by :class:`tax_modeler.calibration.AdminCaseload` to
        calibrate benefit take-up. When ``None``, the bundled
        ``data/admin_caseload/hawaii_caseload.csv`` is used.
    """

    name: str
    state_fips: str
    default_geoid: str
    cg_cap_rate: float
    pease_threshold_single: float
    pease_threshold_mfs: float
    personal_exemption_year_inflection: int
    tax_table_dir: Path
    available_bracket_years: Tuple[int, ...] = field(default_factory=tuple)
    spm_thresholds_csv: Optional[Path] = None
    admin_caseload_csv: Optional[Path] = None


HAWAII = StateConfig(
    name="Hawaii",
    state_fips="15",
    default_geoid="15003",          # Honolulu County
    cg_cap_rate=0.0725,             # HRS §235-51(f)
    pease_threshold_single=166_800.0,
    pease_threshold_mfs=83_400.0,
    personal_exemption_year_inflection=2018,
    tax_table_dir=_HAWAII_TAX_TABLE_DIR,
    # Mirrors projection.hawaii_tax_real._AVAILABLE_BRACKET_YEARS — Act 46 phase-in vintages.
    available_bracket_years=(2018, 2025, 2027, 2029),
)


__all__ = ["StateConfig", "HAWAII"]
