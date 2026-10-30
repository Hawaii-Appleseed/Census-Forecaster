"""Empirical validation of REEC demand scenarios against DPP solar permits.

The SB 3125 forecast projects residential REEC demand as
``base_2023 × income_growth(y) × demand_factor(y)``, where the demand
factors are scenario assumptions (SEIA-anchored, Hawaii-tempered — see
``REEC_DEMAND_SCENARIOS``). Honolulu DPP solar permits are the physical
counterpart: every credited system needs a permit, permits lead claims
by months rather than the ~2-year DOTAX lag, and the bundled aggregates
(``census_forecaster/data/dpp_permits/solar_permits.json``, refreshed by
``python -m census_forecaster.scripts.refresh_dpp_solar``) extend past
the last DOTAX actual (TY2022) into the *projected* vintages.

Read-only diagnostics: nothing here feeds the forecast. The comparison
is published in SB3125_CD1_FORECAST.md; promoting permits to an actual
demand-factor input is a scenario-design decision, not a data hookup.

Interpretation caveats (also in the JSON's ``limitations``):

* Declared permit value ≠ credit basis — compare *ratios*, not levels.
* Oʻahu only (~dominant share of residential REEC, but not all of it).
* The final year is partial; comparisons for it use H1-over-H1 windows.
"""
from __future__ import annotations

import json
from collections.abc import Iterable

from .sb3125_cd1_credits import (
    BASE_YEAR,
    REEC_DEMAND_SCENARIOS,
    _hawaii_nominal_growth,
    _reec_demand_factor,
)


def load_dpp_solar() -> dict:
    """Load the bundled DPP solar-permit aggregates."""
    from importlib.resources import files
    path = (files("census_forecaster") / "data" / "dpp_permits"
            / "solar_permits.json")
    with path.open() as f:
        return json.load(f)


def _partial_year(data: dict) -> int | None:
    """The calendar year the dataset only partially covers, if any."""
    max_iso = data.get("max_issuedate") or ""
    if len(max_iso) >= 7 and int(max_iso[5:7]) < 12:
        return int(max_iso[:4])
    return None


def empirical_demand_factors(
    basis: str = "value",
    base_year: int = BASE_YEAR,
) -> dict[int, float]:
    """Residential solar-permit factors normalized to ``base_year`` = 1.0.

    ``basis`` — 'value' (declared job value; comparable to the model's
    dollar-denominated demand path) or 'count' (permit counts; free of
    price/system-size drift).

    Complete years use full-year ratios. The dataset's partial final
    year uses the H1(y)/H1(base_year) ratio so the comparison window is
    like-for-like; it never mixes a half year against a full year.
    """
    if basis not in ("value", "count"):
        raise ValueError(f"basis must be 'value' or 'count', got {basis!r}")
    key = "res_val" if basis == "value" else "res_n"

    data = load_dpp_solar()
    annual = data["annual"]
    h1 = data["h1_residential"]
    partial = _partial_year(data)

    base = annual.get(str(base_year), {}).get(key)
    if not base:
        raise ValueError(f"no {key} for base year {base_year} in bundle")
    h1_key = "res_val" if basis == "value" else "res_n"
    h1_base = h1.get(str(base_year), {}).get(h1_key)

    out: dict[int, float] = {}
    for y_str, rec in annual.items():
        y = int(y_str)
        if y == partial:
            h1_y = h1.get(y_str, {}).get(h1_key)
            if h1_y and h1_base:
                out[y] = h1_y / h1_base
            continue
        if rec.get(key):
            out[y] = rec[key] / base
    return out


def compare_with_model(
    years: Iterable[int] = (2024, 2025, 2026),
    scenarios: Iterable[str] | None = None,
) -> list[dict]:
    """Empirical permit factors vs the model's ``g(y) × d(y)`` demand path.

    The model's projected demand relative to base is income growth ×
    demand factor, so that product — not the demand factor alone — is
    what permits can confirm or refute. Rows carry both bases ('value'
    tracks the dollar path; 'count' strips price drift) plus the raw
    scenario ``d(y)`` for reference.
    """
    if scenarios is None:
        scenarios = list(REEC_DEMAND_SCENARIOS)
    emp_val = empirical_demand_factors("value")
    emp_cnt = empirical_demand_factors("count")
    partial = _partial_year(load_dpp_solar())

    rows: list[dict] = []
    for y in years:
        row: dict = {
            "year": y,
            "empirical_value_factor": round(emp_val[y], 4) if y in emp_val else None,
            "empirical_count_factor": round(emp_cnt[y], 4) if y in emp_cnt else None,
            "window": "H1/H1" if y == partial else
                      ("full-year" if y in emp_val else "no data"),
        }
        g = _hawaii_nominal_growth(y)
        row["income_growth_g"] = round(g, 4)
        for s in scenarios:
            d = _reec_demand_factor(y, s)
            row[f"model_gxd_{s}"] = round(g * d, 4)
            row[f"d_{s}"] = round(d, 4)
        rows.append(row)
    return rows


__all__ = [
    "load_dpp_solar",
    "empirical_demand_factors",
    "compare_with_model",
]
