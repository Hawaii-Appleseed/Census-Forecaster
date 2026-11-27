"""The tax lines of Hawaiʻi's state tax collections, as DOTAX reports them.

One entry per row of the annual report's "Tax Collections" table (Table 1.8
since FY2011; unnumbered before). Every line carries a tier, which says how
far a change to it can be scored:

``structural``
    A model of who pays exists in the repository and scores a change to the
    law line by line (individual income tax on tax units, conveyance tax on
    sales by price band).
``base_rate``
    Collections are a published base times a statutory rate, so a rate
    change scores directly and a base change needs a driver (visitor days,
    gallons, packs). No model of who pays.
``trend``
    No base the state publishes in a usable form, or one that a handful of
    payers dominate. Forecast from history and the economy; a change to the
    law is not scored.

The line set changes over time. A line absent from a year was either not
yet a separate row or did not exist; ``notes`` says which.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Tier = Literal["structural", "base_rate", "trend"]
TIERS: tuple[Tier, ...] = ("structural", "base_rate", "trend")


@dataclass(frozen=True)
class TaxLine:
    key: str
    label: str
    tier: Tier
    #: Row labels the annual reports have used, normalized by
    #: :func:`state_revenue.annual_report.normalize_label`.
    aliases: tuple[str, ...]
    #: Existing code that models the line, if any.
    model: str | None = None
    notes: str = ""
    statute: str = ""


LINES: tuple[TaxLine, ...] = (
    TaxLine(
        "general_excise", "General excise and use", "base_rate",
        ("general excise and use", "general excise and use tax"),
        model="tax_modeler.excise.get_incidence (resident households only)",
        statute="HRS ch. 237, 238",
        notes="Base by activity in DOTAX's General Excise and Use Tax Collections and Base report.",
    ),
    TaxLine(
        "individual_income", "Individual income", "structural",
        ("income individuals",),
        model="tax_modeler (tax-unit microsimulation)",
        statute="HRS ch. 235",
    ),
    TaxLine(
        "transient_accommodations", "Transient accommodations tax", "base_rate",
        ("trans. accom. tax", "transient accommodations tax"),
        statute="HRS ch. 237D",
        notes="Includes the timeshare occupancy tax.",
    ),
    TaxLine(
        "county_surcharge", "County surcharge on GET", "base_rate",
        ("county surcharge", "honolulu county surcharge", "county surcharges"),
        statute="HRS §46-16.8",
        notes="Honolulu from FY2007, Kauaʻi from FY2019, Hawaiʻi County from FY2020, Maui from FY2024. "
              "Rides on the general excise base.",
    ),
    TaxLine(
        "estate", "Inheritance and estate", "trend",
        ("inheritance and estate",),
        statute="HRS ch. 236E",
        notes="A few estates a year decide it (FY2025 $406M against $56M in FY2024).",
    ),
    TaxLine(
        "corporate_income", "Corporate income", "trend",
        ("income corporations",),
        statute="HRS ch. 235",
    ),
    TaxLine(
        "unemployment", "Employment security contributions", "trend",
        ("employment security contributions", "employment security contrib."),
        statute="HRS ch. 383",
        notes="Rates follow the trust fund's balance; goes to the Unemployment Trust Fund, not the general fund.",
    ),
    TaxLine(
        "insurance_premiums", "Insurance premiums", "trend",
        ("insurance premiums", "insurance premiums and fees"),
        statute="HRS §431:7-202",
        notes="Collected by the Insurance Commissioner.",
    ),
    TaxLine(
        "fuel", "Fuel", "base_rate",
        ("fuel", "fuel and environmental"),
        statute="HRS ch. 243",
        notes="Gallons times per-gallon rates; DOTAX publishes gallons by fuel type. "
              "Includes the environmental response tax through FY2018.",
    ),
    TaxLine(
        "public_service_companies", "Public service companies", "trend",
        ("public service companies",),
        statute="HRS ch. 239",
    ),
    TaxLine(
        "motor_vehicle", "Motor vehicle tax and fees", "base_rate",
        ("motor vehicle tax", "motor vehicle tax and fees"),
        statute="HRS ch. 249",
        notes="Collected by the counties. Includes rental vehicles through FY2018.",
    ),
    TaxLine(
        "rental_vehicle", "Rental vehicle tax and fees", "base_rate",
        ("rental vehicle tax and fees", "rental motor vehicle tax and fees", "rental vehicle tax"),
        statute="HRS ch. 251",
        notes="Inside motor vehicle through FY2018.",
    ),
    TaxLine(
        "conveyance", "Conveyance", "structural",
        ("conveyance",),
        model="tax_modeler.conveyance (county sales by price band)",
        statute="HRS ch. 247",
        notes="Collected by the Bureau of Conveyances.",
    ),
    TaxLine(
        "tobacco", "Tobacco and licenses", "base_rate",
        ("tobacco and licenses",),
        statute="HRS ch. 245",
    ),
    TaxLine(
        "liquor", "Liquor and permits", "base_rate",
        ("liquor and permits", "liquor and licenses"),
        statute="HRS ch. 244D",
    ),
    TaxLine(
        "environmental_response", "Environmental response", "base_rate",
        ("environmental response", "environmental response tax", "environmental tax"),
        statute="HRS §243-3.5",
        notes="The barrel tax on petroleum. Inside fuel through FY2018.",
    ),
    TaxLine(
        "banks", "Banks and financial corporations", "trend",
        ("banks financial corps.", "banks financial corporations"),
        statute="HRS ch. 241",
    ),
    TaxLine(
        "transient_accommodations_fees", "Transient accommodations fees", "trend",
        ("trans. accom. fees", "transient accommodations fees"),
    ),
    TaxLine(
        "all_others", "All others", "trend",
        ("all others",),
        notes="Fuel retail dealer permits, fuel penalty and interest, general excise licenses.",
    ),
)

BY_KEY: dict[str, TaxLine] = {line.key: line for line in LINES}


#: Lines that earlier reports leave inside another row: through FY2018 the
#: fuel row includes the environmental response tax (the FY2014 report: fuel
#: $167.5M plus barrel tax $26.0M is the $193.6M row) and motor vehicle includes
#: rental vehicles. A year without the line has its amount in the host's.
HOST: dict[str, str] = {
    "environmental_response": "fuel",
    "rental_vehicle": "motor_vehicle",
}


def line_for_label(normalized: str) -> TaxLine | None:
    """The line a normalized report label belongs to, or None."""
    for line in LINES:
        if normalized in line.aliases:
            return line
    return None
