# Census-Forecaster — Claude rules

## Hard rules

- **`SB3125_CD1_FORECAST.md` prose MUST be updated before committing** whenever methodology, parameters, scenario design, behavioral channels, tax treatment, or k-values change: update the relevant methodology sections and add a dated changelog section at the top. **The current result tables are generated** — the "Current results (generated)" block between the `<!-- generated:results -->` markers is written from `runs/` by `python scripts/render_results.py` (run by `publish.py`; `--check` exits 1 if stale). Never hand-edit inside the markers. The dated sections' tables and the Section 10 results table are historical snapshots and stay as written. *(This rule previously named `SB3125_CD2_FORECAST.md`, which has never existed — the single forecast doc is `SB3125_CD1_FORECAST.md` and it covers both CD1 and CD2. Corrected 2026-08-19.)*
- **SB 3125 is law.** It was signed May 21, 2026 as **Act 24, SLH 2026**; the enrolled text is **CD2**, so CD2 results are the ones to cite as "Act 24." Bracket schedules are identical between CD1 and CD2; only the REEC credit model differs.
- **Never hand-edit COR projections.** `DEFAULT_COR_IIT_PROJECTIONS_M` loads from `census_forecaster/data/cor/cor_iit_projections.json`, refreshed by `python -m census_forecaster.scripts.refresh_cor_iit` and wired into the monthly `refresh-data` workflow. COR meets on no fixed cadence, and the constant silently sat on the March 2026 vintage for months before this existed. To pick up a new meeting, run the script — do not retype the table. The literals in `quintile_analysis.py` and `calibration/forward_targets.py` are missing-file fallbacks only (`tests/census_forecaster/test_cor_iit.py` pins both to the file); `forward_targets.py` was a second hand-typed copy stuck on the March 10, 2026 vintage until 2026-10-05.
- **Know which baseline a number uses.** `analyses/forecast_sb3125*.py` and `chain/forecast_sb3125_enhanced.py` score **vs Act 46**; `chain/forecast_sb3125_vs_fy26base.py` scores **vs Act 46 frozen at TY2026** (which already banks the TY2024+TY2026 SD steps — it is *not* pre-Act-46); `chain/forecast_act24_vs_pre_act46.py` scores **vs pre-Act-46 (2017) law**, the frame comparable to ITEP's ~$1.4B/yr Act 46 figure. Never compare across these frames without saying which is which.
- **Cadence-aware damping is non-negotiable**: φ=0.92/month for monthly series (CPI), φ=0.85/yr for annual (ACS). Half-lives explicitly documented in `METHODOLOGY.md` §2.3.1. Never copy a φ from one cadence to another.
- **Recency-weighted geometric mean** for trend initialization. Empirically calibrated 90% PIs via backtest, not analytical.
- **Repo-relative paths only** in commits/prompts. Madison's workdir is `~/repos/Census-Forecaster/`.
- **The full suite must pass before committing** — no regressions, no new skips. (The literal count drifts as tests are added; it was 1,955 passed / 3 skipped on 2026-08-07, having been written here as "185/185" long after that stopped being true. Compare against a run on the current HEAD, not a number in this file.) Honolulu County backtest baseline MAPE: 6.76%.

## Shipping a change

1. `python scripts/new_worktree.py <branch>` — never edit the shared main checkout. It copies `runs/` and the gitignored inputs over and builds the worktree's own venv (the main `.venv` imports main's code).
2. Change the code, run the full suite, **commit**.
3. `python scripts/publish.py` — refuses on uncommitted code (manifests stamp HEAD), reruns the chain, imports the runs, rebuilds the simulator against the newly imported capital-gains figures, builds every page.
4. `publish.py` has already regenerated the doc's results block; update its prose if methodology changed and commit it; then commit `site/` and `tests/simulator/`.
   Two steps reuse cached work when their inputs are unchanged, keyed by `common.fingerprint.fingerprint` (every workspace package's `src`, the Python/numpy/pandas versions, the step's own inputs): the working-family tax units (`data/artifacts/working_family_units.pkl`) and the simulator's golden fixtures (`--rebuild-golden` forces them). Deleting the `.key` files in `data/artifacts/` forces both. Within a run, the MID populations (built and projected by step 1, then again by the pre-Act-46 run and the simulator) are shared through `data/artifacts/populations/` (`tax_modeler.scenarios.population_store`), keyed by the same fingerprint of the code and the calibrated-base pickle, which step 1 rewrites every run; `scripts/rerun_models.py` gives each step of a stage `cores // steps` workers unless `CF_MAX_WORKERS` is set.
5. Open the PR; `python scripts/land_pr.py <number>` waits for checks, merges, waits for Pages, compares the live files with the merge commit, removes the worktree and local branch.

## Layout

- `chain/` — the five steps behind the published numbers (`scripts/rerun_models.py` STAGES). `analyses/` — every other forecast/report script. `scripts/` — tooling plus `_forecast_common.py` (paths, `--cd` parsing, the tax-unit cache) and `run_scenario.py`. Each chain/analyses script puts `scripts/` on `sys.path` for `_forecast_common`; run them from the repo root.
- Package code must not import `_forecast_common`. The cache key is the exception: `common.fingerprint` (`fingerprint`, `code_closure`, `closure_guard`) lives in the dependency-free `census-common` package, which anything may import. Its `SCRIPT_DIRS` tuple lists the script directories the closure scan reads: edit it when adding one. `CF_REPO_ROOT` overrides the repo root it derives from its own location.
- The tax-unit parquet cache's `.meta.json` sidecar stores `code_key` (`tax_modeler.artifacts.construction_code_key()`: the fingerprint of the import closure of `tax_modeler.base`), which `check_cache_sidecar` compares; `git_sha` there is provenance only.

## Companion sync

- **Cost-of-Living-Tracker** (`~/Cost-of-Living-Tracker`, formerly
  Housing-Affordability-Tracker — renamed 2026-08) consumes this package as a
  **pinned git dependency**, not a cherry-pick. Both hashes in its
  `requirements.txt` point at one Census-Forecaster commit
  (`census-common` and `census-forecaster`, each `#subdirectory=packages/...`).
  There is **no vendored copy to re-harmonize**: its `census_forecasting/`
  directory holds only scripts, data, backtests and docs, which `import
  census_forecaster.*` from the installed package.
- So a change here reaches that repo only when someone **bumps the pin**. Per
  its own instruction: update both hashes to the same upstream commit, then
  re-run `pip install -r requirements.txt` and pytest there. Nothing is
  automatic, and an un-bumped pin is not a bug — it is the isolation the pin
  exists to provide.
- *(History: it did vendor the projection module until 2026-04-25, when
  `547b3bb` migrated to the package. The old "last sync commit `d7cbdf4`" noted
  here for years is a commit in **that** repo, not this one — it never resolved
  against Census-Forecaster.)*

## Stack

- Python package, fully-typed dataclasses, scipy/numpy.
- Public API: `project_acs_ensemble`, `AcsObservation`, BLS forecaster.
- pytest for tests.

## Source-of-truth docs

- `METHODOLOGY.md` — math and parameter discipline.
- `SB3125_CD1_FORECAST.md` — Act 24 (SB 3125 CD1/CD2) methodology, scenarios, results.
- `RETITC_REPORT_METHODOLOGY.md` — REEC/RETITC credit pipeline walkthrough.
- `docs/archive/` — finished scoping docs, audits and reviews (e.g. `CBO_COMPONENT_AGING_SCOPE.md`: shipped, `--cbo-aging` is on by default); `docs/archive/README.md` says what each was and when it was superseded.

## Companion docs (in vault)

- `~/.openclaw/workspace/projects/Census-Forecaster.md`
- `~/.openclaw/workspace/tasks/Census-Forecaster.md`
