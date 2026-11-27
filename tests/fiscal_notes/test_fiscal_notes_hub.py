"""The hub client, offline: a fake transport that checks every body against the contract, fakes for
fetching, translating and scoring. No network, no model, no Keychain."""
from __future__ import annotations

import copy
import json
import os
import subprocess
from pathlib import Path

import fn_canned as c
import hub_contract as contract
import pytest

from fiscal_notes import expectations as ex
from fiscal_notes import hub, hub_result
from fiscal_notes.cli import main
from fiscal_notes.hub import Deps, HubClient, NetworkError, process_queue
from fiscal_notes.translator import parse_response, translate_bill

EXAMPLES = Path(__file__).resolve().parents[2] / "fiscal_notes" / "examples"
SECRET = "s3cret-token-value"
CREDS = ("client-id-value", SECRET)


@pytest.fixture(autouse=True)
def _no_real_credentials(monkeypatch):
    """Nothing here may read the real environment token, the env file or the Keychain."""
    monkeypatch.delenv("CF_ACCESS_CLIENT_ID", raising=False)
    monkeypatch.delenv("CF_ACCESS_CLIENT_SECRET", raising=False)

    def boom(*a, **k):
        raise AssertionError("a test reached subprocess.run in fiscal_notes.hub")

    monkeypatch.setattr(hub.subprocess, "run", boom)
    monkeypatch.setattr(hub, "ENV_FILE", Path("/nonexistent/fiscal-notes/hub.env"))


# ---- fakes ---------------------------------------------------------------------------------


class FakeHub:
    """The hub's two machine routes. Every POST body is validated against the contract."""

    def __init__(self, queue, reject=None):
        self.queue = queue
        self.reject = reject or {}     # (id, status) -> http status to answer
        self.posts: list[dict] = []
        self.calls: list[tuple] = []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url))
        assert url == "https://hub.example/api/events-sync/fiscal-notes"
        assert headers["CF-Access-Client-Id"] == CREDS[0]
        assert headers["CF-Access-Client-Secret"] == SECRET
        assert headers["User-Agent"].startswith("Census-Forecaster-fiscal-notes")
        if method == "GET":
            assert body is None
            return 200, "application/json", json.dumps({"requests": self.queue})
        sent = json.loads(body)
        assert contract.validate_post(sent) == []
        self.posts.append(sent)
        status = self.reject.get((sent["id"], sent["status"]))
        if status:
            return status, "application/json", json.dumps({"error": f"refused with {status}"})
        return 200, "application/json", json.dumps({"id": sent["id"], "status": sent["status"]})

    def statuses(self, rid):
        return [p["status"] for p in self.posts if p["id"] == rid]


def req(rid="fn_1", bill="HB2306", year=2026, draft="HD1", at="2026-11-26T23:00:00.000Z"):
    return {"id": rid, "bill": bill, "year": year, "draft": draft, "by": "a@hiappleseed.org", "at": at}


def client_for(fake):
    return HubClient("https://hub.example", credentials=CREDS, transport=fake)


def fetcher_for(case):
    return lambda year, bill, draft: {"text": ex.bill_text(case), "url": "https://example/x.HTM",
                                      "title": "T", "dir_year": year}


def translator_for(case):
    return lambda text, name: translate_bill(text, name, lambda prompt: ex.response_text(case))


def hb1850_score():
    r = parse_response(ex.response_text("2026_HB1850_HD2"))
    return c.build_result(c._spec(r["spec"]), c.make_res(), c.YEARS, 2027, c.PROVENANCE)


def deps_for(case, score=None, **kw):
    return Deps(fetch=fetcher_for(case), translate=translator_for(case),
                score=score or (lambda spec_path: c.hb2306_score()), commit=lambda: "abc1234def",
                model="sonnet", **kw)


def run(fake, deps, tmp_path, **kw):
    return process_queue(client_for(fake), deps, work_root=tmp_path / "work", **kw)


# ---- the Result ----------------------------------------------------------------------------


def test_result_for_the_hb2306_example():
    translation = json.loads((EXAMPLES / "hb2306_hd1_2026.translation.json").read_text())
    score = json.loads((EXAMPLES / "hb2306_hd1_2026.score.json").read_text())
    note = (EXAMPLES / "hb2306_hd1_2026.note.md").read_text()
    r = hub_result.build_result(translation, score, note, commit="46d33bb", model="sonnet",
                                bill_sha256="a" * 64)
    assert contract.validate_result(r) == []
    assert r["verdict"] == "partial" and r["label"] == "HB 2306 HD1 (2026)"
    assert r["headline"] == {"five_year_mid": 1572.5, "five_year_low": 1534.6,
                             "five_year_high": 1605.0, "first_year": 2027, "last_year": 2031}
    assert r["counts"] == {"scored": 2, "approximate": 0, "not_scored": 2}
    assert r["markdown"] == note and r["score"] == score and r["translation"] == translation
    assert r["tool"] == {"commit": "46d33bb", "model": "sonnet", "bill_sha256": "a" * 64}


def test_negative_headline_and_no_negative_zero():
    score = c.brackets_score()
    r = hub_result.build_result(c.brackets_translation(), score, "n", commit="x", model="m",
                                bill_sha256="0" * 64)
    assert r["headline"]["five_year_mid"] < 0  # a revenue loss is negative
    score = copy.deepcopy(score)
    score["five_year"]["total"] = {"low": -0.01, "mid": -0.04, "high": 0.0}
    h = hub_result.headline(score)
    assert str(h["five_year_mid"]) == "0.0" and str(h["five_year_low"]) == "0.0"


def test_out_of_scope_result_has_no_headline_and_no_score():
    tr = {"name": "x", "label": "HB 2404 SD1 (2024)", "verdict": "out_of_scope", "provisions": [
        {"section": 1, "summary": "s", "modeled": False, "blank": False, "approximate": False}],
        "notes": "", "problems": [], "model": "sonnet", "attempts": 1}
    r = hub_result.build_result(tr, c.hb2306_score(), "note", commit="x", model="sonnet",
                                bill_sha256="1" * 64)  # a stray score is never sent
    assert contract.validate_result(r) == []
    assert r["headline"] is None and r["score"] is None
    assert r["counts"] == {"scored": 0, "approximate": 0, "not_scored": 1}


def test_counts_partition_scored_approximate_not_scored():
    provs = [
        {"modeled": True, "blank": False, "approximate": False},   # scored
        {"modeled": True, "blank": False, "approximate": True},    # approximate
        {"modeled": True, "blank": True, "approximate": False},    # blank amount: not scored
        {"modeled": True, "blank": True, "approximate": True},     # blank beats approximate
        {"modeled": False, "blank": False, "approximate": False},  # not modeled
    ]
    assert hub_result.counts(provs) == {"scored": 1, "approximate": 1, "not_scored": 3}
    # and the recorded HB 1850 answer has approximate provisions
    recorded = parse_response(ex.response_text("2026_HB1850_HD2"))["provisions"]
    got = hub_result.counts(recorded)
    assert got["approximate"] >= 1 and sum(got.values()) == len(recorded)


def test_note_over_the_cap_is_truncated_with_a_visible_marker():
    big = "\n".join(f"line {i} " + "x" * 80 for i in range(5_000))
    assert len(big) > hub_result.NOTE_CAP
    out = hub_result.cap_note(big)
    assert contract.u16(out) <= hub_result.NOTE_CAP
    assert out.rstrip().splitlines()[-1] == hub_result.TRUNCATED_MARKER
    assert out.startswith("line 0 ")
    assert hub_result.cap_note("short") == "short"


def test_note_cap_counts_utf16_units():
    s = "\U0001F600" * 60_000   # 120,000 UTF-16 units, 60,000 code points
    out = hub_result.cap_note(s)
    assert contract.u16(out) <= hub_result.NOTE_CAP
    assert out.rstrip().endswith(hub_result.TRUNCATED_MARKER)


def test_big_score_drops_revenue_rows_first_then_fails_if_still_big():
    tr = c.hb2306_translation()
    score = c.hb2306_score()
    score["revenue_rows"] = [{"pad": "x" * 100} for _ in range(5_000)]   # ~560k chars
    r = hub_result.build_result(tr, score, "n", commit="x", model="m", bill_sha256="2" * 64)
    assert contract.validate_result(r) == []
    assert "revenue_rows" not in r["score"] and r["score"]["revenue_rows_dropped"] is True
    assert r["score"]["five_year"] == score["five_year"]          # everything else is kept
    assert "revenue_rows" in score                                # the caller's dict is untouched

    score["other"] = "y" * 450_000
    with pytest.raises(hub_result.ResultError, match="even without revenue_rows"):
        hub_result.build_result(tr, score, "n", commit="x", model="m", bill_sha256="2" * 64)

    tr["notes"] = "z" * 450_000
    with pytest.raises(hub_result.ResultError, match="translation JSON"):
        hub_result.build_result(tr, c.hb2306_score(), "n", commit="x", model="m", bill_sha256="2" * 64)


def test_scored_verdict_without_a_score_is_an_error():
    with pytest.raises(hub_result.ResultError):
        hub_result.build_result(c.hb2306_translation(), None, "n", commit="x", model="m",
                                bill_sha256="2" * 64)


# ---- the loop ------------------------------------------------------------------------------


def test_partial_request_posts_running_then_ready(tmp_path):
    fake = FakeHub([req()])
    out = run(fake, deps_for("2026_HB2306_HD1"), tmp_path)
    assert [o.status for o in out] == ["ready"]
    assert fake.statuses("fn_1") == ["running", "ready"]
    running, ready = fake.posts
    assert running == {"id": "fn_1", "status": "running"}
    r = ready["result"]
    assert r["verdict"] == "partial" and r["label"] == "HB 2306 HD1 (2026)"
    assert r["tool"]["commit"] == "abc1234def"
    assert r["tool"]["bill_sha256"] == contract_sha(ex.bill_text("2026_HB2306_HD1"))
    assert r["headline"]["five_year_mid"] == round(c.hb2306_score()["five_year"]["total"]["mid"], 1)
    assert r["translation"]["verdict"] == "partial" and "DRAFT" in r["markdown"]


def contract_sha(text):
    import hashlib
    return hashlib.sha256(text.encode()).hexdigest()


def test_work_directory_keeps_what_was_posted(tmp_path):
    fake = FakeHub([req()])
    run(fake, deps_for("2026_HB2306_HD1"), tmp_path)
    d = tmp_path / "work" / "2026" / "hb2306_hd1_2026"
    names = sorted(p.name for p in d.iterdir())
    assert names == ["hb2306_hd1_2026.note.md", "hb2306_hd1_2026.score.json",
                     "hb2306_hd1_2026.spec.yaml", "hb2306_hd1_2026.translation.json",
                     "hb2306_hd1_2026.txt", "posted.json"]
    assert json.loads((d / "posted.json").read_text()) == fake.posts[-1]
    assert (d / "hb2306_hd1_2026.txt").read_text() == ex.bill_text("2026_HB2306_HD1")


def test_work_directory_defaults_under_runs_fiscal_notes(tmp_path, monkeypatch):
    monkeypatch.setenv("CF_RUNS_DIR", str(tmp_path / "runs"))
    fake = FakeHub([req()])
    process_queue(client_for(fake), deps_for("2026_HB2306_HD1"))
    assert (tmp_path / "runs" / "fiscal_notes" / "2026" / "hb2306_hd1_2026" / "posted.json").exists()


def test_out_of_scope_request_scores_nothing(tmp_path):
    scored = []
    deps = deps_for("2024_HB2404_SD1", score=lambda p: scored.append(p))
    fake = FakeHub([req("fn_o", "HB2404", 2024, "SD1")])
    out = run(fake, deps, tmp_path)
    assert scored == [] and [o.status for o in out] == ["ready"]
    r = fake.posts[-1]["result"]
    assert r["verdict"] == "out_of_scope" and r["headline"] is None and r["score"] is None


def test_approximate_counts_reach_the_hub(tmp_path):
    fake = FakeHub([req("fn_a", "HB1850", 2026, "HD2")])
    run(fake, deps_for("2026_HB1850_HD2", score=lambda p: hb1850_score()), tmp_path)
    r = fake.posts[-1]["result"]
    assert r["verdict"] == "partial" and r["counts"]["approximate"] >= 1


def test_introduced_draft_request(tmp_path):
    seen = []
    deps = deps_for("2026_HB2306_Introduced")
    deps.fetch = lambda y, b, d: seen.append((y, b, d)) or fetcher_for("2026_HB2306_Introduced")(y, b, d)
    fake = FakeHub([req("fn_i", "HB2306", 2026, "")])
    run(fake, deps, tmp_path)
    assert seen == [(2026, "HB2306", "")]
    assert fake.statuses("fn_i")[-1] == "ready"
    assert fake.posts[-1]["result"]["label"] == "HB 2306 Introduced (2026)"


def test_invalid_translation_posts_failed_with_the_problems(tmp_path):
    deps = deps_for("2026_HB2306_HD1")
    deps.translate = lambda text, name: translate_bill(text, name, lambda prompt: "not json at all")
    fake = FakeHub([req()])
    out = run(fake, deps, tmp_path)
    assert [o.status for o in out] == ["failed"]
    assert fake.statuses("fn_1") == ["running", "failed"]
    detail = fake.posts[-1]["detail"]
    assert "response could not be parsed" in detail


def test_invalid_detail_is_cut_to_2000_chars():
    detail = hub_result.invalid_detail([f"problem {i} " + "p" * 100 for i in range(100)])
    assert len(detail) == 2000 and detail.startswith("The bill text did not translate")


def test_fetch_failure_posts_failed(tmp_path):
    def nope(year, bill, draft):
        raise hub.FetchError("HB2306 HD1: not found at https://x or https://y")

    deps = deps_for("2026_HB2306_HD1")
    deps.fetch = nope
    fake = FakeHub([req()])
    out = run(fake, deps, tmp_path)
    assert [o.status for o in out] == ["failed"]
    assert fake.posts[-1] == {"id": "fn_1", "status": "failed",
                              "detail": "HB2306 HD1: not found at https://x or https://y"}


def test_scoring_error_posts_failed(tmp_path):
    from fiscal_notes.score import ScoreError

    def no_population(spec):
        raise ScoreError("no simulator population in runs/tax_simulator")

    fake = FakeHub([req()])
    run(fake, deps_for("2026_HB2306_HD1", score=no_population), tmp_path)
    assert fake.posts[-1]["status"] == "failed" and "no simulator population" in fake.posts[-1]["detail"]

    def explodes(spec):
        raise ValueError("boom")

    fake = FakeHub([req()])
    run(fake, deps_for("2026_HB2306_HD1", score=explodes), tmp_path)
    assert "scoring raised ValueError: boom" in fake.posts[-1]["detail"]


def test_409_on_running_skips_that_request_and_goes_on(tmp_path):
    fake = FakeHub([req("fn_w", at="2026-11-26T01:00:00Z"), req("fn_2", at="2026-11-26T02:00:00Z")],
                   reject={("fn_w", "running"): 409})
    out = run(fake, deps_for("2026_HB2306_HD1"), tmp_path)
    assert [(o.id, o.status) for o in out] == [("fn_w", "skipped"), ("fn_2", "ready")]
    assert fake.statuses("fn_w") == ["running"]          # nothing else sent for the withdrawn one


def test_409_on_ready_is_skipped_not_fatal(tmp_path):
    fake = FakeHub([req()], reject={("fn_1", "ready"): 409})
    out = run(fake, deps_for("2026_HB2306_HD1"), tmp_path)
    assert [o.status for o in out] == ["skipped"]


def test_400_on_ready_is_reported_back_as_failed(tmp_path):
    fake = FakeHub([req()], reject={("fn_1", "ready"): 400})
    out = run(fake, deps_for("2026_HB2306_HD1"), tmp_path)
    assert [o.status for o in out] == ["failed"]
    assert fake.statuses("fn_1") == ["running", "ready", "failed"]
    assert "the hub refused the result" in fake.posts[-1]["detail"]


def test_one_bad_request_does_not_stop_the_next(tmp_path):
    calls = []

    def flaky(text, name):
        calls.append(name)
        if len(calls) == 1:
            raise RuntimeError("the translator fell over")
        return translator_for("2026_HB2306_HD1")(text, name)

    deps = deps_for("2026_HB2306_HD1")
    deps.translate = flaky
    fake = FakeHub([req("fn_a", at="2026-11-26T01:00:00Z"), req("fn_b", at="2026-11-26T02:00:00Z")])
    out = run(fake, deps, tmp_path)
    assert [(o.id, o.status) for o in out] == [("fn_a", "failed"), ("fn_b", "ready")]
    assert "RuntimeError while making the note: the translator fell over" in fake.posts[1]["detail"]


def test_a_text_the_header_parser_rejects_fails_the_request(tmp_path):
    deps = deps_for("2026_HB2306_HD1")
    deps.fetch = lambda y, b, d: {"text": "no header here\n\nSECTION 1. x", "url": "u", "title": ""}
    fake = FakeHub([req()])
    out = run(fake, deps, tmp_path)
    assert [o.status for o in out] == ["failed"] and "header not recognised" in fake.posts[-1]["detail"]


def test_oldest_first_and_limit(tmp_path):
    order = []
    deps = deps_for("2026_HB2306_HD1")
    deps.fetch = lambda y, b, d: order.append(b) or fetcher_for("2026_HB2306_HD1")(y, b, d)
    fake = FakeHub([req("fn_new", "HB3", at="2026-11-26T09:00:00Z"),
                    req("fn_old", "HB1", at="2026-11-26T01:00:00Z"),
                    req("fn_mid", "HB2", at="2026-11-26T05:00:00Z")])
    out = run(fake, deps, tmp_path, limit=2)
    assert order == ["HB1", "HB2"] and [o.id for o in out] == ["fn_old", "fn_mid"]
    assert fake.statuses("fn_new") == []


def test_malformed_request_is_failed_not_fetched(tmp_path):
    bad = {"id": "fn_bad", "bill": "XX9", "year": 2026, "draft": "", "by": "a", "at": "t"}
    deps = deps_for("2026_HB2306_HD1")
    deps.fetch = lambda *a: pytest.fail("a malformed request must not be fetched")
    fake = FakeHub([bad])
    out = run(fake, deps, tmp_path)
    assert [o.status for o in out] == ["failed"] and fake.posts[0]["status"] == "failed"


def test_dry_run_posts_nothing_and_prints_the_summary(tmp_path, capsys):
    fake = FakeHub([req()])
    code = main(["hub", "--hub", "https://hub.example", "--dry-run", "--work-dir", str(tmp_path / "w")],
                hub_client=client_for(fake), deps=deps_for("2026_HB2306_HD1"))
    out = capsys.readouterr().out
    assert code == 0 and fake.posts == [] and [m for m, _ in fake.calls] == ["GET"]
    assert "DRY RUN" in out and "HB2306 HD1 (2026): ready: partial; MID +" in out
    want = hub_result.counts(parse_response(ex.response_text("2026_HB2306_HD1"))["provisions"])
    assert (f"scored {want['scored']}, approximate {want['approximate']}, "
            f"not scored {want['not_scored']}") in out and "note " in out
    assert SECRET not in out
    assert (tmp_path / "w" / "2026" / "hb2306_hd1_2026" / "posted.json").exists()


def test_cli_exit_zero_when_requests_failed_but_were_reported(tmp_path, capsys):
    deps = deps_for("2026_HB2306_HD1")
    deps.fetch = lambda *a: (_ for _ in ()).throw(hub.FetchError("not found"))
    fake = FakeHub([req("fn_a"), req("fn_b")])
    code = main(["hub", "--hub", "https://hub.example", "--work-dir", str(tmp_path)],
                hub_client=client_for(fake), deps=deps)
    out = capsys.readouterr().out
    assert code == 0 and "2 request(s): 2 failed" in out


def test_cli_empty_queue(tmp_path, capsys):
    code = main(["hub", "--once"], hub_client=client_for(FakeHub([])), deps=deps_for("2026_HB2306_HD1"))
    assert code == 0 and "nothing queued" in capsys.readouterr().out


# ---- the transport -------------------------------------------------------------------------


def html_transport(status=200):
    def t(method, url, headers, body, timeout):
        return status, "text/html; charset=utf-8", "<html>Sign in to continue</html>"
    return t


def test_non_json_answer_is_the_refused_token_error():
    client = HubClient("https://hub.example", credentials=CREDS, transport=html_transport())
    with pytest.raises(hub.TokenRefused, match="service token was refused"):
        client.queue()
    with pytest.raises(hub.TokenRefused):
        client.post({"id": "fn_1", "status": "running"})


@pytest.mark.parametrize("status", [302, 401, 403])
def test_redirect_or_identity_refusal_without_json_is_refused_token(status):
    client = HubClient("https://hub.example", credentials=CREDS, transport=html_transport(status))
    with pytest.raises(hub.TokenRefused):
        client.queue()


def test_json_content_type_with_an_html_body_is_refused_too():
    client = HubClient("https://hub.example", credentials=CREDS,
                       transport=lambda *a: (200, "application/json", "<html>"))
    with pytest.raises(hub.TokenRefused):
        client.queue()


def test_cli_exits_nonzero_when_the_token_is_refused(capsys):
    client = HubClient("https://hub.example", credentials=CREDS, transport=html_transport())
    code = main(["hub"], hub_client=client, deps=deps_for("2026_HB2306_HD1"))
    err = capsys.readouterr().err
    assert code != 0 and "service token was refused" in err and SECRET not in err


def test_cli_exits_nonzero_when_the_hub_is_unreachable(capsys):
    def down(*a):
        raise NetworkError("gaierror: no such host", sent=False)

    client = HubClient("https://hub.example", credentials=CREDS, transport=down)
    code = main(["hub"], hub_client=client, deps=deps_for("2026_HB2306_HD1"))
    assert code != 0 and "gaierror" in capsys.readouterr().err


def test_cli_exits_nonzero_without_credentials(capsys, monkeypatch):
    monkeypatch.setattr(hub.sys, "platform", "linux")   # no Keychain either
    code = main(["hub", "--hub", "https://hub.example"], deps=deps_for("2026_HB2306_HD1"))
    assert code != 0 and "No Access service token" in capsys.readouterr().err


def test_json_error_status_is_a_rejection_with_the_hubs_message():
    client = HubClient("https://hub.example", credentials=CREDS,
                       transport=lambda *a: (409, "application/json", '{"error": "not queued"}'))
    with pytest.raises(hub.HubRejected) as e:
        client.post({"id": "fn_1", "status": "running"})
    assert e.value.status == 409 and e.value.message == "not queued"


def test_html_5xx_is_unreachable_not_a_refused_token():
    client = HubClient("https://hub.example", credentials=CREDS, transport=html_transport(502))
    with pytest.raises(hub.HubUnreachable):
        client.queue()


def scripted(*answers):
    """A transport that raises or returns its answers in order; counts calls."""
    seq, calls = list(answers), []

    def t(method, url, headers, body, timeout):
        calls.append(method)
        a = seq.pop(0)
        if isinstance(a, Exception):
            raise a
        return a
    t.calls = calls
    return t


OK_QUEUE = (200, "application/json", '{"requests": []}')
OK_POST = (200, "application/json", '{"id": "fn_1", "status": "ready"}')


def test_get_retries_once_on_a_network_error():
    t = scripted(NetworkError("timed out", sent=True), OK_QUEUE)
    assert HubClient("https://h", credentials=CREDS, transport=t).queue() == []
    assert t.calls == ["GET", "GET"]
    t = scripted(NetworkError("timed out", sent=True), NetworkError("timed out", sent=True), OK_QUEUE)
    with pytest.raises(hub.HubUnreachable):
        HubClient("https://h", credentials=CREDS, transport=t).queue()
    assert t.calls == ["GET", "GET"]


def test_post_is_not_retried_when_it_may_have_been_sent():
    t = scripted(NetworkError("timed out", sent=True), OK_POST)
    with pytest.raises(hub.HubUnreachable):
        HubClient("https://h", credentials=CREDS, transport=t).post({"id": "fn_1", "status": "ready"})
    assert t.calls == ["POST"]


def test_post_is_retried_once_when_the_failure_was_before_sending():
    t = scripted(NetworkError("refused", sent=False), OK_POST)
    out = HubClient("https://h", credentials=CREDS, transport=t).post({"id": "fn_1", "status": "ready"})
    assert out["status"] == "ready" and t.calls == ["POST", "POST"]
    t = scripted(NetworkError("refused", sent=False), NetworkError("refused", sent=False), OK_POST)
    with pytest.raises(hub.HubUnreachable):
        HubClient("https://h", credentials=CREDS, transport=t).post({"id": "fn_1", "status": "ready"})
    assert t.calls == ["POST", "POST"]


def test_hub_going_away_mid_run_stops_the_run(tmp_path):
    fake = FakeHub([req("fn_a"), req("fn_b")])

    def dies_on_ready(method, url, headers, body, timeout):
        if method == "POST" and json.loads(body)["status"] == "ready":
            raise NetworkError("reset", sent=True)
        return fake(method, url, headers, body, timeout)

    client = HubClient("https://hub.example", credentials=CREDS, transport=dies_on_ready)
    with pytest.raises(hub.HubUnreachable):
        process_queue(client, deps_for("2026_HB2306_HD1"), work_root=tmp_path)
    assert fake.statuses("fn_b") == []


def test_urllib_transport_never_follows_a_redirect():
    # the handler that keeps the token from being replayed to Access's sign-in host
    handler = hub._NoRedirect()
    assert handler.redirect_request(None, None, 302, "Found", {}, "https://elsewhere") is None


# ---- credentials ---------------------------------------------------------------------------


def fake_security(values, calls):
    def run(cmd, **kw):
        calls.append(cmd)
        assert cmd[:3] == ["security", "find-generic-password", "-s"] and cmd[-1] == "-w"
        v = values.get(cmd[3])
        if v is None:
            raise subprocess.CalledProcessError(44, cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=v + "\n", stderr="")
    return run


KEYCHAIN = {"apps-script:CF_ACCESS_CLIENT_ID": "kc-id", "apps-script:CF_ACCESS_CLIENT_SECRET": "kc-sec"}


def env_file(tmp_path, mode=0o600, text="CF_ACCESS_CLIENT_ID=file-id\nCF_ACCESS_CLIENT_SECRET=file-sec\n"):
    p = tmp_path / "hub.env"
    p.write_text(text)
    p.chmod(mode)
    return p


def test_credentials_environment_wins(tmp_path):
    calls = []
    got = hub.load_credentials({"CF_ACCESS_CLIENT_ID": "e-id", "CF_ACCESS_CLIENT_SECRET": "e-sec"},
                               env_file(tmp_path), fake_security(KEYCHAIN, calls), "darwin")
    assert got == ("e-id", "e-sec") and calls == []


def test_credentials_env_file_before_keychain(tmp_path):
    calls = []
    got = hub.load_credentials({}, env_file(tmp_path), fake_security(KEYCHAIN, calls), "darwin")
    assert got == ("file-id", "file-sec") and calls == []


def test_credentials_env_file_parses_comments_quotes_and_export(tmp_path):
    p = env_file(tmp_path, text="# token\nexport CF_ACCESS_CLIENT_ID='q-id'\nCF_ACCESS_CLIENT_SECRET=\"q-sec\"\n")
    assert hub.load_credentials({}, p, fake_security({}, []), "linux") == ("q-id", "q-sec")


def test_credentials_env_file_not_600_is_refused(tmp_path):
    p = env_file(tmp_path, mode=0o644)
    with pytest.raises(hub.CredentialsError, match="chmod 600") as e:
        hub.load_credentials({}, p, fake_security(KEYCHAIN, []), "darwin")
    assert "file-sec" not in str(e.value)


def test_credentials_keychain_when_nothing_else(tmp_path):
    calls = []
    got = hub.load_credentials({}, tmp_path / "missing.env", fake_security(KEYCHAIN, calls), "darwin")
    assert got == ("kc-id", "kc-sec")
    assert [c_[3] for c_ in calls] == ["apps-script:CF_ACCESS_CLIENT_ID", "apps-script:CF_ACCESS_CLIENT_SECRET"]


def test_credentials_incomplete_env_file_falls_through_to_keychain(tmp_path):
    p = env_file(tmp_path, text="CF_ACCESS_CLIENT_ID=only-id\n")
    got = hub.load_credentials({"CF_ACCESS_CLIENT_ID": "x"}, p, fake_security(KEYCHAIN, []), "darwin")
    assert got == ("kc-id", "kc-sec")


def test_credentials_missing_everywhere_names_the_places_not_values(tmp_path):
    with pytest.raises(hub.CredentialsError) as e:
        hub.load_credentials({}, tmp_path / "missing.env", fake_security({}, []), "darwin")
    msg = str(e.value)
    assert "CF_ACCESS_CLIENT_ID" in msg and "hub.env" in msg and "apps-script:" in msg


def test_credentials_no_keychain_off_a_mac(tmp_path):
    calls = []
    with pytest.raises(hub.CredentialsError):
        hub.load_credentials({}, tmp_path / "missing.env", fake_security(KEYCHAIN, calls), "linux")
    assert calls == []


# ---- fetching a draft through LRT's interpreter --------------------------------------------


def fake_run(stdout="", returncode=0, stderr="", seen=None):
    def run(cmd, **kw):
        if seen is not None:
            seen.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=stderr)
    return run


def fake_lrt(tmp_path):
    py = tmp_path / "lrt" / ".venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text("")
    return tmp_path / "lrt"


def test_fetch_draft_runs_the_helper_under_lrts_interpreter(tmp_path, monkeypatch):
    root = fake_lrt(tmp_path)
    monkeypatch.setenv("CF_ACCESS_CLIENT_SECRET", SECRET)
    seen = []
    page = {"text": "BILL: HB1 Introduced (2026 session)\n", "url": "u", "title": "t", "dir_year": 2026}
    got = hub.fetch_draft(2026, "HB2306", "HD1", root=root, run=fake_run(json.dumps(page), seen=seen))
    assert got == page
    (cmd, kw), = seen
    assert cmd[0] == str(root / ".venv" / "bin" / "python")
    assert cmd[1].endswith("fiscal_notes/_lrt_fetch.py")
    assert cmd[2:] == ["--year", "2026", "--bill", "HB2306", "--draft", "HD1"]
    assert kw["env"]["PYTHONPATH"] == str(root)
    assert "CF_ACCESS_CLIENT_SECRET" not in kw["env"]   # no credential reaches the child


def test_fetch_draft_root_comes_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("FISCAL_NOTES_LRT_ROOT", str(tmp_path / "somewhere"))
    assert hub.lrt_root() == tmp_path / "somewhere"
    monkeypatch.delenv("FISCAL_NOTES_LRT_ROOT")
    assert hub.lrt_root() == Path("~/repos/Legislative-Research-Tool").expanduser()


def test_fetch_draft_failures_are_clear_errors(tmp_path):
    with pytest.raises(hub.FetchError, match="Legislative-Research-Tool not found"):
        hub.fetch_draft(2026, "HB1", "", root=tmp_path / "nope")
    root = fake_lrt(tmp_path)
    err = json.dumps({"error": "HB1 Introduced: not found at https://a or https://b"})
    with pytest.raises(hub.FetchError, match="not found at https://a or https://b"):
        hub.fetch_draft(2026, "HB1", "", root=root, run=fake_run(err, returncode=1))
    with pytest.raises(hub.FetchError, match="without JSON.*Traceback"):
        hub.fetch_draft(2026, "HB1", "", root=root, run=fake_run("", 1, "Traceback: boom"))
    with pytest.raises(hub.FetchError, match="no text"):
        hub.fetch_draft(2026, "HB1", "", root=root, run=fake_run(json.dumps({"text": " "})))

    def slow(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 5)

    with pytest.raises(hub.FetchError, match="timed out"):
        hub.fetch_draft(2026, "HB1", "", root=root, run=slow)


def test_cli_fetch_writes_the_file_or_reports(tmp_path, capsys):
    page = {"text": ex.bill_text("2026_HB2306_HD1"), "url": "https://u/x.HTM", "title": "t"}
    seen = []
    out = tmp_path / "bill.txt"
    code = main(["fetch", "--year", "2026", "--bill", "hb2306", "--draft", "hd1", "--out", str(out)],
                fetcher=lambda y, b, d: seen.append((y, b, d)) or page)
    assert code == 0 and seen == [(2026, "HB2306", "HD1")] and out.read_text() == page["text"]
    code = main(["fetch", "--year", "2026", "--bill", "HB2306"], fetcher=lambda y, b, d: page)
    assert code == 0 and capsys.readouterr().out.endswith(page["text"][-50:])

    def fails(y, b, d):
        raise hub.FetchError("not found")

    assert main(["fetch", "--year", "2026", "--bill", "HB2306"], fetcher=fails) == 1
    assert "ERROR: not found" in capsys.readouterr().err


def test_default_hub_url_is_production_but_never_contacted_here():
    assert hub.DEFAULT_HUB == "https://staff-updates-internal.pages.dev"
    assert os.environ.get("CF_ACCESS_CLIENT_ID") is None   # the fixture removed any real token
