"""Fetch one bill draft's text through Legislative-Research-Tool's own fetcher and parser.

Run under LRT's interpreter, never this repo's (``fiscal_notes.hub.fetch_draft`` does that):

    PYTHONPATH=<LRT_ROOT> <LRT_ROOT>/.venv/bin/python fiscal_notes/_lrt_fetch.py \
        --year 2026 --bill HB2306 --draft HD1

It prints one JSON object on stdout, ``{"text", "url", "title", "dir_year"}``, or on failure
``{"error": "..."}`` with exit status 1. ``text`` is the translator's input: the three header lines,
a blank line, then the draft's sections joined by blank lines (``drafttext.parse``'s own sections,
nothing reimplemented here).

Only the standard library is imported at module level so this file also imports in the
Census-Forecaster venv, where the URL logic is tested with a fake fetcher. ``testimony`` is imported
inside :func:`lrt_get` and :func:`main`.

URL: built directly from (year, bill, draft). LegiScan's list can lag or lack a draft and its labels
are not unique, so ``drafts.draft_url`` (which rewrites LegiScan's ``state_link``) is not used.
``data.capitol.hawaii.gov``, never ``www`` (which 403s scripts). A bill carried over the biennium
keeps its files in the first (odd) year's directory, so a miss in an even year tries the year before.
The page must name the bill ("HB2306" in its text) or it is refused: a constructed URL that answers
for the wrong file is the failure LRT's docs/traps/amendments.md warns about.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable

HOST = "https://data.capitol.hawaii.gov"
ENCODING = "cp1252"  # the Capitol serves windows-1252; same as testimony.drafttext.ENCODING
INTRODUCED = "Introduced"

_BILL = re.compile(r"^(HB|SB)\d{1,4}$")
_DRAFT = re.compile(r"^(|[HS]D\d|CD\d)$")


class DraftNotFound(RuntimeError):
    """The Capitol has no such file (HTTP 404) in the directory tried."""


class DraftError(RuntimeError):
    """Any other reason the draft's text cannot be produced; the message goes to the hub."""


def draft_label(draft: str) -> str:
    return draft or INTRODUCED


def draft_url(year: int, bill: str, draft: str) -> str:
    """``.../session2026/bills/HB2306_HD1_.HTM``; the introduced draft is ``HB2306_.HTM``."""
    if not _BILL.match(bill):
        raise DraftError(f"not a bill number: {bill!r} (expected HB or SB and 1-4 digits)")
    if not _DRAFT.match(draft):
        raise DraftError(f"not a draft label: {draft!r} (expected '', HD1, SD2, CD1, ...)")
    name = f"{bill}_{draft}_.HTM" if draft else f"{bill}_.HTM"
    return f"{HOST}/sessions/session{year}/bills/{name}"


def candidate_years(year: int) -> list[int]:
    """The directories to try: the session year, then (even years only) the odd year before it."""
    return [year, year - 1] if year % 2 == 0 else [year]


def names_the_bill(page: str, bill: str) -> bool:
    return re.search(rf"\b{bill[:2]}\s*{bill[2:]}\b", page) is not None


def fetch_page(year: int, bill: str, draft: str,
               get: Callable[[str, str], bytes]) -> tuple[str, str, int]:
    """(page, url, directory year). ``get(url, cache_name)`` returns bytes or raises
    :class:`DraftNotFound`; any other exception is not a reason to try another directory."""
    tried = []
    for y in candidate_years(year):
        url = draft_url(y, bill, draft)
        # not LRT's own "draft-<session>-<file>": its session is LegiScan's, not the directory's,
        # so the same name can hold a file from another directory
        cache = f"fiscal-notes-draft-{y}-{url.rsplit('/', 1)[-1]}"
        try:
            raw = get(url, cache)
        except DraftNotFound:
            tried.append(url)
            continue
        page = raw.decode(ENCODING, errors="replace")
        if not names_the_bill(page, bill):
            raise DraftError(f"{url} answered but the page does not name {bill}: refusing it")
        return page, url, y
    raise DraftError(f"{bill} {draft_label(draft)}: not found at " + " or ".join(tried))


def build_text(bill: str, draft: str, year: int, title: str, description: str, sections: list[str]) -> str:
    head = [f"BILL: {bill} {draft_label(draft)} ({year} session)", f"TITLE: {title}",
            f"DESCRIPTION: {description}"]
    return "\n".join(head) + "\n\n" + "\n\n".join(sections)


def fetch_draft(year: int, bill: str, draft: str, get: Callable[[str, str], bytes],
                parse: Callable[[str, str], tuple[str, str, list[str]]]) -> dict:
    """``parse(page, label) -> (title, description, section texts)``."""
    page, url, dir_year = fetch_page(year, bill, draft, get)
    title, description, sections = parse(page, draft_label(draft))
    if not sections:
        raise DraftError(f"{url}: the page parsed to no sections (no 'SECTION n.' openers); "
                         "is it a bill draft?")
    return {"text": build_text(bill, draft, year, title, description, sections), "url": url,
            "title": title, "dir_year": dir_year}


def lrt_get(url: str, cache_name: str) -> bytes:
    from testimony.http import cached_bytes, status_of

    try:
        return cached_bytes(url, cache_name)
    except RuntimeError as e:
        if status_of(e) == 404:
            raise DraftNotFound(url) from e
        raise DraftError(f"could not fetch {url}: {e.__cause__ or e}") from e


def lrt_parse(page: str, label: str) -> tuple[str, str, list[str]]:
    from testimony import drafttext

    d = drafttext.parse(page, label=label)
    return d.title, d.description, [s.text for s in d.sections]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--bill", required=True)
    ap.add_argument("--draft", default="", help="HD1, SD2, CD1; empty for the introduced draft")
    args = ap.parse_args(argv)
    try:
        out = fetch_draft(args.year, args.bill, args.draft, lrt_get, lrt_parse)
    except DraftError as e:
        print(json.dumps({"error": str(e)}))
        return 1
    except ImportError as e:
        print(json.dumps({"error": f"cannot import Legislative-Research-Tool ({e}); "
                                   "set PYTHONPATH to its checkout"}))
        return 1
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
