"""Who pays Hawaiʻi's general excise tax: resident households' tax on what they buy.

The general excise tax (HRS ch. 237) is levied on a business's gross receipts, 4 percent at
retail and on services, contracting and rents, and 0.5 percent on wholesaling; each county adds
a 0.5 percent surcharge on the 4 percent activities. No return says who bore it, so the model
builds each household's taxable spending and applies the rates:

1. **Shape.** How spending on each category rises with income comes from the Consumer
   Expenditure Survey's means by decile of income before taxes (:class:`SpendingProfile`),
   read at each Hawaiʻi household's own income.
2. **Level.** Each category is then scaled so the households' total equals what BEA counts
   Hawaiʻi residents as spending on it (personal consumption expenditures by state, table
   SAPCE4; ``level="pce"``). ``level="cex"`` skips the scaling and keeps the survey's national
   dollars, a floor: households report less in the survey than the accounts find, and prices
   here are higher.
3. **Rent** is each renter household's own contract rent, not an imputation.
4. **Tax.** The tax is on receipts that include the tax passed on, so the tax in a purchase is
   the rate times what the household paid. Groceries bought with SNAP benefits are exempt
   (HRS §237-24.3). Goods also carry the wholesale levy from one stage back
   (:func:`wholesale_markup`).

It is assumed the household bears all of the tax in what it buys. Left out, and so not in any
figure here: tax on business inputs other than the wholesale levy (contracting, business
services and rents, which reach households through prices), medical care paid by insurers,
visitors, and government and nonprofit purchases. :func:`control_totals` sets the modelled
total beside the Department of Taxation's collections so the unmodelled part is in view.

``GET_INCIDENCE_METHODOLOGY.md`` at the repository root is the full account.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

import numpy as np
import pandas as pd

from tax_modeler.errors import ConfigError, MissingDataError

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "excise"
STATE_RATE = 0.04
COUNTY_SURCHARGE = 0.005      # every county since January 1, 2024 (Maui the last to adopt)
RENT = "rent"


@dataclass(frozen=True)
class Category:
    """One kind of household spending the tax reaches.

    ``cex_items`` are the survey items whose sum gives the category's shape over income (none:
    total expenditures). ``pce_lines`` are the SAPCE4 lines whose sum is Hawaiʻi residents'
    spending on it (none: the survey's own dollars are kept). ``goods_lines`` are the lines among
    them that are goods bought from a retailer, which carry the wholesale levy.
    """

    key: str
    label: str
    cex_items: tuple[str, ...]
    pce_lines: tuple[int, ...]
    goods_lines: tuple[int, ...] = ()
    taxable_share: float = 1.0


CATEGORIES: tuple[Category, ...] = (
    Category("groceries", "Groceries", ("FOODHOME",), (4,), (4,)),
    Category("alcohol_tobacco", "Alcohol and tobacco", ("ALCBEVG", "TOBACCO"), (5, 127), (5, 127)),
    Category("restaurants", "Restaurant meals", ("FOODAWAY",), (102,), (102,)),
    Category("apparel", "Clothing and footwear", ("APPAREL",), (7,), (8, 17)),
    Category("vehicles", "Vehicles and parts", ("VEHPURCH",), (54, 58), (54, 58)),
    Category("fuel", "Gasoline and motor fuel", ("GASFUEL",), (59,), (59,)),
    Category("vehicle_services", "Vehicle repair and services", ("CAREPAIR",), (60, 61)),
    Category("household_goods", "Furnishings and household supplies", ("HHFURNSH", "HKPGSUPP"), (30,),
             (31, 32, 33, 34, 35)),
    Category("recreation", "Recreation", ("ENTRTAIN", "READING"), (74, 78, 82, 90, 92, 93),
             (75, 76, 79, 80, 90, 93)),
    Category("personal_care", "Personal care and personal items", ("PERSCARE",), (118, 119), (119,)),
    Category("telecom", "Phone and internet", (), (67, 71, 72), (67,)),
    Category("other_services", "Legal, accounting and funeral services", ("MISC", "HHOPER"), (122, 123, 126)),
    # Out-of-pocket only, in the survey's dollars: BEA's health lines are mostly paid by insurers.
    Category("medical_services", "Medical services paid out of pocket", ("MEDSERVS",), ()),
)
CATEGORY_KEYS = tuple(c.key for c in CATEGORIES) + (RENT,)
LABELS = {**{c.key: c.label for c in CATEGORIES}, RENT: "Rent"}


def _read(name: str) -> dict:
    path = DATA_DIR / name
    if not path.exists():
        raise MissingDataError(f"{path} is missing; run scripts/fetch_get_incidence_inputs.py")
    return json.loads(path.read_text())


@dataclass(frozen=True)
class SpendingProfile:
    """Mean income and mean spending per item at each decile of income before taxes."""

    income: np.ndarray
    items: Mapping[str, np.ndarray]

    def __post_init__(self) -> None:
        if len(self.income) < 2 or np.any(np.diff(self.income) <= 0) or self.income[0] <= 0:
            raise ConfigError("a spending profile needs positive, increasing decile incomes")

    @classmethod
    def load(cls, year: int) -> SpendingProfile:
        years = _read("cex_income_deciles.json")["years"]
        if str(year) not in years:
            raise ConfigError(f"no expenditure survey deciles for {year}; the file has {sorted(years)}")
        rows = years[str(year)]
        deciles = [f"decile_{i}" for i in range(1, 11)]
        items = {k: np.array([v[d] for d in deciles], dtype=float) for k, v in rows.items()}
        return cls(income=items.pop("INCBEFTX"), items=items)

    def at(self, items: tuple[str, ...], income: np.ndarray) -> np.ndarray:
        """Spending on ``items`` (summed) at each ``income``.

        Linear in log income between the decile means. Below the lowest decile's mean it stays at
        that decile's spending; above the highest it grows at the last interval's elasticity.
        """
        missing = [i for i in items if i not in self.items]
        if missing:
            raise ConfigError(f"the spending profile has no item {missing}; it has {sorted(self.items)}")
        y = np.sum([self.items[i] for i in items], axis=0)
        x = np.log(self.income)
        z = np.log(np.maximum(np.asarray(income, dtype=float), self.income[0]))
        out = np.interp(z, x, y)
        if y[-1] > 0 and y[-2] > 0:
            slope = max((np.log(y[-1]) - np.log(y[-2])) / (x[-1] - x[-2]), 0.0)
            above = z > x[-1]
            out[above] = y[-1] * np.exp(slope * (z[above] - x[-1]))
        return out


@cache
def pce_hawaii(year: int) -> dict[int, float]:
    """Hawaiʻi residents' spending by SAPCE4 line in ``year``, dollars."""
    lines = _read("bea_pce_hawaii.json")["lines"]
    out = {int(k): v["millions"][str(year)] * 1e6 for k, v in lines.items() if str(year) in v["millions"]}
    if not out:
        raise ConfigError(f"BEA's Hawaii consumer spending file has no {year}")
    return out


@cache
def dotax_get(fiscal_year: int) -> dict[str, dict[str, float]]:
    """The Department of Taxation's {"tax" | "base": {activity: dollars}} for a fiscal year."""
    years = _read("dotax_get_by_activity.json")["fiscal_years"]
    if str(fiscal_year) not in years:
        raise ConfigError(f"no general excise collections for fiscal year {fiscal_year}; the file has {sorted(years)}")
    return years[str(fiscal_year)]


def wholesale_markup(fiscal_year: int) -> float:
    """The wholesale levy carried in a dollar of retail sales: the Department's wholesaling tax over
    its retailing base. About a quarter of a cent."""
    d = dotax_get(fiscal_year)
    return d["tax"]["wholesaling"] / d["base"]["retailing"]


@dataclass(frozen=True)
class Law:
    """The tax as applied to household purchases. ``exempt`` are category keys taxed at zero at
    retail (their wholesale levy stays); ``rent_pass_through`` is the share of the tax on rent that
    renters bear."""

    state_rate: float = STATE_RATE
    county_rate: float = COUNTY_SURCHARGE
    wholesale: float = 0.0
    exempt: frozenset[str] = frozenset()
    rent_pass_through: float = 1.0
    snap_exempt: bool = True

    def __post_init__(self) -> None:
        unknown = set(self.exempt) - set(CATEGORY_KEYS)
        if unknown:
            raise ConfigError(f"unknown categories {sorted(unknown)}; the model has {list(CATEGORY_KEYS)}")


@dataclass(frozen=True)
class Spending:
    """Household spending by category. ``amounts`` has one column per :data:`CATEGORY_KEYS` entry;
    ``scale`` is the factor each category was multiplied by to reach its BEA total (1.0 where it
    was not scaled)."""

    amounts: pd.DataFrame
    scale: dict[str, float] = field(default_factory=dict)


def impute_spending(income: np.ndarray, weight: np.ndarray, rent: np.ndarray, profile: SpendingProfile, *,
                    pce: Mapping[int, float] | None = None, income_deflator: float = 1.0) -> Spending:
    """Each household's annual spending by category.

    ``pce`` (SAPCE4 line -> dollars) scales every category that names lines so the weighted total
    is BEA's; None keeps the survey's dollars, and drops a category with no survey item of its own.
    ``income_deflator`` divides income before the
    profile is read (a regional price parity would go here; 1.0 reads the national profile at
    Hawaiʻi's nominal incomes). ``rent`` is annual contract rent, zero for owners.
    """
    income = np.asarray(income, dtype=float)
    weight = np.asarray(weight, dtype=float)
    amounts, scale = {}, {}
    for c in CATEGORIES:
        raw = profile.at(c.cex_items or ("TOTALEXP",), income / income_deflator)
        k = 1.0
        if pce is not None and c.pce_lines:
            total = float((raw * weight).sum())
            if total <= 0:
                raise ConfigError(f"the households have no {c.key} spending to scale")
            k = sum(pce[line] for line in c.pce_lines) / total
        elif not c.cex_items:
            k = 0.0     # no survey item of its own: without BEA's level there is nothing to keep
        amounts[c.key], scale[c.key] = raw * k, k
    amounts[RENT], scale[RENT] = np.asarray(rent, dtype=float), 1.0
    return Spending(pd.DataFrame(amounts), scale)


def goods_share(category: Category, pce: Mapping[int, float] | None = None) -> float:
    """The share of a category bought as goods from a retailer: its goods lines over all its lines."""
    if not category.goods_lines:
        return 0.0
    if set(category.goods_lines) == set(category.pce_lines):
        return 1.0
    if pce is None:
        raise ConfigError(f"{category.key} is part goods; its share needs BEA's lines")
    return sum(pce[i] for i in category.goods_lines) / sum(pce[i] for i in category.pce_lines)


def burden(spending: Spending, law: Law, *, snap: np.ndarray | None = None,
           pce: Mapping[int, float] | None = None) -> pd.DataFrame:
    """Tax in each household's purchases: columns ``state_<category>`` and ``county_<category>``,
    and the totals ``state``, ``county`` and ``total``.

    The state column holds the retail tax and, on goods, the wholesale levy; the county column the
    surcharge. ``snap`` is each household's annual SNAP benefit, taken off its groceries before
    the retail tax when ``law.snap_exempt``.
    """
    a = spending.amounts
    out = {}
    for key in CATEGORY_KEYS:
        spent = a[key].to_numpy(dtype=float)
        if key == RENT:
            taxed, share, goods = spent, law.rent_pass_through, 0.0
        else:
            c = next(c for c in CATEGORIES if c.key == key)
            share, goods = c.taxable_share, goods_share(c, pce)
            taxed = spent
            if key == "groceries" and law.snap_exempt and snap is not None:
                taxed = np.maximum(spent - np.asarray(snap, dtype=float), 0.0)
        retail = 0.0 if key in law.exempt else 1.0
        out[f"state_{key}"] = share * (taxed * law.state_rate * retail + spent * goods * law.wholesale)
        out[f"county_{key}"] = share * taxed * law.county_rate * retail
    frame = pd.DataFrame(out, index=a.index)
    frame["state"] = frame[[f"state_{k}" for k in CATEGORY_KEYS]].sum(axis=1)
    frame["county"] = frame[[f"county_{k}" for k in CATEGORY_KEYS]].sum(axis=1)
    frame["total"] = frame["state"] + frame["county"]
    return frame


def income_groups(income: np.ndarray, weight: np.ndarray, n: int = 5) -> np.ndarray:
    """Each household's group, 0 (lowest income) to n - 1, with equal weighted households in each.
    A household is placed by the midpoint of its own weight in the cumulative total."""
    order = np.argsort(income, kind="stable")
    w = np.asarray(weight, dtype=float)[order]
    mid = (np.cumsum(w) - w / 2) / w.sum()
    groups = np.empty(len(order), dtype=int)
    groups[order] = np.minimum((mid * n).astype(int), n - 1)
    return groups


def control_totals(tax: pd.DataFrame, weight: np.ndarray, fiscal_year: int) -> pd.DataFrame:
    """The modelled state tax on resident households beside the Department's collections.

    The remainder is not an estimate of anything: it is visitors, business inputs past the
    wholesale levy, insurer-paid medical care, government and nonprofit purchases, and the
    difference between the survey and the accounts, together.
    """
    d = dotax_get(fiscal_year)["tax"]
    w = np.asarray(weight, dtype=float)
    modelled = float((tax["state"] * w).sum())
    rent = float((tax[f"state_{RENT}"] * w).sum())
    allocated = d["allocated collections"]
    rows = [
        ("Modelled: resident households, state tax", modelled),
        ("  of which on rent", rent),
        ("  of which on purchases (retail tax and wholesale levy)", modelled - rent),
        (f"Department of Taxation allocated collections, fiscal year {fiscal_year}", allocated),
        ("  retailing", d["retailing"]),
        ("  services", d["services"]),
        ("  contracting", d["contracting"]),
        ("  hotel rentals", d["hotel rentals"]),
        ("  all other rentals", d["all other rentals"]),
        ("  wholesaling", d["wholesaling"]),
        ("Not modelled (collections less modelled)", allocated - modelled),
    ]
    out = pd.DataFrame(rows, columns=["line", "dollars"])
    out["share_of_collections"] = out["dollars"] / allocated
    return out
