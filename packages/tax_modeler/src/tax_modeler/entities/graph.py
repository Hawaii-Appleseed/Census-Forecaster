"""Entity graph and lightweight view dataclasses.

The graph is built from the tax-unit DataFrame plus optional person/
household DataFrames. Lookups are indexed at construction time (O(1) by
ID), so iterating thousands of tax units does not re-scan the underlying
frames.

Wrap-only design: nothing in this module mutates the underlying frames.
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

import pandas as pd

from tax_modeler.errors import DataValidationError
from tax_modeler.units.relshipp_codes import SPM_PRIMARY, SPM_UNRELATED

# ---------------------------------------------------------------------------
# View dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Person:
    """A single person row from PUMS."""

    person_id: str       # SERIALNO_SPORDER
    serialno: str        # household identifier
    age: int | None
    sex: int | None
    relationship: int | None   # PUMS RELSHIPP
    earnings: float                # WAGP + SEMP
    _row: pd.Series | None = field(default=None, repr=False, compare=False)

    @property
    def household_id(self) -> str:
        return self.serialno


@dataclass(frozen=True)
class TaxUnit:
    """A tax filing unit. Wraps one row from the units DataFrame."""

    filer_id: str         # TaxUnitConstructor produces composite string IDs
    serialno: str
    filing_status: str
    primary_filer_id: str
    secondary_filer_id: str
    dependent_person_ids: list[str]
    income: float
    weight: float
    _row: pd.Series = field(repr=False, compare=False)

    @property
    def hh_id(self) -> str:
        return self.serialno

    def person_ids(self) -> list[str]:
        """Every person attached to this tax unit (filer + spouse + deps)."""
        out = [self.primary_filer_id]
        if self.secondary_filer_id and self.secondary_filer_id != "0":
            out.append(self.secondary_filer_id)
        out.extend(self.dependent_person_ids)
        return out


@dataclass(frozen=True)
class SPMUnit:
    """An SPM resource-sharing unit.

    Phase 1: one SPMUnit per Household. Phase 2 will refine to the
    Census P60-280 definition (cohabitors + unrelated children under 15
    + foster children).
    """

    spm_unit_id: str             # currently equals serialno
    serialno: str
    tax_unit_filer_ids: list[str]


@dataclass(frozen=True)
class Household:
    """An ACS housing unit."""

    serialno: str
    weight: float
    puma: str | None
    tax_unit_filer_ids: list[str]
    person_ids: list[str]


# ---------------------------------------------------------------------------
# EntityGraph
# ---------------------------------------------------------------------------


_REQUIRED_UNIT_COLS = (
    "filer_id", "SERIALNO", "filing_status",
    "primary_filer_id", "secondary_filer_id",
    "income", "weight",
)


class EntityGraph:
    """Indexed view over tax units, persons, and households.

    Lightweight: stores references to the source DataFrames, not copies.
    Constructing once and reusing across many lookups is the intended
    pattern. Construct via :meth:`from_units` (preferred) or directly.

    Examples
    --------
    >>> graph = EntityGraph.from_units(units_df)
    >>> for hh in graph.households():
    ...     for tu in graph.tax_units_in_household(hh.serialno):
    ...         print(tu.filer_id, tu.income)
    """

    def __init__(
        self,
        units_df: pd.DataFrame,
        person_df: pd.DataFrame | None = None,
        hh_df: pd.DataFrame | None = None,
    ) -> None:
        missing = [c for c in _REQUIRED_UNIT_COLS if c not in units_df.columns]
        if missing:
            raise DataValidationError(
                f"EntityGraph requires columns {missing} on units_df.",
                missing_columns=missing,
            )
        self._units = units_df
        self._persons = person_df
        self._hh = hh_df

        # Indexes — filer_id is a composite string (e.g.
        # "{SERIALNO}_{filing_status}_{...}"); keep it opaque.
        self._units_by_filer_id: dict[str, int] = {
            str(fid): i for i, fid in enumerate(units_df["filer_id"].tolist())
        }
        self._units_by_serialno: dict[str, list[int]] = {}
        for i, sn in enumerate(units_df["SERIALNO"].astype(str).tolist()):
            self._units_by_serialno.setdefault(sn, []).append(i)

    # -- DataFrame access ------------------------------------------------ #

    @property
    def units_df(self) -> pd.DataFrame:
        return self._units

    @property
    def person_df(self) -> pd.DataFrame | None:
        return self._persons

    @property
    def hh_df(self) -> pd.DataFrame | None:
        return self._hh

    # -- Construction --------------------------------------------------- #

    @classmethod
    def from_units(
        cls,
        units_df: pd.DataFrame,
        person_df: pd.DataFrame | None = None,
        hh_df: pd.DataFrame | None = None,
    ) -> EntityGraph:
        return cls(units_df, person_df=person_df, hh_df=hh_df)

    # -- TaxUnit lookups ------------------------------------------------ #

    def tax_unit(self, filer_id: str) -> TaxUnit:
        idx = self._units_by_filer_id.get(str(filer_id))
        if idx is None:
            raise KeyError(f"no tax unit with filer_id={filer_id!r}")
        return self._row_to_tax_unit(self._units.iloc[idx])

    def tax_units(self) -> Iterator[TaxUnit]:
        for _, row in self._units.iterrows():
            yield self._row_to_tax_unit(row)

    def tax_units_in_household(self, serialno: str) -> list[TaxUnit]:
        idxs = self._units_by_serialno.get(str(serialno), [])
        return [self._row_to_tax_unit(self._units.iloc[i]) for i in idxs]

    # -- Person lookups ------------------------------------------------- #

    def person(self, person_id: str) -> Person:
        if self._persons is None:
            raise DataValidationError(
                "EntityGraph has no person_df; person lookups unavailable. "
                "Pass person_df to from_units() to enable.",
            )
        if person_id not in self._persons.index:
            raise KeyError(f"no person with id={person_id}")
        return self._row_to_person(person_id, self._persons.loc[person_id])

    def people_in_tax_unit(self, filer_id: str) -> list[Person]:
        if self._persons is None:
            return []
        tu = self.tax_unit(filer_id)
        return [self.person(pid) for pid in tu.person_ids() if pid in self._persons.index]

    def people_in_household(self, serialno: str) -> list[Person]:
        if self._persons is None:
            return []
        sn = str(serialno)
        rows = self._persons[self._persons["SERIALNO"].astype(str) == sn]
        return [self._row_to_person(pid, row) for pid, row in rows.iterrows()]

    # -- Household lookups ---------------------------------------------- #

    def households(self) -> Iterator[Household]:
        for sn in self._units_by_serialno:
            yield self.household(sn)

    def household(self, serialno: str) -> Household:
        sn = str(serialno)
        idxs = self._units_by_serialno.get(sn, [])
        if not idxs:
            raise KeyError(f"no household with serialno={serialno}")
        rows = self._units.iloc[idxs]
        weight = float(rows["hh_weight"].iloc[0]) if "hh_weight" in rows.columns else float(rows["weight"].iloc[0])
        puma = str(rows["PUMA"].iloc[0]) if "PUMA" in rows.columns else None
        filer_ids = [str(x) for x in rows["filer_id"].tolist()]

        # Aggregate person_ids across all tax units in the household
        person_ids: list[str] = []
        for _, r in rows.iterrows():
            person_ids.append(str(r["primary_filer_id"]))
            sec = str(r.get("secondary_filer_id", "0"))
            if sec and sec != "0":
                person_ids.append(sec)
            deps = r.get("dependent_person_ids", []) or []
            person_ids.extend([str(d) for d in deps])
        return Household(
            serialno=sn,
            weight=weight,
            puma=puma,
            tax_unit_filer_ids=filer_ids,
            person_ids=person_ids,
        )

    # -- SPMUnit lookups (P60-280 when person_df available) ------------- #
    #
    # SPMUnit grouping per Census P60-280:
    #   * Primary group:  householder + spouse/unmarried partner + their
    #     relatives + foster children + ANY unrelated child under 15.
    #   * Each unrelated adult (roomer/boarder/housemate/other nonrelative
    #     not under 15) forms their own SPMUnit.
    #
    # When ``person_df`` is supplied at construction, ``spm_units()``
    # produces this proper grouping. Without ``person_df`` it falls back
    # to the 1-per-household placeholder.
    #
    # Canonical Census 2019+ RELSHIPP grouping (see
    # tax_modeler.units.relshipp_codes): householder + spouses + unmarried
    # partners + all blood/marriage relatives + foster children pool into the
    # primary SPM unit; roommates (34) and other nonrelatives (36) split off;
    # group quarters (37, 38) are excluded.
    _PRIMARY_SPM_RELSHIPP = SPM_PRIMARY
    _UNRELATED_RELSHIPP = SPM_UNRELATED

    def spm_units(self) -> Iterator[SPMUnit]:
        for hh in self.households():
            yield from self._spm_units_for_household(hh.serialno)

    def _spm_units_for_household(self, serialno: str) -> Iterator[SPMUnit]:
        sn = str(serialno)
        if self._persons is None or "RELSHIPP" not in self._persons.columns:
            # Fallback: 1 SPMUnit per Household
            hh = self.household(sn)
            yield SPMUnit(
                spm_unit_id=sn,
                serialno=sn,
                tax_unit_filer_ids=hh.tax_unit_filer_ids,
            )
            return

        people = self._persons[self._persons["SERIALNO"].astype(str) == sn]
        if people.empty:
            return

        # Decide each person's SPMUnit assignment
        # Primary SPMUnit: anyone in _PRIMARY_SPM_RELSHIPP OR <15 regardless
        primary_pids: list[str] = []
        unrelated_pids: list[str] = []
        for pid, row in people.iterrows():
            relshipp = int(row.get("RELSHIPP", 20))
            age = float(row.get("AGEP", 0) or 0)
            if relshipp in self._PRIMARY_SPM_RELSHIPP or age < 15:
                primary_pids.append(str(pid))
            elif relshipp in self._UNRELATED_RELSHIPP:
                unrelated_pids.append(str(pid))
            else:
                # Group quarters / unknown: skip from SPM accounting
                continue

        # Map filer_ids back to SPMUnits via primary_filer_id linkage
        hh = self.household(sn)
        primary_filer_ids: list[str] = []
        # A tax unit lands in the primary SPMUnit if its primary_filer_id is
        # in the primary set; otherwise unrelated.
        unrelated_filer_ids: list[str] = []
        for fid in hh.tax_unit_filer_ids:
            tu = self.tax_unit(fid)
            if tu.primary_filer_id in primary_pids or tu.primary_filer_id in unrelated_pids:
                if tu.primary_filer_id in primary_pids:
                    primary_filer_ids.append(fid)
                else:
                    # One SPMUnit per unrelated tax unit
                    unrelated_filer_ids.append(fid)
            else:
                primary_filer_ids.append(fid)  # unknown → bucket with primary

        if primary_filer_ids:
            yield SPMUnit(
                spm_unit_id=f"{sn}_primary",
                serialno=sn,
                tax_unit_filer_ids=primary_filer_ids,
            )
        for fid in unrelated_filer_ids:
            yield SPMUnit(
                spm_unit_id=f"{sn}_{fid}",
                serialno=sn,
                tax_unit_filer_ids=[fid],
            )

    def spm_unit_for_tax_unit(self, filer_id: str) -> SPMUnit:
        tu = self.tax_unit(filer_id)
        for spm in self._spm_units_for_household(tu.serialno):
            if filer_id in spm.tax_unit_filer_ids:
                return spm
        # Fallback (shouldn't happen): return household-level placeholder
        hh = self.household(tu.serialno)
        return SPMUnit(
            spm_unit_id=tu.serialno,
            serialno=tu.serialno,
            tax_unit_filer_ids=hh.tax_unit_filer_ids,
        )

    # -- Internal helpers ------------------------------------------------ #

    def _row_to_tax_unit(self, row: pd.Series) -> TaxUnit:
        deps_raw = row.get("dependent_person_ids", row.get("dependents", []))
        if not isinstance(deps_raw, list):
            deps_raw = []
        return TaxUnit(
            filer_id=str(row["filer_id"]),
            serialno=str(row["SERIALNO"]),
            filing_status=str(row["filing_status"]),
            primary_filer_id=str(row["primary_filer_id"]),
            secondary_filer_id=str(row.get("secondary_filer_id", "0")),
            dependent_person_ids=[str(d) for d in deps_raw],
            income=float(row["income"]),
            weight=float(row["weight"]),
            _row=row,
        )

    @staticmethod
    def _row_to_person(person_id: str, row: pd.Series) -> Person:
        wagp = float(row.get("WAGP", 0) or 0)
        semp = float(row.get("SEMP", 0) or 0)
        return Person(
            person_id=str(person_id),
            serialno=str(row.get("SERIALNO", "")),
            age=int(row["AGEP"]) if "AGEP" in row.index and pd.notna(row["AGEP"]) else None,
            sex=int(row["SEX"]) if "SEX" in row.index and pd.notna(row["SEX"]) else None,
            relationship=int(row["RELSHIPP"]) if "RELSHIPP" in row.index and pd.notna(row["RELSHIPP"]) else None,
            earnings=wagp + semp,
            _row=row,
        )
