"""Counterfactual: what would the $1M+ non-gains split change?  Read only.

Usage: python scope_composition.py <year> [<year> ...]   (needs /tmp/pufwork/units.parquet
from scope_build_units.py and the cached calibrated base).

1. Hawaii tax of the synthetic rows, as labelled vs relabelled (tax invariance).
2. Federal AGI of the rows (add_federal_income_columns) as labelled vs relabelled.
3. CBO component aging (cbo_aging.age_filers_with_components) of the rows under
   four labellings, against the county-scalar growth the Act 24 chain uses.
4. Static Act 24 (CD2) bracket delta with the $1M+ rows' income moved to each
   alternative growth, re-scored (refresh_stale_hawaii_tax + compare_systems).
"""
import logging
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')
logging.disable(logging.WARNING)
sys.path.insert(0, 'scripts')
import _forecast_common as c  # noqa: E402

from tax_modeler.calibration.cbo_aging import age_filers_with_components  # noqa: E402
from tax_modeler.config.tax_system_config import TaxCalculator, TaxSystemRegistry, compare_systems  # noqa: E402
from tax_modeler.liability.federal import add_federal_income_columns  # noqa: E402
from tax_modeler.loaders.pums_loader import PUMS_INCOME_DOLLAR_YEAR as BASEY  # noqa: E402
from tax_modeler.pipeline import compute_base_tax  # noqa: E402
from tax_modeler.projection.tax_unit_projector import refresh_stale_hawaii_tax  # noqa: E402
from tax_modeler.scenarios.registry import get_scenario  # noqa: E402

PREMIUM = 0.01
# National SOI Table 1.4 TY2022, $1M+ (docs/data_source_tests/synthetic-puf-top-tail.md)
NAT = dict(wages=27.6, business=26.4, divint=8.5, gains=31.9)
NAT['other'] = 100 - sum(NAT.values())
HI_WAGES = 0.209  # Hawaii SOI Table 2 TY2022: wages 1,439M / AGI 6,891M above $1M

b = c.open_calibrated(shared=False)
u, k = b.units(alpha=1.5, top_premium=PREMIUM)
syn = u.is_synthetic_ultra_high.fillna(False).astype(bool).to_numpy()
tail = u.loc[syn].copy()
w = tail.weight.to_numpy()
inc = tail.income.to_numpy()
s = tail.synthetic_cg_share.to_numpy()
W = lambda x: float((w * x).sum())  # noqa: E731
print(f'tail rows {syn.sum()}  weight {w.sum():,.0f}  AGI ${W(inc)/1e6:,.0f}M  weighted CG share {W(inc*s)/W(inc):.3f}')

COLS = ['primary_wagp', 'secondary_wagp', 'primary_semp', 'secondary_semp', 'primary_intp', 'secondary_intp',
        'primary_div', 'secondary_div', 'primary_retp', 'secondary_retp']


def labelled(kind):
    t = tail.copy()
    for col in COLS:
        if col in t.columns:
            t[col] = 0.0
    rest = inc * (1 - s)
    if kind == 'asbuilt':          # the model's own: wages (1-s), "interest" = the gain dollars
        t['primary_wagp'] = rest
        t['primary_intp'] = inc * s
    elif kind == 'twobucket':      # what the label intends: wages (1-s), gains only in synthetic_cg_share
        t['primary_wagp'] = rest
    elif kind in ('national', 'hawaii'):
        if kind == 'national':
            wage = rest * NAT['wages'] / (100 - NAT['gains'])
            other_nonwage = rest - wage
        else:
            wage = np.minimum(inc * HI_WAGES, rest)
            other_nonwage = rest - wage
        nw = NAT['business'] + NAT['divint'] + NAT['other']
        t['primary_wagp'] = wage
        t['primary_semp'] = other_nonwage * NAT['business'] / nw
        t['primary_div'] = other_nonwage * NAT['divint'] / nw / 2     # SOI gives dividends+interest together
        t['primary_intp'] = other_nonwage * NAT['divint'] / nw / 2
        # the remainder (NAT['other']) is the "other" residual in cbo_aging
    t['earned_income'] = t['primary_wagp'] + t['primary_semp']
    t['investment_income'] = t['primary_intp'] + t['primary_div'] + (inc * s if kind == 'asbuilt' else 0)
    return t


KINDS = ['asbuilt', 'twobucket', 'national', 'hawaii']
LAB = {k: labelled(k) for k in KINDS}

print('\n-- 1. Hawaii tax of the tail rows, TY2023 basis, by labelling (the model scores these columns? no)')
base_tax = None
for kind in KINDS:
    sc = compute_base_tax(LAB[kind].assign(hi_tax_liability=0.0), deduction_params=b.ded_params, tax_year=b.tax_year)
    tx = W(sc.hi_tax_before_credits.to_numpy())
    base_tax = tx if base_tax is None else base_tax
    print(f'  {kind:9s} tail tax before credits ${tx/1e6:,.3f}M  (diff vs asbuilt {tx-base_tax:,.2f} $)')

print('\n-- 2. federal_agi of the tail rows (add_federal_income_columns), $M')
for kind in KINDS:
    f = add_federal_income_columns(LAB[kind])
    print(f'  {kind:9s} federal_agi ${W(f.federal_agi.to_numpy())/1e6:,.0f}M  federal_earned ${W(f.federal_earned_income.to_numpy())/1e6:,.0f}M '
          f'({100*W(f.federal_agi.to_numpy())/W(inc):.1f}% of income)')

calc = TaxCalculator()
act46 = TaxSystemRegistry.get_act46_system
act24 = get_scenario('sb3125_cd2').system

for year in [int(a) for a in sys.argv[1:]] or [2027]:
    print(f'\n===== TY{year}')
    proj = b.projected(u, year=year, top_premium=PREMIUM)
    ps = proj.is_synthetic_ultra_high.fillna(False).astype(bool).to_numpy()
    assert ps.sum() == syn.sum()
    pinc = proj.loc[ps, 'income'].to_numpy()
    prem = (1 + PREMIUM) ** (year - BASEY)
    g_chain = pinc / inc                       # county scalar x premium
    print(f'  chain (county B19013 x premium {prem:.4f}): tail growth {W(pinc)/W(inc):.4f} (ex-premium {W(pinc)/W(inc)/prem:.4f})')

    def score(frame):
        rev = compare_systems(frame, act46(year), act24(year), calculator=calc)
        # static bracket delta: scenario - baseline revenue, $M
        return float(rev.iloc[1]['revenue_millions'] - rev.iloc[0]['revenue_millions']), float(rev.iloc[0]['revenue_millions'])

    base_frame = refresh_stale_hawaii_tax(proj, target_year=year)
    d0, r0 = score(base_frame)
    print(f'  chain as-is: Act 24 static delta ${d0:,.1f}M  (Act 46 revenue ${r0:,.0f}M)')
    mask_all = base_frame.is_synthetic_ultra_high.fillna(False).astype(bool).to_numpy()
    t_tax = compute_base_tax  # noqa
    for kind in KINDS:
        aged = age_filers_with_components(LAB[kind], target_year=year, base_year=BASEY)
        ainc = aged.income.to_numpy()
        g = ainc / inc
        newinc = inc * g * prem                # CBO components, then the same top premium
        share = np.where(ainc > 0, aged['cbo_aged_capital_gains'].to_numpy() / ainc, 0.0)
        fr = proj.copy()
        pos = np.flatnonzero(ps)
        fr.iloc[pos, fr.columns.get_loc('income')] = newinc
        fr.iloc[pos, fr.columns.get_loc('agi')] = newinc
        fr.iloc[pos, fr.columns.get_loc('synthetic_cg_share')] = share
        fr = refresh_stale_hawaii_tax(fr, target_year=year)
        d, r = score(fr)
        comp = {cn: W(aged[f'cbo_aged_{cn}'].to_numpy()) / W(ainc) for cn in ('wages', 'business', 'capital_gains', 'dividends', 'interest', 'retirement', 'other')}
        print(f'  CBO aging, labelled {kind:9s}: tail growth ex-premium {W(ainc)/W(inc):.4f} '
              f'(vs chain {W(pinc)/W(inc)/prem:.4f}); tail AGI ${W(newinc)/1e6:,.0f}M ({100*(W(newinc)/W(pinc)-1):+.1f}% vs chain); '
              f'cg share {W(newinc*share)/W(newinc):.3f}; Act 24 static ${d:,.1f}M ({d-d0:+.1f}); Act 46 rev ${r:,.0f}M ({r-r0:+.1f})')
        print('      aged mix: ' + ' '.join(f'{k_}={v:.3f}' for k_, v in comp.items()))
