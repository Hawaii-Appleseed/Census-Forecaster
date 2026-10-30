"""
Tests for Hawaii tax calculator scenarios: Act 46, HB 2306 HD1, SB 3125 SD1.

Covers:
  - Bracket calculations (rate changes, pre-credit impact)
  - CDCC for all three credit scenarios (including SB 3125 formula)
  - Integration regression tests against known revenue totals
  - Scenario isolation (SB 3125 backend changes don't affect unrelated scenarios)
"""
from __future__ import annotations

import math
from pathlib import Path

import pytest
import tax_modeler as _tm
from tax_modeler.adjustments.hawaii_credits import HawaiiTaxCredits
from tax_modeler.config import TaxCalculator, TaxSystemRegistry

# `TaxCalculator(project_root)` reads `project_root/data/raw/*.csv`. The
# small bracket + deduction master CSVs that the calculator needs ship
# inside the wheel under tax_modeler/data/raw/, so resolve project_root
# to the package directory.
_PACKAGE_ROOT = Path(_tm.__file__).resolve().parent


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def calculator():
    return TaxCalculator(_PACKAGE_ROOT)


@pytest.fixture(scope="session")
def act46():
    return TaxSystemRegistry.get_act46_2025_system()


@pytest.fixture(scope="session")
def hb2306():
    return TaxSystemRegistry.get_hb2306_hd1_2027_system()


@pytest.fixture(scope="session")
def sb3125():
    return TaxSystemRegistry.get_sb3125_sd1_2027_system()


@pytest.fixture(scope="session")
def credits_2025():
    return HawaiiTaxCredits(2025)


@pytest.fixture(scope="session")
def credits_2027():
    return HawaiiTaxCredits(2027)


# ── Bracket tests ─────────────────────────────────────────────────────────────

class TestBrackets:
    def test_act46_joint_100k(self, calculator, act46):
        """Act 46 joint filer at $100k should produce a known bracket result."""
        result = calculator.calculate_tax(100_000, act46, "married_filing_jointly", 2)
        # Hawaii std deduction for joint = $8,800; personal exemptions = 2 × $1,200 = $2,400
        # Taxable income = $100k - $8,800 - $2,400 = $88,800
        assert result["tax_liability"] > 0
        assert result["taxable_income"] == pytest.approx(88_800, abs=200)
        # Marginal rate should be in the 6.4–8.0% range for Act 46 at this income
        assert 6.0 <= result["marginal_rate"] <= 8.0

    def test_hb2306_top_bracket_joint_700k(self, calculator, act46, hb2306):
        """HB 2306 top-3 rate +1pp should add ~$2,400 at $688,800 taxable joint.

        HB 2306 HD1 also doubles the standard deduction in 2027 (joint
        $8,800 → $16,000). To isolate the bracket-rate change, we
        evaluate both scenarios at the same TAXABLE income by inflating
        HB 2306's gross by the std-deduction delta ($7,200).
        """
        std_ded_delta = 16_000 - 8_800  # 2027 joint std ded - 2025 joint std ded
        base = calculator.calculate_tax(700_000, act46, "married_filing_jointly", 2)
        hb = calculator.calculate_tax(700_000 + std_ded_delta, hb2306, "married_filing_jointly", 2)
        # Sanity: same taxable income in both branches.
        assert base["taxable_income"] == pytest.approx(hb["taxable_income"], abs=1)
        diff = hb["tax_liability"] - base["tax_liability"]
        # Three brackets get +1pp on top income slices; at $688,800 taxable
        # the $450k-$550k and $550k-$650k slices contribute $1,000 each; the
        # remaining $38,800 in the top bracket contributes ~$388 — total ≈ $2,388.
        assert diff == pytest.approx(2_400, abs=50)

    def test_hb2306_low_income_no_change(self, calculator, act46, hb2306):
        """Below the affected brackets, HB 2306 should not change tax (rate-only).

        Same matched-taxable-income approach as above: HB 2306 HD1
        doubles the single std deduction in 2027 ($4,400 → $8,000), so
        we inflate gross by $3,600 to keep taxable income equal.
        """
        std_ded_delta = 8_000 - 4_400  # 2027 single std ded - 2025 single std ded
        base = calculator.calculate_tax(80_000, act46, "single", 1)
        hb = calculator.calculate_tax(80_000 + std_ded_delta, hb2306, "single", 1)
        assert base["taxable_income"] == pytest.approx(hb["taxable_income"], abs=1)
        assert hb["tax_liability"] == pytest.approx(base["tax_liability"], abs=1)

    def test_sb3125_wider_low_brackets_lower_tax(self, calculator, act46, sb3125):
        """SB 3125 wider low/mid brackets should reduce tax for middle-income filers."""
        base = calculator.calculate_tax(60_000, act46, "single", 1)
        sb = calculator.calculate_tax(60_000, sb3125, "single", 1)
        # SB 3125 expands lower brackets so tax should be <= Act 46 at $60k
        assert sb["tax_liability"] <= base["tax_liability"]

    def test_credit_scenario_on_registry(self):
        """Registry methods should expose correct credit_scenario tags."""
        assert TaxSystemRegistry.get_hb2306_hd1_2027_system().credit_scenario == "hb2306_hd1"
        assert TaxSystemRegistry.get_sb3125_sd1_2027_system().credit_scenario == "sb3125_sd1"
        assert TaxSystemRegistry.get_act46_2025_system().credit_scenario is None


# ── CDCC tests ────────────────────────────────────────────────────────────────

class TestCDCC:
    def test_cdcc_zero_dependents_all_scenarios(self, credits_2025, credits_2027):
        """CDCC is 0 for filers with no dependents regardless of scenario."""
        for cs in (None, "hb2306_hd1", "sb3125_sd1"):
            calc = credits_2027 if cs else credits_2025
            result = calc.calculate_total_credits(80_000, "single", 0, 5_000, cs)
            assert result["child_care"] == 0.0, f"Expected 0 for {cs}, got {result['child_care']}"

    def test_act46_cdcc_hoh_50k_2kids(self, credits_2025):
        """Act 46 CDCC for HoH $50k, 2 kids: expense cap $6k × ~21% ≈ $1,250."""
        result = credits_2025.calculate_total_credits(50_000, "head_of_household", 2, 3_000, None)
        # pct ≈ 20% (above $43k), expenses = min($24k, $6k) = $6k → $1,200
        assert result["child_care"] == pytest.approx(1_200, abs=50)

    def test_act46_cdcc_nonrefundable(self, credits_2025):
        """Act 46 CDCC is non-refundable — capped at tax_before_credits."""
        result = credits_2025.calculate_total_credits(40_000, "head_of_household", 2, 100, None)
        assert result["total_nonrefundable"] <= 100

    def test_act46_cdcc_phases_out_above_100k(self, credits_2025):
        """Act 46 CDCC phases out entirely above $100k AGI."""
        result = credits_2025.calculate_total_credits(150_000, "single", 1, 5_000, None)
        assert result["child_care"] == 0.0

    def test_hb2306_cdcc_hoh_50k_2kids(self, credits_2027):
        """HB 2306 CDCC for HoH $50k, 2 kids: $20k × 50% = $10,000."""
        result = credits_2027.calculate_total_credits(50_000, "head_of_household", 2, 3_000, "hb2306_hd1")
        assert result["child_care"] == pytest.approx(10_000, abs=1)

    def test_hb2306_cdcc_refundable(self, credits_2027):
        """Under HB 2306, CDCC should appear in refundable credits."""
        result = credits_2027.calculate_total_credits(50_000, "head_of_household", 2, 500, "hb2306_hd1")
        assert result["child_care"] > 0
        assert result["total_refundable"] >= result["child_care"]

    def test_hb2306_cdcc_floor_5pct(self, credits_2027):
        """HB 2306 CDCC applicable percentage floors at 5% above $160k."""
        # $200k income → 5% × $10k cap × 1 child = $500
        result = credits_2027.calculate_total_credits(200_000, "single", 1, 10_000, "hb2306_hd1")
        assert result["child_care"] == pytest.approx(500, abs=1)

    def test_sb3125_cdcc_hoh_50k_2kids(self, credits_2027):
        """SB 3125 CDCC for HoH $50k: formula 35% − ceil((50k−43k)/3k)×1pp = 33%, $20k cap → $6,600."""
        steps = math.ceil((50_000 - 43_000) / 3_000)  # ceil(7000/3000) = 3
        expected_pct = max(0.15, 0.35 - 0.01 * steps)  # 0.35 - 0.03 = 0.32
        expected = min(12_000 * 2, 20_000) * expected_pct
        result = credits_2027.calculate_total_credits(50_000, "head_of_household", 2, 3_000, "sb3125_sd1")
        assert result["child_care"] == pytest.approx(expected, abs=1)

    def test_sb3125_cdcc_floor_15pct(self, credits_2027):
        """SB 3125 CDCC applicable percentage floors at 15%."""
        # At AGI = $200k: steps = ceil((200k-43k)/3k) = 53 → 35-53 = -18 → floor 15%
        result = credits_2027.calculate_total_credits(200_000, "single", 1, 10_000, "sb3125_sd1")
        # $10k cap × 15% = $1,500
        assert result["child_care"] == pytest.approx(1_500, abs=1)

    def test_sb3125_cdcc_at_threshold(self, credits_2027):
        """SB 3125 CDCC at exactly $43k AGI should use 35%."""
        result = credits_2027.calculate_total_credits(43_000, "single", 1, 5_000, "sb3125_sd1")
        # steps = ceil(0/3000) = 0 → 35%
        assert result["child_care"] == pytest.approx(10_000 * 0.35, abs=1)

    def test_sb3125_cdcc_refundable(self, credits_2027):
        """Under SB 3125, CDCC should appear in refundable credits."""
        result = credits_2027.calculate_total_credits(50_000, "head_of_household", 2, 200, "sb3125_sd1")
        assert result["child_care"] > 0
        assert result["total_refundable"] >= result["child_care"]

    def test_sb3125_cdcc_zero_no_dependents(self, credits_2027):
        """SB 3125 CDCC is 0 with no dependents."""
        result = credits_2027.calculate_total_credits(50_000, "single", 0, 2_000, "sb3125_sd1")
        assert result["child_care"] == 0.0

    def test_hb2306_cdcc_above_80k_reduced(self, credits_2027):
        """HB 2306 CDCC applicable percentage decreases in $10k AGI steps above $80k."""
        r80 = credits_2027.calculate_total_credits(80_000, "single", 1, 5_000, "hb2306_hd1")
        r90 = credits_2027.calculate_total_credits(90_000, "single", 1, 5_000, "hb2306_hd1")
        assert r80["child_care"] > r90["child_care"]


# ── Spot check integration ────────────────────────────────────────────────────

class TestSpotChecks:
    def test_joint_700k_no_kids_net_tax(self, calculator, act46, hb2306):
        """Joint $700k, 0 deps: HB 2306 +1pp on top brackets should add ~$2,400.

        See ``test_hb2306_top_bracket_joint_700k`` for the std-deduction
        offset rationale.
        """
        std_ded_delta = 16_000 - 8_800  # 2027 joint - 2025 joint std ded
        r_base = calculator.calculate_tax(700_000, act46, "married_filing_jointly", 2)
        r_hb = calculator.calculate_tax(700_000 + std_ded_delta, hb2306, "married_filing_jointly", 2)
        assert r_base["taxable_income"] == pytest.approx(r_hb["taxable_income"], abs=1)
        diff = r_hb["tax_liability"] - r_base["tax_liability"]
        assert diff == pytest.approx(2_400, abs=100)

    def test_hoh_50k_2kids_hb2306_refund(self, calculator, hb2306, credits_2027):
        """HoH $50k, 2 kids under HB 2306: enhanced CDCC should produce net refund."""
        r = calculator.calculate_tax(50_000, hb2306, "head_of_household", 3)
        cr = credits_2027.calculate_total_credits(
            50_000, "head_of_household", 2, r["tax_liability"], "hb2306_hd1"
        )
        net = r["tax_liability"] - cr["total"]
        assert net < 0, f"Expected negative net tax (refund), got {net}"

    def test_hoh_50k_2kids_sb3125_refund(self, calculator, sb3125, credits_2027):
        """HoH $50k, 2 kids under SB 3125: enhanced CDCC should also produce net refund."""
        r = calculator.calculate_tax(50_000, sb3125, "head_of_household", 3)
        cr = credits_2027.calculate_total_credits(
            50_000, "head_of_household", 2, r["tax_liability"], "sb3125_sd1"
        )
        net = r["tax_liability"] - cr["total"]
        assert net < 0, f"Expected negative net tax (refund), got {net}"

    def test_single_300k_no_kids_act46_vs_hb2306(self, calculator, act46, hb2306):
        """Single $300k, 0 deps: HB 2306 +1pp on $225k+ brackets should add ~$694.

        Single std deduction phase-in: $4,400 (2025) → $8,000 (2027). Pass
        the $3,600 delta as extra gross to keep taxable income equal in both
        scenarios. Act 46 single std ded = $4,400, exemption = $1,144,
        so taxable at $300k gross = $294,456.

        At $294,456 taxable: $225k-$275k slice gets +1pp (= +$500),
        $275k-$294,456 slice gets +1pp (= +$194). Total = $694.
        """
        std_ded_delta = 8_000 - 4_400  # 2027 single - 2025 single std ded
        base = calculator.calculate_tax(300_000, act46, "single", 1)
        hb = calculator.calculate_tax(300_000 + std_ded_delta, hb2306, "single", 1)
        assert base["taxable_income"] == pytest.approx(hb["taxable_income"], abs=1)
        diff = hb["tax_liability"] - base["tax_liability"]
        assert diff == pytest.approx(694, abs=10)


# ── Registry / config isolation ───────────────────────────────────────────────

class TestConfigIsolation:
    def test_act46_has_no_credit_scenario(self):
        assert TaxSystemRegistry.get_act46_2025_system().credit_scenario is None

    def test_act46_2027_has_no_credit_scenario(self):
        assert TaxSystemRegistry.get_act46_2027_system().credit_scenario is None

    def test_hb2306_bracket_scenario(self):
        assert TaxSystemRegistry.get_hb2306_hd1_2027_system().bracket_scenario == "hb2306_hd1"

    def test_sb3125_bracket_scenario(self):
        assert TaxSystemRegistry.get_sb3125_sd1_2027_system().bracket_scenario == "sb3125_sd1"

    def test_sb3125_credit_scenario(self):
        assert TaxSystemRegistry.get_sb3125_sd1_2027_system().credit_scenario == "sb3125_sd1"

    def test_unknown_credit_scenario_falls_back_to_current_law(self, credits_2025):
        """An unrecognized credit_scenario should fall back to current-law CDCC."""
        r_none = credits_2025.calculate_total_credits(50_000, "single", 1, 3_000, None)
        r_unknown = credits_2025.calculate_total_credits(50_000, "single", 1, 3_000, "nonexistent")
        assert r_none["child_care"] == r_unknown["child_care"]
