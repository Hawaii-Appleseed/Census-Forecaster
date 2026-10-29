"""The array fast paths return exactly what the per-unit code they replaced did.

* ``HawaiiTaxCredits.total_credits_arrays`` vs ``calculate_total_credits``,
  bit for bit (signed zeros included), over the thresholds of every credit,
  every filing status, every credit scenario and the food/excise table switch.
* ``per_unit.apply_per_unit`` vs rebuilding the frame from row dicts, the loop
  it replaced in the Hawaiʻi tax, EITC and CTC passes, and its lazy rows vs
  ``to_dict("records")``.
* The scalar food/excise credit's bisect lookup vs the numpy table lookup.
* ``PUMA0100Imputer.impute_counties`` vs ``assign_county`` per row, including
  where numpy's global generator is left.
* ``parallel.fork_map`` vs a list comprehension.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from tax_modeler.adjustments.hawaii_credits import HawaiiTaxCredits
from tax_modeler.per_unit import apply_per_unit, unit_dicts, unit_rows

EDGES = [-5e3, -0.0, 0.0, 1, 14_999.99, 15_000, 24_999, 25_000, 25_000.5, 30_000, 35_000, 40_000,
         43_000, 43_001, 46_000, 49_999.99, 50_000, 80_000, 90_000.01, 99_999, 100_000, 100_000.01,
         160_000, 199_999, 200_000, 300_000, 300_000.01, 1e7]
STATUSES = ["single", "married_filing_jointly", "head_of_household", "married_filing_separately",
            "qualifying_widow", "other"]


def _units():
    rng = np.random.default_rng(0)
    grid = [(a, s, d) for a in EDGES for s in STATUSES for d in range(5)]
    agi = np.array([g[0] for g in grid] + list(rng.uniform(-1e4, 4e5, 3000)), dtype=float)
    status = np.array([g[1] for g in grid] + list(rng.choice(STATUSES, 3000)), dtype=object)
    deps = np.array([g[2] for g in grid] + list(rng.integers(0, 6, 3000)), dtype=np.int64)
    tax = np.concatenate([rng.choice([0.0, -0.0, 10.0, 25.0, 1e4], len(grid)), rng.uniform(0, 3e3, 3000)])
    return agi, status, deps, tax


@pytest.mark.parametrize("year", [2026, 2027, 2028])
@pytest.mark.parametrize("scenario", [None, "hb2306_hd1", "sb3125_sd1"])
def test_credit_arrays_match_the_per_unit_method_bit_for_bit(year, scenario):
    agi, status, deps, tax = _units()
    calc = HawaiiTaxCredits(year=year)
    total, child_care = calc.total_credits_arrays(agi, status, deps, tax, credit_scenario=scenario)
    for i in range(len(agi)):
        r = calc.calculate_total_credits(agi=float(agi[i]), filing_status=str(status[i]),
                                         num_dependents=int(deps[i]), tax_before_credits=float(tax[i]),
                                         credit_scenario=scenario)
        assert np.float64(r["total"]).tobytes() == total[i].tobytes(), (agi[i], status[i], deps[i], tax[i])
        assert np.float64(r["child_care"]).tobytes() == child_care[i].tobytes()


def _rebuild(df, fn):
    rows = []
    for unit in df.to_dict("records"):
        unit.update(fn(unit))
        rows.append(unit)
    return pd.DataFrame(rows)


def _frame():
    rng = np.random.default_rng(1)
    n = 200
    return pd.DataFrame({
        "income": rng.uniform(0, 2e5, n),
        "num_dependents": rng.integers(0, 4, n),
        "filing_status": rng.choice(STATUSES[:4], n),
        "is_joint": rng.random(n) < 0.5,
        "dependents_details": [[{"age": int(a)}] for a in rng.integers(0, 18, n)],
        "maybe": [None if i % 3 else float(i) for i in range(n)],
    }, index=rng.permutation(n) + 1000)


@pytest.mark.parametrize("fn", [
    lambda u: {"tax": u["income"] * 0.05, "income": u["income"] + 1},          # new + overwritten
    lambda u: {"flag": u["num_dependents"] > 1, "n": u["num_dependents"] * 2},  # bool + int columns
    lambda u: {"extra": 1.0} if u["num_dependents"] else {"income": 0.0},     # keys vary by unit
    lambda u: {},
])
def test_apply_per_unit_matches_rebuilding_from_row_dicts(fn):
    df = _frame()
    pd.testing.assert_frame_equal(apply_per_unit(df, fn), _rebuild(df, fn), check_exact=True)


def test_unit_dicts_match_to_dict_records():
    df = _frame()
    for a, b in zip(unit_dicts(df), df.to_dict("records")):
        assert a.keys() == b.keys()
        for k in a:
            assert type(a[k]) is type(b[k])
            assert a[k] == b[k] or (a[k] != a[k] and b[k] != b[k])


def test_unit_rows_read_like_to_dict_records():
    df = _frame()
    for a, b in zip(unit_rows(df), df.to_dict("records"), strict=True):
        assert list(a) == list(b) and len(a) == len(b)
        for k in b:
            assert k in a
            assert type(a[k]) is type(b[k]) and type(a.get(k)) is type(b[k])
            assert a[k] == b[k] or (a[k] != a[k] and b[k] != b[k])
        assert "missing" not in a and a.get("missing", 7) == 7
        with pytest.raises(KeyError):
            a["missing"]


@pytest.mark.parametrize("year", [2022, 2026, 2028, None])
def test_scalar_food_excise_lookup_matches_the_table(year):
    from tax_modeler.credits.hi_food_excise import (
        _JOINT_TABLE_STATUSES,
        _per_exemption,
        food_excise_credit,
        hawaii_food_excise_parameters,
    )

    p = hawaii_food_excise_parameters(year)
    ceilings = sorted({c for c, _ in p.single + p.joint})
    agis = [-1.0, -0.0, 0.0, float("nan")] + [x + d for x in ceilings for d in (-0.01, 0.0, 0.01)]
    for agi in agis:
        for status in STATUSES:
            for n in (0, 1, 4):
                table = p.joint if status in _JOINT_TABLE_STATUSES else p.single
                per_ex = _per_exemption(np.array([max(float(agi), 0.0)]), table,
                                        p.income_threshold_factor)[0]
                want = float(per_ex * max(int(n), 0) * p.amount_pct)
                got = food_excise_credit(agi, status, n, year)
                assert np.float64(got).tobytes() == np.float64(want).tobytes(), (agi, status, n)


def test_county_imputation_matches_assign_county_and_leaves_the_generator_alike():
    from tax_modeler.analysis.puma_imputation import PUMA0100Imputer

    rng = np.random.default_rng(2)
    n = 300
    df = pd.DataFrame({
        "PUMA": rng.choice(["0100", "100", "0200"], n),
        "filer_id": [f"F{i}" for i in range(n)],
        "income": rng.uniform(0, 3e5, n),
        "num_dependents": rng.integers(0, 5, n),
        "filing_status": rng.choice(["single", "joint"], n),
        "property_value": [None if i % 4 else float(i * 3_000) for i in range(n)],
        "county": None,
    })
    imputer = PUMA0100Imputer()
    mask = df["PUMA"].isin(["100", "0100"])
    want = [imputer.assign_county(u) for u in df[mask].to_dict("records")]
    after_assign = np.random.random()
    got = imputer.impute_counties(df)
    assert list(got.loc[mask, "county"]) == want
    assert np.random.random() == after_assign


def test_fork_map_returns_results_in_order():
    from tax_modeler.parallel import fork_map

    big = pd.DataFrame({"x": np.arange(1000.0)})
    assert fork_map(lambda i: float(big["x"].iloc[i] * 2), range(12)) == [2.0 * i for i in range(12)]
    assert fork_map(lambda i: i, []) == []
