"""Build the chain's MID units once on the cached calibrated base (read only)
and save them, plus the base and the deduction params' fingerprint, to /tmp."""
import sys
sys.path.insert(0, 'scripts')
import _forecast_common as c
b = c.open_calibrated(shared=False)
u, k = b.units(alpha=1.5, top_premium=0.01)
print('tail_k', k, u.shape, 'base', b.frame.shape, 'tax_year', b.tax_year)
drop = ('dependents', 'dependents_details', 'dependent_person_ids')
cols = [x for x in u.columns if not x.startswith('weight_r') and x not in drop]
u[cols].to_parquet('/tmp/pufwork/units.parquet')
bc = [x for x in b.frame.columns if not x.startswith('weight_r') and x not in drop]
b.frame[bc].to_parquet('/tmp/pufwork/base.parquet')
import pandas as pd
syn = u.is_synthetic_ultra_high.fillna(False).astype(bool)
pd.set_option('display.width', 250, 'display.max_columns', 60)
s = u[syn]
num = s.select_dtypes('number')
print(num.loc[:, (num != 0).any()].iloc[:6].T.to_string())
