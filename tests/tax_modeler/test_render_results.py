"""scripts/render_results.py on a tiny fake runs/ tree."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest
from tax_modeler.runs import write_run_manifest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import render_results as rr  # noqa: E402

SCENS, YEARS = ["LOW", "MID"], [2027, 2028]


def _runs(root: Path, bump: float = 0.0) -> Path:
    enh = pd.DataFrame([
        {"scenario": s, "tax_year": y, "act46_baseline_$M": 2500.0,
         "bracket_delta_static_$M": 50.0, "eti_response_$M": -10.0, "pte_shift_$M": 0.0,
         "bracket_delta_post_$M": 40.0, "reec_savings_$M": 30.0, "cgec_savings_$M": 5.0,
         "tcra_savings_$M": 0.0, "credit_total_$M": 35.0,
         "total_impact_$M": 75.0 + i * 10 + bump + (y - 2027)}
        for i, s in enumerate(SCENS) for y in YEARS])
    q = pd.DataFrame([
        {"tax_year": y, "quintile": f"Q{k}", "avg_per_hh_total_change": -10.0 * k,
         "total_change_$M": -1.0 * k, "pct_pay_more": 2.5}
        for y in YEARS for k in (1, 2)])
    dec = pd.DataFrame([
        {"tax_year": y, "A_act46_banked_by_2026": -500.0, "B_act46_remaining_phaseins": -200.0,
         "C_act24_increment": 50.0, "total_vs_pre_act46": -650.0, "memo_vs_frozen_2026": -150.0}
        for y in YEARS])
    rev = pd.DataFrame([{"tax_year": y, "eitc_loss_$M": 40.0, "food_loss_$M": 30.0,
                         "total_loss_$M": 70.0} for y in (2028, 2029)])
    pov = pd.DataFrame([{"tax_year": 2028, "persons_into_poverty": 2000.0,
                         "children_into_poverty": 500.0, "poverty_gap_increase_$M": 16.0}])
    for name, files in {rr.ENHANCED: {"enhanced.csv": enh, "quintile.csv": q},
                        rr.PRE_ACT46: {"decomposition.csv": dec},
                        rr.WFC: {"revenue_by_year.csv": rev, "poverty_ty2028.csv": pov}}.items():
        d = root / name
        d.mkdir(parents=True, exist_ok=True)
        for f, df in files.items():
            df.to_csv(d / f, index=False)
        if name != rr.PRE_ACT46:  # that run has no manifest
            write_run_manifest(d, script="fake.py", git_sha="abc1234")
    return root


DOC = f"""# Title

intro prose

---

{rr.START}
OLD STALE BLOCK
{rr.END}

---

## Something — October 8, 2026

hand-written $1.0M
"""


@pytest.fixture
def doc(tmp_path):
    p = tmp_path / "doc.md"
    p.write_text(DOC)
    return p


def test_replaces_block_and_keeps_prose(tmp_path, doc):
    runs = _runs(tmp_path / "runs")
    assert rr.main(["--doc", str(doc), "--runs", str(runs)]) == 0
    text = doc.read_text()
    assert "OLD STALE BLOCK" not in text
    assert text.startswith("# Title\n\nintro prose")
    assert text.endswith("hand-written $1.0M\n")
    assert "## Current results (generated)" in text
    assert "git `abc1234`" in text and "no manifest" in text
    # scenario x year table: MID bold, 2-year total row
    assert "| 2027 | $75.0M | **$85.0M** |" in text
    assert "| **2-year total** | **$151.0M** | **$171.0M** |" in text
    assert "| 2027 | −$10.0M" not in text  # decomposition is MID only, per-year
    assert "−$10.0M" in text
    assert "| **5-year**" not in text and "| **2-year** |  |" in text
    assert "| Q1 | **−10** | −1.00 | 2.5% |" in text.replace("Q1 (bottom 20%)", "Q1")
    assert "Poverty (TY2028): 2,000 more people (500 children), poverty gap $16.0M." in text


def test_idempotent(tmp_path, doc):
    runs = _runs(tmp_path / "runs")
    rr.main(["--doc", str(doc), "--runs", str(runs)])
    first = doc.read_text()
    assert rr.main(["--doc", str(doc), "--runs", str(runs)]) == 0
    assert doc.read_text() == first
    assert first.count(rr.START) == 1 and first.count(rr.END) == 1


def test_check_semantics(tmp_path, doc, capsys):
    runs = _runs(tmp_path / "runs")
    assert rr.main(["--check", "--doc", str(doc), "--runs", str(runs)]) == 1
    assert "OLD STALE BLOCK" in doc.read_text()  # --check never writes
    rr.main(["--doc", str(doc), "--runs", str(runs)])
    assert rr.main(["--check", "--doc", str(doc), "--runs", str(runs)]) == 0
    _runs(tmp_path / "runs", bump=1.0)  # a result moves
    assert rr.main(["--check", "--doc", str(doc), "--runs", str(runs)]) == 1


def test_inserts_before_first_dated_section_when_no_markers(tmp_path):
    p = tmp_path / "doc.md"
    p.write_text("# T\n## Tax Years 2027–2031\n\nintro\n\n---\n\n"
                 "## Council — October 5, 2026\n\nbody\n")
    runs = _runs(tmp_path / "runs")
    assert rr.main(["--doc", str(p), "--runs", str(runs)]) == 0
    text = p.read_text()
    assert text.index(rr.START) < text.index("## Council") 
    assert text.index("## Tax Years") < text.index(rr.START)
    assert rr.main(["--check", "--doc", str(p), "--runs", str(runs)]) == 0


def test_money_formatting():
    assert rr.money(81.34) == "$81.3M"
    assert rr.money(-11.06) == "−$11.1M"
    assert rr.money(1036.9) == "$1,036.9M"
    assert rr.money(-0.04) == "$0.0M"
