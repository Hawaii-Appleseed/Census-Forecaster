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

Both score capital gains on the DOTAX-anchored base with the statutory
alternative tax: the simulator through tax_modeler.simulator.gains (under a
second, so it is computed in every mode rather than saved), the Act 24 run
through act24_population.mid_gains_factors. Every mode first checks the
simulator's base against DOTAX and the capital-gains page's published
figures (check_gains_anchor) and that its factors are the ones the Act 24
run recorded, then the reproduction above, then that a capital-gains-only
plan reproduces the capital-gains page's method and lands near its numbers
(check_cg_page_parity, writing cg_page_comparison.json).

--from-saved skips the ~10-minute projection and rewrites the web files,
golden fixtures and preset results with the current scoring code (after a
change to the kernel or the behavioral response, say). The manifest then
records the saved population's own build under inputs.population.

The manifest's git_sha is the commit holding the simulator's code
(tax_modeler.simulator.web.CODE_PATHS), which the page's endnote cites. Built
while any of that code is uncommitted, it reads "<HEAD>-dirty", and
tests/site/test_tax_simulator_version.py fails once that code is committed.
So commit the code first, then rebuild (--from-saved is enough), run
`scripts/build_site.py --import-runs tax-simulator` and `scripts/build_site.py`,
and commit the data and pages on top.
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
CG_PAGE = REPO / "site" / "data" / "capital-gains"   # the capital-gains page's published CSVs

# check_cg_page_parity: simulator / published and the response ratios. The
# populations differ (the page projects with project_and_recalibrate and
# re-synthesizes its tail), so the levels cannot match exactly; on the
# 2026-09-27 population the ratios ran 0.934-0.966 and the response ratios
# within 0.0032 of the page's.
CG_LEVEL_BAND = (0.90, 1.02)
CG_RESPONSE_RATIO_TOL = 0.005


def check_reproduces_act24(pop, out_dir: Path) -> list[str]:
    """Score Act 24 vs Act 46 on the population; compare with the Act 24 run."""
    import numpy as np
    import pandas as pd

    import json

    from tax_modeler.config.tax_system_config import TaxSystemRegistry
    from tax_modeler.scenarios.act24_population import statute
    from tax_modeler.simulator.score import REPORT_NAMES, score_systems

    problems = []
    # The Act 24 run's gains factors (MID, per class and year) are the
    # simulator's: both anchor the same MID population with anchor_nltcg.
    ran = json.loads((ENHANCED / "manifest.json").read_text())["params"].get("gains_factors")
    if ran is None:
        problems.append("runs/sb3125_cd2_enhanced/manifest.json has no gains_factors: re-run "
                        "forecast_sb3125_enhanced.py --cd 2")
    else:
        anchor = pop.meta["cg_anchor"]
        for y in pop.years:
            ours = dict(zip(anchor["classes"], anchor["k"]["mid"][str(y)]))
            for c, k in ran[str(y)].items():
                if ours[c] != k:
                    problems.append(f"TY{y} gains factor {c}: {ours[c]!r} vs the Act 24 run's {k!r}")
    res = score_systems(pop, statute(TaxSystemRegistry.get_act46_system),
                        statute(TaxSystemRegistry.get_sb3125_cd2_system),
                        distribution_years=pop.years)
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


def check_gains_anchor(pop) -> list[str]:
    """The simulator's gains base against DOTAX and the capital-gains page.

    DOTAX anchors MID; LOW and HIGH keep MID's factors
    (tax_modeler.simulator.gains). For every year:

    * MID: each DOTAX class's gains (income x anchored share, weighted) equal
      DOTAX's TY2023 resident gains grown by cg_growth, with TOP_SHARE_1M of
      the $400K+ class at $1M+; the total equals the capital-gains page's
      published nltcg_M and the class targets its anchor_check.csv. Its
      factor k is 0 below $100K and positive in every other class (a class
      with no MID gains would zero LOW's and HIGH's there).
    * LOW and HIGH: the scale factors k equal MID's, element for element;
      and each class's anchored gains equal MID's k times the scenario's own
      model gains in that class, the classes recomputed here from the
      scenario's weighted income ranks (rank_classes).
    * Every scenario: the stored class codes are the ones rank_classes
      gives, no anchored share is capped at 1 (so the share form equals the
      scaled dollars), and no gains sit on income <= 0.

    Sums at rtol 1e-9; codes and k exactly.
    """
    import numpy as np
    import pandas as pd

    from tax_modeler.calibration.cg_anchor import BASE_YEAR, _CLASSES, DOTAX_NLTCG_RES, cg_growth, rank_classes
    from tax_modeler.simulator.gains import CLASSES, ensure_gains_anchor, gains_classes
    from tax_modeler.simulator.population import frame_for, unit_arrays
    from tax_modeler.simulator.scenarios import SCENARIOS

    anchor = ensure_gains_anchor(pop)
    top = anchor["top_share"]
    dotax = dict(zip(_CLASSES, DOTAX_NLTCG_RES[BASE_YEAR], strict=True))
    rev = pd.read_csv(CG_PAGE / "revenue_by_year.csv")
    rev = rev[(rev.law == "act24") & (rev.top_share == "central")]
    anchor_ref = pd.read_csv(CG_PAGE / "anchor_check.csv")
    code = {c: i for i, c in enumerate(CLASSES)}
    problems = []
    for y in pop.years:
        g = cg_growth(y)
        target = {"lt100": 0.0, **{c: dotax[c] * g for c in CLASSES[1:5]},
                  "400_1m": dotax["400p"] * g * (1 - top), "1mp": dotax["400p"] * g * top}
        k_mid = anchor["k"]["mid"][str(y)]
        if k_mid[0] != 0 or not all(v > 0 for v in k_mid[1:]):
            # a class without MID gains would zero LOW's and HIGH's there
            problems.append(f"mid {y}: scale factors {k_mid} (want 0, then all positive)")
        for s in SCENARIOS:
            model = unit_arrays(pop, y, s, gains="model")
            u = unit_arrays(pop, y, s)
            cls = gains_classes(pop, y, s)
            # the scenario's own classes, recomputed from its income ranks
            labels = rank_classes(frame_for(pop, y, s, gains="model"))
            own = np.empty(pop.n_units, dtype=np.uint8)
            own[pop.arrays[f"order_{y}"]] = [code[x] for x in labels]
            own[model["synthetic_cg_share"] == 0] = 0
            if not np.array_equal(cls, own):
                problems.append(f"{s} {y}: {int((cls != own).sum())} class codes differ "
                                f"from the scenario's income ranks")
            k = anchor["k"][s][str(y)]
            if s != "mid" and list(k) != list(k_mid):
                problems.append(f"{s} {y}: scale factors {k} are not MID's {k_mid}")
            raw = model["synthetic_cg_share"] * np.asarray(k)[cls]
            capped, at_zero = raw > 1, (u["income"] <= 0) & (u["synthetic_cg_share"] > 0)
            if capped.any() or at_zero.any():
                problems.append(f"{s} {y}: {int(capped.sum())} shares capped at 1, "
                                f"{int(at_zero.sum())} gains at income <= 0")
            gw = u["income"] * u["synthetic_cg_share"] * u["weight"]
            mw = model["income"] * model["synthetic_cg_share"] * model["weight"]
            for c, name in enumerate(CLASSES):
                got = float(gw[own == c].sum()) / 1e6
                want = (target[name] if s == "mid"
                        else k_mid[c] * float(mw[own == c].sum()) / 1e6)
                what = "DOTAX" if s == "mid" else "MID's k x model"
                if not np.isclose(got, want, rtol=1e-9, atol=1e-9):
                    problems.append(f"{s} {y} {name}: gains ${got:.6f}M vs {what} ${want:.6f}M")
            if s != "mid":
                continue
            pub = rev[rev.tax_year == y]["nltcg_M"].unique()
            if len(pub) != 1 or not np.isclose(float(gw.sum()) / 1e6, pub[0], rtol=1e-9):
                problems.append(f"mid {y}: total gains ${float(gw.sum()) / 1e6:.6f}M "
                                f"vs the page's nltcg_M {list(pub)}")
            ref = anchor_ref[anchor_ref.tax_year == y]
            if len(ref) != len(CLASSES) - 1:
                problems.append(f"mid {y}: anchor_check.csv has {len(ref)} classes")
            for _, r in ref.iterrows():
                if not np.isclose(target[r["group"]], r["dotax_target_M"], rtol=1e-9):
                    problems.append(f"mid {y} {r['group']}: target {target[r['group']]} "
                                    f"vs the page's {r['dotax_target_M']}")
    return problems


def _cg_page_scorer():
    """The capital-gains page's Scorer and constants (forecast_cg_rate_options).
    Importing the script turns warnings and logging off for the process, as
    main() already has."""
    import forecast_cg_rate_options as page

    return page.Scorer, page.BETA, page.CAP_CURRENT


def check_cg_page_parity(pop, out_dir: Path) -> list[str]:
    """A capital-gains-only plan scored by the simulator vs the capital-gains page.

    MID, TY2027-2031, the page's two options (the cg_9 and cg_ordinary
    presets), before credits as the page scores:

    (i)   method: the simulator's path (unit_liabilities, and
          apply_behavioral_response with MID's cg_beta = the page's BETA)
          equals the page's score_option on the same frame and gains base,
          at rtol 1e-9;
    (ii)  level: simulator / published lies in CG_LEVEL_BAND for all 20
          cells (the populations differ; see CG_LEVEL_BAND);
    (iii) response: with response / static is within CG_RESPONSE_RATIO_TOL
          of the page's.

    Writes cg_page_comparison.json (with the simulator's net-of-credit
    figures, what its page shows).
    """
    import json

    import numpy as np
    import pandas as pd

    from tax_modeler.config.tax_system_config import TaxCalculator
    from tax_modeler.reform.income_tax_spec import IncomeTaxSpec
    from tax_modeler.scenarios.behavioral_response import apply_behavioral_response, top_rate_path
    from tax_modeler.simulator.population import frame_for
    from tax_modeler.simulator.presets import presets
    from tax_modeler.simulator.scenarios import SCENARIOS
    from tax_modeler.simulator.score import score_spec

    Scorer, BETA, CAP_CURRENT = _cg_page_scorer()

    # forecast_cg_rate_options.score_option, verbatim (:344-355 at 65e1a6e).
    def score_option(sc, income, nltcg, cap_new, yr, *, behavioral: bool):
        """Per-unit change in pre-credit tax from moving the cap to *cap_new*."""
        base_tax = sc.tax(income, nltcg, CAP_CURRENT)
        if not behavioral:
            return sc.tax(income, nltcg, cap_new) - base_tax
        mr = sc.marginal_rate(income)
        state0 = np.minimum(mr, CAP_CURRENT)
        state1 = mr if cap_new is None else np.minimum(mr, cap_new)
        d_tau = np.maximum(0.0, state1 - state0)
        kept = nltcg * np.exp(-BETA * d_tau)
        income1 = income - (nltcg - kept)
        return sc.tax(income1, kept, cap_new) - base_tax

    calc = TaxCalculator()
    params = SCENARIOS["mid"].behavioral_params
    # round_trip: pandas' default parser is not correctly rounded and can land
    # one ULP from Python's float(), which is what the site's CSV reader (and
    # test_tax_simulator_compares_with_the_capital_gains_page) compares these
    # published figures against exactly.
    rev = pd.read_csv(CG_PAGE / "revenue_by_year.csv", float_precision="round_trip")
    rev = rev[(rev.law == "act24") & (rev.top_share == "central")].set_index(["tax_year", "option"])
    options = {"cap9": "cg_9", "ordinary": "cg_ordinary"}
    by_name = {d["name"]: d for d in presets(calc)}
    problems, rows = [], []
    for opt, preset in options.items():
        spec = IncomeTaxSpec.from_dict(by_name[preset])
        net = {r["tax_year"]: r for r in score_spec(pop, spec, scenarios=["mid"])["revenue"]}
        path = top_rate_path(spec.baseline_for, spec.system_for, pop.years, calc)
        for y in pop.years:
            base_cfg, cfg = spec.baseline_for(y), spec.system_for(y)
            df = frame_for(pop, y, "mid")
            w = df["weight"].to_numpy()
            before = lambda frame, c: calc.unit_liabilities(frame, c)["before_credits"]  # noqa: E731
            base = before(df, base_cfg) @ w / 1e6
            static = before(df, cfg) @ w / 1e6 - base
            adj, _ = apply_behavioral_response(df, params, target_year=y, baseline_cfg=base_cfg,
                                               scenario_cfg=cfg, calculator=calc, top_rate_path=path)
            response = before(adj, cfg) @ adj["weight"].to_numpy() / 1e6 - base
            # the page's method on the same frame and gains
            sc = Scorer(df, base_cfg, calc)
            income = df["income"].to_numpy(float)
            nltcg = income * df["synthetic_cg_share"].to_numpy(float)
            cap = cfg.cg_alt_rate
            page_static = score_option(sc, income, nltcg, cap, y, behavioral=False) @ w / 1e6
            page_response = score_option(sc, income, nltcg, cap, y, behavioral=True) @ w / 1e6
            for what, ours, theirs in (("static", static, page_static),
                                       ("response", response, page_response)):
                if not np.isclose(ours, theirs, rtol=1e-9, atol=0):
                    problems.append(f"{opt} {y} {what}: simulator {ours!r} vs score_option {theirs!r}")
            pub = rev.loc[(y, opt)]
            level = (static / pub["static_M"], response / pub["behavioral_M"])
            ratio, pub_ratio = response / static, pub["behavioral_M"] / pub["static_M"]
            for what, v in zip(("static", "response"), level, strict=True):
                if not CG_LEVEL_BAND[0] <= v <= CG_LEVEL_BAND[1]:
                    problems.append(f"{opt} {y} {what}: simulator / published {v:.4f} "
                                    f"outside {CG_LEVEL_BAND}")
            if abs(ratio - pub_ratio) > CG_RESPONSE_RATIO_TOL:
                problems.append(f"{opt} {y}: response ratio {ratio:.4f} vs the page's {pub_ratio:.4f}")
            rows.append({"option": opt, "preset": preset, "tax_year": y,
                         "static_$M": static, "behavioral_$M": response,
                         "score_option_static_$M": page_static,
                         "score_option_behavioral_$M": page_response,
                         "published_static_$M": float(pub["static_M"]),
                         "published_behavioral_$M": float(pub["behavioral_M"]),
                         "static_vs_published": level[0], "behavioral_vs_published": level[1],
                         "response_ratio": ratio, "published_response_ratio": pub_ratio,
                         "net_static_$M": net[y]["static_$M"],
                         "net_behavioral_$M": net[y]["behavioral_$M"]})
    (out_dir / "cg_page_comparison.json").write_text(json.dumps(
        {"note": "MID, before credits unless net_*; published = site/data/capital-gains/"
                 "revenue_by_year.csv (act24, central top share)",
         "level_band": CG_LEVEL_BAND, "response_ratio_tol": CG_RESPONSE_RATIO_TOL,
         "problems": problems, "rows": rows}, indent=1))
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
                      help="load the saved population and re-run the checks; write only their reports")
    mode.add_argument("--from-saved", action="store_true",
                      help="load the saved population; rewrite web files, golden fixtures and preset results")
    args = ap.parse_args()
    warnings.filterwarnings("ignore")
    logging.disable(logging.WARNING)

    from datetime import datetime, timezone

    from tax_modeler.artifacts import DIRTY_SUFFIX, git_sha_for_code
    from tax_modeler.runs import write_run_manifest
    from tax_modeler.simulator.population import Population, build_population
    from tax_modeler.simulator.web import CODE_PATHS

    code_sha = git_sha_for_code(CODE_PATHS, cwd=REPO)
    if code_sha.endswith(DIRTY_SUFFIX):
        print(f"NOTE: the simulator's code has uncommitted changes, so the manifest records "
              f"git_sha {code_sha}. Commit the code, then rebuild, so the page cites a commit "
              f"that holds it.", flush=True)

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
                             "git_sha": code_sha}
        pop.save(OUT)
        print(f"  {pop.n_units:,} units, {pop.meta['n_households']:,} households "
              f"({time.perf_counter() - t0:.0f}s)", flush=True)

    from tax_modeler.simulator.gains import ensure_gains_anchor

    print("Anchoring capital gains to DOTAX and checking it...", flush=True)
    ensure_gains_anchor(pop)
    problems = check_gains_anchor(pop)
    if problems:
        print(f"FAILED: {len(problems)} differences, first: {problems[:5]}")
        return 1
    print("  MID class totals = DOTAX = the capital-gains page's base; LOW/HIGH keep MID's "
          "factors on their own classes; no share capped", flush=True)
    rows = pop.meta["cg_anchor"]["rows"]
    y0 = str(pop.years[0])
    print(f"  anchored gains TY{y0}, LOW/MID/HIGH: "
          + " / ".join(f"{sum(r['anchored_cg_M'] for r in rows[s][y0]):.1f}" for s in ("low", "mid", "high"))
          + f" $M (DOTAX {sum(r['dotax_target_M'] for r in rows['mid'][y0]):.1f})", flush=True)
    print("Checking it reproduces the Act 24 run...", flush=True)
    problems = check_reproduces_act24(pop, OUT)
    if problems:
        print(f"FAILED: {len(problems)} differences, first: {problems[:5]}")
        return 1
    print("  Act 24 vs Act 46 reproduced on the anchored base: gains factors, LOW/MID/HIGH "
          "revenue, MID distribution", flush=True)
    print("Checking capital-gains plans against the capital-gains page...", flush=True)
    problems = check_cg_page_parity(pop, OUT)
    if problems:
        print(f"FAILED: {len(problems)} differences, first: {problems[:5]}")
        return 1
    print("  score_option reproduced; levels and response ratios within bounds "
          "(cg_page_comparison.json)", flush=True)
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
        git_sha=code_sha,
    )
    print(f"Done in {time.perf_counter() - t0:.0f}s -> {OUT}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
