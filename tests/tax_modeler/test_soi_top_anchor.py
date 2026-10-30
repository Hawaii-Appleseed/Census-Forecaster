"""Tests for soi_top_anchor: SOI Table 1.4-anchored $1M+ synthesis."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from tax_modeler.calibration.soi_top_anchor import (
    HIGH_INCOME_STATUS_SHARES,
    SOITierAnchor,
    load_soi_top_anchors,
    synthesize_top_filers_from_soi,
)


@pytest.fixture(scope="module")
def soi_csv_present(repo_data_dir: Path) -> Path:
    csv = repo_data_dir / "irs_soi" / "national_soi_table_1_4_2022.csv"
    if not csv.exists():
        pytest.skip(
            f"SOI Table 1.4 CSV not present at {csv}. "
            "Run `python -m tax_modeler.scripts.parse_soi_table_14`."
        )
    return csv


@pytest.fixture(scope="module")
def anchors(soi_csv_present: Path) -> list[SOITierAnchor]:
    return load_soi_top_anchors(year=2022, csv_path=soi_csv_present)


def test_load_anchors_returns_five_tiers(anchors: list[SOITierAnchor]):
    # $1M-$1.5M, $1.5M-$2M, $2M-$5M, $5M-$10M, $10M+
    assert len(anchors) == 5
    los = [a.agi_lo for a in anchors]
    assert los == [1_000_000, 1_500_000, 2_000_000, 5_000_000, 10_000_000]


def test_anchor_shares_are_reasonable(anchors: list[SOITierAnchor]):
    # CG share monotonically increases with tier (more passive income at top)
    cg_shares = [a.capgain_share for a in anchors]
    assert cg_shares == sorted(cg_shares), f"CG shares should be monotonic: {cg_shares}"
    # Wage share monotonically decreases (less wage-driven at top)
    wage_shares = [a.wages_share for a in anchors]
    assert wage_shares == sorted(wage_shares, reverse=True), \
        f"Wage shares should decrease: {wage_shares}"
    # All shares between 0 and 1
    for a in anchors:
        for s in (a.wages_share, a.capgain_share, a.business_share,
                  a.dividends_share, a.interest_share):
            assert 0 <= s <= 1, f"Share out of range: {s}"


def _make_synthetic_units(n: int = 1000) -> pd.DataFrame:
    """Minimal frame with a few $1M+ rows the synthesizer should replace."""
    rng = np.random.default_rng(42)
    base = pd.DataFrame({
        "income":          rng.uniform(50_000, 300_000, size=n),
        "agi":             rng.uniform(50_000, 300_000, size=n),
        "filing_status":   rng.choice(
            ["single", "married_filing_jointly", "head_of_household"],
            size=n,
        ),
        "weight":          np.full(n, 100.0),
        "hi_tax_liability": rng.uniform(1_000, 30_000, size=n),
        "hi_state_tax":    rng.uniform(1_000, 30_000, size=n),
        "num_adults":      rng.choice([1, 2], size=n),
    })
    # Stuff a handful of pre-existing $1M+ filers in
    base.loc[:9, "income"] = 2_500_000
    base.loc[:9, "agi"] = 2_500_000
    base.loc[:9, "weight"] = 50.0
    return base


def test_synthesize_replaces_existing_top_filers(anchors):
    units = _make_synthetic_units()
    pre_count = float(units.loc[units["agi"] >= 1_000_000, "weight"].sum())
    assert pre_count > 0  # 10 rows × 50 weight = 500

    target = 2_700
    out = synthesize_top_filers_from_soi(
        units,
        target_filer_count=target,
        soi_anchors=anchors,
    )
    post_count = float(out.loc[out["agi"] >= 1_000_000, "weight"].sum())
    # Existing $1M+ weights are zeroed; synthetic adds the target count
    assert abs(post_count - target) < 5, (
        f"Post $1M+ count {post_count:.1f} should match target {target}"
    )


def test_filing_status_mix_matches_dotax(anchors):
    units = _make_synthetic_units()
    out = synthesize_top_filers_from_soi(
        units, target_filer_count=2_700, soi_anchors=anchors,
    )
    synth = out[out["is_synthetic_ultra_high"].fillna(False)]
    total_synth = synth["weight"].sum()
    for status, expected_share in HIGH_INCOME_STATUS_SHARES.items():
        actual = synth.loc[synth["filing_status"] == status, "weight"].sum() / total_synth
        assert abs(actual - expected_share) < 0.005, (
            f"{status}: expected {expected_share:.2%}, got {actual:.2%}"
        )


def test_cg_share_decreases_with_hawaii_adjustment(anchors):
    units = _make_synthetic_units()
    out_full = synthesize_top_filers_from_soi(
        units, target_filer_count=2_700, soi_anchors=anchors,
        hawaii_capgain_adjustment=1.0,
    )
    out_adj = synthesize_top_filers_from_soi(
        units, target_filer_count=2_700, soi_anchors=anchors,
        hawaii_capgain_adjustment=0.95,
    )
    full_avg = (
        (out_full.loc[out_full["is_synthetic_ultra_high"].fillna(False), "synthetic_cg_share"]
         * out_full.loc[out_full["is_synthetic_ultra_high"].fillna(False), "weight"]).sum()
        / out_full.loc[out_full["is_synthetic_ultra_high"].fillna(False), "weight"].sum()
    )
    adj_avg = (
        (out_adj.loc[out_adj["is_synthetic_ultra_high"].fillna(False), "synthetic_cg_share"]
         * out_adj.loc[out_adj["is_synthetic_ultra_high"].fillna(False), "weight"]).sum()
        / out_adj.loc[out_adj["is_synthetic_ultra_high"].fillna(False), "weight"].sum()
    )
    assert adj_avg < full_avg
    assert abs(adj_avg / full_avg - 0.95) < 0.001


def test_tier_distribution_proportional_to_soi(anchors):
    """Synthetic filer count per tier should match SOI national tier shares."""
    units = _make_synthetic_units()
    target = 2_700
    out = synthesize_top_filers_from_soi(
        units, target_filer_count=target, soi_anchors=anchors,
    )
    synth = out[out["is_synthetic_ultra_high"].fillna(False)]

    total_national = sum(a.n_returns_national for a in anchors)
    for a in anchors:
        expected_share = a.n_returns_national / total_national
        expected_count = target * expected_share
        actual = synth.loc[synth["synthetic_tier_lo"] == a.agi_lo, "weight"].sum()
        assert abs(actual - expected_count) < 5, (
            f"Tier ${a.agi_lo/1e6:.1f}M: expected {expected_count:.0f}, got {actual:.1f}"
        )


def test_cbo_aging_grows_tier_avg_agi(anchors, repo_data_dir):
    """When target_year + cbo_vintage are passed, tier avg AGI is aged forward."""
    cbo_csv = repo_data_dir / "cbo" / "cbo_components_2025-01.csv"
    if not cbo_csv.exists():
        pytest.skip(f"CBO rates CSV not present at {cbo_csv}")

    units = _make_synthetic_units()
    out_unaged = synthesize_top_filers_from_soi(
        units, target_filer_count=2_700, soi_anchors=anchors,
    )
    out_aged = synthesize_top_filers_from_soi(
        units, target_filer_count=2_700, soi_anchors=anchors,
        target_year=2027, cbo_vintage="2025-01",
        cbo_hawaii_factors={c: 1.0 for c in [
            "wages", "business", "capital_gains", "dividends",
            "interest", "retirement", "other",
        ]},
    )
    # Aged tier average AGI should be ~25% higher (5-year aging)
    unaged_top = out_unaged[out_unaged["is_synthetic_ultra_high"].fillna(False)]
    aged_top = out_aged[out_aged["is_synthetic_ultra_high"].fillna(False)]
    unaged_avg = (unaged_top["agi"] * unaged_top["weight"]).sum() / unaged_top["weight"].sum()
    aged_avg = (aged_top["agi"] * aged_top["weight"]).sum() / aged_top["weight"].sum()
    growth = aged_avg / unaged_avg - 1
    assert 0.20 < growth < 0.40, (
        f"CBO-aged tier avg AGI growth {growth:.1%} outside 20-40% sanity band"
    )


def test_cbo_aging_increases_capgain_share(anchors, repo_data_dir):
    """CG growing faster than wages should shift the tier CG share upward."""
    cbo_csv = repo_data_dir / "cbo" / "cbo_components_2025-01.csv"
    if not cbo_csv.exists():
        pytest.skip(f"CBO rates CSV not present at {cbo_csv}")

    units = _make_synthetic_units()
    out_unaged = synthesize_top_filers_from_soi(
        units, target_filer_count=2_700, soi_anchors=anchors,
        hawaii_capgain_adjustment=1.0,
    )
    out_aged = synthesize_top_filers_from_soi(
        units, target_filer_count=2_700, soi_anchors=anchors,
        hawaii_capgain_adjustment=1.0,
        target_year=2027, cbo_vintage="2025-01",
        cbo_hawaii_factors={c: 1.0 for c in [
            "wages", "business", "capital_gains", "dividends",
            "interest", "retirement", "other",
        ]},
    )
    unaged_cg = (
        (out_unaged["synthetic_cg_share"] * out_unaged["weight"])
        [out_unaged["is_synthetic_ultra_high"].fillna(False)].sum()
        / out_unaged.loc[out_unaged["is_synthetic_ultra_high"].fillna(False), "weight"].sum()
    )
    aged_cg = (
        (out_aged["synthetic_cg_share"] * out_aged["weight"])
        [out_aged["is_synthetic_ultra_high"].fillna(False)].sum()
        / out_aged.loc[out_aged["is_synthetic_ultra_high"].fillna(False), "weight"].sum()
    )
    assert aged_cg > unaged_cg, (
        f"CG share should rise after aging: unaged={unaged_cg:.3f}, "
        f"aged={aged_cg:.3f}"
    )
