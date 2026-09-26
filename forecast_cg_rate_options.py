"""Capital-gains rate options on top of Act 24 (SB 3125 CD2) — revenue + distribution.

Usage:
  python forecast_cg_rate_options.py              # project TY2026-2031, score, plot
  python forecast_cg_rate_options.py --plot-only  # redraw figures from saved CSVs

Question
--------
Act 24 (SLH 2026) added a 13% bracket above $1M (joint) / $750K (HoH) /
$500K (single) and raised the $350K-$1M rates, but left the 7.25% alternative
tax on net capital gains (HRS §235-51(f)) untouched. The gap between the top
rate on wages and the rate on capital gains therefore widened from 3.75 to
5.75 points. If the 2027 Legislature closes it, what does each option raise?

  - "cap9":     raise the alternative-tax rate from 7.25% to 9%
  - "ordinary": tax net capital gains at the ordinary bracket rates
                (repeal the alternative tax)

These are the two options in Hawaii Appleseed's Nov 2025 blog post, whose ITEP
figures ($44M / $85M, TY2026, residents only) were scored on Act 46 brackets.
TY2026 is re-scored here on Act 46 as a check against those figures, and every
Act 24 year is also scored on Act 46 brackets to isolate what Act 24 changed.

Method
------
Population: the same calibrated tax-unit base, $1M+ synthesis and CBO-aged
MID projection as forecast_act24_vs_pre_act46.py.

Capital-gains base: the model's own CG shares come from IRS SOI "net capital
gain (less loss)", which includes short-term gains (already taxed at ordinary
rates) and, above $1M, national tier shares. Against DOTAX that puts ~30% too
much at the top and too little in the $200K-$400K range. So the base is
re-anchored to DOTAX Table 21 (TY2022): resident net long-term capital gains
eligible for the alternative rate, by Hawaii AGI class, grown to the tax year
with the Hawaii-adjusted CBO capital-gains factor the projection itself uses.
Classes map onto the projected population by weighted income rank (DOTAX Table
A-8 return counts), so income growth does not push gains across fixed nominal
class lines. The model's CG shares set the distribution within each class,
except that TOP_SHARE_1M of the $400K+ class goes to $1M+ filers (see
_TOP_SHARE_NOTE). Gains below the $100K class (3% of the total) are left
unallocated: rates there are under 7.25%, so no option changes their tax.

Tax: the statutory alternative tax rather than the min(bracket, 7.25% x gain)
shortcut in TaxCalculator — bracket tax on max(TI - NCG, TI taxed below the cap
rate) plus the cap rate on the rest, when that is lower than the regular tax.

Behavioral response: realized gains fall by exp(-BETA x the rise in the state
marginal rate on gains). The federal rate is unchanged and state tax is
nondeductible at the margin for the affected filers (the SALT cap binds), so
the state rise is the rise in the combined rate. BETA = 2 (2% per point) is an
elasticity of ~0.6 with respect to the ~31% combined top rate (23.8% federal +
7.25%), mid-range of the long-run estimates from state tax changes (roughly
0.5-0.8). Static results are reported alongside.

Nonresidents: excluded from the headline, as in the ITEP figures. The add-on
applies residents' revenue per dollar of gains, by AGI class, to DOTAX's
nonresident gains — in a typical year (TY2018-2022 pooled nonresident/resident
ratio) and at the TY2022 ratio, when composite returns spiked.

Requires data/artifacts/sb3125_calibrated_base.pkl (run
forecast_sb3125_enhanced.py --cd 2 first). A full run takes ~25 minutes.

Outputs (runs/cg_rate_options/):
  revenue_by_year.csv       $M by year x law x option x top-share variant
  distribution_ty2027_act24.csv  seven ITEP-style household groups (and
                                 _ty2026_act46, like-for-like with the blog)
  nonresident_addon.csv     nonresident add-on by year x option
  anchor_check.csv          model vs DOTAX-anchored gains by class and year
  fig1_revenue_ty2027.png, fig2_distribution_ty2027.png,
  fig3_act24_effect_ty2027.png, fig4_revenue_by_year.png
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
logging.disable(logging.WARNING)

import numpy as np
import pandas as pd

REPO = Path(__file__).parent
OUT_DIR = REPO / "runs" / "cg_rate_options"

MID_ALPHA = 1.5
MID_TOP_PREMIUM = 0.010

YEARS = [2026, 2027, 2028, 2029, 2030, 2031]
DIST_YEAR = 2027          # the charted year: a 2027 bill's first tax year
DIST_YEARS = (2026, DIST_YEAR)   # 2026 = like-for-like with the blog's Figure 2

CAP_CURRENT = 0.0725                       # HRS §235-51(f)
OPTIONS = {"cap9": 0.09, "ordinary": None}  # None = no alternative tax
BETA = 2.0                                 # realization semi-elasticity, per unit of rate

TOP_SHARE_1M = 0.80
TOP_SHARE_VARIANTS = {"central": TOP_SHARE_1M, "low": 0.70, "model": None}
_TOP_SHARE_NOTE = """
DOTAX TY2022: the $1M+ class (1,824 returns) owed $662.6M before credits
(Table A-8) on roughly $6.75B of taxable income (Table A-1's $400K+ class, less
the $400K-$1M classes at their average AGI). Under the 2018 schedule that is
~$725M at bracket rates, so the 3.75-point cap saved ~$62M, i.e. ~$1.66B of
eligible gains — about 80% of the class's $2.21B (Table 21). The same arithmetic
on the $400K-$1M classes gives ~$0.36B. The model's national-tier shares put
~92% above $1M; 70% brackets the downside.
"""

# ITEP figures in the Nov 2025 blog post (TY2026, residents, Act 46 brackets).
ITEP_TY2026_M = {"cap9": 44.0, "ordinary": 85.0}

# DOTAX "Hawaiʻi Individual Income Tax Statistics" — "Income Eligible for the
# Tax Rate on Net Long-Term Capital Gains by Hawaiʻi AGI Class" (Table 21 in the
# TY2021-2022 editions, 22 in TY2019-2020, 23 in TY2018). $M.
_CLASSES = ["lt100", "100_150", "150_200", "200_300", "300_400", "400p"]
DOTAX_NLTCG_RES = {
    2018: [119.384, 138.876, 130.851, 245.270, 152.521, 2069.080],
    2019: [97.254, 118.064, 115.573, 195.801, 147.801, 3066.801],
    2020: [99.694, 126.950, 127.985, 209.723, 166.122, 2675.089],
    2021: [166.188, 210.106, 216.483, 379.069, 299.145, 4217.475],
    2022: [88.198, 112.025, 126.019, 241.953, 216.944, 2210.277],
}
# Nonresidents; composite returns folded into the top class.
DOTAX_NLTCG_NONRES = {
    2018: [28.527, 30.638, 30.760, 51.728, 45.280, 402.242 + 11.727],
    2019: [27.891, 31.872, 29.895, 68.824, 42.005, 355.744 + 11.248],
    2020: [37.284, 25.539, 23.598, 50.837, 37.860, 333.100 + 2.645],
    2021: [60.028, 52.293, 52.528, 110.191, 96.831, 906.710 + 12.951],
    2022: [30.754, 39.167, 41.897, 96.572, 108.494, 900.309 + 208.326],
}
# DOTAX Table A-8 TY2022 resident returns by the same classes (the $100K-
# class includes loss returns). Used to map classes onto income ranks.
DOTAX_RETURNS_2022 = {
    "100_150": 62_065, "150_200": 27_976, "200_300": 18_937, "300_400": 6_076,
    "400_1m": 2_926 + 2_991 + 1_134, "1mp": 1_824,
}
DOTAX_TOTAL_RETURNS_2022 = 635_117

# Chart palette: the navy / slate of the blog's figures, sampled from its PNGs.
NAVY = "#2c304b"
SLATE = "#5b6f94"
INK = "#1a1a1a"
GRID = "#d4d4d4"


# ─────────────────────────────────────────────────────────────────────────────
# Population
# ─────────────────────────────────────────────────────────────────────────────

def build_base() -> pd.DataFrame:
    """Calibrated base + $1M+ synthesis, identical to forecast_act24_vs_pre_act46.py."""
    from tax_modeler.artifacts import load_calibrated_base
    from tax_modeler.calibration.cg_imputation import impute_capital_gains_from_soi
    from tax_modeler.pipeline import compute_base_tax, enrich_for_credits
    from tax_modeler.scenarios.top_income_synthesis import (
        redistribute_mid_high_incomes,
        rescale_synthetic_tail_to_tax_target,
        synthesize_top_filers,
    )

    from _forecast_common import CALIBRATED_PKL

    if not CALIBRATED_PKL.exists():
        print(f"ERROR: {CALIBRATED_PKL} not found. Run forecast_sb3125_enhanced.py --cd 2 first.")
        sys.exit(1)
    base, cal_ded_params, cal_meta = load_calibrated_base(CALIBRATED_PKL)
    cal_tax_year = int(cal_meta.get("tax_year", 2023))
    units = redistribute_mid_high_incomes(base, pareto_alpha=MID_ALPHA)
    units = synthesize_top_filers(units, pareto_alpha=MID_ALPHA)
    units = enrich_for_credits(units)
    units = impute_capital_gains_from_soi(units)
    units = compute_base_tax(units, deduction_params=cal_ded_params, tax_year=cal_tax_year)
    units, tail_k = rescale_synthetic_tail_to_tax_target(units)
    units = compute_base_tax(units, deduction_params=cal_ded_params, tax_year=cal_tax_year)
    print(f"  tail_k={tail_k:.4f}", flush=True)
    return units


def project(units: pd.DataFrame, yr: int) -> pd.DataFrame:
    """MID projection to *yr* with target-year itemized deductions (weight > 0 rows only)."""
    from tax_modeler.adjustments.itemized_deductions import scale_deduction_params_for_target_year
    from tax_modeler.calibration.year_recalibrator import project_and_recalibrate
    from tax_modeler.pipeline import compute_base_tax

    projected, _ = project_and_recalibrate(
        units,
        target_year=yr,
        use_forward_targets=True,
        use_soi_anchor=True,
        soi_year=2022,
        hawaii_capgain_adjustment=0.95,
        use_cbo_aging=True,
        cbo_vintage="2025-01",
        top_premium_pct=MID_TOP_PREMIUM,
        top_bracket_differential=0.025,
        method="ensemble",
    )
    ded = scale_deduction_params_for_target_year(yr, geoid="15003")
    projected = compute_base_tax(projected.copy(), tax_year=yr, deduction_params=ded)
    return projected[projected["weight"] > 0.01].reset_index(drop=True)


def cg_growth(yr: int) -> float:
    """TY2022 -> *yr* capital-gains growth: CBO Jan 2025 x the Hawaii CAGR ratio."""
    from tax_modeler.calibration.cbo_aging import (
        DEFAULT_HAWAII_FACTORS,
        load_cbo_rates,
        net_growth_factor,
    )
    rates = load_cbo_rates("2025-01")
    return net_growth_factor(rates.factor("capital_gains", yr),
                             DEFAULT_HAWAII_FACTORS["capital_gains"])


# ─────────────────────────────────────────────────────────────────────────────
# Capital-gains base anchored to DOTAX Table 21
# ─────────────────────────────────────────────────────────────────────────────

def rank_classes(df: pd.DataFrame) -> np.ndarray:
    """DOTAX AGI class of each unit by weighted income rank (midpoint rule).

    The $400K+ class is then split at $1M of target-year income: the synthetic
    $1M+ tiers vs everyone else in the class.
    """
    inc = df["income"].to_numpy(float)
    w = df["weight"].to_numpy(float)
    order = np.argsort(-inc, kind="stable")
    ws = w[order]
    cum_mid = (np.cumsum(ws) - ws / 2) / w.sum()
    top_down = [("400p", DOTAX_RETURNS_2022["400_1m"] + DOTAX_RETURNS_2022["1mp"]),
                ("300_400", DOTAX_RETURNS_2022["300_400"]),
                ("200_300", DOTAX_RETURNS_2022["200_300"]),
                ("150_200", DOTAX_RETURNS_2022["150_200"]),
                ("100_150", DOTAX_RETURNS_2022["100_150"])]
    edges = np.cumsum([n for _, n in top_down]) / DOTAX_TOTAL_RETURNS_2022
    lab_sorted = np.select([cum_mid <= e for e in edges], [c for c, _ in top_down],
                           default="lt100")
    labels = np.empty(len(df), dtype=object)
    labels[order] = lab_sorted
    labels[(labels == "400p") & (inc >= 1_000_000)] = "1mp"
    labels[labels == "400p"] = "400_1m"
    return labels


def anchor_nltcg(df: pd.DataFrame, yr: int, top_share_1m: float | None):
    """Per-unit net long-term capital gain eligible for §235-51(f), $.

    Returns (nltcg, labels, anchor_rows).
    """
    inc = df["income"].to_numpy(float)
    w = df["weight"].to_numpy(float)
    model_cg = inc * df["synthetic_cg_share"].fillna(0.0).to_numpy(float)
    labels = rank_classes(df)
    g = cg_growth(yr)
    target = {c: v * g * 1e6 for c, v in zip(_CLASSES, DOTAX_NLTCG_RES[2022], strict=True)}

    groups = {c: [c] for c in ["100_150", "150_200", "200_300", "300_400"]}
    if top_share_1m is None:
        groups["400p"] = ["400_1m", "1mp"]
    else:
        groups["1mp"] = ["1mp"]
        groups["400_1m"] = ["400_1m"]
        target["1mp"] = target["400p"] * top_share_1m
        target["400_1m"] = target["400p"] * (1 - top_share_1m)

    nltcg = np.zeros(len(df))
    rows = []
    for grp, labs in groups.items():
        m = np.isin(labels, labs)
        have = float((model_cg[m] * w[m]).sum())
        k = target[grp] / have if have > 0 else 0.0
        nltcg[m] = model_cg[m] * k
        rows.append({"tax_year": yr, "group": grp, "model_cg_M": have / 1e6,
                     "dotax_target_M": target[grp] / 1e6, "scale": k})
    return np.clip(nltcg, 0.0, np.maximum(inc, 0.0)), labels, rows


# ─────────────────────────────────────────────────────────────────────────────
# Tax under §235-51(f) with a given alternative-tax rate
# ─────────────────────────────────────────────────────────────────────────────

class Scorer:
    """Pre-credit Hawaii tax for one population under one bracket system."""

    def __init__(self, df: pd.DataFrame, cfg, calc):
        from tax_modeler.config.tax_system_config import itemized_deductions
        from tax_modeler.liability.hawaii import exemption_counts

        n = len(df)
        self.cfg = cfg
        self.status = df["filing_status"].to_numpy()
        # Same exemption count and deduction rules as TaxCalculator.unit_liabilities.
        ex = np.nan_to_num(exemption_counts(df), nan=1.0)
        sd = np.empty(n)
        for fs in np.unique(self.status):
            sd[self.status == fs] = calc.get_standard_deduction(cfg.standard_deduction_year, fs)
        itemized = itemized_deductions(df)
        if itemized is None:
            itemized = np.zeros(n)
        self.subtract = np.maximum(sd, itemized) + ex * cfg.personal_exemption
        self.sched = {fs: calc._bracket_schedule(cfg, fs) for fs in np.unique(self.status)}

    @staticmethod
    def _bracket(ti, floors, rates, cum):
        idx = np.clip(np.searchsorted(floors, ti, side="right") - 1, 0, len(floors) - 1)
        return cum[idx] + (ti - floors[idx]) * rates[idx], rates[idx]

    def taxable(self, income):
        return np.maximum(0.0, income - self.subtract)

    def tax(self, income, nltcg, cap):
        """Regular tax, or the §235-51(f) alternative tax at rate *cap* if lower."""
        ti = self.taxable(income)
        out = np.zeros(len(ti))
        for fs, (floors, rates, cum) in self.sched.items():
            m = self.status == fs
            regular, _ = self._bracket(ti[m], floors, rates, cum)
            if cap is None:
                out[m] = regular
                continue
            # "taxable income taxed at a rate below" the cap: the floor of the
            # first bracket whose rate reaches it.
            at_or_above = rates >= cap - 1e-12
            below_cap_top = floors[np.argmax(at_or_above)] if at_or_above.any() else np.inf
            ncg = np.clip(nltcg[m], 0.0, ti[m])
            base = np.maximum(ti[m] - ncg, np.minimum(ti[m], below_cap_top))
            alt = self._bracket(base, floors, rates, cum)[0] + cap * (ti[m] - base)
            out[m] = np.minimum(regular, alt)
        return out

    def marginal_rate(self, income):
        ti = self.taxable(income)
        out = np.zeros(len(ti))
        for fs, (floors, rates, cum) in self.sched.items():
            m = self.status == fs
            out[m] = self._bracket(ti[m], floors, rates, cum)[1]
        return out


def score_option(sc: Scorer, income, nltcg, cap_new, yr, *, behavioral: bool):
    """Per-unit change in pre-credit tax from moving the cap to *cap_new*."""
    base_tax = sc.tax(income, nltcg, CAP_CURRENT)
    if not behavioral:
        return sc.tax(income, nltcg, cap_new) - base_tax
    mr = sc.marginal_rate(income)
    state0 = np.minimum(mr, CAP_CURRENT)
    state1 = mr if cap_new is None else np.minimum(mr, cap_new)
    d_tau = np.maximum(0.0, state1 - state0)
    kept = nltcg * np.exp(-BETA * d_tau)
    income1 = income - (nltcg - kept)
    return sc.tax(income1, kept, cap_new) - base_tax


# ─────────────────────────────────────────────────────────────────────────────
# Distribution: ITEP-style household groups
# ─────────────────────────────────────────────────────────────────────────────

_GROUPS = [("Lowest 20%", 0.00, 0.20), ("Second 20%", 0.20, 0.40),
           ("Middle 20%", 0.40, 0.60), ("Fourth 20%", 0.60, 0.80),
           ("Next 15%", 0.80, 0.95), ("Next 4%", 0.95, 0.99), ("Top 1%", 0.99, 1.00)]


def distribution(df: pd.DataFrame, changes: dict[str, np.ndarray]) -> pd.DataFrame:
    """Average change per household by ITEP-style group.

    Households are ranked on summed total cash income (TCI) and cut at
    percentiles of the first filer's weight, as compute_quintile_breaks does
    for the repo's ITEP-anchored quintiles. As in generate_quintile_report,
    dollar totals use the calibrated filer weight and per-household averages
    divide by the PUMS household weight.
    """
    hh = df["hh_id"].to_numpy()
    col = "total_cash_income" if "total_cash_income" in df.columns else "income"
    frame = pd.DataFrame({"hh_id": hh, "tci": df[col].to_numpy(float),
                          "fw": df["weight"].to_numpy(float),
                          "hw": df["hh_weight"].to_numpy(float)})
    for k, v in changes.items():
        frame[k] = v * frame["fw"]            # weighted $ per unit
        frame[f"{k}_raw"] = v                 # the unit's own change
    agg = {"tci": "sum", "fw": "first", "hw": "first", **{k: "sum" for k in changes},
           **{f"{k}_raw": "sum" for k in changes}}
    h = frame.groupby("hh_id").agg(agg).sort_values("tci").reset_index()
    cum = (h["fw"].cumsum() - h["fw"] / 2) / h["fw"].sum()
    rows = []
    for name, lo, hi in _GROUPS:
        m = (cum > lo) & (cum <= hi) if lo > 0 else (cum <= hi)
        g = h[m]
        row = {"group": name, "tci_lo": g["tci"].min(), "tci_hi": g["tci"].max(),
               "households": g["hw"].sum()}
        for k in changes:
            row[f"avg_{k}"] = g[k].sum() / g["hw"].sum()
            row[f"total_{k}_M"] = g[k].sum() / 1e6
            row[f"pct_hh_up_{k}"] = g.loc[g[f"{k}_raw"] >= 1.0, "hw"].sum() / g["hw"].sum() * 100
        rows.append(row)
    out = pd.DataFrame(rows)
    for k in changes:
        out[f"share_{k}"] = out[f"total_{k}_M"] / out[f"total_{k}_M"].sum()
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Driver
# ─────────────────────────────────────────────────────────────────────────────

def _class_of(labels: np.ndarray) -> np.ndarray:
    return np.where(np.isin(labels, ["400_1m", "1mp"]), "400p", labels)


def _nonresident_addon(yr, opt, labels, nltcg, w, changes) -> list[dict]:
    """Residents' revenue per $ of gains, by class, applied to DOTAX nonresident gains."""
    cls = _class_of(labels)
    g = cg_growth(yr)
    nr22 = dict(zip(_CLASSES, DOTAX_NLTCG_NONRES[2022], strict=True))
    res22 = dict(zip(_CLASSES, DOTAX_NLTCG_RES[2022], strict=True))
    pooled = {c: sum(DOTAX_NLTCG_NONRES[y][i] for y in DOTAX_NLTCG_NONRES)
              / sum(DOTAX_NLTCG_RES[y][i] for y in DOTAX_NLTCG_RES)
              for i, c in enumerate(_CLASSES)}
    rows = []
    for kind, arr in changes.items():
        per_dollar = {}
        for c in _CLASSES[1:]:
            m = cls == c
            base_amt = (nltcg[m] * w[m]).sum()
            per_dollar[c] = (arr[m] * w[m]).sum() / base_amt if base_amt else 0.0
        rows.append({
            "tax_year": yr, "option": opt, "kind": kind,
            "typical_year_M": sum(per_dollar[c] * pooled[c] * res22[c] * g for c in per_dollar),
            "ty2022_level_M": sum(per_dollar[c] * nr22[c] * g for c in per_dollar),
        })
    return rows


def run() -> None:
    from tax_modeler.config.tax_system_config import TaxCalculator
    from tax_modeler.config.tax_system_config import TaxSystemRegistry as R

    wall = time.perf_counter()
    print("Building calibrated base...", flush=True)
    units = build_base()
    calc = TaxCalculator()

    rev_rows, anchor_rows, nr_rows, dists = [], [], [], {}
    for yr in YEARS:
        t0 = time.perf_counter()
        print(f"  TY {yr}: projecting...", flush=True)
        df = project(units, yr)
        income = df["income"].to_numpy(float)
        w = df["weight"].to_numpy(float)
        laws = {"act46": R.get_act46_system(yr)}
        if yr >= 2027:
            laws["act24"] = R.get_sb3125_cd2_system(yr)
        headline = "act24" if yr >= 2027 else "act46"   # the law in force that year
        scorers = {law: Scorer(df, cfg, calc) for law, cfg in laws.items()}

        dist_changes = {}
        for variant, share in TOP_SHARE_VARIANTS.items():
            nltcg, labels, rows = anchor_nltcg(df, yr, share)
            if variant == "central":
                anchor_rows += rows
            for law, sc in scorers.items():
                for opt, cap in OPTIONS.items():
                    stat = score_option(sc, income, nltcg, cap, yr, behavioral=False)
                    beh = score_option(sc, income, nltcg, cap, yr, behavioral=True)
                    rev_rows.append({
                        "tax_year": yr, "law": law, "option": opt, "top_share": variant,
                        "static_M": float((stat * w).sum() / 1e6),
                        "behavioral_M": float((beh * w).sum() / 1e6),
                        "nltcg_M": float((nltcg * w).sum() / 1e6),
                    })
                    if variant == "central" and law == headline:
                        nr_rows += _nonresident_addon(
                            yr, opt, labels, nltcg, w, {"static": stat, "behavioral": beh})
                        if yr in DIST_YEARS:
                            dist_changes[f"{opt}_static"] = stat
                            dist_changes[f"{opt}_behavioral"] = beh
        if dist_changes:
            dists[(yr, headline)] = distribution(df, dist_changes)
        print(f"    done in {time.perf_counter() - t0:.0f}s", flush=True)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rev = pd.DataFrame(rev_rows)
    rev.to_csv(OUT_DIR / "revenue_by_year.csv", index=False)
    pd.DataFrame(anchor_rows).to_csv(OUT_DIR / "anchor_check.csv", index=False)
    nr = pd.DataFrame(nr_rows)
    nr.to_csv(OUT_DIR / "nonresident_addon.csv", index=False)
    for (yr, law), frame in dists.items():
        frame.to_csv(OUT_DIR / f"distribution_ty{yr}_{law}.csv", index=False)
    _print_summary(rev, dists, nr)
    plot_figures()

    from tax_modeler.runs import write_run_manifest

    from _forecast_common import cache_provenance
    write_run_manifest(
        OUT_DIR, script="forecast_cg_rate_options.py",
        params={"years": YEARS, "dist_years": DIST_YEARS, "beta": BETA,
                "top_share_variants": TOP_SHARE_VARIANTS, "cap_current": CAP_CURRENT,
                "options": dict(OPTIONS), "alpha": MID_ALPHA,
                "top_premium": MID_TOP_PREMIUM, "cbo_vintage": "2025-01"},
        inputs={"tax_unit_cache": cache_provenance(),
                "dotax": "Hawaii Individual Income Tax Statistics TY2018-2022, Tables 21/A-1/A-8"},
    )
    print(f"\nSaved to {OUT_DIR}/  ({time.perf_counter() - wall:.0f}s)", flush=True)


def _print_summary(rev: pd.DataFrame, dists: dict, nr: pd.DataFrame) -> None:
    pd.set_option("display.width", 200)
    c = rev[rev["top_share"] == "central"]
    print("\n" + "=" * 96)
    print("Capital-gains options — resident revenue, $M (central top share; static / behavioral)")
    print("=" * 96)
    for law in ["act46", "act24"]:
        sub = c[c["law"] == law]
        if sub.empty:
            continue
        print(f"\n{law.upper()} brackets")
        for yr, g in sub.groupby("tax_year"):
            cells = "  ".join(
                f"{opt:9s} {r.static_M:6.1f} / {r.behavioral_M:6.1f}"
                for opt, r in g.set_index("option").iterrows())
            print(f"  TY{yr}  base ${g['nltcg_M'].iloc[0]:,.0f}M   {cells}")
    ty26 = c[(c["tax_year"] == 2026) & (c["law"] == "act46")].set_index("option")
    print("\nTY2026 check vs ITEP (blog):",
          ", ".join(f"{o} static {ty26.loc[o, 'static_M']:.0f} / behavioral "
                    f"{ty26.loc[o, 'behavioral_M']:.0f} vs ITEP {ITEP_TY2026_M[o]:.0f}"
                    for o in OPTIONS))
    print("\nTop-share sensitivity, TY2027 Act 24 (behavioral):")
    s = rev[(rev["tax_year"] == 2027) & (rev["law"] == "act24")]
    print(s.pivot(index="top_share", columns="option", values="behavioral_M").round(1))
    for (yr, law), dist in dists.items():
        print(f"\nDistribution TY{yr} ({law}, ordinary rates):")
        print(dist[["group", "tci_lo", "tci_hi", "households", "avg_ordinary_static",
                    "avg_ordinary_behavioral", "share_ordinary_behavioral"]]
              .round(2).to_string(index=False))
    print("\nNonresident add-on, $M:")
    print(nr.round(1).to_string(index=False))


# ─────────────────────────────────────────────────────────────────────────────
# Figures — the blog's layout and palette
# ─────────────────────────────────────────────────────────────────────────────

def _style():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({
        "font.family": ["Helvetica", "Arial", "DejaVu Sans"],
        "font.size": 17, "axes.edgecolor": GRID, "axes.labelcolor": INK,
        "xtick.color": INK, "ytick.color": INK, "text.color": INK,
        "text.parse_math": False,          # "$31,600–$67,800" is money, not math
    })
    return plt


def _money(v: float) -> str:
    return f"${v:,.0f}"


def _columns(plt, path: Path, categories: list[str], series: list[tuple[str, str, list[float]]],
             *, width: float, step: float, label_fmt, tick_kw=None, legend=True) -> None:
    """Grouped columns in the blog's layout: legend on top, dollar gridlines, 1500x1000.

    *series* is [(legend label, color, values per category)]. Axes, ticks and
    legend are laid out first so the rounded data-ends can be sized in pixels.
    """
    from matplotlib.lines import Line2D
    from matplotlib.patches import PathPatch
    from matplotlib.path import Path as MPath
    from matplotlib.ticker import FuncFormatter, MultipleLocator

    fig, ax = plt.subplots(figsize=(10, 20 / 3), dpi=150)
    fig.patch.set_facecolor("white")
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color("#8a8a8a")
    ax.yaxis.grid(True, color=GRID, linewidth=1.0)
    ax.set_axisbelow(True)
    ax.tick_params(axis="both", length=0, pad=8)

    n_cat, n_ser = len(categories), len(series)
    top = max(max(v) for _, _, v in series)
    ax.set_xlim(-0.6, n_cat - 0.4)
    ax.set_ylim(0, np.ceil(top * 1.12 / step) * step)
    ax.yaxis.set_major_locator(MultipleLocator(step))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: _money(v)))
    ax.set_xticks(range(n_cat))
    ax.set_xticklabels(categories, **(tick_kw or {}))
    if legend and n_ser > 1:
        handles = [Line2D([0], [0], marker="s", color="white", markerfacecolor=c,
                          markersize=16, label=lab) for lab, c, _ in series]
        ax.legend(handles=handles, loc="lower center", bbox_to_anchor=(0.5, 1.0),
                  ncol=n_ser, frameon=False, handletextpad=0.4, columnspacing=1.6)
    fig.tight_layout()
    fig.canvas.draw()

    bbox = ax.get_window_extent()
    (x0, x1), (y0, y1) = ax.get_xlim(), ax.get_ylim()
    px_x, px_y = (x1 - x0) / bbox.width, (y1 - y0) / bbox.height
    gap = 3 * px_x                                 # surface gap between touching columns
    for i, (_, color, values) in enumerate(series):
        for j, v in enumerate(values):
            x = j + (i - (n_ser - 1) / 2) * (width + gap)
            if v > 0:
                rx, ry = min(8 * px_x, width / 2), min(8 * px_y, v)
                lft, rgt = x - width / 2, x + width / 2
                verts = [(lft, 0), (lft, v - ry), (lft, v), (lft + rx, v), (rgt - rx, v),
                         (rgt, v), (rgt, v - ry), (rgt, 0), (lft, 0)]
                codes = [MPath.MOVETO, MPath.LINETO, MPath.CURVE3, MPath.CURVE3,
                         MPath.LINETO, MPath.CURVE3, MPath.CURVE3, MPath.LINETO,
                         MPath.CLOSEPOLY]
                ax.add_patch(PathPatch(MPath(verts, codes), facecolor=color, edgecolor="none"))
            ax.text(x, max(v, 0) + 10 * px_y, label_fmt(v), ha="center", va="bottom",
                    fontsize=16 if n_ser * n_cat <= 8 else 14, color=INK)
    fig.savefig(path, facecolor="white")
    plt.close(fig)


def plot_figures() -> None:
    plt = _style()
    rev = pd.read_csv(OUT_DIR / "revenue_by_year.csv")
    dist = pd.read_csv(OUT_DIR / f"distribution_ty{DIST_YEAR}_act24.csv")
    c = rev[rev["top_share"] == "central"].set_index(["tax_year", "law", "option"])
    val = lambda yr, law, opt: float(c.loc[(yr, law, opt), "behavioral_M"]) * 1e6  # noqa: E731
    names = {"cap9": "9% Rate", "ordinary": "Same Rate As Ordinary Income"}
    colors = {"cap9": NAVY, "ordinary": SLATE}
    to_million = lambda v: _money(round(v, -6))  # noqa: E731

    # Figure 1: the two options, first year under Act 24 brackets.
    _columns(plt, OUT_DIR / "fig1_revenue_ty2027.png", ["Revenue"],
             [(names[o], colors[o], [val(DIST_YEAR, "act24", o)]) for o in OPTIONS],
             width=0.3, step=25e6, label_fmt=to_million)

    # Figure 2: average increase per household under ordinary rates.
    # Cut points midway between adjacent groups, labeled the way ITEP's are.
    cuts = [round((a + b) / 2, -2) for a, b in zip(dist.tci_hi[:-1], dist.tci_lo[1:], strict=True)]
    labels = [f"Below ${cuts[0]:,.0f}", f"${cuts[0]:,.0f}–${cuts[1]:,.0f}"]
    labels += [f"${lo + 1:,.0f}–${hi:,.0f}" for lo, hi in zip(cuts[1:-1], cuts[2:], strict=True)]
    labels.append(f"${cuts[-1] + 1:,.0f} and Above")
    avg = dist["avg_ordinary_behavioral"].tolist()
    _columns(plt, OUT_DIR / "fig2_distribution_ty2027.png", labels,
             [("Same Rate As Ordinary Income", SLATE, avg)],
             width=0.62, step=5000 if max(avg) > 20000 else 2500,
             label_fmt=lambda v: _money(v),
             tick_kw={"rotation": 45, "ha": "right", "rotation_mode": "anchor", "fontsize": 15})

    # Figure 3: what Act 24 changed — the same tax year on old vs new brackets.
    laws = [("act46", "Act 46 rates\n(if Act 24 had not passed)"),
            ("act24", "Act 24 rates\n(current law)")]
    _columns(plt, OUT_DIR / "fig3_act24_effect_ty2027.png", [t for _, t in laws],
             [(names[o], colors[o], [val(DIST_YEAR, law, o) for law, _ in laws]) for o in OPTIONS],
             width=0.34, step=25e6, label_fmt=lambda v: f"${v / 1e6:,.0f}M")

    # Figure 4: the five-year window under Act 24.
    yrs = [y for y in YEARS if y >= 2027]
    _columns(plt, OUT_DIR / "fig4_revenue_by_year.png", [str(y) for y in yrs],
             [(names[o], colors[o], [val(y, "act24", o) for y in yrs]) for o in OPTIONS],
             width=0.36, step=50e6, label_fmt=lambda v: f"${v / 1e6:,.0f}M")
    print(f"Figures written to {OUT_DIR}/", flush=True)


def _parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--plot-only", action="store_true",
                   help="Redraw figures from the CSVs of a previous run.")
    return p.parse_args()


if __name__ == "__main__":
    if _parse_args().plot_only:
        plot_figures()
    else:
        run()
