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
   ``scripts/build_simulator_population.py --population-only`` builds the
   simulator's population beside them, since it too needs only step 1.
3. ``scripts/build_simulator_population.py --from-saved``, which prices its
   credit options from the working-family-credits run and checks itself
   against the Act 24 and capital-gains runs.

The steps run with Python's bytecode cache on, kept under
``runs/.pycache`` (``PYTHONPYCACHEPREFIX``), even where the shell turns it
off (``PYTHONDONTWRITEBYTECODE``): otherwise every step recompiles pandas,
numpy, scipy and the model from source, about a second per process.

Each step's output goes to ``runs/chain_logs/<step>.log``. A failed step stops
the chain before the next stage. Importing the runs into the site is left to
``scripts/build_site.py --import-runs`` (see its docstring), so publishing
stays a separate, reviewed step.
"""
from __future__ import annotations

import os
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
     ["forecast_working_family_credits.py"],
     ["scripts/build_simulator_population.py", "--population-only"]],
    [["scripts/build_simulator_population.py", "--from-saved"]],
]


def _name(cmd: list[str]) -> str:
    return "_".join([Path(cmd[0]).stem, *(a.lstrip("-") for a in cmd[1:])])


def _env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k != "PYTHONDONTWRITEBYTECODE"}
    env.setdefault("PYTHONPYCACHEPREFIX", str(REPO / "runs" / ".pycache"))
    return env


def main(stages: list[list[list[str]]] = STAGES) -> int:
    LOGS.mkdir(parents=True, exist_ok=True)
    env = _env()
    t_all = time.perf_counter()
    for stage in stages:
        procs = []
        for cmd in stage:
            log = open(LOGS / f"{_name(cmd)}.log", "w")  # noqa: SIM115 (open while the step runs)
            procs.append((cmd, log, time.perf_counter(),
                          subprocess.Popen([sys.executable, *cmd], cwd=REPO, stdout=log,
                                           stderr=subprocess.STDOUT, env=env)))
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
