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
    g = cg_growth(yr)
    t = {c: v * g for c, v in zip(_CLASSES, DOTAX_NLTCG_RES[BASE_YEAR], strict=True)}
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
    assert (nl <= np.maximum(df["income"].to_numpy(), 0)).all() and (nl >= 0).all()


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
