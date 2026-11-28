"""What the state-tax translator must produce for each fixture bill.

Shared by ``tests/fiscal_notes`` (recorded responses) and
``python -m fiscal_notes state-eval`` (the real model), like ``expectations.py``
for the income tax path.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from fiscal_notes.translator import CheckResult

REPO = Path(__file__).resolve().parents[1]
BILLS_DIR = REPO / "tests" / "fiscal_notes" / "bills_state"
RESPONSES_DIR = REPO / "tests" / "fiscal_notes" / "responses_state"


@dataclass(frozen=True)
class Case:
    #: verdicts the checks may derive (a judgement call can sit between two)
    verdicts: tuple[str, ...]
    #: checks on the validated spec (None when nothing is modeled): spec -> [(label, ok)]
    spec: Callable[[dict | None], list[tuple[str, bool]]] = lambda spec: []
    #: checks on the provisions: provisions -> [(label, ok)]
    provisions: Callable[[list[dict]], list[tuple[str, bool]]] = lambda provisions: []


def _get(spec: dict | None, *path: str):
    cur = spec
    for part in path:
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


CASES: dict[str, Case] = {
    # Exempts SNAP- and WIC-eligible groceries; applies to taxable years after 12/31/2025.
    "2026_HB1585_Introduced": Case(
        verdicts=("full", "partial"),
        spec=lambda s: [
            ("exempts groceries", _get(s, "general_excise", "exempt") == ["groceries"]),
            ("effective 2026-01-01", _get(s, "effective") == "2026-01-01"),
            ("sets nothing else", s is not None and set(s) <= {"effective", "general_excise"}),
        ],
    ),
    # Raises the transient accommodations tax by one percentage point.
    "2026_HB604_Introduced": Case(
        verdicts=("full", "partial"),
        spec=lambda s: [
            ("one percentage point on the transient accommodations tax",
             _get(s, "transient_accommodations", "rate_change_points") == 1.0),
            ("no general excise change", _get(s, "general_excise") is None),
        ],
    ),
    # Cigarettes and little cigars from 16 to 21 cents; reallocates the revenue.
    "2026_HB1085_Introduced": Case(
        verdicts=("full", "partial"),
        spec=lambda s: [
            ("21 cents a cigarette", _get(s, "cigarettes", "cents_per_cigarette") == 21.0),
            ("effective 2026-01-01", _get(s, "effective") == "2026-01-01"),
        ],
    ),
    # Exempts groceries and nonprescription drugs; raises the general excise rate by amounts
    # left blank; income tax changes.
    "2026_HB959_Introduced": Case(
        verdicts=("partial",),
        spec=lambda s: [
            ("exempts groceries", _get(s, "general_excise", "exempt") == ["groceries"]),
            ("no general excise rate (the bill leaves it blank)", _get(s, "general_excise", "rates") is None),
        ],
        provisions=lambda ps: [
            ("an unscored provision is listed", any(not p["modeled"] or p["blank"] for p in ps)),
        ],
    ),
    # Replaces the liquor categories with alcohol-by-volume tiers: not the spec's three beverages.
    # Either nothing is modeled, or the tiers are mapped onto them and flagged approximate.
    "2026_HB1991_Introduced": Case(
        verdicts=("out_of_scope", "partial"),
        spec=lambda s: [("only liquor, if anything", s is None or set(s) <= {"effective", "liquor"})],
        provisions=lambda ps: [
            ("anything modeled is flagged approximate", all(p.get("approximate") for p in ps if p["modeled"])),
        ],
    ),
    # Raises filing thresholds.
    "2026_HB2156_Introduced": Case(verdicts=("out_of_scope",),
                                   spec=lambda s: [("no spec", s is None)]),
}


def bill_text(case: str) -> str:
    return (BILLS_DIR / f"{case}.txt").read_text()


def response_text(case: str) -> str:
    return (RESPONSES_DIR / f"{case}.json").read_text()


def evaluate(case: str, result: CheckResult) -> list[tuple[str, bool]]:
    c = CASES[case]
    out = [(f"verdict in {list(c.verdicts)} (got {result.verdict})", result.verdict in c.verdicts)]
    out += c.spec(result.spec)
    out += c.provisions(result.provisions)
    return out
