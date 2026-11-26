"""Follow-ups from the 2026 backtest, offline: an empty stub spec is no spec, a CLI run never leaves a
spec file for a bill with nothing to score, and approximate provisions are visible everywhere
(parsing, verdict, note, diff, translation JSON)."""
from __future__ import annotations

import json

import fn_canned as c
import pytest

from fiscal_notes import expectations as ex
from fiscal_notes.cli import main
from fiscal_notes.diff import provision_changes
from fiscal_notes.note import render_note
from fiscal_notes.translator import TranslationError, check, parse_response

NAME = "test_bill_2026"
HD1 = "2026_HB2306_HD1"
HB1850 = "2026_HB1850_HD2"


def recorded(case: str) -> dict:
    return parse_response(ex.response_text(case))


def section(note: str, title: str) -> str:
    lines = note.splitlines()
    start = lines.index(f"## {title}") + 1
    end = next((i for i in range(start, len(lines)) if lines[i].startswith("## ")), len(lines))
    return "\n".join(lines[start:end])


def _bill_file(tmp_path, case):
    p = tmp_path / f"{case}.txt"
    p.write_text(ex.bill_text(case))
    return p


# ---- recordings ------------------------------------------------------------------------------


def test_ten_cases_are_recorded_and_meet_expectations():
    assert len(ex.CASES) == 10
    for case in ex.CASES:
        res = check(recorded(case), ex.bill_text(case), NAME)
        failed = [e.label for e in ex.evaluate(case, res).expectations if not e.ok]
        assert not failed, (case, failed)


def test_backtest_expectations():
    hb1850 = check(recorded(HB1850), ex.bill_text(HB1850), NAME)
    assert hb1850.verdict == "partial"
    assert hb1850.spec == {"first_year": 2027, "income_tax": {"capital_gains_rate": "ordinary"}}
    cg = next(p for p in hb1850.provisions if "income_tax.capital_gains_rate" in p["spec_keys"])
    assert cg["approximate"] and "home" in cg["approximation"].lower()
    hb1147 = check(recorded("2026_HB1147_HD1"), ex.bill_text("2026_HB1147_HD1"), NAME)
    assert hb1147.spec["credits"] == {"eitc_percent": 40}
    for case in ("2026_HB2007_SD1", "2026_HB476_SD1"):
        res = check(recorded(case), ex.bill_text(case), NAME)
        assert (res.verdict, res.spec) == ("out_of_scope", None)
    assert any(p["blank"] for p in check(recorded("2026_HB476_SD1"), ex.bill_text("2026_HB476_SD1"),
                                         NAME).provisions)


# ---- a spec that sets nothing is no spec -------------------------------------------------------

STUBS = [{}, {"first_year": 2027}, {"first_year": 2027, "credits": {}},
         {"first_year": 2027, "income_tax": {}}, {"first_year": 2027, "income_tax": {}, "credits": {}}]


@pytest.mark.parametrize("stub", STUBS)
def test_stub_spec_is_no_spec(stub):
    r = recorded("2024_HB2404_SD1")
    r["spec"] = stub
    res = check(r, ex.bill_text("2024_HB2404_SD1"), NAME)
    assert res.spec is None and res.verdict == "out_of_scope" and res.problems == []


def test_stub_spec_with_a_modeled_provision_is_still_out_of_scope_not_invalid():
    r = recorded(HD1)
    r["spec"] = {"first_year": 2027, "credits": {}}
    res = check(r, ex.bill_text(HD1), NAME)
    assert res.spec is None and res.verdict == "out_of_scope" and res.problems == []


def test_spec_with_one_leaf_is_still_a_spec():
    r = recorded("2026_HB1147_HD1")
    assert check(r, ex.bill_text("2026_HB1147_HD1"), NAME).spec is not None


@pytest.mark.parametrize("stub", [{"first_year": 2027}, {"first_year": 2027, "credits": {}}])
def test_cli_stub_spec_writes_no_spec_file(tmp_path, stub):
    case = "2024_HB2404_SD1"
    r = recorded(case)
    r["spec"] = stub
    out = tmp_path / "o"
    code = main(["translate", str(_bill_file(tmp_path, case)), "--out", str(out)],
                runner=lambda prompt: json.dumps(r))
    assert code == 0
    assert sorted(p.name for p in out.iterdir()) == ["hb2404_sd1_2024.translation.json"]
    assert json.loads((out / "hb2404_sd1_2024.translation.json").read_text())["verdict"] == "out_of_scope"


def test_cli_removes_a_stale_spec_when_the_new_draft_is_out_of_scope(tmp_path):
    case = "2024_HB2404_SD1"
    out = tmp_path / "o"
    out.mkdir()
    stale = out / "hb2404_sd1_2024.spec.yaml"
    stale.write_text("name: hb2404_sd1_2024\nfirst_year: 2027\n")
    code = main(["translate", str(_bill_file(tmp_path, case)), "--out", str(out)],
                runner=lambda prompt: ex.response_text(case))
    assert code == 0 and not stale.exists()
    assert (out / "hb2404_sd1_2024.translation.json").exists()


def test_cli_invalid_writes_no_spec_and_removes_a_stale_one(tmp_path):
    case = HD1
    r = recorded(case)
    r["provisions"][0]["quote"] = "not in the bill"
    out = tmp_path / "o"
    out.mkdir()
    stale = out / "hb2306_hd1_2026.spec.yaml"
    stale.write_text("name: hb2306_hd1_2026\nfirst_year: 2027\n")
    code = main(["translate", str(_bill_file(tmp_path, case)), "--out", str(out)],
                runner=lambda prompt: json.dumps(r))
    assert code == 1 and not stale.exists()


def test_cli_invalid_without_a_stale_spec_writes_none(tmp_path):
    case = HD1
    r = recorded(case)
    r["provisions"][0]["quote"] = "not in the bill"
    out = tmp_path / "o"
    main(["translate", str(_bill_file(tmp_path, case)), "--out", str(out)],
         runner=lambda prompt: json.dumps(r))
    assert [p.name for p in out.iterdir()] == ["hb2306_hd1_2026.translation.json"]


def test_cli_keeps_writing_a_spec_for_partial(tmp_path):
    case = HB1850
    out = tmp_path / "o"
    main(["translate", str(_bill_file(tmp_path, case)), "--out", str(out)],
         runner=lambda prompt: ex.response_text(case))
    assert (out / "hb1850_hd2_2026.spec.yaml").exists()


def test_translation_json_carries_approximation_fields(tmp_path):
    case = HB1850
    out = tmp_path / "o"
    main(["translate", str(_bill_file(tmp_path, case)), "--out", str(out)],
         runner=lambda prompt: ex.response_text(case))
    tr = json.loads((out / "hb1850_hd2_2026.translation.json").read_text())
    assert tr["verdict"] == "partial"
    for p in tr["provisions"]:
        assert isinstance(p["approximate"], bool) and isinstance(p["approximation"], str)
    cg = next(p for p in tr["provisions"] if p["approximate"])
    assert cg["approximation"].strip()


# ---- approximate: parsing ----------------------------------------------------------------------


def _raw(**prov_changes) -> str:
    r = json.loads(ex.response_text(HB1850))
    r["provisions"][0].update(prov_changes)
    return json.dumps(r)


def test_missing_approximation_fields_default_for_older_recordings():
    r = json.loads(ex.response_text(HD1))
    for p in r["provisions"]:
        p.pop("approximate", None)
        p.pop("approximation", None)
    parsed = parse_response(json.dumps(r))
    assert all(p["approximate"] is False and p["approximation"] == "" for p in parsed["provisions"])


def test_missing_approximation_alone_defaults_to_empty_when_not_approximate():
    r = json.loads(ex.response_text(HD1))
    del r["provisions"][0]["approximation"]
    assert parse_response(json.dumps(r))["provisions"][0]["approximation"] == ""


def test_approximate_true_needs_approximation_text():
    for text in ("", "   "):
        with pytest.raises(TranslationError, match="approximation"):
            parse_response(_raw(approximate=True, approximation=text))
    r = json.loads(ex.response_text(HB1850))
    del r["provisions"][0]["approximation"]
    with pytest.raises(TranslationError, match="approximation"):
        parse_response(json.dumps(r))


def test_approximate_only_on_a_modeled_provision():
    with pytest.raises(TranslationError, match="only allowed on a modeled provision"):
        parse_response(_raw(modeled=False, spec_keys=[], approximate=True, approximation="rough"))


def test_approximation_text_without_the_flag_is_rejected():
    with pytest.raises(TranslationError, match="approximate.*false"):
        parse_response(_raw(approximate=False, approximation="rough"))


@pytest.mark.parametrize("key, value", [("approximate", "yes"), ("approximate", 1),
                                        ("approximation", None), ("approximation", 3)])
def test_approximation_field_types(key, value):
    with pytest.raises(TranslationError, match=key):
        parse_response(_raw(**{key: value}))


def test_a_bad_approximate_answer_goes_through_the_retry_path():
    from fiscal_notes.translator import translate_bill

    answers = [_raw(approximate=True, approximation=""), ex.response_text(HB1850)]
    prompts = []

    def runner(prompt):
        prompts.append(prompt)
        return answers.pop(0)

    t = translate_bill(ex.bill_text(HB1850), NAME, runner)
    assert (t.result.verdict, t.attempts) == ("partial", 2)
    assert "could not be parsed" in prompts[1] and "approximation" in prompts[1]


# ---- approximate: verdict ----------------------------------------------------------------------


def _modeled_only(case=HD1) -> dict:
    r = recorded(case)
    r["provisions"] = [p for p in r["provisions"] if p["modeled"]]
    return r


def test_verdict_full_without_approximation():
    assert check(_modeled_only(), ex.bill_text(HD1), NAME).verdict == "full"


def test_approximate_provision_caps_the_verdict_at_partial():
    r = _modeled_only()
    r["provisions"][0].update(approximate=True, approximation="rounds a phase-in, estimate too high")
    assert check(r, ex.bill_text(HD1), NAME).verdict == "partial"


def test_hb1850_is_partial_only_because_of_the_approximation():
    r = recorded(HB1850)
    r["provisions"] = [p for p in r["provisions"] if p["modeled"]]
    assert len(r["provisions"]) == 1 and r["provisions"][0]["approximate"]
    assert check(r, ex.bill_text(HB1850), NAME).verdict == "partial"
    r["provisions"][0].update(approximate=False, approximation="")
    assert check(r, ex.bill_text(HB1850), NAME).verdict == "full"


# ---- approximate: note -------------------------------------------------------------------------

APPROX = "Gains on a principal home keep 7.25% in the bill; the spec taxes them as ordinary income, so the estimate is too high."


def _approx_translation(*, unscored: bool) -> dict:
    tr = c.brackets_translation(verdict="partial" if unscored else "full")
    tr["verdict"] = "partial"
    tr["provisions"][0].update(approximate=True, approximation=APPROX)
    return tr


def test_note_has_a_scored_approximately_section_between_scored_and_not_scored():
    note = render_note(_approx_translation(unscored=True), c.brackets_score())
    titles = [ln[3:] for ln in note.splitlines() if ln.startswith("## ")]
    assert titles == ["Bottom line", "Revenue by tax year ($ millions, change vs current law)",
                      "Who pays and who gains", "What is scored", "Scored approximately",
                      "What is NOT scored", "Reviewer notes", "Method and provenance"]
    approx = section(note, "Scored approximately")
    assert "Changes the bracket schedule" in approx and f"Approximation: {APPROX}" in approx
    assert "Changes the bracket schedule" not in section(note, "What is scored")
    assert "Changes the bracket schedule" not in section(note, "What is NOT scored")
    assert "A credit the model cannot express" in section(note, "What is NOT scored")


def test_note_without_approximation_has_no_such_section():
    note = render_note(c.brackets_translation(), c.brackets_score())
    assert "Scored approximately" not in note and "approximate" not in note.lower()


def test_note_bottom_line_and_verdict_words_when_only_approximate():
    note = render_note(_approx_translation(unscored=False), c.brackets_score())
    bottom = section(note, "Bottom line")
    assert "Part of the estimate is approximate" in bottom and "Scored approximately" in bottom
    assert "leaves out" not in bottom
    status = note.splitlines()[2]
    assert "scored only approximately" in status and "not scored" not in status
    assert "None: every provision" in section(note, "What is NOT scored")


def test_note_bottom_line_and_verdict_words_when_both():
    note = render_note(_approx_translation(unscored=True), c.brackets_score())
    bottom = section(note, "Bottom line")
    assert "Part of the estimate is approximate" in bottom
    assert "leaves out the provisions listed under \"What is NOT scored\"" in bottom
    assert "some provisions are not scored and some are scored only approximately" in note.splitlines()[2]


def test_note_verdict_words_unchanged_when_only_unscored():
    note = render_note(c.brackets_translation(verdict="partial"), c.brackets_score())
    assert "some provisions are not scored, listed below" in note.splitlines()[2]
    assert "approximate" not in section(note, "Bottom line")


def test_blank_approximate_provision_counts_as_not_scored():
    tr = _approx_translation(unscored=False)
    tr["provisions"][0]["blank"] = True
    note = render_note(tr, c.brackets_score())
    assert "Scored approximately" not in note
    assert "Changes the bracket schedule" in section(note, "What is NOT scored")


# ---- approximate: diff -------------------------------------------------------------------------


def _prov(approximate=False, approximation=""):
    return {"section": 2, "summary": "CG", "modeled": True, "spec_keys": ["income_tax.capital_gains_rate"],
            "blank": False, "approximate": approximate, "approximation": approximation,
            "applies_from_tax_year": 2027, "quote": "same quote"}


def test_diff_shows_approximate_flag_change():
    out = "\n".join(provision_changes([_prov()], [_prov(True, "too high")]))
    assert "approximate flag False -> True" in out
    assert "approximation text changed" in out and "too high" in out
    assert "scored approximately" in out


def test_diff_shows_approximation_text_change():
    out = "\n".join(provision_changes([_prov(True, "too high")], [_prov(True, "too low")]))
    assert '"too high" -> "too low"' in out
    assert "approximate flag" not in out


def test_diff_ignores_whitespace_only_approximation_change():
    out = provision_changes([_prov(True, "too  high")], [_prov(True, "too high")])
    assert out == ["Provisions: the same in both drafts.", ""]


def test_diff_old_drafts_without_the_fields_compare_equal_to_exact_ones():
    old = _prov()
    del old["approximate"], old["approximation"]
    assert provision_changes([old], [_prov()]) == ["Provisions: the same in both drafts.", ""]


def test_diff_shows_change_when_the_quote_pairing_is_one_to_one():
    new = _prov(True, "too high")
    new["quote"] = "different quote"
    out = "\n".join(provision_changes([_prov()], [new]))
    assert "approximate flag False -> True" in out and "quoted text differs" in out

