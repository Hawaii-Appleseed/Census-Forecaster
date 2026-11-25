"""Nonresidents' net long-term capital gains, scored as returns of their own.

Nonresidents (and part-year residents) file Form N-15 and pay Hawaiʻi tax on
Hawaiʻi-source income: mostly gains on Hawaiʻi real property and on interests
in Hawaiʻi businesses. N-15 prorates the deductions and exemptions by the
ratio of Hawaiʻi to worldwide AGI, then runs Hawaiʻi taxable income straight
through the rate schedule, with the §235-51(f) alternative tax on gains. So a
nonresident's gains fill the brackets from the bottom of *Hawaiʻi* income,
however large the filer's income elsewhere.

The records come from two DOTAX tables that share the Hawaiʻi AGI classes
(``dotax_indinc_<year>.json``, TY2018-2023):

- Table 21: nonresident returns with eligible gains, and the gains, by class.
- A-3: taxable nonresident returns, Hawaiʻi AGI, taxable income and tax before
  credits, by class.

Below $400,000 each class becomes two records, its gain returns and the
rest, at the class's mean taxable income (gains up to it). The open
$400,000+ class is a Pareto in Hawaiʻi AGI from $400,000, its shape set by
the class's mean (``K_TOP`` quantile points); gain returns are the same
share of every point, and their gains the share of taxable income that adds
up to the class's gains (``GAIN_PLACEMENTS["central"]``). The variant
``"gains_only"`` gives gain returns no other Hawaiʻi income, so gains start
in the lowest brackets: the lower bound on what a higher rate raises. Each
record is split across filing status by nonresidents' Hawaiʻi AGI (Table
6A). Composite returns (filed for nonresident owners of pass-through
entities) are left out: $3.5M of gains in TY2023, but $208M in TY2022, a
one-off.

:func:`check_a3` scores the base year under the law then in force (the 2018
schedule) and compares tax with A-3 class by class.

Level. Nonresident gains move with sales of Hawaiʻi property by owners who
live elsewhere, not with residents' stock gains: $511-589M a year in
TY2018-2020, $1.2-1.3B in TY2021-2022, $967M in TY2023 (composites out).
Each year's gains, repriced to TY2023 with the FHFA Hawaiʻi house price
index, give three levels (:data:`LEVEL_YEARS`): TY2023 itself (central), the
TY2018-2020 mean (low: pre-pandemic sales volume at today's prices) and the
TY2021-2022 mean (high: the boom's volume). From TY2023 gains grow with the
same Hawaiʻi-adjusted CBO factor as residents' (``cg_anchor.cg_growth``)
and other income with CBO business income.

Behavioral response. A home state credits Hawaiʻi's tax against its own tax
on the same gain, so the part of a rise in Hawaiʻi's rate that stays under
the home state's rate costs the filer nothing and changes no sale; that
revenue still comes to Hawaiʻi, from the other state's treasury. The rest of
the rise draws the residents' response. ``offset_share`` is the share of the
rise that is credited back (:func:`home_state_offset`), from the mailing
addresses of Oʻahu's home sellers of December 2023 to September 2026: about
40%, nearly all of it California's sellers. Oʻahu only: the neighbor islands'
second-home sellers may come from other places.
"""
from __future__ import annotations

from functools import cache
from pathlib import Path

import numpy as np
import pandas as pd

from tax_modeler.calibration import dotax_base
from tax_modeler.calibration.cg_anchor import cg_growth

YEARS = (2018, 2019, 2020, 2021, 2022, 2023)
"""DOTAX editions on file (Table 21 and A-3 parsed). TY2024 is not yet published:
the Department's page (tax.hawaii.gov/stats/a5_1annual/a5_5ind_inc/, updated
October 7, 2026) lists TY2023 as the newest edition."""
BASE_YEAR = 2023
CAP_CURRENT = 0.0725
K_TOP = 400
"""Quantile points in the $400,000+ class."""
TOP_FLOOR = 400_000.0

# Table 6A (TY2023): nonresident Hawaiʻi AGI (positive, $M) by filing status,
# composites out. Separate returns use the single schedule.
_STATUS_AGI = {"married_filing_jointly": 2152, "single": 1108 + 698, "head_of_household": 113}
STATUS_SHARES = {fs: v / sum(_STATUS_AGI.values()) for fs, v in _STATUS_AGI.items()}

GAIN_PLACEMENTS = ("central", "gains_only")

# FHFA all-transactions house price index for Hawaiʻi (FRED HISTHPI), annual
# means of the quarters, as census_forecaster's anchors/fred_hi_hpi.json held
# them in November 2026. Pinned: that file is refreshed monthly, and a revision
# should not move these figures unannounced.
HPI = {2018: 612.53, 2019: 632.053, 2020: 645.197, 2021: 709.783, 2022: 849.69, 2023: 864.095}


def nonresident_gains_M(year: int) -> float:
    """Nonresident gains in TY*year*, composites out, $M (Table 21)."""
    return sum(r["nonres_amount_M"] for r in dotax_base.load_indinc(year)["table_21_nltcg"]["classes"])


def repriced_M(year: int, to: int = BASE_YEAR) -> float:
    """TY*year*'s nonresident gains at TY*to* house prices, $M."""
    return nonresident_gains_M(year) * HPI[to] / HPI[year]


LEVEL_YEARS = {"low": (2018, 2019, 2020), "central": (2023,), "high": (2021, 2022)}


def level_factor(level: str) -> float:
    """The level's gains over TY2023's, both at TY2023 house prices."""
    yrs = LEVEL_YEARS[level]
    return float(np.mean([repriced_M(y) for y in yrs])) / nonresident_gains_M(BASE_YEAR)


# ─────────────────────────────────────────────────────────────────────────────
# Who bears a higher rate: the home state's credit for Hawaiʻi's tax
# ─────────────────────────────────────────────────────────────────────────────

# Top rate on a large long-term gain under each home state's 2026 law, %, that
# the state's credit for another state's tax can offset, for the states above
# Hawaiʻi's 7.25%; every other state is below it (Tax Foundation, "State
# Individual Income Tax Rates and Brackets, 2026", checked November 24, 2026).
# NY is the state alone: New York City's rate is not added, since a mailing
# address does not say who is a city resident. MD is the 6.5% top rate, the 2%
# surtax on gains for federal AGI over $350,000 (2025 Budget Reconciliation and
# Financing Act; it exempts only a primary residence under $1.5M among real
# property) and the 3.2% county rate the most populous counties charge (the
# cap is 3.3%). MN leaves out its 1% tax on net investment income: the credit
# for other states' tax cannot be taken against it. Washington's tax on gains
# exempts real estate, so its sellers count as untaxed at home.
HOME_STATE_RATE = {"CA": 13.3, "MD": 11.7, "NJ": 10.75, "DC": 10.75, "NY": 9.65, "OR": 9.9, "MN": 9.85,
                   "MA": 9.0, "VT": 8.75}
_ORIGIN_FILE = Path(__file__).resolve().parents[1] / "data" / "calibration" / "nonresident_seller_origin_oahu.csv"


@cache
def seller_origin() -> pd.DataFrame:
    """Oʻahu resales of December 2023 to September 2026 by the seller's mailing
    address, with assessed value (``scripts/nonresident_seller_origin.py``)."""
    return pd.read_csv(_ORIGIN_FILE)


def home_state_offset(new_rate: float) -> float:
    """Share of out-of-state sellers' gains on which a rise in Hawaiʻi's rate
    from 7.25% to *new_rate* (decimal) is credited back by the home state.

    A state credits Hawaiʻi's tax up to its own tax on the gain, so a seller
    from a state with rate r is spared the part of the rise below r:
    min(1, (r - 7.25%) / (new - 7.25%)). Foreign sellers are counted as bearing
    all of it: the federal tax on their gain already uses up the credit their
    own country allows. Weighted by 2026 assessed value; Hawaiʻi addresses,
    military post offices and unparsed ones are left out.
    """
    o = seller_origin()
    o = o[~o["origin"].isin(["HI", "unknown", "AA", "AE", "AP"])]
    r = o["origin"].map(HOME_STATE_RATE).fillna(0.0) / 100
    spared = ((r - CAP_CURRENT) / (new_rate - CAP_CURRENT)).clip(0.0, 1.0)
    return float((spared * o["value_M"]).sum() / o["value_M"].sum())


def top_rate(cfg) -> float:
    """The schedule's top rate (decimal), joint filers."""
    return float(_schedules(cfg, ["married_filing_jointly"])["married_filing_jointly"][1].max())


# ─────────────────────────────────────────────────────────────────────────────
# Records
# ─────────────────────────────────────────────────────────────────────────────

def edition(year: int) -> pd.DataFrame:
    """Table 21 and A-3 by Hawaiʻi AGI class, one row per class ($M, returns)."""
    d = dotax_base.load_indinc(year)
    gains = {r["agi_lo"]: r for r in d["table_21_nltcg"]["classes"]}
    rows = []
    for r in d["table_a3_nonresident_by_hawaii_agi"]["taxable_classes"]:
        g = gains[r["agi_lo"]]
        rows.append({"agi_lo": r["agi_lo"], "agi_hi": r["agi_hi"], "returns": r["returns"],
                     "hawaii_agi_M": r["hawaii_agi_M"], "ti_M": r["taxable_income_M"],
                     "tax_before_M": r["tax_before_M"],
                     "gain_returns": g["nonres_returns"], "gains_M": g["nonres_amount_M"]})
    return pd.DataFrame(rows)


def _pareto_points(mean: float, floor: float, k: int) -> np.ndarray:
    alpha = mean / (mean - floor)
    q = (np.arange(k) + 0.5) / k
    return floor * (1.0 - q) ** (-1.0 / alpha)


def records(year: int = BASE_YEAR, placement: str = "central") -> pd.DataFrame:
    """Taxable nonresident returns of TY*year*: ``weight``, ``ti``, ``gains`` ($),
    ``filing_status``, ``agi_lo`` (the class) and ``base_year``."""
    if placement not in GAIN_PLACEMENTS:
        raise ValueError(f"placement must be one of {GAIN_PLACEMENTS}")
    parts = []
    for c in edition(year).itertuples():
        n_gain = min(c.gain_returns, c.returns)
        if c.agi_hi is not None and not np.isnan(c.agi_hi):
            ti = c.ti_M * 1e6 / c.returns
            gain = min(c.gains_M * 1e6 / n_gain, ti) if n_gain else 0.0
            parts.append(pd.DataFrame({"weight": [n_gain, c.returns - n_gain],
                                       "ti": [ti, ti], "gains": [gain, 0.0]}).assign(agi_lo=c.agi_lo))
            continue
        w = c.returns / K_TOP
        ti = _pareto_points(c.hawaii_agi_M * 1e6 / c.returns, TOP_FLOOR, K_TOP)
        ti *= c.ti_M * 1e6 / (w * ti).sum()   # quantile midpoints miss the tail's mass: hold A-3's total
        share = n_gain / c.returns
        s = c.gains_M * 1e6 / (share * w * ti).sum()   # gains as a share of a gain return's TI
        if s > 1:
            raise ValueError(f"TY{year}: $400K+ gains exceed gain returns' taxable income (s={s:.2f})")
        gain_ti, other_ti = ti, ti
        if placement == "gains_only":
            # gain returns hold nothing but their gains; their other income goes to
            # the rest, so the class still holds A-3's taxable income
            gain_ti = ti * s
            other_ti = ti * (1 + share * (1 - s) / (1 - share))
        parts.append(pd.DataFrame({"weight": np.r_[np.full(K_TOP, w * share), np.full(K_TOP, w * (1 - share))],
                                   "ti": np.r_[gain_ti, other_ti], "gains": np.r_[ti * s, np.zeros(K_TOP)]})
                     .assign(agi_lo=c.agi_lo))
    base = pd.concat(parts, ignore_index=True)
    base = base[base["weight"] > 0]
    out = pd.concat([base.assign(filing_status=fs, weight=base["weight"] * sh)
                     for fs, sh in STATUS_SHARES.items()], ignore_index=True)
    return out.assign(base_year=year)


# ─────────────────────────────────────────────────────────────────────────────
# Scoring
# ─────────────────────────────────────────────────────────────────────────────

def _schedules(cfg, statuses) -> dict:
    from tax_modeler.config.tax_system_config import TaxCalculator
    calc = _calc()
    assert isinstance(calc, TaxCalculator)
    return {fs: calc._bracket_schedule(cfg, fs) for fs in statuses}


@cache
def _calc():
    from tax_modeler.config.tax_system_config import TaxCalculator
    return TaxCalculator()


def tax(rec: pd.DataFrame, cfg, cap: float | None, ti=None, gains=None) -> np.ndarray:
    """Pre-credit tax per record under *cfg* with alternative-tax rate *cap*."""
    from tax_modeler.liability.cg_alternative import alternative_tax

    ti = rec["ti"].to_numpy(float) if ti is None else ti
    gains = rec["gains"].to_numpy(float) if gains is None else gains
    status = rec["filing_status"].to_numpy()
    out = np.zeros(len(rec))
    for fs, (floors, rates, cum) in _schedules(cfg, np.unique(status)).items():
        m = status == fs
        out[m] = alternative_tax(ti[m], gains[m], floors, rates, cum, cap)
    return out


def marginal_rate(rec: pd.DataFrame, cfg, ti: np.ndarray) -> np.ndarray:
    status = rec["filing_status"].to_numpy()
    out = np.zeros(len(rec))
    for fs, (floors, rates, _cum) in _schedules(cfg, np.unique(status)).items():
        m = status == fs
        idx = np.clip(np.searchsorted(floors, ti[m], side="right") - 1, 0, len(floors) - 1)
        out[m] = rates[idx]
    return out


def check_a3(year: int = BASE_YEAR, placement: str = "central") -> pd.DataFrame:
    """Modeled tax before credits against A-3, by class, under the 2018 schedule
    (the law for TY2018-2023)."""
    from tax_modeler.config.tax_system_config import TaxSystemRegistry as R

    rec = records(year, placement)
    t = tax(rec, R.get_2017_system(), CAP_CURRENT) * rec["weight"].to_numpy()
    model = pd.Series(t, index=rec.index).groupby(rec["agi_lo"]).sum() / 1e6
    out = edition(year).set_index("agi_lo")[["returns", "ti_M", "gains_M", "tax_before_M"]]
    out["model_tax_M"] = model
    out["ratio"] = out["model_tax_M"] / out["tax_before_M"]
    return out.reset_index()


def grow(rec: pd.DataFrame, yr: int, level: str = "central") -> tuple[np.ndarray, np.ndarray]:
    """Taxable income and gains in tax year *yr* ($ per record)."""
    from tax_modeler.calibration.cbo_aging import (
        DEFAULT_HAWAII_FACTORS,
        load_cbo_rates,
        net_growth_factor,
    )
    from_year = int(rec["base_year"].iloc[0])
    rates = load_cbo_rates()
    g_other = net_growth_factor(rates.growth("business", yr, from_year), DEFAULT_HAWAII_FACTORS["business"])
    gains0 = rec["gains"].to_numpy(float)
    other = rec["ti"].to_numpy(float) - gains0
    gains = gains0 * cg_growth(yr, from_year=from_year) * level_factor(level)
    return other * g_other + gains, gains


def score(cfg, yr: int, cap_new: float | None, *, level: str = "central", placement: str = "central",
          beta: float = 0.0, offset_share: float | None = 0.0) -> dict[str, float]:
    """Nonresident revenue from moving the alternative-tax rate to *cap_new*
    (None: gains taxed as ordinary income), tax year *yr*, $M.

    With ``beta`` > 0, the gains of the ``1 - offset_share`` who bear the
    increase fall by ``exp(-beta x`` the rise in their state rate on gains), as
    ``forecast_cg_rate_options.score_option`` does for residents.
    ``offset_share=None`` takes it from the sellers' home states
    (:func:`home_state_offset`) at the new rate (the top bracket rate when gains
    become ordinary income).
    """
    if offset_share is None:
        offset_share = home_state_offset(top_rate(cfg) if cap_new is None else cap_new)
    rec = records(BASE_YEAR, placement)
    w = rec["weight"].to_numpy(float)
    ti, gains = grow(rec, yr, level)
    base = tax(rec, cfg, CAP_CURRENT, ti, gains)
    static = tax(rec, cfg, cap_new, ti, gains) - base
    mr = marginal_rate(rec, cfg, ti)
    d_tau = np.maximum(0.0, (mr if cap_new is None else np.minimum(mr, cap_new)) - np.minimum(mr, CAP_CURRENT))
    kept = gains * (offset_share + (1 - offset_share) * np.exp(-beta * d_tau))
    behavioral = tax(rec, cfg, cap_new, ti - (gains - kept), kept) - base
    return {"gains_M": float((gains * w).sum() / 1e6), "static_M": float((static * w).sum() / 1e6),
            "behavioral_M": float((behavioral * w).sum() / 1e6)}
