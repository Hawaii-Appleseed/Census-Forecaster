"""
Utility functions for tax unit construction.

This module contains helper functions used throughout the tax unit construction process.
"""

from __future__ import annotations

import logging

import pandas as pd

# Configure logging
logger = logging.getLogger(__name__)

def setup_logging(level: int = logging.INFO) -> None:
    """
    Set up basic logging configuration.
    
    Args:
        level: Logging level (default: logging.INFO)
    """
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    logger.setLevel(level)

def validate_input_data(person_df: pd.DataFrame, hh_df: pd.DataFrame) -> tuple[bool, str]:
    """
    Validate the input data frames.
    
    Args:
        person_df: Person-level data
        hh_df: Household-level data
        
    Returns:
        tuple: (is_valid, error_message)
    """
    required_person_cols = ['SERIALNO', 'SPORDER', 'AGEP', 'SEX', 'MAR', 'RELSHIPP']
    required_hh_cols = ['SERIALNO']
    
    # Check for required columns
    missing_person = [col for col in required_person_cols if col not in person_df.columns]
    if missing_person:
        return False, f"Missing required person columns: {', '.join(missing_person)}"
        
    missing_hh = [col for col in required_hh_cols if col not in hh_df.columns]
    if missing_hh:
        return False, f"Missing required household columns: {', '.join(missing_hh)}"
    
    # Check for duplicate person IDs
    if 'person_id' in person_df.columns:
        if person_df['person_id'].duplicated().any():
            return False, "Duplicate person_id values found"
    
    # Check that all people belong to a household
    if not set(person_df['SERIALNO']).issubset(set(hh_df['SERIALNO'])):
        return False, "Some people belong to households not in household data"
    
    return True, ""

def create_person_id(person_df: pd.DataFrame) -> pd.Series:
    """
    Create a unique person ID from SERIALNO and SPORDER.
    
    Args:
        person_df: Person-level data with SERIALNO and SPORDER columns
        
    Returns:
        Series: Unique person IDs
    """
    return person_df['SERIALNO'].astype(str) + '_' + person_df['SPORDER'].astype(str)

def calculate_age_group(age: int) -> str:
    """
    Categorize a person into an age group.
    
    Args:
        age: Person's age
        
    Returns:
        str: Age group
    """
    if age < 19:
        return 'child'
    elif age < 26:
        return 'young_adult'
    elif age < 65:
        return 'adult'
    else:
        return 'senior'

def is_working_age(age: int) -> bool:
    """
    Check if a person is of working age.
    
    Args:
        age: Person's age
        
    Returns:
        bool: True if working age (18-64)
    """
    return 18 <= age < 65

def is_retired(age: int, in_labor_force: bool = None) -> bool:
    """
    Check if a person is likely retired.
    
    Args:
        age: Person's age
        in_labor_force: Whether the person is in the labor force (optional)
        
    Returns:
        bool: True if likely retired
    """
    if age >= 65:
        if in_labor_force is not None:
            return not in_labor_force
        return True
    return False

def get_income_quantile(income: float, quantiles: list[float]) -> int:
    """
    Determine which income quantile a value falls into.
    
    Args:
        income: Income amount
        quantiles: List of quantile boundaries
        
    Returns:
        int: Quantile index (0 for lowest)
    """
    for i, q in enumerate(sorted(quantiles)):
        if income <= q:
            return i
    return len(quantiles)

def safe_divide(numerator: float, denominator: float, default: float = 0.0) -> float:
    """
    Safely divide two numbers, returning a default if denominator is zero.
    
    Args:
        numerator: Numerator
        denominator: Denominator
        default: Value to return if denominator is zero
        
    Returns:
        float: Result of division or default
    """
    if denominator == 0:
        return default
    return numerator / denominator

def format_currency(amount: float) -> str:
    """
    Format a number as currency.
    
    Args:
        amount: Amount to format
        
    Returns:
        str: Formatted currency string
    """
    if pd.isna(amount):
        return "$0.00"
    return f"${amount:,.2f}"

def log_memory_usage(df: pd.DataFrame, name: str = "DataFrame") -> None:
    """
    Log the memory usage of a DataFrame.
    
    Args:
        df: DataFrame to check
        name: Name to use in log message
    """
    if df is None:
        logger.debug(f"{name}: None")
        return
        
    try:
        mb = df.memory_usage(deep=True).sum() / (1024 * 1024)
        logger.debug(f"{name} memory usage: {mb:.2f} MB")
    except Exception as e:
        logger.debug(f"Could not determine memory usage for {name}: {e}")

def time_execution(func):
    """
    Decorator to log the execution time of a function.
    
    Args:
        func: Function to time
        
    Returns:
        Wrapped function
    """
    import time
    from functools import wraps
    
    @wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.time()
        result = func(*args, **kwargs)
        end_time = time.time()
        logger.debug(f"{func.__name__} executed in {end_time - start_time:.4f} seconds")
        return result
    
    return wrapper
