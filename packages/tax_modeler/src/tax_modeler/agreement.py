"""Do the revenue base and the poverty population agree on what they share?

The model keeps two tax-unit populations. The revenue base (``tax_modeler.base``) is
the 2020-24 five-year PUMS, raked to the Department of Taxation's filer counts.
The poverty population (``scripts/poverty_impact_report.py``) is the 2024 one-year
PUMS at the Census Bureau's published weights, with SPM units attached. They
describe the same state, so any quantity both can measure should be close.
:func:`compare_populations` measures a set of such quantities on each and says how
far apart they are, in per cent and in the poverty sample's standard errors, with
the Department's own figure alongside where it publishes one.

The poverty population is read as the working-family step uses it, not as it is
cached: aged to the calibration year and with the EITC by-children reweight
(``poverty_impact_report._apply_eitc_reweight``) applied, before take-up. That
reweight exists because the unadjusted population is short in 1- and 2-child
EITC units; read before it, the EITC rows show a 13 per cent shortfall that the
step has already corrected, and after it a 6 per cent excess.

It changes nothing. It exists so that the gap is visible on every run before
anyone adjusts a weight, and so a later change to the poverty weights can be
judged by whether it narrows these gaps.

What the standard error means here. It is the ACS successive-difference-replication
error of the *poverty* population's figure (80 replicates, factor 4/80), the same
formula the working-family-credits page uses. The revenue base carries 80 replicate
weight columns too, but they are the Census originals: its main weight was raked
and they were not, so their sums (about 725,000 units) no longer match the weight's
(about 664,000). They would give the base a wrong error, so none is computed, and
the gap is judged against the poverty sample's error alone. That overstates the
significance of a gap a little, since the base is a larger sample, and the check
says "gap in poverty standard errors" for that reason.

Two benchmarks. The Department of Taxation's tables count resident returns. The IRS
table (:mod:`tax_modeler.calibration.irs_soi_hi_table2`) counts every federal return
filed from a Hawaii address, including military families and people who moved in or
out during the year, and runs 15 to 27 percent above the Department's in every class
above $50,000. The revenue base is raked to the Department's counts, which is right
for state tax revenue. The poverty population describes everyone who lives in Hawaii,
which is what the IRS counts, so it is judged against the IRS: returns, wage returns,
wages and the average wage per return by IRS class. Those rows carry the IRS figure in
``irs`` and are marked ``group == "irs"``.

Both populations are scored with the 2023 federal credit rules before the EITC rows
are read, so those rows compare eligibility on one set of rules, before take-up.
They are not claims, and the Department's 78,399 state EITC claims are not an
anchor for them.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

#: ACS successive-difference replication: variance = (4/80) * sum of squared deviations.
SDR_FACTOR = 4 / 80
STATUSES = ("single", "married_filing_jointly", "head_of_household", "married_filing_separately")
_STATUS_LABEL = {
    "single": "Single",
    "married_filing_jointly": "Married filing jointly",
    "head_of_household": "Head of household",
    "married_filing_separately": "Married filing separately",
}


def _money(x: float) -> str:
    if np.isinf(x):
        return "and up"
    return f"${x / 1000:,.0f}K" if x < 1e6 else f"${x / 1e6:,.1f}M"


def _band_label(lo: float, hi: float) -> str:
    return f"AGI {_money(lo)} and up" if np.isinf(hi) else f"AGI {_money(lo)} to {_money(hi)}"


def _irs_label(lo: float, hi: float) -> str:
    """An IRS Table 2 class, e.g. ``$75K to $100K`` or ``$1.0M and up``."""
    def m(x: float) -> str:
        if x == 1:
            return "$1"
        return f"${x / 1000:,.0f}K" if x < 1e6 else f"${x / 1e6:,.1f}M"

    if np.isinf(lo):
        return f"below {m(hi)}"
    return f"{m(lo)} and up" if np.isinf(hi) else f"{m(lo)} to {m(hi)}"


def replicate_columns(units: pd.DataFrame) -> list[str]:
    """The ``weight_r01`` .. ``weight_r80`` columns, in order, or [] if absent."""
    cols = [f"weight_r{i:02d}" for i in range(1, 81)]
    return cols if all(c in units.columns for c in cols) else []


def _anchors() -> dict[str, float]:
    """The Department of Taxation's figures for the quantities below, by measure name."""
    from tax_modeler.calibration import dotax_base

    out: dict[str, float] = {}
    for (lo, hi), n in dotax_base.filer_targets().items():
        out[f"Returns, {_band_label(lo, hi)}"] = float(n)
    for (lo, hi), m in dotax_base.agi_targets_M().items():
        out[f"AGI $M, {_band_label(lo, hi)}"] = float(m)
    for status, n in dotax_base.status_targets().items():
        out[f"Returns, {_STATUS_LABEL[status]}"] = float(n)
    out["Returns, all classes"] = float(sum(dotax_base.filer_targets().values()))
    return out


def _irs_anchors() -> dict[str, float]:
    """The IRS Table 2 figures for the IRS-band measures, by measure name."""
    from tax_modeler.calibration import irs_soi_hi_table2 as irs

    out: dict[str, float] = {}
    ret, wret, wages, avg = irs.returns(), irs.wage_returns(), irs.wages_M(), irs.average_wage()
    for band in irs.bands()[1:]:                      # the under-$1 class is non-filers here
        lab = _irs_label(*band)
        out[f"Returns, IRS band {lab}"] = ret[band]
        out[f"Wage returns, IRS band {lab}"] = wret[band]
        out[f"Wages $M, IRS band {lab}"] = wages[band]
        out[f"Average wage, IRS band {lab}"] = avg[band]
    return out


def design(units: pd.DataFrame) -> tuple[list[str], np.ndarray]:
    """One column per measure: summing ``weight * column`` gives the measure.

    Returns ``(names, X)`` with ``X`` of shape (units, measures). Indicator columns
    count units; value columns total dollars, in millions for AGI and EITC.
    """
    from tax_modeler.calibration import dotax_base

    agi = units["federal_agi"].to_numpy(dtype=float)
    kids = units["num_qualifying_children"].fillna(0).to_numpy(dtype=float)
    status = units["filing_status"].astype(str).to_numpy()
    names: list[str] = []
    cols: list[np.ndarray] = []

    def add(name: str, x: np.ndarray) -> None:
        names.append(name)
        cols.append(np.asarray(x, dtype=float))

    add("Tax units", np.ones(len(units)))
    add("Units, AGI below $0", agi < 0)
    add("Returns, all classes", agi >= 0)
    for lo, hi in dotax_base.filer_targets():
        inside = (agi >= lo) & (agi < hi)
        add(f"Returns, {_band_label(lo, hi)}", inside)
    for lo, hi in dotax_base.agi_targets_M():
        inside = (agi >= lo) & (agi < hi)
        add(f"AGI $M, {_band_label(lo, hi)}", np.where(inside, agi, 0.0) / 1e6)
    for s in STATUSES:
        add(f"Returns, {_STATUS_LABEL[s]}", status == s)
    add("Units with a qualifying child", kids > 0)
    add("Qualifying children", kids)
    add("Units with 1 qualifying child", kids == 1)
    add("Units with 2 qualifying children", kids == 2)
    add("Units with 3+ qualifying children", kids >= 3)
    if "eitc_amount" in units.columns:
        eitc = units["eitc_amount"].fillna(0).to_numpy(dtype=float)
        add("Units eligible for the federal EITC", eitc > 0)
        add("Federal EITC eligibility $M", eitc / 1e6)
        add("EITC-eligible units with children", (eitc > 0) & (kids > 0))
    if "primary_wagp" in units.columns:
        from tax_modeler.calibration import irs_soi_hi_table2 as irs
        from tax_modeler.liability.federal import FEDERAL_W2_WAGE_FACTOR

        def col(c: str) -> np.ndarray:
            return units[c].fillna(0).to_numpy(dtype=float) if c in units.columns else np.zeros(len(units))

        wages = col("primary_wagp") + col("secondary_wagp")
        for lo, hi in irs.bands()[1:]:
            lab = _irs_label(lo, hi)
            inside = (agi >= lo) & (agi < hi)
            add(f"Returns, IRS band {lab}", inside)
            add(f"Wage returns, IRS band {lab}", inside & (wages > 0))
            add(f"Wages $M, IRS band {lab}", np.where(inside, wages * FEDERAL_W2_WAGE_FACTOR, 0.0) / 1e6)
    return names, np.column_stack(cols)


#: Bands from this AGI up are filled in the revenue pipeline by the synthetic
#: top-income tail, added after the base this check reads, so the base is short
#: there by design and a gap in them says nothing about the weights.
TAIL_FROM_AGI = 500_000


def is_tail_measure(name: str) -> bool:
    """True for a measure in an AGI band the synthetic tail fills ($500K and up)."""
    if "IRS band" in name:
        return any(t in name for t in ("IRS band $500K to $1.0M", "IRS band $1.0M and up"))
    return name.startswith(("Returns, AGI $", "AGI $M, AGI $")) and any(
        name.endswith(t) for t in ("$500K to $750K", "$750K to $1.0M", "$1.0M and up")
    )


def _ratio(num, den):
    """``num / den``, NaN where ``den`` is zero."""
    num, den = np.asarray(num, dtype=float), np.asarray(den, dtype=float)
    return np.divide(num, den, out=np.full(np.broadcast(num, den).shape, np.nan), where=den > 0)


def _with_averages(names: list[str], value: np.ndarray, theta: np.ndarray | None):
    """Append the average wage per wage return for each IRS band, and its replicates."""
    idx = {n: i for i, n in enumerate(names)}
    extra, vals, thetas = [], [], []
    for n in names:
        if not n.startswith("Wages $M, IRS band "):
            continue
        lab = n[len("Wages $M, IRS band "):]
        k = idx.get(f"Wage returns, IRS band {lab}")
        if k is None:
            continue
        extra.append(f"Average wage, IRS band {lab}")
        vals.append(_ratio(value[idx[n]] * 1e6, value[k]))
        if theta is not None:
            thetas.append(_ratio(theta[:, idx[n]] * 1e6, theta[:, k]))
    if not extra:
        return names, value, theta
    value = np.concatenate([value, np.array(vals)])
    if theta is not None:
        theta = np.column_stack([theta, *thetas])
    return names + extra, value, theta


def profile(units: pd.DataFrame, *, with_error: bool) -> pd.DataFrame:
    """Each measure on one population: ``value`` and, if asked, ``se``."""
    names, X = design(units)
    w = units["weight"].to_numpy(dtype=float)
    value = w @ X
    theta = None
    if with_error:
        reps = replicate_columns(units)
        if not reps:
            raise ValueError("with_error needs weight_r01..weight_r80 on the frame")
        R = units[reps].to_numpy(dtype=float).T          # (80, n)
        theta = R @ X                                     # (80, measures)
    names, value, theta = _with_averages(names, value, theta)
    out = pd.DataFrame({"measure": names, "value": value})
    if theta is not None:
        out["se"] = np.sqrt(SDR_FACTOR * ((theta - value) ** 2).sum(axis=0))
    return out


def carry_weight_changes(before: pd.DataFrame, after: pd.DataFrame) -> pd.DataFrame:
    """``after`` with each replicate weight scaled by the factor that changed ``weight``.

    A step that edits ``weight`` (the EITC by-children reweight) leaves the 80
    replicates on the old scale, so every replicate would sit a constant shift
    from the headline and the standard error would measure the edit, not the
    sample. Every such edit here is a multiplicative factor on the unit's weight,
    so scaling the replicates by the same factor is their replicate (the same
    argument ``forecast_working_family_credits._replicate_ratios`` makes).
    """
    if len(before) != len(after) or not before.index.equals(after.index):
        raise ValueError("carry_weight_changes needs the same units in the same order")
    out = after.copy()
    reps = replicate_columns(out)
    if reps:
        w0 = before["weight"].to_numpy(dtype=float)
        w1 = after["weight"].to_numpy(dtype=float)
        factor = np.divide(w1, w0, out=np.ones_like(w1), where=w0 > 0)
        out[reps] = out[reps].to_numpy(dtype=float) * factor[:, None]
    return out


def score_eitc(units: pd.DataFrame, *, tax_year: int = 2023) -> pd.DataFrame:
    """``units`` with the federal EITC scored on ``tax_year`` rules, as the revenue base is.

    Works on a copy and leaves numpy's global generator where it was: scoring
    must not move any later random draw in the step that calls this.
    """
    from tax_modeler.pipeline import compute_base_tax

    state = np.random.get_state()
    try:
        return compute_base_tax(units.copy(), tax_year=tax_year)
    finally:
        np.random.set_state(state)


def compare_populations(base: pd.DataFrame, poverty: pd.DataFrame) -> pd.DataFrame:
    """The two populations side by side on every shared measure.

    ``base`` is the calibrated revenue base, ``poverty`` the poverty population.
    Columns: ``measure``, ``base``, ``poverty``, ``gap`` (poverty minus base),
    ``gap_pct``, ``poverty_se``, ``gap_in_se``, ``dotax`` (the Department's figure,
    where it publishes one), ``base_vs_dotax_pct`` and ``poverty_vs_dotax_pct``, and the
    same three for the IRS (``irs``, ``base_vs_irs_pct``, ``poverty_vs_irs_pct``).
    ``group`` is ``shared`` for the measures both populations are compared on and
    ``irs`` for the IRS-class returns, wages and average wage.
    """
    if "eitc_amount" not in poverty.columns:
        poverty = score_eitc(poverty)
    b = profile(base, with_error=False).set_index("measure")["value"]
    p = profile(poverty, with_error=True).set_index("measure")
    out = pd.DataFrame({"base": b, "poverty": p["value"], "poverty_se": p["se"]})
    out["gap"] = out["poverty"] - out["base"]
    out["gap_pct"] = 100 * out["gap"] / out["base"].replace(0, np.nan)
    out["gap_in_se"] = out["gap"] / out["poverty_se"].replace(0, np.nan)
    anchors = _anchors()
    out["dotax"] = pd.Series(anchors).reindex(out.index)
    out["base_vs_dotax_pct"] = 100 * (out["base"] / out["dotax"] - 1)
    out["poverty_vs_dotax_pct"] = 100 * (out["poverty"] / out["dotax"] - 1)
    out["irs"] = pd.Series(_irs_anchors()).reindex(out.index)
    out["base_vs_irs_pct"] = 100 * (out["base"] / out["irs"] - 1)
    out["poverty_vs_irs_pct"] = 100 * (out["poverty"] / out["irs"] - 1)
    out = out.reset_index(names="measure")
    out["tail_band"] = out["measure"].map(is_tail_measure)
    out["group"] = np.where(out["measure"].str.contains("IRS band"), "irs", "shared")
    return out


def summarize(table: pd.DataFrame, *, z: float = 2.0) -> str:
    """A short text report: the headline gaps and how many exceed ``z`` standard errors."""
    t = table.set_index("measure")
    lines = [
        "Population agreement: revenue base (2020-24 5-year, raked to DOTAX) vs "
        "poverty population (2024 1-year, Census weights)",
        "",
        f"{'':46}{'base':>14}{'poverty':>14}{'gap %':>9}{'gap/SE':>8}{'DOTAX':>14}",
    ]
    for name in (
        "Tax units", "Returns, all classes", "Qualifying children",
        "Units with a qualifying child", "Units eligible for the federal EITC",
        "Federal EITC eligibility $M",
    ):
        if name not in t.index:
            continue
        r = t.loc[name]
        dotax = "" if pd.isna(r["dotax"]) else f"{r['dotax']:,.0f}"
        lines.append(f"{name:46}{r['base']:>14,.1f}{r['poverty']:>14,.1f}"
                     f"{r['gap_pct']:>9.1f}{r['gap_in_se']:>8.1f}{dotax:>14}")
    shared = t[t["group"] == "shared"]
    counted = shared[(shared["poverty_se"] > 0) & ~shared["tail_band"]]
    wide = counted[counted["gap_in_se"].abs() > z]
    lines += ["", f"{len(wide)} of {len(counted)} measures differ by more than {z:g} poverty "
                  f"standard errors (the {int(shared['tail_band'].sum())} measures in bands from "
                  f"${TAIL_FROM_AGI // 1000}K up are left out: the revenue base gets its top "
                  f"tail by synthesis, after this base)."]
    if len(wide):
        worst = wide.reindex(wide["gap_in_se"].abs().sort_values(ascending=False).index).head(5)
        lines.append("Widest: " + "; ".join(
            f"{m} ({r.gap_pct:+.1f}%, {r.gap_in_se:+.1f} SE)" for m, r in worst.iterrows()))
    irs_rows = t[t.index.str.startswith("Returns, IRS band") & ~t["tail_band"]]
    if len(irs_rows):
        lines += ["", "Against the IRS (every federal return filed from a Hawaii address), by AGI class:",
                  f"{'':26}{'IRS returns':>13}{'base':>9}{'poverty':>9}{'avg wage, poverty':>20}"]
        for name, r in irs_rows.iterrows():
            lab = name.split("IRS band ", 1)[1]
            row_avg = t.loc[f"Average wage, IRS band {lab}"] if f"Average wage, IRS band {lab}" in t.index else None
            avg_txt = "" if row_avg is None or pd.isna(row_avg["poverty_vs_irs_pct"]) else f"{row_avg['poverty_vs_irs_pct']:+.0f}%"
            lines.append(f"{lab:26}{r['irs']:>13,.0f}{r['base_vs_irs_pct']:>+8.0f}%{r['poverty_vs_irs_pct']:>+8.0f}%{avg_txt:>20}")
        lines.append("(the Department of Taxation's tables count resident returns only, a narrower group; "
                     "see tax_modeler.calibration.irs_soi_hi_table2)")
    return "\n".join(lines)
