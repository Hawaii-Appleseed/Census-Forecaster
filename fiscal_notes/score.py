"""Score a spec file with the simulator's own functions and return a plain dict.

The same library calls ``analyses/forecast_custom.py --spec`` makes (``load_spec``,
``Population.load``, ``score_spec``); nothing here duplicates model logic. The dict is
JSON-serialisable and is what :mod:`fiscal_notes.note` and :mod:`fiscal_notes.diff` read.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

SCENARIOS = ("low", "mid", "high")
#: the series every consumer reads, in display order: (key, scenario, revenue field)
SERIES = (("static_mid", "mid", "static_$M"), ("low", "low", "behavioral_$M"),
          ("mid", "mid", "behavioral_$M"), ("high", "high", "behavioral_$M"))


class ScoreError(RuntimeError):
    """The spec cannot be scored here (missing input, bad spec); the message says what to do."""


def runs_dir() -> Path:
    return Path(os.environ.get("CF_RUNS_DIR") or REPO / "runs")


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def model_provenance() -> dict:
    """Which code scored the spec: HEAD, and whether tracked files differ from it."""
    sha = _git("rev-parse", "HEAD")
    dirty = _git("status", "--porcelain", "--untracked-files=no")
    return {"model_git_sha": sha, "model_tree_dirty": bool(dirty) if dirty is not None else None}


def _sum(values: list[float | None]) -> float | None:
    return None if any(v is None for v in values) else float(sum(values))


def build_result(spec, res: dict, years: tuple[int, ...], dist_year: int, provenance: dict) -> dict:
    """Shape ``score_spec``'s answer into the dict the note renders (pure; no model calls)."""
    rows = {(r["scenario"], int(r["tax_year"])): r for r in res["revenue"]}
    income_tax: dict[str, dict[str, float | None]] = {}
    for key, scenario, field in SERIES:
        income_tax[key] = {str(y): (float(rows[(scenario, y)][field]) if (scenario, y) in rows else None)
                           for y in years}

    credits = None
    if res.get("credits") is not None and spec.changes_credits:
        by_year = {int(c["tax_year"]): c for c in res["credits"]}
        credits = {col: {str(y): float(by_year[y][f"{col}_$M"]) for y in years}
                   for col in ("eitc", "food_excise", "total")}

    total = {}
    for key, series in income_tax.items():
        total[key] = {y: (None if v is None else v + (credits["total"][y] if credits else 0.0))
                      for y, v in series.items()}

    def five(block: dict) -> dict[str, float | None]:
        return {k: _sum(list(s.values())) for k, s in block.items()}

    five_year = {"income_tax": five(income_tax), "total": five(total)}
    if credits:
        five_year["credits"] = {k: _sum(list(s.values())) for k, s in credits.items()}

    dist = res["distribution"][dist_year]
    return {
        "name": spec.name,
        "label": spec.label,
        "spec": spec.to_dict(),
        "years": list(years),
        "first_year": spec.first_year,
        "income_tax": income_tax,
        "credits": credits,
        "total": total,
        "five_year": five_year,
        "distribution": {"year": dist_year, "quintile": dist["quintile"],
                         "income_class": dist["income_class"]},
        "revenue_rows": res["revenue"],
        "provenance": provenance,
    }


def score_spec_file(spec_path: str | Path, population_dir: str | Path | None = None) -> dict:
    """Score the spec file on the simulator population, as ``forecast_custom.py`` does."""
    logging.disable(logging.INFO)  # tax_modeler logs every schedule it loads
    import yaml
    from tax_modeler.errors import ConfigError
    from tax_modeler.reform.income_tax_spec import load_spec
    from tax_modeler.simulator.credits import load_credit_table
    from tax_modeler.simulator.population import Population
    from tax_modeler.simulator.score import score_spec

    spec_path = Path(spec_path)
    try:
        spec = load_spec(spec_path)
    except OSError as e:
        raise ScoreError(f"cannot read {spec_path}: {e.strerror or e}") from e
    except (ConfigError, ValueError, yaml.YAMLError) as e:
        raise ScoreError(f"invalid spec {spec_path}: {e}") from e

    pop_dir = Path(population_dir) if population_dir else runs_dir() / "tax_simulator"
    if not (pop_dir / "population.npz").exists():
        raise ScoreError(f"no simulator population in {pop_dir} (population.npz is missing); "
                         "build it with scripts/build_simulator_population.py")
    credits_json = pop_dir / "credits.json"
    wfc = runs_dir() / "working_family_credits"
    if spec.changes_credits and not credits_json.exists() and not (wfc / "revenue_by_year.csv").exists():
        raise ScoreError(
            f"the spec changes credits, but neither {credits_json} nor the working-family credits "
            f"run {wfc} exists; run chain/forecast_working_family_credits.py and "
            "scripts/build_simulator_population.py")

    pop = Population.load(pop_dir)
    years = tuple(int(y) for y in pop.years)
    if spec.first_year not in years:
        raise ScoreError(f"the spec's first_year {spec.first_year} is outside the population's "
                         f"tax years {list(years)}")

    credit_table = None
    if spec.changes_credits:
        if credits_json.exists():
            credit_table = json.loads(credits_json.read_text())
        else:
            # the table build_simulator_population.py would have written, from the same run
            credit_table = load_credit_table(wfc, years)

    res = score_spec(pop, spec, distribution_years=[spec.first_year], credit_table=credit_table)

    built = pop.meta.get("built") or {}
    provenance = {
        **model_provenance(),
        "population_git_sha": built.get("git_sha"),
        "population_created_at": built.get("created_at"),
        "credits_source": (credit_table or {}).get("source"),
        "spec_sha256": hashlib.sha256(spec_path.read_bytes()).hexdigest(),
    }
    return build_result(spec, res, years, spec.first_year, provenance)


def dumps(result: dict) -> str:
    """Stable text form: sorted keys, so two drafts' score files diff line by line."""
    return json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
