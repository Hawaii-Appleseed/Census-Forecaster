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

Housing assistance is drawn as well when the SPM frame carries ``housing_value`` and ``housing_takeup_prob``
(``benefits.housing.housing_takeup``): the resources hold the expected value, and each draw adds the value or not
by the household's own uniform, less the expected value. With it the baseline is drawn too.

Common random numbers. Every draw is a uniform number computed from a hash of the row's own key, the stream (the
resource draws, or which claim block) and the draw's index (:func:`uniforms`), not taken in turn from one generator.
So a run that changes one household's income changes that household's draws and no one else's: a unit entering or
leaving a block, or moving to another donor pool, no longer shifts every row after it onto other numbers. Until
November 25, 2026 the draws came from one stream in row order, and a small change in incomes (CBO's growth for
interest income alone, which barely reaches the households that lose SNAP) moved H.R.1's people pushed into poverty
from 852 to 689 by reshuffling who was drawn.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

DEFAULT_SEED = 20261120
DEFAULT_DRAWS = 2000

_GOLDEN = np.uint64(0x9E3779B97F4A7C15)
HOUSING_STREAM = 1_000_000   # claim blocks take 1, 2, ...; resource draws 0


def _housing_columns(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray] | None:
    """(value, probability) of housing assistance per SPM unit when the frame carries them and some is uncertain."""
    if "housing_value" not in df.columns or "housing_takeup_prob" not in df.columns:
        return None
    value = df["housing_value"].fillna(0).to_numpy(dtype=float)
    prob = df["housing_takeup_prob"].fillna(0).clip(0, 1).to_numpy(dtype=float)
    if not ((value > 0) & (prob > 0) & (prob < 1)).any():
        return None
    return value, prob


def _splitmix64(x: np.ndarray) -> np.ndarray:
    """The splitmix64 finalizer, elementwise on uint64 (wrapping arithmetic)."""
    with np.errstate(over="ignore"):
        x = (x ^ (x >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
        x = (x ^ (x >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
        return x ^ (x >> np.uint64(31))


def stable_keys(ids) -> np.ndarray:
    """A uint64 key per id, the same on every run and machine (``pandas.util.hash_array`` on the ids as strings)."""
    return pd.util.hash_array(np.asarray([str(i) for i in ids], dtype=object))


def uniforms(keys: np.ndarray, stream: int, seed: int, start: int, n: int) -> np.ndarray:
    """``(len(keys), n)`` uniform numbers in [0, 1): row ``i``, draw ``start + j`` depends only on ``keys[i]``,
    ``stream``, ``seed`` and ``start + j``."""
    with np.errstate(over="ignore"):
        salt = _splitmix64(np.array([np.uint64(seed) * _GOLDEN + np.uint64(stream)], dtype=np.uint64))[0]
        base = _splitmix64(np.asarray(keys, dtype=np.uint64) ^ salt)
        draw = np.arange(start, start + n, dtype=np.uint64) * _GOLDEN
        x = _splitmix64(base[:, None] + draw[None, :])
    return (x >> np.uint64(11)).astype(np.float64) * (1.0 / 9007199254740992.0)


def block_keys(spm_unit_id: np.ndarray) -> np.ndarray:
    """Default row keys for a claim block: the SPM unit and the row's place among that unit's rows in the block."""
    sid = pd.Series(np.asarray(spm_unit_id, dtype=object)).astype(str)
    return stable_keys(sid + "#" + sid.groupby(sid).cumcount().astype(str))


@dataclass(frozen=True)
class ClaimBlock:
    """One credit's claimants (one entry per tax unit).

    ``spm_unit_id``: the SPM unit of each tax unit (as ``spm_unit_id`` on the SPM frame). ``prob``: probability the
    unit claims. ``renewed``: dollars it receives in the baseline if it claims. ``scenario``: dollars it receives in
    each scenario if it claims (the scenario's own amount, not the change). ``key``: each row's identity for its
    draws (uint64, :func:`stable_keys`); None: the SPM unit and the row's place among that unit's rows
    (:func:`block_keys`), stable as long as a unit's own rows keep their order.
    """

    spm_unit_id: np.ndarray
    prob: np.ndarray
    renewed: np.ndarray
    scenario: Mapping[str, np.ndarray] = field(default_factory=dict)
    key: np.ndarray | None = None


@dataclass(frozen=True)
class DrawSpec:
    """What to draw. ``baseline_fixed(df)``: the SPM frame's baseline resources without the drawn credits (None: the
    baseline is not drawn and stays ``spm_resources``, unless ``resource_draws`` is set, when it is drawn from
    ``spm_resources``). ``scenario_fixed[name](df)``: each drawn scenario's resources without the drawn credits.
    ``resource_draws(df, u)``: given ``u``, an ``(SPM units, n)`` array of uniform numbers keyed by each unit's
    ``spm_unit_id`` (stream 0), an ``(SPM units, n)`` array added to every case's resources in each of the ``n``
    draws. Claim blocks use streams 1, 2, ..., so the claim draws are the same with it or without."""

    blocks: tuple[ClaimBlock, ...]
    baseline_fixed: Callable[[pd.DataFrame], np.ndarray] | None = None
    scenario_fixed: Mapping[str, Callable[[pd.DataFrame], np.ndarray]] = field(default_factory=dict)
    resource_draws: Callable[[pd.DataFrame, np.ndarray], np.ndarray] | None = None
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


def _draw_chunks(spec: DrawSpec, df: pd.DataFrame):
    """Yield, chunk by chunk, ``{case: (SPM units, chunk) resources}`` for the baseline (when drawn) and each
    scenario in ``spec.scenario_fixed``."""
    from scipy import sparse

    n = len(df)
    index = pd.Index(df["spm_unit_id"])
    mats, probs, keys = [], [], []
    for b in spec.blocks:
        code = index.get_indexer(b.spm_unit_id)
        ok = code >= 0
        m = sparse.csr_matrix((np.ones(int(ok.sum())), (code[ok], np.flatnonzero(ok))), shape=(n, len(code)))
        mats.append(m)
        probs.append(np.clip(np.asarray(b.prob, dtype=float), 0.0, 1.0))
        keys.append(block_keys(b.spm_unit_id) if b.key is None else np.asarray(b.key, dtype=np.uint64))
    base_fixed = None if spec.baseline_fixed is None else np.asarray(spec.baseline_fixed(df), dtype=float)
    housing = _housing_columns(df)
    if base_fixed is None and (spec.resource_draws is not None or housing is not None):
        base_fixed = df["spm_resources"].to_numpy(dtype=float)
    scn_fixed = {s: np.asarray(f(df), dtype=float) for s, f in spec.scenario_fixed.items()}
    unit_keys = (stable_keys(df["spm_unit_id"]) if spec.resource_draws is not None or housing is not None
                 else None)
    done = 0
    while done < spec.n_draws:
        c = min(spec.chunk, spec.n_draws - done)
        claims = [uniforms(k, i + 1, spec.seed, done, c) < p[:, None]        # (tax units, c)
                  for i, (k, p) in enumerate(zip(keys, probs, strict=True))]
        recv: dict[str, np.ndarray] = {}
        if base_fixed is not None:
            recv["baseline"] = base_fixed[:, None] + sum(m @ (cl * b.renewed[:, None]) for m, cl, b in zip(mats, claims, spec.blocks, strict=True))
        for s, fixed in scn_fixed.items():
            recv[s] = fixed[:, None] + sum(m @ (cl * np.asarray(b.scenario[s], dtype=float)[:, None])
                                           for m, cl, b in zip(mats, claims, spec.blocks, strict=True) if s in b.scenario)
        if spec.resource_draws is not None:
            extra = np.asarray(spec.resource_draws(df, uniforms(unit_keys, 0, spec.seed, done, c)), dtype=float)
            recv = {k: r + extra for k, r in recv.items()}
        if housing is not None:
            value, prob = housing
            takes = uniforms(unit_keys, HOUSING_STREAM, spec.seed, done, c) < prob[:, None]
            extra = value[:, None] * (takes - prob[:, None])
            recv = {k: r + extra for k, r in recv.items()}
        yield recv
        done += c


def poverty_draws(spec: DrawSpec, df: pd.DataFrame, threshold: np.ndarray, weights: Mapping[str, np.ndarray]) -> DrawResult:
    """Run the draws for the SPM frame ``df`` (needs ``spm_unit_id``); ``weights``: named per-unit weights whose total
    over the units below the line is recorded for every draw (persons, children, ...)."""
    poor_sum: dict[str, np.ndarray] = {}
    gap_sum: dict[str, np.ndarray] = {}
    counts: dict[tuple[str, str], list] = {}
    for recv in _draw_chunks(spec, df):
        for k, r in recv.items():
            poor = r < threshold[:, None]
            poor_sum[k] = poor_sum.get(k, 0) + poor.sum(axis=1)
            gap_sum[k] = gap_sum.get(k, 0) + np.maximum(threshold[:, None] - r, 0.0).sum(axis=1)
            for w, vec in weights.items():
                counts.setdefault((w, k), []).append(np.asarray(vec, dtype=float) @ poor)
    return DrawResult(
        poor_prob={k: v / spec.n_draws for k, v in poor_sum.items()},
        gap={k: v / spec.n_draws for k, v in gap_sum.items()},
        counts={k: np.concatenate(v) for k, v in counts.items()},
        n_draws=spec.n_draws, seed=spec.seed)


def resources_by_draw(spec: DrawSpec, df: pd.DataFrame) -> dict[str, np.ndarray]:
    """Every SPM unit's resources in every draw: ``{case: (SPM units, n_draws)}`` for the baseline (when drawn) and
    each scenario in ``spec.scenario_fixed``, the same draws :func:`poverty_draws` scores. For scoring many policies
    against one set of draws."""
    out: dict[str, list] = {}
    for recv in _draw_chunks(spec, df):
        for k, r in recv.items():
            out.setdefault(k, []).append(r)
    return {k: np.concatenate(v, axis=1) for k, v in out.items()}


__all__ = ["ClaimBlock", "DrawSpec", "DrawResult", "poverty_draws", "resources_by_draw", "stable_keys", "uniforms",
           "block_keys", "DEFAULT_DRAWS", "DEFAULT_SEED"]
