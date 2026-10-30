"""Unit tests for the v3 calibration strata primitives."""
from __future__ import annotations

from census_forecaster.acs.strata import (
    DEFAULT_N_THRESHOLD,
    POP_BUCKET_BOUNDS,
    WILDCARD,
    StrataRecord,
    classify_horizon,
    classify_pop,
    index_records,
    record_from_dict,
    record_to_dict,
)

# -----------------------------------------------------------------------------
# Bucket boundary tests
# -----------------------------------------------------------------------------

class TestClassifyPop:
    """Population bucket classification at boundaries and edge cases."""

    def test_small_county(self):
        assert classify_pop(0) == "small"
        assert classify_pop(25_000) == "small"
        assert classify_pop(49_999) == "small"

    def test_medium_county(self):
        # Half-open: 50_000 is the lower bound of medium, not the upper of small.
        assert classify_pop(50_000) == "medium"
        assert classify_pop(100_000) == "medium"
        assert classify_pop(199_999) == "medium"

    def test_large_county(self):
        assert classify_pop(200_000) == "large"
        assert classify_pop(500_000) == "large"
        assert classify_pop(999_999) == "large"

    def test_xlarge_county(self):
        assert classify_pop(1_000_000) == "xlarge"
        assert classify_pop(10_000_000) == "xlarge"  # LA County

    def test_none_input(self):
        assert classify_pop(None) is None

    def test_negative_input(self):
        # Census suppression sentinel
        assert classify_pop(-555_555_555) is None

    def test_nan_input(self):
        assert classify_pop(float("nan")) is None

    def test_string_input_invalid(self):
        assert classify_pop("not a number") is None  # type: ignore[arg-type]

    def test_float_input_coerced(self):
        # A float that's really a small-county value
        assert classify_pop(49_999.9) == "small"

    def test_bucket_partition_is_exhaustive(self):
        """Every valid non-negative integer pop falls into exactly one bucket."""
        # Sample at boundaries plus a few values inside each range
        samples = [0, 1, 49_999, 50_000, 50_001, 199_999, 200_000,
                   500_000, 999_999, 1_000_000, 1_000_001, 5_000_000]
        for v in samples:
            b = classify_pop(v)
            assert b in {"small", "medium", "large", "xlarge"}, (
                f"value {v} fell into bucket {b!r}"
            )


class TestClassifyHorizon:
    """Horizon bucket classification."""

    def test_short_horizons(self):
        assert classify_horizon(1) == "short"
        assert classify_horizon(2) == "short"

    def test_long_horizons(self):
        assert classify_horizon(3) == "long"
        assert classify_horizon(4) == "long"
        assert classify_horizon(5) == "long"

    def test_zero_horizon(self):
        # h=0 means target = anchor; not a projection situation.
        assert classify_horizon(0) is None

    def test_negative_horizon(self):
        assert classify_horizon(-1) is None

    def test_float_horizon_rounded(self):
        # The classifier rounds before bucketing — a 1.4-year horizon
        # rounds to 1 → short.
        assert classify_horizon(1.4) == "short"
        assert classify_horizon(2.6) == "long"  # rounds to 3

    def test_large_horizon_clamps_to_longest(self):
        # h=10 isn't in any defined bucket; should clamp to "long".
        assert classify_horizon(10) == "long"
        assert classify_horizon(100) == "long"

    def test_none_horizon(self):
        assert classify_horizon(None) is None


# -----------------------------------------------------------------------------
# Fallback chain tests
# -----------------------------------------------------------------------------

class TestIndexRecordsFallback:
    """The lookup callable should walk the chain in the documented order."""

    @staticmethod
    def _make_records():
        """Build a record set covering several fallback scenarios."""
        return [
            # Exact cell with high n
            StrataRecord("B19013", "trend", "small", "short",
                         value=1.20, n_folds=200),
            # Exact cell with low n (below threshold) → falls through
            StrataRecord("B19013", "trend", "small", "long",
                         value=99.0, n_folds=5),
            # h-marginalised fallback for (small, long)
            StrataRecord("B19013", "trend", "small", WILDCARD,
                         value=1.40, n_folds=300),
            # No exact OR h-marginalised cell for (medium, *) → must use global
            StrataRecord("B19013", "trend", WILDCARD, WILDCARD,
                         value=1.30, n_folds=1000),
            # Different indicator/method for the floor test
            StrataRecord("B25064", "multi_anchor", WILDCARD, WILDCARD,
                         value=1.10, n_folds=10),  # below threshold
        ]

    def test_exact_match_when_n_above_threshold(self):
        lookup = index_records(self._make_records())
        rec, source = lookup("B19013", "trend", "small", "short")
        assert source == "exact"
        assert rec.value == 1.20
        assert rec.n_folds == 200

    def test_falls_through_to_h_marginal_when_exact_under_n(self):
        lookup = index_records(self._make_records())
        rec, source = lookup("B19013", "trend", "small", "long")
        # Exact (small, long) has n=5 < 20, so we drop to h_marg (small, *)
        assert source == "h_marg"
        assert rec.value == 1.40

    def test_falls_through_to_global_when_no_pop_match(self):
        lookup = index_records(self._make_records())
        rec, source = lookup("B19013", "trend", "medium", "short")
        # No exact, no h_marg → global
        assert source == "global"
        assert rec.value == 1.30

    def test_floor_when_nothing_qualifies(self):
        """If no record meets the threshold at any level, return the floor."""
        lookup = index_records(self._make_records(), floor_value=1.30)
        rec, source = lookup("B25064", "multi_anchor", "large", "long")
        assert source == "floor"
        assert rec.value == 1.30
        assert rec.n_folds == 0

    def test_unknown_indicator_returns_floor(self):
        lookup = index_records(self._make_records(), floor_value=0.0)
        rec, source = lookup("UNKNOWN", "trend", "small", "short")
        assert source == "floor"
        assert rec.value == 0.0

    def test_none_pop_bucket_skips_to_global(self):
        """A county with unknown 2020 population should bypass exact/h_marg."""
        lookup = index_records(self._make_records())
        rec, source = lookup("B19013", "trend", None, "short")
        assert source == "global"
        assert rec.value == 1.30

    def test_none_h_bucket_uses_h_marginal(self):
        # h_bucket is None when horizon ≤ 0 (passthrough projections).
        # pop is known and there's an h-marginalised record for that pop;
        # using it is correct because the wildcard in h means "applies
        # to any horizon", which is exactly the right answer here.
        lookup = index_records(self._make_records())
        rec, source = lookup("B19013", "trend", "small", None)
        assert source == "h_marg"
        assert rec.value == 1.40

    def test_none_h_bucket_with_no_h_marg_falls_to_global(self):
        """If there's no h-marginalised record for the pop, fall to global."""
        records = [
            StrataRecord("B19013", "trend", "small", "short",
                         value=1.20, n_folds=200),
            StrataRecord("B19013", "trend", WILDCARD, WILDCARD,
                         value=1.30, n_folds=1000),
        ]
        lookup = index_records(records)
        rec, source = lookup("B19013", "trend", "small", None)
        assert source == "global"
        assert rec.value == 1.30

    def test_n_threshold_respected(self):
        """Tightening n_threshold should reject more cells."""
        records = [
            StrataRecord("B19013", "trend", "small", "short",
                         value=1.20, n_folds=15),  # marginal
            StrataRecord("B19013", "trend", WILDCARD, WILDCARD,
                         value=1.30, n_folds=100),
        ]
        lookup_default = index_records(records, n_threshold=20)  # above 15
        rec_d, src_d = lookup_default("B19013", "trend", "small", "short")
        assert src_d == "global"  # 15 < 20 so falls through

        lookup_loose = index_records(records, n_threshold=10)
        rec_l, src_l = lookup_loose("B19013", "trend", "small", "short")
        assert src_l == "exact"
        assert rec_l.value == 1.20


# -----------------------------------------------------------------------------
# Round-trip tests
# -----------------------------------------------------------------------------

class TestRecordRoundTrip:
    def test_minimal_record(self):
        r = StrataRecord("B19013", "trend", "small", "short", 1.5, 42)
        d = record_to_dict(r)
        r2 = record_from_dict(d)
        assert r2 == r

    def test_record_with_extra(self):
        r = StrataRecord(
            "B19013", "trend", "*", "long", -0.04, 50,
            extra={"b_raw": -0.07, "clamped": 1.0},
        )
        d = record_to_dict(r)
        r2 = record_from_dict(d)
        assert r2.indicator == r.indicator
        assert r2.value == r.value
        assert r2.extra == {"b_raw": -0.07, "clamped": 1.0}

    def test_record_list_round_trip(self):
        records = [
            StrataRecord("B19013", "trend", "small", "short", 1.20, 200),
            StrataRecord("B19013", "trend", "small", "*", 1.40, 400),
            StrataRecord("B19013", "trend", "*", "*", 1.30, 1000),
        ]
        # Round-trip through dicts
        dicts = [record_to_dict(r) for r in records]
        records2 = [record_from_dict(d) for d in dicts]
        assert records2 == records
        # And the index built from each is functionally identical
        lookup1 = index_records(records)
        lookup2 = index_records(records2)
        for ind, m, p, h in [
            ("B19013", "trend", "small", "short"),
            ("B19013", "trend", "medium", "long"),
            ("UNKNOWN", "trend", "large", "short"),
        ]:
            r1, s1 = lookup1(ind, m, p, h)
            r2, s2 = lookup2(ind, m, p, h)
            assert s1 == s2
            assert r1.value == r2.value
            assert r1.n_folds == r2.n_folds


# -----------------------------------------------------------------------------
# Duplicate-key behaviour
# -----------------------------------------------------------------------------

class TestDuplicateKeys:
    def test_higher_n_wins(self):
        """If two records share the same key, the one with higher n_folds wins."""
        records = [
            StrataRecord("B19013", "trend", "small", "short", 1.5, 50),
            StrataRecord("B19013", "trend", "small", "short", 1.7, 100),  # higher n
            StrataRecord("B19013", "trend", "small", "short", 1.3, 75),
        ]
        lookup = index_records(records)
        rec, source = lookup("B19013", "trend", "small", "short")
        assert rec.n_folds == 100
        assert rec.value == 1.7


# -----------------------------------------------------------------------------
# Module sanity
# -----------------------------------------------------------------------------

def test_default_n_threshold_is_sane():
    """The default threshold should match the methodology rationale."""
    # n=20 → SE on 90% coverage estimate is ~6.7%. Anything weaker
    # would admit too much noise; tighter would reject too many cells.
    assert DEFAULT_N_THRESHOLD == 20


def test_pop_bucket_bounds_partition():
    """Population bucket bounds should partition [0, ∞) without gaps."""
    # Sort by lower bound
    bounds = sorted(POP_BUCKET_BOUNDS, key=lambda b: b[1])
    # First bucket starts at 0
    assert bounds[0][1] == 0
    # Each bucket's upper bound equals the next bucket's lower bound
    for (_, _, hi), (_, lo_next, _) in zip(bounds, bounds[1:]):
        assert hi == lo_next
    # Last bucket has unbounded upper
    assert bounds[-1][2] is None
