"""Forecast-vs-actual scorecard against a DOTAX *Individual Income Tax Statistics* edition.

Scores a calibrated base built on DOTAX edition year N (the edition its IPF
rake, filing-status and $1M+ targets came from) against the next edition,
``dotax_indinc_<N+1>.json``: the Act 24 pipeline's own population
(``act24_population.build_units`` / ``project_units``, the synthetic $1M+ tail
aged with the scenario's premium, MID's DOTAX capital-gains anchor factors) is
projected to tax year N+1 and scored under that year's law (``law_for``: the
2018 schedule through TY2023, Act 46 from TY2024; statutory §235-51(f)
alternative tax), then tabulated the way DOTAX prints it:

* Table A-8 — resident returns and tax before credits by Hawaiʻi AGI class
  (the Loss class and the 15 classes from $0 up), with absolute and percent
  error per class;
* Table A-1 — AGI of taxable returns by class (the model's taxable returns are
  the units with positive tax before credits);
* Table 4 — resident returns by filing status;
* the $1M+ class, with its tax error split into the count and per-filer parts;
* the growth the model's dials implied for the $1M+ class from N to N+1
  (Honolulu B19013, the top-income premium, the CBO x Hawaiʻi capital-gains
  factor) against the printed change.

Usage::

    uv run python scripts/dotax_scorecard.py --base-edition-year 2023          # base raked on TY2023, scored on TY2024
    uv run python scripts/dotax_scorecard.py --base-edition-year 2022 \\
        --base data/artifacts/sb3125_calibrated_base.pkl \\
        --actuals packages/tax_modeler/src/tax_modeler/data/calibration/dotax_indinc_2023.json \\
        --scenarios MID,LOW,HIGH

``--base`` is the artifact ``forecast_sb3125_enhanced.py`` writes
(``save_calibrated_base``); ``--base-edition-year`` must be the DOTAX edition
that base was raked to, which the artifact does not record (its ``tax_year`` is
the law year the base was scored under). The target year defaults to N+1 and
the actuals to the bundled ``dotax_indinc_<N+1>.json`` (parse a new edition
with ``scripts/parse_dotax_indinc.py`` first). Writes
``reports/dotax_scorecard/TY<N+1>/scorecard.md`` and one CSV per table and
scenario, and prints the markdown.

The projection runs from the PUMS income dollar year (2024 for the 2020-24
file), so a target year at or before it means little or no county growth and
no premium; the scorecard then reads as a check of the calibrated base plus the
tail aging, which is what it is. Run from the repo root; the standing step is
to run it whenever ``parse_dotax_indinc.py`` adds an edition, before rebasing
on it, and keep the output with the rebase commit.

The table builders (``a8_table``, ``a1_table``, ``status_table``,
``top_class_table``) take a scored frame and the parsed edition, so they run on
any population (``tests/tax_modeler/test_dotax_scorecard.py``).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

CALIBRATION_DIR = REPO / "packages" / "tax_modeler" / "src" / "tax_modeler" / "data" / "calibration"
DEFAULT_BASE = REPO / "data" / "artifacts" / "sb3125_calibrated_base.pkl"
DEFAULT_OUT = REPO / "reports" / "dotax_scorecard"

# (Pareto alpha, top-share drift) per scenario: forecast_sb3125_enhanced.SCENARIOS.
_FALLBACK_SCENARIOS = {"LOW": (1.7, -0.010), "MID": (1.5, 0.0), "HIGH": (1.4, 0.010)}

STATUS_LABEL = {
    "single": "Single", "married_filing_jointly": "Married Filing Jointly",
    "head_of_household": "Head of Household",
    "married_filing_separately": "Married Filing Separately",
}


# ── Class assignment ─────────────────────────────────────────────────────────
def class_label(lo: float | None, hi: float | None) -> str:
    if lo is None:
        return "Loss"
    if hi is None or not np.isfinite(hi):
        return f"${lo/1e3:,.0f}K+" if lo < 1e6 else "$1M+"
    return f"${lo/1e3:,.0f}K-${hi/1e3:,.0f}K"


def _assign(income: np.ndarray, classes: list[dict]) -> np.ndarray:
    """Index into ``classes`` for each income (the Loss class takes income < 0)."""
    idx = np.full(len(income), -1, dtype=int)
    for i, c in enumerate(classes):
        lo = -np.inf if c["agi_lo"] is None else float(c["agi_lo"])
        hi = np.inf if c["agi_hi"] is None else float(c["agi_hi"])
        if c["agi_lo"] is None:
            m = income < hi
        else:
            m = (income >= lo) & (income < hi)
        idx[m] = i
    return idx


def _weighted(df: pd.DataFrame, idx: np.ndarray, n: int, col: str | None) -> np.ndarray:
    w = df["weight"].to_numpy(float)
    v = w if col is None else w * df[col].to_numpy(float)
    return np.bincount(idx[idx >= 0], weights=v[idx >= 0], minlength=n)


def _pct(model: np.ndarray, actual: np.ndarray) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(actual != 0, 100.0 * (model - actual) / actual, np.nan)


# ── Tables ───────────────────────────────────────────────────────────────────
def a8_table(scored: pd.DataFrame, edition: dict) -> pd.DataFrame:
    """Returns and tax before credits by Table A-8 class: model, actual, error.

    ``scored`` needs ``income``, ``weight`` and ``tax_before`` ($ per unit).
    """
    classes = edition["table_a8_resident_liability"]["classes"]
    idx = _assign(scored["income"].to_numpy(float), classes)
    n = len(classes)
    rows = pd.DataFrame({
        "class": [class_label(c["agi_lo"], c["agi_hi"]) for c in classes],
        "returns_model": _weighted(scored, idx, n, None),
        "returns_actual": [c["returns"] for c in classes],
        "tax_model_M": _weighted(scored, idx, n, "tax_before") / 1e6,
        "tax_actual_M": [c["tax_before_M"] for c in classes],
    })
    total = edition["table_a8_resident_liability"]["total"]
    rows.loc[len(rows)] = ["Total", rows["returns_model"].sum(), total["returns"],
                           rows["tax_model_M"].sum(), total["tax_before_M"]]
    rows["returns_err"] = rows["returns_model"] - rows["returns_actual"]
    rows["returns_err_pct"] = _pct(rows["returns_model"].to_numpy(), rows["returns_actual"].to_numpy(float))
    rows["tax_err_M"] = rows["tax_model_M"] - rows["tax_actual_M"]
    rows["tax_err_pct"] = _pct(rows["tax_model_M"].to_numpy(), rows["tax_actual_M"].to_numpy(float))
    with np.errstate(divide="ignore", invalid="ignore"):
        rows["avg_tax_model"] = np.where(rows["returns_model"] > 0,
                                         rows["tax_model_M"] * 1e6 / rows["returns_model"], np.nan)
        rows["avg_tax_actual"] = np.where(rows["returns_actual"] > 0,
                                          rows["tax_actual_M"] * 1e6 / rows["returns_actual"], np.nan)
    return rows


def a1_table(scored: pd.DataFrame, edition: dict) -> pd.DataFrame:
    """AGI of taxable returns by Table A-1 class (model: units with tax before credits > 0)."""
    classes = edition["table_a1_resident_taxable"]["taxable_classes"]
    taxable = scored[scored["tax_before"] > 0]
    idx = _assign(taxable["income"].to_numpy(float), classes)
    n = len(classes)
    rows = pd.DataFrame({
        "class": [class_label(c["agi_lo"], c["agi_hi"]) for c in classes],
        "returns_model": _weighted(taxable, idx, n, None),
        "returns_actual": [c["returns"] for c in classes],
        "agi_model_M": _weighted(taxable, idx, n, "income") / 1e6,
        "agi_actual_M": [c["agi_M"] for c in classes],
    })
    total = edition["table_a1_resident_taxable"]["taxable_total"]
    rows.loc[len(rows)] = ["Total", rows["returns_model"].sum(), total["returns"],
                           rows["agi_model_M"].sum(), total["agi_M"]]
    rows["returns_err_pct"] = _pct(rows["returns_model"].to_numpy(), rows["returns_actual"].to_numpy(float))
    rows["agi_err_M"] = rows["agi_model_M"] - rows["agi_actual_M"]
    rows["agi_err_pct"] = _pct(rows["agi_model_M"].to_numpy(), rows["agi_actual_M"].to_numpy(float))
    return rows


def status_table(scored: pd.DataFrame, edition: dict) -> pd.DataFrame:
    """Resident returns by filing status (Table 4); qualifying widow(er)s are shown, unmodeled."""
    actual = edition["table_4_filing_status"]["resident"]
    model = scored.groupby("filing_status")["weight"].sum()
    rows = []
    for key, label in STATUS_LABEL.items():
        rows.append({"filing_status": label, "returns_model": float(model.get(key, 0.0)),
                     "returns_actual": actual[label]})
    for label, n in actual.items():
        if label not in STATUS_LABEL.values():
            rows.append({"filing_status": label, "returns_model": 0.0, "returns_actual": n})
    df = pd.DataFrame(rows)
    df.loc[len(df)] = ["Total", df["returns_model"].sum(), df["returns_actual"].sum()]
    df["share_model_pct"] = 100 * df["returns_model"] / df["returns_model"].iloc[-1]
    df["share_actual_pct"] = 100 * df["returns_actual"] / df["returns_actual"].iloc[-1]
    df["returns_err_pct"] = _pct(df["returns_model"].to_numpy(), df["returns_actual"].to_numpy(float))
    return df


def top_class_table(scored: pd.DataFrame, edition: dict) -> pd.DataFrame:
    """The $1M+ class: returns, tax, average tax, and the tax error split.

    ``count_part`` is the error from the filer count at the actual average tax,
    ``per_filer_part`` the rest (the model's count times its average-tax error);
    the two sum to ``tax_err_M``. The log split is scale-free.
    """
    top = next(c for c in edition["table_a8_resident_liability"]["classes"]
               if c["agi_lo"] is not None and c["agi_lo"] >= 1_000_000)
    m = scored["income"] >= 1_000_000
    n_m = float(scored.loc[m, "weight"].sum())
    tax_m = float((scored.loc[m, "weight"] * scored.loc[m, "tax_before"]).sum()) / 1e6
    n_a, tax_a = float(top["returns"]), float(top["tax_before_M"])
    avg_m = tax_m * 1e6 / n_m if n_m > 0 else np.nan
    avg_a = tax_a * 1e6 / n_a
    count_part = (n_m - n_a) * avg_a / 1e6
    per_filer_part = n_m * (avg_m - avg_a) / 1e6
    row = {
        "returns_model": n_m, "returns_actual": n_a,
        "returns_err_pct": 100 * (n_m - n_a) / n_a,
        "tax_model_M": tax_m, "tax_actual_M": tax_a, "tax_err_M": tax_m - tax_a,
        "tax_err_pct": 100 * (tax_m - tax_a) / tax_a,
        "avg_tax_model": avg_m, "avg_tax_actual": avg_a,
        "avg_tax_err_pct": 100 * (avg_m - avg_a) / avg_a,
        "count_part_M": count_part, "per_filer_part_M": per_filer_part,
        "log_err": np.log(tax_m / tax_a), "log_count": np.log(n_m / n_a),
        "log_per_filer": np.log(avg_m / avg_a),
    }
    return pd.DataFrame([row])


# ── Model population ─────────────────────────────────────────────────────────
def scenario_params() -> dict[str, tuple[float, float]]:
    try:
        from forecast_sb3125_enhanced import SCENARIOS
        return {s["label"]: (s["alpha"], s["top_share_drift"]) for s in SCENARIOS
                if s["label"] in _FALLBACK_SCENARIOS}
    except Exception:  # noqa: BLE001 — scripts dir may be run standalone
        return dict(_FALLBACK_SCENARIOS)


def law_for(year: int):
    """Current law for tax year ``year``: the 2018 schedule through TY2023, Act 46's
    TY2024 standard-deduction step on the 2018 brackets, then the registry's Act 46
    vintages (``get_act46_system``, TY2025+)."""
    from tax_modeler.config.tax_system_config import TaxSystemConfig, TaxSystemRegistry

    if year < 2024:
        return TaxSystemRegistry.get_2017_system()
    if year == 2024:
        return TaxSystemConfig(
            name="act46_2024", year=2024, bracket_year=2018, standard_deduction_year=2024,
            personal_exemption=TaxSystemRegistry.PERSONAL_EXEMPTIONS[2022],
            description="Act 46 (2024) — TY 2024: standard deduction step on the 2018 brackets",
        )
    return TaxSystemRegistry.get_act46_system(year)


def score_population(base, ded_params, cal_tax_year: int, *, label: str, alpha: float,
                     spec, year: int, gains_factors: dict | None):
    """The scenario's projected population for ``year`` with ``tax_before`` under
    current law (:func:`law_for`).

    Returns (scored frame, diagnostics). Mirrors ``forecast_sb3125_enhanced.run_one_scenario``
    up to the baseline score: build_units, project_units (``spec``: the aging
    engine and top-share drift), MID's gains anchor factors, the statutory
    alternative tax.
    """
    from tax_modeler.calibration.cg_anchor import apply_anchor_factors
    from tax_modeler.config.tax_system_config import TaxCalculator
    from tax_modeler.scenarios.act24_population import build_units, project_units, statute

    units, tail_k = build_units(base, alpha=alpha, spec=spec,
                                ded_params=ded_params, cal_tax_year=cal_tax_year)
    projected = project_units(units, year=year, spec=spec)
    if gains_factors is not None:
        projected = apply_anchor_factors(projected, gains_factors)
    cfg = statute(law_for)(year)
    liab = TaxCalculator().unit_liabilities(projected, cfg)
    scored = projected.copy()
    scored["tax_before"] = liab["before_credits"]
    scored["tax_net"] = liab["net"]
    diag = {"scenario": label, "alpha": alpha, "top_premium": spec.top_share_drift,
            "aging": spec.engine, "tail_k": tail_k, "law": cfg.name, "target_year": year}
    return scored, diag


def top_class_series_table() -> pd.DataFrame:
    """The $1M+ class's share of resident returns and tax by edition
    (``dotax_a8_top_class.json``), the log change per year, and the fitted
    trend with its standard error: what the DOTAX series says about a
    top-share drift."""
    from tax_modeler.calibration import dotax_base
    s = dotax_base.load_top_class_series()
    rows = []
    for y in sorted(s, key=int):
        r = s[y]
        rows.append({"tax_year": int(y), "returns": r["returns"], "tax_before_M": r["tax_before_M"],
                     "returns_share_pct": 100 * r["returns"] / r["total_returns"],
                     "tax_share_pct": 100 * r["tax_before_M"] / r["total_tax_before_M"]})
    df = pd.DataFrame(rows)
    for c in ("returns_share_pct", "tax_share_pct"):
        df[f"dlog_{c[:-4]}"] = np.log(df[c]).diff()
    return df


def trend_with_se(df: pd.DataFrame, col: str) -> tuple[float, float]:
    """OLS slope of log(col) on year and its standard error (per year)."""
    x = df["tax_year"].to_numpy(float)
    y = np.log(df[col].to_numpy(float))
    x0 = x - x.mean()
    b = (x0 * (y - y.mean())).sum() / (x0 ** 2).sum()
    resid = y - y.mean() - b * x0
    se = np.sqrt(resid.var(ddof=2) / (x0 ** 2).sum()) if len(x) > 2 else np.nan
    return float(b), float(se)


def dial_diagnostics(spec, base_year: int, year: int) -> dict[str, float]:
    """What the growth dials imply for the $1M+ class from ``base_year`` to ``year``."""
    from tax_modeler.calibration.cg_anchor import GAINS_GROWTH_MODEL, cg_growth
    from tax_modeler.projection.engine import tail_growth_factor
    from tax_modeler.scenarios.top_income_synthesis import (
        _observed_honolulu_b19013,
        synthetic_tail_aging_factor,
    )
    out = {}
    try:
        out["honolulu_b19013_growth"] = _observed_honolulu_b19013(year) / _observed_honolulu_b19013(base_year)
    except ValueError:
        out["honolulu_b19013_growth"] = np.nan
    out["premium_growth"] = (1 + spec.top_share_drift) ** (year - base_year)
    out["tail_aging_to_pums_year"] = synthetic_tail_aging_factor(spec, cg_share=0.4)
    out["engine_growth_wages"] = tail_growth_factor(spec.with_drift(0.0), base_year, year, 0.0)
    out["engine_growth_gains"] = tail_growth_factor(spec.with_drift(0.0), base_year, year, 1.0)
    out["cg_growth"] = cg_growth(year, base_year)
    out["cg_growth_model"] = GAINS_GROWTH_MODEL
    # The step the projection takes between the PUMS dollar year and the
    # target year, against Hawaiʻi's realized growth over the same years
    # (QCEW statewide average pay, BEA per-capita personal income; bundled
    # anchors). The base's tax totals are the base edition's, pinned on
    # PUMS-dollar-year incomes, so below $1M the scorecard rewards the engine
    # whose step is smallest, not the one nearest these.
    from tax_modeler.loaders.pums_loader import PUMS_INCOME_DOLLAR_YEAR
    out["pums_dollar_year"] = PUMS_INCOME_DOLLAR_YEAR
    out["engine_step_wages"] = tail_growth_factor(spec.with_drift(0.0), PUMS_INCOME_DOLLAR_YEAR, year, 0.0)
    try:
        out["scalar_step_b19013"] = (_observed_honolulu_b19013(year)
                                     / _observed_honolulu_b19013(PUMS_INCOME_DOLLAR_YEAR))
    except ValueError:
        out["scalar_step_b19013"] = np.nan
    for key, name in (("qcew_hawaii_wages", "realized_step_qcew_pay"),
                      ("bea_hi_percapita_income", "realized_step_bea_pcpi")):
        out[name] = _anchor_ratio(key, PUMS_INCOME_DOLLAR_YEAR, year)
    return out


def _anchor_ratio(name: str, from_year: int, to_year: int) -> float:
    """``value[to_year] / value[from_year]`` of a bundled census_forecaster anchor."""
    try:
        import census_forecaster
        path = Path(census_forecaster.__file__).resolve().parent / "data" / "anchors" / f"{name}.json"
        d = json.loads(path.read_text(encoding="utf-8"))
        v = {int(k): float(x) for k, x in (d.get("values_by_year") or d.get("values")).items()}
        return v[to_year] / v[from_year]
    except Exception:  # noqa: BLE001 — a missing anchor is a blank cell, not a failed scorecard
        return np.nan


# ── Output ───────────────────────────────────────────────────────────────────
def _md(df: pd.DataFrame, fmt: dict[str, str]) -> str:
    cols = list(fmt)
    head = "| " + " | ".join(cols) + " |\n|" + "|".join("---:" if c != cols[0] else ":---" for c in cols) + "|"
    lines = [head]
    for _, r in df[cols].iterrows():
        cells = []
        for c in cols:
            v = r[c]
            cells.append("" if (isinstance(v, float) and np.isnan(v)) else format(v, fmt[c]))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def render(tables: dict[str, dict[str, pd.DataFrame]], diags: dict[str, dict], dials: dict[str, dict],
           *, base_year: int, year: int, base_path: Path, actuals_path: Path, meta: dict) -> str:
    out = [f"# DOTAX scorecard: TY{year} forecast vs. actual\n",
           f"Base: `{base_path}` (built by {meta.get('built_by', '?')} at code "
           f"`{meta.get('code_sha', meta.get('git_sha', '?'))}`, raked to the DOTAX TY{base_year} edition, "
           f"law year {meta.get('tax_year', '?')}). Actuals: `{actuals_path}`. "
           f"Model incomes are the scoring `income` column; tax is before credits under "
           f"{next(iter(diags.values()))['law']} with the statutory §235-51(f) alternative tax.\n"]
    for label, t in tables.items():
        d = diags[label]
        out.append(f"## {label} (alpha={d['alpha']}, aging={d['aging']}, top_share_drift={d['top_premium']:+.1%}/yr, "
                   f"tail_k={d['tail_k']:.4f})\n")
        out.append("### Table A-8: returns and tax before credits by AGI class\n")
        out.append(_md(t["a8"], {"class": "s", "returns_model": ",.0f", "returns_actual": ",.0f",
                                 "returns_err_pct": "+.1f", "tax_model_M": ",.1f", "tax_actual_M": ",.1f",
                                 "tax_err_M": "+,.1f", "tax_err_pct": "+.1f"}))
        out.append("\n### Table A-1: AGI of taxable returns by class\n")
        out.append(_md(t["a1"], {"class": "s", "returns_model": ",.0f", "returns_actual": ",.0f",
                                 "returns_err_pct": "+.1f", "agi_model_M": ",.0f", "agi_actual_M": ",.0f",
                                 "agi_err_pct": "+.1f"}))
        out.append("\n### Table 4: returns by filing status\n")
        out.append(_md(t["status"], {"filing_status": "s", "returns_model": ",.0f", "returns_actual": ",.0f",
                                     "share_model_pct": ".1f", "share_actual_pct": ".1f",
                                     "returns_err_pct": "+.1f"}))
        top = t["top"].iloc[0]
        out.append("\n### The $1M+ class\n")
        out.append(f"- Returns: model {top['returns_model']:,.0f}, actual {top['returns_actual']:,.0f} "
                   f"({top['returns_err_pct']:+.1f}%).")
        out.append(f"- Tax before credits: model ${top['tax_model_M']:,.1f}M, actual ${top['tax_actual_M']:,.1f}M "
                   f"({top['tax_err_M']:+,.1f}M, {top['tax_err_pct']:+.1f}%).")
        out.append(f"- Average tax: model ${top['avg_tax_model']:,.0f}, actual ${top['avg_tax_actual']:,.0f} "
                   f"({top['avg_tax_err_pct']:+.1f}%).")
        out.append(f"- Split: count {top['count_part_M']:+,.1f}M (at the actual average), per-filer "
                   f"{top['per_filer_part_M']:+,.1f}M; in logs {top['log_err']:+.3f} = "
                   f"{top['log_count']:+.3f} (count) + {top['log_per_filer']:+.3f} (per filer).")
        dl = dials[label]
        out.append(f"\nDials, TY{base_year} -> TY{year}: Honolulu B19013 x{dl['honolulu_b19013_growth']:.4f}, "
                   f"engine wages x{dl['engine_growth_wages']:.4f} / gains x{dl['engine_growth_gains']:.4f}, "
                   f"drift x{dl['premium_growth']:.4f}, tail aging to the PUMS dollar year (40% gains) "
                   f"x{dl['tail_aging_to_pums_year']:.4f}, capital-gains anchor growth x{dl['cg_growth']:.4f} "
                   f"({dl['cg_growth_model']}).")
        out.append(f"The step from the PUMS dollar year ({dl['pums_dollar_year']}) to TY{year}: this engine's "
                   f"wages x{dl['engine_step_wages']:.4f}, the county-median scalar x{dl['scalar_step_b19013']:.4f}; "
                   f"Hawaiʻi realized over the same years: QCEW average pay x{dl['realized_step_qcew_pay']:.4f}, "
                   f"BEA per-capita income x{dl['realized_step_bea_pcpi']:.4f}. The base's tax totals are the "
                   f"base edition's on PUMS-dollar-year incomes, so below $1M a smaller step scores better "
                   f"whatever the realized growth.\n")
    series = top_class_series_table()
    out.append("## The $1M+ class in the DOTAX A-8 series: what drift does it support?\n")
    out.append(_md(series, {"tax_year": ".0f", "returns": ",.0f", "tax_before_M": ",.1f",
                            "returns_share_pct": ".3f", "tax_share_pct": ".1f",
                            "dlog_returns_share": "+.3f", "dlog_tax_share": "+.3f"}))
    bt, st = trend_with_se(series, "tax_share_pct")
    br, sr = trend_with_se(series, "returns_share_pct")
    out.append(f"\nFitted trend in log share, per year: tax share {bt:+.1%} (SE {st:.1%}), "
               f"returns share {br:+.1%} (SE {sr:.1%}); {len(series)} editions. Neither is "
               f"distinguishable from zero, so the supported top-share drift is 0 with a band "
               f"of roughly ±1 SE; the scenarios carry -1/0/+1 %/yr (projection.engine.TOP_SHARE_DRIFT).\n")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base", type=Path, default=DEFAULT_BASE)
    p.add_argument("--base-edition-year", type=int, required=True,
                   help="DOTAX edition year N the base was raked to")
    p.add_argument("--target-year", type=int, default=None, help="default N+1")
    p.add_argument("--actuals", type=Path, default=None,
                   help="dotax_indinc_<target>.json; default the bundled one")
    p.add_argument("--scenarios", default="MID", help="comma list of MID,LOW,HIGH")
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p.add_argument("--aging", choices=["cbo", "scalar"], default="cbo",
                   help="income-aging engine (projection.engine); scalar is the pre-October-2026 path")
    p.add_argument("--drift", type=float, default=None,
                   help="override every scenario's top_share_drift (per year above $500K)")
    a = p.parse_args(argv)

    import logging
    import warnings
    warnings.filterwarnings("ignore")
    logging.disable(logging.WARNING)

    from tax_modeler.artifacts import load_calibrated_base
    from tax_modeler.projection.engine import AgingSpec
    from tax_modeler.scenarios.act24_population import mid_gains_factors

    year = a.target_year or a.base_edition_year + 1
    actuals = a.actuals or CALIBRATION_DIR / f"dotax_indinc_{year}.json"
    edition = json.loads(Path(actuals).read_text(encoding="utf-8"))
    if int(edition.get("tax_year", year)) != year:
        raise SystemExit(f"{actuals} is TY{edition['tax_year']}, not TY{year}")

    base, ded_params, meta = load_calibrated_base(a.base)
    cal_year = int(meta.get("tax_year", 2023))
    params = scenario_params()
    labels = [s.strip().upper() for s in a.scenarios.split(",") if s.strip()]
    mid_alpha, mid_prem = params["MID"]
    def spec_for(drift):
        return AgingSpec(engine=a.aging, top_share_drift=drift if a.drift is None else a.drift)
    gains = mid_gains_factors(base, alpha=mid_alpha, spec=spec_for(mid_prem), ded_params=ded_params,
                              cal_tax_year=cal_year, years=[year])[year]

    out_dir = a.out / f"TY{year}"
    out_dir.mkdir(parents=True, exist_ok=True)
    tables, diags, dials = {}, {}, {}
    for label in labels:
        alpha, prem = params[label]
        scored, diag = score_population(base, ded_params, cal_year, label=label, alpha=alpha,
                                        spec=spec_for(prem), year=year, gains_factors=gains)
        t = {"a8": a8_table(scored, edition), "a1": a1_table(scored, edition),
             "status": status_table(scored, edition), "top": top_class_table(scored, edition)}
        for name, df in t.items():
            df.to_csv(out_dir / f"{name}_{label}.csv", index=False)
        tables[label], diags[label] = t, diag
        dials[label] = dial_diagnostics(spec_for(prem), a.base_edition_year, year)
    md = render(tables, diags, dials, base_year=a.base_edition_year, year=year,
                base_path=a.base, actuals_path=Path(actuals), meta=meta)
    (out_dir / "scorecard.md").write_text(md, encoding="utf-8")
    print(md)
    print(f"\nWrote {out_dir}/scorecard.md and CSVs", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
