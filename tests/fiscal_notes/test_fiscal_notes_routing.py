"""Routing a bill to the income tax path, the state-tax path or both, offline."""
from __future__ import annotations

import json
from pathlib import Path

import fn_canned as c
import hub_contract as contract
import pytest

from fiscal_notes import expectations as ex
from fiscal_notes import hub, routing
from fiscal_notes import state_expectations as sx
from fiscal_notes import state_taxes as st
from fiscal_notes.hub import Deps, HubClient, process_queue
from fiscal_notes.translator import CheckResult, Translation, translate_bill

INCOME_FOR_STATE = Path(__file__).parent / "responses_income_for_state"
CREDS = ("client-id-value", "s3cret")


@pytest.fixture(autouse=True)
def _no_real_calls(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("a test reached subprocess.run")

    monkeypatch.setattr(hub.subprocess, "run", boom)
    monkeypatch.delenv("CF_ACCESS_CLIENT_ID", raising=False)
    monkeypatch.delenv("CF_ACCESS_CLIENT_SECRET", raising=False)


class FakeHub:
    def __init__(self, queue):
        self.queue, self.posts = queue, []

    def __call__(self, method, url, headers, body, timeout):
        if method == "GET":
            return 200, "application/json", json.dumps({"requests": self.queue})
        sent = json.loads(body)
        assert contract.validate_post(sent) == [], contract.validate_post(sent)
        self.posts.append(sent)
        return 200, "application/json", json.dumps({"ok": True})


def run(case, tmp_path, *, income=None, **deps_kw):
    """Process one queued request for a state-tax fixture bill; returns (final post, work dir)."""
    text = sx.bill_text(case)
    recorded = INCOME_FOR_STATE / f"{case}.json"
    income = income or (lambda bill, name: translate_bill(bill, name, lambda prompt: recorded.read_text()))
    deps = Deps(fetch=lambda y, b, d: {"text": text, "url": "https://example/x.HTM", "title": "T", "dir_year": y},
                translate=income, score=lambda spec_path: c.hb2306_score(), commit=lambda: "abc1234",
                model="sonnet",
                **{"route_state": True,
                   "translate_state": lambda bill: st.translate_bill(bill, lambda prompt: sx.response_text(case)),
                   **deps_kw})
    fake = FakeHub([{"id": "fn_1", "bill": case.split("_")[1], "year": 2026, "draft": "",
                     "by": "a@hiappleseed.org", "at": "2026-11-28T00:00:00.000Z"}])
    outcomes = process_queue(HubClient("https://hub.example", credentials=CREDS, transport=fake), deps,
                             work_root=tmp_path)
    return fake.posts[-1], outcomes[0].work_dir


# ---- the gate ------------------------------------------------------------------------------


def test_gate_is_the_chapters_a_bill_amends():
    assert all(routing.mentions_state_taxes(sx.bill_text(case)) for case in sx.CASES)
    # income tax bills cite "general excise tax license number" and "chapter 237" without amending either
    assert not any(routing.mentions_state_taxes(ex.bill_text(case)) for case in ex.CASES)
    assert routing.mentions_state_taxes("SECTION 1. Section 237D-2, Hawaii Revised Statutes, is amended to read")
    assert routing.mentions_state_taxes("Section 245-3(a), Hawaii Revised Statutes, is amended")
    assert not routing.mentions_state_taxes("Section 235-51, Hawaii Revised Statutes, is amended")


# ---- one path or the other ---------------------------------------------------------------------


def test_state_only_bill_posts_the_state_note(tmp_path):
    body, work = run("2026_HB604_Introduced", tmp_path)
    assert body["status"] == "ready"
    r = body["result"]
    assert r["verdict"] == "partial"
    assert "Transient accommodations tax rate" in r["markdown"] and "If visitors respond" in r["markdown"]
    h = r["headline"]
    assert h["first_year"] == 2027 and h["last_year"] == 2031
    assert h["five_year_mid"] == pytest.approx(r["score"]["state_total"] / 1e3, abs=0.06)
    assert h["five_year_low"] < h["five_year_mid"] == h["five_year_high"]     # visitors may respond
    assert r["counts"]["approximate"] == 1
    assert (work / "hb604_introduced_2026.state_note.md").exists()


def test_income_path_invalid_but_state_path_scores(tmp_path):
    # HB 959: the recorded income tax answer fails its figure check; the grocery exemption still scores.
    body, _ = run("2026_HB959_Introduced", tmp_path)
    assert body["status"] == "ready"
    assert "General excise exemption: groceries" in body["result"]["markdown"]
    assert body["result"]["headline"]["five_year_mid"] < 0


def test_routing_off_leaves_the_income_path_alone(tmp_path):
    body, work = run("2026_HB604_Introduced", tmp_path, route_state=False)
    assert body["status"] == "ready" and body["result"]["verdict"] == "out_of_scope"
    assert not list(work.glob("*.state_*"))


def test_state_path_failure_never_sinks_the_request(tmp_path):
    def broken(bill):
        raise RuntimeError("model unavailable")

    body, work = run("2026_HB604_Introduced", tmp_path, translate_state=broken)
    assert body["status"] == "ready" and body["result"]["verdict"] == "out_of_scope"
    assert "model unavailable" in (work / "hb604_introduced_2026.state_path.txt").read_text()


def test_state_path_with_nothing_to_score_changes_nothing(tmp_path):
    body, work = run("2026_HB2156_Introduced", tmp_path,
                     income=lambda bill, name: Translation(
                         CheckResult("out_of_scope", None, [], "", []), ["{}"], 1))
    assert body["result"]["verdict"] == "out_of_scope" and body["result"]["headline"] is None
    assert "out_of_scope" in (work / "hb2156_introduced_2026.state_path.txt").read_text()


# ---- both paths ----------------------------------------------------------------------------------


def _income_scored(bill, name):
    """A hand-built income tax translation for HB 959: one exactly scored provision in section 9."""
    quote = "RELATING TO TAXATION"
    assert quote in bill.upper()
    prov = {"section": 9, "summary": "Raises the standard deduction", "modeled": True,
            "spec_keys": ["income_tax.standard_deduction"], "blank": False, "approximate": False,
            "approximation": "", "applies_from_tax_year": 2027, "quote": bill[bill.upper().index(quote):][:20]}
    other = {**prov, "section": 2, "summary": "Exempts groceries from the general excise tax", "modeled": False,
             "spec_keys": []}
    spec = {"first_year": 2027, "income_tax": {"standard_deduction": {
        "single": 8800, "head_of_household": 12848, "married_filing_jointly": 17600}}}
    return Translation(CheckResult("partial", spec, [prov, other], "income notes", []), ["{}"], 1)


def test_bill_scored_by_both_paths_gets_one_combined_note(tmp_path):
    body, work = run("2026_HB959_Introduced", tmp_path, income=_income_scored)
    assert body["status"] == "ready"
    r = body["result"]
    md = r["markdown"]
    assert md.startswith("# Fiscal note (draft): HB 959 Introduced (2026)")
    assert "## Combined bottom line" in md and "# Part 1: income tax" in md
    assert "# Part 2: general excise, transient accommodations and excise taxes" in md
    assert md.count("# Fiscal note (draft)") == 1
    income_mid = c.hb2306_score()["five_year"]["total"]["mid"]
    state_mid = r["score"]["state_taxes"]["state_total"] / 1e3
    assert r["headline"]["five_year_mid"] == pytest.approx(income_mid + state_mid, abs=0.11)
    assert r["verdict"] == "partial"
    paths = {p["path"] for p in r["translation"]["provisions"] if p["modeled"]}
    assert paths == {"income_tax", "state_taxes"}
    # the grocery exemption, unscored on the income side, is not also listed as unscored
    grocery = [p for p in r["translation"]["provisions"] if "grocer" in p["summary"].lower()]
    assert grocery and all(p["modeled"] for p in grocery if p["section"] == grocery[0]["section"])
    assert "income notes" in r["translation"]["notes"]
    assert (work / "hb959_introduced_2026.combined_note.md").read_text() == md


def test_merge_provisions_and_headlines():
    def p(section, modeled, summary="x", **kw):
        return {"section": section, "summary": summary, "modeled": modeled, "blank": False, **kw}

    merged = routing.merge_provisions([p(1, True), p(2, False), p(3, False)],
                                      [p(1, False), p(2, True), p(3, False), p(4, False)])
    assert [(m["section"], m["modeled"], m["path"]) for m in merged] == [
        (1, True, "income_tax"), (2, True, "state_taxes"), (3, False, "income_tax"), (4, False, "state_taxes")]
    assert routing.combined_verdict("full", "full") == "full"
    assert routing.combined_verdict("full", "partial") == "partial"
    head = routing.combined_headline(
        {"five_year_mid": -100.0, "five_year_low": -120.0, "five_year_high": -80.0, "first_year": 2027,
         "last_year": 2031},
        {"five_year_mid": 50.0, "five_year_low": 40.0, "five_year_high": 50.0, "first_year": 2027,
         "last_year": 2031})
    assert head == {"five_year_mid": -50.0, "five_year_low": -80.0, "five_year_high": -30.0,
                    "first_year": 2027, "last_year": 2031}
