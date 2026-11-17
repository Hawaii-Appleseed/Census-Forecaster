"""Data-driven scenario registry (Phase 2 of docs/archive/DASHBOARD_PIPELINE_SCOPE.md).

``TaxSystemRegistry`` exposes one classmethod per bill variant, so nothing
could *enumerate* the modeled scenarios — every runner hardcoded its own
``if cd == "1" ... else ...`` ladder and every consumer had to know which
getters exist. This module turns that knowledge into data: one
``ScenarioSpec`` per slug, so a runner (or a dashboard) can list what is
modelable and resolve a slug to its tax system without importing the
ladder.

Scope: domain facts only — which tax system, which baseline, which credit
overlay, which years are supported. Presentation (chart colors, output
filenames, report subtitles) stays with the calling script.

Follows the registry recipe proven in docs/archive/REGISTRY_MIGRATION_SCOPE.md: the
spec is the single source of truth, and the existing callables are
referenced, never reimplemented, so scenario math cannot drift.

This is the only table of tax-system names. ``reform.Reform``'s
``tax_system:`` names (``TAX_SYSTEM_FACTORY_REGISTRY``) are built from it,
plus :data:`ALIASES`, and :data:`CURRENT_LAW` names the law in force. To add a
bill, write its getter on ``TaxSystemRegistry`` and add one spec here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from tax_modeler.config.tax_system_config import TaxSystemConfig, TaxSystemRegistry

# Credit-overlay behavior per scenario:
#   "none"     — bill does not restrict REEC/CGEC/TCRA (no overlay applied)
#   "standard" — compute_credit_overlay with the base knobs (CD1 path)
#   "vintage"  — CD2 path: adds vintage carryforward pool simulation and
#                per-year dynamic AGI eligibility
OVERLAY_MODES = ("none", "standard", "vintage")


@dataclass(frozen=True)
class ScenarioSpec:
    """Everything a runner needs to resolve a scenario slug."""

    slug: str
    label: str
    family: str
    system: Callable[..., TaxSystemConfig]
    overlay: str = "none"
    baseline: str | None = "act46"
    takes_year: bool = True
    fixed_year: int | None = None
    statute: str = ""

    def __post_init__(self) -> None:
        if self.overlay not in OVERLAY_MODES:
            raise ValueError(
                f"{self.slug}: overlay must be one of {OVERLAY_MODES}, "
                f"got {self.overlay!r}"
            )
        if not self.takes_year and self.fixed_year is None:
            raise ValueError(
                f"{self.slug}: year-less system getters must declare fixed_year"
            )

    def supports_year(self, year: int) -> bool:
        return True if self.takes_year else year == self.fixed_year

    def system_for(self, year: int) -> TaxSystemConfig:
        """Resolve this scenario's tax system for ``year``.

        Hides the split between year-parameterized getters and the
        2027-only ones, which is the detail every runner hardcoded.
        """
        if self.takes_year:
            return self.system(year)
        if year != self.fixed_year:
            raise ValueError(
                f"{self.slug} is only defined for TY{self.fixed_year}, "
                f"not TY{year}"
            )
        return self.system()

    def factory(self) -> Callable[[int], TaxSystemConfig]:
        """The system as a function of the tax year, as ``reform.Reform``
        takes it. A year-less system ignores the year it is given (Reform's
        convention), where :meth:`system_for` refuses any but ``fixed_year``."""
        if self.takes_year:
            return self.system
        return lambda _year: self.system()


_SPECS: list[ScenarioSpec] = [
    ScenarioSpec(
        slug="act46",
        label="Act 46",
        family="act46",
        system=TaxSystemRegistry.get_act46_system,
        baseline=None,  # this IS the baseline
        statute="Act 46 (2024) tax-cut phase-in, current law",
    ),
    ScenarioSpec(
        slug="sb3125_cd1",
        label="SB 3125 CD1",
        family="sb3125",
        system=TaxSystemRegistry.get_sb3125_cd1_system,
        overlay="standard",
        statute="§235-51 brackets; REEC §235-12.5 cap, CGEC, TCRA",
    ),
    ScenarioSpec(
        slug="sb3125_cd2",
        label="SB 3125 CD2",
        family="sb3125",
        system=TaxSystemRegistry.get_sb3125_cd2_system,
        overlay="vintage",
        statute="CD1 plus round-2 REEC knobs (vintage carryforward, dynamic AGI)",
    ),
    ScenarioSpec(
        slug="sb3125_sd1",
        label="SB 3125 SD1",
        family="sb3125",
        system=TaxSystemRegistry.get_sb3125_sd1_system,
        overlay="standard",
        statute="Senate draft 1 bracket schedule",
    ),
    ScenarioSpec(
        slug="sb3125_original",
        label="SB 3125 (as introduced)",
        family="sb3125",
        system=TaxSystemRegistry.get_sb3125_original_2027_system,
        overlay="standard",
        takes_year=False,
        fixed_year=2027,
        statute="Introduced version, TY2027 only",
    ),
    ScenarioSpec(
        slug="hb2306_hd1",
        label="HB 2306 HD1",
        family="hb2306",
        system=TaxSystemRegistry.get_hb2306_hd1_system,
        overlay="none",  # bill does not cap or restrict REEC
        statute="Top 3 bracket rates +1pp; enhanced refundable CDCC",
    ),
    ScenarioSpec(
        slug="hb2306_orig",
        label="HB 2306 (as introduced)",
        family="hb2306",
        system=TaxSystemRegistry.get_hb2306_orig_system,
        overlay="none",
        statute="Introduced version",
    ),
    ScenarioSpec(
        slug="millionaire_tax",
        label="Millionaire surcharge",
        family="other",
        system=TaxSystemRegistry.get_millionaire_tax_2027,
        takes_year=False,
        fixed_year=2027,
        statute="2pp surcharge on $1M+ income, TY2027",
    ),
    ScenarioSpec(
        slug="act46_rollback_targeted",
        label="Act 46 targeted rollback",
        family="act46",
        system=TaxSystemRegistry.get_act46_rollback_targeted,
        statute="Freezes Act 46 phase-in for upper brackets only",
    ),
    # Single-year schedules, used as fixed baselines (Reform specs name them).
    ScenarioSpec(
        slug="act46_2025",
        label="Act 46, TY2025 schedule",
        family="act46",
        system=TaxSystemRegistry.get_act46_2025_system,
        baseline=None,
        takes_year=False,
        fixed_year=2025,
        statute="Act 46 brackets and standard deductions as of TY2025",
    ),
    ScenarioSpec(
        slug="act46_2027",
        label="Act 46, TY2027 schedule",
        family="act46",
        system=TaxSystemRegistry.get_act46_2027_system,
        baseline=None,
        takes_year=False,
        fixed_year=2027,
        statute="Act 46 TY2027 bracket and standard-deduction phase-in",
    ),
    ScenarioSpec(
        slug="ty2017",
        label="2017 law (pre-Act 46)",
        family="other",
        system=TaxSystemRegistry.get_2017_system,
        baseline=None,
        takes_year=False,
        fixed_year=2018,
        statute="Pre-Act 46 brackets, standard deduction and personal exemption",
    ),
]

SCENARIO_SPECS: dict[str, ScenarioSpec] = {s.slug: s for s in _SPECS}

# Other names for a registry slug. Act 24, SLH 2026 is SB 3125 as enacted (CD2).
ALIASES: dict[str, str] = {"act24": "sb3125_cd2"}

# The income tax law in force, which the tax simulator and custom specs score
# against.
CURRENT_LAW = "act24"


def get_scenario(slug: str) -> ScenarioSpec:
    """The spec for ``slug`` (or one of its :data:`ALIASES`)."""
    try:
        return SCENARIO_SPECS[ALIASES.get(slug, slug)]
    except KeyError:
        raise KeyError(
            f"Unknown scenario {slug!r}. Known slugs: "
            f"{', '.join(sorted(SCENARIO_SPECS))}"
        ) from None


def list_scenarios(family: str | None = None) -> list[ScenarioSpec]:
    """All specs, optionally filtered to one family, in registry order."""
    specs = list(SCENARIO_SPECS.values())
    if family is not None:
        specs = [s for s in specs if s.family == family]
    return specs


def baseline_for(spec_or_slug) -> ScenarioSpec | None:
    """Resolve a scenario's baseline spec (``None`` for the baseline itself)."""
    spec = spec_or_slug if isinstance(spec_or_slug, ScenarioSpec) else get_scenario(spec_or_slug)
    return get_scenario(spec.baseline) if spec.baseline else None
