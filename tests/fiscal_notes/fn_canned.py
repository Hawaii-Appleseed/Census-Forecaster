"""Canned translations and score results, so the note, the diff and ``run`` are tested without the
simulator population (a local, uncommitted input) and without a model."""
from __future__ import annotations

from fiscal_notes import expectations as ex
from fiscal_notes.score import build_result
from fiscal_notes.translator import parse_response

YEARS = (2027, 2028, 2029, 2030, 2031)

PROVENANCE = {"model_git_sha": "abc1234def", "model_tree_dirty": False,
              "population_git_sha": "9999999", "population_created_at": "2026-11-26T00:00:00+00:00",
              "credits_source": {"git_sha": "2cba8d0"}, "spec_sha256": "f" * 64}


def make_res(mid=(10.0, 20.0, 30.0, 40.0, 50.0), spread=1.0, credits=None) -> dict:
    """What ``score_spec`` returns: mid static = mid + 2, low/high = mid -/+ ``spread``."""
    rev = []
    for scenario, off in (("low", -spread), ("mid", 0.0), ("high", spread)):
        for y, m in zip(YEARS, mid, strict=True):
            rev.append({"scenario": scenario, "tax_year": y, "baseline_$M": 0.0, "reform_$M": 0.0,
                        "static_$M": m + 2.0, "behavioral_$M": m + off,
                        "filers_1m_post_response": 1.0})
    q = [{"group": g, "household_count": 100000.0, "avg_per_hh_baseline_tax": 1000.0,
          "avg_per_hh_reform_tax": 1100.0, "avg_per_hh_bracket_change": 100.0,
          "total_baseline_$M": 100.0, "total_reform_$M": 110.0, "total_bracket_$M": 10.0,
          "pct_pay_more": 90.0, "pct_pay_less": 5.0, "pct_no_change": 5.0}
         for g in ("Q1 (bottom 20%)", "Q2", "Q3", "Q4", "Q5 (top 20%)")]
    c = [{"group": g, "filer_count": 50000.0, "avg_baseline_tax": 2000.0, "avg_reform_tax": 2300.0,
          "avg_bracket_change": 300.0, "total_baseline_$M": 100.0, "total_reform_$M": 115.0,
          "total_bracket_$M": 15.0, "pct_pay_more": 80.0, "pct_pay_less": 10.0, "pct_no_change": 10.0}
         for g in ("Under $10K", "$1M+")]
    res = {"revenue": rev, "distribution": {2027: {"quintile": q, "income_class": c}}}
    if credits is not None:
        res["credits"] = [{"tax_year": y, "eitc_$M": e, "food_excise_$M": f, "total_$M": e + f}
                          for y, (e, f) in zip(YEARS, credits, strict=True)]
    return res


def _spec(d: dict):
    from tax_modeler.reform.income_tax_spec import IncomeTaxSpec
    return IncomeTaxSpec.from_dict({"name": "canned_bill_2026", "label": "Canned Bill (2026)", **d})


def hb2306_translation() -> dict:
    r = parse_response(ex.response_text("2026_HB2306_HD1"))
    return {"name": "hb2306_hd1_2026", "label": "HB 2306 HD1 (2026)", "verdict": "partial",
            "provisions": r["provisions"], "notes": r["notes"], "problems": [], "model": "sonnet",
            "attempts": 1, "bill_sha256": "b" * 64, "prompt_sha256": "c" * 64}


def hb2306_score() -> dict:
    r = parse_response(ex.response_text("2026_HB2306_HD1"))
    spec = _spec({**r["spec"]})
    credits = [(0.0, 0.0), (-42.8, -33.7), (-43.5, -32.2), (-44.8, -31.6), (-47.4, -30.8)]
    return build_result(spec, make_res(mid=(230.2, 230.3, 470.1, 475.0, 473.8), spread=5.0, credits=credits),
                        YEARS, 2027, PROVENANCE)


BRACKETS = {"single": [[0, 1.4], [9600, 3.2], [14400, 5.5]],
            "head_of_household": [[0, 1.4], [14400, 3.2], [21600, 5.5]],
            "married_filing_jointly": [[0, 1.4], [19200, 3.2], [28800, 5.5]]}


def brackets_spec_dict(top_rate: float = 5.5) -> dict:
    b = {k: [list(r) for r in v] for k, v in BRACKETS.items()}
    for rows in b.values():
        rows[-1][1] = top_rate
    return {"first_year": 2027, "income_tax": {"brackets": b}}


def brackets_translation(*, verdict="full", section=1, top="5.50") -> dict:
    prov = [{"section": section, "summary": f"Changes the bracket schedule (top rate {top}%)",
             "modeled": True, "spec_keys": ["income_tax.brackets"], "blank": False,
             "applies_from_tax_year": 2027, "quote": f"{top}% of excess over $28,800"}]
    if verdict == "partial":
        prov.append({"section": 2, "summary": "A credit the model cannot express", "modeled": False,
                     "spec_keys": [], "blank": False, "applies_from_tax_year": None,
                     "quote": "a credit of $100"})
    return {"name": "canned_bill_2026", "label": "Canned Bill (2026)", "verdict": verdict,
            "provisions": prov, "notes": "", "problems": [], "model": "sonnet", "attempts": 1,
            "bill_sha256": "d" * 64, "prompt_sha256": "e" * 64}


def brackets_score(top_rate: float = 5.5, mid=(-10.0, -20.0, -30.0, -40.0, -50.0)) -> dict:
    return build_result(_spec(brackets_spec_dict(top_rate)), make_res(mid=mid), YEARS, 2027, PROVENANCE)
