"""_forecast_common.fingerprint, the key of the chain's cached step outputs."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from _forecast_common import fingerprint  # noqa: E402


def test_same_inputs_same_key():
    assert fingerprint(b"x", {"a": 1}) == fingerprint(b"x", {"a": 1})


def test_key_order_of_a_dict_does_not_matter():
    assert fingerprint({"a": 1, "b": 2}) == fingerprint({"b": 2, "a": 1})


def test_any_part_changes_the_key(tmp_path):
    f = tmp_path / "input.csv"
    f.write_text("1\n")
    before = fingerprint(f, b"x")
    assert fingerprint(f, b"y") != before
    f.write_text("2\n")
    assert fingerprint(f, b"x") != before


def test_a_directory_hashes_its_files(tmp_path):
    (tmp_path / "a.txt").write_text("a")
    before = fingerprint(tmp_path)
    (tmp_path / "b.txt").write_text("b")
    assert fingerprint(tmp_path) != before
