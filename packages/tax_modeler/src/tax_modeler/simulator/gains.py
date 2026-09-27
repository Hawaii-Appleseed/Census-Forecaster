"""The simulator's capital-gains base, anchored to DOTAX Table 21.

The population carries the model's own gains share per unit (``cg``, from IRS
SOI net capital gain, the Act 24 page's base). On this population that puts
about 17% too much at $1M+ and half or less of DOTAX's gains at $300K-$1M.
The simulator scores on the capital-gains page's base instead
(``tax_modeler.calibration.cg_anchor.anchor_nltcg``): each unit is placed in
a DOTAX AGI class by weighted income rank, and each class's model gains are
scaled by a factor ``k`` so that, in the MID scenario, the class holds DOTAX's
TY2022 resident net long-term gains grown to the tax year, with
``TOP_SHARE_1M`` of the $400K+ class at $1M+.

**DOTAX anchors MID; LOW and HIGH keep MID's factors.** LOW and HIGH use
MID's ``k`` for each class and year, applied to their own classes (each
scenario's own weighted income ranks) and their own model gains, so their
gains are not DOTAX's totals: they move with their own top incomes. DOTAX
corrects how the model spreads gains across income classes; LOW and HIGH
express uncertainty about top incomes (the tail's shape and the top-income
growth premium), and gains should move with that. Pinning each scenario to
DOTAX's totals instead pushed all of a scenario's extra top income into
ordinary income, which stretched the scenario range of Act 24 against Act 46
(five-year static, ratios to MID 0.91 / 1 / 1.21, against the Act 24 page's
1.02 / 1 / 1.10; with MID's factors, 1.00 / 1 / 1.10) and gave a plan that
changes only the gains rate almost no range across scenarios.

The anchored share is kept as a share, ``min(1, cg x k[class])``, so gains
still scale with income wherever income moves (the top-income premium, ETI):

* ``cgcls_<year>`` (u1): each unit's class code in the MID scenario, in
  canonical order (``CLASSES``; 0 wherever the unit has no gains);
* ``cgcls_<low|high>_<year>_idx`` / ``_val``: the units whose class differs
  in that scenario (ranks move with the tail and the top premium);
* ``meta["cg_anchor"]``: the top share, the scale factors ``k`` per scenario
  and year (``k[0]`` is 0: gains below the $100K class are unallocated;
  LOW's and HIGH's lists equal MID's, kept per scenario so the kernel and
  the web format read them the same way) and the anchoring rows per scenario
  and year: each class's model gains, anchored gains and MID's DOTAX target,
  $M (for MID, anchored = target).

:func:`ensure_gains_anchor` computes these once (under a second) and caches
them on the population; :func:`.population.unit_arrays` reads them.
"""
from __future__ import annotations

import numpy as np

from tax_modeler.calibration.cg_anchor import TOP_SHARE_1M, anchor_nltcg

from .population import Population, frame_for, unit_arrays
from .scenarios import SCENARIOS

#: Class codes (index = code): DOTAX AGI classes, $400K+ split at $1M.
CLASSES = ("lt100", "100_150", "150_200", "200_300", "300_400", "400_1m", "1mp")
_CODE = {c: i for i, c in enumerate(CLASSES)}

#: The scenario whose class totals are pinned to DOTAX; the others keep its k.
ANCHOR_SCENARIO = "mid"


def ensure_gains_anchor(pop: Population, top_share: float = TOP_SHARE_1M) -> dict:
    """Anchor ``pop``'s gains (see module docstring) unless already done;
    return ``pop.meta["cg_anchor"]``. Idempotent: an anchor already on the
    population is kept (``check_gains_anchor`` in the build script verifies it
    against DOTAX on every build)."""
    have = pop.meta.get("cg_anchor")
    if have is not None:
        if have["top_share"] != top_share:
            raise ValueError(f"population is anchored with top share {have['top_share']}, "
                             f"not {top_share}")
        return have
    a = pop.arrays
    order = [ANCHOR_SCENARIO] + [s for s in SCENARIOS if s != ANCHOR_SCENARIO]
    k_table: dict[str, dict[str, list[float]]] = {s: {} for s in order}
    row_table: dict[str, dict[str, list[dict]]] = {s: {} for s in order}
    codes: dict[tuple[str, int], np.ndarray] = {}
    for y in pop.years:
        for s in order:
            d = frame_for(pop, y, s, gains="model")
            # anchor_nltcg gives the scenario's own classes (by its weighted
            # income ranks) and, per class, its model gains and DOTAX target;
            # its scale is used for MID only.
            _, labels, rows = anchor_nltcg(d, y, top_share)
            c = np.empty(pop.n_units, dtype=np.uint8)
            c[a[f"order_{y}"]] = np.fromiter((_CODE[x] for x in labels), dtype=np.uint8,
                                             count=len(labels))
            c[unit_arrays(pop, y, s, gains="model")["synthetic_cg_share"] == 0] = 0
            codes[s, y] = c
            if s == ANCHOR_SCENARIO:
                k = [0.0] * len(CLASSES)
                for r in rows:
                    k[_CODE[r["group"]]] = float(r["scale"])
            else:
                k = list(k_table[ANCHOR_SCENARIO][str(y)])
            k_table[s][str(y)] = k
            row_table[s][str(y)] = [
                {"tax_year": y, "group": r["group"], "model_cg_M": r["model_cg_M"],
                 "anchored_cg_M": r["model_cg_M"] * k[_CODE[r["group"]]],
                 "dotax_target_M": r["dotax_target_M"], "scale": k[_CODE[r["group"]]]}
                for r in rows]
    for y in pop.years:
        mid = codes[ANCHOR_SCENARIO, y]
        a[f"cgcls_{y}"] = mid
        for s in SCENARIOS:
            if s == ANCHOR_SCENARIO:
                continue
            diff = np.flatnonzero(codes[s, y] != mid)
            a[f"cgcls_{s}_{y}_idx"] = diff.astype(np.int32)
            a[f"cgcls_{s}_{y}_val"] = codes[s, y][diff]
    pop.meta["cg_anchor"] = {"top_share": top_share, "classes": list(CLASSES),
                             "k": {s: k_table[s] for s in SCENARIOS},
                             "rows": {s: row_table[s] for s in SCENARIOS}}
    return pop.meta["cg_anchor"]


def gains_classes(pop: Population, year: int, scenario: str = "mid") -> np.ndarray:
    """Canonical-order class codes for (year, scenario)."""
    a = pop.arrays
    cls = a[f"cgcls_{year}"].copy()
    if scenario != "mid":
        cls[a[f"cgcls_{scenario}_{year}_idx"]] = a[f"cgcls_{scenario}_{year}_val"]
    return cls


def anchored_share(pop: Population, year: int, scenario: str, model_share: np.ndarray) -> np.ndarray:
    """``min(1, model share x k[class])`` for (year, scenario), canonical order;
    ``model_share`` is the scenario's (tail overrides applied)."""
    anchor = pop.meta.get("cg_anchor") or ensure_gains_anchor(pop)
    k = np.asarray(anchor["k"][scenario][str(year)], dtype=float)
    return np.minimum(1.0, model_share * k[gains_classes(pop, year, scenario)])
