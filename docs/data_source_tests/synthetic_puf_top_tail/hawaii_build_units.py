import sys; sys.path.insert(0,'scripts')
import _forecast_common as c, numpy as np
b=c.open_calibrated(shared=False)
u,k=b.units(alpha=1.5, top_premium=0.01)
print('tail_k',k,u.shape)
syn=u.get('is_synthetic_ultra_high')
print(syn.fillna(False).sum() if syn is not None else None)
cols=[x for x in u.columns if not x.startswith('weight_r') and x not in('dependents','dependents_details','dependent_person_ids')]
u[cols].to_parquet('/tmp/pufwork/hi_units.parquet')
print(cols)
