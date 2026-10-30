"""Tests for the multi-family dashboard builder (Phase 4).

The builder must be scenario-agnostic: it reads manifests and tidy tables,
so a new scenario appears without touching this code. These tests pin that
property and the graceful handling of a repo with no runs yet.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

_spec = importlib.util.spec_from_file_location(
    "build_dashboard", REPO_ROOT / "scripts" / "build_dashboard.py"
)
build_dashboard = importlib.util.module_from_spec(_spec)
sys.modules["build_dashboard"] = build_dashboard
_spec.loader.exec_module(build_dashboard)

from tax_modeler.runs import write_run_manifest  # noqa: E402


@pytest.fixture
def fake_runs(tmp_path):
    runs = tmp_path / "runs"
    run = runs / "sb3125_cd2_enhanced"
    run.mkdir(parents=True)
    pd.DataFrame({
        "scenario": ["MID", "MID", "LOW", "LOW"],
        "tax_year": [2027, 2028, 2027, 2028],
        "metric": ["total_impact_$M"] * 4,
        "value": [124.9, 139.5, 100.0, 110.0],
    }).to_csv(run / "fiscal_tidy.csv", index=False)
    write_run_manifest(run, script="chain/forecast_sb3125_enhanced.py --cd 2",
                       params={"cd": "2"})
    return runs


@pytest.fixture
def fake_reports(tmp_path):
    reports = tmp_path / "reports"
    d = reports / "poverty_impact_2025_tier3"
    d.mkdir(parents=True)
    pd.DataFrame({
        "tax_year": [2025, 2025],
        "scenario": ["no_eitc", "no_ctc"],
        "population": ["all", "all"],
        "metric": ["persons_lifted", "persons_lifted"],
        "value": [20457.0, 16669.0],
    }).to_csv(d / "by_state_long.csv", index=False)
    return reports


def test_index_shape(fake_runs, fake_reports):
    index = build_dashboard.build_index(fake_runs, fake_reports)
    assert len(index["runs"]) == 1
    run = index["runs"][0]
    assert run["id"] == "sb3125_cd2_enhanced"
    assert run["script"] == "chain/forecast_sb3125_enhanced.py --cd 2"
    assert len(run["fiscal"]) == 4
    assert index["poverty"]["id"] == "poverty_impact_2025_tier3"
    assert len(index["poverty"]["rows"]) == 2


def test_slug_and_label_resolved_from_registry(fake_runs, fake_reports):
    """Run id -> registry slug -> human label, without a hardcoded map."""
    run = build_dashboard.build_index(fake_runs, fake_reports)["runs"][0]
    assert run["slug"] == "sb3125_cd2"
    assert run["label"] == "SB 3125 CD2"
    assert run["overlay"] == "vintage"


def test_slug_inference_prefers_longest_match():
    # 'sb3125_cd2' must win over any shorter prefix that also matches.
    assert build_dashboard._slug_from_run_id("sb3125_cd2_enhanced") == "sb3125_cd2"
    assert build_dashboard._slug_from_run_id("hb2306_hd1_quintile") == "hb2306_hd1"
    assert build_dashboard._slug_from_run_id("mystery_run") is None


def test_every_registry_scenario_listed(fake_runs, fake_reports):
    from tax_modeler.scenarios.registry import SCENARIO_SPECS
    index = build_dashboard.build_index(fake_runs, fake_reports)
    assert {s["slug"] for s in index["scenarios"]} == set(SCENARIO_SPECS)


def test_empty_repo_renders_without_crashing(tmp_path):
    """No runs and no reports must still produce a usable page."""
    index = build_dashboard.build_index(tmp_path / "runs", tmp_path / "reports")
    assert index["runs"] == []
    assert index["poverty"] is None
    html = build_dashboard.render_html(index)
    assert "<!doctype html>" in html
    assert "run_scenario.py" in html  # actionable empty state


def test_render_embeds_payload_and_palette(fake_runs, fake_reports):
    from tax_modeler.reporting.palette import TEAL
    index = build_dashboard.build_index(fake_runs, fake_reports)
    html = build_dashboard.render_html(index)
    assert TEAL in html                       # brand palette applied
    assert 'id="payload"' in html
    start = html.index('id="payload" type="application/json">') + len(
        'id="payload" type="application/json">')
    payload = json.loads(html[start:html.index("</script>", start)])
    assert payload["runs"][0]["label"] == "SB 3125 CD2"


def test_all_four_tabs_present(fake_runs, fake_reports):
    html = build_dashboard.render_html(
        build_dashboard.build_index(fake_runs, fake_reports))
    for tab in ("fiscal", "dist", "poverty", "catalog"):
        assert f'data-tab="{tab}"' in html
        assert f'id="tab-{tab}"' in html
