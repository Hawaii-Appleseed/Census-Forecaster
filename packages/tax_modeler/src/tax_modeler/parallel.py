"""Run independent pieces of a model step at the same time, in order.

:func:`fork_map` is ``[fn(x) for x in items]`` spread over forked worker
processes. Results come back in the order of ``items`` whatever order the
workers finish in, so a step that collects them gives the same output as the
serial loop. Workers are forked, so ``fn`` may be a closure over large frames
(nothing is pickled on the way in; only results are pickled on the way back).
Where the platform cannot fork, or there is only one item, it runs serially.

``CF_MAX_WORKERS`` caps the workers per call, so a runner that starts several
steps at once (``scripts/rerun_models.py``) can share the cores between them.

:func:`process_pool` is the pool for callers that submit picklable top-level
functions and do other work while they run (the scenario workers of
``forecast_sb3125_enhanced.py``, the simulator's population build).
"""
from __future__ import annotations

import gc
import multiprocessing as mp
import os
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager
from typing import Any

_FORK_FN: list[Callable[[Any], Any]] = []


def _call(item: Any) -> Any:
    return _FORK_FN[-1](item)


def max_workers(n_items: int) -> int:
    cap = os.cpu_count() or 1
    env = os.environ.get("CF_MAX_WORKERS")
    if env:
        cap = min(cap, max(1, int(env)))
    return max(1, min(n_items, cap))


def fork_map(fn: Callable[[Any], Any], items: Iterable[Any]) -> list[Any]:
    items = list(items)
    workers = max_workers(len(items))
    if workers == 1 or "fork" not in mp.get_all_start_methods():
        return [fn(x) for x in items]
    _FORK_FN.append(fn)
    try:
        with ProcessPoolExecutor(max_workers=workers, mp_context=mp.get_context("fork")) as pool:
            return list(pool.map(_call, items))
    finally:
        _FORK_FN.pop()


def process_pool(max_workers: int) -> ProcessPoolExecutor:
    """A ProcessPoolExecutor whose workers are forked where the platform can
    fork, so they start with this process's imports instead of importing
    pandas, numpy and the model again, and spawned (the default) elsewhere.
    Submit to it inside :func:`forking`."""
    context = mp.get_context("fork") if "fork" in mp.get_all_start_methods() else None
    return ProcessPoolExecutor(max_workers=max_workers, mp_context=context)


@contextmanager
def forking() -> Iterator[None]:
    """Freeze this process's objects while worker processes are forked.

    A forked worker shares the parent's memory until either writes to it, and
    the garbage collector writes to every object it scans: a worker's first
    full collection would copy the pages of every frame it inherited.
    ``gc.freeze`` moves them out of the collector's reach in the workers;
    this process unfreezes them once the workers exist. A fork-context
    ProcessPoolExecutor (Python 3.12+) starts all its workers on the first
    ``submit``, so wrap the submits.
    """
    gc.freeze()
    try:
        yield
    finally:
        gc.unfreeze()
