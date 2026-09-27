"""The simulator population in the form the estimates-site page loads.

Two files:

* ``population.json`` — metadata (years, statuses, scenarios and their
  behavioral parameters, current law and presets, credit tables, provenance)
  and an index of the arrays in the binary: name, dtype, element count and
  byte offset.
* ``population.bin.gz`` — the arrays, little-endian, each starting on an
  8-byte boundary, gzipped. The page decompresses it with the browser's
  ``DecompressionStream`` (GitHub Pages does not compress binary files).

Money and weights stay float64, so the kernel reads exactly the values the
Python reference scorer uses; codes and counts are packed small.
"""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

import numpy as np

from tax_modeler.config.tax_system_config import TaxCalculator
from tax_modeler.reform.income_tax_spec import FIRST_YEAR_RANGE, MAX_BRACKETS, MAX_FLOOR, current_law_system

from .population import Population
from .presets import current_law_vintages, presets
from .scenarios import SCENARIOS
from .systems import food_excise_tables, system_to_json

WEB_FORMAT_VERSION = 1

# AGI classes of generate_quintile_report (right-open), for the kernel's table.
AGI_CLASS_BREAKS = [10_000, 30_000, 60_000, 100_000, 175_000, 350_000, 500_000, 1_000_000]
AGI_CLASS_LABELS = ["Under $10K", "$10K–$30K", "$30K–$60K", "$60K–$100K", "$100K–$175K",
                    "$175K–$350K", "$350K–$500K", "$500K–$1M", "$1M+"]

_DTYPES = {"f8": "<f8", "u4": "<u4", "u1": "u1"}


def _web_arrays(pop: Population) -> dict[str, tuple[str, np.ndarray]]:
    a = pop.arrays
    out: dict[str, tuple[str, np.ndarray]] = {
        "fs": ("u1", a["fs"]), "deps": ("u1", a["deps"]), "hh": ("u4", a["hh"]),
        "weight": ("f8", a["weight"]), "cg": ("f8", a["cg"]), "hh_weight": ("f8", a["hh_weight"]),
    }
    # Household fifths and household weights: shipped once when every year
    # has the same (they do: fixed base-year breaks and a stable sort), else per year.
    for name, dtype in (("quint", "u1"), ("hhfw", "f8")):
        first = a[f"{name}_{pop.years[0]}"]
        if all(np.array_equal(first, a[f"{name}_{y}"], equal_nan=True) for y in pop.years):
            out[name] = (dtype, first)
        else:
            for y in pop.years:
                out[f"{name}_{y}"] = (dtype, a[f"{name}_{y}"])
    for y in pop.years:
        out[f"agi_{y}"] = ("f8", a[f"agi_{y}"])
        out[f"item_{y}"] = ("f8", a[f"item_{y}"])
        for k in SCENARIOS:
            if k == "mid":
                continue
            out[f"ovr_{k}_{y}_idx"] = ("u4", a[f"ovr_{k}_{y}_idx"])
            out[f"ovr_{k}_{y}_agi"] = ("f8", a[f"ovr_{k}_{y}_agi"])
            out[f"ovr_{k}_{y}_item"] = ("f8", a[f"ovr_{k}_{y}_item"])
    for k in SCENARIOS:
        if k == "mid":
            continue
        out[f"tail_{k}_idx"] = ("u4", a[f"tail_{k}_idx"])
        out[f"tail_{k}_weight"] = ("f8", a[f"tail_{k}_weight"])
        out[f"tail_{k}_cg"] = ("f8", a[f"tail_{k}_cg"])
    return out


def pack(arrays: dict[str, tuple[str, np.ndarray]]) -> tuple[bytes, list[dict]]:
    """Concatenate arrays on 8-byte boundaries; return (bytes, index)."""
    chunks, index, offset = [], [], 0
    for name, (dtype, arr) in arrays.items():
        data = np.ascontiguousarray(arr, dtype=_DTYPES[dtype]).tobytes()
        pad = (-offset) % 8
        chunks.append(b"\0" * pad)
        offset += pad
        index.append({"name": name, "dtype": dtype, "length": int(len(arr)), "offset": offset})
        chunks.append(data)
        offset += len(data)
    return b"".join(chunks), index


def model_version(pop: Population, blob: bytes, meta: dict, kernel_source: bytes = b"") -> str:
    """``<calibrated-base date>+<content hash>``. The hash covers everything a
    plan's numbers depend on: the population arrays, the metadata (current
    law, scenario parameters, credit tables) and the kernel's source. A
    rebuild that could change any result changes the version, so a share
    link made before it can say its numbers have moved; an identical rebuild
    keeps it."""
    h = hashlib.sha256(blob)
    h.update(json.dumps({k: v for k, v in meta.items() if k != "model_version"},
                        sort_keys=True).encode())
    h.update(kernel_source)
    base = pop.meta.get("calibrated_base", {})
    return f"{str(base.get('created_at', ''))[:10]}+{h.hexdigest()[:10]}"


def web_meta(pop: Population, index: list[dict], calc: TaxCalculator | None = None) -> dict:
    calc = calc or TaxCalculator()
    years = pop.years
    scen = {}
    for k, s in SCENARIOS.items():
        p = s.behavioral_params
        scen[k] = {"label": s.label, "alpha": s.alpha, "top_premium": s.top_premium,
                   "eti": p.eti, "migration_elast": p.migration_elast,
                   "migration_phase_in_years": p.migration_phase_in_years,
                   "pte_capture": p.pte_capture}
    agi0, weight = pop.arrays[f"agi_{years[0]}"], pop.arrays["weight"]
    return {
        "web_format_version": WEB_FORMAT_VERSION,
        # Identifies the model run (filled in by write_web_files): share links
        # carry it, so a link made with an earlier run can say its numbers have moved.
        "model_version": None,
        "years": list(years),
        "first_year_range": list(FIRST_YEAR_RANGE),
        "max_brackets": MAX_BRACKETS,
        "max_floor": MAX_FLOOR,
        "filing_statuses": pop.meta["filing_statuses"],
        "n_units": pop.n_units, "n_households": pop.meta["n_households"],
        "quintile_labels": pop.meta["quintile_labels"],
        "agi_class_breaks": AGI_CLASS_BREAKS, "agi_class_labels": AGI_CLASS_LABELS,
        "scenarios": scen,
        "current_law": {str(y): system_to_json(current_law_system(y), calc) for y in years},
        "current_law_vintages": current_law_vintages(calc),
        "presets": presets(calc),
        "food_excise": food_excise_tables(years),
        "provenance": pop.meta.get("calibrated_base", {}),
        # How thin the top is: the page says so next to any top-only change.
        # (Records that carry weight: the survey's own $1M+ records are
        # replaced by synthetic ones and keep a weight of 0.)
        "top_tail": {"tax_year": years[0],
                     "records_1m": int(((agi0 >= 1_000_000) & (weight > 0)).sum()),
                     "returns_1m": float(weight[agi0 >= 1_000_000].sum())},
        "arrays": index,
    }


def write_web_files(pop: Population, directory, *, kernel_source: bytes = b"") -> list[Path]:
    """``kernel_source``: the bytes of ``site/assets/simulator/kernel.js``,
    folded into the model version."""
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    blob, index = pack(_web_arrays(pop))
    meta = web_meta(pop, index)
    meta["model_version"] = model_version(pop, blob, meta, kernel_source)
    (d / "population.bin.gz").write_bytes(gzip.compress(blob, compresslevel=9, mtime=0))
    (d / "population.json").write_text(json.dumps(meta, indent=1) + "\n")
    return [d / "population.json", d / "population.bin.gz"]
