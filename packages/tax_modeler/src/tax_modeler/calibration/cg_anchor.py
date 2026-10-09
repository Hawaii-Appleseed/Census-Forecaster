"""Capital-gains base anchored to DOTAX Table 21 (resident net long-term gains).

The model's own gains shares come from IRS SOI "net capital gain (less
loss)", which includes short-term gains (already taxed at ordinary rates)
and, above $1M, national tier shares. :func:`anchor_nltcg` rescales them so
each DOTAX Hawaiʻi AGI class holds DOTAX's TY2023 resident net long-term
capital gain eligible for the HRS §235-51(f) alternative tax, grown to the
tax year with the Hawaiʻi-adjusted CBO capital-gains factor
(:func:`cg_growth`). Classes map onto a projected population by weighted
income rank (:func:`rank_classes`), so income growth does not push gains
across fixed nominal class lines. Gains below the $100K class are left
unallocated (rates there are under 7.25%).

Moved verbatim from ``forecast_cg_rate_options.py`` (the capital-gains page),
which imports these names back, so the page and the tax simulator
(``tax_modeler.simulator.gains``) anchor with one implementation.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from tax_modeler.calibration import dotax_base

TOP_SHARE_1M = 0.80
TOP_SHARE_VARIANTS = {"central": TOP_SHARE_1M, "low": 0.70, "model": None}
_TOP_SHARE_NOTE = """
Derivation (DOTAX TY2022): the $1M+ class (1,824 returns) owed $662.6M before credits
(Table A-8) on roughly $6.75B of taxable income (Table A-1's $400K+ class, less
the $400K-$1M classes at their average AGI). Under the 2018 schedule that is
~$725M at bracket rates, so the 3.75-point cap saved ~$62M, i.e. ~$1.66B of
eligible gains — about 80% of the class's $2.21B (Table 21). The same arithmetic
on the $400K-$1M classes gives ~$0.36B. The model's national-tier shares put
~92% above $1M; 70% brackets the downside.
Cross-check (DOTAX TY2023, same arithmetic, approximate): the $1M+ class (1,704
returns) owed $441M on ~$4.7B of taxable income (A-8's 9.4% effective rate), about
$0.5B at bracket rates, so ~$58M saved and ~$1.55B of eligible gains: 76% of
Table 21's $2.04B $400K+ total, 81% net of ~$0.36B for the $400K-$1M classes
(TY2022's figure; TY2023's is not published separately). Inside the 70-80% range,
so the share stands.
"""

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
    2023: dotax_base.nltcg_M(2023),   # PDF edition: published to $0.1M
}
# Nonresidents; composite returns folded into the top class.
DOTAX_NLTCG_NONRES = {
    2018: [28.527, 30.638, 30.760, 51.728, 45.280, 402.242 + 11.727],
    2019: [27.891, 31.872, 29.895, 68.824, 42.005, 355.744 + 11.248],
    2020: [37.284, 25.539, 23.598, 50.837, 37.860, 333.100 + 2.645],
    2021: [60.028, 52.293, 52.528, 110.191, 96.831, 906.710 + 12.951],
    2022: [30.754, 39.167, 41.897, 96.572, 108.494, 900.309 + 208.326],
    2023: dotax_base.nltcg_M(2023, nonresident=True),
}
# DOTAX Table A-8 resident returns by the same classes (the $100K- class
# includes loss returns). Used to map classes onto income ranks.
BASE_YEAR = dotax_base.BASE_YEAR
DOTAX_RETURNS_2022 = dotax_base.cg_class_returns(2022)
DOTAX_TOTAL_RETURNS_2022 = dotax_base.total_returns(2022)
DOTAX_RETURNS = dotax_base.cg_class_returns(BASE_YEAR)
DOTAX_TOTAL_RETURNS = dotax_base.total_returns(BASE_YEAR)


def cg_growth(yr: int, from_year: int = BASE_YEAR) -> float:
    """``from_year`` -> *yr* capital-gains growth: CBO Jan 2025 x the Hawaii CAGR ratio.

    ``from_year`` is the DOTAX edition the gains come from (default the base,
    TY2023); the CBO table is relative to 2022, so it is a ratio of two years.
    """
    from tax_modeler.calibration.cbo_aging import (
        DEFAULT_HAWAII_FACTORS,
        load_cbo_rates,
        net_growth_factor,
    )
    rates = load_cbo_rates("2025-01")
    return net_growth_factor(rates.growth("capital_gains", yr, from_year),
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
    top_down = [("400p", DOTAX_RETURNS["400_1m"] + DOTAX_RETURNS["1mp"]),
                ("300_400", DOTAX_RETURNS["300_400"]),
                ("200_300", DOTAX_RETURNS["200_300"]),
                ("150_200", DOTAX_RETURNS["150_200"]),
                ("100_150", DOTAX_RETURNS["100_150"])]
    edges = np.cumsum([n for _, n in top_down]) / DOTAX_TOTAL_RETURNS
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
    target = {c: v * g * 1e6 for c, v in zip(_CLASSES, DOTAX_NLTCG_RES[BASE_YEAR], strict=True)}

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
# The anchored base as scale factors, for scoring a population on it
# ─────────────────────────────────────────────────────────────────────────────

def anchor_factors(df: pd.DataFrame, yr: int,
                   top_share_1m: float = TOP_SHARE_1M) -> dict[str, float]:
    """Each class's scale factor k (``anchor_nltcg``'s ``scale``) on ``df``,
    keyed by class label; classes with no factor (``lt100``) are left out.

    The Act 24 pipeline computes these on its MID population and applies them
    to every scenario (:func:`apply_anchor_factors`), as the tax simulator
    does (``tax_modeler.simulator.gains``): DOTAX anchors MID, and the other
    scenarios' gains move with their own top incomes."""
    _, _, rows = anchor_nltcg(df, yr, top_share_1m)
    return {r["group"]: float(r["scale"]) for r in rows}


def apply_anchor_factors(df: pd.DataFrame, factors: dict[str, float],
                         share_col: str = "synthetic_cg_share") -> pd.DataFrame:
    """A copy of ``df`` with its gains share rescaled to the anchored base:
    ``min(1, share × k[class])``, with each unit's class from its own weighted
    income rank (:func:`rank_classes`) and k = 0 below the $100K class. The
    same share the tax simulator's kernel and ``anchored_share`` compute."""
    labels = rank_classes(df)
    k = np.fromiter((factors.get(c, 0.0) for c in labels), dtype=float, count=len(labels))
    out = df.copy()
    out[share_col] = np.minimum(1.0, out[share_col].fillna(0.0).to_numpy(float) * k)
    return out
