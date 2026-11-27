"""The Council on Revenues' general fund forecasts, line by line.

COR forecasts only the general fund's total. With each forecast DOTAX's Tax
Research and Planning Office posts "Attachment 1", line-item projections
consistent with that total: one row per tax, two BASE fiscal years (actual,
the second sometimes preliminary) and seven ESTIMATED ones. Those rows are
the official baseline a fiscal note scores against, and the benchmark the
ledger's own forecasts are backtested on.

``parse_attachment`` reads the text ``pdftotext -layout`` makes of one
attachment; ``state_revenue.scripts.refresh_cor_forecasts`` fetches every
posted one (2014 on) into ``data/cor_line_forecasts.json``.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parent / "data" / "cor_line_forecasts.json"

#: COR row label (lower case, no footnote mark) -> key. Keys are the ledger's
#: (``state_revenue.lines``) except ``miscellaneous`` (COR's catch-all:
#: penalties, fees, the environmental response tax's general fund share) and
#: ``total``.
COR_ROWS = {
    "general excise and use tax": "general_excise",
    "individual income tax": "individual_income",
    "transient accommodations tax": "transient_accommodations",
    "corporate income tax": "corporate_income",
    "tax on insurance premiums": "insurance_premiums",
    "public service company tax": "public_service_companies",
    "cigarette and tobacco tax": "tobacco",
    "liquor tax": "liquor",
    "tax on banks and other financial corps.": "banks",
    "inheritance and estate tax": "estate",
    "conveyance tax": "conveyance",
    "miscellaneous taxes": "miscellaneous",
    "general fund total": "total",
}

_NUM = r"\$?\s*\(?-?[\d,]+\)?"
_ROW = re.compile(rf"^\s*(?P<label>[A-Za-z][A-Za-z .&]*?)\**\s+(?P<nums>(?:{_NUM}\s+)*{_NUM})\s*$")
_FNAME = re.compile(r"(\d{4})gf(\d{2})-(\d{2})_attach_1")


class AttachmentError(ValueError):
    """The text is not an attachment this parser recognises."""


@dataclass(frozen=True)
class CorForecast:
    meeting: date
    #: ``{fiscal_year: {key: thousands}}`` for the BASE (actual) columns.
    base: dict[int, dict[str, int]]
    #: ``{fiscal_year: {key: thousands}}`` for the ESTIMATED columns.
    estimates: dict[int, dict[str, int]]
    #: BASE years DOTAX marked preliminary (``FY 2026*``).
    preliminary: tuple[int, ...] = ()

    def horizon(self, fy: int) -> int:
        """Fiscal years from the last complete one at the meeting: the fiscal
        year in progress is 1 (an August or September meeting has its first
        two months' collections), the next 2."""
        last_complete = self.meeting.year if self.meeting.month >= 7 else self.meeting.year - 1
        return fy - last_complete


def _num(tok: str) -> int:
    t = tok.replace("$", "").replace(",", "").strip()
    neg = t.startswith("(") or t.startswith("-")
    v = int(t.strip("()-"))
    return -v if neg else v


def parse_attachment(text: str, meeting: date) -> CorForecast:
    lines = text.splitlines()
    header_i = next((i for i, line in enumerate(lines) if line.strip().upper().startswith("TYPE OF TAX")),
                    None)
    if header_i is None:
        raise AttachmentError("no 'TYPE OF TAX' header")
    header = lines[header_i]
    years = [int(y) for y in re.findall(r"FY\s*(\d{4})", header)]
    preliminary = tuple(int(y) for y in re.findall(r"FY\s*(\d{4})\*", header))
    above = " ".join(lines[max(0, header_i - 3): header_i]).upper()
    if len(years) < 4 or "BASE" not in above or "ESTIMATED" not in above:
        raise AttachmentError(f"unexpected header: {header.strip()!r}")
    n_base = 2
    rows: dict[str, list[int]] = {}
    for line in lines[header_i + 1:]:
        m = _ROW.match(line)
        if not m:
            continue
        label = re.sub(r"\s+", " ", m["label"]).strip().lower()
        key = COR_ROWS.get(label)
        if key is None:
            continue
        nums = re.findall(_NUM, m["nums"])
        if len(nums) != len(years):
            raise AttachmentError(f"{label!r}: {len(nums)} figures for {len(years)} years")
        rows[key] = [_num(t) for t in nums]
        if key == "total":
            break
    missing = set(COR_ROWS.values()) - set(rows)
    if missing:
        raise AttachmentError(f"rows not found: {sorted(missing)}")
    table = {fy: {k: v[i] for k, v in rows.items()} for i, fy in enumerate(years)}
    return CorForecast(
        meeting=meeting,
        base={fy: table[fy] for fy in years[:n_base]},
        estimates={fy: table[fy] for fy in years[n_base:]},
        preliminary=preliminary,
    )


def meeting_from_filename(name: str) -> date:
    m = _FNAME.search(name)
    if not m:
        raise AttachmentError(f"not a general fund attachment name: {name}")
    return date(int(m[1]), int(m[2]), int(m[3]))


@lru_cache(maxsize=1)
def load_cor_forecasts() -> tuple[CorForecast, ...]:
    """Every bundled COR line-item forecast, oldest meeting first."""
    payload = json.loads(DATA_PATH.read_text())
    out = []
    for m in payload["meetings"]:
        out.append(CorForecast(
            meeting=date.fromisoformat(m["meeting"]),
            base={int(fy): v for fy, v in m["base"].items()},
            estimates={int(fy): v for fy, v in m["estimates"].items()},
            preliminary=tuple(m.get("preliminary", ())),
        ))
    return tuple(sorted(out, key=lambda f: f.meeting))
