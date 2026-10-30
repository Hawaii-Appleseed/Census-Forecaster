"""Loader for Hawaii DHS / SSA administrative caseload targets.

Take-up imputation (TRIM3-style) requires authoritative counts of who
*actually* receives each benefit, separately from who is *eligible*
according to the simulator. The gap is the take-up rate.

For Hawaii, the canonical sources are:

* **SNAP** — HI DHS Med-QUEST division publishes monthly "SNAP State
  Activity Reports" (households + individuals) and USDA-FNS publishes
  total state allotments. Annual averages are stable enough for
  fiscal-impact work.
* **SSI** — SSA's Annual Statistical Supplement, Table 7.A4 (Recipients
  and total payments by state).
* **HI SSI supplement** — Derived from SSI recipient count × the flat
  state supplement per month (currently $5/mo).

The shipped CSV at ``data/admin_caseload/hawaii_caseload.csv`` carries
the most recent values for the programs Phase 3 supports (SNAP, SSI,
SSI HI supplement). Replace with newer figures by overwriting the CSV
or pointing at one outside the package via :class:`StateConfig` (the
``spm_thresholds_csv`` field — the same pattern can be re-used; we add
a sibling ``admin_caseload_csv`` field below for clarity).
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from tax_modeler.errors import ConfigError, MissingDataError

_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_CASELOAD_CSV = _PACKAGE_ROOT / "data" / "admin_caseload" / "hawaii_caseload.csv"


@dataclass(frozen=True)
class CaseloadTarget:
    """Administrative caseload target for one program × year."""

    program: str
    year: int
    unit: str                    # "household" | "person"
    count: float                 # number of recipient units
    annual_dollars_millions: float
    source: str = ""

    def __repr__(self) -> str:
        return (
            f"CaseloadTarget({self.program!r}, year={self.year}, "
            f"count={self.count:,.0f} {self.unit}s, "
            f"${self.annual_dollars_millions:,.1f}M)"
        )


class AdminCaseload:
    """In-memory view of the Hawaii admin-caseload CSV.

    Use :meth:`load` to construct from disk. Look up targets via
    :meth:`target` (raises :class:`ConfigError` if not found) or
    iterate via :meth:`programs`.
    """

    def __init__(self, df: pd.DataFrame) -> None:
        self._df = df.copy()
        # Index by (program, year) for O(1) lookup
        self._index: dict[tuple[str, int], CaseloadTarget] = {}
        for _, row in df.iterrows():
            key = (str(row["program"]), int(row["year"]))
            self._index[key] = CaseloadTarget(
                program=str(row["program"]),
                year=int(row["year"]),
                unit=str(row["unit"]),
                count=float(row["count"]),
                annual_dollars_millions=float(row["annual_dollars_millions"]),
                source=str(row.get("source", "") or ""),
            )

    @classmethod
    def load(cls, path: Path | None = None) -> AdminCaseload:
        """Load the caseload table from CSV. Falls back to the bundled file."""
        p = Path(path) if path else _DEFAULT_CASELOAD_CSV
        if not p.exists():
            raise MissingDataError(
                f"Admin caseload CSV not found.\n"
                f"Expected at {p}. Either (1) populate that file with the "
                "Hawaii DHS / SSA caseload values, or (2) pass an explicit "
                "path to AdminCaseload.load().",
                path=p,
            )
        return cls(pd.read_csv(p))

    def target(self, program: str, year: int) -> CaseloadTarget:
        key = (program, int(year))
        if key not in self._index:
            available = sorted({k for k in self._index})
            raise ConfigError(
                f"No admin caseload target for program={program!r}, year={year}",
                available=available,
            )
        return self._index[key]

    def programs(self) -> Iterator[CaseloadTarget]:
        yield from self._index.values()

    def to_dataframe(self) -> pd.DataFrame:
        return self._df.copy()

    def __len__(self) -> int:
        return len(self._index)

    def __contains__(self, key: tuple[str, int]) -> bool:
        return (key[0], int(key[1])) in self._index
