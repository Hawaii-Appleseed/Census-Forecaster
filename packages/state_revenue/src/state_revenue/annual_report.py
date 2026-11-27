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
