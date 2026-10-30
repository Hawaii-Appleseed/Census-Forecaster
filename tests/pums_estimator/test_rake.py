"""Tests for IPF raking (Deming-Stephan)."""
from __future__ import annotations

import pytest
from pums_estimator.models import PumsRecord, CountyControls
from pums_estimator.estimation.rake import rake, _categorise


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_records(n_by_category: dict[str, int], var: str = "VEH",
                  weight: float = 1.0) -> list[PumsRecord]:
    """Build synthetic records with equal weights, split by category."""
    records: list[PumsRecord] = []
    for cat, count in n_by_category.items():
        for _ in range(count):
            records.append(PumsRecord(
                serial="X", puma="1500100", weight=weight,
                variables={var: float(cat)},
            ))
    return records


# ---------------------------------------------------------------------------
# _categorise
# ---------------------------------------------------------------------------

class TestCategorise:
    def test_exact_integer(self):
        assert _categorise(2.0, {"0": 10, "1": 20, "2": 30}) == "2"

    def test_ceiling_category(self):
        assert _categorise(4.0, {"0": 10, "1": 20, "2+": 30}) == "2+"

    def test_ceiling_exact_threshold(self):
        assert _categorise(2.0, {"0": 10, "1": 20, "2+": 30}) == "2+"

    def test_no_match_returns_str(self):
        # Falls back to str(int(value))
        assert _categorise(5.0, {"0": 1}) == "5"


# ---------------------------------------------------------------------------
# rake()
# ---------------------------------------------------------------------------

class TestRake:
    def test_returns_same_length(self):
        records = _make_records({"0": 5, "1": 5})
        controls = CountyControls("15003", 1000, {"VEH": {"0": 50, "1": 50}})
        weights = rake(records, controls)
        assert len(weights) == 10

    def test_empty_records_returns_empty(self):
        controls = CountyControls("15003", 1000, {"VEH": {"0": 50}})
        assert rake([], controls) == []

    def test_no_margins_returns_original_weights(self):
        records = _make_records({"0": 5, "1": 5})
        controls = CountyControls("15003", 1000, {})
        weights = rake(records, controls)
        assert weights == [r.weight for r in records]

    def test_margins_match_after_raking(self):
        """After raking, the weighted sum per category should match targets."""
        records = _make_records({"0": 10, "1": 5})  # 10 with VEH=0, 5 with VEH=1
        targets = {"0": 40.0, "1": 60.0}
        controls = CountyControls("15003", 1000, {"VEH": targets})
        weights = rake(records, controls)

        w_by_cat: dict[str, float] = {}
        for rec, w in zip(records, weights):
            cat = str(int(rec.variables["VEH"]))
            w_by_cat[cat] = w_by_cat.get(cat, 0.0) + w

        assert w_by_cat["0"] == pytest.approx(40.0, rel=1e-3)
        assert w_by_cat["1"] == pytest.approx(60.0, rel=1e-3)

    def test_two_variable_convergence(self):
        """Two-variable IPF should converge and satisfy both margins."""
        import random
        random.seed(42)
        records = []
        for _ in range(50):
            records.append(PumsRecord(
                serial="X", puma="1500100", weight=1.0,
                variables={
                    "VEH": float(random.choice([0, 1, 2])),
                    "TEN": float(random.choice([1, 2])),
                },
            ))

        veh_targets = {"0": 5.0, "1": 25.0, "2": 20.0}
        ten_targets = {"1": 30.0, "2": 20.0}
        controls = CountyControls("15003", 1000,
                                  {"VEH": veh_targets, "TEN": ten_targets})
        weights = rake(records, controls, tol=1e-6)

        # Check VEH margins
        w_veh: dict[str, float] = {}
        w_ten: dict[str, float] = {}
        for rec, w in zip(records, weights):
            vcat = str(int(rec.variables["VEH"]))
            tcat = str(int(rec.variables["TEN"]))
            w_veh[vcat] = w_veh.get(vcat, 0.0) + w
            w_ten[tcat] = w_ten.get(tcat, 0.0) + w

        for cat, target in veh_targets.items():
            if cat in w_veh:
                assert w_veh[cat] == pytest.approx(target, rel=1e-3)
        for cat, target in ten_targets.items():
            if cat in w_ten:
                assert w_ten[cat] == pytest.approx(target, rel=1e-3)

    def test_ceiling_category_raking(self):
        """Ceiling categories ('2+') should be handled during raking."""
        records = _make_records({"0": 5, "1": 5, "2": 5, "3": 5})
        targets = {"0": 5.0, "1": 10.0, "2+": 15.0}  # "2+" covers VEH=2 and VEH=3
        controls = CountyControls("15003", 1000, {"VEH": targets})
        weights = rake(records, controls)

        w_by_cat: dict[str, float] = {"0": 0.0, "1": 0.0, "2+": 0.0}
        for rec, w in zip(records, weights):
            cat = _categorise(rec.variables["VEH"], targets)
            if cat in w_by_cat:
                w_by_cat[cat] += w

        assert w_by_cat["0"] == pytest.approx(5.0, rel=1e-3)
        assert w_by_cat["1"] == pytest.approx(10.0, rel=1e-3)
        assert w_by_cat["2+"] == pytest.approx(15.0, rel=1e-3)


# ---------------------------------------------------------------------------
# rake_arrays: rake over arrays, bit for bit
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("seed", [0, 1, 2])
def test_rake_arrays_returns_rakes_weights_bit_for_bit(seed):
    """Two margins (one with an "X+" category and values outside every
    category, one with a zero and a negative target), uneven weights, a
    category with no records: the same weights as rake on one record per unit."""
    import numpy as np
    from pums_estimator.estimation.rake import rake_arrays

    rng = np.random.default_rng(seed)
    n = 3000
    weights = rng.lognormal(3, 1, n)
    veh = rng.integers(-1, 7, n).astype(float)
    fs = rng.integers(0, 4, n).astype(float)
    margins = {
        "VEH": {"0": 900.0, "1": 2_000.0, "2": 1_800.0, "3+": 1_500.0, "9": 50.0},
        "FS": {"0": 3_000, "1": 2_500, "2": 0, "3": -5},
    }
    records = [PumsRecord(serial="X", puma="1500100", weight=float(weights[i]),
                          variables={"VEH": float(veh[i]), "FS": float(fs[i])}) for i in range(n)]
    for max_iter, tol in [(100, 1e-4), (3, 1e-12), (100, 1e-14)]:
        want = np.array(rake(records, CountyControls(geoid="15003", population=0, margins=margins),
                             max_iter=max_iter, tol=tol))
        got = rake_arrays(weights, {"VEH": veh, "FS": fs}, margins, max_iter=max_iter, tol=tol)
        assert got.tobytes() == want.tobytes()
    assert weights.tobytes() == np.array([r.weight for r in records]).tobytes()   # input untouched


def test_rake_arrays_matches_rake_where_rake_never_categorises_or_meets_nan():
    """max_iter=0 returns the weights without reading any value; a NaN scale
    (inf/inf) is passed over when deciding convergence, as rake's
    ``delta > max_delta`` does."""
    import numpy as np
    from pums_estimator.estimation.rake import rake_arrays

    assert rake_arrays([1.0], {"A": [float("nan")]}, {"A": {"0": 1.0}}, max_iter=0).tolist() == [1.0]
    weights = [float("inf"), 1.0, 1.0]
    values = {"A": [0.0, 1.0, 1.0]}
    margins = {"A": {"0": float("inf"), "1": 10.0}}
    records = [PumsRecord(serial="X", puma="1500100", weight=w, variables={"A": v})
               for w, v in zip(weights, values["A"])]
    for max_iter in (1, 3):
        want = np.array(rake(records, CountyControls(geoid="15003", population=0, margins=margins),
                             max_iter=max_iter, tol=1e-4))
        got = rake_arrays(weights, values, margins, max_iter=max_iter, tol=1e-4)
        assert np.array_equal(got, want, equal_nan=True)
