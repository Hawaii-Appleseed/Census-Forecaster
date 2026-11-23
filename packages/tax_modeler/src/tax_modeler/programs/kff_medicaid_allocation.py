"""Federal Medicaid cuts to Hawaiʻi under H.R.1 (P.L. 119-21), by KFF's method.

KFF ("Allocating CBO's Estimates of Federal Medicaid Spending Reductions Across the States: Enacted Reconciliation
Package", July 23, 2025) takes CBO's national ten-year outlay change for each Medicaid section of the law (CBO, July
21, 2025, $990 billion gross) and divides it among the states by a key chosen for the section:

* sections that act on the ACA expansion group (the work requirement 71119, six-month redeterminations 71107, the
  emergency Medicaid match 71110, cost sharing 71120): each expansion state's share of FY2024 federal spending on the
  expansion group (Wisconsin's "ACA-equivalent" adults counted);
* state-directed payments (71116): the House-passed bill's reduction over states with directed payments, the rest of
  the enacted reduction over states KFF judged likely or possibly affected, each by FY2023 federal managed care
  spending;
* provider taxes (71115): the House-passed bill's reduction over all states by FY2024 federal spending, the rest
  over expansion states with hospital or MCO taxes above 3.5% (Hawaiʻi is not among them);
* the uniform-tax waiver (71117): states with MCO taxes (Hawaiʻi has none); the expansion-incentive sunset (71114):
  non-expansion states; the home equity limit (71108): long-term care spending in states with limits over $1 million;
* everything else, and CBO's interactions (82.4% of Subtitle B's, which take $79 billion off), by each state's
  share of FY2024 federal Medicaid spending.

:func:`allocate_hawaii` applies those keys to every year of CBO's table. The inputs, each with its source, are in
``data/medicaid/kff_allocation_inputs.json`` (README alongside). The rebuild comes to $3,965 million over 2025-2034
against KFF's published $3,902 million for Hawaiʻi (+1.6%; KFF's state lists and baseline spending are not all
published); :func:`kff_aligned` scales every provision and year by one factor to KFF's figure, so the totals quoted
are KFF's own and the split by provision and year is the rebuild's. KFF's range is the estimate plus or minus 25%.

Nothing here is on the published chain: ``analyses/forecast_hr1_snap_medicaid.py`` is the caller.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from tax_modeler.errors import DataValidationError

_INPUTS = Path(__file__).resolve().parents[1] / "data" / "medicaid" / "kff_allocation_inputs.json"
YEARS = tuple(range(2025, 2035))
KFF_RANGE = 0.25

# Display groups, in KFF's Figure 1 terms.
GROUPS = {
    "71119": "Work requirement",
    "71107": "Six-month redeterminations",
    "71115": "Provider taxes",
    "71116": "State-directed payments",
    "71101": "Eligibility and enrollment rules",
    "71102": "Eligibility and enrollment rules",
    "71110": "Other expansion-group changes",
    "71120": "Other expansion-group changes",
}
EXPANSION_SECTIONS = ("71119", "71107", "71110", "71120")
ZERO_FOR_HAWAII = {"71114": "expansion-incentive sunset: non-expansion states only",
                   "71117": "uniform-tax waiver: states with MCO taxes; Hawaiʻi has none"}


def load_inputs(path: Path | None = None) -> dict:
    p = Path(path) if path else _INPUTS
    if not p.exists():
        raise DataValidationError(f"{p} is missing")
    return json.loads(p.read_text())


@dataclass(frozen=True)
class HawaiiShares:
    expansion: float          # of expansion-group federal spending FY2024 (with Wisconsin's ACA-equivalent)
    all_states: float         # of total federal Medicaid spending FY2024
    sdp_house: float          # of federal managed care FY2023, states with directed payments
    sdp_rest: float           # of federal managed care FY2023, states likely or possibly affected
    provider_tax_rest: float  # expansion states with hospital or MCO taxes over 3.5%: Hawaiʻi not on the list
    home_equity: float        # of federal LTC spending, states with home equity limits over $1 million


def hawaii_shares(inputs: dict) -> HawaiiShares:
    k, flags = inputs["keys"], inputs["hawaii_flags"]
    exp = k["expansion_federal_fy2024"]
    wisconsin = 992_273_291   # (24% - 11%) x Wisconsin's federal spending, per KFF's method (see README)
    mc = k["federal_managed_care_fy2023_mco_x_fedshare"]
    return HawaiiShares(
        expansion=exp["hawaii"] / (exp["us_or_subset_total"] + wisconsin),
        all_states=k["total_federal_fy2024"]["hawaii"] / k["total_federal_fy2024"]["us_or_subset_total"],
        sdp_house=(mc["hawaii"] / mc["us_or_subset_total"]) if flags["hawaii_in_sdp_house_equivalent_set"] else 0.0,
        sdp_rest=(k["federal_managed_care_sdp_likely_possibly_total"]["hawaii"]
                  / k["federal_managed_care_sdp_likely_possibly_total"]["us_or_subset_total"])
        if (flags["hawaii_sdp_likely_affected"] or flags["hawaii_sdp_possibly_affected"]) else 0.0,
        provider_tax_rest=0.0 if not flags["hawaii_in_provider_tax_above_3_5pct_expansion_list"] else float("nan"),
        home_equity=(k["ltc_users_2021_x_spend_per_user_x_fmap"]["hawaii"]
                     / k["ltc_users_2021_x_spend_per_user_x_fmap"]["us_or_subset_total"])
        if flags["hawaii_home_equity_limit_above_1M"] else 0.0,
    )


def _years(row: dict) -> pd.Series:
    return pd.Series({y: -float(row[str(y)]) for y in YEARS})      # reductions as positive dollars ($M)


def allocate_hawaii(inputs: dict | None = None) -> pd.DataFrame:
    """Hawaiʻi's federal Medicaid reduction by section and fiscal year, $ millions (positive = less federal money),
    by KFF's keys, before scaling to KFF's total. Columns: section, title, group, key, share, 2025..2034, total."""
    d = inputs or load_inputs()
    s = hawaii_shares(d)
    house = d["house_values"]
    rows = []
    for r in d["cbo_by_section"]:
        sec, yrs = str(r["section"]), _years(r["years"])
        group = GROUPS.get(sec, "Other changes")
        if sec in EXPANSION_SECTIONS:
            parts = [("expansion-group federal spending FY2024", s.expansion, yrs)]
        elif sec == "71116":
            h = _years(house["sdp_house_sec44133_years"])
            parts = [("directed payments, House-passed part: federal managed care FY2023", s.sdp_house, h),
                     ("directed payments, rest: likely or possibly affected states", s.sdp_rest, yrs - h)]
        elif sec == "71115":
            h = _years(house["provider_tax_house_years"])
            parts = [("provider taxes, House-passed part: federal spending FY2024", s.all_states, h),
                     ("provider taxes, rest: expansion states with taxes over 3.5%", s.provider_tax_rest, yrs - h)]
        elif sec in ZERO_FOR_HAWAII:
            parts = [(ZERO_FOR_HAWAII[sec], 0.0, yrs)]
        elif sec == "71108":
            parts = [("long-term care spending, states with home equity limits over $1M", s.home_equity, yrs)]
        else:
            parts = [("federal spending FY2024", s.all_states, yrs)]
        for key, share, y in parts:
            hi = y * share
            rows.append({"section": sec, "title": r["title"], "group": group, "key": key, "share": share,
                         **{str(k): float(v) for k, v in hi.items()}, "total": float(hi.sum())})
    # CBO's interactions, the Medicaid part (KFF: 82.4%), by federal spending: they add back money.
    inter = d["cbo_interactions_subtitleB_outlays"]
    kff_share = (-d["cbo_other"]["medicaid_chapter_total"] - d["kff_result"]["us"] / 1e6) / d["cbo_other"]["subtitle_B_interactions_total"]
    iy = pd.Series({y: -float(inter[str(y)]) * kff_share * s.all_states for y in YEARS})
    rows.append({"section": "interactions", "title": "Interactions with other provisions (Medicaid share)",
                 "group": "Interactions", "key": f"federal spending FY2024 (x {kff_share:.3f} of Subtitle B)",
                 "share": s.all_states, **{str(k): float(v) for k, v in iy.items()}, "total": float(iy.sum())})
    return pd.DataFrame(rows)


@dataclass(frozen=True)
class KffAligned:
    by_section: pd.DataFrame      # scaled rows of allocate_hawaii
    by_group: pd.DataFrame        # group x year, $ millions, plus total
    by_year: pd.DataFrame         # year, estimate, low, high ($ millions)
    rebuild_total: float          # before scaling, $ millions
    kff_total: float              # KFF's published Hawaiʻi figure, $ millions
    scale: float


def kff_aligned(inputs: dict | None = None) -> KffAligned:
    """The rebuild scaled to KFF's published Hawaiʻi total, with KFF's plus-or-minus 25 percent range."""
    d = inputs or load_inputs()
    raw = allocate_hawaii(d)
    rebuild = float(raw["total"].sum())
    kff = d["kff_result"]["hawaii"] / 1e6
    scale = kff / rebuild
    cols = [str(y) for y in YEARS] + ["total"]
    scaled = raw.copy()
    scaled[cols] = scaled[cols] * scale
    order = list(dict.fromkeys(GROUPS.values())) + ["Other changes", "Interactions"]
    by_group = scaled.groupby("group")[cols].sum().reindex([g for g in order if g in set(scaled["group"])])
    yearly = scaled[[str(y) for y in YEARS]].sum()
    by_year = pd.DataFrame({"year": list(YEARS), "estimate": yearly.to_numpy(),
                            "low": yearly.to_numpy() * (1 - KFF_RANGE), "high": yearly.to_numpy() * (1 + KFF_RANGE)})
    return KffAligned(by_section=scaled, by_group=by_group.reset_index(), by_year=by_year,
                      rebuild_total=rebuild, kff_total=kff, scale=scale)
