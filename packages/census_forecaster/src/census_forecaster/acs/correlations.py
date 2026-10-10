"""Empirical ensemble-member correlations from the calibration fold cache.

Every variance formula in the ensemble combiner needs a correlation
between the members it blends. Until October 2026 those were literals
(ρ_inner=0.7 for damped↔AR(1) and trend↔ML, ρ_anchor=0.5, cross-source
ρ=0.6, level-fusion ρ=0.5 — `ensemble.py`, `anchors.py`), assumed in v1
and never re-examined (ACS_PREDICTION_ASSESSMENT_2026-08-21 §B7). The
fold cache that `run_stratified_calibration` already builds contains
one residual per member per (geoid, anchor, horizon) fold, so the
correlation of member *errors* is a group-by away.

This module is pure: it turns fold residuals into `StrataRecord`s keyed
by a *pair name* in the `method` slot (population bucket is always the
wildcard — correlations are estimated per indicator and, where the cell
has ≥ RHO_N_THRESHOLD folds, per horizon bucket) and resolves them
back with the literal as the floor.

Pair names
----------
``damped_log_trend|ar1_log_diff``
    The two classical trend members inside ``trend_ensemble``
    (`ensemble.combine_forecasts`, literal 0.7).
``trend_ensemble|ml_trend``
    Classical trend ensemble vs the cross-county HGB member
    (`project_ensemble_multi` ``correlation_rho_inner``, literal 0.7).
``target_ensemble|multi_anchor``
    The target-side blend (trend, or trend+ML when ML is on) vs the
    multi-source macro anchor (``correlation_rho_anchor``, literal 0.5).
``rate_blend|level_anchor``
    The rate-anchored blend vs the level anchor (literal 0.5).
``anchor_sources``
    Mean pairwise correlation between per-source anchor forecasts for
    the same indicator (`anchors.combined_anchor_rate` ``correlation_rho``,
    literal 0.6).

Residuals are log errors ``log(point / actual)``; Pearson correlation
is computed within each (indicator, h_bucket) cell on folds where both
members exist. Synthetic members (``target_ensemble``, ``rate_blend``)
are inverse-variance blends of the cached member points, which is the
weighting the ensemble uses — the ρ assumed in the blend only enters
the blend's SE, never its point, so the synthetic residual is exact.

Estimates are clamped to [RHO_MIN, RHO_MAX]: a negative estimate would
make the blend's CI tighter than independence, which no ensemble
member pair here has a structural reason to be, and ρ→1 collapses the
diversity benefit; the unclamped value is kept in ``extra["rho_raw"]``.
"""
from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence

from .strata import (
    DEFAULT_N_THRESHOLD,
    H_BUCKET_BOUNDS,
    WILDCARD,
    StrataRecord,
    index_records,
    record_from_dict,
)

PAIR_COMPONENTS: str = "damped_log_trend|ar1_log_diff"
PAIR_TREND_ML: str = "trend_ensemble|ml_trend"
PAIR_TARGET_ANCHOR: str = "target_ensemble|multi_anchor"
PAIR_RATE_LEVEL: str = "rate_blend|level_anchor"
PAIR_ANCHOR_SOURCES: str = "anchor_sources"

# The pre-October-2026 literals. These remain the production values
# unless the payload says `rho_empirical_enabled: true`, and they are
# the floor of the lookup chain in every case.
LITERAL_RHO: Mapping[str, float] = {
    PAIR_COMPONENTS: 0.7,
    PAIR_TREND_ML: 0.7,
    PAIR_TARGET_ANCHOR: 0.5,
    PAIR_RATE_LEVEL: 0.5,
    PAIR_ANCHOR_SOURCES: 0.6,
}

ALL_PAIRS: tuple[str, ...] = tuple(LITERAL_RHO)

# Same fold threshold as κ / bias — the SE of a Pearson r at n=20 is
# roughly (1 − r²)/√n ≈ 0.11 at r=0.7, comparable to the spread between
# the literals being replaced.
RHO_N_THRESHOLD: int = DEFAULT_N_THRESHOLD
RHO_MIN: float = 0.0
RHO_MAX: float = 0.95

# Calibration payload keys.
PAYLOAD_KEY: str = "member_correlations"
PAYLOAD_FLAG: str = "rho_empirical_enabled"

FoldKey = tuple[str, str, int, int]  # (indicator, geoid, anchor_year, horizon)


# -----------------------------------------------------------------------------
# Pearson on paired log residuals
# -----------------------------------------------------------------------------

def pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Pearson correlation; None when fewer than 3 pairs or zero variance."""
    n = len(xs)
    if n < 3 or n != len(ys):
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    r = sxy / math.sqrt(sxx * syy)
    if not math.isfinite(r):
        return None
    return max(-1.0, min(1.0, r))


def _log_resid(point: float, actual: float) -> float | None:
    if point > 0 and actual > 0 and math.isfinite(point) and math.isfinite(actual):
        return math.log(point / actual)
    return None


def _iv_blend_point(a_point: float, a_se: float, b_point: float, b_se: float) -> float:
    """Inverse-variance blended point, mirroring `ensemble._combine_two_with_rho`."""
    inv_a = 1.0 / (a_se ** 2) if (math.isfinite(a_se) and a_se > 0) else 0.0
    inv_b = 1.0 / (b_se ** 2) if (math.isfinite(b_se) and b_se > 0) else 0.0
    total = inv_a + inv_b
    if total <= 0:
        wa = wb = 0.5
    else:
        wa, wb = inv_a / total, inv_b / total
    return wa * a_point + wb * b_point


def _iv_blend_se(a_se: float, b_se: float, rho: float) -> float:
    inv_a = 1.0 / (a_se ** 2) if (math.isfinite(a_se) and a_se > 0) else 0.0
    inv_b = 1.0 / (b_se ** 2) if (math.isfinite(b_se) and b_se > 0) else 0.0
    total = inv_a + inv_b
    if total <= 0:
        wa = wb = 0.5
    else:
        wa, wb = inv_a / total, inv_b / total
    var = (wa * a_se) ** 2 + (wb * b_se) ** 2 + 2.0 * rho * wa * wb * a_se * b_se
    return math.sqrt(var) if var > 0 else 0.0


# -----------------------------------------------------------------------------
# Record construction
# -----------------------------------------------------------------------------

def _clamp_rho(r: float) -> float:
    return max(RHO_MIN, min(RHO_MAX, r))


def _records_for_pair(
    indicator: str,
    pair: str,
    cells: Mapping[str, tuple[list[float], list[float]]],
    extra_by_cell: Mapping[str, Mapping[str, float]] | None = None,
) -> list[StrataRecord]:
    """One record per h_bucket plus the h-marginalised ("*") record.

    `cells` maps h_bucket → (xs, ys) paired residual lists. The "*"
    record pools every h_bucket.
    """
    out: list[StrataRecord] = []
    pooled_x: list[float] = []
    pooled_y: list[float] = []
    for hb in H_BUCKET_BOUNDS:
        xs, ys = cells.get(hb, ([], []))
        pooled_x.extend(xs)
        pooled_y.extend(ys)
        r = pearson(xs, ys)
        if r is None:
            continue
        extra = {"rho_raw": r}
        if extra_by_cell and hb in extra_by_cell:
            extra.update(extra_by_cell[hb])
        out.append(StrataRecord(
            indicator=indicator, method=pair, pop_bucket=WILDCARD, h_bucket=hb,
            value=_clamp_rho(r), n_folds=len(xs), extra=extra,
        ))
    r = pearson(pooled_x, pooled_y)
    if r is not None:
        extra = {"rho_raw": r}
        if extra_by_cell and WILDCARD in extra_by_cell:
            extra.update(extra_by_cell[WILDCARD])
        out.append(StrataRecord(
            indicator=indicator, method=pair, pop_bucket=WILDCARD, h_bucket=WILDCARD,
            value=_clamp_rho(r), n_folds=len(pooled_x), extra=extra,
        ))
    return out


def _pair_cells(
    by_fold: Mapping[FoldKey, Mapping[str, tuple[float, float]]],
    h_bucket_of: Mapping[FoldKey, str],
    member_a: str,
    member_b: str,
) -> dict[str, dict[str, tuple[list[float], list[float]]]]:
    """indicator → h_bucket → (xs, ys) of paired log residuals."""
    cells: dict[str, dict[str, tuple[list[float], list[float]]]] = defaultdict(
        lambda: defaultdict(lambda: ([], []))
    )
    for key, members in by_fold.items():
        a = members.get(member_a)
        b = members.get(member_b)
        if a is None or b is None:
            continue
        xs, ys = cells[key[0]][h_bucket_of[key]]
        xs.append(a[0])
        ys.append(b[0])
    return cells


def estimate_member_correlations(
    fold_residuals: Iterable,
    component_residuals: Iterable = (),
    source_folds: Iterable = (),
) -> list[StrataRecord]:
    """Estimate every pair in `ALL_PAIRS` from cached fold residuals.

    Parameters
    ----------
    fold_residuals : FoldResidual-like objects (``indicator, method, geoid,
        anchor_year, horizon, h_bucket, actual, point, se_total``) for the
        ensemble members (``trend_ensemble``, ``multi_anchor``,
        ``ml_trend``, ``level_anchor`` …).
    component_residuals : same shape, for the ``damped_log_trend`` and
        ``ar1_log_diff`` members *inside* ``trend_ensemble`` (the generator
        caches these separately so they never receive κ / bias records).
    source_folds : HoldOutFold-like objects (``indicator, geoid,
        anchor_year, horizon, method="source:<name>", actual, projected``)
        from the per-source anchor pass.

    Returns records with ``method`` = pair name and ``pop_bucket`` = "*".
    Pairs with no overlapping folds for an indicator simply have no
    record — the consumer falls through to the literal.
    """
    from .strata import classify_horizon

    # (indicator, geoid, anchor, h) → member → (log_resid, point, se_total)
    by_fold: dict[FoldKey, dict[str, tuple[float, float, float]]] = defaultdict(dict)
    h_bucket_of: dict[FoldKey, str] = {}

    def _ingest(r, method: str) -> None:
        lr = _log_resid(r.point, r.actual)
        if lr is None:
            return
        key = (r.indicator, r.geoid, int(r.anchor_year), int(r.horizon))
        by_fold[key][method] = (lr, r.point, r.se_total)
        hb = getattr(r, "h_bucket", None) or classify_horizon(r.horizon) or WILDCARD
        h_bucket_of[key] = hb

    for r in fold_residuals:
        _ingest(r, r.method)
    for r in component_residuals:
        _ingest(r, r.method)

    # Synthetic members. `target_ensemble` = IV(trend_ensemble, ml_trend) or
    # trend_ensemble alone; `rate_blend` = IV(target_ensemble, multi_anchor)
    # or whichever exists. Actual is needed to log the residual: recover it
    # from any member (point / exp(log_resid)).
    for members in by_fold.values():
        tr = members.get("trend_ensemble")
        ml = members.get("ml_trend")
        an = members.get("multi_anchor")
        actual: float | None = None
        for m in (tr, ml, an):
            if m is not None:
                actual = m[1] / math.exp(m[0])
                break
        if actual is None:
            continue
        target: tuple[float, float, float] | None = None
        if tr is not None and ml is not None:
            p = _iv_blend_point(tr[1], tr[2], ml[1], ml[2])
            se = _iv_blend_se(tr[2], ml[2], LITERAL_RHO[PAIR_TREND_ML])
            lr = _log_resid(p, actual)
            if lr is not None:
                target = (lr, p, se)
        elif tr is not None:
            target = tr
        elif ml is not None:
            target = ml
        if target is not None:
            members["target_ensemble"] = target
        rate: tuple[float, float, float] | None = None
        if target is not None and an is not None:
            p = _iv_blend_point(target[1], target[2], an[1], an[2])
            se = _iv_blend_se(target[2], an[2], LITERAL_RHO[PAIR_TARGET_ANCHOR])
            lr = _log_resid(p, actual)
            if lr is not None:
                rate = (lr, p, se)
        elif target is not None:
            rate = target
        elif an is not None:
            rate = an
        if rate is not None:
            members["rate_blend"] = rate

    records: list[StrataRecord] = []
    for pair in (PAIR_COMPONENTS, PAIR_TREND_ML, PAIR_TARGET_ANCHOR, PAIR_RATE_LEVEL):
        a, b = pair.split("|")
        cells = _pair_cells(by_fold, h_bucket_of, a, b)
        for indicator, hcells in cells.items():
            records.extend(_records_for_pair(indicator, pair, hcells))

    # Cross-source anchor correlation: mean pairwise Pearson across the
    # rate sources available for the indicator, weighted by pair n.
    src_by_fold: dict[FoldKey, dict[str, float]] = defaultdict(dict)
    src_hb: dict[FoldKey, str] = {}
    for f in source_folds:
        if not isinstance(f.method, str) or not f.method.startswith("source:"):
            continue
        lr = _log_resid(f.projected, f.actual)
        if lr is None:
            continue
        key = (f.indicator, f.geoid, int(f.anchor_year), int(f.horizon))
        src_by_fold[key][f.method.split(":", 1)[1]] = lr
        src_hb[key] = classify_horizon(f.horizon) or WILDCARD
    # indicator → h_bucket → (src_a, src_b) → (xs, ys)
    src_cells: dict[str, dict[str, dict[tuple[str, str], tuple[list[float], list[float]]]]] = (
        defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: ([], []))))
    )
    for key, srcs in src_by_fold.items():
        names = sorted(srcs)
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                xs, ys = src_cells[key[0]][src_hb[key]][(names[i], names[j])]
                xs.append(srcs[names[i]])
                ys.append(srcs[names[j]])
    for indicator, hcells in src_cells.items():
        buckets = list(H_BUCKET_BOUNDS) + [WILDCARD]
        for hb in buckets:
            if hb == WILDCARD:
                pair_lists: dict[tuple[str, str], tuple[list[float], list[float]]] = defaultdict(
                    lambda: ([], [])
                )
                for pairs in hcells.values():
                    for pr, (xs, ys) in pairs.items():
                        pair_lists[pr][0].extend(xs)
                        pair_lists[pr][1].extend(ys)
            else:
                pair_lists = hcells.get(hb, {})
            num = 0.0
            den = 0
            extra: dict[str, float] = {}
            for (sa, sb), (xs, ys) in pair_lists.items():
                r = pearson(xs, ys)
                if r is None:
                    continue
                num += r * len(xs)
                den += len(xs)
                extra[f"rho:{sa}|{sb}"] = r
            if den == 0:
                continue
            r_mean = num / den
            extra["rho_raw"] = r_mean
            # n_folds here is the smallest pair n — the binding sample size.
            n_min = min(len(xs) for (xs, _ys) in pair_lists.values() if len(xs) >= 3)
            records.append(StrataRecord(
                indicator=indicator, method=PAIR_ANCHOR_SOURCES, pop_bucket=WILDCARD,
                h_bucket=hb, value=_clamp_rho(r_mean), n_folds=n_min, extra=extra,
            ))
    return records


# -----------------------------------------------------------------------------
# Lookup
# -----------------------------------------------------------------------------

RhoLookup = Callable[[str, str, str | None], float]
"""rho_fn(indicator, pair, h_bucket) → ρ."""


def literal_rho_lookup(indicator: str, pair: str, h_bucket: str | None = None) -> float:
    """The pre-October-2026 literals, for any (indicator, h_bucket)."""
    return LITERAL_RHO[pair]


def build_rho_lookup(
    records: Iterable[StrataRecord],
    n_threshold: int = RHO_N_THRESHOLD,
) -> RhoLookup:
    """Resolve ρ for (indicator, pair, h_bucket) with the literal as floor.

    Chain: (indicator, pair, *, h_bucket) with n ≥ threshold →
    (indicator, pair, *, *) with n ≥ threshold → literal.
    """
    recs = list(records)
    lookups: dict[str, Callable] = {}
    for pair, literal in LITERAL_RHO.items():
        lookups[pair] = index_records(
            records=[r for r in recs if r.method == pair],
            n_threshold=n_threshold,
            floor_value=literal,
        )

    def _rho(indicator: str, pair: str, h_bucket: str | None = None) -> float:
        fn = lookups.get(pair)
        if fn is None:
            return LITERAL_RHO[pair]
        rec, _src = fn(indicator, pair, WILDCARD, h_bucket or None)
        return float(rec.value)

    return _rho


def rho_lookup_from_calibration(calibration: Mapping | None) -> RhoLookup:
    """Build the consumer-side lookup from a calibration payload.

    Returns the literal lookup unless the payload carries
    ``rho_empirical_enabled: true`` *and* ``member_correlations`` — the
    same gate pattern as ``phi_enabled``: the generator's folds must have
    been combined under the same ρ the consumer applies, or the κ records
    fit on them describe a different model than production runs.
    """
    if not calibration or not calibration.get(PAYLOAD_FLAG):
        return literal_rho_lookup
    raw = calibration.get(PAYLOAD_KEY) or []
    if not raw:
        return literal_rho_lookup
    return build_rho_lookup(record_from_dict(d) for d in raw)


__all__ = [
    "PAIR_COMPONENTS",
    "PAIR_TREND_ML",
    "PAIR_TARGET_ANCHOR",
    "PAIR_RATE_LEVEL",
    "PAIR_ANCHOR_SOURCES",
    "LITERAL_RHO",
    "ALL_PAIRS",
    "RHO_N_THRESHOLD",
    "RHO_MIN",
    "RHO_MAX",
    "PAYLOAD_KEY",
    "PAYLOAD_FLAG",
    "pearson",
    "estimate_member_correlations",
    "build_rho_lookup",
    "literal_rho_lookup",
    "rho_lookup_from_calibration",
]
