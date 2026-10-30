"""DOTAX Tax Credits Claimed figures: the committed data, its loader, the parser's checks, and the
working-family step's use of them."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from tax_modeler.calibration import dotax_credits_claimed as claimed

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "parse_dotax_credits_claimed", REPO / "scripts" / "parse_dotax_credits_claimed.py")
parser = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(parser)


def test_the_file_has_both_years_and_each_workbooks_hash():
    doc = json.loads(claimed._PATH.read_text())
    assert claimed.years() == [2022, 2023]
    for year in ("2022", "2023"):
        f = doc["source"]["files"][year]
        assert len(f["sha256"]) == 64 and f["url"].startswith("https://files.hawaii.gov/tax/stats/stats/credits/")


def test_known_values_exactly_as_the_department_prints_them():
    assert claimed.claims("eitc", 2023) == 84_470
    assert claimed.claims("food_excise", 2023) == 249_806
    assert claimed.dollars_M("eitc", 2023) == pytest.approx(77.05416)
    assert claimed.dollars_M("food_excise", 2023) == pytest.approx(63.957442)
    assert claimed.dollars_M("eitc", 2022) == pytest.approx(19.786)
    assert claimed.dollars_M("food_excise", 2022) == pytest.approx(24.961)
    assert claimed.claims("eitc", 2022) == 60_903
    assert claimed.claims("food_excise", 2022) == 208_786


def test_unknown_year_and_credit_say_what_is_available():
    with pytest.raises(KeyError, match="2019.*2022, 2023"):
        claimed.claims("eitc", 2019)
    with pytest.raises(KeyError, match="not one of"):
        claimed.dollars_M("ctc", 2023)


def test_the_working_family_step_uses_the_figures_it_always_has():
    """The table the step fits and checks against, read from the data file, is the one that was typed in."""
    for p in (REPO / "chain", REPO / "scripts"):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    import forecast_working_family_credits as wfc

    assert wfc.dotax() == {
        2022: {"eitc_$M": 19.786, "eitc_claims": 60_903, "food_$M": 24.961, "food_claims": 208_786},
        2023: {"eitc_$M": 77.054, "eitc_claims": 84_470, "food_$M": 63.957, "food_claims": 249_806},
    }


# ---- the parser, on a small workbook laid out like the Department's -----------------------------

def _workbook(path: Path, *, food_all: object = 100.0, food_ind: object = 100.0, duplicate: bool = False,
              title1: str = "DOLLAR AMOUNTS OF TAX CREDITS CLAIMED") -> Path:
    import openpyxl

    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for sheet, title, scale in (("A-1", title1, 1.0), ("A-2", "NUMBER OF TAX CREDITS CLAIMED", 10.0)):
        ws = wb.create_sheet(sheet)
        ws.append([f"Table {sheet}"])
        ws.append([title])
        ws.append(["BY TYPE OF CREDIT AND TYPE OF TAXPAYER"])
        ws.append(["TAXPAYER TYPE"])
        ws.append(["Type of Credit", "ALL", "Individuals", "Corporations"])
        ws.append(["      Refundable Food/Excise Tax Credit   ", food_all if sheet == "A-1" else 1000,
                   food_ind if sheet == "A-1" else 1000, "na"])
        ws.append(["      Earned Income Tax Credit", 77.5 * scale, 77.5 * scale, "na"])
        if duplicate:
            ws.append(["      Earned Income Tax Credit", 1, 1, "na"])
    wb.save(path)
    return path


def test_the_parser_reads_a_workbook(tmp_path):
    d = parser.parse_workbook(_workbook(tmp_path / "2023credit_tables.xlsx"))
    assert d["eitc"] == {"dollars_k": 77.5, "claims": 775.0}
    assert d["food_excise"] == {"dollars_k": 100.0, "claims": 1000.0}


def test_the_parser_refuses_a_suppressed_cell(tmp_path):
    with pytest.raises(ValueError, match="expected a number, found 'd'"):
        parser.parse_workbook(_workbook(tmp_path / "w.xlsx", food_all="d", food_ind="d"))


def test_the_parser_refuses_columns_that_disagree(tmp_path):
    with pytest.raises(ValueError, match="ALL 100.0 != Individuals 90.0"):
        parser.parse_workbook(_workbook(tmp_path / "w.xlsx", food_ind=90.0))


def test_the_parser_refuses_a_repeated_row(tmp_path):
    with pytest.raises(ValueError, match="2 rows start"):
        parser.parse_workbook(_workbook(tmp_path / "w.xlsx", duplicate=True))


def test_the_parser_refuses_the_wrong_table(tmp_path):
    with pytest.raises(ValueError, match="title does not say"):
        parser.parse_workbook(_workbook(tmp_path / "w.xlsx", title1="SOMETHING ELSE"))
