"""Which fiscal-note path a bill takes, and one note when it takes both.

The income tax path (``translator.py``) reads every bill. The state-tax path
(``state_taxes.py``) reads a bill only when its text touches one of the taxes
that path models; :func:`mentions_state_taxes` is that gate (the bill amends
one of those chapters), so an income tax bill costs one model call as before.

A bill can be scored by neither, one or both:

==================  ==================  =====================================
income tax path     state-tax path      result
==================  ==================  =====================================
scored              not run / nothing   the income tax note (as before)
nothing             scored              the state-tax note
scored              scored              one combined note (:func:`combine`)
nothing / invalid   nothing / invalid   as the income tax path alone decides
==================  ==================  =====================================

Everything here is a pure function of the two paths' outputs; the hub client
and ``python -m fiscal_notes auto`` do the calling.
"""
from __future__ import annotations

import re

from fiscal_notes.hub_result import ResultError

SCORED = ("full", "partial")

#: A bill goes down the state-tax path when it AMENDS (or adds to) one of the chapters that path
#: models: general excise (237), transient accommodations (237D), fuel (243), liquor (244D),
#: tobacco (245), rental vehicles (251). A mention is not enough: income tax bills cite "general
#: excise tax license number" and "chapter 237" without changing either.
_CHAPTERS = r"(?:237D?|243|244D|245|251)"
_GATE = re.compile(
    rf"(?:section|chapter)\s+{_CHAPTERS}(?:-[\w.]+)?(?:\([\w()]+\))*,?\s+Hawaii\s+Revised\s+Statutes,?\s+"
    r"(?:is|are)\s+(?:amended|repealed)",
    re.IGNORECASE,
)


def mentions_state_taxes(bill_text: str) -> bool:
    """Whether the bill amends a chapter the state-tax path models."""
    return _GATE.search(bill_text) is not None


# --------------------------------------------------------------------------- headlines


def state_headline(score: dict) -> dict:
    """The hub's headline block from a state-tax score: five-year totals in
    $ millions, positive raises revenue. The years are FISCAL years (the income
    tax path's are tax years); the note says so. ``low`` is the total under
    the largest sourced response of each component that has one (transient
    accommodations, cigarettes, gasoline); ``high`` and ``mid`` are static."""
    years = score["fiscal_years"]
    if len(years) < 1:
        raise ResultError("the state-tax score has no fiscal years")
    static = float(score["state_total"])
    low = static
    for comp in score["components"].values():
        r = comp.get("with_response")
        if r:                                    # the largest sourced response against none
            static_part = sum(float(v) for v in comp["state"].values())
            low += float(r["high"]) - static_part
    lo, hi = min(low, static), max(low, static)
    return {"five_year_mid": round(static / 1e3, 1) + 0.0, "five_year_low": round(lo / 1e3, 1) + 0.0,
            "five_year_high": round(hi / 1e3, 1) + 0.0,
            "first_year": int(years[0]), "last_year": int(years[-1])}


def combined_headline(income: dict, state: dict) -> dict:
    """Income tax and state-tax headlines added. The year labels are the income
    tax path's (tax years); the state part covers the fiscal years in its note."""
    return {
        "five_year_mid": round(income["five_year_mid"] + state["five_year_mid"], 1) + 0.0,
        "five_year_low": round(income["five_year_low"] + state["five_year_low"], 1) + 0.0,
        "five_year_high": round(income["five_year_high"] + state["five_year_high"], 1) + 0.0,
        "first_year": income["first_year"], "last_year": income["last_year"],
    }


# --------------------------------------------------------------------------- combining


def _scored(p: dict) -> bool:
    return bool(p["modeled"]) and not p["blank"]


def merge_provisions(income: list[dict], state: list[dict]) -> list[dict]:
    """One provision list for a bill both paths scored.

    Each translator lists every provision and marks the ones outside its own
    taxes "not modeled". Keep each path's scored provisions, then the ones
    neither scored: a path's unscored provision is dropped when the other path
    scored something in the same section (it is that provision, seen from the
    other side). Sections with a scored and an unscored provision of the same
    path keep both.
    """
    out = [{**p, "path": "income_tax"} for p in income if _scored(p)]
    out += [{**p, "path": "state_taxes"} for p in state if _scored(p)]
    income_sections = {p["section"] for p in income if _scored(p)}
    state_sections = {p["section"] for p in state if _scored(p)}
    listed: set[int] = set()
    for p in income:
        if not _scored(p) and p["section"] not in state_sections:
            out.append({**p, "path": "income_tax"})
            listed.add(p["section"])
    for p in state:
        if not _scored(p) and p["section"] not in income_sections and p["section"] not in listed:
            out.append({**p, "path": "state_taxes"})
    return sorted(out, key=lambda p: (p["section"], not _scored(p)))


def combine_notes(label: str, income_note: str, state_note: str, headline: dict) -> str:
    """One Markdown note: a combined bottom line, then each path's note in full."""
    def body(note: str) -> str:                  # drop the path's own title line
        lines = note.splitlines()
        return "\n".join(lines[1:] if lines and lines[0].startswith("# ") else lines).strip()

    def m(v: float) -> str:
        return f"{'+' if v > 0 else '−' if v < 0 else ''}${abs(v):,.1f}M"

    return "\n".join([
        f"# Fiscal note (draft): {label}", "",
        "**DRAFT, not reviewed.** This bill changes the income tax and other state taxes. The two "
        "parts are scored by different models and are set out one after the other below.", "",
        "## Combined bottom line", "",
        f"State revenue changes by about **{m(headline['five_year_mid'])}** over five years "
        f"(range {m(headline['five_year_low'])} to {m(headline['five_year_high'])}): the income tax "
        "part by tax year, the other taxes by fiscal year, each static or as its own section says. "
        "The two are added without interaction.", "",
        "---", "", "# Part 1: income tax", "", body(income_note), "",
        "---", "", "# Part 2: general excise, transient accommodations and excise taxes", "",
        body(state_note), "",
    ])


def combined_verdict(income_verdict: str, state_verdict: str) -> str:
    return "full" if income_verdict == state_verdict == "full" else "partial"
