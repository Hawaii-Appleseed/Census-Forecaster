"""
Geographic Analysis Module

This module provides functionality for analyzing tax credits at various geographic levels
including state, county, PUMA, and legislative districts.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass
class GeographicLevel:
    """Defines geographic aggregation levels."""
    STATE = 'state'
    COUNTY = 'county'
    PUMA = 'puma'
    SENATE_DISTRICT = 'senate_district'
    HOUSE_DISTRICT = 'house_district'


class GeographicAnalyzer:
    """
    Analyzes tax credits and benefits at various geographic levels.
    
    This class provides methods to aggregate tax unit data and calculate
    statistics at state, county, PUMA, and legislative district levels.
    """
    
    def __init__(self, tax_units_df: pd.DataFrame, puma_crosswalk: pd.DataFrame | None = None):
        """
        Initialize the GeographicAnalyzer.
        
        Args:
            tax_units_df: DataFrame containing tax units with CTC calculations
            puma_crosswalk: Optional DataFrame mapping PUMAs to legislative districts
        """
        self.tax_units_df = tax_units_df.copy()
        self.puma_crosswalk = puma_crosswalk
        
        # Ensure required columns exist
        self._validate_data()
        
        # Add geographic identifiers if crosswalk is provided
        if self.puma_crosswalk is not None:
            self._add_district_mappings()
    
    def _validate_data(self) -> None:
        """Validate that required columns exist in the data."""
        required_cols = ['PUMA', 'state']
        missing_cols = [col for col in required_cols if col not in self.tax_units_df.columns]
        
        if missing_cols:
            raise ValueError(f"Missing required geographic columns: {missing_cols}")
    
    def _add_district_mappings(self) -> None:
        """Add legislative district mappings using PUMA crosswalk."""
        if self.puma_crosswalk is not None:
            # Merge with crosswalk to add district information
            self.tax_units_df = self.tax_units_df.merge(
                self.puma_crosswalk,
                on=['state', 'PUMA'],
                how='left'
            )
    
    def analyze_ctc_by_geography(
        self, 
        level: str = GeographicLevel.STATE,
        weight_col: str = 'PWGTP'
    ) -> pd.DataFrame:
        """
        Analyze CTC statistics by geographic level.
        
        Args:
            level: Geographic level for analysis
            weight_col: Column name for survey weights
            
        Returns:
            DataFrame with CTC statistics by geographic area
        """
        # Define grouping column based on level
        group_cols = self._get_group_columns(level)
        
        # Check if CTC columns exist
        ctc_cols = [col for col in self.tax_units_df.columns if col.startswith('ctc_')]
        if not ctc_cols:
            raise ValueError("No CTC columns found. Run CTC calculation first.")
        
        # Calculate weighted statistics
        results = []
        
        for name, group in self.tax_units_df.groupby(group_cols):
            if isinstance(name, tuple):
                geo_dict = dict(zip(group_cols, name))
            else:
                geo_dict = {group_cols[0]: name}
            
            # Calculate weighted statistics
            weights = group[weight_col] if weight_col in group.columns else 1
            
            stats = {
                **geo_dict,
                'total_tax_units': len(group),
                'weighted_tax_units': weights.sum() if hasattr(weights, 'sum') else len(group),
                'units_with_ctc': len(group[group.get('ctc_ctc_total', 0) > 0]),
                'ctc_participation_rate': len(group[group.get('ctc_ctc_total', 0) > 0]) / len(group) * 100,
                'total_ctc_amount': self._weighted_sum(group.get('ctc_ctc_total', 0), weights),
                'average_ctc_per_unit': self._weighted_mean(group.get('ctc_ctc_total', 0), weights),
                'total_refundable_ctc': self._weighted_sum(group.get('ctc_ctc_refundable', 0), weights),
                'total_nonrefundable_ctc': self._weighted_sum(group.get('ctc_ctc_nonrefundable', 0), weights),
                'total_qualifying_children': self._weighted_sum(group.get('ctc_qualifying_children', 0), weights),
                'average_income': self._weighted_mean(group.get('income', 0), weights),
                'median_income': self._weighted_median(group.get('income', 0), weights)
            }
            
            # Add recipient-only statistics
            recipients = group[group.get('ctc_ctc_total', 0) > 0]
            if len(recipients) > 0:
                recipient_weights = recipients[weight_col] if weight_col in recipients.columns else 1
                stats['average_ctc_per_recipient'] = self._weighted_mean(
                    recipients.get('ctc_ctc_total', 0), recipient_weights
                )
            else:
                stats['average_ctc_per_recipient'] = 0
            
            results.append(stats)
        
        return pd.DataFrame(results).sort_values(group_cols)
    
    def _get_group_columns(self, level: str) -> list[str]:
        """Get grouping columns for the specified geographic level."""
        if level == GeographicLevel.STATE:
            return ['state']
        elif level == GeographicLevel.COUNTY:
            # For Hawaii, we'll use PUMA as a proxy for county since PUMS doesn't have county
            return ['state', 'PUMA']
        elif level == GeographicLevel.PUMA:
            return ['state', 'PUMA']
        elif level == GeographicLevel.SENATE_DISTRICT:
            if 'senate_district' not in self.tax_units_df.columns:
                raise ValueError("Senate district mapping not available. Provide PUMA crosswalk.")
            return ['state', 'senate_district']
        elif level == GeographicLevel.HOUSE_DISTRICT:
            if 'house_district' not in self.tax_units_df.columns:
                raise ValueError("House district mapping not available. Provide PUMA crosswalk.")
            return ['state', 'house_district']
        else:
            raise ValueError(f"Unknown geographic level: {level}")
    
    def _weighted_sum(self, values: pd.Series, weights: pd.Series | int) -> float:
        """Calculate weighted sum."""
        if hasattr(weights, '__len__') and len(weights) > 1:
            return (values * weights).sum()
        else:
            return values.sum()
    
    def _weighted_mean(self, values: pd.Series, weights: pd.Series | int) -> float:
        """Calculate weighted mean."""
        if hasattr(weights, '__len__') and len(weights) > 1:
            return (values * weights).sum() / weights.sum()
        else:
            return values.mean()
    
    def _weighted_median(self, values: pd.Series, weights: pd.Series | int) -> float:
        """Calculate weighted median (approximation)."""
        # For simplicity, return regular median
        # In practice, would implement proper weighted median calculation
        return values.median()
    
    def create_summary_report(self, levels: list[str] = None) -> dict[str, pd.DataFrame]:
        """
        Create a comprehensive summary report for multiple geographic levels.
        
        Args:
            levels: List of geographic levels to analyze
            
        Returns:
            Dictionary with DataFrames for each geographic level
        """
        if levels is None:
            levels = [GeographicLevel.STATE, GeographicLevel.PUMA]
        
        report = {}
        
        for level in levels:
            try:
                report[level] = self.analyze_ctc_by_geography(level)
            except ValueError as e:
                print(f"Warning: Could not analyze {level}: {e}")
                continue
        
        return report
    
    def compare_geographic_areas(
        self, 
        level: str, 
        metric: str = 'average_ctc_per_unit'
    ) -> pd.DataFrame:
        """
        Compare geographic areas by a specific metric.
        
        Args:
            level: Geographic level for comparison
            metric: Metric to compare (e.g., 'average_ctc_per_unit')
            
        Returns:
            DataFrame sorted by the specified metric
        """
        analysis = self.analyze_ctc_by_geography(level)
        
        if metric not in analysis.columns:
            raise ValueError(f"Metric '{metric}' not found in analysis results")
        
        return analysis.sort_values(metric, ascending=False)
    
    def get_top_areas(
        self, 
        level: str, 
        metric: str = 'total_ctc_amount', 
        n: int = 10
    ) -> pd.DataFrame:
        """
        Get top N geographic areas by a specific metric.
        
        Args:
            level: Geographic level
            metric: Metric to rank by
            n: Number of top areas to return
            
        Returns:
            DataFrame with top N areas
        """
        comparison = self.compare_geographic_areas(level, metric)
        return comparison.head(n)


def create_hawaii_puma_crosswalk() -> pd.DataFrame:
    """
    Create a basic PUMA to district crosswalk for Hawaii.
    
    This is a simplified mapping. In practice, you would use official
    crosswalk files from the Census Bureau or state redistricting office.
    
    Returns:
        DataFrame mapping PUMAs to legislative districts
    """
    # Hawaii PUMA codes (2020 Census)
    # This is a simplified example - actual crosswalks would be more detailed
    crosswalk_data = [
        {'state': 'Hawaii', 'PUMA': '00100', 'county': 'Honolulu', 'senate_district': 'SD01', 'house_district': 'HD01'},
        {'state': 'Hawaii', 'PUMA': '00200', 'county': 'Honolulu', 'senate_district': 'SD02', 'house_district': 'HD02'},
        {'state': 'Hawaii', 'PUMA': '00300', 'county': 'Hawaii', 'senate_district': 'SD03', 'house_district': 'HD03'},
        {'state': 'Hawaii', 'PUMA': '00400', 'county': 'Maui', 'senate_district': 'SD04', 'house_district': 'HD04'},
        {'state': 'Hawaii', 'PUMA': '00500', 'county': 'Kauai', 'senate_district': 'SD05', 'house_district': 'HD05'},
    ]
    
    return pd.DataFrame(crosswalk_data)


def analyze_ctc_by_all_levels(
    tax_units_df: pd.DataFrame, 
    include_districts: bool = False
) -> dict[str, pd.DataFrame]:
    """
    Convenience function to analyze CTC at all available geographic levels.
    
    Args:
        tax_units_df: DataFrame with tax units and CTC calculations
        include_districts: Whether to include legislative district analysis
        
    Returns:
        Dictionary with analysis results for each geographic level
    """
    # Create crosswalk if districts are requested
    crosswalk = create_hawaii_puma_crosswalk() if include_districts else None
    
    # Initialize analyzer
    analyzer = GeographicAnalyzer(tax_units_df, crosswalk)
    
    # Define levels to analyze
    levels = [GeographicLevel.STATE, GeographicLevel.PUMA]
    if include_districts:
        levels.extend([GeographicLevel.SENATE_DISTRICT, GeographicLevel.HOUSE_DISTRICT])
    
    # Create comprehensive report
    return analyzer.create_summary_report(levels)
