"""The bill-to-spec translator, offline: recorded model answers, tampered copies, a fake runner."""
from __future__ import annotations

import copy
import json

import pytest

from fiscal_notes import expectations as ex
from fiscal_notes.translator import (
    TranslationError,
    build_prompt,
    check,
    effective_brackets,
    in_force,
    parse_response,
    prompt_rules,
    run_claude,
    translate_bill,
)

CASES = list(ex.CASES)
NAME = "test_bill_2026"


def recorded(case: str) -> dict:
    return parse_response(ex.response_text(case))


def problems_for(response: dict, case: str = "2026_HB2306_HD1") -> list[str]:
    res = check(response, ex.bill_text(case), NAME)
    assert res.verdict == "invalid", res.problems
    return res.problems


# ---- recorded responses ----------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES)
def test_recorded_response_meets_expectations(case):
    res = check(recorded(case), ex.bill_text(case), NAME)
    ev = ex.evaluate(case, res)
    failed = [f"{e.label}: {e.detail}" for e in ev.expectations if not e.ok]
    assert not failed, failed
    assert res.problems == []


def test_every_fixture_has_a_case_and_a_recording():
    bills = {p.stem for p in ex.BILLS_DIR.glob("*.txt")}
    recs = {p.stem for p in ex.RESPONSES_DIR.glob("*.json")}
    assert bills == recs == set(CASES)


def test_sd1_2029_schedule_is_the_bills_own():
    spec = recorded("2026_SB3125_SD1")["spec"]
    assert effective_brackets(spec, 2029)["single"] == [list(map(float, r)) for r in
                                                        ex.SB3125_SD1_2029_SINGLE]
    assert effective_brackets(spec, 2027)["single"] != effective_brackets(spec, 2029)["single"]


def test_effective_brackets_picks_latest_vintage_not_after_year():
    spec = recorded("2026_SB3125_CD2")["spec"]
    years = sorted(v["from"] for v in spec["income_tax"]["bracket_vintages"])
    assert len(years) >= 2
    assert effective_brackets(spec, years[1] - 1) == effective_brackets(spec, years[0])
    assert effective_brackets(spec, years[1]) != effective_brackets(spec, years[0])
    assert effective_brackets({"first_year": 2028}, 2028) is None


# ---- parse_response ----------------------------------------------------------------------------


def test_fenced_response_still_parses():
    raw = ex.response_text("2024_HB2404_SD1")
    assert parse_response(f"```json\n{raw}\n```") == parse_response(raw)
    assert parse_response(f"```\n{raw}```") == parse_response(raw)


@pytest.mark.parametrize("raw", ["", "not json", '{"spec": null, "provisions": [', "[1, 2]"])
def test_malformed_json_raises(raw):
    with pytest.raises(TranslationError):
        parse_response(raw)


def test_malformed_json_message_names_the_cause():
    with pytest.raises(TranslationError, match="not valid JSON"):
        parse_response("{nope")


def _mutated(**changes):
    r = copy.deepcopy(recorded("2026_HB2306_HD1"))
    r.update(changes)
    return r


@pytest.mark.parametrize("bad, match", [
    ({"spec": "x"}, "spec"),
    ({"notes": 3}, "notes"),
    ({"provisions": {}}, "provisions"),
    ({"provisions": [1]}, r"provisions\[0\]"),
])
def test_structure_is_validated(bad, match):
    with pytest.raises(TranslationError, match=match):
        parse_response(json.dumps(_mutated(**bad)))


@pytest.mark.parametrize("key, value", [
    ("section", "1"), ("section", True), ("summary", 1), ("modeled", "yes"),
    ("spec_keys", "income_tax"), ("spec_keys", [1]), ("blank", 0),
    ("applies_from_tax_year", "2027"), ("quote", None),
])
def test_provision_field_types(key, value):
    r = _mutated()
    r["provisions"][0][key] = value
    with pytest.raises(TranslationError, match=key):
        parse_response(json.dumps(r))


def test_missing_provision_key_and_top_key():
    r = _mutated()
    del r["provisions"][0]["quote"]
    with pytest.raises(TranslationError, match="quote"):
        parse_response(json.dumps(r))
    r = _mutated()
    del r["notes"]
    with pytest.raises(TranslationError, match="notes"):
        parse_response(json.dumps(r))


# ---- tampered recordings -----------------------------------------------------------------------


def test_altered_quote():
    r = recorded("2026_HB2306_HD1")
    r["provisions"][0]["quote"] += " and furthermore invented words"
    p = problems_for(r)
    assert any("quote is not verbatim" in m and f"section {r['provisions'][0]['section']}" in m
               for m in p)


def test_quote_matches_across_whitespace_differences():
    r = recorded("2026_HB2306_HD1")
    q = r["provisions"][0]["quote"]
    r["provisions"][0]["quote"] = q.replace(" ", "\n  ", 1)
    assert check(r, ex.bill_text("2026_HB2306_HD1"), NAME).problems == []


def test_empty_quote():
    r = recorded("2026_HB2306_HD1")
    r["provisions"][0]["quote"] = "  "
    assert any("no quote" in m for m in problems_for(r))


def test_bracket_floor_not_in_bill():
    r = recorded("2026_HB2306_HD1")
    r["spec"]["income_tax"]["bracket_vintages"][0]["brackets"]["single"][1][0] = 9601
    assert any("$9,601" in m and "does not appear" in m for m in problems_for(r))


def test_figure_only_in_repealed_text_does_not_count():
    # HB 2306 HD1 repeals the 2027 schedule whose joint top floor is $800,000
    r = recorded("2026_HB2306_HD1")
    assert "$800,000" in ex.bill_text("2026_HB2306_HD1")
    r["spec"]["income_tax"]["bracket_vintages"][0]["brackets"]["married_filing_jointly"][-1][0] = 800000
    assert any("$800,000" in p for p in problems_for(r))


def test_in_force_strips_nested_repeals():
    assert in_force("a [b [c] d] e [f]").split() == ["a", "e"]


def test_bracket_rate_not_in_bill():
    r = recorded("2026_HB2306_HD1")
    r["spec"]["income_tax"]["bracket_vintages"][0]["brackets"]["single"][1][1] = 3.3
    assert any("3.30%" in m and "does not appear" in m for m in problems_for(r))


def test_rate_must_be_written_with_two_decimals():
    # "6.40%" is in the bill; 6.4 -> "6.40%" fine, but "16.40%" must not satisfy 6.40
    from fiscal_notes.translator import _bracket_rate_in
    assert _bracket_rate_in("rate 6.40% here", 6.4)
    assert not _bracket_rate_in("rate 16.40% here", 6.4)
    assert not _bracket_rate_in("rate 6.4% here", 6.4)


def test_dollar_figure_matching():
    from fiscal_notes.translator import _dollar_in
    assert _dollar_in("over $9,600 but", 9600)
    assert _dollar_in("amount of $800.", 800)
    assert not _dollar_in("over $19,600 but", 9600)
    assert not _dollar_in("over 9,600 but", 9600)
    assert not _dollar_in("over $9,6001 but", 9600)


def test_capital_gains_rate_forms():
    from fiscal_notes.translator import _cg_rate_in
    assert _cg_rate_in("a tax of 7.25 per cent of", 7.25)
    assert _cg_rate_in("a tax of 7.25% of", 7.25)
    assert _cg_rate_in("tax of 9 percent", 9)
    assert _cg_rate_in("tax of 9.0 per cent", 9)
    assert _cg_rate_in("a tax of .5 per cent", 0.5)
    assert not _cg_rate_in("a tax of 17.25 per cent", 7.25)
    assert not _cg_rate_in("a tax of 9 dollars", 9)


def test_capital_gains_rate_not_in_bill():
    r = recorded("2026_HB2306_HD1")
    r["spec"]["income_tax"]["capital_gains_rate"] = 8.5
    r["provisions"][0]["spec_keys"].append("income_tax.capital_gains_rate")
    assert any("8.5%" in m and "capital gains" in m for m in problems_for(r))


def test_standard_deduction_and_exemption_not_in_bill():
    r = recorded("2026_HB2306_HD1")
    r["spec"]["income_tax"]["standard_deduction"] = {
        "single": 12345, "head_of_household": 12345, "married_filing_jointly": 12345}
    r["spec"]["income_tax"]["personal_exemption"] = 7777
    r["provisions"][0]["spec_keys"] += ["income_tax.standard_deduction",
                                        "income_tax.personal_exemption"]
    p = problems_for(r)
    assert any("$12,345" in m and "standard deduction" in m for m in p)
    assert any("$7,777" in m and "personal exemption" in m for m in p)


def test_unclaimed_spec_key():
    r = recorded("2026_HB2306_HD1")
    for p in r["provisions"]:
        p["spec_keys"] = [k for k in p["spec_keys"] if k != "credits.eitc_percent"]
    assert any("`credits.eitc_percent` is not claimed" in m for m in problems_for(r))


def test_claim_by_unmodeled_provision_does_not_count():
    r = recorded("2026_HB2306_HD1")
    for p in r["provisions"]:
        if "credits.eitc_percent" in p["spec_keys"]:
            p["modeled"] = False
    assert any("`credits.eitc_percent` is not claimed" in m for m in problems_for(r))


def test_modeled_provision_cites_missing_key():
    r = recorded("2026_HB2306_HD1")
    modeled = next(p for p in r["provisions"] if p["modeled"])
    modeled["spec_keys"].append("income_tax.personal_exemption")
    assert any("cites spec key `income_tax.personal_exemption`" in m for m in problems_for(r))


def test_unknown_spec_key_is_rejected_by_the_validator():
    r = recorded("2026_HB2306_HD1")
    r["spec"]["income_tax"]["surtax"] = 5
    assert any("spec is not valid" in m and "surtax" in m for m in problems_for(r))


def test_spec_without_first_year_is_invalid():
    r = recorded("2026_HB2306_HD1")
    del r["spec"]["first_year"]
    assert any("spec is not valid" in m and "first_year" in m for m in problems_for(r))


# ---- verdicts ----------------------------------------------------------------------------------


def test_verdict_full_when_everything_modeled_and_nothing_blank():
    r = recorded("2026_HB2306_HD1")
    r["provisions"] = [p for p in r["provisions"] if p["modeled"]]
    for p in r["provisions"]:
        p["blank"] = False
    assert check(r, ex.bill_text("2026_HB2306_HD1"), NAME).verdict == "full"


def test_verdict_partial_when_a_modeled_provision_is_blank():
    r = recorded("2026_HB2306_HD1")
    r["provisions"] = [p for p in r["provisions"] if p["modeled"]]
    r["provisions"][0]["blank"] = True
    assert check(r, ex.bill_text("2026_HB2306_HD1"), NAME).verdict == "partial"


def test_verdict_out_of_scope_when_nothing_modeled():
    r = recorded("2024_HB2404_SD1")
    assert r["spec"] is None
    assert check(r, ex.bill_text("2024_HB2404_SD1"), NAME).verdict == "out_of_scope"


def test_verdict_is_never_taken_from_the_model():
    r = recorded("2024_HB2404_SD1")
    r["verdict"] = "full"
    r["provisions"][0]["verdict"] = "full"
    assert check(r, ex.bill_text("2024_HB2404_SD1"), NAME).verdict == "out_of_scope"


# ---- prompt and runner -------------------------------------------------------------------------


def test_prompt_is_rules_then_bill():
    p = build_prompt("BILL: X\n\nSECTION 1. hi")
    assert p.startswith(prompt_rules().rstrip())
    assert p.rstrip().endswith("SECTION 1. hi")
    assert "\n## The bill\n" in p
    assert "Effective-date, applicability and definition-only sections are not provisions" in p


def test_run_claude_command_and_errors(monkeypatch, tmp_path):
    import subprocess
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"], seen["kw"] = cmd, kw
        return subprocess.CompletedProcess(cmd, 0, stdout="answer", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setenv("CLAUDE_BIN", "/x/claude")
    assert run_claude("PROMPT", model="opus", timeout=7) == "answer"
    assert seen["cmd"] == ["/x/claude", "-p", "--model", "opus", "--tools", ""]
    assert seen["kw"]["input"] == "PROMPT" and seen["kw"]["timeout"] == 7
    monkeypatch.delenv("CLAUDE_BIN")
    run_claude("p")
    assert seen["cmd"][0] == "claude"

    from fiscal_notes.translator import RunnerError

    def failing(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 3, stdout="", stderr="boom")

    monkeypatch.setattr(subprocess, "run", failing)
    with pytest.raises(RunnerError, match="exited 3.*boom"):
        run_claude("p")

    def slow(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(subprocess, "run", slow)
    with pytest.raises(RunnerError, match="timed out"):
        run_claude("p", timeout=1)


# ---- translate_bill with a fake runner ---------------------------------------------------------


class FakeRunner:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.answers.pop(0)


def _bad_response(case="2026_HB2306_HD1") -> str:
    r = recorded(case)
    r["provisions"][0]["quote"] = "words that are not in the bill"
    return json.dumps(r)


def test_translate_succeeds_first_try():
    case = "2026_HB2306_HD1"
    runner = FakeRunner(ex.response_text(case))
    t = translate_bill(ex.bill_text(case), NAME, runner)
    assert (t.result.verdict, t.attempts, len(t.responses)) == ("partial", 1, 1)
    assert len(runner.prompts) == 1


def test_translate_retries_once_with_the_problems():
    case = "2026_HB2306_HD1"
    runner = FakeRunner(_bad_response(), ex.response_text(case))
    t = translate_bill(ex.bill_text(case), NAME, runner)
    assert (t.result.verdict, t.attempts) == ("partial", 2)
    assert "Your previous answer had these problems" in runner.prompts[1]
    assert "quote is not verbatim" in runner.prompts[1]
    assert "Return the corrected JSON object." in runner.prompts[1]
    assert "Your previous answer had these problems" not in runner.prompts[0]


def test_translate_retries_after_malformed_json():
    case = "2024_HB2404_SD1"
    runner = FakeRunner("sorry, here you go: {", ex.response_text(case))
    t = translate_bill(ex.bill_text(case), NAME, runner)
    assert (t.result.verdict, t.attempts) == ("out_of_scope", 2)
    assert "could not be parsed" in runner.prompts[1]


def test_translate_fails_twice_returns_invalid():
    case = "2026_HB2306_HD1"
    runner = FakeRunner(_bad_response(), _bad_response())
    t = translate_bill(ex.bill_text(case), NAME, runner)
    assert (t.result.verdict, t.attempts, len(t.responses)) == ("invalid", 2, 2)
    assert any("quote is not verbatim" in p for p in t.result.problems)
    assert runner.answers == []


def test_translate_fails_twice_on_unparseable():
    runner = FakeRunner("nope", "still nope")
    t = translate_bill(ex.bill_text("2024_HB2404_SD1"), NAME, runner)
    assert t.result.verdict == "invalid" and t.result.spec is None
    assert "could not be parsed" in t.result.problems[0]
