"""
Consumer Expenditure Survey (CEX) Data Loader.

This module loads and processes CEX data to provide:
- Income source distributions (wages, self-employment, investment, etc.)
- Non-wage income patterns that PUMS undercounts
- Expenditure-based income validation
- Income composition by demographic characteristics

Data Source: https://www.bls.gov/cex/pumd.htm
Free public microdata updated annually.

Key Features:
- Loads SAS7BDAT files from local directory
- Income source decomposition
- Investment and business income patterns
- Demographic-specific income distributions
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

# pyreadstat is needed only for SAS XPT inputs. Make it optional so the
# rest of tax_modeler imports cleanly without it; CEXLoader methods that
# actually read XPT files raise a clear ImportError at call time.
try:
    import pyreadstat
except ImportError:  # pragma: no cover
    pyreadstat = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# Constants for CEX data
CEX_INTERVIEW_DIR = 'intrvw22'
CEX_DIARY_DIR = 'diary22'
CEX_WEIGHTS_DIR = 'weights'

# Income-related variable mappings
# Based on CEX 2022 Interview Survey FMLI file structure
INCOME_VARIABLES = {
    'FINCBTXM': 'total_income',  # Total household income before taxes (annualized)
    'FSALARYM': 'wage_income',   # Wage and salary income (annualized)
    'FRRETIRM': 'retirement_income',  # retirement income (annualized)
    'INTRDVXM': 'investment_income',  #Interest and dividend income (annualized)
    'OTHRINCM': 'other_income',  # Other income (annualized)
    'RETSURVM': 'pension_income',  # Pension and survivor benefits (annualized)
}

# Demographic variables
DEMOGRAPHIC_VARS = {
    'AGE_REF': 'age_ref',  # Age of reference person
    'AGE2': 'age_spouse',  # Age of spouse
    'EDUCA': 'education_ref',  # Education of reference person
    'EDUCA2': 'education_spouse',  # Education of spouse
    'FAM_SIZE': 'family_size',  # Family size
    'MARITAL1': 'marital_status',  # Marital status
    'OCCUCOD1': 'occupation_ref',  # Occupation of reference person
    'OCCUCOD2': 'occupation_spouse',  # Occupation of spouse
    'RACE': 'race',  # Race of reference person
    'SEX_REF': 'sex_ref',  # Sex of reference person
    'STATE': 'state',  # State
    'PERSLT18': 'persons_under_18',  # Number of persons under 18
    'PERSOT64': 'persons_over_64',  # Number of persons over 64
    'REGION': 'region',  # Region
    'BLS_URBN': 'urban_rural',  # Urban/rural status
    'FAM_TYPE': 'family_type',  # Family type
}

@dataclass
class CEXIncomeProfile:
    """Container for CEX income composition data."""
    total_income: float
    wage_income: float
    self_employment_income: float
    investment_income: float
    rental_income: float
    retirement_income: float
    other_income: float
    age_group: str
    family_size: int
    education_level: str
    
    @property
    def wage_share(self) -> float:
        """Wage income as share of total."""
        return self.wage_income / self.total_income if self.total_income > 0 else 0
    
    @property
    def non_wage_share(self) -> float:
        """Non-wage income as share of total."""
        return 1 - self.wage_share
    
    @property
    def investment_share(self) -> float:
        """Investment income as share of total."""
        return self.investment_income / self.total_income if self.total_income > 0 else 0
    
    @property
    def business_share(self) -> float:
        """Self-employment income as share of total."""
        return self.self_employment_income / self.total_income if self.total_income > 0 else 0


class CEXLoader:
    """
    Loader for Consumer Expenditure Survey data.
    
    Provides income source distributions critical for modeling non-wage
    income that PUMS systematically undercounts.
    """
    
    # BLS CEX data URLs
    CEX_BASE_URL = "https://www.bls.gov/cex/pumd/data/comma"
    
    def __init__(
        self,
        data_dir: Path | None = None,
        cache_data: bool = True
    ):
        """
        Initialize CEX loader.
        
        Args:
            data_dir: Directory for cached data
            cache_data: Whether to cache downloaded data
        """
        self.data_dir = Path(data_dir) if data_dir else Path('data/external/cex')
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.cache_data = cache_data
        
        self._income_data_cache: dict[int, pd.DataFrame] = {}
        
        # Set up file paths for SAS data
        self.interview_dir = self.data_dir / CEX_INTERVIEW_DIR
        self.diary_dir = self.data_dir / CEX_DIARY_DIR
        self.weights_dir = self.data_dir / CEX_WEIGHTS_DIR
    
    def _load_sas_file(self, file_pattern: str, directory: Path | None = None) -> pd.DataFrame:
        """Load a SAS7BDAT file matching the given pattern.
        
        Args:
            file_pattern: Pattern to match file name (e.g., 'fmli2*')
            directory: Directory to search in (defaults to interview directory)
            
        Returns:
            DataFrame containing the loaded data
        """
        if directory is None:
            directory = self.interview_dir
            
        # Find matching files
        matches = list(directory.glob(f"**/{file_pattern}.sas7bdat"))
        if not matches:
            raise FileNotFoundError(f"No files matching {file_pattern} found in {directory}")
            
        # Load all matching files and concatenate
        dfs = []
        for file_path in matches:
            logger.info(f"Loading SAS file: {file_path}")
            df, meta = pyreadstat.read_sas7bdat(str(file_path))
            df.columns = df.columns.str.upper()
            dfs.append(df)
            
        # Concatenate all files
        result = pd.concat(dfs, ignore_index=True) if len(dfs) > 1 else dfs[0]
        return result
    
    def load_family_data(self) -> pd.DataFrame:
        """Load and process family characteristics data (FMLI)."""
        if 'family' in self._income_data_cache:
            return self._income_data_cache['family']
            
        logger.info("Loading family characteristics data...")
        df = self._load_sas_file('fmli2*')
        
        # Don't rename yet - keep original column names for processing
        # Renaming will happen in _process_income_data
        
        # Convert data types
        for col in df.select_dtypes(include=['object']).columns:
            df[col] = df[col].astype(str).str.strip()
            
        # Cache the result
        self._income_data_cache['family'] = df
        return df
    
    def load_expenditure_data(self) -> pd.DataFrame:
        """Load and process expenditure data (MTBI)."""
        if 'expenditure' in self._income_data_cache:
            return self._income_data_cache['expenditure']
            
        logger.info("Loading expenditure data...")
        df = self._load_sas_file('mtbi2*')
        
        # Standardize column names
        df.columns = df.columns.str.upper()
        
        # Convert data types
        for col in ['COST', 'UCCSEQ']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
                
        # Cache the result
        self._income_data_cache['expenditure'] = df
        return df
    
    def load_income_data(
        self,
        year: int = 2022,
        force_download: bool = False
    ) -> pd.DataFrame:
        """
        Load CEX income data for a specific year.
        
        Args:
            year: Year to load data for
            force_download: Force download even if cached
            
        Returns:
            DataFrame with income composition data
        """
        # Check cache
        if not force_download and year in self._income_data_cache:
            logger.info(f"Using cached CEX data for {year}")
            return self._income_data_cache[year]
        
        # Check file cache
        cache_file = self.data_dir / f'cex_income_{year}.parquet'
        if not force_download and cache_file.exists():
            logger.info(f"Loading CEX data from cache: {cache_file}")
            df = pd.read_parquet(cache_file)
            self._income_data_cache[year] = df
            return df
        
        # Download from BLS
        logger.info(f"Downloading CEX data for year {year}")
        df = self._download_cex_data(year)
        
        # Cache the data
        if self.cache_data:
            df.to_parquet(cache_file, index=False)
            logger.info(f"Cached CEX data to {cache_file}")
        
        self._income_data_cache[year] = df
        return df
    
    def _download_cex_data(self, year: int) -> pd.DataFrame:
        """
        Download CEX data from BLS.
        
        Note: CEX data is published as zip files containing CSV files.
        For production, implement actual download logic.
        """
        # Try to load from SAS files first
        try:
            logger.info(f"Loading CEX data from SAS files for year {year}")
            df = self.load_family_data()
            
            # Process income data
            income_df = self._process_income_data(df)
            return income_df
            
        except FileNotFoundError as e:
            logger.warning(f"Could not find SAS files: {e}")
            logger.warning("CEX download not fully implemented - using mock data")
            logger.info(f"To use real data, download from: {self.CEX_BASE_URL}")
            
            # Check if user has downloaded the data manually
            manual_file = self.data_dir / f'fmli{year}.csv'
            if manual_file.exists():
                logger.info(f"Found manually downloaded CEX file: {manual_file}")
                return self._parse_cex_fmli(manual_file, year)
            
            # Return mock data for development
            return self._create_mock_cex_data(year)
    
    def _process_income_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """Process family data to extract income information."""
        # Select income columns
        income_cols = {}
        for src_col, target_col in INCOME_VARIABLES.items():
            if src_col in df.columns:
                income_cols[src_col] = target_col
                
        if not income_cols:
            raise ValueError("No income variables found in the data")
            
        # Create a clean income DataFrame
        income_df = df[list(income_cols.keys())].copy()
        income_df = income_df.rename(columns=income_cols)
        
        # Convert to numeric, handling non-numeric values
        for col in income_df.columns:
            income_df[col] = pd.to_numeric(income_df[col], errors='coerce').fillna(0)
            
        # Calculate total income if not present
        if 'total_income' not in income_df.columns or income_df['total_income'].sum() == 0:
            # Sum up all income components
            income_components = [
                'wage_income', 'self_employment_income', 'business_income',
                'interest_income', 'dividend_income',
                'rental_income', 'retirement_income', 'pension_income',
                'social_security', 'unemployment_comp', 'welfare_income',
                'child_support', 'other_income'
            ]
            available_components = [c for c in income_components if c in income_df.columns]
            income_df['total_income'] = income_df[available_components].sum(axis=1)
            
        # Add demographic variables
        demo_cols = {k: v for k, v in DEMOGRAPHIC_VARS.items() 
                     if k in df.columns and v not in income_df.columns}
        if demo_cols:
            for src_col, target_col in demo_cols.items():
                income_df[target_col] = df[src_col]
            
        # Add age groups
        if 'age_ref' in income_df.columns:
            bins = [0, 25, 35, 45, 55, 65, 75, 200]
            labels = ['<25', '25-34', '35-44', '45-54', '55-64', '65-74', '75+']
            income_df['age_group'] = pd.cut(
                pd.to_numeric(income_df['age_ref'], errors='coerce'), 
                bins=bins, 
                labels=labels,
                right=False
            )
            
        # Filter out zero income records
        income_df = income_df[income_df['total_income'] > 0].copy()
        
        return income_df
    
    def _parse_cex_fmli(self, file_path: Path, year: int) -> pd.DataFrame:
        """
        Parse CEX Family (FMLI) file.
        
        The FMLI file contains family-level income and demographic data.
        Key variables:
        - FINCBTXM: Income before taxes
        - FSALARYM: Wage and salary income
        - FRRETIRM: Retirement income
        - FINC_BUS: Business/self-employment income
        - FINC_INT: Interest income
        - FINC_DIV: Dividend income
        """
        logger.info(f"Parsing CEX FMLI file: {file_path}")
        
        df = pd.read_csv(file_path)
        
        # Extract income variables (annualized)
        income_cols = {
            'FINCBTXM': 'total_income',
            'FSALARYM': 'wage_income',
            'FRRETIRM': 'retirement_income',
            'FINC_BUS': 'self_employment_income',
            'FINC_INT': 'interest_income',
            'FINC_DIV': 'dividend_income',
            'FINC_RNT': 'rental_income'
        }
        
        # Rename columns
        available_cols = {k: v for k, v in income_cols.items() if k in df.columns}
        df = df.rename(columns=available_cols)
        
        # Calculate investment income
        if 'interest_income' in df.columns and 'dividend_income' in df.columns:
            df['investment_income'] = df['interest_income'] + df['dividend_income']
        
        # Extract demographics
        demographic_cols = {
            'AGE_REF': 'age',
            'FAM_SIZE': 'family_size',
            'EDUC_REF': 'education',
            'REGION': 'region'
        }
        
        available_demo = {k: v for k, v in demographic_cols.items() if k in df.columns}
        df = df.rename(columns=available_demo)
        
        # Add year
        df['year'] = year
        
        # Calculate other income
        income_components = ['wage_income', 'self_employment_income', 'investment_income',
                           'rental_income', 'retirement_income']
        available_components = [c for c in income_components if c in df.columns]
        
        if 'total_income' in df.columns and available_components:
            df['other_income'] = df['total_income'] - df[available_components].sum(axis=1)
        
        logger.info(f"Parsed {len(df):,} CEX consumer units")
        
        return df
    
    def _create_mock_cex_data(self, year: int) -> pd.DataFrame:
        """
        Create mock CEX data for development/testing.
        
        This provides realistic income composition patterns.
        Replace with real data in production.
        """
        logger.warning("Creating mock CEX data - replace with real data in production")
        
        np.random.seed(42)
        n_samples = 5000
        
        # Generate realistic income distributions
        # Based on typical CEX patterns
        
        # Age groups
        ages = np.random.choice([25, 35, 45, 55, 65, 75], size=n_samples, 
                               p=[0.15, 0.20, 0.20, 0.20, 0.15, 0.10])
        
        # Family size
        family_sizes = np.random.choice([1, 2, 3, 4, 5], size=n_samples,
                                       p=[0.30, 0.35, 0.15, 0.15, 0.05])
        
        # Education (1=less than HS, 2=HS, 3=some college, 4=college, 5=grad)
        education = np.random.choice([1, 2, 3, 4, 5], size=n_samples,
                                    p=[0.10, 0.25, 0.25, 0.25, 0.15])
        
        # Total income (log-normal distribution)
        base_income = np.random.lognormal(mean=10.8, sigma=0.8, size=n_samples)
        
        # Adjust by age and education
        age_factor = 1 + (ages - 25) / 100  # Peak earnings in middle age
        age_factor = np.where(ages > 65, age_factor * 0.7, age_factor)  # Retirement reduction
        education_factor = 1 + (education - 3) * 0.2
        
        total_income = base_income * age_factor * education_factor
        
        # Income composition varies by age and income level
        # Younger: mostly wages
        # Middle age: wages + some investment/business
        # Older: retirement + investment
        
        wage_share = np.where(ages < 65, 0.75, 0.20)
        wage_share += np.random.normal(0, 0.1, n_samples)
        wage_share = np.clip(wage_share, 0.1, 0.95)
        
        retirement_share = np.where(ages >= 65, 0.50, 0.05)
        retirement_share += np.random.normal(0, 0.05, n_samples)
        retirement_share = np.clip(retirement_share, 0, 0.80)
        
        # Investment income increases with total income
        investment_share = np.minimum(0.30, total_income / 500000 * 0.20)
        investment_share += np.random.normal(0, 0.02, n_samples)
        investment_share = np.clip(investment_share, 0, 0.40)
        
        # Self-employment
        self_employment_share = np.random.beta(2, 10, n_samples) * 0.30
        
        # Normalize shares
        total_share = wage_share + retirement_share + investment_share + self_employment_share
        wage_share /= total_share
        retirement_share /= total_share
        investment_share /= total_share
        self_employment_share /= total_share
        
        # Calculate dollar amounts
        wage_income = total_income * wage_share
        retirement_income = total_income * retirement_share
        investment_income = total_income * investment_share
        self_employment_income = total_income * self_employment_share
        
        # Rental income (small share for some households)
        rental_income = np.where(
            np.random.random(n_samples) < 0.10,  # 10% have rental income
            total_income * np.random.beta(1, 20, n_samples),
            0
        )
        
        # Other income
        other_income = total_income - (wage_income + retirement_income + investment_income + 
                                      self_employment_income + rental_income)
        other_income = np.maximum(0, other_income)
        
        # Create DataFrame
        df = pd.DataFrame({
            'total_income': total_income,
            'wage_income': wage_income,
            'self_employment_income': self_employment_income,
            'investment_income': investment_income,
            'rental_income': rental_income,
            'retirement_income': retirement_income,
            'other_income': other_income,
            'age': ages,
            'family_size': family_sizes,
            'education': education,
            'year': year
        })
        
        return df
    
    def get_income_composition_by_group(
        self,
        year: int = 2022,
        group_by: str = 'age_group'
    ) -> pd.DataFrame:
        """
        Get income composition statistics by demographic group.
        
        Args:
            year: Year to analyze
            group_by: Variable to group by ('age_group', 'income_bracket', 'education')
            
        Returns:
            DataFrame with income composition by group
        """
        df = self.load_income_data(year)
        
        # Create grouping variable
        if group_by == 'age_group':
            df['group'] = pd.cut(
                df['age'],
                bins=[0, 35, 50, 65, 100],
                labels=['Under 35', '35-50', '50-65', '65+']
            )
        elif group_by == 'income_bracket':
            df['group'] = pd.qcut(
                df['total_income'],
                q=5,
                labels=['Bottom 20%', '20-40%', '40-60%', '60-80%', 'Top 20%']
            )
        elif group_by == 'education':
            df['group'] = pd.cut(
                df['education'],
                bins=[0, 2, 3, 4, 6],
                labels=['HS or less', 'Some college', 'College', 'Graduate']
            )
        else:
            raise ValueError(f"Unknown group_by: {group_by}")
        
        # Calculate shares
        income_cols = ['wage_income', 'self_employment_income', 'investment_income',
                      'rental_income', 'retirement_income', 'other_income']
        
        # Group statistics
        grouped = df.groupby('group').agg({
            'total_income': ['mean', 'median', 'count'],
            **{col: 'mean' for col in income_cols}
        })
        
        # Calculate shares
        for col in income_cols:
            grouped[(col, 'share')] = grouped[(col, 'mean')] / grouped[('total_income', 'mean')]
        
        return grouped
    
    def get_non_wage_income_patterns(
        self,
        year: int = 2022,
        income_percentile: int | None = None
    ) -> dict[str, float]:
        """
        Get non-wage income patterns for statistical matching.
        
        This is critical for imputing investment and business income
        that PUMS undercounts.
        
        Args:
            year: Year to analyze
            income_percentile: Optional income percentile to filter (e.g., 90 for top 10%)
            
        Returns:
            Dictionary with non-wage income statistics
        """
        df = self.load_income_data(year)
        
        # Filter by income percentile if specified
        if income_percentile:
            threshold = df['total_income'].quantile(income_percentile / 100)
            df = df[df['total_income'] >= threshold]
            logger.info(f"Filtered to top {100-income_percentile}% (income >= ${threshold:,.0f})")
        
        # Calculate statistics
        stats = {
            'mean_total_income': df['total_income'].mean(),
            'median_total_income': df['total_income'].median(),
            'mean_wage_income': df['wage_income'].mean(),
            'mean_self_employment': df['self_employment_income'].mean(),
            'mean_investment': df['investment_income'].mean(),
            'mean_rental': df['rental_income'].mean(),
            'mean_retirement': df['retirement_income'].mean(),
            'wage_share': (df['wage_income'] / df['total_income']).mean(),
            'self_employment_share': (df['self_employment_income'] / df['total_income']).mean(),
            'investment_share': (df['investment_income'] / df['total_income']).mean(),
            'rental_share': (df['rental_income'] / df['total_income']).mean(),
            'retirement_share': (df['retirement_income'] / df['total_income']).mean(),
            'pct_with_investment': (df['investment_income'] > 0).mean(),
            'pct_with_self_employment': (df['self_employment_income'] > 0).mean(),
            'pct_with_rental': (df['rental_income'] > 0).mean(),
            'sample_size': len(df)
        }
        
        return stats
    
    def create_income_composition_lookup(
        self,
        year: int = 2022,
        n_bins: int = 10
    ) -> pd.DataFrame:
        """
        Create lookup table for income composition by total income level.
        
        This can be used for statistical matching to impute income sources.
        
        Args:
            year: Year to create lookup for
            n_bins: Number of income bins
            
        Returns:
            DataFrame with income composition by income bin
        """
        df = self.load_income_data(year)
        
        # Create income bins
        df['income_bin'] = pd.qcut(df['total_income'], q=n_bins, labels=False, duplicates='drop')
        
        # Calculate statistics by bin
        income_cols = ['wage_income', 'self_employment_income', 'investment_income',
                      'rental_income', 'retirement_income', 'other_income']
        
        lookup = df.groupby('income_bin').agg({
            'total_income': ['min', 'max', 'mean', 'median'],
            **{col: ['mean', 'median', 'std'] for col in income_cols}
        })
        
        # Calculate shares
        for col in income_cols:
            lookup[(col, 'share')] = lookup[(col, 'mean')] / lookup[('total_income', 'mean')]
        
        # Add sample sizes
        lookup[('sample', 'count')] = df.groupby('income_bin').size()
        
        return lookup
    
    def match_income_composition(
        self,
        total_income: float,
        age: int | None = None,
        year: int = 2022
    ) -> dict[str, float]:
        """
        Match income composition for a given total income level.
        
        Uses CEX data to estimate income source breakdown.
        
        Args:
            total_income: Total income to match
            age: Optional age for better matching
            year: Year to use for matching
            
        Returns:
            Dictionary with estimated income composition
        """
        df = self.load_income_data(year)
        
        # Filter by age if provided
        if age:
            age_range = 10
            df = df[(df['age'] >= age - age_range) & (df['age'] <= age + age_range)]
        
        # Find similar income levels
        income_diff = np.abs(df['total_income'] - total_income)
        n_matches = min(100, len(df))
        similar_indices = income_diff.nsmallest(n_matches).index
        similar = df.loc[similar_indices]
        
        # Calculate average composition
        composition = {
            'wage_income': similar['wage_income'].mean(),
            'self_employment_income': similar['self_employment_income'].mean(),
            'investment_income': similar['investment_income'].mean(),
            'rental_income': similar['rental_income'].mean(),
            'retirement_income': similar['retirement_income'].mean(),
            'other_income': similar['other_income'].mean(),
            'n_matches': len(similar),
            'match_quality': 1.0 / (1.0 + income_diff.loc[similar_indices].mean() / total_income)
        }
        
        return composition
    
    def get_summary_statistics(self, year: int = 2022) -> dict:
        """Get summary statistics for CEX data."""
        df = self.load_income_data(year)
        
        return {
            'year': year,
            'sample_size': len(df),
            'mean_total_income': df['total_income'].mean(),
            'median_total_income': df['total_income'].median(),
            'income_composition': {
                'wage_share': (df['wage_income'] / df['total_income']).mean(),
                'self_employment_share': (df['self_employment_income'] / df['total_income']).mean(),
                'investment_share': (df['investment_income'] / df['total_income']).mean(),
                'rental_share': (df['rental_income'] / df['total_income']).mean(),
                'retirement_share': (df['retirement_income'] / df['total_income']).mean()
            },
            'prevalence': {
                'pct_with_wage': (df['wage_income'] > 0).mean(),
                'pct_with_self_employment': (df['self_employment_income'] > 0).mean(),
                'pct_with_investment': (df['investment_income'] > 0).mean(),
                'pct_with_rental': (df['rental_income'] > 0).mean(),
                'pct_with_retirement': (df['retirement_income'] > 0).mean()
            }
        }
