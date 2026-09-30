"""The simulator's scoring population: the Act 24 page's projected tax units.

Built from the calibrated base with the same steps as the Act 24 estimates
(``tax_modeler.scenarios.act24_population``): for each revenue scenario
(:mod:`.scenarios`) and tax year 2027-2031, the projected units with every
field the income tax scorers read. Everything that does not depend on the
policy is done here, once; scoring a spec is then pure tax arithmetic.

Arrays are aligned to one record order (the MID units' ``filer_id`` order).
Per year, the MID scenario carries every unit's AGI, itemized deduction and
total cash income, and the LOW/HIGH scenarios carry only the units that
differ from MID (the top-income premium rows, and the 24 synthetic $1M+ units,
whose weight and capital-gains share also depend on the tail's Pareto index).

The distributional tables rank households on their summed total cash income
and cut them into fifths of households by the PUMS household weight
(``hh_weight``); a fifth's dollar totals sum each unit's change times its own
filer weight (``generate_quintile_report``). Each household's fifth is
recorded here per year (``quint_<year>``), with the projected frame's row
order (``order_<year>``), so the browser kernel and :func:`frame_for`
reproduce the published tables.

Capital gains: the arrays carry the model's own gains share (``cg``). The
simulator and the Act 24 page score on the DOTAX-anchored base instead
(:mod:`.gains`), which :func:`unit_arrays` applies by default.
"""
from __future__ import annotations

import json
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .scenarios import SCENARIOS, YEARS

FILING_STATUSES = ("single", "married_filing_jointly", "head_of_household",
                   "married_filing_separately")
QUINTILE_LABELS = ("Q1 (bottom 20%)", "Q2", "Q3", "Q4", "Q5 (top 20%)")
NO_QUINTILE = 255   # households whose units all weigh <= 0.01, which the tables drop
FORMAT_VERSION = 1


@dataclass
class Population:
    """Arrays (see module docstring) plus metadata; :meth:`save` / :meth:`load`."""

    arrays: dict[str, np.ndarray]
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def years(self) -> tuple[int, ...]:
        return tuple(self.meta["years"])

    @property
    def n_units(self) -> int:
        return len(self.arrays["fs"])

    def save(self, directory) -> Path:
        d = Path(directory)
        d.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(d / "population.npz", **self.arrays)
        (d / "population.json").write_text(json.dumps(self.meta, indent=2) + "\n")
        return d

    @classmethod
    def load(cls, directory) -> "Population":
        d = Path(directory)
        with np.load(d / "population.npz", allow_pickle=False) as z:
            arrays = {k: z[k] for k in z.files}
        return cls(arrays=arrays, meta=json.loads((d / "population.json").read_text()))


# ---------------------------------------------------------------------------
# building
# ---------------------------------------------------------------------------

def _scenario_worker(calibrated_path: str, key: str, years: tuple[int, ...]) -> dict:
    """Build one scenario's units and project them to every year (picklable)."""
    import logging
    import warnings
    warnings.filterwarnings("ignore")
    logging.disable(logging.WARNING)

    from tax_modeler.artifacts import load_calibrated_base
    from tax_modeler.scenarios.act24_population import build_units, project_units

    sc = SCENARIOS[key]
    base, ded_params, meta = load_calibrated_base(calibrated_path)
    units, tail_k = build_units(base, alpha=sc.alpha, top_premium=sc.top_premium,
                                ded_params=ded_params,
                                cal_tax_year=int(meta.get("tax_year", 2023)), enrich=True)
    out = {"units": units, "tail_k": tail_k, "years": {},
           "calibrated_meta": {k: v for k, v in meta.items() if k != "deduction_params"}}
    for y in years:
        out["years"][y] = project_units(units, year=y, top_premium=sc.top_premium)
    return out


def _positions(frame_ids: np.ndarray, canon_index: dict[str, int]) -> np.ndarray:
    return np.fromiter((canon_index[f] for f in frame_ids), dtype=np.int64, count=len(frame_ids))


def _household_quintiles(projected: pd.DataFrame, breaks: np.ndarray, year: int) -> pd.Series:
    """Per household: the fifth ``generate_quintile_report`` assigns."""
    from tax_modeler.config.tax_system_config import TaxCalculator
    from tax_modeler.reform.income_tax_spec import current_law_system
    from tax_modeler.scenarios.quintile_analysis import generate_quintile_report

    cfg = current_law_system(year)
    _, _, pu_sorted = generate_quintile_report(
        projected, cfg, cfg, {}, TaxCalculator(), scenario_params=None,
        quintile_breaks=breaks)
    return pu_sorted.groupby("hh_id", observed=True)["quintile"].first()


def build_population(calibrated_path, *, years=YEARS, max_workers: int | None = None) -> Population:
    """Build the scoring population from the calibrated base (a few minutes)."""
    from tax_modeler.scenarios.quintile_analysis import compute_quintile_breaks

    keys = list(SCENARIOS)
    with ProcessPoolExecutor(max_workers=max_workers or len(keys)) as pool:
        futures = {k: pool.submit(_scenario_worker, str(calibrated_path), k, tuple(years))
                   for k in keys}
        built = {k: f.result() for k, f in futures.items()}

    mid_units = built["mid"]["units"]
    canon_ids = mid_units["filer_id"].to_numpy().astype(str)
    canon_index = {f: i for i, f in enumerate(canon_ids)}
    for k in keys:
        ids = built[k]["units"]["filer_id"].to_numpy().astype(str)
        if not np.array_equal(ids, canon_ids):
            raise RuntimeError(f"scenario {k}: units differ from MID's")

    status_code = {s: i for i, s in enumerate(FILING_STATUSES)}
    bad = set(mid_units["filing_status"].unique()) - set(status_code)
    if bad:
        raise RuntimeError(f"unexpected filing statuses {bad}")
    hh_codes, hh_ids = pd.factorize(mid_units["hh_id"].astype(str), sort=False)
    H = len(hh_ids)
    arrays: dict[str, np.ndarray] = {
        "filer_id": canon_ids.astype("U"),
        "hh_id": np.asarray(hh_ids, dtype="U"),
        "hh": hh_codes.astype(np.int32),
        "fs": mid_units["filing_status"].map(status_code).to_numpy(np.uint8),
        "deps": mid_units["num_dependents"].to_numpy(np.float64),
        "weight": mid_units["weight"].to_numpy(np.float64),
        "cg": mid_units["synthetic_cg_share"].fillna(0.0).to_numpy(np.float64),
    }
    if np.isnan(arrays["deps"]).any():
        raise RuntimeError("NaN num_dependents in the population")
    arrays["deps"] = arrays["deps"].astype(np.uint8)
    hhw = mid_units.groupby(hh_codes)["hh_weight"].agg(["first", "nunique"])
    if (hhw["nunique"] > 1).any():
        raise RuntimeError("hh_weight differs within a household")
    arrays["hh_weight"] = hhw["first"].to_numpy(np.float64)
    breaks = compute_quintile_breaks(mid_units)

    for y in years:
        # ---- MID: every unit ------------------------------------------------
        proj = built["mid"]["years"][y]
        pos = _positions(proj["filer_id"].to_numpy().astype(str), canon_index)
        agi = np.empty(len(canon_ids)); item = np.empty(len(canon_ids)); tci = np.empty(len(canon_ids))
        agi[pos] = proj["income"].to_numpy(np.float64)
        item[pos] = proj["hi_itemized_deduction"].to_numpy(np.float64)
        tci[pos] = proj["total_cash_income"].to_numpy(np.float64)
        for col, ref in (("weight", arrays["weight"]), ("synthetic_cg_share", arrays["cg"]),
                         ("num_dependents", arrays["deps"].astype(float))):
            got = np.empty(len(canon_ids)); got[pos] = proj[col].fillna(0.0).to_numpy(np.float64)
            if not np.array_equal(got, ref):
                raise RuntimeError(f"TY{y}: projection changed {col}")
        if np.isnan(item).any():
            raise RuntimeError(f"TY{y}: NaN itemized deduction survived the refresh")
        arrays[f"agi_{y}"], arrays[f"item_{y}"], arrays[f"tci_{y}"] = agi, item, tci
        arrays[f"order_{y}"] = pos.astype(np.int32)

        fifth = _household_quintiles(proj, breaks, y)
        hh_of = {h: i for i, h in enumerate(hh_ids)}
        hi = np.fromiter((hh_of[str(h)] for h in fifth.index), dtype=np.int64, count=len(fifth))
        quint = np.full(H, NO_QUINTILE, dtype=np.uint8)
        quint[hi] = fifth.cat.codes.to_numpy().astype(np.uint8)
        arrays[f"quint_{y}"] = quint

        # ---- LOW / HIGH: only what differs from MID ------------------------
        for k in keys:
            if k == "mid":
                continue
            p2 = built[k]["years"][y]
            pos2 = _positions(p2["filer_id"].to_numpy().astype(str), canon_index)
            a2 = np.empty(len(canon_ids)); i2 = np.empty(len(canon_ids))
            a2[pos2] = p2["income"].to_numpy(np.float64)
            i2[pos2] = p2["hi_itemized_deduction"].to_numpy(np.float64)
            diff = np.flatnonzero((a2 != agi) | (i2 != item))
            arrays[f"ovr_{k}_{y}_idx"] = diff.astype(np.int32)
            arrays[f"ovr_{k}_{y}_agi"] = a2[diff]
            arrays[f"ovr_{k}_{y}_item"] = i2[diff]

    for k in keys:
        if k == "mid":
            continue
        u = built[k]["units"]
        w = u["weight"].to_numpy(np.float64); c = u["synthetic_cg_share"].fillna(0.0).to_numpy(np.float64)
        diff = np.flatnonzero((w != arrays["weight"]) | (c != arrays["cg"]))
        arrays[f"tail_{k}_idx"] = diff.astype(np.int32)
        arrays[f"tail_{k}_weight"] = w[diff]
        arrays[f"tail_{k}_cg"] = c[diff]

    meta = {
        "format_version": FORMAT_VERSION,
        "years": list(years),
        "filing_statuses": list(FILING_STATUSES),
        "quintile_labels": list(QUINTILE_LABELS),
        "quintile_breaks": [float(b) for b in breaks],
        "scenarios": {k: {"label": s.label, "alpha": s.alpha, "top_premium": s.top_premium,
                          "behavior": s.behavior, "tail_k": float(built[k]["tail_k"])}
                      for k, s in SCENARIOS.items()},
        "n_units": int(len(canon_ids)),
        "n_households": int(H),
        "calibrated_base": built["mid"]["calibrated_meta"],
    }
    return Population(arrays=arrays, meta=meta)


# ---------------------------------------------------------------------------
# reconstruction for the Python pipeline
# ---------------------------------------------------------------------------

GAINS_BASES = ("anchored", "model")


def unit_arrays(pop: Population, year: int, scenario: str = "mid",
                gains: str = "anchored") -> dict[str, np.ndarray]:
    """Canonical-order arrays for one (year, scenario): income, itemized
    deduction, weight and capital-gains share with LOW/HIGH overrides applied.

    ``gains``: ``"anchored"``, the simulator's DOTAX-anchored gains base
    (:mod:`.gains`, computed and cached on first use), or ``"model"``, the
    model's own shares, which the Act 24 page scores."""
    if gains not in GAINS_BASES:
        raise ValueError(f"gains: one of {GAINS_BASES}, not {gains!r}")
    a = pop.arrays
    agi = a[f"agi_{year}"].copy()
    item = a[f"item_{year}"].copy()
    weight = a["weight"].copy()
    cg = a["cg"].copy()
    if scenario != "mid":
        idx = a[f"ovr_{scenario}_{year}_idx"]
        agi[idx] = a[f"ovr_{scenario}_{year}_agi"]
        item[idx] = a[f"ovr_{scenario}_{year}_item"]
        t = a[f"tail_{scenario}_idx"]
        weight[t] = a[f"tail_{scenario}_weight"]
        cg[t] = a[f"tail_{scenario}_cg"]
    if gains == "anchored":
        from .gains import anchored_share
        cg = anchored_share(pop, year, scenario, cg)
    return {"income": agi, "hi_itemized_deduction": item, "weight": weight,
            "synthetic_cg_share": cg}


def frame_for(pop: Population, year: int, scenario: str = "mid",
              gains: str = "anchored") -> pd.DataFrame:
    """The frame the pipeline scored for (year, scenario), in its row order,
    with the columns the income tax scorers and ``generate_quintile_report``
    read; ``gains`` as in :func:`unit_arrays` (``"model"`` is the frame the
    Act 24 page scored)."""
    a = pop.arrays
    u = unit_arrays(pop, year, scenario, gains)
    order = a[f"order_{year}"]
    statuses = np.asarray(FILING_STATUSES)[a["fs"]]
    df = pd.DataFrame({
        "filer_id": a["filer_id"],
        "hh_id": a["hh_id"][a["hh"]],
        "filing_status": statuses,
        "num_dependents": a["deps"].astype(np.int64),
        "income": u["income"],
        "hi_agi": u["income"],
        "hi_itemized_deduction": u["hi_itemized_deduction"],
        "weight": u["weight"],
        "hh_weight": a["hh_weight"][a["hh"]],
        "synthetic_cg_share": u["synthetic_cg_share"],
        "total_cash_income": a[f"tci_{year}"],
    })
    return df.iloc[order].reset_index(drop=True)
