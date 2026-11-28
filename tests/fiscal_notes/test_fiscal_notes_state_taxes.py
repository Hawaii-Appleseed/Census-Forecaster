"""The state-tax fiscal note path, offline: recorded model answers, tampered copies, fake runners."""
from __future__ import annotations

import copy
import json

import pytest

from fiscal_notes import state_expectations as ex
from fiscal_notes import state_taxes as st
from fiscal_notes.cli import main
from fiscal_notes.translator import parse_response

CASES = list(ex.CASES)


def recorded(case: str) -> dict:
    return parse_response(ex.response_text(case))


# ---- recorded responses ----------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES)
def test_recorded_response_meets_expectations(case):
    result = st.check(recorded(case), ex.bill_text(case))
    assert result.problems == []
    failed = [label for label, ok in ex.evaluate(case, result) if not ok]
    assert failed == []


def test_prompt_names_every_spec_key():
    prompt = st.PROMPT_PATH.read_text()
    for key in ("general_excise.exempt", "general_excise.rates", "general_excise.repeal_exemptions",
                "transient_accommodations.rate_change_points", "cigarettes.cents_per_cigarette",
                "fuel.cents_per_gallon", "fuel.dollars_per_barrel", "liquor.dollars_per_gallon",
                "rental_vehicle.dollars_per_day"):
        assert key in prompt, key
    for cat in st.EXEMPT_CATEGORIES:
        assert f'"{cat}"' in prompt


# ---- the checks never trust the model -------------------------------------------------------


def _invalid(response: dict, case: str) -> list[str]:
    result = st.check(response, ex.bill_text(case))
    assert result.verdict == "invalid", result.problems
    return result.problems


def test_figure_not_in_the_bill_is_refused():
    r = copy.deepcopy(recorded("2026_HB1085_Introduced"))
    r["spec"]["cigarettes"]["cents_per_cigarette"] = 26
    assert any("26 cents" in p for p in _invalid(r, "2026_HB1085_Introduced"))


def test_repealed_figure_does_not_count():
    # 15.00 cents is in the bill only inside [brackets]: the rate it repeals.
    r = copy.deepcopy(recorded("2026_HB1085_Introduced"))
    r["spec"]["cigarettes"]["cents_per_cigarette"] = 15
    assert any("15 cents" in p for p in _invalid(r, "2026_HB1085_Introduced"))


def test_quote_must_be_verbatim():
    r = copy.deepcopy(recorded("2026_HB1585_Introduced"))
    r["provisions"][0]["quote"] = "groceries shall be exempt from all taxes forever"
    assert any("not verbatim" in p for p in _invalid(r, "2026_HB1585_Introduced"))


def test_unknown_category_and_unclaimed_key_are_refused():
    r = copy.deepcopy(recorded("2026_HB1585_Introduced"))
    r["spec"]["general_excise"]["exempt"] = ["diapers"]
    assert any("spec is not valid" in p for p in _invalid(r, "2026_HB1585_Introduced"))
    r = copy.deepcopy(recorded("2026_HB1585_Introduced"))
    r["spec"]["rental_vehicle"] = {"dollars_per_day": 7}
    problems = _invalid(r, "2026_HB1585_Introduced")
    assert any("rental_vehicle.dollars_per_day" in p and "not claimed" in p for p in problems)


def test_word_numbers_are_found():
    text = "shall be increased by one percentage point; a tax of four and one-half per cent; 21.00 cents"
    assert st._number_before(text, 1, st._PERCENT)
    assert st._number_before(text, 4.5, st._PERCENT)
    assert st._number_before(text, 21, r"cents?")
    assert not st._number_before(text, 5, st._PERCENT)
    assert not st._number_before(text, 1, r"cents?")          # "21.00 cents" is not "1 cent"


# ---- the spec ----------------------------------------------------------------------------------


def test_validate_spec():
    spec = st.validate_spec({"effective": "2027-07-01", "general_excise": {"rates": {"retailing": 4.5}},
                             "transient_accommodations": {"rate_change_points": 1}})
    assert spec["general_excise"]["rates"] == {"retailing": 4.5}
    for bad in ({"effective": "2027-07-01"},
                {"general_excise": {"exempt": ["yachts"]}},
                {"general_excise": {"rates": {"retailing": 45}}},
                {"transient_accommodations": {"rate": 11, "rate_change_points": 1}},
                {"cigarettes": {"dollars_per_pack": 5}},
                {"effective": "next July", "cigarettes": {"cents_per_cigarette": 21}},
                {"income_tax": {"personal_exemption": 2000}}):
        with pytest.raises(st.SpecError):
            st.validate_spec(bad)


# ---- scoring -----------------------------------------------------------------------------------


def test_score_prorates_the_first_year_and_adds_up():
    from state_revenue import excise

    s = st.score_spec({"effective": "2027-01-01", "cigarettes": {"cents_per_cigarette": 21}})
    assert s["fiscal_years"] == [2027, 2028, 2029, 2030, 2031]
    full = excise.score_cigarettes(0.21, 2028).state
    assert s["state"][2028] == pytest.approx(full)
    half = excise.score_cigarettes(0.21, 2027).state
    assert s["state"][2027] == pytest.approx(half * 181 / 365)
    assert s["state_total"] == pytest.approx(sum(s["state"].values()))
    assert any("FY2027 counts" in a for a in s["assumptions"])


def test_past_or_missing_date_is_assumed_and_said():
    for eff in (None, "2026-01-01"):
        s = st.score_spec({"effective": eff, "general_excise": {"exempt": ["groceries"]}})
        assert s["fiscal_years"][0] == 2027 and s["effective"] == "2026-07-01"
        assert any("assumed" in a for a in s["assumptions"])
        assert s["state"][2027] < 0 and s["county"][2027] < 0


def test_score_components():
    from state_revenue import excise, general_excise

    s = st.score_spec({"effective": "2027-07-01",
                       "general_excise": {"rates": {"retailing": 4.5}, "repeal_exemptions": ["113"],
                                          "exempt": ["medical_services"]},
                       "transient_accommodations": {"rate_change_points": 1},
                       "fuel": {"cents_per_gallon": {"gasoline": 21}, "dollars_per_barrel": 2.05},
                       "liquor": {"dollars_per_gallon": {"beer": 1.03}},
                       "rental_vehicle": {"dollars_per_day": 8}})
    c = s["components"]
    fy = 2028
    assert c["general_excise.rates"]["state"][fy] == pytest.approx(
        general_excise.score_rate_change({"retailing": 0.045}, fy).state)
    assert c["general_excise.repeal_exemptions.113"]["state"][fy] > 0
    assert c["general_excise.exempt.medical_services"]["state"][fy] < 0
    now = excise.rate_in("transient_accommodations", fy)
    assert c["transient_accommodations"]["state"][fy] == pytest.approx(excise.score_tat(now + 0.01, fy).state)
    assert c["transient_accommodations"]["borne_by"]["visitors"] > 0.6
    assert c["fuel"]["state"][fy] == pytest.approx(
        excise.score_fuel({"gasoline": 0.21, "ert_barrel": 2.05}, fy).state)
    assert c["rental_vehicle"]["state"][fy] == pytest.approx(excise.score_rental_vehicle(8, fy).state)
    assert s["state"][fy] == pytest.approx(sum(x["state"][fy] for x in c.values()))


# ---- the note and the CLI --------------------------------------------------------------------------


def test_note_sections_and_json_round_trip():
    case = "2026_HB1085_Introduced"
    result = st.check(recorded(case), ex.bill_text(case))
    translation = {"label": "HB 1085 (2026)", "verdict": result.verdict, "provisions": result.provisions,
                   "notes": result.notes, "bill_sha256": "0" * 64, "model": "recorded"}
    score = json.loads(st.dumps(st.score_spec(result.spec)))       # as read back from disk
    note = st.render_note(translation, score)
    for heading in ("# Fiscal note (draft): HB 1085 (2026)", "## Bottom line", "## Revenue by fiscal year",
                    "## Scored approximately", "## What is NOT scored", "## Method"):
        assert heading in note
    assert "DRAFT, not reviewed" in note and "Cigarette tax" in note
    assert "assumed" in note                                       # the bill's dates have passed
    empty = st.render_note({**translation, "verdict": "out_of_scope"}, None)
    assert "Nothing in this bill can be scored" in empty


def test_cli_state_run_writes_spec_score_and_note(tmp_path):
    case = "2026_HB1585_Introduced"
    bill = ex.BILLS_DIR / f"{case}.txt"
    rc = main(["state-run", str(bill), "--out", str(tmp_path)], runner=lambda prompt: ex.response_text(case))
    assert rc == 0
    names = {p.name for p in tmp_path.iterdir()}
    assert names == {"hb1585_introduced_2026.state_spec.yaml", "hb1585_introduced_2026.state_translation.json",
                     "hb1585_introduced_2026.state_score.json", "hb1585_introduced_2026.state_note.md"}
    score = json.loads((tmp_path / "hb1585_introduced_2026.state_score.json").read_text())
    assert score["state_total"] < 0
    rc = main(["state-score", str(tmp_path / "hb1585_introduced_2026.state_spec.yaml")])
    assert rc == 0


def test_cli_state_run_out_of_scope_and_invalid(tmp_path):
    case = "2026_HB2156_Introduced"
    rc = main(["state-run", str(ex.BILLS_DIR / f"{case}.txt"), "--out", str(tmp_path)],
              runner=lambda prompt: ex.response_text(case))
    assert rc == 0
    assert not list(tmp_path.glob("*.state_spec.yaml")) and not list(tmp_path.glob("*.state_score.json"))
    assert "Nothing in this bill can be scored" in next(tmp_path.glob("*.state_note.md")).read_text()
    rc = main(["state-run", str(ex.BILLS_DIR / f"{case}.txt"), "--out", str(tmp_path / "bad")],
              runner=lambda prompt: "not json")
    assert rc == 1


def test_tat_note_gives_the_sourced_response_range():
    score = st.score_spec({"effective": "2027-07-01", "transient_accommodations": {"rate_change_points": 1}})
    r = score["components"]["transient_accommodations"]["with_response"]
    assert r["low"] == pytest.approx(score["state_total"]) and r["high"] < r["mid"] < r["low"]
    translation = {"label": "TEST (2027)", "verdict": "full", "provisions": [], "notes": "",
                   "bill_sha256": "0" * 64, "model": "x"}
    note = st.render_note(translation, json.loads(st.dumps(score)))
    assert "## If buyers respond" in note and "Fujii, Khaled and Mak 1988" in note
    cig = st.score_spec({"cigarettes": {"cents_per_cigarette": 21}})
    r = cig["components"]["cigarettes"]["with_response"]
    assert r["high"] < r["mid"] < r["low"] < cig["state_total"]      # every sourced response is below static
    assert "Community Preventive Services Task Force" in st.render_note(translation, cig)
    plain = st.render_note(translation, st.score_spec({"rental_vehicle": {"dollars_per_day": 8}}))
    assert "If buyers respond" not in plain
