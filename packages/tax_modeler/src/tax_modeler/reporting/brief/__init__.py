"""Poverty-impact brief generation (data → charts → PDF/HTML).

Modules:
    data          — BriefData, constants, formatting utilities, data loading
    charts        — matplotlib figure generation (PNG)
    pdf_renderer  — fpdf2 PDF assembly
    html_renderer — self-contained HTML fallback

Typical usage::

    from tax_modeler.reporting.brief import (
        load_brief_data, make_figures, build_pdf, build_html,
    )
"""

from .charts import make_figures
from .data import BriefData, load_brief_data
from .html_renderer import build_html
from .pdf_renderer import build_pdf

__all__ = ["BriefData", "load_brief_data", "make_figures", "build_pdf", "build_html"]
