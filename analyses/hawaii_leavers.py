#!/usr/bin/env python3
"""Who leaves Hawaiʻi: people who moved out, compared with people who moved in and people who stayed.

Three groups from the ACS 2020-2024 5-year PUMS, by where each person lived a year before the survey:

* **stayers**   live in Hawaiʻi and lived in Hawaiʻi a year ago (same house or another Hawaiʻi address);
* **movers in** live in Hawaiʻi and lived in another state a year ago;
* **leavers**   live in another state and lived in Hawaiʻi a year ago (``scripts/fetch_hawaii_leavers.py``
  pulls their households from the national files).

Headline groups are civilians in housing units: households with a member in the armed forces (``ESR`` 4/5) and
group quarters (dorms, barracks, prisons) are dropped, since their moves are orders and enrolment. Veterans
(``MIL`` 2) stay in; two sensitivity cases drop every veteran household and survey year 2020.

Person statistics cover adults (18+). Tax statistics cover tax units built by ``build_raw_units`` and are
assigned to a group by the primary filer. Each leaver unit is scored under Hawaiʻi law *as if it had stayed*:
current law (Act 46, TY2026) and Act 24 (TY2027 brackets). Leaver income is mostly earned after the move, so the
tax figure is an illustration of the base that left, not a revenue loss.

Weights are raw Census weights (the 5-year weights average one year, so sums are per year) with the DOTAX
filing-status factors set to 1.0 for every group, since they are Hawaiʻi calibrations. Standard errors are the
Census successive-difference replicate formula over the 80 replicate weights. Incomes are in 2024 dollars
(``ADJINC``). Poverty is the official measure (``POVPIP``), the same federal line in every state.

The IRS side is the SOI gross migration files by size of AGI, 2015-16 to 2022-23, whose Hawaiʻi rows
``scripts/fetch_irs_migration.py`` writes to ``data/tax_modeler/irs_soi/migration_hawaii_by_agi.csv``: returns and
AGI moving out and in, all filers and $200,000 or more, with the AGI each side reported from Hawaiʻi.

Outputs (``runs/hawaii_leavers/``, published to ``site/data/hawaii-leavers/`` by ``scripts/build_site.py
--import-runs hawaii-leavers``): results.csv (every statistic and its standard error, by case), destinations.csv,
irs_flows.csv, irs_by_agi.csv, manifest.json. Results and caveats: ``HAWAII_LEAVERS.md``.

    uv run python analyses/hawaii_leavers.py [--no-sensitivity]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(REPO / "scripts")]
RAW = REPO / "packages" / "data" / "raw"
HI_DIR = RAW / "pums_2020_2024"
US_DIR = RAW / "pums_2020_2024_us"
IRS_CSV = REPO / "data" / "tax_modeler" / "irs_soi" / "migration_hawaii_by_agi.csv"
TOP_CLASS = "$200,000 or more"
HAWAII = 15
GROUPS = ("stayers", "movers in", "leavers")
REPS = [f"PWGTP{i}" for i in range(1, 81)]
UNIT_REPS = [f"weight_r{i:02d}" for i in range(1, 81)]
AGE_BANDS = [(18, 29), (30, 44), (45, 64), (65, 200)]
STATE_NAMES = {
    1: "Alabama", 2: "Alaska", 4: "Arizona", 5: "Arkansas", 6: "California", 8: "Colorado", 9: "Connecticut",
    10: "Delaware", 11: "District of Columbia", 12: "Florida", 13: "Georgia", 16: "Idaho", 17: "Illinois",
    18: "Indiana", 19: "Iowa", 20: "Kansas", 21: "Kentucky", 22: "Louisiana", 23: "Maine", 24: "Maryland",
    25: "Massachusetts", 26: "Michigan", 27: "Minnesota", 28: "Mississippi", 29: "Missouri", 30: "Montana",
    31: "Nebraska", 32: "Nevada", 33: "New Hampshire", 34: "New Jersey", 35: "New Mexico", 36: "New York",
    37: "North Carolina", 38: "North Dakota", 39: "Ohio", 40: "Oklahoma", 41: "Oregon", 42: "Pennsylvania",
    44: "Rhode Island", 45: "South Carolina", 46: "South Dakota", 47: "Tennessee", 48: "Texas", 49: "Utah",
    50: "Vermont", 51: "Virginia", 53: "Washington", 54: "West Virginia", 55: "Wisconsin", 56: "Wyoming",
    72: "Puerto Rico",
}


# ── loading and grouping ───────────────────────────────────────────────────────────────────────────────────

def _read(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype={"SERIALNO": str}, low_memory=False)
    if "STATE" in df.columns:
        df = df.rename(columns={"STATE": "ST"})
    return df


def load() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Hawaiʻi persons/households and leaver persons/households."""
    return (_read(HI_DIR / "psam_p15.csv"), _read(HI_DIR / "psam_h15.csv"),
            _read(US_DIR / "psam_p_leavers.csv"), _read(US_DIR / "psam_h_leavers.csv"))


def assign_group(p: pd.DataFrame) -> pd.Series:
    """stayers / movers in / leavers by residence now (``ST``) and a year ago (``MIG``, ``MIGSP``); else NaN."""
    in_hi = p["ST"] == HAWAII
    was_hi = (p["MIG"] == 1) & in_hi | (p["MIG"] == 3) & (p["MIGSP"] == HAWAII)
    from_state = (p["MIG"] == 3) & (p["MIGSP"] != HAWAII) & (p["MIGSP"] <= 56)
    group = pd.Series(np.nan, index=p.index, dtype=object)
    group[in_hi & was_hi] = "stayers"
    group[in_hi & from_state] = "movers in"
    group[~in_hi & (p["MIGSP"] == HAWAII)] = "leavers"
    return group


def civilian_households(p: pd.DataFrame) -> pd.Series:
    """True for persons in a housing unit with no armed-forces member."""
    military = p.loc[p["ESR"].isin([4, 5]), "SERIALNO"]
    return ~p["SERIALNO"].isin(military) & ~p["SERIALNO"].str.contains("GQ")


def prepare_persons(hi_p: pd.DataFrame, lv_p: pd.DataFrame) -> pd.DataFrame:
    p = pd.concat([hi_p, lv_p], ignore_index=True)
    derived = pd.DataFrame({
        "group": assign_group(p),
        "civilian": civilian_households(p),
        "person_id": p["SERIALNO"] + "_" + p["SPORDER"].astype(str),
        "income": p["PINCP"].fillna(0) * p["ADJINC"] / 1e6,
        "poor": p["POVPIP"] < 100,
        "born_hi": p["POBP"] == HAWAII,
        "native_hawaiian": p["RACNH"] == 1,
        "veteran": p["MIL"] == 2,
        "year": p["SERIALNO"].str[:4].astype(int),
    }, index=p.index)
    return pd.concat([p, derived], axis=1)


# ── replicate-weight statistics ───────────────────────────────────────────────────────────────────────────

def with_se(stat, df: pd.DataFrame, weight: str, reps: list[str]) -> tuple[float, float]:
    """Estimate and SDR standard error of ``stat(df, weights)`` (Census: var = 4/80 · Σ (θ_r − θ)²)."""
    est = stat(df, df[weight].to_numpy(float))
    draws = np.array([stat(df, df[r].to_numpy(float)) for r in reps])
    return float(est), float(np.sqrt(4 / 80 * np.sum((draws - est) ** 2)))


def wquantile(x: np.ndarray, w: np.ndarray, q: float) -> float:
    keep = w > 0
    x, w = x[keep], w[keep]
    order = np.argsort(x)
    cum = np.cumsum(w[order])
    return float(x[order][np.searchsorted(cum, q * cum[-1])])


def total(df, w):
    return w.sum()


def median_income(df, w):
    return wquantile(df["income"].to_numpy(float), w, 0.5)


def p90_income(df, w):
    return wquantile(df["income"].to_numpy(float), w, 0.9)


def median_age(df, w):
    return wquantile(df["AGEP"].to_numpy(float), w, 0.5)


def share(col: str):
    def f(df, w):
        return (w * df[col].to_numpy(float)).sum() / w.sum()
    return f


def share_over(threshold: float):
    def f(df, w):
        return (w * (df["income"].to_numpy(float) >= threshold)).sum() / w.sum()
    return f


def age_standardized(stat, ref: pd.DataFrame):
    """``stat`` computed within each age band and reweighted to ``ref``'s band shares (stayers)."""
    ref_w = np.array([ref.loc[ref["AGEP"].between(lo, hi), "PWGTP"].sum() for lo, hi in AGE_BANDS], float)
    ref_w /= ref_w.sum()

    def f(df, w):
        vals = []
        for lo, hi in AGE_BANDS:
            band = df["AGEP"].between(lo, hi).to_numpy()
            vals.append(stat(df[band], w[band]))
        return float(np.dot(ref_w, vals))
    return f


# ── tax units ─────────────────────────────────────────────────────────────────────────────────────────────

def build_units(persons: pd.DataFrame, households: pd.DataFrame) -> pd.DataFrame:
    from tax_modeler.base import build_raw_units
    from tax_modeler.units import constructor

    constructor.FILING_STATUS_WEIGHT_FACTORS.update({k: 1.0 for k in constructor.FILING_STATUS_WEIGHT_FACTORS})
    keep = households["SERIALNO"].isin(persons["SERIALNO"])
    return build_raw_units(persons.copy(), households[keep].copy())


def score_units(units: pd.DataFrame) -> pd.DataFrame:
    """Net Hawaiʻi income tax (after credits) under current law and Act 24."""
    from tax_modeler.config.tax_system_config import TaxCalculator
    from tax_modeler.pipeline import enrich_for_credits
    from tax_modeler.scenarios.registry import get_scenario

    units = enrich_for_credits(units)
    calc = TaxCalculator()
    units["tax_current"] = calc.unit_liabilities(units, get_scenario("act46").system_for(2026))["net"]
    units["tax_act24"] = calc.unit_liabilities(units, get_scenario("act24").system_for(2027))["net"]
    return units


def unit_table(units: pd.DataFrame) -> dict:
    out = {}
    for g in GROUPS:
        u = units[units["group"] == g]
        row = {"records": int(len(u))}
        for name, stat in [
            ("units", total),
            ("tax_current_total", lambda d, w: (w * d["tax_current"]).sum()),
            ("tax_act24_total", lambda d, w: (w * d["tax_act24"]).sum()),
            ("tax_current_per_unit", lambda d, w: (w * d["tax_current"]).sum() / w.sum()),
            ("median_unit_income", median_income),
            ("units_200k_plus", lambda d, w: (w * (d["income"] >= 200_000)).sum()),
            ("share_tax_from_200k_plus", lambda d, w: (w * d["tax_current"] * (d["income"] >= 200_000)).sum()
             / (w * d["tax_current"]).sum()),
        ]:
            row[name] = with_se(stat, u, "weight", UNIT_REPS)
        row["records_1m_plus"] = int((u["income"] >= 1_000_000).sum())
        row["records_500k_plus"] = int((u["income"] >= 500_000).sum())
        out[g] = row
    return out


# ── the report ────────────────────────────────────────────────────────────────────────────────────────────

def person_table(adults: pd.DataFrame) -> dict:
    stayers = adults[adults["group"] == "stayers"]
    stats = {
        "adults_per_year": total,
        "median_income": median_income,
        "p90_income": p90_income,
        "share_200k_plus": share_over(200_000),
        "share_poor": share("poor"),
        "median_age": median_age,
        "share_born_hi": share("born_hi"),
        "share_native_hawaiian": share("native_hawaiian"),
        "share_veteran": share("veteran"),
        "age_std_mean_income": age_standardized(lambda d, w: (w * d["income"]).sum() / w.sum(), stayers),
        "age_std_share_poor": age_standardized(share("poor"), stayers),
        "age_std_share_200k_plus": age_standardized(share_over(200_000), stayers),
    }
    out = {}
    for g in GROUPS:
        a = adults[adults["group"] == g]
        out[g] = {"records": int(len(a))} | {k: with_se(f, a, "PWGTP", REPS) for k, f in stats.items()}
    return out


def by_age(adults: pd.DataFrame) -> dict:
    out = {}
    for lo, hi in AGE_BANDS:
        label = f"{lo}+" if hi > 120 else f"{lo}-{hi}"
        band = adults[adults["AGEP"].between(lo, hi)]
        out[label] = {g: {"records": int((band["group"] == g).sum()),
                          "median_income": with_se(median_income, band[band["group"] == g], "PWGTP", REPS),
                          "share_poor": with_se(share("poor"), band[band["group"] == g], "PWGTP", REPS)}
                      for g in GROUPS}
    return out


def born_in_hawaii(adults: pd.DataFrame) -> dict:
    """The person table's core rows for adults born in Hawaiʻi (small samples among movers)."""
    born = adults[adults["born_hi"]]
    stats = {"adults_per_year": total, "median_income": median_income, "share_poor": share("poor"),
             "median_age": median_age}
    return {g: {"records": int((born["group"] == g).sum())}
            | {k: with_se(f, born[born["group"] == g], "PWGTP", REPS) for k, f in stats.items()} for g in GROUPS}


def destinations(adults: pd.DataFrame, n: int = 10) -> list:
    lv = adults[adults["group"] == "leavers"]
    top = lv.groupby("ST")["PWGTP"].sum().sort_values(ascending=False).head(n)
    return [{"state": STATE_NAMES.get(int(s), str(s)), "adults_per_year": int(v),
             "records": int((lv["ST"] == s).sum())} for s, v in top.items()]


def irs_flows() -> pd.DataFrame:
    """Hawaiʻi filers moving out and in, all filers and AGI $200,000 or more, by pair of filing years.

    Each side's AGI is the year it filed from Hawaiʻi: the first year for those leaving, the second for those
    arriving. The leaving rate divides those leaving by every return filed from Hawaiʻi in the first year that
    could be matched (non-migrants, movers within the state and those leaving)."""
    t = pd.read_csv(IRS_CSV)
    rows = []
    for (y1, y2), yr in t.groupby(["year1", "year2"]):
        for cls, label in (("All", "all"), (TOP_CLASS, "200k_plus")):
            f = yr[yr["agi_class"] == cls].set_index("flow")
            base = f.loc[["nonmigrant", "same_state", "outflow"], "returns"].sum()
            rows.append({"year1": y1, "year2": y2, "agi_class": label,
                         "returns_out": f.loc["outflow", "returns"], "returns_in": f.loc["inflow", "returns"],
                         "leaving_rate": f.loc["outflow", "returns"] / base,
                         "agi_out_k": f.loc["outflow", "agi_year1_k"], "agi_in_k": f.loc["inflow", "agi_year2_k"]})
    out = pd.DataFrame(rows)
    out["net_returns_in"] = out["returns_in"] - out["returns_out"]
    out["net_agi_in_k"] = out["agi_in_k"] - out["agi_out_k"]
    return out


def tidy(case: str, res: dict) -> pd.DataFrame:
    """One row per (table, group, band, metric) with its estimate and standard error."""
    rows = []

    def add(table, group, band, d):
        for metric, v in d.items():
            est, se = (v, np.nan) if not isinstance(v, (tuple, list)) else v
            rows.append({"case": case, "table": table, "group": group, "band": band, "metric": metric,
                         "estimate": est, "se": se})

    for table in ("persons", "units", "born_in_hawaii"):
        for g, d in res[table].items():
            add(table, g, "", d)
    for band, by_g in res["by_age"].items():
        for g, d in by_g.items():
            add("by_age", g, band, d)
    add("net", "leavers minus movers in", "", res["net_tax_out_minus_in"])
    return pd.DataFrame(rows)


def run(p: pd.DataFrame, hi_h: pd.DataFrame, lv_h: pd.DataFrame) -> dict:
    adults = p[p["group"].notna() & p["civilian"] & (p["AGEP"] >= 18)]
    civ = p[p["civilian"] & p["group"].notna()]
    households = pd.concat([hi_h, lv_h], ignore_index=True)
    # Leaver households can hold mates who were not in Hawaiʻi; build units on every member, group by filer.
    members = p[p["SERIALNO"].isin(civ["SERIALNO"])]
    units = score_units(build_units(members, households))
    units = units.merge(p[["person_id", "group"]], left_on="primary_filer_id", right_on="person_id", how="left")
    units = units[units["group"].notna()]
    ut = unit_table(units)
    net = {k: (ut["leavers"][k][0] - ut["movers in"][k][0]) for k in ("tax_current_total", "tax_act24_total")}
    return {"persons": person_table(adults), "by_age": by_age(adults), "born_in_hawaii": born_in_hawaii(adults),
            "units": ut,
            "net_tax_out_minus_in": net, "destinations": destinations(adults)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-sensitivity", action="store_true", help="skip the no-veteran and no-2020 cases")
    args = ap.parse_args()

    from _forecast_common import save_run, silence_noise

    silence_noise()
    hi_p, hi_h, lv_p, lv_h = load()
    p = prepare_persons(hi_p, lv_p)
    main_case = run(p, hi_h, lv_h)
    cases = {"main": main_case}
    if not args.no_sensitivity:
        vet_hh = p.loc[p["veteran"] & p["group"].notna(), "SERIALNO"]
        cases["no_veteran_households"] = run(p[~p["SERIALNO"].isin(vet_hh)], hi_h, lv_h)
        cases["no_2020"] = run(p[p["year"] != 2020], hi_h, lv_h)
    frames = {
        "results.csv": pd.concat([tidy(c, r) for c, r in cases.items()], ignore_index=True),
        "destinations.csv": pd.DataFrame(main_case["destinations"]),
        "irs_flows.csv": irs_flows(),
        "irs_by_agi.csv": pd.read_csv(IRS_CSV),
    }
    run_dir = save_run(
        "hawaii_leavers", frames, script="analyses/hawaii_leavers.py",
        params={"acs": "2020-2024 5-year", "tax_current": "act46 TY2026", "tax_act24": "act24 TY2027",
                "filing_status_factors": "1.0", "cases": list(cases)},
        inputs={f.name: f.stat().st_size for f in (US_DIR / "psam_p_leavers.csv", US_DIR / "psam_h_leavers.csv",
                                                    HI_DIR / "psam_p15.csv", IRS_CSV)})
    print(f"wrote {run_dir}")


if __name__ == "__main__":
    main()
