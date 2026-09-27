"""Versioned save/load for forecast-script artifacts (calibrated bases, unit caches).

Why this exists: forecast scripts used to share a bare DataFrame pickle in
``/tmp``. Two bug classes followed:

1. **C3** — the calibrated base was built WITH itemized-deduction params
   (``CAL_DED_PARAMS``), but downstream scripts re-scored it WITHOUT them
   (SD-only tax), so ``rescale_synthetic_tail_to_tax_target`` derived its
   ``tail_k`` from inconsistent liabilities and under-weighted the synthetic
   $1M+ tail in every quintile/bracket delta.
2. **Stale caches** — nothing recorded which code or params built an
   artifact, so unit-construction fixes silently failed to propagate to
   scripts reading an old cache.

Calibrated-base artifacts now carry their deduction params and provenance
metadata; loaders validate the params fingerprint. Parquet unit caches get a
``.meta.json`` sidecar with the git SHA of the code that built them.
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

ARTIFACT_VERSION = 1

# packages/tax_modeler/ (editable-install repo layout: src/tax_modeler/artifacts.py)
_PKG_ROOT = Path(__file__).resolve().parents[2]


def canonical_deduction_params_path() -> Path:
    """Path of the repo's canonical itemized-deduction params JSON."""
    return _PKG_ROOT / "config" / "deduction_params.json"


def load_canonical_deduction_params() -> Dict[str, Any]:
    path = canonical_deduction_params_path()
    if not path.exists():
        raise FileNotFoundError(
            f"Canonical deduction params not found at {path} — expected the "
            "repo checkout layout (packages/tax_modeler/config/deduction_params.json)."
        )
    with open(path) as f:
        return json.load(f)


def params_fingerprint(params: Any) -> str:
    """Stable short fingerprint of a params object (dict/list of JSON-able values)."""
    blob = json.dumps(params, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


def _git_sha(cwd: Path | str = _PKG_ROOT) -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=cwd, capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001 — provenance is best-effort
        return "unknown"


DIRTY_SUFFIX = "-dirty"


def git_sha_for_code(paths: Sequence[str], cwd: Path | str = _PKG_ROOT) -> str:
    """The commit that holds the code at ``paths`` (relative to the
    repository root): the short HEAD SHA, as :func:`_git_sha`, with
    ``-dirty`` appended when any of them has uncommitted changes, untracked
    files included.

    For an output that cites the commit of the code that made it (the tax
    simulator's manifest and page endnote): built from uncommitted code, no
    commit holds that code, so the bare HEAD SHA would cite one without it.
    Rebuild after committing the code. :func:`_git_sha` itself stays bare,
    because cache staleness checks compare it with HEAD.
    """
    sha = _git_sha(cwd)
    if sha == "unknown":
        return sha
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain", "--", *(f":(top){p}" for p in paths)],
            cwd=cwd, capture_output=True, text=True, timeout=10,
        )
    except Exception:  # noqa: BLE001 — provenance is best-effort
        return sha
    if out.returncode != 0:
        return sha
    return sha + DIRTY_SUFFIX if out.stdout.strip() else sha


# ---------------------------------------------------------------------------
# Calibrated-base artifact (pickle: DataFrame + deduction params + meta)
# ---------------------------------------------------------------------------

def save_calibrated_base(
    units: pd.DataFrame,
    path: Path | str,
    *,
    deduction_params: Any,
    tax_year: int,
    extra_meta: Optional[Dict[str, Any]] = None,
) -> None:
    """Persist a calibrated base with the deduction params it was scored under.

    ``tax_year`` is the calibration vintage (the year passed to
    ``compute_base_tax`` when the base was built) — consumers must re-score
    with the SAME params and year for the tail rescale to be consistent.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "artifact_version": ARTIFACT_VERSION,
        "units": units,
        "deduction_params": deduction_params,
        "meta": {
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "git_sha": _git_sha(),
            "n_units": int(len(units)),
            "tax_year": int(tax_year),
            "params_fingerprint": params_fingerprint(deduction_params),
            **(extra_meta or {}),
        },
    }
    pd.to_pickle(payload, path)
    logger.info(
        "Saved calibrated base: %s (%d units, TY%d, params %s, git %s)",
        path, len(units), tax_year,
        payload["meta"]["params_fingerprint"], payload["meta"]["git_sha"],
    )


def load_calibrated_base(
    path: Path | str,
    *,
    expected_params: Any = None,
) -> Tuple[pd.DataFrame, Any, Dict[str, Any]]:
    """Load a calibrated base; returns ``(units, deduction_params, meta)``.

    Legacy bare-DataFrame pickles are accepted with a loud warning — their
    deduction params fall back to the canonical config (fingerprint cannot
    be validated). When ``expected_params`` is given, a fingerprint mismatch
    against the embedded params raises ``ValueError`` (stale artifact).
    """
    path = Path(path)
    obj = pd.read_pickle(path)

    if isinstance(obj, pd.DataFrame):
        logger.warning(
            "Legacy bare-DataFrame artifact at %s — no embedded deduction "
            "params; falling back to the canonical config. Re-run "
            "forecast_sb3125_enhanced.py to regenerate a versioned artifact.",
            path,
        )
        return obj, load_canonical_deduction_params(), {"artifact_version": 0}

    if not isinstance(obj, dict) or "units" not in obj:
        raise ValueError(f"Unrecognized calibrated-base artifact format at {path}")

    units = obj["units"]
    params = obj.get("deduction_params")
    meta = dict(obj.get("meta", {}))

    if expected_params is not None:
        expected_fp = params_fingerprint(expected_params)
        if expected_fp != meta.get("params_fingerprint"):
            raise ValueError(
                f"Stale calibrated-base artifact at {path}: embedded deduction "
                f"params ({meta.get('params_fingerprint')}) differ from current "
                f"config ({expected_fp}) — re-run forecast_sb3125_enhanced.py."
            )

    logger.info(
        "Loaded calibrated base: %s (%s units, TY%s, params %s, git %s, created %s)",
        path, meta.get("n_units", len(units)), meta.get("tax_year", "?"),
        meta.get("params_fingerprint", "?"), meta.get("git_sha", "?"),
        meta.get("created_at", "?"),
    )
    return units, params, meta


# ---------------------------------------------------------------------------
# Parquet unit-cache sidecar (provenance guard against stale construction code)
# ---------------------------------------------------------------------------

def _sidecar_path(cache_path: Path | str) -> Path:
    return Path(cache_path).with_suffix(".meta.json")


def write_cache_sidecar(
    cache_path: Path | str,
    extra: Optional[Dict[str, Any]] = None,
) -> Path:
    """Write a ``.meta.json`` sidecar recording who built a parquet cache."""
    sidecar = _sidecar_path(cache_path)
    meta = {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_sha": _git_sha(),
        **(extra or {}),
    }
    sidecar.write_text(json.dumps(meta, indent=2))
    return sidecar


def check_cache_sidecar(cache_path: Path | str) -> Optional[Dict[str, Any]]:
    """Warn (loudly, to stdout AND the log) if a cache predates current code.

    Returns the sidecar metadata, or ``None`` when no sidecar exists. A
    missing sidecar or a git-SHA mismatch does NOT abort — unit construction
    is expensive and the cache may still be intentional — but the warning
    tells the user the cache predates the current checkout (e.g. built before
    a constructor fix) and should be deleted to force a rebuild.
    """
    sidecar = _sidecar_path(cache_path)
    if not sidecar.exists():
        msg = (
            f"WARNING: {cache_path} has no provenance sidecar — it predates "
            "cache versioning. If unit-construction code changed since it was "
            "built, delete it to force a rebuild."
        )
        print(msg, flush=True)
        logger.warning(msg)
        return None
    meta = json.loads(sidecar.read_text())
    current = _git_sha()
    if current != "unknown" and meta.get("git_sha") not in ("unknown", current):
        msg = (
            f"WARNING: {cache_path} was built at git {meta.get('git_sha')} "
            f"(current: {current}, created {meta.get('created_at')}). If unit "
            "construction changed in between, delete the cache to rebuild."
        )
        print(msg, flush=True)
        logger.warning(msg)
    return meta
