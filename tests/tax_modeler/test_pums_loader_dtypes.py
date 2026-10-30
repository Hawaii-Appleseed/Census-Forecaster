"""Regression tests for PUMSDataLoader parquet dtype handling.

Parquet PUMS files store ``ST``/``STATE`` as int64 (the loader's dtype maps
only apply to CSV reads). Before the fix, the ``ST == state`` filter compared
int64 against the string ``'15'`` and silently dropped every row, so
``load_data`` blew up with ``KeyError: 'SERIALNO'`` on an empty frame.
"""

from __future__ import annotations

import pandas as pd
import pytest
from tax_modeler.loaders.pums_loader import PUMSDataLoader


def _write_synthetic_pums(data_dir, st_column: str, st_dtype):
    """Write minimal psam_h15/psam_p15 parquet files with the given ST dtype."""
    hh = pd.DataFrame({
        'SERIALNO': ['2024H1', '2024H2', '2024H3'],
        'PUMA': [100, 301, 305],
        st_column: pd.Series([15, 15, 15], dtype=st_dtype),
        'HINCP': [52_000.0, 130_000.0, 87_500.0],
        'ADJINC': [1_000_000.0] * 3,
        'WGTP': [12, 40, 25],
        'NP': [2, 4, 1],
    })
    persons = pd.DataFrame({
        'SERIALNO': ['2024H1', '2024H1', '2024H2', '2024H3'],
        'SPORDER': [1, 2, 1, 1],
        'PUMA': [100, 100, 301, 305],
        st_column: pd.Series([15, 15, 15, 15], dtype=st_dtype),
        'AGEP': [34, 3, 51, 68],
        'PWGTP': [12, 12, 40, 25],
        'ADJINC': [1_000_000.0] * 4,
    })
    hh.to_parquet(data_dir / 'psam_h15.parquet')
    persons.to_parquet(data_dir / 'psam_p15.parquet')


@pytest.mark.parametrize('st_column', ['ST', 'STATE'])
@pytest.mark.parametrize('pums_type', ['1yr', '5yr'])
def test_load_data_parquet_int64_st(tmp_path, st_column, pums_type):
    """int64 ST/STATE in parquet must survive the ST == '15' state filter."""
    _write_synthetic_pums(tmp_path, st_column, 'int64')
    loader = PUMSDataLoader(data_dir=tmp_path)

    person_df, hh_df = loader.load_data(state='15', pums_type=pums_type)

    assert len(hh_df) == 3, 'state filter dropped households (int64 ST bug)'
    assert len(person_df) == 4
    assert (hh_df['ST'] == '15').all()


def test_load_data_parquet_str_st_unchanged(tmp_path):
    """String ST (the CSV-path convention) keeps working identically."""
    _write_synthetic_pums(tmp_path, 'ST', 'str')
    loader = PUMSDataLoader(data_dir=tmp_path)

    person_df, hh_df = loader.load_data(state='15', pums_type='5yr')

    assert len(hh_df) == 3
    assert len(person_df) == 4


def test_parquet_preferred_over_csv_for_all_vintages(tmp_path):
    """_hh/_person_file_path pick parquet regardless of pums_type."""
    _write_synthetic_pums(tmp_path, 'ST', 'int64')
    # Decoy CSVs that would fail on read — must not be chosen.
    (tmp_path / 'psam_h15.csv').write_text('bogus')
    (tmp_path / 'psam_p15.csv').write_text('bogus')
    loader = PUMSDataLoader(data_dir=tmp_path)

    for pums_type in ('1yr', '5yr'):
        assert loader._hh_file_path('15', pums_type).suffix == '.parquet'
        assert loader._person_file_path('15', pums_type).suffix == '.parquet'
