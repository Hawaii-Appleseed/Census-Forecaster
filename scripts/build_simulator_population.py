"""Build the tax simulator's scoring population (TAX_SIMULATOR_SCOPE.md, Phase 2).

    python scripts/build_simulator_population.py              # build + check + outputs
    python scripts/build_simulator_population.py --from-saved # outputs from the saved population
    python scripts/build_simulator_population.py --check-only # check only; writes nothing

Reads data/artifacts/sb3125_calibrated_base.pkl, which
`forecast_sb3125_enhanced.py --cd 2` writes; run that first so the simulator
and the Act 24 page share a population and a model version. Writes
runs/tax_simulator/ (population.npz + population.json for Python, the web
files for the estimates site, golden kernel fixtures, manifest.json), then
checks that scoring Act 24 against Act 46 on the population reproduces the
Act 24 run (runs/sb3125_cd2_enhanced/): the static and post-response bracket
change for LOW/MID/HIGH, and every MID distribution table. Exits non-zero if
it does not, before writing anything else; so after a change that moves the
Act 24 figures, re-run `forecast_sb3125_enhanced.py --cd 2` first.

--from-saved skips the ~10-minute projection and rewrites the web files,
golden fixtures and preset results with the current scoring code (after a
change to the kernel or the behavioral response, say). The manifest then
records the saved population's own build under inputs.population.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
import warnings
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from _forecast_common import CALIBRATED_PKL, RUNS_DIR, cache_provenance  # noqa: E402

OUT = RUNS_DIR / "tax_simulator"
ENHANCED = RUNS_DIR / "sb3125_cd2_enhanced"


def check_reproduces_act24(pop, out_dir: Path) -> list[str]:
    """Score Act 24 vs Act 46 on the population; compare with the Act 24 run."""
    import numpy as np
    import pandas as pd

    from tax_modeler.config.tax_system_config import TaxSystemRegistry
    from tax_modeler.simulator.score import REPORT_NAMES, score_systems

    res = score_systems(pop, TaxSystemRegistry.get_act46_system,
                        TaxSystemRegistry.get_sb3125_cd2_system, distribution_years=pop.years)
    problems = []
    fiscal = pd.read_csv(ENHANCED / "enhanced.csv")
    for r in res["revenue"]:
        row = fiscal[(fiscal.scenario == r["scenario"].upper()) & (fiscal.tax_year == r["tax_year"])].iloc[0]
        for ours, theirs in (("baseline_$M", "act46_baseline_$M"),
                             ("static_$M", "bracket_delta_static_$M"),
                             ("behavioral_$M", "bracket_delta_post_$M")):
            if round(r[ours], 2) != round(float(row[theirs]), 2):
                problems.append(f"{r['scenario']} {r['tax_year']} {ours}: {r[ours]:.4f} vs {row[theirs]}")
    quint = pd.read_csv(ENHANCED / "quintile.csv")
    klass = pd.read_csv(ENHANCED / "bracket.csv")
    for y, tables in res["distribution"].items():
        for kind, ref, key in (("quintile", quint, "quintile"), ("income_class", klass, "income_bracket")):
            ref_y = ref[ref.tax_year == y].set_index(key)
            for g in tables[kind]:
                refrow = ref_y.loc[g["group"]]
                for ours, v in g.items():
                    col = REPORT_NAMES.get(ours, ours)      # the Act 24 run's name
                    if ours == "group" or col not in refrow.index or col.startswith("pct_"):
                        # pct_* in the Act 24 tables include credit claimants;
                        # the bracket-only shares are checked by the kernel tests.
                        continue
                    if not np.isclose(v, float(refrow[col]), rtol=1e-9, atol=1e-6):
                        problems.append(f"TY{y} {kind} {g['group']} {col}: {v} vs {refrow[col]}")
    (out_dir / "act24_reproduction.json").write_text(
        __import__("json").dumps({"problems": problems, "revenue": res["revenue"]}, indent=1))
    return problems


def write_preset_results(golden_path: Path, out: Path) -> None:
    """The presets' results, as the model computed them, for the page's
    first render and its no-JavaScript table (from the golden fixtures, which
    score every preset with the real pipeline)."""
    import gzip
    import json

    from tax_modeler.simulator.presets import presets

    golden = json.loads(gzip.decompress(golden_path.read_bytes()))
    by_name = {c["name"]: c for c in golden["cases"]}
    out.write_text(json.dumps({p["name"]: {"label": p["label"], "first_year": p["first_year"],
                                           **by_name[p["name"]]["expected"]}
                               for p in presets()}) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--check-only", action="store_true",
                      help="load the saved population and re-run the Act 24 check; write nothing else")
    mode.add_argument("--from-saved", action="store_true",
                      help="load the saved population; rewrite web files, golden fixtures and preset results")
    args = ap.parse_args()
    warnings.filterwarnings("ignore")
    logging.disable(logging.WARNING)

    from datetime import datetime, timezone

    from tax_modeler.artifacts import _git_sha
    from tax_modeler.runs import write_run_manifest
    from tax_modeler.simulator.population import Population, build_population

    t0 = time.perf_counter()
    if args.check_only or args.from_saved:
        pop = Population.load(OUT)
    else:
        if not CALIBRATED_PKL.exists():
            print(f"ERROR: {CALIBRATED_PKL} missing; run forecast_sb3125_enhanced.py --cd 2 first.")
            return 1
        print("Building the scoring population (LOW/MID/HIGH x TY2027-2031)...", flush=True)
        pop = build_population(CALIBRATED_PKL)
        pop.meta["built"] = {"created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                             "git_sha": _git_sha()}
        pop.save(OUT)
        print(f"  {pop.n_units:,} units, {pop.meta['n_households']:,} households "
              f"({time.perf_counter() - t0:.0f}s)", flush=True)

    print("Checking it reproduces the Act 24 run...", flush=True)
    problems = check_reproduces_act24(pop, OUT)
    if problems:
        print(f"FAILED: {len(problems)} differences, first: {problems[:5]}")
        return 1
    print("  Act 24 vs Act 46 reproduced: LOW/MID/HIGH revenue, MID distribution", flush=True)
    if args.check_only:
        return 0

    from tax_modeler.simulator.web import write_web_files
    from tax_modeler.simulator.golden import write_golden

    kernel = (REPO / "site/assets/simulator/kernel.js").read_bytes()
    web = write_web_files(pop, OUT / "web", kernel_source=kernel)
    print(f"  web files: {', '.join(f'{p.name} {p.stat().st_size / 1e6:.2f} MB' for p in web)}", flush=True)
    golden = write_golden(pop, OUT / "golden.json.gz")
    print(f"  golden fixtures: {golden}", flush=True)
    write_preset_results(OUT / "golden.json.gz", OUT / "web" / "presets_results.json")

    write_run_manifest(
        OUT, script="scripts/build_simulator_population.py",
        params={"years": pop.meta["years"], "scenarios": pop.meta["scenarios"]},
        inputs={"calibrated_base": pop.meta["calibrated_base"], "tax_units_cache": cache_provenance(),
                "population": pop.meta.get("built")},
    )
    print(f"Done in {time.perf_counter() - t0:.0f}s -> {OUT}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
