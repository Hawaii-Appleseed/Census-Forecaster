"""Manifested run-output store (Phase 1 of DASHBOARD_PIPELINE_SCOPE.md).

A "run" is one forecast-script execution. Its outputs land in a single
directory (``runs/<slug>/`` at the repo root) together with a
``manifest.json`` recording what produced them: script, parameters and
their fingerprint, git SHA, timestamp, and the input artifacts consumed.
The manifest is what lets a dashboard (or a human) enumerate available
results without knowing each script's conventions.

Reuses the fingerprint / git-SHA machinery from ``tax_modeler.artifacts``
rather than inventing a second provenance scheme.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd

from tax_modeler.artifacts import _git_sha, params_fingerprint

MANIFEST_NAME = "manifest.json"


def write_run_manifest(
    run_dir: Path | str,
    *,
    script: str,
    params: Optional[Dict[str, Any]] = None,
    inputs: Optional[Dict[str, Any]] = None,
    outputs: Optional[Sequence[str]] = None,
    git_sha: Optional[str] = None,
) -> Path:
    """Write ``manifest.json`` into ``run_dir`` (created if needed).

    Args:
        script: producing script, repo-relative (e.g.
            ``forecast_sb3125_enhanced.py --cd 2``).
        params: JSON-able parameter dict; fingerprinted for staleness
            checks.
        inputs: provenance of consumed artifacts (e.g. the tax-unit
            cache sidecar contents).
        outputs: filenames inside ``run_dir``. Defaults to every file
            present except the manifest itself.
        git_sha: the commit to record; defaults to HEAD's. A run whose
            outputs cite their code's commit passes
            ``artifacts.git_sha_for_code``, which marks uncommitted code.
    """
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    if outputs is None:
        outputs = sorted(
            p.name for p in run_dir.iterdir()
            if p.is_file() and p.name != MANIFEST_NAME
        )
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_sha": git_sha if git_sha is not None else _git_sha(),
        "script": script,
        "params": params,
        "params_fingerprint": params_fingerprint(params) if params else None,
        "inputs": inputs,
        "outputs": list(outputs),
    }
    path = run_dir / MANIFEST_NAME
    with open(path, "w") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")
    return path


def read_run_manifest(run_dir: Path | str) -> Dict[str, Any]:
    with open(Path(run_dir) / MANIFEST_NAME) as f:
        return json.load(f)


def list_runs(runs_root: Path | str) -> List[Dict[str, Any]]:
    """Return every manifest under ``runs_root`` (one level deep),
    each augmented with its ``run_dir`` name — the dashboard's index."""
    runs_root = Path(runs_root)
    found = []
    if not runs_root.exists():
        return found
    for d in sorted(runs_root.iterdir()):
        if d.is_dir() and (d / MANIFEST_NAME).exists():
            m = read_run_manifest(d)
            m["run_dir"] = d.name
            found.append(m)
    return found


def tidy_long(
    df: pd.DataFrame,
    id_vars: Sequence[str],
    *,
    metric_name: str = "metric",
    value_name: str = "value",
) -> pd.DataFrame:
    """Melt a wide metrics frame into tidy long form.

    One row per (*id_vars*, metric): new scenarios/years become rows,
    not columns — the property the dashboard schema depends on. Only
    numeric non-id columns are melted; non-numeric extras are dropped.
    """
    id_vars = list(id_vars)
    value_cols = [
        c for c in df.columns
        if c not in id_vars and pd.api.types.is_numeric_dtype(df[c])
    ]
    return df.melt(
        id_vars=id_vars,
        value_vars=value_cols,
        var_name=metric_name,
        value_name=value_name,
    )
