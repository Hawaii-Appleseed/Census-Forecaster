"""Conveyance tax schedules: HRS §247-2 today and SB 3028 SD2 HD2 (2026)."""
from __future__ import annotations

import numpy as np
import pytest
from tax_modeler.conveyance import (
    HD2_NONOWNER,
    HD2_OWNER,
    breakeven_price,
    current_law_tax,
    hd2_tax,
    marginal_tax,
    pareto_alpha_grouped,
    value_above,
)


@pytest.mark.parametrize("price,owner,other", [
    (500_000, 500, 750),              # 0.10% / 0.15%
    (800_000, 1_600, 2_000),          # 0.20% / 0.25% of the whole price
    (3_500_000, 17_500, 21_000),      # 0.50% / 0.60%
    (3_999_000, 19_995, 23_994),
    (4_000_000, 28_000, 34_000),      # the cliff: +$8,005 for $1,000 more
    (12_000_000, 120_000, 150_000),
])
def test_current_law_cliff(price, owner, other):
    assert float(current_law_tax(price, "owner")) == pytest.approx(owner)
    assert float(current_law_tax(price, "nonowner")) == pytest.approx(other)
    assert float(current_law_tax(price, "nonres")) == pytest.approx(owner)


@pytest.mark.parametrize("sched,points", [
    # The bill states each band as "$X plus Y per $100 over $Z"; the $X are the
    # cumulative marginal tax at each bound.
    (HD2_OWNER, {600e3: 600, 1e6: 1_800, 2e6: 6_300, 3e6: 26_300, 6e6: 146_300, 10e6: 346_300}),
    (HD2_NONOWNER, {600e3: 900, 1e6: 2_300, 2e6: 8_800, 3e6: 58_800, 6e6: 268_800, 10e6: 588_800}),
])
def test_hd2_bases_match_the_bill(sched, points):
    for price, base in points.items():
        assert float(marginal_tax(price, sched)) == pytest.approx(base)


def test_reported_example_35m_owner():
    # Reported for the latest draft: $17,500 today -> $46,300 under SB 3028.
    assert float(current_law_tax(3.5e6, "owner")) == 17_500
    assert float(hd2_tax(3.5e6, "owner")) == pytest.approx(46_300)


def test_caps_bind_only_at_the_top():
    assert float(hd2_tax(20e6, "owner")) == pytest.approx(0.04 * 20e6)
    assert float(hd2_tax(20e6, "nonowner")) == pytest.approx(0.06 * 20e6)
    assert float(hd2_tax(9e6, "owner")) < 0.04 * 9e6


def test_nonresidential_unchanged():
    p = np.array([5e5, 1.5e6, 3.5e6, 8e6, 25e6])
    np.testing.assert_allclose(hd2_tax(p, "nonres"), current_law_tax(p, "nonres"))


def test_breakevens_match_house_finance_description():
    # House Finance: higher rates begin around $2.3M (owner) and $2.1M (other).
    assert breakeven_price("owner") == pytest.approx(2.247e6, abs=2e3)
    assert breakeven_price("nonowner") == pytest.approx(2.073e6, abs=2e3)


def test_cpi_index_raises_brackets():
    assert float(hd2_tax(3.5e6, "owner", index=1.1)) < float(hd2_tax(3.5e6, "owner"))
    assert float(hd2_tax(3.5e6, "owner", index=0.9)) == float(hd2_tax(3.5e6, "owner"))   # upward only


def test_value_above_reads_tiers_and_interpolates_a_pareto_tail():
    # Maui non-owner-occupied, tax year 2026-27 ($): bands up to $1M, $1M-$2.5M, above $2.5M.
    tiers = [(0, 10_226_832e3), (1e6, 4_657_218e3), (2.5e6, 4_521_320e3)]
    assert value_above(tiers, 2.5e6) == pytest.approx(4_521_320e3)
    assert value_above(tiers, 1e6) == pytest.approx(9_178_538e3)
    v2 = value_above(tiers, 2e6)
    assert 4_521_320e3 < v2 < 9_178_538e3
    # exact for a Pareto tail: value above t = N * x_m**a * t**(1-a) / (a-1)
    a, n, xm = 2.5, 1000, 1e6
    def pareto_above(t):
        return n * xm**a * t ** (1 - a) / (a - 1)
    pts = [(0, 0.0), (1e6, pareto_above(1e6) - pareto_above(3e6)), (3e6, pareto_above(3e6))]
    assert value_above(pts, 2e6) == pytest.approx(pareto_above(2e6))
    assert value_above(pts, 5e6) == pytest.approx(pareto_above(5e6))
    assert value_above([(0, 0.0), (1e6, pareto_above(1e6))], 2e6, alpha=a) == pytest.approx(pareto_above(2e6))
    with pytest.raises(ValueError):
        value_above([(0, 1.0), (1e6, 1.0)], 2e6)


def test_pareto_alpha_grouped_recovers_the_index():
    a = 2.4
    edges = [1e6, 2e6, 3e6, 4e6, 5e6, np.inf]
    n = 100_000
    bands = [(lo, hi, n * ((lo / 1e6) ** -a - (0 if np.isinf(hi) else (hi / 1e6) ** -a)))
             for lo, hi in zip(edges[:-1], edges[1:], strict=True)]
    assert pareto_alpha_grouped(bands, 1e6) == pytest.approx(a, abs=0.002)
