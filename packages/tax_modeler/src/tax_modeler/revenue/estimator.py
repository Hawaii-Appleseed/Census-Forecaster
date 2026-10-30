"""
Hawaii Tax Revenue Estimator

Combines Hawaii state income tax liability with CTC and EITC credits to
produce population-weighted revenue estimates at state, county, and
legislative district levels.

Data flow:
    tax_units_df (with income, earned_income, investment_income,
                  dependents_details, ctc_*, eitc_*, hi_*)
        → aggregate with weights
        → revenue summary tables

Key output columns (all dollar amounts are weighted population estimates):
    hi_tax_liability     - Hawaii state income tax before any credits
    hi_low_income_credit - Hawaii low-income refundable credit
    hi_net_revenue       - hi_tax_liability (net of Hawaii low-income credit)
    ctc_refundable       - Federal refundable CTC (ACTC) — reduces federal revenue
    eitc_amount          - Federal EITC — reduces federal revenue
    total_credits        - ctc_total + eitc_amount (all credits claimed)

Note: Federal credits (CTC, EITC) reduce FEDERAL revenue, not Hawaii revenue.
Hawaii has a refundable state EITC at 40% of federal EITC under HRS §235-55.75
(Act 209, 2023; rate raised from 20% non-refundable for TY 2023+). The state
cost flows through as ``hi_eitc_amount`` and reduces Hawaii net revenue once
:func:`~tax_modeler.credits.hi_eitc.compute_hi_eitc_for_units` has run.
Hawaii has no state CTC as of 2026.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Columns required on the input DataFrame
REQUIRED_COLS = ['weight', 'filing_status', 'income', 'hi_tax_liability']
CREDIT_COLS = ['ctc_total', 'ctc_refundable', 'eitc_amount']
GEO_COLS = ['PUMA', 'county', 'house_district', 'senate_district']


# ---------------------------------------------------------------------------
# Main estimator
# ---------------------------------------------------------------------------

class RevenueEstimator:
    """
    Compute weighted population-level revenue estimates from a tax-unit DataFrame.

    Usage:
        estimator = RevenueEstimator(tax_units_df)
        summary = estimator.state_summary()
        by_district = estimator.by_district('house_district')
        by_filing = estimator.by_filing_status()
        by_income = estimator.by_income_quintile()
    """

    def __init__(self, tax_units_df: pd.DataFrame):
        self._validate(tax_units_df)
        self.df = tax_units_df.copy()
        self._add_derived_columns()
        logger.info(
            f"RevenueEstimator initialized with {len(self.df):,} tax units "
            f"representing {self.df['weight'].sum():,.0f} filers."
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def state_summary(self) -> dict[str, float]:
        """
        Aggregate revenue estimates for the entire state of Hawaii.

        Returns a dict with dollar totals (weighted), counts, and effective rates.
        """
        w = self.df['weight']
        total_filers = w.sum()

        def wsum(col: str) -> float:
            if col not in self.df.columns:
                return 0.0
            return float((self.df[col] * w).sum())

        def wsum_pos(col: str) -> float:
            """Weighted sum of positive values only (liability owed, not refunds)."""
            if col not in self.df.columns:
                return 0.0
            vals = self.df[col].clip(lower=0)
            return float((vals * w).sum())

        total_income = wsum('income')
        hi_gross_tax = wsum('hi_tax_before_credits')
        hi_low_income_credit = wsum('hi_low_income_credit')
        hi_net_revenue = wsum('hi_tax_liability')
        ctc_total = wsum('ctc_total')
        ctc_refundable = wsum('ctc_refundable')
        eitc_total = wsum('eitc_amount')
        total_credits = ctc_total + eitc_total

        return {
            # Counts
            'total_weighted_filers': total_filers,
            'filers_with_hi_liability': float(
                w[self.df['hi_tax_liability'] > 0].sum()
            ),
            'filers_receiving_eitc': float(
                w[self.df.get('eitc_eligible', pd.Series(False, index=self.df.index))].sum()
            ),
            'filers_receiving_ctc': float(
                w[self.df.get('ctc_total', pd.Series(0.0, index=self.df.index)) > 0].sum()
            ),

            # Income
            'total_income': total_income,

            # Hawaii state tax
            'hi_gross_tax_revenue': hi_gross_tax,
            'hi_low_income_credit_cost': hi_low_income_credit,
            'hi_net_tax_revenue': hi_net_revenue,

            # Federal credits (reduce federal, not Hawaii, revenue)
            'total_ctc': ctc_total,
            'total_ctc_refundable': ctc_refundable,
            'total_eitc': eitc_total,
            'total_federal_credits': total_credits,

            # Effective rates (avoid division by zero)
            'hi_effective_rate': hi_net_revenue / total_income if total_income > 0 else 0.0,
            'avg_hi_tax_per_filer': hi_net_revenue / total_filers if total_filers > 0 else 0.0,
            'avg_eitc_per_recipient': (
                eitc_total / w[self.df.get('eitc_eligible', pd.Series(False, index=self.df.index))].sum()
                if w[self.df.get('eitc_eligible', pd.Series(False, index=self.df.index))].sum() > 0
                else 0.0
            ),
        }

    def by_filing_status(self) -> pd.DataFrame:
        """Revenue estimates broken down by filing status."""
        return self._aggregate_by('filing_status')

    def by_income_quintile(self) -> pd.DataFrame:
        """
        Revenue estimates broken down by income quintile.

        Quintiles are computed from the weighted income distribution.
        """
        df = self.df.copy()
        df['income_quintile'] = _weighted_quintile_labels(df['income'], df['weight'])
        return self._aggregate_by('income_quintile', df=df)

    def by_district(self, district_col: str = 'house_district') -> pd.DataFrame:
        """
        Revenue estimates broken down by geographic district.

        Args:
            district_col: Column name for the district ('house_district',
                          'senate_district', 'county', or 'PUMA').

        Returns:
            DataFrame with one row per district.
        """
        if district_col not in self.df.columns:
            raise ValueError(
                f"Column '{district_col}' not in DataFrame. "
                f"Available geographic columns: {[c for c in GEO_COLS if c in self.df.columns]}"
            )
        return self._aggregate_by(district_col)

    def by_county(self) -> pd.DataFrame:
        """Revenue estimates by Hawaii county."""
        return self.by_district('county')

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _add_derived_columns(self) -> None:
        """Add columns needed for aggregation if not already present."""
        # Ensure EITC columns exist even if EITC wasn't run
        for col, default in [('eitc_amount', 0.0), ('eitc_eligible', False),
                              ('ctc_total', 0.0), ('ctc_refundable', 0.0),
                              ('hi_tax_before_credits', 0.0), ('hi_tax_liability', 0.0),
                              ('hi_low_income_credit', 0.0)]:
            if col not in self.df.columns:
                self.df[col] = default
                logger.debug(f"Added missing column '{col}' with default {default}")

    def _aggregate_by(self, group_col: str, df: pd.DataFrame | None = None) -> pd.DataFrame:
        """
        Produce a weighted revenue summary grouped by a single column.

        Returns a DataFrame with one row per group and columns for:
        weighted filer count, income, Hawaii tax, credits, and effective rates.
        """
        if df is None:
            df = self.df

        rows = []
        for group_val, group_df in df.groupby(group_col, observed=True):
            w = group_df['weight']
            total_filers = w.sum()
            total_income = float((group_df['income'] * w).sum())
            hi_gross = float((group_df['hi_tax_before_credits'] * w).sum())
            hi_credit = float((group_df['hi_low_income_credit'] * w).sum())
            hi_net = float((group_df['hi_tax_liability'] * w).sum())
            ctc = float((group_df['ctc_total'] * w).sum())
            ctc_ref = float((group_df['ctc_refundable'] * w).sum())
            eitc = float((group_df['eitc_amount'] * w).sum())

            rows.append({
                group_col: group_val,
                'weighted_filers': total_filers,
                'total_income': total_income,
                'hi_gross_tax': hi_gross,
                'hi_low_income_credit': hi_credit,
                'hi_net_revenue': hi_net,
                'total_ctc': ctc,
                'total_ctc_refundable': ctc_ref,
                'total_eitc': eitc,
                'total_federal_credits': ctc + eitc,
                'hi_effective_rate': hi_net / total_income if total_income > 0 else 0.0,
            })

        result = pd.DataFrame(rows)
        if not result.empty:
            result = result.sort_values(group_col).reset_index(drop=True)
        return result

    @staticmethod
    def _validate(df: pd.DataFrame) -> None:
        missing = [c for c in REQUIRED_COLS if c not in df.columns]
        if missing:
            raise ValueError(
                f"RevenueEstimator requires columns {REQUIRED_COLS}. "
                f"Missing: {missing}"
            )


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------

def estimate_revenue(tax_units_df: pd.DataFrame) -> dict:
    """
    Run the full revenue estimation pipeline on a tax-unit DataFrame.

    Expects the DataFrame to already have CTC, EITC, and Hawaii tax columns
    (run calculate_ctc_for_tax_units, calculate_eitc_for_tax_units, and
    calculate_hawaii_tax_for_units before calling this).

    Returns:
        dict with keys:
            'state_summary'  — dict of statewide totals
            'by_filing_status' — DataFrame
            'by_income_quintile' — DataFrame
            'by_county' — DataFrame (if 'county' column present)
            'by_house_district' — DataFrame (if 'house_district' column present)
            'by_senate_district' — DataFrame (if 'senate_district' column present)
    """
    estimator = RevenueEstimator(tax_units_df)

    output = {
        'state_summary': estimator.state_summary(),
        'by_filing_status': estimator.by_filing_status(),
        'by_income_quintile': estimator.by_income_quintile(),
    }

    for col, key in [
        ('county',           'by_county'),
        ('house_district',   'by_house_district'),
        ('senate_district',  'by_senate_district'),
    ]:
        if col in tax_units_df.columns:
            output[key] = estimator.by_district(col)

    return output


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _weighted_quintile_labels(values: pd.Series, weights: pd.Series) -> pd.Series:
    """
    Assign weighted income quintile labels ('Q1' … 'Q5') to each row.

    Uses the weighted empirical CDF so quintiles represent equal shares of
    the population (not equal shares of rows).
    """
    # Sort by value, compute cumulative weight fraction
    order = values.argsort()
    sorted_weights = weights.iloc[order].values
    cum_weight = np.cumsum(sorted_weights)
    total_weight = cum_weight[-1]
    percentile = cum_weight / total_weight

    # Assign quintile (1–5) based on percentile
    quintile = np.searchsorted(np.array([0.2, 0.4, 0.6, 0.8, 1.0]), percentile) + 1
    quintile = np.clip(quintile, 1, 5)

    # Re-index back to original order
    labels = pd.Series(index=values.index, dtype='object')
    labels.iloc[order] = [f'Q{q}' for q in quintile]
    return labels
