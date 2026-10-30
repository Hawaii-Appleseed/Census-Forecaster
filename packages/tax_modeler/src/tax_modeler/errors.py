"""Typed exceptions for tax_modeler.

Boundary failures (missing data files, bad year ranges, malformed DataFrames,
calibration non-convergence) should raise these instead of generic
``KeyError`` / ``ValueError`` / ``FileNotFoundError`` so that callers can
catch them by intent and surface clear remediation steps to end users.

Hierarchy:

    TaxModelerError
        ├── MissingDataError       (file/dir not found, env var unset)
        ├── DataValidationError    (DataFrame schema, dtype, emptiness)
        ├── ConfigError            (bad year, unknown state, missing params)
        └── CalibrationError       (IPF non-convergence, target inconsistency)

Each subclass attaches structured context (paths searched, columns missing,
years available, etc.) so log/CLI output can render actionable guidance
without parsing the message string.
"""
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path


class TaxModelerError(Exception):
    """Base class for all tax_modeler-raised errors."""


class MissingDataError(TaxModelerError, FileNotFoundError):
    """A required data file or directory is absent.

    Inherits from :class:`FileNotFoundError` so legacy ``except
    FileNotFoundError`` blocks still catch it.
    """

    def __init__(
        self,
        message: str,
        *,
        path: Path | None = None,
        env_var: str | None = None,
    ) -> None:
        self.path = Path(path) if path is not None else None
        self.env_var = env_var
        hint_parts = [message]
        if self.path is not None:
            hint_parts.append(f"  searched: {self.path}")
        if env_var:
            hint_parts.append(f"  hint: set ${env_var} to override the default search path")
        super().__init__("\n".join(hint_parts))


class DataValidationError(TaxModelerError, ValueError):
    """A DataFrame failed schema or content validation."""

    def __init__(
        self,
        message: str,
        *,
        missing_columns: Iterable[str] | None = None,
        empty: bool = False,
    ) -> None:
        self.missing_columns = tuple(missing_columns) if missing_columns else ()
        self.empty = empty
        parts = [message]
        if self.missing_columns:
            parts.append(f"  missing columns: {list(self.missing_columns)}")
        if empty:
            parts.append("  input DataFrame is empty")
        super().__init__("\n".join(parts))


class ConfigError(TaxModelerError, ValueError):
    """A configuration value (year, state, parameter) is invalid or unknown."""

    def __init__(
        self,
        message: str,
        *,
        available: Iterable | None = None,
    ) -> None:
        self.available = tuple(available) if available is not None else ()
        parts = [message]
        if self.available:
            parts.append(f"  available: {list(self.available)}")
        super().__init__("\n".join(parts))


class CalibrationError(TaxModelerError, RuntimeError):
    """An IPF or rake calibration step failed to converge or produced a bad result."""

    def __init__(
        self,
        message: str,
        *,
        iterations: int | None = None,
        residual: float | None = None,
    ) -> None:
        self.iterations = iterations
        self.residual = residual
        parts = [message]
        if iterations is not None:
            parts.append(f"  iterations: {iterations}")
        if residual is not None:
            parts.append(f"  residual: {residual:.4g}")
        super().__init__("\n".join(parts))


REQUIRED_PUMS_COLUMNS = (
    "filing_status",
    "weight",
    "num_dependents",
)


def validate_units_schema(df, *, required: Iterable[str] = REQUIRED_PUMS_COLUMNS) -> None:
    """Validate that ``df`` has the columns required for downstream pipeline stages.

    Raises :class:`DataValidationError` if columns are missing or the frame
    is empty.  Used at public-API entry points (e.g. ``run_pipeline``) so
    bad input is rejected before stages waste compute on a broken frame.
    """
    if df is None or len(df) == 0:
        raise DataValidationError(
            "tax-units DataFrame is empty or None",
            empty=True,
        )
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise DataValidationError(
            "tax-units DataFrame is missing required columns",
            missing_columns=missing,
        )


__all__ = [
    "TaxModelerError",
    "MissingDataError",
    "DataValidationError",
    "ConfigError",
    "CalibrationError",
    "REQUIRED_PUMS_COLUMNS",
    "validate_units_schema",
]
