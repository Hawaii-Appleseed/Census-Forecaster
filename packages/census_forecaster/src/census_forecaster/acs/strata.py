"""Stratification primitives for the v3 calibration layer.

The v2 calibration uses one global `EMPIRICAL_SE_INFLATOR=1.30` plus a
sparse `(indicator, method) → kappa` override table. v3 extends this
with two additional dimensions:

* **Population bucket** — derived from each county's 2020 ACS population
  (B01003_001E). Frozen for the lifetime of the calibration vintage, so
  bucket assignments don't drift between calibration runs. Boundaries
  are intentionally coarse (4 levels) to keep cells statistically
  reliable on a ~150-county panel.

* **Horizon bucket** — short (h ∈ {1, 2}) vs long (h ∈ {3, 4, 5}). A
  3-bucket scheme (medium = h{3, 4}) was considered but fragments the
  small-population cells below n≈30 once the suspended 2020 1-year ACS
  is accounted for. 2-bucket retains statistical power without losing
  the dominant horizon-coverage signal (κ at long horizon is materially
  larger than κ at short).

The classifier functions and the indexed-lookup primitive in this module
are pure — no I/O, no model dependencies. The calibration generator
emits `StrataRecord` lists which are then indexed by `index_records()`
into a dict supporting the documented fallback chain:

    (indicator, method, pop, h)  → exact cell with n ≥ n_threshold
    (indicator, method, pop, *)  → marginalised over h
    (indicator, method, *,   *)  → marginalised over both
    sentinel floor               → returned when no record qualifies

The "*" sentinel marks pre-aggregated records produced by the
calibration generator (NOT computed at lookup time). This keeps lookups
O(1) and avoids re-aggregation cost on every projection call.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Literal

# -----------------------------------------------------------------------------
# Bucket types and boundaries
# -----------------------------------------------------------------------------

# String-literal aliases. Using `str` underneath rather than Enum lets the
# JSON round-trip be trivially direct (no `.value` calls, no custom
# (de)serialiser) and keeps the `"*"` marginalisation sentinel typeable.
PopBucket = Literal["small", "medium", "large", "xlarge"]
HBucket = Literal["short", "long"]

# Sentinel marking a marginalised dimension in `StrataRecord`. Stored as
# a plain string in JSON; not a separate type to keep schema simple.
WILDCARD: str = "*"

# Population bucket boundaries, in 2020 ACS B01003_001E units. Half-open
# intervals: a county with pop equal to the upper bound falls into the
# *next* bucket. Tuned to put roughly 1/4 of US counties in each bucket
# at the 2020 vintage (~3,143 counties total).
POP_BUCKET_BOUNDS: tuple[tuple[str, int, int | None], ...] = (
    ("small",  0,        50_000),
    ("medium", 50_000,   200_000),
    ("large",  200_000,  1_000_000),
    ("xlarge", 1_000_000, None),  # None = no upper bound
)

# Horizon bucket assignments. A 3rd bucket (medium = {3, 4}) is feasible
# and exposed as a future option, but v3 keeps to 2 buckets — see module
# docstring for the cell-count rationale.
H_BUCKET_BOUNDS: dict[str, tuple[int, ...]] = {
    "short": (1, 2),
    "long":  (3, 4, 5),
}

# Default minimum fold count required before a strata cell's calibrated
# value is considered trustworthy. Below this, the lookup falls back to a
# more-aggregated cell (or the floor). The 20-fold threshold is calibrated
# from the standard error of the empirical CI90 coverage estimator: at
# n=20, SE on a coverage probability is sqrt(0.9 * 0.1 / 20) ≈ 6.7%, which
# is comparable to the 10-percentage-point in-band tolerance. Tightening
# n further would reject too many small-county cells; loosening it admits
# noise-driven κ and bias estimates.
DEFAULT_N_THRESHOLD: int = 20

# Phi (damping constant) bounds for the per-cell affine calibration.
# The n_threshold for phi uses 15 (vs 20 for kappa/bias) because
# weighted medians are more robust than coverage proportions at small n.
PHI_DEFAULT: float = 0.85   # global floor when no calibrated phi is available
PHI_LO: float = 0.70        # floor for high-signal cells
PHI_HI: float = 0.95        # ceiling for high-noise cells
PHI_N_THRESHOLD: int = 15


# -----------------------------------------------------------------------------
# Classifiers
# -----------------------------------------------------------------------------

def classify_pop(pop_2020: int | float | None) -> PopBucket | None:
    """Return the population bucket for a 2020 ACS B01003_001E value.

    Returns None when input is missing/invalid so callers can decide
    whether to fall through to a marginalised cell or skip the record.
    Negative values (Census suppressed/unreliable sentinels) and NaN
    yield None.
    """
    if pop_2020 is None:
        return None
    try:
        p = float(pop_2020)
    except (TypeError, ValueError):
        return None
    if p != p:  # NaN
        return None
    if p < 0:
        return None
    for name, lo, hi in POP_BUCKET_BOUNDS:
        if hi is None:
            if p >= lo:
                return name  # type: ignore[return-value]
        elif lo <= p < hi:
            return name  # type: ignore[return-value]
    return None


def classify_horizon(horizon_years: int | float) -> HBucket | None:
    """Return the horizon bucket for an integer-or-rounded horizon.

    Non-positive horizons (target year ≤ anchor) return None — those
    aren't projection situations and shouldn't be calibrated.
    Horizons beyond the H_BUCKET_BOUNDS coverage (e.g. h=10) clamp to
    the longest defined bucket to keep extrapolation well-defined.
    """
    if horizon_years is None:
        return None
    try:
        h = int(round(float(horizon_years)))
    except (TypeError, ValueError):
        return None
    if h <= 0:
        return None
    for name, hs in H_BUCKET_BOUNDS.items():
        if h in hs:
            return name  # type: ignore[return-value]
    # Beyond defined buckets: clamp to the longest
    longest_name = max(H_BUCKET_BOUNDS.items(), key=lambda kv: max(kv[1]))[0]
    return longest_name  # type: ignore[return-value]


# -----------------------------------------------------------------------------
# Record type
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class StrataRecord:
    """A single calibrated value for one (possibly marginalised) cell.

    Fields
    ------
    indicator : str            — ACS table cell, e.g. "B19013_001E".
    method    : str            — e.g. "trend_ensemble", "multi_anchor".
    pop_bucket : str           — one of {"small", "medium", "large", "xlarge", "*"}.
    h_bucket   : str           — one of {"short", "long", "*"}.
    value      : float         — the calibrated metric (κ, b, RMSE, coverage).
    n_folds    : int           — number of folds backing this cell.
    extra      : Mapping[str, float]
        Metric-specific auxiliary fields. For bias records the
        convention is `{"b_raw": <unclamped>, "clamped": 0.0 or 1.0}`.
        For κ records: `{"coverage_pre": ..., "coverage_post": ...}`.
        Empty for plain RMSE / coverage records.

    The marginalisation sentinel is the literal string "*" in either
    pop_bucket or h_bucket (or both). A record with `pop_bucket == "*"`
    is the calibration generator's pre-aggregation over all populations
    for that (indicator, method, h_bucket); the lookup uses it as a
    fallback when the more-specific cell falls below the n threshold.
    """
    indicator: str
    method: str
    pop_bucket: str
    h_bucket: str
    value: float
    n_folds: int
    extra: Mapping[str, float] = field(default_factory=dict)


# -----------------------------------------------------------------------------
# Indexed lookup with fallback chain
# -----------------------------------------------------------------------------

LookupResult = tuple[StrataRecord, str]
"""(record, source_label) — `source_label` ∈ {"exact", "h_marg", "global", "floor"}."""


def index_records(
    records: Iterable[StrataRecord],
    n_threshold: int = DEFAULT_N_THRESHOLD,
    floor_value: float = 0.0,
) -> Callable[[str, str, str | None, str | None], LookupResult]:
    """Return a lookup callable with the v3 fallback chain.

    The returned callable signature is::

        lookup(indicator, method, pop_bucket, h_bucket) -> (StrataRecord, source)

    Where `source` is one of:

    * "exact"   — exact (indicator, method, pop, h) record with n ≥ threshold.
    * "h_marg"  — h-marginalised (indicator, method, pop, "*") with n ≥ threshold.
    * "global"  — fully marginalised (indicator, method, "*", "*") with n ≥ threshold.
    * "floor"   — no qualifying record; returns a synthetic StrataRecord
                  with value=`floor_value` and n_folds=0.

    `pop_bucket` and `h_bucket` may be None (e.g. a county whose 2020
    population is unknown). In that case the lookup skips the exact and
    h_marg levels and goes straight to "global" → "floor".

    Parameters
    ----------
    records : iterable of StrataRecord
        Includes both exact cells and pre-aggregated marginalised cells
        (those with "*" in pop_bucket and/or h_bucket).
    n_threshold : int
        Minimum fold count for a cell to be considered trustworthy.
        Records below this are silently skipped at every level — the
        chain falls through to the next.
    floor_value : float
        Value to return when no qualifying record exists. For κ this is
        typically `EMPIRICAL_SE_INFLATOR` (1.30); for bias it is 0.0
        (no correction).

    Notes
    -----
    Records are indexed once at construction time; all lookups are O(1).
    Records with `n_folds < n_threshold` are *kept* in the index but
    skipped at lookup time — this lets external callers iterate every
    record (e.g. for diagnostic dumps) without losing the under-n cells.
    """
    # Index every record by its full key. Don't filter on n_threshold
    # here so the diagnostic round-trip stays lossless.
    by_key: dict[tuple[str, str, str, str], StrataRecord] = {}
    for r in records:
        key = (r.indicator, r.method, r.pop_bucket, r.h_bucket)
        # In case of duplicate keys, prefer the record with higher n_folds.
        existing = by_key.get(key)
        if existing is None or r.n_folds > existing.n_folds:
            by_key[key] = r

    def lookup(
        indicator: str,
        method: str,
        pop_bucket: str | None,
        h_bucket: str | None,
    ) -> LookupResult:
        # 1. Exact match
        if pop_bucket and h_bucket:
            r = by_key.get((indicator, method, pop_bucket, h_bucket))
            if r is not None and r.n_folds >= n_threshold:
                return (r, "exact")
        # 2. h-marginalised
        if pop_bucket:
            r = by_key.get((indicator, method, pop_bucket, WILDCARD))
            if r is not None and r.n_folds >= n_threshold:
                return (r, "h_marg")
        # 3. Globally marginalised
        r = by_key.get((indicator, method, WILDCARD, WILDCARD))
        if r is not None and r.n_folds >= n_threshold:
            return (r, "global")
        # 4. Floor
        floor = StrataRecord(
            indicator=indicator,
            method=method,
            pop_bucket=pop_bucket or WILDCARD,
            h_bucket=h_bucket or WILDCARD,
            value=floor_value,
            n_folds=0,
            extra={},
        )
        return (floor, "floor")

    return lookup


# -----------------------------------------------------------------------------
# JSON round-trip helpers
# -----------------------------------------------------------------------------

def record_to_dict(r: StrataRecord) -> dict:
    """Serialise a StrataRecord to a JSON-friendly dict."""
    out = {
        "indicator": r.indicator,
        "method": r.method,
        "pop_bucket": r.pop_bucket,
        "h_bucket": r.h_bucket,
        "value": r.value,
        "n_folds": r.n_folds,
    }
    if r.extra:
        out["extra"] = dict(r.extra)
    return out


def record_from_dict(d: Mapping) -> StrataRecord:
    """Inverse of `record_to_dict`. Raises KeyError on malformed input."""
    return StrataRecord(
        indicator=d["indicator"],
        method=d["method"],
        pop_bucket=d["pop_bucket"],
        h_bucket=d["h_bucket"],
        value=float(d["value"]),
        n_folds=int(d["n_folds"]),
        extra=dict(d.get("extra", {})),
    )


__all__ = [
    "PopBucket",
    "HBucket",
    "WILDCARD",
    "POP_BUCKET_BOUNDS",
    "H_BUCKET_BOUNDS",
    "DEFAULT_N_THRESHOLD",
    "classify_pop",
    "classify_horizon",
    "StrataRecord",
    "LookupResult",
    "index_records",
    "record_to_dict",
    "record_from_dict",
]
