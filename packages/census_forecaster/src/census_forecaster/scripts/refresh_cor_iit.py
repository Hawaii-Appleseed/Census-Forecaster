"""Fetch the Council on Revenues' individual income tax (IIT) projections.

Source: the Hawaiʻi Council on Revenues meeting page

    https://tax.hawaii.gov/useful/a9_1cor/

Each General Fund meeting posts an "Attachment 1" PDF titled *Estimates of
General Fund Tax Revenue from the Meeting of <date>*, produced by DOTAX's Tax
Research & Planning Office. It carries one row per tax type across FY columns;
the row this script wants is **Individual Income Tax**.

Why this exists
---------------
``tax_modeler.scenarios.quintile_analysis`` scales microsim impact estimates to
the official State baseline via ``cor_scale_factor_for_year`` — the ratio of
COR's IIT projection to the microsim's own Act 46 baseline. That constant used
to be a hand-transcribed dict, which silently went stale: it sat on the March
10, 2026 vintage while COR had since met on May 21, 2026.

COR meets ~4-5 times a year on no fixed cadence, so "check whether a newer
vintage exists" is exactly the kind of thing that should not depend on someone
remembering. This script re-derives the dict from whatever the newest posted
General Fund attachment says.

Scope note
----------
The COR letter itself only forecasts *total* General Fund revenue. IIT is a
DOTAX line-item reconciliation to that total, which is why the number lives in
the attachment rather than the letter. If the attachment layout changes, this
script fails loudly rather than writing a partial dict — a wrong scale factor is
worse than a stale one.

FY→TY convention
----------------
``FY(n+1) = TY(n)``, per the DOTAX fiscal-note convention already used
throughout this repo. The emitted ``projections_by_tax_year`` applies that
shift; ``projections_by_fiscal_year`` keeps the raw source values so the
mapping stays auditable.

Run it (needs pdfplumber — ``pip install census-forecaster[cor]``)::

    python -m census_forecaster.scripts.refresh_cor_iit
    python -m census_forecaster.scripts.refresh_cor_iit --dry-run

Output
------
    src/census_forecaster/data/cor/cor_iit_projections.json
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

COR_URL = "https://tax.hawaii.gov/useful/a9_1cor/"
USER_AGENT = "census-forecaster/1.0 (+https://github.com/Hawaii-Appleseed/Census-Forecaster)"

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "cor"
OUT_PATH = DATA_DIR / "cor_iit_projections.json"

# The attachment filenames follow 2026gf05-21_attach_1.pdf — a 4-digit year,
# "gf", MM-DD, then the attachment index. TPI (total personal income) meetings
# use "tpi" instead of "gf" and carry no General Fund line items, so the
# pattern deliberately matches "gf" only.
ATTACH_RE = re.compile(
    r'href=["\']([^"\']*?(\d{4})gf(\d{2})-(\d{2})_attach_1\.pdf)["\']',
    re.IGNORECASE,
)

# Row label as it appears in the attachment table.
IIT_ROW_RE = re.compile(r"^\s*Individual\s+Income\s+Tax\s+(.*)$", re.MULTILINE)
# The header row that tells us which FY each column is.
FY_HEADER_RE = re.compile(r"TYPE\s+OF\s+TAX\s+(.*)$", re.MULTILINE | re.IGNORECASE)
FY_RE = re.compile(r"FY\s*(\d{4})")
# Numbers look like 3,139,079 or $3,139,079 (thousands of dollars).
NUM_RE = re.compile(r"\$?\(?-?[\d,]+\)?")


class RefreshError(RuntimeError):
    """Raised when the source cannot be parsed into a trustworthy dict."""


def fetch(url: str, *, binary: bool = False):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read()
    return raw if binary else raw.decode("utf-8", errors="replace")


def find_latest_attachment(page_html: str) -> tuple[str, date]:
    """Return (absolute_url, meeting_date) for the newest GF Attachment 1."""
    found: list[tuple[date, str]] = []
    for href, yyyy, mm, dd in ATTACH_RE.findall(page_html):
        try:
            meeting = date(int(yyyy), int(mm), int(dd))
        except ValueError:
            continue
        url = urllib.parse.urljoin(COR_URL, href) if "://" not in href else href
        found.append((meeting, url))

    if not found:
        raise RefreshError(
            f"no '<yyyy>gf<mm>-<dd>_attach_1.pdf' links found on {COR_URL} — "
            "the page layout probably changed; update ATTACH_RE."
        )
    meeting, url = max(found, key=lambda t: t[0])
    return url, meeting


def parse_iit_row(text: str) -> dict[int, float]:
    """Parse {fiscal_year: iit_$M} out of the attachment's extracted text."""
    header = FY_HEADER_RE.search(text)
    if not header:
        raise RefreshError(
            "could not find the 'TYPE OF TAX ... FY ####' header row in the "
            "attachment; layout changed."
        )
    fiscal_years = [int(y) for y in FY_RE.findall(header.group(1))]
    if not fiscal_years:
        raise RefreshError("header row found but no 'FY ####' columns parsed.")

    row = IIT_ROW_RE.search(text)
    if not row:
        raise RefreshError(
            "could not find the 'Individual Income Tax' row in the attachment."
        )

    values: list[float] = []
    for tok in NUM_RE.findall(row.group(1)):
        cleaned = tok.replace("$", "").replace(",", "").strip()
        neg = cleaned.startswith("(") and cleaned.endswith(")")
        cleaned = cleaned.strip("()")
        if not cleaned or cleaned == "-":
            continue
        try:
            v = float(cleaned)
        except ValueError:
            continue
        values.append(-v if neg else v)

    if len(values) != len(fiscal_years):
        raise RefreshError(
            f"column mismatch: header has {len(fiscal_years)} fiscal years "
            f"{fiscal_years} but the IIT row parsed {len(values)} numbers "
            f"{values}. Refusing to guess the alignment."
        )

    # Source is thousands of dollars; the model works in $M.
    return {fy: round(v / 1_000.0, 3) for fy, v in zip(fiscal_years, values, strict=False)}


def extract_pdf_text(pdf_bytes: bytes) -> str:
    try:
        import pdfplumber
    except ImportError:  # pragma: no cover - environment-dependent
        print(
            "ERROR: pdfplumber is required to parse the COR attachment.\n"
            "       pip install 'census-forecaster[cor]'",
            file=sys.stderr,
        )
        raise SystemExit(2) from None

    chunks = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            chunks.append(page.extract_text() or "")
    return "\n".join(chunks)


def build_payload(url: str, meeting: date, by_fy: dict[int, float]) -> dict:
    # FY(n+1) = TY(n): shift back one year.
    by_ty = {fy - 1: v for fy, v in by_fy.items()}
    return {
        "source_url": url,
        "source_page": COR_URL,
        "meeting_date": meeting.isoformat(),
        "retrieved": date.today().isoformat(),
        "units": "$M",
        "fy_to_ty_convention": "FY(n+1) = TY(n)",
        "note": (
            "Individual Income Tax line-item projections from the DOTAX Tax "
            "Research & Planning Office attachment to the COR General Fund "
            "forecast. Regenerate with "
            "`python -m census_forecaster.scripts.refresh_cor_iit`."
        ),
        "projections_by_fiscal_year": {str(k): v for k, v in sorted(by_fy.items())},
        "projections_by_tax_year": {str(k): v for k, v in sorted(by_ty.items())},
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--dry-run", action="store_true",
        help="parse and print, but do not write the JSON",
    )
    ap.add_argument(
        "--url", default=None,
        help="parse this attachment URL instead of discovering the newest",
    )
    args = ap.parse_args(argv)

    try:
        if args.url:
            url = args.url
            m = re.search(r"(\d{4})gf(\d{2})-(\d{2})_attach", url)
            meeting = (
                date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                if m else date.today()
            )
        else:
            print(f"[cor] discovering newest GF attachment from {COR_URL}", flush=True)
            url, meeting = find_latest_attachment(fetch(COR_URL))

        print(f"[cor] meeting {meeting.isoformat()} -> {url}", flush=True)
        text = extract_pdf_text(fetch(url, binary=True))
        by_fy = parse_iit_row(text)
    except (urllib.error.URLError, urllib.error.HTTPError) as exc:
        print(f"::warning::COR fetch failed ({exc}); keeping committed data", file=sys.stderr)
        return 0
    except RefreshError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    payload = build_payload(url, meeting, by_fy)

    print(f"[cor] parsed {len(by_fy)} fiscal years:", flush=True)
    for fy, v in sorted(by_fy.items()):
        print(f"       FY{fy} (TY{fy - 1}): ${v:,.3f}M", flush=True)

    if args.dry_run:
        print("[cor] --dry-run: not writing", flush=True)
        return 0

    prior = None
    if OUT_PATH.exists():
        try:
            prior = json.loads(OUT_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            prior = None

    # Idempotency: `retrieved` is a wall-clock stamp, so comparing full payloads
    # would emit a diff on every run and defeat the refresh-data workflow's
    # "running it twice produces no diff" invariant. Compare only the substantive
    # fields, and carry the prior stamp forward when nothing real changed.
    def _substance(d: dict) -> dict:
        return {k: v for k, v in d.items() if k != "retrieved"}

    if prior is not None and _substance(prior) == _substance(payload):
        print(
            f"[cor] unchanged (vintage {payload['meeting_date']}); leaving file as-is",
            flush=True,
        )
        return 0

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")

    prior_meeting = (prior or {}).get("meeting_date")
    if prior_meeting and prior_meeting != payload["meeting_date"]:
        print(
            f"[cor] NEW VINTAGE: {prior_meeting} -> {payload['meeting_date']}",
            flush=True,
        )
    print(f"[cor] wrote {OUT_PATH}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
