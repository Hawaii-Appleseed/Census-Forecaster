"""PUMSDataLoader must fail with actionable guidance, not a bare path.

Raw PUMS is gitignored, so a fresh clone rebuilding the tax-unit cache
reaches these paths with nothing to load. The direct callers of this
loader (analyses/forecast_sb3125.py, poverty_impact_report.py,
eitc_ctc_geo_report.py) bypass pipeline._load_pums and so used to get an
unadorned FileNotFoundError with no hint about where to get the data.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from tax_modeler.errors import MissingDataError
from tax_modeler.loaders.pums_loader import PUMSDataLoader


@pytest.fixture
def empty_loader(tmp_path):
    return PUMSDataLoader(data_dir=tmp_path)


def _assert_actionable(exc: MissingDataError, tmp_path: Path):
    msg = str(exc)
    assert "Download the ACS PUMS files" in msg
    assert "state FIPS 15" in msg
    assert "psam_h15.parquet" in msg and "psam_p15.parquet" in msg
    assert str(tmp_path) in msg
    assert "HAWAII_PUMS_DIR" in msg          # how to redirect the search
    assert exc.env_var == "HAWAII_PUMS_DIR"
    assert exc.path is not None


def test_get_total_households_guidance(empty_loader, tmp_path):
    with pytest.raises(MissingDataError) as ei:
        empty_loader.get_total_households(state="15", pums_type="5yr")
    _assert_actionable(ei.value, tmp_path)


def test_load_households_batch_guidance(empty_loader, tmp_path):
    with pytest.raises(MissingDataError) as ei:
        empty_loader.load_households_batch(state="15", pums_type="5yr")
    _assert_actionable(ei.value, tmp_path)


def test_load_persons_guidance(empty_loader, tmp_path):
    with pytest.raises(MissingDataError) as ei:
        empty_loader.load_persons_for_households(
            serialnos=["2024H1"], state="15", pums_type="5yr")
    _assert_actionable(ei.value, tmp_path)


def test_still_catchable_as_filenotfounderror(empty_loader):
    """MissingDataError subclasses FileNotFoundError — existing
    `except FileNotFoundError` handlers must keep working."""
    with pytest.raises(FileNotFoundError):
        empty_loader.get_total_households(state="15", pums_type="5yr")


def test_message_reflects_configured_data_dir(tmp_path):
    """The path shown is the loader's dir, not a hardcoded default."""
    custom = tmp_path / "somewhere" / "else"
    with pytest.raises(MissingDataError) as ei:
        PUMSDataLoader(data_dir=custom).get_total_households(state="15")
    assert str(custom) in str(ei.value)


def test_no_error_when_files_present(tmp_path):
    """Guard against the check firing when data actually exists."""
    for name in ("psam_h15", "psam_p15"):
        pd.DataFrame({
            "SERIALNO": ["2024H1"], "PUMA": [100], "ST": [15],
            "WGTP": [10], "PWGTP": [10], "NP": [1], "ADJINC": [1e6],
        }).to_parquet(tmp_path / f"{name}.parquet")
    assert PUMSDataLoader(data_dir=tmp_path).get_total_households(state="15") == 1
