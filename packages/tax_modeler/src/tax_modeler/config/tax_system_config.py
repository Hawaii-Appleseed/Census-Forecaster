#!/usr/bin/env python3
"""
Centralized tax system configuration for Hawaii state income tax projections.
Handles loading brackets, deductions, exemptions, and tax calculations for any year/scenario.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple, Optional
import pandas as pd
import numpy as np
import logging

logger = logging.getLogger(__name__)


@dataclass
class TaxSystemConfig:
    """Configuration for a specific tax system (year, brackets, deductions, exemptions)."""
    
    name: str
    year: int  # Tax year (what year's income this applies to)
    bracket_year: int  # Which bracket schedule to use
    standard_deduction_year: int  # Which standard deduction schedule to use
    personal_exemption: float  # Personal exemption amount per person
    description: str = ""
    
    # Optional: bracket adjustments (for rollback scenarios)
    bracket_adjustments: Optional[Dict[str, List[Tuple[float, float]]]] = None
    # Format: {'filing_status': [(income_threshold, rate_adjustment_pp), ...]}

    # Optional: bracket scenario tag (matches 'scenario' column in bracket CSV)
    # Leave None to use baseline rows (scenario == '')
    bracket_scenario: Optional[str] = None

    # Optional: credit scenario tag (e.g. 'hb2306_hd1' for enhanced CDCC)
    # Leave None to use current-law credit calculations
    credit_scenario: Optional[str] = None

    # Optional: income surcharges
    surcharges: Optional[Dict[str, Dict[str, float]]] = None
    # Format: {'filing_status': {'threshold': 1000000, 'rate': 0.01}}


class TaxSystemRegistry:
    """Registry of pre-configured tax systems for common scenarios."""
    
    # Personal exemption amounts by year
    PERSONAL_EXEMPTIONS = {
        2018: 1144,  # 2017 law (applies to 2018 tax year)
        2022: 1144,
        2025: 1200,  # Indexed
        2026: 1200,
        2027: 1200,
    }
    
    @classmethod
    def get_2017_system(cls) -> TaxSystemConfig:
        """2017 tax law (pre-Act 46)."""
        return TaxSystemConfig(
            name="2017_system",
            year=2018,  # Applies to 2018 tax year
            bracket_year=2018,
            standard_deduction_year=2018,
            personal_exemption=cls.PERSONAL_EXEMPTIONS[2018],
            description="Pre-Act 46 baseline (2017 law)"
        )
    
    @classmethod
    def get_act46_2025_system(cls) -> TaxSystemConfig:
        """Act 46 tax system for 2025 tax year."""
        return TaxSystemConfig(
            name="act46_2025",
            year=2025,
            bracket_year=2025,  # Act 46 brackets (taxable years beginning after 12/31/2024)
            standard_deduction_year=2025,  # Act 46 standard deductions
            personal_exemption=cls.PERSONAL_EXEMPTIONS[2025],
            description="Act 46 (2024 law, applies to 2025+ tax years)"
        )
    
    @classmethod
    def get_act46_2027_system(cls) -> TaxSystemConfig:
        """Act 46 tax system for 2027 tax year.

        Act 46 (2024) includes a scheduled bracket phase-in for 2027+.
        Brackets expand (e.g., first bracket widens from 0-9,600 to 0-14,400) to match
        the increased standard deduction ($4,400/$8,800 in 2025 → $8,000/$16,000 in 2027).
        """
        return TaxSystemConfig(
            name="act46_2027",
            year=2027,
            bracket_year=2027,  # Act 46 2027 bracket schedule (includes phase-in)
            standard_deduction_year=2027,  # Act 46 scheduled phase-in: $8k single / $16k joint
            personal_exemption=cls.PERSONAL_EXEMPTIONS.get(2027, 1200),
            description="Act 46 (2024) — 2027 bracket phase-in and standard deduction phase-in"
        )
    
    @classmethod
    def get_millionaire_tax_2027(cls, surcharge_rate: float = 0.02) -> TaxSystemConfig:
        """Act 46 2027 + millionaire's tax surcharge on taxable income above $1M."""
        return TaxSystemConfig(
            name=f"millionaire_tax_2027_{surcharge_rate:.0%}",
            year=2027,
            bracket_year=2027,
            standard_deduction_year=2027,
            personal_exemption=cls.PERSONAL_EXEMPTIONS.get(2027, 1200),
            description=f"Act 46 2027 + {surcharge_rate:.0%} surcharge on taxable income > $1M (all statuses)",
            surcharges={
                'Joint_Surviving_Spouse': {'threshold': 1_000_000, 'rate': surcharge_rate},
                'Single_Married_Separate': {'threshold': 1_000_000, 'rate': surcharge_rate},
                'Head_of_Household': {'threshold': 1_000_000, 'rate': surcharge_rate},
            }
        )

    @classmethod
    def get_act46_system(cls, year: int) -> TaxSystemConfig:
        """Act 46 baseline for any tax year >= 2025.

        Selects the Act 46 bracket vintage that applies to ``year`` per the
        statutory phase-in schedule: 2025 brackets for TY 2025-2026, 2027
        brackets for TY 2027-2028, 2029 brackets for TY 2029+.
        Standard deductions follow the year-by-year schedule in
        hawaii_standard_deductions_by_year.csv.
        """
        if year < 2025:
            raise ValueError(f"get_act46_system requires year >= 2025, got {year}")
        if year < 2027:
            bracket_year = 2025
        elif year < 2029:
            bracket_year = 2027
        else:
            bracket_year = 2029
        return TaxSystemConfig(
            name=f"act46_{year}",
            year=year,
            bracket_year=bracket_year,
            standard_deduction_year=year,
            personal_exemption=cls.PERSONAL_EXEMPTIONS.get(year, 1200),
            description=f"Act 46 (2024) — TY {year} (bracket vintage {bracket_year})",
        )

    @classmethod
    def get_sb3125_cd1_system(cls, year: int) -> TaxSystemConfig:
        """SB 3125 CD1 (2026 conference draft) — overrides Act 46 brackets for TY 2027+.

        Bracket scenario tag ``sb3125_cd1`` selects the rows added to
        hawaii_tax_brackets_master_all.csv from the bill text:
          - year=2027 schedule applies TY 2027-2028 (bill: "after Dec 31, 2026")
          - year=2029 schedule applies TY 2029+      (bill: "after Dec 31, 2028")

        Standard deductions are NOT amended by the bill — they follow the
        Act 46 phase-in already in the deductions CSV.
        """
        if year < 2027:
            raise ValueError(f"SB 3125 CD1 brackets only apply to year >= 2027, got {year}")
        bracket_year = 2027 if year < 2029 else 2029
        return TaxSystemConfig(
            name=f"sb3125_cd1_{year}",
            year=year,
            bracket_year=bracket_year,
            standard_deduction_year=year,
            personal_exemption=cls.PERSONAL_EXEMPTIONS.get(year, 1200),
            description=f"SB 3125 CD1 — TY {year} (lower mid-bracket rates, new 13% top bracket)",
            bracket_scenario='sb3125_cd1',
        )

    @classmethod
    def get_sb3125_cd2_system(cls, year: int) -> TaxSystemConfig:
        """SB 3125 CD2 (2026 conference draft 2) — overrides Act 46 brackets for TY 2027+.

        Bracket schedule is identical to the sb3125_cd1 tag in the CSV (which was
        already loaded with CD2-vintage values):
          - Lower 2nd/3rd brackets: 2.50% and 5.00% (vs 3.20%/5.50% in Act 46 TY2027)
          - New 13% top bracket at $1,000,000+ MFJ / $750,000+ HoH / $500,000+ Single
          - year=2027 schedule applies TY 2027-2028 (bill: "after Dec 31, 2026")
          - year=2029 schedule applies TY 2029+      (bill: "after Dec 31, 2028")

        CD2 adds vs CD1:
          - AGI limits on REEC ($175K single / $350K MFJ) — already in credit overlay
          - REEC aggregate cap ($40M/yr 2027-2030, $0 from 2031) — already in overlay
          - CGEC sunset after Dec 31, 2027 — already in overlay
          - TCRA acceleration to Jan 1, 2029 — already in overlay
          - §235-110.51 (Tech Infrastructure Renovation Credit) repeal TY2028+ — ~$0
          - §235-110.9  (High Tech Business Investment Credit) repeal TY2029+ — ~$0

        Standard deductions follow Act 46 phase-in (bill does not amend deductions).
        """
        if year < 2027:
            raise ValueError(f"SB 3125 CD2 brackets only apply to year >= 2027, got {year}")
        bracket_year = 2027 if year < 2029 else 2029
        return TaxSystemConfig(
            name=f"sb3125_cd2_{year}",
            year=year,
            bracket_year=bracket_year,
            standard_deduction_year=year,
            personal_exemption=cls.PERSONAL_EXEMPTIONS.get(year, 1200),
            description=f"SB 3125 CD2 — TY {year} (lower mid-bracket rates, 13% at $1M+ MFJ)",
            bracket_scenario='sb3125_cd2',
        )

    @classmethod
    def get_sb3125_original_2027_system(cls) -> TaxSystemConfig:
        """SB3125 original proposal (2026 session, before SD1 amendments).

        Alternative to Act 46 with bracket expansion for TY2027+.
        Wider low/middle brackets compared to Act 46.
        Top brackets: $350k-$450k at 7.90%, etc.
        """
        return TaxSystemConfig(
            name="sb3125_original_2027",
            year=2027,
            bracket_year=2027,
            standard_deduction_year=2027,
            personal_exemption=cls.PERSONAL_EXEMPTIONS.get(2027, 1200),
            description="SB3125 original (2026) — bracket expansion for TY2027",
            bracket_scenario='',  # Use baseline (original SB3125) rows
        )

    @classmethod
    def get_sb3125_sd1_2027_system(cls) -> TaxSystemConfig:
        """SB3125 SD1 (2026 session) bracket schedule effective TY2027."""
        return cls.get_sb3125_sd1_system(2027)

    @classmethod
    def get_sb3125_sd1_system(cls, year: int) -> TaxSystemConfig:
        """SB 3125 SD1 (2026 senate draft) bracket schedule for any TY 2027+.

        Same SD1 bracket schedule (effective TY2027) carried forward for all
        later years — SD1 has no scheduled phase-in beyond 2027. Standard
        deductions and personal exemptions still follow the year-by-year
        schedule (the bill does not amend deductions).

        Changes vs Act 46 (TY2027 MFJ):
          - $350K-$450K: 7.9% → 8.25% (+0.35pp)
          - $450K-$550K: 8.25% → 9.0% (+0.75pp)
          - $550K-$650K: 9.0% → 10.0% (+1.0pp)
          - $650K-$800K: 10.0% → 11.0% (+1.0pp; collapsed into $650K+ at 11%)
          - $800K+: 11.0% → 11.0% (no change)
          - All brackets below $350K: no change

        Notably, SD1 has NO bottom-bracket tax cuts and NO 13% top bracket
        (those were added in CD1).
        """
        if year < 2027:
            raise ValueError(f"SB 3125 SD1 brackets only apply to year >= 2027, got {year}")
        return TaxSystemConfig(
            name=f"sb3125_sd1_{year}",
            year=year,
            bracket_year=2027,            # SD1 has no post-2027 phase-in
            standard_deduction_year=year,
            personal_exemption=cls.PERSONAL_EXEMPTIONS.get(year, 1200),
            description=f"SB 3125 SD1 — TY {year} (higher top bracket rates, no 13% bracket)",
            bracket_scenario='sb3125_sd1',
            credit_scenario='sb3125_sd1',
        )

    @classmethod
    def get_hb2306_orig_system(cls, year: int) -> TaxSystemConfig:
        """HB 2306 (original / introduced version) — Gov. Green's tax-cut freeze.

        Repeals the Act 46 (SLH 2024) future phase-ins for TY 2027-2031. Both
        bracket and standard-deduction schedules are FROZEN at the TY2026
        configuration (i.e. the rates and deductions in effect on 12/31/2026).
        No new rates, no surcharges, no CG changes. Pure freeze of scheduled
        tax cuts. CG alt-tax (§235-51(f)) unchanged.

        DOTAX fiscal note: ~$1.83B preserved revenue over FY2028-2032.

        For ``year >= 2027``, this returns:
          - bracket_year = 2025  (the bracket schedule in effect TY2026,
                                   since Act 46's first phase-in was TY2027)
          - standard_deduction_year = 2026  (frozen at TY2026 deduction amount)
        """
        if year < 2027:
            raise ValueError(f"HB 2306 original applies to year >= 2027, got {year}")
        return TaxSystemConfig(
            name=f"hb2306_orig_{year}",
            year=year,
            bracket_year=2025,             # frozen at TY2026 bracket schedule
            standard_deduction_year=2026,  # frozen at TY2026 standard deduction
            personal_exemption=cls.PERSONAL_EXEMPTIONS.get(2026, 1200),
            description=f"HB 2306 (original) — TY {year}: freezes Act 46 phase-ins at TY2026",
        )

    @classmethod
    def get_hb2306_hd1_system(cls, year: int) -> TaxSystemConfig:
        """HB2306 HD1 — any tax year from 2027 onward.

        HB 2306 HD1 repeals the scheduled 2027 and 2029 Act 46 bracket
        phase-ins, so the bracket schedule is frozen at the 2027 vintage
        for all years. Standard deductions still follow the Act 46
        year-by-year schedule (bill does not amend deductions).
        Enhanced CDCC (refundable, higher caps) applied via credit_scenario.
        """
        if year < 2027:
            raise ValueError(f"HB 2306 HD1 brackets only apply to year >= 2027, got {year}")
        return TaxSystemConfig(
            name=f"hb2306_hd1_{year}",
            year=year,
            bracket_year=2027,          # frozen — bill repeals 2027 and 2029 phase-ins
            standard_deduction_year=year,  # Act 46 standard-deduction schedule unchanged
            personal_exemption=cls.PERSONAL_EXEMPTIONS.get(year, 1200),
            description=f"HB 2306 HD1 — TY {year} (top 3 +1pp, enhanced CDCC, brackets frozen)",
            bracket_scenario='hb2306_hd1',
            credit_scenario='hb2306_hd1',
        )

    @classmethod
    def get_hb2306_hd1_2027_system(cls) -> TaxSystemConfig:
        """HB2306 HD1 (2025 session) bracket schedule effective TY2027.

        Changes vs Act 46 2025 baseline:
          - Keeps all 12 brackets from Act 46 2025 schedule (same boundaries)
          - Top 3 rates increase by 1pp: 9%→10%, 10%→11%, 11%→12%
          - Repeals previously scheduled 2027 and 2029 bracket phase-ins
        Also includes enhanced CDCC and sunset extension (Act 163 to 2032),
        modeled separately via credit_scenario.
        """
        return TaxSystemConfig(
            name="hb2306_hd1_2027",
            year=2027,
            bracket_year=2027,
            standard_deduction_year=2027,  # Same Act 46 phase-in as baseline
            personal_exemption=cls.PERSONAL_EXEMPTIONS.get(2027, 1200),
            description="HB2306 HD1 — top 3 rates +1pp, enhanced CDCC, sunset extension to 2032",
            bracket_scenario='hb2306_hd1',
            credit_scenario='hb2306_hd1',
        )

    @classmethod
    def get_act46_rollback_targeted(cls, base_year: int = 2025) -> TaxSystemConfig:
        """Act 46 with targeted rollback increases (+0.25pp, +0.5pp, +1.0pp on top 5 brackets)."""
        return TaxSystemConfig(
            name="act46_rollback_targeted",
            year=base_year,
            bracket_year=base_year,
            standard_deduction_year=base_year,
            personal_exemption=cls.PERSONAL_EXEMPTIONS.get(base_year, 1200),
            description="Act 46 with targeted rate increases (top 5 brackets)",
            bracket_adjustments={
                # Adjustments will be applied to top 5 brackets of each filing status
                'top_5_adjustments': [0.25, 0.25, 0.25, 0.50, 1.00]  # 5th, 4th, 3rd, 2nd, 1st highest
            }
        )


def itemized_deductions(tax_units: pd.DataFrame) -> np.ndarray | None:
    """Each unit's itemized deduction, or ``None`` when the frame has none.

    Scorers take ``max(standard deduction, itemized)``. A NaN here is an
    error, not a zero: it marks a unit whose income changed after its tax was
    computed. ``apply_top_income_growth_premium`` blanks the column on the
    rows it scales, precisely so a stale value cannot be used, and filling
    those blanks with 0 scored every filer above $500K on the standard
    deduction alone, overstating their tax and the Act 24 bracket gain.
    Recompute first with
    ``tax_modeler.projection.tax_unit_projector.refresh_stale_hawaii_tax``
    (projected frames) or ``tax_modeler.pipeline.compute_base_tax``.
    """
    if "hi_itemized_deduction" not in tax_units.columns:
        return None
    itemized = tax_units["hi_itemized_deduction"].to_numpy(dtype=float)
    missing = np.isnan(itemized)
    if missing.any():
        raise ValueError(
            f"hi_itemized_deduction is NaN on {int(missing.sum()):,} of "
            f"{len(itemized):,} tax units: their income changed after tax was "
            "computed (apply_top_income_growth_premium blanks the column on the "
            "rows it scales). Recompute with tax_unit_projector."
            "refresh_stale_hawaii_tax or pipeline.compute_base_tax before scoring."
        )
    return itemized


class TaxCalculator:
    """Centralized tax calculation engine."""
    
    def __init__(self, project_root: Optional[Path] = None):
        """Initialize calculator with data file paths."""
        if project_root is None:
            # Bundled data lives inside the package: tax_modeler/data/raw/...
            project_root = Path(__file__).parent.parent

        self.project_root = project_root
        self.brackets_path = project_root / "data/raw/hawaii_tax_brackets_master_all.csv"
        self.deductions_path = project_root / "data/raw/hawaii_standard_deductions_by_year.csv"
        
        # Load data
        self.all_brackets = pd.read_csv(self.brackets_path)
        self.all_deductions = pd.read_csv(self.deductions_path)
        
        logger.info(f"Loaded brackets for years: {sorted(self.all_brackets['year'].unique())}")
        logger.info(f"Loaded deductions for years: {sorted(self.all_deductions['Year'].unique())}")
    
    def get_brackets(self, year: int, filing_status: str,
                     scenario: Optional[str] = None) -> pd.DataFrame:
        """Get tax brackets for a specific year, filing status, and optional scenario.

        Args:
            scenario: If None, loads baseline rows (scenario column is blank/NaN).
                      If set (e.g. 'sb3125_sd1'), loads rows with that scenario tag.
        """
        status_map = {
            'single': 'Single_Married_Separate',
            'married_filing_jointly': 'Joint_Surviving_Spouse',
            'married_filing_separately': 'Single_Married_Separate',
            'head_of_household': 'Head_of_Household',
            'qualifying_widow': 'Joint_Surviving_Spouse'
        }

        mapped_status = status_map.get(filing_status, filing_status)

        df = self.all_brackets
        mask = (df['year'] == year) & (df['filing_status'] == mapped_status)

        if scenario:
            mask &= (df['scenario'].fillna('') == scenario)
        else:
            mask &= (df['scenario'].fillna('') == '')

        brackets = df[mask].copy()

        if brackets.empty:
            raise ValueError(f"No brackets found for year={year}, status={mapped_status}, "
                             f"scenario={scenario!r}")

        return brackets.sort_values('income_min').reset_index(drop=True)
    
    def get_standard_deduction(self, year: int, filing_status: str) -> float:
        """Get standard deduction for a specific year and filing status."""
        # Standardize filing status names
        status_map = {
            'single': 'Single_Married_Separate',
            'married_filing_jointly': 'Joint_Surviving_Spouse',
            'married_filing_separately': 'Single_Married_Separate',
            'head_of_household': 'Head_of_Household',
            'qualifying_widow': 'Joint_Surviving_Spouse'
        }
        
        mapped_status = status_map.get(filing_status, filing_status)
        
        deduction_row = self.all_deductions[self.all_deductions['Year'] == year]
        
        if deduction_row.empty:
            raise ValueError(f"No standard deduction found for year {year}")
        
        return float(deduction_row[mapped_status].iloc[0])
    
    def apply_bracket_adjustments(
        self, 
        brackets: pd.DataFrame, 
        adjustments: Dict[str, List[float]]
    ) -> pd.DataFrame:
        """Apply rate adjustments to brackets (for rollback scenarios)."""
        adjusted = brackets.copy()
        
        if 'top_5_adjustments' in adjustments:
            # Apply to top 5 brackets
            top_5_increases = adjustments['top_5_adjustments']
            
            # Get top 5 brackets by income_min
            top_5_indices = adjusted.nlargest(5, 'income_min').index
            
            for i, idx in enumerate(sorted(top_5_indices, reverse=True)):
                if i < len(top_5_increases):
                    adjusted.loc[idx, 'rate'] = adjusted.loc[idx, 'rate'] + top_5_increases[i] / 100
        
        return adjusted
    
    # HRS §235-16: net long-term capital gains taxed at no more than 7.25%
    HAWAII_CG_CAP_RATE: float = 0.0725

    def _bracket_tax(
        self,
        taxable_income: float,
        brackets: "pd.DataFrame",
    ) -> tuple:
        """Apply bracket schedule to taxable income; return (tax, marginal_rate)."""
        tax = 0.0
        marginal_rate = 0.0
        for _, bracket in brackets.iterrows():
            bracket_min = bracket['income_min']
            bracket_max = (bracket['income_max']
                           if pd.notna(bracket['income_max']) else float('inf'))
            rate = bracket['rate']
            if rate > 1:
                rate = rate / 100
            if taxable_income > bracket_min:
                tax += (min(taxable_income, bracket_max) - bracket_min) * rate
                marginal_rate = rate
                if taxable_income <= bracket_max:
                    break
        return tax, marginal_rate

    def calculate_tax(
        self,
        income: float,
        config: TaxSystemConfig,
        filing_status: str,
        num_exemptions: int = 1,
        deduction_override: Optional[float] = None,
        cg_income: float = 0.0,
    ) -> Dict[str, float]:
        """
        Calculate tax liability for a single tax unit.

        Args:
            income: Gross income (AGI), including capital gains.
            config: Tax system configuration.
            filing_status: Filing status.
            num_exemptions: Number of personal exemptions.
            deduction_override: If provided, use this instead of the standard deduction.
            cg_income: Net long-term capital gains included in ``income``.
                When non-zero, the Hawaii §235-16 7.25% cap is applied: the
                incremental bracket tax on the CG portion is limited to
                7.25% × cg_income.  Defaults to 0 (all income treated as
                ordinary — backward-compatible).

        Returns:
            Dict with tax calculation details, including ``cg_cap_savings``.
        """
        # Get standard deduction (or use override if provided)
        std_deduction = self.get_standard_deduction(config.standard_deduction_year, filing_status)
        if deduction_override is not None:
            std_deduction = deduction_override

        # Calculate personal exemptions
        personal_exemptions = num_exemptions * config.personal_exemption

        # Calculate taxable income (on full AGI including CG)
        taxable_income = max(0, income - std_deduction - personal_exemptions)

        # Get brackets (with optional scenario tag)
        brackets = self.get_brackets(config.bracket_year, filing_status,
                                     scenario=config.bracket_scenario)

        # Apply adjustments if specified
        if config.bracket_adjustments:
            brackets = self.apply_bracket_adjustments(brackets, config.bracket_adjustments)

        # Calculate bracket tax on full income
        tax, marginal_rate = self._bracket_tax(taxable_income, brackets)

        # ---- HRS §235-16 capital gains cap (7.25%) --------------------------
        cg_cap_savings = 0.0
        cg_income = max(0.0, float(cg_income))
        if cg_income > 0:
            # Tax on ordinary income only (total AGI minus CG share)
            ordinary_agi = max(0.0, income - cg_income)
            ordinary_taxable = max(0.0, ordinary_agi - std_deduction - personal_exemptions)
            ordinary_tax, _ = self._bracket_tax(ordinary_taxable, brackets)
            # Incremental bracket tax that would be charged on the CG portion
            cg_tax_uncapped = max(0.0, tax - ordinary_tax)
            # Cap: CG income taxed at no more than 7.25%
            cg_tax_capped = min(cg_tax_uncapped, cg_income * self.HAWAII_CG_CAP_RATE)
            cg_cap_savings = cg_tax_uncapped - cg_tax_capped
            tax = ordinary_tax + cg_tax_capped

        # Apply surcharges if configured
        surcharge_amount = 0.0
        if config.surcharges:
            status_map = {
                'single': 'Single_Married_Separate',
                'married_filing_jointly': 'Joint_Surviving_Spouse',
                'married_filing_separately': 'Single_Married_Separate',
                'head_of_household': 'Head_of_Household',
                'qualifying_widow': 'Joint_Surviving_Spouse'
            }
            status_keys = [filing_status, status_map.get(filing_status, filing_status)]
            for status_key in status_keys:
                if status_key in config.surcharges:
                    surcharge_config = config.surcharges[status_key]
                    threshold = surcharge_config['threshold']
                    rate = surcharge_config['rate']
                    if taxable_income > threshold:
                        surcharge = (taxable_income - threshold) * rate
                        surcharge_amount += surcharge
                        tax += surcharge
                        marginal_rate += rate
                    break

        return {
            'gross_income': income,
            'standard_deduction': std_deduction,
            'personal_exemptions': personal_exemptions,
            'taxable_income': taxable_income,
            'tax_liability': tax,
            'surcharge_amount': surcharge_amount,
            'cg_cap_savings': cg_cap_savings,
            'marginal_rate': marginal_rate * 100,
            'effective_rate': (tax / income * 100) if income > 0 else 0,
        }
    
    def _bracket_schedule(
        self,
        config: TaxSystemConfig,
        filing_status: str,
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Return (floors, rates_decimal, cum_tax_at_floor) for vectorized bracket math.

        ``cum_tax_at_floor[i]`` is the cumulative bracket tax owed at exactly
        ``floors[i]``. For taxable income ``t`` falling into bracket ``i``:
            tax(t) = cum_tax_at_floor[i] + (t - floors[i]) * rates[i]
        Brackets are assumed contiguous (each ``income_min`` equals the prior
        bracket's ``income_max``), which is true for the Hawaii master schedule.
        """
        brackets = self.get_brackets(
            config.bracket_year, filing_status, scenario=config.bracket_scenario
        )
        if config.bracket_adjustments:
            brackets = self.apply_bracket_adjustments(brackets, config.bracket_adjustments)

        floors = brackets['income_min'].to_numpy(dtype=float)
        rates_raw = brackets['rate'].to_numpy(dtype=float)
        rates = rates_raw / 100.0 if rates_raw.max() > 1.0 else rates_raw

        # cum_tax_at_floor[0] = 0; cum_tax_at_floor[i+1] = cum_tax_at_floor[i]
        # + (floors[i+1] - floors[i]) * rates[i]
        if len(floors) > 1:
            widths = np.diff(floors)  # widths[i] = floors[i+1] - floors[i]
            cum = np.concatenate(([0.0], np.cumsum(widths * rates[:-1])))
        else:
            cum = np.zeros_like(floors)
        return floors, rates, cum

    def _vectorized_bracket_tax(
        self,
        taxable_income: np.ndarray,
        statuses: np.ndarray,
        config: TaxSystemConfig,
    ) -> np.ndarray:
        """Vectorized bracket tax over an array of taxable incomes and statuses."""
        out = np.zeros(len(taxable_income), dtype=float)
        for fs in np.unique(statuses):
            floors, rates, cum = self._bracket_schedule(config, fs)
            mask = statuses == fs
            ti = taxable_income[mask]
            # searchsorted side='right' → idx s.t. floors[idx-1] <= ti < floors[idx]
            idx = np.searchsorted(floors, ti, side='right') - 1
            idx = np.clip(idx, 0, len(floors) - 1)
            out[mask] = cum[idx] + (ti - floors[idx]) * rates[idx]
        return out

    def unit_liabilities(
        self,
        tax_units: pd.DataFrame,
        config: TaxSystemConfig,
        filing_status_col: str = 'filing_status',
        income_col: str = 'income',
        num_exemptions_col: str = 'num_exemptions',
        num_dependents_col: str = 'num_dependents',
    ) -> dict[str, np.ndarray]:
        """Per-unit Hawaiʻi tax under ``config``: the one scoring kernel.

        Returns arrays aligned with ``tax_units``:

        * ``before_credits`` — bracket tax with the §235-16 capital-gains cap;
        * ``credits`` — refundable credits plus nonrefundable ones limited to
          ``before_credits`` (``HawaiiTaxCredits``);
        * ``child_care`` — the dependent-care credit within ``credits``;
        * ``net`` — ``before_credits - credits``, negative when refundable
          credits exceed the tax.

        :meth:`calculate_revenue_vectorized` sums these, and
        ``quintile_analysis.per_unit_tax`` returns ``net``, so the fiscal and
        distributional paths cannot score the same unit differently. They used
        to carry separate copies, and ``per_unit_tax``'s never subtracted
        credits (it read a key the credit calculator does not return).

        Exemptions come from ``num_exemptions_col`` when the frame has it, and
        otherwise from the statutory count (filer, spouse on a joint return,
        dependents). The deduction is ``max(standard, hi_itemized_deduction)``
        (see :func:`itemized_deductions`, which rejects NaN). Rows whose
        exemption count is NaN get no liability, and rows whose exemption or
        dependent count is NaN get no credits, as in :meth:`calculate_revenue`.
        Surcharges are not applied (no registered config in the forecast path
        uses them); :meth:`calculate_tax` still does.
        """
        from tax_modeler.adjustments.hawaii_credits import HawaiiTaxCredits
        from tax_modeler.liability.hawaii import statutory_exemption_counts

        n = len(tax_units)
        incomes = tax_units[income_col].to_numpy(dtype=float)
        statuses = tax_units[filing_status_col].to_numpy()

        # Preserve NaN handling consistent with the loop version: rows whose
        # exemptions/dependents are NaN should end up with zero liability and
        # zero credits (the loop's per-row try/except achieves this by
        # raising on int(NaN)). We track NaN masks and zero those rows out.
        if num_dependents_col in tax_units.columns:
            raw_dep = tax_units[num_dependents_col].to_numpy(dtype=float)
        else:
            raw_dep = np.zeros(n, dtype=float)
        nan_dep = np.isnan(raw_dep)
        num_dependents = np.where(nan_dep, 0, raw_dep).astype(int)

        if num_exemptions_col in tax_units.columns:
            raw_exemp = tax_units[num_exemptions_col].to_numpy(dtype=float)
        else:
            raw_exemp = statutory_exemption_counts(statuses, raw_dep)
        nan_exemp = np.isnan(raw_exemp)
        num_exemptions = np.where(nan_exemp, 1.0, raw_exemp)

        # Per-filer effective deduction: max(filing-status SD, hi_itemized_deduction).
        # Column-name override was removed — always use the standard logic so callers
        # cannot silently pass a wrong column.
        sd_per_filer = np.empty(n, dtype=float)
        for fs in np.unique(statuses):
            sd_per_filer[statuses == fs] = self.get_standard_deduction(
                config.standard_deduction_year, fs
            )
        itemized = itemized_deductions(tax_units)
        deductions = sd_per_filer if itemized is None else np.maximum(sd_per_filer, itemized)

        exemption_amount = num_exemptions * config.personal_exemption
        taxable_full = np.maximum(0.0, incomes - deductions - exemption_amount)

        # CG income for §235-16 cap: auto-detect synthetic_cg_share column.
        cg_shares = (
            tax_units["synthetic_cg_share"].fillna(0.0).to_numpy(dtype=float)
            if "synthetic_cg_share" in tax_units.columns
            else np.zeros(n, dtype=float)
        )
        cg_income = np.maximum(0.0, incomes * cg_shares)
        has_cg = cg_income > 0

        # Ordinary taxable = max(0, ordinary_agi - ded - exemption)
        ordinary_agi = np.maximum(0.0, incomes - cg_income)
        taxable_ordinary = np.maximum(0.0, ordinary_agi - deductions - exemption_amount)

        # Bracket tax (vectorized): full and ordinary
        tax_full = self._vectorized_bracket_tax(taxable_full, statuses, config)
        # Skip the ordinary pass when no CG present anywhere
        if has_cg.any():
            tax_ordinary = self._vectorized_bracket_tax(taxable_ordinary, statuses, config)
            cg_tax_uncapped = np.maximum(0.0, tax_full - tax_ordinary)
            cg_tax_capped = np.minimum(cg_tax_uncapped, cg_income * self.HAWAII_CG_CAP_RATE)
            liabilities = np.where(has_cg, tax_ordinary + cg_tax_capped, tax_full)
        else:
            liabilities = tax_full

        # Match the loop version's NaN-row drop: zero out liability for any
        # row whose exemption was NaN (loop's int(NaN) → ValueError → except).
        liabilities[nan_exemp] = 0.0

        # Credits — per-filer Python loop (HawaiiTaxCredits is not vectorized).
        # Profile shows credits are ~10-15% of original runtime; bracket math
        # was the dominant cost. Skip vectorization here for now. Skip rows
        # whose exemptions or dependents were NaN to match the loop version's
        # silent-drop behavior.
        credit_calc = HawaiiTaxCredits(year=config.year)
        total_credits = np.zeros(n, dtype=float)
        total_cdcc = np.zeros(n, dtype=float)
        skip = nan_exemp | nan_dep
        for i in range(n):
            if skip[i]:
                continue
            try:
                cr = credit_calc.calculate_total_credits(
                    agi=float(incomes[i]),
                    filing_status=str(statuses[i]),
                    num_dependents=int(num_dependents[i]),
                    tax_before_credits=float(liabilities[i]),
                    credit_scenario=config.credit_scenario,
                )
                total_credits[i] = cr['total']
                total_cdcc[i] = cr['child_care']
            except Exception:
                pass

        return {
            'before_credits': liabilities,
            'credits': total_credits,
            'child_care': total_cdcc,
            'net': liabilities - total_credits,
        }

    def calculate_revenue_vectorized(
        self,
        tax_units: pd.DataFrame,
        config: TaxSystemConfig,
        filing_status_col: str = 'filing_status',
        income_col: str = 'income',
        weight_col: str = 'weight',
        num_exemptions_col: str = 'num_exemptions',
        num_dependents_col: str = 'num_dependents',
    ) -> Dict[str, float]:
        """Vectorized counterpart of :meth:`calculate_revenue` (~30-50× faster).

        Weighted totals of :meth:`unit_liabilities`, which computes per-filer
        tax in two numpy passes (full taxable + ordinary taxable for the
        §235-16 CG cap) and then runs the credit calculation in a per-filer
        Python loop. The per-filer tax loop previously dominated runtime;
        eliminating it cuts the hot path by roughly an order of magnitude.
        Surcharge handling is omitted — none of the registered configs use
        ``surcharges`` in the SB 3125 forecast path, and ``calculate_tax``
        retains the original surcharge code for backward compatibility.
        """
        units = self.unit_liabilities(
            tax_units, config,
            filing_status_col=filing_status_col,
            income_col=income_col,
            num_exemptions_col=num_exemptions_col,
            num_dependents_col=num_dependents_col,
        )
        liabilities = units['before_credits']
        total_credits = units['credits']
        total_cdcc = units['child_care']
        net_liabilities = units['net']
        incomes = tax_units[income_col].to_numpy(dtype=float)
        weights = tax_units[weight_col].to_numpy(dtype=float)

        total_revenue_before = float(np.sum(liabilities * weights)) / 1e6
        total_credits_m = float(np.sum(total_credits * weights)) / 1e6
        total_cdcc_m = float(np.sum(total_cdcc * weights)) / 1e6
        total_revenue = float(np.sum(net_liabilities * weights)) / 1e6
        avg_tax = float(np.average(net_liabilities, weights=weights))
        avg_income = float(np.average(incomes, weights=weights))

        return {
            'total_revenue_millions': total_revenue,
            'total_revenue_before_credits_millions': total_revenue_before,
            'total_credits_millions': total_credits_m,
            'total_cdcc_millions': total_cdcc_m,
            'average_tax_per_filer': avg_tax,
            'average_income': avg_income,
            'effective_rate': (avg_tax / avg_income * 100) if avg_income > 0 else 0,
            'total_filers': float(weights.sum())
        }

    def calculate_revenue(
        self,
        tax_units: pd.DataFrame,
        config: TaxSystemConfig,
        filing_status_col: str = 'filing_status',
        income_col: str = 'income',
        weight_col: str = 'weight',
        num_exemptions_col: str = 'num_exemptions',
        num_dependents_col: str = 'num_dependents',
    ) -> Dict[str, float]:
        """
        Calculate total revenue for a dataframe of tax units.

        Args:
            tax_units: DataFrame with tax units
            config: Tax system configuration
            filing_status_col: Column name for filing status
            income_col: Column name for income
            weight_col: Column name for weights
            num_exemptions_col: Column name for number of exemptions
            num_dependents_col: Column name for number of dependents

        Returns:
            Dict with revenue statistics (includes credit breakdown)
        """
        from tax_modeler.adjustments.hawaii_credits import HawaiiTaxCredits
        from tax_modeler.liability.hawaii import statutory_exemption_counts

        liabilities = []
        weights = tax_units[weight_col].values

        # Set default dependents if column doesn't exist
        if num_dependents_col not in tax_units.columns:
            num_dependents = np.zeros(len(tax_units))
        else:
            num_dependents = tax_units[num_dependents_col].values

        # Without an exemptions column, count them as the statute does
        # (filer, spouse on a joint return, dependents), not one per return.
        if num_exemptions_col not in tax_units.columns:
            num_exemptions = statutory_exemption_counts(
                tax_units[filing_status_col].to_numpy(), num_dependents,
            )
        else:
            num_exemptions = tax_units[num_exemptions_col].values

        # Per-filer effective deduction: max(filing-status SD, hi_itemized_deduction).
        # Column-name override was removed to eliminate the silent wrong-column pattern.
        _n = len(tax_units)
        _statuses = tax_units[filing_status_col].to_numpy()
        _sd = np.empty(_n, dtype=float)
        for _fs in np.unique(_statuses):
            _sd[_statuses == _fs] = self.get_standard_deduction(
                config.standard_deduction_year, _fs
            )
        _itemized = itemized_deductions(tax_units)
        deductions = _sd if _itemized is None else np.maximum(_sd, _itemized)

        # CG income for §235-16 cap: auto-detect synthetic_cg_share column.
        # Base PUMS units don't have it → 0 (cap not applied).
        cg_shares = (
            tax_units["synthetic_cg_share"].fillna(0.0).values
            if "synthetic_cg_share" in tax_units.columns
            else np.zeros(len(tax_units))
        )

        for i, (income, status, num_ex) in enumerate(zip(
            tax_units[income_col],
            tax_units[filing_status_col],
            num_exemptions
        )):
            try:
                ded = float(deductions[i])
                cg_inc = float(income) * float(cg_shares[i])
                result = self.calculate_tax(
                    income, config, status, int(num_ex),
                    deduction_override=ded, cg_income=cg_inc,
                )
                liabilities.append(result['tax_liability'])
            except Exception as e:
                logger.warning(f"Error calculating tax for income ${income:,.0f}, status {status}: {e}")
                liabilities.append(0)

        liabilities = np.array(liabilities)

        # Calculate credits
        credit_calc = HawaiiTaxCredits(year=config.year)
        total_credits = np.zeros(len(tax_units))
        total_cdcc = np.zeros(len(tax_units))

        for i, (income, status, tax_li, n_dep) in enumerate(zip(
            tax_units[income_col],
            tax_units[filing_status_col],
            liabilities,
            num_dependents
        )):
            try:
                cr = credit_calc.calculate_total_credits(
                    agi=income, filing_status=status,
                    num_dependents=int(n_dep),
                    tax_before_credits=tax_li,
                    credit_scenario=config.credit_scenario,
                )
                total_credits[i] = cr['total']
                total_cdcc[i] = cr['child_care']
            except Exception:
                pass

        net_liabilities = liabilities - total_credits

        total_revenue_before = float(np.sum(liabilities * weights)) / 1e6
        total_credits_m = float(np.sum(total_credits * weights)) / 1e6
        total_cdcc_m = float(np.sum(total_cdcc * weights)) / 1e6
        total_revenue = float(np.sum(net_liabilities * weights)) / 1e6
        avg_tax = float(np.average(net_liabilities, weights=weights))
        avg_income = float(np.average(tax_units[income_col], weights=weights))

        return {
            'total_revenue_millions': total_revenue,
            'total_revenue_before_credits_millions': total_revenue_before,
            'total_credits_millions': total_credits_m,
            'total_cdcc_millions': total_cdcc_m,
            'average_tax_per_filer': avg_tax,
            'average_income': avg_income,
            'effective_rate': (avg_tax / avg_income * 100) if avg_income > 0 else 0,
            'total_filers': float(weights.sum())
        }


def compare_systems(
    tax_units: pd.DataFrame,
    baseline_config: TaxSystemConfig,
    scenario_config: TaxSystemConfig,
    calculator: Optional[TaxCalculator] = None,
) -> pd.DataFrame:
    """
    Compare two tax systems and return detailed comparison.

    Effective deduction per filer is automatically computed as
    ``max(filing-status standard deduction, hi_itemized_deduction)``
    when the ``hi_itemized_deduction`` column is present; otherwise the
    filing-status standard deduction is used. The former
    ``deduction_col`` parameter has been removed to eliminate the
    silent wrong-column anti-pattern.

    Args:
        tax_units: DataFrame with tax units
        baseline_config: Baseline tax system
        scenario_config: Scenario tax system to compare
        calculator: Optional pre-initialized calculator

    Returns:
        DataFrame with comparison results
    """
    if calculator is None:
        calculator = TaxCalculator()

    # Vectorized path is ~250× faster on the bracket math (the dominant cost).
    # The legacy ``calculate_revenue`` (per-filer Python loop) remains
    # available on TaxCalculator for backwards compatibility.
    baseline_revenue = calculator.calculate_revenue_vectorized(
        tax_units, baseline_config,
    )
    scenario_revenue = calculator.calculate_revenue_vectorized(
        tax_units, scenario_config,
    )
    
    def _row(rev, cfg):
        return {
            'system': cfg.name,
            'description': cfg.description,
            'revenue_millions': rev['total_revenue_millions'],
            'revenue_before_credits_millions': rev['total_revenue_before_credits_millions'],
            'total_credits_millions': rev['total_credits_millions'],
            'total_cdcc_millions': rev['total_cdcc_millions'],
            'avg_tax': rev['average_tax_per_filer'],
            'effective_rate': rev['effective_rate'],
        }

    results = [_row(baseline_revenue, baseline_config),
               _row(scenario_revenue, scenario_config)]

    # Add comparison row
    revenue_diff = scenario_revenue['total_revenue_millions'] - baseline_revenue['total_revenue_millions']

    results.append({
        'system': 'Difference',
        'description': f"{scenario_config.name} vs {baseline_config.name}",
        'revenue_millions': revenue_diff,
        'revenue_before_credits_millions': (scenario_revenue['total_revenue_before_credits_millions']
                                            - baseline_revenue['total_revenue_before_credits_millions']),
        'total_credits_millions': (scenario_revenue['total_credits_millions']
                                   - baseline_revenue['total_credits_millions']),
        'total_cdcc_millions': (scenario_revenue['total_cdcc_millions']
                                - baseline_revenue['total_cdcc_millions']),
        'avg_tax': scenario_revenue['average_tax_per_filer'] - baseline_revenue['average_tax_per_filer'],
        'effective_rate': scenario_revenue['effective_rate'] - baseline_revenue['effective_rate'],
    })

    return pd.DataFrame(results)
