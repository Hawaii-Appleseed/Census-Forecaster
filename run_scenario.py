"""Single entry point for listing and running modeled scenarios.

Phase 2 of DASHBOARD_PIPELINE_SCOPE.md. Before this, answering "what can
this repo model, and how do I run it?" meant reading every forecast_*.py
docstring and knowing each one's private ``--cd``/``--bill`` convention.

    python run_scenario.py --list
    python run_scenario.py --slug sb3125_cd1 --runner quintile
    python run_scenario.py --slug sb3125_cd2 --runner enhanced
    python run_scenario.py --spec reforms/examples/top_rate_14.yaml

``--spec`` scores a user-defined income tax change (the tax simulator's spec
format) against current law, Act 24, via forecast_custom.py.

This module DISPATCHES to the existing scripts — it does not reimplement
any model math, so routing work through it cannot change results. Domain
facts come from tax_modeler.scenarios.registry; this file only knows
which script handles which analysis and how to spell its arguments.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Sequence

from tax_modeler.scenarios.registry import get_scenario, list_scenarios

from _forecast_common import REPO, RUNS_DIR

# SB 3125 conference-draft scripts take --cd 1|2 rather than a slug.
_CD_ARG = {"sb3125_cd1": "1", "sb3125_cd2": "2"}


def _cd_args(slug: str) -> List[str]:
    return ["--cd", _CD_ARG[slug]]


def _bill_args(slug: str) -> List[str]:
    return ["--bill", slug]


@dataclass(frozen=True)
class Runner:
    """One analysis a scenario can be put through."""

    name: str
    script: str
    describe: str
    slugs: Sequence[str]
    build_args: Callable[[str], List[str]]
    #: True when the script writes its own runs/ manifest (Phase 1).
    self_manifests: bool = False
    #: Runner needs the venv interpreter (library imports).
    needs_venv: bool = True


RUNNERS: Dict[str, Runner] = {
    r.name: r for r in [
        Runner(
            name="quintile",
            script="forecast_bill_quintile.py",
            describe="Distributional quintile analysis vs Act 46 (dynamic pipeline)",
            slugs=("sb3125_cd1", "sb3125_sd1", "hb2306_hd1"),
            build_args=_bill_args,
        ),
        Runner(
            name="enhanced",
            script="forecast_sb3125_enhanced.py",
            describe="Fiscal forecast with behavioral response (LOW/MID/HIGH/RECESSION)",
            slugs=("sb3125_cd1", "sb3125_cd2"),
            build_args=_cd_args,
            self_manifests=True,
        ),
        Runner(
            name="fy26base",
            script="forecast_sb3125_vs_fy26base.py",
            describe="ITEP-comparable comparison vs a TY2026-frozen baseline",
            slugs=("sb3125_cd1", "sb3125_cd2"),
            build_args=_cd_args,
            self_manifests=True,
        ),
        Runner(
            name="static_quintile",
            script="forecast_sb3125_static_quintile.py",
            describe="Static-scoring quintile breakdown (CBO/TPC methodology)",
            slugs=("sb3125_cd1", "sb3125_cd2"),
            build_args=_cd_args,
        ),
        Runner(
            name="sensitivity",
            script="forecast_sb3125_sensitivity.py",
            describe="LOW/MID/HIGH sweep over Pareto alpha x REEC demand",
            slugs=("sb3125_cd1", "sb3125_cd2"),
            build_args=_cd_args,
        ),
    ]
}


def runners_for(slug: str) -> List[Runner]:
    return [r for r in RUNNERS.values() if slug in r.slugs]


def print_listing() -> None:
    """Show every registry scenario and the analyses available for it."""
    print("Scenarios (tax_modeler.scenarios.registry):\n")
    for spec in list_scenarios():
        years = "TY%d only" % spec.fixed_year if not spec.takes_year else "TY2027-2031"
        names = [r.name for r in runners_for(spec.slug)]
        print(f"  {spec.slug:26} {spec.label:28} {years:12} overlay={spec.overlay}")
        if spec.statute:
            print(f"  {'':26} {spec.statute}")
        print(f"  {'':26} runners: {', '.join(names) if names else '(none wired yet)'}")
        print()

    print("Runners:\n")
    for r in RUNNERS.values():
        print(f"  {r.name:16} {r.script}")
        print(f"  {'':16} {r.describe}")
        print()
    print("A scenario with no runner is defined in the registry but has no "
          "analysis script wired to it yet.")


def run(slug: str, runner_name: str, *, dry_run: bool = False) -> int:
    try:
        spec = get_scenario(slug)      # validates the slug
    except KeyError as e:
        print(f"ERROR: {e.args[0]}", file=sys.stderr)
        return 2
    try:
        runner = RUNNERS[runner_name]
    except KeyError:
        print(f"ERROR: unknown runner {runner_name!r}. "
              f"Known: {', '.join(RUNNERS)}", file=sys.stderr)
        return 2

    if slug not in runner.slugs:
        print(f"ERROR: runner {runner.name!r} does not support {slug!r}. "
              f"Supported: {', '.join(runner.slugs)}.\n"
              f"Available for {slug!r}: "
              f"{', '.join(r.name for r in runners_for(slug)) or '(none)'}",
              file=sys.stderr)
        return 2

    python = str(REPO / ".venv" / "bin" / "python") if runner.needs_venv else sys.executable
    cmd = [python, str(REPO / runner.script), *runner.build_args(slug)]
    print(f"→ {spec.label} via {runner.name}: {' '.join(cmd)}", flush=True)
    if dry_run:
        return 0

    t0 = time.perf_counter()
    proc = subprocess.run(cmd, cwd=REPO)
    elapsed = time.perf_counter() - t0

    if proc.returncode != 0:
        print(f"FAILED after {elapsed:.1f}s (exit {proc.returncode})", file=sys.stderr)
        return proc.returncode

    print(f"OK in {elapsed:.1f}s", flush=True)
    if not runner.self_manifests:
        # Record the dispatch for runners that don't write their own
        # manifest yet, so every run is still enumerable.
        from tax_modeler.runs import write_run_manifest
        from _forecast_common import cache_provenance
        run_dir = RUNS_DIR / f"{slug}_{runner.name}"
        run_dir.mkdir(parents=True, exist_ok=True)
        write_run_manifest(
            run_dir,
            script=" ".join([runner.script, *runner.build_args(slug)]),
            params={"slug": slug, "runner": runner.name, "label": spec.label,
                    "overlay": spec.overlay},
            inputs={"tax_units_cache": cache_provenance()},
            outputs=[],  # script still writes to /tmp; Phase 1 migration pending
        )
        print(f"Recorded dispatch: {run_dir}", flush=True)
    return 0


def _parse_args():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--list", action="store_true",
                   help="List scenarios and the runners wired to each.")
    p.add_argument("--slug", help="Scenario slug (see --list).")
    p.add_argument("--runner", help="Analysis to run (see --list).")
    p.add_argument("--dry-run", action="store_true",
                   help="Print the command without executing it.")
    p.add_argument("--spec", help="Score an income tax spec file against Act 24 "
                                  "(forecast_custom.py).")
    return p.parse_args()


def run_spec(spec_path: str, *, dry_run: bool = False) -> int:
    python = str(REPO / ".venv" / "bin" / "python")
    # forecast_custom runs from the repo root; resolve the path against the caller's directory
    cmd = [python, str(REPO / "forecast_custom.py"), "--spec", str(Path(spec_path).resolve())]
    print(f"→ spec {spec_path} vs current law (Act 24): {' '.join(cmd)}", flush=True)
    if dry_run:
        return 0
    return subprocess.run(cmd, cwd=REPO).returncode


if __name__ == "__main__":
    args = _parse_args()
    if args.spec:
        sys.exit(run_spec(args.spec, dry_run=args.dry_run))
    if args.list or not args.slug:
        print_listing()
        sys.exit(0 if args.list else 1)
    if not args.runner:
        names = [r.name for r in runners_for(args.slug)]
        print(f"ERROR: --runner required. Available for {args.slug!r}: "
              f"{', '.join(names) or '(none)'}", file=sys.stderr)
        sys.exit(2)
    sys.exit(run(args.slug, args.runner, dry_run=args.dry_run))
