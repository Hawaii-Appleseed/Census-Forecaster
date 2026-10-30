"""Populate ``data/tax_modeler/raw/`` with Hawaii DOTAX SOI tables.

The Hawaii Department of Taxation publishes annual Statistics-of-Income
tables (A2, A4, A8, A9, A12, A13, A17, A21, plus the "Selected Resident
Return Data" sub-tables) used by tax_modeler's calibration step. The
tables are published at https://tax.hawaii.gov/stats/ but DOTAX doesn't
expose a stable per-file URL pattern — links are buried in WordPress
pages with year-specific PDF + XLSX uploads, and historically the
ctc-and-eitc tree downloaded them manually and stored CSVs in
``data/raw/``.

This script supports two modes:

1. **Local copy (default)**: ``--copy-from PATH`` copies the SOI CSVs
   from a local source directory (typically a clone of the original
   ctc-and-eitc repo, e.g.
   ``~/ctc-and-eitc/data/raw/``). This is the recommended path: you
   download the tables once into the legacy tree (or by hand), then
   run this script to mirror them into the monorepo's gitignored
   ``data/tax_modeler/raw/``.

2. **Manual mode**: with neither flag, the script prints the manual
   download instructions and exits.

A future enhancement could add live scraping of tax.hawaii.gov, but
DOTAX's HTML layout changes between SOI publications, so a stable
scraper requires tracking each year's page individually.

Usage
-----

    # Mirror DOTAX SOI from a local ctc-and-eitc checkout:
    uv run python -m tax_modeler.scripts.fetch_dotax_soi \\
        --copy-from ~/ctc-and-eitc/data/raw

    # Show manual-download instructions:
    uv run python -m tax_modeler.scripts.fetch_dotax_soi
"""
from __future__ import annotations

import argparse
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path

# Files we expect to populate ``data/tax_modeler/raw/`` with. Each entry
# is a glob pattern that matches both the top-level "Dotax Soi 2022 - X"
# files and the "Selected Resident Return Data/Dotax Soi 2022 - AY" sub-
# files. Patterns are matched case-insensitively against filenames.
_EXPECTED_PATTERNS: tuple[str, ...] = (
    "*Dotax Soi*.csv",  # All annual SOI tables (top level + subdir)
)


def _default_out_dir() -> Path:
    """Resolve to monorepo-root ``data/tax_modeler/raw/``."""
    return Path(__file__).resolve().parents[5] / "data" / "tax_modeler" / "raw"


def _copy_with_layout(source: Path, dest: Path) -> tuple[int, int]:
    """Copy DOTAX SOI files from ``source`` to ``dest`` preserving layout.

    Walks ``source`` recursively, mirrors any file whose name contains
    "Dotax Soi" (case-insensitive) into the equivalent relative path
    under ``dest``. Returns ``(copied, skipped)`` counts; ``skipped``
    counts files that already existed at the destination with the same
    size (idempotent re-runs).
    """
    if not source.exists():
        raise FileNotFoundError(
            f"--copy-from path does not exist: {source}"
        )
    if not source.is_dir():
        raise NotADirectoryError(
            f"--copy-from path is not a directory: {source}"
        )

    copied = 0
    skipped = 0
    for src in source.rglob("*"):
        if not src.is_file():
            continue
        name_l = src.name.lower()
        if "dotax soi" not in name_l:
            continue
        rel = src.relative_to(source)
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.exists() and out.stat().st_size == src.stat().st_size:
            skipped += 1
            continue
        shutil.copy2(src, out)
        copied += 1
    return copied, skipped


def _print_manual_instructions(out_dir: Path) -> None:
    msg = (
        "tax_modeler.scripts.fetch_dotax_soi requires either:\n"
        "\n"
        "  (a) A --copy-from PATH pointing at an existing checkout that\n"
        "      already has the SOI CSVs (typically\n"
        "      ~/ctc-and-eitc/data/raw/), e.g.\n"
        "\n"
        "        uv run python -m tax_modeler.scripts.fetch_dotax_soi \\\n"
        "            --copy-from ~/ctc-and-eitc/data/raw\n"
        "\n"
        "  (b) A manual download:\n"
        "      1. Visit https://tax.hawaii.gov/stats/\n"
        "      2. Download the latest 'Hawaii Individual Income\n"
        "         Statistics' XLSX files (look for tables A2, A4, A8,\n"
        "         A9, A12, A13, A17, A21).\n"
        "      3. Convert each sheet to CSV and save under\n"
        f"         {out_dir}\n"
        "         using the existing naming convention\n"
        "         'Dotax Soi YYYY - <table>.csv' (e.g.\n"
        "         'Dotax Soi 2022 - A2-1.csv').\n"
        "\n"
        "Why no live scraper? DOTAX uploads each year's tables as\n"
        "manually-curated WordPress pages with no stable URL pattern,\n"
        "and the layout changes between SOI publications. A robust\n"
        "scraper would need year-by-year HTML rules; mirroring from a\n"
        "local checkout is simpler and idempotent.\n"
    )
    print(msg, file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Populate data/tax_modeler/raw/ with DOTAX SOI tables.",
    )
    parser.add_argument(
        "--out-dir", type=Path, default=_default_out_dir(),
        help="Destination directory (default: data/tax_modeler/raw/ at repo root).",
    )
    parser.add_argument(
        "--copy-from", type=Path, default=None,
        help="Source directory to mirror DOTAX SOI files from "
             "(e.g. ~/ctc-and-eitc/data/raw/). The script walks the "
             "source recursively and copies any file whose name contains "
             "'Dotax Soi'.",
    )
    args = parser.parse_args(argv)

    args.out_dir.mkdir(parents=True, exist_ok=True)

    if args.copy_from is None:
        _print_manual_instructions(args.out_dir)
        return 1

    src = args.copy_from.expanduser().resolve()
    print(f"[fetch_dotax_soi] Mirroring from {src}", file=sys.stderr)
    print(f"[fetch_dotax_soi]              -> {args.out_dir}", file=sys.stderr)
    copied, skipped = _copy_with_layout(src, args.out_dir)
    print(
        f"[fetch_dotax_soi] Done: {copied} copied, {skipped} unchanged.",
        file=sys.stderr,
    )

    # Quick sanity check
    files = list(args.out_dir.rglob("*Dotax Soi*.csv"))
    if not files:
        print(
            "[fetch_dotax_soi] WARNING: no 'Dotax Soi*.csv' files in destination.",
            file=sys.stderr,
        )
        return 2
    print(
        f"[fetch_dotax_soi] {len(files)} DOTAX SOI files in {args.out_dir}.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
