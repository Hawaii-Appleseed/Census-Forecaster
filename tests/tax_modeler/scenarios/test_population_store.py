"""PopulationStore: the chain's shared Act 24 populations."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tax_modeler.scenarios.population_store import PopulationStore

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _frame(n=5, seed=0):
    rng = np.random.default_rng(seed)
    return pd.DataFrame({"weight": rng.uniform(1, 9, n), "income": rng.uniform(0, 1e5, n),
                         "deps": [[{"age": 3}]] * n})


def test_a_result_is_computed_once_per_key_and_restores_the_generator(tmp_path):
    calls = []

    def compute():
        calls.append(1)
        np.random.seed(7)
        np.random.random()           # the computation moves numpy's generator
        return _frame()

    first = PopulationStore(tmp_path, "k" * 64).get("pop", compute)
    after_compute = np.random.random()
    np.random.seed(123)              # elsewhere, the generator is somewhere else
    again = PopulationStore(tmp_path, "k" * 64).get("pop", compute)
    pd.testing.assert_frame_equal(again, first)
    assert len(calls) == 1
    assert np.random.random() == after_compute   # restored to where compute left it


def test_a_new_key_recomputes_and_clears_the_old_entries(tmp_path):
    calls = []
    PopulationStore(tmp_path, "a" * 64).get("pop", lambda: calls.append(1) or _frame())
    PopulationStore(tmp_path, "b" * 64).get("pop", lambda: calls.append(1) or _frame())
    assert len(calls) == 2
    assert [p.name for p in tmp_path.iterdir()] == ["b" * 16]


def test_only_unchanged_frames_the_store_produced_are_named(tmp_path):
    store = PopulationStore(tmp_path, "c" * 64)
    frame = store.get("pop", _frame)
    assert store.name_of(frame) == "pop"
    assert store.name_of(frame.copy()) is None        # a copy is not the stored frame
    frame.loc[0, "income"] = -1.0                      # changed in place
    assert store.name_of(frame) is None


def test_store_backed_population_matches_and_is_loaded(tmp_path, monkeypatch):
    """build_units / project_units through a store give the frames they give
    without one, and a second store with the same key loads them."""
    import tax_modeler.scenarios.act24_population as pop
    from tax_modeler.artifacts import load_canonical_deduction_params, save_calibrated_base
    from tax_modeler.pipeline import compute_base_tax, enrich_for_credits
    from tax_modeler.units.constructor import TaxUnitConstructor

    raw = TaxUnitConstructor(pd.read_parquet(FIXTURES / "synthetic_pums_persons.parquet"),
                             pd.read_parquet(FIXTURES / "synthetic_pums_households.parquet"),
                             use_soi_calibration=False, progress_bar=False
                             ).create_rule_based_units(parallel=False)
    ded = load_canonical_deduction_params()
    pkl = tmp_path / "base.pkl"
    save_calibrated_base(compute_base_tax(enrich_for_credits(raw), deduction_params=ded,
                                          tax_year=2023), pkl, deduction_params=ded, tax_year=2023)

    def run(store):
        if store is None:
            from tax_modeler.artifacts import load_calibrated_base
            base, d, _ = load_calibrated_base(pkl)
        else:
            base, d, _ = store.base(pkl)
        units, k = pop.build_units(base, alpha=1.5, top_premium=0.010, ded_params=d,
                                   cal_tax_year=2023, store=store)
        return units, k, pop.project_units(units, year=2028, top_premium=0.010, store=store)

    plain = run(None)
    stored = run(PopulationStore(tmp_path / "store", "d" * 64))
    monkeypatch.setattr(pop, "_build_units", lambda *a, **k: pytest.fail("rebuilt"))
    monkeypatch.setattr(pop, "_project_units", lambda *a, **k: pytest.fail("reprojected"))
    loaded = run(PopulationStore(tmp_path / "store", "d" * 64))
    for got in (stored, loaded):
        pd.testing.assert_frame_equal(got[0], plain[0])
        assert got[1] == plain[1]
        pd.testing.assert_frame_equal(got[2], plain[2])


def test_rerun_models_shares_the_cores_within_a_stage(monkeypatch):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
    import rerun_models

    monkeypatch.delenv("CF_MAX_WORKERS", raising=False)
    monkeypatch.setattr(rerun_models.os, "cpu_count", lambda: 10)
    assert rerun_models._stage_env({}, 5)["CF_MAX_WORKERS"] == "2"
    assert rerun_models._stage_env({}, 1)["CF_MAX_WORKERS"] == "10"
    monkeypatch.setenv("CF_MAX_WORKERS", "3")
    assert "CF_MAX_WORKERS" not in rerun_models._stage_env({}, 5)
