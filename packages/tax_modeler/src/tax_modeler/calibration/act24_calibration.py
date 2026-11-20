"""The Act 24 base's calibration: every target at once, by bounded generalized raking.

The base used to be raked one margin at a time (``IPFCalibrationOrchestrator.calibrate_via_rake``): filer counts and
filing status jointly, then tax by class, then SOI AGI, round after round, until the misses stopped changing. Because
those targets cannot all be met by one margin at a time, it ended at a "compromise fixed-point" with its worst
filer-count class 18.8% over and tax by class 10% over, and said so only in a log line. This module states all the
targets as linear constraints on the weights and solves them together (:mod:`.bounded_raking`), with the weight
ratios to the raw PUMS weights held inside bounds (``[0.1, 10]`` by default; see ``DEFAULT_BOUNDS``).

Targets (each ``sum_i w_i x_ic = t_c``, units under $1M of AGI; the $1M+ class is the synthetic tail's), all from
the Department of Taxation's resident returns on Hawaiʻi AGI, so no universe scaling is needed, except the AGI
margin, which has no resident table and is the IRS's, scaled class by class:

* **filer counts** by the Department's Table A-8 AGI classes;
* **filing status**: single, joint and head of household, to Table 4's resident counts scaled to the same total as
  the class counts (the status targets include the $1M+ filers the tail stands for); married filing separately is
  then implied by the total;
* **tax before credits** by class (A-8) against the model's ``hi_tax_before_credits`` (before 2026-11-20 the net
  ``hi_state_tax`` was matched to these before-credits targets, which cannot be met in the classes where refundable
  credits exceed tax). Tax after credits (A-8) is not a target: with the before-credits targets and the family
  margins it conflicts by up to 12% (it needs the model's food/excise credit mass by class to match the
  Department's), so it is validation (``tax_after=True`` adds it);
* **AGI** by class from $10,000 up: IRS SOI Table 2 for Hawaiʻi, tax year 2023, federal AGI, each class scaled to the
  resident universe by Department returns over IRS returns on classes whose edges the two tables share
  (:func:`.family_margins.agi_targets_M`), used for the model's Hawaiʻi AGI in the Department's classes (the two AGIs
  average within 2% of each other in every class; the IRS's federal-AGI classes laid over the Department's Hawaiʻi-AGI
  classes make the system needlessly stiff);
* **families**, from Tables A-2, A-5 and A-6 (:func:`.dotax_base.family_targets`): head of household returns,
  dependent exemptions (the model's ``num_dependents``), and returns with three or more regular exemptions (self,
  spouse on a joint return, dependents: ``1 + joint + num_dependents >= 3``), each by coarse Hawaiʻi-AGI class.

The IRS family counts (head of household, nonrefundable CTC/ODC, ACTC) are no longer raked to; they are validation
(:func:`.family_margins.validation_table`).
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from . import dotax_base
from .bounded_raking import CalibrationResult, calibrate

logger = logging.getLogger(__name__)

TOP = 1_000_000.0
#: Weight-ratio bounds. The system was first tried at [0.2, 5]; with every target at once it is infeasible there
#: (best achievable: every target within 0.9%, worst AGI $100k-$200k, with 22% of weights on the 0.2 floor, same
#: answer at 3,000 iterations), and exact at [0.1, 10] with 2% of weights on the floor. Narrower bounds can be passed;
#: when a system is infeasible the solve returns its least-squares (relative misses) answer and says so, see
#: :class:`.bounded_raking.CalibrationResult`.
DEFAULT_BOUNDS = (0.1, 10.0)


#: Coarse Hawaiʻi-AGI classes of the family margins (Department class edges).
FAMILY_EDGES = {"hoh": (-np.inf, 10_000.0, 50_000.0, 75_000.0, 100_000.0),
                "dependents": (-np.inf, 10_000.0, 30_000.0, 50_000.0, 75_000.0, 100_000.0, 150_000.0),
                "exemptions_3plus": (-np.inf, 10_000.0, 30_000.0, 50_000.0, 75_000.0, 100_000.0, 150_000.0)}


def _label(lo, hi):
    f = lambda v: "-inf" if v == -np.inf else ("inf" if v == np.inf else f"{v / 1e3:,.0f}k")  # noqa: E731
    return f"{f(lo)}-{f(hi)}"


def constraints(df: pd.DataFrame, *, families: bool = True, irs_families: bool = False, tax_after: bool = False) -> tuple[list[str], np.ndarray, np.ndarray]:
    """``(names, X, targets)`` for the base ``df`` (needs ``agi``, ``filing_status``, ``hi_tax_before_credits``,
    ``hi_state_tax`` and ``num_dependents``). ``irs_families`` adds the IRS head of household,
    CTC/ODC and ACTC margins of #82 (off: they are validation)."""
    agi = df["agi"].to_numpy(float)
    cols, names, targets = [], [], []

    def add(name, x, t):
        names.append(name)
        cols.append(np.asarray(x, dtype=float))
        targets.append(float(t))

    filers = {k: v for k, v in dotax_base.filer_targets().items() if k[0] < TOP}
    total = float(sum(filers.values()))
    for (lo, hi), n in filers.items():
        add(f"filers {_label(lo, hi)}", (agi >= lo) & (agi < hi), n)
    status = dotax_base.status_targets()
    scale = total / float(sum(status.values()))
    for s in ("single", "married_filing_jointly", "head_of_household"):
        add(f"status {s}", (df["filing_status"] == s).to_numpy(), status[s] * scale)
    tb = df["hi_tax_before_credits"].to_numpy(float)
    ta = df["hi_state_tax"].to_numpy(float)
    for (lo, hi), t in dotax_base.tax_targets_M().items():
        if lo < TOP:
            add(f"tax before credits {_label(lo, hi)} $M", ((agi >= lo) & (agi < hi)) * tb / 1e6, t)
    if tax_after:
        for (lo, hi), t in dotax_base.tax_after_credits_M().items():
            if lo < TOP:
                add(f"tax after credits {_label(lo, hi)} $M", ((agi >= lo) & (agi < hi)) * ta / 1e6, t)
    from .family_margins import ALIGNED_EDGES, agi_targets_M

    for i, t in agi_targets_M().items():
        if i == 0:        # the IRS "under $1" class carries the loss returns' negative AGI; no weights reach it
            continue
        lo, hi = ALIGNED_EDGES[i], ALIGNED_EDGES[i + 1]
        add(f"agi {_label(lo, hi)} $M", ((agi >= lo) & (agi < hi)) * agi / 1e6, t)
    if families:
        n_dep = df["num_dependents"].fillna(0).to_numpy(float)
        joint = (df["filing_status"] == "married_filing_jointly").to_numpy()
        measures = {
            "hoh": (df["filing_status"] == "head_of_household").to_numpy().astype(float),
            "dependents": n_dep,
            "exemptions_3plus": ((1 + joint + n_dep) >= 3).astype(float),
        }
        names_ = {"hoh": "head of household returns", "dependents": "dependent exemptions",
                  "exemptions_3plus": "returns with 3+ exemptions"}
        for kind, x in measures.items():
            edges = np.asarray(FAMILY_EDGES[kind])
            cls = np.searchsorted(edges, agi, side="right") - 1
            tg = dotax_base.family_targets(kind, edges)
            if kind == "hoh":   # A-2's H/H column includes qualifying widow(er)s; bring it to Table 4's head-of-household count
                f = status["head_of_household"] * scale / sum(tg.values())
                tg = {k: v * f for k, v in tg.items()}
            for i in range(len(edges)):
                hi = edges[i + 1] if i + 1 < len(edges) else np.inf
                add(f"{names_[kind]} {_label(edges[i], hi)}", (cls == i) * x, tg[i])
    if irs_families:
        from .family_margins import MARGIN_EDGES, margin_codes

        codes = margin_codes(df)
        if codes is not None:
            for key, (c, tg) in codes.items():
                edges = MARGIN_EDGES[key]
                for i in range(len(edges)):
                    hi = edges[i + 1] if i + 1 < len(edges) else TOP
                    add(f"IRS {key} {_label(edges[i], hi)}", c == i + 1, tg[str(i + 1)])
    return names, np.column_stack(cols), np.array(targets)


def table_for(df: pd.DataFrame, weights, *, families: bool = True, irs_families: bool = False, tax_after: bool = False) -> pd.DataFrame:
    """Every constraint's target, value and relative miss at ``weights``."""
    names, X, t = constraints(df, families=families, irs_families=irs_families, tax_after=tax_after)
    v = (np.asarray(weights, dtype=float)[:, None] * X).sum(axis=0)
    return pd.DataFrame({"constraint": names, "target": t, "value": v, "relative_miss": v / t - 1.0})


def calibrate_act24(df: pd.DataFrame, *, bounds=DEFAULT_BOUNDS, families: bool = True, irs_families: bool = False,
                    tol: float = 1e-6) -> tuple[pd.DataFrame, CalibrationResult]:
    """The base with weights solved to every target together, and the full account of the solve.

    Weights are ``weight * ratio`` with each ratio inside ``bounds``; the frame's other columns are unchanged.
    ``result.exact`` says whether every target was met to ``tol``; if not, ``result.conflicts()`` lists what was
    missed and ``result.table`` has each constraint's achievable range under the bounds.
    """
    names, X, t = constraints(df, families=families, irs_families=irs_families)
    w0 = df["weight"].to_numpy(float)
    res = calibrate(w0, X, t, names=names, bounds=bounds, tol=tol)
    out = df.copy()
    out["weight"] = w0 * res.ratios
    out.attrs["calibration"] = res
    return out, res
