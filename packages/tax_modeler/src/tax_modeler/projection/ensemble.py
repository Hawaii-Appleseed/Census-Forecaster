"""
Ensemble income projection system combining ACS and BLS data.

Projects income forward from base year (PUMS 2023) to target year using:
1. ACS aggregate income growth trends (state-level)
2. BLS occupation-specific wage growth (confidence-weighted)
3. Hierarchical matching with quality control
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from .occupation_matcher import OccupationMatcher

logger = logging.getLogger(__name__)


class EnsembleProjector:
    """
    Ensemble income projector combining multiple data sources.
    
    Balances aggregate income trends (ACS) with occupation-specific
    growth patterns (BLS OES) using confidence-weighted blending.
    """
    
    def __init__(
        self,
        acs_timeseries_dir: Path,
        bls_oes_data: pd.DataFrame,
        base_year: int = 2023,
        acs_weight: float = 0.4,
        bls_weight: float = 0.6
    ):
        """
        Initialize ensemble projector.
        
        Args:
            acs_timeseries_dir: Directory with ACS time-series data
            bls_oes_data: BLS OES occupation wage data with growth rates
            base_year: Base year for PUMS data
            acs_weight: Weight for ACS aggregate trend (0-1)
            bls_weight: Weight for BLS occupation-specific growth (0-1)
        """
        self.acs_dir = Path(acs_timeseries_dir)
        self.bls_data = bls_oes_data
        self.base_year = base_year
        self.acs_weight = acs_weight
        self.bls_weight = bls_weight
        
        # Initialize occupation matcher
        self.matcher = OccupationMatcher(bls_oes_data)
        
        # Load ACS income growth trends
        self._load_acs_growth()
        
        logger.info("Initialized EnsembleProjector:")
        logger.info(f"  Base year: {base_year}")
        logger.info(f"  Ensemble weights: ACS={acs_weight:.1f}, BLS={bls_weight:.1f}")
    
    def _load_acs_growth(self) -> None:
        """Load and calculate ACS income growth rates."""
        
        # Load median household income table (B19013)
        income_file = self.acs_dir / 'wide' / 'B19013_wide.parquet'
        
        if not income_file.exists():
            logger.warning(f"ACS income file not found: {income_file}")
            logger.warning("Using default 2% annual growth rate")
            self.acs_growth_rate = 0.02
            return
        
        income_df = pd.read_parquet(income_file)
        
        # Calculate compound annual growth rate
        income_df = income_df.sort_values('year')
        
        if len(income_df) >= 2:
            first_year = income_df['year'].iloc[0]
            last_year = income_df['year'].iloc[-1]
            first_income = income_df['median_household_income'].iloc[0]
            last_income = income_df['median_household_income'].iloc[-1]
            
            years_span = last_year - first_year
            if years_span > 0 and first_income > 0:
                cagr = (last_income / first_income) ** (1 / years_span) - 1
                self.acs_growth_rate = cagr
                
                logger.info(f"ACS income growth rate: {cagr:.2%} annually ({first_year}-{last_year})")
            else:
                self.acs_growth_rate = 0.02
        else:
            self.acs_growth_rate = 0.02
        
        # Store full ACS time series for reference
        self.acs_timeseries = income_df
    
    def project_income(
        self,
        tax_units: pd.DataFrame,
        pums_persons: pd.DataFrame,
        target_year: int,
        income_column: str = 'income'
    ) -> pd.DataFrame:
        """
        Project tax unit incomes from base year to target year.
        
        Args:
            tax_units: DataFrame with tax units (from base year)
            pums_persons: DataFrame with PUMS person data including SOCP codes
            target_year: Target year for projection
            income_column: Name of income column to project
            
        Returns:
            DataFrame with projected incomes for target year
        """
        
        logger.info(f"Projecting income from {self.base_year} to {target_year}")
        logger.info(f"Tax units: {len(tax_units):,}")
        
        years_forward = target_year - self.base_year
        
        if years_forward == 0:
            logger.info("No projection needed (target year equals base year)")
            return tax_units.copy()
        
        if years_forward < 0:
            raise ValueError(f"Target year {target_year} is before base year {self.base_year}")
        
        # Create working copy
        projected = tax_units.copy()
        
        # Match tax units to person-level occupation codes
        logger.info("Matching tax units to occupation codes...")
        projected = self._link_occupation_codes(projected, pums_persons)
        
        # Apply ensemble growth projection
        logger.info("Applying ensemble growth projection...")
        projected = self._apply_ensemble_growth(
            projected,
            years_forward,
            income_column
        )
        
        # Add projection metadata
        projected['base_year'] = self.base_year
        projected['projection_year'] = target_year
        projected['years_projected'] = years_forward
        
        logger.info("Projection complete:")
        logger.info(f"  Average growth: {((projected[income_column] / tax_units[income_column]).mean() - 1) * 100:.1f}%")
        logger.info(f"  Median growth: {((projected[income_column] / tax_units[income_column]).median() - 1) * 100:.1f}%")
        
        return projected
    
    def _link_occupation_codes(
        self,
        tax_units: pd.DataFrame,
        pums_persons: pd.DataFrame
    ) -> pd.DataFrame:
        """Link tax units to person-level occupation codes."""
        
        # Join primary filer occupation
        primary_occs = pums_persons[['SERIALNO', 'SPORDER', 'SOCP']].copy()
        
        # Ensure consistent data types for merge
        tax_units = tax_units.copy()
        if 'primary_filer_id' in tax_units.columns:
            tax_units['primary_filer_id'] = pd.to_numeric(tax_units['primary_filer_id'], errors='coerce')
        if 'secondary_filer_id' in tax_units.columns:
            tax_units['secondary_filer_id'] = pd.to_numeric(tax_units['secondary_filer_id'], errors='coerce')
        
        # Match on primary filer
        tax_units = tax_units.merge(
            primary_occs.rename(columns={'SERIALNO': 'hh_id', 'SPORDER': 'primary_filer_id', 'SOCP': 'primary_socp'}),
            on=['hh_id', 'primary_filer_id'],
            how='left'
        )
        
        # For joint filers, also get secondary filer occupation
        if 'secondary_filer_id' in tax_units.columns:
            tax_units = tax_units.merge(
                primary_occs.rename(columns={'SERIALNO': 'hh_id', 'SPORDER': 'secondary_filer_id', 'SOCP': 'secondary_socp'}),
                on=['hh_id', 'secondary_filer_id'],
                how='left'
            )
        
        # Use primary SOCP (or secondary if primary missing)
        tax_units['socp'] = tax_units['primary_socp'].fillna(
            tax_units.get('secondary_socp', pd.Series(dtype=object))
        )
        
        logger.info(f"Linked occupation codes: {tax_units['socp'].notna().sum():,} / {len(tax_units):,} ({tax_units['socp'].notna().sum() / len(tax_units) * 100:.1f}%)")
        
        return tax_units
    
    def _apply_ensemble_growth(
        self,
        tax_units: pd.DataFrame,
        years_forward: int,
        income_column: str
    ) -> pd.DataFrame:
        """Apply ensemble growth projection to incomes."""
        
        # Match occupations to BLS wage data
        tax_units = self.matcher.match_dataframe(tax_units, socp_column='socp')
        
        # Calculate ACS aggregate growth factor
        acs_factor = (1 + self.acs_growth_rate) ** years_forward
        
        # Calculate BLS occupation-specific growth factors
        bls_factor = (1 + tax_units['bls_wage_growth']) ** years_forward
        
        # Blend using confidence-weighted ensemble
        # High confidence BLS data gets more weight, low confidence falls back to ACS
        confidence = tax_units['bls_confidence']
        
        # Adjust ensemble weights by confidence
        effective_bls_weight = self.bls_weight * confidence
        effective_acs_weight = 1 - effective_bls_weight
        
        # Combined growth factor
        ensemble_factor = (
            effective_acs_weight * acs_factor +
            effective_bls_weight * bls_factor
        )
        
        # Apply to income
        tax_units[f'{income_column}_original'] = tax_units[income_column]
        tax_units[income_column] = tax_units[income_column] * ensemble_factor
        
        # Store growth factor for analysis
        tax_units['income_growth_factor'] = ensemble_factor
        tax_units['acs_contribution'] = effective_acs_weight * acs_factor
        tax_units['bls_contribution'] = effective_bls_weight * bls_factor
        
        logger.info("Growth factor statistics:")
        logger.info(f"  Mean: {ensemble_factor.mean():.3f} ({(ensemble_factor.mean() - 1) * 100:.1f}% growth)")
        logger.info(f"  Median: {ensemble_factor.median():.3f}")
        logger.info(f"  Min/Max: {ensemble_factor.min():.3f} / {ensemble_factor.max():.3f}")
        
        return tax_units
    
    def get_projection_summary(self, projected_units: pd.DataFrame) -> dict:
        """Generate summary statistics for projection."""
        
        summary = {
            'total_units': len(projected_units),
            'base_year': projected_units['base_year'].iloc[0] if 'base_year' in projected_units.columns else None,
            'projection_year': projected_units['projection_year'].iloc[0] if 'projection_year' in projected_units.columns else None,
            'years_forward': projected_units['years_projected'].iloc[0] if 'years_projected' in projected_units.columns else None,
        }
        
        # Income growth statistics
        if 'income_growth_factor' in projected_units.columns:
            summary.update({
                'avg_growth_factor': projected_units['income_growth_factor'].mean(),
                'median_growth_factor': projected_units['income_growth_factor'].median(),
                'avg_annual_growth': (projected_units['income_growth_factor'].mean() ** (1 / summary['years_forward']) - 1) if summary['years_forward'] > 0 else 0,
            })
        
        # Match quality statistics
        if 'bls_match_level' in projected_units.columns:
            match_counts = projected_units['bls_match_level'].value_counts().to_dict()
            summary['match_quality'] = {
                level: {'count': count, 'percent': count / len(projected_units) * 100}
                for level, count in match_counts.items()
            }
            summary['avg_confidence'] = projected_units['bls_confidence'].mean()
        
        # Income distribution changes
        if 'income_original' in projected_units.columns:
            summary['income_change'] = {
                'base_total': projected_units['income_original'].sum(),
                'projected_total': projected_units['income'].sum(),
                'total_growth_pct': (projected_units['income'].sum() / projected_units['income_original'].sum() - 1) * 100
            }
        
        return summary
