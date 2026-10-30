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
* The array versions of ``calculate_hawaii_tax_for_units``, the CTC
  (``ctc_for_units``) and ``calculate_eitc_for_tax_units`` vs running the
  scalar calculator on each unit: the same frame, dtypes and every float's
  bits, across bracket, phaseout and tier edges, missing values, every filing
  status, published and extrapolated years and each deduction-params choice;
  and ``per_unit.py_round`` vs ``round``.
"""
from __future__ import annotations

from pathlib import Path

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


@pytest.mark.parametrize("values", [
    np.array(STATUSES * 50, dtype=object)[np.random.default_rng(3).permutation(300)],
    np.array(["single"] * 7),                       # numpy unicode, one value
    pd.array(STATUSES * 3, dtype="string[pyarrow]").to_numpy(),
    np.array([3, 1, 2, 3, 1]),
    np.array([], dtype=object),
])
def test_groups_by_value_match_np_unique(values):
    from tax_modeler.per_unit import groups_by_value

    got = groups_by_value(values)
    want = [(v, values == v) for v in np.unique(values)]
    assert [g[0] for g in got] == [w[0] for w in want]
    for (_, a), (_, b) in zip(got, want, strict=True):
        assert np.array_equal(a, b)


# ---------------------------------------------------------------------------
# Array versions of the Hawaiʻi tax, CTC and EITC vs the scalar calculators
# ---------------------------------------------------------------------------

def _assert_same_bits(got: pd.DataFrame, want: pd.DataFrame) -> None:
    """Same columns in the same order, index, dtypes, and values: every float
    bit for bit (signed zeros included; any NaN matches any NaN)."""
    assert list(got.columns) == list(want.columns)
    assert got.index.equals(want.index)
    for c in want.columns:
        a, b = got[c], want[c]
        assert a.dtype == b.dtype, (c, a.dtype, b.dtype)
        if b.dtype.kind == "f":
            x, y = a.to_numpy(), b.to_numpy()
            same = (x.view(np.int64) == y.view(np.int64)) | (np.isnan(x) & np.isnan(y))
            assert same.all(), (c, x[~same][:5], y[~same][:5])
        else:
            assert a.equals(b), c


INCOME_EDGES = [
    float("nan"), -1e4, -0.0, 0.0, 0.01, 1.0, 2_200, 3_212, 4_400, 7_840, 9_799.99, 9_800,
    11_750, 17_640, 21_560, 28_120, 29_999.99, 30_000, 48_000, 50_000, 63_398, 75_000,
    83_400, 83_400.01, 96_000, 100_000, 150_000, 166_800, 166_800.01, 175_000, 199_999.5,
    200_000, 200_000.01, 200_999.99, 201_000.5, 225_000, 300_000, 325_000, 350_000,
    399_999, 400_000, 400_000.5, 401_000, 650_000, 1e6, 1e7, 3e7,
]
TAX_STATUSES = ["single", "married_filing_jointly", "married_filing_separately",
                "head_of_household", "qualifying_widow", "other"]


def _dependent(rng) -> dict:
    return {
        "age": int(rng.choice([0, 5, 16, 17, 18, 19, 22, 23, 24, 30, 70])),
        "relationship": rng.choice([25, 26, 27, 28, 30, 35, 20, 33, 36, "25", "x", None, 25.0],
                                   p=[.3, .05, .05, .05, .05, .05, .1, .1, .05, .05, .05, .05, .05]),
        "citizenship": rng.choice([1, 2, 4, 5, "1", "US", "5", 1.0]),
        "school_level": int(rng.choice([0, 15, 16, 20])),
        "disabled": bool(rng.random() < 0.1),
        "months_in_home": 12,
    }


def _tax_unit_frame(seed: int = 0, n_random: int = 4000) -> pd.DataFrame:
    """Units on every edge above for every status, plus random ones, with the
    columns the three calculators read (missing values included)."""
    rng = np.random.default_rng(seed)
    grid = [(a, s) for a in INCOME_EDGES for s in TAX_STATUSES]
    income = np.array([g[0] for g in grid]
                      + list(rng.lognormal(10.8, 1.2, n_random) * rng.choice([1, 1, 1, -0.1], n_random)))
    n = len(income)
    status = [g[1] for g in grid] + list(rng.choice(TAX_STATUSES, n_random))
    num_deps = rng.integers(0, 5, n)
    deps = [[_dependent(rng) for _ in range(k)] for k in num_deps]

    def holes(values, share=0.1):
        values = np.asarray(values, dtype=float)
        return np.where(rng.random(n) < share, np.nan, values)

    earned = income * rng.choice([0.0, 0.5, 1.0, 1.2], n)
    return pd.DataFrame({
        "income": income,
        "filing_status": status,
        "num_dependents": num_deps,
        "dependents": deps,
        "dependents_details": deps,
        "earned_income": holes(earned),
        "federal_earned_income": holes(earned * 0.97, 0.3),
        "federal_agi": holes(income * 0.98, 0.3),
        "investment_income": holes(rng.choice([0.0, -0.0, 50.0, 11_000, 11_950, 1e5], n)),
        "federal_tax_before_credits": holes(rng.uniform(0, 3e4, n), 0.05),
        "primary_agep": rng.choice([0, 18, 24, 25, 40, 64, 64.9, 65, 80], n).astype(float),
        "filer_has_ssn": rng.choice([True, False, np.nan], n, p=[0.8, 0.1, 0.1]),
        "synthetic_cg_share": holes(rng.choice([0.0, -0.0, 0.25, 0.9, 1.0, 1.5], n), 0.3),
        "weight": rng.uniform(1, 50, n),
    }, index=rng.permutation(n) + 7)


def _per_unit_hawaii(df, tax_year, deduction_params):
    from tax_modeler.liability.hawaii import (
        _calculate_hawaii_tax_per_unit,
        _normalize_deduction_params,
    )
    return _calculate_hawaii_tax_per_unit(
        df, tax_year, _normalize_deduction_params(deduction_params, tax_year, "test"))


def _deduction_choice(name, year):
    from tax_modeler.artifacts import load_canonical_deduction_params
    from tax_modeler.liability.hawaii import NO_ITEMIZING

    if name == "none":
        return None
    if name == "no_itemizing":
        return NO_ITEMIZING
    if name == "canonical":
        return load_canonical_deduction_params()
    from tax_modeler.adjustments.itemized_deductions import scale_deduction_params_for_target_year
    return scale_deduction_params_for_target_year(year)


@pytest.mark.parametrize("year,deductions", [
    (2022, "none"), (2023, "none"), (2023, "canonical"), (2023, "no_itemizing"),
    (2024, "canonical"), (2025, "scaled"), (2026, "no_itemizing"), (2027, "scaled"),
    (2028, "canonical"), (2029, "scaled"), (2031, "scaled"),
])
def test_hawaii_tax_arrays_match_the_per_unit_calculator(year, deductions):
    from tax_modeler.liability.hawaii import (
        _hawaii_tax_frame,
        _normalize_deduction_params,
        calculate_hawaii_tax_for_units,
    )

    df = _tax_unit_frame()
    params = _deduction_choice(deductions, year)
    assert _hawaii_tax_frame(df, year, _normalize_deduction_params(params, year, "test")) is not None
    _assert_same_bits(calculate_hawaii_tax_for_units(df, tax_year=year, deduction_params=params),
                      _per_unit_hawaii(df, year, params))


@pytest.mark.parametrize("year,extrapolate", [
    (2022, False), (2023, False), (2024, False), (2025, False), (2026, False),
    (2027, True), (2029, True), (2031, True),
])
def test_ctc_arrays_match_calculate_ctc(year, extrapolate):
    from tax_modeler.credits.ctc import _ctc_arrays, calculate_ctc, ctc_for_units

    df = _tax_unit_frame(1)
    assert _ctc_arrays(df, year, extrapolate) is not None
    want = apply_per_unit(df, lambda u: calculate_ctc(u, tax_year=year, extrapolate=extrapolate))
    _assert_same_bits(ctc_for_units(df, tax_year=year, extrapolate=extrapolate), want)
    # Without the federal tax column, each unit's is computed from its income.
    df = df.drop(columns=["federal_tax_before_credits", "filer_has_ssn"])
    want = apply_per_unit(df, lambda u: calculate_ctc(u, tax_year=year, extrapolate=extrapolate))
    _assert_same_bits(ctc_for_units(df, tax_year=year, extrapolate=extrapolate), want)


@pytest.mark.parametrize("year,extrapolate", [
    (2022, False), (2023, False), (2024, False), (2025, False), (2026, False),
    (2027, True), (2029, True), (2031, True),
])
def test_eitc_arrays_match_calculate_eitc(year, extrapolate):
    from tax_modeler.credits.eitc import _eitc_arrays, calculate_eitc, calculate_eitc_for_tax_units

    df = _tax_unit_frame(2)
    assert _eitc_arrays(df, year, extrapolate) is not None
    want = apply_per_unit(df, lambda u: calculate_eitc(u, tax_year=year, extrapolate=extrapolate))
    _assert_same_bits(calculate_eitc_for_tax_units(df, tax_year=year, extrapolate=extrapolate), want)


@pytest.mark.parametrize("year", [2023, 2028])
def test_arrays_read_narrow_and_categorical_columns_like_the_scalar_code(year):
    """The poverty pipeline's frames carry int8 dependents and a categorical
    filing status; float32 and unsigned columns convert exactly too."""
    from tax_modeler.artifacts import load_canonical_deduction_params
    from tax_modeler.credits.ctc import _ctc_arrays, calculate_ctc, ctc_for_units
    from tax_modeler.credits.eitc import _eitc_arrays, calculate_eitc, calculate_eitc_for_tax_units
    from tax_modeler.liability.hawaii import calculate_hawaii_tax_for_units

    df = _tax_unit_frame(5, n_random=1500)
    df["num_dependents"] = df["num_dependents"].astype(np.int8)
    df["filing_status"] = df["filing_status"].astype("category")
    df["primary_agep"] = df["primary_agep"].astype(np.float32)
    df["investment_income"] = df["investment_income"].astype(np.float32)
    df["federal_tax_before_credits"] = df["federal_tax_before_credits"].fillna(0).astype(np.uint32)
    params = load_canonical_deduction_params()
    _assert_same_bits(calculate_hawaii_tax_for_units(df, tax_year=year, deduction_params=params),
                      _per_unit_hawaii(df, year, params))
    assert _ctc_arrays(df, year, True) is not None and _eitc_arrays(df, year, True) is not None
    _assert_same_bits(ctc_for_units(df, tax_year=year, extrapolate=True),
                      apply_per_unit(df, lambda u: calculate_ctc(u, tax_year=year, extrapolate=True)))
    _assert_same_bits(calculate_eitc_for_tax_units(df, tax_year=year, extrapolate=True),
                      apply_per_unit(df, lambda u: calculate_eitc(u, tax_year=year, extrapolate=True)))


def test_int_valued_columns_keep_the_dtype_the_per_unit_frame_infers():
    """Where every unit's value is a Python int (TY2023's standard deduction
    and exemptions with no itemizing; a CTC every unit gets in full against
    ample tax), the per-unit frame stores int64, and so must the arrays."""
    from tax_modeler.credits.ctc import calculate_ctc, ctc_for_units
    from tax_modeler.liability.hawaii import calculate_hawaii_tax_for_units

    df = pd.DataFrame({
        "income": [30_000.0, 80_000.0, 150_000.0],
        "filing_status": ["single", "married_filing_jointly", "head_of_household"],
        "num_dependents": [1, 2, 3],
        "dependents": [[{"age": 3, "relationship": 25, "citizenship": 1}]] * 3,
        "federal_tax_before_credits": [9e3, 9e3, 9e3],
    })
    got = calculate_hawaii_tax_for_units(df, tax_year=2023)
    _assert_same_bits(got, _per_unit_hawaii(df, 2023, None))
    assert got["hi_standard_deduction"].dtype == np.int64
    assert got["hi_personal_exemptions"].dtype == np.int64
    got = ctc_for_units(df, tax_year=2024)
    _assert_same_bits(got, apply_per_unit(df, lambda u: calculate_ctc(u, tax_year=2024)))
    assert got["ctc_total"].dtype == np.int64


@pytest.mark.parametrize("change", ["empty", "object_income", "nan_dependents"])
def test_frames_the_arrays_skip_take_the_per_unit_path(change):
    """The array versions hand these frames to the scalar calculators, which
    then convert (or reject) each unit exactly as before."""
    from tax_modeler.credits.ctc import calculate_ctc, ctc_for_units
    from tax_modeler.credits.eitc import calculate_eitc, calculate_eitc_for_tax_units
    from tax_modeler.liability.hawaii import calculate_hawaii_tax_for_units

    df = _tax_unit_frame(3, n_random=50)
    if change == "empty":
        df = df.iloc[:0]
    elif change == "object_income":
        df["income"] = pd.Series([None if i % 5 == 0 else v for i, v in enumerate(df["income"])],
                                 index=df.index, dtype=object)
    else:
        df["num_dependents"] = df["num_dependents"].astype(float)
        df.iloc[3, df.columns.get_loc("num_dependents")] = np.nan
        with pytest.raises(ValueError):          # int(NaN), in the scalar calculator
            calculate_hawaii_tax_for_units(df, tax_year=2023)
        return
    _assert_same_bits(calculate_hawaii_tax_for_units(df, tax_year=2023),
                      _per_unit_hawaii(df, 2023, None))
    _assert_same_bits(ctc_for_units(df, tax_year=2025),
                      apply_per_unit(df, lambda u: calculate_ctc(u, tax_year=2025)))
    _assert_same_bits(calculate_eitc_for_tax_units(df, tax_year=2025),
                      apply_per_unit(df, lambda u: calculate_eitc(u, tax_year=2025)))


def test_py_round_matches_round():
    from tax_modeler.per_unit import py_round

    rng = np.random.default_rng(4)
    cents = rng.integers(-10**9, 10**9, 20_000)
    x = np.concatenate([
        rng.uniform(-1e5, 1e5, 20_000),
        rng.lognormal(5, 4, 20_000),
        (cents + 0.5) / 100,                     # ties on the half cent
        cents / 100 + rng.choice([-1, 1], 20_000) * np.spacing(cents / 100),
        [0.125, 0.375, 2.675, 1.005, -0.0, 0.0, -0.001, 1e300, -1e300, 4.5e13 + 0.005,
         float("nan"), float("inf"), float("-inf"), 5e-324],
    ])
    got = py_round(x, 2)
    want = np.array([round(float(v), 2) for v in x])
    same = (got.view(np.int64) == want.view(np.int64)) | (np.isnan(got) & np.isnan(want))
    assert same.all(), (x[~same][:5], got[~same][:5], want[~same][:5])


def test_the_act24_population_is_unchanged_by_the_array_paths(monkeypatch):
    """The Act 24 population steps (base tax, the synthetic $1M+ tail, a
    recession-year projection) on the 50-household synthetic PUMS, with the
    array versions and with every unit through the scalar calculators: the
    same frames, bit for bit. Runs everywhere (no gitignored data)."""
    import tax_modeler.credits.ctc as ctc
    import tax_modeler.credits.eitc as eitc
    import tax_modeler.liability.hawaii as hawaii
    from tax_modeler.artifacts import load_canonical_deduction_params
    from tax_modeler.pipeline import compute_base_tax, enrich_for_credits
    from tax_modeler.scenarios.act24_population import build_units, project_units
    from tax_modeler.units.constructor import TaxUnitConstructor

    fixtures = Path(__file__).resolve().parent / "fixtures"
    raw = TaxUnitConstructor(pd.read_parquet(fixtures / "synthetic_pums_persons.parquet"),
                             pd.read_parquet(fixtures / "synthetic_pums_households.parquet"),
                             use_soi_calibration=False, progress_bar=False
                             ).create_rule_based_units(parallel=False)
    enriched = enrich_for_credits(raw)
    ded = load_canonical_deduction_params()

    def population():
        base = compute_base_tax(enriched, deduction_params=ded, tax_year=2023)
        units, _ = build_units(base, alpha=1.5, top_premium=0.010, ded_params=ded, cal_tax_year=2023)
        return base, units, project_units(units, year=2029, top_premium=0.013, macro_shock="moderate")

    fast = population()
    monkeypatch.setattr(hawaii, "_hawaii_tax_frame", lambda *a, **k: None)
    monkeypatch.setattr(ctc, "_ctc_arrays", lambda *a, **k: None)
    monkeypatch.setattr(eitc, "_eitc_arrays", lambda *a, **k: None)
    slow = population()
    for got, want in zip(fast, slow, strict=True):
        _assert_same_bits(got, want)


def test_array_paths_read_only_what_the_scalar_reads():
    """Inputs the scalar code never touches on a unit cannot make the array
    path fail: a pd.NA SSN flag or filing status on a childless unit (CTC),
    a homeownership config or tier no unit reaches (itemized deductions)."""
    from tax_modeler.artifacts import load_canonical_deduction_params
    from tax_modeler.credits.ctc import calculate_ctc, ctc_for_units
    from tax_modeler.liability.hawaii import calculate_hawaii_tax_for_units

    child = [{"age": 4, "relationship": 25, "citizenship": 1}]
    df = pd.DataFrame({"income": [5e4, 6e4], "filing_status": ["single", "single"],
                       "dependents": [child, []], "federal_tax_before_credits": [3e3, 3e3],
                       "filer_has_ssn": pd.array([True, pd.NA], dtype="boolean")})
    _assert_same_bits(ctc_for_units(df, tax_year=2025),
                      apply_per_unit(df, lambda u: calculate_ctc(u, tax_year=2025)))
    df["filing_status"] = pd.array(["single", pd.NA], dtype="string")
    df["filer_has_ssn"] = True
    _assert_same_bits(ctc_for_units(df, tax_year=2024),
                      apply_per_unit(df, lambda u: calculate_ctc(u, tax_year=2024)))

    params = load_canonical_deduction_params()
    params = {**params, "homeownership": {k: v for k, v in params["homeownership"].items() if k != "hoh"},
              "real_estate_taxes": {**params["real_estate_taxes"],
                                    "tiers": params["real_estate_taxes"]["tiers"] + [{"broken": 1}]}}
    units = pd.DataFrame({"income": [4e4, 9e4], "num_dependents": [0, 2],
                          "filing_status": ["single", "married_filing_jointly"]})
    _assert_same_bits(calculate_hawaii_tax_for_units(units, tax_year=2028, deduction_params=params),
                      _per_unit_hawaii(units, 2028, params))


def test_absurd_dependent_counts_take_the_per_unit_path():
    """Past 2**31 dependents the int64 exemption product could overflow, so
    those frames go through the scalar's exact Python ints."""
    from tax_modeler.liability.hawaii import _hawaii_tax_frame, calculate_hawaii_tax_for_units

    df = pd.DataFrame({"income": [5e4, 6e4], "num_dependents": [8.1e15, 3.0],
                       "filing_status": ["single", "single"]})
    assert _hawaii_tax_frame(df, 2023, None) is None
    _assert_same_bits(calculate_hawaii_tax_for_units(df, tax_year=2023), _per_unit_hawaii(df, 2023, None))


def test_bracket_cache_keys_are_exact_for_numpy_schedules():
    """Two inline schedules that differ past repr's 8 digits get their own
    cache entries."""
    from dataclasses import replace

    from tax_modeler.config.tax_system_config import TaxCalculator, TaxSystemRegistry

    calc = TaxCalculator()
    base = TaxSystemRegistry.get_act46_system(2028)
    tops = []
    for top in (12.000000001, 12.000000002):
        sched = np.array([[0.0, 1.0], [5e4, 6.5], [4e5, top]])
        cfg = replace(base, brackets={fs: sched for fs in ("Single_Married_Separate", "Head_of_Household",
                                                           "Joint_Surviving_Spouse")})
        tops.append(calc.brackets_for(cfg, "single")["rate"].iloc[-1])
        assert calc._bracket_schedule(cfg, "single")[1][-1] == top / 100
    assert tops == [12.000000001, 12.000000002]


def test_group_sums_match_series_sum_per_group():
    """Each group's total is Series.sum's on that group, bit for bit: NaNs
    skipped, signed zeros kept, sizes 1-40 and empty groups."""
    from tax_modeler.per_unit import group_sums

    rng = np.random.default_rng(6)
    n_groups = 3000
    codes = rng.integers(0, n_groups, 40_000)
    codes = np.concatenate([codes, np.repeat(7, 40)])        # one group of 40+
    values = -rng.exponential(0.4, len(codes)) * rng.choice([1, 1, 1, 0, -0.0], len(codes))
    values[rng.random(len(codes)) < 0.02] = np.nan
    got = group_sums(values, codes, n_groups + 5)            # the last 5 groups are empty
    for g in range(n_groups + 5):
        want = pd.Series(values[codes == g], dtype=float).sum()
        assert np.float64(want).tobytes() == got[g].tobytes(), g


def test_bracket_schedules_are_built_once_and_stay_unchanged():
    """TaxCalculator.brackets_for / _bracket_schedule cache each schedule:
    the cached schedule equals a fresh build for registry, inline and
    scenario-tagged systems, a caller's edits to the frame it gets do not
    reach the cache, and the schedule arrays are read-only."""
    from dataclasses import replace

    from tax_modeler.config.tax_system_config import TaxCalculator, TaxSystemRegistry

    calc = TaxCalculator()
    inline = replace(TaxSystemRegistry.get_act46_system(2028), name="inline",
                     brackets={fs: ((0.0, 1.0), (50_000.0, 6.5), (400_000.0, 12.0))
                               for fs in ("Single_Married_Separate", "Head_of_Household",
                                          "Joint_Surviving_Spouse")})
    configs = [TaxSystemRegistry.get_act46_system(2027), TaxSystemRegistry.get_sb3125_cd2_system(2029),
               TaxSystemRegistry.get_hb2306_orig_system(2028), inline]
    for cfg in configs:
        for fs in ("single", "married_filing_jointly", "head_of_household", "married_filing_separately"):
            want = calc._build_brackets(cfg, fs)
            got = calc.brackets_for(cfg, fs)
            pd.testing.assert_frame_equal(got, want, check_exact=True)
            got["rate_decimal"] = 9.9                       # a caller's own copy
            pd.testing.assert_frame_equal(calc.brackets_for(cfg, fs), want, check_exact=True)
            floors, rates, cum = calc._bracket_schedule(cfg, fs)
            for a, b in zip((floors, rates, cum), calc._build_schedule(cfg, fs), strict=True):
                assert a.tobytes() == b.tobytes() and not a.flags.writeable
