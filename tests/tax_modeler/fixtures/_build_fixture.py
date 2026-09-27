"""Build a deterministic synthetic PUMS fixture for smoke tests.

Run::

    python tests/tax_modeler/fixtures/_build_fixture.py

Writes ``synthetic_pums_persons.parquet`` and
``synthetic_pums_households.parquet`` next to this script.  Both files are
checked in so CI doesn't regenerate; this script exists so the fixture can
be regenerated when schema or distribution requirements change.

Design notes
------------

The fixture has 50 households / ~120 persons drawn from a fixed seed.
Income levels are chosen to span the four ITEP income bins (<$50K,
$50K-$200K, $200K-$1M, $1M+) so the resulting tax-unit DataFrame
exercises the bracket-walk math at every level, including the §235-51(f)
capital-gains cap.

Filing status mix targets DOTAX 2022 proportions roughly:
- ~40% married-filing-jointly
- ~10% married-filing-separately (high-disparity couples to trigger MFS)
- ~30% single
- ~20% head-of-household (single parent + child)

The schema mirrors the columns that
``tax_modeler.units.constructor.TaxUnitConstructor`` reads, plus the
benefit-relevant columns ``enrich_for_benefits`` uses
(``DIS``, ``HICOV``, ``HINS1``, ``HINS3``, ``HINS4``, ``WKHP``,
``CIT``, ``DIV``, ``OFTP_FS``):
``SERIALNO``, ``SPORDER``, ``RELSHIPP``, ``SEX``, ``AGEP``, ``MAR``,
``PINCP``, ``PWGTP``, ``RAC1P``, ``SCHL``, ``ESR``, ``WAGP``, ``SEMP``,
``INTP``, ``DIV``, ``RETP``, ``OIP``, ``PAP``, ``SSP``, ``SSIP``,
``ADJINC``, ``DIS``, ``HICOV``, ``HINS1``, ``HINS3``, ``HINS4``,
``WKHP``, ``CIT`` for persons; ``SERIALNO``, ``HINCP``, ``NP``,
``TYPE``, ``TEN``, ``YBL``, ``WGTP``, ``FS`` for households.

PUMS coding conventions used here:
  - ``DIS``       1 = with disability,           2 = without
  - ``HICOV``     1 = with health insurance,     2 = without
  - ``HINS1/3/4`` 1 = covered by the program,    2 = not covered
                  (HINS1=employer, HINS3=Medicaid, HINS4=Medicare)
  - ``CIT``       1-4 = US citizen/national/eligible, 5 = not a citizen
  - ``WKHP``      hours worked per week (NaN if not in labor force)
  - ``TEN``       1 = owner with mortgage, 2 = owner free, 3 = renter,
                  4 = occupied without rent (treated as renter)
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

OUT_DIR = Path(__file__).resolve().parent
SEED = 0xCAFEF00D

# Income tier centers ($) and their household share — picks roughly span
# the four ITEP bins so the smoke suite exercises every bracket level.
INCOME_TIERS = [
    (25_000,  0.20),   # <$50K
    (75_000,  0.40),
    (150_000, 0.20),   # $50K-$200K
    (350_000, 0.10),   # $200K-$1M
    (750_000, 0.07),
    (1_500_000, 0.03), # $1M+ (CG cap territory)
]


def _filing_pattern(rng: np.random.Generator) -> str:
    """Pick one of: 'mfj', 'mfs', 'single', 'hoh'."""
    return rng.choice(["mfj", "mfs", "single", "hoh"], p=[0.4, 0.1, 0.3, 0.2])


def _pick_income(rng: np.random.Generator) -> float:
    centers, probs = zip(*INCOME_TIERS)
    pick = rng.choice(len(centers), p=probs)
    # Lognormal jitter around the tier center
    return float(centers[pick] * rng.lognormal(mean=0.0, sigma=0.25))


def _make_person(serial: str, sporder: int, **overrides) -> dict:
    person = {
        "SERIALNO": serial,
        "SPORDER": sporder,
        "RELSHIPP": 20,
        "SEX": 1,
        "AGEP": 35,
        "MAR": 5,
        "PINCP": 0.0,
        "PWGTP": 100,
        "RAC1P": 1,
        "SCHL": 21,
        "ESR": 1,
        "WAGP": 0.0,
        "SEMP": 0.0,
        "INTP": 0.0,
        "DIV": 0.0,
        "RETP": 0.0,
        "OIP": 0.0,
        "PAP": 0.0,
        "SSP": 0.0,
        "SSIP": 0.0,
        "ADJINC": 1_000_000,
        "PUMA": "00100",
        # Benefit-relevant variables (Phase 7)
        "DIS": 2,        # default: not disabled
        "HICOV": 1,      # default: has insurance
        "HINS1": 1,      # default: employer-covered
        "HINS3": 2,      # default: not on Medicaid
        "HINS4": 2,      # default: not on Medicare
        "WKHP": 40,      # default: full-time
        "CIT": 1,        # default: US citizen
    }
    person.update(overrides)
    return person


def _make_household(serial: str, **overrides) -> dict:
    hh = {
        "SERIALNO": serial,
        "HINCP": 0.0,
        "NP": 1,
        "TYPE": 1,
        "TEN": 3,        # default: renter (most common in low-income)
        "YBL": 2000,
        "WGTP": 100,
        "PUMA": "00100",
        "FS": 2,         # food stamps received: 2=No (default)
    }
    hh.update(overrides)
    return hh


def _benefit_attrs(rng: np.random.Generator, income: float, age: int) -> dict:
    """Realistic distribution of disability / insurance / hours by income tier."""
    attrs: dict = {}
    # Disability: ~12% of adults overall, much higher at low incomes
    if age >= 18:
        p_disabled = 0.30 if income < 25_000 else (0.15 if income < 50_000 else 0.05)
        attrs["DIS"] = 1 if rng.random() < p_disabled else 2
    else:
        attrs["DIS"] = 1 if rng.random() < 0.05 else 2
    # Health insurance: ~95% covered overall in HI; coverage path varies by income
    has_cov = rng.random() < 0.95
    attrs["HICOV"] = 1 if has_cov else 2
    if has_cov:
        if income < 25_000:
            # Low income → likely Medicaid (Med-QUEST)
            attrs["HINS3"] = 1; attrs["HINS1"] = 2; attrs["HINS4"] = 2
        elif age >= 65:
            attrs["HINS4"] = 1; attrs["HINS1"] = 2; attrs["HINS3"] = 2
        else:
            attrs["HINS1"] = 1; attrs["HINS3"] = 2; attrs["HINS4"] = 2
    else:
        attrs["HINS1"] = 2; attrs["HINS3"] = 2; attrs["HINS4"] = 2
    # Hours worked: 0 if no earned income, else ~40h ± noise
    attrs["WKHP"] = 0 if income < 5_000 else int(np.clip(rng.normal(38, 6), 10, 60))
    # Citizenship: 95% citizen in HI
    attrs["CIT"] = 1 if rng.random() < 0.95 else 5
    return attrs


def _household_attrs(rng: np.random.Generator, income: float) -> dict:
    """Tenure + food-stamp receipt distribution by income tier."""
    # TEN: 1=owner-mortgage, 2=owner-free, 3=renter, 4=no-rent
    if income < 30_000:
        ten = rng.choice([3, 4, 1], p=[0.65, 0.05, 0.30])
    elif income < 100_000:
        ten = rng.choice([1, 3, 2], p=[0.55, 0.30, 0.15])
    else:
        ten = rng.choice([1, 2], p=[0.70, 0.30])
    # FS (received SNAP last 12 months): correlated with low income
    fs = 1 if (income < 30_000 and rng.random() < 0.45) else 2
    return {"TEN": int(ten), "FS": int(fs)}


def build_fixture(n_households: int = 50, seed: int = SEED) -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    persons: list[dict] = []
    households: list[dict] = []

    for i in range(n_households):
        serial = f"{i+1:07d}"
        pattern = _filing_pattern(rng)
        income = _pick_income(rng)
        hh_attrs = _household_attrs(rng, income)

        # CG-share: a fraction of high-income units have meaningful capital gains
        cg_share = 0.0
        if income > 250_000:
            cg_share = float(rng.uniform(0.15, 0.55))

        wage = income * (1.0 - cg_share)
        intp = income * cg_share * 0.3   # interest+dividends approximation
        # SEMP for self-employment realism in mid-tier
        semp = wage * 0.05 if income > 75_000 else 0.0

        if pattern == "mfj":
            split = float(rng.uniform(0.55, 0.75))
            primary_inc = wage * split
            spouse_inc = wage * (1.0 - split)
            primary_age = int(rng.integers(28, 70))
            spouse_age = int(rng.integers(28, 70))
            households.append(
                _make_household(serial, NP=2, HINCP=income, **hh_attrs)
            )
            persons.append(_make_person(
                serial, 1, RELSHIPP=20, MAR=1, SEX=1, AGEP=primary_age,
                PINCP=primary_inc, WAGP=primary_inc - semp, SEMP=semp,
                INTP=intp / 2,
                **_benefit_attrs(rng, primary_inc, primary_age),
            ))
            persons.append(_make_person(
                serial, 2, RELSHIPP=21, MAR=1, SEX=2, AGEP=spouse_age,
                PINCP=spouse_inc, WAGP=spouse_inc,
                INTP=intp / 2,
                **_benefit_attrs(rng, spouse_inc, spouse_age),
            ))
        elif pattern == "mfs":
            primary_inc = max(income, 500_000)
            spouse_inc = primary_inc / 100.0
            households.append(
                _make_household(serial, NP=2, HINCP=primary_inc + spouse_inc, **hh_attrs)
            )
            persons.append(_make_person(
                serial, 1, RELSHIPP=20, MAR=1, SEX=1, AGEP=45,
                PINCP=primary_inc, WAGP=primary_inc * (1.0 - cg_share), INTP=primary_inc * cg_share * 0.3,
                **_benefit_attrs(rng, primary_inc, 45),
            ))
            persons.append(_make_person(
                serial, 2, RELSHIPP=21, MAR=1, SEX=2, AGEP=42,
                PINCP=spouse_inc, WAGP=spouse_inc,
                **_benefit_attrs(rng, spouse_inc, 42),
            ))
        elif pattern == "hoh":
            households.append(
                _make_household(serial, NP=2, HINCP=income, **hh_attrs)
            )
            persons.append(_make_person(
                serial, 1, RELSHIPP=20, MAR=5, SEX=2, AGEP=42,
                PINCP=income, WAGP=wage - semp, SEMP=semp, INTP=intp,
                **_benefit_attrs(rng, income, 42),
            ))
            persons.append(_make_person(
                serial, 2, RELSHIPP=25, MAR=5, SEX=1, AGEP=10,
                PINCP=0.0, WAGP=0.0,
                **_benefit_attrs(rng, 0.0, 10),
            ))
        else:  # single
            single_age = int(rng.integers(22, 80))
            households.append(
                _make_household(serial, NP=1, HINCP=income, **hh_attrs)
            )
            persons.append(_make_person(
                serial, 1, RELSHIPP=20, MAR=5, SEX=1, AGEP=single_age,
                PINCP=income, WAGP=wage - semp, SEMP=semp, INTP=intp,
                **_benefit_attrs(rng, income, single_age),
            ))

    person_df = pd.DataFrame(persons)
    hh_df = pd.DataFrame(households)
    return person_df, hh_df


def main() -> int:
    person_df, hh_df = build_fixture()
    person_path = OUT_DIR / "synthetic_pums_persons.parquet"
    hh_path = OUT_DIR / "synthetic_pums_households.parquet"
    person_df.to_parquet(person_path, index=False)
    hh_df.to_parquet(hh_path, index=False)
    print(f"Wrote {len(person_df)} persons → {person_path}")
    print(f"Wrote {len(hh_df)} households → {hh_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
