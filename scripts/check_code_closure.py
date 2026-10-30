"""Check that each code-keyed cache's key covers the code its build imports.

    python scripts/check_code_closure.py            # every cache, ~2 min of CPU
    python scripts/check_code_closure.py golden     # one

A cache keyed with ``fingerprint(..., code=ENTRIES)`` under
``CF_NARROW_FINGERPRINT=1`` hashes only the workspace modules
``code_closure(*ENTRIES)`` finds by reading the source. This runs each cached
build in a fresh process, with nothing of the model loaded beforehand, writing
to a temporary directory, and fails if the build imported a workspace module
outside that closure: an edit to such a module would keep a stale cache.
(``closure_guard`` checks the same during the chain, but only sees modules the
step had not already loaded.) Needs the inputs a chain run leaves: the PUMS
files, ``runs/tax_simulator`` and ``runs/working_family_credits``.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))


def working_family(tmp: Path) -> tuple:
    import _forecast_common
    import forecast_working_family_credits as wfc

    _forecast_common.ARTIFACT_DIR = tmp   # an empty cache: the build runs
    wfc.load_population()
    return wfc.BUILD_CODE


def golden(tmp: Path) -> tuple:
    import build_simulator_population as bsp
    from tax_modeler.simulator.credits import load_credit_table
    from tax_modeler.simulator.golden import write_golden
    from tax_modeler.simulator.population import Population

    pop = Population.load(bsp.OUT)
    credit_table = load_credit_table(bsp.RUNS_DIR / "working_family_credits", pop.years)
    write_golden(pop, tmp / "golden.json.gz", credit_table=credit_table)
    return bsp.GOLDEN_CODE


CHECKS = {"working_family": working_family, "golden": golden}


def run_one(name: str) -> int:
    import _forecast_common as fc

    with tempfile.TemporaryDirectory() as tmp:
        code = CHECKS[name](Path(tmp))
    closure = fc.code_closure(*code)
    if closure is None:
        print(f"{name}: closure untraceable, the key hashes the whole packages")
        return 0
    # This file and the scripts it calls into; their own source is in the key as a Path part.
    harness = {Path(__file__).resolve(), REPO / "_forecast_common.py",
               REPO / "forecast_working_family_credits.py", REPO / "scripts" / "build_simulator_population.py"}
    allowed = set(closure) | harness
    loaded = {Path(f).resolve() for m in list(sys.modules.values())
              if (f := getattr(m, "__file__", None))}
    missed = sorted(str(f.relative_to(REPO)) for f in loaded
                    if fc._is_workspace_file(f) and f not in allowed)
    used = sum(1 for f in loaded if fc._is_workspace_file(f))
    if missed:
        print(f"{name}: FAIL — imported outside its key's closure:\n  " + "\n  ".join(missed))
        return 1
    print(f"{name}: ok — imported {used} workspace modules, all among the {len(closure)} in its key")
    return 0


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--in-process":
        return run_one(sys.argv[2])
    names = sys.argv[1:] or list(CHECKS)
    env = {**os.environ, "CF_NARROW_FINGERPRINT": "1"}
    return max(subprocess.run([sys.executable, __file__, "--in-process", n], env=env, cwd=REPO).returncode
               for n in names)


if __name__ == "__main__":
    sys.exit(main())
