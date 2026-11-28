"""Fetch every Council on Revenues line-item forecast into one file.

Reads the COR page and its archive for the general fund meetings'
"Attachment 1" PDFs (``YYYYgfMM-DD_attach_1.pdf``, 2014 on), parses each
with :func:`state_revenue.cor.parse_attachment` and writes
``data/cor_line_forecasts.json``. Every attachment's rows must add to its
total (within 5 thousand) or nothing is written.

    python -m state_revenue.scripts.refresh_cor_forecasts --cache /tmp/cor
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from datetime import date
from pathlib import Path

from state_revenue.cor import DATA_PATH, meeting_from_filename, parse_attachment
from state_revenue.scripts.refresh_annual_collections import _fetch, pdf_text

PAGES = ("https://tax.hawaii.gov/useful/a9_1cor/", "https://tax.hawaii.gov/useful/a9_1corarchive/")
LINK = re.compile(r"https://files\.hawaii\.gov/tax/useful/cor/(?:archive/)?\d{4}gf\d{2}-\d{2}_attach_1\.pdf")
TOLERANCE = 5


def attachment_urls(pages_html: list[str]) -> dict[str, str]:
    """``{file name: url}``; a meeting on both pages keeps the current page's link."""
    out: dict[str, str] = {}
    for html in reversed(pages_html):            # archive first, current page wins
        for url in LINK.findall(html):
            out[url.rsplit("/", 1)[-1]] = url
    return dict(sorted(out.items()))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cache", type=Path, help="keep downloaded PDFs here (reused if present)")
    ap.add_argument("--out", type=Path, default=DATA_PATH)
    ap.add_argument("--if-new", action="store_true",
                    help="do nothing unless a meeting is posted that the bundled file lacks")
    args = ap.parse_args(argv)

    urls = attachment_urls([_fetch(p).decode("utf-8", "replace") for p in PAGES])
    if not urls:
        print("ERROR: no general fund attachments found", file=sys.stderr)
        return 1
    if args.if_new and args.out.exists():
        have = {m["file"] for m in json.loads(args.out.read_text())["meetings"]}
        if set(urls) <= have:
            print("[cor] no new meeting; bundled file kept", file=sys.stderr)
            return 0
    cache = args.cache or Path(tempfile.mkdtemp(prefix="cor_"))
    cache.mkdir(parents=True, exist_ok=True)

    meetings = []
    for name, url in urls.items():
        pdf = cache / name
        if not pdf.exists():
            pdf.write_bytes(_fetch(url))
        f = parse_attachment(pdf_text(pdf), meeting_from_filename(name))
        for fy, row in {**f.base, **f.estimates}.items():
            gap = row["total"] - sum(v for k, v in row.items() if k != "total")
            if abs(gap) > TOLERANCE:
                print(f"ERROR: {name} FY{fy}: rows miss the total by {gap}", file=sys.stderr)
                return 1
        meetings.append({
            "meeting": f.meeting.isoformat(),
            "file": name,
            "url": url,
            "sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
            "base": {str(fy): v for fy, v in f.base.items()},
            "estimates": {str(fy): v for fy, v in f.estimates.items()},
            "preliminary": list(f.preliminary),
        })
        print(f"[cor] {name}: base {list(f.base)}, FY{min(f.estimates)}-FY{max(f.estimates)}",
              file=sys.stderr)

    payload = {
        "source": "Hawaii Council on Revenues, general fund meetings, Attachment 1 'Estimates of "
                  "General Fund Tax Revenue' (line items by DOTAX's Tax Research and Planning "
                  "Office, consistent with the Council's total)",
        "fetch_date": date.today().isoformat(),
        "units": "thousands of dollars, general fund, fiscal year ending June 30",
        "meetings": sorted(meetings, key=lambda m: m["meeting"]),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=1) + "\n")
    print(f"[cor] {len(meetings)} meetings -> {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
