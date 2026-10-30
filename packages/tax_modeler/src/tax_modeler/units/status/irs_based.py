"""
IRS-Based Filing Status Determination

This module implements filing status determination logic based on IRS Statistics of Income (SOI)
documentation and empirical patterns observed in official tax data.

Filing status probabilities are loaded from config/filing_status_rates.json.
If the config file is missing, hardcoded fallbacks matching the original SOI values are used.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
from pathlib import Path
from typing import Any

import pandas as pd

from tax_modeler.units.relshipp_codes import PARENT, PARENT_IN_LAW

logger = logging.getLogger(__name__)

# Config file location
_CONFIG_PATH = Path(__file__).parent.parent.parent.parent.parent / "config" / "filing_status_rates.json"
_FS_PARAMS = None


def _load_fs_params(config_path: Path = _CONFIG_PATH) -> dict:
    """Load filing status parameters from JSON config, with hardcoded fallbacks."""
    global _FS_PARAMS
    if _FS_PARAMS is not None:
        return _FS_PARAMS

    fallback = {
        "income_based_joint_probability": {
            "tiers": [
                {"min_income": 100001, "max_income": None, "probability": 0.92},
                {"min_income": 50001, "max_income": 100000, "probability": 0.85},
                {"min_income": 0, "max_income": 50000, "dual_earner": 0.75, "single_earner": 0.40},
            ]
        },
        "age_based_joint_probability": {
            "tiers": [
                {"min_age": 60, "probability": 0.95},
                {"min_age": 40, "probability": 0.85},
                {"min_age": 25, "probability": 0.75},
                {"min_age": 0, "probability": 0.65},
            ]
        },
    }

    if config_path.exists():
        try:
            with open(config_path) as f:
                loaded = json.load(f)
            params = {k: v for k, v in loaded.items() if not k.startswith("_")}
            for key, val in params.items():
                if isinstance(val, dict):
                    params[key] = {k: v for k, v in val.items() if not k.startswith("_")}
            logger.info(f"Loaded filing status params from {config_path}")
            _FS_PARAMS = params
            return _FS_PARAMS
        except Exception as e:
            logger.warning(f"Failed to load {config_path}: {e}. Using hardcoded fallbacks.")

    _FS_PARAMS = fallback
    return _FS_PARAMS


def income_based_joint_probability(adult1: dict[str, Any], adult2: dict[str, Any] | None = None) -> float:
    """
    Determine joint filing probability based on income patterns from IRS SOI data.

    Probabilities loaded from config/filing_status_rates.json.

    Args:
        adult1: Primary adult's data
        adult2: Secondary adult's data (if applicable)

    Returns:
        float: Probability of filing jointly (0.0 to 1.0)
    """
    params = _load_fs_params()
    tiers = params["income_based_joint_probability"]["tiers"]

    income1 = float(adult1.get('PINCP', 0) or 0)
    income2 = float(adult2.get('PINCP', 0) or 0) if adult2 else 0
    total_income = income1 + income2

    logger.debug(f"Income-based analysis: Adult1=${income1:,.0f}, Adult2=${income2:,.0f}, Total=${total_income:,.0f}")

    for tier in tiers:
        min_inc = tier.get("min_income", 0)
        max_inc = tier.get("max_income")

        if max_inc is None:
            # Open-ended top tier
            if total_income > min_inc:
                prob = tier["probability"]
                logger.debug(f"Income tier (>${min_inc:,}): Joint probability = {prob}")
                return prob
        elif total_income >= min_inc and total_income <= max_inc:
            # Check for dual/single earner split
            if "dual_earner" in tier:
                if income1 > 0 and income2 > 0:
                    prob = tier["dual_earner"]
                    logger.debug(f"Lower income, dual earner (${total_income:,.0f}): Joint probability = {prob}")
                else:
                    prob = tier["single_earner"]
                    logger.debug(f"Lower income, single earner (${total_income:,.0f}): Joint probability = {prob}")
                return prob
            else:
                prob = tier["probability"]
                logger.debug(f"Income tier (${min_inc:,}-${max_inc:,}): Joint probability = {prob}")
                return prob

    # Fallback
    return 0.75


def get_marital_duration_factor(adult1: dict[str, Any], adult2: dict[str, Any] | None = None, current_year: int = 2023) -> float:
    """
    Calculate joint filing probability factor based on likely duration of marriage using age as proxy.
    
    Args:
        adult1: Primary adult's data
        adult2: Secondary adult's data (if applicable)
        current_year: Current tax year
        
    Returns:
        float: Age-based joint filing probability (0.0 to 1.0)
    """
    params = _load_fs_params()
    tiers = params["age_based_joint_probability"]["tiers"]

    age1 = int(adult1.get('AGEP', 30))
    age2 = int(adult2.get('AGEP', 30)) if adult2 else age1
    avg_age = (age1 + age2) / 2

    logger.debug(f"Age analysis: Adult1={age1}, Adult2={age2}, Average={avg_age:.1f}")

    for tier in tiers:
        if avg_age >= tier["min_age"]:
            prob = tier["probability"]
            label = tier.get("label", f"age >= {tier['min_age']}")
            logger.debug(f"{label} (avg age {avg_age:.1f}): Joint probability = {prob}")
            return prob

    # Fallback
    return 0.75


def dependent_based_rules(household: list[dict[str, Any]], adult1: dict[str, Any], adult2: dict[str, Any] | None = None) -> float | None:
    """
    Apply IRS-observed patterns related to dependents.
    
    Args:
        household: List of all household members
        adult1: Primary adult's data
        adult2: Secondary adult's data (if applicable)
        
    Returns:
        Optional[float]: Joint filing probability based on dependents, or None if no adjustment
    """
    num_children = sum(1 for p in household if int(p.get('AGEP', 0)) < 18)
    
    logger.debug(f"Dependent analysis: {num_children} children in household")
    
    # Couples with children are much more likely to file jointly
    if num_children > 0:
        if num_children == 1:
            prob = 0.85  # 85% joint filing with one child
            logger.debug(f"One child: Joint probability = {prob}")
            return prob
        else:
            prob = 0.92  # 92% with multiple children
            logger.debug(f"Multiple children ({num_children}): Joint probability = {prob}")
            return prob
    
    # No children - check other dependent situations
    has_elderly_dependent = any(
        int(p.get('AGEP', 0)) > 65
        and int(p.get('RELSHIPP', 0)) in {PARENT, PARENT_IN_LAW}
        for p in household
    )
    
    if has_elderly_dependent:
        prob = 0.88  # High joint filing with elderly dependents
        logger.debug(f"Elderly dependent present: Joint probability = {prob}")
        return prob
        
    logger.debug("No dependents affecting filing status")
    return None  # No adjustment


def hawaii_specific_adjustment(base_probability: float) -> float:
    """
    Adjust for Hawaii's specific filing patterns based on IRS SOI state data.
    
    Args:
        base_probability: Base joint filing probability
        
    Returns:
        float: Adjusted probability for Hawaii
    """
    # Hawaii has slightly higher joint filing rates than national average
    # Source: IRS SOI State Data Tables
    adjusted = min(1.0, base_probability * 1.08)  # 8% higher than national average
    
    logger.debug(f"Hawaii adjustment: {base_probability:.3f} -> {adjusted:.3f}")
    return adjusted


def should_file_jointly_irs_method(adult1: dict[str, Any], adult2: dict[str, Any] | None, 
                                  household: list[dict[str, Any]], use_stable_random: bool = True) -> bool:
    """
    Final determination using IRS-recommended approach combining all factors.
    
    Args:
        adult1: Primary adult's data
        adult2: Secondary adult's data (if applicable)
        household: List of all household members
        use_stable_random: Whether to use deterministic randomness based on household ID
        
    Returns:
        bool: True if should file jointly, False if should file separately
    """
    logger.debug(f"IRS-based joint filing determination for adults {adult1.get('SPORDER', 'N/A')} and {adult2.get('SPORDER', 'N/A') if adult2 else 'N/A'}")
    
    # Base probability from income
    base_prob = income_based_joint_probability(adult1, adult2)
    
    # Adjust for age/marital duration
    age_factor = get_marital_duration_factor(adult1, adult2)
    combined_prob = (base_prob * 0.6) + (age_factor * 0.4)
    
    # Adjust for dependents
    dep_factor = dependent_based_rules(household, adult1, adult2)
    if dep_factor is not None:
        combined_prob = (combined_prob * 0.5) + (dep_factor * 0.5)
    
    # Apply Hawaii-specific adjustment
    combined_prob = hawaii_specific_adjustment(combined_prob)
    
    # Ensure probability stays within bounds
    final_prob = max(0.05, min(0.98, combined_prob))
    
    logger.debug(f"Final joint filing probability: {final_prob:.3f}")
    
    # Use deterministic randomness if requested
    if use_stable_random:
        # Create a stable seed from household identifiers
        serialno = str(adult1.get('SERIALNO', '0'))
        sporder1 = str(adult1.get('SPORDER', 0))
        sporder2 = str(adult2.get('SPORDER', 1)) if adult2 else '0'
        seed_string = f"{serialno}_{sporder1}_{sporder2}_irs_joint"
        seed = int(hashlib.md5(seed_string.encode()).hexdigest()[:8], 16)
        
        random.seed(seed)
        result = random.random() < final_prob
    else:
        result = random.random() < final_prob
    
    logger.debug(f"IRS-based decision: {'Joint' if result else 'Separate'} filing")
    return result


def calibrate_to_soi_totals(tax_units: pd.DataFrame, 
                           target_distributions: dict[str, float] | None = None,
                           weight_col: str = 'weight') -> pd.DataFrame:
    """
    Adjust filing status distribution to match SOI targets using weighted post-processing calibration.
    
    This function calibrates the filing status distribution to match DOTAX SOI benchmarks
    by converting tax units between filing statuses based on income and likelihood scores.
    
    Args:
        tax_units: DataFrame of tax units
        target_distributions: Dict mapping filing_status -> target percentage
                            Default: Hawaii DOTAX 2022 benchmarks
        weight_col: Column name for weights (default: 'weight')
        
    Returns:
        pd.DataFrame: Calibrated tax units with 'calibrated' flag
    """
    
    # Default to Hawaii DOTAX 2022 benchmarks
    if target_distributions is None:
        target_distributions = {
            'single': 0.5276,  # 335,198 / 635,117
            'married_filing_jointly': 0.3407,  # 216,358 / 635,117
            'head_of_household': 0.1061,  # 67,393 / 635,117
            'married_filing_separately': 0.0252  # 16,007 / 635,117
        }
    
    if tax_units.empty:
        return tax_units
    
    # Make a copy to avoid modifying original
    df = tax_units.copy()
    
    # Add calibrated flag if not present
    if 'calibrated' not in df.columns:
        df['calibrated'] = False
    
    # Calculate current distribution (weighted)
    total_weight = df[weight_col].sum()
    current_dist = df.groupby('filing_status')[weight_col].sum() / total_weight
    
    logger.info("="*80)
    logger.info("SOI CALIBRATION - Adjusting Filing Status Distribution")
    logger.info("="*80)
    logger.info(f"\nTotal weighted tax units: {total_weight:,.0f}")
    logger.info("\nCurrent vs Target Distribution:")
    logger.info(f"{'Status':<30} {'Current':>10} {'Target':>10} {'Gap':>10}")
    logger.info("-"*80)
    
    for status in ['single', 'married_filing_jointly', 'head_of_household', 'married_filing_separately']:
        current = current_dist.get(status, 0)
        target = target_distributions.get(status, 0)
        gap = current - target
        logger.info(f"{status:<30} {current:>9.1%} {target:>9.1%} {gap:>+9.1%}")
    
    # Prioritized calibration approach: handle married statuses first, then unmarried
    max_iterations = 15
    tolerance = 0.0005  # 0.05% tolerance
    
    # Define adjustment order: married statuses first to avoid cross-interference
    married_statuses = ['married_filing_jointly', 'married_filing_separately']
    unmarried_statuses = ['single', 'head_of_household']
    
    for iteration in range(max_iterations):
        logger.info(f"\n--- Iteration {iteration + 1} ---")
        
        # Recalculate current distribution
        total_weight = df[weight_col].sum()
        current_dist = df.groupby('filing_status')[weight_col].sum() / total_weight
        
        # Find largest gap, prioritizing married statuses first
        max_gap = 0
        max_gap_status = None
        
        # First check married statuses
        for status in married_statuses:
            if status in target_distributions:
                current = current_dist.get(status, 0)
                target = target_distributions[status]
                gap = abs(current - target)
                if gap > max_gap:
                    max_gap = gap
                    max_gap_status = status
        
        # If no significant married gaps, check unmarried statuses
        if max_gap < tolerance:
            for status in unmarried_statuses:
                if status in target_distributions:
                    current = current_dist.get(status, 0)
                    target = target_distributions[status]
                    gap = abs(current - target)
                    if gap > max_gap:
                        max_gap = gap
                        max_gap_status = status
        
        if max_gap < tolerance:
            logger.info(f"✅ Converged! Max gap: {max_gap:.3%}")
            break
        
        # Adjust the status with the largest gap
        current = current_dist.get(max_gap_status, 0)
        target = target_distributions.get(max_gap_status, 0)
        
        if current < target:
            # Need to convert TO this status
            deficit = (target - current) * total_weight
            logger.info(f"Need to add {deficit:,.0f} weighted units to {max_gap_status}")
            
            # Determine source statuses to convert from
            # ONLY allow legally valid conversions
            if max_gap_status == 'married_filing_jointly':
                # Can only convert from MFS (both are married couples)
                df = _convert_to_status(df, 'married_filing_separately', max_gap_status, deficit, weight_col)
            elif max_gap_status == 'single':
                # Can only convert from HoH (both are unmarried)
                df = _convert_to_status(df, 'head_of_household', max_gap_status, deficit, weight_col)
            elif max_gap_status == 'head_of_household':
                # Can only convert from single (both are unmarried)
                df = _convert_to_status(df, 'single', max_gap_status, deficit, weight_col)
            elif max_gap_status == 'married_filing_separately':
                # Can only convert from MFJ (both are married couples)
                df = _convert_to_status(df, 'married_filing_jointly', max_gap_status, deficit, weight_col)
        
        else:
            # Need to convert FROM this status
            surplus = (current - target) * total_weight
            logger.info(f"Need to remove {surplus:,.0f} weighted units from {max_gap_status}")
            
            # Determine target statuses to convert to
            # ONLY allow legally valid conversions
            if max_gap_status == 'married_filing_jointly':
                # Can only convert to MFS (both are married couples)
                df = _convert_from_status(df, max_gap_status, 'married_filing_separately', surplus, weight_col)
            elif max_gap_status == 'single':
                # Can only convert to HoH (both are unmarried)
                df = _convert_from_status(df, max_gap_status, 'head_of_household', surplus, weight_col)
            elif max_gap_status == 'head_of_household':
                # Can only convert to single (both are unmarried)
                df = _convert_from_status(df, max_gap_status, 'single', surplus, weight_col)
            elif max_gap_status == 'married_filing_separately':
                # Can only convert to MFJ (both are married couples)
                df = _convert_from_status(df, max_gap_status, 'married_filing_jointly', surplus, weight_col)
    
    # Final single vs HoH fine-tuning (if still outside tolerance)
    total_weight = df[weight_col].sum()
    single_target = target_distributions.get('single')
    hoh_target = target_distributions.get('head_of_household')
    if single_target is not None and hoh_target is not None:
        for _ in range(6):
            current_dist = df.groupby('filing_status')[weight_col].sum() / total_weight
            single_current = current_dist.get('single', 0.0)
            single_gap = single_target - single_current
            if abs(single_gap) < tolerance:
                break
            adjust_weight = single_gap * total_weight
            if adjust_weight > 0:
                logger.info(f"Fine-tuning: adding {adjust_weight:,.0f} weighted units to single from head_of_household")
                df = _convert_to_status(df, 'head_of_household', 'single', adjust_weight, weight_col)
            else:
                logger.info(f"Fine-tuning: removing {-adjust_weight:,.0f} weighted units from single to head_of_household")
                df = _convert_to_status(df, 'single', 'head_of_household', -adjust_weight, weight_col)
            total_weight = df[weight_col].sum()

    # Final statistics
    total_weight = df[weight_col].sum()
    final_dist = df.groupby('filing_status')[weight_col].sum() / total_weight
    calibrated_count = df['calibrated'].sum()
    calibrated_weight = df[df['calibrated']][weight_col].sum()
    
    logger.info("\n" + "="*80)
    logger.info("CALIBRATION COMPLETE")
    logger.info("="*80)
    logger.info("\nFinal Distribution:")
    logger.info(f"{'Status':<30} {'Current':>10} {'Target':>10} {'Gap':>10}")
    logger.info("-"*80)
    
    for status in ['single', 'married_filing_jointly', 'head_of_household', 'married_filing_separately']:
        current = final_dist.get(status, 0)
        target = target_distributions.get(status, 0)
        gap = current - target
        logger.info(f"{status:<30} {current:>9.1%} {target:>9.1%} {gap:>+9.1%}")
    
    logger.info(f"\nCalibrated units: {calibrated_count:,} ({calibrated_weight:,.0f} weighted, {calibrated_weight/total_weight:.1%})")
    
    return df


def _convert_to_status(df: pd.DataFrame, from_status: str, to_status: str, 
                      target_weight: float, weight_col: str) -> pd.DataFrame:
    """Convert units FROM one status TO another using AGI-stratified, fractional adjustments."""
    candidates = df[df['filing_status'] == from_status].copy()

    if candidates.empty or target_weight <= 0:
        return df

    sort_col = 'income' if 'income' in candidates.columns else 'agi'

    # Determine ordering preferences by destination status
    if to_status == 'head_of_household':
        ascending = True
        band_order = ['low', 'mid', 'high']
    else:
        ascending = False
        band_order = ['high', 'mid', 'low']

    # Assign AGI bands to preserve income distribution during calibration
    agi_series = pd.to_numeric(candidates.get('agi'), errors='coerce').fillna(0)
    bins = [-float('inf'), 50000, 150000, float('inf')]
    labels = ['low', 'mid', 'high']
    candidates['agi_band'] = pd.cut(agi_series, bins=bins, labels=labels, include_lowest=True)
    candidates['agi_band'] = candidates['agi_band'].astype(str)
    candidates.loc[~candidates['agi_band'].isin(labels), 'agi_band'] = 'mid'

    converted_weight = 0.0
    converted_indices: set[int] = set()
    remaining = target_weight

    for band in band_order:
        if remaining <= 0.01:
            break

        band_candidates = candidates[
            (candidates['agi_band'] == band) &
            (~candidates.index.isin(converted_indices))
        ]

        if band_candidates.empty:
            continue

        band_candidates = band_candidates.sort_values(sort_col, ascending=ascending)
        band_target = min(remaining, band_candidates[weight_col].sum())

        df, band_converted, indices = _apply_conversions(
            df, band_candidates, band_target, to_status, weight_col
        )

        converted_weight += band_converted
        converted_indices.update(indices)
        remaining = target_weight - converted_weight

    # Fallback: apply any remaining weight using all leftover candidates
    if converted_weight < target_weight and remaining > 0.01:
        fallback_candidates = candidates[~candidates.index.isin(converted_indices)]
        if not fallback_candidates.empty:
            fallback_candidates = fallback_candidates.sort_values(sort_col, ascending=ascending)
            df, extra_converted, indices = _apply_conversions(
                df, fallback_candidates, remaining, to_status, weight_col
            )
            converted_weight += extra_converted
            converted_indices.update(indices)

    if converted_weight > 0:
        logger.info(
            f"  Converted {len(converted_indices):,} units ({converted_weight:,.0f} weighted) "
            f"from {from_status} to {to_status}"
        )

    return df


def _apply_conversions(df: pd.DataFrame, candidates: pd.DataFrame, target_weight: float,
                       to_status: str, weight_col: str) -> tuple[pd.DataFrame, float, list[int]]:
    """Apply conversions for a set of candidates with fractional splitting."""
    converted_weight = 0.0
    converted_indices: list[int] = []

    if target_weight <= 0 or candidates.empty:
        return df, converted_weight, converted_indices

    cumsum = candidates[weight_col].cumsum()
    full_mask = cumsum <= target_weight + 1e-9
    full_converts = candidates[full_mask]

    if not full_converts.empty:
        df.loc[full_converts.index, 'filing_status'] = to_status
        df.loc[full_converts.index, 'calibrated'] = True
        converted_weight += full_converts[weight_col].sum()
        converted_indices.extend(full_converts.index.tolist())

    remaining = target_weight - converted_weight
    if remaining > 0.01:
        next_candidates = candidates[~full_mask]
        if not next_candidates.empty:
            split_unit = next_candidates.iloc[0]
            split_idx = split_unit.name
            original_weight = split_unit[weight_col]
            take_weight = min(remaining, original_weight)

            if take_weight > 0:
                df.loc[split_idx, weight_col] = original_weight - take_weight

                new_record = split_unit.copy()
                if 'agi_band' in new_record.index:
                    new_record = new_record.drop(labels=['agi_band'])
                new_record[weight_col] = take_weight
                new_record['filing_status'] = to_status
                new_record['calibrated'] = True

                df = pd.concat([df, pd.DataFrame([new_record])], ignore_index=True)
                converted_weight += take_weight

    return df, converted_weight, converted_indices


def _convert_from_status(df: pd.DataFrame, from_status: str, to_status: str,
                        target_weight: float, weight_col: str) -> pd.DataFrame:
    """Helper function to convert units FROM one status TO another (same as _convert_to_status)."""
    return _convert_to_status(df, from_status, to_status, target_weight, weight_col)


def adjust_high_income_statuses(df: pd.DataFrame, weight_col: str = 'weight', 
                               agi_threshold: float = 200_000, max_adjustment_weight: float = 80_000_000) -> pd.DataFrame:
    """
    Fine-tune high-income filing status distribution to improve revenue accuracy.
    
    Args:
        df: DataFrame with tax units including 'agi', 'filing_status', weight_col
        weight_col: Name of weight column
        agi_threshold: Minimum AGI to consider for adjustment (default $200k)
        max_adjustment_weight: Maximum total weight to adjust (default 80M weighted units)
    
    Returns:
        DataFrame with adjusted filing statuses and 'high_income_adjusted' flag
    """
    logger.info(f"\n{'='*60}")
    logger.info("HIGH-INCOME FILING STATUS FINE-TUNING")
    logger.info(f"{'='*60}")
    
    # Make copy and add adjustment flag
    result_df = df.copy()
    if 'high_income_adjusted' not in result_df.columns:
        result_df['high_income_adjusted'] = False
    
    # Filter to high-income, taxable units
    high_income_mask = (
        (result_df['agi'] >= agi_threshold) & 
        (~result_df.get('is_nontaxable', False))
    )
    high_income_units = result_df[high_income_mask].copy()
    
    if high_income_units.empty:
        logger.info(f"No high-income units found above ${agi_threshold:,.0f}")
        return result_df
    
    logger.info(f"High-income units (AGI >= ${agi_threshold:,.0f}): {len(high_income_units):,}")
    logger.info(f"Total high-income weight: {high_income_units[weight_col].sum():,.0f}")
    
    # Current distribution in high-income segment
    hi_dist = high_income_units.groupby('filing_status')[weight_col].sum()
    logger.info("\nHigh-income filing status distribution:")
    for status, weight in hi_dist.items():
        pct = weight / hi_dist.sum() * 100
        logger.info(f"  {status}: {weight:,.0f} ({pct:.1f}%)")
    
    # Identify conversion candidates
    joint_candidates = high_income_units[
        high_income_units['filing_status'] == 'married_filing_jointly'
    ].sort_values('agi', ascending=True)  # Start with lowest AGI joint filers
    
    single_candidates = high_income_units[
        high_income_units['filing_status'] == 'single'
    ].sort_values('agi', ascending=False)  # Start with highest AGI single filers
    
    logger.info("\nConversion candidates:")
    logger.info(f"  Joint filers available: {len(joint_candidates):,} ({joint_candidates[weight_col].sum():,.0f} weighted)")
    logger.info(f"  Single filers available: {len(single_candidates):,} ({single_candidates[weight_col].sum():,.0f} weighted)")
    
    # Calculate target adjustment (aim to move ~$70M in liability from joint to single)
    # Rough estimate: $70M liability ≈ 15-20k weighted high-income units
    target_weight_to_convert = min(max_adjustment_weight, joint_candidates[weight_col].sum() * 0.15)
    
    logger.info(f"\nTarget weight to convert: {target_weight_to_convert:,.0f}")
    
    if target_weight_to_convert < 1000:
        logger.info("Target weight too small, skipping adjustment")
        return result_df
    
    # Apply conversion using existing fractional splitting logic
    converted_weight = 0.0
    converted_indices = []
    
    cumsum = joint_candidates[weight_col].cumsum()
    full_converts = joint_candidates[cumsum <= target_weight_to_convert]
    
    if not full_converts.empty:
        # Convert full units to married_filing_separately (more realistic than single)
        result_df.loc[full_converts.index, 'filing_status'] = 'married_filing_separately'
        result_df.loc[full_converts.index, 'high_income_adjusted'] = True
        converted_weight += full_converts[weight_col].sum()
        converted_indices.extend(full_converts.index.tolist())
    
    # Handle fractional conversion if needed
    remaining = target_weight_to_convert - converted_weight
    if remaining > 0.01:
        next_candidates = joint_candidates[cumsum > target_weight_to_convert]
        if not next_candidates.empty:
            split_unit = next_candidates.iloc[0]
            split_idx = split_unit.name
            original_weight = split_unit[weight_col]
            take_weight = min(remaining, original_weight)
            
            if take_weight > 0:
                # Reduce original unit's weight
                result_df.loc[split_idx, weight_col] = original_weight - take_weight
                
                # Create new record with MFS status
                new_record = split_unit.copy()
                new_record[weight_col] = take_weight
                new_record['filing_status'] = 'married_filing_separately'
                new_record['high_income_adjusted'] = True
                
                # Add new record to dataframe
                result_df = pd.concat([result_df, pd.DataFrame([new_record])], ignore_index=True)
                converted_weight += take_weight
    
    logger.info("\nConversion completed:")
    logger.info(f"  Units converted: {len(converted_indices):,}")
    logger.info(f"  Weight converted: {converted_weight:,.0f}")
    logger.info("  Conversion: married_filing_jointly → married_filing_separately")
    
    # Final distribution check
    final_dist = result_df.groupby('filing_status')[weight_col].sum()
    total_adjusted = result_df[result_df['high_income_adjusted']][weight_col].sum()
    
    logger.info("\nFinal adjustment summary:")
    logger.info(f"  Total adjusted weight: {total_adjusted:,.0f} ({total_adjusted/result_df[weight_col].sum()*100:.2f}% of total)")
    logger.info(f"  New MFS share: {final_dist.get('married_filing_separately', 0)/final_dist.sum()*100:.1f}%")
    
    return result_df
