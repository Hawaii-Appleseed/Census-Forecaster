"""Score a user-defined income tax change against current law (Act 24).

Usage:
  python forecast_custom.py --spec reforms/examples/top_rate_14.yaml
  python forecast_custom.py --spec my.json --distribution-year 2027 --distribution-year 2031

The spec format is tax_modeler.reform.income_tax_spec (the same JSON the
estimates-site simulator downloads). Scores it on the simulator population
(runs/tax_simulator/, built by scripts/build_simulator_population.py after
forecast_sb3125_enhanced.py --cd 2) with the model's own functions: revenue
for TY2027-2031 in the LOW / MID / HIGH scenarios, static and after the
behavioral response, and MID distribution tables. Baseline: current law,
Act 24 -- not Act 46, which the forecast_sb3125_* scripts score against.

Outputs runs/custom/<spec name>/: revenue.csv, quintile_<year>.csv,
income_class_<year>.csv, spec.json, manifest.json.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd

from _forecast_common import RUNS_DIR, silence_noise


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spec", required=True, help="spec file (.yaml/.yml/.json)")
    ap.add_argument("--population", default=str(RUNS_DIR / "tax_simulator"),
                    help="population directory (default: runs/tax_simulator)")
    ap.add_argument("--distribution-year", type=int, action="append", default=None,
                    help="tax year(s) for the distribution tables (default: first_year)")
    ap.add_argument("--out", default=None, help="output directory (default: runs/custom/<name>)")
    args = ap.parse_args(argv)
    silence_noise()

    from tax_modeler.errors import ConfigError
    from tax_modeler.reform.income_tax_spec import load_spec
    from tax_modeler.runs import write_run_manifest
    from tax_modeler.simulator.population import Population
    from tax_modeler.simulator.score import score_spec

    import yaml

    try:
        spec = load_spec(args.spec)
    except OSError as e:
        print(f"ERROR: cannot read {args.spec}: {e.strerror or e}", file=sys.stderr)
        return 2
    except (ConfigError, ValueError, yaml.YAMLError) as e:   # ValueError covers bad JSON
        print(f"ERROR: invalid spec: {e}", file=sys.stderr)
        return 2
    pop_dir = Path(args.population)
    if not (pop_dir / "population.npz").exists():
        print(f"ERROR: no population in {pop_dir}; run scripts/build_simulator_population.py", file=sys.stderr)
        return 1
    t0 = time.perf_counter()
    pop = Population.load(pop_dir)
    dist_years = args.distribution_year or [spec.first_year]
    bad = sorted(set(dist_years) - set(pop.years))
    if bad:
        print(f"ERROR: --distribution-year {bad}: the population covers {list(pop.years)}", file=sys.stderr)
        return 2
    res = score_spec(pop, spec, distribution_years=dist_years)

    out = Path(args.out) if args.out else RUNS_DIR / "custom" / spec.name
    out.mkdir(parents=True, exist_ok=True)
    rev = pd.DataFrame(res["revenue"])
    rev.to_csv(out / "revenue.csv", index=False)
    for y, tables in res["distribution"].items():
        pd.DataFrame(tables["quintile"]).to_csv(out / f"quintile_{y}.csv", index=False)
        pd.DataFrame(tables["income_class"]).to_csv(out / f"income_class_{y}.csv", index=False)
    (out / "spec.json").write_text(json.dumps(spec.to_dict(), indent=2) + "\n")
    write_run_manifest(out, script=f"forecast_custom.py --spec {args.spec}",
                       params={"spec": spec.to_dict(), "distribution_years": dist_years},
                       inputs={"population": json.loads((pop_dir / "manifest.json").read_text())
                               if (pop_dir / "manifest.json").exists() else str(pop_dir)})

    print(f"{spec.label or spec.name} — change vs current law (Act 24), $M")
    piv = rev.pivot(index="tax_year", columns="scenario", values="behavioral_$M")
    piv = piv[[c for c in ("low", "mid", "high") if c in piv.columns]]
    table = piv.rename(columns=str.upper)
    # The static column is MID's, shown when MID was scored (a spec's
    # behavior list can leave it out).
    if (rev.scenario == "mid").any():
        table.insert(0, "MID static", rev[rev.scenario == "mid"].set_index("tax_year")["static_$M"])
    table.loc["5-year"] = table.sum(min_count=1)
    print(table.to_string(float_format=lambda v: f"{v:+,.1f}"))
    print(f"\nSaved {out} ({time.perf_counter() - t0:.1f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
