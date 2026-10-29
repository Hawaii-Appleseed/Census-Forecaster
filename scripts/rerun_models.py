"""Rerun the Act 24 model chain, running independent steps at the same time.

    python scripts/rerun_models.py

The order the steps depend on:

1. ``forecast_sb3125_enhanced.py --cd 2`` rebuilds the calibrated base every
   later step reads, and writes the enhanced run that
   ``forecast_act24_vs_pre_act46.py`` compares against.
2. Then, at the same time (each reads only step 1's outputs and writes only its
   own ``runs/`` directory): ``forecast_act24_vs_pre_act46.py``,
   ``forecast_sb3125_vs_fy26base.py --cd 2``, ``forecast_cg_rate_options.py``
   and ``forecast_working_family_credits.py``.
3. ``scripts/build_simulator_population.py``, which prices its credit options
   from the working-family-credits run and checks itself against the Act 24
   and capital-gains runs.

Each step's output goes to ``runs/chain_logs/<step>.log``. A failed step stops
the chain before the next stage. Importing the runs into the site is left to
``scripts/build_site.py --import-runs`` (see its docstring), so publishing
stays a separate, reviewed step.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LOGS = REPO / "runs" / "chain_logs"

STAGES: list[list[list[str]]] = [
    [["forecast_sb3125_enhanced.py", "--cd", "2"]],
    [["forecast_act24_vs_pre_act46.py"],
     ["forecast_sb3125_vs_fy26base.py", "--cd", "2"],
     ["forecast_cg_rate_options.py"],
     ["forecast_working_family_credits.py"]],
    [["scripts/build_simulator_population.py"]],
]


def _name(cmd: list[str]) -> str:
    return Path(cmd[0]).stem


def main() -> int:
    LOGS.mkdir(parents=True, exist_ok=True)
    t_all = time.perf_counter()
    for stage in STAGES:
        procs = []
        for cmd in stage:
            log = open(LOGS / f"{_name(cmd)}.log", "w")  # noqa: SIM115 (open while the step runs)
            procs.append((cmd, log, time.perf_counter(),
                          subprocess.Popen([sys.executable, *cmd], cwd=REPO, stdout=log,
                                           stderr=subprocess.STDOUT)))
            print(f"started {' '.join(cmd)}", flush=True)
        failed = []
        for cmd, log, t0, proc in procs:
            rc = proc.wait()
            log.close()
            print(f"{'ok' if rc == 0 else f'FAILED (exit {rc})'}: {' '.join(cmd)} "
                  f"in {time.perf_counter() - t0:.0f}s", flush=True)
            if rc:
                failed.append(cmd)
        if failed:
            print(f"Stopping: see {LOGS.relative_to(REPO)}/"
                  f"{', '.join(_name(c) + '.log' for c in failed)}", file=sys.stderr)
            return 1
    print(f"Chain done in {time.perf_counter() - t_all:.0f}s; logs in {LOGS.relative_to(REPO)}/",
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
