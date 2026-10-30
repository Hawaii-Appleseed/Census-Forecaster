"""``tax_modeler.base``: the production population build the chain scripts and
``run_pipeline`` share."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pandas.testing as pdt
import pytest
from tax_modeler import base
from tax_modeler.artifacts import load_canonical_deduction_params
from tax_modeler.pipeline import compute_base_tax, enrich_for_credits
from tax_modeler.units.constructor import TaxUnitConstructor

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def pums():
    return (pd.read_parquet(FIXTURES / "synthetic_pums_persons.parquet"),
            pd.read_parquet(FIXTURES / "synthetic_pums_households.parquet"))


def test_build_raw_units_is_the_uncalibrated_constructor(pums):
    persons, hh = pums
    got = base.build_raw_units(persons.copy(), hh.copy())
    want = TaxUnitConstructor(persons.copy(), hh.copy(), use_soi_calibration=False,
                              progress_bar=False).create_rule_based_units(parallel=False)
    pdt.assert_frame_equal(got, want)


def test_build_calibrated_base_scores_on_the_canonical_params(pums):
    persons, hh = pums
    raw = base.build_raw_units(persons.copy(), hh.copy())
    got, params = base.build_calibrated_base(raw, skip_calibration=True, log=lambda *a, **k: None)
    canonical = load_canonical_deduction_params()
    assert params == canonical
    want = compute_base_tax(enrich_for_credits(raw), deduction_params=canonical, tax_year=2023)
    pdt.assert_frame_equal(got, want)


def test_build_calibrated_base_keeps_the_caller_params(pums):
    persons, hh = pums
    raw = base.build_raw_units(persons.copy(), hh.copy())
    canonical = load_canonical_deduction_params()
    _, params = base.build_calibrated_base(raw, deduction_params=canonical, skip_calibration=True)
    assert params is canonical


def test_load_or_build_raw_units_reads_an_existing_cache(tmp_path, monkeypatch):
    cache = tmp_path / "units.parquet"
    pd.DataFrame({"weight": [1.0, 2.0]}).to_parquet(cache)
    monkeypatch.setattr("tax_modeler.artifacts.check_cache_sidecar", lambda p: None)
    got = base.load_or_build_raw_units(cache, tmp_path / "no-pums", log=lambda *a, **k: None)
    assert got["weight"].tolist() == [1.0, 2.0]


def test_load_or_build_raw_units_without_cache_or_pums_is_missing_data(tmp_path):
    from tax_modeler.errors import MissingDataError

    with pytest.raises(MissingDataError):
        base.load_or_build_raw_units(tmp_path / "units.parquet", tmp_path / "no-pums",
                                     log=lambda *a, **k: None)
    assert not (tmp_path / "units.parquet").exists()
