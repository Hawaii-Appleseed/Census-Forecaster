"""Hawaii DOTAX deductions (AGI - taxable income, as a share of AGI) by AGI
class vs the model's, on the units scope_build_units.py saved.

DOTAX Table A-1 (resident taxable returns: AGI and taxable income by class,
TY2019-2023) and Table A-8 (resident returns: tax before credits and the
effective rate on taxable income and on AGI by class; 1 - etr_agi/etr_ti is
(deductions + exemptions)/AGI). Model: hi_agi - hi_taxable_income over hi_agi,
which is effective deduction + personal exemptions, the same quantity.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

CAL = Path('packages/tax_modeler/src/tax_modeler/data/calibration')
YEARS = (2019, 2021, 2022, 2023)


def dotax(year):
    return json.load(open(CAL / f'dotax_indinc_{year}.json'))


print('== DOTAX A-1: (AGI - taxable income)/AGI, resident taxable returns')
edges = [(2e5, 3e5), (3e5, 4e5), (4e5, np.inf)]
rows = {}
for y in YEARS:
    for r in dotax(y)['table_a1_resident_taxable']['taxable_classes']:
        lo = r['agi_lo']
        rows[(y, lo)] = 1 - r['taxable_income_M'] / r['agi_M']
for lo in (0, 1e4, 2e4, 3e4, 4e4, 5e4, 75e3, 1e5, 15e4, 2e5, 3e5, 4e5):
    print(f'  {lo:>9,.0f}+ ' + '  '.join(f'{y}: {100*rows[(y, lo)]:5.1f}%' for y in YEARS))

# $200-500K pooled from A-1 (AGI-weighted), TY2023
d = dotax(2023)['table_a1_resident_taxable']['taxable_classes']
sel = [r for r in d if r['agi_lo'] in (2e5, 3e5)]
agi = sum(r['agi_M'] for r in sel); ti = sum(r['taxable_income_M'] for r in sel)
print(f'  $200-400K pooled TY2023: {100*(1-ti/agi):.1f}%')

print('\n== DOTAX A-8: 1 - etr_agi/etr_ti (deductions+exemptions share of AGI), top classes')
for y in YEARS:
    cl = dotax(y)['table_a8_resident_liability']['classes']
    out = []
    for r in cl:
        if r['agi_lo'] and r['agi_lo'] >= 2e5 and r['etr_ti_before_pct']:
            out.append(f"{r['agi_lo']/1e3:.0f}K: {100*(1-r['etr_agi_before_pct']/r['etr_ti_before_pct']):4.1f}%")
    print(' ', y, '  '.join(out))

print('\n== Model, calibrated base + synthetic tail (units(alpha=1.5, premium=.01))')
u = pd.read_parquet(sys.argv[1] if len(sys.argv) > 1 else '/tmp/pufwork/units.parquet')
u = u[u.weight > 0]
w = u.weight.to_numpy()
inc = u.hi_agi.to_numpy()
ti = u.hi_taxable_income.to_numpy()
ded = u.hi_standard_deduction.to_numpy()      # effective: max(SD, itemized)
exm = u.hi_personal_exemptions.to_numpy()
item = u.hi_itemized_deduction.fillna(0).to_numpy()
tax = u.hi_tax_before_credits.to_numpy()
syn = u.is_synthetic_ultra_high.fillna(False).astype(bool).to_numpy()
edges = [(0, 1e4), (1e4, 2e4), (2e4, 3e4), (3e4, 4e4), (4e4, 5e4), (5e4, 75e3), (75e3, 1e5),
         (1e5, 15e4), (15e4, 2e5), (2e5, 3e5), (3e5, 4e5), (4e5, 5e5), (5e5, 75e4), (75e4, 1e6), (1e6, np.inf)]
hdr = 'class          returns   AGI$M  (AGI-TI)/AGI  eff.ded/AGI  itemizing  tax$M  tax/AGI  | taxable-only (AGI-TI)/AGI'
print(hdr)
dot = {r['agi_lo']: r for r in dotax(2023)['table_a1_resident_taxable']['taxable_classes']}
recs = []
for lo, hi in edges:
    m = (inc >= lo) & (inc < hi)
    if not m.any():
        continue
    A = (w[m] * inc[m]).sum()
    t = m & (tax > 0)
    At = (w[t] * inc[t]).sum()
    r = dict(lo=lo, n=w[m].sum(), agi=A / 1e6, gap=1 - (w[m] * ti[m]).sum() / A,
             eff=(w[m] * ded[m]).sum() / A, itemizing=(w[m] * (item[m] > 0) * (item[m] >= ded[m] - 1)).sum() / w[m].sum(),
             tax=(w[m] * tax[m]).sum() / 1e6, etr=(w[m] * tax[m]).sum() / A,
             gap_taxable=1 - (w[t] * ti[t]).sum() / At if At else np.nan)
    recs.append(r)
    print(f"{lo/1e3:>8,.0f}K+ {r['n']:>10,.0f} {r['agi']:>9,.0f}   {100*r['gap']:>8.1f}%   {100*r['eff']:>8.1f}%  {100*r['itemizing']:>6.1f}%  {r['tax']:>7,.0f} {100*r['etr']:>6.2f}% | {100*r['gap_taxable']:>6.1f}%")
pd.DataFrame(recs).to_csv('/tmp/pufwork/model_ded_by_class.csv', index=False)

print('\n== AGI and taxable income by class, model vs DOTAX TY2023 A-1 (resident taxable returns), $M')
for lo, hi in edges[:-4]:
    m = (inc >= lo) & (inc < hi)
    t = m & (tax > 0)
    r = dot.get(float(lo))
    if r:
        print(f"  {lo/1e3:>6,.0f}K+ returns model {w[t].sum():>8,.0f} DOTAX {r['returns']:>7,} | AGI model {(w[t]*inc[t]).sum()/1e6:>7,.0f} DOTAX {r['agi_M']:>7,.0f} ({100*((w[t]*inc[t]).sum()/1e6/r['agi_M']-1):+5.1f}%) | TI model {(w[t]*ti[t]).sum()/1e6:>7,.0f} DOTAX {r['taxable_income_M']:>7,.0f} ({100*((w[t]*ti[t]).sum()/1e6/r['taxable_income_M']-1):+5.1f}%)")

print('\n== Model vs DOTAX TY2023 A-8: returns and tax before credits by class (the calibration targets)')
cl = dotax(2023)['table_a8_resident_liability']['classes']
for r in cl:
    if r['agi_lo'] and r['agi_lo'] >= 1e5:
        lo, hi = r['agi_lo'], r['agi_hi'] if r['agi_hi'] else np.inf
        m = (inc >= lo) & (inc < hi)
        print(f"  {lo/1e3:>6,.0f}K+ DOTAX returns {r['returns']:>7,} tax ${r['tax_before_M']:>6,.0f}M | model {w[m].sum():>9,.0f} ${(w[m]*tax[m]).sum()/1e6:>6,.0f}M")
print('  Synthetic share of model $1M+ tax:', f"{(w[syn]*tax[syn]).sum()/1e6:,.0f}M")

print('\n== $400K+ classes: AGI and taxable income implied by A-8 (tax / etr_agi, tax / etr_ti; rounded rates, so +-1%) vs model, TY2023, $M')
for r in dotax(2023)['table_a8_resident_liability']['classes']:
    if r['agi_lo'] and r['agi_lo'] >= 4e5:
        lo, hi = r['agi_lo'], r['agi_hi'] if r['agi_hi'] else np.inf
        m = (inc >= lo) & (inc < hi)
        a8_agi = r['tax_before_M'] / (r['etr_agi_before_pct'] / 100)
        a8_ti = r['tax_before_M'] / (r['etr_ti_before_pct'] / 100)
        mA = (w[m] * inc[m]).sum() / 1e6
        mT = (w[m] * ti[m]).sum() / 1e6
        print(f"  {lo/1e3:>6,.0f}K+ AGI DOTAX~{a8_agi:>7,.0f} model {mA:>7,.0f} ({100*(mA/a8_agi-1):+5.1f}%) | TI DOTAX~{a8_ti:>7,.0f} model {mT:>7,.0f} ({100*(mT/a8_ti-1):+5.1f}%)"
              f" | ded+exempt share DOTAX {100*(1-a8_ti and 1-r['etr_agi_before_pct']/r['etr_ti_before_pct']):.1f}% model {100*(1-mT/mA):.1f}%")
