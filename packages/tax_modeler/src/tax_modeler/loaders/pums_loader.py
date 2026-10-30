"""
PUMS Data Loader for CTC and EITC Analysis

This module handles loading and preprocessing PUMS data for Hawaii,
with specific attention to variables needed for CTC and EITC calculations.

Use :class:`PUMSDataLoader` when you need:
  - Incremental batched loading across large CSV files
    (``load_households_batch``)
  - Explicit person-vs-household column splits
  - PUMS vintage column-rename handling (``STATE`` → ``ST``)

For one-off DataFrame loads that need no local files, use
``pums_estimator.fetch_pums`` (Census API).

For constructing tax units from loaded person/household data, use
:class:`tax_modeler.units.base.TaxUnitConstructor` rather than any
shortcut on this loader.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from tax_modeler.errors import MissingDataError

logger = logging.getLogger(__name__)

DEFAULT_PUMS_YEAR = 2022  # Most recent 5-year data available (2018-2022)
DEFAULT_STATE = '15'      # Hawaii FIPS code
DEFAULT_DATA_DIR = Path(__file__).parent.parent.parent.parent.parent / 'data' / 'raw' / 'pums'
DEFAULT_PUMS_TYPE = '5yr' # 5-year data

# Single source of truth for the dollar-year of the loaded PUMS incomes.
# PUMS incomes are inflation-adjusted (via ADJINC) to the file's final survey
# year. The current local file is the 2020-2024 5-year ACS PUMS, so incomes are
# in 2024 dollars. EVERY income-aging entry point (CBO component aging, county
# scalar growth, BLS occupation growth) must grow FROM this year — growing from
# a hardcoded 2022 double-counts ~2 years of growth. See PIPELINE_AUDIT_ROUND2.
PUMS_INCOME_DOLLAR_YEAR = 2024


def detect_pums_dollar_year(
    person_df: pd.DataFrame,
    *,
    default: int = PUMS_INCOME_DOLLAR_YEAR,
) -> int:
    """Best-effort detection of the income dollar-year of a PUMS person frame.

    PUMS incomes are inflation-adjusted to the file's final survey year via
    ``ADJINC``; the count of distinct ADJINC values equals the number of survey
    years (1 for a 1-year file, 5 for a 5-year file). The exact calendar year is
    not recoverable from the raw columns alone, so this logs the vintage
    fingerprint as a guard against silently feeding a different-vintage file and
    returns the configured ``default``. Callers thread the result into the aging
    engines instead of assuming a hardcoded base year.
    """
    if "ADJINC" not in person_df.columns:
        logger.warning(
            "detect_pums_dollar_year: no ADJINC column; assuming dollar-year %d",
            default,
        )
        return default
    distinct = person_df["ADJINC"].dropna().round(0).unique()
    n = len(distinct)
    span = "1-year" if n == 1 else f"{n}-year"
    logger.info(
        "PUMS vintage fingerprint: %d distinct ADJINC value(s) (%s file); "
        "income dollar-year assumed %d",
        n, span, default,
    )
    return default

# PUMS replicate weight column names (Census ships 80 for variance estimation
# via Successive Difference Replication). PWGTP1..PWGTP80 on the person file,
# WGTP1..WGTP80 on the household file. For the SPM poverty pipeline we only
# need household-grain WGTP_r because aggregate_to_spm_units picks up WGTP
# per household, but PWGTP_r is loaded too so person-grain estimators (kept
# out of scope for the poverty path) can opt in later.
N_PUMS_REPLICATES = 80
PWGTP_REPLICATE_COLS = tuple(f'PWGTP{i}' for i in range(1, N_PUMS_REPLICATES + 1))
WGTP_REPLICATE_COLS = tuple(f'WGTP{i}' for i in range(1, N_PUMS_REPLICATES + 1))


class PUMSDataLoader:
    """Handles loading and processing of PUMS data for tax benefit analysis."""

    def __init__(
        self,
        data_dir: Path = DEFAULT_DATA_DIR,
        load_replicate_weights: bool = False,
    ):
        """Initialize the PUMS data loader.

        Args:
            data_dir: Directory containing PUMS data files
            load_replicate_weights: If True, request the 80 PUMS replicate
                weight columns (``PWGTP1``-``PWGTP80`` on persons,
                ``WGTP1``-``WGTP80`` on households) for SDR variance
                estimation downstream. Default ``False`` keeps the file
                I/O cheap for the common case.
        """
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.load_replicate_weights = bool(load_replicate_weights)

        # Batch state (used by load_households_batch / reset_batch_state)
        self._batch_offset = 0
        self._total_households = None
        # Cache for parquet household files (avoids re-reading on each batch call)
        self._parquet_cache: dict[str, pd.DataFrame] = {}

        # Column renames between PUMS vintages (2024 renamed ST → STATE)
        self._column_renames = {
            'STATE': 'ST',
        }

        self.person_columns = {
            'SERIALNO': str, 'SPORDER': int, 'PUMA': str, 'ST': str, 'STATE': str,
            'AGEP': int, 'SEX': int, 'HISP': int, 'RAC1P': int,
            'SCHL': int, 'DIS': int, 'MIL': int, 'MILITARY': int,
            'WAGP': float, 'SEMP': float, 'INTP': float, 'RETP': float,
            'SSP': float, 'SSIP': float, 'PAP': float, 'OIP': float,
            'POVPIP': float, 'PWGTP': int, 'NP': int, 'MAR': int,
            'NOC': int, 'PINCP': float, 'ADJINC': float, 'RELSHIPP': int,
            'MSP': int, 'HICOV': int, 'HINS1': int, 'HINS2': int,
            'HINS3': int, 'HINS4': int, 'MIG': int,
        }

        self.household_columns = {
            'SERIALNO': str, 'PUMA': str, 'ST': str, 'STATE': str, 'HINCP': float,
            'ADJINC': float, 'WGTP': int, 'NP': int, 'TEN': int,
            'HHT': int, 'NOC': int, 'NRC': int, 'BLD': int,
            'ACCESS': int, 'BROADBND': int, 'COMPOTHX': int, 'HFL': int,
            'FS': int, 'HHL': int, 'LNGI': int, 'MULTG': int,
            'PARTNER': int, 'PLM': int, 'PSF': int, 'R18': int,
            'R65': int, 'RESMODE': int, 'SMX': int, 'SRNT': int,
            'TAXP': int, 'WIF': int, 'WKEXREL': int, 'WORKSTAT': int,
            'YBL': int,
        }

        if self.load_replicate_weights:
            for col in PWGTP_REPLICATE_COLS:
                self.person_columns[col] = int
            for col in WGTP_REPLICATE_COLS:
                self.household_columns[col] = int

    def _hh_file_path(self, state: str, pums_type: str) -> Path:
        """Return the household PUMS file path (parquet preferred over CSV)."""
        parquet = self.data_dir / f'psam_h{state}.parquet'
        if parquet.exists():
            return parquet
        return self.data_dir / f'psam_h{state}.csv'

    def _person_file_path(self, state: str, pums_type: str) -> Path:
        """Return the person PUMS file path (parquet preferred over CSV)."""
        parquet = self.data_dir / f'psam_p{state}.parquet'
        if parquet.exists():
            return parquet
        return self.data_dir / f'psam_p{state}.csv'

    def _missing(self, kind: str, path: Path, state: str) -> MissingDataError:
        """Build an actionable error for an absent PUMS file.

        Raw PUMS is gitignored, so a fresh clone reaches here with nothing
        to load. Say where the files go and how to redirect the search,
        rather than a bare path — this is the same guidance
        ``pipeline._load_pums`` gives, made available to the direct
        callers of this loader (analyses/forecast_sb3125.py, poverty_impact_report,
        eitc_ctc_geo_report) that bypass the pipeline wrapper.
        """
        return MissingDataError(
            f"PUMS {kind} file not found.\n"
            f"Download the ACS PUMS files for state FIPS {state} and place them at:\n"
            f"  {self.data_dir}/psam_h{state}.parquet  (or .csv)\n"
            f"  {self.data_dir}/psam_p{state}.parquet  (or .csv)",
            path=path,
            env_var="HAWAII_PUMS_DIR",
        )

    def get_total_households(
        self,
        state: str = DEFAULT_STATE,
        pums_type: str = DEFAULT_PUMS_TYPE,
    ) -> int:
        """Return the total number of household records in the PUMS file."""
        hh_file = self._hh_file_path(state, pums_type)
        if not hh_file.exists():
            raise self._missing("household", hh_file, state)

        if hh_file.suffix == '.parquet':
            import pyarrow.parquet as pq
            return pq.read_metadata(hh_file).num_rows
        else:
            import subprocess
            result = subprocess.run(['wc', '-l', str(hh_file)], capture_output=True, text=True)
            return max(0, int(result.stdout.strip().split()[0]) - 1)

    def load_households_batch(
        self,
        batch_size: int = 1000,
        state: str = DEFAULT_STATE,
        pums_type: str = DEFAULT_PUMS_TYPE,
    ) -> pd.DataFrame:
        """Load a batch of household records using internal state management.

        For parquet files the full dataset is cached in memory on the first
        call and subsequent calls slice from the cache.  For CSV files each
        call reads ``batch_size`` rows starting from the current offset.

        Returns an empty DataFrame when all records have been consumed.
        """
        hh_file = self._hh_file_path(state, pums_type)
        if not hh_file.exists():
            raise self._missing("household", hh_file, state)

        if self._total_households is None:
            self._total_households = self.get_total_households(state, pums_type)

        if self._batch_offset >= self._total_households:
            logger.debug("Reached end of data: offset=%d, total=%d",
                         self._batch_offset, self._total_households)
            return pd.DataFrame()

        logger.debug("Loading household batch: offset=%d, size=%d",
                     self._batch_offset, batch_size)

        if hh_file.suffix == '.parquet':
            cache_key = str(hh_file)
            if cache_key not in self._parquet_cache:
                logger.debug("Caching parquet household file: %s", hh_file)
                self._parquet_cache[cache_key] = pd.read_parquet(hh_file)
            full_df = self._parquet_cache[cache_key]
            hh_df = full_df.iloc[self._batch_offset:self._batch_offset + batch_size].copy()
        else:
            # CSV: skip the header plus rows already consumed.
            # Use float for all numeric columns — PUMS has many nullable int
            # fields (e.g. HFL, ACCESS) that arrive as float64 with NaN values
            # and cannot be safely cast to int64 during read.
            skiprows = range(1, self._batch_offset + 1) if self._batch_offset > 0 else []
            csv_dtypes = {
                k: (float if v is int else v)
                for k, v in self.household_columns.items()
            }
            hh_df = pd.read_csv(
                hh_file,
                skiprows=skiprows,
                nrows=batch_size,
                dtype=csv_dtypes,
            )

        if hh_df.empty:
            return pd.DataFrame()

        self._batch_offset += len(hh_df)
        hh_df = self._normalize_columns(hh_df)
        hh_df = self._adjust_income(hh_df)
        if 'ST' in hh_df.columns:
            hh_df = hh_df[hh_df['ST'] == state].copy()

        return hh_df

    def reset_batch_state(self) -> None:
        """Reset batch loading state to start from the beginning."""
        self._batch_offset = 0
        self._total_households = None
        logger.debug("Reset batch loading state")

    def load_persons_for_households(
        self,
        serialnos: list[str],
        state: str = DEFAULT_STATE,
        pums_type: str = DEFAULT_PUMS_TYPE,
    ) -> pd.DataFrame:
        """Load person records for specific households.

        Args:
            serialnos: List of SERIALNO values to load.
            state: State FIPS code (default: '15' for Hawaii).
            pums_type: ``'1yr'`` or ``'5yr'`` for 1-year or 5-year PUMS data.

        Returns:
            DataFrame with person data for the specified households.
        """
        if not serialnos or (hasattr(serialnos, '__len__') and len(serialnos) == 0):
            return pd.DataFrame()

        person_file = self._person_file_path(state, pums_type)
        if not person_file.exists():
            raise self._missing("person", person_file, state)

        if hasattr(serialnos, 'tolist'):
            serialnos = serialnos.tolist()
        serialno_set = {str(sn) for sn in serialnos}

        if person_file.suffix == '.parquet':
            person_df = pd.read_parquet(person_file)
            person_df = person_df[
                person_df['SERIALNO'].astype(str).isin(serialno_set)
            ].copy()
        else:
            csv_dtypes = {
                k: (float if v is int else v)
                for k, v in self.person_columns.items()
            }
            chunks = []
            for chunk in pd.read_csv(
                person_file, chunksize=10_000, dtype=csv_dtypes
            ):
                filtered = chunk[chunk['SERIALNO'].astype(str).isin(serialno_set)]
                if not filtered.empty:
                    chunks.append(filtered)
            person_df = pd.concat(chunks, ignore_index=True) if chunks else pd.DataFrame()

        if person_df.empty:
            return pd.DataFrame()

        person_df = self._normalize_columns(person_df)
        person_df = self._adjust_income(person_df)
        if 'ST' in person_df.columns:
            person_df = person_df[person_df['ST'] == state].copy()

        return person_df

    def load_data(
        self,
        year: int = DEFAULT_PUMS_YEAR,
        state: str = DEFAULT_STATE,
        sample_size: int | None = None,
        pums_type: str = DEFAULT_PUMS_TYPE,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Load and process PUMS data for the specified year and state.

        Returns:
            Tuple of (persons_df, households_df) DataFrames.
        """
        logger.info("Loading PUMS data for state %s (%s)...", state, pums_type)

        total_households = self.get_total_households(state=state, pums_type=pums_type)
        logger.info("Loading all %d households...", total_households)

        self.reset_batch_state()
        hh_batches = []
        while True:
            batch = self.load_households_batch(
                batch_size=10_000, state=state, pums_type=pums_type
            )
            if batch.empty:
                break
            hh_batches.append(batch)

        hh_df = pd.concat(hh_batches, ignore_index=True) if hh_batches else pd.DataFrame()

        person_df = self.load_persons_for_households(
            serialnos=hh_df['SERIALNO'].tolist(),
            state=state,
            pums_type=pums_type,
        )

        if sample_size and sample_size < len(person_df):
            logger.info("Taking random sample of %d persons...", sample_size)
            person_df = person_df.sample(n=sample_size, random_state=42)

        return person_df, hh_df

    def _normalize_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """Rename columns for cross-vintage compatibility (e.g. STATE→ST in 2024 PUMS).

        Also normalizes ``ST`` to str: parquet files store it as int64
        (the ``household_columns``/``person_columns`` dtype maps only apply
        to CSV reads), and an int64 ``ST`` silently fails the
        ``ST == state`` filter, dropping every row.
        """
        rename_map = {
            old: new
            for old, new in self._column_renames.items()
            if old in df.columns and new not in df.columns
        }
        if rename_map:
            df = df.rename(columns=rename_map)
        if 'ST' in df.columns and df['ST'].dtype != object:
            df = df.copy()
            df['ST'] = df['ST'].astype(str)
        return self._coalesce_puma(df)

    def _coalesce_puma(self, df: pd.DataFrame) -> pd.DataFrame:
        """Backfill ``PUMA`` from ``PUMA20``/``PUMA10`` for 5-year vintages.

        The 2018-2022 5-year ACS PUMS exposes both ``PUMA10`` and ``PUMA20``
        because the 2020-vintage PUMA boundaries were adopted mid-window:
        rows from survey years 2018-2019 carry valid ``PUMA10`` and
        ``PUMA20=-9``, while 2020-2022 rows carry valid ``PUMA20`` and
        ``PUMA10=-9``. Older 1-year files only have ``PUMA10`` (or a bare
        ``PUMA``); newer 1-year files only have ``PUMA20``. Coalesce to a
        single ``PUMA`` column, preferring ``PUMA20`` when valid (≥0).
        """
        if "PUMA" in df.columns:
            return df
        has20 = "PUMA20" in df.columns
        has10 = "PUMA10" in df.columns
        if not (has20 or has10):
            return df
        df = df.copy()
        if has20 and has10:
            puma20 = pd.to_numeric(df["PUMA20"], errors="coerce")
            puma10 = pd.to_numeric(df["PUMA10"], errors="coerce")
            df["PUMA"] = puma20.where(puma20 >= 0, puma10)
        elif has20:
            df["PUMA"] = pd.to_numeric(df["PUMA20"], errors="coerce")
        else:
            df["PUMA"] = pd.to_numeric(df["PUMA10"], errors="coerce")
        return df

    def _adjust_income(self, df: pd.DataFrame) -> pd.DataFrame:
        """Fill NA values for monetary columns with 0; ensure ADJINC is float."""
        df = df.copy()
        monetary_cols = [
            'WAGP', 'SEMP', 'INTP', 'RETP', 'SSP', 'SSIP',
            'OIP', 'PAP', 'PINCP', 'HINCP', 'PERNP', 'POVPIP',
        ]
        for col in monetary_cols:
            if col in df.columns:
                df[col] = df[col].fillna(0)
        if 'ADJINC' in df.columns:
            df['ADJINC'] = df['ADJINC'].fillna(1.0).astype(float)
        return df


def load_pums_data(
    year: int = DEFAULT_PUMS_YEAR,
    state: str = DEFAULT_STATE,
    sample_size: int | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Convenience function to load PUMS data.

    Returns:
        Tuple of (persons_df, households_df) DataFrames.
    """
    loader = PUMSDataLoader()
    return loader.load_data(year=year, state=state, sample_size=sample_size)
