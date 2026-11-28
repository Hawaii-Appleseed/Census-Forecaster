"""Read the "Tax Collections" table out of a DOTAX annual report.

The annual report (files.hawaii.gov/tax/stats/stats/annual/) carries one
table of every tax's collections for the fiscal year and the one before, in
thousands of dollars. This parses the text ``pdftotext -layout`` makes of the
report; the PDF fetch is in ``state_revenue.scripts.refresh_annual_collections``.

pdfplumber was tried and splits digits inside numbers on the newer reports
("1 0,296"), so the layout text is the input.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from state_revenue.lines import line_for_label


class TableNotFound(ValueError):
    """The text holds no collections table this parser recognises."""


@dataclass(frozen=True)
class AnnualTable:
    """One report's table: ``{fiscal_year: {line_key: thousands}}``."""
    years: tuple[int, int]                 # (current, prior)
    lines: dict[int, dict[str, int]]
    totals: dict[int, int]
    #: Labels found in the table that map to no line (should be empty).
    unmatched: tuple[str, ...]


_NUM = r"(?:\(?\d[\d,]*\)?|-+)"
_PCT = r"(?:\(?-?\d+\.\d+%?\)?|-+)"
_ROW = re.compile(
    r"^\s*(?P<label>[A-Za-z][^$\d]*?)"
    r"(?:\s+\d{1,2}/?(?:,\s*\d{1,2}/?)*)?"            # footnote marks: "3, 4", "1/"
    rf"\s+(?:\$\s*)*(?P<a>{_NUM})\s+(?P<pa>{_PCT})"     # "$      $28,912" (FY2024 report)
    rf"\s+(?:\$\s*)*(?P<b>{_NUM})\s+(?P<pb>{_PCT})\s*$"
)
#: Table headings, by the table's name. The general fund table distributes
#: the same collections: what of each line the general fund kept.
HEADINGS = {
    "collections": re.compile(r"TAX COLLECTIONS(?:\s+\d{1,2})?\s*$"),
    "general_fund": re.compile(r"STATE GENERAL FUND\s*\**(?:\s+\d{1,2})?\s*$"),
}
#: A label wrapped onto two lines leaves a line of words only.
_WORDS_ONLY = re.compile(r"^\s*[A-Za-z][A-Za-z .&’'–—\-]*$")


def normalize_label(label: str) -> str:
    """Lower case, footnote marks, dashes and ampersands out."""
    s = label.replace("*", "").replace("&", "and")
    s = re.sub(r"[–—-]", " ", s)
    s = re.sub(r"\s+\d+(?:,\s*\d+)*\s*$", "", s)     # trailing footnote numbers
    return re.sub(r"\s+", " ", s).strip().lower()


def _amount(tok: str) -> int | None:
    """Thousands; None for a dash (the line did not exist that year)."""
    if set(tok) <= {"-"}:
        return None
    neg = tok.startswith("(")
    v = int(tok.strip("()").replace(",", ""))
    return -v if neg else v


def _table_start(lines: list[str], table: str) -> int:
    heading = HEADINGS[table]
    for i, line in enumerate(lines[:-1]):
        if heading.search(line) and "thousands" in lines[i + 1].lower():
            return i
    raise TableNotFound(f"no {heading.pattern!r} heading followed by '(In Thousands of Dollars)'")


def parse_tax_collections(text: str, table: str = "collections") -> AnnualTable:
    """Parse a table (``collections`` or ``general_fund``) from a report's layout text."""
    lines = text.splitlines()
    start = _table_start(lines, table)
    years = None
    for line in lines[start: start + 8]:
        found = re.findall(r"FY\s*(\d{4})", line)
        if len(found) >= 2:
            years = (int(found[0]), int(found[1]))
            break
    if years is None:
        raise TableNotFound("no 'FY yyyy  FY yyyy' header under the heading")

    cur, prior = years
    out: dict[int, dict[str, int]] = {cur: {}, prior: {}}
    totals: dict[int, int] = {}
    unmatched: list[str] = []
    body = start + 2
    for i in range(start, min(start + 12, len(lines))):
        if "SOURCE OF REVENUE" in lines[i].upper():
            body = i + 1
            break
    pending = ""
    for line in lines[body: body + 70]:
        m = _ROW.match(line)
        if not m:
            pending = line.strip() if _WORDS_ONLY.match(line) else ""
            continue
        label = normalize_label(f"{pending} {m['label']}")
        pending = ""
        a, b = _amount(m["a"]), _amount(m["b"])
        if label.startswith("total"):
            totals = {cur: a or 0, prior: b or 0}
            break
        tax_line = line_for_label(label)
        if tax_line is None:
            unmatched.append(label)
            continue
        for fy, v in ((cur, a), (prior, b)):
            if v is not None:
                out[fy][tax_line.key] = out[fy].get(tax_line.key, 0) + v
    if not totals:
        raise TableNotFound(f"no TOTAL row in the FY{cur} table")
    return AnnualTable(years=years, lines=out, totals=totals, unmatched=tuple(unmatched))


def residuals(table: AnnualTable) -> dict[int, int]:
    """Total minus the sum of the lines, per year (rounding leaves a few thousand)."""
    return {fy: table.totals[fy] - sum(table.lines[fy].values()) for fy in table.years}


# The general excise tax by business activity (Table 1.1 since FY2011, unnumbered
# before): the base and the tax for each activity, two fiscal years.

#: Report label (normalized) -> activity key. Sugar processing and pineapple
#: canning (0.5%, gone after FY2005) count as manufacturing.
GET_ACTIVITIES = {
    "retailing": "retailing",
    "services": "services",
    "contracting": "contracting",
    "hotel rentals": "transient_accommodations",
    "transient accommodations rentals": "transient_accommodations",
    "trans. accom. rentals": "transient_accommodations",
    "transient accomm. rentals": "transient_accommodations",
    "all other rentals": "other_rentals",
    "all others (4%)": "other_4pct",
    "all others": "other_4pct",
    "producing": "producing",
    "manufacturing": "manufacturing",
    "manfacturing": "manufacturing",           # FY2014-FY2018 reports
    "sugar processing": "manufacturing",
    "pineapple canning": "manufacturing",
    "wholesaling": "wholesaling",
    "use (1/2%)": "use",
    "use": "use",                              # FY2010 report's tax rows
    "intermediary services": "wholesale_services",
    "wholesale services": "wholesale_services",
    "services (wholesale)": "wholesale_services",
    "services (intermediary)": "wholesale_services",
    "insurance commissions": "insurance_commissions",
    "insurance solicitors": "insurance_commissions",
    "unallocated": "unallocated",
}
_GET_HEADING = re.compile(r"GENERAL EXCISE AND USE TAX BASE AND TAXES?(?:\s+FOR FISCAL)?\s*$")
_GET_NUM = r"\(?\$?\s*\(?-?[\d,]+\)?"
_GET_ROW = re.compile(
    rf"^\s*(?P<label>[A-Za-z][A-Za-z .&/’'–—\-]*?(?:\s*\([^)]*\))?)\s*(?:\d{{1,2}}/?|\*)?\s+"
    rf"(?:(?P<rate>\d+(?:\.\d+)?%)\s+)?(?P<a>{_GET_NUM})\s+(?P<b>{_GET_NUM})(?:\s|$)")


@dataclass(frozen=True)
class GetActivityTable:
    years: tuple[int, int]
    base: dict[int, dict[str, int]]        # thousands of dollars
    tax: dict[int, dict[str, int]]
    rates: dict[str, float]                # activity -> rate printed beside its tax
    tax_totals: dict[int, int]
    base_totals: dict[int, int]
    unmatched: tuple[str, ...]


def _get_amount(tok: str) -> int:
    neg = "(" in tok or "-" in tok
    v = int(re.sub(r"[^\d]", "", tok))
    return -v if neg else v


def parse_get_activity(text: str) -> GetActivityTable:
    """Parse the general excise base and tax by activity from a report's layout text."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines)
                  if _GET_HEADING.search(line) and "...." not in line), None)
    if start is None:
        raise TableNotFound("no 'GENERAL EXCISE AND USE TAX BASE AND TAXES' heading")
    years = None
    for line in lines[start: start + 10]:
        found = re.findall(r"FY\s*(\d{4})", line)
        if len(found) >= 2:
            years = (int(found[0]), int(found[1]))
            break
    if years is None:
        raise TableNotFound("no 'FY yyyy  FY yyyy' header under the heading")
    cur, prior = years
    base: dict[int, dict[str, int]] = {cur: {}, prior: {}}
    tax: dict[int, dict[str, int]] = {cur: {}, prior: {}}
    rates: dict[str, float] = {}
    totals = {"base": {}, "tax": {}}
    unmatched: list[str] = []
    section = None
    for line in lines[start + 3: start + 70]:
        head = line.strip().upper()
        if head == "TAX BASE":
            section = "base"
            continue
        if head == "TAX":
            section = "tax"
            continue
        if section is None:
            continue
        m = _GET_ROW.match(line)
        if not m:
            continue
        label = re.sub(r"\s+", " ", re.sub(r"[–—]", "-", m["label"])).strip().lower()
        a, b = _get_amount(m["a"]), _get_amount(m["b"])
        if label.startswith("total"):
            totals[section] = {cur: a, prior: b}
            if section == "tax":
                break
            continue
        if label.startswith("subtotal"):
            continue
        key = GET_ACTIVITIES.get(label)
        if key is None:
            unmatched.append(label)
            continue
        target = base if section == "base" else tax
        for fy, v in ((cur, a), (prior, b)):
            target[fy][key] = target[fy].get(key, 0) + v
        if section == "tax" and m["rate"]:
            rates[key] = float(m["rate"].rstrip("%")) / 100
    if not totals["tax"] or not totals["base"]:
        raise TableNotFound(f"no TOTAL rows in the FY{cur} general excise table")
    return GetActivityTable(years, base, tax, rates, totals["tax"], totals["base"], tuple(unmatched))


# County fuel taxes (the "Allocation of Fuel Taxes" table) and how the
# transient accommodations tax is distributed (its own table): what the
# collections table does not split.

def _two_years(lines: list[str], start: int, span: int = 12) -> tuple[int, int] | None:
    for line in lines[start: start + span]:
        found = re.findall(r"FY\s*(\d{4})", line)
        if len(found) >= 2:
            return int(found[0]), int(found[1])
    return None


def parse_county_fuel(text: str) -> dict[int, int]:
    """County fuel taxes by fiscal year, thousands: the COUNTY HIGHWAY FUNDS
    total of the allocation table. The collections table's fuel row is state
    plus county. Empty when the report's table is not found (FY2024's heading
    wraps); the next report carries the year."""
    lines = text.splitlines()
    heads = [i for i, line in enumerate(lines)
             if "ALLOCATION OF FUEL TAX" in line and "...." not in line]
    if not heads:
        return {}
    start = heads[-1]
    years = _two_years(lines, start)
    county = next((j for j in range(start, min(start + 80, len(lines)))
                   if "COUNTY HIGHWAY FUND" in lines[j].upper()), None)
    if years is None or county is None:
        return {}
    for line in lines[county + 1: county + 12]:
        m = re.match(r"^\s*(?:Sub-?total|TOTAL)\s+\$?\s*([\d,]+)\s+\$?\s*([\d,]+)", line, re.I)
        if m:
            return {years[0]: int(m[1].replace(",", "")), years[1]: int(m[2].replace(",", ""))}
    return {}


_TAT_HEADING = re.compile(r"TRANSIENT ACCOMMODATIONS TAX(?:\s+\d)?\s*$")
_TAT_ROW = re.compile(r"^\s*([A-Za-z][A-Za-z .&’'/]*?)\s+\$?\s*([\d,]+|-)\s+\$?\s*([\d,]+|-)(?:\s|$)")


def parse_tat_distribution(text: str) -> dict[int, dict[str, int]]:
    """Where the transient accommodations tax went, by fund, thousands
    (``{fiscal_year: {fund label: amount}}``; HRS §237D-6.5). Empty if not found."""
    lines = text.splitlines()
    heads = [i for i, line in enumerate(lines)
             if _TAT_HEADING.search(line) and "...." not in line
             and i + 1 < len(lines) and "thousands" in lines[i + 1].lower()]
    if not heads:
        return {}
    start = heads[-1]
    years = _two_years(lines, start)
    if years is None:
        return {}
    out: dict[int, dict[str, int]] = {years[0]: {}, years[1]: {}}
    inside = False
    for line in lines[start: start + 40]:
        if line.strip().lower().startswith("distribution"):
            inside = True
            continue
        if not inside:
            continue
        m = _TAT_ROW.match(line)
        if not m:
            continue
        label = re.sub(r"\s+", " ", m[1]).strip()
        if label.lower() == "total":
            break
        for fy, tok in zip(years, (m[2], m[3]), strict=True):
            out[fy][label] = 0 if tok == "-" else int(tok.replace(",", ""))
    return out if out[years[0]] else {}
