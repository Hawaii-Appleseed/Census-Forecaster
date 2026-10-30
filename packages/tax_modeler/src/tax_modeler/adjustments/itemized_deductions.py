"""
Itemized Deductions Estimation for Hawaii

Parameters are loaded from config/deduction_params.json, which can be updated
without code changes when new SOI data becomes available.

Based on IRS SOI 2022 data:
- 12.1% of filers itemize (81,360 out of 674,660)
- Average itemized deduction: $34,592
- Total itemized deductions: $2.81B

Major federal categories we keep for Hawaii modeling:
- Home Mortgage Interest: $1.09B (81.9% of itemizers)
- Charitable Contributions: $502M (72.7% of itemizers)
- Medical & Dental: $339M (17.1% of itemizers)
- Real Estate Taxes: $254M (85.7% of itemizers)

Note: State income taxes (SALT) are excluded because Hawaii taxable
income should not deduct Hawaii income tax payments.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

# Locate config file relative to project root
_CONFIG_PATH = Path(__file__).parent.parent.parent.parent / "config" / "deduction_params.json"

# Module-level cache for loaded params
_PARAMS = None


def _load_params(config_path: Path = _CONFIG_PATH) -> dict:
    """Load deduction parameters from JSON config, with hardcoded fallbacks."""
    global _PARAMS
    if _PARAMS is not None:
        return _PARAMS

    # Hardcoded fallbacks (match original values if config file is missing)
    fallback = {
        "overall_itemization_rate": 0.121,
        "avg_itemized_deduction": 34592,
        "itemization_rates": {
            "under_50k": 0.05, "50k_to_100k": 0.12,
            "100k_to_200k": 0.20, "200k_plus": 0.30,
        },
        "avg_deductions": {
            "under_50k": 10800, "50k_to_100k": 15600,
            "100k_to_200k": 25200, "200k_plus": 36000,
        },
        "filing_status_multipliers": {
            "married_filing_jointly": 1.3, "qualifying_widow": 1.3,
            "head_of_household": 1.1, "single": 1.0,
            "married_filing_separately": 1.0,
        },
        "max_itemization_rate": 0.80,
        "homeownership": {
            "joint": {"base": 0.40, "slope": 0.40, "income_scale": 200000, "cap": 0.80},
            "hoh":   {"base": 0.30, "slope": 0.40, "income_scale": 200000, "cap": 0.70},
            "other": {"base": 0.20, "slope": 0.40, "income_scale": 200000, "cap": 0.60},
        },
        "mortgage_interest_tiers": {
            "tiers": [
                {"max_agi": 30000, "base_interest": 3000},
                {"max_agi": 50000, "base_interest": 6000},
                {"max_agi": 75000, "base_interest": 10000},
                {"max_agi": 100000, "base_interest": 15000},
                {"max_agi": 150000, "base_interest": 20000},
                {"max_agi": 200000, "base_interest": 25000},
                {"max_agi": 300000, "base_interest": 32000},
            ],
            "default_base_interest": 40000,
            "joint_multiplier": 1.3,
        },
        "charitable_giving": {
            "claim_rate": 0.727,
            "rates_by_agi": [
                {"max_agi": 75000, "rate": 0.02},
                {"max_agi": 150000, "rate": 0.03},
                {"max_agi": 300000, "rate": 0.04},
            ],
            "default_rate": 0.05,
        },
        "medical_expenses": {
            "agi_threshold": 0.075,
            "elderly_age": 65,
            "claim_rate_elderly": 0.30,
            "claim_rate_other": 0.10,
            "expense_pct_elderly": 0.15,
            "expense_pct_other": 0.10,
        },
        "real_estate_taxes": {
            "tiers": [
                {"max_agi": 75000, "amount": 3200},
                {"max_agi": 150000, "amount": 4800},
                {"max_agi": 300000, "amount": 7000},
            ],
            "default_amount": 9000,
        },
    }

    if config_path.exists():
        try:
            with open(config_path) as f:
                loaded = json.load(f)
            # Strip _note/_description keys
            params = {k: v for k, v in loaded.items() if not k.startswith("_")}
            # Strip nested _note keys
            for key, val in params.items():
                if isinstance(val, dict):
                    params[key] = {k: v for k, v in val.items() if not k.startswith("_")}
            logger.info(f"Loaded deduction params from {config_path}")
            _PARAMS = params
            return _PARAMS
        except Exception as e:
            logger.warning(f"Failed to load {config_path}: {e}. Using hardcoded fallbacks.")

    logger.info("Using hardcoded deduction parameter fallbacks")
    _PARAMS = fallback
    return _PARAMS


class ItemizedDeductionEstimator:
    """
    Estimate itemized deductions based on SOI patterns.

    Parameters are loaded from config/deduction_params.json at initialization.
    If the config file is missing, hardcoded fallbacks matching the original
    SOI 2022 values are used.
    """

    def __init__(self, config_path: Path | None = None):
        """Initialize with parameters from config or fallbacks."""
        params = _load_params(config_path or _CONFIG_PATH)

        self.overall_itemization_rate = params["overall_itemization_rate"]
        self.avg_itemized_deduction = params["avg_itemized_deduction"]
        self.ITEMIZATION_RATES = params["itemization_rates"]
        self.AVG_DEDUCTIONS = params["avg_deductions"]
        self.filing_status_multipliers = params["filing_status_multipliers"]
        self.max_itemization_rate = params["max_itemization_rate"]
        self.homeownership = params["homeownership"]
        self.mortgage_tiers = params["mortgage_interest_tiers"]
        self.charitable = params["charitable_giving"]
        self.medical = params["medical_expenses"]
        self.real_estate = params["real_estate_taxes"]

    def get_income_bracket(self, agi: float) -> str:
        """Determine income bracket for itemization estimation."""
        if agi < 50000:
            return 'under_50k'
        elif agi < 100000:
            return '50k_to_100k'
        elif agi < 200000:
            return '100k_to_200k'
        else:
            return '200k_plus'

    def will_itemize(self, agi: float, filing_status: str) -> bool:
        """
        Determine if filer will itemize based on income and filing status.

        Higher income filers are more likely to itemize.
        Joint filers itemize more than singles due to higher deduction thresholds.
        """
        bracket = self.get_income_bracket(agi)
        base_rate = self.ITEMIZATION_RATES[bracket]

        # Adjust by filing status
        multiplier = self.filing_status_multipliers.get(filing_status, 1.0)
        rate = base_rate * multiplier

        # Cap at reasonable maximum
        rate = min(rate, self.max_itemization_rate)

        # Random selection based on probability
        return np.random.random() < rate

    def estimate_mortgage_interest(self, agi: float, filing_status: str) -> float:
        """
        Estimate home mortgage interest deduction.

        Income-sensitive amounts that scale with AGI.
        Joint filers more likely to own homes and have larger mortgages.
        """
        # Homeownership probability by income and filing status
        if filing_status in ['married_filing_jointly', 'qualifying_widow']:
            ho = self.homeownership["joint"]
        elif filing_status == 'head_of_household':
            ho = self.homeownership["hoh"]
        else:
            ho = self.homeownership["other"]

        ownership_prob = min(ho["cap"], ho["base"] + (agi / ho["income_scale"]) * ho["slope"])

        if np.random.random() > ownership_prob:
            return 0

        # Income-sensitive mortgage interest from tiers
        base_interest = self.mortgage_tiers["default_base_interest"]
        for tier in self.mortgage_tiers["tiers"]:
            if agi < tier["max_agi"]:
                base_interest = tier["base_interest"]
                break

        # Filing status adjustment
        if filing_status in ['married_filing_jointly', 'qualifying_widow']:
            return base_interest * self.mortgage_tiers["joint_multiplier"]
        else:
            return base_interest

    def estimate_charitable_contributions(self, agi: float, filing_status: str) -> float:
        """
        Estimate charitable contribution deduction.

        From SOI: 72.7% of itemizers claim charitable, avg $8,488
        """
        # Charitable giving as % of income from tiers
        giving_rate = self.charitable["default_rate"]
        for tier in self.charitable["rates_by_agi"]:
            if agi < tier["max_agi"]:
                giving_rate = tier["rate"]
                break

        # Claim rate from config
        if np.random.random() < self.charitable["claim_rate"]:
            return agi * giving_rate
        return 0

    def estimate_medical_deduction(self, agi: float, age: int | None = None) -> float:
        """
        Estimate medical and dental expense deduction.

        From SOI: 17.1% of itemizers claim medical, avg $24,371
        Only expenses > 7.5% of AGI are deductible
        """
        med = self.medical
        is_elderly = age is not None and age >= med["elderly_age"]

        claim_rate = med["claim_rate_elderly"] if is_elderly else med["claim_rate_other"]

        if np.random.random() < claim_rate:
            threshold = agi * med["agi_threshold"]
            expense_pct = med["expense_pct_elderly"] if is_elderly else med["expense_pct_other"]
            medical_expenses = agi * expense_pct
            return max(0, medical_expenses - threshold)

        return 0

    def estimate_real_estate_taxes(self, agi: float, filing_status: str) -> float:
        """
        Estimate real estate tax deduction.

        From SOI: 85.7% of itemizers claim real estate taxes, avg $3,638
        Note: This is separate from SALT income taxes
        """
        amount = self.real_estate["default_amount"]
        for tier in self.real_estate["tiers"]:
            if agi < tier["max_agi"]:
                amount = tier["amount"]
                break
        return amount

    def apply_pease_limitation(self, total_itemized: float, agi: float,
                               filing_status: str) -> float:
        """
        Apply Hawaii's Pease limitation (HRS § 235-2.4(f)).

        Hawaii did not adopt the TCJA suspension of IRC § 68 (Pease), so the
        overall limitation on itemized deductions still applies using the AGI
        thresholds that were operative for federal tax year 2009 (not
        inflation-adjusted). Reduces itemized deductions by 3% of excess AGI
        above the threshold, capped at 80% of total itemized deductions.

        2009 thresholds (IRS Rev. Proc. 2008-66):
          - $166,800: single, MFJ, head of household, qualifying widow
          - $83,400: married filing separately
        """
        if filing_status == 'married_filing_separately':
            threshold = 83_400
        else:
            threshold = 166_800

        if agi <= threshold:
            return total_itemized

        reduction = 0.03 * (agi - threshold)
        max_reduction = 0.80 * total_itemized
        return total_itemized - min(reduction, max_reduction)

    def estimate_total_itemized_deductions(self, agi: float,
                                          filing_status: str,
                                          age: int | None = None) -> dict[str, float]:
        """
        Estimate total itemized deductions after Hawaii's Pease limitation.

        Categories modeled: mortgage interest, charitable contributions,
        medical expenses, real estate taxes. Hawaii has no mortgage interest
        dollar cap (did not adopt TCJA $750k limit) and no SALT cap (did not
        adopt TCJA $10k limit). Pease limitation applied per HRS § 235-2.4(f).

        Returns:
            Dictionary with individual deductions, pre-Pease total, and
            post-Pease total.
        """
        deductions = {
            'mortgage_interest': self.estimate_mortgage_interest(agi, filing_status),
            'charitable': self.estimate_charitable_contributions(agi, filing_status),
            'medical': self.estimate_medical_deduction(agi, age),
            'real_estate_taxes': self.estimate_real_estate_taxes(agi, filing_status),
        }

        pre_pease_total = sum(deductions.values())
        post_pease_total = self.apply_pease_limitation(pre_pease_total, agi, filing_status)

        deductions['pre_pease_total'] = pre_pease_total
        deductions['total'] = post_pease_total

        return deductions

    def get_deduction(self, agi: float, filing_status: str,
                     standard_deduction: float,
                     age: int | None = None) -> dict[str, float]:
        """
        Determine whether to use standard or itemized deduction.

        Args:
            agi: Adjusted Gross Income
            filing_status: Filing status
            standard_deduction: Standard deduction amount for this filer
            age: Age of primary filer

        Returns:
            Dictionary with deduction info
        """
        # Determine if filer will itemize
        will_itemize = self.will_itemize(agi, filing_status)

        if will_itemize:
            # Calculate itemized deductions (includes Pease limitation)
            itemized = self.estimate_total_itemized_deductions(agi, filing_status, age)

            # Only itemize if post-Pease total exceeds standard deduction
            if itemized['total'] > standard_deduction:
                return {
                    'type': 'itemized',
                    'amount': itemized['total'],
                    'standard_deduction': standard_deduction,
                    'benefit_over_standard': itemized['total'] - standard_deduction,
                    'details': itemized
                }

        # Use standard deduction
        return {
            'type': 'standard',
            'amount': standard_deduction,
            'standard_deduction': standard_deduction,
            'benefit_over_standard': 0,
            'details': {}
        }


def scale_deduction_params_for_target_year(
    target_year: int,
    geoid: str | None = None,
    *,
    state_config=None,
) -> dict:
    """Return deduction params with ``mortgage_interest_tiers`` scaled to ``target_year``.

    Loads the base params from config/deduction_params.json (or hardcoded
    fallbacks), then scales the mortgage interest tiers by the B25077
    (median home value) nominal growth factor from census_forecaster.

    Use this when constructing :class:`ItemizedDeductionEstimator` for a
    projected tax year rather than the 2022 SOI calibration year.

    Parameters
    ----------
    target_year:
        Tax year to project deduction parameters to.
    geoid:
        County FIPS for the B25077 lookup.  When omitted, falls back to
        ``state_config.default_geoid`` (Honolulu 15003 for Hawaii).
    state_config:
        State configuration object (see :class:`StateConfig`).  Defaults
        to :data:`HAWAII`.  Only consulted when ``geoid`` is None.

    Returns
    -------
    dict
        Deep copy of the loaded params dict with mortgage tiers scaled
        forward. Fallback to the original params if the B25077 forecast
        is unavailable.

    Example
    -------
    ::

        params = scale_deduction_params_for_target_year(2026, geoid="15001")
        # or, equivalently for the default county:
        params = scale_deduction_params_for_target_year(2026)
    """
    from tax_modeler.config.state_config import HAWAII
    from tax_modeler.projection.acs_supplement_forecast import scale_mortgage_deductions

    cfg = state_config if state_config is not None else HAWAII
    resolved_geoid = geoid if geoid is not None else cfg.default_geoid

    base_params = _load_params()
    return scale_mortgage_deductions(base_params, target_year, resolved_geoid)


def estimate_deduction(agi: float, filing_status: str,
                      standard_deduction: float,
                      age: int | None = None) -> dict[str, float]:
    """
    Convenience function to estimate deduction (standard or itemized).

    Args:
        agi: Adjusted Gross Income
        filing_status: Filing status
        standard_deduction: Standard deduction amount
        age: Age of primary filer

    Returns:
        Dictionary with deduction information
    """
    estimator = ItemizedDeductionEstimator()
    return estimator.get_deduction(agi, filing_status, standard_deduction, age)


if __name__ == '__main__':
    # Example usage
    estimator = ItemizedDeductionEstimator()

    # Test cases
    test_cases = [
        (40000, 'single', 4400, 30, "Low income single"),
        (85000, 'married_filing_jointly', 8800, 45, "Middle income joint"),
        (150000, 'married_filing_jointly', 8800, 50, "High income joint"),
        (300000, 'married_filing_jointly', 8800, 55, "Very high income joint"),
    ]

    print("Itemized Deduction Examples:")
    print("="*80)

    for agi, status, std_ded, age, desc in test_cases:
        print(f"\n{desc}")
        print(f"  AGI: ${agi:,} | Status: {status} | Std Deduction: ${std_ded:,}")

        # Run multiple times to show probability
        itemized_count = 0
        total_deduction = 0

        for _ in range(100):
            result = estimator.get_deduction(agi, status, std_ded, age)
            if result['type'] == 'itemized':
                itemized_count += 1
                total_deduction += result['amount']

        print(f"  Itemization Rate: {itemized_count}%")
        if itemized_count > 0:
            avg_itemized = total_deduction / itemized_count
            print(f"  Avg Itemized Deduction: ${avg_itemized:,.2f}")
            print(f"  Benefit over Standard: ${avg_itemized - std_ded:,.2f}")
