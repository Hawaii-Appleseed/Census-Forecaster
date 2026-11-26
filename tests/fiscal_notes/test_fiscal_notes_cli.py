"""The ``python -m fiscal_notes`` command line, with a fake runner (no model, no network)."""
from __future__ import annotations

import hashlib
import json

import pytest
import yaml
from tax_modeler.reform.income_tax_spec import IncomeTaxSpec

from fiscal_notes import expectations as ex
from fiscal_notes.cli import main, parse_header
from fiscal_notes.translator import PROMPT_PATH, parse_response


def _bill_file(tmp_path, case):
    p = tmp_path / f"{case}.txt"
    p.write_text(ex.bill_text(case))
    return p


@pytest.mark.parametrize("case, name, label, session", [
    ("2026_HB2306_HD1", "hb2306_hd1_2026", "HB 2306 HD1 (2026)", "2026"),
    ("2026_SB3125_CD2", "sb3125_cd2_2026", "SB 3125 CD2 (2026)", "2026"),
    ("2024_HB2404_SD1", "hb2404_sd1_2024", "HB 2404 SD1 (2024)", "2024"),
    ("2026_HB2306_Introduced", "hb2306_introduced_2026", "HB 2306 Introduced (2026)", "2026"),
])
def test_header(case, name, label, session):
    h = parse_header(ex.bill_text(case))
    assert (h["name"], h["label"], h["session"]) == (name, label, session)
    IncomeTaxSpec.from_dict({"name": h["name"], "first_year": 2027})  # name satisfies the regex


def test_translate_writes_spec_and_translation(tmp_path, capsys):
    case = "2026_HB2306_HD1"
    out = tmp_path / "out"
    code = main(["translate", str(_bill_file(tmp_path, case)), "--out", str(out)],
                runner=lambda prompt: ex.response_text(case))
    assert code == 0
    spec_path = out / "hb2306_hd1_2026.spec.yaml"
    text = spec_path.read_text()
    assert text.startswith("# Machine-translated") and "must check every figure" in text
    spec = yaml.safe_load(text)
    assert spec["name"] == "hb2306_hd1_2026" and spec["label"] == "HB 2306 HD1 (2026)"
    loaded = IncomeTaxSpec.from_dict(spec)  # the written file loads as a spec
    assert loaded.first_year == 2027 and loaded.eitc_percent == 40
    tr = json.loads((out / "hb2306_hd1_2026.translation.json").read_text())
    assert tr["verdict"] == "partial" and tr["problems"] == [] and tr["attempts"] == 1
    assert tr["model"] == "sonnet"
    assert tr["bill_sha256"] == hashlib.sha256(ex.bill_text(case).encode()).hexdigest()
    assert tr["prompt_sha256"] == hashlib.sha256(PROMPT_PATH.read_bytes()).hexdigest()
    assert tr["provisions"] == parse_response(ex.response_text(case))["provisions"]
    shown = capsys.readouterr().out
    assert "partial" in shown and "modeled:" in shown and "not modeled:" in shown


def test_out_of_scope_writes_no_spec_and_exits_zero(tmp_path):
    case = "2024_HB2404_SD1"
    out = tmp_path / "o"
    code = main(["translate", str(_bill_file(tmp_path, case)), "--out", str(out), "--model", "opus"],
                runner=lambda prompt: ex.response_text(case))
    assert code == 0
    assert sorted(p.name for p in out.iterdir()) == ["hb2404_sd1_2024.translation.json"]
    tr = json.loads((out / "hb2404_sd1_2024.translation.json").read_text())
    assert tr["verdict"] == "out_of_scope" and tr["model"] == "opus"


def test_invalid_exits_one_and_lists_problems(tmp_path, capsys):
    case = "2026_HB2306_HD1"
    r = parse_response(ex.response_text(case))
    r["provisions"][0]["quote"] = "not in the bill"
    out = tmp_path / "o"
    code = main(["translate", str(_bill_file(tmp_path, case)), "--out", str(out)],
                runner=lambda prompt: json.dumps(r))
    assert code == 1
    tr = json.loads((out / "hb2306_hd1_2026.translation.json").read_text())
    assert tr["verdict"] == "invalid" and tr["attempts"] == 2
    assert "quote is not verbatim" in capsys.readouterr().out


def test_eval_with_fake_runner(tmp_path, monkeypatch, capsys):
    by_marker = {ex.bill_text(c).splitlines()[0]: ex.response_text(c) for c in ex.CASES}

    def runner(prompt):
        return next(r for marker, r in by_marker.items() if marker in prompt)

    # two bills share a header line only in their year; markers are unique per fixture
    assert len(by_marker) == len(ex.CASES)
    monkeypatch.setattr(ex, "RESPONSES_DIR", tmp_path / "rec")
    code = main(["eval", "--record"], runner=runner)
    out = capsys.readouterr().out
    assert code == 0, out
    assert out.count("ALL MET") == len(ex.CASES)
    assert len(list((tmp_path / "rec").glob("*.json"))) == len(ex.CASES)
