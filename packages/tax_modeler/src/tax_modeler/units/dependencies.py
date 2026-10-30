"""
Dependency identification for tax purposes.

This module provides functions for identifying dependents and
qualifying relatives for tax purposes.
"""

from __future__ import annotations

import pandas as pd

from tax_modeler.units.relshipp_codes import (
    FOSTER_CHILD,
    GRANDCHILD,
    OWN_CHILD,
    QUALIFYING_RELATIVE_RELS,
    REFERENCE_PERSON,
    SPOUSE,
)

# IRS qualifying-relative gross-income limit by tax year (Rev. Proc. inflation
# adjustments). A would-be qualifying relative earning at/above this cannot be
# claimed as a dependent.
_QR_GROSS_INCOME_LIMIT_BY_YEAR = {2022: 4_400, 2023: 4_700, 2024: 5_050, 2025: 5_200}
_DEFAULT_DEP_TAX_YEAR = 2023  # matches the DOTAX Table A8 calibration anchor


def _qr_gross_income_limit(tax_year: int) -> int:
    return _QR_GROSS_INCOME_LIMIT_BY_YEAR.get(
        tax_year, _QR_GROSS_INCOME_LIMIT_BY_YEAR[_DEFAULT_DEP_TAX_YEAR]
    )


# Self-support income floors. Below these, a household member living rent-free
# is assumed NOT to have provided over half their own support (imputed parental
# housing + food in Hawaii is roughly $15-25K/yr), so they remain a dependent.
# PUMS has no direct support data, so these income proxies stand in for the
# IRS support test.
_SELF_SUPPORT_FLOOR_MINOR = 15_000    # age < 19
_SELF_SUPPORT_FLOOR_STUDENT = 20_000  # age 19-23, student
_SELF_SUPPORT_FLOOR_ADULT = 25_000    # age >= 24


def _self_support_floor(age: float) -> int:
    if age < 19:
        return _SELF_SUPPORT_FLOOR_MINOR
    if age < 24:
        return _SELF_SUPPORT_FLOOR_STUDENT
    return _SELF_SUPPORT_FLOOR_ADULT


def identify_dependents(
    household: pd.DataFrame, tax_year: int = _DEFAULT_DEP_TAX_YEAR
) -> dict[str, list[str]]:
    """
    Identify all potential dependents in a household.
    
    Args:
        household: DataFrame containing all persons in a household
        
    Returns:
        dict: Mapping from person ID to list of their potential dependents
    """
    if household.empty:
        return {}
        
    # Initialize result
    dependents = {person_id: [] for person_id in household.index}
    
    # Get all adults in the household (potential filers)
    # For tax purposes, students under 24 can still be dependents
    # In PUMS, SCHL is the education level, where 1-24 indicates various levels of education
    # We'll consider someone a student if they are enrolled in school (SCHL >= 15 for college)
    # However, householders (RELSHIPP=20) and spouses (RELSHIPP=21) are always filers,
    # even if they are students under 24.
    # A young person 18-23 is only a potential *dependent* if they are actually
    # ENROLLED in school (PUMS SCH), not merely if they have a high-school-level
    # attainment (SCHL). The prior ``SCHL >= 15`` test (attainment) classified
    # every 18-23-year-old with a HS diploma as a "young student", so working,
    # non-enrolled adult children were pulled in as dependents with no support
    # test (audit B4). ``_is_student`` reads SCH enrollment (2/3) with a SCHL
    # fallback only when SCH is absent.
    enrolled = household.apply(_is_student, axis=1)
    is_adult_age = (household['AGEP'] >= 18)
    is_young_student = (household['AGEP'] >= 18) & (household['AGEP'] < 24) & enrolled
    is_householder_or_spouse = household['RELSHIPP'].isin([20, 21, 1, 2])
    adults = household[
        is_adult_age & (~is_young_student | is_householder_or_spouse)
    ].copy()

    # Children/students: anyone under 18, or an enrolled 18-23-year-old.
    children = household[
        (household['AGEP'] < 18)  # Under 18
        | is_young_student        # enrolled 18-23
    ].copy()

    # First, assign children and students to potential filers
    for _, child in children.iterrows():
        child_id = child.name

        # Skip if this is already an adult filer
        if child_id in adults.index:
            continue

        # An adult (18+) who provides over half of their own support is NOT a
        # dependent (IRS qualifying-child support test). Without this, an
        # enrolled 18-23-year-old with substantial earnings would be claimed as
        # a dependent and their income folded into the guardian's unit (audit
        # B4/B5). They fall through to filing for themselves in the constructor.
        if child.get('AGEP', 0) >= 18 and _provides_over_half_own_support(child, household):
            continue

        # Find potential parents/guardians
        potential_guardians = _find_potential_guardians(child, adults, household)
        # Safety: only keep guardians that exist in the adults DataFrame
        potential_guardians = [g for g in potential_guardians if g in adults.index]

        if potential_guardians:
            # Prioritize guardians to maximize credit benefit:
            # 1. Householder (RELSHIPP=20 or 1) — primary taxpayer
            # 2. Spouse (RELSHIPP=21 or 2) — part of the joint return
            # 3. All others, sorted by ascending income (lower income gets more ACTC benefit)
            def _guardian_sort_key(gid):
                g = adults.loc[gid]
                rel = g.get('RELSHIPP', 0)
                if rel in [20, 1]:   # householder
                    return (0, 0)
                if rel in [21, 2]:   # spouse
                    return (1, 0)
                income = _calculate_income(g)
                return (2, income)

            guardian_id = sorted(potential_guardians, key=_guardian_sort_key)[0]
            dependents[guardian_id].append(child_id)
    
    # Next, identify other potential dependents (qualifying relatives)
    for _, adult in adults.iterrows():
        adult_id = adult.name
        
        # Skip if this adult is already a dependent
        if any(adult_id in deps for deps in dependents.values()):
            continue
            
        # Check if this adult could be a qualifying relative of another adult
        for _, potential_guardian in adults[adults.index != adult_id].iterrows():
            if _is_qualifying_relative(adult, potential_guardian, household, tax_year):
                dependents[potential_guardian.name].append(adult_id)
                break
    
    return dependents

def _find_potential_guardians(
    child: pd.Series, 
    potential_guardians: pd.DataFrame,
    household: pd.DataFrame
) -> list[str]:
    """
    Find potential guardians for a child or student.
    
    Args:
        child: The child's or student's data
        potential_guardians: DataFrame of potential guardians
        household: Full household data for reference
        
    Returns:
        list: List of potential guardian IDs
    """
    potential = []
    
    # Check each potential guardian
    for _, guardian in potential_guardians.iterrows():
        guardian_id = guardian.name
        
        # Check if guardian is a parent
        if _is_parent(guardian, child, household):
            potential.append(guardian_id)
            continue
            
        # Check if guardian is a stepparent
        if _is_stepparent(guardian, child, household):
            potential.append(guardian_id)
            continue
            
        # Check if guardian is a foster parent
        if _is_foster_parent(guardian, child, household):
            potential.append(guardian_id)
            continue
            
        # For students, also consider the primary filer (householder) as a potential guardian
        # PUMS code 20 = householder; test data may use code 1
        if (_is_student(child) and
            guardian.get('RELSHIPP') in [20, 1] and  # Primary filer (householder)
            _lived_with_all_year(child, guardian, household)):  # Lives with the primary filer
            potential.append(guardian_id)
            continue

    # If no guardians found and this is a student, default to the primary filer if they live together.
    # Search potential_guardians (adults) first; fall back to full household only if found there too.
    if not potential and _is_student(child):
        primary_filer = potential_guardians[potential_guardians['RELSHIPP'].isin([20, 1])]
        if primary_filer.empty:
            # Householder might not be in adults (shouldn't happen now, but be safe)
            primary_filer = household[household['RELSHIPP'].isin([20, 1])]
        if not primary_filer.empty and _lived_with_all_year(child, primary_filer.iloc[0], household):
            potential.append(primary_filer.index[0])
    
    return potential

def _is_parent(adult: pd.Series, child: pd.Series, household: pd.DataFrame) -> bool:
    """Check if adult is likely the parent of the child."""
    
    # Age difference should be reasonable (at least 15 years)
    age_diff = adult.get('AGEP', 0) - child.get('AGEP', 0)
    if age_diff < 15:
        return False
    
    # Check relationship codes (canonical Census 2019+ RELSHIPP; see
    # tax_modeler.units.relshipp_codes). A householder (20) or their spouse
    # (21) is treated as the parent of an own child (bio 25 / adopted 26 /
    # step 27) or grandchild (30) living in the household.
    adult_rel = adult.get('RELSHIPP', 0)
    child_rel = child.get('RELSHIPP', 0)

    _parent_child_codes = OWN_CHILD | {GRANDCHILD}

    if adult_rel == REFERENCE_PERSON and child_rel in _parent_child_codes:
        return True

    # Spouse of the householder (21 = opposite-sex, 23 = same-sex)
    if adult_rel in {21, 23} and child_rel in _parent_child_codes:
        return True

    # Foster child relationship
    if child_rel == FOSTER_CHILD:
        return True

    return False

def _is_stepparent(guardian: pd.Series, child: pd.Series, household: pd.DataFrame) -> bool:
    """Check if guardian is a stepparent of the child."""
    # Check if guardian is married to a parent of the child
    if guardian.get('MAR') == 1:  # Married
        # Find guardian's spouse
        spouse_id = _find_spouse(guardian, household)
        if spouse_id and spouse_id in household.index:
            spouse = household.loc[spouse_id]
            # Check if spouse is a parent of the child
            if _is_parent(spouse, child, household):
                return True
    
    return False

def _is_foster_parent(guardian: pd.Series, child: pd.Series, household: pd.DataFrame) -> bool:
    """Check if guardian is a foster parent of the child."""
    # Canonical RELSHIPP: 35 = Foster child, 20 = householder.
    child_rel = child.get('RELSHIPP')
    guardian_rel = guardian.get('RELSHIPP')
    if child_rel == FOSTER_CHILD and guardian_rel == REFERENCE_PERSON:
        return True

    return False

def _find_spouse(person: pd.Series, household: pd.DataFrame) -> str | None:
    """Find the spouse of a person in the household."""
    if person.get('MAR') != 1:  # Not married
        return None
        
    # Check all other household members for a spouse
    for _, member in household[household.index != person.name].iterrows():
        if _are_spouses(person, member):
            return member.name
            
    return None

def _are_spouses(person1: pd.Series, person2: pd.Series) -> bool:
    """Check if two people are spouses."""
    # Both must be marked as married
    if person1.get('MAR') != 1 or person2.get('MAR') != 1:
        return False

    # Check relationship codes. Canonical RELSHIPP: householder=20,
    # spouse=21 (opposite-sex) or 23 (same-sex).
    rel1 = person1.get('RELSHIPP', 0)
    rel2 = person2.get('RELSHIPP', 0)

    return (
        (rel1 == REFERENCE_PERSON and rel2 in SPOUSE) or
        (rel2 == REFERENCE_PERSON and rel1 in SPOUSE)
    )

def _is_qualifying_relative(
    person: pd.Series,
    potential_guardian: pd.Series,
    household: pd.DataFrame,
    tax_year: int = _DEFAULT_DEP_TAX_YEAR,
) -> bool:
    """
    Check if a person is a qualifying relative of another person.

    Applies the IRS qualifying-relative tests (IRC §152(d)):
      - not a qualifying child of the guardian
      - relationship-or-member-of-household test (PUMS RELSHIPP codes)
      - gross-income test (year-aware limit)
      - support test (guardian provides over half)
      - not filing a joint return

    Args:
        person: The potential dependent
        potential_guardian: The potential guardian
        household: Full household data for reference
        tax_year: Tax year (selects the gross-income limit)

    Returns:
        bool: True if person is a qualifying relative of potential_guardian
    """
    # Can't be a qualifying child of the potential guardian
    if _is_qualifying_child(person, potential_guardian, household):
        return False

    # Relationship-or-member-of-household + residency test
    if not (_is_relative(person, potential_guardian)
            and _lived_with_all_year(person, potential_guardian, household)):
        return False

    # Gross-income test (year-aware): must earn under the limit
    if _calculate_income(person) >= _qr_gross_income_limit(tax_year):
        return False

    # Support test: guardian must provide over half of the person's support
    if not _provides_over_half_support(person, potential_guardian, household):
        return False

    # Not filing a joint return (unless only to claim a refund)
    if person.get('MAR') == 1:  # Married
        return False

    return True

def _is_qualifying_child(
    child: pd.Series, 
    potential_guardian: pd.Series,
    household: pd.DataFrame
) -> bool:
    """Check if a person is a qualifying child of another person."""
    # Age test
    age = child.get('AGEP', 0)
    
    # Must be under 19, or under 24 if a student, or any age if permanently disabled
    if age >= 19:
        # Check if a student
        if age < 24 and _is_student(child):
            pass  # Continue with other tests
        else:
            return False
    
    # Relationship test
    if not _is_child_relationship(child, potential_guardian, household):
        return False
        
    # Support test - child must not provide over half their own support
    if _provides_over_half_own_support(child, household):
        return False
        
    # Must have lived with the potential guardian for more than half the year
    if not _lived_with_all_year(child, potential_guardian, household):
        return False
        
    # Cannot file a joint return (unless only to claim a refund)
    if child.get('MAR') == 1:  # Married
        return False
        
    # Must be younger than the potential guardian
    guardian_age = potential_guardian.get('AGEP', 0)
    if age >= guardian_age:
        return False
        
    return True

def _is_child_relationship(
    child: pd.Series, 
    potential_guardian: pd.Series,
    household: pd.DataFrame
) -> bool:
    """Check if the relationship is a qualifying child relationship."""
    # Check if potential_guardian is a parent, stepparent, or foster parent.
    # _is_parent already covers own child (25/26/27) and grandchild (30) of
    # the householder or their spouse.
    if (_is_parent(potential_guardian, child, household) or
            _is_stepparent(potential_guardian, child, household) or
            _is_foster_parent(potential_guardian, child, household)):
        return True

    return False

def _is_student(person: pd.Series) -> bool:
    """Check if a person is currently enrolled in school.

    PUMS ``SCH`` (school enrollment) coding:
        1 = No, has NOT attended in the last 3 months
        2 = Yes, public school / public college
        3 = Yes, private school / private college / home school
    Enrolled is therefore ``SCH in (2, 3)`` — the prior ``SCH == 1`` test was
    inverted, flagging every non-enrolled person as a student and every actual
    student as a non-student (audit B2).
    """
    # Check school enrollment (preferred — actual enrollment, not attainment).
    if 'SCH' in person:
        sch = person['SCH']
        if pd.notna(sch):
            try:
                return int(sch) in (2, 3)
            except (TypeError, ValueError):
                pass

    # Fallback when SCH is absent/NA: approximate with attainment + age.
    # SCHL is educational ATTAINMENT, not enrollment, so this is only a proxy.
    age = person.get('AGEP', 0)
    education = person.get('SCHL', 0)

    # In college or graduate school
    if education >= 16 and age <= 24:
        return True

    return False

def _lived_with_all_year(
    person1: pd.Series, 
    person2: pd.Series,
    household: pd.DataFrame
) -> bool:
    """
    Check if two people lived together for the entire year.
    
    This is a simplified check based on being in the same household.
    In reality, would need more detailed data.
    """
    # If they're in the same household, assume they lived together all year
    return person1.get('SERIALNO') == person2.get('SERIALNO')

def _provides_over_half_support(
    person: pd.Series, 
    potential_guardian: pd.Series,
    household: pd.DataFrame
) -> bool:
    """
    Check if potential_guardian provides over half of person's support.
    
    This is a simplified check based on income.
    In reality, would need more detailed data on support.
    """
    person_income = _calculate_income(person)
    guardian_income = _calculate_income(potential_guardian)
    age = float(person.get('AGEP', 0) or 0)

    # The guardian is assumed to provide over half of support when the person's
    # own income is below the age-appropriate self-support floor (imputed
    # parental housing/food dominates) AND the guardian out-earns the person.
    floor = _self_support_floor(age)
    return person_income <= floor and guardian_income > person_income


def _provides_over_half_own_support(person: pd.Series, household: pd.DataFrame) -> bool:
    """
    Check if a person provides over half of their own support.

    PUMS has no support data, so income stands in: a household member living
    rent-free who earns below the age-appropriate floor is assumed NOT to have
    provided over half their own support (imputed parental housing + food in
    Hawaii is roughly $15-25K/yr). Above the floor, own earnings plausibly
    exceed half of total support.
    """
    person_income = _calculate_income(person)
    age = float(person.get('AGEP', 0) or 0)
    return person_income > _self_support_floor(age)

# Canonical RELSHIPP codes that denote a blood/marriage relative of the
# householder eligible to be a qualifying relative (see
# tax_modeler.units.relshipp_codes.QUALIFYING_RELATIVE_RELS): own children,
# sibling, parent, grandchild, in-laws, other relative, foster child.
# Householder (20), spouses (21/23) and unmarried partners (22/24) are NOT
# in this set — spouses file together and the householder is the reference
# point, not a dependent.
_RELATIVE_RELSHIPP_CODES = QUALIFYING_RELATIVE_RELS


def _is_relative(person1: pd.Series, person2: pd.Series) -> bool:
    """Check whether two household members are related, using PUMS RELSHIPP.

    RELSHIPP encodes each person's relationship to the *householder*, not a
    pairwise relationship. So:
      - householder (20) is related to anyone coded as a relative;
      - two relative-coded members are treated as related to each other
        (e.g. the householder's adult child claiming the householder's
        parent), which is the modeling assumption that lets a non-householder
        guardian claim a non-householder relative.
    """
    def _rel(p):
        try:
            return int(p.get('RELSHIPP', 0) or 0)
        except (TypeError, ValueError):
            return 0

    r1, r2 = _rel(person1), _rel(person2)

    # Householder related to any coded relative (either direction).
    if r1 == 20 and r2 in _RELATIVE_RELSHIPP_CODES:
        return True
    if r2 == 20 and r1 in _RELATIVE_RELSHIPP_CODES:
        return True
    # Two relatives of the same householder.
    if r1 in _RELATIVE_RELSHIPP_CODES and r2 in _RELATIVE_RELSHIPP_CODES:
        return True
    return False

def _adjinc_factor(person: pd.Series) -> float:
    """Return ADJINC as a multiplicative factor (~1.0).

    PUMS stores ADJINC as an integer (e.g. 1184371 → 1.184371), but some
    fixtures/test data store it already as a factor (e.g. 1.0). Treat any
    value > 2 as the raw integer form (divide by 1e6); values <= 2 are taken
    as an already-applied factor. Guards against silently zeroing incomes when
    ADJINC=1.0 is divided by 1e6.
    """
    raw = person.get('ADJINC', 1.0)
    try:
        raw = float(raw)
    except (TypeError, ValueError):
        return 1.0
    if raw <= 0:
        return 1.0
    return raw / 1_000_000.0 if raw > 2 else raw


def _calculate_income(person: pd.Series) -> float:
    """Calculate total income for a person."""
    # Use PINCP if available
    pincp = person.get('PINCP', 0)
    if pincp and pincp > 0:
        return float(pincp) * _adjinc_factor(person)

    # Fallback to summing components
    income = 0.0
    for col in ['WAGP', 'SEMP', 'INTP', 'RETP', 'SSP', 'SSIP', 'PAP', 'OIP']:
        income += float(person.get(col, 0) or 0)

    return income * _adjinc_factor(person)
