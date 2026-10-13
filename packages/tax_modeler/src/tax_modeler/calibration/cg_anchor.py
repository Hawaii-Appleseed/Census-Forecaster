"""Capital-gains base anchored to DOTAX Table 21 (resident net long-term gains).

The model's own gains shares come from IRS SOI "net capital gain (less
loss)", which includes short-term gains (already taxed at ordinary rates)
and, above $1M, national tier shares. :func:`anchor_nltcg` rescales them so
each DOTAX Hawaiʻi AGI class holds DOTAX's resident net long-term capital
gain eligible for the HRS §235-51(f) alternative tax — the TY2019-2023 window
mean of the class's share of resident tax before credits on the base edition's
total (``dotax_base.nltcg_anchor_M``; :func:`anchored_nltcg_M`), as the $1M+
class is anchored — grown to the tax year with the income base
(:func:`cg_growth`, ``GAINS_GROWTH_MODEL``), times the anticipatory
realization hook, which the Act 24 rate path leaves at 1.0. Classes map onto a projected population by weighted
income rank (:func:`rank_classes`), so income growth does not push gains
across fixed nominal class lines. Gains below the $100K class are left
unallocated (rates there are under 7.25%).

The anchor sets the gains level and does not touch non-gains income: when a
class's gains are rescaled by k, each unit's income (and AGI, and every income
field derived from gains) moves by exactly ``(k - 1) x gains`` and the share is
recomputed as gains / income (:func:`rescale_gains`). Until 2026-10-12 income
was held fixed, which reclassified ``(1 - k) x gains`` as ordinary income and
made the gains-growth sensitivity an artifact (k falls toward 0.2 under a
gains-growth shift and the leaked ordinary income is taxed at Act 24's top
rates; see SB3125_CD1_FORECAST.md).

Moved verbatim from ``forecast_cg_rate_options.py`` (the capital-gains page),
which imports these names back, so the page and the tax simulator
(``tax_modeler.simulator.gains``) anchor with one implementation.
"""
from __future__ import annotations

import os

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


GAINS_GROWTH_MODELS = ("window", "cbo")
GAINS_GROWTH_MODEL = os.environ.get("CG_GROWTH_MODEL", "window")
"""How the gains anchor is carried from the base edition to a tax year
(``CG_GROWTH_MODEL`` in the environment overrides, for the scorecard's ablations).

``"window"`` (default): the anchor is the window mean of each class's share of
resident tax before credits (``dotax_base.nltcg_anchor_M``), and it holds that
share of the income base: it grows at CBO wages x the Hawaiʻi wage factor, the
base's dominant source. This is the null the realization fit could not beat
(``calibration.cg_realization``: six Table 21 points, leave-one-out).
``"cbo"``: the pre-October-2026 rule, CBO national realizations x the 0.85
Hawaiʻi gains factor on the base edition as printed; kept for comparison.
"""


def cg_growth(yr: int, from_year: int = BASE_YEAR, model: str = GAINS_GROWTH_MODEL) -> float:
    """``from_year`` -> *yr* growth of the gains anchor (``GAINS_GROWTH_MODEL``).

    ``from_year`` is the DOTAX edition the gains come from (default the base);
    the CBO table is relative to 2022, so each is a ratio of two years.
    """
    from tax_modeler.calibration.cbo_aging import (
        default_hawaii_factors,
        hawaii_growth,
        load_cbo_rates,
    )
    if model not in GAINS_GROWTH_MODELS:
        raise ValueError(f"model must be one of {GAINS_GROWTH_MODELS}, not {model!r}")
    rates = load_cbo_rates("2025-01")
    comp = "capital_gains" if model == "cbo" else "wages"
    return hawaii_growth(default_hawaii_factors(), comp, rates, from_year, yr)


def anchored_nltcg_M(year: int = BASE_YEAR) -> list[float]:
    """Resident Table 21 gains by anchor class at ``year``, $M: the window mean
    (``dotax_base.GAINS_WINDOW``) on the base edition, else as printed."""
    return dotax_base.nltcg_anchor_M(year, DOTAX_NLTCG_RES)


# ─────────────────────────────────────────────────────────────────────────────
# Anticipatory realization: a hook that Act 24 leaves a no-op
# ─────────────────────────────────────────────────────────────────────────────

CAP_RATE_CURRENT = 0.0725          # HRS §235-51(f), unchanged by Act 24
ANTICIPATION_BETA = 2.0            # realizations pulled forward per unit of next year's rate rise


def act24_cap_rate_path(years=range(2026, 2032)) -> dict[int, float]:
    """The alternative-tax rate on net long-term gains by tax year under Act 24:
    7.25% throughout (Act 24 changed the brackets, not §235-51(f))."""
    return {int(y): CAP_RATE_CURRENT for y in years}


def anticipatory_realization_factor(year: int, cap_rates: dict[int, float] | None = None,
                                    beta: float = ANTICIPATION_BETA) -> float:
    """Multiplier on ``year``'s gains anchor when the gains rate rises the year
    after: filers realize ahead of the increase, ``exp(beta x (rate_{y+1} - rate_y))``
    for a rise, 1.0 otherwise (no pull-back is modeled for a cut, and the year
    of the rise itself is the capital-gains page's realization response).
    With the Act 24 path (``act24_cap_rate_path``) every factor is 1.0."""
    path = act24_cap_rate_path() if cap_rates is None else cap_rates
    now, nxt = path.get(year), path.get(year + 1)
    if now is None or nxt is None or nxt <= now:
        return 1.0
    return float(np.exp(beta * (nxt - now)))


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


def model_gains(income, share) -> np.ndarray:
    """A unit's gains as the model carries them, ``income x share`` (zero for a
    unit with non-positive income or share)."""
    inc = np.asarray(income, dtype=float)
    sh = np.nan_to_num(np.asarray(share, dtype=float), nan=0.0)
    return np.where((inc > 0) & (sh > 0), inc * sh, 0.0)


def anchor_nltcg(df: pd.DataFrame, yr: int, top_share_1m: float | None):
    """Per-unit net long-term capital gain eligible for §235-51(f), $.

    Returns (nltcg, labels, anchor_rows). ``nltcg`` is ``k x`` the model's
    gains in the unit's class; :func:`apply_anchor_factors` puts the same
    gains on the frame, moving income by the change in gains.
    """
    inc = df["income"].to_numpy(float)
    w = df["weight"].to_numpy(float)
    model_cg = model_gains(inc, df["synthetic_cg_share"])
    labels = rank_classes(df)
    g = cg_growth(yr) * anticipatory_realization_factor(yr)
    target = {c: v * g * 1e6 for c, v in zip(_CLASSES, anchored_nltcg_M(), strict=True)}

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
    return nltcg, labels, rows


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


def rescale_gains(income, share, k) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Rescale each unit's gains by ``k`` and leave everything else alone.

    A unit's gains are ``g = income x share`` (zero for a unit with
    non-positive income or share). The anchor sets the gains level, so
    ``g -> k g`` and the unit's income moves by exactly that change,
    ``income' = income + (k - 1) g``: non-gains income, ``income - g``, is
    untouched, and the share is recomputed as ``k g / income'``. (Holding
    income fixed instead, as this did until 2026-10-12, reclassified
    ``(1 - k) g`` as ordinary income whenever ``k < 1``, which Act 24's higher
    top rates then taxed.)

    Returns ``(income', share', delta)``, ``delta = (k - 1) g``; ``share'``
    is 0 where ``income'`` is not positive.
    """
    inc = np.asarray(income, dtype=float)
    k = np.asarray(k, dtype=float)
    g = model_gains(inc, share)
    delta = (k - 1.0) * g
    new_inc = inc + delta
    new_share = np.where(new_inc > 0, k * g / np.where(new_inc > 0, new_inc, 1.0), 0.0)
    return new_inc, new_share, delta


# Income fields that carry a unit's gains: each moves by the same (k - 1) g as
# ``income`` (see :func:`rescale_gains`). ``investment_income`` and
# ``primary_intp`` hold the gains only on the synthetic $1M+ rows (the
# synthesizer writes them there); on a PUMS row they are interest and
# dividends, which are not gains.
_GAINS_INCOME_COLUMNS = ("agi", "hi_agi", "federal_agi", "synthetic_total_income")
_SYNTHETIC_GAINS_COLUMNS = ("investment_income", "primary_intp")


def apply_anchor_factors(df: pd.DataFrame, factors: dict[str, float],
                         share_col: str = "synthetic_cg_share") -> pd.DataFrame:
    """A copy of ``df`` with its capital gains anchored to the DOTAX base.

    Each unit's class comes from its own weighted income rank
    (:func:`rank_classes`, on the incoming ``income``) and each class's gains
    are scaled by ``k = factors[class]`` (0 below the $100K class). The anchor
    sets the gains level and does not touch non-gains income: ``income`` and
    every income field derived from gains (``agi``, ``hi_agi``,
    ``federal_agi``, ``total_cash_income``, ``synthetic_total_income``, the
    synthetic rows' ``investment_income`` / ``primary_intp``,
    ``cbo_aged_capital_gains``) move by ``(k - 1) x gains``, and the share is
    recomputed as gains / income (:func:`rescale_gains`). Tax columns the
    projection computed are not refreshed here: every scorer re-derives the
    tax from ``income``, the share and the deductions. Not idempotent."""
    labels = rank_classes(df)
    k = np.fromiter((factors.get(c, 0.0) for c in labels), dtype=float, count=len(labels))
    out = df.copy()
    inc, share, delta = rescale_gains(out["income"], out[share_col], k)
    out["income"] = inc
    out[share_col] = share
    for col in _GAINS_INCOME_COLUMNS:
        if col in out.columns:
            out[col] = out[col].to_numpy(float) + delta
    if "total_cash_income" in out.columns:
        out["total_cash_income"] = np.maximum(out["total_cash_income"].to_numpy(float) + delta, 0.0)
    if "is_synthetic_ultra_high" in out.columns:
        syn = out["is_synthetic_ultra_high"].fillna(False).to_numpy(dtype=bool)
        for col in _SYNTHETIC_GAINS_COLUMNS:
            if col in out.columns:
                out[col] = np.where(syn, out[col].to_numpy(float) + delta, out[col].to_numpy(float))
    if "cbo_aged_capital_gains" in out.columns:
        out["cbo_aged_capital_gains"] = out["cbo_aged_capital_gains"].to_numpy(float) * k
    return out
