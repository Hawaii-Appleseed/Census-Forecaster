"""Scoring, the note, ``run`` and ``diff``: canned score results and fake runners only.

No test needs the simulator population (a local, uncommitted input), a model or the network. The
real end-to-end check is by hand: ``python -m fiscal_notes score`` against
``analyses/forecast_custom.py --spec`` on the same spec (see ``fiscal_notes/README.md``).
"""
from __future__ import annotations

import json
from pathlib import Path

import fn_canned as c
import pytest

from fiscal_notes import expectations as ex
from fiscal_notes.cli import main
from fiscal_notes.diff import provision_changes, render_diff, resolve_draft, spec_changes
from fiscal_notes.note import DRAFT, render_note, render_out_of_scope_note
from fiscal_notes.score import ScoreError, build_result, dumps, score_spec_file
from fiscal_notes.translator import parse_response


def section(note: str, title: str) -> str:
    """The text of the ``## title`` section."""
    body = note.split(f"## {title}\n", 1)[1]
    return body.split("\n## ", 1)[0]


# --------------------------------------------------------------------------- build_result


def test_build_result_sums_and_credits():
    r = c.hb2306_score()
    assert r["income_tax"]["mid"]["2029"] == 470.1
    assert r["income_tax"]["static_mid"]["2029"] == 472.1
    assert r["income_tax"]["low"]["2029"] == 465.1 and r["income_tax"]["high"]["2029"] == 475.1
    assert r["credits"]["total"]["2028"] == pytest.approx(-76.5)
    assert r["total"]["mid"]["2028"] == pytest.approx(230.3 - 76.5)
    assert r["five_year"]["income_tax"]["mid"] == pytest.approx(230.2 + 230.3 + 470.1 + 475.0 + 473.8)
    assert r["five_year"]["total"]["mid"] == pytest.approx(
        r["five_year"]["income_tax"]["mid"] + r["five_year"]["credits"]["total"])
    assert r["distribution"]["year"] == 2027
    assert len(r["distribution"]["quintile"]) == 5


def test_build_result_brackets_only_has_no_credits():
    r = c.brackets_score()
    assert r["credits"] is None and "credits" not in r["five_year"]
    assert r["total"] == r["income_tax"]


def test_build_result_missing_scenario_gives_none():
    from tax_modeler.reform.income_tax_spec import IncomeTaxSpec

    spec = IncomeTaxSpec.from_dict({"name": "x_2026", **c.brackets_spec_dict()})
    res = c.make_res()
    res["revenue"] = [row for row in res["revenue"] if row["scenario"] != "low"]
    r = build_result(spec, res, c.YEARS, 2027, c.PROVENANCE)
    assert r["income_tax"]["low"]["2027"] is None and r["five_year"]["total"]["low"] is None


def test_score_json_is_stable_and_sorted():
    r = c.hb2306_score()
    text = dumps(r)
    assert dumps(json.loads(text)) == text
    assert list(json.loads(text)) == sorted(json.loads(text))


def test_score_file_fails_clearly_without_population(tmp_path):
    spec = tmp_path / "s.spec.yaml"
    spec.write_text("name: s_2026\nfirst_year: 2027\nincome_tax:\n  top_rate: current_law\n")
    # a spec the model's own loader may reject is also a ScoreError, never a traceback
    with pytest.raises(ScoreError):
        score_spec_file(spec, population_dir=tmp_path / "nope")


def test_score_file_fails_clearly_without_credits_table(tmp_path, monkeypatch):
    monkeypatch.setenv("CF_RUNS_DIR", str(tmp_path / "runs"))
    spec = tmp_path / "s.spec.yaml"
    spec.write_text("name: s_2026\nfirst_year: 2027\ncredits:\n  eitc_percent: 40\n  food_excise: act163\n")
    pop = tmp_path / "pop"
    pop.mkdir()
    (pop / "population.npz").write_bytes(b"")
    with pytest.raises(ScoreError, match="working-family credits"):
        score_spec_file(spec, population_dir=pop)


def test_score_file_fails_clearly_without_population_file(tmp_path):
    spec = tmp_path / "s.spec.yaml"
    spec.write_text("name: s_2026\nfirst_year: 2027\ncredits:\n  eitc_percent: 40\n")
    with pytest.raises(ScoreError, match="no simulator population"):
        score_spec_file(spec, population_dir=tmp_path)


# --------------------------------------------------------------------------- note


def test_note_credits_changing_partial():
    note = render_note(c.hb2306_translation(), c.hb2306_score())
    assert note.startswith("# HB 2306 HD1 (2026): shadow fiscal note")
    assert DRAFT in note.splitlines()[2]
    assert "some provisions are not scored, listed below" in note.splitlines()[2]
    titles = [ln[3:] for ln in note.splitlines() if ln.startswith("## ")]
    assert titles == ["Bottom line", "Revenue by tax year ($ millions, change vs current law)",
                      "Who pays and who gains", "What is scored", "What is NOT scored",
                      "Reviewer notes", "Method and provenance"]
    bottom = section(note, "Bottom line")
    # mid total five-year: income tax 1,879.4 + credits -306.8 = 1,572.6
    assert "raises $1,572.6 million" in bottom
    assert "gain of $1,547.6 million to a gain of $1,597.6 million" in bottom   # 5-year LOW and HIGH
    assert "income tax is +1,879.4 and credits are -306.8" in bottom
    rev = section(note, "Revenue by tax year ($ millions, change vs current law)")
    assert "Income tax:" in rev and "Credits (EITC and food/excise credit)" in rev and "Total (income tax" in rev
    assert "| 2028 | -42.8 | -33.7 | -76.5 |" in rev
    assert "| 5-year | +1,889.4 | +1,854.4 | +1,879.4 | +1,904.4 |" in rev
    assert "| 5-year | +1,582.6 | +1,547.6 | +1,572.6 | +1,597.6 |" in rev
    who = section(note, "Who pays and who gains")
    assert "Q1 (bottom 20%)" in who and "$1M+" in who and "100,000" in who and "+100" in who
    # every unmodeled provision is listed under NOT scored, every modeled one under scored
    tr = c.hb2306_translation()
    not_scored = section(note, "What is NOT scored")
    scored = section(note, "What is scored")
    for p in tr["provisions"]:
        target = scored if p["modeled"] else not_scored
        assert p["summary"][:60] in target
        assert p["summary"][:60] not in (not_scored if p["modeled"] else scored)
    assert scored.count("> \"") == 2 and "on December 31, [2027,] 2032" in scored
    assert "leaves these provisions out" in not_scored
    assert tr["notes"][:60] in section(note, "Reviewer notes")
    prov = section(note, "Method and provenance")
    for needle in ("Act 24 (SLH 2026)", "2027-2031", "working-family credits run", "asymmetric",
                   "abc1234def", "b" * 64, "`sonnet`"):
        assert needle in prov


def test_note_brackets_only_full_costs_wording():
    note = render_note(c.brackets_translation(), c.brackets_score())
    assert "every fiscal provision is scored" in note.splitlines()[2]
    bottom = section(note, "Bottom line")
    assert "costs $150.0 million" in bottom and "raises" not in bottom
    assert "a cost of $149.0 million to a cost of $151.0 million" not in bottom
    assert "a cost of $155.0 million" in bottom and "a cost of $145.0 million" in bottom
    assert "leaves out" not in bottom
    assert "Credits (" not in note and "income tax is" not in bottom
    rev = section(note, "Revenue by tax year ($ millions, change vs current law)")
    assert "| 2027 | -8.0 | -11.0 | -10.0 | -9.0 |" in rev
    assert "| 5-year | -140.0 | -155.0 | -150.0 | -145.0 |" in rev
    assert "None: every provision" in section(note, "What is NOT scored")
    assert section(note, "Reviewer notes").strip() == "None."


def test_note_blank_modeled_provision_is_not_scored():
    tr = c.brackets_translation(verdict="partial")
    tr["provisions"][0]["blank"] = True
    note = render_note(tr, c.brackets_score())
    assert "Changes the bracket schedule" in section(note, "What is NOT scored")
    assert "the amount is left blank" in section(note, "What is NOT scored")
    assert "Changes the bracket schedule" not in section(note, "What is scored")


def test_note_zero_change_wording():
    from tax_modeler.reform.income_tax_spec import IncomeTaxSpec

    spec = IncomeTaxSpec.from_dict({"name": "canned_bill_2026", **c.brackets_spec_dict()})
    zero = build_result(spec, c.make_res(mid=(0.0,) * 5, spread=0.0), c.YEARS, 2027, c.PROVENANCE)
    note = render_note(c.brackets_translation(), zero)
    bottom = section(note, "Bottom line")
    assert "does not change revenue" in bottom and "is no change in each scenario" in bottom


def test_note_refuses_unscored_verdicts():
    for v in ("invalid", "out_of_scope"):
        with pytest.raises(ValueError):
            render_note({**c.brackets_translation(), "verdict": v}, c.brackets_score())


def test_out_of_scope_note_is_three_lines():
    tr = parse_response(ex.response_text("2024_HB2404_SD1"))
    translation = {"label": "HB 2404 SD1 (2024)", "verdict": "out_of_scope",
                   "provisions": tr["provisions"], "notes": tr["notes"]}
    note = render_out_of_scope_note(translation)
    lines = [ln for ln in note.splitlines() if ln.strip()]
    assert len(lines) == 3
    assert lines[0].startswith("# HB 2404 SD1 (2024)") and DRAFT in lines[1]
    assert "Nothing in this bill can be scored" in lines[1]
    for p in translation["provisions"]:
        assert f"section {p['section']}" in lines[2]
    assert "## Bottom line" not in note


# --------------------------------------------------------------------------- score / run commands


def _write_translation(d: Path, translation: dict) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    p = d / "canned_bill_2026.translation.json"
    p.write_text(json.dumps(translation))
    (d / "canned_bill_2026.spec.yaml").write_text("name: canned_bill_2026\nfirst_year: 2027\n")
    return d / "canned_bill_2026.spec.yaml"


def _boom(spec_path):
    raise AssertionError("the scorer must not be called")


def test_score_writes_note_and_json_beside_spec(tmp_path, capsys):
    spec = _write_translation(tmp_path / "d", c.brackets_translation())
    calls = []

    def scorer(path):
        calls.append(path)
        return c.brackets_score()

    assert main(["score", str(spec)], scorer=scorer) == 0
    assert calls == [spec]
    note = (spec.parent / "canned_bill_2026.note.md").read_text()
    assert DRAFT in note
    assert json.loads((spec.parent / "canned_bill_2026.score.json").read_text())["name"] == "canned_bill_2026"


def test_score_out_dir_and_translation_option(tmp_path):
    spec = _write_translation(tmp_path / "d", c.brackets_translation())
    other = tmp_path / "elsewhere.json"
    other.write_text(json.dumps(c.brackets_translation(verdict="partial")))
    out = tmp_path / "out"
    assert main(["score", str(spec), "--translation", str(other), "--out", str(out)],
                scorer=lambda p: c.brackets_score()) == 0
    assert "some provisions are not scored" in (out / "canned_bill_2026.note.md").read_text()


def test_score_refuses_invalid_translation(tmp_path, capsys):
    bad = {**c.brackets_translation(), "verdict": "invalid", "problems": ["quote is not verbatim"]}
    spec = _write_translation(tmp_path / "d", bad)
    assert main(["score", str(spec)], scorer=_boom) == 1
    assert "invalid" in capsys.readouterr().err
    assert not (spec.parent / "canned_bill_2026.note.md").exists()
    assert not (spec.parent / "canned_bill_2026.score.json").exists()


def test_score_missing_translation_exits_two(tmp_path, capsys):
    spec = tmp_path / "x.spec.yaml"
    spec.write_text("name: x_2026\nfirst_year: 2027\n")
    assert main(["score", str(spec)], scorer=_boom) == 2
    assert "translation file not found" in capsys.readouterr().err


def test_score_error_exits_one_without_writing(tmp_path, capsys):
    spec = _write_translation(tmp_path / "d", c.brackets_translation())

    def scorer(path):
        raise ScoreError("no simulator population in X")

    assert main(["score", str(spec)], scorer=scorer) == 1
    assert "no simulator population" in capsys.readouterr().err
    assert not (spec.parent / "canned_bill_2026.note.md").exists()


def _bill_file(tmp_path, case):
    p = tmp_path / f"{case}.txt"
    p.write_text(ex.bill_text(case))
    return p


def test_run_translates_scores_and_writes_note(tmp_path):
    case = "2026_HB2306_HD1"
    out = tmp_path / "out"
    seen = []

    def scorer(path):
        seen.append(path)
        return c.hb2306_score()

    code = main(["run", str(_bill_file(tmp_path, case)), "--out", str(out)],
                runner=lambda prompt: ex.response_text(case), scorer=scorer)
    assert code == 0
    assert seen == [out / "hb2306_hd1_2026.spec.yaml"]
    assert sorted(p.name for p in out.iterdir()) == [
        "hb2306_hd1_2026.note.md", "hb2306_hd1_2026.score.json", "hb2306_hd1_2026.spec.yaml",
        "hb2306_hd1_2026.translation.json"]
    note = (out / "hb2306_hd1_2026.note.md").read_text()
    assert "Verdict `partial`" in note and "raises $1,572.6 million" in note


def test_run_out_of_scope_writes_translation_and_short_note_only(tmp_path):
    case = "2024_HB2404_SD1"
    out = tmp_path / "o"
    code = main(["run", str(_bill_file(tmp_path, case)), "--out", str(out)],
                runner=lambda prompt: ex.response_text(case), scorer=_boom)
    assert code == 0
    assert sorted(p.name for p in out.iterdir()) == ["hb2404_sd1_2024.note.md",
                                                     "hb2404_sd1_2024.translation.json"]
    lines = [ln for ln in (out / "hb2404_sd1_2024.note.md").read_text().splitlines() if ln.strip()]
    assert len(lines) == 3


def test_run_invalid_exits_one_without_scoring(tmp_path, capsys):
    case = "2026_HB2306_HD1"
    r = parse_response(ex.response_text(case))
    r["provisions"][0]["quote"] = "not in the bill"
    out = tmp_path / "o"
    code = main(["run", str(_bill_file(tmp_path, case)), "--out", str(out)],
                runner=lambda prompt: json.dumps(r), scorer=_boom)
    assert code == 1
    assert "quote is not verbatim" in capsys.readouterr().out
    assert [p.name for p in out.iterdir() if p.suffix != ".yaml"] == ["hb2306_hd1_2026.translation.json"]


# --------------------------------------------------------------------------- diff


def _drafts(tmp_path, old, new):
    paths = []
    for tag, (tr, score) in (("old", old), ("new", new)):
        d = tmp_path / tag
        d.mkdir()
        (d / "bill.translation.json").write_text(json.dumps(tr))
        (d / "bill.score.json").write_text(dumps(score))
        paths.append(d / "bill")
    return paths


def test_diff_two_drafts(tmp_path, capsys):
    old_tr, new_tr = c.brackets_translation(verdict="partial"), c.brackets_translation(top="6.00")
    old = (old_tr, c.brackets_score(5.5, mid=(-10.0, -20.0, -30.0, -40.0, -50.0)))
    new = (new_tr, c.brackets_score(6.0, mid=(-5.0, -10.0, -15.0, -20.0, -25.0)))
    po, pn = _drafts(tmp_path, old, new)
    out = tmp_path / "diff.md"
    assert main(["diff", str(po), str(pn), "--out", str(out)]) == 0
    text = out.read_text()
    assert text == out.read_text() and text.strip() in capsys.readouterr().out
    assert "Verdict changed: `partial` -> `full`" in text
    # only the changed bracket rows, for each status
    assert text.count("| single | from 2027 | $14,400 | 5.5% | 6% |") == 1
    assert "$9,600" not in text and "$19,200" not in text
    assert "A credit the model cannot express" in text and "Provisions dropped" in text
    assert "| 2027 | -10.0 | -5.0 | +5.0 |" in text
    assert "| 5-year | -150.0 | -75.0 | +75.0 |" in text
    assert "| LOW | -155.0 | -80.0 | +75.0 |" in text and "| HIGH | -145.0 | -70.0 | +75.0 |" in text


def test_diff_accepts_score_json_paths(tmp_path):
    old = (c.brackets_translation(), c.brackets_score())
    po, pn = _drafts(tmp_path, old, old)
    assert resolve_draft(f"{po}.score.json") == resolve_draft(po)
    assert main(["diff", f"{po}.score.json", f"{pn}.score.json"]) == 0


def test_diff_identical_drafts_report_no_changes():
    tr, sc = c.brackets_translation(), c.brackets_score()
    text = render_diff(sc, tr, sc, tr)
    assert "Other settings: unchanged" in text and "Brackets: unchanged" in text
    assert "the same in both drafts" in text and "| 5-year | -150.0 | -150.0 | +0.0 |" in text


def test_diff_spec_settings_and_credits():
    old = c.hb2306_score()["spec"]
    new = json.loads(json.dumps(old))
    new["credits"]["eitc_percent"] = 30.0
    new["income_tax"]["standard_deduction"] = {"single": 5000, "head_of_household": 7000,
                                               "married_filing_jointly": 10000}
    text = "\n".join(spec_changes(old, new))
    assert "| `credits.eitc_percent` | 40.0 | 30.0 |" in text
    assert "`income_tax.standard_deduction`" in text


def test_diff_vintage_only_in_one_draft():
    old = c.hb2306_score()["spec"]
    new = json.loads(json.dumps(old))
    b = new["income_tax"].pop("brackets")
    new["income_tax"]["bracket_vintages"] = [{"from": 2027, "brackets": b}, {"from": 2029, "brackets": b}]
    assert "vintage from tax year 2029 is only in the new draft" in "\n".join(spec_changes(old, new))


def _prov(section, keys, quote, **kw):
    return {"section": section, "summary": f"s{section} {quote}", "modeled": bool(keys),
            "spec_keys": keys, "blank": False, "applies_from_tax_year": 2027, "quote": quote, **kw}


def test_provision_matching_by_section_and_keys():
    old = [_prov(1, ["income_tax.brackets"], "old wording"), _prov(2, [], "credit a")]
    new = [_prov(1, ["income_tax.brackets"], "new wording"), _prov(4, ["credits.eitc_percent"], "eitc")]
    text = "\n".join(provision_changes(old, new))
    assert "Provisions added in the new draft" in text and "section 4" in text
    assert "Provisions dropped from the old draft" in text and "section 2: s2 credit a" in text
    assert "changed" in text and "quoted text differs" in text


def test_provision_matching_unsure_lists_both_sides():
    old = [_prov(2, [], "alpha"), _prov(2, [], "beta")]
    new = [_prov(2, [], "gamma")]
    text = "\n".join(provision_changes(old, new))
    assert "could not be matched safely" in text
    assert text.count("old: section 2") == 2 and text.count("new: section 2") == 1


def test_provision_status_change_reported():
    old = [_prov(1, ["income_tax.brackets"], "same quote")]
    new = [_prov(1, ["income_tax.brackets"], "same quote", blank=True)]
    assert "blank flag False -> True" in "\n".join(provision_changes(old, new))
