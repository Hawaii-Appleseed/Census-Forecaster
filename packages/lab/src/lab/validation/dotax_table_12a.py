"""
DOTAX SOI Table 12A Validation

Validates model tax liability against DOTAX Table 12A:
"Tax Liability of Residents Before and After Tax Credits by AGI Class"
"""

from __future__ import annotations

import pandas as pd
import numpy as np
from typing import Dict, Tuple
import logging

logger = logging.getLogger(__name__)


class DotaxTable12AValidator:
    """Validate tax liability by AGI brackets using DOTAX Table 12A."""
    
    def __init__(self):
        """Initialize with DOTAX Table 12A benchmarks."""
        # DOTAX SOI 2022 Table 12A - Before Credits
        # (Returns, Tax Liability in $ millions, Average tax $)
        self.agi_brackets = [
            (0, 10000, 129376, 3, 21),
            (10000, 20000, 64160, 21, 333),
            (20000, 30000, 57835, 51, 889),
            (30000, 40000, 59827, 92, 1533),
            (40000, 50000, 53555, 116, 2166),
            (50000, 75000, 91459, 293, 3201),
            (75000, 100000, 54976, 261, 4751),
            (100000, 150000, 62065, 438, 7055),
            (150000, 200000, 27976, 294, 10496),
            (200000, 300000, 18937, 310, 16391),
            (300000, 400000, 6076, 153, 25119),
            (400000, float('inf'), 8875, 998, 112416),
        ]
        
        self.total_returns = 635117
        self.total_tax_millions = 3029
        self.average_tax = 4770
        
    def assign_agi_bracket(self, agi: float) -> Tuple[float, float]:
        """Assign AGI to a bracket, returning (min, max)."""
        for min_agi, max_agi, _, _, _ in self.agi_brackets:
            if min_agi <= agi < max_agi:
                return (min_agi, max_agi)
        # Return highest bracket if above all
        return (400000, float('inf'))
    
    def get_bracket_label(self, min_agi: float, max_agi: float) -> str:
        """Get readable label for AGI bracket."""
        if max_agi == float('inf'):
            return f"${min_agi/1000:.0f}k+"
        else:
            return f"${min_agi/1000:.0f}k-${max_agi/1000:.0f}k"
    
    def validate_tax_units(self, tax_units: pd.DataFrame, 
                          weight_col: str = 'weight',
                          agi_col: str = 'agi',
                          tax_col: str = 'hi_state_tax') -> pd.DataFrame:
        """
        Validate tax units against DOTAX Table 12A benchmarks.
        
        Args:
            tax_units: DataFrame with tax unit data
            weight_col: Column name for weights
            agi_col: Column name for AGI
            tax_col: Column name for Hawaii state tax
            
        Returns:
            DataFrame with validation results by AGI bracket
        """
        logger.info("Validating against DOTAX SOI Table 12A...")
        
        # Assign brackets
        tax_units = tax_units.copy()
        bracket_assignment = tax_units[agi_col].apply(self.assign_agi_bracket)
        tax_units['agi_bracket_min'] = bracket_assignment.apply(lambda x: x[0])
        tax_units['agi_bracket_max'] = bracket_assignment.apply(lambda x: x[1])
        
        # Calculate statistics by bracket
        results = []
        
        for min_agi, max_agi, dotax_returns, dotax_tax_millions, dotax_avg_tax in self.agi_brackets:
            # Filter to bracket
            mask = (tax_units['agi_bracket_min'] == min_agi) & (tax_units['agi_bracket_max'] == max_agi)
            bracket_df = tax_units[mask]
            
            if len(bracket_df) == 0:
                model_returns = 0
                model_tax_millions = 0
                model_avg_tax = 0
            else:
                model_returns = bracket_df[weight_col].sum()
                model_tax_millions = (bracket_df[tax_col] * bracket_df[weight_col]).sum() / 1_000_000
                model_avg_tax = model_tax_millions * 1_000_000 / model_returns if model_returns > 0 else 0
            
            # Calculate differences
            returns_diff = model_returns - dotax_returns
            returns_pct = (returns_diff / dotax_returns * 100) if dotax_returns > 0 else 0
            
            tax_diff = model_tax_millions - dotax_tax_millions
            tax_pct = (tax_diff / dotax_tax_millions * 100) if dotax_tax_millions > 0 else 0
            
            avg_tax_diff = model_avg_tax - dotax_avg_tax
            avg_tax_pct = (avg_tax_diff / dotax_avg_tax * 100) if dotax_avg_tax > 0 else 0
            
            label = self.get_bracket_label(min_agi, max_agi)
            
            results.append({
                'agi_bracket': label,
                'min_agi': min_agi,
                'max_agi': max_agi,
                'dotax_returns': dotax_returns,
                'model_returns': model_returns,
                'returns_diff': returns_diff,
                'returns_pct_diff': returns_pct,
                'dotax_tax_millions': dotax_tax_millions,
                'model_tax_millions': model_tax_millions,
                'tax_diff': tax_diff,
                'tax_pct_diff': tax_pct,
                'dotax_avg_tax': dotax_avg_tax,
                'model_avg_tax': model_avg_tax,
                'avg_tax_diff': avg_tax_diff,
                'avg_tax_pct_diff': avg_tax_pct
            })
        
        results_df = pd.DataFrame(results)
        
        # Add totals
        total_model_returns = tax_units[weight_col].sum()
        total_model_tax = (tax_units[tax_col] * tax_units[weight_col]).sum() / 1_000_000
        total_model_avg = total_model_tax * 1_000_000 / total_model_returns if total_model_returns > 0 else 0
        
        results_df = pd.concat([results_df, pd.DataFrame([{
            'agi_bracket': 'TOTAL',
            'min_agi': 0,
            'max_agi': float('inf'),
            'dotax_returns': self.total_returns,
            'model_returns': total_model_returns,
            'returns_diff': total_model_returns - self.total_returns,
            'returns_pct_diff': (total_model_returns - self.total_returns) / self.total_returns * 100,
            'dotax_tax_millions': self.total_tax_millions,
            'model_tax_millions': total_model_tax,
            'tax_diff': total_model_tax - self.total_tax_millions,
            'tax_pct_diff': (total_model_tax - self.total_tax_millions) / self.total_tax_millions * 100,
            'dotax_avg_tax': self.average_tax,
            'model_avg_tax': total_model_avg,
            'avg_tax_diff': total_model_avg - self.average_tax,
            'avg_tax_pct_diff': (total_model_avg - self.average_tax) / self.average_tax * 100
        }])], ignore_index=True)
        
        return results_df
    
    def print_validation_report(self, results_df: pd.DataFrame):
        """Print formatted validation report."""
        print("\n" + "="*100)
        print("DOTAX SOI TABLE 12A VALIDATION - TAX LIABILITY BY AGI BRACKET (Before Credits)")
        print("="*100)
        
        # Returns comparison
        print("\n" + "-"*100)
        print("RETURNS BY AGI BRACKET")
        print("-"*100)
        print(f"{'AGI Bracket':<20} {'DOTAX':>12} {'Model':>12} {'Difference':>12} {'% Diff':>10}")
        print("-"*100)
        
        for _, row in results_df.iterrows():
            bracket = row['agi_bracket']
            dotax_ret = row['dotax_returns']
            model_ret = row['model_returns']
            diff = row['returns_diff']
            pct = row['returns_pct_diff']
            
            # Color code based on accuracy
            status = "✅" if abs(pct) < 10 else "⚠️" if abs(pct) < 20 else "❌"
            
            if bracket == 'TOTAL':
                print("-"*100)
            
            print(f"{bracket:<20} {dotax_ret:>12,.0f} {model_ret:>12,.0f} {diff:>+12,.0f} {pct:>+9.1f}% {status}")
        
        # Tax liability comparison
        print("\n" + "-"*100)
        print("TAX LIABILITY BY AGI BRACKET ($ Millions)")
        print("-"*100)
        print(f"{'AGI Bracket':<20} {'DOTAX ($M)':>12} {'Model ($M)':>12} {'Difference':>12} {'% Diff':>10}")
        print("-"*100)
        
        for _, row in results_df.iterrows():
            bracket = row['agi_bracket']
            dotax_tax = row['dotax_tax_millions']
            model_tax = row['model_tax_millions']
            diff = row['tax_diff']
            pct = row['tax_pct_diff']
            
            # Color code based on accuracy
            status = "✅" if abs(pct) < 10 else "⚠️" if abs(pct) < 20 else "❌"
            
            if bracket == 'TOTAL':
                print("-"*100)
            
            print(f"{bracket:<20} ${dotax_tax:>11.1f} ${model_tax:>11.1f} ${diff:>+11.1f} {pct:>+9.1f}% {status}")
        
        # Average tax comparison
        print("\n" + "-"*100)
        print("AVERAGE TAX PER RETURN BY AGI BRACKET")
        print("-"*100)
        print(f"{'AGI Bracket':<20} {'DOTAX ($)':>12} {'Model ($)':>12} {'Difference':>12} {'% Diff':>10}")
        print("-"*100)
        
        for _, row in results_df.iterrows():
            bracket = row['agi_bracket']
            dotax_avg = row['dotax_avg_tax']
            model_avg = row['model_avg_tax']
            diff = row['avg_tax_diff']
            pct = row['avg_tax_pct_diff']
            
            # Color code based on accuracy
            status = "✅" if abs(pct) < 10 else "⚠️" if abs(pct) < 20 else "❌"
            
            if bracket == 'TOTAL':
                print("-"*100)
            
            print(f"{bracket:<20} ${dotax_avg:>11,.0f} ${model_avg:>11,.0f} ${diff:>+11,.0f} {pct:>+9.1f}% {status}")
        
        print("="*100)
        
        # Summary statistics
        non_total = results_df[results_df['agi_bracket'] != 'TOTAL']
        brackets_within_10pct_tax = (non_total['tax_pct_diff'].abs() < 10).sum()
        total_brackets = len(non_total)
        
        print(f"\n📊 Summary:")
        print(f"   Brackets within ±10% (tax liability): {brackets_within_10pct_tax}/{total_brackets} ({brackets_within_10pct_tax/total_brackets*100:.1f}%)")
        print(f"   Total returns accuracy: {results_df[results_df['agi_bracket']=='TOTAL']['returns_pct_diff'].values[0]:+.2f}%")
        print(f"   Total tax accuracy: {results_df[results_df['agi_bracket']=='TOTAL']['tax_pct_diff'].values[0]:+.2f}%")


def validate_against_table_12a(tax_units: pd.DataFrame,
                               weight_col: str = 'weight',
                               agi_col: str = 'agi',
                               tax_col: str = 'hi_state_tax') -> pd.DataFrame:
    """
    Convenience function to validate tax units against DOTAX Table 12A.
    
    Args:
        tax_units: DataFrame with tax unit data
        weight_col: Column name for weights
        agi_col: Column name for AGI
        tax_col: Column name for Hawaii state tax
        
    Returns:
        DataFrame with validation results
    """
    validator = DotaxTable12AValidator()
    results = validator.validate_tax_units(tax_units, weight_col, agi_col, tax_col)
    validator.print_validation_report(results)
    return results
