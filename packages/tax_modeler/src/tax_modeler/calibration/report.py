"""Calibration report: what the rake did to the weights, and what it hid.

The joint IPF (``ipf_orchestrator.calibrate_via_rake``) and the per-year
re-anchoring (``year_recalibrator.project_and_recalibrate``) each take steps
that silently move numbers without changing any input: a damped tax-margin
step clips its per-bin factor, the SOI AGI step has its own cap, the $1M+
tier rake clamps to [0.5, 2.0], bins whose DOTAX target is infeasible are
dropped from the margin, and non-convergence only ever reached a log line.
A forecast audit (October 2026) found that none of those were visible from
the scripts' output or their run manifests.

``CalibrationReport`` records all of it for one calibration stage:

* convergence (mode, iterations, final max relative deviation),
* every margin's target vs achieved value and its source year,
* every clip/cap event and every dropped bin,
* weight-dispersion diagnostics before and after (Kish design effect,
  effective sample size, max/mean and min/mean weight ratios, share of
  total weight in the top 1% of units),
* the statute-vs-COR wedge where the stage computes one.

Reports nest (``children``) so a script can persist its base rake plus each
projection year's re-anchoring as one JSON next to the run manifest.
``to_markdown()`` renders the same content for the console.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Leaf records
# ---------------------------------------------------------------------------

@dataclass
class MarginResidual:
    """One cell of one margin: its target and what the weights achieve."""
    margin: str                       # e.g. "dotax_filer_count"
    key: str                          # e.g. "$50k-$60k", "single"
    target: float
    achieved: float
    source: str = ""                  # e.g. "DOTAX Table A-8"
    source_year: Optional[int] = None
    counted: bool = True              # False: excluded from the convergence test

    @property
    def relative(self) -> float:
        """``(achieved - target) / target``; NaN when the target is zero."""
        if self.target == 0:
            return float("nan")
        return (self.achieved - self.target) / self.target


@dataclass
class ClipEvent:
    """A per-cell scaling factor that hit a cap or a clamp."""
    stage: str                        # e.g. "tax_total_step", "tier_rake"
    key: str
    requested: float                  # the factor the margin asked for
    applied: float                    # the factor after the cap/clamp
    bounds: Tuple[float, float]       # (lower, upper)
    iteration: Optional[int] = None


@dataclass
class DroppedBin:
    """A cell removed from a margin before raking (never fitted)."""
    stage: str
    key: str
    reason: str
    target: Optional[float] = None
    feasible: Optional[float] = None


@dataclass
class WeightDiagnostics:
    """Dispersion of one weight vector.

    ``kish_deff = n * sum(w^2) / sum(w)^2`` (Kish 1965) — the variance
    inflation unequal weights impose on a mean; ``effective_n = n / deff``.
    """
    n: int
    weight_sum: float
    kish_deff: float
    effective_n: float
    max_mean_ratio: float
    min_mean_ratio: float
    top1_share: float                 # share of total weight on the top 1% of units
    n_zero: int = 0                   # zero-weight units (counted in n, not in ratios)

    @classmethod
    def from_weights(cls, weights: Iterable[float]) -> "WeightDiagnostics":
        w = np.asarray(list(weights) if not isinstance(weights, np.ndarray) else weights,
                       dtype=float)
        w = w[np.isfinite(w)]
        n = int(w.size)
        if n == 0:
            return cls(0, 0.0, float("nan"), float("nan"), float("nan"),
                       float("nan"), float("nan"), 0)
        total = float(w.sum())
        sq = float((w ** 2).sum())
        deff = n * sq / total ** 2 if total > 0 else float("nan")
        ess = total ** 2 / sq if sq > 0 else float("nan")
        mean = total / n
        n_zero = int((w == 0).sum())
        positive = w[w > 0]
        top_k = max(1, int(math.ceil(0.01 * n)))
        top_share = float(np.sort(w)[::-1][:top_k].sum() / total) if total > 0 else float("nan")
        return cls(
            n=n,
            weight_sum=total,
            kish_deff=float(deff),
            effective_n=float(ess),
            max_mean_ratio=float(w.max() / mean) if mean > 0 else float("nan"),
            min_mean_ratio=float(positive.min() / mean) if positive.size and mean > 0 else float("nan"),
            top1_share=top_share,
            n_zero=n_zero,
        )


# ---------------------------------------------------------------------------
# Source-year bookkeeping
# ---------------------------------------------------------------------------

_WARNED_YEAR_MISMATCH: set = set()


def warn_once_on_year_mismatch(source_years: Dict[str, Optional[int]]) -> Optional[str]:
    """Emit one warning per distinct year combination when margins raked
    together come from different tax years. Returns the message (or None)
    so the caller can keep it in the report's ``warnings``."""
    years = {k: y for k, y in source_years.items() if y is not None}
    if len(set(years.values())) <= 1:
        return None
    msg = (
        "Calibration margins come from different tax years and are raked "
        "jointly: " + ", ".join(f"{k}=TY{y}" for k, y in sorted(years.items()))
        + ". The targets are used as published (no year adjustment)."
    )
    signature = tuple(sorted(years.items()))
    if signature not in _WARNED_YEAR_MISMATCH:
        _WARNED_YEAR_MISMATCH.add(signature)
        logger.warning(msg)
    return msg


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------

@dataclass
class CalibrationReport:
    stage: str                                     # "base_ipf_rake", "year_recalibration"
    converged: bool
    iterations: int
    max_iterations: int
    tolerance: float
    final_max_dev: Optional[float] = None
    convergence_mode: str = ""                     # "tolerance" | "fixed_point" | "max_iterations"
    target_year: Optional[int] = None
    margins: List[MarginResidual] = field(default_factory=list)
    clip_events: List[ClipEvent] = field(default_factory=list)
    dropped_bins: List[DroppedBin] = field(default_factory=list)
    weights_before: Optional[WeightDiagnostics] = None
    weights_after: Optional[WeightDiagnostics] = None
    source_years: Dict[str, Optional[int]] = field(default_factory=dict)
    statutory_tax_M: Optional[float] = None
    cor_tax_M: Optional[float] = None
    statute_vs_cor_wedge: Optional[float] = None
    warnings: List[str] = field(default_factory=list)
    children: List["CalibrationReport"] = field(default_factory=list)

    # -- derived -----------------------------------------------------------

    @property
    def margin_names(self) -> List[str]:
        seen: List[str] = []
        for m in self.margins:
            if m.margin not in seen:
                seen.append(m.margin)
        return seen

    def margin_summary(self) -> Dict[str, Dict[str, Any]]:
        """Per margin: cells, worst |relative| residual (counted cells),
        cells within tolerance, and the source year."""
        out: Dict[str, Dict[str, Any]] = {}
        for name in self.margin_names:
            cells = [m for m in self.margins if m.margin == name]
            counted = [m for m in cells if m.counted and not math.isnan(m.relative)]
            rel = [abs(m.relative) for m in counted]
            out[name] = {
                "cells": len(cells),
                "counted": len(counted),
                "max_abs_relative": max(rel) if rel else None,
                "within_tolerance": sum(r <= self.tolerance for r in rel),
                "source": cells[0].source,
                "source_year": cells[0].source_year,
            }
        return out

    def all_clip_events(self) -> List[ClipEvent]:
        evs = list(self.clip_events)
        for c in self.children:
            evs.extend(c.all_clip_events())
        return evs

    def any_nonconverged(self) -> bool:
        return (not self.converged) or any(c.any_nonconverged() for c in self.children)

    # -- serialisation -----------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # asdict recurses into children already; make relative explicit.
        d["margins"] = [{**asdict(m), "relative": _nan_to_none(m.relative)} for m in self.margins]
        d["margin_summary"] = self.margin_summary()
        d["children"] = [c.to_dict() for c in self.children]
        return _nan_to_none(d)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CalibrationReport":
        d = dict(d)
        d.pop("margin_summary", None)
        margins = [MarginResidual(**{k: v for k, v in m.items() if k != "relative"})
                   for m in d.pop("margins", [])]
        clips = [ClipEvent(**{**c, "bounds": tuple(c["bounds"])}) for c in d.pop("clip_events", [])]
        dropped = [DroppedBin(**b) for b in d.pop("dropped_bins", [])]
        wb = d.pop("weights_before", None)
        wa = d.pop("weights_after", None)
        children = [cls.from_dict(c) for c in d.pop("children", [])]
        return cls(
            margins=margins, clip_events=clips, dropped_bins=dropped,
            weights_before=WeightDiagnostics(**wb) if wb else None,
            weights_after=WeightDiagnostics(**wa) if wa else None,
            children=children, **d,
        )

    def to_json(self, path: Path | str) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)
            f.write("\n")
        return path

    @classmethod
    def from_json(cls, path: Path | str) -> "CalibrationReport":
        with open(path) as f:
            return cls.from_dict(json.load(f))

    # -- rendering ---------------------------------------------------------

    def to_markdown(self, *, level: int = 3, max_cells: int = 40) -> str:
        h = "#" * level
        title = self.stage if self.target_year is None else f"{self.stage} TY{self.target_year}"
        lines = [f"{h} Calibration report — {title}", ""]

        status = "converged" if self.converged else "DID NOT CONVERGE"
        mode = f" ({self.convergence_mode})" if self.convergence_mode else ""
        dev = (f", final max rel. dev {self.final_max_dev:.4f}"
               if self.final_max_dev is not None else "")
        lines.append(f"- **Convergence:** {status}{mode} — {self.iterations}/{self.max_iterations} "
                     f"iterations, tol {self.tolerance:g}{dev}")

        if self.source_years:
            yrs = ", ".join(f"{k}: {'TY' + str(y) if y is not None else 'n/a'}"
                            for k, y in self.source_years.items())
            lines.append(f"- **Margin source years:** {yrs}")

        if self.statute_vs_cor_wedge is not None:
            lines.append(
                f"- **Statute-vs-COR wedge:** {self.statute_vs_cor_wedge:.4f} "
                f"(statutory ${_fmt(self.statutory_tax_M)}M vs COR ${_fmt(self.cor_tax_M)}M)"
            )

        for label, w in (("before", self.weights_before), ("after", self.weights_after)):
            if w is None:
                continue
            lines.append(
                f"- **Weights {label}:** n={w.n:,} (zero: {w.n_zero:,}), sum={w.weight_sum:,.0f}, "
                f"Kish deff={w.kish_deff:.3f}, ESS={w.effective_n:,.0f}, "
                f"max/mean={w.max_mean_ratio:.2f}, min/mean={w.min_mean_ratio:.4f}, "
                f"top-1% share={w.top1_share:.1%}"
            )

        for w in self.warnings:
            lines.append(f"- **Warning:** {w}")

        summ = self.margin_summary()
        if summ:
            lines += ["", "| Margin | Source | Cells | Within tol | Max abs rel. residual |",
                      "|---|---|---:|---:|---:|"]
            for name, s in summ.items():
                src = s["source"] + (f" (TY{s['source_year']})" if s["source_year"] else "")
                mx = "—" if s["max_abs_relative"] is None else f"{s['max_abs_relative']:.4f}"
                lines.append(f"| {name} | {src} | {s['cells']} | {s['within_tolerance']}/{s['counted']} | {mx} |")

        if self.margins and max_cells > 0:
            lines += ["", "| Margin | Cell | Target | Achieved | Rel. | Counted |",
                      "|---|---|---:|---:|---:|:---:|"]
            for m in self.margins[:max_cells]:
                rel = "—" if math.isnan(m.relative) else f"{m.relative:+.3%}"
                lines.append(f"| {m.margin} | {m.key} | {_fmt(m.target)} | {_fmt(m.achieved)} | {rel} | "
                             f"{'yes' if m.counted else 'no'} |")
            if len(self.margins) > max_cells:
                lines.append(f"| … | {len(self.margins) - max_cells} more cells | | | | |")

        if self.dropped_bins:
            lines += ["", "**Dropped bins (never fitted):**"]
            for b in self.dropped_bins:
                extra = ""
                if b.target is not None:
                    extra = f" — target {_fmt(b.target)}"
                    if b.feasible is not None:
                        extra += f", feasible {_fmt(b.feasible)}"
                lines.append(f"- {b.stage} {b.key}: {b.reason}{extra}")

        if self.clip_events:
            n = len(self.clip_events)
            lines += ["", f"**Clip/cap events: {n}**"]
            by_stage: Dict[str, List[ClipEvent]] = {}
            for e in self.clip_events:
                by_stage.setdefault(e.stage, []).append(e)
            for stage, evs in by_stage.items():
                worst = max(evs, key=lambda e: abs(math.log(e.requested / e.applied)) if e.applied > 0 else 0)
                iters = sorted({e.iteration for e in evs if e.iteration is not None})
                it = f", iterations {iters[0]}–{iters[-1]}" if iters else ""
                lines.append(
                    f"- {stage}: {len(evs)} events, bounds [{worst.bounds[0]:g}, {worst.bounds[1]:g}]{it}; "
                    f"largest: {worst.key} asked ×{worst.requested:.3f}, applied ×{worst.applied:.3f}"
                )
        else:
            lines += ["", "**Clip/cap events: none**"]

        for c in self.children:
            lines += ["", c.to_markdown(level=level + 1, max_cells=0)]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def bracket_label(lo: float, hi: float) -> str:
    if hi == float("inf") or (isinstance(hi, float) and math.isinf(hi)):
        return f"${lo / 1000:.0f}k+"
    return f"${lo / 1000:.0f}k-${hi / 1000:.0f}k"


def _fmt(x: Optional[float]) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    if abs(x) >= 1000:
        return f"{x:,.0f}"
    return f"{x:,.2f}"


def _nan_to_none(obj: Any) -> Any:
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: _nan_to_none(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_nan_to_none(v) for v in obj]
    return obj


__all__ = [
    "CalibrationReport",
    "ClipEvent",
    "DroppedBin",
    "MarginResidual",
    "WeightDiagnostics",
    "bracket_label",
    "warn_once_on_year_mismatch",
]
