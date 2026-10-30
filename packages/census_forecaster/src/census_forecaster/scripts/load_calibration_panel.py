"""Load the bundled calibration panel back into the in-memory shape.

Counterpart to `build_calibration_panel.py`. The build script writes the
three JSON files; this loader reads them back into:

* `series_by_key: dict[(geoid, indicator) → list[AcsObservation]]` —
  the form `run_holdout_calibration` expects.
* `populations: dict[geoid → int]` — the lookup used at projection
  time to classify a county into a population bucket.

The loader gracefully handles the case where the bundled panel doesn't
exist (e.g. the user hasn't run the build script yet) by raising
`PanelMissingError` with a clear remediation message. Callers that want
to fall back to the legacy Hawaii-only fixture can catch this.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from common.models import AcsObservation


class PanelMissingError(FileNotFoundError):
    """Raised when the bundled calibration panel is not present.

    The remediation is always the same: run the build script with a
    Census API key. The error message includes the exact command.
    """


def _default_panel_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "calibration_panel"


def panel_dir_exists(panel_dir: Path | None = None) -> bool:
    """Lightweight check: are the three expected files present?"""
    d = panel_dir or _default_panel_dir()
    return all(
        (d / fn).exists()
        for fn in ("acs_panel.json", "county_population_2020.json", "manifest.json")
    )


def load_panel(panel_dir: Path | None = None) -> tuple[
    dict[tuple[str, str], list[AcsObservation]],
    dict[str, int],
    dict,
]:
    """Load the bundled calibration panel.

    Returns
    -------
    (series_by_key, populations, manifest)
    """
    d = panel_dir or _default_panel_dir()
    if not panel_dir_exists(d):
        raise PanelMissingError(
            f"Calibration panel not found at {d}. Run "
            "`CENSUS_API_KEY=<key> python -m census_forecaster.scripts.build_calibration_panel` "
            "to fetch it. See data/calibration_panel/README.md for details."
        )

    with open(d / "acs_panel.json") as f:
        panel = json.load(f)
    with open(d / "county_population_2020.json") as f:
        pop_payload = json.load(f)
    with open(d / "manifest.json") as f:
        manifest = json.load(f)

    series_by_key: dict[tuple[str, str], list[AcsObservation]] = defaultdict(list)
    for row in panel.get("observations", []):
        obs = AcsObservation(
            estimate=float(row["estimate"]),
            moe=float(row["moe"]) if row["moe"] is not None else float("nan"),
            year=int(row["year"]),
            vintage=str(row["vintage"]),
            geoid=str(row["geoid"]),
            indicator=str(row["indicator"]),
        )
        series_by_key[(obs.geoid, obs.indicator)].append(obs)

    # Sort each series by effective year for downstream consistency.
    for _key, obs_list in series_by_key.items():
        obs_list.sort(key=lambda o: (o.year, o.vintage))

    populations: dict[str, int] = {
        str(g): int(p) for g, p in pop_payload.get("populations", {}).items()
    }

    return dict(series_by_key), populations, manifest


def load_populations(panel_dir: Path | None = None) -> dict[str, int]:
    """Load just the population lookup (cheaper than loading the full panel)."""
    d = panel_dir or _default_panel_dir()
    pop_path = d / "county_population_2020.json"
    if not pop_path.exists():
        raise PanelMissingError(
            f"Population lookup not found at {pop_path}. Run "
            "`python -m census_forecaster.scripts.build_calibration_panel` "
            "to fetch it."
        )
    with open(pop_path) as f:
        payload = json.load(f)
    return {str(g): int(p) for g, p in payload.get("populations", {}).items()}


__all__ = [
    "PanelMissingError",
    "panel_dir_exists",
    "load_panel",
    "load_populations",
]
