"""IRS SOI Table 2 for Hawaii: the committed data, its loader, and the parser's checks."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest
from tax_modeler.calibration import irs_soi_hi_table2 as irs

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("parse_irs_soi_hi_table2", REPO / "scripts" / "parse_irs_soi_hi_table2.py")
parser = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(parser)


def test_the_file_has_both_years_and_each_files_hash():
    doc = json.loads(irs._PATH.read_text())
    assert irs.years() == [2022, 2023]
    for year in ("2022", "2023"):
        f = doc["source"]["files"][year]
        assert len(f["sha256"]) == 64 and f["url"].startswith("https://www.irs.gov/pub/irs-soi/")


def test_ten_contiguous_classes_from_minus_infinity_to_infinity():
    b = irs.bands()
    assert len(b) == 10
    assert np.isneginf(b[0][0]) and np.isposinf(b[-1][1])
    assert all(b[i][1] == b[i + 1][0] for i in range(9))


@pytest.mark.parametrize("year", [2022, 2023])
def test_every_class_set_adds_to_its_printed_total(year):
    doc = json.loads(irs._PATH.read_text())["years"][str(year)]
    for key in ("returns", "joint_returns", "wage_returns"):
        assert abs(sum(doc[key]) - doc[f"{key}_total"]) <= 50
    for key in ("agi_amount_k", "wage_amount_k"):
        assert abs(sum(doc[key]) - doc[f"{key}_total"]) <= max(10, 1e-4 * abs(doc[f"{key}_total"]))


def test_known_values_for_tax_year_2023():
    r = irs.returns(2023)
    assert r[(75_000, 100_000)] == 74_340
    assert r[(100_000, 200_000)] == 131_140
    assert irs.wages_M(2023)[(100_000, 200_000)] == pytest.approx(12_428.112)
    assert irs.average_wage(2023)[(100_000, 200_000)] == pytest.approx(116_674, abs=1)
    assert sum(irs.returns(2023).values()) == pytest.approx(695_470, abs=50)


def test_family_rows_for_tax_year_2023_as_the_irs_prints_them():
    assert irs.values("hoh_returns", 2023) == [650, 4_110, 11_380, 24_020, 15_890, 7_860, 7_570, 1_130, 130, 50]
    assert irs.values("ctc_nonref_returns", 2023) == [30, 60, 4_800, 34_650, 29_820, 21_790, 49_220, 16_010, 20, 0]
    assert irs.values("actc_returns", 2023) == [410, 4_250, 15_110, 26_380, 9_080, 2_040, 1_470, 170, 0, 0]
    assert irs.values("cdcc_returns", 2023) == [0, 0, 280, 2_440, 2_670, 2_460, 8_070, 3_570, 330, 70]
    assert irs.values("cdcc_amount_k", 2023) == [0, 0, 66, 1_383, 1_499, 1_494, 4_831, 2_132, 192, 42]
    doc = json.loads(irs._PATH.read_text())["years"]["2023"]
    assert (doc["hoh_returns_total"], doc["ctc_nonref_returns_total"], doc["actc_returns_total"],
            doc["cdcc_returns_total"], doc["cdcc_amount_k_total"]) == (72_790, 156_390, 58_910, 19_890, 11_639)


@pytest.mark.parametrize("year", [2022, 2023])
def test_every_family_row_adds_to_its_printed_total(year):
    doc = json.loads(irs._PATH.read_text())["years"][str(year)]
    for key in ("hoh_returns", "ctc_nonref_returns", "actc_returns", "cdcc_returns"):
        assert abs(sum(doc[key]) - doc[f"{key}_total"]) <= 50
    for key in ("ctc_nonref_amount_k", "actc_amount_k", "cdcc_amount_k"):
        assert abs(sum(doc[key]) - doc[f"{key}_total"]) <= max(10, 1e-4 * abs(doc[f"{key}_total"]))


def test_the_default_year_is_the_departments_base_year():
    from tax_modeler.calibration import dotax_base

    assert irs.BASE_YEAR == dotax_base.BASE_YEAR


def test_unknown_year_and_key_say_what_is_available():
    with pytest.raises(KeyError, match="2019.*2022, 2023"):
        irs.values("returns", 2019)
    with pytest.raises(KeyError, match="not one of"):
        irs.values("wages")


# ---- the parser, on a small workbook laid out like the IRS's ------------------------------

def _workbook(path: Path, *, year: int = 2023, state: str | None = "HAWAII", bump: float = 0.0,
              actc_label: str = "Additional child tax credit: Number", ctc_bump: float = 0.0) -> Path:
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append([f"Table 2.  Individual Income and Tax Data, by State\n and Size of Adjusted Gross Income, Tax Year {year}"])
    ws.append(["[Money amounts are in thousands of dollars]"])
    ws.append(["Item", state])
    ws.append([None, "All returns", "Size of adjusted gross income"])

    def put(label, classes, *, total_off=0.0):
        ws.append([label, sum(classes) + total_off, *classes])

    cls = [10.0 * k for k in range(1, 11)]
    put("Number of returns [1]", cls)
    put("Number of joint returns", [c / 2 for c in cls])
    put("Number of head of household returns", [c / 5 for c in cls])
    put("Adjusted gross income (AGI) [6]", [c * 100 for c in cls], total_off=bump)
    put("Salaries and wages in AGI:  Number", [c * 0.8 for c in cls])
    put("Amount", [c * 90 for c in cls])
    put("Child and dependent care credit: Number", [c / 10 for c in cls])
    put("Amount", [c * 2 for c in cls])
    put("Nonrefundable child and other dependent tax credit: Number", [c / 2 for c in cls], total_off=ctc_bump)
    put("Amount", [c * 9 for c in cls])
    put(actc_label, [c / 4 for c in cls])
    put("Amount", [c * 6 for c in cls])
    wb.save(path)
    return path


def test_the_parser_reads_a_workbook_and_its_totals_hold(tmp_path):
    d = parser.parse_workbook(_workbook(tmp_path / "23in12hi.xlsx"))
    assert d["tax_year"] == 2023
    assert d["returns"] == [10.0 * k for k in range(1, 11)]
    assert d["wage_amount_k"][0] == 900.0


def test_the_parser_reads_the_family_rows_and_both_labels_of_the_additional_credit(tmp_path):
    d = parser.parse_workbook(_workbook(tmp_path / "23in12hi.xlsx"))
    assert d["hoh_returns"][0] == 2.0 and d["cdcc_returns"][-1] == 10.0 and d["cdcc_amount_k"][0] == 20.0
    assert d["ctc_nonref_returns"][1] == 10.0 and d["ctc_nonref_amount_k"][0] == 90.0
    assert d["actc_returns"][0] == 2.5 and d["actc_amount_k"][0] == 60.0
    old = parser.parse_workbook(_workbook(tmp_path / "22in12hi.xlsx", year=2022, state=None,
                                          actc_label="Refundable child tax credit or additional child tax credit: Number"))
    assert old["actc_returns"] == d["actc_returns"]


def test_the_parser_refuses_family_rows_that_do_not_add_up(tmp_path):
    with pytest.raises(ValueError, match="ctc_nonref_returns classes add to"):
        parser.parse_workbook(_workbook(tmp_path / "23in12hi.xlsx", ctc_bump=5_000.0))


def test_the_parser_refuses_classes_that_do_not_add_to_the_printed_total(tmp_path):
    with pytest.raises(ValueError, match="agi_amount_k classes add to"):
        parser.parse_workbook(_workbook(tmp_path / "23in12hi.xlsx", bump=5_000.0))


def test_the_parser_refuses_a_workbook_that_is_not_hawaii(tmp_path):
    with pytest.raises(ValueError, match="nothing says this is the Hawaii table"):
        parser.parse_workbook(_workbook(tmp_path / "23in06ca.xlsx", state="CALIFORNIA"))


def test_the_parser_accepts_the_unlabelled_hawaii_file_by_its_irs_name(tmp_path):
    d = parser.parse_workbook(_workbook(tmp_path / "22in12hi.xlsx", year=2022, state=None))
    assert d["tax_year"] == 2022
