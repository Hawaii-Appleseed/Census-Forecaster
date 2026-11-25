"""Parent-first claimant assignment (units/claimants.py) on small synthetic households."""

from __future__ import annotations

import pandas as pd
import pytest
from tax_modeler.units.constructor import TaxUnitConstructor
from tax_modeler.units.dependencies import identify_dependents, identify_dependents_with_basis


def _p(order, age, rel, *, wagp=0, mar=5, sfn=None, sfr=None, sch=0, serial="1"):
    row = {'SERIALNO': serial, 'SPORDER': order, 'AGEP': age, 'SEX': 1, 'MAR': mar, 'RELSHIPP': rel,
           'SCHL': 16 if age >= 18 else 3, 'SCH': (sch or 2) if age < 18 else sch, 'WAGP': wagp, 'PINCP': wagp,
           'PWGTP': 50, 'CIT': 1, 'ADJINC': 1.0, 'SEMP': 0}
    if sfn is not None:
        row['SFN'], row['SFR'] = sfn, sfr
    return row


def _hh(rows, *, subfamily_columns=True):
    df = pd.DataFrame(rows)
    if subfamily_columns and 'SFN' not in df:
        df['SFN'], df['SFR'] = float('nan'), float('nan')
    elif subfamily_columns:
        df[['SFN', 'SFR']] = df[['SFN', 'SFR']].astype(float)
    df['person_id'] = df['SERIALNO'] + '_' + df['SPORDER'].astype(str)
    return df.set_index('person_id')


def _three_generation(**kw):
    # householder couple, their 32-year-old single-parent daughter, her 5-year-old
    return _hh([
        _p(1, 62, 20, wagp=150000, mar=1), _p(2, 60, 21, mar=1),
        _p(3, 32, 25, wagp=18000, sfn=1, sfr=3),
        _p(4, 5, 30, sfn=1, sfr=5),
    ], **kw)


def _claimant_of(deps, child):
    return next((g for g, ds in deps.items() if child in ds), None)


def test_grandchild_goes_to_its_parent_not_the_householder():
    deps, basis = identify_dependents_with_basis(_three_generation())
    assert _claimant_of(deps, '1_4') == '1_3'
    assert basis['1_4'] == 'subfamily'


def test_legacy_assignment_gives_the_grandchild_to_the_householder():
    deps, basis = identify_dependents_with_basis(_three_generation(), assignment='legacy')
    assert _claimant_of(deps, '1_4') == '1_1'
    assert basis == {}


def test_two_adult_children_each_with_a_child_use_the_subfamily_link():
    hh = _hh([
        _p(1, 64, 20, wagp=90000, mar=1), _p(2, 62, 21, mar=1),
        _p(3, 40, 25, wagp=60000, sfn=1, sfr=3), _p(4, 14, 30, sfn=1, sfr=6),   # older parent, older child
        _p(5, 29, 25, wagp=20000, sfn=2, sfr=3), _p(6, 3, 30, sfn=2, sfr=5),    # younger parent, young child
    ])
    deps = identify_dependents(hh)
    assert _claimant_of(deps, '1_4') == '1_3'
    assert _claimant_of(deps, '1_6') == '1_5'


def test_parent_choice_without_subfamily_columns_low_nearest_high():
    # no SFN/SFR in the data: two adult children could be the parent of the 5-year-old
    rows = [_p(1, 64, 20, wagp=90000, mar=1), _p(2, 62, 21, mar=1),
            _p(3, 40, 25, wagp=60000), _p(4, 27, 25, wagp=12000), _p(5, 5, 30)]
    hh = _hh(rows, subfamily_columns=False)
    # low: ambiguous, so the grandparent as before
    assert _claimant_of(identify_dependents(hh, parent_link='rule_low'), '1_5') == '1_1'
    # nearest: gap 22 (age 27) is closer to the typical 28 than gap 35
    assert _claimant_of(identify_dependents(hh, parent_link='rule_nearest'), '1_5') == '1_4'
    # high: the lower-income candidate (the larger credit); here the same person
    assert _claimant_of(identify_dependents(hh, parent_link='rule_high'), '1_5') == '1_4'
    # default policy falls back to the rule when the file has no subfamily columns
    assert _claimant_of(identify_dependents(hh), '1_5') == '1_4'


def test_high_and_nearest_can_differ():
    rows = [_p(1, 64, 20, wagp=90000, mar=1), _p(2, 62, 21, mar=1),
            _p(3, 33, 25, wagp=60000), _p(4, 45, 25, wagp=9000), _p(5, 5, 30)]
    hh = _hh(rows, subfamily_columns=False)
    assert _claimant_of(identify_dependents(hh, parent_link='rule_nearest'), '1_5') == '1_3'  # gap 28
    assert _claimant_of(identify_dependents(hh, parent_link='rule_high'), '1_5') == '1_4'     # lowest income


def test_a_subfamily_less_minor_stays_with_the_householder_under_sfn_policy():
    # the file has SFN columns and says the grandchild is in no subfamily: not an adult child's
    hh = _hh([_p(1, 62, 20, wagp=50000, mar=1), _p(2, 32, 25, wagp=18000), _p(3, 5, 30)])
    deps, basis = identify_dependents_with_basis(hh)
    assert _claimant_of(deps, '1_3') == '1_1' and basis['1_3'] == 'householder'
    deps = identify_dependents(hh, parent_link='sfn+rule_nearest')
    assert _claimant_of(deps, '1_3') == '1_2'


def test_unmarried_partners_child_goes_to_the_partner():
    hh = _hh([_p(1, 41, 20, wagp=70000), _p(2, 37, 22, wagp=15000), _p(3, 6, 36, sch=1)])
    deps, basis = identify_dependents_with_basis(hh)
    assert _claimant_of(deps, '1_3') == '1_2' and basis['1_3'] == 'rule'
    # legacy: nobody claimed it
    assert _claimant_of(identify_dependents(hh, assignment='legacy'), '1_3') is None


def test_unrelated_housemates_child_goes_to_the_housemate_never_the_householder():
    hh = _hh([_p(1, 52, 20, wagp=80000), _p(2, 30, 34, wagp=22000), _p(3, 4, 36)])
    deps, basis = identify_dependents_with_basis(hh)
    assert _claimant_of(deps, '1_3') == '1_2'
    assert _claimant_of(deps, '1_3') != '1_1'


def test_children_with_no_plausible_parent():
    # an unrelated child (not a student) with no adult in the age range: claimed by nobody, as before
    hh = _hh([_p(1, 80, 20, wagp=30000), _p(2, 4, 36)])
    hh.loc['1_2', 'SCH'] = 1
    deps, basis = identify_dependents_with_basis(hh)
    assert _claimant_of(deps, '1_2') is None and basis['1_2'] == 'unclaimed'
    # a grandchild with no parent present: the householder (the grandparent)
    hh = _hh([_p(1, 66, 20, wagp=30000), _p(2, 9, 30)])
    deps, basis = identify_dependents_with_basis(hh)
    assert _claimant_of(deps, '1_2') == '1_1' and basis['1_2'] == 'householder'
    # a non-student 'other relative' minor with no parent present now goes to the householder
    hh = _hh([_p(1, 45, 20, wagp=30000), _p(2, 3, 33)])
    hh.loc['1_2', 'SCH'] = 1
    assert _claimant_of(identify_dependents(hh), '1_2') == '1_1'
    assert _claimant_of(identify_dependents(hh, assignment='legacy'), '1_2') is None


def test_a_parent_is_never_made_another_adults_qualifying_relative():
    # daughter earns nothing and is supported by the householder, but she holds her own child
    hh = _hh([_p(1, 62, 20, wagp=150000, mar=1), _p(2, 60, 21, mar=1),
              _p(3, 32, 25, wagp=0, sfn=1, sfr=3), _p(4, 5, 30, sfn=1, sfr=5)])
    deps = identify_dependents(hh)
    assert _claimant_of(deps, '1_4') == '1_3'
    assert _claimant_of(deps, '1_3') is None


def test_unrelated_students_keep_the_legacy_householder_claim():
    hh = _hh([_p(1, 52, 20, wagp=80000), _p(2, 15, 36)])   # enrolled (SCH=2 for minors in _p)
    deps, basis = identify_dependents_with_basis(hh)
    assert _claimant_of(deps, '1_2') == '1_1' and basis['1_2'] == 'student_householder'


def test_bad_options_are_refused():
    with pytest.raises(ValueError):
        identify_dependents(_three_generation(), assignment='nope')
    with pytest.raises(ValueError):
        identify_dependents(_three_generation(), parent_link='nope')
    with pytest.raises(ValueError):
        TaxUnitConstructor(pd.DataFrame(), pd.DataFrame(), claimant_assignment='nope')


# --- through the constructor: the units and the credit code's view of the child ---

def _units(rows, assignment):
    persons = _hh(rows)
    persons['HINCP'] = 100000
    hh = pd.DataFrame([{'SERIALNO': '1', 'HINCP': 100000, 'ADJINC': 1.0, 'WGTP': 100}])
    ctor = TaxUnitConstructor(persons.drop(columns=['HINCP']), hh, progress_bar=False, use_soi_calibration=False,
                              include_replicate_weights=False, claimant_assignment=assignment)
    return ctor.create_rule_based_units(parallel=False)


def test_constructor_puts_the_grandchild_on_the_parents_unit_and_legacy_reproduces_the_old_unit():
    rows = [_p(1, 62, 20, wagp=150000, mar=1), _p(2, 60, 21, mar=1),
            _p(3, 32, 25, wagp=18000, sfn=1, sfr=3), _p(4, 5, 30, sfn=1, sfr=5)]
    new = _units(rows, 'parent_first')
    old = _units(rows, 'legacy')
    parent = new[new['primary_filer_id'] == '1_3'].iloc[0]
    assert list(parent['dependents']) == ['1_4']
    assert parent['filing_status'] == 'head_of_household'
    joint = new[new['filing_status'] == 'married_filing_jointly'].iloc[0]
    assert joint['num_dependents'] == 0
    assert old[old['primary_filer_id'] == '1_3'].iloc[0]['num_dependents'] == 0
    assert old[old['filing_status'] == 'married_filing_jointly'].iloc[0]['num_dependents'] == 1


def test_a_partners_child_reaches_the_credit_code_as_an_own_child():
    from tax_modeler.credits.eitc import _count_qualifying_children_eitc
    rows = [_p(1, 41, 20, wagp=70000), _p(2, 37, 22, wagp=15000), _p(3, 6, 36, sch=1)]
    new = _units(rows, 'parent_first')
    unit = new[new['primary_filer_id'] == '1_2'].iloc[0]
    assert _count_qualifying_children_eitc(list(unit['dependents_details'])) == 1
    # the householder does not hold it, and legacy left it with nobody
    assert new[new['primary_filer_id'] == '1_1'].iloc[0]['num_dependents'] == 0
    old = _units(rows, 'legacy')
    assert old['num_dependents'].sum() == 0


def test_every_person_is_still_in_a_unit_or_a_dependent_of_one():
    rows = [_p(1, 62, 20, wagp=150000, mar=1), _p(2, 60, 21, mar=1),
            _p(3, 32, 25, wagp=18000, sfn=1, sfr=3), _p(4, 5, 30, sfn=1, sfr=5),
            _p(5, 30, 22, wagp=9000), _p(6, 3, 36)]
    new = _units(rows, 'parent_first')
    covered = set(new['primary_filer_id']) | set(new['secondary_filer_id'].dropna())
    for d in new['dependents']:
        covered |= set(d)
    assert {'1_1', '1_2', '1_3', '1_4', '1_5', '1_6'} <= covered


def test_a_minor_spouse_or_partner_is_nobodys_dependent():
    hh = _hh([_p(1, 40, 20, wagp=60000), _p(2, 17, 22, wagp=0)])
    deps, basis = identify_dependents_with_basis(hh)
    assert _claimant_of(deps, '1_2') is None and basis['1_2'] == 'unclaimed'
