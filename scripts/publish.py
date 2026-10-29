"""Rerun the model chain and rebuild the estimates site from it, in the one safe order.

    python scripts/publish.py               # rerun, import, rebuild the simulator, build pages
    python scripts/publish.py --skip-rerun  # runs/ is already fresh: import and build only

Each run's manifest stamps ``git rev-parse HEAD``, so the published pages cite
the commit that made them. This script refuses to start while anything other
than ``site/`` or a ``*.md`` doc is uncommitted: commit the code first.

The order:

1. Stages 1 and 2 of ``scripts/rerun_models.py`` (the Act 24 base, then the
   four scorers and the simulator population at once).
2. ``build_site.py --import-runs act-24 working-family-credits capital-gains``.
3. ``build_simulator_population.py --from-saved``. It runs here, after the
   import, and not as rerun_models' stage 3, because it checks itself against
   the capital-gains page's *published* CSVs (site/data/capital-gains/) and
   prices its credit options from the working-family-credits run. Run before
   the import, it compares against the previous figures.
4. ``build_site.py --import-runs tax-simulator``, then every page.

It stops there. Before committing: update ``SB3125_CD1_FORECAST.md`` if any
result moved (CLAUDE.md), commit that, then commit ``site/`` on top.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import build_site as site  # noqa: E402
import rerun_models  # noqa: E402


def _uncommitted_code() -> list[str]:
    out = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all", "--", ".",
         ":(exclude)site", ":(exclude)*.md"],
        cwd=REPO, check=True, capture_output=True, text=True).stdout
    return [line for line in out.splitlines() if line.strip()]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skip-rerun", action="store_true",
                    help="use the runs/ already there (still rebuilds the simulator)")
    args = ap.parse_args()

    dirty = _uncommitted_code()
    if dirty:
        print("Uncommitted code; the published pages would cite a commit that does not "
              "hold it. Commit first:", file=sys.stderr)
        print("\n".join(f"  {line}" for line in dirty), file=sys.stderr)
        return 1

    if not args.skip_rerun and rerun_models.main(rerun_models.STAGES[:2]):
        return 1

    for est in (site.ACT24, site.WORKING_FAMILIES, site.CAPITAL_GAINS):
        site.import_runs(est)

    if rerun_models.main([[["scripts/build_simulator_population.py", "--from-saved"]]]):
        return 1
    site.import_runs(site.TAX_SIMULATOR)

    for p in site.build():
        print(f"wrote {p.relative_to(REPO)}")

    changed = subprocess.run(["git", "status", "--short", "--", "site", "tests/simulator"],
                             cwd=REPO, check=True, capture_output=True, text=True).stdout
    print("\nChanged:\n" + (changed or "  nothing\n"))
    print("Next: if a result moved, update SB3125_CD1_FORECAST.md and commit it; "
          "then commit site/ and tests/simulator/.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
