"""The general excise tax in full: who bears all of it, and what changes cost.

Three questions the resident incidence model (``tax_modeler.excise``) cannot
answer, because it sees only households' own purchases (about 35% of the tax):

1. **Who bears the rest.** DBEDT's 2022 input-output table records the state
   general excise tax each of 62 sectors pays and where every sector's output
   goes: to other sectors, or to final purchasers (resident households,
   visitors, investment, government, exports). With the Leontief inverse
   ``L = (I - A)^-1``, the tax a sector pays on its inputs travels into the
   price of what it sells, so every dollar of the tax lands on a final
   purchaser: ``tax_k = t L f_k`` where ``t`` is the tax per dollar of each
   sector's output and ``f_k`` the purchaser's spending by sector. "Direct" is
   the tax on the final sale itself (``t * f_k``), "embedded" the tax carried
   in from earlier stages (pyramiding). Full forward shifting is assumed.
2. **What a rate change costs, by business activity** (DOTAX's Table 1.1
   activities: retailing, services, contracting, ...): the change in rate
   times the activity's base, grown with the baseline, and who bears it.
3. **What an exemption costs,** with the part visitors would stop paying:
   consumer categories (groceries, restaurant meals, clothing) from BEA's
   resident spending and HTA's visitor spending; or whole sectors' sales,
   including sales to other businesses, from the input-output table.

All of it static: no change in spending or prices beyond the tax itself.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cached_property, lru_cache
from pathlib import Path

import numpy as np

from state_revenue.ledger import load_ledger

DATA = Path(__file__).resolve().parent / "data"
STATE_RATE = 0.04
COUNTY_RATE = 0.005          # every county since January 2024

#: Final purchasers, as groups of the input-output table's final demand columns.
PURCHASERS: dict[str, tuple[str, ...]] = {
    "residents": ("PCE",),
    "visitors": ("VE",),
    "investment": ("Gross private investment", "Change in inventories"),
    "government": ("State and local gov't consumption", "State and local gov't investment",
                   "Federal defense consumption", "Federal defense investment",
                   "Federal nondefense consumption", "Federal nondefense investment"),
    "exports": ("Exports",),
}

#: DOTAX's activities (Table 1.1) -> the input-output sectors whose tax they
#: are. DBEDT placed DOTAX's FY2022 activities on sectors: retail trade's
#: $1,732.3M is the retailing activity's exactly, construction's $413.1M
#: contracting's. The rest is approximate (services and rentals spread over
#: many sectors). Used only to say who bears a change in an activity's rate,
#: never for its cost.
ACTIVITY_SECTORS: dict[str, tuple[str, ...]] = {
    "retailing": ("S32",),
    "services": ("S16", "S17", "S18", "S19", "S20", "S21", "S22", "S23", "S24", "S25", "S26",
                 "S27", "S28", "S33", "S34", "S35", "S39", "S40", "S41", "S42", "S43", "S44",
                 "S45", "S46", "S47", "S48", "S49", "S50", "S51", "S52", "S53", "S54", "S57",
                 "S58", "S59"),
    "contracting": ("S7", "S8", "S9", "S10"),
    "transient_accommodations": ("S55",),
    "other_rentals": ("S37", "S38"),
    "wholesale": ("S1", "S2", "S3", "S4", "S5", "S6", "S11", "S12", "S13", "S14", "S15", "S31"),
}
#: Table 1.1 activities taxed at the wholesale rate, mapped to "wholesale" above.
WHOLESALE_ACTIVITIES = ("producing", "manufacturing", "wholesaling", "use", "wholesale_services")

#: Visitor spending (HTA) in each consumer category the resident model knows.
VISITOR_CATEGORIES: dict[str, tuple[str, ...]] = {
    "groceries": ("groceries", "hawaii_food_products"),
    "restaurants": ("restaurants", "dinner_shows"),
    "apparel": ("clothing", "jewelry", "leather"),
    "recreation": ("entertainment",),
    "personal_care": ("cosmetics",),
    "fuel": ("gasoline_parking",),
}


# --- the input-output table ---------------------------------------------------

@dataclass(frozen=True)
class IOTable:
    year: int
    codes: tuple[str, ...]
    names: tuple[str, ...]
    output: np.ndarray                       # $ millions, by sector
    flows: np.ndarray                        # [i, j]: sector i's sales to sector j
    final: dict[str, np.ndarray]             # final demand column -> by sector
    rows: dict[str, np.ndarray]              # state taxes, imports, ... by sector

    @cached_property
    def leontief(self) -> np.ndarray:
        x = np.where(self.output > 0, self.output, 1.0)
        return np.linalg.inv(np.eye(len(self.codes)) - self.flows / x)

    def tax_rate(self, tax: str = "state_get") -> np.ndarray:
        """Tax per dollar of each sector's output."""
        return self.rows[tax] / np.where(self.output > 0, self.output, 1.0)

    def purchaser(self, name: str) -> np.ndarray:
        return sum(self.final[c] for c in PURCHASERS[name])

    def index(self, codes) -> np.ndarray:
        pos = {c: i for i, c in enumerate(self.codes)}
        return np.array([pos[c] for c in codes], dtype=int)


@lru_cache(maxsize=1)
def load_io() -> IOTable:
    d = json.loads((DATA / "io_2022.json").read_text())
    return IOTable(
        year=d["year"],
        codes=tuple(s["code"] for s in d["sectors"]),
        names=tuple(s["name"] for s in d["sectors"]),
        output=np.array(d["output"]),
        flows=np.array(d["flows"]),
        final={k: np.array(v) for k, v in d["final_demand"].items()},
        rows={k: np.array(v) for k, v in d["rows"].items()},
    )


@lru_cache(maxsize=1)
def load_visitor_spending() -> dict[int, dict[str, float]]:
    d = json.loads((DATA / "visitor_spending.json").read_text())
    return {int(y): v for y, v in d["years"].items()}


# --- 1. who bears the tax -------------------------------------------------------

@dataclass(frozen=True)
class Burden:
    purchaser: str
    direct: float          # $ millions, the IO year
    embedded: float

    @property
    def total(self) -> float:
        return self.direct + self.embedded


def burden_by_purchaser(io: IOTable | None = None, *, tax: str = "state_get",
                        sectors: tuple[str, ...] | None = None) -> list[Burden]:
    """Every dollar of ``tax`` by final purchaser; ``sectors`` limits it to the
    tax those sectors pay (to say who bears one activity's tax)."""
    io = io or load_io()
    t = io.tax_rate(tax)
    if sectors is not None:
        mask = np.zeros_like(t)
        mask[io.index(sectors)] = 1.0
        t = t * mask
    carried = t @ io.leontief
    out = []
    for name in PURCHASERS:
        f = io.purchaser(name)
        direct = float(t @ f)
        out.append(Burden(name, direct, float(carried @ f) - direct))
    return out


def shares(burdens: list[Burden]) -> dict[str, float]:
    total = sum(b.total for b in burdens)
    return {b.purchaser: b.total / total for b in burdens}


# --- 2. rate changes by activity ---------------------------------------------------

def _latest_activity() -> tuple[int, dict]:
    payload = json.loads((DATA / "annual_collections.json").read_text())
    years = payload["general_excise_by_activity"]
    fy = max(int(y) for y in years)
    return fy, years[str(fy)]


def _growth(fy: int, last: int, baseline_get: dict[int, float] | None) -> float:
    """The general excise tax in ``fy`` over ``last`` (the last actual year)."""
    if fy == last:
        return 1.0
    if baseline_get is None:
        from state_revenue.baseline import build_baseline
        baseline_get = build_baseline("all").line("general_excise")
    actual = load_ledger("all")[last].lines["general_excise"]
    return baseline_get[fy] / actual


@dataclass(frozen=True)
class Score:
    """A change's static effect on state revenue in one fiscal year."""
    fiscal_year: int
    state: float                         # $ thousands; negative is revenue lost
    county: float                        # the county surcharge's part
    borne_by: dict[str, float]           # share of the change by final purchaser
    detail: dict[str, float]


def score_rate_change(new_rates: dict[str, float], fiscal_year: int, *,
                      baseline_get: dict[int, float] | None = None) -> Score:
    """Set activities' state rates (``{"retailing": 0.045}``) in ``fiscal_year``.

    Base: the last actual year's base by activity (Table 1.1), grown with the
    general excise baseline. Static: the base does not respond to the rate.
    The county surcharge is unchanged.
    """
    last, row = _latest_activity()
    g = _growth(fiscal_year, last, baseline_get)
    detail, weights = {}, {}
    for activity, rate in new_rates.items():
        if activity not in row["rates"]:
            raise KeyError(f"unknown activity {activity!r}; Table 1.1 has {sorted(row['rates'])}")
        delta = (rate - row["rates"][activity]) * row["base"][activity] * g
        detail[activity] = delta
        group = "wholesale" if activity in WHOLESALE_ACTIVITIES else activity
        weights[group] = weights.get(group, 0.0) + delta
    borne = _mix_shares(weights)
    return Score(fiscal_year, sum(detail.values()), 0.0, borne, detail)


def _mix_shares(weights: dict[str, float]) -> dict[str, float]:
    """Who bears a change spread over activity groups, weighted by each group's
    part of the change (groups with no sector mapping count as services)."""
    total = sum(weights.values())
    out = dict.fromkeys(PURCHASERS, 0.0)
    if not total:
        return out
    for group, w in weights.items():
        sectors = ACTIVITY_SECTORS.get(group, ACTIVITY_SECTORS["services"])
        for k, v in shares(burden_by_purchaser(sectors=sectors)).items():
            out[k] += v * w / total
    return out


# --- 3. exemptions -------------------------------------------------------------------

def resident_spending(category: str, year: int) -> float:
    """Hawaiʻi residents' taxable spending on a consumer category in ``year``,
    $ thousands: BEA's lines for it (the incidence model's own mapping), times
    its taxable share, less SNAP benefits for groceries (exempt already)."""
    from tax_modeler.excise import get_incidence as gi  # heavy import; only here
    cat = next((c for c in gi.CATEGORIES if c.key == category), None)
    if cat is None or not cat.pce_lines:
        raise KeyError(f"no BEA spending for category {category!r}")
    pce = gi.pce_hawaii(year)
    spend = sum(pce[line] for line in cat.pce_lines) * cat.taxable_share / 1e3
    if category == "groceries":
        spend -= snap_issuance(year) / 1e3
    return spend


def snap_issuance(year: int) -> float:
    """SNAP benefits issued in Hawaiʻi, dollars: DHS, state fiscal year ``year + 1``
    (July of ``year`` to June) when present, else the latest."""
    import csv
    from importlib.resources import files
    path = files("tax_modeler") / "data" / "admin_caseload" / "hawaii_caseload.csv"
    rows = {int(r["year"]): float(r["annual_dollars_millions"]) * 1e6
            for r in csv.DictReader(path.open()) if r["program"] == "snap"}
    return rows.get(year + 1, rows[max(rows)])


def visitor_spending(category: str, year: int) -> float:
    """Visitors' spending on a consumer category, $ thousands (HTA)."""
    years = load_visitor_spending()
    row = years.get(year) or years[max(years)]
    return sum(row[k] for k in VISITOR_CATEGORIES.get(category, ())) * 1e3


def score_category_exemption(category: str, fiscal_year: int, *, spending_year: int = 2024,
                             baseline_get: dict[int, float] | None = None) -> Score:
    """Exempt a consumer category from the retail tax (state and county).

    Spending in ``spending_year`` (a calendar year, read as the fiscal year that
    ends in the next June) grown with the general excise baseline. The wholesale
    levy on the goods stays. ``borne_by`` splits the relief between residents
    and visitors.
    """
    residents = resident_spending(category, spending_year)
    visitors = visitor_spending(category, spending_year)
    g = _growth(fiscal_year, spending_year + 1, baseline_get) if fiscal_year != spending_year + 1 else 1.0
    base = (residents + visitors) * g
    total = residents + visitors
    return Score(
        fiscal_year,
        state=-STATE_RATE * base,
        county=-COUNTY_RATE * base,
        borne_by={"residents": residents / total, "visitors": visitors / total},
        detail={"resident_spending": residents * g, "visitor_spending": visitors * g},
    )


def score_sector_exemption(sectors: tuple[str, ...], fiscal_year: int, *,
                           purchasers: tuple[str, ...] = (*PURCHASERS, "businesses"),
                           baseline_get: dict[int, float] | None = None) -> Score:
    """Exempt sectors' sales (input-output codes) to some purchasers.

    ``"businesses"`` is sales to other sectors: exempting them removes tax that
    would have been carried into later prices, and ``borne_by`` follows that
    relief to the final purchasers. Scaled from the input-output year's tax to
    ``fiscal_year``'s general excise baseline (state only; the county surcharge
    follows at an eighth of it).
    """
    io = load_io()
    t = io.tax_rate("state_get")
    idx = io.index(sectors)
    relief = dict.fromkeys(PURCHASERS, 0.0)
    for name in PURCHASERS:
        if name in purchasers:
            relief[name] += float(t[idx] @ io.purchaser(name)[idx])
    if "businesses" in purchasers:
        x = np.where(io.output > 0, io.output, 1.0)
        cut = np.zeros(len(io.codes))                    # tax removed per dollar of each buyer's output
        cut += (t[idx][:, None] * io.flows[idx, :]).sum(axis=0) / x
        carried = cut @ io.leontief
        for name in PURCHASERS:
            relief[name] += float(carried @ io.purchaser(name))
    lost = sum(relief.values())                          # $ millions, IO year
    last = max(load_ledger("all").years)
    scale = (load_ledger("all")[last].lines["general_excise"] / (io.rows["state_get"].sum() * 1e3)
             * _growth(fiscal_year, last, baseline_get))
    state = -lost * 1e3 * scale
    return Score(fiscal_year, state, state * COUNTY_RATE / STATE_RATE,
                 {k: v / lost for k, v in relief.items()} if lost else relief,
                 {f"{k}_relief": -v * 1e3 * scale for k, v in relief.items()})


def main(argv: list[str] | None = None) -> int:
    """Print who bears the tax and a few standard changes.

        python -m state_revenue.general_excise [--fiscal-year 2027]
    """
    import argparse
    ap = argparse.ArgumentParser(description="The general excise tax in full.")
    ap.add_argument("--fiscal-year", type=int, default=2027)
    args = ap.parse_args(argv)
    fy = args.fiscal_year
    io = load_io()
    burdens = burden_by_purchaser(io)
    total = sum(b.total for b in burdens)
    print(f"State general excise tax by final purchaser (input-output {io.year}, $ millions):\n")
    print("| Purchaser | Direct | Embedded | Total | Share |\n|---|---|---|---|---|")
    for b in burdens:
        print(f"| {b.purchaser} | {b.direct:,.0f} | {b.embedded:,.0f} | {b.total:,.0f} | {b.total / total:.1%} |")
    print(f"| total | {sum(b.direct for b in burdens):,.0f} | {sum(b.embedded for b in burdens):,.0f} "
          f"| {total:,.0f} | |\n")
    print(f"Changes, FY{fy}, state revenue ($ millions; county surcharge beside):\n")
    print("| Change | State | County | Residents | Visitors | Investment | Government | Exports |")
    print("|---|---|---|---|---|---|---|---|")
    four = ("retailing", "services", "contracting", "transient_accommodations", "other_rentals",
            "other_4pct")
    cases = [
        ("Every 4% activity to 4.5%", score_rate_change(dict.fromkeys(four, 0.045), fy)),
        ("Retailing to 4.5%", score_rate_change({"retailing": 0.045}, fy)),
        ("Exempt groceries", score_category_exemption("groceries", fy)),
        ("Exempt restaurant meals", score_category_exemption("restaurants", fy)),
        ("Exempt hospitals and outpatient care", score_sector_exemption(("S50", "S51"), fy)),
    ]
    for label, s in cases:
        shares_ = [s.borne_by.get(k, 0.0) for k in PURCHASERS]
        print(f"| {label} | {s.state / 1e3:,.1f} | {s.county / 1e3:,.1f} | "
              + " | ".join(f"{v:.0%}" for v in shares_) + " |")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
