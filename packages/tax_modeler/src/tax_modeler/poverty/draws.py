"""Poverty headcounts from draws of who claims each credit, instead of an expected credit for everyone.

A credit's take-up is a probability: some units claim it and most of the rest do not. The SPM poverty line is a
threshold, so a headcount is not linear in the credit: giving every unit its expected amount (probability times
dollars) puts a household whose resources sit near the line on one side of it, always, and the headcount swings on
a handful of households (Act 24 branch, TY2028: one sample household of 603 persons crossed a $33 knife-edge and
moved "persons into poverty" by +603). Drawing the claims gives each unit its probability of being poor.

:class:`ClaimBlock` is one credit's claimants: the SPM unit each belongs to, the claim probability, and the dollars
each receives if it claims in the baseline and in every scenario. A claim is drawn once per unit and credit and is
common to the baseline and the scenarios, so a household's status is the same in both. :func:`poverty_draws` returns,
for the baseline and each scenario, every SPM unit's probability of resources below its threshold (the mean over
``n_draws`` draws), the mean poverty gap, and each draw's persons headcount so the Monte Carlo error can be reported.
:func:`tax_modeler.poverty.impact.compute_poverty_impact` takes these as ``poor_prob_*`` columns, so the sampling
standard errors by the replicate weights are computed on the draw-averaged probabilities (the replicate estimate is
the same function of the draws with the replicate weight, with the Monte Carlo noise averaged out; the Monte Carlo
error is reported separately).

``DrawSpec.resource_draws`` draws something other than claims as well: amounts added to every case's resources in
each draw, common to the baseline and the scenarios (``benefits.moop.moop_resource_draws``: medical spending drawn
from donors, less the expected amount the resources carry). With it the baseline is drawn too.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

DEFAULT_SEED = 20261120
DEFAULT_DRAWS = 2000


@dataclass(frozen=True)
class ClaimBlock:
    """One credit's claimants (one entry per tax unit).

    ``spm_unit_id``: the SPM unit of each tax unit (as ``spm_unit_id`` on the SPM frame). ``prob``: probability the
    unit claims. ``renewed``: dollars it receives in the baseline if it claims. ``scenario``: dollars it receives in
    each scenario if it claims (the scenario's own amount, not the change).
    """

    spm_unit_id: np.ndarray
    prob: np.ndarray
    renewed: np.ndarray
    scenario: Mapping[str, np.ndarray] = field(default_factory=dict)


@dataclass(frozen=True)
class DrawSpec:
    """What to draw. ``baseline_fixed(df)``: the SPM frame's baseline resources without the drawn credits (None: the
    baseline is not drawn and stays ``spm_resources``, unless ``resource_draws`` is set, when it is drawn from
    ``spm_resources``). ``scenario_fixed[name](df)``: each drawn scenario's resources without the drawn credits.
    ``resource_draws(df, rng, n)``: an ``(SPM units, n)`` array added to every case's resources in each of ``n``
    draws, from its own random stream so the claim draws are the same with it or without."""

    blocks: tuple[ClaimBlock, ...]
    baseline_fixed: Callable[[pd.DataFrame], np.ndarray] | None = None
    scenario_fixed: Mapping[str, Callable[[pd.DataFrame], np.ndarray]] = field(default_factory=dict)
    resource_draws: Callable[[pd.DataFrame, np.random.Generator, int], np.ndarray] | None = None
    n_draws: int = DEFAULT_DRAWS
    seed: int = DEFAULT_SEED
    chunk: int = 250


@dataclass(frozen=True)
class DrawResult:
    """``poor_prob``: scenario (and ``"baseline"``) -> per-SPM-unit probability of being poor. ``gap``: scenario ->
    per-unit mean of ``max(threshold - resources, 0)``. ``counts``: ``(weight name, scenario)`` -> that weight summed
    over the units below the line, in each draw (main weights): the Monte Carlo error. ``n_draws``, ``seed``."""

    poor_prob: dict[str, np.ndarray]
    gap: dict[str, np.ndarray]
    counts: dict[tuple[str, str], np.ndarray]
    n_draws: int
    seed: int


def poverty_draws(spec: DrawSpec, df: pd.DataFrame, threshold: np.ndarray, weights: Mapping[str, np.ndarray]) -> DrawResult:
    """Run the draws for the SPM frame ``df`` (needs ``spm_unit_id``); ``weights``: named per-unit weights whose total
    over the units below the line is recorded for every draw (persons, children, ...)."""
    from scipy import sparse

    n = len(df)
    index = pd.Index(df["spm_unit_id"])
    mats, probs = [], []
    for b in spec.blocks:
        code = index.get_indexer(b.spm_unit_id)
        ok = code >= 0
        m = sparse.csr_matrix((np.ones(int(ok.sum())), (code[ok], np.flatnonzero(ok))), shape=(n, len(code)))
        mats.append(m)
        probs.append(np.clip(np.asarray(b.prob, dtype=float), 0.0, 1.0))
    base_fixed = None if spec.baseline_fixed is None else np.asarray(spec.baseline_fixed(df), dtype=float)
    if base_fixed is None and spec.resource_draws is not None:
        base_fixed = df["spm_resources"].to_numpy(dtype=float)
    scn_fixed = {s: np.asarray(f(df), dtype=float) for s, f in spec.scenario_fixed.items()}
    names = (["baseline"] if base_fixed is not None else []) + list(scn_fixed)
    poor_sum = {k: np.zeros(n) for k in names}
    gap_sum = {k: np.zeros(n) for k in names}
    counts = {(w, k): [] for w in weights for k in names}
    rng = np.random.default_rng(spec.seed)
    resource_rng = np.random.default_rng([spec.seed, 1])
    done = 0
    while done < spec.n_draws:
        c = min(spec.chunk, spec.n_draws - done)
        claims = [rng.random((len(p), c)) < p[:, None] for p in probs]      # (tax units, c)
        recv: dict[str, np.ndarray] = {}
        if base_fixed is not None:
            recv["baseline"] = base_fixed[:, None] + sum(m @ (cl * b.renewed[:, None]) for m, cl, b in zip(mats, claims, spec.blocks, strict=True))
        for s, fixed in scn_fixed.items():
            recv[s] = fixed[:, None] + sum(m @ (cl * np.asarray(b.scenario[s], dtype=float)[:, None])
                                           for m, cl, b in zip(mats, claims, spec.blocks, strict=True) if s in b.scenario)
        if spec.resource_draws is not None:
            extra = np.asarray(spec.resource_draws(df, resource_rng, c), dtype=float)
            recv = {k: r + extra for k, r in recv.items()}
        for k, r in recv.items():
            poor = r < threshold[:, None]
            poor_sum[k] += poor.sum(axis=1)
            gap_sum[k] += np.maximum(threshold[:, None] - r, 0.0).sum(axis=1)
            for w, vec in weights.items():
                counts[(w, k)].append(np.asarray(vec, dtype=float) @ poor)
        done += c
    return DrawResult(
        poor_prob={k: v / spec.n_draws for k, v in poor_sum.items()},
        gap={k: v / spec.n_draws for k, v in gap_sum.items()},
        counts={k: np.concatenate(v) for k, v in counts.items()},
        n_draws=spec.n_draws, seed=spec.seed)


__all__ = ["ClaimBlock", "DrawSpec", "DrawResult", "poverty_draws", "DEFAULT_DRAWS", "DEFAULT_SEED"]
