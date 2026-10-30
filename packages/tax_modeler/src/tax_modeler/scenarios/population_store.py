"""Act 24 populations built once per calibrated base and shared by the chain's steps.

The model chain builds and projects the same populations several times: the
MID units and their 2027-2031 projections in the capital-gains anchoring, the
MID scenario worker and the pre-Act-46 decomposition; the enriched MID units
in the quintile tables and the simulator. :class:`PopulationStore` keeps each
result in a file the first time a step computes it, and later calls with the
same arguments load it.

A store's key is the caller's fingerprint of the calibrated-base pickle and
the model code (``_forecast_common.fingerprint``). Step 1 rewrites that
pickle on every run, so a store never outlives the run that filled it, and
opening a store removes the directories of other keys.

Only frames the store itself handed out are cached: :meth:`PopulationStore.base`
loads the calibrated base, ``build_units`` / ``project_units`` (``store=``)
record what they return, and a frame the store did not produce, or one whose
shape, columns, weights or incomes changed since, is computed as usual.

numpy's global generator: each entry also saves the generator's state after
the computation, and a hit restores it, so later draws are what they would
have been (as ``forecast_working_family_credits.load_population`` does).
"""
from __future__ import annotations

import hashlib
import os
import pickle
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


class PopulationStore:
    """Results by name under ``directory/<key>``; see the module docstring."""

    def __init__(self, directory, key: str):
        self.key = key
        self.root = Path(directory)
        self.directory = self.root / key[:16]
        self.directory.mkdir(parents=True, exist_ok=True)
        for other in self.root.iterdir():
            if other.is_dir() and other != self.directory:
                shutil.rmtree(other, ignore_errors=True)
        self._names: dict[int, tuple[str, pd.DataFrame, str]] = {}

    # -- frames the store handed out ---------------------------------------
    def name_of(self, frame: pd.DataFrame) -> str | None:
        """The name ``frame`` was stored under, if the store produced this
        very object and it is unchanged; else None."""
        entry = self._names.get(id(frame))
        if entry is None or entry[1] is not frame or entry[2] != _checksum(frame):
            return None
        return entry[0]

    def _register(self, name: str, value: Any) -> None:
        frames = value if isinstance(value, tuple) else (value,)
        for frame in frames:
            if isinstance(frame, pd.DataFrame):
                self._names[id(frame)] = (name, frame, _checksum(frame))

    # -- cached computations -------------------------------------------------
    def get(self, name: str, compute: Callable[[], Any]) -> Any:
        """``compute()``, or the value saved under ``name`` for this key."""
        path = self.directory / f"{name}.pkl"
        try:
            with open(path, "rb") as f:
                key, value, state = pickle.load(f)
        except (OSError, EOFError, pickle.UnpicklingError, ValueError):
            key = None
        if key == self.key:
            np.random.set_state(state)
        else:
            value = compute()
            tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
            with open(tmp, "wb") as f:
                pickle.dump((self.key, value, np.random.get_state()), f,
                            protocol=pickle.HIGHEST_PROTOCOL)
            tmp.replace(path)
        self._register(name, value)
        return value

    def base(self, calibrated_path):
        """``artifacts.load_calibrated_base(calibrated_path)``, recorded so
        ``build_units(base, store=...)`` can be cached."""
        from tax_modeler.artifacts import load_calibrated_base

        base, ded_params, meta = load_calibrated_base(calibrated_path)
        self._register("base", base)
        return base, ded_params, meta


def _checksum(frame: pd.DataFrame) -> str:
    """Shape, columns, weights and incomes: enough to notice a frame changed
    in place after the store recorded it."""
    h = hashlib.blake2b(digest_size=16)
    h.update(repr((frame.shape, list(frame.columns))).encode())
    for col in ("weight", "income"):
        if col in frame.columns:
            h.update(np.ascontiguousarray(frame[col].to_numpy(dtype=float)).tobytes())
    return h.hexdigest()


def params_tag(value: Any) -> str:
    """A short, stable tag for an argument's value (deduction params, say)."""
    import json

    return hashlib.blake2b(json.dumps(value, sort_keys=True, default=repr).encode(),
                           digest_size=6).hexdigest()
