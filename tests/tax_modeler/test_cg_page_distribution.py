"""The capital-gains page's household groups (forecast_cg_rate_options.distribution).

Regression for the September 29, 2026 pipeline audit: the groups were cut on
the filer weight of each household's first tax unit while their household
counts and per-household averages use the PUMS household weight (WGTP), so
the "Lowest 20%" did not hold a fifth of households. They are now cut on WGTP.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

N_HH = 300


def _frame() -> tuple[pd.DataFrame, np.ndarray]:
    """Households of one to three units, income rising with the index; the
    first unit's filer weight rises with income too, and the others' differ."""
    rng = np.random.default_rng(11)
    rows = []
    for h in range(N_HH):
        hw = float(rng.integers(5, 40))
        n_units = 1 + h % 3
        for k in range(n_units):
            rows.append({"hh_id": f"H{h:03d}", "hh_weight": hw,
                         "weight": 2 + 0.5 * h if k == 0 else float(rng.uniform(1, 60)),
                         "total_cash_income": 10_000 * 1.02 ** h / n_units})
    df = pd.DataFrame(rows)
    change = rng.normal(50, 200, len(df))
    return df, change


def test_groups_hold_their_share_of_households(cg_page):
    df, change = _frame()
    out = cg_page.distribution(df, {"x": change}).set_index("group")
    hw = df.groupby("hh_id")["hh_weight"].first()
    assert out["households"].sum() == pytest.approx(hw.sum())
    granularity = hw.max() / hw.sum()
    for name, lo, hi in cg_page.GROUPS:
        assert abs(out.loc[name, "households"] / hw.sum() - (hi - lo)) <= granularity, name


def test_totals_weight_each_unit_by_its_own_filer_weight(cg_page):
    df, change = _frame()
    out = cg_page.distribution(df, {"x": change})
    assert out["total_x_M"].sum() == pytest.approx((change * df["weight"]).sum() / 1e6, rel=1e-12)
    np.testing.assert_allclose(out["avg_x"] * out["households"], out["total_x_M"] * 1e6, rtol=1e-12)
