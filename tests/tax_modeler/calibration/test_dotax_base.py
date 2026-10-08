"""``calibration.dotax_base``: the DOTAX Individual Income Tax Statistics anchors,
read from the TY2023 edition (``scripts/parse_dotax_indinc.py``), with the
TY2022 constants kept for reproducing the earlier base.
"""
from __future__ import annotations

import numpy as np
import pytest
from tax_modeler.calibration import (
    cg_anchor,
    dotax_base,
    forward_agi_targets,
    forward_targets,
    ipf_orchestrator,
    orchestrator,
    simultaneous_calibrator,
)

TOP = (1_000_000, np.inf)


def test_base_year_is_the_newest_edition_on_file():
    assert dotax_base.BASE_YEAR == 2023
    assert dotax_base.load_indinc(2023)["tax_year"] == 2023


def test_missing_edition_says_how_to_add_it():
    with pytest.raises(FileNotFoundError, match="parse_dotax_indinc"):
        dotax_base.load_indinc(2099)


@pytest.mark.parametrize("year", [2022, 2023])
def test_every_target_covers_the_same_15_brackets(year):
    for targets in (dotax_base.filer_targets(year), dotax_base.tax_targets_M(year),
                    dotax_base.agi_targets_M(year)):
        assert tuple(targets) == dotax_base.AGI_BRACKETS
    assert set(dotax_base.status_targets(year)) == set(dotax_base.STATUSES)


def test_ty2023_totals_are_the_published_ones():
    filers = dotax_base.filer_targets(2023)
    # Table A-8: 644,631 resident returns, 13,506 of them in the Loss class.
    assert dotax_base.total_returns(2023) == 644_631
    assert sum(filers.values()) == 644_631 - 13_506
    assert filers[TOP] == 1_704
    # tax before credits: $2,960M printed, the classes round to $2,961M
    assert sum(dotax_base.tax_targets_M(2023).values()) == pytest.approx(2_960, abs=3)
    assert dotax_base.tax_targets_M(2023)[TOP] == 441.0


def test_ty2022_constants_are_the_ones_hand_typed_before():
    assert sum(dotax_base.filer_targets(2022).values()) == 618_423
    assert dotax_base.filer_targets(2022)[TOP] == 1_824
    assert sum(dotax_base.tax_targets_M(2022).values()) == 3_030.0
    assert sum(dotax_base.agi_targets_M(2022).values()) == pytest.approx(47_255, abs=1)
    assert dotax_base.total_returns(2022) == 635_117


@pytest.mark.parametrize("year", [2022, 2023])
def test_filing_status_sums_to_the_filer_total(year):
    assert sum(dotax_base.status_targets(year).values()) == sum(dotax_base.filer_targets(year).values())


def test_ty2023_status_shares_follow_table_4():
    s = dotax_base.status_targets(2023)
    # Table 4 (residents): single 341,973 / joint 216,407 / HoH 69,345 / separate 16,718
    assert s["single"] > s["married_filing_jointly"] > s["head_of_household"] > s["married_filing_separately"]
    assert s["single"] / sum(s.values()) == pytest.approx(341_973 / 644_443, abs=1e-4)


def test_ty2023_agi_matches_a1_and_splits_the_400k_class():
    agi = dotax_base.agi_targets_M(2023)
    # A-1: $47,510M of taxable-return AGI, $9,319M of it in the $400K+ class
    assert sum(agi.values()) == pytest.approx(47_510, abs=2)
    top4 = [agi[b] for b in dotax_base.AGI_BRACKETS[-4:]]
    assert sum(top4) == pytest.approx(9_319, abs=1e-6)
    # implied average AGI per return rises with the class, and $1M+ sits above $2M
    f = dotax_base.filer_targets(2023)
    avg = [agi[b] * 1e6 / f[b] for b in dotax_base.AGI_BRACKETS[-4:]]
    assert avg == sorted(avg) and avg[-1] > 2e6
    assert 400_000 < avg[0] < 500_000 and 500_000 < avg[1] < 750_000 and 750_000 < avg[2] < 1_000_000


def test_nltcg_classes_and_totals():
    res, non = dotax_base.nltcg_M(2023), dotax_base.nltcg_M(2023, nonresident=True)
    assert len(res) == len(non) == len(dotax_base.CG_CLASSES)
    assert sum(res) == pytest.approx(2_646, abs=1.5)              # Table 21 resident total
    assert sum(non) == pytest.approx(970, abs=1.5)                # nonresident total, composites in
    assert res[-1] == 2_041.4


def test_cg_class_returns_partition_the_filers():
    c = dotax_base.cg_class_returns(2023)
    assert c["400_1m"] + c["1mp"] == 2_952 + 3_120 + 1_158 + 1_704
    assert c["1mp"] == dotax_base.filer_targets(2023)[TOP]


def test_consumers_share_one_copy():
    """The modules that rake or synthesize to DOTAX read dotax_base, not their own."""
    assert simultaneous_calibrator.DOTAX_FILER_TARGETS == dotax_base.filer_targets()
    assert simultaneous_calibrator.DOTAX_TAX_TARGETS == dotax_base.tax_targets_M()
    assert simultaneous_calibrator.DOTAX_STATUS_TARGETS == dotax_base.status_targets()
    for orch in (orchestrator.CalibrationOrchestrator, ipf_orchestrator.IPFCalibrationOrchestrator):
        assert orch.DOTAX_FILER_TARGETS == dotax_base.filer_targets()
        assert orch.DOTAX_TAX_TARGETS == dotax_base.tax_targets_M()
        assert orch.DOTAX_FILING_STATUS_TARGETS == dotax_base.status_targets()
    assert cg_anchor.DOTAX_RETURNS == dotax_base.cg_class_returns()
    assert cg_anchor.DOTAX_NLTCG_RES[2023] == dotax_base.nltcg_M(2023)


def test_forward_targets_default_to_the_base_year_and_2022_reproduces_the_old_one():
    new = forward_targets.build_targets(2027, include_agi_targets=False)
    old = forward_targets.build_targets(2027, base_year=2022, include_agi_targets=False)
    # the COR aggregate does not depend on the base; the shape does
    assert new.aggregate_tax_M == old.aggregate_tax_M
    assert new.filer_targets != old.filer_targets
    assert new.filer_targets[TOP] < old.filer_targets[TOP]           # 1,704 vs 1,824 at the base
    assert sum(new.status_targets.values()) == pytest.approx(sum(new.filer_targets.values()), abs=4)


def test_agi_targets_grow_from_the_base_year():
    t23 = forward_agi_targets.build_agi_targets(2023, include_soi_tiers=False)
    assert t23.bracket_agi_targets == pytest.approx(dotax_base.agi_targets_M(2023))
    t22 = forward_agi_targets.build_agi_targets(2022, base_year=2022, include_soi_tiers=False)
    assert t22.bracket_agi_targets == pytest.approx(dotax_base.agi_targets_M(2022))
    t27 = forward_agi_targets.build_agi_targets(2027, include_soi_tiers=False)
    assert t27.aggregate_agi_M > t23.aggregate_agi_M


# ── The Earned Income Tax Credit Report, TY2024 ──────────────────────────────

def test_eitc_report_ty2024_is_the_published_one():
    new = dotax_base.eitc_new_credit(2024)
    # Table 1, total row: 78,399 new claims worth $76,981,028
    assert new["claims"] == 78_399
    assert new["dollars_M"] == pytest.approx(76.981028)
    t = dotax_base.load_eitc_report(2024)
    total = t["table_1_by_federal_agi"]["total"]
    # applied = new + carried in - carried out, and it is the report's headline $78.5M
    assert total["applied"]["dollars"] == 78_514_304
    assert total["applied"]["dollars"] == (total["new"]["dollars"] + total["carried_in"]["dollars"]
                                           - total["carried_out"]["dollars"])


def test_eitc_report_tables_agree_with_table_1():
    t = dotax_base.load_eitc_report(2024)
    applied = t["table_1_by_federal_agi"]["total"]["applied"]
    for name in ("table_2_district", "table_3_resident_status", "table_4_filing_status"):
        assert sum(r["claims"] for r in t[name].values()) == applied["claims"], name
        assert sum(r["dollars"] for r in t[name].values()) == applied["dollars"], name
    # residents (N-11) are 94.6% of claims and 97.3% of dollars
    res = t["table_3_resident_status"]["Forms N-11"]
    assert res["claims"] / applied["claims"] == pytest.approx(0.946, abs=1e-3)
    assert res["dollars"] / applied["dollars"] == pytest.approx(0.973, abs=1e-3)


def test_missing_eitc_report_says_how_to_add_it():
    with pytest.raises(FileNotFoundError, match="parse_dotax_eitc_report"):
        dotax_base.load_eitc_report(2099)
