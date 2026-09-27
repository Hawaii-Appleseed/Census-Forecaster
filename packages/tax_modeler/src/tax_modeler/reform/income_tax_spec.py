"""User-defined income tax changes: the tax simulator's policy spec.

One JSON/YAML shape serves the estimates-site simulator, its share links, and
the Python pipeline (``forecast_custom.py --spec``, ``Reform.from_dict``). See
TAX_SIMULATOR_SCOPE.md, "The policy spec". Example::

    name: top_14_over_1m
    label: "14% above $1 million (joint)"
    first_year: 2028            # current law applies before this tax year
    income_tax:
      brackets:                 # replaces current law from first_year on,
        single: [[0, 1.4], [14400, 2.5], ..., [500000, 14.0]]   # (floor, rate %)
        head_of_household: {scale: single, factor: 1.5}
        married_filing_jointly: {scale: single, factor: 2.0}
      standard_deduction: current_law   # or {single: .., head_of_household: .., married_filing_jointly: ..}
      personal_exemption: current_law   # or dollars per exemption

Current law is Act 24 (SB 3125 CD2), which already schedules a different
bracket vintage from TY2029 and a rising standard deduction; a spec's brackets
or deduction, when given, replace current law for every year from
``first_year``, including those scheduled steps. To keep a schedule change
aligned with current law's own steps, give ``bracket_vintages`` instead of
``brackets``::

      bracket_vintages:
        - {from: 2027, brackets: {single: [...], head_of_household: ..., married_filing_jointly: ...}}
        - {from: 2029, brackets: {...}}

Each vintage applies from its year until the next; the first must start at
``first_year``. (The simulator page edits current law's two vintages, so
changing only the top rate changes only the top rate in every year.) A spec
that changes nothing scores exactly zero. Married filing separately uses the
single schedule and deduction, as Hawaiʻi law and the bracket CSV do.
"""
from __future__ import annotations

import dataclasses
import math
import re
from dataclasses import dataclass
from typing import Any, Mapping

from tax_modeler.config.tax_system_config import TaxSystemConfig, TaxSystemRegistry
from tax_modeler.errors import ConfigError

#: Spec filing-status keys -> the bracket/deduction CSV's status names.
SPEC_STATUSES = {
    "single": "Single_Married_Separate",
    "head_of_household": "Head_of_Household",
    "married_filing_jointly": "Joint_Surviving_Spouse",
}
#: Tax years the simulator scores; Act 24's brackets start in TY2027.
FIRST_YEAR_RANGE = (2027, 2031)
BEHAVIORS = ("static", "low", "mid", "high")
MAX_BRACKETS = 30
MAX_FLOOR = 1e10     # $10 billion: no bracket floor is meaningful above it
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_TOP_KEYS = {"name", "label", "first_year", "income_tax", "behavior", "baseline",
             "model_version", "metadata"}
_INCOME_TAX_KEYS = {"brackets", "bracket_vintages", "standard_deduction", "personal_exemption"}


def current_law_system(year: int) -> TaxSystemConfig:
    """Current law (Act 24, the enacted SB 3125 CD2) for ``year`` (TY2027+)."""
    return TaxSystemRegistry.get_sb3125_cd2_system(year)


Schedule = tuple[tuple[float, float], ...]


def _number(value: Any, where: str, *, lo: float = 0.0, hi: float = math.inf,
            hi_open: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{where}: expected a number, got {value!r}")
    try:
        v = float(value)
    except OverflowError:                 # an integer too large for a float
        v = math.inf
    if not math.isfinite(v) or v < lo or (v >= hi if hi_open else v > hi):
        bound = f"[{lo:g}, {hi:g}{')' if hi_open else ']'}"
        raise ConfigError(f"{where}: {value!r} is outside {bound}")
    return v


def validate_schedule(rows: Any, where: str) -> Schedule:
    """``[[floor, rate %], ...]`` -> a checked schedule: floors start at 0 and
    rise strictly up to ``MAX_FLOOR``, rates are percentages in [0, 100)."""
    if not isinstance(rows, (list, tuple)) or not rows:
        raise ConfigError(f"{where}: expected a non-empty list of [floor, rate] pairs")
    if len(rows) > MAX_BRACKETS:
        raise ConfigError(f"{where}: {len(rows)} brackets; at most {MAX_BRACKETS}")
    out = []
    for i, row in enumerate(rows):
        if not isinstance(row, (list, tuple)) or len(row) != 2:
            raise ConfigError(f"{where}[{i}]: expected [floor, rate], got {row!r}")
        floor = _number(row[0], f"{where}[{i}] floor", hi=MAX_FLOOR)
        rate = _number(row[1], f"{where}[{i}] rate", hi=100.0, hi_open=True)
        if i == 0 and floor != 0:
            raise ConfigError(f"{where}: the first bracket must start at 0, not {row[0]!r}")
        if i > 0 and floor <= out[-1][0]:
            raise ConfigError(f"{where}[{i}]: floor {row[0]!r} must be above {out[-1][0]:g}")
        out.append((floor, rate))
    return tuple(out)


def _resolve_brackets(raw: Any) -> dict[str, Schedule]:
    if not isinstance(raw, Mapping):
        raise ConfigError("income_tax.brackets: expected a mapping of filing status to schedule")
    unknown = set(raw) - set(SPEC_STATUSES)
    if unknown:
        raise ConfigError(f"income_tax.brackets: unknown filing status {sorted(unknown)}",
                          available=sorted(SPEC_STATUSES))
    missing = set(SPEC_STATUSES) - set(raw)
    if missing:
        raise ConfigError(f"income_tax.brackets: missing {sorted(missing)}; give all three "
                          "(a status can be {scale: single, factor: 1.5})")
    explicit = {fs: validate_schedule(v, f"income_tax.brackets.{fs}")
                for fs, v in raw.items() if not isinstance(v, Mapping)}
    out = dict(explicit)
    for fs, v in raw.items():
        if not isinstance(v, Mapping):
            continue
        where = f"income_tax.brackets.{fs}"
        if set(v) != {"scale", "factor"}:
            raise ConfigError(f"{where}: a derived schedule is {{scale: <status>, factor: <n>}}")
        src = v["scale"]
        if not isinstance(src, str) or src not in explicit:
            raise ConfigError(f"{where}: scale must name a status with its own schedule, "
                              f"got {src!r}", available=sorted(explicit))
        factor = _number(v["factor"], f"{where}.factor", lo=0.0)
        if factor <= 0:
            raise ConfigError(f"{where}.factor must be positive")
        # re-checked: a large factor can push a floor past MAX_FLOOR
        out[fs] = validate_schedule([(floor * factor, rate) for floor, rate in explicit[src]], where)
    return out


def _year(value: Any) -> int | None:
    """A tax year: an int, or an integral float (JSON cannot tell 2027 from
    2027.0, so the page and this parser must agree on both). Else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _resolve_vintages(raw: Any, first_year: int) -> tuple[tuple[int, dict[str, Schedule]], ...]:
    where = "income_tax.bracket_vintages"
    if not isinstance(raw, (list, tuple)) or not raw:
        raise ConfigError(f"{where}: a non-empty list of {{from: <year>, brackets: ...}}")
    out = []
    for i, v in enumerate(raw):
        if not isinstance(v, Mapping) or set(v) != {"from", "brackets"}:
            raise ConfigError(f"{where}[{i}]: expected {{from: <year>, brackets: ...}}")
        start = _year(v["from"])
        if start is None:
            raise ConfigError(f"{where}[{i}].from: a tax year, got {v['from']!r}")
        if i == 0 and start != first_year:
            raise ConfigError(f"{where}: the first vintage must start at first_year ({first_year})")
        if i > 0 and start <= out[-1][0]:
            raise ConfigError(f"{where}[{i}].from: {start} must be after {out[-1][0]}")
        out.append((start, _resolve_brackets(v["brackets"])))
    return tuple(out)


def _resolve_deductions(raw: Any) -> dict[str, float] | None:
    if raw == "current_law":
        return None
    if not isinstance(raw, Mapping) or set(raw) != set(SPEC_STATUSES):
        raise ConfigError("income_tax.standard_deduction: 'current_law' or an amount for "
                          f"each of {sorted(SPEC_STATUSES)}")
    return {fs: _number(v, f"income_tax.standard_deduction.{fs}", hi=1e7)
            for fs, v in raw.items()}


@dataclass(frozen=True)
class IncomeTaxSpec:
    """A validated user-defined change to Hawaiʻi's income tax.

    ``brackets`` / ``standard_deduction`` / ``personal_exemption`` are ``None``
    where the spec keeps current law. ``brackets`` is a tuple of vintages
    ``(from_year, {status: schedule})``, the first from ``first_year``. Build
    with :meth:`from_dict`.
    """

    name: str
    first_year: int
    label: str = ""
    brackets: tuple[tuple[int, dict[str, Schedule]], ...] | None = None
    standard_deduction: dict[str, float] | None = None
    personal_exemption: float | None = None
    behavior: tuple[str, ...] = BEHAVIORS
    model_version: str | None = None
    metadata: Mapping[str, Any] = dataclasses.field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "IncomeTaxSpec":
        if not isinstance(data, Mapping):
            raise ConfigError("spec: expected a mapping")
        unknown = set(data) - _TOP_KEYS
        if unknown:
            raise ConfigError(f"spec: unknown keys {sorted(unknown)}", available=sorted(_TOP_KEYS))
        name = data.get("name")
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise ConfigError("spec.name: lowercase letters, digits, '_' or '-', 1-64 characters")
        label = data.get("label", "")
        if not isinstance(label, str) or len(label) > 200:
            raise ConfigError("spec.label: a string of at most 200 characters")
        if data.get("baseline", "act24") != "act24":
            raise ConfigError("spec.baseline: specs are scored against current law, 'act24'")
        fy = _year(data.get("first_year"))
        if fy is None or not (FIRST_YEAR_RANGE[0] <= fy <= FIRST_YEAR_RANGE[1]):
            raise ConfigError(f"spec.first_year: a tax year from {FIRST_YEAR_RANGE[0]} "
                              f"to {FIRST_YEAR_RANGE[1]}, got {data.get('first_year')!r}")
        it = data.get("income_tax", {})
        if not isinstance(it, Mapping):
            raise ConfigError("spec.income_tax: expected a mapping")
        unknown = set(it) - _INCOME_TAX_KEYS
        if unknown:
            raise ConfigError(f"spec.income_tax: unknown keys {sorted(unknown)}",
                              available=sorted(_INCOME_TAX_KEYS))
        raw_b = it.get("brackets", "current_law")
        raw_v = it.get("bracket_vintages")
        if raw_v is not None and raw_b != "current_law":
            raise ConfigError("spec.income_tax: give brackets or bracket_vintages, not both")
        if raw_v is not None:
            brackets = _resolve_vintages(raw_v, fy)
        elif raw_b == "current_law":
            brackets = None
        else:
            brackets = ((fy, _resolve_brackets(raw_b)),)
        sd = _resolve_deductions(it.get("standard_deduction", "current_law"))
        raw_pe = it.get("personal_exemption", "current_law")
        pe = None if raw_pe == "current_law" else _number(
            raw_pe, "income_tax.personal_exemption", hi=1e6)
        behavior = data.get("behavior", list(BEHAVIORS))
        if (not isinstance(behavior, (list, tuple)) or not behavior
                or not all(isinstance(b, str) for b in behavior)
                or not set(behavior) <= set(BEHAVIORS)):
            raise ConfigError(f"spec.behavior: a non-empty subset of {list(BEHAVIORS)}")
        mv = data.get("model_version")
        if mv is not None and not isinstance(mv, str):
            raise ConfigError("spec.model_version: a string")
        meta = data.get("metadata")
        if meta is None:
            meta = {}
        if not isinstance(meta, Mapping):
            raise ConfigError("spec.metadata: a mapping")
        return cls(name=name, first_year=fy, label=label, brackets=brackets,
                   standard_deduction=sd, personal_exemption=pe,
                   behavior=tuple(b for b in BEHAVIORS if b in behavior),
                   model_version=mv, metadata=dict(meta))

    def to_dict(self) -> dict[str, Any]:
        """Round-trips through :meth:`from_dict`. Derived schedules are written
        out in full (the ``{scale, factor}`` shorthand is not preserved)."""
        def sched(b):
            return {fs: [[f, r] for f, r in b[fs]] for fs in SPEC_STATUSES}

        it: dict[str, Any] = {}
        if self.brackets is None:
            it["brackets"] = "current_law"
        elif len(self.brackets) == 1:
            it["brackets"] = sched(self.brackets[0][1])
        else:
            it["bracket_vintages"] = [{"from": y, "brackets": sched(b)} for y, b in self.brackets]
        it.update({
            "standard_deduction": ("current_law" if self.standard_deduction is None
                                   else {fs: self.standard_deduction[fs] for fs in SPEC_STATUSES}),
            "personal_exemption": ("current_law" if self.personal_exemption is None
                                   else self.personal_exemption),
        })
        out: dict[str, Any] = {"name": self.name, "label": self.label,
                               "first_year": self.first_year, "baseline": "act24",
                               "income_tax": it, "behavior": list(self.behavior)}
        if self.model_version is not None:
            out["model_version"] = self.model_version
        if self.metadata:
            out["metadata"] = dict(self.metadata)
        return out

    @property
    def changes_anything(self) -> bool:
        return not (self.brackets is None and self.standard_deduction is None
                    and self.personal_exemption is None)

    def brackets_for_year(self, year: int) -> dict[str, Schedule] | None:
        """The spec's schedules in force in ``year`` (None: current law's)."""
        if self.brackets is None:
            return None
        chosen = None
        for start, sched in self.brackets:
            if start <= year:
                chosen = sched
        return chosen

    def baseline_for(self, year: int) -> TaxSystemConfig:
        """Current law for ``year``: what every spec is scored against."""
        return current_law_system(year)

    def system_for(self, year: int) -> TaxSystemConfig:
        """The spec's tax system for ``year``: current law before
        ``first_year``, current law with the spec's overrides from then on."""
        base = current_law_system(year)
        if year < self.first_year or not self.changes_anything:
            return base
        changes: dict[str, Any] = {
            "name": f"{self.name}_{year}",
            "description": self.label or f"User-defined income tax spec {self.name!r}, TY {year}",
        }
        vintage = self.brackets_for_year(year)
        if vintage is not None:
            changes["brackets"] = {SPEC_STATUSES[fs]: s for fs, s in vintage.items()}
            changes["bracket_scenario"] = None
            changes["bracket_adjustments"] = None
        if self.standard_deduction is not None:
            changes["standard_deductions"] = {
                SPEC_STATUSES[fs]: v for fs, v in self.standard_deduction.items()}
        if self.personal_exemption is not None:
            changes["personal_exemption"] = self.personal_exemption
        return dataclasses.replace(base, **changes)


def load_spec(path) -> IncomeTaxSpec:
    """Read a spec from a ``.yaml``/``.yml`` or ``.json`` file."""
    import json
    from pathlib import Path

    p = Path(path)
    text = p.read_text()
    if p.suffix.lower() in (".yaml", ".yml"):
        import yaml
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    return IncomeTaxSpec.from_dict(data)
