"""Count margins for families with children, by AGI class, from IRS SOI Historic Table 2 for Hawaiʻi.

The Act 24 base is raked to the Department of Taxation's resident-return counts, tax and AGI by class
(:mod:`.ipf_orchestrator`). None of those sees children, and the base carried about a fifth fewer working
families than the IRS's counts imply. This module gives the rake three more count margins, each by AGI
class (federal AGI, the IRS's own):

* **head of household returns** (the row "Number of head of household returns");
* **returns claiming the nonrefundable child and other dependent tax credit** (CTC and ODC);
* **returns claiming the additional (refundable) child tax credit** (ACTC).

The model counts the same things: filing status head of household; ``ctc_nonrefundable > 0`` or an
other-dependent credit (:func:`odc_amount`, $500 for each dependent who is not a CTC child, within the
federal tax left after the CTC); ``ctc_refundable > 0``. Dependents carry their real ages
(``tax_modeler.pipeline.enrich_for_credits``).

**Universe.** The IRS counts every federal return filed from a Hawaiʻi address, including military families
and part-year residents; the Department's Table A-8 counts resident returns. Each IRS class is scaled to the
resident universe by its own ratio, Department returns over IRS returns, on classes whose edges both tables
share (:func:`universe_ratios`), not by one factor. The IRS classes are (Under $1, $1-10k, 10-25k, 25-50k,
50-75k, 75-100k, 100-200k, 200-500k, 500k-1M, 1M+); the Department's edges are every $10,000 to $50,000,
then 75k, 100k, 150k, 200k, 300k, 400k, 500k, 750k and $1M, so they meet at $10k, $50k, $75k, $100k, $200k,
$500k and $1M.

**Coarse classes.** A cell the rake cannot hit stably is merged with its neighbour: each margin uses the
classes in ``MARGIN_EDGES``. Returns at $1M or more are not raked (the synthetic tail stands for them).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import dotax_base, irs_soi_hi_table2

#: The tax year of the IRS table the margins use (the base year of the Department's tables).
IRS_YEAR = irs_soi_hi_table2.BASE_YEAR

#: Lower edges (federal AGI) of the aligned classes, where the IRS's and the Department's edges meet.
ALIGNED_EDGES = (-np.inf, 10_000.0, 50_000.0, 75_000.0, 100_000.0, 200_000.0, 500_000.0, 1_000_000.0)

#: Lower edges of each margin's classes, merged from the aligned ones; the last class stops at $1M.
MARGIN_EDGES: dict[str, tuple[float, ...]] = {
    "hoh_returns": (-np.inf, 10_000.0, 50_000.0, 75_000.0, 100_000.0),
    "ctc_nonref_returns": (-np.inf, 50_000.0, 75_000.0, 100_000.0, 200_000.0),
    "actc_returns": (-np.inf, 10_000.0, 50_000.0, 75_000.0),
}
TOP = 1_000_000.0           # returns at or above this are left to the tail

# IRS class (lo, hi) in order, as the workbook prints them; "Under $1" is below $1.
_IRS_EDGES = [(-np.inf, 1.0), (1.0, 10_000.0), (10_000.0, 25_000.0), (25_000.0, 50_000.0), (50_000.0, 75_000.0),
              (75_000.0, 100_000.0), (100_000.0, 200_000.0), (200_000.0, 500_000.0), (500_000.0, 1_000_000.0),
              (1_000_000.0, np.inf)]


def _aligned_index(lo: float) -> int:
    """The aligned class an IRS class (identified by its lower edge) lies in."""
    return int(np.searchsorted(np.array(ALIGNED_EDGES), lo, side="right") - 1)


def universe_ratios(year: int = IRS_YEAR) -> list[dict]:
    """Department returns over IRS returns on each aligned class, with both counts."""
    irs = irs_soi_hi_table2.values("returns", year)
    n = len(ALIGNED_EDGES)
    irs_by = np.zeros(n)
    for (lo, _), v in zip(_IRS_EDGES, irs, strict=True):
        irs_by[_aligned_index(lo)] += v
    dot_by = np.zeros(n)
    for (lo, _), v in dotax_base.filer_targets().items():
        dot_by[_aligned_index(float(lo))] += v
    return [{"lo": ALIGNED_EDGES[i], "hi": ALIGNED_EDGES[i + 1] if i + 1 < n else np.inf, "dotax": dot_by[i],
             "irs": irs_by[i], "ratio": dot_by[i] / irs_by[i]} for i in range(n)]


def margin_targets(key: str, year: int = IRS_YEAR) -> dict[int, float]:
    """The margin's target count in each of its coarse classes (class index -> returns), the IRS count in
    each IRS class times its aligned class's universe ratio, summed into the margin's classes."""
    ratio = [r["ratio"] for r in universe_ratios(year)]
    edges = np.array(MARGIN_EDGES[key])
    out = {i: 0.0 for i in range(len(edges))}
    for (lo, _), v in zip(_IRS_EDGES, irs_soi_hi_table2.values(key, year), strict=True):
        if lo >= TOP:
            continue
        out[int(np.searchsorted(edges, lo, side="right") - 1)] += v * ratio[_aligned_index(lo)]
    return out


def odc_amount(units: pd.DataFrame) -> np.ndarray:
    """Other dependent credit available to each unit before the federal tax limit: $500 for each dependent who
    is not a CTC child (age 17 or older, from the real records), phase-out ignored (it starts at $200,000 /
    $400,000, where the credit is a sliver of the count). Zero without the dependent records."""
    if "dependents_details" not in units.columns:
        return np.zeros(len(units))
    n_other = np.zeros(len(units))
    for i, d in enumerate(units["dependents_details"].to_numpy()):
        if isinstance(d, (list, tuple)):
            n_other[i] = sum(1 for x in d if isinstance(x, dict) and float(x.get("age", 0)) >= 17)
    return 500.0 * n_other


def family_flags(units: pd.DataFrame) -> dict[str, np.ndarray] | None:
    """Per unit: head of household, claims the nonrefundable CTC/ODC, claims the ACTC (booleans); ``None``
    when the frame lacks the federal credit columns."""
    need = {"ctc_nonrefundable", "ctc_refundable", "filing_status"}
    if not need <= set(units.columns):
        return None
    nonref = units["ctc_nonrefundable"].fillna(0).to_numpy(float)
    tax = units["federal_tax_before_credits"].fillna(0).to_numpy(float) if "federal_tax_before_credits" in units.columns \
        else np.full(len(units), np.inf)
    odc = np.minimum(odc_amount(units), np.maximum(tax - nonref, 0.0))
    return {
        "hoh_returns": (units["filing_status"] == "head_of_household").to_numpy(),
        "ctc_nonref_returns": (nonref + odc) > 0,
        "actc_returns": units["ctc_refundable"].fillna(0).to_numpy(float) > 0,
    }


def federal_agi(units: pd.DataFrame) -> np.ndarray:
    col = "federal_agi" if "federal_agi" in units.columns else "agi"
    return units[col].fillna(units["agi"] if "agi" in units.columns else 0).to_numpy(float)


def margin_codes(units: pd.DataFrame) -> dict[str, tuple[np.ndarray, dict[str, float]]] | None:
    """``{name: (codes, targets)}`` for the rake: ``codes`` is 0 for a unit outside the margin and ``1 + class``
    for a flagged unit in a class; ``targets`` maps each code (as a string) to its returns, with the remainder
    of the Department's total on code 0. ``None`` when the frame lacks what the flags need."""
    flags = family_flags(units)
    if flags is None:
        return None
    agi = federal_agi(units)
    total = float(sum(dotax_base.status_targets().values()))
    out = {}
    for key, edges in MARGIN_EDGES.items():
        cls = np.searchsorted(np.array(edges), agi, side="right") - 1
        codes = np.where(flags[key] & (agi < TOP), cls + 1, 0).astype(int)
        t = margin_targets(key)
        targets = {str(i + 1): v for i, v in t.items()}
        targets["0"] = total - sum(targets.values())
        out[key] = (codes, targets)
    return out
