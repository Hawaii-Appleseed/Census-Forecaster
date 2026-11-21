"""How much the Act 163 sunset figures depend on WHO claims each credit, not just how many.

``chain/forecast_working_family_credits.py`` gives every eligible unit the same take-up factor per credit, set so
modeled dollars match the Department of Taxation's. The Department's total fixes the level; nothing measured says
the factor is the same for every household. This script reruns that step's population and credits with take-up
that differs between units (:mod:`tax_modeler.calibration.takeup_propensity`), each variant still meeting the same
Department dollars, and sets the results side by side:

  uniform            the published method, recomputed here; checked against runs/working_family_credits/
  eitc_dotax_agi     EITC take-up by federal AGI range fitted to the claims in the Department's EITC report
                     (Table 1, new credit); the other two credits uniform. Administrative shape, no survey.
  food_low_x0.25 ..  WHAT-IF: food/excise credit, tax units under $15,000 of federal AGI have a quarter, half,
  food_low_x4        twice and four times the odds of claiming (--food-odds sets other multiples). Not
                     estimates: they bracket what a survey question on who claims the credit could find, to
                     show whether the answer would move the figures.
  --model CREDIT=PATH  a fitted model (PropensityModel.to_json), e.g. from a survey, as one more variant.

It reads the chain step's functions and writes nothing the chain or the site reads. Outputs
(reports/takeup_propensity/): summary.csv (one row per variant), revenue_by_year.csv, distribution_ty2028.csv,
eitc_by_agi_ty2024.csv, models.json.

    .venv/bin/python analyses/takeup_propensity_sensitivity.py
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "chain"))
OUT_DIR = REPO / "reports" / "takeup_propensity"
CREDITS = ("eitc", "food", "cdcc")
LOW_AGI = 15_000


class Takeup:
    """One credit's take-up in one variant: the model, and the level found in the calibration year."""

    def __init__(self, model, frame: pd.DataFrame, weighted_amount: np.ndarray, target: float):
        from tax_modeler.calibration.takeup_propensity import claim_probabilities

        self.model = model
        self.fit = claim_probabilities(model, frame, weighted_amount=weighted_amount, target=target)

    def prob(self, frame: pd.DataFrame, eligible: np.ndarray) -> np.ndarray:
        """Claim probability of each unit of ``frame`` (another year's population), 0 where not eligible."""
        from tax_modeler.calibration.takeup_propensity import probabilities_at

        if self.fit.scale > 1.0 or not self.model.terms:
            return np.where(eligible, min(self.fit.uniform_factor, 1.0), 0.0)
        return probabilities_at(self.model, frame, shift=self.fit.shift, eligible=eligible)

    @property
    def scale(self) -> float:
        return self.fit.scale


def _agi_terms():
    from forecast_working_family_credits import EITC_RANGES
    from tax_modeler.calibration.takeup_propensity import Term

    # The first range is the reference class.
    return tuple(Term(label, "federal_agi", lo=None if np.isinf(lo) else lo, hi=None if np.isinf(hi) else hi)
                 for label, lo, hi in EITC_RANGES[1:])


def eitc_model_from_dotax(u: pd.DataFrame, c: pd.DataFrame):
    """EITC take-up by federal AGI range, fitted to the claims in the Department's report for the same year."""
    import forecast_working_family_credits as wfc
    from tax_modeler.calibration import dotax_base
    from tax_modeler.calibration.takeup_propensity import fit_from_margins

    report = dotax_base.load_eitc_report(wfc.EITC_CAL_YEAR)["table_1_by_federal_agi"]["ranges"]
    agi = u["federal_agi"].to_numpy(dtype=float)
    eligible_w = u["weight"].to_numpy(dtype=float) * (c["eitc_expanded"].to_numpy(dtype=float) > 0)
    margins = [((agi >= lo) & (agi < hi), float(r["new"]["claims"]))
               for (_, lo, hi), r in zip(wfc.EITC_RANGES, report, strict=True)]
    model, table = fit_from_margins(
        u, terms=_agi_terms(), eligible_weight=eligible_w, margins=margins,
        source=f"DOTAX Earned Income Tax Credit Report TY{wfc.EITC_CAL_YEAR}, Table 1 new-credit claims by federal AGI")
    table.insert(0, "federal_agi_range", [label for label, _, _ in wfc.EITC_RANGES])
    return model, table


def food_what_if(log_odds: float, label: str):
    from tax_modeler.calibration.takeup_propensity import PropensityModel, Term

    term = Term("federal_agi_under_15000", "federal_agi", lo=None, hi=LOW_AGI)
    return PropensityModel(terms=(term,), coefficients={term.name: log_odds},
                           source=f"WHAT-IF, not an estimate: {label}")


def claim_draws(u: pd.DataFrame, c: pd.DataFrame, prob: dict[str, np.ndarray], scale: dict[str, float]):
    """``forecast_working_family_credits.claim_draws`` with a claim probability per unit instead of one per credit."""
    import forecast_working_family_credits as wfc
    from tax_modeler.poverty.draws import DEFAULT_SEED, ClaimBlock, DrawSpec

    sid = u["spm_unit_id"].to_numpy()
    blocks = []
    for k in ("eitc", "food"):
        ren, exp_ = c[f"{k}_expanded"].to_numpy(dtype=float), c[f"{k}_reverted"].to_numpy(dtype=float)
        blocks.append(ClaimBlock(sid, np.where(ren > 0, prob[k], 0.0), ren * scale[k],
                                 {"act163_sunset": exp_ * scale[k]}))
    q = np.clip(c["cdcc_expanded_claim_prob"].to_numpy(dtype=float), 0.0, 1.0)
    ren, exp_ = c["cdcc_expanded"].to_numpy(dtype=float), c["cdcc_reverted"].to_numpy(dtype=float)
    cond_ren = np.divide(ren, q, out=np.zeros(len(q)), where=q > 0) * scale["cdcc"]
    cond_exp = np.divide(exp_, q, out=np.zeros(len(q)), where=q > 0) * scale["cdcc"]
    # every unit with a care credit has q > 0, so its probability is the unit's own
    blocks.append(ClaimBlock(sid, q * np.where(q > 0, np.maximum(prob["cdcc"], 0.0), 0.0), cond_ren,
                             {"act163_sunset": cond_exp}))

    def without_credits(df: pd.DataFrame) -> np.ndarray:
        return (df["spm_resources"] - df["hi_eitc_amount"] - df["hi_food_excise_amount"]
                - df["hi_cdcc_amount"]).to_numpy(dtype=float)

    return DrawSpec(blocks=tuple(blocks), baseline_fixed=without_credits,
                    scenario_fixed={"act163_sunset": without_credits}, n_draws=wfc.DRAW_COUNT, seed=DEFAULT_SEED)


def score(name: str, takeup: dict[str, Takeup], frames, persons, *, with_poverty: bool = True) -> dict:
    """One variant's figures: revenue by year, the TY2028 distribution and poverty, the EITC check by AGI range."""
    import forecast_working_family_credits as wfc

    rev = []
    for yr, (u, c) in frames.items():
        w = u["weight"].to_numpy(dtype=float)
        row = {"variant": name, "tax_year": yr}
        for k in CREDITS:
            exp_, rev_ = c[f"{k}_expanded"].to_numpy(dtype=float), c[f"{k}_reverted"].to_numpy(dtype=float)
            f = takeup[k].prob(u, exp_ > 0) * takeup[k].scale
            row[f"{k}_expanded_$M"] = float((exp_ * w * f).sum() / 1e6)
            row[f"{k}_reverted_$M"] = float((rev_ * w * f).sum() / 1e6)
            row[f"{k}_loss_$M"] = row[f"{k}_expanded_$M"] - row[f"{k}_reverted_$M"]
        row["total_loss_$M"] = sum(row[f"{k}_loss_$M"] for k in CREDITS)
        rev.append(row)
    rev = pd.DataFrame(rev)

    # Federal AGI ranges of the EITC report, calibration year.
    uE, cE = frames[wfc.EITC_CAL_YEAR]
    wE, agi = uE["weight"].to_numpy(dtype=float), uE["federal_agi"].to_numpy(dtype=float)
    pE = takeup["eitc"].prob(uE, cE["eitc_expanded"].to_numpy(dtype=float) > 0)
    by_agi = pd.DataFrame([{"variant": name, "federal_agi_range": label,
                            "model_claims": float((wE * pE)[(agi >= lo) & (agi < hi)].sum())}
                           for label, lo, hi in wfc.EITC_RANGES])

    # Distribution and poverty, as the chain step sets them up, with per-unit factors.
    u, c = frames[wfc.POVERTY_YEAR]
    u = u.copy()
    prob = {k: takeup[k].prob(u, c[f"{k}_expanded"].to_numpy(dtype=float) > 0) for k in CREDITS}
    scale = {k: takeup[k].scale for k in CREDITS}
    loss = {k: (c[f"{k}_expanded"] - c[f"{k}_reverted"]).to_numpy(dtype=float) * prob[k] * scale[k] for k in CREDITS}
    u["hi_eitc_amount"] = c["eitc_expanded"].to_numpy(dtype=float) * prob["eitc"] * scale["eitc"]
    u["hi_food_excise_amount"] = c["food_expanded"].to_numpy(dtype=float) * prob["food"] * scale["food"]
    u["hi_cdcc_amount"] = c["cdcc_expanded"].to_numpy(dtype=float) * prob["cdcc"] * scale["cdcc"]
    u["act163_sunset_loss"] = loss["eitc"] + loss["food"] + loss["cdcc"]
    q_eitc = np.where(c["eitc_expanded"] > 0, prob["eitc"], 0.0)
    q_food = np.where(c["food_expanded"] > c["food_reverted"], prob["food"], 0.0)
    q_cdcc = c["cdcc_loss_prob"].to_numpy(dtype=float) * prob["cdcc"]
    u["_claim_prob"] = 1 - (1 - q_eitc) * (1 - q_food) * (1 - q_cdcc)
    u["_loss_eitc"], u["_loss_food"], u["_loss_cdcc"] = loss["eitc"], loss["food"], loss["cdcc"]
    u["_q_eitc"], u["_q_food"], u["_q_cdcc"] = q_eitc, q_food, q_cdcc
    dist = wfc.distribution(u)
    dist.insert(0, "variant", name)
    all_three = wfc.by_credit(u).set_index("credit").loc["All three"]

    r28 = rev.set_index("tax_year").loc[wfc.POVERTY_YEAR]
    summary = {
        "variant": name,
        **{f"takeup_{k}": "uniform" if not takeup[k].model.terms else takeup[k].model.source for k in CREDITS},
        "ty2028_total_loss_$M": r28["total_loss_$M"], "ty2028_eitc_loss_$M": r28["eitc_loss_$M"],
        "ty2028_food_loss_$M": r28["food_loss_$M"], "ty2028_cdcc_loss_$M": r28["cdcc_loss_$M"],
        "ty2028_2031_total_loss_$M": float(rev.loc[rev["tax_year"].isin(wfc.SCORE_YEARS), "total_loss_$M"].sum()),
        "households_losing": float(all_three["pct_households_losing"] * all_three["households"] / 100),
        "lowest_fifth_loss_$M": float(dist.loc[dist["group"] == "Lowest 20%", "total_loss_$M"].iloc[0]),
        "lowest_fifth_pct_losing": float(dist.loc[dist["group"] == "Lowest 20%", "pct_households_losing"].iloc[0]),
    }
    if with_poverty:
        pov = wfc.poverty(u, persons, draws=claim_draws(u, c, prob, scale)).iloc[0]
        for col in ("poverty_rate_renewed", "poverty_rate_expired", "persons_into_poverty", "persons_into_poverty_se",
                    "persons_into_poverty_mc_se", "children_into_poverty", "children_into_poverty_se",
                    "poverty_gap_increase_$M"):
            summary[col] = float(pov[col])
    return {"summary": summary, "revenue": rev, "distribution": dist, "eitc_by_agi": by_agi}


def check_uniform_against_chain(result: dict) -> list[str]:
    """The uniform variant is the chain step's method; say how far it is from that step's saved run."""
    import forecast_working_family_credits as wfc

    lines = []
    run = wfc.OUT_DIR
    if not (run / "revenue_by_year.csv").exists():
        return [f"no {run.relative_to(REPO)} to check the uniform variant against"]
    ref = pd.read_csv(run / "revenue_by_year.csv").set_index("tax_year")
    mine = result["revenue"].set_index("tax_year")
    worst = max(float(np.abs(mine[col] / ref[col] - 1).max()) for col in ref.columns if col in mine.columns)
    lines.append(f"uniform vs chain revenue_by_year.csv: largest relative difference {worst:.1e}")
    pov = run / f"poverty_ty{wfc.POVERTY_YEAR}.csv"
    if pov.exists() and "persons_into_poverty" in result["summary"]:
        ref_p = float(pd.read_csv(pov)["persons_into_poverty"].iloc[0])
        diff = result["summary"]["persons_into_poverty"] - ref_p
        lines.append(f"uniform vs chain persons_into_poverty: {result['summary']['persons_into_poverty']:,.1f} "
                     f"vs {ref_p:,.1f} (difference {diff:+.2f})")
        worst = max(worst, abs(diff) / max(abs(ref_p), 1.0))
    if worst > 1e-6:
        raise SystemExit("the uniform variant does not reproduce the chain step: " + "; ".join(lines)
                         + " (rerun chain/forecast_working_family_credits.py on this checkout first)")
    return lines


def run(extra_models: dict[str, dict[str, Path]] | None = None, with_poverty: bool = True,
        food_odds: tuple[float, ...] = (0.25, 0.5, 2.0, 4.0)) -> pd.DataFrame:
    import forecast_working_family_credits as wfc
    from tax_modeler.calibration import dotax_base
    from tax_modeler.calibration.takeup_propensity import PropensityModel

    t0 = time.perf_counter()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # The population exactly as the chain step builds it, years in the same order.
    base, persons = wfc.load_population()
    ratios = wfc.unit_replicate_ratios(base)
    rake = wfc.irs_rake(base, ratios=ratios) if wfc.IRS_REWEIGHT else None
    frames = {}
    for yr in wfc.YEARS:
        u = wfc.units_for_year(base, yr, rake)
        frames[yr] = (u, wfc.state_credits(u))
    print(f"population and credits built ({time.perf_counter() - t0:.0f}s)", flush=True)

    uE, cE = frames[wfc.EITC_CAL_YEAR]
    uF, cF = frames[wfc.CAL_YEAR]
    targets = {"eitc": (uE, cE, dotax_base.eitc_new_credit(wfc.EITC_CAL_YEAR)["dollars_M"] * 1e6),
               "food": (uF, cF, wfc.dotax()[wfc.CAL_YEAR]["food_$M"] * 1e6),
               "cdcc": (uF, cF, wfc.dotax()[wfc.CAL_YEAR]["cdcc_$M"] * 1e6)}

    def takeup(models: dict) -> dict[str, Takeup]:
        out = {}
        for k, (u, c, target) in targets.items():
            amount = u["weight"].to_numpy(dtype=float) * c[f"{k}_expanded"].to_numpy(dtype=float)
            out[k] = Takeup(models.get(k, PropensityModel()), u, amount, target)
        return out

    eitc_model, eitc_fit = eitc_model_from_dotax(uE, cE)
    variants: dict[str, dict] = {
        "uniform": {},
        "eitc_dotax_agi": {"eitc": eitc_model},
        **{f"food_low_x{odds:g}": {"food": food_what_if(
            np.log(odds), f"under $15,000 of federal AGI: {odds:g} times the odds of claiming")} for odds in food_odds},
    }
    for name, paths in (extra_models or {}).items():
        variants[name] = {k: PropensityModel.from_json(p) for k, p in paths.items()}

    results = {}
    for name, models in variants.items():
        results[name] = score(name, takeup(models), frames, persons, with_poverty=with_poverty)
        print(f"  {name} scored ({time.perf_counter() - t0:.0f}s)", flush=True)
    notes = check_uniform_against_chain(results["uniform"])

    summary = pd.DataFrame([r["summary"] for r in results.values()])
    summary.to_csv(OUT_DIR / "summary.csv", index=False)
    pd.concat([r["revenue"] for r in results.values()]).to_csv(OUT_DIR / "revenue_by_year.csv", index=False)
    pd.concat([r["distribution"] for r in results.values()]).to_csv(
        OUT_DIR / f"distribution_ty{wfc.POVERTY_YEAR}.csv", index=False)
    by_agi = pd.concat([r["eitc_by_agi"] for r in results.values()]).pivot(
        index="federal_agi_range", columns="variant", values="model_claims")
    by_agi = eitc_fit.set_index("federal_agi_range")[["target", "eligible"]].rename(
        columns={"target": "dotax_claims", "eligible": "model_eligible"}).join(by_agi)
    by_agi.to_csv(OUT_DIR / f"eitc_by_agi_ty{wfc.EITC_CAL_YEAR}.csv")
    (OUT_DIR / "models.json").write_text(json.dumps({
        name: {k: {"source": m.source, "coefficients": dict(m.coefficients)} for k, m in models.items()}
        for name, models in variants.items()}, indent=2) + "\n")

    pd.set_option("display.width", 250)
    print("\n" + "\n".join(notes))
    print(f"\nEITC claims by federal AGI range, TY{wfc.EITC_CAL_YEAR}:")
    print(by_agi[["dotax_claims", "model_eligible", "uniform", "eitc_dotax_agi"]].to_string(
        float_format=lambda v: f"{v:,.0f}"))
    cols = [c for c in summary.columns if not c.startswith("takeup_")]
    print("\nAct 163 sunset by take-up variant:")
    print(summary[cols].set_index("variant").T.to_string(float_format=lambda v: f"{v:,.3f}"))
    print(f"\nDone in {time.perf_counter() - t0:.0f}s -> {OUT_DIR.relative_to(REPO)}")
    return summary


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--model", action="append", default=[], metavar="NAME:CREDIT=PATH",
                    help="one more variant from a PropensityModel JSON, e.g. survey:food=models/food.json "
                         "(repeat with the same NAME to set more than one credit)")
    ap.add_argument("--food-odds", type=float, nargs="*", default=[0.25, 0.5, 2.0, 4.0],
                    help="odds multiples for the food/excise what-if variants (default: 0.25 0.5 2 4)")
    ap.add_argument("--no-poverty", action="store_true", help="skip the poverty draws (revenue and distribution only)")
    args = ap.parse_args(argv)
    extra: dict[str, dict[str, Path]] = {}
    for spec in args.model:
        name, _, rest = spec.partition(":")
        credit, _, path = rest.partition("=")
        if credit not in CREDITS or not path:
            ap.error(f"--model {spec!r}: expected NAME:CREDIT=PATH with CREDIT one of {CREDITS}")
        extra.setdefault(name, {})[credit] = Path(path)
    if any(o <= 0 for o in args.food_odds):
        ap.error("--food-odds must be positive")
    run(extra, with_poverty=not args.no_poverty, food_odds=tuple(args.food_odds))


if __name__ == "__main__":
    import warnings

    warnings.filterwarnings("ignore")
    logging.disable(logging.WARNING)
    main()
