"""Tests for the run_scenario dispatcher.

Dispatch correctness only — that the right script is invoked with the
right arguments. The scripts themselves are covered by their own goldens.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

sys.path.insert(0, str(REPO_ROOT / "scripts"))   # _forecast_common
_spec = importlib.util.spec_from_file_location(
    "run_scenario", REPO_ROOT / "scripts" / "run_scenario.py"
)
run_scenario = importlib.util.module_from_spec(_spec)
# Register before exec: @dataclass resolves type hints via
# sys.modules[cls.__module__], which is None for an unregistered module.
sys.modules["run_scenario"] = run_scenario
_spec.loader.exec_module(run_scenario)

from tax_modeler.scenarios.registry import SCENARIO_SPECS  # noqa: E402


def test_every_runner_slug_exists_in_registry():
    """A runner cannot advertise a scenario the registry doesn't define."""
    for runner in run_scenario.RUNNERS.values():
        for slug in runner.slugs:
            assert slug in SCENARIO_SPECS, f"{runner.name} -> unknown slug {slug}"


def test_every_runner_script_exists():
    for runner in run_scenario.RUNNERS.values():
        assert (REPO_ROOT / runner.script).exists(), runner.script


def test_arg_spelling_per_runner():
    """CD scripts take --cd 1|2; the bill script takes --bill <slug>."""
    assert run_scenario.RUNNERS["enhanced"].build_args("sb3125_cd2") == ["--cd", "2"]
    assert run_scenario.RUNNERS["enhanced"].build_args("sb3125_cd1") == ["--cd", "1"]
    assert run_scenario.RUNNERS["quintile"].build_args("hb2306_hd1") == [
        "--bill", "hb2306_hd1",
    ]


def test_runners_for_lookup():
    names = {r.name for r in run_scenario.runners_for("sb3125_cd1")}
    assert names == {"quintile", "enhanced", "fy26base", "static_quintile", "sensitivity"}
    assert [r.name for r in run_scenario.runners_for("hb2306_hd1")] == ["quintile"]
    assert run_scenario.runners_for("millionaire_tax") == []


def test_dry_run_succeeds_without_executing():
    assert run_scenario.run("sb3125_cd1", "quintile", dry_run=True) == 0


def test_unsupported_pairing_is_rejected():
    # hb2306 has no fiscal 'enhanced' runner — must fail cleanly, not dispatch.
    assert run_scenario.run("hb2306_hd1", "enhanced", dry_run=True) == 2


def test_unknown_slug_and_runner_rejected():
    assert run_scenario.run("no_such_bill", "quintile", dry_run=True) == 2
    assert run_scenario.run("sb3125_cd1", "no_such_runner", dry_run=True) == 2


def test_listing_renders(capsys):
    run_scenario.print_listing()
    out = capsys.readouterr().out
    assert "sb3125_cd1" in out and "millionaire_tax" in out
    assert "(none wired yet)" in out  # scenarios without runners are surfaced
    for name in run_scenario.RUNNERS:
        assert name in out
