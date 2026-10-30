"""
Married Filing Separately status determination.

This module contains logic for determining if a taxpayer qualifies for
Married Filing Separately status.
"""

from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)

def is_married_filing_separately(
    person1: pd.Series, 
    person2: pd.Series, 
    person_data: pd.DataFrame
) -> bool:
    """
    Determine if a married person should file as Married Filing Separately.
    
    Args:
        person1: First person's data
        person2: Second person's data (spouse)
        person_data: Full person data for reference
        
    Returns:
        bool: True if they should file separately
    """
    logger.debug(f"MFS Check: Starting for persons with SERIALNO {person1.get('SERIALNO')} and {person2.get('SERIALNO')}")
    
    # Must be married to each other
    if not _are_married(person1, person2):
        logger.debug("MFS Check: Not married to each other, returning False")
        return False
    
    logger.debug("MFS Check: Confirmed married to each other")
    
    # Get incomes
    income1 = _calculate_income(person1)
    income2 = _calculate_income(person2)
    
    # Strong indicators for filing separately (VERY CONSERVATIVE):
    
    # 1. Extreme income disparity (one spouse earns significantly more)
    # Made much more conservative to reduce MFS rate
    if income1 > 0 and income2 > 0:
        ratio = max(income1, income2) / min(income1, income2)
        logger.debug(f"Income ratio check - income1: {income1}, income2: {income2}, ratio: {ratio}")
        
        # Relaxed threshold for income disparity
        # If income ratio is significant (10:1 or higher) and at least one earns a reasonable income
        max_income = max(income1, income2)
        min_income = min(income1, income2)
        
        # Case 1: More moderate income differences
        if ratio >= 5 and max_income > 50000 and min_income < 25000:
            logger.debug(f"  Moderate income ratio {ratio:.1f}:1 (${max_income:,.0f} vs ${min_income:,.0f}), considering MFS")
            return True
            
        # Case 2: High earner with moderate income difference
        if ratio >= 3 and max_income > 200000:
            logger.debug(f"  Moderate earner (${max_income:,.0f}) with ratio {ratio:.1f}:1, considering MFS")
            return True
    
    # 2. More lenient business loss criteria
    if (income1 < -20000 and income2 > 50000) or (income2 < -20000 and income1 > 50000):
        logger.debug(f"  Business loss detected (${income1:,.0f} vs ${income2:,.0f}), considering MFS")
        return True
    
    # 3. More lenient disability criteria
    if 'DIS' in person1 or 'DIS' in person2:
        if person1.get('DIS', 2) != person2.get('DIS', 2):
            if person1.get('DIS') == 1 or person2.get('DIS') == 1:
                # Lower income threshold for itemizing
                if max(income1, income2) > 40000:
                    logger.debug("  Disability status difference detected, considering MFS")
                    return True
    
    # 4. More lenient citizenship status criteria
    if 'CIT' in person1 and 'CIT' in person2:
        cit1 = person1.get('CIT', 0)
        cit2 = person2.get('CIT', 0)
        logger.debug(f"Citizenship check - CIT1: {cit1}, CIT2: {cit2}")
        # Broader citizenship difference check
        if (cit1 >= 3 and cit2 < 3) or (cit2 >= 3 and cit1 < 3):
            logger.debug("  Different citizenship status detected")
            if max(income1, income2) > 30000:  # Much lower income threshold
                logger.debug("  Income threshold met, considering MFS")
                return True
    
    # 5. More lenient self-employment criteria
    semp1 = float(person1.get('SEMP', 0) or 0)
    semp2 = float(person2.get('SEMP', 0) or 0)
    if (abs(semp1) > 15000 and abs(semp2) < 5000) or (abs(semp2) > 15000 and abs(semp1) < 5000):
        logger.debug(f"  Significant self-employment difference (${semp1:,.0f} vs ${semp2:,.0f}), considering MFS")
        return True
    
    # 6. More lenient investment income criteria
    intp1 = float(person1.get('INTP', 0) or 0)
    intp2 = float(person2.get('INTP', 0) or 0)
    if (intp1 > 5000 and intp2 < 2000) or (intp2 > 5000 and intp1 < 2000):
        logger.debug(f"  Significant investment income difference (${intp1:,.0f} vs ${intp2:,.0f}), considering MFS")
        return True
    
    # 7. Age difference suggesting different life stages/tax situations
    age1 = person1.get('AGEP', 0)
    age2 = person2.get('AGEP', 0)
    age_diff = abs(age1 - age2)
    if age_diff > 20 and max(income1, income2) > 80000:
        return True
    
    # 8. One spouse receiving significant public assistance
    pap1 = float(person1.get('PAP', 0) or 0)
    pap2 = float(person2.get('PAP', 0) or 0)
    if (pap1 > 5000 and pap2 == 0 and income2 > 40000) or (pap2 > 5000 and pap1 == 0 and income1 > 40000):
        return True
    
    # 9. Educational differences that might affect tax credits
    schl1 = person1.get('SCHL', 0)
    schl2 = person2.get('SCHL', 0)
    # If one is in graduate school (SCHL 20-21) and the other is not
    if ((schl1 >= 20 and schl2 < 16) or (schl2 >= 20 and schl1 < 16)) and max(income1, income2) > 60000:
        return True
        
    # 10. HINS1 - Medicare coverage (significant factor for MFS)
    hins1_1 = person1.get('HINS1', 0) == 1  # 1 = Yes, 2 = No
    hins1_2 = person2.get('HINS1', 0) == 1
    
    # If one spouse has Medicare and the other doesn't, consider MFS
    if hins1_1 != hins1_2:
        logger.debug(f"MFS Check: Medicare coverage mismatch - Person1: {hins1_1}, Person2: {hins1_2}")
        # Medicare coverage difference with income disparity suggests MFS
        if income1 != income2 and max(income1, income2) > 30000:
            logger.debug("  Medicare coverage difference with income disparity, considering MFS")
            return True
    
    logger.debug("MFS Check: No MFS criteria met, returning False")
    return False

def _are_married(person1: pd.Series, person2: pd.Series) -> bool:
    """Check if two people are married to each other.
    
    STRICT VALIDATION: Only identifies actual married couples based on PUMS relationship codes.
    This prevents incorrect pairing of unrelated married adults.
    
    In canonical PUMS RELSHIPP data:
    - RELSHIPP=20 is householder
    - RELSHIPP=21 is opposite-sex spouse, 23 is same-sex spouse
    """
    # Get MAR and RELSHIPP values
    mar1 = person1.get('MAR', -1)
    mar2 = person2.get('MAR', -1)
    rel1 = person1.get('RELSHIPP', 0)
    rel2 = person2.get('RELSHIPP', 0)

    # Both must be marked as married
    if mar1 != 1 or mar2 != 1:
        return False

    # STRICT CHECK: Only allow householder + spouse patterns. This prevents
    # incorrect pairing of unrelated married adults in the same household.
    return (
        (rel1 == 20 and rel2 in {21, 23}) or
        (rel2 == 20 and rel1 in {21, 23})
    )

def _calculate_income(person: pd.Series) -> float:
    """Calculate total income for a person."""
    # First check for WAGP (wages) as it's commonly used in test data
    wagp = person.get('WAGP', 0)
    if wagp and wagp > 0:
        return float(wagp)
    
    # Then check PINCP (total person income) if available
    pincp = person.get('PINCP', 0)
    if pincp and pincp > 0:
        # Apply adjustment factor if it's a reasonable value
        adjinc = person.get('ADJINC', 1.0)
        if adjinc and adjinc > 0 and adjinc < 2.0:  # Only apply if it's a reasonable adjustment
            return float(pincp) * float(adjinc)
        return float(pincp)
    
    # Fallback to summing individual components
    income = 0.0
    for col in ['WAGP', 'SEMP', 'INTP', 'RETP', 'SSP', 'SSIP', 'PAP', 'OIP']:
        value = person.get(col, 0)
        if value:
            income += float(value)
    
    # Only apply adjustment factor if it's a reasonable value
    adjinc = person.get('ADJINC', 1.0)
    if adjinc and adjinc > 0 and adjinc < 2.0:  # Only apply if it's a reasonable adjustment
        income *= float(adjinc)
    
    return income
