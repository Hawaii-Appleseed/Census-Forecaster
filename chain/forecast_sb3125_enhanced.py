"""SB 3125 fiscal forecast with behavioral response and growth corrections.

Usage:
  python chain/forecast_sb3125_enhanced.py --cd 1   # SB 3125 CD1 (default)
  python chain/forecast_sb3125_enhanced.py --cd 2   # SB 3125 CD2

Replaces:
  forecast_sb3125_cd1_enhanced.py
  forecast_sb3125_cd2_enhanced.py

Improvements over the static forecast it replaced (analyses/forecast_sb3125.py, retired 2026-11-21):

  1. **ETI behavioral response** — per-filer taxable-income elasticity.
     Each filer's marginal rate is computed under both the Act 46 baseline
     and SB 3125 CD{N} schedules; ETI shrinkage is applied based on the
     actual rate change (not a hard-coded 11% → 13%). This correctly
     captures behavior for $350K–$1M MFJ filers (and equivalents) who
     also face rate increases under the bill, not just $1M+ filers.
     Saez/Slemrod/Giertz range: ETI=0.15 (low) / 0.25 (mid) / 0.40 (high).

  2. **Migration response** — share of $1M+ filers who leave per pp of
     top-rate change (0.001 / 0.0025 / 0.01; US state-tax evidence, see
     BEHAVIORAL_ACCOUNTING_REVIEW.md). Phased in over 5 years from 2027.

     Both responses are scored against Act 46 with nobody responding
     (``behavioral_response.score_with_response``): only SB 3125 is scored
     again on the responded population, so a filer who moves away costs
     their whole Hawaii tax. Until September 27, 2026 Act 46 was re-scored
     on the responded population too, which charged a migrant only the
     rate increase.

  3. **PTE election shift** — Bill creates 4pp incentive for $1M+ pass-through
     income to elect PTE (9%) vs face individual 13%. Reported as a
     deduction from gross bracket revenue.

  4. **Top-income growth premium** — Top earners (>$500K) get an additional
     premium above the median-anchored projection. Anchored to PUMS 2024
     base year (5-year vintage panel inflation-adjusted to 2024 dollars).
     The synthetic $1M+ tail is calibrated to DOTAX's TY2023 $1M+ tax, so
     it is first aged 2023 -> 2024 (Honolulu's observed B19013 growth and
     one year of the scenario's premium; ``age_synthetic_tail``). Until
     September 29, 2026 it entered the projection at its base-year level, and
     its single-step rescale overshot the tax target by ~3.6% (MID).

  5. **Effective deductions plumbed through** — The bracket comparison
     uses each filer's projection-time effective deduction (greater of
     standard and itemized via expected-value Pease-limited Hawaii rules)
     instead of a fixed-amount itemized-premium subtraction. This is the
     same deduction that drives ``hi_tax_liability`` upstream, so the
     comparison is internally consistent. Units whose income the premium or
     the recession shock rescales are re-scored first
     (``refresh_stale_hawaii_tax``); until September 2026 the premium's
     blanked deductions were scored as zero, putting every filer above
     $500K on the standard deduction.

  5b. **Capital gains on the DOTAX-anchored base** — Each DOTAX AGI class's
     gains are scaled to DOTAX's resident net long-term gains
     (``calibration.cg_anchor``; factors from the MID population, applied to
     every scenario: ``act24_population.mid_gains_factors``), and both laws
     are scored with the statutory §235-51(f) alternative tax
     (``act24_population.statute``), as the tax simulator and the
     capital-gains page do. Until September 28, 2026 this script scored the
     model's own gains shares with the stacked shortcut.

  6. **Corporate REEC: §48E vs §25D split** — OBBBA terminated §25D
     (residential) but extended §48E (commercial). Corporate REEC
     baseline now uses the §48E factor (no decay through forecast
     horizon); residential decay still applies to individual REEC.

  7. **REEC refundable / nonrefundable utilization split** — The
     ``reec_eff_share`` parameter is the **nonrefundable** utilization
     rate; refundable claims are always 100% effective. Aggregate
     effective shares are blended per the DOTAX 2023 refundable mix
     (~23% individual, ~89% corporate).

  8. **Recession scenario** — Mild-to-moderate recession with trough
     2027 and gradual recovery through 2031. MID behavioral params.

  9. **Baseline-scaling diagnostic** — Reports both raw and COR-scaled
     bracket delta (microsim baseline ≈ $2.3B; COR FY27 = $3.05B).

CD2-only additions (gated by --cd 2):
  - Round-2 REEC knobs per scenario: pro_rata_elasticity, interpretation,
    dynamic_refundable_share
  - compute_dynamic_agi_eligibility_share: per-year AGI eligibility recomputed
    from projected filer distribution (§235-12.5(a) thresholds are unindexed,
    so nominal income growth erodes them over the forecast horizon)
  - Vintage carryforward simulation (model_carryforward_pool=True)
  - 11 extra diagnostic columns in output CSV

Four integrated scenarios (three behavioral + one recession macro; PTE
capture is 0 in all, Act 58):
    LOW       — eti=0.60, migration=0.01,   alpha=1.7, reec=obbba_severe
    MID       — eti=0.40, migration=0.0025, alpha=1.5, reec=obbba_mid  [no recession]
    HIGH      — eti=0.15, migration=0.001,  alpha=1.4, reec=pre_obbba
    RECESSION — MID behavioral params + moderate recession macro shock
                (−2.0% all-filer income in 2027, −3.5% top-income, partial
                rebound 2028, back to baseline 2029+)

REEC carryforward (§235-12.5(e) 5-year window):
  TY2031 $0 new-claim cap may still be partially offset by carryforward
  utilization from TY2026-2030 nonrefundable claims. Scenario-specific
  estimates: LOW=$4M, MID=$6M, HIGH=$9M, RECESSION=$6M.

Outputs:
  data/artifacts/sb3125_calibrated_base.pkl  (read by every later chain step)
  runs/sb3125_cd{N}_enhanced/  enhanced.csv, fiscal_tidy.csv, quintile.csv,
                               bracket.csv, manifest.json
  /tmp/sb3125_cd{N}_{enhanced,quintile_mid,bracket_mid}_2027_2031.csv  (copies)
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))   # _forecast_common

from _forecast_common import (  # noqa: E402
    CALIBRATED_PKL,
    TARGET_YEARS,
    load_cached_units,
    open_calibrated,
    parse_cd_args,
    save_run,
    silence_noise,
)
from tax_modeler.scenarios.act24_population import MID_ALPHA, MID_TOP_PREMIUM  # noqa: E402
from tax_modeler.scenarios.scenario_params import CREDIT_KNOBS  # noqa: E402

# Three calibrated scenarios. Calibration is anchored to (a) the official
# 5yr fiscal-impact estimate of ~$680M, (b) state-level ETI literature
# (Rauh/Shyu 2024 California 13.3% study), (c) PTE rate 9% (Act 50, Hawaii)
# vs bill's 13% individual top → 4pp arbitrage gap, and (d) REEC nonrefundable
# utilization considerations + Hawaii solar saturation.
# Scenarios (effective deductions are computed automatically as
# max(filing-status SD, hi_itemized_deduction) inside compare_systems).
#
# Round-2 knobs (pro_rata_elasticity, interpretation, dynamic_refundable_share)
# are used only by the CD2 code path; CD1 ignores them.
SCENARIOS = [
    {
        "label":  "LOW",
        "alpha":  1.7,                # steeper top tail (less concentration)
        "behav":  "high",             # strong behavioral response (low revenue gain)
        "top_premium":     0.003,     # +0.3%/yr: MID – 1.0pp (strong outmigration)
        "macro_shock":     None,
        **CREDIT_KNOBS["LOW"].scenario_fields(),   # reec, corp_agi_limit, reec_eff_share, cgec_growth, reec_cf_m
        # CD2 round-2 knobs
        "pro_rata_elasticity":      0.5,    # strong demand response
        "interpretation":           "A",    # conservative: TY2026 cap binds
        "dynamic_refundable_share": True,
    },
    {
        "label":  "MID",
        "alpha":  MID_ALPHA,          # 1.5, shared with every step that scores MID
        "behav":  "mid",              # ETI=0.40, migr=0.0025, pte=0
        "top_premium":     MID_TOP_PREMIUM,  # +1.0%/yr: IRS SOI 1.8pp – 0.8pp Hawaii haircut
        "macro_shock":     None,
        **CREDIT_KNOBS["MID"].scenario_fields(),
        # CD2 round-2 knobs
        "pro_rata_elasticity":      0.3,    # mid demand response
        "interpretation":           "B",
        "dynamic_refundable_share": True,
    },
    {
        "label":  "HIGH",
        "alpha":  1.4,
        "behav":  "low",              # weak behavioral (high revenue gain)
        "top_premium":     0.023,     # +2.3%/yr: MID + 1.0pp (national-rate convergence)
        "macro_shock":     None,
        **CREDIT_KNOBS["HIGH"].scenario_fields(),
        # CD2 round-2 knobs
        "pro_rata_elasticity":      0.15,   # weak demand response
        "interpretation":           "B",
        "dynamic_refundable_share": True,
    },
    {
        # Recession scenario: MID behavioral params + moderate recession macro shock.
        "label":  "RECESSION",
        "alpha":  MID_ALPHA,          # same as MID
        "behav":  "mid",              # MID behavioral: ETI=0.40, migr=0.10, pte=0.70
        "top_premium":     0.013,
        "macro_shock":     "moderate",
        **CREDIT_KNOBS["MID"].scenario_fields(),   # same credits as MID
        # CD2 round-2 knobs
        "pro_rata_elasticity":      0.3,
        "interpretation":           "B",
        "dynamic_refundable_share": True,
    },
]

# Requires the workspace to be installed: `uv sync --all-packages`.

CAPITAL_GAINS_NOTE = ("DOTAX-anchored base (calibration.cg_anchor; MID's factors in every "
                      "scenario), statutory alternative tax (HRS §235-51(f))")


def _scenario(label):
    return next(s for s in SCENARIOS if s["label"] == label)


def scoring_systems(cd, *, statutory):
    """(Act 46, SB 3125 CD{cd}) system getters; ``statutory`` scores capital
    gains with the statutory alternative tax (``act24_population.statute``)."""
    from tax_modeler.config.tax_system_config import TaxSystemRegistry
    from tax_modeler.scenarios.act24_population import statute
    from tax_modeler.scenarios.registry import get_scenario

    act46, bill = TaxSystemRegistry.get_act46_system, get_scenario(f"sb3125_cd{cd}").system
    return (statute(act46), statute(bill)) if statutory else (act46, bill)


def credit_overlay(scenario, year, cd, projected):
    """The REEC/CGEC/TCRA cap overlay for one scenario and tax year.

    CD2 adds the vintage carryforward simulation and the round-2 knobs, with
    AGI eligibility recomputed from the projected filers: the §235-12.5(a)
    thresholds ($175K/$350K) are unindexed, so nominal income growth erodes
    them and fewer filers are eligible each forecast year."""
    from tax_modeler.scenarios.sb3125_cd1_credits import (
        CarryforwardPool,
        compute_credit_overlay,
        compute_dynamic_agi_eligibility_share,
    )

    knobs = {
        "reec_demand_scenario":            scenario["reec"],
        "corp_subject_to_agi_limit":       scenario["corp_agi_limit"],
        "reec_effective_claim_share":      scenario["reec_eff_share"],
        "cgec_annual_growth":              scenario["cgec_growth"],
        "reec_carryforward_utilization_m": scenario.get("reec_cf_m", 0.0),
    }
    if cd != "2":
        return compute_credit_overlay(year, **knobs)
    return compute_credit_overlay(
        year, **knobs,
        carryforward_pool=CarryforwardPool(
            agi_eligibility_by_year={year: compute_dynamic_agi_eligibility_share(projected)},
            pro_rata_elasticity=float(scenario.get("pro_rata_elasticity", 0.0)),
            interpretation=scenario.get("interpretation", "B"),
            dynamic_refundable_share=bool(scenario.get("dynamic_refundable_share", False)),
        ),
    )


def _scenario_worker(scenario_dict, target_years, cd="1", gains_factors=None, shared=False):
    """Top-level worker (must be picklable) for the scenario process pool.

    The pool (``tax_modeler.parallel.process_pool``) forks its workers where
    the platform can, so they start with the parent's imports; elsewhere
    they are spawned, which relies on ``tax_modeler`` being installed in the
    new interpreter (it is when launched under ``uv run`` or after
    ``uv pip install -e``). Either way the worker reads the calibrated base
    from the pickle. ``shared`` builds and projects through the chain's
    population store (``_forecast_common.population_store``), for the
    population other steps build again (MID).
    """
    from _forecast_common import open_calibrated

    return run_one_scenario(open_calibrated(shared=shared), scenario=scenario_dict,
                            target_years=target_years, cd=cd, gains_factors=gains_factors)


def run_one_scenario(base, *, scenario, target_years, cd="1", gains_factors=None):
    """Run a single scenario across all target years and return per-year rows.

    ``base`` is the ``act24_population.CalibratedBase``: re-scoring here uses
    the deduction params and vintage it was calibrated under, or the
    synthetic-tail rescale derives a wrong ``tail_k`` (C3).

    ``gains_factors`` ({year: {DOTAX class: k}}, from
    ``act24_population.mid_gains_factors``) puts each year's projected gains
    on the DOTAX-anchored base; both laws are scored with the statutory
    alternative tax. None (tests, diagnostics) keeps the model's own gains
    base and the stacked shortcut.
    """
    import logging
    import time
    import warnings

    warnings.filterwarnings("ignore")
    logging.disable(logging.WARNING)

    from tax_modeler.config.tax_system_config import TaxCalculator
    from tax_modeler.loaders.pums_loader import PUMS_INCOME_DOLLAR_YEAR
    from tax_modeler.scenarios.behavioral_response import (
        BehavioralParams,
        score_with_response,
        top_rate_path,
    )
    from tax_modeler.scenarios.quintile_analysis import cor_scale_factor_for_year
    from tax_modeler.scenarios.top_income_synthesis import (
        synthetic_tail_aging_factor,
        validate_top_synthesis,
    )

    label  = scenario["label"]
    alpha  = scenario["alpha"]
    reec   = scenario["reec"]
    behav  = scenario["behav"]
    corp_agi = scenario["corp_agi_limit"]
    top_premium = scenario["top_premium"]
    reec_eff = scenario["reec_eff_share"]
    cgec_g   = scenario["cgec_growth"]
    macro_shock = scenario.get("macro_shock")   # None = baseline macro; "moderate" = recession
    reec_cf_m = scenario.get("reec_cf_m", 0.0)          # TY2031 carryforward utilization

    print(f"\n{'='*78}\nSCENARIO {label}: alpha={alpha} reec={reec} behav={behav} "
          f"corp_agi={corp_agi} top_premium={top_premium:+.3f}\n"
          f"  reec_eff_share={reec_eff} cgec_growth={cgec_g:.3f} "
          f"reec_cf_m={reec_cf_m:.1f}M "
          f"macro_shock={macro_shock or 'none'}", end="", flush=True)
    if cd == "2":
        print(f"\n  pro_rata_eta={float(scenario.get('pro_rata_elasticity', 0.0)):.2f} "
              f"interp={scenario.get('interpretation', 'B')} "
              f"dyn_refundable={bool(scenario.get('dynamic_refundable_share', False))}",
              end="", flush=True)
    print(f"\n{'='*78}", flush=True)

    # Synthesize fresh from the calibrated (no-synthesis) base, re-scored with
    # the SAME deduction params the base was calibrated under (C3), the tail
    # calibrated to DOTAX's TY2023 $1M+ tax and aged to the PUMS dollar year
    # with this scenario's premium. Shared with the tax simulator's population
    # (tax_modeler.scenarios.act24_population).
    units, tail_k = base.units(alpha=alpha, top_premium=top_premium)
    v = validate_top_synthesis(units)
    print(f"  Synthesis: {v['filers_1m_plus']:,.0f} filers @ $1M+ "
          f"({100*v['filer_target_ratio']:.1f}%), tail_k={tail_k:.4f} to the TY2023 tax, "
          f"aged x{synthetic_tail_aging_factor(top_premium):.4f} to "
          f"{PUMS_INCOME_DOLLAR_YEAR}: ${v['tax_1m_plus_$M']:,.1f}M tax", flush=True)

    behav_params = BehavioralParams.named(behav)
    print(f"  Behavioral: ETI={behav_params.eti}, migration_elast="
          f"{behav_params.migration_elast}, pte_capture={behav_params.pte_capture}",
          flush=True)

    calc = TaxCalculator()
    # Each year's change in the top rate, from TY2027, when the bill takes
    # effect: migration phases each change in from the year it starts.
    path = top_rate_path(*scoring_systems(cd, statutory=False),
                         range(2027, max(target_years) + 1), calc)
    baseline_system, scenario_system = scoring_systems(cd, statutory=gains_factors is not None)
    rows = []
    for year in target_years:
        t0 = time.perf_counter()
        # 1-2) County B19013 projection with per-filer effective deductions,
        #      the top-income growth premium, the recession shock (RECESSION
        #      only), then re-scoring every unit whose income those changed
        #      (see SB3125_CD1_FORECAST.md, "Scoring-path fixes"); then the
        #      capital gains on the DOTAX-anchored base (MID's factors).
        projected = base.projected(
            units, year=year, top_premium=top_premium, macro_shock=macro_shock,
            gains_factors=None if gains_factors is None else gains_factors[year])

        # 3-5) Static scores, the behavioral response (per-filer ETI +
        #      migration) and the PTE shift. Act 46 is scored once, on the
        #      population before anyone responds; SB 3125 again on the
        #      responded population (score_with_response).
        revenue, _, behav_diag = score_with_response(
            projected, behav_params, target_year=year,
            baseline_cfg=baseline_system(year), scenario_cfg=scenario_system(year),
            calculator=calc, top_rate_path=path,
        )
        baseline_static = revenue["baseline_$M"]
        diff_static = revenue["static_$M"]
        # SB 3125 revenue lost to the response (ETI and migration), against static
        diff_behav = revenue["reform_post_$M"] - baseline_static
        pte_shift = behav_diag["pte_revenue_loss_$M"]
        bracket_delta_after_response = revenue["behavioral_$M"]

        # 6) Credit-cap overlay
        credit = credit_overlay(scenario, year, cd, projected)
        credit_total = credit["total_credit_savings_$M"]

        total = bracket_delta_after_response + credit_total

        # 7) COR-scaled diagnostic: scale bracket delta to match COR baseline
        cor_scale_factor = cor_scale_factor_for_year(year, baseline_static)
        bracket_delta_cor_scaled = bracket_delta_after_response * cor_scale_factor

        row = {
            "scenario":                       label,
            "tax_year":                       year,
            "act46_baseline_$M":              round(baseline_static, 2),
            "bracket_delta_static_$M":        round(diff_static, 2),
            "eti_response_$M":                round(diff_behav - diff_static, 2),
            "pte_shift_$M":                   round(-pte_shift, 2),
            "bracket_delta_post_$M":          round(bracket_delta_after_response, 2),
            "bracket_delta_cor_scaled_$M":    round(bracket_delta_cor_scaled, 2),
            "reec_savings_$M":                credit["reec_savings_$M"],
            "cgec_savings_$M":                credit["cgec_savings_$M"],
            "tcra_savings_$M":                credit["tcra_savings_$M"],
            "credit_total_$M":                credit_total,
            "total_impact_$M":                round(total, 2),
            "filers_1m_post_response":        round(behav_diag["filers_1m_post_response"], 0),
        }
        if cd == "2":
            row.update({
                # Vintage-simulation diagnostics: pre-2027 carryforward dynamics
                "reec_base_state_cost_$M":        credit.get("reec_baseline_state_cost_$M"),
                "reec_scen_state_cost_$M":        credit.get("reec_scenario_state_cost_$M"),
                "reec_scen_new_certs_$M":         credit.get("reec_scenario_new_certs_$M"),
                "reec_scen_refundable_$M":        credit.get("reec_scenario_refundable_$M"),
                "reec_scen_nonref_usage_$M":      credit.get("reec_scenario_nonref_usage_$M"),
                "reec_scen_end_stock_$M":         credit.get("reec_scenario_end_stock_$M"),
                "reec_base_end_stock_$M":         credit.get("reec_baseline_end_stock_$M"),
                # Round-2 diagnostics
                "reec_eligibility_share":         credit.get("reec_eligibility_share"),
                "reec_pro_rata_factor":           credit.get("reec_pro_rata_factor"),
                "reec_suppression_factor":        credit.get("reec_suppression_factor"),
                "reec_ind_refundable_share":      credit.get("reec_ind_refundable_share"),
                "reec_interpretation":            credit.get("reec_interpretation"),
            })
        rows.append(row)
        print(f"  TY {year}: static={diff_static:+.1f}M  "
              f"ETI={diff_behav-diff_static:+.1f}M  "
              f"PTE={-pte_shift:+.1f}M  "
              f"-> bracket={bracket_delta_after_response:+.1f}M  "
              f"credit={credit_total:+.1f}M  "
              f"TOTAL={total:+.1f}M  ({time.perf_counter()-t0:.1f}s)", flush=True)
    return rows


def calibrate(cd):
    """Build, validate and save the calibrated base every later step reads."""
    from tax_modeler.artifacts import save_calibrated_base
    from tax_modeler.base import build_calibrated_base, validate_calibrated_base

    units = load_cached_units(cd, built_by="chain/forecast_sb3125_enhanced.py")

    print("Enriching + base tax + calibrating (one-time)...", flush=True)
    # Calibrates on the same itemized-deduction params the projection uses
    # (the canonical set), so the calibration target and projected tax share
    # one deduction basis. Avoids the standard-only-vs-itemized "double
    # discount" at high incomes that previously made TY 2027 baseline
    # drop ~$349M below the calibration anchor.
    calibrated_base, cal_ded_params = build_calibrated_base(units, tax_year=2023)

    # Validate post-rake AND post-synthesis (top-tier filers added in
    # synthesis fill the $1M+ bin which the IPF rake can't reach via its
    # 1.5x bin-cap). Calibration target is met after synthesis. The tail
    # stays at the TY2023 level here (no aging), like the targets below.
    v = validate_calibrated_base(calibrated_base, cal_ded_params, tax_year=2023)
    print(
        f"  CALIBRATION VALIDATION (post-rake + post-synthesis, itemized on):\n"
        f"    hi_tax_liability:      ${v['hi_tax_liability_M']:>7,.0f}M  (DOTAX Table A8 target: ${v['dotax_before_credits_M']:,.0f}M)\n"
        f"    hi_state_tax (net):    ${v['hi_state_tax_M']:>7,.0f}M  (DOTAX TY{v['dotax_base_year']} after credits: ${v['dotax_after_credits_M']:,.0f}M)",
        flush=True,
    )

    # Save calibrated base to a pickle so each parallel worker can read
    # it independently. Pickle (not parquet) is required because the
    # ``dependents`` column holds Python objects (lists of dicts) that
    # parquet can't round-trip without lossy string conversion.
    # save_calibrated_base embeds the deduction params + provenance metadata
    # so consumers re-score on the SAME deduction basis (C3 fix).
    save_calibrated_base(
        calibrated_base, CALIBRATED_PKL,
        deduction_params=cal_ded_params, tax_year=2023,
        extra_meta={"built_by": "chain/forecast_sb3125_enhanced.py", "cd": cd},
    )


def mid_distribution(base, cd, gains_factors):
    """MID's quintile and bracket tables for every target year:
    ``(quintiles, brackets)``, with quintile boundaries fixed at the base year."""
    import pandas as pd
    from tax_modeler.config.tax_system_config import TaxCalculator
    from tax_modeler.scenarios.quintile_analysis import (
        compute_quintile_breaks,
        cor_scale_factor_for_year,
        generate_quintile_report,
        per_unit_tax,
    )

    mid = _scenario("MID")
    calc = TaxCalculator()
    baseline_system, scenario_system = scoring_systems(cd, statutory=True)

    # Re-synthesize MID (same params as the parallel worker); re-score on
    # the calibration deduction basis (C3 fix — see run_one_scenario).
    # enrich=True adds total_cash_income for TCI quintile binning.
    units, _ = base.units(alpha=mid["alpha"], top_premium=mid["top_premium"], enrich=True)

    # Anchor quintile boundaries to the 2026 base-year income distribution
    # so Q1–Q5 membership is fixed across all projection years.
    breaks = compute_quintile_breaks(units)
    print(f"  2026 quintile AGI breaks (p20/p40/p60/p80): "
          f"${breaks[0]:,.0f} / ${breaks[1]:,.0f} / "
          f"${breaks[2]:,.0f} / ${breaks[3]:,.0f}", flush=True)

    quintile_frames, bracket_frames = [], []
    for yr in TARGET_YEARS:
        projected = base.projected(units, year=yr, top_premium=mid["top_premium"],
                                   gains_factors=gains_factors[yr])
        base_cfg, scen_cfg = baseline_system(yr), scenario_system(yr)
        overlay = credit_overlay(mid, yr, cd, projected)

        # Year-specific COR scale factor from this scenario's Act 46
        # microsim baseline (per-filer aggregated revenue, $M).
        act46_baseline_M = float(
            (per_unit_tax(projected, base_cfg, calc)
             * projected["weight"].to_numpy(dtype=float)).sum() / 1e6
        )
        q_df, b_df, _ = generate_quintile_report(
            projected, base_cfg, scen_cfg, overlay, calc,
            scenario_params=mid,
            quintile_breaks=breaks,
            cor_scale_factor=cor_scale_factor_for_year(yr, act46_baseline_M),
        )
        q_df.insert(0, "tax_year", yr)
        b_df.insert(0, "tax_year", yr)
        quintile_frames.append(q_df)
        bracket_frames.append(b_df)
        print(f"  TY {yr} quintile done", flush=True)

    return (pd.concat(quintile_frames, ignore_index=True),
            pd.concat(bracket_frames, ignore_index=True))


def main(cd):
    import time
    from concurrent.futures import as_completed

    import pandas as pd
    from tax_modeler.parallel import forking, process_pool
    from tax_modeler.runs import tidy_long

    cd_label = f"CD{cd}"
    print(f"SB 3125 {cd_label} enhanced fiscal forecast", flush=True)
    wall_start = time.perf_counter()

    calibrate(cd)

    # Capital gains: DOTAX anchors MID; every scenario rescales its gains
    # with MID's factors (act24_population.mid_gains_factors), as the tax
    # simulator does. The MID populations here are built and projected
    # again by the scenario worker, the pre-Act-46 run and the simulator:
    # build them once, from the pickle every step reads, in the shared store.
    base = open_calibrated()
    mid = _scenario("MID")
    print("Anchoring capital gains to DOTAX on the MID population...", flush=True)
    gains_factors = base.gains_factors(years=TARGET_YEARS, alpha=mid["alpha"],
                                       top_premium=mid["top_premium"])

    # Parallel: run all four scenarios concurrently, each in a worker
    # that reads the calibrated pickle. The workers are forked where the
    # platform allows (tax_modeler.parallel.process_pool), so they start
    # with this process's imports instead of importing everything again.
    n_workers = min(len(SCENARIOS), 4)
    print(f"\nLaunching {n_workers} parallel scenario workers ({cd_label})...", flush=True)
    pool = process_pool(n_workers)
    with forking():
        futures = {
            pool.submit(_scenario_worker, sc, TARGET_YEARS, cd,
                        gains_factors, sc["label"] == "MID"): sc["label"]
            for sc in SCENARIOS
        }

    # The MID quintile analysis needs only the gains factors, so it runs
    # here while the workers score the scenarios, not after them.
    print("\nRunning distributional quintile analysis (MID scenario)...", flush=True)
    all_quintiles, all_brackets = mid_distribution(base, cd, gains_factors)

    all_rows = []
    with pool:
        for fut in as_completed(futures):
            label = futures[fut]
            rows = fut.result()
            all_rows.extend(rows)
            print(f"  ✓ {label} complete ({len(rows)} rows)", flush=True)

    # Sort rows so the CSV stays in scenario × year order
    scen_order = {s["label"]: i for i, s in enumerate(SCENARIOS)}
    all_rows.sort(key=lambda r: (scen_order[r["scenario"]], r["tax_year"]))
    df = pd.DataFrame(all_rows)

    # The /tmp copies are read by analyses/generate_itep_report.py; the run
    # directory is canonical (scripts/build_site.py imports from it).
    out_csv = Path(f"/tmp/sb3125_cd{cd}_enhanced_2027_2031.csv")
    df.to_csv(out_csv, index=False)
    for kind, frame in (("quintile", all_quintiles), ("bracket", all_brackets)):
        tmp = Path(f"/tmp/sb3125_cd{cd}_{kind}_mid_2027_2031.csv")
        frame.to_csv(tmp, index=False)
        print(f"Saved: {tmp}", flush=True)
    run_dir = save_run(
        f"sb3125_cd{cd}_enhanced",
        {"enhanced.csv": df,
         "fiscal_tidy.csv": tidy_long(df, ["scenario", "tax_year"]),
         "quintile.csv": all_quintiles,
         "bracket.csv": all_brackets},
        script=f"chain/forecast_sb3125_enhanced.py --cd {cd}",
        params={"cd": cd, "target_years": TARGET_YEARS, "scenarios": SCENARIOS,
                "capital_gains": CAPITAL_GAINS_NOTE,
                "gains_factors": {str(y): k for y, k in gains_factors.items()}},
    )
    print(f"Saved run: {run_dir}", flush=True)

    _print_summary(df, all_quintiles, cd_label)
    print(f"\nSaved: {out_csv}", flush=True)
    print(f"Total elapsed: {time.perf_counter() - wall_start:.1f}s", flush=True)


def _print_summary(df, all_quintiles, cd_label):
    import pandas as pd

    # TY 2027 quintile summary
    q27 = all_quintiles[all_quintiles["tax_year"] == 2027].copy()
    print("\n" + "=" * 100, flush=True)
    print("DISTRIBUTIONAL ANALYSIS — MID Scenario, TY 2027 "
          "(bracket change + REEC credit loss per household)", flush=True)
    print("=" * 100, flush=True)
    pd.set_option("display.float_format", "{:,.1f}".format)
    pd.set_option("display.max_colwidth", 20)
    display_cols = [
        "quintile", "household_count",
        "avg_per_hh_bracket_change", "avg_per_hh_credit_loss", "avg_per_hh_total_change",
        "total_bracket_$M", "total_credit_loss_$M", "total_change_$M",
        "pct_pay_more", "pct_pay_less",
    ]
    print(q27[display_cols].to_string(index=False), flush=True)

    print("\n" + "=" * 100, flush=True)
    print(f"SB 3125 {cd_label} ENHANCED FISCAL IMPACT  "
          f"($M, vs Act 46 baseline, with behavioral response)", flush=True)
    print("=" * 100, flush=True)

    scen_order = [s["label"] for s in SCENARIOS]  # ["LOW","MID","HIGH","RECESSION"]
    for column, title in (
        ("total_impact_$M", "Total annual fiscal impact ($M, post-behavioral):"),
        ("bracket_delta_post_$M", "Bracket-change impact (post-ETI/migration/PTE) ($M):"),
        ("bracket_delta_cor_scaled_$M",
         "Bracket-change impact, COR-scaled to actual revenue base ($M):"),
        ("credit_total_$M", "Credit-cap impact ($M):"),
    ):
        pv = df.pivot(index="tax_year", columns="scenario", values=column)[scen_order]
        pv.loc["5yr cum"] = pv.sum()
        print(f"\n{title}")
        print(pv.to_string(float_format=lambda x: f"{x:>9,.1f}"), flush=True)

    # Behavioral decomposition for MID at TY 2027
    mid27 = df[(df["scenario"] == "MID") & (df["tax_year"] == 2027)].iloc[0]
    print("\n" + "-" * 100, flush=True)
    print("BEHAVIORAL DECOMPOSITION (MID scenario, TY 2027):", flush=True)
    print("-" * 100, flush=True)
    print(f"  Static bracket delta:                   ${mid27['bracket_delta_static_$M']:>+8.2f}M", flush=True)
    print(f"  Behavioral response (ETI + migration):  ${mid27['eti_response_$M']:>+8.2f}M", flush=True)
    print(f"  PTE election shift to entity tax:       ${mid27['pte_shift_$M']:>+8.2f}M", flush=True)
    print(f"  Post-behavioral bracket delta:          ${mid27['bracket_delta_post_$M']:>+8.2f}M", flush=True)
    cor_diag = (mid27['bracket_delta_cor_scaled_$M'] / mid27['bracket_delta_post_$M']
                if mid27['bracket_delta_post_$M'] else 1.0)
    print(f"  COR-scaled (×{cor_diag:.3f}):                "
          f"${mid27['bracket_delta_cor_scaled_$M']:>+8.2f}M", flush=True)


if __name__ == "__main__":
    silence_noise()
    args = parse_cd_args(__doc__)
    try:
        main(args.cd)
    except Exception as e:
        print(f"\nERROR: {e}", flush=True)
        traceback.print_exc()
        sys.exit(1)
