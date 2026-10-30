"""
Hierarchical occupation code matching between PUMS SOCP and BLS OES SOC codes.

Implements the three-tier matching strategy:
1. Exact 6-digit match (65.5% coverage, confidence: 1.0)
2. Major group match (26.2% coverage, confidence: 0.7)
3. State-wide fallback (8.3% coverage, confidence: 0.4)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class OccupationMatch:
    """Result of occupation matching with confidence score."""
    pums_code: str
    bls_code: str | None
    match_level: str  # 'exact', 'major_group', 'state_wide'
    confidence: float
    wage_data: dict[str, float] | None = None
    

class OccupationMatcher:
    """
    Hierarchical matcher for PUMS SOCP → BLS OES SOC codes.
    
    Provides wage data lookup with confidence scoring based on match quality.
    """
    
    def __init__(self, bls_oes_data: pd.DataFrame):
        """
        Initialize matcher with BLS OES data.
        
        Args:
            bls_oes_data: DataFrame with BLS OES occupation wage data
                         Must have columns: occupation_code, mean_wage_annual, etc.
        """
        self.bls_data = bls_oes_data
        self._build_lookup_tables()
        
    def _build_lookup_tables(self) -> None:
        """Build optimized lookup tables for matching."""
        
        # Detect if we're using summary data or detailed timeseries data
        has_summary_cols = 'avg_mean_wage' in self.bls_data.columns
        
        # Exact match lookup
        self.exact_matches = {}
        for _, row in self.bls_data.iterrows():
            code = row['occupation_code']
            
            if has_summary_cols:
                # Using summary data
                self.exact_matches[code] = {
                    'mean_wage': row.get('avg_mean_wage'),
                    'median_wage': row.get('avg_median_wage'),
                    'employment': row.get('avg_employment'),
                    'wage_growth': row.get('annual_wage_growth', 0)
                }
            else:
                # Using detailed timeseries data
                self.exact_matches[code] = {
                    'mean_wage': row.get('mean_wage_annual'),
                    'median_wage': row.get('median_wage_annual'),
                    'employment': row.get('employment'),
                    'wage_growth': row.get('mean_wage_annual_cagr', 0)
                }
        
        # Major group aggregates (2-digit codes)
        self.major_group_data = {}
        major_groups = self.bls_data['occupation_code'].str[:2].unique()
        
        # Determine column names based on data type
        emp_col = 'avg_employment' if has_summary_cols else 'employment'
        mean_wage_col = 'avg_mean_wage' if has_summary_cols else 'mean_wage_annual'
        median_wage_col = 'avg_median_wage' if has_summary_cols else 'median_wage_annual'
        growth_col = 'annual_wage_growth' if has_summary_cols else 'mean_wage_annual_cagr'
        
        for major in major_groups:
            major_data = self.bls_data[self.bls_data['occupation_code'].str.startswith(major)]
            
            if not major_data.empty and emp_col in major_data.columns:
                # Employment-weighted averages
                total_emp = major_data[emp_col].sum()
                if total_emp > 0:
                    weights = major_data[emp_col] / total_emp
                    
                    self.major_group_data[major] = {
                        'mean_wage': np.average(major_data[mean_wage_col], weights=weights),
                        'median_wage': np.average(major_data[median_wage_col], weights=weights),
                        'employment': total_emp,
                        'wage_growth': major_data.get(growth_col, pd.Series([0])).mean(),
                        'n_occupations': len(major_data)
                    }
        
        # State-wide aggregates
        if emp_col in self.bls_data.columns:
            total_emp = self.bls_data[emp_col].sum()
            weights = self.bls_data[emp_col] / total_emp
            
            self.state_wide_data = {
                'mean_wage': np.average(self.bls_data[mean_wage_col], weights=weights),
                'median_wage': np.average(self.bls_data[median_wage_col], weights=weights),
                'wage_growth': self.bls_data.get(growth_col, pd.Series([0])).mean()
            }
        else:
            self.state_wide_data = {
                'mean_wage': self.bls_data[mean_wage_col].mean(),
                'median_wage': self.bls_data[median_wage_col].median(),
                'wage_growth': 0.02  # Default 2% if no growth data
            }
        
        logger.info("Built occupation lookup tables:")
        logger.info(f"  - Exact matches: {len(self.exact_matches)} codes")
        logger.info(f"  - Major groups: {len(self.major_group_data)} groups")
        logger.info("  - State-wide fallback available")
    
    def match(self, pums_socp: str) -> OccupationMatch:
        """
        Match a PUMS SOCP code to BLS wage data.
        
        Args:
            pums_socp: PUMS SOCP code (e.g., '434051' or '43-4051')
            
        Returns:
            OccupationMatch with wage data and confidence score
        """
        
        # Normalize SOCP format
        if pd.isna(pums_socp):
            return self._fallback_state_wide(None, "missing_code")
        
        socp_str = str(pums_socp).replace('-', '')
        
        # Convert to SOC format (XX-XXXX)
        if len(socp_str) >= 6:
            soc_code = f"{socp_str[:2]}-{socp_str[2:6]}"
            major_group = socp_str[:2]
        else:
            return self._fallback_state_wide(pums_socp, "invalid_format")
        
        # Try exact match
        if soc_code in self.exact_matches:
            return OccupationMatch(
                pums_code=pums_socp,
                bls_code=soc_code,
                match_level='exact',
                confidence=1.0,
                wage_data=self.exact_matches[soc_code]
            )
        
        # Try major group match
        if major_group in self.major_group_data:
            return OccupationMatch(
                pums_code=pums_socp,
                bls_code=f"{major_group}-XXXX",
                match_level='major_group',
                confidence=0.7,
                wage_data=self.major_group_data[major_group]
            )
        
        # Fallback to state-wide
        return self._fallback_state_wide(pums_socp, "no_match")
    
    def _fallback_state_wide(self, pums_socp: str | None, reason: str) -> OccupationMatch:
        """Create state-wide fallback match."""
        return OccupationMatch(
            pums_code=pums_socp or "unknown",
            bls_code=None,
            match_level='state_wide',
            confidence=0.4,
            wage_data=self.state_wide_data
        )
    
    def match_dataframe(
        self, 
        df: pd.DataFrame, 
        socp_column: str = 'SOCP',
        output_prefix: str = 'bls_'
    ) -> pd.DataFrame:
        """
        Match occupation codes for an entire DataFrame.
        
        Args:
            df: DataFrame with PUMS data
            socp_column: Name of column containing SOCP codes
            output_prefix: Prefix for output columns
            
        Returns:
            DataFrame with added BLS wage match columns
        """
        
        logger.info(f"Matching {len(df):,} records to BLS occupation codes")
        
        # Apply matching
        matches = df[socp_column].apply(self.match)
        
        # Extract match results
        df = df.copy()
        df[f'{output_prefix}match_level'] = [m.match_level for m in matches]
        df[f'{output_prefix}confidence'] = [m.confidence for m in matches]
        df[f'{output_prefix}matched_code'] = [m.bls_code for m in matches]
        
        # Extract wage data
        df[f'{output_prefix}mean_wage'] = [m.wage_data.get('mean_wage') if m.wage_data else None for m in matches]
        df[f'{output_prefix}wage_growth'] = [m.wage_data.get('wage_growth', 0) if m.wage_data else 0 for m in matches]
        
        # Report match statistics
        match_stats = df[f'{output_prefix}match_level'].value_counts()
        total = len(df)
        
        logger.info("Match results:")
        for level, count in match_stats.items():
            pct = count / total * 100
            logger.info(f"  - {level}: {count:,} ({pct:.1f}%)")
        
        avg_confidence = df[f'{output_prefix}confidence'].mean()
        logger.info(f"Average confidence: {avg_confidence:.2f}")
        
        return df
    
    def get_match_summary(self, df: pd.DataFrame, match_prefix: str = 'bls_') -> pd.DataFrame:
        """
        Generate summary statistics for occupation matching.
        
        Args:
            df: DataFrame with matched occupation data
            match_prefix: Prefix used for match columns
            
        Returns:
            DataFrame with match quality summary statistics
        """
        
        summary_data = []
        
        for level in ['exact', 'major_group', 'state_wide']:
            mask = df[f'{match_prefix}match_level'] == level
            level_data = df[mask]
            
            if len(level_data) > 0:
                summary_data.append({
                    'match_level': level,
                    'count': len(level_data),
                    'percent': len(level_data) / len(df) * 100,
                    'avg_confidence': level_data[f'{match_prefix}confidence'].mean(),
                    'avg_wage': level_data[f'{match_prefix}mean_wage'].mean(),
                    'avg_growth': level_data[f'{match_prefix}wage_growth'].mean()
                })
        
        return pd.DataFrame(summary_data)
