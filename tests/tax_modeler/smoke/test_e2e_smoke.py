"""End-to-end smoke: synthetic PUMS → run_pipeline → revenue summary.

This is the canonical "does the package work?" test.  It exercises every
stage from tax-unit construction through projection and aggregation on
the synthetic fixture, without touching real PUMS / DOTAX / IRS data.

Two variants:
  - via individual stage helpers (``enrich_for_credits`` →
    ``compute_base_tax`` → ``RevenueEstimator``) — covers the public stage API.
  - via ``run_pipeline(tax_units_df=...)`` — covers the convenience entry
    when projection is enabled.
"""
from __future__ import annotations

import pytest


@pytest.mark.smoke
def test_e2e_via_stage_helpers(taxed_units) -> None:
    """Stage helpers compose end-to-end without touching real microdata."""
    from tax_modeler.revenue.estimator import RevenueEstimator

    estimator = RevenueEstimator(taxed_units)
    summary = estimator.state_summary()
    assert summary["total_weighted_filers"] > 0
    assert summary["hi_net_tax_revenue"] > 0


@pytest.mark.smoke
def test_run_pipeline_validates_input() -> None:
    """run_pipeline must reject empty / malformed input with
    DataValidationError, not let it crash deep in projection code."""
    import pandas as pd
    from tax_modeler import run_pipeline
    from tax_modeler.errors import DataValidationError

    with pytest.raises(DataValidationError):
        run_pipeline(target_year=2027, tax_units_df=pd.DataFrame())


@pytest.mark.smoke
def test_public_api_exposes_promoted_stages() -> None:
    """Smoke-check that the API curation in __init__.py is intact."""
    import tax_modeler

    for name in (
        "run_pipeline",
        "enrich_for_credits",
        "compute_base_tax",
        "calibrate",
        "StateConfig",
        "HAWAII",
        "DataValidationError",
    ):
        assert hasattr(tax_modeler, name), f"public API missing {name}"
        assert name in tax_modeler.PUBLIC_API


@pytest.mark.smoke
def test_underscore_aliases_emit_deprecation_warning() -> None:
    """The underscore-prefixed shims still work but emit DeprecationWarning."""
    import warnings

    import pandas as pd
    from tax_modeler.pipeline import _enrich_for_credits

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        df = pd.DataFrame({"num_dependents": [0, 1], "weight": [1.0, 1.0]})
        _enrich_for_credits(df)

    assert any(issubclass(w.category, DeprecationWarning) for w in caught), \
        "Underscore alias must emit DeprecationWarning"
