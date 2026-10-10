"""SDR sampling intervals on the Act 24 revenue deltas.

``behavioral_response.score_with_response`` scores the population once per
year: Act 46 on the population before anyone responds, the scenario on the
same population (static) and again on the responded population. Each
figure is a weighted sum of a per-unit tax vector, so under replicate
weight ``r`` only the sums change::

    baseline_r   = Σ w_r  T_base(y)
    static_r     = Σ w_r (T_scen(y) − T_base(y))
    post_r       = Σ w'_r T_scen(y′)              (responded population)
    behavioral_r = post_r − baseline_r − PTE shift

with ``w_r`` the calibrated replicate weights (:mod:`.replicates`) and
``w'_r`` the same after the migration response. :func:`act24_delta_sdr`
recomputes the three per-unit vectors with the one scoring kernel
(``TaxCalculator.unit_liabilities``) and reduces the 81 sums to a point,
an SE and a 90% interval per aggregate, overall and by AGI class.

The PTE shift is a fixed aggregate here (it is zero in every Act 24
scenario, Act 58); the credit overlay is not microdata and gets no band.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from tax_modeler.uncertainty.replicates import replicate_weight_matrix
from tax_modeler.uncertainty.sdr import SDREstimate, Z_90, summarize

# The income classes of the Act 24 page's bracket table (quintile_analysis).
AGI_CLASSES: tuple[tuple[str, float, float], ...] = (
    ("Under $10K", float("-inf"), 10_000),
    ("$10K–$30K", 10_000, 30_000),
    ("$30K–$60K", 30_000, 60_000),
    ("$60K–$100K", 60_000, 100_000),
    ("$100K–$175K", 100_000, 175_000),
    ("$175K–$350K", 175_000, 350_000),
    ("$350K–$500K", 350_000, 500_000),
    ("$500K–$1M", 500_000, 1_000_000),
    ("$1M+", 1_000_000, float("inf")),
)

TOTAL_KEYS = ("act46_baseline_$M", "bracket_delta_static_$M", "eti_response_$M",
              "bracket_delta_post_$M")


def _reduce(point_and_reps: np.ndarray, z: float) -> SDREstimate:
    return summarize(point_and_reps[0], point_and_reps[1:], z=z)


def act24_delta_sdr(
    pre: pd.DataFrame,
    adjusted: pd.DataFrame,
    *,
    baseline_cfg,
    scenario_cfg,
    calculator,
    pte_loss_m: float = 0.0,
    by_class: bool = True,
    income_col: str = "income",
    z: float = Z_90,
) -> tuple[dict[str, SDREstimate], Optional[pd.DataFrame]]:
    """SDR estimates of the year's aggregates, and per AGI class.

    ``pre`` is the projected frame ``score_with_response`` scored and
    ``adjusted`` the responded population it returned (same rows, same
    order). Returns ``(totals, by_class)``: ``totals`` maps
    ``act46_baseline_$M``, ``bracket_delta_static_$M``, ``eti_response_$M``
    and ``bracket_delta_post_$M`` to estimates whose points reproduce the
    script's own figures; ``by_class`` has one row per AGI class (classes
    on the pre-response income) with the static and post-response deltas.
    """
    if len(pre) != len(adjusted):
        raise ValueError("act24_delta_sdr: pre and adjusted frames differ in length")
    t_base = np.asarray(calculator.unit_liabilities(pre, baseline_cfg)["net"], dtype=float)
    t_scen = np.asarray(calculator.unit_liabilities(pre, scenario_cfg)["net"], dtype=float)
    t_post = np.asarray(calculator.unit_liabilities(adjusted, scenario_cfg)["net"], dtype=float)
    w_pre = replicate_weight_matrix(pre)
    w_adj = replicate_weight_matrix(adjusted)

    def sums(vals: np.ndarray, W: np.ndarray, mask: Optional[np.ndarray] = None) -> np.ndarray:
        if mask is not None:
            vals, W = vals[mask], W[mask]
        return (vals @ W) / 1e6

    baseline = sums(t_base, w_pre)
    static = sums(t_scen - t_base, w_pre)
    post = sums(t_post, w_adj)
    behavioral = post - baseline - pte_loss_m
    totals = {
        "act46_baseline_$M": _reduce(baseline, z),
        "bracket_delta_static_$M": _reduce(static, z),
        "eti_response_$M": _reduce(behavioral - static, z),
        "bracket_delta_post_$M": _reduce(behavioral, z),
    }

    classes = None
    if by_class:
        inc = pre[income_col].to_numpy(dtype=float)
        rows = []
        for label, lo, hi in AGI_CLASSES:
            m = (inc >= lo) & (inc < hi)
            if not m.any():
                continue
            s = _reduce(sums(t_scen - t_base, w_pre, m), z)
            # Post-response: the scenario on the responded rows of the class
            # against Act 46 on the same rows before the response.
            p = _reduce(sums(t_post, w_adj, m) - sums(t_base, w_pre, m), z)
            rows.append({
                "agi_class": label,
                "bracket_delta_static_$M": s.point,
                "bracket_delta_static_se_$M": s.se,
                "bracket_delta_static_ci90_low_$M": s.ci_low,
                "bracket_delta_static_ci90_high_$M": s.ci_high,
                "bracket_delta_post_$M": p.point,
                "bracket_delta_post_se_$M": p.se,
                "bracket_delta_post_ci90_low_$M": p.ci_low,
                "bracket_delta_post_ci90_high_$M": p.ci_high,
            })
        classes = pd.DataFrame(rows)
    return totals, classes


def sdr_row_columns(totals: dict[str, SDREstimate], *, ndigits: int = 3) -> dict[str, float]:
    """``<name>_se`` / ``<name>_ci90_low`` / ``<name>_ci90_high`` for a results row,
    with the ``$M`` suffix kept at the end of each column name."""
    out: dict[str, float] = {}
    for name, est in totals.items():
        stem = name[:-3] if name.endswith("_$M") else name
        unit = "_$M" if name.endswith("_$M") else ""
        out[f"{stem}_se{unit}"] = round(est.se, ndigits)
        out[f"{stem}_ci90_low{unit}"] = round(est.ci_low, ndigits)
        out[f"{stem}_ci90_high{unit}"] = round(est.ci_high, ndigits)
    return out


__all__ = ["AGI_CLASSES", "TOTAL_KEYS", "act24_delta_sdr", "sdr_row_columns"]
