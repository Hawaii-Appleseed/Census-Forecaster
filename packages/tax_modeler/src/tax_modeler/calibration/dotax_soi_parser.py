"""
Parser for Hawaii DOTAX Statistics of Income (SOI) 2022 data.

This module parses multiple DOTAX SOI tables:
- Table 5A: Filing Status Summary (Residents)
- Table 13A: Tax Rates by Income - Single/MFS (Residents)
- Table 13B: Tax Rates by Income - MFJ/QW (Residents)
- Table 13C: Tax Rates by Income - HoH (Residents)
- Table 17A: Nonresident Tax Liability by AGI

The data distinguishes between residents and nonresidents, which is critical
for accurate Hawaii tax modeling.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class DOTAXSOIParser:
    """Parser for Hawaii DOTAX SOI 2022 data files."""
    
    def __init__(self, data_dir: str = "data/raw"):
        """
        Initialize parser with data directory.
        
        Args:
            data_dir: Directory containing DOTAX SOI CSV files
        """
        self.data_dir = Path(data_dir)
        self._validate_files()
        
    def _validate_files(self):
        """Check that all required SOI files exist."""
        required_files = [
            "Dotax Soi 2022 - 4.csv",    # Filing status by residency
            "Dotax Soi 2022 - 5A.csv",   # Filing status summary
            "Dotax Soi 2022 - 13A.csv",  # Single/MFS tax rates
            "Dotax Soi 2022 - 13B.csv",  # MFJ/QW tax rates
            "Dotax Soi 2022 - 13C.csv",  # HoH tax rates
            "Dotax Soi 2022 - 17A.csv",  # Nonresident data
        ]
        
        missing = []
        for file in required_files:
            if not (self.data_dir / file).exists():
                missing.append(file)
        
        if missing:
            raise FileNotFoundError(
                f"Missing DOTAX SOI files in {self.data_dir}: {missing}"
            )
        
        logger.info(f"All DOTAX SOI files found in {self.data_dir}")
    
    def parse_filing_status_summary(self) -> pd.DataFrame:
        """
        Parse Table 5A: Filing Status Summary for Residents.
        
        Returns:
            DataFrame with columns:
                - filing_status: Filing status name
                - num_returns: Number of returns
                - pct_returns: Percentage of total returns
                - agi_positive: Positive AGI (millions)
                - pct_agi_positive: Percentage of positive AGI
                - agi_negative: Negative AGI (millions)
                - pct_agi_negative: Percentage of negative AGI
                - tax_before_credits: Tax liability before credits (millions)
                - pct_tax_before: Percentage of tax before credits
                - tax_after_credits: Tax liability after credits (millions)
                - pct_tax_after: Percentage of tax after credits
        """
        file_path = self.data_dir / "Dotax Soi 2022 - 5A.csv"
        
        # Read CSV, skip header rows
        df = pd.read_csv(file_path, skiprows=4, nrows=6)
        
        # Clean column names
        df.columns = [
            'filing_status', 'num_returns', 'pct_returns',
            'agi_positive', 'pct_agi_positive',
            'agi_negative', 'pct_agi_negative',
            'tax_before_credits', 'pct_tax_before',
            'tax_after_credits', 'pct_tax_after'
        ]
        
        # Clean numeric columns
        numeric_cols = df.columns[1:]
        for col in numeric_cols:
            df[col] = df[col].astype(str).str.replace('$', '').str.replace(',', '').str.replace('%', '')
            df[col] = pd.to_numeric(df[col], errors='coerce')
        
        # Convert percentages to decimals
        pct_cols = [c for c in df.columns if 'pct' in c]
        for col in pct_cols:
            df[col] = df[col] / 100
        
        logger.info(f"Parsed filing status summary: {len(df)} filing statuses")
        return df
    
    def parse_filing_status_by_residency(self) -> pd.DataFrame:
        """
        Parse Table 4: Filing Status by Residency (2022).
        
        Returns:
            DataFrame with columns:
                - filing_status: Filing status name
                - all_returns: Total returns (residents + nonresidents)
                - all_pct: Percentage of all returns
                - resident_returns: Resident returns
                - resident_pct: Percentage of resident returns
                - nonresident_returns: Nonresident returns
                - nonresident_pct: Percentage of nonresident returns
        """
        file_path = self.data_dir / "Dotax Soi 2022 - 4.csv"
        
        # Read CSV, skip header rows, read 2022 data only
        df = pd.read_csv(file_path, skiprows=6, nrows=7)
        
        # Clean column names
        df.columns = [
            'filing_status', 
            'all_returns', 'all_pct',
            'resident_returns', 'resident_pct',
            'nonresident_returns', 'nonresident_pct'
        ]
        
        # Clean filing status names (remove leading spaces)
        df['filing_status'] = df['filing_status'].str.strip()
        
        # Clean numeric columns
        numeric_cols = df.columns[1:]
        for col in numeric_cols:
            df[col] = df[col].astype(str).str.replace('$', '').str.replace(',', '').str.replace('%', '').str.replace('n/a', '')
            df[col] = pd.to_numeric(df[col], errors='coerce')
        
        # Convert percentages to decimals
        pct_cols = [c for c in df.columns if 'pct' in c]
        for col in pct_cols:
            df[col] = df[col] / 100
        
        logger.info(f"Parsed filing status by residency: {len(df)} filing statuses")
        return df
    
    def parse_tax_rates_by_income(self, filing_status: str) -> pd.DataFrame:
        """
        Parse Table 13A/B/C: Tax Rates by Income for Residents.
        
        Args:
            filing_status: One of 'single_mfs', 'mfj_qw', 'hoh'
        
        Returns:
            DataFrame with columns:
                - income_bracket_min: Minimum taxable income
                - income_bracket_max: Maximum taxable income
                - num_returns: Number of returns
                - marginal_rate: Marginal tax rate
                - effective_rate_before: Average effective rate before credits
                - effective_rate_after: Average effective rate after credits
        """
        file_map = {
            'single_mfs': "Dotax Soi 2022 - 13A.csv",
            'mfj_qw': "Dotax Soi 2022 - 13B.csv",
            'hoh': "Dotax Soi 2022 - 13C.csv"
        }
        
        if filing_status not in file_map:
            raise ValueError(f"Invalid filing status: {filing_status}")
        
        file_path = self.data_dir / file_map[filing_status]
        
        # Read CSV, skip header rows
        df = pd.read_csv(file_path, skiprows=6, nrows=12)
        
        # Parse income brackets
        brackets = []
        for _, row in df.iterrows():
            # Extract min and max from bracket description
            bracket_text = ' '.join([str(row[i]) for i in range(4) if pd.notna(row[i])])
            
            # Parse bracket ranges
            if 'Not' in bracket_text and 'over' in bracket_text:
                min_val = 0
                max_match = re.search(r'\$([0-9,]+)', bracket_text)
                max_val = int(max_match.group(1).replace(',', '')) if max_match else None
            elif 'Over' in bracket_text and 'to' in bracket_text:
                amounts = re.findall(r'\$([0-9,]+)', bracket_text)
                min_val = int(amounts[0].replace(',', ''))
                max_val = int(amounts[1].replace(',', '')) if len(amounts) > 1 else None
            elif 'Over' in bracket_text:
                amounts = re.findall(r'\$([0-9,]+)', bracket_text)
                min_val = int(amounts[0].replace(',', ''))
                max_val = np.inf
            else:
                continue
            
            # Extract numeric values
            num_returns = int(str(row[4]).replace(',', ''))
            marginal_rate = float(str(row[5]).replace('%', '')) / 100
            effective_before = float(str(row[6]).replace('%', '')) / 100
            effective_after = float(str(row[7]).replace('%', '')) / 100
            
            brackets.append({
                'income_bracket_min': min_val,
                'income_bracket_max': max_val,
                'num_returns': num_returns,
                'marginal_rate': marginal_rate,
                'effective_rate_before': effective_before,
                'effective_rate_after': effective_after
            })
        
        result_df = pd.DataFrame(brackets)
        logger.info(f"Parsed {filing_status} tax rates: {len(result_df)} brackets")
        return result_df
    
    def parse_nonresident_data(self) -> pd.DataFrame:
        """
        Parse Table 17A: Nonresident Tax Liability by AGI.
        
        Returns:
            DataFrame with columns:
                - agi_bracket_min: Minimum AGI
                - agi_bracket_max: Maximum AGI
                - num_returns: Number of returns
                - pct_returns: Percentage of total returns
                - tax_before_millions: Tax before credits (millions)
                - pct_tax_before: Percentage of tax before credits
                - avg_tax_before: Average tax before credits
                - tax_after_millions: Tax after credits (millions)
                - pct_tax_after: Percentage of tax after credits
                - avg_tax_after: Average tax after credits
        """
        file_path = self.data_dir / "Dotax Soi 2022 - 17A.csv"
        
        # Read CSV, skip header rows
        df = pd.read_csv(file_path, skiprows=5, nrows=13)
        
        # Parse AGI brackets
        brackets = []
        for _, row in df.iterrows():
            # Extract min and max from bracket description
            bracket_text = ' '.join([str(row[i]) for i in range(3) if pd.notna(row[i])])
            
            # Parse bracket ranges
            if 'Less' in bracket_text:
                min_val = -np.inf
                max_val = 10000
            elif 'and over' in bracket_text:
                amounts = re.findall(r'\$([0-9,]+)', bracket_text)
                min_val = int(amounts[0].replace(',', ''))
                max_val = np.inf
            elif 'Composite' in bracket_text:
                # Skip composite returns row
                continue
            elif 'TOTAL' in bracket_text:
                # Skip total row
                continue
            else:
                amounts = re.findall(r'\$([0-9,]+)', bracket_text)
                if len(amounts) >= 2:
                    min_val = int(amounts[0].replace(',', ''))
                    max_val = int(amounts[1].replace(',', ''))
                else:
                    continue
            
            # Extract numeric values
            num_returns = int(str(row[3]).replace(',', '').strip())
            pct_returns = float(str(row[4]).replace('%', '')) / 100
            tax_before = float(str(row[5]).replace('$', '').replace(',', ''))
            pct_tax_before = float(str(row[6]).replace('%', '')) / 100
            avg_before = float(str(row[7]).replace('$', '').replace(',', ''))
            tax_after = float(str(row[8]).replace('$', '').replace(',', ''))
            pct_tax_after = float(str(row[9]).replace('%', '')) / 100
            avg_after = float(str(row[10]).replace('$', '').replace(',', ''))
            
            brackets.append({
                'agi_bracket_min': min_val,
                'agi_bracket_max': max_val,
                'num_returns': num_returns,
                'pct_returns': pct_returns,
                'tax_before_millions': tax_before,
                'pct_tax_before': pct_tax_before,
                'avg_tax_before': avg_before,
                'tax_after_millions': tax_after,
                'pct_tax_after': pct_tax_after,
                'avg_tax_after': avg_after
            })
        
        result_df = pd.DataFrame(brackets)
        logger.info(f"Parsed nonresident data: {len(result_df)} AGI brackets")
        return result_df
    
    def get_resident_totals(self) -> dict[str, float]:
        """
        Get total resident statistics from Table 5A.
        
        Returns:
            Dictionary with:
                - total_returns: Total number of resident returns
                - total_agi_positive: Total positive AGI (millions)
                - total_agi_negative: Total negative AGI (millions)
                - total_tax_before: Total tax before credits (millions)
                - total_tax_after: Total tax after credits (millions)
        """
        df = self.parse_filing_status_summary()
        total_row = df[df['filing_status'].str.contains('TOTAL', na=False)].iloc[0]
        
        return {
            'total_returns': int(total_row['num_returns']),
            'total_agi_positive': float(total_row['agi_positive']),
            'total_agi_negative': float(total_row['agi_negative']),
            'total_tax_before': float(total_row['tax_before_credits']),
            'total_tax_after': float(total_row['tax_after_credits'])
        }
    
    def get_nonresident_totals(self) -> dict[str, float]:
        """
        Get total nonresident statistics from Table 17A.
        
        Returns:
            Dictionary with:
                - total_returns: Total number of nonresident returns
                - total_tax_before: Total tax before credits (millions)
                - total_tax_after: Total tax after credits (millions)
                - avg_tax_before: Average tax before credits
                - avg_tax_after: Average tax after credits
        """
        # Read the TOTAL row directly
        file_path = self.data_dir / "Dotax Soi 2022 - 17A.csv"
        df = pd.read_csv(file_path, skiprows=5)
        
        total_row = df[df.iloc[:, 0].str.contains('TOTAL', na=False)].iloc[0]
        
        return {
            'total_returns': int(str(total_row.iloc[3]).replace(',', '').strip()),
            'total_tax_before': float(str(total_row.iloc[5]).replace('$', '').replace(',', '')),
            'total_tax_after': float(str(total_row.iloc[8]).replace('$', '').replace(',', '')),
            'avg_tax_before': float(str(total_row.iloc[7]).replace('$', '').replace(',', '')),
            'avg_tax_after': float(str(total_row.iloc[10]).replace('$', '').replace(',', ''))
        }
    
    def get_combined_totals(self) -> dict[str, float]:
        """
        Get combined resident + nonresident totals.
        
        Returns:
            Dictionary with combined statistics
        """
        resident = self.get_resident_totals()
        nonresident = self.get_nonresident_totals()
        
        return {
            'total_returns': resident['total_returns'] + nonresident['total_returns'],
            'resident_returns': resident['total_returns'],
            'nonresident_returns': nonresident['total_returns'],
            'pct_nonresident': nonresident['total_returns'] / (resident['total_returns'] + nonresident['total_returns']),
            'total_tax_before': resident['total_tax_before'] + nonresident['total_tax_before'],
            'total_tax_after': resident['total_tax_after'] + nonresident['total_tax_after']
        }


def main():
    """Demo usage of DOTAX SOI parser."""
    logging.basicConfig(level=logging.INFO)
    
    parser = DOTAXSOIParser()
    
    print("\n" + "="*80)
    print("HAWAII DOTAX SOI 2022 DATA SUMMARY")
    print("="*80)
    
    # Filing status summary
    print("\n### FILING STATUS SUMMARY (RESIDENTS) ###")
    filing_status_df = parser.parse_filing_status_summary()
    print(filing_status_df.to_string(index=False))
    
    # Tax rates by filing status
    for status in ['single_mfs', 'mfj_qw', 'hoh']:
        print(f"\n### TAX RATES BY INCOME - {status.upper()} ###")
        rates_df = parser.parse_tax_rates_by_income(status)
        print(rates_df.to_string(index=False))
    
    # Nonresident data
    print("\n### NONRESIDENT TAX LIABILITY BY AGI ###")
    nonres_df = parser.parse_nonresident_data()
    print(nonres_df.to_string(index=False))
    
    # Combined totals
    print("\n### COMBINED TOTALS (RESIDENTS + NONRESIDENTS) ###")
    totals = parser.get_combined_totals()
    for key, value in totals.items():
        if 'pct' in key:
            print(f"{key}: {value:.2%}")
        elif isinstance(value, float):
            print(f"{key}: ${value:,.0f}M" if value > 100 else f"{key}: {value:,.0f}")
        else:
            print(f"{key}: {value:,}")
    
    print("\n" + "="*80)
    print(f"KEY INSIGHT: {totals['pct_nonresident']:.1%} of Hawaii tax returns are from NONRESIDENTS")
    print("="*80)


if __name__ == '__main__':
    main()
