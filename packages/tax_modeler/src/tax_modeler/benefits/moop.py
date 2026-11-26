"""MOOP (Medical Out-Of-Pocket) imputation for SPM resources.

The Supplemental Poverty Measure subtracts medical out-of-pocket spending
(premiums, Medicare Part B, and other out-of-pocket medical costs) from a
unit's resources. PUMS doesn't carry it, so each SPM unit gets the weighted
mean MOOP of Hawaii donor SPM units like it: the Census Bureau's own ACS SPM
research extracts for 2021-2023, in which Census imputed MOOP onto ACS
households from the CPS ASEC (``scripts/build_moop_donors.py`` builds
``data/cps_donor/acs_spm_hawaii_moop_donors.csv`` from them).

Units are matched on size (1-5+), members 65 and over (0, 1, 2+) and money
income quintile, falling back to size x 65+ and then 65+ alone where a cell
is empty. Donor dollars are put in the target year's by CPI-U (annual
averages to 2024, then ``CREDIT_PARAM_CPI_GROWTH``).

MOOP is an SPM-unit expense, as Census measures it. Given ``persons`` and
units that carry ``spm_unit_id``, :func:`compute_moop_for_units` imputes one
amount per SPM unit (members and ages from ``persons``, money income summed
over its tax units) and books it on the unit's first tax unit, so
``aggregate_to_spm_units`` sums it back exactly. Without them (tax-unit
diagnostics) each tax unit is matched as a unit of its own.

Until November 2026 this module matched tax units to
``cps_moop_donors.csv``, a 200-row synthetic test fixture (no zeros, nothing
above $9,057), which put Hawaii MOOP at about half of Census's.

Public API: :func:`compute_moop_for_units`.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

_DEFAULT_DONOR_PATH = (
    Path(__file__).resolve().parents[1]
    / "data" / "cps_donor" / "acs_spm_hawaii_moop_donors.csv"
)

# Donor dollars are put in DOLLAR_YEAR's (CPI-U annual averages, BLS CUUR0000SA0).
DOLLAR_YEAR = 2023
CPI_U: dict[int, float] = {2021: 270.970, 2022: 292.655, 2023: 304.702, 2024: 313.689}

_INCOME_QUANTILES = (0.2, 0.4, 0.6, 0.8)
_MAX_SIZE = 5
_MAX_65 = 2


def cpi_index(year: int) -> float:
    """CPI-U for ``year``: the annual average to 2024, then compounded at ``CREDIT_PARAM_CPI_GROWTH``."""
    if year in CPI_U:
        return CPI_U[year]
    last, first = max(CPI_U), min(CPI_U)
    if year < first:
        raise ValueError(f"no CPI-U before {first}")
    from tax_modeler.credits.eitc import CREDIT_PARAM_CPI_GROWTH

    return CPI_U[last] * (1.0 + CREDIT_PARAM_CPI_GROWTH) ** (year - last)


def load_donors(path: Path | None = None) -> pd.DataFrame:
    """The donor SPM units with money income and MOOP in DOLLAR_YEAR dollars."""
    d = pd.read_csv(path or _DEFAULT_DONOR_PATH)
    to_dollar_year = CPI_U[DOLLAR_YEAR] / d["year"].map(cpi_index)
    return d.assign(money_income=d["money_income"] * to_dollar_year, moop=d["moop"] * to_dollar_year)


def _weighted_quantiles(x: np.ndarray, w: np.ndarray, qs) -> np.ndarray:
    o = np.argsort(x)
    cum = np.cumsum(w[o]) / w.sum()
    return np.array([x[o][np.searchsorted(cum, q)] for q in qs])


class _Donors:
    """Donor pools by (size, 65+, income quintile), falling back to (size, 65+) and then 65+ alone.

    :meth:`pools` gives each recipient the first non-empty pool at the finest level; :meth:`mean` is that
    pool's weighted mean MOOP, :meth:`draw` draws donors from it in proportion to their weights.
    """

    def __init__(self, donors: pd.DataFrame):
        w = donors["weight"].to_numpy(dtype=float)
        self.edges = _weighted_quantiles(donors["money_income"].to_numpy(dtype=float), w, _INCOME_QUANTILES)
        keys = self._keys(donors["n_persons"], donors["n_65"], donors["money_income"])
        moop = donors["moop"].to_numpy(dtype=float)
        self.values: list[np.ndarray] = []
        self.probs: list[np.ndarray] = []
        self.means: list[float] = []
        self.cum: list[np.ndarray] = []
        self.levels: list[tuple[list[str], dict]] = []
        for cols in (["size", "old", "q"], ["size", "old"], ["old"]):
            table = {}
            for key, idx in keys.groupby(cols).indices.items():
                table[key if len(cols) > 1 else (key[0] if isinstance(key, tuple) else key)] = self._add(moop[idx], w[idx])
            self.levels.append((cols, table))
        self.overall = self._add(moop, w)

    def _add(self, values: np.ndarray, weights: np.ndarray) -> int:
        order = np.argsort(values, kind="stable")      # sorted, so a draw's uniform is the donor's rank in the pool
        values, weights = np.asarray(values, dtype=float)[order], np.asarray(weights, dtype=float)[order]
        self.values.append(values)
        self.probs.append(weights / weights.sum())
        self.cum.append(np.cumsum(self.probs[-1]))
        self.means.append(float(values @ self.probs[-1]))
        return len(self.values) - 1

    def _keys(self, n_persons, n_65, money) -> pd.DataFrame:
        return pd.DataFrame({
            "size": np.clip(np.asarray(n_persons, dtype=int), 1, _MAX_SIZE),
            "old": np.clip(np.asarray(n_65, dtype=int), 0, _MAX_65),
            "q": np.searchsorted(self.edges, np.asarray(money, dtype=float), side="right"),
        })

    def pools(self, n_persons, n_65, money) -> np.ndarray:
        keys = self._keys(n_persons, n_65, money)
        out = np.full(len(keys), -1)
        for cols, table in self.levels:
            todo = out < 0
            if not todo.any():
                break
            idx = keys.loc[todo, cols].itertuples(index=False, name=None)
            out[todo] = [table.get(k if len(cols) > 1 else k[0], -1) for k in idx]
        return np.where(out < 0, self.overall, out)

    def mean(self, pools: np.ndarray) -> np.ndarray:
        return np.asarray(self.means)[pools]

    def draw(self, pools: np.ndarray, u: np.ndarray) -> np.ndarray:
        """The donor MOOP at each row's uniform ``u`` (rows x draws) in its pool's weighted distribution: the
        inverse CDF, so a unit's draw depends on its own uniform and pool only."""
        out = np.zeros(u.shape)
        for pool in np.unique(pools):
            rows = np.flatnonzero(pools == pool)
            idx = np.searchsorted(self.cum[pool], u[rows], side="right")
            out[rows] = self.values[pool][np.minimum(idx, len(self.values[pool]) - 1)]
        return out


def _unit_pools(units, persons, donors, *, money, spm_unit_id_col, age_col, spouse_age_col, dependents_col,
                filing_status_col) -> tuple[np.ndarray, pd.Series | None]:
    """Each tax unit's donor pool as a unit of its own, and (with ``persons`` and SPM ids) each SPM unit's."""
    joint = (units[filing_status_col] == "married_filing_jointly").to_numpy()
    size = 1 + joint.astype(int) + units[dependents_col].fillna(0).clip(lower=0).astype(int).to_numpy()
    age = units[age_col].fillna(0).to_numpy(dtype=float) if age_col in units.columns else np.zeros(len(units))
    spouse = (units[spouse_age_col].fillna(0).to_numpy(dtype=float) if spouse_age_col in units.columns
              else np.zeros(len(units)))
    n_65 = (age >= 65).astype(int) + (joint & (spouse >= 65)).astype(int)
    own = donors.pools(size, n_65, money)
    if persons is None or spm_unit_id_col not in units.columns:
        return own, None
    ids = units[spm_unit_id_col]
    in_spm = ids.notna().to_numpy()
    if not in_spm.any():
        return own, None
    pers = persons.loc[persons[spm_unit_id_col].notna()]
    unit_money = pd.Series(money[in_spm]).groupby(ids[in_spm].to_numpy()).sum()
    spm = pd.DataFrame({
        "n": pers.groupby(spm_unit_id_col).size(),
        "old": (pers["AGEP"] >= 65).groupby(pers[spm_unit_id_col]).sum(),
        "money": unit_money,
    }).dropna(subset=["money"])
    spm["n"] = spm["n"].fillna(1)
    spm["old"] = spm["old"].fillna(0)
    return own, pd.Series(donors.pools(spm["n"], spm["old"], spm["money"]), index=spm.index)


def compute_moop_for_units(
    units: pd.DataFrame,
    persons: pd.DataFrame | None = None,
    *,
    year: int = DOLLAR_YEAR,
    donor_path: Path | None = None,
    out_col: str = "moop_amount",
    spm_unit_id_col: str = "spm_unit_id",
    age_col: str = "primary_agep",
    spouse_age_col: str = "secondary_agep",
    dependents_col: str = "num_dependents",
    income_col: str | None = None,
    filing_status_col: str = "filing_status",
) -> pd.DataFrame:
    """Impute annual MOOP, in ``year``'s dollars, from Hawaii ACS SPM donors.

    Parameters
    ----------
    units:
        Tax-unit DataFrame.
    persons:
        PUMS persons with ``spm_unit_id`` and ``AGEP``. With it, and units
        that carry ``spm_unit_id``, MOOP is imputed per SPM unit and booked
        on the unit's first tax unit (others get 0); tax units outside any
        SPM unit, and every unit when ``persons`` is None, are matched on
        their own composition (filers, dependents, filer ages).
    year:
        Dollar year of the result and of the income it matches on.
    income_col:
        Money income to match on; default ``spm_money_income`` when the frame
        has it (a projected frame), else ``total_cash_income``.

    Returns
    -------
    pd.DataFrame
        Copy of ``units`` with ``out_col`` (non-negative annual dollars): the
        weighted mean of the unit's donor pool, the expected MOOP. A cell
        mean gives every unit in a cell the same amount, which bunches units
        at the poverty line; headcounts should draw MOOP instead
        (:func:`moop_resource_draws`).
    """
    donors = _Donors(load_donors(donor_path))
    scale = cpi_index(year) / CPI_U[DOLLAR_YEAR]
    out = units.copy()
    if income_col is None:
        income_col = "spm_money_income" if "spm_money_income" in out.columns else "total_cash_income"
    money = out[income_col].fillna(0).astype(float).to_numpy() / scale
    own, per_spm = _unit_pools(out, persons, donors, money=money, spm_unit_id_col=spm_unit_id_col, age_col=age_col,
                               spouse_age_col=spouse_age_col, dependents_col=dependents_col,
                               filing_status_col=filing_status_col)
    moop = donors.mean(own)
    if per_spm is not None:
        ids = out[spm_unit_id_col]
        first = (~ids.duplicated() & ids.notna()).to_numpy()
        spm_mean = pd.Series(donors.mean(per_spm.to_numpy()), index=per_spm.index)
        moop = np.where(ids.notna().to_numpy(), 0.0, moop)
        moop = np.where(first, ids.map(spm_mean).fillna(0).to_numpy(dtype=float), moop)

    out[out_col] = np.maximum(moop * scale, 0.0)
    return out


def moop_resource_draws(
    units: pd.DataFrame,
    persons: pd.DataFrame,
    *,
    year: int = DOLLAR_YEAR,
    donor_path: Path | None = None,
    spm_unit_id_col: str = "spm_unit_id",
    age_col: str = "primary_agep",
    spouse_age_col: str = "secondary_agep",
    dependents_col: str = "num_dependents",
    income_col: str | None = None,
    filing_status_col: str = "filing_status",
):
    """A ``DrawSpec.resource_draws`` hook: MOOP drawn from each SPM unit's donor pool.

    ``units`` and ``persons`` as for :func:`compute_moop_for_units`, whose
    expected MOOP the SPM frame's resources carry. The hook, called as
    ``hook(df, u)`` on the SPM frame with ``u`` an ``(SPM units, n_draws)``
    array of uniform numbers (``poverty.draws.uniforms``, keyed by unit),
    returns an ``(SPM units, n_draws)`` array: the expected MOOP less a donor's MOOP drawn in
    proportion to the donors' weights, in ``year``'s dollars, to add to the
    unit's resources in each draw. Its mean over draws is zero; it keeps the
    spread of medical spending that a cell mean removes. Units not found
    (no SPM unit, or not in ``units``) get zero.
    """
    donors = _Donors(load_donors(donor_path))
    scale = cpi_index(year) / CPI_U[DOLLAR_YEAR]
    if income_col is None:
        income_col = "spm_money_income" if "spm_money_income" in units.columns else "total_cash_income"
    money = units[income_col].fillna(0).astype(float).to_numpy() / scale
    _, per_spm = _unit_pools(units, persons, donors, money=money, spm_unit_id_col=spm_unit_id_col, age_col=age_col,
                             spouse_age_col=spouse_age_col, dependents_col=dependents_col,
                             filing_status_col=filing_status_col)
    if per_spm is None:
        raise ValueError("moop_resource_draws needs units with spm_unit_id and the persons frame")

    def hook(df: pd.DataFrame, u: np.ndarray) -> np.ndarray:
        pools = df[spm_unit_id_col].map(per_spm)
        found = pools.notna().to_numpy()
        out = np.zeros(u.shape)
        if found.any():
            p = pools[found].to_numpy(dtype=int)
            out[found] = (donors.mean(p)[:, None] - donors.draw(p, u[found])) * scale
        return out

    return hook


__all__ = ["compute_moop_for_units", "moop_resource_draws", "cpi_index", "load_donors", "DOLLAR_YEAR"]
