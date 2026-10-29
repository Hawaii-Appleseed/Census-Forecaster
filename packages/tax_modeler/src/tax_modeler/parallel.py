"""Run independent pieces of a model step at the same time, in order.

:func:`fork_map` is ``[fn(x) for x in items]`` spread over forked worker
processes. Results come back in the order of ``items`` whatever order the
workers finish in, so a step that collects them gives the same output as the
serial loop. Workers are forked, so ``fn`` may be a closure over large frames
(nothing is pickled on the way in; only results are pickled on the way back).
Where the platform cannot fork, or there is only one item, it runs serially.

``CF_MAX_WORKERS`` caps the workers per call, so a runner that starts several
steps at once (``scripts/rerun_models.py``) can share the cores between them.
A caller that runs several fork_maps at once (one per scenario, say) gives
each a share of that cap as ``cap``.
"""
from __future__ import annotations

import multiprocessing as mp
import os
from collections.abc import Callable, Iterable
from concurrent.futures import ProcessPoolExecutor
from typing import Any

_FORK_FN: list[Callable[[Any], Any]] = []


def _call(item: Any) -> Any:
    return _FORK_FN[-1](item)


def max_workers(n_items: int, cap: int | None = None) -> int:
    limit = os.cpu_count() or 1
    env = os.environ.get("CF_MAX_WORKERS")
    if env:
        limit = min(limit, max(1, int(env)))
    if cap is not None:
        limit = min(limit, max(1, cap))
    return max(1, min(n_items, limit))


def fork_map(fn: Callable[[Any], Any], items: Iterable[Any], *,
             cap: int | None = None) -> list[Any]:
    items = list(items)
    workers = max_workers(len(items), cap)
    if workers == 1 or "fork" not in mp.get_all_start_methods():
        return [fn(x) for x in items]
    _FORK_FN.append(fn)
    try:
        with ProcessPoolExecutor(max_workers=workers, mp_context=mp.get_context("fork")) as pool:
            return list(pool.map(_call, items))
    finally:
        _FORK_FN.pop()
