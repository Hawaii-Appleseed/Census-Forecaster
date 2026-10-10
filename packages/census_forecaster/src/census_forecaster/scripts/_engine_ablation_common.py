"""Shared pieces for the October 2026 engine ablations.

Both `compare_rho_ablation` and `compare_moe_weighted_ablation` run the
v3 stratified calibration twice on the bundled panel and compare the
production ensemble synthesised from the cached, bias+κ-corrected fold
residuals — the `compare_ml_ablation` recipe. The synthesis here is the
ML harness's inverse-variance blend extended with (a) a per-fold ρ
lookup (so the empirical-ρ arm blends at its own correlations) and (b)
the level-anchor fusion `project_ensemble_multi` applies, gated by the
same RMSE-ratio rule.

Ship gate (unchanged from the ML ablation, METHODOLOGY §"Why opt-in"):
RMSE gate + CI90 coverage ∈ [85%, 95%] for every indicator + no
indicator regresses by > 2% RMSE. The RMSE gate for an *engine* change
is "ensemble RMSE does not get worse overall" — these changes are not
expected to earn the ML member's ≥5%-on-75% bar, and the verdict text
says which rule it is applying.
"""
from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from ..acs.calibration import COVERAGE_LOWER_BOUND, COVERAGE_UPPER_BOUND, FoldResidual
from ..acs.correlations import (
    PAIR_RATE_LEVEL,
    PAIR_TARGET_ANCHOR,
    PAIR_TREND_ML,
    RhoLookup,
)
from .compare_ml_ablation import (
    SHIP_GATE_REGRESSION_LIMIT,
    IndicatorMetrics,
    _combine_two_residuals,
    _replace_method,
)

ENSEMBLE_LABEL = "ensemble"
LEVEL_ANCHOR_RMSE_RATIO = 1.25  # mirrors ensemble._LEVEL_ANCHOR_RMSE_RATIO


def build_ensemble_residuals(
    fold_residuals: Sequence[FoldResidual],
    rho_fn: RhoLookup,
    rmse_table: dict | None = None,
) -> list[FoldResidual]:
    """Synthesise the production blend per (indicator, geoid, anchor, h).

    trend_ensemble (+ ml_trend at ρ_inner) → + multi_anchor at ρ_anchor
    → + level_anchor at ρ_level when the level anchor passes the
    calibrated RMSE-ratio gate. Inverse-variance weights throughout
    (the ML harness convention; production's macro weight is the
    Bates-Granger RMSE ratio, which is the same quantity in expectation).
    """
    by_key: dict[tuple[str, str, int, int], dict[str, FoldResidual]] = defaultdict(dict)
    for r in fold_residuals:
        by_key[(r.indicator, r.geoid, r.anchor_year, r.horizon)][r.method] = r

    out: list[FoldResidual] = []
    for key, methods in by_key.items():
        indicator = key[0]
        trend = methods.get("trend_ensemble")
        ml = methods.get("ml_trend")
        anchor = methods.get("multi_anchor")
        level = methods.get("level_anchor")
        hb = next(iter(methods.values())).h_bucket

        if trend is not None and ml is not None:
            inner: FoldResidual | None = _combine_two_residuals(
                trend, ml, rho=rho_fn(indicator, PAIR_TREND_ML, hb), label="target_ensemble",
            )
        else:
            inner = trend if trend is not None else ml

        if inner is not None and anchor is not None:
            rate = _combine_two_residuals(
                inner, anchor, rho=rho_fn(indicator, PAIR_TARGET_ANCHOR, hb), label=ENSEMBLE_LABEL,
            )
        elif inner is not None:
            rate = _replace_method(inner, ENSEMBLE_LABEL)
        elif anchor is not None:
            rate = _replace_method(anchor, ENSEMBLE_LABEL)
        else:
            rate = None

        if level is not None and _level_anchor_ok(indicator, rmse_table):
            if rate is None:
                rate = _replace_method(level, ENSEMBLE_LABEL)
            else:
                rate = _combine_two_residuals(
                    rate, level, rho=rho_fn(indicator, PAIR_RATE_LEVEL, hb), label=ENSEMBLE_LABEL,
                )
        if rate is not None:
            out.append(rate)
    return out


def _level_anchor_ok(indicator: str, rmse_table: dict | None) -> bool:
    if not rmse_table:
        return True
    row = rmse_table.get(indicator, {})
    lv = row.get("level_anchor")
    if lv is None:
        return True
    best = min(
        (row.get(m, float("inf")) for m in ("trend_ensemble", "ml_trend", "multi_anchor")),
        default=float("inf"),
    )
    if not math.isfinite(best):
        return True
    return lv <= best * LEVEL_ANCHOR_RMSE_RATIO


# -----------------------------------------------------------------------------
# Aggregation
# -----------------------------------------------------------------------------

def aggregate(
    residuals: Sequence[FoldResidual],
    key_fn: Callable[[FoldResidual], object],
    method: str | None = None,
) -> dict:
    """Group residuals by `key_fn` → IndicatorMetrics (rmse_pct, coverage, n)."""
    groups: dict = defaultdict(list)
    for r in residuals:
        if method is not None and r.method != method:
            continue
        if r.actual > 0:
            groups[key_fn(r)].append(r)
    out: dict = {}
    for k, items in groups.items():
        sq = [((r.point - r.actual) / r.actual) ** 2 for r in items]
        cov = [1 if r.ci90_low <= r.actual <= r.ci90_high else 0 for r in items]
        out[k] = IndicatorMetrics(
            indicator=str(k),
            rmse_pct=math.sqrt(sum(sq) / len(sq)),
            coverage=sum(cov) / len(cov),
            n_folds=len(items),
        )
    return out


def pooled_rmse(residuals: Sequence[FoldResidual]) -> float:
    sq = [((r.point - r.actual) / r.actual) ** 2 for r in residuals if r.actual > 0]
    return math.sqrt(sum(sq) / len(sq)) if sq else float("nan")


# -----------------------------------------------------------------------------
# Reporting
# -----------------------------------------------------------------------------

@dataclass
class GateResult:
    improved: int
    regressed: int
    n_total: int
    pooled_delta: float
    coverage_violations: list[str]
    rmse_rule: bool
    coverage_rule: bool
    regression_rule: bool

    @property
    def ship(self) -> bool:
        return self.rmse_rule and self.coverage_rule and self.regression_rule


def comparison_table(
    base: dict, treat: dict, base_label: str, treat_label: str, key_label: str = "Indicator",
) -> tuple[list[str], int, int, list[str]]:
    """Markdown rows + (improved, regressed, coverage violations) for a pair of metric dicts."""
    lines = [
        f"| {key_label} | RMSE {base_label} | RMSE {treat_label} | Δ (rel) | "
        f"Cov90 {base_label} | Cov90 {treat_label} | n folds |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    improved = regressed = 0
    violations: list[str] = []
    for k in sorted(set(base) | set(treat), key=str):
        a = base.get(k)
        b = treat.get(k)
        if a is None or b is None:
            continue
        rel = (b.rmse_pct - a.rmse_pct) / a.rmse_pct if a.rmse_pct > 0 else float("nan")
        if rel <= -0.02:
            improved += 1
        if rel >= SHIP_GATE_REGRESSION_LIMIT:
            regressed += 1
        flag = ""
        if not (COVERAGE_LOWER_BOUND <= b.coverage <= COVERAGE_UPPER_BOUND):
            violations.append(f"{k}: cov={b.coverage * 100:.1f}% (target [85, 95])")
            flag = " ⚠"
        lines.append(
            f"| {k} | {a.rmse_pct * 100:.2f}% | {b.rmse_pct * 100:.2f}% | {rel * 100:+.2f}% | "
            f"{a.coverage * 100:.1f}% | {b.coverage * 100:.1f}%{flag} | {b.n_folds} |"
        )
    return lines, improved, regressed, violations


def gate(
    base_metrics: dict,
    treat_metrics: dict,
    base_pooled: float,
    treat_pooled: float,
) -> GateResult:
    _lines, improved, regressed, violations = comparison_table(
        base_metrics, treat_metrics, "a", "b",
    )
    n_total = sum(1 for k in base_metrics if k in treat_metrics)
    pooled_delta = (treat_pooled - base_pooled) / base_pooled if base_pooled > 0 else float("nan")
    return GateResult(
        improved=improved,
        regressed=regressed,
        n_total=n_total,
        pooled_delta=pooled_delta,
        coverage_violations=violations,
        rmse_rule=(pooled_delta <= 0.0),
        coverage_rule=(not violations),
        regression_rule=(regressed == 0),
    )


def verdict_lines(g: GateResult, flag_name: str) -> list[str]:
    lines = [
        "## Ship gate verdict",
        "",
        f"* RMSE gate (pooled ensemble RMSE does not worsen): "
        f"**{'PASS' if g.rmse_rule else 'FAIL'}** (pooled Δ {g.pooled_delta * 100:+.2f}%; "
        f"{g.improved}/{g.n_total} indicators improve ≥2%)",
        f"* Coverage gate (CI90 ∈ [85%, 95%] for every indicator): "
        f"**{'PASS' if g.coverage_rule else 'FAIL'}**",
    ]
    for v in g.coverage_violations:
        lines.append(f"  * {v}")
    lines.append(
        f"* Regression gate (no indicator regresses > {SHIP_GATE_REGRESSION_LIMIT * 100:.0f}% RMSE): "
        f"**{'PASS' if g.regression_rule else 'FAIL'}** ({g.regressed} regressed)"
    )
    lines.append("")
    if g.ship:
        lines.append(f"## **Verdict: SHIP** — flip `{flag_name}` default on.")
    else:
        lines.append(f"## **Verdict: HOLD** — `{flag_name}` stays off by default (opt-in flag).")
    return lines
