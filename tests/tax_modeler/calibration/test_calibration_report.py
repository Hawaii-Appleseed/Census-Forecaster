"""``calibration.report``: the record of what the rake did to the weights —
convergence, residuals, clip/cap events, dropped bins, weight dispersion,
source years — and its capture in ``ipf_orchestrator.calibrate_via_rake``.
"""
from __future__ import annotations

import json
import logging

import numpy as np
import pandas as pd
import pytest

from tax_modeler.calibration import report as report_mod
from tax_modeler.calibration.ipf_orchestrator import (
    IPFCalibrationOrchestrator,
    apply_ipf_calibration_via_rake,
)
from tax_modeler.calibration.report import (
    CalibrationReport,
    ClipEvent,
    DroppedBin,
    MarginResidual,
    WeightDiagnostics,
    warn_once_on_year_mismatch,
)


# ---------------------------------------------------------------------------
# Weight-dispersion math
# ---------------------------------------------------------------------------

def test_equal_weights_have_no_design_effect():
    d = WeightDiagnostics.from_weights([2.0, 2.0, 2.0, 2.0])
    assert d.n == 4
    assert d.kish_deff == pytest.approx(1.0)
    assert d.effective_n == pytest.approx(4.0)
    assert d.max_mean_ratio == pytest.approx(1.0)
    assert d.min_mean_ratio == pytest.approx(1.0)
    assert d.top1_share == pytest.approx(0.25)  # ceil(1% of 4) = 1 unit of 4


def test_kish_deff_and_ess_on_a_tiny_vector():
    # w = [1, 3]: sum 4, sum of squares 10 -> deff = 2 * 10 / 16 = 1.25, ESS = 16 / 10 = 1.6
    d = WeightDiagnostics.from_weights(np.array([1.0, 3.0]))
    assert d.kish_deff == pytest.approx(1.25)
    assert d.effective_n == pytest.approx(1.6)
    assert d.effective_n == pytest.approx(d.n / d.kish_deff)
    assert d.max_mean_ratio == pytest.approx(1.5)
    assert d.min_mean_ratio == pytest.approx(0.5)
    assert d.top1_share == pytest.approx(0.75)


def test_zero_weights_count_in_n_but_not_in_min_ratio():
    d = WeightDiagnostics.from_weights([0.0, 1.0, 1.0])
    assert d.n == 3 and d.n_zero == 1
    assert d.min_mean_ratio == pytest.approx(1.0 / (2.0 / 3.0))
    assert d.kish_deff == pytest.approx(3 * 2 / 4)


def test_empty_weights_do_not_raise():
    d = WeightDiagnostics.from_weights([])
    assert d.n == 0 and np.isnan(d.kish_deff)


# ---------------------------------------------------------------------------
# Residuals, serialisation, rendering
# ---------------------------------------------------------------------------

def test_margin_residual_relative_and_zero_target():
    assert MarginResidual("m", "k", 100.0, 110.0).relative == pytest.approx(0.10)
    assert np.isnan(MarginResidual("m", "k", 0.0, 5.0).relative)


def _sample_report(converged: bool) -> CalibrationReport:
    return CalibrationReport(
        stage="base_ipf_rake", converged=converged, iterations=30, max_iterations=30,
        tolerance=0.01, final_max_dev=0.08,
        convergence_mode="tolerance" if converged else "max_iterations",
        margins=[MarginResidual("dotax_filer_count", "$0k-$10k", 100.0, 108.0,
                                source="DOTAX", source_year=2023),
                 MarginResidual("soi_agi_M", "$0k-$25k", 50.0, 50.0,
                                source="SOI", source_year=2022)],
        clip_events=[ClipEvent("tax_total_step", "$50k-$75k", 7.0, 5.0, (0.2, 5.0), 3)],
        dropped_bins=[DroppedBin("tax_total_margin", "$0k-$10k", "credits exceed tax", 3.0, 0.0)],
        weights_before=WeightDiagnostics.from_weights([1, 1, 1, 1]),
        weights_after=WeightDiagnostics.from_weights([1, 3, 1, 3]),
        source_years={"dotax_filer_count": 2023, "soi_agi_M": 2022},
        warnings=[] if converged else ["Joint IPF did not converge in 30 outer iterations"],
    )


def test_to_dict_is_json_serialisable_and_round_trips(tmp_path):
    rep = _sample_report(converged=False)
    d = rep.to_dict()
    json.dumps(d)  # no NaN / tuple surprises
    assert d["margins"][0]["relative"] == pytest.approx(0.08)
    assert d["margin_summary"]["dotax_filer_count"]["max_abs_relative"] == pytest.approx(0.08)
    assert d["margin_summary"]["dotax_filer_count"]["source_year"] == 2023
    back = CalibrationReport.from_json(rep.to_json(tmp_path / "r.json"))
    assert back.converged is False
    assert back.clip_events[0].bounds == (0.2, 5.0)
    assert back.weights_after.kish_deff == pytest.approx(rep.weights_after.kish_deff)
    assert [m.key for m in back.margins] == [m.key for m in rep.margins]


def test_markdown_shows_nonconvergence_clips_and_source_years():
    md = _sample_report(converged=False).to_markdown()
    assert "DID NOT CONVERGE" in md
    assert "max_iterations" in md
    assert "Clip/cap events: 1" in md
    assert "asked ×7.000, applied ×5.000" in md
    assert "TY2023" in md and "TY2022" in md
    assert "Dropped bins" in md
    assert "Kish deff" in md
    assert "converged" in _sample_report(converged=True).to_markdown()


def test_children_nest_in_markdown_and_nonconvergence_propagates():
    parent = _sample_report(converged=True)
    parent.children.append(CalibrationReport(
        stage="year_recalibration", converged=False, iterations=100, max_iterations=100,
        tolerance=0.005, target_year=2027, statute_vs_cor_wedge=0.97,
        statutory_tax_M=2900.0, cor_tax_M=2990.0,
    ))
    assert parent.any_nonconverged()
    md = parent.to_markdown()
    assert "year_recalibration TY2027" in md
    assert "Statute-vs-COR wedge:** 0.9700" in md
    assert CalibrationReport.from_dict(parent.to_dict()).children[0].target_year == 2027


# ---------------------------------------------------------------------------
# Source-year mismatch warning
# ---------------------------------------------------------------------------

def test_year_mismatch_warns_once_per_combination(caplog):
    report_mod._WARNED_YEAR_MISMATCH.clear()
    years = {"dotax_tax_M": 2023, "soi_agi_M": 2022}
    with caplog.at_level(logging.WARNING, logger="tax_modeler.calibration.report"):
        msg1 = warn_once_on_year_mismatch(years)
        msg2 = warn_once_on_year_mismatch(dict(years))
    assert msg1 and msg1 == msg2  # the message is returned both times for the report
    assert sum("different tax years" in r.message for r in caplog.records) == 1
    assert warn_once_on_year_mismatch({"a": 2023, "b": 2023, "c": None}) is None


# ---------------------------------------------------------------------------
# Capture inside the orchestrator
# ---------------------------------------------------------------------------

def _units(n: int = 4000, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    agi = np.exp(rng.normal(10.8, 1.0, n))
    return pd.DataFrame({
        "weight": rng.uniform(80, 240, n),
        "agi": agi,
        "filing_status": rng.choice(
            ["single", "married_filing_jointly", "head_of_household",
             "married_filing_separately"], n, p=[0.5, 0.35, 0.1, 0.05]),
        "hi_state_tax": np.maximum(0.0, agi * 0.05 - 800.0),
    })


def test_weighted_sum_step_records_clip_events_only_when_the_cap_binds():
    orch = IPFCalibrationOrchestrator()
    df = pd.DataFrame({"weight": [1.0, 1.0], "agi": [5_000.0, 50_000.0],
                       "val": [100.0, 100.0]})
    events: list = []
    out = orch.ipf_adjust_weights_to_weighted_sum(
        df, {(0, 10_000): 300.0, (10_000, 100_000): 105.0}, value_col="val",
        max_adjustment=1.1, scale=1.0, events=events, stage="s", iteration=4,
    )
    assert len(events) == 1
    ev = events[0]
    assert ev.stage == "s" and ev.key == "$0k-$10k" and ev.iteration == 4
    assert ev.requested == pytest.approx(3.0) and ev.applied == pytest.approx(1.1)
    assert ev.bounds == pytest.approx((1 / 1.1, 1.1))
    assert out.loc[0, "weight"] == pytest.approx(1.1)
    assert out.loc[1, "weight"] == pytest.approx(1.05)


def test_nonconvergence_is_in_the_report_not_only_the_log():
    df = _units()
    out, rep = IPFCalibrationOrchestrator().calibrate_via_rake(
        df, calibrate_soi_agi=False, max_outer_iters=1, outer_tolerance=1e-9,
        return_report=True,
    )
    assert rep.converged is False
    assert rep.convergence_mode == "max_iterations"
    assert rep.iterations == 1 and rep.max_iterations == 1
    assert rep.final_max_dev is not None and rep.final_max_dev > 1e-9
    assert any("did not converge" in w for w in rep.warnings)
    assert "DID NOT CONVERGE" in rep.to_markdown()
    # attached to the frame too, as a dict
    assert out.attrs["calibration_report"]["converged"] is False
    assert len(out) == len(df)


def test_report_records_margins_dropped_bins_and_source_years():
    _, rep = IPFCalibrationOrchestrator().calibrate_via_rake(
        _units(), calibrate_soi_agi=False, max_outer_iters=3, return_report=True,
    )
    names = rep.margin_names
    assert names == ["dotax_filer_count", "dotax_filing_status", "dotax_tax_M"]
    assert rep.source_years == {k: 2023 for k in names}
    # 15 brackets + 4 statuses + 15 tax bins
    assert len(rep.margins) == 15 + 4 + 15
    top = next(m for m in rep.margins if m.margin == "dotax_filer_count" and m.key == "$1000k+")
    assert top.counted is False  # synthesis fills it; excluded from convergence
    # The synthetic tax is far below DOTAX's per-filer tax, so bins get dropped
    # by the feasibility rule, and each dropped bin is uncounted in the margin.
    assert rep.dropped_bins, "expected the 1.25x feasibility rule to drop bins"
    dropped_keys = {b.key for b in rep.dropped_bins}
    for m in rep.margins:
        if m.margin == "dotax_tax_M" and m.key in dropped_keys:
            assert m.counted is False
    assert all(b.stage == "tax_total_margin" for b in rep.dropped_bins)
    assert rep.weights_before.n == rep.weights_after.n == 4000
    assert rep.weights_after.effective_n <= rep.weights_after.n


def test_soi_margin_carries_its_own_year_and_the_mismatch_warning():
    pytest.importorskip("pums_estimator")
    try:
        _, rep = apply_ipf_calibration_via_rake(
            _units(), calibrate_soi_agi=True, return_report=True,
        )
    except FileNotFoundError:
        pytest.skip("SOI extract not on disk")
    if "soi_agi_M" not in rep.source_years:
        pytest.skip("SOI margin disabled")
    assert rep.source_years["soi_agi_M"] == 2022
    assert rep.source_years["dotax_tax_M"] == 2023
    assert any("different tax years" in w for w in rep.warnings)
    assert all(e.stage in {"tax_total_step", "soi_agi_step"} for e in rep.clip_events)


def test_pipeline_calibrate_returns_report_and_attaches_it():
    from tax_modeler.pipeline import calibrate
    df = _units(2000)
    plain = calibrate(df)
    assert "calibration_report" in plain.attrs
    out, rep = calibrate(df, return_report=True)
    assert isinstance(rep, CalibrationReport)
    assert rep.stage == "base_ipf_rake"
    assert out.attrs["calibration_report"]["iterations"] == rep.iterations
