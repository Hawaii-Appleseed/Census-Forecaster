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
    monkeypatch.setattr("tax_modeler.artifacts.cache_status", lambda p: (None, {}))
    got = base.load_or_build_raw_units(cache, tmp_path / "no-pums", log=lambda *a, **k: None)
    assert got["weight"].tolist() == [1.0, 2.0]


def test_load_or_build_raw_units_without_cache_or_pums_is_missing_data(tmp_path):
    from tax_modeler.errors import MissingDataError

    with pytest.raises(MissingDataError):
        base.load_or_build_raw_units(tmp_path / "units.parquet", tmp_path / "no-pums",
                                     log=lambda *a, **k: None)
    assert not (tmp_path / "units.parquet").exists()


# ---------------------------------------------------------------------------
# A cache that is not current is rebuilt, not used with a warning
# ---------------------------------------------------------------------------

def _quiet(*a, **k):
    pass


@pytest.fixture
def fake_build(monkeypatch):
    """PUMS loading and unit construction replaced by a frame the test controls; counts builds."""
    state = {"units": pd.DataFrame({"weight": [1.0, 2.0], "fs": ["single", "joint"]}), "builds": 0}

    def build(persons, hh):
        state["builds"] += 1
        return state["units"].copy()

    monkeypatch.setattr("tax_modeler.loaders.pums_loader.PUMSDataLoader.load_data",
                        lambda self, state, pums_type: (pd.DataFrame(), pd.DataFrame()))
    monkeypatch.setattr(base, "build_raw_units", build)
    monkeypatch.delenv(base.ALLOW_STALE_CACHE_ENV, raising=False)
    return state


def _stale_cache(path, weights=(1.0, 2.0)):
    from tax_modeler.artifacts import write_cache_sidecar

    pd.DataFrame({"weight": list(weights), "fs": ["single", "joint"]}).to_parquet(path, index=False)
    write_cache_sidecar(path, code_key="an-old-key")


def test_a_current_cache_is_read_without_building(tmp_path, fake_build):
    from tax_modeler.artifacts import cache_status

    cache = tmp_path / "units.parquet"
    base.load_or_build_raw_units(cache, tmp_path, log=_quiet)
    assert fake_build["builds"] == 1 and cache_status(cache)[0] is None
    got = base.load_or_build_raw_units(cache, tmp_path, log=_quiet)
    assert fake_build["builds"] == 1
    assert got["weight"].tolist() == [1.0, 2.0]


def test_a_stale_cache_is_rebuilt_and_the_log_says_it_came_out_the_same(tmp_path, fake_build):
    from tax_modeler.artifacts import cache_status

    cache = tmp_path / "units.parquet"
    _stale_cache(cache)
    lines = []
    got = base.load_or_build_raw_units(cache, tmp_path, log=lambda m, **k: lines.append(m))
    assert fake_build["builds"] == 1
    assert cache_status(cache)[0] is None
    assert got.equals(pd.read_parquet(cache))
    text = "\n".join(lines)
    assert "different construction code" in text and "rebuilding it" in text
    assert "identical to the cache they replace" in text
    assert not cache.with_suffix(".tmp.parquet").exists()


def test_a_rebuild_that_changes_the_units_says_which_columns(tmp_path, fake_build):
    cache = tmp_path / "units.parquet"
    _stale_cache(cache, weights=(1.0, 3.0))
    lines = []
    base.load_or_build_raw_units(cache, tmp_path, log=lambda m, **k: lines.append(m))
    assert any("REBUILT UNITS DIFFER" in m and "['weight']" in m for m in lines)


def test_the_override_loads_a_stale_cache_with_a_warning(tmp_path, fake_build, monkeypatch, capsys):
    cache = tmp_path / "units.parquet"
    _stale_cache(cache, weights=(5.0, 6.0))
    monkeypatch.setenv(base.ALLOW_STALE_CACHE_ENV, "1")
    got = base.load_or_build_raw_units(cache, tmp_path, log=_quiet)
    assert fake_build["builds"] == 0
    assert got["weight"].tolist() == [5.0, 6.0]
    assert "CF_ALLOW_STALE_CACHE=1" in capsys.readouterr().out


def test_without_pums_a_stale_cache_is_loaded_with_a_warning(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv(base.ALLOW_STALE_CACHE_ENV, raising=False)
    cache = tmp_path / "units.parquet"
    _stale_cache(cache, weights=(7.0, 8.0))
    got = base.load_or_build_raw_units(cache, tmp_path / "no-pums", log=_quiet)
    assert got["weight"].tolist() == [7.0, 8.0]
    out = capsys.readouterr().out
    assert "no PUMS" in out and "Do not publish" in out


def test_a_build_returns_what_a_later_cache_hit_returns(tmp_path, fake_build):
    fake_build["units"] = pd.DataFrame({"weight": [1.0], "obj": [("a", 1)]})     # object column, not a str
    cache = tmp_path / "units.parquet"
    built = base.load_or_build_raw_units(cache, tmp_path, log=_quiet)
    again = base.load_or_build_raw_units(cache, tmp_path, log=_quiet)
    assert built.equals(again)
