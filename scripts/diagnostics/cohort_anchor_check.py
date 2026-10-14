"""Check of the per-year gains anchor modes (cohort / pooled / separate), TY2023 base, MID.

Usage: python scripts/diagnostics/cohort_anchor_check.py   (reads data/artifacts/sb3125_calibrated_base.pkl)

Per year TY2027-2031 and per mode: k by anchor class (cohort: base-year cohorts; pooled and
separate: that year's income-rank classes), the gains the anchored frame holds in each CURRENT
class against the separate Table 21 targets (cg_anchor.anchor_nltcg's "separate" rows), and the
$1M+ filers after the behavioral response against the no-anchor projection. Also the base-year
$1M+ tax before credits (the base-year anchor's check: $562.3M against the windowed anchor).
"""
import os, sys, warnings, logging
warnings.filterwarnings("ignore"); logging.disable(logging.WARNING)
import numpy as np
sys.path.insert(0, os.getcwd())
from _forecast_common import CALIBRATED_PKL, TARGET_YEARS
from tax_modeler.artifacts import load_calibrated_base
from tax_modeler.calibration import cg_anchor
from tax_modeler.config.tax_system_config import TaxCalculator, TaxSystemRegistry
from tax_modeler.projection.engine import AgingSpec, TOP_SHARE_DRIFT
from tax_modeler.scenarios import act24_population as ap
from tax_modeler.scenarios.behavioral_response import BehavioralParams, score_with_response, top_rate_path
from tax_modeler.scenarios.top_income_synthesis import validate_top_synthesis

MODES = ("cohort", "pooled", "separate")
base, ded, meta = load_calibrated_base(str(CALIBRATED_PKL))
spec = AgingSpec(engine="cbo", top_share_drift=TOP_SHARE_DRIFT["MID"])
units, tail_k = ap.build_units(base, alpha=1.5, spec=spec, ded_params=ded,
                               cal_tax_year=int(meta.get("tax_year", 2023)))
# the base-year check, unaged (as forecast_sb3125_enhanced.py's calibration validation)
from tax_modeler.pipeline import compute_base_tax
from tax_modeler.scenarios.top_income_synthesis import calibrate_synthetic_tail_to_tax_target, synthesize_top_filers
_score = lambda u: compute_base_tax(u, deduction_params=ded, tax_year=int(meta.get("tax_year", 2023)))  # noqa: E731
_b, _ = calibrate_synthetic_tail_to_tax_target(
    _score(cg_anchor.anchor_tail_gains(_score(synthesize_top_filers(base, pareto_alpha=1.5)))[0]),
    score=_score, scale_gains=False)
print(f"base year: $1M+ tax before credits ${validate_top_synthesis(_b)['tax_1m_plus_$M']:,.1f}M "
      f"(windowed anchor $562.3M)\n")
calc = TaxCalculator()
get_sc = TaxSystemRegistry.get_sb3125_cd2_system
path = top_rate_path(TaxSystemRegistry.get_act46_system, get_sc, range(2027, 2032), calc)
bp = BehavioralParams.named("mid")
CLS = ("100_150", "150_200", "200_300", "300_400", "400_1m", "1mp")


def filers(frame, y):
    _, _, diag = score_with_response(
        frame, bp, target_year=y, baseline_cfg=ap.statute(TaxSystemRegistry.get_act46_system)(y),
        scenario_cfg=ap.statute(get_sc)(y), calculator=calc, top_rate_path=path)
    return diag["filers_1m_post_response"]


for y in TARGET_YEARS:
    proj = ap.project_units(units, year=y, spec=spec)
    w = proj["weight"].to_numpy(float)
    cur = cg_anchor.rank_classes(proj)
    _, _, sep_rows = cg_anchor.anchor_nltcg(proj, y, cg_anchor.TOP_SHARE_1M, mode="separate")
    target = {r["group"]: r["dotax_target_M"] for r in sep_rows}
    print(f"TY{y}: $1M+ filers after response, no anchor {filers(proj, y):,.0f}")
    for m in MODES:
        k = cg_anchor.anchor_factors(proj, y, mode=m)
        out = cg_anchor.apply_anchor_factors(proj, k, mode=m)
        g = cg_anchor.model_gains(out["income"], out["synthetic_cg_share"]) * w
        ks = " ".join(f"{c} {k.get(c, float('nan')):.3f}" for c in CLS)
        gi = " ".join(f"{c} {g[cur == c].sum() / 1e6:,.1f}/{target[c]:,.1f}" for c in ("400_1m", "1mp"))
        print(f"  {m:8s} k: {ks}\n  {'':8s} implied gains by current class / separate target ($M): {gi}"
              f"; $1M+ filers {filers(out, y):,.0f}")
