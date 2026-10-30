"""
Head of Household status determination.

This module contains logic for determining if a taxpayer qualifies for
Head of Household filing status.
"""

from __future__ import annotations

import pandas as pd

from tax_modeler.units.relshipp_codes import (
    EITC_QUALIFYING_CHILD_RELS,
    FOSTER_CHILD,
    GRANDCHILD,
    OWN_CHILD,
    QUALIFYING_RELATIVE_RELS,
)


def is_head_of_household(
    person: pd.Series, 
    person_data: pd.DataFrame,
    has_dependents: bool = False
) -> bool:
    """
    Determine if a person qualifies as Head of Household.
    
    Args:
        person: The person's data
        person_data: Full person data for reference
        has_dependents: Whether this person has dependents in their tax unit
        
    Returns:
        bool: True if qualifies as Head of Household
    """
    import logging
    logger = logging.getLogger(__name__)
    
    person_id = person.name
    _person_age = person.get('AGEP', 0)
    _person_rel = person.get('RELSHIPP', 0)
    
    # Must be unmarried or considered unmarried on the last day of the year
    if not _is_unmarried(person, person_data):
        logger.debug(f"Person {person_id} failed HoH: not unmarried (MAR={person.get('MAR', 'N/A')})")
        return False
    
    # STRICT: Must actually have dependents in their tax unit
    # HoH requires claiming a qualifying person as a dependent
    if not has_dependents:
        logger.debug(f"Person {person_id} failed HoH: no dependents in tax unit")
        return False
        
    # Must have a qualifying person (usually a child) living with them
    if not _has_qualifying_person(person, person_data):
        logger.debug(f"Person {person_id} failed HoH: no qualifying person in household")
        return False
        
    # Must have paid more than half the cost of keeping up a home
    if not _paid_half_home_cost(person, person_data):
        logger.debug(f"Person {person_id} failed HoH: did not pay half home cost")
        return False
    
    logger.debug(f"Person {person_id} qualifies as Head of Household")
    return True

def _is_unmarried(person: pd.Series, person_data: pd.DataFrame) -> bool:
    """Check if person is unmarried for HOH purposes.
    
    This includes:
    1. Never married, divorced, separated, widowed
    2. Married but "considered unmarried" (spouse not in household for last 6 months)
    """
    marital_status = person.get('MAR', 0)
    
    # Unmarried includes: widowed (2), divorced (3), separated (4), never married (5)
    if marital_status in [2, 3, 4, 5]:
        return True
    
    # If married (1), check if "considered unmarried"
    # A married person is considered unmarried for HoH if:
    # - Spouse did not live in home during last 6 months of year
    # - They paid more than half the cost of keeping up the home
    # - Home was the main home of their child/stepchild for more than half the year
    if marital_status == 1:
        household_id = person.get('SERIALNO')
        if not household_id:
            return False
            
        # Filter to same household only
        household = person_data[person_data['SERIALNO'] == household_id]
        person_rel = person.get('RELSHIPP', 0)
        
        # If person is householder (20), check if spouse (21) is in same household
        if person_rel == 20:
            spouse_present = any(household['RELSHIPP'] == 21)
            # If spouse is NOT present, can be "considered unmarried"
            if not spouse_present:
                return True
        
        # If person is spouse (21) but no householder (20) present
        # This is rare but can happen in PUMS data
        if person_rel == 21:
            householder_present = any(household['RELSHIPP'] == 20)
            if not householder_present:
                return True
    
    return False

def _has_qualifying_person(person: pd.Series, person_data: pd.DataFrame) -> bool:
    """Check if person has a qualifying person (child or relative) for HoH.

    Routes through :func:`_is_qualifying_child` / :func:`_is_qualifying_relative`,
    which decode relationships via the canonical Census 2019+ RELSHIPP sets
    (see :mod:`tax_modeler.units.relshipp_codes`). The prior implementation used
    the pre-2019 RELP scheme inline — ``[22, 23, 24, 25]`` as "child/grandchild"
    (actually opposite/same-sex partner, same-sex spouse, and biological child),
    ``34`` as "foster child" (actually roommate), and ``27`` as "parent"
    (actually stepchild). That denied HoH to stepchild/adopted/grandchild
    families and granted it to households with an unmarried partner or roommate
    (audit B3).
    """
    import logging
    logger = logging.getLogger(__name__)

    household_id = person.get('SERIALNO')
    person_id = person.name

    if not household_id:
        logger.debug(f"Person {person_id} has no SERIALNO")
        return False

    household = person_data[person_data['SERIALNO'] == household_id]

    for _, other_person in household.iterrows():
        if other_person.name == person.name:
            continue
        if _is_qualifying_child(other_person, person, person_data):
            logger.debug(f"  Person {other_person.name} is a qualifying child for HoH")
            return True
        if _is_qualifying_relative(other_person, person, person_data):
            logger.debug(f"  Person {other_person.name} is a qualifying relative for HoH")
            return True

    logger.debug(f"Person {person_id} has no qualifying persons in household")
    return False

def _has_strict_qualifying_person(person: pd.Series, person_data: pd.DataFrame) -> bool:
    """Check if person has a strict qualifying person for HOH (more restrictive criteria)."""
    import logging
    logger = logging.getLogger(__name__)
    
    household_id = person.get('SERIALNO')
    person_id = person.name
    
    if not household_id:
        return False
        
    household = person_data[person_data['SERIALNO'] == household_id]
    
    # STRICT: Only direct children or grandchildren under 19, or disabled dependents
    for _, other_person in household.iterrows():
        if other_person.name == person.name:
            continue
            
        other_rel = other_person.get('RELSHIPP', 0)
        other_age = other_person.get('AGEP', 0)
        other_id = other_person.name
        
        # Only direct children or grandchildren (canonical RELSHIPP).
        if other_rel in (OWN_CHILD | {GRANDCHILD}):
            # Must be under 19 (no student exception for strict criteria)
            if other_age < 19:
                logger.debug(f"  Person {other_id} qualifies as strict qualifying child (age {other_age})")
                return True
            
            # Or disabled dependent of any age
            disability = other_person.get('DIS', 0)
            if disability == 1:  # Has disability
                logger.debug(f"  Person {other_id} qualifies as disabled dependent")
                return True
    
    logger.debug(f"Person {person_id} has no strict qualifying persons")
    return False

def _is_qualifying_child(
    child: pd.Series, 
    potential_hoh: pd.Series,
    person_data: pd.DataFrame
) -> bool:
    """Check if a person is a qualifying child of the potential HOH."""
    # Relationship to filer must be an EITC-style qualifying-child relationship
    # (own child / sibling / grandchild / foster child) -- canonical Census
    # 2019+ RELSHIPP codes; see tax_modeler.units.relshipp_codes.
    rel = child.get('RELSHIPP', 0)

    if rel not in EITC_QUALIFYING_CHILD_RELS:
        return False
        
    # Age test - must be under 19, or under 24 if full-time student, or any age if disabled
    age = child.get('AGEP', 0)
    if age >= 19:
        # Check if full-time student under 24
        if age < 24:
            school_level = child.get('SCHL', 0)
            # SCHL codes 16-21 indicate college/graduate school
            if school_level < 16:  # Not in college
                return False
        else:
            # Over 24, must be disabled
            disability = child.get('DIS', 2)
            if disability != 1:  # Not disabled
                return False
        
    # Residency test - must live with filer for more than half the year
    # Assuming same household means they live together (PUMS limitation)
    
    # Support test - cannot provide more than half of their own support
    child_income = _calculate_income(child)
    if child_income > 4300:  # 2023 threshold, should be configurable
        return False
        
    # Not filing a joint return (unless only to claim refund)
    if child.get('MAR') == 1:  # Married
        # In PUMS, we can't easily determine if it's just for refund
        # So we'll be conservative and exclude married children
        return False
        
    return True

def _is_qualifying_relative(
    relative: pd.Series, 
    potential_hoh: pd.Series,
    person_data: pd.DataFrame
) -> bool:
    """Check if a person is a qualifying relative of the potential HOH."""
    # Can't be a qualifying child of the filer or anyone else
    if _is_qualifying_child(relative, potential_hoh, person_data):
        return False
        
    # Relationship test - must be a blood/marriage relative (canonical
    # RELSHIPP; see tax_modeler.units.relshipp_codes.QUALIFYING_RELATIVE_RELS).
    rel = relative.get('RELSHIPP', 0)

    if rel not in QUALIFYING_RELATIVE_RELS:
        return False
        
    # Gross income test
    income = _calculate_income(relative)
    if income >= 4300:  # 2023 amount, should be configurable
        return False
        
    # Support test - filer must provide more than half of support
    # This is simplified - in reality would need detailed expense data
    hoh_income = _calculate_income(potential_hoh)
    if hoh_income <= income:  # Simplified support test
        return False
        
    # Not filing a joint return (unless only to claim refund)
    if relative.get('MAR') == 1:  # Married
        return False
        
    return True

def _paid_half_home_cost(person: pd.Series, person_data: pd.DataFrame) -> bool:
    """Check if the person paid more than half the cost of keeping up a home.
    
    For PUMS data, we'll use a realistic approach that considers:
    1. Householder status (primary indicator)
    2. Income contribution in multi-adult households
    3. Presence of dependents (indicates responsibility for household)
    """
    import logging
    logger = logging.getLogger(__name__)
    
    person_id = person.name
    person_rel = person.get('RELSHIPP', 0)
    
    # If person is the householder (RELSHIPP=20), they are almost certainly paying housing costs
    if person_rel == 20:
        logger.debug(f"Person {person_id} qualifies for home cost test as householder")
        return True
        
    household_id = person.get('SERIALNO')
    if not household_id:
        logger.debug(f"Person {person_id} has no SERIALNO")
        return False
        
    # Get household members
    household = person_data[person_data['SERIALNO'] == household_id]
    
    # Count number of adults in household
    adults = household[household['AGEP'] >= 18]
    num_adults = len(adults)
    
    # If only one adult, they must be paying the costs
    if num_adults == 1:
        logger.debug(f"Person {person_id} qualifies as only adult in household")
        return True
        
    # Check if this person has qualifying dependents in the household
    has_qualifying_dependents = False
    qualifying_dependents = []
    
    for _, other_person in household.iterrows():
        if other_person.name == person.name:
            continue
            
        other_rel = other_person.get('RELSHIPP', 0)
        other_age = other_person.get('AGEP', 0)
        
        # Check for children/dependents of this person (canonical RELSHIPP:
        # own child / grandchild / foster child).
        if other_rel in (OWN_CHILD | {GRANDCHILD, FOSTER_CHILD}):
            if other_age < 19 or (other_age < 24 and other_person.get('SCHL', 0) >= 16) or other_person.get('DIS', 2) == 1:
                has_qualifying_dependents = True
                qualifying_dependents.append(other_person.name)
    
    logger.debug(f"Person {person_id} has {len(qualifying_dependents)} qualifying dependents: {qualifying_dependents}")
    
    # If this person has qualifying dependents, be extremely lenient with cost test
    if has_qualifying_dependents:
        person_income = _calculate_income(person)
        total_adult_income = sum(_calculate_income(adult) for _, adult in adults.iterrows())
        
        logger.debug(f"Person {person_id} income: {person_income}, total adult income: {total_adult_income}")
        
        # For households with 2 adults where one has dependents
        if num_adults == 2:
            if total_adult_income > 0:
                contribution_ratio = person_income / total_adult_income
                logger.debug(f"Person {person_id} contribution ratio: {contribution_ratio:.3f}")
                # Extremely lenient threshold for parents with dependents (5%)
                if contribution_ratio >= 0.05:
                    logger.debug(f"Person {person_id} qualifies with {contribution_ratio:.1%} contribution (2 adults with dependents)")
                    return True
                else:
                    logger.debug(f"Person {person_id} failed home cost test: {contribution_ratio:.1%} < 5% (2 adults with dependents)")
                    return False
            else:
                # If no income data, assume parent with dependents pays costs
                logger.debug(f"Person {person_id} qualifies due to no income data but has dependents")
                return True
        
        # For households with 3+ adults where one has dependents
        elif num_adults >= 3:
            if total_adult_income > 0:
                contribution_ratio = person_income / total_adult_income
                logger.debug(f"Person {person_id} contribution ratio: {contribution_ratio:.3f}")
                # Extremely lenient threshold for parents with dependents (3%)
                if contribution_ratio >= 0.03:
                    logger.debug(f"Person {person_id} qualifies with {contribution_ratio:.1%} contribution (3+ adults with dependents)")
                    return True
                else:
                    logger.debug(f"Person {person_id} failed home cost test: {contribution_ratio:.1%} < 3% (3+ adults with dependents)")
                    return False
            else:
                # If no income data, assume parent with dependents pays costs
                logger.debug(f"Person {person_id} qualifies due to no income data but has dependents")
                return True
    
    # For people without qualifying dependents, check if they might still qualify
    # based on relationship and income contribution
    person_income = _calculate_income(person)
    total_adult_income = sum(_calculate_income(adult) for _, adult in adults.iterrows())
    
    if total_adult_income > 0:
        contribution_ratio = person_income / total_adult_income
        logger.debug(f"Person {person_id} (no dependents) contribution ratio: {contribution_ratio:.3f}")
        
        # For 2 adults without dependents, use moderate threshold
        if num_adults == 2 and contribution_ratio >= 0.35:
            logger.debug(f"Person {person_id} qualifies with {contribution_ratio:.1%} contribution (2 adults, no dependents)")
            return True
        
        # For 3+ adults without dependents, check if they contribute more than fair share
        elif num_adults >= 3:
            fair_share = 1.0 / num_adults
            if contribution_ratio > fair_share * 1.2:  # 20% above fair share
                logger.debug(f"Person {person_id} qualifies with {contribution_ratio:.1%} vs fair share {fair_share:.1%}")
                return True
    
    logger.debug(f"Person {person_id} failed home cost test")
    return False

def _calculate_income(person: pd.Series) -> float:
    """Calculate total income for a person."""
    # Use PINCP (total person income) if available, otherwise sum components
    pincp = person.get('PINCP', 0)
    if pincp and pincp > 0:
        # Apply adjustment factor
        # CRITICAL: ADJINC in PUMS is stored as integer (e.g., 1184371 = 1.184371)
        # Must divide by 1,000,000 to get the actual adjustment factor
        adjinc_raw = person.get('ADJINC', 1000000)
        adjinc = float(adjinc_raw) / 1000000.0 if adjinc_raw and adjinc_raw > 0 else 1.0
        return float(pincp) * adjinc
    
    # Fallback to summing individual components
    income = 0.0
    for col in ['WAGP', 'SEMP', 'INTP', 'RETP', 'SSP', 'SSIP', 'PAP', 'OIP']:
        value = person.get(col, 0)
        if value:
            income += float(value)
    
    # Apply adjustment factor
    # CRITICAL: ADJINC in PUMS is stored as integer (e.g., 1184371 = 1.184371)
    adjinc_raw = person.get('ADJINC', 1000000)
    adjinc = float(adjinc_raw) / 1000000.0 if adjinc_raw and adjinc_raw > 0 else 1.0
    income *= adjinc
    
    return income
