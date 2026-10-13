"""Scratch diagnostic: decompose the capital-gains growth-shift sensitivity of the MID Act 24 total.

Usage (env HAWAII_GROWTH_SHIFT set for shifted runs; HAWAII_PUMS_DIR not needed, reads the cached base):
  python scripts/diagnostics/gains_shift_decomp.py NAME [--hold tail,factors,credit] [--save]
  --save   : keep per-year projected frames/factors/labels in /tmp/gs/NAME.pkl (use on the base run)
  --hold X : comma list, taken from /tmp/gs/base.pkl:
     tail    age the synthetic tail with the UNSHIFTED spec (base tail aging)
     factors use base-run anchor factors (k per class, year)
     credit  use base projected frame for the credit overlay's AGI-eligibility input
     (ETI hold is done post hoc in the write-up: eti term is reported separately)
NOTE: the old `labels` and `ordinary` holds were dropped on 2026-10-13: they hand-rolled the pre-fix
min(1, share x k) rule, and with the gains anchored at the base year (pipeline.calibrate_anchored) and
income moving with anchored gains (cg_anchor.rescale_gains) there is no reclassified income left to hold.
The unshifted run reproduces the headline MID by year (compare with runs/sb3125_cd2_enhanced/enhanced.csv).
Writes /tmp/gs/NAME.json. MID scenario, CD2 only. Does not change defaults.
"""
import json, os, pickle, sys, warnings, logging
warnings.filterwarnings("ignore"); logging.disable(logging.WARNING)
import numpy as np, pandas as pd
sys.path.insert(0, os.getcwd())
name = sys.argv[1]
holds = set()
if "--hold" in sys.argv:
    holds = set(sys.argv[sys.argv.index("--hold") + 1].split(","))
save = "--save" in sys.argv
from _forecast_common import TARGET_YEARS
CALIBRATED_PKL = os.environ.get('GS_CAL_PKL', '/tmp/gs/cal.pkl')  # copy of data/artifacts/sb3125_calibrated_base.pkl
from tax_modeler.artifacts import load_calibrated_base
from tax_modeler.config.tax_system_config import TaxCalculator, TaxSystemRegistry
from tax_modeler.projection.engine import AgingSpec, TOP_SHARE_DRIFT
from tax_modeler.calibration import cg_anchor
from tax_modeler.scenarios import act24_population as ap
from tax_modeler.scenarios.behavioral_response import BehavioralParams, score_with_response, top_rate_path
from tax_modeler.scenarios.sb3125_cd1_credits import compute_credit_overlay, compute_dynamic_agi_eligibility_share

base, ded, meta = load_calibrated_base(str(CALIBRATED_PKL))
cal_ty = int(meta.get("tax_year", 2023))
B = pickle.load(open("/tmp/gs/base.pkl", "rb")) if holds else None
spec = AgingSpec(engine="cbo", top_share_drift=TOP_SHARE_DRIFT["MID"])
spec_base = AgingSpec(engine="cbo", top_share_drift=TOP_SHARE_DRIFT["MID"], growth_shift={})
print(name, "shift:", dict(spec.growth_shift), "holds:", holds, flush=True)

units, tail_k = ap.build_units(base, alpha=1.5, spec=(spec_base if "tail" in holds else spec),
                               ded_params=ded, cal_tax_year=cal_ty)
# the engine-level shift in the projection still follows `spec`; build_units only used it for tail aging
units_for_k = units
# anchor factors on the MID population (as mid_gains_factors does; same units/spec)
factors = {y: cg_anchor.anchor_factors(ap.project_units(units, year=y, spec=spec), y) for y in TARGET_YEARS}
if "factors" in holds:
    factors = B["factors"]

calc = TaxCalculator()
get_sc = TaxSystemRegistry.get_sb3125_cd2_system
path = top_rate_path(TaxSystemRegistry.get_act46_system, get_sc, range(2027, 2032), calc)
bp = BehavioralParams.named("mid")
out, store = {}, {"factors": factors, "labels": {}, "proj": {}, "tail_k": tail_k}
for y in TARGET_YEARS:
    proj = ap.project_units(units, year=y, spec=spec)
    store["labels"][y] = cg_anchor.rank_classes(proj)
    proj_a = cg_anchor.apply_anchor_factors(proj, factors[y])
    store["proj"][y] = proj
    bcfg = ap.statute(TaxSystemRegistry.get_act46_system)(y)
    scfg = ap.statute(get_sc)(y)
    rev, resp, diag = score_with_response(proj_a, bp, target_year=y, baseline_cfg=bcfg,
                                          scenario_cfg=scfg, calculator=calc, top_rate_path=path)
    cproj = B["proj"][y] if "credit" in holds else proj_a
    credit = compute_credit_overlay(
        y, reec_demand_scenario="obbba_mid", corp_subject_to_agi_limit=False,
        reec_effective_claim_share=0.65, cgec_annual_growth=0.015,
        reec_carryforward_utilization_m=6.0, model_carryforward_pool=True,
        agi_eligibility_by_year={y: compute_dynamic_agi_eligibility_share(cproj)},
        pro_rata_elasticity=0.3, interpretation="B", dynamic_refundable_share=True)
    tb = np.asarray(calc.unit_liabilities(proj_a, bcfg)["net"], float)
    ts = np.asarray(calc.unit_liabilities(proj_a, scfg)["net"], float)
    w = proj_a["weight"].to_numpy(float)
    syn = proj_a["is_synthetic_ultra_high"].fillna(False).astype(bool).to_numpy()
    labs = store["labels"][y]
    row = dict(base46=rev["baseline_$M"], static=rev["static_$M"],
               eti=rev["reform_post_$M"] - rev["baseline_$M"] - rev["static_$M"] ,
               post=rev["behavioral_$M"], credit=credit["total_credit_savings_$M"])
    row["pte"] = diag["pte_revenue_loss_$M"]
    row["total"] = row["post"] + row["credit"]
    row["act24"] = rev["reform_$M"]
    row["tail_static"] = float(((ts - tb) * w)[syn].sum() / 1e6)
    row["pums_static"] = float(((ts - tb) * w)[~syn].sum() / 1e6)
    row["tail_base46"] = float((tb * w)[syn].sum() / 1e6)
    row["pums_base46"] = float((tb * w)[~syn].sum() / 1e6)
    row["classes"] = {c: dict(base46=float((tb * w)[labs == c].sum() / 1e6),
                              static=float(((ts - tb) * w)[labs == c].sum() / 1e6),
                              n=float(w[labs == c].sum()),
                              gains=float((proj_a["income"].to_numpy(float) * proj_a["synthetic_cg_share"].fillna(0).to_numpy(float) * w)[labs == c].sum() / 1e6),
                              income=float((proj_a["income"].to_numpy(float) * w)[labs == c].sum() / 1e6))
                      for c in sorted(set(labs))}
    row["factors"] = factors[y]
    out[y] = row
    print(y, {k: round(v, 2) for k, v in row.items() if isinstance(v, float)}, flush=True)
json.dump(dict(out=out, tail_k=tail_k), open(f"/tmp/gs/{name}.json", "w"), indent=1, default=float)
if save:
    pickle.dump(store, open(f"/tmp/gs/{name}.pkl", "wb"))
print("5yr total", sum(out[y]["total"] for y in TARGET_YEARS))
