"""Populate ``data/tax_modeler/external/`` with IRS SOI + BLS OES + CEX data.

The IRS Statistics-of-Income (SOI) Division publishes per-state and
national income / deduction distributions used by tax_modeler's
high-income calibration and ultra-high-income synthesis modules. These
are XLS/XLSX files at https://www.irs.gov/statistics/soi-tax-stats-individual-tax-statistics
plus auxiliary BLS OES wage data and Bureau of Labor Statistics
Consumer Expenditure Survey (CEX) microdata.

Like the DOTAX fetcher (``fetch_dotax_soi.py``), there's no stable URL
pattern for these tables — they're posted on IRS / BLS WordPress-style
pages with year-specific filenames. The pragmatic solution is to mirror
from a local source directory (typically a clone of the original
ctc-and-eitc repo).

This script supports two modes:

1. **Local copy (default)**: ``--copy-from PATH`` mirrors the entire
   ``external/`` tree from the source. Most users will want this.

2. **Manual mode**: with no flag, prints download instructions and
   exits with status 1.

Usage
-----

    # Mirror IRS / BLS / CEX tables from a local ctc-and-eitc checkout:
    uv run python -m tax_modeler.scripts.fetch_irs_soi \\
        --copy-from ~/ctc-and-eitc/data/external

    # Show manual-download instructions:
    uv run python -m tax_modeler.scripts.fetch_irs_soi
"""
from __future__ import annotations

import argparse
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path


def _default_out_dir() -> Path:
    """Resolve to monorepo-root ``data/tax_modeler/external/``."""
    return Path(__file__).resolve().parents[5] / "data" / "tax_modeler" / "external"


def _copy_tree_idempotent(source: Path, dest: Path) -> tuple[int, int]:
    """Mirror ``source`` recursively into ``dest``.

    Returns ``(copied, skipped)`` counts. ``skipped`` counts files that
    already exist at the destination with the same size (idempotent
    re-runs).
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
        "tax_modeler.scripts.fetch_irs_soi requires either:\n"
        "\n"
        "  (a) A --copy-from PATH pointing at a local ctc-and-eitc/\n"
        "      data/external/ tree, e.g.\n"
        "\n"
        "        uv run python -m tax_modeler.scripts.fetch_irs_soi \\\n"
        "            --copy-from ~/ctc-and-eitc/data/external\n"
        "\n"
        "  (b) Manual downloads:\n"
        "      - IRS SOI (national income brackets, capital gains):\n"
        "          https://www.irs.gov/statistics/soi-tax-stats-individual-tax-statistics\n"
        "        Save XLS files as e.g. 'irs_soi_national_2022.xls' under\n"
        f"        {out_dir}\n"
        "      - BLS OES (wage data by occupation, used by EnsembleProjector):\n"
        "          https://www.bls.gov/oes/tables.htm\n"
        f"        Save under {out_dir / 'bls_oes'}\n"
        "      - Consumer Expenditure Survey (CEX, used by CEXLoader):\n"
        "          https://www.bls.gov/cex/pumd.htm\n"
        f"        Save the SAS XPT files under {out_dir / 'cex'}\n"
        "        (also requires `tax-modeler[cex]` for pyreadstat).\n"
        "\n"
        "Why no live scraper? IRS / BLS publish each year's tables as\n"
        "manually-curated WordPress / ASP.NET pages with no stable URL\n"
        "pattern. Mirroring from a local checkout is simpler, idempotent,\n"
        "and avoids rebuilding scrapers each year.\n"
    )
    print(msg, file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Populate data/tax_modeler/external/ with IRS / BLS / CEX data.",
    )
    parser.add_argument(
        "--out-dir", type=Path, default=_default_out_dir(),
        help="Destination directory (default: data/tax_modeler/external/ at repo root).",
    )
    parser.add_argument(
        "--copy-from", type=Path, default=None,
        help="Source directory to mirror from (e.g. ~/ctc-and-eitc/data/external/). "
             "The script walks the source recursively and copies every file.",
    )
    args = parser.parse_args(argv)

    args.out_dir.mkdir(parents=True, exist_ok=True)

    if args.copy_from is None:
        _print_manual_instructions(args.out_dir)
        return 1

    src = args.copy_from.expanduser().resolve()
    print(f"[fetch_irs_soi] Mirroring from {src}", file=sys.stderr)
    print(f"[fetch_irs_soi]              -> {args.out_dir}", file=sys.stderr)
    copied, skipped = _copy_tree_idempotent(src, args.out_dir)
    print(
        f"[fetch_irs_soi] Done: {copied} copied, {skipped} unchanged.",
        file=sys.stderr,
    )

    # Quick sanity check
    files = list(args.out_dir.rglob("*"))
    n_files = sum(1 for f in files if f.is_file())
    if n_files == 0:
        print(
            "[fetch_irs_soi] WARNING: no files in destination after copy.",
            file=sys.stderr,
        )
        return 2
    print(
        f"[fetch_irs_soi] {n_files} files in {args.out_dir}.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
