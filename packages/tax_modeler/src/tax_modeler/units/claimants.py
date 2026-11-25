"""Who claims each child: parent first, then the householder.

The legacy assignment (``assignment="legacy"`` in :func:`dependencies.identify_dependents`) could match a
child only to the householder or the householder's spouse: a grandchild went to the householder while the
child's actual parent (an adult child of the householder) filed with no children, and minors of the
householder's unmarried partner or of unrelated adults were claimed by nobody. This module assigns each
child, in this order (IRC 152(c)(4): a parent who can claim has priority over a non-parent):

(a) the child's own parent living in the household. The parent link is, in order of trust:
    * the Census subfamily variables ``SFN`` / ``SFR`` (a child in a subfamily, ``SFR`` 4-6, is linked to the
      subfamily member with ``SFR`` 2 or 3, the couple with children or the single parent); this covers every
      related minor whose parent is not the householder or spouse;
    * the householder or spouse for their own children (RELSHIPP 25-27) and foster children (35);
    * a documented age-gap rule (:func:`rule_candidates`) for children PUMS gives no subfamily link: minors of
      the householder's unmarried partner or of other unrelated adults (never in a subfamily), anyone aged 18
      or over (a subfamily child is under 18 by definition), and every child when the file has no SFN column;
(b) otherwise another adult relative the child lives with who can lawfully claim a child with that
    relationship: in PUMS this is the householder (grandparent, sibling, foster parent, or the householder
    for any relative child with no parent present). A relative coded "other relative" (33: nieces, nephews,
    cousins) with no parent present goes to the householder as a dependent but not as an EITC / CTC
    qualifying child, because 33 cannot separate nieces and nephews from cousins (conservative);
(c) otherwise the householder, except for unrelated children (roommate / other nonrelative codes 34, 36),
    which keep the legacy treatment: claimed by the householder only when enrolled in school, and then not as an
    EITC / CTC qualifying child (their RELSHIPP is not a qualifying relationship), else by nobody.

A parent is an adult (18 or over) who is not himself a dependent student. A child's own parent is never moved
to whoever would gain more credit; the choice among several candidate parents is made by a stated rule
(``parent_link``), with a low and a high variant so its effect can be bounded.

The functions here are pure: they take the household frame and return mappings.
"""

from __future__ import annotations

import math
from collections.abc import Callable

import pandas as pd

from tax_modeler.units.relshipp_codes import (
    BIO_CHILD,
    CHILD_IN_LAW,
    FOSTER_CHILD,
    GRANDCHILD,
    OTHER_NONRELATIVE,
    OWN_CHILD,
    PARENT,
    REFERENCE_PERSON,
    ROOMMATE,
    SIBLING,
    SPOUSE_OPPOSITE,
    SPOUSE_SAME,
    UNMARRIED_PARTNER,
)

#: Parent-link policies. ``sfn`` (default): subfamily link, rule only where none can exist. ``sfn+rule_nearest``:
#: also apply the nearest-age rule to minors the file says have no in-household parent besides the householder
#: and spouse. ``rule_low`` / ``rule_nearest`` / ``rule_high`` ignore SFN: the rule alone, as the diagnosis
#: (docs/data_source_tests/eitc-eligible-shortfall.md) had to, for the sensitivity to the parent choice.
PARENT_LINKS = ("sfn", "sfn+rule_nearest", "rule_low", "rule_nearest", "rule_high")
ASSIGNMENTS = ("parent_first", "legacy")

#: A parent is 15 to 50 years older than the child.
MIN_PARENT_GAP = 15
MAX_PARENT_GAP = 50
#: ``nearest`` picks the candidate whose age gap is closest to this (mean age of US mothers at a birth, 27-29).
TYPICAL_PARENT_GAP = 28

_UNRELATED = frozenset({ROOMMATE, OTHER_NONRELATIVE})
_HOUSEHOLD_CORE = frozenset({REFERENCE_PERSON, 1, SPOUSE_OPPOSITE, SPOUSE_SAME}) | set(UNMARRIED_PARTNER)
_NONRELATIVE_PARENT_RELS = frozenset({ROOMMATE, OTHER_NONRELATIVE})


def _int(value, default: int = 0) -> int:
    try:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def has_subfamily_columns(household: pd.DataFrame) -> bool:
    return "SFN" in household.columns and "SFR" in household.columns


def subfamily_parents(child: pd.Series, household: pd.DataFrame) -> list:
    """Ids of the child's parents from the subfamily variables (empty when the child has no link).

    A subfamily child (SFR 4: child in a married-couple subfamily; 5 / 6: child of a mother / father alone)
    shares ``SFN`` with its parents, who carry SFR 2 (couple with children) or SFR 3 (parent alone).
    """
    if not has_subfamily_columns(household):
        return []
    sfn, sfr = child.get("SFN"), child.get("SFR")
    if pd.isna(sfn) or pd.isna(sfr) or _int(sfr) not in (4, 5, 6):
        return []
    same = household[(household["SFN"] == sfn) & household["SFR"].isin([2, 3])]
    if "SERIALNO" in household.columns and "SERIALNO" in child.index:
        same = same[same["SERIALNO"] == child["SERIALNO"]]
    return sorted(same.index)


def rule_candidates(child: pd.Series, adults: pd.DataFrame, calc_income: Callable[[pd.Series], float]):
    """Candidate parent units for a child with no subfamily link, as ``[(claimant_id, age_gap, income)]``.

    * grandchild (RELSHIPP 30): adult children (25-27) and children-in-law (32) of the householder; a married
      child and child-in-law are one unit (the child's id stands for the couple);
    * the householder's minor sibling (28): the householder's parent (29);
    * a nonrelative minor (34, 36): the householder's unmarried partner first (22, 24), else the other
      nonrelative adults (34, 36);
    * anything else (33 other relative, 25-27 own child, 35 foster): none: PUMS cannot say.

    Age gap must be 15 to 50 years.
    """
    rel = _int(child.get("RELSHIPP"))
    age = _int(child.get("AGEP"))
    if rel == GRANDCHILD:
        groups = [set(OWN_CHILD) | {CHILD_IN_LAW}]
    elif rel == SIBLING:
        groups = [{PARENT}]
    elif rel in _UNRELATED:
        groups = [set(UNMARRIED_PARTNER), set(_NONRELATIVE_PARENT_RELS)]
    else:
        return []
    for rels in groups:
        pool = adults[adults["RELSHIPP"].isin(rels)]
        found = []
        for pid, a in pool.iterrows():
            gap = _int(a.get("AGEP")) - age
            if MIN_PARENT_GAP <= gap <= MAX_PARENT_GAP:
                found.append((pid, gap, float(calc_income(a)), _int(a.get("RELSHIPP")), _int(a.get("MAR"))))
        if not found:
            continue
        if rel == GRANDCHILD:
            found = _merge_couples(found)
        return [(pid, gap, inc) for pid, gap, inc, *_ in found]
    return []


def _merge_couples(found: list[tuple]) -> list[tuple]:
    """A married adult child and child-in-law, one of each, are one candidate unit (the child's id)."""
    kids = [f for f in found if f[3] in OWN_CHILD and f[4] == 1]
    ins = [f for f in found if f[3] == CHILD_IN_LAW and f[4] == 1]
    if len(kids) == 1 and len(ins) == 1:
        k, i = kids[0], ins[0]
        merged = (k[0], k[1], k[2] + i[2], k[3], k[4])
        return [merged] + [f for f in found if f not in (k, i)]
    return found


def choose_parent(candidates: list[tuple], policy: str, already: dict[str, int]):
    """One candidate id, or None. ``candidates`` are ``(id, gap, income)``.

    ``low``: only an unambiguous candidate. ``nearest``: a candidate already chosen for an older sibling,
    else the gap closest to :data:`TYPICAL_PARENT_GAP`. ``high``: the lowest-income candidate (the one that
    gives the larger credit: the upper bound), a sibling's choice kept.
    """
    if not candidates:
        return None
    if policy == "low":
        return candidates[0][0] if len(candidates) == 1 else None
    if len(candidates) == 1:
        return candidates[0][0]
    for pid, _, _ in candidates:
        if pid in already:
            return pid
    if policy == "high":
        return min(candidates, key=lambda c: (c[2], abs(c[1] - TYPICAL_PARENT_GAP), str(c[0])))[0]
    return min(candidates, key=lambda c: (abs(c[1] - TYPICAL_PARENT_GAP), c[2], str(c[0])))[0]


def assign_claimants(
    household: pd.DataFrame,
    adults: pd.DataFrame,
    children: pd.DataFrame,
    *,
    parent_link: str,
    is_student: Callable[[pd.Series], bool],
    calc_income: Callable[[pd.Series], float],
    skip: Callable[[pd.Series], bool],
):
    """Claimant for every child. Returns ``(claims, basis)``.

    ``claims`` maps child id -> claimant id (an adult in ``adults``); children claimed by nobody are absent.
    ``basis`` maps child id -> how: ``own_child`` (householder's child), ``subfamily`` / ``rule`` (own parent
    found by the subfamily link / age-gap rule), ``householder`` (no parent present: the householder),
    ``student_householder`` (an unrelated enrolled child kept on the householder as before), or
    ``unclaimed``. ``subfamily`` and ``rule`` and ``own_child`` claims are the claimant's own child.
    """
    if parent_link not in PARENT_LINKS:
        raise ValueError(f"parent_link must be one of {PARENT_LINKS}, got {parent_link!r}")
    use_sfn = parent_link in ("sfn", "sfn+rule_nearest") and has_subfamily_columns(household)
    rule_policy = {"sfn": "nearest", "sfn+rule_nearest": "nearest", "rule_low": "low",
                   "rule_nearest": "nearest", "rule_high": "high"}[parent_link]
    householders = household[household["RELSHIPP"].isin([REFERENCE_PERSON, 1])]
    head = next((i for i in householders.index if i in adults.index), None)

    claims: dict = {}
    basis: dict = {}
    chosen: dict = {}

    for child_id, child in children.sort_values("AGEP", ascending=False, kind="stable").iterrows():
        if child_id in adults.index or skip(child):
            continue
        rel = _int(child.get("RELSHIPP"))
        age = _int(child.get("AGEP"))

        # The householder's spouse or unmarried partner, or the householder, who is a minor: nobody's dependent.
        if rel in _HOUSEHOLD_CORE:
            basis[child_id] = "unclaimed"
            continue

        # (a1) the householder's own child or foster child: the householder.
        if rel in OWN_CHILD or rel == FOSTER_CHILD:
            if head is not None:
                claims[child_id], basis[child_id] = head, "own_child"
            else:
                basis[child_id] = "unclaimed"
            continue

        # (a2) subfamily link.
        if use_sfn and age < 18:
            parents = [p for p in subfamily_parents(child, household) if p in adults.index]
            if parents:
                claims[child_id], basis[child_id] = parents[0], "subfamily"
                continue

        # (a3) rule: no link is possible (partner's / unrelated children, 18+, no SFN column), or the policy
        # applies the rule even to minors the subfamily variables leave without a parent.
        sfn_says_none = use_sfn and age < 18 and rel not in _UNRELATED
        if not sfn_says_none or parent_link == "sfn+rule_nearest":
            cands = rule_candidates(child, adults, calc_income)
            pid = choose_parent(cands, rule_policy, chosen)
            if pid is not None:
                chosen[pid] = chosen.get(pid, 0) + 1
                claims[child_id], basis[child_id] = pid, "rule"
                continue

        # (b)/(c) no parent: the householder for a relative; unrelated children keep the legacy treatment.
        if rel in _UNRELATED:
            if is_student(child) and head is not None:
                claims[child_id], basis[child_id] = head, "student_householder"
            else:
                basis[child_id] = "unclaimed"
        elif head is not None:
            claims[child_id], basis[child_id] = head, "householder"
        else:
            basis[child_id] = "unclaimed"

    return claims, basis


#: Relationship an own-child claim carries into the credit code (``dependents_details``): the child of the
#: claimant, whatever the child's relationship to the householder.
OWN_CHILD_CODE = BIO_CHILD
OWN_CHILD_BASES = frozenset({"subfamily", "rule"})
