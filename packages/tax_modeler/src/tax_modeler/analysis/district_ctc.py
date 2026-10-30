#!/usr/bin/env python3
"""
District-Level Child Tax Credit Analysis

This module provides comprehensive analysis of Child Tax Credit (CTC) estimates
at the district level for Hawaii, including:
- Legislative district aggregation
- County-level summaries  
- Demographic breakdowns
- Policy impact analysis
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pandas as pd

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent.parent))

from tax_modeler.credits.ctc import calculate_ctc

logger = logging.getLogger(__name__)

class DistrictCTCAnalyzer:
    """
    Comprehensive district-level CTC analysis for Hawaii.
    """
    
    def __init__(self, tax_units_df: pd.DataFrame, person_df: pd.DataFrame | None = None):
        """
        Initialize the district CTC analyzer.
        
        Args:
            tax_units_df: DataFrame with tax units and geographic information
            person_df: Optional person-level data for additional demographics
        """
        self.tax_units_df = tax_units_df.copy()
        self.person_df = person_df
        
        # Ensure required columns exist
        self._validate_data()
        
        # Calculate CTC if not already done
        if 'ctc_total' not in self.tax_units_df.columns:
            self._calculate_ctc_for_all_units()
    
    def _validate_data(self):
        """Validate that required columns exist in the data."""
        required_cols = ['PUMA', 'weight', 'filing_status', 'income']
        missing_cols = [col for col in required_cols if col not in self.tax_units_df.columns]
        
        if missing_cols:
            raise ValueError(f"Missing required columns: {missing_cols}")
        
        logger.info(f"Validated data with {len(self.tax_units_df):,} tax units")
    
    def _calculate_ctc_for_all_units(self):
        """Calculate CTC for all tax units if not already done."""
        logger.info("Calculating CTC for all tax units...")
        
        ctc_results = []
        for _, unit in self.tax_units_df.iterrows():
            unit_dict = unit.to_dict()
            ctc_result = calculate_ctc(unit_dict)
            ctc_results.append(ctc_result)
        
        # Add CTC results to DataFrame
        ctc_df = pd.DataFrame(ctc_results)
        for col in ctc_df.columns:
            self.tax_units_df[f'ctc_{col}'] = ctc_df[col]
        
        logger.info("CTC calculations completed")
    
    def generate_district_estimates(self, 
                                  district_type: str = 'house',
                                  include_demographics: bool = True) -> pd.DataFrame:
        """
        Generate CTC estimates by legislative district.
        
        Args:
            district_type: Type of district ('house', 'senate', or 'county')
            include_demographics: Whether to include demographic breakdowns
            
        Returns:
            DataFrame with district-level CTC statistics
        """
        logger.info(f"Generating {district_type} district estimates...")
        
        # Add district mappings
        df_with_districts = self._add_district_mappings(district_type)
        
        # Determine the district column name
        district_col = f'{district_type}_district' if district_type != 'county' else 'county'
        
        # Filter out unmapped districts
        df_mapped = df_with_districts[~df_with_districts[district_col].isna()].copy()
        
        if len(df_mapped) == 0:
            logger.warning("No tax units could be mapped to districts")
            return pd.DataFrame()
            
        # Group by district and calculate statistics
        results = []
        for district_name, district_group in df_mapped.groupby(district_col):
            try:
                stats = {'district': district_name}
                
                # Calculate basic CTC stats
                stats.update(self._calculate_district_ctc_stats(district_group, district_name))
                
                # Add demographic breakdowns if requested
                if include_demographics:
                    stats.update(self._calculate_demographic_breakdowns(district_group))
                
                results.append(stats)
                
            except Exception as e:
                logger.error(f"Error processing {district_type} district {district_name}: {e}")
                continue
        
        if not results:
            logger.error("No district results could be generated")
            return pd.DataFrame()
            
        results_df = pd.DataFrame(results)
        logger.info(f"Generated estimates for {len(results_df)} {district_type} districts")
        
        return results_df
    
    def _add_district_mappings(self, district_type: str) -> pd.DataFrame:
        """Add district mappings to tax units based on PUMA codes."""
        district_col = f'{district_type}_district'
        
        # Check if district column already exists in tax units
        if district_col in self.tax_units_df.columns:
            logger.info(f"{district_col} column already exists in tax units data")
            # Check for missing values
            missing_districts = self.tax_units_df[district_col].isna().sum()
            if missing_districts > 0:
                logger.warning(f"{missing_districts} tax units have missing {district_col} values")
            else:
                logger.info(f"All tax units have valid {district_col} assignments")
            return self.tax_units_df
        
        # If district column doesn't exist, create mapping from PUMA to district using official crosswalk
        logger.info(f"Creating {district_col} mappings from official 2022 Census crosswalk")
        
        # Load official crosswalk
        try:
            crosswalk_path = Path(__file__).parent.parent.parent / 'data' / 'crosswalks' / 'hawaii_puma_districts_official_2022.csv'
            crosswalk_df = pd.read_csv(crosswalk_path)
            logger.info(f"Loaded official crosswalk with {len(crosswalk_df)} PUMA-district combinations")
            
            # Select relevant columns based on district type
            if district_type == 'county':
                # Use county column directly for county analysis
                puma_df = crosswalk_df[['PUMA', 'county']].drop_duplicates()
                puma_df = puma_df.rename(columns={'county': district_col})
            else:
                district_col_name = f'{district_type}_district'
                puma_df = crosswalk_df[['PUMA', district_col_name]].drop_duplicates()
                puma_df = puma_df.rename(columns={district_col_name: district_col})
            
            # Ensure PUMA is string type and normalize PUMA codes
            self.tax_units_df['PUMA'] = self.tax_units_df['PUMA'].astype(str)
            puma_df['PUMA'] = puma_df['PUMA'].astype(str)
            
            # Remove leading zeros from tax units PUMA codes to match crosswalk format
            self.tax_units_df['PUMA'] = self.tax_units_df['PUMA'].str.lstrip('0')
            # Handle edge case where PUMA might be all zeros
            self.tax_units_df['PUMA'] = self.tax_units_df['PUMA'].replace('', '0')
            
            # Merge with tax units - use left join to preserve all tax units
            df_with_districts = self.tax_units_df.merge(
                puma_df,
                on='PUMA',
                how='left'
            )
            
            # Log mapping results
            mapped = df_with_districts[df_with_districts[district_col].notna()]
            unmapped = df_with_districts[df_with_districts[district_col].isna()]
            
            logger.info(f"Successfully mapped {len(mapped)} tax units to {district_type} districts")
            if len(unmapped) > 0:
                logger.warning(f"{len(unmapped)} tax units could not be mapped to {district_type} districts")
                logger.warning(f"Unmapped PUMAs: {unmapped['PUMA'].unique()}")
            
            return df_with_districts
            
        except Exception as e:
            logger.error(f"Error loading official crosswalk: {e}")
            # Fallback to old method
            puma_district_map = self._create_puma_district_mapping(district_type)
            
            puma_df = pd.DataFrame({
                'PUMA': list(puma_district_map.keys()),
                district_col: list(puma_district_map.values())
            })
            
            self.tax_units_df['PUMA'] = self.tax_units_df['PUMA'].astype(str)
            puma_df['PUMA'] = puma_df['PUMA'].astype(str)
            
            df_with_districts = self.tax_units_df.merge(
                puma_df,
                on='PUMA',
                how='left'
            )
            
            unmapped = df_with_districts[df_with_districts[district_col].isna()]
            if len(unmapped) > 0:
                logger.warning(f"{len(unmapped)} tax units could not be mapped to {district_type} districts")
            
            return df_with_districts
    
    def _create_puma_district_mapping(self, district_type: str) -> dict[str, str]:
        """Create PUMA to district mapping for Hawaii."""
        try:
            # Load crosswalk
            crosswalk_path = Path(__file__).parent.parent.parent / 'data' / 'crosswalks' / 'hawaii_puma_districts_3digit.csv'
            crosswalk = pd.read_csv(crosswalk_path, dtype={'PUMA': str})
            
            # Create mapping
            if district_type == 'county':
                mapping = dict(zip(crosswalk['PUMA'], crosswalk['county'], strict=False))
            else:
                mapping = dict(zip(crosswalk['PUMA'], crosswalk[f'{district_type}_district'].astype(str), strict=False))
                
            logger.info(f"Created {district_type} mapping for {len(mapping)} PUMAs")
            return mapping
            
        except Exception as e:
            logger.error(f"Error creating {district_type} mapping: {e}")
            raise
    
    def _calculate_district_ctc_stats(self, district_group: pd.DataFrame, district_name: str) -> dict:
        """Calculate CTC statistics for a single district."""
        # Apply weights for population scaling
        total_weight = district_group['weight'].sum()
        
        # Basic counts
        stats = {
            'total_tax_units': len(district_group),
            'weighted_tax_units': total_weight,
            'total_population_estimate': total_weight,  # Approximation
        }
        
        # CTC statistics (weighted)
        ctc_recipients = district_group[district_group['ctc_ctc_total'] > 0]
        
        stats.update({
            'ctc_recipient_units': len(ctc_recipients),
            'ctc_recipient_rate': len(ctc_recipients) / len(district_group) if len(district_group) > 0 else 0,
            'total_ctc_amount': (district_group['ctc_ctc_total'] * district_group['weight']).sum(),
            'total_refundable_ctc': (district_group['ctc_ctc_refundable'] * district_group['weight']).sum(),
            'total_nonrefundable_ctc': (district_group['ctc_ctc_nonrefundable'] * district_group['weight']).sum(),
            'total_qualifying_children': (district_group['ctc_qualifying_children'] * district_group['weight']).sum(),
            'avg_ctc_per_unit': (district_group['ctc_ctc_total'] * district_group['weight']).sum() / total_weight if total_weight > 0 else 0,
            'avg_ctc_per_recipient': (ctc_recipients['ctc_ctc_total'] * ctc_recipients['weight']).sum() / ctc_recipients['weight'].sum() if len(ctc_recipients) > 0 else 0,
        })
        
        return stats
    
    def _calculate_demographic_breakdowns(self, district_group: pd.DataFrame) -> dict:
        """Calculate demographic breakdowns for a district."""
        breakdowns = {}
        
        # Income quintiles
        district_group['income_quintile'] = pd.qcut(
            district_group['income'], 
            q=5, 
            labels=['Q1_Lowest', 'Q2_Low', 'Q3_Middle', 'Q4_High', 'Q5_Highest']
        )
        
        # CTC by income quintile
        for quintile in district_group['income_quintile'].unique():
            if pd.notna(quintile):
                quintile_group = district_group[district_group['income_quintile'] == quintile]
                quintile_ctc = (quintile_group['ctc_ctc_total'] * quintile_group['weight']).sum()
                breakdowns[f'ctc_{quintile}'] = quintile_ctc
        
        # CTC by filing status
        for status in district_group['filing_status'].unique():
            status_group = district_group[district_group['filing_status'] == status]
            status_ctc = (status_group['ctc_ctc_total'] * status_group['weight']).sum()
            breakdowns[f'ctc_{status}'] = status_ctc
        
        # Family size analysis (if available)
        if 'num_children' in district_group.columns:
            for num_children in [0, 1, 2, 3, 4]:
                child_group = district_group[district_group['num_children'] == num_children]
                if len(child_group) > 0:
                    child_ctc = (child_group['ctc_ctc_total'] * child_group['weight']).sum()
                    breakdowns[f'ctc_{num_children}_children'] = child_ctc
        
        return breakdowns
    
    def generate_statewide_summary(self) -> dict:
        """Generate statewide CTC summary statistics."""
        logger.info("Generating statewide CTC summary...")
        
        total_weight = self.tax_units_df['weight'].sum()
        ctc_recipients = self.tax_units_df[self.tax_units_df['ctc_ctc_total'] > 0]
        
        summary = {
            'total_tax_units': len(self.tax_units_df),
            'weighted_tax_units': total_weight,
            'ctc_recipient_units': len(ctc_recipients),
            'ctc_recipient_rate': len(ctc_recipients) / len(self.tax_units_df),
            'total_ctc_amount': (self.tax_units_df['ctc_ctc_total'] * self.tax_units_df['weight']).sum(),
            'total_refundable_ctc': (self.tax_units_df['ctc_ctc_refundable'] * self.tax_units_df['weight']).sum(),
            'total_nonrefundable_ctc': (self.tax_units_df['ctc_ctc_nonrefundable'] * self.tax_units_df['weight']).sum(),
            'total_qualifying_children': (self.tax_units_df['ctc_qualifying_children'] * self.tax_units_df['weight']).sum(),
            'avg_ctc_per_unit': (self.tax_units_df['ctc_ctc_total'] * self.tax_units_df['weight']).sum() / total_weight,
            'avg_ctc_per_recipient': (ctc_recipients['ctc_ctc_total'] * ctc_recipients['weight']).sum() / ctc_recipients['weight'].sum() if len(ctc_recipients) > 0 else 0,
        }
        
        return summary
    
    def compare_districts(self, 
                         district_estimates: pd.DataFrame,
                         metric: str = 'total_ctc_amount') -> pd.DataFrame:
        """
        Compare districts by a specific CTC metric.
        
        Args:
            district_estimates: DataFrame from generate_district_estimates()
            metric: Metric to compare (e.g., 'total_ctc_amount', 'avg_ctc_per_unit')
            
        Returns:
            DataFrame with district rankings
        """
        comparison = district_estimates[['district', metric]].copy()
        comparison = comparison.sort_values(metric, ascending=False)
        comparison['rank'] = range(1, len(comparison) + 1)
        comparison['pct_of_total'] = comparison[metric] / comparison[metric].sum() * 100
        
        return comparison
    
    def export_district_profiles(self, 
                               output_dir: Path,
                               district_type: str = 'house') -> None:
        """
        Export individual district profiles to CSV files.
        
        Args:
            output_dir: Directory to save district profiles
            district_type: Type of district to analyze
        """
        logger.info(f"Exporting {district_type} district profiles...")
        
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Generate district estimates
        district_estimates = self.generate_district_estimates(
            district_type=district_type,
            include_demographics=True
        )
        
        # Save summary file
        summary_file = output_dir / f'hawaii_{district_type}_districts_ctc_summary.csv'
        district_estimates.to_csv(summary_file, index=False)
        logger.info(f"Saved summary to {summary_file}")
        
        # Save individual district files
        for _, district_row in district_estimates.iterrows():
            district_name = district_row['district']
            district_file = output_dir / f'{district_name}_ctc_profile.csv'
            
            # Create detailed profile for this district
            district_profile = pd.DataFrame([district_row])
            district_profile.to_csv(district_file, index=False)
        
        logger.info(f"Exported {len(district_estimates)} district profiles to {output_dir}")


def create_ctc_district_report(tax_units_df: pd.DataFrame, 
                              output_dir: str = 'output/district_analysis') -> dict:
    """
    Create comprehensive CTC district analysis report.
    
    Args:
        tax_units_df: DataFrame with tax units
        output_dir: Directory to save outputs
        
    Returns:
        Dictionary with summary statistics
    """
    logger.info("Creating comprehensive CTC district analysis report...")
    
    # Initialize analyzer
    analyzer = DistrictCTCAnalyzer(tax_units_df)
    
    # Generate statewide summary
    statewide_summary = analyzer.generate_statewide_summary()
    
    # Generate district estimates for all types
    results = {
        'statewide_summary': statewide_summary,
        'house_districts': analyzer.generate_district_estimates('house'),
        'senate_districts': analyzer.generate_district_estimates('senate'),
        'counties': analyzer.generate_district_estimates('county')
    }
    
    # Export profiles
    output_path = Path(output_dir)
    analyzer.export_district_profiles(output_path / 'house', 'house')
    analyzer.export_district_profiles(output_path / 'senate', 'senate')
    analyzer.export_district_profiles(output_path / 'county', 'county')
    
    logger.info("CTC district analysis report completed")
    return results
