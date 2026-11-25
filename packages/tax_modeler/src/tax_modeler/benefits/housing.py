"""Federal housing assistance (Section 8 vouchers + public housing).

HUD-funded programs administered in Hawaii by Hawaii Public Housing
Authority (HPHA) and several local PHAs: Housing Choice Vouchers and public
housing, where the household pays about 30% of its income in rent and HUD pays
the rest up to a payment standard (about the Fair Market Rent).

The model values both as one in-kind transfer **per renter household**, the
way the Census Bureau values it in the SPM:

    value = max(0, min(FMR, housing portion of the SPM threshold) - 0.30 x income)

* Household: the tax units sharing ``hh_id`` (PUMS household); ``income`` is
  their total, never below zero for the tenant's payment; the threshold is the
  renter line for the household's adults and children in its SPM area
  (``poverty.thresholds.renter_housing_portion``, about 54% of the line in Urban
  Honolulu). The value sits on the household's first tax unit and reaches the
  primary SPM unit through the aggregation.
* Only renters (``tenure``; a unit without it counts as a renter).
* Eligibility: income under ``income_fpl_cap`` times the Hawaii FPL for the
  household's size, a proxy for HUD's very-low-income limit.
* FMR: the Honolulu MSA two-bedroom Fair Market Rent, about $30,000 a year;
  the threshold cap binds well before it for most households.

Take-up is a probability, not a ranking: ``housing_takeup`` gives every
eligible household the same chance, set so the weighted count matches HUD's
caseload, writes the expected value (probability x value) to the resources
and carries the value and the probability so the poverty draws give each
household the subsidy or not (``poverty.draws``).

Until November 25, 2026 the subsidy was computed per tax unit at
``FMR - 0.30 x income`` with no cap ($30,000 for a unit with no income),
and HUD's caseload was filled lowest income first: the cutoff fell inside a
block of tax units with zero income, often an adult relative filing alone,
and which households got $30,000 was an arbitrary tie-break. It moved
H.R.1's people pushed into poverty between 692 and 969 depending on the
pick, and the 15,600 recipients carried about $470M against HUD's $140M.

Reform DSL (``Reform.benefit_overrides["housing"]``):

  * ``payment_standard_pct``     multiplier on FMR (default 1.0)
  * ``income_threshold_factor``   multiplier on the income limit
  * ``tenant_rent_pct``          tenant rent share of income (default 0.30)
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from tax_modeler.errors import ConfigError

from ._fpl import fpl_year_for, hawaii_fpl

# 2024 HUD Honolulu MSA 2BR Fair Market Rent annualized.
# Source: HUD Office of Policy Development & Research FMR tables.
_FMR_HONOLULU_2BR_ANNUAL_2024 = 30_000.0
# 50% of HHS Hawaii FPL × ~3.5 ≈ rough HUD "very-low-income" income limit.
# In production replace with HUD AMI tables loaded by household size.
_HOUSING_FPL_PROXY_CAP = 3.50


@dataclass(frozen=True)
class HousingParameters:
    fmr_annual: float = _FMR_HONOLULU_2BR_ANNUAL_2024
    income_fpl_cap: float = _HOUSING_FPL_PROXY_CAP
    tenant_rent_pct: float = 0.30
    payment_standard_pct: float = 1.0
    income_threshold_factor: float = 1.0


def hawaii_housing_parameters() -> HousingParameters:
    return HousingParameters()


def with_housing_overrides(
    base: HousingParameters, overrides: Mapping[str, object] | None
) -> HousingParameters:
    if not overrides:
        return base
    valid = set(base.__dataclass_fields__)
    bad = set(overrides) - valid
    if bad:
        raise ConfigError(
            f"unknown housing override keys: {sorted(bad)}",
            available=sorted(valid),
        )
    return replace(base, **dict(overrides))


def _households(df: pd.DataFrame) -> pd.Series:
    key = "hh_id" if "hh_id" in df.columns else ("SERIALNO" if "SERIALNO" in df.columns else None)
    return df[key].astype(str) if key else pd.Series(df.index.astype(str), index=df.index)


def compute_housing_for_units(
    units: pd.DataFrame,
    *,
    tax_year: int = 2024,
    params: HousingParameters | None = None,
    overrides: Mapping[str, object] | None = None,
    out_col: str = "housing_subsidy_amount",
) -> pd.DataFrame:
    """The housing-assistance value of every eligible renter household, before take-up.

    Writes ``out_col`` (the value, on the household's first tax unit; zero on the
    others and on ineligible households) and ``housing_eligible``. ``tax_year``
    selects the FPL table and the SPM threshold year (extrapolated past the table).
    """
    from tax_modeler.poverty.thresholds import COUNTY_AREA, DEFAULT_AREA, renter_housing_portion

    p = with_housing_overrides(params or hawaii_housing_parameters(), overrides)
    df = units.copy()
    hh = _households(df)
    joint = (df["filing_status"] == "married_filing_jointly").astype(int)
    frame = pd.DataFrame({
        "hh": hh,
        "adults": 1 + joint,
        "children": df["num_dependents"].fillna(0).clip(lower=0).astype(int),
        "income": df["income"].fillna(0).astype(float),
        "renter": (df["tenure"].fillna("renter") == "renter") if "tenure" in df.columns else True,
        "area": df["county"].map(COUNTY_AREA).fillna(DEFAULT_AREA) if "county" in df.columns else DEFAULT_AREA,
    }, index=df.index)
    first = ~frame["hh"].duplicated()
    agg = frame.groupby("hh", sort=False).agg(adults=("adults", "sum"), children=("children", "sum"),
                                              income=("income", "sum"), renter=("renter", "first"),
                                              area=("area", "first"))
    size = (agg["adults"] + agg["children"]).clip(lower=1)
    fpl = np.array([hawaii_fpl(fpl_year_for(tax_year), household_size=int(s)) for s in size])
    eligible = agg["renter"].to_numpy(bool) & (agg["income"].to_numpy() < p.income_fpl_cap * p.income_threshold_factor * fpl)
    cache: dict[tuple, float] = {}
    cap = np.empty(len(agg))
    for i, key in enumerate(zip(agg["adults"].clip(lower=1), agg["children"], agg["area"], strict=True)):
        if key not in cache:
            cache[key] = renter_housing_portion(tax_year, n_adults=int(key[0]), n_children=int(key[1]),
                                                area=key[2], extrapolate=True)
        cap[i] = cache[key]
    tenant = p.tenant_rent_pct * np.maximum(agg["income"].to_numpy(), 0.0)
    value = np.where(eligible, np.maximum(np.minimum(p.fmr_annual * p.payment_standard_pct, cap) - tenant, 0.0), 0.0)
    by_hh = pd.Series(value, index=agg.index)
    elig_hh = pd.Series(eligible & (value > 0), index=agg.index)
    df[out_col] = np.where(first, hh.map(by_hh).to_numpy(), 0.0)
    df["housing_eligible"] = first.to_numpy() & hh.map(elig_hh).to_numpy(bool)
    return df


def housing_takeup(
    units: pd.DataFrame,
    *,
    target_households: float,
    amount_col: str = "housing_subsidy_amount",
    weight_col: str | None = None,
) -> pd.DataFrame:
    """Take-up as one probability for every eligible household (``compute_housing_for_units``), set so the
    weighted count is ``target_households``. Writes ``housing_value`` (the value if it takes up),
    ``housing_takeup_prob`` and the expected value to ``amount_col``. ``weight_col``: ``hh_weight`` if present,
    else ``weight``."""
    df = units.copy()
    w_col = weight_col or ("hh_weight" if "hh_weight" in df.columns else "weight")
    elig = df["housing_eligible"].to_numpy(bool)
    pool = float(df.loc[elig, w_col].sum())
    prob = min(1.0, target_households / pool) if pool > 0 else 0.0
    df["housing_value"] = np.where(elig, df[amount_col].to_numpy(dtype=float), 0.0)
    df["housing_takeup_prob"] = np.where(elig, prob, 0.0)
    df[amount_col] = df["housing_value"] * df["housing_takeup_prob"]
    return df
