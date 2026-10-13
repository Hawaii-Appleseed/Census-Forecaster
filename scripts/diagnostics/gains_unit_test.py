"""Per-unit check: does Act 24 minus Act 46 liability change with gains alone?
Uses the base projected frame saved by gains_shift_decomp.py --save (TY2029, anchored).
Cases per unit: (A) gains dollars +50% with ORDINARY income held (income rises by the gains added);
(B) ordinary income +E with gains dollars held (what the anchored shifted run does: extra income lands as ordinary)."""
import os, pickle, sys, warnings, logging
warnings.filterwarnings("ignore"); logging.disable(logging.WARNING)
import numpy as np
sys.path.insert(0, os.getcwd())
from tax_modeler.config.tax_system_config import TaxCalculator, TaxSystemRegistry
from tax_modeler.calibration import cg_anchor
from tax_modeler.scenarios import act24_population as ap
Y = 2029
S = pickle.load(open("/tmp/gs/base.pkl", "rb"))
df = S["proj"][Y]; df = cg_anchor.apply_anchor_factors(df, S["factors"][Y])
lab = S["labels"][Y]; calc = TaxCalculator()
b = ap.statute(TaxSystemRegistry.get_act46_system)(Y); s = ap.statute(TaxSystemRegistry.get_sb3125_cd2_system)(Y)
def d(x):
    return np.asarray(calc.unit_liabilities(x, s)["net"], float) - np.asarray(calc.unit_liabilities(x, b)["net"], float)
rng = np.random.default_rng(0)
rows = []
for grp, mask in (("1mp", lab == "1mp"), ("200K-1M", np.isin(lab, ["200_300", "300_400", "400_1m"]))):
    idx = np.where(mask & (df["synthetic_cg_share"].to_numpy() > 0.05))[0]
    pick = rng.choice(idx, 5, replace=False)
    sub = df.iloc[pick].copy()
    inc = sub["income"].to_numpy(float); sh = sub["synthetic_cg_share"].to_numpy(float); g = inc * sh
    A = sub.copy(); A["income"] = inc + 0.5 * g; A["synthetic_cg_share"] = (1.5 * g) / A["income"]
    Bf = sub.copy(); Bf["income"] = inc * 1.25; Bf["synthetic_cg_share"] = g / Bf["income"]
    # strip any stale tax columns that compare may reuse
    d0, dA, dB = d(sub), d(A), d(Bf)
    for i in range(5):
        print(f"{grp:8s} inc={inc[i]/1e6:8.3f}M gains={g[i]/1e6:7.3f}M  d24-46: base={d0[i]:12.0f}  gains+50%={dA[i]:12.0f}  ordinary+25%inc={dB[i]:12.0f}")
