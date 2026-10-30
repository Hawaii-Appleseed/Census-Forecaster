"""PDF rendering for the poverty-impact brief (fpdf2)."""

from __future__ import annotations

from pathlib import Path

import matplotlib
from fpdf import FPDF

from .data import (
    CHARCOAL,
    DATA_SOURCE_CITATION,
    GOLD,
    LIGHT_GRAY,
    LIGHT_TEAL,
    SLATE,
    TEAL,
    BriefData,
    _fmt_int,
    _fmt_money_m,
    _fmt_pct,
    _hex_to_rgb,
)


# ---------------------------------------------------------------------------
# Font helper
# ---------------------------------------------------------------------------

def _matplotlib_font(name: str) -> Path:
    return Path(matplotlib.get_data_path()) / "fonts" / "ttf" / name


# ---------------------------------------------------------------------------
# BriefPDF class
# ---------------------------------------------------------------------------

class BriefPDF(FPDF):
    """PDF with the Appleseed-style page chrome."""

    BODY_FONT = "Sans"
    ITALIC_FONT = "SansItalic"

    def __init__(self, tax_year: int):
        super().__init__(format="Letter", unit="in")
        self.tax_year = tax_year
        self.set_auto_page_break(auto=False)
        self.set_margins(0.75, 0.75, 0.75)
        self._suppress_chrome = False
        self.alias_nb_pages()
        # Register a Unicode font family so we can render the okina and other
        # non-Latin-1 characters used throughout the brief.
        self.add_font(self.BODY_FONT, "", str(_matplotlib_font("DejaVuSans.ttf")))
        self.add_font(self.BODY_FONT, "B", str(_matplotlib_font("DejaVuSans-Bold.ttf")))
        self.add_font(self.BODY_FONT, "I", str(_matplotlib_font("DejaVuSans-Oblique.ttf")))
        self.add_font(
            self.BODY_FONT, "BI", str(_matplotlib_font("DejaVuSans-BoldOblique.ttf"))
        )

    def header(self) -> None:  # noqa: D401
        # Per-page header handled manually per page; nothing here.
        return None

    def footer(self) -> None:  # noqa: D401
        if self._suppress_chrome:
            return
        if self.page_no() <= 1:
            return
        self.set_y(-0.55)
        self.set_font(BriefPDF.BODY_FONT, "", 8)
        self.set_text_color(*_hex_to_rgb(SLATE))
        left = "Hawaiʻi Appleseed Center for Law & Economic Justice  |  hiappleseed.org"
        right = f"Page {self.page_no()} of {{nb}}"
        self.cell(0, 0.3, left, align="L")
        self.set_xy(-3.0, -0.55)
        self.cell(2.25, 0.3, right, align="R")
        self.set_text_color(0, 0, 0)


# ---------------------------------------------------------------------------
# Chrome helpers
# ---------------------------------------------------------------------------

def _add_section_title(pdf: BriefPDF, label_small: str, heading: str) -> None:
    pdf.set_font(BriefPDF.BODY_FONT, "B", 9)
    pdf.set_text_color(*_hex_to_rgb(GOLD))
    pdf.cell(0, 0.22, label_small.upper(), ln=1)
    pdf.set_text_color(*_hex_to_rgb(TEAL))
    page_w = pdf.w - pdf.l_margin - pdf.r_margin
    # Auto-shrink the heading to fit on a single line if needed.
    size = 22
    pdf.set_font(BriefPDF.BODY_FONT, "B", size)
    while size > 12 and pdf.get_string_width(heading) > page_w - 0.05:
        size -= 1
        pdf.set_font(BriefPDF.BODY_FONT, "B", size)
    line_h = (size / 72.0) * 1.2
    pdf.multi_cell(page_w, line_h, heading, align="L")
    # underline
    x1 = pdf.l_margin
    x2 = pdf.w - pdf.r_margin
    y = pdf.get_y() + 0.02
    pdf.set_draw_color(*_hex_to_rgb(GOLD))
    pdf.set_line_width(0.02)
    pdf.line(x1, y, x2, y)
    pdf.ln(0.18)
    pdf.set_text_color(*_hex_to_rgb(CHARCOAL))


def _body_paragraph(pdf: BriefPDF, text: str, width: float | None = None) -> None:
    pdf.set_font(BriefPDF.BODY_FONT, "", 10.5)
    pdf.set_text_color(*_hex_to_rgb(CHARCOAL))
    if width is None:
        width = pdf.w - pdf.l_margin - pdf.r_margin
    pdf.multi_cell(width, 0.19, text, align="L")
    pdf.ln(0.08)


def _callout_box(
    pdf: BriefPDF, headline: str, body: str, *, x: float, y: float, w: float, h: float
) -> None:
    pdf.set_fill_color(*_hex_to_rgb(LIGHT_TEAL))
    pdf.set_draw_color(*_hex_to_rgb(LIGHT_TEAL))
    pdf.rect(x, y, w, h, "F")
    # Headline
    pdf.set_xy(x + 0.2, y + 0.2)
    pdf.set_text_color(*_hex_to_rgb(TEAL))
    # Headline font size shrinks to fit inside the box (single line preferred).
    size = 22
    pdf.set_font(BriefPDF.BODY_FONT, "B", size)
    while size > 11 and pdf.get_string_width(headline) > (w - 0.4):
        size -= 1
        pdf.set_font(BriefPDF.BODY_FONT, "B", size)
    pdf.multi_cell(w - 0.4, (size / 72.0) * 1.15, headline, align="L")
    # Body
    pdf.set_xy(x + 0.2, pdf.get_y() + 0.08)
    pdf.set_text_color(*_hex_to_rgb(CHARCOAL))
    pdf.set_font(BriefPDF.BODY_FONT, "", 9.5)
    pdf.multi_cell(w - 0.4, 0.16, body, align="L")


# ---------------------------------------------------------------------------
# Page builders
# ---------------------------------------------------------------------------

def _cover_page(pdf: BriefPDF, data: BriefData, date_str: str) -> None:
    pdf.add_page()
    pdf._suppress_chrome = True

    page_w = pdf.w
    page_h = pdf.h
    teal_h = page_h * 0.62
    pdf.set_fill_color(*_hex_to_rgb(TEAL))
    pdf.rect(0, 0, page_w, teal_h, "F")
    pdf.set_fill_color(*_hex_to_rgb(GOLD))
    pdf.rect(0, teal_h, page_w, 0.1, "F")

    logo_x, logo_y, logo_w, logo_h = 0.6, 0.6, 3.1, 0.85
    pdf.set_fill_color(255, 255, 255)
    pdf.set_draw_color(255, 255, 255)
    pdf.rect(logo_x, logo_y, logo_w, logo_h, "F")
    pdf.set_xy(logo_x + 0.18, logo_y + 0.14)
    pdf.set_text_color(*_hex_to_rgb(TEAL))
    pdf.set_font(BriefPDF.BODY_FONT, "B", 14)
    pdf.cell(logo_w - 0.36, 0.28, "HAWAIʻI APPLESEED")
    pdf.set_xy(logo_x + 0.18, logo_y + 0.45)
    pdf.set_font(BriefPDF.BODY_FONT, "B", 8)
    pdf.cell(logo_w - 0.36, 0.22, "CENTER FOR LAW & ECONOMIC JUSTICE")

    pdf.set_xy(page_w - 2.6, 0.85)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font(BriefPDF.BODY_FONT, "B", 12)
    pdf.cell(1.85, 0.25, date_str.upper(), align="R")

    title_lines = ["KEEPING HAWAIʻI'S", "FAMILIES OUT OF", "POVERTY"]
    title_size = 38
    pdf.set_font(BriefPDF.BODY_FONT, "B", title_size)
    while title_size > 22 and max(
        pdf.get_string_width(line) for line in title_lines
    ) > page_w - 1.5:
        title_size -= 1
        pdf.set_font(BriefPDF.BODY_FONT, "B", title_size)
    line_step = (title_size / 72.0) * 1.12
    title_y = 2.4
    pdf.set_text_color(255, 255, 255)
    for i, line in enumerate(title_lines):
        pdf.set_xy(0.75, title_y + i * line_step)
        pdf.cell(0, line_step, line)

    subtitle_y = title_y + len(title_lines) * line_step + 0.2
    pdf.set_xy(0.75, subtitle_y)
    pdf.set_font(BriefPDF.BODY_FONT, "", 13)
    pdf.multi_cell(
        page_w - 1.5,
        0.22,
        "How federal and state tax credits lift tens of thousands of local "
        "residents above the poverty line — and what expanding them would do.",
        align="L",
    )

    pdf.set_xy(0.75, teal_h + 0.4)
    pdf.set_font(BriefPDF.BODY_FONT, "B", 11)
    pdf.set_text_color(*_hex_to_rgb(TEAL))
    pdf.cell(0, 0.25, "A POLICY BRIEF", ln=1)

    pdf.set_x(0.75)
    pdf.set_font(BriefPDF.BODY_FONT, "", 11)
    pdf.set_text_color(*_hex_to_rgb(CHARCOAL))
    pdf.cell(0, 0.22, "Author: Devin Thomas", ln=1)
    pdf.set_x(0.75)
    pdf.cell(0, 0.22, "Hawaiʻi Appleseed Center for Law & Economic Justice", ln=1)
    pdf.set_x(0.75)
    pdf.cell(0, 0.22, date_str, ln=1)

    state = data.state
    combined_lifted = int(round(float(state["persons_lifted_no_credits"])))
    pdf.set_xy(0.75, page_h - 2.4)
    pdf.set_font(BriefPDF.BODY_FONT, "B", 34)
    pdf.set_text_color(*_hex_to_rgb(TEAL))
    pdf.cell(0, 0.5, _fmt_int(combined_lifted), ln=1)
    pdf.set_x(0.75)
    pdf.set_font(BriefPDF.BODY_FONT, "", 12)
    pdf.set_text_color(*_hex_to_rgb(CHARCOAL))
    pdf.multi_cell(
        page_w - 1.5,
        0.2,
        "local residents kept out of poverty every year by the federal EITC, "
        f"federal CTC, and Hawaiʻi state EITC combined (TY{data.tax_year}).",
        align="L",
    )

    pdf.set_xy(0.75, page_h - 0.9)
    pdf.set_font(BriefPDF.BODY_FONT, "", 8.5)
    pdf.set_text_color(*_hex_to_rgb(SLATE))
    pdf.multi_cell(page_w - 1.5, 0.16, DATA_SOURCE_CITATION, align="L")

    pdf._suppress_chrome = False


def _executive_summary(pdf: BriefPDF, data: BriefData) -> None:
    pdf.add_page()
    _add_section_title(pdf, "Executive Summary", "AT A GLANCE")

    state = data.state
    combined_lifted = int(round(float(state["persons_lifted_no_credits"])))
    combined_gap = float(state["gap_closed_no_credits_$"])
    hi_ctc_lifted = int(round(float(state["persons_lifted_hi_ctc_650"])))

    page_w = pdf.w - pdf.l_margin - pdf.r_margin
    box_w = (page_w - 0.4) / 3
    box_h = 1.9
    y = pdf.get_y()

    _callout_box(
        pdf,
        f"{_fmt_int(combined_lifted)} people",
        "kept out of poverty every year by federal EITC, federal CTC, and "
        "Hawaiʻi state EITC combined.",
        x=pdf.l_margin,
        y=y,
        w=box_w,
        h=box_h,
    )
    _callout_box(
        pdf,
        f"{_fmt_money_m(combined_gap)}",
        "in poverty gap closed annually by these three tax credits.",
        x=pdf.l_margin + box_w + 0.2,
        y=y,
        w=box_w,
        h=box_h,
    )
    _callout_box(
        pdf,
        f"+{_fmt_int(hi_ctc_lifted)}",
        "additional residents would be lifted out of poverty if Hawaiʻi "
        "enacted a $650-per-child state Child Tax Credit.",
        x=pdf.l_margin + (box_w + 0.2) * 2,
        y=y,
        w=box_w,
        h=box_h,
    )

    pdf.set_y(y + box_h + 0.35)
    _body_paragraph(
        pdf,
        "Hawaiʻi has the highest cost of living in the nation. Rent, food, and "
        "child care consume a disproportionate share of working families' "
        "budgets, pushing many local residents into poverty even when they hold "
        "full-time jobs. Refundable tax credits — dollars paid directly to "
        "low- and moderate-income filers — are among the most effective "
        "anti-poverty tools we have, and they pay for themselves several times "
        "over in stronger family budgets, healthier kids, and reduced reliance "
        "on safety-net programs.",
    )
    _body_paragraph(
        pdf,
        "This brief uses the Supplemental Poverty Measure (SPM), the Census "
        "Bureau's more comprehensive measure of poverty, applied to the "
        "American Community Survey 5-Year Public Use Microdata Sample for "
        "Hawaiʻi (2018–2022). It estimates how many local residents are "
        "kept above the poverty line each year by the federal Earned Income Tax "
        "Credit (EITC), the federal Child Tax Credit (CTC), and Hawaiʻi's "
        "state EITC — and what would happen if Hawaiʻi expanded its own "
        "credits.",
    )

    combined_pp = (float(state["poverty_rate_no_credits"]) - float(state["poverty_rate_baseline"])) * 100
    state_rate = _fmt_pct(state["poverty_rate_baseline"])
    persons_in_pov = _fmt_int(state["persons_in_poverty_baseline"])
    no_credits_pop = _fmt_int(int(round(float(state["persons_in_poverty_baseline"]))) + combined_lifted)
    _body_paragraph(
        pdf,
        f"In TY{data.tax_year}, an estimated {persons_in_pov} Hawaiʻi "
        f"residents ({state_rate}) lived below the SPM poverty line. Together, "
        f"the federal EITC, federal CTC, and Hawaiʻi state EITC reduce the SPM "
        f"poverty rate by {combined_pp:.1f} percentage points — keeping "
        f"{_fmt_int(combined_lifted)} residents above the line. Without these "
        f"credits, approximately {no_credits_pop} residents would be in poverty.",
    )

    pdf.set_y(pdf.h - 1.2)
    pdf.set_font(BriefPDF.BODY_FONT, "I", 8.5)
    pdf.set_text_color(*_hex_to_rgb(SLATE))
    pdf.multi_cell(0, 0.16, DATA_SOURCE_CITATION, align="L")


def _figure_page(
    pdf: BriefPDF,
    section_label: str,
    heading: str,
    figure_path: Path,
    narrative_paragraphs: list[str],
) -> None:
    pdf.add_page()
    _add_section_title(pdf, section_label, heading)
    page_w = pdf.w - pdf.l_margin - pdf.r_margin
    pdf.image(str(figure_path), x=pdf.l_margin, w=page_w)
    pdf.ln(0.25)
    for para in narrative_paragraphs:
        _body_paragraph(pdf, para)


def _expansion_page(pdf: BriefPDF, data: BriefData, figure_path: Path) -> None:
    pdf.add_page()
    _add_section_title(pdf, "Looking Ahead", "WHAT MORE COULD BE DONE?")
    state = data.state
    page_w = pdf.w - pdf.l_margin - pdf.r_margin
    pdf.image(str(figure_path), x=pdf.l_margin, w=page_w)
    pdf.ln(0.2)

    pdf.set_font(BriefPDF.BODY_FONT, "B", 10)
    pdf.set_fill_color(*_hex_to_rgb(TEAL))
    pdf.set_text_color(255, 255, 255)
    col_w = [3.1, 1.5, 1.2, 1.2]
    pdf.cell(col_w[0], 0.3, "Policy scenario", border=0, fill=True)
    pdf.cell(col_w[1], 0.3, "Est. people lifted", border=0, fill=True, align="R")
    pdf.cell(col_w[2], 0.3, "Gap closed", border=0, fill=True, align="R")
    pdf.cell(col_w[3], 0.3, "Annual cost*", border=0, fill=True, align="R")
    pdf.ln()

    rows = [
        (
            "Raise HI state EITC to 100% of federal",
            _fmt_int(state["persons_lifted_hi_eitc_100pct"]),
            _fmt_money_m(state["gap_closed_hi_eitc_100pct_$"]),
            "~$30M",
        ),
        (
            "Enact new $650/child HI state CTC",
            _fmt_int(state["persons_lifted_hi_ctc_650"]),
            _fmt_money_m(state["gap_closed_hi_ctc_650_$"]),
            "~$83M",
        ),
    ]
    _rx = data.rxkids_state
    if _rx is not None:
        rows.append((
            "RxKids Hawaiʻi (proposed)",
            _fmt_int(_rx["persons_lifted_rxkids_hi"]),
            _fmt_money_m(_rx["gap_closed_rxkids_hi_$"]),
            "~$60M",
        ))
        _total_lifted = (
            float(state["persons_lifted_hi_eitc_100pct"])
            + float(state["persons_lifted_hi_ctc_650"])
            + float(_rx["persons_lifted_rxkids_hi"])
        )
        _total_gap = (
            float(state["gap_closed_hi_eitc_100pct_$"])
            + float(state["gap_closed_hi_ctc_650_$"])
            + float(_rx["gap_closed_rxkids_hi_$"])
        )
        rows.append((
            "All three combined",
            "~" + _fmt_int(_total_lifted),
            _fmt_money_m(_total_gap),
            "~$173M",
        ))
    else:
        rows.append((
            "Both combined (non-additive)",
            "~" + _fmt_int(
                float(state["persons_lifted_hi_eitc_100pct"])
                + float(state["persons_lifted_hi_ctc_650"])
            ),
            _fmt_money_m(
                float(state["gap_closed_hi_eitc_100pct_$"])
                + float(state["gap_closed_hi_ctc_650_$"])
            ),
            "~$113M",
        ))
    pdf.set_text_color(*_hex_to_rgb(CHARCOAL))
    pdf.set_font(BriefPDF.BODY_FONT, "", 10)
    for i, row in enumerate(rows):
        fill = i % 2 == 0
        if fill:
            pdf.set_fill_color(*_hex_to_rgb(LIGHT_GRAY))
        pdf.cell(col_w[0], 0.3, row[0], border=0, fill=fill)
        pdf.cell(col_w[1], 0.3, row[1], border=0, fill=fill, align="R")
        pdf.cell(col_w[2], 0.3, row[2], border=0, fill=fill, align="R")
        pdf.cell(col_w[3], 0.3, row[3], border=0, fill=fill, align="R")
        pdf.ln()

    pdf.ln(0.1)
    pdf.set_font(BriefPDF.BODY_FONT, "I", 8.5)
    pdf.set_text_color(*_hex_to_rgb(SLATE))
    pdf.multi_cell(
        0,
        0.16,
        "* Annual cost figures are legislative estimates from prior fiscal notes, "
        "not produced by this model. Persons-lifted and gap-closed figures are "
        "model output and may differ from other estimates because they reflect "
        "the SPM definition of poverty and IRS-calibrated take-up.",
        align="L",
    )
    pdf.ln(0.1)
    pdf.set_font(BriefPDF.BODY_FONT, "", 10.5)
    pdf.set_text_color(*_hex_to_rgb(CHARCOAL))
    if data.rxkids_state is not None:
        _body_paragraph(
            pdf,
            "Any of these three policies would meaningfully reduce poverty in "
            "Hawaiʻi. Enacted together, they represent a comprehensive strategy: "
            "expanding EITC reaches low-income workers; a new state CTC supports "
            "parents of young children; and RxKids delivers direct cash to "
            "expecting mothers and infants in the critical first months of life. "
            "Because all three channels target the families with the lowest "
            "incomes, every dollar is likely to flow back into the local economy "
            "through rent, food, and child care.",
        )
    else:
        _body_paragraph(
            pdf,
            "Either expansion would meaningfully reduce poverty in Hawaiʻi; "
            "together they would lift several thousand additional residents above "
            "the SPM threshold and close hundreds of millions of dollars in "
            "remaining poverty gap. Because both credits are refundable, they reach "
            "the families with the lowest incomes — those most likely to spend "
            "every dollar locally on rent, food, and child care.",
        )


def _methodology_page(pdf: BriefPDF, data: BriefData) -> None:
    pdf.add_page()
    _add_section_title(pdf, "Methodology", "HOW WE DID THIS")
    _body_paragraph(
        pdf,
        "These estimates are produced by the Census-Forecaster tax simulation "
        "model maintained by Hawaiʻi Appleseed. The model uses the U.S. "
        "Census Bureau's 5-Year American Community Survey Public Use "
        "Microdata Sample (ACS PUMS) for Hawaiʻi, 2018–2022, projected "
        "forward to the tax year of interest using a recency-weighted, "
        "cadence-aware damped-trend ensemble.",
    )
    _body_paragraph(
        pdf,
        "Poverty is measured using the Supplemental Poverty Measure (SPM), the "
        "Census Bureau's comprehensive poverty measure. Unlike the official "
        "poverty measure, the SPM accounts for federal and state tax payments, "
        "refundable tax credits, non-cash benefits such as SNAP and housing "
        "subsidies, medical out-of-pocket costs, and Hawaiʻi's "
        "above-average cost of living through a geographic adjustment to the "
        "poverty threshold.",
    )
    _body_paragraph(
        pdf,
        "Following Census P60-280, poverty is measured at the SPM-unit level "
        "(the resource-sharing group within a household: householder + "
        "relatives + unmarried partner + foster children + any unrelated "
        "children under 15), not at the tax-unit level. Tax credits and "
        "benefits are computed on tax returns and then summed to the SPM "
        "unit for the poverty comparison.",
    )
    _body_paragraph(
        pdf,
        "Counterfactual scenarios are static: each scenario re-runs the SPM "
        "calculation with the relevant credit removed (or expanded), holding "
        "labor supply and program take-up fixed. Take-up of each credit is "
        "calibrated to IRS SOI Hawaiʻi totals so that modeled receipt "
        "matches administrative records, not statutory eligibility.",
    )
    _body_paragraph(
        pdf,
        "District-level estimates (Senate and House) reflect within-PUMA "
        "imputation and carry larger uncertainty than statewide and county "
        "figures — typically ±20 percent at the district level. "
        "Statewide totals are within roughly 5 percent of administrative "
        "benchmarks where available.",
    )
    _body_paragraph(
        pdf,
        "The methodology, parameters, and full results tables are documented "
        "in this project's public methodology files (METHODOLOGY.md, "
        "docs/archive/REVIEW_FINDINGS.md). The model and its source code are maintained as a "
        "Python package; a v2 release was published in 2025.",
    )


def _about_page(pdf: BriefPDF, date_str: str) -> None:
    pdf.add_page()
    _add_section_title(pdf, "About", "ABOUT HAWAIʻI APPLESEED")
    _body_paragraph(
        pdf,
        "Hawaiʻi Appleseed Center for Law & Economic Justice is committed "
        "to a more socially and economically just Hawaiʻi, where everyone "
        "has genuine opportunities to achieve economic security and fulfill "
        "their potential. We change systems to address inequity and foster "
        "greater opportunity by conducting data analysis and research to "
        "address income inequality, educating policymakers and the public, "
        "engaging in collaborative problem solving and coalition building, and "
        "advocating for policy and systems change.",
    )
    _body_paragraph(
        pdf,
        "The work of Hawaiʻi Appleseed is about people. The issues we work "
        "on — housing, food, wages, mobility, the state budget and "
        "taxation, and racial and indigenous equity — are important because "
        "they ensure people have access to shelter, sustenance, and the means "
        "to survive and thrive individually and collectively.",
    )
    pdf.ln(0.25)
    _add_section_title(pdf, "Author", "ACKNOWLEDGMENTS")
    _body_paragraph(pdf, "Author: Devin Thomas, Policy Analyst, Hawaiʻi Appleseed.")
    _body_paragraph(
        pdf,
        "This brief uses the Census-Forecaster open-source tax simulation "
        "model. The author thanks the Hawaiʻi state Department of Taxation "
        "and the U.S. Census Bureau for the public data that make this analysis "
        "possible.",
    )
    pdf.ln(0.4)
    pdf.set_font(BriefPDF.BODY_FONT, "", 9.5)
    pdf.set_text_color(*_hex_to_rgb(SLATE))
    pdf.multi_cell(
        0,
        0.18,
        f"Published {date_str}. "
        "Hawaiʻi Appleseed Center for Law & Economic Justice, "
        "733 Bishop Street, Suite 1180, Honolulu, HI 96813. "
        "hiappleseed.org",
        align="L",
    )
    pdf.ln(0.1)
    pdf.set_font(BriefPDF.BODY_FONT, "I", 8.5)
    pdf.multi_cell(0, 0.16, DATA_SOURCE_CITATION, align="L")


def _background_page(pdf: BriefPDF, data: BriefData, charts: dict[str, Path]) -> None:
    pdf.add_page()
    _add_section_title(pdf, "Background", "WHO BEARS THE BURDEN OF POVERTY IN HAWAIʻI?")

    state = data.state
    hht = data.household_types
    rs = data.racial_stats

    hoh_rate = None
    mfj_rate = None
    if hht is not None:
        hoh_row = hht[hht["filing_status"] == "head_of_household"]
        mfj_row = hht[hht["filing_status"] == "married_filing_jointly"]
        if len(hoh_row) > 0:
            hoh_rate = float(hoh_row.iloc[0]["poverty_rate_baseline"])
        if len(mfj_row) > 0:
            mfj_rate = float(mfj_row.iloc[0]["poverty_rate_baseline"])

    if hoh_rate is not None and mfj_rate is not None:
        ratio = hoh_rate / mfj_rate
        _body_paragraph(
            pdf,
            f"Poverty in Hawaiʻi is not spread evenly. Single-parent households "
            f"face an SPM poverty rate of {hoh_rate*100:.1f}% — more than "
            f"{ratio:.0f} times the {mfj_rate*100:.1f}% rate among married-couple "
            f"households. These are the families most exposed when rent increases, "
            f"child-care costs rise, or a job is lost — and they are the primary "
            f"beneficiaries of the tax credits analyzed in this brief.",
        )

    if "fig_bg1" in charts:
        fig_img_w = pdf.w - pdf.l_margin - pdf.r_margin
        fig_img_h = fig_img_w * (3.8 / 7.5)
        y_fig = pdf.get_y() + 0.1
        pdf.image(str(charts["fig_bg1"]), x=pdf.l_margin, y=y_fig,
                  w=fig_img_w, h=fig_img_h)
        pdf.set_y(y_fig + fig_img_h + 0.15)

    if rs is not None:
        nhpi = rs[rs["race"].str.contains("Pacific Islander|NHPI", na=False)]
        nh_combo = rs[rs["race"].str.contains("Hawaiian.*alone or", na=False)]
        wht = rs[rs["race"].str.contains("White", na=False)]
        rs_vintage = rs["vintage"].iloc[0] if "vintage" in rs.columns else "ACS PUMS"
        if len(nhpi) > 0 and len(wht) > 0:
            nhpi_rate = float(nhpi.iloc[0]["poverty_rate"])
            wht_rate = float(wht.iloc[0]["poverty_rate"])
            nhpi_inc = float(nhpi.iloc[0]["median_income"])
            wht_inc = float(wht.iloc[0]["median_income"])
            nh_txt = ""
            if len(nh_combo) > 0:
                nh_rate = float(nh_combo.iloc[0]["poverty_rate"])
                nh_txt = (
                    f" Native Hawaiians (alone or in combination with another race) "
                    f"experience a {nh_rate*100:.1f}% official poverty rate, "
                    f"compared to {wht_rate*100:.1f}% for White alone residents."
                )
            _body_paragraph(
                pdf,
                f"Racial disparities compound this picture. Pacific Islander households "
                f"(NHPI alone) have an official poverty rate of {nhpi_rate*100:.1f}% — "
                f"nearly twice the {wht_rate*100:.1f}% rate for White alone residents. "
                f"The median personal income for NHPI-alone earners is "
                f"${nhpi_inc:,.0f}/year, compared to ${wht_inc:,.0f} for White alone "
                f"earners — a gap of ${wht_inc - nhpi_inc:,.0f}.{nh_txt} "
                f"(Source: {rs_vintage} PUMS, official poverty measure.)",
            )

    if "fig_bg2" in charts:
        fig_img_w = pdf.w - pdf.l_margin - pdf.r_margin
        fig_img_h = fig_img_w * (3.8 / 7.5)
        y_fig2 = pdf.get_y() + 0.1
        if y_fig2 + fig_img_h < pdf.h - 1.5:
            pdf.image(str(charts["fig_bg2"]), x=pdf.l_margin, y=y_fig2,
                      w=fig_img_w, h=fig_img_h)
            pdf.set_y(y_fig2 + fig_img_h + 0.1)

    pdf.set_y(pdf.h - 1.2)
    pdf.set_font(BriefPDF.BODY_FONT, "I", 8.5)
    pdf.set_text_color(*_hex_to_rgb(SLATE))
    pdf.multi_cell(0, 0.16, DATA_SOURCE_CITATION, align="L")


# ---------------------------------------------------------------------------
# PDF orchestrator — public alias: build_pdf
# ---------------------------------------------------------------------------

def _build_pdf(
    data: BriefData,
    charts: dict[str, Path],
    out_pdf: Path,
    date_str: str,
) -> None:
    pdf = BriefPDF(tax_year=data.tax_year)
    _cover_page(pdf, data, date_str)
    _executive_summary(pdf, data)
    _background_page(pdf, data, charts)

    state = data.state
    state_rate = _fmt_pct(state["poverty_rate_baseline"])
    state_pov = _fmt_int(state["persons_in_poverty_baseline"])

    _figure_page(
        pdf,
        "Baseline",
        "HOW MANY HAWAIʻI RESIDENTS ARE IN POVERTY?",
        charts["fig1"],
        [
            f"Under the Supplemental Poverty Measure, an estimated {state_pov} "
            f"Hawaiʻi residents — {state_rate} of the state population — "
            f"lived in poverty in TY{data.tax_year}. The SPM differs from the "
            "official poverty measure by accounting for taxes, refundable tax "
            "credits, non-cash benefits, medical out-of-pocket costs, and "
            "Hawaiʻi's above-average cost of living.",
            "Neighbor-island counties show higher SPM rates than Oʻahu, "
            "reflecting lower wages combined with rents and food prices that "
            "remain near urban-Honolulu levels. These geographic differences "
            "are why a flat federal threshold understates poverty in Hawaiʻi.",
        ],
    )
    fed_eitc = int(round(float(state["persons_lifted_no_eitc"])))
    hi_eitc = int(round(float(state["persons_lifted_no_hi_eitc"])))
    eitc_total = fed_eitc + hi_eitc
    fed_eitc_pp = (float(state["poverty_rate_no_eitc"]) - float(state["poverty_rate_baseline"])) * 100
    hi_eitc_pp = (float(state["poverty_rate_no_hi_eitc"]) - float(state["poverty_rate_baseline"])) * 100
    _figure_page(
        pdf,
        "EITC (Federal + State)",
        "HAWAIʻI'S LARGEST ANTI-POVERTY CREDIT",
        charts["fig2"],
        [
            f"The Earned Income Tax Credit is Hawaiʻi's single most "
            f"powerful anti-poverty tool. The federal EITC alone reduces "
            f"the SPM poverty rate by {fed_eitc_pp:.1f} percentage points, "
            f"lifting {_fmt_int(fed_eitc)} residents above the poverty line "
            f"each year. Hawaiʻi's state EITC — made fully refundable and "
            f"raised to 40% of the federal credit by Act 209 (2023) — reduces "
            f"the rate by an additional {hi_eitc_pp:.1f} percentage points, "
            f"lifting {_fmt_int(hi_eitc)} more residents. Combined, the two "
            f"EITCs keep roughly {_fmt_int(eitc_total)} local residents out "
            f"of poverty annually (sum is approximate; the credits interact).",
            "Both credits are fully refundable, targeted at low- and "
            "moderate-income workers, and scale with earnings up to a "
            "plateau — rewarding work while still reaching the lowest-income "
            "families. Because EITC dollars arrive as a tax refund, they "
            "typically flow back into the local economy through rent, "
            "groceries, and child care.",
            "Hawaiʻi's 40% state match is now among the most generous in "
            "the country. Raising it further — or simplifying eligibility "
            "for the lowest-income filers — would deliver meaningful "
            "additional poverty reduction at modest fiscal cost (see "
            "expansion scenarios below).",
        ],
    )
    ctc_pp = (float(state["poverty_rate_no_ctc"]) - float(state["poverty_rate_baseline"])) * 100
    ctc_lifted_str = _fmt_int(state["persons_lifted_no_ctc"])
    _figure_page(
        pdf,
        "Federal CTC",
        "THE FEDERAL CHILD TAX CREDIT",
        charts["fig3"],
        [
            f"The federal Child Tax Credit reduces the SPM poverty rate by "
            f"{ctc_pp:.1f} percentage points, lifting {ctc_lifted_str} "
            f"Hawaiʻi residents above the poverty line. After the "
            "American Rescue Plan's 2021 expansion expired, the credit reverted "
            "to a partially refundable structure that leaves many of the lowest-income "
            "families with less than the full benefit.",
            "Even in its current form, the CTC remains one of the most "
            "powerful tools available for reducing child poverty in Hawaiʻi — "
            "and is among the credits whose expansion would have the largest "
            "incremental impact on families with young children.",
        ],
    )
    if "fig4" in charts and data.rxkids_state is not None:
        rx = data.rxkids_state
        rx_lifted = int(round(float(rx["persons_lifted_rxkids_hi"])))
        rx_gap = float(rx["gap_closed_rxkids_hi_$"])
        rx_pp = (float(rx["poverty_rate_baseline"]) - float(rx["poverty_rate_rxkids_hi"])) * 100
        _figure_page(
            pdf,
            "RxKids Hawaiʻi (Proposed)",
            "A CASH PRESCRIPTION FOR HAWAIʻI'S YOUNGEST",
            charts["fig4"],
            [
                "RxKids is a prenatal-and-infant cash-transfer program "
                "first launched in Flint, Michigan and now operating in "
                "35+ Michigan communities. A Hawaiʻi-equivalent program — "
                "modeled here as an unconditional benefit available to "
                "all expecting mothers regardless of income — would provide "
                "a one-time $1,500 payment during pregnancy and six monthly "
                "payments of $500 beginning at birth ($3,000 postnatal "
                "total per child).",
                f"Under this design, an estimated {_fmt_int(rx_lifted)} "
                f"Hawaiʻi residents would be lifted above the SPM poverty "
                f"line, reducing the SPM poverty rate by {rx_pp:.1f} percentage "
                f"points and closing "
                f"{_fmt_money_m(rx_gap)} in poverty gap. "
                "The program would cost roughly $60M per year statewide — "
                "reaching all ~15,500 annual births with a combined "
                "$4,500 per pregnancy benefit.",
                "The SPM poverty-line crossing count is a conservative "
                "floor on the program's value. Research on the Flint "
                "program documents significant downstream effects on "
                "birth weight, maternal mental health, and infant "
                "developmental outcomes that are not captured in the "
                "poverty-rate metric. For comparison, Figure 4 also "
                "shows the projected lift from the two other proposed "
                "Hawaiʻi credit expansions modeled in this brief.",
            ],
        )
    _figure_page(
        pdf,
        "Combined Impact",
        "BY STATE SENATE DISTRICT",
        charts["fig5"],
        [
            "Looking across the state's 25 Senate districts, the top ten "
            "districts — generally those covering working-class "
            "neighborhoods on Oʻahu and the neighbor islands — "
            "account for more than half of all residents lifted out of poverty "
            "by the three credits combined.",
            "These district-level estimates are useful for legislators "
            "evaluating who in their communities benefits today — and how "
            "many more would benefit if Hawaiʻi expanded its credits.",
        ],
    )
    _expansion_page(pdf, data, charts["fig6"])
    _methodology_page(pdf, data)
    _about_page(pdf, date_str)

    pdf.output(str(out_pdf))


# Public alias
build_pdf = _build_pdf
