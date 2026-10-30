"""
Dynamic Growth Rate Loader

Reads fetched economic data (FRED, BLS, BEA) and computes growth rates used
by the projection engine. Each rate has a hardcoded fallback that matches the
original model assumptions — ensuring the model always runs, even without
API data.

Replaces these hardcoded values when data is available:
    - Inflation: 8.84% resident (3yr), 6.81% nonresident (4yr)
    - Interest/dividends: 5.0%/yr
    - Capital gains: 5.0%/yr (via HPI proxy)
    - Retirement income: 3.5%/yr
    - Social Security: 3.8%/yr
    - Other income: 3.0%/yr
    - Population growth: -0.83%/yr working-age, +5.0%/yr seniors

Usage:
    from tax_modeler.projection.growth_rate_loader import load_all_rates
    rates = load_all_rates(project_root)
    # rates['fixed_rates'] → dict for SourceSpecificGrowthProjector
    # rates['inflation_resident'] → float for income_growth.py
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

# Reasonable bounds for validation (annual rates)
_BOUNDS = {
    "cpi_annual": (-0.02, 0.15),          # -2% to +15%
    "hpi_annual": (-0.10, 0.20),           # -10% to +20%
    "interest_rate": (0.01, 0.15),         # 1% to 15%
    "dividend_rate": (0.01, 0.15),         # 1% to 15%
    "retirement_rate": (0.01, 0.10),       # 1% to 10%
    "ss_cola_rate": (0.01, 0.10),          # 1% to 10%
    "other_income_rate": (0.005, 0.08),    # 0.5% to 8%
    "pop_growth": (-0.05, 0.10),           # -5% to +10%
}

# Original hardcoded defaults (from source_specific_growth.py and income_growth.py)
_DEFAULTS = {
    "intp": 0.050,
    "div": 0.050,
    "retp": 0.035,
    "ssp": 0.038,
    "oip": 0.030,
    "cap_gains_rate": 0.050,
    "inflation_resident": 1.0884,        # 3-year total factor (2023→2026)
    "inflation_nonresident": 1.0681,     # 4-year total factor (2022→2026)
    "pop_growth_working_age": -0.0083,
    "pop_growth_senior": 0.050,
}


def _validate_rate(rate: float, name: str, bounds_key: str) -> float | None:
    """Validate a computed rate against reasonable bounds.

    Returns the rate if valid, None if out of bounds.
    """
    lo, hi = _BOUNDS.get(bounds_key, (-0.10, 0.30))
    if lo <= rate <= hi:
        return rate
    logger.warning(
        f"Computed {name} rate {rate:.4f} is outside bounds [{lo}, {hi}]. "
        f"Using fallback."
    )
    return None


def _compute_cagr(series: pd.Series, years: int) -> float | None:
    """Compute CAGR from the last N years of a time series.

    Args:
        series: Pandas Series with numeric values, ordered by time.
        years: Number of years for CAGR computation.

    Returns:
        Annual CAGR as a decimal, or None if insufficient data.
    """
    if len(series) < 2:
        return None

    end_val = series.iloc[-1]
    # Find the value approximately `years` periods back
    if len(series) > years:
        start_val = series.iloc[-(years + 1)]
    else:
        start_val = series.iloc[0]
        years = len(series) - 1

    if start_val <= 0 or end_val <= 0 or years <= 0:
        return None

    cagr = (end_val / start_val) ** (1.0 / years) - 1
    return cagr


def _read_fred_csv(project_root: Path, filename: str) -> pd.DataFrame | None:
    """Read a FRED CSV file from data/external/fred/.

    Returns DataFrame with 'date' and 'value' columns, sorted by date.
    """
    path = project_root / "data" / "external" / "fred" / filename
    if not path.exists():
        return None

    try:
        df = pd.read_csv(path, parse_dates=["date"])
        df = df.sort_values("date").reset_index(drop=True)
        return df
    except Exception as e:
        logger.warning(f"Failed to read {path}: {e}")
        return None


def _read_bls_cpi_csv(project_root: Path) -> pd.DataFrame | None:
    """Read BLS CPI CSV from data/external/bls_cpi/."""
    path = project_root / "data" / "external" / "bls_cpi" / "honolulu_cpi.csv"
    if not path.exists():
        return None

    try:
        df = pd.read_csv(path, parse_dates=["date"])
        df = df.sort_values("date").reset_index(drop=True)
        return df
    except Exception as e:
        logger.warning(f"Failed to read BLS CPI: {e}")
        return None


def _load_cpi_cagr(project_root: Path, years: int) -> float:
    """Load CPI-based inflation CAGR from FRED and/or BLS data.

    Tries BLS Honolulu CPI first (finer geography), falls back to FRED Hawaii CPI.
    Returns a total inflation factor (e.g., 1.0884 for 8.84% over the period).

    Args:
        project_root: Path to project root.
        years: Number of years for the inflation window.
    """
    fallback_key = "inflation_resident" if years <= 3 else "inflation_nonresident"
    fallback = _DEFAULTS[fallback_key]
    source_used = None

    # Try BLS CPI first (Honolulu-specific, more granular)
    bls_df = _read_bls_cpi_csv(project_root)
    if bls_df is not None and len(bls_df) >= 2:
        # Use annual January values for consistency
        annual = bls_df.groupby(bls_df["date"].dt.year)["value"].first().sort_index()
        cagr = _compute_cagr(annual, years)
        if cagr is not None:
            validated = _validate_rate(cagr, "BLS CPI", "cpi_annual")
            if validated is not None:
                total_factor = (1 + validated) ** years
                source_used = f"BLS Honolulu CPI ({validated:.2%}/yr, {years}yr → {total_factor:.4f})"
                logger.info(f"Inflation ({years}yr): {source_used}")
                return total_factor

    # Fall back to FRED Hawaii CPI
    fred_df = _read_fred_csv(project_root, "CUUSA426SA0_hawaii_cpi.csv")
    if fred_df is not None and len(fred_df) >= 2:
        cagr = _compute_cagr(fred_df["value"], years)
        if cagr is not None:
            validated = _validate_rate(cagr, "FRED CPI", "cpi_annual")
            if validated is not None:
                total_factor = (1 + validated) ** years
                source_used = f"FRED Hawaii CPI ({validated:.2%}/yr, {years}yr → {total_factor:.4f})"
                logger.info(f"Inflation ({years}yr): {source_used}")
                return total_factor

    logger.info(f"Inflation ({years}yr): using fallback {fallback:.4f}")
    return fallback


def _load_interest_rate(project_root: Path) -> float:
    """Load interest income growth rate from BEA SAINC5N line 46.

    BEA line 46 = Dividends, interest, and rent.
    """
    fallback = _DEFAULTS["intp"]
    bea_path = project_root / "data" / "raw" / "bea_hawaii_sainc5n.csv"
    if not bea_path.exists():
        logger.info(f"Interest rate: using fallback {fallback:.1%} (no BEA data)")
        return fallback

    try:
        df = pd.read_csv(bea_path)
        line46 = df[df["line_code"] == 46].sort_values("year")
        if len(line46) < 2:
            logger.info(f"Interest rate: using fallback {fallback:.1%} (insufficient BEA data)")
            return fallback

        # Use 3-year CAGR from most recent data
        cagr = _compute_cagr(line46["value_thousands"], min(3, len(line46) - 1))
        if cagr is None:
            return fallback

        # Moderate: BEA 2022-2024 showed 9.4% (interest rate surge); apply 50% moderation
        # for projected years as Fed rate cuts normalize
        moderated = cagr * 0.5
        validated = _validate_rate(moderated, "interest (BEA moderated)", "interest_rate")
        if validated is not None:
            logger.info(f"Interest rate: {validated:.1%} (BEA line 46 CAGR {cagr:.1%}, moderated 50%)")
            return validated
    except Exception as e:
        logger.warning(f"Failed to compute interest rate from BEA: {e}")

    logger.info(f"Interest rate: using fallback {fallback:.1%}")
    return fallback


def _load_dividend_rate(project_root: Path) -> float:
    """Load dividend growth rate. Uses same BEA source as interest."""
    # Dividends track closely with interest in Hawaii (both in BEA line 46)
    # Use the same computation but with less moderation (equities less rate-sensitive)
    fallback = _DEFAULTS["div"]
    bea_path = project_root / "data" / "raw" / "bea_hawaii_sainc5n.csv"
    if not bea_path.exists():
        logger.info(f"Dividend rate: using fallback {fallback:.1%}")
        return fallback

    try:
        df = pd.read_csv(bea_path)
        line46 = df[df["line_code"] == 46].sort_values("year")
        if len(line46) < 2:
            return fallback

        cagr = _compute_cagr(line46["value_thousands"], min(3, len(line46) - 1))
        if cagr is None:
            return fallback

        # Moderate less than interest (equities are not as rate-sensitive)
        moderated = cagr * 0.55
        validated = _validate_rate(moderated, "dividend (BEA moderated)", "dividend_rate")
        if validated is not None:
            logger.info(f"Dividend rate: {validated:.1%} (BEA line 46 CAGR {cagr:.1%}, moderated 55%)")
            return validated
    except Exception as e:
        logger.warning(f"Failed to compute dividend rate from BEA: {e}")

    logger.info(f"Dividend rate: using fallback {fallback:.1%}")
    return fallback


def _load_retirement_rate(project_root: Path) -> float:
    """Load retirement income growth rate.

    Uses CPI as base (retirement distributions track inflation) plus
    a premium for growing retiree population.
    """
    fallback = _DEFAULTS["retp"]

    # Try to derive from CPI + population trend
    fred_df = _read_fred_csv(project_root, "CUUSA426SA0_hawaii_cpi.csv")
    if fred_df is not None and len(fred_df) >= 3:
        cpi_cagr = _compute_cagr(fred_df["value"], min(3, len(fred_df) - 1))
        if cpi_cagr is not None and 0 < cpi_cagr < 0.10:
            # Retirement distributions ≈ CPI + 0.5% (population shift to retirees)
            rate = cpi_cagr + 0.005
            validated = _validate_rate(rate, "retirement", "retirement_rate")
            if validated is not None:
                logger.info(f"Retirement rate: {validated:.1%} (CPI {cpi_cagr:.1%} + 0.5% retiree growth)")
                return validated

    logger.info(f"Retirement rate: using fallback {fallback:.1%}")
    return fallback


def _load_ss_cola_rate(project_root: Path) -> float:
    """Load Social Security COLA rate.

    SSA publishes COLAs in advance. Recent: 8.7% (2023), 3.2% (2024), 2.5% (2025).
    We use CPI as a proxy when COLA data isn't directly available.
    """
    fallback = _DEFAULTS["ssp"]

    # Known COLAs (hardcoded since SSA publishes these)
    known_colas = {
        2023: 0.087,
        2024: 0.032,
        2025: 0.025,
    }

    # Compute compound CAGR from known COLAs
    if known_colas:
        years = sorted(known_colas.keys())
        compound = 1.0
        for y in years:
            compound *= (1 + known_colas[y])
        n_years = len(years)
        cagr = compound ** (1.0 / n_years) - 1

        # For projected years beyond known COLAs, use CPI as proxy
        fred_df = _read_fred_csv(project_root, "CUUSA426SA0_hawaii_cpi.csv")
        if fred_df is not None and len(fred_df) >= 3:
            cpi_cagr = _compute_cagr(fred_df["value"], 3)
            if cpi_cagr is not None and 0 < cpi_cagr < 0.10:
                # Blend known COLAs with CPI-projected COLAs
                # Weight recent COLAs more (they're actual data)
                blended = 0.6 * cagr + 0.4 * cpi_cagr
                validated = _validate_rate(blended, "SS COLA", "ss_cola_rate")
                if validated is not None:
                    logger.info(
                        f"SS COLA rate: {validated:.1%} "
                        f"(known COLA CAGR {cagr:.1%}, CPI proxy {cpi_cagr:.1%}, blended)"
                    )
                    return validated

        validated = _validate_rate(cagr, "SS COLA (known only)", "ss_cola_rate")
        if validated is not None:
            logger.info(f"SS COLA rate: {validated:.1%} (from known COLAs only)")
            return validated

    logger.info(f"SS COLA rate: using fallback {fallback:.1%}")
    return fallback


def _load_other_income_rate(project_root: Path) -> float:
    """Load other income growth rate (general inflation proxy)."""
    fallback = _DEFAULTS["oip"]

    # Use CPI CAGR directly — "other income" tracks general inflation
    fred_df = _read_fred_csv(project_root, "CUUSA426SA0_hawaii_cpi.csv")
    if fred_df is not None and len(fred_df) >= 3:
        cpi_cagr = _compute_cagr(fred_df["value"], min(3, len(fred_df) - 1))
        if cpi_cagr is not None:
            validated = _validate_rate(cpi_cagr, "other income (CPI)", "other_income_rate")
            if validated is not None:
                logger.info(f"Other income rate: {validated:.1%} (CPI-based)")
                return validated

    logger.info(f"Other income rate: using fallback {fallback:.1%}")
    return fallback


def _load_cap_gains_rate(project_root: Path) -> float:
    """Load capital gains growth rate from FRED House Price Index.

    HPI is a proxy for real asset appreciation. We blend it with equity
    market returns (fixed 7% nominal long-run) since cap gains come from
    both real estate and equities.
    """
    fallback = _DEFAULTS["cap_gains_rate"]

    fred_df = _read_fred_csv(project_root, "HISTHPI_hawaii_hpi.csv")
    if fred_df is not None and len(fred_df) >= 8:  # Need multi-year data
        # Use 5-year CAGR for HPI
        # HPI is quarterly; use annual averages
        fred_df["year"] = fred_df["date"].dt.year
        annual = fred_df.groupby("year")["value"].mean().sort_index()
        hpi_cagr = _compute_cagr(annual, min(5, len(annual) - 1))

        if hpi_cagr is not None:
            validated_hpi = _validate_rate(hpi_cagr, "HPI", "hpi_annual")
            if validated_hpi is not None:
                # Blend: 40% real estate (HPI), 60% equities (7% nominal long-run)
                equity_return = 0.07
                blended = 0.40 * validated_hpi + 0.60 * equity_return
                validated = _validate_rate(blended, "cap gains (blended)", "hpi_annual")
                if validated is not None:
                    logger.info(
                        f"Capital gains rate: {validated:.1%} "
                        f"(HPI {validated_hpi:.1%} × 40% + equity 7% × 60%)"
                    )
                    return validated

    logger.info(f"Capital gains rate: using fallback {fallback:.1%}")
    return fallback


def _load_pop_growth(project_root: Path, cohort: str) -> float:
    """Load population growth rate.

    Currently falls back to hardcoded DBEDT estimates since population
    data requires DBEDT downloads (not yet automated).
    """
    if cohort == "working_age":
        fallback = _DEFAULTS["pop_growth_working_age"]
    else:
        fallback = _DEFAULTS["pop_growth_senior"]

    # TODO: Load from DBEDT data when fetch_dbedt_qser.py is implemented
    logger.info(f"Population growth ({cohort}): using fallback {fallback:.2%} (DBEDT not yet automated)")
    return fallback


def load_all_rates(project_root: str | Path) -> dict:
    """Load all data-driven growth rates with hardcoded fallbacks.

    Args:
        project_root: Path to the project root directory.

    Returns:
        Dict with keys:
            - 'fixed_rates': dict compatible with SourceSpecificGrowthProjector
            - 'cap_gains_rate': float
            - 'inflation_resident': float (total factor, e.g., 1.0884)
            - 'inflation_nonresident': float (total factor)
            - 'pop_growth_working_age': float (annual rate)
            - 'pop_growth_senior': float (annual rate)
            - 'source_details': dict mapping rate name to source description
    """
    project_root = Path(project_root)
    logger.info("Loading data-driven growth rates...")

    rates = {
        "fixed_rates": {
            "intp": _load_interest_rate(project_root),
            "div": _load_dividend_rate(project_root),
            "retp": _load_retirement_rate(project_root),
            "ssp": _load_ss_cola_rate(project_root),
            "oip": _load_other_income_rate(project_root),
        },
        "cap_gains_rate": _load_cap_gains_rate(project_root),
        "inflation_resident": _load_cpi_cagr(project_root, years=3),
        "inflation_nonresident": _load_cpi_cagr(project_root, years=4),
        "pop_growth_working_age": _load_pop_growth(project_root, "working_age"),
        "pop_growth_senior": _load_pop_growth(project_root, "senior"),
    }

    # Summary
    fr = rates["fixed_rates"]
    logger.info(
        f"Growth rates loaded:\n"
        f"  Interest:     {fr['intp']:.1%}  (default: {_DEFAULTS['intp']:.1%})\n"
        f"  Dividends:    {fr['div']:.1%}  (default: {_DEFAULTS['div']:.1%})\n"
        f"  Retirement:   {fr['retp']:.1%}  (default: {_DEFAULTS['retp']:.1%})\n"
        f"  SS COLA:      {fr['ssp']:.1%}  (default: {_DEFAULTS['ssp']:.1%})\n"
        f"  Other income: {fr['oip']:.1%}  (default: {_DEFAULTS['oip']:.1%})\n"
        f"  Cap gains:    {rates['cap_gains_rate']:.1%}  (default: {_DEFAULTS['cap_gains_rate']:.1%})\n"
        f"  Inflation (resident 3yr):    {rates['inflation_resident']:.4f}  (default: {_DEFAULTS['inflation_resident']:.4f})\n"
        f"  Inflation (nonresident 4yr): {rates['inflation_nonresident']:.4f}  (default: {_DEFAULTS['inflation_nonresident']:.4f})\n"
        f"  Pop growth (working-age):    {rates['pop_growth_working_age']:.2%}  (default: {_DEFAULTS['pop_growth_working_age']:.2%})\n"
        f"  Pop growth (senior):         {rates['pop_growth_senior']:.2%}  (default: {_DEFAULTS['pop_growth_senior']:.2%})"
    )

    return rates
