"""The DOTAX-anchored capital-gains base (``tax_modeler.calibration.cg_anchor``),
shared by the capital-gains page and the tax simulator.
"""

from __future__ import annotations

import ast
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tax_modeler.calibration import cg_anchor
from tax_modeler.calibration.cg_anchor import (
    _CLASSES,
    BASE_YEAR,
    DOTAX_NLTCG_RES,
    DOTAX_RETURNS,
    DOTAX_TOTAL_RETURNS,
    TOP_SHARE_1M,
    anchor_nltcg,
    cg_growth,
    rank_classes,
)

REPO = Path(__file__).resolve().parents[2]
MOVED = {"BASE_YEAR", "_CLASSES", "_TOP_SHARE_NOTE", "DOTAX_NLTCG_NONRES", "DOTAX_NLTCG_RES",
         "DOTAX_RETURNS_2022", "DOTAX_TOTAL_RETURNS_2022", "TOP_SHARE_1M", "TOP_SHARE_VARIANTS",
         "anchor_nltcg", "cg_growth", "rank_classes"}


def test_anchor_is_the_window_not_the_printed_edition():
    from tax_modeler.calibration import dotax_base
    assert dotax_base.GAINS_WINDOW == (2019, BASE_YEAR)
    assert sum(cg_anchor.anchored_nltcg_M()) > sum(DOTAX_NLTCG_RES[BASE_YEAR])


def _frame(n=20_000, seed=0):
    rng = np.random.default_rng(seed)
    income = rng.lognormal(11.0, 0.8, n)
    income[:600] = rng.uniform(250_000, 1_000_000, 600)   # enough filers in the top classes
    income[:60] = rng.uniform(1e6, 2e7, 60)               # a $1M+ tail inside the $400K+ class
    return pd.DataFrame({
        "income": income, "weight": rng.uniform(1, 60, n),
        "synthetic_cg_share": np.where(rng.random(n) < 0.4, rng.uniform(0.01, 0.3, n), 0.0),
    })


def _targets(yr, top_share):
    # the anchor is the windowed series on the base edition (dotax_base.nltcg_anchor_M),
    # grown with the income base; Act 24 leaves the anticipation hook at 1.0
    g = cg_growth(yr) * cg_anchor.anticipatory_realization_factor(yr)
    t = {c: v * g for c, v in zip(_CLASSES, cg_anchor.anchored_nltcg_M(), strict=True)}
    return {**{c: t[c] for c in _CLASSES[1:5]},
            "1mp": t["400p"] * top_share, "400_1m": t["400p"] * (1 - top_share)}


@pytest.mark.parametrize("yr", [2026, 2027, 2031])
def test_classes_hit_the_dotax_targets(yr):
    df = _frame()
    w = df["weight"].to_numpy()
    nl, labels, rows = anchor_nltcg(df, yr, TOP_SHARE_1M)
    k = {r["group"]: r["scale"] for r in rows}
    assert (df["synthetic_cg_share"] * [k.get(x, 0.0) for x in labels]).max() < 1   # no clipping
    want = _targets(yr, TOP_SHARE_1M)
    for c, t in want.items():
        assert (nl * w)[labels == c].sum() / 1e6 == pytest.approx(t, rel=1e-12), c
    assert (nl[labels == "lt100"] == 0).all()                 # below $100K: unallocated
    assert {r["group"]: r["dotax_target_M"] for r in rows} == pytest.approx(want, rel=1e-12)
    # within a class, the model's shares set the distribution
    for r in rows:
        m = labels == r["group"]
        model = (df["income"] * df["synthetic_cg_share"]).to_numpy()[m]
        np.testing.assert_allclose(nl[m], model * r["scale"], rtol=1e-12)


def test_model_top_split_keeps_one_400k_class():
    df = _frame(seed=1)
    nl, labels, rows = anchor_nltcg(df, 2027, None)
    assert [r["group"] for r in rows] == ["100_150", "150_200", "200_300", "300_400", "400p"]
    t = _targets(2027, TOP_SHARE_1M)
    top = np.isin(labels, ["400_1m", "1mp"])
    assert (nl * df["weight"].to_numpy())[top].sum() / 1e6 == pytest.approx(t["1mp"] + t["400_1m"], rel=1e-12)


def test_rank_classes_follow_dotax_return_shares():
    df = _frame(seed=2)
    labels = rank_classes(df)
    w, inc = df["weight"].to_numpy(), df["income"].to_numpy()
    order = ["lt100", "100_150", "150_200", "200_300", "300_400", "400_1m", "1mp"]
    code = np.array([order.index(x) for x in labels])
    srt = np.argsort(inc, kind="stable")
    assert (np.diff(code[srt]) >= 0).all()                     # never falls as income rises
    share = w[code >= 5].sum() / w.sum()
    want = (DOTAX_RETURNS["400_1m"] + DOTAX_RETURNS["1mp"]) / DOTAX_TOTAL_RETURNS
    assert share == pytest.approx(want, abs=w.max() / w.sum())
    assert (inc[labels == "1mp"] >= 1e6).all() and (inc[labels == "400_1m"] < 1e6).all()


def test_gains_never_exceed_income():
    df = _frame(seed=3)
    df.loc[:50, "synthetic_cg_share"] = 0.95                   # a scale factor would push these past 1
    nl, _, _ = anchor_nltcg(df, 2027, TOP_SHARE_1M)
    assert (nl >= 0).all()
    # on the anchored frame income moves with the gains, so gains stay within income
    out = cg_anchor.apply_anchor_factors(df, cg_anchor.anchor_factors(df, 2027))
    g = (out["income"] * out["synthetic_cg_share"]).to_numpy()
    assert (g <= out["income"].to_numpy() + 1e-6).all() and (out["synthetic_cg_share"] <= 1).all()


# ── the anchor sets the gains level and leaves non-gains income alone ────────

def _anchored(yr=2027, seed=0, extra_cols=False):
    df = _frame(seed=seed)
    if extra_cols:
        df["agi"] = df["hi_agi"] = df["federal_agi"] = df["income"]
        df["total_cash_income"] = df["income"] * 1.1
    factors = cg_anchor.anchor_factors(df, yr)
    return df, factors, cg_anchor.apply_anchor_factors(df, factors)


@pytest.mark.parametrize("yr", [2027, 2031])
def test_anchoring_leaves_non_gains_income_unchanged(yr):
    df, factors, out = _anchored(yr, extra_cols=True)
    g0 = df["income"] * df["synthetic_cg_share"]
    g1 = out["income"] * out["synthetic_cg_share"]
    np.testing.assert_allclose(out["income"] - g1, df["income"] - g0, rtol=1e-12, atol=1e-6)
    labels = rank_classes(df)
    k = np.array([factors.get(c, 0.0) for c in labels])
    assert (k < 1).any() and (k > 1).any()                   # both directions exercised
    np.testing.assert_allclose(out["income"], df["income"] + (k - 1) * g0, rtol=1e-12, atol=1e-6)
    np.testing.assert_allclose(g1, k * g0, rtol=1e-12, atol=1e-6)
    # every field derived from gains moves with it
    for col in ("agi", "hi_agi", "federal_agi"):
        np.testing.assert_allclose(out[col], out["income"], rtol=1e-12)
    np.testing.assert_allclose(out["total_cash_income"], np.maximum(df["total_cash_income"] + (k - 1) * g0, 0),
                               rtol=1e-12, atol=1e-6)
    assert (out["synthetic_cg_share"].between(0, 1)).all()
    assert df["income"].equals(_frame()["income"])           # the input frame is not modified


@pytest.mark.parametrize("yr", [2027, 2031])
def test_anchored_class_gains_hit_the_targets(yr):
    df, _, out = _anchored(yr)
    labels = rank_classes(df)                                 # classes by the pre-anchor ranks
    w = df["weight"].to_numpy()
    g = (out["income"] * out["synthetic_cg_share"]).to_numpy()
    for c, t in _targets(yr, TOP_SHARE_1M).items():
        assert (g * w)[labels == c].sum() / 1e6 == pytest.approx(t, rel=1e-9), c
    nl, _, _ = anchor_nltcg(df, yr, TOP_SHARE_1M)
    np.testing.assert_allclose(g, nl, rtol=1e-12, atol=1e-6)
    assert (g[labels == "lt100"] == 0).all()


def test_a_gains_growth_shift_moves_act24_minus_act46_only_through_the_gains_channel():
    """Gains +50% with non-gains income held (what a gains-growth shift does
    once the anchor no longer reclassifies the difference as ordinary income)
    leaves Act 24 minus Act 46 liability unchanged where the alternative tax
    applies in both laws (7.25%, so the gains slice is taxed alike); adding the
    same dollars as ordinary income changes it (scripts/diagnostics/gains_unit_test.py)."""
    from tax_modeler.config.tax_system_config import TaxCalculator, TaxSystemRegistry
    from tax_modeler.scenarios.act24_population import statute

    calc, yr = TaxCalculator(), 2029
    n = 6
    inc = np.array([1.5e6, 2.5e6, 6e6, 4e5, 3e5, 2.5e5])
    df = pd.DataFrame({
        "income": inc, "weight": np.ones(n), "synthetic_cg_share": np.full(n, 0.35),
        "filing_status": ["single", "married_filing_jointly"] * 3, "num_dependents": 0,
        "hi_itemized_deduction": 0.0,
    })
    g = (df["income"] * df["synthetic_cg_share"]).to_numpy()
    b, s = (statute(TaxSystemRegistry.get_act46_system)(yr),
            statute(TaxSystemRegistry.get_sb3125_cd2_system)(yr))

    def diff(frame):
        return (np.asarray(calc.unit_liabilities(frame, s)["net"], float)
                - np.asarray(calc.unit_liabilities(frame, b)["net"], float))

    # a growth shift as the anchor applies it: k = 1.5 on every class
    shifted = cg_anchor.apply_anchor_factors(df, {c: 1.5 for c in ("100_150", "150_200", "200_300",
                                                                  "300_400", "400_1m", "1mp")})
    kept = shifted["income"].to_numpy() - shifted["income"] * shifted["synthetic_cg_share"]
    np.testing.assert_allclose(kept, (df["income"] - g).to_numpy(), rtol=1e-12)
    top = rank_classes(df) != "lt100"
    assert top.any()
    np.testing.assert_allclose(diff(shifted)[top], diff(df)[top], rtol=0, atol=1e-6)
    # ...and the old reclassification (income held, share cut) does change it
    old = df.copy()
    old["synthetic_cg_share"] = old["synthetic_cg_share"] * 0.5
    assert np.abs(diff(old) - diff(df)).max() > 100


def test_the_page_imports_the_package_code():
    """forecast_cg_rate_options.py takes these names from the package rather
    than defining its own (checked on the source: importing the script turns
    warnings and logging off for the whole process)."""
    tree = ast.parse((REPO / "forecast_cg_rate_options.py").read_text())
    imported = {a.name for n in tree.body if isinstance(n, ast.ImportFrom)
                and n.module == "tax_modeler.calibration.cg_anchor" for a in n.names}
    assert imported == MOVED
    defined = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
    defined |= {t.id for n in tree.body if isinstance(n, ast.Assign) for t in n.targets
                if isinstance(t, ast.Name)}
    assert not defined & MOVED
    assert all(hasattr(cg_anchor, name) for name in MOVED)
