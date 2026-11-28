"""Fiscal notes for bills that change the State's excise-type taxes.

The income tax path (``translator.py``, ``score.py``, ``note.py``) scores an
``IncomeTaxSpec`` on the tax-unit simulator. This is the same pipeline for the
general excise tax, the transient accommodations tax, cigarettes, fuel, liquor
and the rental vehicle surcharge, scored through ``state_revenue``
(``STATE_REVENUE_SCOPE.md``):

    bill text -> LLM -> spec (JSON) -> deterministic checks -> score -> draft note

As there, nothing the model returns is trusted: the spec must validate, every
quote must be verbatim in the bill, every figure in the spec must appear in
the bill's in-force text, and every spec key must be claimed by a provision.
The verdict is derived here.

Scores are static fiscal-year changes in state revenue against the
``state_revenue`` baseline (the Council on Revenues' line items), for five
fiscal years from the one the bill takes effect in.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fiscal_notes.translator import (
    CheckResult,
    Translation,
    TranslationError,
    _collapse,
    _dollar_in,
    _num_text,
    _walk,
    in_force,
    parse_response,
    retry_prompt,
    run_claude,
)

PROMPT_PATH = Path(__file__).with_name("prompt_state_taxes.md")
YEARS_SCORED = 5

EXEMPT_CATEGORIES = ("groceries", "restaurants", "apparel", "medical_services")
#: Categories scored from input-output sectors instead of consumer spending.
SECTOR_CATEGORIES = {"medical_services": ("S50", "S51", "S52")}
GET_ACTIVITIES = ("retailing", "services", "contracting", "transient_accommodations", "other_rentals",
                  "other_4pct", "wholesaling", "manufacturing", "producing")
FUELS = ("gasoline", "diesel_highway", "diesel_off_highway", "aviation")
BEVERAGES = ("spirits", "wine", "beer")
BLOCKS = ("general_excise", "transient_accommodations", "cigarettes", "fuel", "liquor", "rental_vehicle")


class SpecError(ValueError):
    """The spec is not one this module can score."""


# --------------------------------------------------------------------------- the spec


def _number(v: Any, what: str, lo: float, hi: float) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise SpecError(f"{what} must be a number, got {v!r}")
    if not lo <= v <= hi:
        raise SpecError(f"{what} is {v}, outside {lo} to {hi}")
    return float(v)


def _mapping(v: Any, what: str, allowed: tuple[str, ...], lo: float, hi: float) -> dict[str, float]:
    if not isinstance(v, dict) or not v:
        raise SpecError(f"{what} must be a non-empty object")
    unknown = sorted(set(v) - set(allowed))
    if unknown:
        raise SpecError(f"{what} has unknown keys {unknown}; allowed: {list(allowed)}")
    return {k: _number(x, f"{what}.{k}", lo, hi) for k, x in v.items()}


def validate_spec(spec: dict) -> dict:
    """Check a spec's shape and ranges; returns it with numbers as floats."""
    if not isinstance(spec, dict):
        raise SpecError("spec must be an object")
    unknown = sorted(set(spec) - {"effective", "name", "label", *BLOCKS})
    if unknown:
        raise SpecError(f"unknown spec keys {unknown}")
    out: dict[str, Any] = {}
    eff = spec.get("effective")
    if eff is not None:
        try:
            out["effective"] = dt.date.fromisoformat(eff).isoformat()
        except (TypeError, ValueError) as e:
            raise SpecError(f"effective must be YYYY-MM-DD or null, got {eff!r}") from e
    else:
        out["effective"] = None

    ge = spec.get("general_excise")
    if ge is not None:
        if not isinstance(ge, dict) or not ge:
            raise SpecError("general_excise must be a non-empty object")
        extra = sorted(set(ge) - {"exempt", "rates", "repeal_exemptions"})
        if extra:
            raise SpecError(f"general_excise has unknown keys {extra}")
        block: dict[str, Any] = {}
        if "exempt" in ge:
            cats = ge["exempt"]
            if not isinstance(cats, list) or not cats or set(cats) - set(EXEMPT_CATEGORIES):
                raise SpecError(f"general_excise.exempt must list some of {list(EXEMPT_CATEGORIES)}, "
                                f"got {cats!r}")
            block["exempt"] = sorted(set(cats))
        if "rates" in ge:
            block["rates"] = _mapping(ge["rates"], "general_excise.rates", GET_ACTIVITIES, 0.0, 10.0)
        if "repeal_exemptions" in ge:
            codes = ge["repeal_exemptions"]
            if not isinstance(codes, list) or not codes or not all(
                    isinstance(c, str) and re.fullmatch(r"\d{3}", c) for c in codes):
                raise SpecError(f"general_excise.repeal_exemptions must list three-digit codes, got {codes!r}")
            block["repeal_exemptions"] = sorted(set(codes))
        out["general_excise"] = block

    tat = spec.get("transient_accommodations")
    if tat is not None:
        if not isinstance(tat, dict) or len(tat) != 1 or set(tat) - {"rate", "rate_change_points"}:
            raise SpecError("transient_accommodations takes exactly one of `rate` or `rate_change_points`")
        key = next(iter(tat))
        lo, hi = (0.0, 25.0) if key == "rate" else (-10.0, 10.0)
        out["transient_accommodations"] = {key: _number(tat[key], f"transient_accommodations.{key}", lo, hi)}

    for block_name, key, rng in (("cigarettes", "cents_per_cigarette", (0.0, 100.0)),
                                 ("rental_vehicle", "dollars_per_day", (0.0, 50.0))):
        b = spec.get(block_name)
        if b is not None:
            if not isinstance(b, dict) or set(b) != {key}:
                raise SpecError(f"{block_name} takes exactly `{key}`")
            out[block_name] = {key: _number(b[key], f"{block_name}.{key}", *rng)}

    fuel = spec.get("fuel")
    if fuel is not None:
        if not isinstance(fuel, dict) or not fuel or set(fuel) - {"cents_per_gallon", "dollars_per_barrel"}:
            raise SpecError("fuel takes `cents_per_gallon` and/or `dollars_per_barrel`")
        block = {}
        if "cents_per_gallon" in fuel:
            block["cents_per_gallon"] = _mapping(fuel["cents_per_gallon"], "fuel.cents_per_gallon",
                                                 FUELS, 0.0, 200.0)
        if "dollars_per_barrel" in fuel:
            block["dollars_per_barrel"] = _number(fuel["dollars_per_barrel"], "fuel.dollars_per_barrel",
                                                  0.0, 20.0)
        out["fuel"] = block

    liquor = spec.get("liquor")
    if liquor is not None:
        if not isinstance(liquor, dict) or set(liquor) != {"dollars_per_gallon"}:
            raise SpecError("liquor takes exactly `dollars_per_gallon`")
        out["liquor"] = {"dollars_per_gallon": _mapping(liquor["dollars_per_gallon"],
                                                        "liquor.dollars_per_gallon", BEVERAGES, 0.0, 50.0)}
    if not any(b in out for b in BLOCKS):
        raise SpecError("spec changes nothing: no tax block is set")
    return out


def spec_leaves(spec: dict) -> list[str]:
    """The ``block.key`` settings the spec makes."""
    return [f"{b}.{k}" for b in BLOCKS if isinstance(spec.get(b), dict) for k in spec[b]]


# --------------------------------------------------------------------------- figure checks

_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty"]
_FRACTIONS = {0.5: ("one-half", "one half"), 0.25: ("one-quarter", "one-fourth", "one quarter", "one fourth"),
              0.75: ("three-quarters", "three-fourths", "three quarters", "three fourths")}


def _word_forms(value: float) -> list[str]:
    """Statute spellings of a small number: 4 -> "four", 4.5 -> "four and one-half"."""
    whole, frac = int(abs(value)), round(abs(value) - int(abs(value)), 4)
    if whole >= len(_ONES):
        return []
    if frac == 0:
        return [_ONES[whole]]
    if frac in _FRACTIONS:
        if whole == 0:
            return list(_FRACTIONS[frac])
        return [f"{_ONES[whole]} and {f}" for f in _FRACTIONS[frac]]
    return []


def _number_before(text: str, value: float, unit: str) -> bool:
    """``value`` written as digits or words directly before ``unit`` (a regex)."""
    n = _num_text(abs(value))
    digits = (rf"0?{re.escape(n[1:])}" if n.startswith("0.") else re.escape(n)) + r"(?:\.0+)?"
    forms = [rf"(?<![\d.]){digits}"] + [rf"\b{re.escape(w)}" for w in _word_forms(value)]
    return any(re.search(rf"{f}\s*{unit}", text, re.IGNORECASE) for f in forms)


_PERCENT = r"(?:%|per\s?cent(?:age\s+points?)?)"


def _figure_problems(spec: dict, text: str) -> list[str]:
    problems: list[str] = []

    def need(ok: bool, shown: str, where: str) -> None:
        if not ok:
            problems.append(f"figure {shown} ({where}) does not appear in the bill text")

    for activity, rate in (spec.get("general_excise", {}).get("rates") or {}).items():
        need(_number_before(text, rate, _PERCENT), f"{_num_text(rate)}%", f"general excise rate, {activity}")
    tat = spec.get("transient_accommodations") or {}
    if "rate" in tat:
        need(_number_before(text, tat["rate"], _PERCENT), f"{_num_text(tat['rate'])}%",
             "transient accommodations rate")
    if "rate_change_points" in tat:
        v = tat["rate_change_points"]
        need(_number_before(text, v, _PERCENT), f"{_num_text(abs(v))} percentage points",
             "transient accommodations rate change")
    cig = spec.get("cigarettes") or {}
    if "cents_per_cigarette" in cig:
        need(_number_before(text, cig["cents_per_cigarette"], r"cents?"),
             f"{_num_text(cig['cents_per_cigarette'])} cents", "tax per cigarette")
    fuel = spec.get("fuel") or {}
    for kind, cents in (fuel.get("cents_per_gallon") or {}).items():
        need(_number_before(text, cents, r"cents?"), f"{_num_text(cents)} cents", f"fuel tax, {kind}")
    if "dollars_per_barrel" in fuel:
        v = fuel["dollars_per_barrel"]
        need(_dollar_in(text, v), f"${v:,.2f}", "environmental response tax per barrel")
    for kind, dollars in ((spec.get("liquor") or {}).get("dollars_per_gallon") or {}).items():
        need(_dollar_in(text, dollars), f"${dollars:,.2f}", f"liquor tax, {kind}")
    rv = spec.get("rental_vehicle") or {}
    if "dollars_per_day" in rv:
        v = rv["dollars_per_day"]
        need(_dollar_in(text, v) or _number_before(text, v, r"dollars?"), f"${v:,.2f}",
             "rental vehicle surcharge per day")
    return problems


# --------------------------------------------------------------------------- translate


def build_prompt(bill_text: str) -> str:
    return f"{PROMPT_PATH.read_text().rstrip()}\n\n## The bill\n\n{bill_text.strip()}\n"


def check(response: dict, bill_text: str) -> CheckResult:
    """Every deterministic check on a parsed response; never trusts the model."""
    spec = response["spec"]
    provisions = response["provisions"]
    problems: list[str] = []
    if spec is not None and not spec_leaves(spec):
        spec = None                                   # only `effective`: nothing modeled
    if spec is not None:
        try:
            spec = validate_spec(spec)
        except SpecError as e:
            problems.append(f"spec is not valid: {e}")

    haystack = _collapse(bill_text)
    for p in provisions:
        quote = _collapse(p["quote"])
        if not quote:
            problems.append(f"section {p['section']}: the provision has no quote")
        elif quote not in haystack:
            problems.append(f"section {p['section']}: quote is not verbatim in the bill: {p['quote'][:80]!r}")

    if spec is not None and not problems:
        problems.extend(_figure_problems(spec, _collapse(in_force(bill_text))))
        claimed: set[str] = set()
        for p in provisions:
            if not p["modeled"]:
                continue
            for key in p["spec_keys"]:
                claimed.add(key)
                if not _walk(spec, key):
                    problems.append(f"section {p['section']}: modeled provision cites spec key "
                                    f"`{key}`, which is not in the spec")
        for leaf in spec_leaves(spec):
            if not any(k == leaf or k.startswith(leaf + ".") for k in claimed):
                problems.append(f"spec key `{leaf}` is not claimed by any modeled provision")

    if problems:
        verdict = "invalid"
    elif spec is None or not any(p["modeled"] for p in provisions):
        verdict = "out_of_scope"
    elif any((not p["modeled"]) or p["blank"] or p.get("approximate") for p in provisions):
        verdict = "partial"
    else:
        verdict = "full"
    return CheckResult(verdict=verdict, spec=spec, provisions=provisions,
                       notes=response["notes"], problems=problems)


def translate_bill(bill_text: str, runner: Callable[[str], str] = run_claude) -> Translation:
    """Prompt, run, parse, check; one retry with the problems appended."""
    prompt = build_prompt(bill_text)
    responses: list[str] = []
    result: CheckResult | None = None
    for attempt in (1, 2):
        raw = runner(prompt if attempt == 1 else retry_prompt(prompt, responses[-1], result.problems))
        responses.append(raw)
        try:
            result = check(parse_response(raw), bill_text)
        except TranslationError as e:
            result = CheckResult(verdict="invalid", spec=None, provisions=[], notes="",
                                 problems=[f"response could not be parsed: {e}"])
        if result.verdict != "invalid":
            break
    return Translation(result=result, responses=responses, attempts=len(responses))


# --------------------------------------------------------------------------- score


def fiscal_year_of(day: dt.date) -> int:
    return day.year + 1 if day.month >= 7 else day.year


def _first_year_share(effective: dt.date, fy: int) -> float:
    """Share of fiscal year ``fy`` on or after ``effective``."""
    start, end = dt.date(fy - 1, 7, 1), dt.date(fy, 7, 1)
    if effective <= start:
        return 1.0
    return max(0.0, (end - effective).days / (end - start).days)


def _range_total(ranges: dict[int, dict[str, float]], eff: dt.date, years: list[int]) -> dict[str, float]:
    """Sum a component's low, middle and high responses over the scored years."""
    return {k: sum(ranges[fy][k] * _first_year_share(eff, fy) for fy in years)
            for k in ("low", "mid", "high")}


def score_spec(spec: dict) -> dict:
    """Static change in state revenue by fiscal year, $ thousands, by component.

    The first fiscal year scored is the one holding ``effective`` (prorated by
    days), or the baseline's second year when the date is absent or past (a
    full year: the note says the date was assumed).
    """
    from state_revenue import excise, general_excise
    from state_revenue.baseline import build_baseline

    spec = validate_spec(spec)
    baseline = build_baseline("all")
    first_possible = baseline.fiscal_years[1]
    assumptions: list[str] = []
    eff = dt.date.fromisoformat(spec["effective"]) if spec["effective"] else None
    if eff is None or fiscal_year_of(eff) < first_possible:
        why = "the bill gives no usable effective date" if eff is None else f"the bill's date ({eff}) has passed"
        eff = dt.date(first_possible - 1, 7, 1)
        assumptions.append(f"Effective date assumed to be {eff:%B} {eff.day}, {eff.year}: {why}.")
    first = fiscal_year_of(eff)
    years = [fy for fy in range(first, first + YEARS_SCORED) if fy in baseline.values]
    if len(years) < YEARS_SCORED:
        assumptions.append(f"Scored through FY{years[-1]}, the baseline's last year.")
    if _first_year_share(eff, first) < 1.0:
        assumptions.append(f"FY{first} counts the {_first_year_share(eff, first):.0%} of the year "
                           f"from {eff:%B} {eff.day}, {eff.year}.")

    components: dict[str, dict] = {}

    def add(key: str, label: str, fn: Callable[[int], Any]) -> None:
        state, county, borne = {}, {}, {}
        for fy in years:
            s = fn(fy)
            share = _first_year_share(eff, fy)
            state[fy], county[fy] = s.state * share, s.county * share
            borne = s.borne_by or borne
        components[key] = {"label": label, "state": state, "county": county, "borne_by": borne}

    ge = spec.get("general_excise") or {}
    for cat in ge.get("exempt", []):
        if cat in SECTOR_CATEGORIES:
            add(f"general_excise.exempt.{cat}", f"General excise exemption: {cat.replace('_', ' ')}",
                lambda fy, c=cat: general_excise.score_sector_exemption(
                    SECTOR_CATEGORIES[c], fy, purchasers=tuple(general_excise.PURCHASERS)))
        else:
            add(f"general_excise.exempt.{cat}", f"General excise exemption: {cat}",
                lambda fy, c=cat: general_excise.score_category_exemption(c, fy))
    if "rates" in ge:
        rates = {k: v / 100 for k, v in ge["rates"].items()}
        add("general_excise.rates", "General excise rates by activity",
            lambda fy: general_excise.score_rate_change(rates, fy))
    for code in ge.get("repeal_exemptions", []):
        add(f"general_excise.repeal_exemptions.{code}", f"Repeal of general excise exemption {code}",
            lambda fy, c=code: general_excise.score_exemption_repeal(c, fy))
    tat = spec.get("transient_accommodations") or {}
    if tat:
        def tat_score(fy: int):
            now = excise.rate_in("transient_accommodations", fy)
            new = tat["rate"] / 100 if "rate" in tat else now + tat["rate_change_points"] / 100
            return excise.score_tat(new, fy)
        add("transient_accommodations", "Transient accommodations tax rate", tat_score)
        # The one tax with a sourced, Hawaii-specific response and no price needed.
        ranges = {fy: excise.tat_range(
            tat["rate"] / 100 if "rate" in tat else
            excise.rate_in("transient_accommodations", fy) + tat["rate_change_points"] / 100, fy)
            for fy in years}
        components["transient_accommodations"]["with_response"] = _range_total(ranges, eff, years)
    if "cigarettes" in spec:
        cents = spec["cigarettes"]["cents_per_cigarette"]
        add("cigarettes", "Cigarette tax", lambda fy: excise.score_cigarettes(cents / 100, fy))
        components["cigarettes"]["with_response"] = _range_total(
            {fy: excise.cigarette_range(cents / 100, fy) for fy in years}, eff, years)
    fuel = spec.get("fuel") or {}
    new_fuel = {k: v / 100 for k, v in (fuel.get("cents_per_gallon") or {}).items()}
    if "dollars_per_barrel" in fuel:
        new_fuel["ert_barrel"] = fuel["dollars_per_barrel"]
    if new_fuel:
        add("fuel", "Fuel and environmental response taxes", lambda fy: excise.score_fuel(new_fuel, fy))
        if {"gasoline", "diesel_highway"} & set(new_fuel):
            components["fuel"]["with_response"] = _range_total(
                {fy: excise.gasoline_range(new_fuel, fy) for fy in years}, eff, years)
    if "liquor" in spec:
        add("liquor", "Liquor tax",
            lambda fy: excise.score_liquor(spec["liquor"]["dollars_per_gallon"], fy))
    if "rental_vehicle" in spec:
        add("rental_vehicle", "Rental motor vehicle surcharge",
            lambda fy: excise.score_rental_vehicle(spec["rental_vehicle"]["dollars_per_day"], fy))

    state = {fy: sum(c["state"][fy] for c in components.values()) for fy in years}
    county = {fy: sum(c["county"][fy] for c in components.values()) for fy in years}
    return {
        "effective": eff.isoformat(),
        "fiscal_years": years,
        "state": state,
        "county": county,
        "state_total": sum(state.values()),
        "county_total": sum(county.values()),
        "baseline_state_taxes": {fy: baseline.total(fy) for fy in years},
        "components": components,
        "assumptions": assumptions,
        "provenance": {"baseline_cor_meeting": str(baseline.cor_meeting),
                       "baseline_last_actual": baseline.origin, "method": "static"},
    }


def dumps(score: dict) -> str:
    return json.dumps(score, indent=2, sort_keys=True, default=str) + "\n"


# --------------------------------------------------------------------------- note

_VERDICT_WORDS = {
    "full": "every provision that changes taxes is scored",
    "partial": "some provisions are not scored or are scored approximately",
    "out_of_scope": "nothing in this bill can be scored",
}


def _m(thousands: float) -> str:
    """$ millions with sign, from thousands."""
    v = thousands / 1e3
    return f"{'+' if v > 0 else '−' if v < 0 else ''}${abs(v):,.1f}M"


_RESPONSE_NOTES = {
    "transient_accommodations": "Room nights to the room price: none (Bonham and Gangnes 1996, on the 1987 "
                                "room tax), -1.0 and -1.5 (Fujii, Khaled and Mak 1988).",
    "cigarettes": "Adult demand to the price of a pack: -0.29, -0.37, -0.47 (Community Preventive Services "
                  "Task Force 2012, national), at the average Hawaii pack price (CDC, Tax Burden on Tobacco).",
    "fuel": "Gasoline and highway diesel to the pump price, short run: -0.034, -0.077, -0.21 (Hughes, "
            "Knittel and Sperling 2006, national), at DBEDT's Hawaii prices; other fuels static.",
}


def _provision_lines(provisions: list[dict]) -> list[str]:
    out = []
    for p in provisions:
        out.append(f"- **Section {p['section']}.** {p['summary']}")
        out.append(f"  > {_collapse(p['quote'])}")
        if p.get("approximate"):
            out.append(f"  Approximation: {p['approximation']}")
    return out


def render_note(translation: dict, score: dict | None) -> str:
    """The one-page draft note: a pure function of the translation and the score."""
    label, verdict = translation["label"], translation["verdict"]
    lines = [f"# Fiscal note (draft): {label}", "",
             f"**DRAFT, not reviewed.** Machine-translated from the bill text; {_VERDICT_WORDS[verdict]}. "
             "A person must check every provision against the bill before this is cited.", ""]
    provisions = translation["provisions"]
    exact = [p for p in provisions if p["modeled"] and not p.get("approximate") and not p["blank"]]
    approx = [p for p in provisions if p["modeled"] and p.get("approximate")]
    unscored = [p for p in provisions if not p["modeled"] or p["blank"]]

    if score is None:
        lines += ["## Bottom line", "", "Nothing in this bill can be scored by the state revenue model.", ""]
    else:
        years = score["fiscal_years"]
        lines += ["## Bottom line", "",
                  f"State revenue changes by **{_m(score['state_total'])}** over FY{years[0]}–FY{years[-1]} "
                  f"against current law (static)."
                  + (f" County surcharge revenue changes by {_m(score['county_total'])}."
                     if abs(score["county_total"]) >= 50 else ""), ""]
        lines += ["## Revenue by fiscal year ($ millions)", "",
                  "| | " + " | ".join(f"FY{fy}" for fy in years) + " | Total |",
                  "|---|" + "---|" * (len(years) + 1)]
        for c in score["components"].values():
            vals = [c["state"][fy] if fy in c["state"] else c["state"][str(fy)] for fy in years]
            lines.append(f"| {c['label']} | " + " | ".join(_m(v) for v in vals) + f" | {_m(sum(vals))} |")
        st = [score["state"][fy] if fy in score["state"] else score["state"][str(fy)] for fy in years]
        lines.append("| **State total** | " + " | ".join(f"**{_m(v)}**" for v in st)
                     + f" | **{_m(sum(st))}** |")
        if abs(score["county_total"]) >= 50:
            co = [score["county"][fy] if fy in score["county"] else score["county"][str(fy)] for fy in years]
            lines.append("| County surcharge | " + " | ".join(_m(v) for v in co) + f" | {_m(sum(co))} |")
        lines.append("")
        responding = [(k, c) for k, c in score["components"].items() if "with_response" in c]
        if responding:
            lines += ["## If buyers respond", "",
                      "The table above is static. With the sourced responses (low, middle, high; "
                      "`state_revenue/elasticities.py`), the change over the period is:", ""]
            for key, c in responding:
                r = c["with_response"]
                lines.append(f"- {c['label']}: {_m(r['low'])}, {_m(r['mid'])}, {_m(r['high'])}. "
                             + _RESPONSE_NOTES[key])
            lines += ["", "Tax lost on other lines when less is sold (the general excise tax on rooms, "
                      "packs or gallons) is not counted.", ""]
        borne = [(c["label"], c["borne_by"]) for c in score["components"].values() if c["borne_by"]]
        if borne:
            lines += ["## Who bears it", ""]
            for comp_label, shares in borne:
                parts = ", ".join(f"{k} {v:.0%}" for k, v in sorted(shares.items(), key=lambda kv: -kv[1])
                                  if v >= 0.005)
                lines.append(f"- {comp_label}: {parts}.")
            lines.append("")
    for title, group in (("What is scored", exact), ("Scored approximately", approx),
                         ("What is NOT scored (the estimate leaves these out)", unscored)):
        if group:
            lines += [f"## {title}", "", *_provision_lines(group), ""]
    if translation.get("notes"):
        lines += ["## Reviewer notes", "", translation["notes"], ""]
    lines += ["## Method", ""]
    if score is not None:
        lines += [f"- {a}" for a in score["assumptions"]]
        p = score["provenance"]
        lines += [
            "- Static: no change in spending, prices or visitor numbers beyond the tax itself. A positive "
            "figure means more revenue.",
            "- Bases: DOTAX's general excise base by activity and exemptions claimed, its transient "
            "accommodations base, fuel gallons and tobacco collections; BEA consumer spending and HTA visitor "
            "spending; NIAAA gallons of liquor. Grown with the state revenue baseline (Council on Revenues "
            f"line items, meeting of {p['baseline_cor_meeting']}; last actual FY{p['baseline_last_actual']}).",
            "- One tax at a time: a change in one tax's effect on another's base (the general excise tax on "
            "hotel rentals when the transient accommodations tax changes) is not counted.",
        ]
    lines += [f"- Bill text sha256 `{translation.get('bill_sha256', '')[:16]}`; translator model "
              f"`{translation.get('model', '')}`; model: `state_revenue` (STATE_REVENUE_SCOPE.md).", ""]
    return "\n".join(lines)
