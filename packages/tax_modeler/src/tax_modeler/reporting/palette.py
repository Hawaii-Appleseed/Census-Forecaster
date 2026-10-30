"""Hawaiʻi Appleseed brand palette — single source for branded deliverables.

These hex values were duplicated verbatim (under two different naming
conventions) in the poverty-impact brief and the poverty dashboard. Both
now import from here, so a brand-guide change is a one-line edit.

Applies to Appleseed-branded outputs only. The other deliverables keep
their own deliberate design languages and must NOT be folded in here:

  - ``tax_modeler.reporting.quintile_pdf`` — navy/blue legislative
    distributional reports
  - ``analyses/generate_reec_report.py`` — blue-tinted REEC deliverable
  - ``analyses/generate_itep_report.py`` — ITEP-comparable HTML (also runs on
    system python3 with no library imports, so it cannot import this)
"""

from __future__ import annotations

# Brand guide: Ash / Teal / Slate / Charcoal
TEAL = "#005F73"        # primary
GOLD = "#E9B949"        # accent
SLATE = "#4A4E69"       # secondary text / bars
CHARCOAL = "#2D2D2D"    # body text
LIGHT_GRAY = "#F5F5F5"  # page/section background ("ash")
LIGHT_TEAL = "#E8F4F6"  # callout background
WHITE = "#FFFFFF"

#: Dashboard CSS-variable names → brand hex, so the HTML template and the
#: matplotlib charts cannot drift apart.
CSS_VARS = {
    "teal": TEAL,
    "gold": GOLD,
    "slate": SLATE,
    "charcoal": CHARCOAL,
    "light": LIGHT_GRAY,
    "callout": LIGHT_TEAL,
}


def hex_to_rgb(h: str) -> tuple[int, int, int]:
    """``"#005F73"`` → ``(0, 95, 115)`` — fpdf2 wants integer RGB."""
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
