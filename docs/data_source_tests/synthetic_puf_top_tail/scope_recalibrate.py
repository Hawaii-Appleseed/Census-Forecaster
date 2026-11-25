"""Recalibrate the base in memory with the CURRENT code, from the raw tax-unit cache
(read only, nothing written under data/), then build the same units scope_build_units.py
builds.  Why: the pickle open_calibrated() loads is the 2026-10-09 calibration (its meta has
no content_key, so no later step has re-saved it), which predates the bounded raking of
2026-11-20; this shows what the current calibration gives.  The raw cache is the 2026-10-30
one (its code_key is not the current one: tax-unit construction may have changed since)."""
import logging
import sys
import warnings

import pandas as pd

warnings.filterwarnings('ignore')
logging.disable(logging.WARNING)
sys.path.insert(0, 'scripts')
import _forecast_common as c  # noqa: E402

from tax_modeler.base import build_calibrated_base  # noqa: E402
from tax_modeler.scenarios.act24_population import build_units  # noqa: E402

raw = pd.read_parquet(c.CACHE_FILE)
print('raw units', raw.shape)
base, ded = build_calibrated_base(raw, tax_year=2023, log=lambda *a, **k: None)
print('recalibrated base', base.shape, 'weight sum', round(base.weight.sum()))
u, k = build_units(base, alpha=1.5, top_premium=0.01, ded_params=ded, cal_tax_year=2023)
print('tail_k', k, u.shape)
drop = ('dependents', 'dependents_details', 'dependent_person_ids')
cols = [x for x in u.columns if not x.startswith('weight_r') and x not in drop]
u[cols].to_parquet('/tmp/pufwork/units_recal.parquet')
