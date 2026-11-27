"""The draft-fetch helper's URL and fallback logic, with a fake fetcher (no network, no LRT)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from fiscal_notes import _lrt_fetch as f
from fiscal_notes import expectations as ex

HELPER = Path(f.__file__)


def test_urls():
    base = "https://data.capitol.hawaii.gov/sessions/session2026/bills/"
    assert f.draft_url(2026, "HB2306", "HD1") == base + "HB2306_HD1_.HTM"
    assert f.draft_url(2026, "HB2306", "") == base + "HB2306_.HTM"
    assert f.draft_url(2024, "SB3125", "CD2") == "https://data.capitol.hawaii.gov/sessions/session2024/bills/SB3125_CD2_.HTM"
    assert "www." not in f.draft_url(2026, "HB1", "")


@pytest.mark.parametrize("bill, draft", [("HR12", ""), ("HB", ""), ("HB12345", ""), ("hb1", ""),
                                         ("HB1", "HD"), ("HB1", "XD1"), ("HB1", "../x")])
def test_bad_inputs_are_refused_before_any_fetch(bill, draft):
    with pytest.raises(f.DraftError):
        f.draft_url(2026, bill, draft)


def test_candidate_years_even_falls_back_to_the_odd_year_before():
    assert f.candidate_years(2026) == [2026, 2025]
    assert f.candidate_years(2024) == [2024, 2023]
    assert f.candidate_years(2025) == [2025]


def page_for(bill="HB2306"):
    return f"<html><title>{bill} HD1</title><p>SECTION 1. x</p></html>".encode("cp1252")


def parse(page, label):
    return "TITLE.", "Desc.", ["SECTION 1. One.", "SECTION 2. Two."]


def test_requested_year_directory_is_used_first():
    asked = []

    def get(url, cache):
        asked.append((url, cache))
        return page_for()

    out = f.fetch_draft(2026, "HB2306", "HD1", get, parse)
    assert out["dir_year"] == 2026 and out["url"].endswith("/session2026/bills/HB2306_HD1_.HTM")
    assert len(asked) == 1
    assert asked[0][1] == "fiscal-notes-draft-2026-HB2306_HD1_.HTM"   # not LRT's own cache names
    assert out["text"] == ("BILL: HB2306 HD1 (2026 session)\nTITLE: TITLE.\nDESCRIPTION: Desc.\n\n"
                           "SECTION 1. One.\n\nSECTION 2. Two.")
    assert out["title"] == "TITLE."


def test_introduced_draft_label_and_file():
    asked = []
    out = f.fetch_draft(2026, "HB2306", "", lambda u, c: asked.append(u) or page_for(), parse)
    assert asked[0].endswith("/HB2306_.HTM")
    assert out["text"].startswith("BILL: HB2306 Introduced (2026 session)\n")


def test_carry_over_falls_back_to_the_previous_odd_year_and_says_so():
    asked = []

    def get(url, cache):
        asked.append(url)
        if "session2026" in url:
            raise f.DraftNotFound(url)
        return page_for()

    out = f.fetch_draft(2026, "HB2306", "HD1", get, parse)
    assert out["dir_year"] == 2025 and "/session2025/" in out["url"]
    assert len(asked) == 2
    assert out["text"].startswith("BILL: HB2306 HD1 (2026 session)")   # still the requested session


def test_odd_year_does_not_fall_back():
    asked = []

    def get(url, cache):
        asked.append(url)
        raise f.DraftNotFound(url)

    with pytest.raises(f.DraftError, match="not found at"):
        f.fetch_draft(2025, "HB1", "", get, parse)
    assert len(asked) == 1


def test_not_found_in_either_directory_names_both_urls():
    def get(url, cache):
        raise f.DraftNotFound(url)

    with pytest.raises(f.DraftError) as e:
        f.fetch_draft(2026, "HB9999", "HD2", get, parse)
    msg = str(e.value)
    assert "session2026/bills/HB9999_HD2_.HTM" in msg and "session2025/bills/HB9999_HD2_.HTM" in msg


def test_another_failure_is_not_a_reason_to_try_the_other_directory():
    asked = []

    def get(url, cache):
        asked.append(url)
        raise f.DraftError("could not fetch: timed out")

    with pytest.raises(f.DraftError, match="timed out"):
        f.fetch_draft(2026, "HB2306", "HD1", get, parse)
    assert len(asked) == 1


def test_a_page_for_the_wrong_bill_is_refused():
    with pytest.raises(f.DraftError, match="does not name HB2306"):
        f.fetch_draft(2026, "HB2306", "HD1", lambda u, c: page_for("HB2307"), parse)
    assert f.names_the_bill("HB 2306 HD1", "HB2306") and not f.names_the_bill("HB12306", "HB2306")


def test_no_sections_is_an_error():
    with pytest.raises(f.DraftError, match="no sections"):
        f.fetch_draft(2026, "HB2306", "HD1", lambda u, c: page_for(), lambda p, label: ("T", "D", []))


def test_built_text_matches_the_recorded_fixture_layout():
    bill = ex.bill_text("2026_HB2306_HD1")
    head, body = bill.split("\n\n", 1)
    title, desc = head.splitlines()[1][len("TITLE: "):], head.splitlines()[2][len("DESCRIPTION: "):]
    sections = body.split("\n\n")
    assert f.build_text("HB2306", "HD1", 2026, title, desc, sections) == bill


def test_the_helper_script_runs_standalone_and_reports_errors_as_json():
    # `--draft` is validated before anything is fetched, so this makes no request and needs no LRT
    proc = subprocess.run([sys.executable, str(HELPER), "--year", "2026", "--bill", "XX1"],
                          capture_output=True, text=True, cwd="/")
    out = json.loads(proc.stdout)
    assert proc.returncode == 1 and "not a bill number" in out["error"]
