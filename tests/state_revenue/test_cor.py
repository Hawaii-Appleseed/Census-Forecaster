"""The Council on Revenues' line-item forecasts: parser and bundled archive."""
from datetime import date

import pytest
from state_revenue import load_ledger
from state_revenue.cor import (
    COR_ROWS,
    AttachmentError,
    load_cor_forecasts,
    meeting_from_filename,
    parse_attachment,
)
from state_revenue.scripts.refresh_cor_forecasts import attachment_urls

pytestmark = pytest.mark.smoke

ROWS = [
    ("General Excise and Use Tax", 4596199, 4805495, 5012754, 5230146),
    ("Individual Income Tax", 3288541, 3120844, 3050119, 3070958),
    ("Transient Accommodations Tax", 710611, 770119, 849314, 884576),
    ("Corporate Income Tax", 400097, 447540, 448006, 436529),
    ("Tax on Insurance Premiums", 218833, 221545, 236492, 252210),
    ("Public Service Company Tax", 154478, 152813, 162058, 170629),
    ("Cigarette and Tobacco Tax", 56818, 56979, 56833, 57114),
    ("Liquor Tax", 50149, 50183, 51078, 51742),
    ("Tax on Banks and Other Financial Corps.", 10296, 12643, 25373, 18712),
    ("Inheritance and Estate Tax", 405766, 50720, 52252, 53632),
    ("Conveyance Tax", 53733, 78515, 68671, 88548),
    ("Miscellaneous Taxes**", 28641, 25713, 24986, 24279),
]


def _attachment(header="TYPE OF TAX      FY 2025    FY 2026*     FY 2027     FY 2028"):
    lines = ["  ESTIMATES OF GENERAL FUND TAX REVENUE FROM THE MEETING OF SEPTEMBER 3, 2026",
             "                       (in thousands of dollars)",
             "                    BASE                         ESTIMATED",
             header]
    for label, *vals in ROWS:
        lines.append(f"{label:<45}" + "".join(f"${v:>12,}  " for v in vals))
    totals = [sum(r[i] for r in ROWS) for i in range(1, 5)]
    lines.append(f"{'GENERAL FUND TOTAL':<45}" + "".join(f"${v:>12,}  " for v in totals))
    lines.append("GROWTH RATE      4.2%   -1.8%   2.5%   3.0%")
    return "\n".join(lines)


def test_parse_attachment():
    f = parse_attachment(_attachment(), date(2026, 9, 3))
    assert list(f.base) == [2025, 2026] and list(f.estimates) == [2027, 2028]
    assert f.preliminary == (2026,)
    assert f.estimates[2027]["general_excise"] == 5_012_754
    assert f.estimates[2027]["miscellaneous"] == 24_986
    assert set(f.estimates[2027]) == set(COR_ROWS.values())
    assert f.horizon(2027) == 1 and f.horizon(2028) == 2       # FY2026 ended in June
    assert parse_attachment(_attachment(), date(2026, 3, 10)).horizon(2026) == 1


def test_parse_attachment_rejects_short_rows_and_bad_headers():
    with pytest.raises(AttachmentError):
        parse_attachment(_attachment(header="TYPE OF TAX  FY 2025  FY 2026  FY 2027  FY 2028  FY 2029"),
                         date(2026, 9, 3))
    with pytest.raises(AttachmentError):
        parse_attachment("nothing here", date(2026, 9, 3))


def test_meeting_from_filename_and_links():
    assert meeting_from_filename("2019gf08-29_attach_1.pdf") == date(2019, 8, 29)
    with pytest.raises(AttachmentError):
        meeting_from_filename("2026tpi07-30RptGov.pdf")
    current = '<a href="https://files.hawaii.gov/tax/useful/cor/2026gf09-03_attach_1.pdf">'
    archive = ('<a href="https://files.hawaii.gov/tax/useful/cor/archive/2026gf09-03_attach_1.pdf">'
               '<a href="https://files.hawaii.gov/tax/useful/cor/archive/2014gf01-07_attach_1.pdf">'
               '<a href="https://files.hawaii.gov/tax/useful/cor/archive/2014gf01-07_attach_2.pdf">')
    urls = attachment_urls([current, archive])
    assert list(urls) == ["2014gf01-07_attach_1.pdf", "2026gf09-03_attach_1.pdf"]
    assert "/archive/" not in urls["2026gf09-03_attach_1.pdf"]


def test_bundled_archive():
    forecasts = load_cor_forecasts()
    assert len(forecasts) >= 52
    assert forecasts[0].meeting == date(2014, 1, 7)
    sept_2026 = next(f for f in forecasts if f.meeting == date(2026, 9, 3))
    assert sept_2026.estimates[2027]["general_excise"] == 5_012_754
    assert sept_2026.estimates[2027]["total"] == 10_037_937
    for f in forecasts:
        for row in (*f.base.values(), *f.estimates.values()):
            assert abs(row["total"] - sum(v for k, v in row.items() if k != "total")) <= 5


def test_cor_actuals_equal_annual_report_general_fund():
    """The Council's BASE figures and DOTAX's general fund table are the same
    numbers through FY2024, so the annual report is a fair truth for both."""
    gf = load_ledger("general_fund")
    base = {}
    for f in load_cor_forecasts():
        for fy, row in f.base.items():
            if fy not in f.preliminary:
                base[fy] = row
    for fy in range(2012, 2025):
        assert base[fy]["total"] == gf[fy].total
        for key in ("general_excise", "individual_income", "transient_accommodations"):
            assert abs(base[fy][key] - gf[fy].lines[key]) <= 1      # rounding
