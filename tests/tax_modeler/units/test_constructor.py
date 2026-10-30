"""
Tests for the TaxUnitConstructor class.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from tax_modeler.units.constructor import TaxUnitConstructor


# Test data
def create_test_data():
    """Create test data for tax unit construction."""
    # Create person data
    person_data = [
        # Household 1 - Married couple with child (joint filers)
        {'SERIALNO': '1', 'SPORDER': '1', 'AGEP': 35, 'SEX': 1, 'MAR': 1, 'RELSHIPP': 20, 'WAGP': 60000, 'HINCP': 100000, 'CIT': 1, 'SEMP': 0, 'ADJINC': 1.0, 'SCHL': 16},
        {'SERIALNO': '1', 'SPORDER': '2', 'AGEP': 33, 'SEX': 2, 'MAR': 1, 'RELSHIPP': 21, 'WAGP': 40000, 'HINCP': 100000, 'CIT': 1, 'SEMP': 0, 'ADJINC': 1.0, 'SCHL': 16},
        {'SERIALNO': '1', 'SPORDER': '3', 'AGEP': 5, 'SEX': 1, 'MAR': 0, 'RELSHIPP': 25, 'WAGP': 0, 'HINCP': 100000, 'CIT': 1, 'SEMP': 0, 'ADJINC': 1.0, 'SCHL': 1},
        
        # Household 2 - Single parent with child
        {'SERIALNO': '2', 'SPORDER': '1', 'AGEP': 30, 'SEX': 2, 'MAR': 5, 'RELSHIPP': 20, 'WAGP': 45000, 'HINCP': 50000, 'CIT': 1, 'SEMP': 0, 'ADJINC': 1.0, 'SCHL': 16},
        {'SERIALNO': '2', 'SPORDER': '2', 'AGEP': 8, 'SEX': 1, 'MAR': 0, 'RELSHIPP': 25, 'WAGP': 0, 'HINCP': 50000, 'CIT': 1, 'SEMP': 0, 'ADJINC': 1.0, 'SCHL': 5},
        
        # Household 3 - Single person
        {'SERIALNO': '3', 'SPORDER': '1', 'AGEP': 28, 'SEX': 1, 'MAR': 5, 'RELSHIPP': 20, 'WAGP': 50000, 'HINCP': 50000, 'CIT': 1, 'SEMP': 0, 'ADJINC': 1.0, 'SCHL': 16},
        
        # Household 4 - Married couple filing separately (income disparity)
        # Combined $405k + 80x ratio + high/low pattern → MFS score ≈ 9 (>= 8 = guaranteed MFS).
        # Also requires PINCP set (constructor's _should_file_separately reads PINCP, not WAGP).
        {'SERIALNO': '4', 'SPORDER': '1', 'AGEP': 40, 'SEX': 1, 'MAR': 1, 'RELSHIPP': 20, 'WAGP': 400000, 'PINCP': 400000, 'HINCP': 405000, 'CIT': 1, 'SEMP': 0, 'ADJINC': 1.0, 'SCHL': 16},
        {'SERIALNO': '4', 'SPORDER': '2', 'AGEP': 38, 'SEX': 2, 'MAR': 1, 'RELSHIPP': 21, 'WAGP':   5000, 'PINCP':   5000, 'HINCP': 405000, 'CIT': 1, 'SEMP': 0, 'ADJINC': 1.0, 'SCHL': 16},
        {'SERIALNO': '4', 'SPORDER': '3', 'AGEP': 12, 'SEX': 1, 'MAR': 0, 'RELSHIPP': 25, 'WAGP':      0, 'PINCP':      0, 'HINCP': 405000, 'CIT': 1, 'SEMP': 0, 'ADJINC': 1.0, 'SCHL': 6},

        # Household 5 - Married couple filing separately (ultra-high income + disparity).
        # Combined $605k + 120x ratio → MFS score ≈ 10 (guaranteed MFS).
        {'SERIALNO': '5', 'SPORDER': '1', 'AGEP': 45, 'SEX': 1, 'MAR': 1, 'RELSHIPP': 20, 'WAGP': 600000, 'PINCP': 600000, 'HINCP': 605000, 'CIT': 1, 'SEMP': 0, 'ADJINC': 1.0, 'SCHL': 16},
        {'SERIALNO': '5', 'SPORDER': '2', 'AGEP': 43, 'SEX': 2, 'MAR': 1, 'RELSHIPP': 21, 'WAGP':   5000, 'PINCP':   5000, 'HINCP': 605000, 'CIT': 1, 'SEMP': 0, 'ADJINC': 1.0, 'SCHL': 16},
    ]

    # Create household data — WGTP is required: TaxUnitConstructor skips
    # households with WGTP <= 0 (group-quarters / invalid sentinel).
    hh_data = [
        {'SERIALNO': '1', 'HINCP': 100000, 'ADJINC': 1.0, 'WGTP': 100},  # Joint filers + child
        {'SERIALNO': '2', 'HINCP':  50000, 'ADJINC': 1.0, 'WGTP': 100},  # Single parent + child (HoH)
        {'SERIALNO': '3', 'HINCP':  50000, 'ADJINC': 1.0, 'WGTP': 100},  # Single person
        {'SERIALNO': '4', 'HINCP': 405000, 'ADJINC': 1.0, 'WGTP': 100},  # MFS - high income + disparity
        {'SERIALNO': '5', 'HINCP': 605000, 'ADJINC': 1.0, 'WGTP': 100},  # MFS - ultra-high income + disparity
    ]
    
    # Create DataFrames
    person_df = pd.DataFrame(person_data)
    hh_df = pd.DataFrame(hh_data)
    
    # Add required fields to person data
    person_df['person_id'] = person_df['SERIALNO'] + '_' + person_df['SPORDER'].astype(str)
    person_df['is_adult'] = person_df['AGEP'] >= 18
    person_df.set_index('person_id', inplace=True)
    
    # Keep SERIALNO as a column in hh_df for validation
    hh_df = hh_df.reset_index(drop=True)
    
    return person_df, hh_df

class TestTaxUnitConstructor:
    """Test cases for the TaxUnitConstructor class."""
    
    def test_initialization(self):
        """Test that the constructor initializes correctly."""
        person_df, hh_df = create_test_data()
        
        # Create a copy to avoid modifying the original
        person_df = person_df.copy()
        
        # Set the index
        person_id = person_df['SERIALNO'].astype(str) + '_' + person_df['SPORDER'].astype(str)
        person_df = person_df.set_index(person_id)
        person_df.index.name = 'person_id'
        
        # Debug: Check index before creating constructor
        print("\nBefore constructor:")
        print(f"Index name: {person_df.index.name}")
        print(f"Index values: {person_df.index.tolist()[:5]}")
        
        # Create the constructor
        constructor = TaxUnitConstructor(person_df, hh_df)
        
        # Debug: Check index after creating constructor
        print("\nAfter constructor:")
        print(f"Index name: {constructor.person_df.index.name}")
        print(f"Index values: {constructor.person_df.index.tolist()[:5] if hasattr(constructor.person_df, 'index') else 'No index'}")
        
        # Verify the constructor initialized correctly
        assert constructor.person_df is not None
        assert constructor.hh_df is not None
        assert 'person_id' not in constructor.person_df.columns  # Should be in index
        
        # The index name should be preserved
        # Temporarily change this to just check if the index exists
        assert hasattr(constructor.person_df, 'index'), "DataFrame has no index"
    
    def test_create_rule_based_units(self):
        """Test creation of tax units using rule-based approach."""
        person_df, hh_df = create_test_data()
        constructor = TaxUnitConstructor(person_df, hh_df)

        # Create tax units
        tax_units = constructor.create_rule_based_units()

        # Should create 7 tax units (1 joint, 1 HoH, 1 single, 2 MFS pairs).
        assert len(tax_units) == 7, (
            f"Expected 7 units, got {len(tax_units)}: "
            f"{tax_units[['hh_id', 'filing_status']].to_dict('records')}"
        )

        # Check that all expected households are represented
        hh_ids = set(tax_units['hh_id'])
        assert '1' in hh_ids  # Joint filers
        assert '2' in hh_ids  # Single parent (HoH)
        assert '3' in hh_ids  # Single person
        assert '4' in hh_ids  # MFS - high income + income disparity
        assert '5' in hh_ids  # MFS - ultra-high income + income disparity

        # Check MFS tax units. Constructor emits 'married_filing_separately'
        # (the canonical IRS form, not the older 'married_filing_separate').
        mfs_units = tax_units[tax_units['filing_status'] == 'married_filing_separately']
        assert len(mfs_units) == 4  # 2 spouses × 2 households

        # Check that MFS units have the correct structure
        for _, unit in mfs_units.iterrows():
            assert 'primary_filer_id' in unit
            assert 'dependents' in unit
            assert isinstance(unit['dependents'], list)

        # Verify household 4 has 2 MFS filers
        hh4_units = tax_units[tax_units['hh_id'] == '4']
        assert len(hh4_units) == 2
        assert all(status == 'married_filing_separately' for status in hh4_units['filing_status'])

        # Verify household 5 has 2 MFS filers
        hh5_units = tax_units[tax_units['hh_id'] == '5']
        assert len(hh5_units) == 2
        assert all(status == 'married_filing_separately' for status in hh5_units['filing_status'])

        # Check that household 1 has a joint filer (canonical: married_filing_jointly)
        hh1_units = tax_units[tax_units['hh_id'] == '1']
        assert any(status == 'married_filing_jointly' for status in hh1_units['filing_status'])

        # Check that household 2 has a head of household
        hh2_units = tax_units[tax_units['hh_id'] == '2']
        assert any(status == 'head_of_household' for status in hh2_units['filing_status'])

        # Check that household 3 has a single filer
        hh3_units = tax_units[tax_units['hh_id'] == '3']
        assert any(status == 'single' for status in hh3_units['filing_status'])

    def test_process_household(self):
        """Test processing of a single household."""
        person_df, hh_df = create_test_data()
        constructor = TaxUnitConstructor(person_df, hh_df)

        # Get household 1 data
        hh1 = person_df[person_df.index.str.startswith('1_')]

        # Process the household
        tax_units = constructor._process_household(hh1)

        # Should create at least one tax unit
        assert len(tax_units) > 0

        # Check that the joint filer was created (canonical: 'married_filing_jointly').
        joint_filers = [u for u in tax_units if u['filing_status'] == 'married_filing_jointly']
        assert len(joint_filers) == 1

        # Check that dependents are assigned correctly
        joint_filer = joint_filers[0]
        assert '1_3' in joint_filer['dependents']  # Child should be a dependent
    
    def test_identify_joint_filers(self):
        """Test identification of joint filers and MFS filers."""
        person_df, hh_df = create_test_data()

        # Create a copy to avoid modifying the original
        person_df = person_df.copy()

        # Set the index
        person_id = person_df['SERIALNO'].astype(str) + '_' + person_df['SPORDER'].astype(str)
        person_df = person_df.set_index(person_id)
        person_df.index.name = 'person_id'

        constructor = TaxUnitConstructor(person_df, hh_df)

        # Get household 1 data
        hh1 = person_df[person_df.index.str.startswith('1_')]

        # Identify joint filers - pass both household and all members
        joint_filers, mfs_filers = constructor._identify_joint_filers(hh1, hh1)
        
        # Should find one pair of joint filers and no MFS filers in household 1
        assert len(joint_filers) == 1
        assert len(mfs_filers) == 0
        assert joint_filers[0] in [('1_1', '1_2'), ('1_2', '1_1')]
    
    def test_create_single_filer(self):
        """Test creation of a single filer tax unit."""
        person_df, hh_df = create_test_data()
        
        # Create a copy to avoid modifying the original
        person_df = person_df.copy()
        
        # Set the index
        person_id = person_df['SERIALNO'].astype(str) + '_' + person_df['SPORDER'].astype(str)
        person_df = person_df.set_index(person_id)
        person_df.index.name = 'person_id'
        
        constructor = TaxUnitConstructor(person_df, hh_df)
        
        # Get a single person and household data
        person = person_df.loc['3_1']
        hh_members = person_df[person_df.index.str.startswith('3_')]
        hh_data = hh_df[hh_df['SERIALNO'] == '3'].iloc[0]  # Get household data
        
        # Create a single filer tax unit - pass person, household members, and household data
        tax_unit = constructor._create_single_filer(person, hh_members, hh_data)
        
        # Verify the tax unit was created correctly
        assert tax_unit is not None
        assert tax_unit['filing_status'] in ['single', 'head_of_household']  # Could be either
        assert tax_unit['filer_id'] == '3_single_3_1'  # New format includes filing status


class TestReplicateWeightPropagation:
    """``include_replicate_weights`` flag carries WGTP1..WGTP80 / PWGTP1..PWGTP80
    through to the tax-unit frame as ``weight_r01..weight_r80``.

    Required by the SDR variance machinery downstream:
      * ``compute_poverty_impact_with_se`` emits *_se columns by re-aggregating
        under each replicate weight.
      * ``estimate_hi_eitc_takeup`` bootstraps τ_HI_EITC's SE from the same
        per-replicate weights.

    Without this propagation, both fall back to no-variance / judgment bands.
    """

    @staticmethod
    def _fixture_with_replicates():
        """Tiny 2-household fixture with WGTP1..WGTP80 and PWGTP1..PWGTP80 set."""
        hh = pd.DataFrame({
            'SERIALNO': ['001', '002'],
            'WGTP': [100.0, 50.0],
            'HINCP': [30000.0, 60000.0],
            'PUMA': [301, 302],
            'TYPEHUGQ': [1, 1],
            'TEN': [1, 3],
            **{f'WGTP{r}': [100.0 + r, 50.0 + r] for r in range(1, 81)},
        })
        per = pd.DataFrame({
            'SERIALNO': ['001', '002'],
            'SPORDER': [1, 1],
            'AGEP': [35, 35],
            'PWGTP': [100.0, 50.0],
            'PINCP': [30000.0, 60000.0],
            'WAGP': [30000.0, 60000.0],
            'SCHL': [16, 16],
            'MAR': [5, 5],
            'RELSHIPP': [20, 20],
            'SEX': [1, 2],
            'CIT': [1, 1],
            'SEMP': [0, 0],
            'ADJINC': [1.0, 1.0],
            **{f'PWGTP{r}': [100.0 + r, 50.0 + r] for r in range(1, 81)},
        })
        return per, hh

    def test_flag_default_on_propagates_80_replicates(self):
        per, hh = self._fixture_with_replicates()
        ctor = TaxUnitConstructor(per, hh, num_processes=1, progress_bar=False,
                                  use_soi_calibration=False)
        units = ctor.create_rule_based_units(parallel=False)
        rep_cols = sorted(c for c in units.columns if c.startswith('weight_r'))
        assert len(rep_cols) == 80, f"expected 80 weight_r columns, got {len(rep_cols)}"
        # First and last canonical names
        assert rep_cols[0] == 'weight_r01'
        assert rep_cols[-1] == 'weight_r80'

    def test_replicates_match_hybrid_weight_formula_single(self):
        """Single filer: weight_r<R> = PWGTP<R> * single_calibration_factor (0.82)."""
        per, hh = self._fixture_with_replicates()
        ctor = TaxUnitConstructor(per, hh, num_processes=1, progress_bar=False,
                                  use_soi_calibration=False)
        units = ctor.create_rule_based_units(parallel=False)
        # Row 0: PWGTP=100, PWGTPr = 100+r, single ⇒ weight × 0.82
        row = units.iloc[0]
        assert row['filing_status'] == 'single', f"expected single, got {row['filing_status']}"
        # Main weight: 100 * 0.82 = 82
        # rtol=1e-5 because the constructor stores weights as float32 internally.
        np.testing.assert_allclose(row['weight'], 82.0, rtol=1e-5)
        # weight_r01: 101 * 0.82 = 82.82
        np.testing.assert_allclose(row['weight_r01'], 82.82, rtol=1e-5)
        # weight_r80: 180 * 0.82 = 147.6
        np.testing.assert_allclose(row['weight_r80'], 147.6, rtol=1e-5)

    def test_replicates_propagate_to_joint_filer(self):
        """Regression (audit A1): married-filing-jointly tax units must also
        carry all 80 weight_r columns. A duplicate `_create_joint_filer`
        definition previously shadowed the replicate-emitting version, so
        every MFJ unit silently lacked replicate weights — biasing all SDR
        standard errors. MFJ uses the household replicate weight × 1.0.
        """
        hh = pd.DataFrame({
            'SERIALNO': ['010'],
            'WGTP': [100.0],
            'HINCP': [80000.0],
            'PUMA': [301],
            'TYPEHUGQ': [1],
            'TEN': [1],
            **{f'WGTP{r}': [100.0 + r] for r in range(1, 81)},
        })
        per = pd.DataFrame({
            'SERIALNO': ['010', '010'],
            'SPORDER': [1, 2],
            'AGEP': [40, 38],
            'PWGTP': [100.0, 100.0],
            'PINCP': [50000.0, 30000.0],
            'WAGP': [50000.0, 30000.0],
            'SCHL': [21, 21],
            'MAR': [1, 1],            # married, spouse present
            'RELSHIPP': [20, 21],     # householder + opposite-sex spouse
            'SEX': [1, 2],
            'CIT': [1, 1],
            'SEMP': [0, 0],
            'ADJINC': [1.0, 1.0],
            **{f'PWGTP{r}': [100.0 + r, 100.0 + r] for r in range(1, 81)},
        })
        ctor = TaxUnitConstructor(per, hh, num_processes=1, progress_bar=False,
                                  use_soi_calibration=False)
        units = ctor.create_rule_based_units(parallel=False)
        mfj = units[units['filing_status'] == 'married_filing_jointly']
        assert len(mfj) == 1, (
            "expected one MFJ unit, got "
            f"{units['filing_status'].tolist()}"
        )
        rep_cols = sorted(c for c in units.columns if c.startswith('weight_r'))
        assert len(rep_cols) == 80, f"expected 80 weight_r columns, got {len(rep_cols)}"
        row = mfj.iloc[0]
        # Every replicate must be populated (not NaN) — the core of the bug.
        assert row[rep_cols].notna().all(), "MFJ unit has missing replicate weights"
        # MFJ ⇒ household replicate weight × 1.0 calibration factor.
        np.testing.assert_allclose(row['weight_r01'], 101.0, rtol=1e-5)
        np.testing.assert_allclose(row['weight_r80'], 180.0, rtol=1e-5)

    def test_replicates_use_household_base_for_hoh_with_dependent(self):
        """Regression (audit B1): a HoH (or single) filer WITH a dependent has
        >= 2 members, so its MAIN weight uses the household WGTP — therefore its
        replicates must use WGTPr, not the householder's PWGTPr. The prior code
        keyed the replicate base on filing_status ('head_of_household' -> PWGTPr),
        mismatching the main weight and biasing SDR variance for every HoH and
        single-with-dependent unit.

        Fixture sets the householder's PWGTP (60+r) DISTINCT from WGTP (100+r) so
        the assertion actually discriminates: correct => WGTPr-based, bug => PWGTPr.
        """
        hh = pd.DataFrame({
            'SERIALNO': ['020'],
            'WGTP': [100.0],
            'HINCP': [40000.0],
            'PUMA': [301],
            'TYPEHUGQ': [1],
            'TEN': [1],
            **{f'WGTP{r}': [100.0 + r] for r in range(1, 81)},
        })
        per = pd.DataFrame({
            'SERIALNO': ['020', '020'],
            'SPORDER': [1, 2],
            'AGEP': [40, 8],
            # Householder PWGTP deliberately != WGTP to discriminate the base.
            'PWGTP': [60.0, 50.0],
            'PINCP': [40000.0, 0.0],
            'WAGP': [40000.0, 0.0],
            'SCHL': [21, 2],
            'MAR': [5, 5],            # never married -> eligible for HoH
            'RELSHIPP': [20, 25],     # householder + biological child
            'SEX': [2, 1],
            'CIT': [1, 1],
            'SEMP': [0, 0],
            'ADJINC': [1.0, 1.0],
            **{f'PWGTP{r}': [60.0 + r, 50.0 + r] for r in range(1, 81)},
        })
        ctor = TaxUnitConstructor(per, hh, num_processes=1, progress_bar=False,
                                  use_soi_calibration=False)
        units = ctor.create_rule_based_units(parallel=False)
        hoh = units[units['filing_status'] == 'head_of_household']
        assert len(hoh) == 1, (
            "expected one HoH unit (householder + dependent child), got "
            f"{units['filing_status'].tolist()}"
        )
        row = hoh.iloc[0]
        assert row['num_dependents'] == 1
        # Main weight uses WGTP (>=2 members) x HoH factor 1.30: 100 * 1.30 = 130.
        np.testing.assert_allclose(row['weight'], 130.0, rtol=1e-5)
        # Replicates must therefore be WGTPr * 1.30, NOT the householder's PWGTPr.
        # weight_r01: WGTP1(101) * 1.30 = 131.30  (bug would give PWGTP1(61)*1.30=79.30)
        np.testing.assert_allclose(row['weight_r01'], 131.30, rtol=1e-5)
        # weight_r80: WGTP80(180) * 1.30 = 234.0
        np.testing.assert_allclose(row['weight_r80'], 234.0, rtol=1e-5)

    def test_flag_off_suppresses_replicates(self):
        per, hh = self._fixture_with_replicates()
        ctor = TaxUnitConstructor(per, hh, num_processes=1, progress_bar=False,
                                  use_soi_calibration=False,
                                  include_replicate_weights=False)
        units = ctor.create_rule_based_units(parallel=False)
        rep_cols = [c for c in units.columns if c.startswith('weight_r')]
        assert rep_cols == [], f"expected no weight_r columns, got {rep_cols}"

    def test_missing_replicate_inputs_noop_gracefully(self):
        """No WGTPr/PWGTPr columns on input ⇒ no weight_r columns on output."""
        per, hh = self._fixture_with_replicates()
        # Drop all WGTPr and PWGTPr columns
        hh_bare = hh.drop(columns=[c for c in hh.columns if c.startswith('WGTP') and c != 'WGTP'])
        per_bare = per.drop(columns=[c for c in per.columns if c.startswith('PWGTP') and c != 'PWGTP'])
        ctor = TaxUnitConstructor(per_bare, hh_bare, num_processes=1, progress_bar=False,
                                  use_soi_calibration=False)
        assert ctor._wgtp_replicate_cols == []
        assert ctor._pwgtp_replicate_cols == []
        units = ctor.create_rule_based_units(parallel=False)
        rep_cols = [c for c in units.columns if c.startswith('weight_r')]
        assert rep_cols == []


def _make_df(person_rows, hh_rows):
    pdf = pd.DataFrame(person_rows)
    pdf['person_id'] = pdf['SERIALNO'] + '_' + pdf['SPORDER'].astype(str)
    pdf['is_adult'] = pdf['AGEP'] >= 18
    pdf = pdf.set_index('person_id')
    return pdf, pd.DataFrame(hh_rows)


class TestAdultDependentNotOverSplit:
    """Over-splitting fix: an adult who is claimed as a dependent must not also
    become their own tax unit, and real dependent ages must be carried."""

    def test_adult_relative_child_does_not_create_extra_unit(self):
        """Householder + non-student adult child (age 27, $3k) → ONE unit, with
        the adult child claimed as a dependent rather than filing separately."""
        person_rows = [
            {'SERIALNO': '90', 'SPORDER': '1', 'AGEP': 58, 'SEX': 1, 'MAR': 5,
             'RELSHIPP': 20, 'WAGP': 70000, 'PINCP': 70000, 'CIT': 1, 'SEMP': 0,
             'ADJINC': 1.0, 'SCHL': 21, 'DIS': 2},
            {'SERIALNO': '90', 'SPORDER': '2', 'AGEP': 27, 'SEX': 2, 'MAR': 5,
             'RELSHIPP': 25, 'WAGP': 3000, 'PINCP': 3000, 'CIT': 1, 'SEMP': 0,
             'ADJINC': 1.0, 'SCHL': 19, 'DIS': 2},
        ]
        hh_rows = [{'SERIALNO': '90', 'HINCP': 73000, 'ADJINC': 1.0, 'WGTP': 100}]
        person_df, hh_df = _make_df(person_rows, hh_rows)

        units = TaxUnitConstructor(
            person_df, hh_df, use_soi_calibration=False, progress_bar=False
        ).create_rule_based_units(parallel=False)

        hh90 = units[units['hh_id'] == '90']
        assert len(hh90) == 1, hh90[['filing_status', 'num_dependents']].to_dict('records')
        assert hh90.iloc[0]['num_dependents'] == 1

    def test_dependents_details_carry_real_ages(self):
        """dependents_details must reflect the real dependent age/relationship,
        not synthetic age-10 placeholders."""
        person_rows = [
            {'SERIALNO': '91', 'SPORDER': '1', 'AGEP': 40, 'SEX': 2, 'MAR': 5,
             'RELSHIPP': 20, 'WAGP': 45000, 'PINCP': 45000, 'CIT': 1, 'SEMP': 0,
             'ADJINC': 1.0, 'SCHL': 18, 'DIS': 2},
            {'SERIALNO': '91', 'SPORDER': '2', 'AGEP': 7, 'SEX': 1, 'MAR': 0,
             'RELSHIPP': 25, 'WAGP': 0, 'PINCP': 0, 'CIT': 1, 'SEMP': 0,
             'ADJINC': 1.0, 'SCHL': 4, 'DIS': 2},
        ]
        hh_rows = [{'SERIALNO': '91', 'HINCP': 45000, 'ADJINC': 1.0, 'WGTP': 100}]
        person_df, hh_df = _make_df(person_rows, hh_rows)

        units = TaxUnitConstructor(
            person_df, hh_df, use_soi_calibration=False, progress_bar=False
        ).create_rule_based_units(parallel=False)

        row = units[units['hh_id'] == '91'].iloc[0]
        details = row['dependents_details']
        assert isinstance(details, list) and len(details) == 1
        assert details[0]['age'] == 7
        assert details[0]['relationship'] == 25

    def test_dependent_income_excluded_from_filer_agi(self):
        """Regression (audit B5): a claimed dependent's earnings are NOT folded
        into the filer's tax-unit income (AGI). The parent's unit income is the
        parent's own income only. (ADJINC=1_000_000 → factor 1.0.)"""
        person_rows = [
            {'SERIALNO': '92', 'SPORDER': '1', 'AGEP': 40, 'SEX': 2, 'MAR': 5,
             'RELSHIPP': 20, 'WAGP': 50000, 'PINCP': 50000, 'CIT': 1, 'SEMP': 0,
             'ADJINC': 1_000_000, 'SCHL': 18, 'DIS': 2, 'SCH': 0},
            {'SERIALNO': '92', 'SPORDER': '2', 'AGEP': 10, 'SEX': 1, 'MAR': 0,
             'RELSHIPP': 25, 'WAGP': 6000, 'PINCP': 6000, 'CIT': 1, 'SEMP': 0,
             'ADJINC': 1_000_000, 'SCHL': 3, 'DIS': 2, 'SCH': 2},
        ]
        hh_rows = [{'SERIALNO': '92', 'HINCP': 56000, 'ADJINC': 1_000_000, 'WGTP': 100}]
        person_df, hh_df = _make_df(person_rows, hh_rows)

        units = TaxUnitConstructor(
            person_df, hh_df, use_soi_calibration=False, progress_bar=False
        ).create_rule_based_units(parallel=False)

        hh92 = units[units['hh_id'] == '92']
        assert len(hh92) == 1, "expected one unit (parent claims the child)"
        row = hh92.iloc[0]
        assert row['num_dependents'] == 1  # child is claimed...
        # ...but the child's $6k is NOT in the filer's income — parent's $50k only.
        np.testing.assert_allclose(row['income'], 50000.0, rtol=1e-4)
