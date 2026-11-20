"""DOTAX Tables A-2, A-5 and A-6 (resident returns by Hawaiʻi AGI class), parsed by scripts/parse_dotax_family_tables.py."""
from __future__ import annotations

import numpy as np
import pytest
from tax_modeler.calibration import dotax_base


def test_the_json_cites_its_source_and_the_three_tables_agree_on_the_resident_total():
    d = dotax_base.load_family(2023)
    assert d["source"]["url"].startswith("https://files.hawaii.gov/tax/stats/stats/indinc/2023indinc_tables")
    assert len(d["source"]["sha256"]) == 64
    a2 = d["a2_returns_by_status"]["totals"]["resident"]
    total = a2["joint"] + a2["single"] + a2["hoh"]
    assert total == pytest.approx(d["a5_exemptions"]["totals"]["resident"]["returns"], abs=5)
    assert total == pytest.approx(sum(r["returns"] for r in d["a6_returns_by_regular_exemptions"]["classes"]), abs=5)


@pytest.mark.parametrize("kind", dotax_base.FAMILY_KINDS)
def test_rebinning_keeps_the_total_and_merges_only_on_department_edges(kind):
    fine = sum(v for _, v in dotax_base._fine_family(kind, 2023))
    e = (-np.inf, 10_000.0, 50_000.0, 100_000.0)
    coarse = dotax_base.family_targets(kind, e)
    assert sum(coarse.values()) == pytest.approx(fine)
    assert all(v >= 0 for v in coarse.values())


def test_printed_values_are_what_the_workbook_says():
    # TY2023 resident returns: head of household (and qualifying widow(er)) 69,533; dependent exemptions 301,132;
    # returns with three or more regular exemptions 122,950
    assert sum(dotax_base.family_targets("hoh", (-np.inf, 10_000.0)).values()) == pytest.approx(69_533)
    assert sum(dotax_base.family_targets("dependents", (-np.inf, 10_000.0)).values()) == pytest.approx(301_132)
    assert sum(dotax_base.family_targets("exemptions_3plus", (-np.inf, 10_000.0)).values()) == pytest.approx(122_950)


def test_tax_after_credits_is_negative_in_the_lowest_class_and_below_tax_before_credits():
    after = dotax_base.tax_after_credits_M()
    before = dotax_base.tax_targets_M()
    assert after[(0.0, 10_000.0)] < 0
    assert sum(after.values()) < sum(before.values())


def test_an_unknown_kind_is_refused():
    with pytest.raises(KeyError):
        dotax_base.family_targets("nope", (-np.inf, 10_000.0))
