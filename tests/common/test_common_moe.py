"""Verify common.moe directly (not via census_forecaster shim)."""
from __future__ import annotations

import pytest
from common.moe import ACS_MOE_Z, ci_from_se, combine_se, moe_sum, moe_to_se, se_to_moe


class TestMoeDirectImport:
    def test_acs_moe_z(self):
        assert ACS_MOE_Z == pytest.approx(1.645)

    def test_moe_to_se_roundtrip(self):
        se = moe_to_se(100.0)
        assert se_to_moe(se) == pytest.approx(100.0)

    def test_combine_se(self):
        # combine_se(3, 4) = 5 (Pythagorean)
        assert combine_se(3.0, 4.0) == pytest.approx(5.0)

    def test_ci_from_se_symmetric(self):
        lo, hi = ci_from_se(100.0, 10.0)
        assert hi - lo == pytest.approx(2 * ACS_MOE_Z * 10.0)

    def test_moe_sum(self):
        result = moe_sum([30.0, 40.0])
        assert result == pytest.approx(50.0)
