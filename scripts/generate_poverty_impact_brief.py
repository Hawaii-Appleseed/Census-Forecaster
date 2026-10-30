"""Generate Hawaii Appleseed-styled policy brief PDF from poverty-impact CSVs.

The heavy lifting lives in tax_modeler.reporting.brief:
    data.py          — BriefData, constants, data loading
    charts.py        — matplotlib figure generation
    pdf_renderer.py  — fpdf2 PDF assembly
    html_renderer.py — self-contained HTML fallback
"""

from __future__ import annotations

import argparse
import datetime as _dt
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

from tax_modeler.reporting.brief import build_html, build_pdf, make_figures
from tax_modeler.reporting.brief.data import (
    _resolve_data_dir,
    _resolve_rxkids_dir,
    load_brief_data,
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--tax-year", type=int, default=2024)
    p.add_argument(
        "--out-dir",
        type=Path,
        default=REPO_ROOT / "reports" / "poverty_impact_brief",
    )
    p.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Path to a reports/poverty_impact_*/ directory. "
        "If omitted, auto-detects the latest tier available.",
    )
    p.add_argument(
        "--rxkids-dir",
        type=Path,
        default=None,
        help="Path to a reports/rxkids_impact_*/ directory. "
        "If omitted, auto-detects reports/rxkids_impact_<tax-year>/.",
    )
    p.add_argument(
        "--date",
        type=str,
        default=None,
        help="Date string to print on the cover (e.g. 'May 2026'). Defaults to today.",
    )
    return p.parse_args(argv)


def _rel(path: Path) -> str:
    """Return path relative to REPO_ROOT, or absolute string if outside."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    data_dir = _resolve_data_dir(args.data_dir, args.tax_year)
    print(f"[brief] using data dir: {_rel(data_dir)}", flush=True)
    rxkids_dir = _resolve_rxkids_dir(args.rxkids_dir, args.tax_year)
    if rxkids_dir is not None:
        print(f"[brief] using rxkids dir: {_rel(rxkids_dir)}", flush=True)
    else:
        print("[brief] no rxkids report found — skipping RxKids section", flush=True)

    data = load_brief_data(data_dir, args.tax_year, rxkids_dir)

    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    charts_dir = out_dir / "_charts"
    charts = make_figures(data, charts_dir)

    date_str = args.date or _dt.date.today().strftime("%B %Y")

    out_pdf = out_dir / f"HI_Appleseed_Poverty_Impact_Brief_TY{data.tax_year}.pdf"
    out_html = out_dir / f"HI_Appleseed_Poverty_Impact_Brief_TY{data.tax_year}.html"

    pdf_ok = True
    try:
        build_pdf(data, charts, out_pdf, date_str)
        print(f"[brief] wrote {_rel(out_pdf)}", flush=True)
    except Exception as exc:  # pragma: no cover - defensive
        pdf_ok = False
        print(f"[brief] PDF generation failed: {exc}", file=sys.stderr)
        print(
            "[brief] continuing with HTML fallback; install weasyprint or "
            "reportlab to enable an alternate PDF path.",
            file=sys.stderr,
        )

    build_html(data, charts, out_html, date_str)
    print(f"[brief] wrote {_rel(out_html)}", flush=True)

    return 0 if pdf_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
