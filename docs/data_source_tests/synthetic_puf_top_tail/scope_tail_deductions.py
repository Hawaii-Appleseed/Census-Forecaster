"""How much does the $1M+ deduction level move the tail's AGI?  Read only.

The tail is calibrated to DOTAX's $1M+ tax (tail_k scales its incomes until the
re-scored tax is the target), so a different deduction level at $1M+ is paid for
in AGI: lower deductions need less income for the same tax.  Re-run the tail
calibration (synthesize_calibrated_tail, on a deep copy of the deduction params
with the mortgage, real-estate and charitable amounts scaled by f) and report
the tail's AGI and effective deduction share.  Nothing is saved or changed.
"""
import copy
import logging
import sys
import warnings

import numpy as np

warnings.filterwarnings('ignore')
logging.disable(logging.WARNING)
sys.path.insert(0, 'scripts')
import _forecast_common as c  # noqa: E402

from tax_modeler.base import synthesize_calibrated_tail  # noqa: E402
from tax_modeler.pipeline import compute_base_tax  # noqa: E402

b = c.open_calibrated(shared=False)


def scaled(params, f):
    p = copy.deepcopy(params)
    for t in p['mortgage_interest_tiers']['tiers']:
        t['base_interest'] *= f
    p['mortgage_interest_tiers']['default_base_interest'] *= f
    for t in p['charitable_giving']['rates_by_agi']:
        t['rate'] *= f
    p['charitable_giving']['default_rate'] *= f
    for t in p['real_estate_taxes']['tiers']:
        t['amount'] *= f
    p['real_estate_taxes']['default_amount'] *= f
    return p


print('f     tail_k   tail AGI $M  (AGI-TI)/AGI  eff.ded/AGI  tail tax $M   [deduction params scaled by f; calibrated base fixed]')
for f in (1.0, 0.8, 0.6, 0.5, 0.4, 0.3):
    p = scaled(b.ded_params, f)

    def score(u, p=p):
        return compute_base_tax(u, deduction_params=p, tax_year=b.tax_year)

    u, k = synthesize_calibrated_tail(b.frame, alpha=1.5, score=score, enrich=False)
    m = u.is_synthetic_ultra_high.fillna(False).astype(bool).to_numpy()
    w = u.weight.to_numpy()[m]
    inc = u.hi_agi.to_numpy()[m]
    A = (w * inc).sum()
    gap = 1 - (w * u.hi_taxable_income.to_numpy()[m]).sum() / A
    eff = (w * u.hi_standard_deduction.to_numpy()[m]).sum() / A
    tax = (w * u.hi_tax_before_credits.to_numpy()[m]).sum() / 1e6
    print(f'{f:.1f}  {k:7.4f}  {A/1e6:10,.0f}   {100*gap:8.1f}%   {100*eff:8.1f}%   {tax:8,.1f}')
