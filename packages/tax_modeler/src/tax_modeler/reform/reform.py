"""Reform dataclass + apply_reform.

The Reform DSL covers:

* **Tax-rate reforms** via :func:`compare_systems` against a scenario
  :class:`TaxSystemConfig` produced by ``reform.tax_system_factory``.
* **Benefit reforms** via per-program override dicts under
  ``reform.benefit_overrides``. Phase 3 supports
  ``snap`` / ``ssi`` / ``ssi_hi_supplement`` / ``eitc`` / ``ctc``.
  The first three are recomputed by the benefit modules with overridden
  parameters; ``eitc`` and ``ctc`` use simple multipliers
  (``amount_pct``) on the existing per-unit credit columns.

:func:`apply_reform` returns a :class:`ReformResult` containing the
baseline and counterfactual unit frames plus aggregate deltas.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable, Mapping, Optional

import numpy as np
import pandas as pd

from tax_modeler.config.tax_system_config import (
    TaxCalculator,
    TaxSystemConfig,
    TaxSystemRegistry,
    compare_systems,
)
from tax_modeler.errors import ConfigError


# Programs the Reform DSL recognizes under benefit_overrides.
_KNOWN_BENEFIT_PROGRAMS = frozenset(
    {
        # Phase 3
        "snap", "ssi", "ssi_hi_supplement",
        "eitc", "ctc",
        # Phase 5
        "aca_ptc", "medicaid", "wic", "liheap", "childcare", "housing",
        # Hawaii state-level refundable credits
        "hi_eitc", "hi_food_excise", "hi_renters",
    }
)


# Type aliases
TaxSystemFactory = Callable[[int], TaxSystemConfig]
BenefitOverrides = Mapping[str, Mapping[str, Any]]


# ---------------------------------------------------------------------------
# Phase 11: tax-system factory registry (string ↔ callable)
# ---------------------------------------------------------------------------
# Bridges the YAML-friendly string identifier (``"sb3125_cd1"``) and the
# actual callable factory (``TaxSystemRegistry.get_sb3125_cd1_system``).
# Pre-populated with every Hawaii bill currently shipped; users can
# extend at runtime via :func:`register_tax_system`.

TAX_SYSTEM_FACTORY_REGISTRY: dict[str, TaxSystemFactory] = {
    "act46":             TaxSystemRegistry.get_act46_system,
    "act46_2025":        lambda y: TaxSystemRegistry.get_act46_2025_system(),
    "act46_2027":        lambda y: TaxSystemRegistry.get_act46_2027_system(),
    "sb3125_cd1":        TaxSystemRegistry.get_sb3125_cd1_system,
    "sb3125_cd2":        TaxSystemRegistry.get_sb3125_cd2_system,
    "sb3125_sd1":        TaxSystemRegistry.get_sb3125_sd1_system,
    "sb3125_original":   lambda y: TaxSystemRegistry.get_sb3125_original_2027_system(),
    "hb2306_orig":       TaxSystemRegistry.get_hb2306_orig_system,
    "hb2306_hd1":        TaxSystemRegistry.get_hb2306_hd1_system,
    "ty2017":            lambda y: TaxSystemRegistry.get_2017_system(),
}


def register_tax_system(name: str, factory: TaxSystemFactory) -> None:
    """Add a custom tax-system factory to the registry.

    Once registered, the factory's name can be referenced from YAML
    reform specs (``tax_system: my_custom_bill``) without modifying
    :class:`TaxSystemRegistry`.
    """
    if not name:
        raise ConfigError("register_tax_system: name must be non-empty")
    TAX_SYSTEM_FACTORY_REGISTRY[name] = factory


def _factory_to_name(factory: TaxSystemFactory) -> Optional[str]:
    """Reverse lookup: callable → registry name (None if unregistered).

    Bound methods of the same underlying function compare ``==`` even
    when they're separate bound-method instances (Python rebinds on
    every attribute lookup), so we use ``==`` rather than ``is``.
    """
    for name, f in TAX_SYSTEM_FACTORY_REGISTRY.items():
        if f == factory:
            return name
    return None


def _frozen_metadata(meta: Optional[Mapping]) -> Mapping:
    if meta is None:
        return MappingProxyType({})
    return MappingProxyType(dict(meta))


@dataclass(frozen=True)
class Reform:
    """A frozen description of a policy change relative to baseline.

    Parameters
    ----------
    name:
        Short identifier (e.g. ``"sb3125_cd1"``, ``"snap_minus_10pct"``).
    tax_system_factory:
        ``year -> TaxSystemConfig`` returning the scenario tax system.
        Defaults to ``None`` (no tax change).
    benefit_overrides:
        Per-program parameter overrides, e.g.
        ``{"snap": {"max_benefit_pct": 0.90}}``. Stored on the reform
        but unused until Phase 3 benefit modules land.
    metadata:
        Free-form annotations (description, source citation, etc.).
        Frozen at construction.
    """

    name: str
    tax_system_factory: Optional[TaxSystemFactory] = None
    benefit_overrides: Optional[BenefitOverrides] = None
    metadata: Mapping = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        if not self.name:
            raise ConfigError("Reform.name must be a non-empty string")
        # Freeze metadata if a plain dict was supplied
        if not isinstance(self.metadata, MappingProxyType):
            object.__setattr__(self, "metadata", _frozen_metadata(self.metadata))
        if self.benefit_overrides is not None and not isinstance(
            self.benefit_overrides, MappingProxyType
        ):
            # Deep-freeze one level (program → params); params themselves stay readable
            frozen = MappingProxyType({
                k: MappingProxyType(dict(v)) for k, v in self.benefit_overrides.items()
            })
            object.__setattr__(self, "benefit_overrides", frozen)

    @property
    def changes_taxes(self) -> bool:
        return self.tax_system_factory is not None

    @property
    def changes_benefits(self) -> bool:
        return bool(self.benefit_overrides)

    # ------------------------------------------------------------------
    # Phase 11: serialization (YAML / dict) + composition
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Serialize to a plain dict (YAML/JSON-friendly).

        ``tax_system_factory`` is encoded by its registered name (string)
        from :data:`TAX_SYSTEM_FACTORY_REGISTRY` — callables are not
        directly serializable. Custom factories not in the registry
        raise :class:`ConfigError`.
        """
        out: dict = {"name": self.name}
        if self.tax_system_factory is not None:
            factory_name = _factory_to_name(self.tax_system_factory)
            if factory_name is None:
                raise ConfigError(
                    "Reform.to_dict: tax_system_factory is not in the registry "
                    "(register it via tax_modeler.reform.register_tax_system)"
                )
            out["tax_system"] = factory_name
        if self.benefit_overrides:
            out["benefit_overrides"] = {
                k: dict(v) for k, v in self.benefit_overrides.items()
            }
        if self.metadata:
            out["metadata"] = dict(self.metadata)
        return out

    @classmethod
    def from_dict(cls, data: Mapping) -> "Reform":
        """Reconstruct from a plain dict (output of :meth:`to_dict`).

        Maps ``tax_system`` string back to the factory via
        :data:`TAX_SYSTEM_FACTORY_REGISTRY`.
        """
        if "name" not in data:
            raise ConfigError("Reform.from_dict requires 'name' key")
        factory = None
        if "tax_system" in data and data["tax_system"]:
            ts_name = str(data["tax_system"])
            if ts_name not in TAX_SYSTEM_FACTORY_REGISTRY:
                raise ConfigError(
                    f"Unknown tax_system {ts_name!r} in reform spec",
                    available=sorted(TAX_SYSTEM_FACTORY_REGISTRY),
                )
            factory = TAX_SYSTEM_FACTORY_REGISTRY[ts_name]
        return cls(
            name=str(data["name"]),
            tax_system_factory=factory,
            benefit_overrides=data.get("benefit_overrides"),
            metadata=data.get("metadata") or {},
        )

    def to_yaml(self, path) -> None:
        """Write the reform to a YAML file."""
        import yaml
        with open(path, "w") as f:
            yaml.safe_dump(self.to_dict(), f, sort_keys=False)

    @classmethod
    def from_yaml(cls, path) -> "Reform":
        """Read a reform from a YAML file."""
        import yaml
        with open(path) as f:
            return cls.from_dict(yaml.safe_load(f))

    @classmethod
    def compose(cls, *reforms: "Reform", name: Optional[str] = None,
                metadata: Optional[Mapping] = None) -> "Reform":
        """Combine multiple reforms into one. Last-write-wins on conflicts.

        - If multiple reforms set ``tax_system_factory``, the last
          non-None wins (with a `composed_from` annotation in metadata).
        - ``benefit_overrides`` are merged at the program level: the
          later reform's program overrides replace the earlier reform's
          for that program (full replacement, not deep-merge).

        Parameters
        ----------
        *reforms:
            Reforms to compose, in order. At least one required.
        name:
            Composite name. Defaults to ``"composed:<r1>+<r2>+..."``.
        metadata:
            Composite metadata. Auto-populated with
            ``composed_from=[r.name for r in reforms]``.
        """
        if not reforms:
            raise ConfigError("Reform.compose requires at least one reform")
        composed_name = name or "composed:" + "+".join(r.name for r in reforms)
        merged_factory: Optional[TaxSystemFactory] = None
        merged_overrides: dict[str, dict] = {}
        for r in reforms:
            if r.tax_system_factory is not None:
                merged_factory = r.tax_system_factory
            if r.benefit_overrides:
                for prog, params in r.benefit_overrides.items():
                    merged_overrides[prog] = dict(params)
        composed_meta = dict(metadata or {})
        composed_meta.setdefault(
            "composed_from", [r.name for r in reforms],
        )
        return cls(
            name=composed_name,
            tax_system_factory=merged_factory,
            benefit_overrides=merged_overrides or None,
            metadata=composed_meta,
        )


@dataclass
class ReformResult:
    """Output of :func:`apply_reform`.

    Holds the baseline and counterfactual tax-system configs used, the
    headline revenue deltas from ``compare_systems``, and (when benefit
    overrides are present) the counterfactual unit-level DataFrame plus
    per-program flow deltas in dollars.

    Fields
    ------
    reform, year, baseline_system, scenario_system, comparison,
    revenue_delta_millions, avg_tax_delta:
        As before — the tax-side comparison.
    counterfactual_units:
        Full per-unit DataFrame after benefit overrides have been
        applied. ``None`` when no benefit overrides are present.
    benefit_flow_deltas_millions:
        Mapping ``program -> total $M change`` (positive = more dollars
        going to households; negative = cuts). Empty when no benefit
        overrides are present. The deltas are weighted by
        ``df["weight"]`` if that column exists, else unweighted.
    """

    reform: Reform
    year: int
    baseline_system: TaxSystemConfig
    scenario_system: TaxSystemConfig
    comparison: pd.DataFrame
    revenue_delta_millions: float
    avg_tax_delta: float
    counterfactual_units: Optional[pd.DataFrame] = None
    benefit_flow_deltas_millions: Mapping[str, float] = field(
        default_factory=lambda: MappingProxyType({})
    )


def _ensure_baseline_benefits(
    units: pd.DataFrame, programs: set[str], *, tax_year: int = 2024
) -> pd.DataFrame:
    """Populate baseline benefit columns on ``units`` for any program in ``programs``.

    Idempotent: existing columns are left alone. Used so that benefit
    deltas can be computed cleanly even when the caller hasn't already
    run the benefit modules on their baseline frame.

    ``tax_year`` is threaded into the FPL-based Phase 5 modules (ACA PTC,
    Medicaid, WIC, LIHEAP, childcare, housing) so eligibility tests use
    same-year FPL thresholds on forward-projected runs. Modules clamp to
    the earliest published FPL year via ``fpl_year_for``; the 2024 default
    preserves the prior frozen-FPL behavior.
    """
    df = units
    if "snap" in programs and "snap_amount" not in df.columns:
        from tax_modeler.benefits.snap import compute_snap_for_units
        df = compute_snap_for_units(df)
    if "ssi" in programs and "ssi_amount" not in df.columns:
        from tax_modeler.benefits.ssi import compute_ssi_for_units
        df = compute_ssi_for_units(df)
    if "ssi_hi_supplement" in programs and "ssi_hi_amount" not in df.columns:
        from tax_modeler.benefits.ssi_hi_supplement import (
            compute_ssi_hi_supplement_for_units,
        )
        df = compute_ssi_hi_supplement_for_units(df)
    # Phase 5 — FPL-based eligibility; thread the run's tax_year so income
    # tests use same-year FPL thresholds (fpl_year_for clamps pre-2024).
    if "aca_ptc" in programs and "aca_ptc_amount" not in df.columns:
        from tax_modeler.benefits.aca_ptc import compute_aca_ptc_for_units
        df = compute_aca_ptc_for_units(df, tax_year=tax_year)
    if "medicaid" in programs and "medicaid_amount" not in df.columns:
        from tax_modeler.benefits.medicaid_hi_quest import compute_medicaid_for_units
        df = compute_medicaid_for_units(df, tax_year=tax_year)
    if "wic" in programs and "wic_amount" not in df.columns:
        from tax_modeler.benefits.wic import compute_wic_for_units
        df = compute_wic_for_units(df, tax_year=tax_year)
    if "liheap" in programs and "liheap_amount" not in df.columns:
        from tax_modeler.benefits.liheap import compute_liheap_for_units
        df = compute_liheap_for_units(df, tax_year=tax_year)
    if "childcare" in programs and "childcare_amount" not in df.columns:
        from tax_modeler.benefits.childcare import compute_childcare_for_units
        df = compute_childcare_for_units(df, tax_year=tax_year)
    if "housing" in programs and "housing_subsidy_amount" not in df.columns:
        from tax_modeler.benefits.housing import compute_housing_for_units
        df = compute_housing_for_units(df, tax_year=tax_year)
    # Hawaii state credits
    if "hi_eitc" in programs and "hi_eitc_amount" not in df.columns:
        from tax_modeler.credits.hi_eitc import compute_hi_eitc_for_units
        df = compute_hi_eitc_for_units(df, tax_year=tax_year)
    if "hi_food_excise" in programs and "hi_food_excise_amount" not in df.columns:
        from tax_modeler.credits.hi_food_excise import compute_hi_food_excise_for_units
        df = compute_hi_food_excise_for_units(df, tax_year=tax_year)
    if "hi_renters" in programs and "hi_renters_amount" not in df.columns:
        from tax_modeler.credits.hi_renters import compute_hi_renters_for_units
        df = compute_hi_renters_for_units(df)
    return df


def _apply_benefit_overrides(
    baseline_df: pd.DataFrame,
    overrides: Mapping[str, Mapping[str, Any]],
    *,
    tax_year: int = 2024,
) -> tuple[pd.DataFrame, dict[str, float]]:
    """Compute counterfactual benefit columns and return per-program $M deltas.

    Returns ``(counterfactual_df, deltas)`` where ``deltas`` maps program
    name to the weighted dollar change vs baseline (in millions).

    ``tax_year`` is threaded into the FPL-based Phase 5 modules so the
    counterfactual eligibility tests use the same same-year FPL thresholds
    as the baseline (see :func:`_ensure_baseline_benefits`).
    """
    df = baseline_df.copy()
    deltas: dict[str, float] = {}

    weights = df.get("weight")
    if weights is None:
        weights = pd.Series(1.0, index=df.index)

    def _record(program: str, baseline_col: str, new_values: np.ndarray) -> None:
        baseline_vals = df[baseline_col].fillna(0).to_numpy(dtype=float)
        delta = float(((new_values - baseline_vals) * weights.to_numpy()).sum() / 1e6)
        deltas[program] = delta
        df[baseline_col] = new_values

    if "snap" in overrides:
        from tax_modeler.benefits.snap import compute_snap_for_units
        snap_df = compute_snap_for_units(
            df, overrides=overrides["snap"], out_col="_cf_snap_amount"
        )
        new_vals = snap_df["_cf_snap_amount"].to_numpy()
        _record("snap", "snap_amount", new_vals)

    if "ssi" in overrides:
        from tax_modeler.benefits.ssi import compute_ssi_for_units
        ssi_df = compute_ssi_for_units(
            df, overrides=overrides["ssi"], out_col="_cf_ssi_amount"
        )
        new_vals = ssi_df["_cf_ssi_amount"].to_numpy()
        _record("ssi", "ssi_amount", new_vals)

    if "ssi_hi_supplement" in overrides:
        from tax_modeler.benefits.ssi_hi_supplement import (
            compute_ssi_hi_supplement_for_units,
        )
        supp_df = compute_ssi_hi_supplement_for_units(
            df,
            overrides=overrides["ssi_hi_supplement"],
            out_col="_cf_ssi_hi_amount",
        )
        new_vals = supp_df["_cf_ssi_hi_amount"].to_numpy()
        _record("ssi_hi_supplement", "ssi_hi_amount", new_vals)

    if "eitc" in overrides:
        # Multiplier-only knob for the MVP. Bracket-by-bracket EITC reform
        # would require re-running calculate_eitc_for_tax_units against a
        # parameterized EITCParameters; deferred to Phase 5+.
        pct = float(overrides["eitc"].get("amount_pct", 1.0))
        if "eitc_amount" not in df.columns:
            raise ConfigError(
                "eitc reform requires 'eitc_amount' column on the input frame; "
                "run compute_base_tax / calculate_eitc_for_tax_units first."
            )
        baseline_vals = df["eitc_amount"].fillna(0).to_numpy(dtype=float)
        new_vals = baseline_vals * pct
        _record("eitc", "eitc_amount", new_vals)

    if "ctc" in overrides:
        pct = float(overrides["ctc"].get("amount_pct", 1.0))
        for col in ("ctc_total", "ctc_refundable", "ctc_nonrefundable"):
            if col not in df.columns:
                continue
            baseline_vals = df[col].fillna(0).to_numpy(dtype=float)
            new_vals = baseline_vals * pct
            # Only the headline ctc_total goes into the deltas dict
            if col == "ctc_total":
                _record("ctc", col, new_vals)
            else:
                df[col] = new_vals

    # Phase 5 benefit modules — recompute with overridden parameters
    if "aca_ptc" in overrides:
        from tax_modeler.benefits.aca_ptc import compute_aca_ptc_for_units
        cf = compute_aca_ptc_for_units(
            df, tax_year=tax_year,
            overrides=overrides["aca_ptc"], out_col="_cf_aca_ptc_amount"
        )
        _record("aca_ptc", "aca_ptc_amount", cf["_cf_aca_ptc_amount"].to_numpy())

    if "medicaid" in overrides:
        from tax_modeler.benefits.medicaid_hi_quest import compute_medicaid_for_units
        cf = compute_medicaid_for_units(
            df, tax_year=tax_year,
            overrides=overrides["medicaid"],
            out_col="_cf_medicaid_amount",
            receives_col="_cf_medicaid_receives",
        )
        _record("medicaid", "medicaid_amount", cf["_cf_medicaid_amount"].to_numpy())
        df["medicaid_receives"] = cf["_cf_medicaid_receives"].to_numpy()

    if "wic" in overrides:
        from tax_modeler.benefits.wic import compute_wic_for_units
        cf = compute_wic_for_units(
            df, tax_year=tax_year,
            overrides=overrides["wic"], out_col="_cf_wic_amount"
        )
        _record("wic", "wic_amount", cf["_cf_wic_amount"].to_numpy())

    if "liheap" in overrides:
        from tax_modeler.benefits.liheap import compute_liheap_for_units
        cf = compute_liheap_for_units(
            df, tax_year=tax_year,
            overrides=overrides["liheap"], out_col="_cf_liheap_amount"
        )
        _record("liheap", "liheap_amount", cf["_cf_liheap_amount"].to_numpy())

    if "childcare" in overrides:
        from tax_modeler.benefits.childcare import compute_childcare_for_units
        cf = compute_childcare_for_units(
            df, tax_year=tax_year,
            overrides=overrides["childcare"], out_col="_cf_childcare_amount"
        )
        _record("childcare", "childcare_amount", cf["_cf_childcare_amount"].to_numpy())

    if "housing" in overrides:
        from tax_modeler.benefits.housing import compute_housing_for_units
        cf = compute_housing_for_units(
            df, tax_year=tax_year,
            overrides=overrides["housing"], out_col="_cf_housing_amount"
        )
        _record("housing", "housing_subsidy_amount", cf["_cf_housing_amount"].to_numpy())

    # Hawaii state-level refundable credits
    if "hi_eitc" in overrides:
        from tax_modeler.credits.hi_eitc import compute_hi_eitc_for_units
        cf = compute_hi_eitc_for_units(
            df, tax_year=tax_year, overrides=overrides["hi_eitc"], out_col="_cf_hi_eitc_amount"
        )
        _record("hi_eitc", "hi_eitc_amount", cf["_cf_hi_eitc_amount"].to_numpy())

    if "hi_food_excise" in overrides:
        from tax_modeler.credits.hi_food_excise import compute_hi_food_excise_for_units
        cf = compute_hi_food_excise_for_units(
            df, tax_year=tax_year, overrides=overrides["hi_food_excise"],
            out_col="_cf_hi_food_excise_amount"
        )
        _record(
            "hi_food_excise",
            "hi_food_excise_amount",
            cf["_cf_hi_food_excise_amount"].to_numpy(),
        )

    if "hi_renters" in overrides:
        from tax_modeler.credits.hi_renters import compute_hi_renters_for_units
        cf = compute_hi_renters_for_units(
            df, overrides=overrides["hi_renters"], out_col="_cf_hi_renters_amount"
        )
        _record(
            "hi_renters",
            "hi_renters_amount",
            cf["_cf_hi_renters_amount"].to_numpy(),
        )

    return df, deltas


def apply_reform(
    units: pd.DataFrame,
    reform: Reform,
    *,
    year: int,
    baseline_factory: Optional[TaxSystemFactory] = None,
    calculator: Optional[TaxCalculator] = None,
) -> ReformResult:
    """Apply a reform to a tax-unit DataFrame and return the comparison.

    Parameters
    ----------
    units:
        Tax-unit DataFrame already enriched and tax-computed (output of
        ``compute_base_tax`` or a projected counterpart).
    reform:
        The :class:`Reform` to apply.
    year:
        Tax year for which to materialize both baseline and scenario
        :class:`TaxSystemConfig` objects. Also threaded into the FPL-based
        Phase 5 benefit modules (ACA PTC, Medicaid, WIC, LIHEAP, childcare,
        housing) so eligibility income tests use same-year FPL thresholds
        rather than the frozen 2024 default.
    baseline_factory:
        Optional ``year -> TaxSystemConfig`` factory for the baseline.
        Defaults to ``TaxSystemRegistry.get_act46_system`` (current law).
    calculator:
        Optional pre-initialized :class:`TaxCalculator`. One is created
        if not supplied.

    Returns
    -------
    ReformResult
        Aggregate tax comparison plus, when ``benefit_overrides`` are
        present, the counterfactual unit frame and per-program flow
        deltas. Compose with :mod:`tax_modeler.poverty.spm` and
        :mod:`tax_modeler.metrics` to compute downstream poverty /
        distribution effects.
    """
    base_factory = baseline_factory or TaxSystemRegistry.get_act46_system
    base_cfg = base_factory(year)
    scen_cfg = (
        reform.tax_system_factory(year) if reform.changes_taxes else base_cfg
    )

    if calculator is None:
        calculator = TaxCalculator()

    # If benefits are part of the reform, populate any missing baseline
    # columns first so deltas reflect the full reform-vs-baseline diff.
    benefit_overrides = reform.benefit_overrides or {}
    bad_programs = set(benefit_overrides) - _KNOWN_BENEFIT_PROGRAMS
    if bad_programs:
        raise ConfigError(
            f"unknown benefit programs in reform: {sorted(bad_programs)}",
            available=sorted(_KNOWN_BENEFIT_PROGRAMS),
        )

    if benefit_overrides:
        baseline_units = _ensure_baseline_benefits(
            units, set(benefit_overrides), tax_year=year
        )
    else:
        baseline_units = units

    cmp = compare_systems(baseline_units, base_cfg, scen_cfg, calculator=calculator)
    diff_row = cmp[cmp["system"] == "Difference"].iloc[0]

    counterfactual_units = None
    benefit_deltas: Mapping[str, float] = MappingProxyType({})
    if benefit_overrides:
        counterfactual_units, deltas = _apply_benefit_overrides(
            baseline_units, benefit_overrides, tax_year=year
        )
        benefit_deltas = MappingProxyType(deltas)

    return ReformResult(
        reform=reform,
        year=year,
        baseline_system=base_cfg,
        scenario_system=scen_cfg,
        comparison=cmp,
        revenue_delta_millions=float(diff_row["revenue_millions"]),
        avg_tax_delta=float(diff_row["avg_tax"]),
        counterfactual_units=counterfactual_units,
        benefit_flow_deltas_millions=benefit_deltas,
    )
