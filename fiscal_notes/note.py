"""Render the one-page draft fiscal note: a pure function of (translation JSON, score result).

No model calls, no file access. Figures are $ millions formatted like ``forecast_custom.py``'s
printout (``+12.3``, thousands separators). The note states what the data supports and takes no
position on the bill.
"""
from __future__ import annotations

DRAFT = "DRAFT: machine-translated, not reviewed"

VERDICT_WORDS = {
    "full": "every fiscal provision is scored",
    "partial": "some provisions are not scored, listed below",
    "out_of_scope": "nothing in the bill can be scored",
}
PARTIAL_APPROX = "some provisions are scored only approximately, listed below"
PARTIAL_BOTH = "some provisions are not scored and some are scored only approximately, listed below"

COLS = (("static_mid", "MID static"), ("low", "LOW"), ("mid", "MID"), ("high", "HIGH"))


# --------------------------------------------------------------------------- formatting


def signed(v: float | None) -> str:
    """``+12.3`` / ``-1,234.5`` ($M); a figure that rounds to zero is ``+0.0``."""
    if v is None:
        return "n/a"
    return f"{round(v, 1) + 0.0:+,.1f}"


def millions(v: float) -> str:
    return f"${abs(v):,.1f} million"


def raises_or_costs(v: float) -> str:
    r = round(v, 1)
    if r > 0:
        return f"raises {millions(v)}"
    if r < 0:
        return f"costs {millions(v)}"
    return "does not change revenue (less than $0.05 million either way)"


def gain_or_cost(v: float) -> str:
    r = round(v, 1)
    if r > 0:
        return f"a gain of {millions(v)}"
    if r < 0:
        return f"a cost of {millions(v)}"
    return "no change"


def _collapse(s: str) -> str:
    return " ".join(s.split())


def table(header: list[str], rows: list[list[str]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    out.extend("| " + " | ".join(r) + " |" for r in rows)
    return out


# --------------------------------------------------------------------------- sections


def _revenue_tables(score: dict) -> list[str]:
    years = [str(y) for y in score["years"]]
    credits = score["credits"]

    def block(data: dict, five: dict) -> list[str]:
        rows = [[y, *(signed(data[k][y]) for k, _ in COLS)] for y in years]
        rows.append(["5-year", *(signed(five[k]) for k, _ in COLS)])
        return table(["Tax year", *(label for _, label in COLS)], rows)

    if not credits:
        return block(score["income_tax"], score["five_year"]["income_tax"])
    cred_rows = [[y, *(signed(credits[c][y]) for c in ("eitc", "food_excise", "total"))] for y in years]
    cred_rows.append(["5-year", *(signed(score["five_year"]["credits"][c])
                                  for c in ("eitc", "food_excise", "total"))])
    return [
        "Income tax:", "", *block(score["income_tax"], score["five_year"]["income_tax"]), "",
        "Credits (EITC and food/excise credit), the same in every scenario:", "",
        *table(["Tax year", "EITC", "Food/excise", "Total"], cred_rows), "",
        "Total (income tax plus credits):", "", *block(score["total"], score["five_year"]["total"]),
    ]


def _distribution(score: dict) -> list[str]:
    d = score["distribution"]

    def pct(r: dict) -> list[str]:
        return [f"{r['pct_pay_more']:.1f}", f"{r['pct_pay_less']:.1f}", f"{r['pct_no_change']:.1f}"]

    q_rows = [[r["group"], f"{r['household_count']:,.0f}", f"{r['avg_per_hh_baseline_tax']:,.0f}",
               f"{r['avg_per_hh_reform_tax']:,.0f}", f"{r['avg_per_hh_bracket_change']:+,.0f}",
               signed(r["total_bracket_$M"]), *pct(r)] for r in d["quintile"]]
    c_rows = [[r["group"], f"{r['filer_count']:,.0f}", f"{r['avg_baseline_tax']:,.0f}",
               f"{r['avg_reform_tax']:,.0f}", f"{r['avg_bracket_change']:+,.0f}",
               signed(r["total_bracket_$M"]), *pct(r)] for r in d["income_class"]]
    tail = ["Pay more %", "Pay less %", "No change %"]
    return [
        f"Tax year {d['year']} (the first year the spec applies), MID scenario. Static: no behavioral "
        "response. Income tax only: credit changes are not in these tables. Dollar figures are per "
        "household (or per filer) per year; a positive change means the group pays more.",
        "", "By income quintile:", "",
        *table(["Quintile", "Households", "Avg tax, current law ($)", "Avg tax, bill ($)",
                "Avg change ($)", "Total change ($M)", *tail], q_rows),
        "", "By income class:", "",
        *table(["Income class", "Filers", "Avg tax, current law ($)", "Avg tax, bill ($)",
                "Avg change ($)", "Total change ($M)", *tail], c_rows),
    ]


def _scored(p: dict) -> bool:
    return p["modeled"] and not p["blank"]


def _approximate(p: dict) -> bool:
    return _scored(p) and bool(p.get("approximate"))


def _provision(p: dict) -> list[str]:
    bits = []
    if p["spec_keys"]:
        bits.append("spec: " + ", ".join(f"`{k}`" for k in p["spec_keys"]))
    if p.get("applies_from_tax_year"):
        bits.append(f"applies from tax year {p['applies_from_tax_year']}")
    tail = f" ({'; '.join(bits)})" if bits else ""
    return [f"- **Section {p['section']}**: {_collapse(p['summary'])}{tail}",
            f"  > \"{_collapse(p['quote'])}\""]


def _provenance(translation: dict, score: dict, prov: dict) -> list[str]:
    sha = prov.get("model_git_sha") or "unknown"
    tree = {True: " (tracked files differ from that commit)", False: ""}.get(prov.get("model_tree_dirty"), "")
    lines = [
        f"- Baseline: current law, Act 24 (SLH 2026). Tax years {score['years'][0]}-{score['years'][-1]}. "
        "Change in revenue, $ millions; positive means more revenue.",
        "- Income tax is scored on the simulator population (LOW, MID and HIGH scenarios), static and "
        "after behavioral response (`TAX_SIMULATOR_SCOPE.md`).",
    ]
    if score["credits"]:
        src = prov.get("credits_source") or {}
        lines.append("- Credits are priced from the working-family credits run, not from the simulator "
                     "population, and are the same in every scenario"
                     + (f" (run commit `{src['git_sha']}`)." if src.get("git_sha") else "."))
    lines += [
        "- Rate cuts get no behavioral response: the model's response to a change in the top rate is "
        "asymmetric by design, so it applies to rises and not to cuts (`TAX_SIMULATOR_SCOPE.md`, "
        "decision 3). When a bill cuts rates, the MID figure can fall outside the LOW-HIGH range.",
        "- The top tail is thin: 49 sample records (MID, tax year 2027) stand for the roughly 2,300 "
        "returns above $1 million (`TAX_SIMULATOR_SCOPE.md`). Read the LOW-HIGH range, not the single "
        "MID figure, for changes that touch only the top.",
        "- The list of provisions, their quotes and the spec come from a language model; the checks "
        "confirm that quotes and figures appear in the bill text, not that the list is complete.",
        f"- Model code: commit `{sha}`{tree}.",
    ]
    if prov.get("population_git_sha"):
        built = f", built {prov['population_created_at'][:10]}" if prov.get("population_created_at") else ""
        lines.append(f"- Simulator population: built from commit `{prov['population_git_sha']}`{built}.")
    lines += [
        f"- Bill text sha256: `{translation.get('bill_sha256', 'unknown')}`.",
        f"- Translator model: `{translation.get('model', 'unknown')}` "
        f"(attempts: {translation.get('attempts', 'unknown')}).",
    ]
    if prov.get("spec_sha256"):
        lines.append(f"- Spec file sha256: `{prov['spec_sha256']}`.")
    return lines


# --------------------------------------------------------------------------- notes


def render_note(translation: dict, score: dict, provenance: dict | None = None) -> str:
    """The full one-page note for a ``full`` or ``partial`` translation."""
    verdict = translation["verdict"]
    if verdict not in ("full", "partial"):
        raise ValueError(f"cannot render a scored note for verdict {verdict!r}")
    prov = provenance if provenance is not None else score.get("provenance", {})
    label = translation["label"]
    years = score["years"]
    span = f"{years[0]}-{years[-1]}"
    five = score["five_year"]
    mid = five["total"]["mid"]
    scen = [five["total"][k] for k in ("low", "mid", "high") if five["total"][k] is not None]

    lo, hi = gain_or_cost(min(scen)), gain_or_cost(max(scen))
    spread = (f"is {lo} in each scenario" if lo == hi else f"runs from {lo} to {hi}")
    bottom = (f"Against current law (Act 24), the bill as scored {raises_or_costs(mid)} over tax years "
              f"{span} in the MID scenario, after behavioral response. Across the LOW to HIGH scenarios "
              f"the five-year change {spread}.")
    if score["credits"]:
        bottom += (f" Of the MID figure, income tax is {signed(five['income_tax']['mid'])} and credits "
                   f"are {signed(five['credits']['total'])} ($ millions).")
    scored = [p for p in translation["provisions"] if _scored(p) and not _approximate(p)]
    approx = [p for p in translation["provisions"] if _approximate(p)]
    unscored = [p for p in translation["provisions"] if not _scored(p)]
    if approx:
        bottom += (" Part of the estimate is approximate: the provisions under \"Scored approximately\" "
                   "are in it, but the spec cannot express them exactly.")
    if unscored:
        bottom += " The estimate leaves out the provisions listed under \"What is NOT scored\"."
    words = VERDICT_WORDS[verdict]
    if verdict == "partial" and approx:
        words = PARTIAL_BOTH if unscored else PARTIAL_APPROX

    out = [f"# {label}: shadow fiscal note", "",
           f"**{DRAFT}.** Verdict `{verdict}`: {words}.", "",
           "## Bottom line", "", bottom, "",
           "## Revenue by tax year ($ millions, change vs current law)", "", *_revenue_tables(score), "",
           "## Who pays and who gains", "", *_distribution(score), "",
           "## What is scored", ""]
    for p in scored:
        out.extend(_provision(p))
    if not scored:
        out.append("None.")
    if approx:
        out += ["", "## Scored approximately", ""]
        for p in approx:
            out.extend(_provision(p))
            out.append(f"  Approximation: {_collapse(p['approximation'])}")
        out += ["", "These provisions are in the estimate, but the spec only approximates them; the "
                "direction of the error is stated for each."]
    out += ["", "## What is NOT scored", ""]
    if unscored:
        for p in unscored:
            why = "the amount is left blank in the bill" if p["modeled"] else "not modeled"
            out.append(f"- **Section {p['section']}**: {_collapse(p['summary'])} ({why})")
        out += ["", "The estimate leaves these provisions out. It does not count their effect on "
                "revenue, in either direction."]
    else:
        out.append("None: every provision the translator found is scored.")
    out += ["", "## Reviewer notes", "", _collapse(translation.get("notes", "")) or "None.", "",
            "## Method and provenance", "", *_provenance(translation, score, prov), ""]
    return "\n".join(out)


def render_out_of_scope_note(translation: dict) -> str:
    """Three lines: title, the statement that nothing can be scored, the provisions found."""
    provs = translation["provisions"]
    listed = "; ".join(f"section {p['section']}: {_collapse(p['summary'])}" for p in provs) or \
        "the translator found no fiscal provisions"
    return "\n\n".join([
        f"# {translation['label']}: shadow fiscal note",
        f"**{DRAFT}.** Nothing in this bill can be scored by the model, so there is no estimate.",
        f"Provisions found, none scored: {listed}.",
    ]) + "\n"
