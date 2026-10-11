"""
Multi-Stage IPF (Iterative Proportional Fitting) Calibration Orchestrator

Implements simultaneous calibration to multiple targets using IPF/Raking algorithm.
This approach handles overlapping targets better than pure sequential calibration.

IPF Algorithm:
1. Define multiple target distributions (margins)
2. Iteratively adjust to each margin while holding others constant
3. Repeat until convergence (all margins within tolerance)

References:
- Deming & Stephan (1940) - Original IPF algorithm
- TPC/CBO microsimulation models - Multi-dimensional calibration
- Creedy (2003) - Survey reweighting with IPF
"""

from __future__ import annotations

import pandas as pd
import numpy as np
import logging
from typing import Dict, Tuple, List, Optional
from dataclasses import dataclass

from tax_modeler.calibration import dotax_base
from tax_modeler.calibration.report import (
    CalibrationReport,
    ClipEvent,
    DroppedBin,
    MarginResidual,
    WeightDiagnostics,
    bracket_label,
    warn_once_on_year_mismatch,
)

logger = logging.getLogger(__name__)


@dataclass
class CalibrationTarget:
    """Represents a single calibration target (margin)."""
    name: str
    dimension: str  # 'weight', 'agi', 'filing_status', 'tax'
    targets: Dict[Tuple, float]  # Mapping of bracket/category to target value
    weight: float = 1.0  # Relative importance (for prioritization)


class IPFCalibrationOrchestrator:
    """
    Multi-stage IPF calibrator for tax microsimulation.
    
    Handles multiple overlapping calibration targets:
    1. Filer counts by AGI bracket
    2. Tax totals by AGI bracket
    3. Filing status distribution
    4. Income distribution within brackets
    
    Uses IPF/raking to iteratively adjust weights and other dimensions
    until all targets converge simultaneously.
    """
    
    # DOTAX targets: Table A-8 (tax, $M; filers) and Table 4 (filing status),
    # newest edition on file. One copy in ``dotax_base``.
    DOTAX_TAX_TARGETS = dotax_base.tax_targets_M()
    DOTAX_FILER_TARGETS = dotax_base.filer_targets()
    DOTAX_FILING_STATUS_TARGETS = dotax_base.status_targets()

    def __init__(self, 
                 max_iterations: int = 20,
                 tolerance: float = 0.01,
                 convergence_criterion: str = 'chi_squared'):
        """
        Initialize IPF calibration orchestrator.
        
        Args:
            max_iterations: Maximum IPF iterations
            tolerance: Convergence tolerance (1% default for IPF)
            convergence_criterion: 'chi_squared' or 'max_deviation'
        """
        self.max_iterations = max_iterations
        self.tolerance = tolerance
        self.convergence_criterion = convergence_criterion
        self.calibration_history = []
        
    def calculate_chi_squared(self, 
                              current: Dict[Tuple, float],
                              target: Dict[Tuple, float]) -> float:
        """
        Calculate chi-squared statistic for convergence testing.
        
        χ² = Σ ((current - target)² / target)
        """
        chi_sq = 0
        for key in target:
            if key in current and target[key] > 0:
                chi_sq += (current[key] - target[key])**2 / target[key]
        return chi_sq
    
    def calculate_max_deviation(self,
                                 current: Dict[Tuple, float],
                                 target: Dict[Tuple, float]) -> float:
        """
        Calculate maximum relative deviation for convergence testing.
        
        max_dev = max(|current - target| / target)
        """
        max_dev = 0
        for key in target:
            if key in current and target[key] > 0:
                dev = abs(current[key] - target[key]) / target[key]
                max_dev = max(max_dev, dev)
        return max_dev
    
    def ipf_adjust_weights_to_filer_counts(self,
                                           df: pd.DataFrame,
                                           targets: Dict[Tuple, float],
                                           agi_col: str = 'agi',
                                           weight_col: str = 'weight') -> pd.DataFrame:
        """
        IPF step: Adjust weights to match filer count targets by AGI bracket.
        
        This is one "margin" in the IPF algorithm.
        """
        result = df.copy()
        
        for (min_agi, max_agi), target_count in targets.items():
            mask = (result[agi_col] >= min_agi) & (result[agi_col] < max_agi)
            current_count = result.loc[mask, weight_col].sum()
            
            if current_count > 0 and abs(current_count - target_count) > 1:
                adjustment_factor = target_count / current_count
                result.loc[mask, weight_col] *= adjustment_factor
                logger.debug(f"  Adjusted ${min_agi//1000}k-${max_agi//1000}k: "
                           f"{current_count:>8,.0f} → {target_count:>8,.0f} (×{adjustment_factor:.4f})")
        
        return result
    
    def ipf_adjust_weights_to_tax_totals(self,
                                         df: pd.DataFrame,
                                         targets: Dict[Tuple, float],
                                         agi_col: str = 'agi',
                                         tax_col: str = 'hi_state_tax',
                                         weight_col: str = 'weight',
                                         max_adjustment: float = 1.5,
                                         events: Optional[List[ClipEvent]] = None,
                                         iteration: Optional[int] = None) -> pd.DataFrame:
        """IPF step: Adjust weights to match tax total targets by AGI bracket."""
        return self.ipf_adjust_weights_to_weighted_sum(
            df, targets, value_col=tax_col, agi_col=agi_col,
            weight_col=weight_col, max_adjustment=max_adjustment,
            scale=1_000_000.0,  # column is in dollars; targets are $M
            events=events, stage="tax_total_step", iteration=iteration,
        )

    def ipf_adjust_weights_to_weighted_sum(
        self,
        df: pd.DataFrame,
        targets: Dict[Tuple, float],
        value_col: str,
        agi_col: str = 'agi',
        weight_col: str = 'weight',
        max_adjustment: float = 1.5,
        scale: float = 1_000_000.0,
        events: Optional[List[ClipEvent]] = None,
        stage: str = "weighted_sum_step",
        iteration: Optional[int] = None,
    ) -> pd.DataFrame:
        """Generic IPF step: scale weights per AGI bracket so that
        ``Σ(weight × value_col) / scale`` matches the per-bracket target.

        Used by both the DOTAX tax-total step (value_col='hi_state_tax')
        and the SOI AGI-total step (value_col='agi').

        When ``events`` is a list, every factor the cap binds on is appended
        to it as a :class:`ClipEvent` (the factor the bin asked for and the
        one applied) — the calibration report's record of damping.
        """
        result = df.copy()
        for (min_agi, max_agi), target in targets.items():
            mask = (result[agi_col] >= min_agi) & (result[agi_col] < max_agi)
            current = (result.loc[mask, value_col] * result.loc[mask, weight_col]).sum() / scale
            if current > 0 and abs(current - target) > 0.1:
                requested = target / current
                factor = float(np.clip(requested, 1 / max_adjustment, max_adjustment))
                if events is not None and factor != requested:
                    events.append(ClipEvent(
                        stage=stage, key=bracket_label(min_agi, max_agi),
                        requested=float(requested), applied=factor,
                        bounds=(1 / max_adjustment, max_adjustment),
                        iteration=iteration,
                    ))
                result.loc[mask, weight_col] *= factor
        return result
    
    def ipf_adjust_weights_to_filing_status(self,
                                            df: pd.DataFrame,
                                            targets: Dict[str, float],
                                            filing_status_col: str = 'filing_status',
                                            weight_col: str = 'weight') -> pd.DataFrame:
        """
        IPF step: Adjust weights to match filing status distribution.
        
        This is the third "margin" in the IPF algorithm.
        """
        result = df.copy()
        
        for status, target_count in targets.items():
            mask = result[filing_status_col] == status
            current_count = result.loc[mask, weight_col].sum()
            
            if current_count > 0 and abs(current_count - target_count) > 1:
                adjustment_factor = target_count / current_count
                result.loc[mask, weight_col] *= adjustment_factor
                logger.debug(f"  Adjusted {status}: "
                           f"{current_count:>8,.0f} → {target_count:>8,.0f} (×{adjustment_factor:.4f})")
        
        return result
    
    def calibrate_ipf(self,
                      df: pd.DataFrame,
                      calibrate_filer_counts: bool = True,
                      calibrate_tax_totals: bool = True,
                      calibrate_filing_status: bool = True) -> pd.DataFrame:
        """
        Main IPF calibration loop.
        
        Iteratively adjusts to each margin until convergence:
        1. Filer counts by AGI bracket
        2. Tax totals by AGI bracket
        3. Filing status distribution
        
        Args:
            df: Input DataFrame with tax units
            calibrate_filer_counts: Whether to calibrate to filer count targets
            calibrate_tax_totals: Whether to calibrate to tax total targets
            calibrate_filing_status: Whether to calibrate to filing status targets
            
        Returns:
            Calibrated DataFrame
        """
        logger.info("=" * 80)
        logger.info("MULTI-STAGE IPF CALIBRATION")
        logger.info("=" * 80)
        logger.info(f"Max iterations: {self.max_iterations}")
        logger.info(f"Tolerance: {self.tolerance * 100}%")
        logger.info(f"Convergence criterion: {self.convergence_criterion}")
        
        result = df.copy()
        
        # Initial validation
        logger.info("\nInitial state:")
        filer_dev = self._validate_filer_counts(result)
        tax_dev = self._validate_tax_totals(result)
        status_dev = self._validate_filing_status(result)
        
        # IPF iteration loop
        for iteration in range(self.max_iterations):
            logger.info(f"\n{'='*80}")
            logger.info(f"IPF ITERATION {iteration + 1}/{self.max_iterations}")
            logger.info(f"{'='*80}")
            
            # Store previous weights for convergence check
            prev_weights = result['weight'].copy()
            
            # Margin 1: Filer counts by AGI bracket
            if calibrate_filer_counts:
                logger.info("\n1. Adjusting to filer count targets...")
                result = self.ipf_adjust_weights_to_filer_counts(
                    result, 
                    self.DOTAX_FILER_TARGETS
                )
            
            # Margin 2: Tax totals by AGI bracket  
            if calibrate_tax_totals:
                logger.info("\n2. Adjusting to tax total targets...")
                result = self.ipf_adjust_weights_to_tax_totals(
                    result,
                    self.DOTAX_TAX_TARGETS
                )
            
            # Margin 3: Filing status distribution
            if calibrate_filing_status:
                logger.info("\n3. Adjusting to filing status targets...")
                result = self.ipf_adjust_weights_to_filing_status(
                    result,
                    self.DOTAX_FILING_STATUS_TARGETS
                )
            
            # Check convergence
            weight_change = abs(result['weight'] - prev_weights).max() / prev_weights.mean()
            
            logger.info(f"\nMax weight change: {weight_change:.4f}")
            
            # Validate current state
            filer_dev = self._validate_filer_counts(result)
            tax_dev = self._validate_tax_totals(result)
            status_dev = self._validate_filing_status(result)
            
            # Calculate convergence metrics
            if self.convergence_criterion == 'chi_squared':
                conv_metric = filer_dev['chi_squared']
                logger.info(f"Chi-squared: {conv_metric:.4f}")
            else:
                conv_metric = filer_dev['max_deviation']
                logger.info(f"Max deviation: {conv_metric:.4f}")
            
            # Check for convergence
            if weight_change < self.tolerance:
                logger.info(f"\n✅ IPF CONVERGED in {iteration + 1} iterations!")
                logger.info(f"   Weight change: {weight_change:.4f} < {self.tolerance}")
                break
        else:
            logger.warning(f"\n⚠️  IPF reached max iterations ({self.max_iterations})")
            logger.warning(f"   Final weight change: {weight_change:.4f}")
        
        # Final validation report
        logger.info("\n" + "="*80)
        logger.info("FINAL IPF CALIBRATION RESULTS")
        logger.info("="*80)
        
        self._print_validation_summary(result)
        
        return result
    
    # ------------------------------------------------------------------
    # rake()-backed calibration
    # ------------------------------------------------------------------

    def calibrate_via_rake(
        self,
        df: pd.DataFrame,
        calibrate_filer_counts: bool = True,
        calibrate_tax_totals: bool = True,
        calibrate_filing_status: bool = True,
        calibrate_soi_agi: bool = True,
        soi_agi_targets_M: Optional[Dict[Tuple[float, float], float]] = None,
        soi_universe_scale: float = 1.0,
        max_outer_iters: int = 30,
        outer_tolerance: float = 0.01,
        tax_total_max_adjustment: float = 5.0,
        soi_max_adjustment: float = 1.10,
        skip_synthesis_bin: bool = True,
        infeasibility_threshold: float = 0.25,
        return_report: bool = False,
        soi_source_year: Optional[int] = None,
        method: str = "joint",
        bounds: Tuple[float, float] = (0.2, 5.0),
        tax_value_col: Optional[str] = None,
        joint_tolerance: float = 1e-3,
        agi_check_targets_M: Optional[Dict[Tuple[float, float], float]] = None,
        agi_check_source: str = "DOTAX Table A-1 AGI ($M)",
        agi_check_year: Optional[int] = None,
    ) -> pd.DataFrame | Tuple[pd.DataFrame, CalibrationReport]:
        """Joint multi-pass IPF calibration.

        ``method="joint"`` (default since October 2026) solves every margin in
        one generalized-raking system (:meth:`calibrate_joint`); the
        parameters below the ``soi_source_year`` line belong to it. The
        legacy damped sequential scheme is ``method="sequential"`` and is
        documented in the rest of this docstring.

        Wraps three margins (filer counts by AGI bracket, filing-status
        distribution, tax totals by AGI bracket) in an outer iteration loop
        so each margin's adjustment is reconciled against the others until
        joint convergence (or ``max_outer_iters`` reached).

        Inner step (per outer iteration):
          1. Canonical pums_estimator rake() jointly solves the two count
             margins (filer-count + filing-status) via Deming-Stephan IPF.
          2. ``ipf_adjust_weights_to_tax_totals`` scales weights per AGI
             bracket to match DOTAX Table A8 tax-liability targets.

        Outer iteration re-runs the inner sequence so the tax-total scaling
        in step 2 (which breaks the count match from step 1) is reconciled
        by the next iteration's count rake. Convergence is the maximum
        relative deviation across all three margins.

        Parameters
        ----------
        df:
            Input DataFrame with columns ``weight``, ``agi``, ``filing_status``,
            and (when ``calibrate_tax_totals``) ``hi_state_tax``.
        calibrate_filer_counts:
            Rake to DOTAX filer-count targets by AGI bracket.
        calibrate_tax_totals:
            Scale weights to DOTAX tax-total targets by AGI bracket.
        calibrate_filing_status:
            Rake to DOTAX filing-status distribution.
        max_outer_iters:
            Cap on outer iterations. Convergence is normally hit in 10-25.
        outer_tolerance:
            Joint convergence threshold (max relative deviation across all
            three margins). Default 1%.
        tax_total_max_adjustment:
            Per-bin scaling cap for ONE tax-total step (damping: each outer
            iteration moves a bin at most this far toward its tax target so
            the count rake can keep up). Effective amplification across N
            iterations is up to ``cap^N``. The default is 5.0; the docstring
            said 1.15 until October 2026 while the code said 5.0. Every
            binding cap is a ``ClipEvent`` in the report.
        skip_synthesis_bin:
            When True, omits the $1M+ bin from tax-total raking. PUMS
            topcodes top incomes, so this bin is unreachable pre-synthesis;
            including it would consume cap headroom and cause
            non-convergence. Synthesis fills this bin downstream.
        infeasibility_threshold:
            Bins are dropped from the tax-total margin if their target
            implies a per-filer tax > this fraction higher than the bin's
            actual per-filer tax distribution allows. Prevents the rake
            from oscillating against impossible targets (e.g., DOTAX bin
            tax $21M with count target 64K but PUMS-derived per-filer tax
            of only ~$200 → max feasible $13M). Default 0.50 (50%).
        calibrate_soi_agi:
            Also rake against IRS SOI Hawaii Table 2 AGI-total targets
            (per SOI bin). DOTAX provides only **tax** anchors per bin;
            SOI provides **income** anchors that constrain the income
            distribution underlying those taxes. SOI uses 6 coarser bins
            ($25K boundaries vs DOTAX $10K/$50K).
        soi_agi_targets_M:
            Dict keyed by (lo, hi) tuple → target AGI in $millions per
            SOI bin. Required when ``calibrate_soi_agi`` is True. Build
            via ``irs_soi_state_targets.build_soi_calibration_margins``.
        soi_universe_scale:
            Multiplier applied to SOI targets to match the calibrated
            universe. SOI Hawaii has 674,660 returns; DOTAX has 618,423
            (SOI counts federal-only filers DOTAX excludes). Pass
            ``DOTAX_total / SOI_total ≈ 0.917`` to scale SOI AGI down.
        soi_max_adjustment:
            Per-bin scaling cap for the SOI AGI step. SOI AGI is more
            achievable than DOTAX tax (PUMS records have AGI directly),
            so a tighter cap is appropriate. Default 1.10x (the docstring
            said 2.0 until October 2026 while the code said 1.10).
        return_report:
            When True, return ``(calibrated_df, CalibrationReport)``. The
            report is also attached as ``calibrated_df.attrs["calibration_report"]``
            (a dict) either way.
        soi_source_year:
            Tax year of the SOI AGI margin, recorded in the report next to
            the DOTAX margins' ``dotax_base.BASE_YEAR``; a one-time warning
            fires when they differ. Defaults to
            ``irs_soi_state_targets.SOI_TAX_YEAR``.
        """
        if method == "joint":
            return self.calibrate_joint(
                df,
                calibrate_filer_counts=calibrate_filer_counts,
                calibrate_tax_totals=calibrate_tax_totals,
                calibrate_filing_status=calibrate_filing_status,
                skip_synthesis_bin=skip_synthesis_bin,
                bounds=bounds,
                tax_value_col=tax_value_col,
                tolerance=joint_tolerance,
                soi_agi_targets_M=soi_agi_targets_M if calibrate_soi_agi else None,
                soi_universe_scale=soi_universe_scale,
                soi_source_year=soi_source_year,
                agi_check_targets_M=agi_check_targets_M,
                agi_check_source=agi_check_source,
                agi_check_year=agi_check_year,
                return_report=return_report,
            )
        if method != "sequential":
            raise ValueError(f"method must be 'joint' or 'sequential', got {method!r}")
        from pums_estimator.estimation.rake import rake as _rake  # lazy cross-package import

        result = df.copy()
        margins: Dict[str, Dict[str, float]] = {}
        temp_cols: List[str] = []
        clip_events: List[ClipEvent] = []
        dropped: List[DroppedBin] = []
        report_warnings: List[str] = []
        weights_before = WeightDiagnostics.from_weights(df["weight"].to_numpy(dtype=float))

        # --- filer-count margin ------------------------------------------
        if calibrate_filer_counts:
            bracket_keys = list(self.DOTAX_FILER_TARGETS.keys())

            def _bracket_idx(agi: float) -> int:
                for i, (lo, hi) in enumerate(bracket_keys):
                    if lo <= agi < hi:
                        return i
                return len(bracket_keys) - 1

            result["_bracket_idx"] = result["agi"].map(_bracket_idx)
            temp_cols.append("_bracket_idx")
            margins["_bracket_idx"] = {
                str(i): count
                for i, count in enumerate(self.DOTAX_FILER_TARGETS.values())
            }

        # --- filing-status margin ----------------------------------------
        _FS_CODE: Dict[str, int] = {
            "single": 0,
            "married_filing_jointly": 1,
            "head_of_household": 2,
            "married_filing_separately": 3,
        }
        if calibrate_filing_status:
            result["_fs_code"] = result["filing_status"].map(_FS_CODE)
            temp_cols.append("_fs_code")
            margins["_fs_code"] = {
                str(code): self.DOTAX_FILING_STATUS_TARGETS[fs]
                for fs, code in _FS_CODE.items()
            }

        # --- prepare tax-total targets (skip synthesis-handled bin) ------
        if calibrate_tax_totals and skip_synthesis_bin:
            tax_targets = {
                k: v for k, v in self.DOTAX_TAX_TARGETS.items()
                if k[0] < 1_000_000
            }
        else:
            tax_targets = dict(self.DOTAX_TAX_TARGETS)

        # --- drop infeasible bins from tax-total margin ------------------
        # If hitting the bin's tax target would require multiplying the
        # per-filer tax by more than (1 + infeasibility_threshold) over what
        # the count-target population can produce, the constraint is
        # arithmetically inconsistent (PUMS-derived per-filer tax doesn't
        # match DOTAX). Skip raking it so the multi-pass IPF can converge.
        infeasible_bins: List[Tuple[float, float]] = []
        if calibrate_tax_totals:
            for (lo, hi), tgt in list(tax_targets.items()):
                ftgt = self.DOTAX_FILER_TARGETS.get((lo, hi), 0)
                if ftgt <= 0:
                    continue
                mask = (result["agi"] >= lo) & (result["agi"] < hi)
                # Per-filer tax that the bin's PUMS population would have
                # IF its weight summed to the count target (no scaling).
                pop_tax_m = (
                    result.loc[mask, "hi_state_tax"] * result.loc[mask, "weight"]
                ).sum() / 1e6
                pop_count = result.loc[mask, "weight"].sum()
                if pop_count <= 0:
                    continue
                feasible_tax_m = pop_tax_m * (ftgt / pop_count)  # at count target
                if feasible_tax_m <= 0:
                    # Negative-tax bins (EITC-driven); rake can't fix.
                    infeasible_bins.append((lo, hi))
                    dropped.append(DroppedBin(
                        stage="tax_total_margin", key=bracket_label(lo, hi),
                        reason="non-positive tax at the count target (credits exceed tax)",
                        target=float(tgt), feasible=float(feasible_tax_m),
                    ))
                    continue
                if tgt > feasible_tax_m * (1 + infeasibility_threshold):
                    infeasible_bins.append((lo, hi))
                    dropped.append(DroppedBin(
                        stage="tax_total_margin", key=bracket_label(lo, hi),
                        reason=(f"target exceeds {1 + infeasibility_threshold:.2f}x the tax "
                                "feasible at the count target"),
                        target=float(tgt), feasible=float(feasible_tax_m),
                    ))
            for k in infeasible_bins:
                del tax_targets[k]
            if infeasible_bins:
                logger.info(
                    "Joint IPF: skipping %d infeasible tax-total bins: %s",
                    len(infeasible_bins),
                    [f"${lo//1000}k-${('inf' if hi==float('inf') else hi//1000)}k"
                     for lo, hi in infeasible_bins],
                )

        # --- inner-loop helpers ------------------------------------------
        class _TaxRecord:
            __slots__ = ("weight", "variables")
            def __init__(self, w: float, v: Dict) -> None:
                self.weight = w
                self.variables = v

        class _Controls:
            __slots__ = ("margins",)
            def __init__(self, m: Dict) -> None:
                self.margins = m

        margin_cols = list(margins)
        var_arrays = (
            {col: result[col].to_numpy() for col in margin_cols}
            if margin_cols else {}
        )

        def _run_count_rake(weights_in: np.ndarray) -> np.ndarray:
            if not margin_cols:
                return weights_in
            records = [
                _TaxRecord(
                    float(weights_in[i]),
                    {col: float(var_arrays[col][i]) for col in margin_cols},
                )
                for i in range(len(weights_in))
            ]
            return np.asarray(_rake(
                records, _Controls(margins),
                max_iter=self.max_iterations, tol=self.tolerance,
            ), dtype=float)

        def _max_dev_count(d: pd.DataFrame) -> float:
            if not calibrate_filer_counts:
                return 0.0
            m = 0.0
            for (lo, hi), tgt in self.DOTAX_FILER_TARGETS.items():
                # Skip $1M+ — PUMS topcodes; synthesis fills this bin.
                if skip_synthesis_bin and lo >= 1_000_000:
                    continue
                cur = d.loc[(d["agi"] >= lo) & (d["agi"] < hi), "weight"].sum()
                if tgt > 0:
                    m = max(m, abs(cur - tgt) / tgt)
            return m

        def _max_dev_fs(d: pd.DataFrame) -> float:
            if not calibrate_filing_status:
                return 0.0
            m = 0.0
            for fs, tgt in self.DOTAX_FILING_STATUS_TARGETS.items():
                cur = d.loc[d["filing_status"] == fs, "weight"].sum()
                if tgt > 0:
                    m = max(m, abs(cur - tgt) / tgt)
            return m

        def _max_dev_tax(d: pd.DataFrame) -> float:
            if not calibrate_tax_totals:
                return 0.0
            m = 0.0
            for (lo, hi), tgt in tax_targets.items():
                mask = (d["agi"] >= lo) & (d["agi"] < hi)
                cur = (d.loc[mask, "hi_state_tax"] * d.loc[mask, "weight"]).sum() / 1e6
                # Skip negative-tax bottom bins (EITC > tax; rake can't fix).
                if tgt > 0.5 and cur > 0:
                    m = max(m, abs(cur - tgt) / tgt)
            return m

        # --- SOI AGI targets (scaled to DOTAX universe) ------------------
        # Skip the SOI top bin ($200K+) — synthesis fills the $1M+ tier
        # with target tax of $663M, which dominates this SOI bin's AGI.
        # Including it would force the rake to fight synthesis.
        soi_targets_scaled: Dict[Tuple[float, float], float] = {}
        if calibrate_soi_agi and soi_agi_targets_M:
            for (lo, hi), tgt in soi_agi_targets_M.items():
                if skip_synthesis_bin and lo >= 200_000:
                    continue
                soi_targets_scaled[(lo, hi)] = tgt * soi_universe_scale

        def _max_dev_soi_agi(d: pd.DataFrame) -> float:
            if not calibrate_soi_agi or not soi_targets_scaled:
                return 0.0
            m = 0.0
            for (lo, hi), tgt in soi_targets_scaled.items():
                mask = (d["agi"] >= lo) & (d["agi"] < hi)
                cur = (d.loc[mask, "agi"] * d.loc[mask, "weight"]).sum() / 1e6
                if tgt > 0.5 and cur > 0:
                    m = max(m, abs(cur - tgt) / tgt)
            return m

        # --- outer iteration ---------------------------------------------
        initial_weight = float(df["weight"].sum())
        history: List[Tuple[int, float, float, float]] = []
        converged = False
        convergence_mode = "max_iterations"
        outer = -1
        last_max_dev: Optional[float] = None
        STALE_LIMIT = 3  # iterations with no meaningful improvement → fixed point
        STALE_TOL = 1e-4
        stale_count = 0
        for outer in range(max_outer_iters):
            # Step 1: count rake (filer-count + filing-status, jointly)
            if margin_cols:
                result["weight"] = _run_count_rake(
                    result["weight"].to_numpy(dtype=float)
                )

            # Step 2: tax-total scaling (DOTAX bins)
            if calibrate_tax_totals:
                result = self.ipf_adjust_weights_to_tax_totals(
                    result, tax_targets,
                    max_adjustment=tax_total_max_adjustment,
                    events=clip_events, iteration=outer + 1,
                )

            # Step 3: SOI AGI-total scaling (SOI bins)
            if calibrate_soi_agi and soi_targets_scaled:
                result = self.ipf_adjust_weights_to_weighted_sum(
                    result, soi_targets_scaled,
                    value_col="agi",
                    max_adjustment=soi_max_adjustment,
                    events=clip_events, stage="soi_agi_step", iteration=outer + 1,
                )

            # Step 4: convergence check
            fc_d = _max_dev_count(result)
            fs_d = _max_dev_fs(result)
            tt_d = _max_dev_tax(result)
            soi_d = _max_dev_soi_agi(result)
            history.append((outer + 1, fc_d, fs_d, tt_d))
            max_dev = max(fc_d, fs_d, tt_d, soi_d)
            last_max_dev = max_dev
            logger.info(
                "Joint IPF outer iter %d: max_dev=%.4f "
                "(filer_count=%.4f, filing_status=%.4f, tax_total=%.4f, soi_agi=%.4f)",
                outer + 1, max_dev, fc_d, fs_d, tt_d, soi_d,
            )
            if max_dev < outer_tolerance:
                converged = True
                convergence_mode = "tolerance"
                logger.info(
                    "Joint IPF converged in %d outer iterations (max_dev=%.4f)",
                    outer + 1, max_dev,
                )
                break

            # Detect fixed-point cycling — system has reached the best
            # compromise feasible given the (jointly inconsistent) targets.
            if outer >= 1:
                prev = history[-2]
                if (abs(prev[1] - fc_d) < STALE_TOL
                        and abs(prev[2] - fs_d) < STALE_TOL
                        and abs(prev[3] - tt_d) < STALE_TOL):
                    stale_count += 1
                else:
                    stale_count = 0
                if stale_count >= STALE_LIMIT:
                    converged = True
                    convergence_mode = "fixed_point"
                    logger.info(
                        "Joint IPF reached compromise fixed-point in %d outer "
                        "iterations (max_dev=%.4f — constraints jointly "
                        "inconsistent at this residual)",
                        outer + 1, max_dev,
                    )
                    break

        if not converged:
            logger.warning(
                "Joint IPF did not converge in %d outer iterations "
                "(final max_dev=%.4f)",
                max_outer_iters,
                max(history[-1][1:]) if history else float("nan"),
            )

        logger.info(
            "Joint IPF complete: %d records, weight sum %.0f → %.0f",
            len(result), initial_weight, result["weight"].sum(),
        )

        result.drop(columns=temp_cols, inplace=True, errors="ignore")

        # --- calibration report ------------------------------------------
        dotax_year = dotax_base.BASE_YEAR
        if soi_source_year is None:
            from tax_modeler.calibration.irs_soi_state_targets import SOI_TAX_YEAR
            soi_source_year = SOI_TAX_YEAR
        source_years: Dict[str, Optional[int]] = {}
        residuals: List[MarginResidual] = []
        if calibrate_filer_counts:
            source_years["dotax_filer_count"] = dotax_year
            for (lo, hi), tgt in self.DOTAX_FILER_TARGETS.items():
                cur = float(result.loc[(result["agi"] >= lo) & (result["agi"] < hi), "weight"].sum())
                residuals.append(MarginResidual(
                    "dotax_filer_count", bracket_label(lo, hi), float(tgt), cur,
                    source="DOTAX Table A-8 returns", source_year=dotax_year,
                    counted=not (skip_synthesis_bin and lo >= 1_000_000) and tgt > 0,
                ))
        if calibrate_filing_status:
            source_years["dotax_filing_status"] = dotax_year
            for fs, tgt in self.DOTAX_FILING_STATUS_TARGETS.items():
                cur = float(result.loc[result["filing_status"] == fs, "weight"].sum())
                residuals.append(MarginResidual(
                    "dotax_filing_status", fs, float(tgt), cur,
                    source="DOTAX Table 4 filing status", source_year=dotax_year,
                    counted=tgt > 0,
                ))
        if calibrate_tax_totals:
            source_years["dotax_tax_M"] = dotax_year
            for (lo, hi), tgt in self.DOTAX_TAX_TARGETS.items():
                mask = (result["agi"] >= lo) & (result["agi"] < hi)
                cur = float((result.loc[mask, "hi_state_tax"] * result.loc[mask, "weight"]).sum() / 1e6)
                residuals.append(MarginResidual(
                    "dotax_tax_M", bracket_label(lo, hi), float(tgt), cur,
                    source="DOTAX Table A-8 tax before credits ($M)", source_year=dotax_year,
                    counted=(lo, hi) in tax_targets and tgt > 0.5 and cur > 0,
                ))
        if calibrate_soi_agi and soi_agi_targets_M:
            source_years["soi_agi_M"] = soi_source_year
            for (lo, hi), tgt in soi_agi_targets_M.items():
                mask = (result["agi"] >= lo) & (result["agi"] < hi)
                cur = float((result.loc[mask, "agi"] * result.loc[mask, "weight"]).sum() / 1e6)
                scaled = float(tgt * soi_universe_scale)
                residuals.append(MarginResidual(
                    "soi_agi_M", bracket_label(lo, hi), scaled, cur,
                    source=f"IRS SOI Table 2 AGI ($M, x{soi_universe_scale:.4f} universe scale)",
                    source_year=soi_source_year,
                    counted=(lo, hi) in soi_targets_scaled and scaled > 0.5 and cur > 0,
                ))
        mismatch = warn_once_on_year_mismatch(source_years)
        if mismatch:
            report_warnings.append(mismatch)
        if not converged:
            report_warnings.append(
                f"Joint IPF did not converge in {max_outer_iters} outer iterations"
            )
        elif convergence_mode == "fixed_point" and last_max_dev is not None \
                and last_max_dev >= outer_tolerance:
            report_warnings.append(
                f"Joint IPF stopped at a compromise fixed point after {outer + 1} outer "
                f"iterations: max relative deviation {last_max_dev:.4f} is above the "
                f"{outer_tolerance:g} tolerance (margins are jointly inconsistent)"
            )

        report = CalibrationReport(
            stage="base_ipf_rake",
            converged=converged,
            iterations=outer + 1,
            max_iterations=max_outer_iters,
            tolerance=outer_tolerance,
            final_max_dev=float(last_max_dev) if last_max_dev is not None else None,
            convergence_mode=convergence_mode,
            margins=residuals,
            clip_events=clip_events,
            dropped_bins=dropped,
            weights_before=weights_before,
            weights_after=WeightDiagnostics.from_weights(result["weight"].to_numpy(dtype=float)),
            source_years=source_years,
            warnings=report_warnings,
        )
        result.attrs["calibration_report"] = report.to_dict()
        if return_report:
            return result, report
        return result

    # ------------------------------------------------------------------
    # Joint generalized raking (October 2026)
    # ------------------------------------------------------------------

    TAX_VALUE_COL_PREFERRED = "hi_tax_before_credits"

    def calibrate_joint(
        self,
        df: pd.DataFrame,
        *,
        calibrate_filer_counts: bool = True,
        calibrate_tax_totals: bool = True,
        calibrate_filing_status: bool = True,
        skip_synthesis_bin: bool = True,
        bounds: Tuple[float, float] = (0.2, 5.0),
        tax_value_col: Optional[str] = None,
        tolerance: float = 1e-3,
        max_iter: int = 100,
        min_tax_target_M: float = 10.0,
        soi_agi_targets_M: Optional[Dict[Tuple[float, float], float]] = None,
        soi_universe_scale: float = 1.0,
        soi_source_year: Optional[int] = None,
        agi_check_targets_M: Optional[Dict[Tuple[float, float], float]] = None,
        agi_check_source: str = "DOTAX Table A-1 AGI ($M)",
        agi_check_year: Optional[int] = None,
        return_report: bool = False,
    ) -> pd.DataFrame | Tuple[pd.DataFrame, CalibrationReport]:
        """One generalized-raking system for every fitted margin.

        Fitted margins (one linear constraint per cell, solved together by
        :func:`calibration.generalized_raking.calmar` with logit-bounded
        weight ratios ``w / d`` in ``bounds``):

        * filer counts by AGI bracket (DOTAX Table A-8, all 15 classes —
          the $1M+ class is raked to its anchor so the filer total holds;
          synthesis later replaces its units);
        * filing status (DOTAX Table 4);
        * **tax before credits** by bracket (A-8), on ``tax_value_col`` —
          ``hi_tax_before_credits`` when the frame has it, else
          ``hi_state_tax``. The legacy scheme fitted ``hi_state_tax``
          (= ``hi_tax_liability``, after the refundable low-income credit)
          to A-8's *before*-credit column, which is why its $0–30K bins
          were "infeasible": at $0–10K the model's after-credit tax is
          −$32M against a +$3M target. On the before-credit column every
          bin's target lies inside the range the bracket's units can reach.
          A bin whose target is outside that range (:func:`feasible_interval`)
          is dropped and reported, as is a bin below ``min_tax_target_M``
          (the $0–10K class, $3M); the $1M+ bin is skipped as before.

        Check-only margins (reported with ``counted=False``, never fitted):

        * ``agi_check_targets_M`` — DOTAX Table A-1 AGI by class (TY2023,
          Hawaiʻi AGI on the same brackets). Fitting it is jointly
          infeasible with the count and tax margins under any usable
          bound (the LP test fails) and costs Kish deff 2.3 → 3.8 for a
          margin the weights already satisfy to within 0.3–1.6% per
          class; so it is a check.
        * ``soi_agi_targets_M`` — the IRS SOI Table 2 federal-AGI margin
          (TY2022) the legacy scheme fitted with a 1.10× cap. Reported for
          continuity; not fitted (year and AGI-definition mismatch with the
          DOTAX margins).

        Convergence is ``max_j |achieved_j − target_j| / |target_j| < tolerance``
        over the fitted cells (default 0.1%, ten times tighter than the
        legacy 1%), reported as ``convergence_mode="tolerance"``; otherwise
        ``"max_iterations"`` with the residuals in the report.
        """
        from tax_modeler.calibration.generalized_raking import calmar, feasible_interval

        result = df.copy()
        clip_events: List[ClipEvent] = []
        dropped: List[DroppedBin] = []
        report_warnings: List[str] = []
        notes: List[str] = []
        d = result["weight"].to_numpy(dtype=float)
        weights_before = WeightDiagnostics.from_weights(d)
        agi = result["agi"].to_numpy(dtype=float)
        fs = result["filing_status"].to_numpy()

        if tax_value_col is None:
            tax_value_col = (self.TAX_VALUE_COL_PREFERRED
                             if self.TAX_VALUE_COL_PREFERRED in result.columns
                             else "hi_state_tax")
        tax = (result[tax_value_col].to_numpy(dtype=float)
               if calibrate_tax_totals else None)
        if calibrate_tax_totals and tax_value_col != self.TAX_VALUE_COL_PREFERRED:
            notes.append(
                f"tax margin fitted on '{tax_value_col}' (no "
                f"'{self.TAX_VALUE_COL_PREFERRED}' column): DOTAX A-8 tax is before credits"
            )

        def _bmask(lo: float, hi: float) -> np.ndarray:
            return (agi >= lo) & (agi < hi)

        cols: List[np.ndarray] = []
        totals: List[float] = []
        cells: List[Tuple[str, str, float, float]] = []   # (margin, key, lo, hi)

        if calibrate_filer_counts:
            for (lo, hi), tgt in self.DOTAX_FILER_TARGETS.items():
                m = _bmask(lo, hi)
                if tgt <= 0 or not m.any():
                    if tgt > 0:
                        dropped.append(DroppedBin("filer_count_margin", bracket_label(lo, hi),
                                                  "no units in the bracket", float(tgt), 0.0))
                    continue
                cols.append(m.astype(float)); totals.append(float(tgt))
                cells.append(("dotax_filer_count", bracket_label(lo, hi), lo, hi))
        if calibrate_filing_status:
            for status, tgt in self.DOTAX_FILING_STATUS_TARGETS.items():
                m = fs == status
                if tgt <= 0 or not m.any():
                    continue
                cols.append(m.astype(float)); totals.append(float(tgt))
                cells.append(("dotax_filing_status", status, float("nan"), float("nan")))
        tax_fitted: Dict[Tuple[float, float], float] = {}
        if calibrate_tax_totals:
            for (lo, hi), tgt in self.DOTAX_TAX_TARGETS.items():
                if skip_synthesis_bin and lo >= 1_000_000:
                    continue
                m = _bmask(lo, hi)
                ftgt = self.DOTAX_FILER_TARGETS.get((lo, hi), 0)
                if tgt <= 0 or ftgt <= 0 or not m.any():
                    continue
                if tgt < min_tax_target_M:
                    # $0-$10K: $3M of tax on 110K returns. Fitting a cell
                    # this small moves weight onto the bracket's few
                    # taxpaying units for 0.1% of the aggregate (Kish deff
                    # +0.10). Reported, not fitted.
                    dropped.append(DroppedBin(
                        "tax_total_margin", bracket_label(lo, hi),
                        f"target below the {min_tax_target_M:g} $M materiality floor",
                        float(tgt), None,
                    ))
                    continue
                lo_f, hi_f = feasible_interval(tax[m] / 1e6, d[m], float(ftgt), bounds)
                if not (lo_f <= tgt <= hi_f):
                    dropped.append(DroppedBin(
                        "tax_total_margin", bracket_label(lo, hi),
                        (f"target outside the range [{lo_f:,.1f}, {hi_f:,.1f}] $M reachable at the "
                         f"count target with weight ratios in [{bounds[0]:g}, {bounds[1]:g}]"),
                        float(tgt), float(hi_f if tgt > hi_f else lo_f),
                    ))
                    continue
                cols.append(m * tax / 1e6); totals.append(float(tgt))
                cells.append(("dotax_tax_M", bracket_label(lo, hi), lo, hi))
                tax_fitted[(lo, hi)] = float(tgt)

        converged = False
        iterations = 0
        max_rel: Optional[float] = None
        if cols:
            X = np.column_stack(cols)
            T = np.asarray(totals, dtype=float)
            res = calmar(X, d, T, method="logit", bounds=bounds, tol=1e-6, max_iter=max_iter)
            w = res.weights
            rel = np.abs(res.residuals) / np.where(np.abs(T) > 0, np.abs(T), 1.0)
            max_rel = float(rel.max())
            converged = max_rel < tolerance
            iterations = int(res.iterations)
            notes.append(
                f"generalized raking (logit distance, weight-ratio bounds "
                f"[{bounds[0]:g}, {bounds[1]:g}]), {len(T)} fitted cells, Newton tol 1e-6; "
                f"units at lower bound: {res.n_at_lower:,}, at upper bound: {res.n_at_upper:,}; "
                f"weight ratio range [{res.g.min():.3f}, {res.g.max():.3f}]"
            )
            result["weight"] = w
            logger.info(
                "Joint rake: %d cells, %d Newton iterations, max rel. residual %.2e (%s)",
                len(T), iterations, max_rel, "converged" if converged else "NOT converged",
            )
        else:
            converged = True
            max_rel = 0.0

        # --- report -------------------------------------------------------
        dotax_year = dotax_base.BASE_YEAR
        wf = result["weight"].to_numpy(dtype=float)
        residuals: List[MarginResidual] = []
        source_years: Dict[str, Optional[int]] = {}
        if calibrate_filer_counts:
            source_years["dotax_filer_count"] = dotax_year
            for (lo, hi), tgt in self.DOTAX_FILER_TARGETS.items():
                cur = float(wf[_bmask(lo, hi)].sum())
                residuals.append(MarginResidual(
                    "dotax_filer_count", bracket_label(lo, hi), float(tgt), cur,
                    source="DOTAX Table A-8 returns", source_year=dotax_year,
                    counted=any(c[0] == "dotax_filer_count" and c[1] == bracket_label(lo, hi)
                                for c in cells),
                ))
        if calibrate_filing_status:
            source_years["dotax_filing_status"] = dotax_year
            for status, tgt in self.DOTAX_FILING_STATUS_TARGETS.items():
                cur = float(wf[fs == status].sum())
                residuals.append(MarginResidual(
                    "dotax_filing_status", status, float(tgt), cur,
                    source="DOTAX Table 4 filing status", source_year=dotax_year,
                    counted=tgt > 0,
                ))
        if calibrate_tax_totals:
            source_years["dotax_tax_M"] = dotax_year
            for (lo, hi), tgt in self.DOTAX_TAX_TARGETS.items():
                m = _bmask(lo, hi)
                cur = float((tax[m] * wf[m]).sum() / 1e6)
                residuals.append(MarginResidual(
                    "dotax_tax_M", bracket_label(lo, hi), float(tgt), cur,
                    source=f"DOTAX Table A-8 tax before credits ($M, on {tax_value_col})",
                    source_year=dotax_year, counted=(lo, hi) in tax_fitted,
                ))
        if agi_check_targets_M:
            year = agi_check_year if agi_check_year is not None else dotax_year
            source_years["dotax_agi_M_check"] = year
            for (lo, hi), tgt in agi_check_targets_M.items():
                m = _bmask(lo, hi)
                cur = float((agi[m] * wf[m]).sum() / 1e6)
                residuals.append(MarginResidual(
                    "dotax_agi_M_check", bracket_label(lo, hi), float(tgt), cur,
                    source=agi_check_source + " — check, not fitted", source_year=year,
                    counted=False,
                ))
        if soi_agi_targets_M:
            if soi_source_year is None:
                from tax_modeler.calibration.irs_soi_state_targets import SOI_TAX_YEAR
                soi_source_year = SOI_TAX_YEAR
            source_years["soi_agi_M_check"] = soi_source_year
            for (lo, hi), tgt in soi_agi_targets_M.items():
                m = _bmask(lo, hi)
                cur = float((agi[m] * wf[m]).sum() / 1e6)
                residuals.append(MarginResidual(
                    "soi_agi_M_check", bracket_label(lo, hi), float(tgt * soi_universe_scale), cur,
                    source=(f"IRS SOI Table 2 federal AGI ($M, x{soi_universe_scale:.4f} universe "
                            "scale) — check, not fitted"),
                    source_year=soi_source_year, counted=False,
                ))
        fitted_years = {k: y for k, y in source_years.items() if not k.endswith("_check")}
        mismatch = warn_once_on_year_mismatch(fitted_years)
        if mismatch:
            report_warnings.append(mismatch)
        if not converged:
            report_warnings.append(
                f"Joint rake did not reach the {tolerance:g} relative tolerance in {iterations} "
                f"Newton iterations (max relative residual {max_rel:.4g}); the fitted margins are "
                f"jointly infeasible within weight-ratio bounds [{bounds[0]:g}, {bounds[1]:g}]"
            )

        report = CalibrationReport(
            stage="base_ipf_rake",
            converged=converged,
            iterations=iterations,
            max_iterations=max_iter,
            tolerance=tolerance,
            final_max_dev=max_rel,
            convergence_mode="tolerance" if converged else "max_iterations",
            margins=residuals,
            clip_events=clip_events,
            dropped_bins=dropped,
            weights_before=weights_before,
            weights_after=WeightDiagnostics.from_weights(wf),
            source_years=source_years,
            warnings=report_warnings,
            notes=notes,
        )
        result.attrs["calibration_report"] = report.to_dict()
        if return_report:
            return result, report
        return result

    def _validate_filer_counts(self, df: pd.DataFrame) -> Dict[str, float]:
        """Validate filer counts and return deviation metrics."""
        current_counts = {}
        total_sq_error = 0
        max_dev = 0
        
        for (min_agi, max_agi), target in self.DOTAX_FILER_TARGETS.items():
            mask = (df['agi'] >= min_agi) & (df['agi'] < max_agi)
            current = df.loc[mask, 'weight'].sum()
            current_counts[(min_agi, max_agi)] = current
            
            if target > 0:
                sq_error = (current - target)**2 / target
                total_sq_error += sq_error
                dev = abs(current - target) / target
                max_dev = max(max_dev, dev)
        
        return {
            'current': current_counts,
            'chi_squared': total_sq_error,
            'max_deviation': max_dev
        }
    
    def _validate_tax_totals(self, df: pd.DataFrame) -> Dict[str, float]:
        """Validate tax totals and return deviation metrics."""
        current_totals = {}
        total_sq_error = 0
        max_dev = 0
        
        for (min_agi, max_agi), target_m in self.DOTAX_TAX_TARGETS.items():
            mask = (df['agi'] >= min_agi) & (df['agi'] < max_agi)
            current_m = (df.loc[mask, 'hi_state_tax'] * df.loc[mask, 'weight']).sum() / 1_000_000
            current_totals[(min_agi, max_agi)] = current_m
            
            if target_m > 0:
                sq_error = (current_m - target_m)**2 / target_m
                total_sq_error += sq_error
                dev = abs(current_m - target_m) / target_m
                max_dev = max(max_dev, dev)
        
        return {
            'current': current_totals,
            'chi_squared': total_sq_error,
            'max_deviation': max_dev
        }
    
    def _validate_filing_status(self, df: pd.DataFrame) -> Dict[str, float]:
        """Validate filing status distribution and return deviation metrics."""
        current_counts = {}
        total_sq_error = 0
        max_dev = 0
        
        for status, target in self.DOTAX_FILING_STATUS_TARGETS.items():
            mask = df['filing_status'] == status
            current = df.loc[mask, 'weight'].sum()
            current_counts[status] = current
            
            if target > 0:
                sq_error = (current - target)**2 / target
                total_sq_error += sq_error
                dev = abs(current - target) / target
                max_dev = max(max_dev, dev)
        
        return {
            'current': current_counts,
            'chi_squared': total_sq_error,
            'max_deviation': max_dev
        }
    
    def _print_validation_summary(self, df: pd.DataFrame):
        """Print comprehensive validation summary."""
        
        # Filer counts
        logger.info("\n1. FILER COUNTS BY AGI BRACKET:")
        filer_results = self._validate_filer_counts(df)
        within_tolerance = 0
        total_brackets = len(self.DOTAX_FILER_TARGETS)
        
        for (min_agi, max_agi), target in self.DOTAX_FILER_TARGETS.items():
            current = filer_results['current'][(min_agi, max_agi)]
            deviation = abs(current - target) / target if target > 0 else 0
            
            if deviation <= self.tolerance:
                within_tolerance += 1
                status = "✅"
            else:
                status = "❌" if deviation > 0.10 else "⚠️"
            
            logger.info(f"   ${min_agi//1000:>4}k-${max_agi//1000:>4}k: "
                       f"{current:>8,.0f} vs {target:>8,.0f} "
                       f"({deviation:>+6.1%}) {status}")
        
        logger.info(f"\n   Within tolerance: {within_tolerance}/{total_brackets} brackets")
        logger.info(f"   Chi-squared: {filer_results['chi_squared']:.2f}")
        logger.info(f"   Max deviation: {filer_results['max_deviation']:.1%}")
        
        # Tax totals
        logger.info("\n2. TAX TOTALS BY AGI BRACKET:")
        tax_results = self._validate_tax_totals(df)
        within_tolerance = 0
        
        for (min_agi, max_agi), target_m in self.DOTAX_TAX_TARGETS.items():
            current_m = tax_results['current'][(min_agi, max_agi)]
            deviation = abs(current_m - target_m) / target_m if target_m > 0 else 0
            
            if deviation <= self.tolerance:
                within_tolerance += 1
                status = "✅"
            else:
                status = "❌" if deviation > 0.10 else "⚠️"
            
            logger.info(f"   ${min_agi//1000:>4}k-${max_agi//1000}k: "
                       f"${current_m:>7.1f}M vs ${target_m:>7.1f}M "
                       f"({deviation:>+6.1%}) {status}")
        
        logger.info(f"\n   Within tolerance: {within_tolerance}/{total_brackets} brackets")
        logger.info(f"   Chi-squared: {tax_results['chi_squared']:.2f}")
        logger.info(f"   Max deviation: {tax_results['max_deviation']:.1%}")
        
        # Filing status
        logger.info("\n3. FILING STATUS DISTRIBUTION:")
        status_results = self._validate_filing_status(df)
        
        for status, target in self.DOTAX_FILING_STATUS_TARGETS.items():
            current = status_results['current'][status]
            deviation = abs(current - target) / target if target > 0 else 0
            status_icon = "✅" if deviation <= self.tolerance else "⚠️"
            
            logger.info(f"   {status:<30}: "
                       f"{current:>8,.0f} vs {target:>8,.0f} "
                       f"({deviation:>+6.1%}) {status_icon}")


def apply_ipf_calibration(df: pd.DataFrame,
                          max_iterations: int = 20,
                          tolerance: float = 0.01,
                          calibrate_filer_counts: bool = True,
                          calibrate_tax_totals: bool = True,
                          calibrate_filing_status: bool = True) -> pd.DataFrame:
    """
    Convenience function to apply IPF calibration.

    Args:
        df: Input DataFrame with tax units
        max_iterations: Maximum IPF iterations (default: 20)
        tolerance: Convergence tolerance (default: 1%)
        calibrate_filer_counts: Whether to calibrate to filer counts
        calibrate_tax_totals: Whether to calibrate to tax totals
        calibrate_filing_status: Whether to calibrate to filing status

    Returns:
        Calibrated DataFrame
    """
    orchestrator = IPFCalibrationOrchestrator(
        max_iterations=max_iterations,
        tolerance=tolerance
    )

    return orchestrator.calibrate_ipf(
        df,
        calibrate_filer_counts=calibrate_filer_counts,
        calibrate_tax_totals=calibrate_tax_totals,
        calibrate_filing_status=calibrate_filing_status
    )


def apply_ipf_calibration_via_rake(
    df: pd.DataFrame,
    max_iterations: int = 100,
    tolerance: float = 1e-4,
    calibrate_filer_counts: bool = True,
    calibrate_tax_totals: bool = True,
    calibrate_filing_status: bool = True,
    calibrate_soi_agi: bool = True,
    return_report: bool = False,
    method: str = "joint",
) -> pd.DataFrame | Tuple[pd.DataFrame, CalibrationReport]:
    """
    IPF calibration using pums_estimator's canonical rake() engine.

    ``method="joint"`` (default): one generalized-raking system for the
    DOTAX count, status and tax-before-credits margins, with DOTAX A-1 AGI
    (TY2023) and the SOI AGI (TY2022) reported as checks
    (:meth:`IPFCalibrationOrchestrator.calibrate_joint`).
    ``method="sequential"``: the legacy damped multi-pass scheme, which
    fitted the SOI AGI margin with a 1.10x cap.

    Delegates filer-count and filing-status margins to
    ``pums_estimator.estimation.rake.rake()``, then applies the tax-total
    adjustment as a separate step.

    Args:
        df: Input DataFrame with columns 'weight', 'agi', 'filing_status'.
            Also requires 'hi_state_tax' when calibrate_tax_totals is True.
        max_iterations: Passed to rake() as max_iter (default: 100).
        tolerance: Convergence tolerance passed to rake() (default: 1e-4).
        calibrate_filer_counts: Rake to DOTAX filer-count targets by AGI bracket.
        calibrate_tax_totals: Scale weights to DOTAX tax-total targets by AGI bracket.
        calibrate_filing_status: Rake to DOTAX filing-status distribution.
        return_report: When True, return ``(df, CalibrationReport)`` — the
            convergence, residual, clip-event and weight-dispersion record
            of the rake (``calibration.report``). The report is attached to
            ``df.attrs["calibration_report"]`` (as a dict) either way.

    Returns:
        Calibrated DataFrame (and the report when ``return_report``).
    """
    orchestrator = IPFCalibrationOrchestrator(
        max_iterations=max_iterations,
        tolerance=tolerance,
    )

    # Auto-load IRS SOI Hawaii Table 2 AGI targets if enabled. Universe
    # scaling: SOI counts ~674.7K returns vs DOTAX 618.4K; we scale SOI
    # AGI down so it matches the DOTAX-anchored filer universe.
    soi_agi_targets_M: Optional[Dict[Tuple[float, float], float]] = None
    soi_universe_scale = 1.0
    if calibrate_soi_agi:
        try:
            from tax_modeler.calibration.irs_soi_state_targets import (
                load_soi_state_targets,
            )
            soi_df = load_soi_state_targets()
            soi_agi_targets_M = {
                (float(lo), float(hi)): float(row["agi_M"])
                for (lo, hi), row in soi_df.iterrows()
            }
            soi_total_returns = float(soi_df["n_returns"].sum())
            dotax_total_returns = float(
                sum(orchestrator.DOTAX_FILER_TARGETS.values())
            )
            if soi_total_returns > 0:
                soi_universe_scale = dotax_total_returns / soi_total_returns
            logger.info(
                "SOI margin loaded: 6 bins, total AGI=$%.0fM, universe_scale=%.4f",
                sum(soi_agi_targets_M.values()), soi_universe_scale,
            )
        except (FileNotFoundError, ImportError) as e:
            logger.warning("SOI calibration disabled (data unavailable): %s", e)
            calibrate_soi_agi = False

    agi_check: Optional[Dict[Tuple[float, float], float]] = None
    if method == "joint":
        # DOTAX Table A-1 AGI by class, Hawaii AGI, TY2023 — the printed
        # classes below $400K only (above, dotax_base derives the split and
        # its $400K-$1M classes imply mean AGI below the class floor); and
        # only where taxable returns are >=97% of all returns, since A-1
        # covers taxable returns and A-8 all returns.
        try:
            a1 = dotax_base.load_indinc(dotax_base.BASE_YEAR)["table_a1_resident_taxable"]["taxable_classes"]
            taxable = {(float(r["agi_lo"]), float(r["agi_hi"])): int(r["returns"])
                       for r in a1 if r.get("agi_lo") is not None and r.get("agi_hi") is not None}
            filers = {(float(lo), float(hi)): n for (lo, hi), n in orchestrator.DOTAX_FILER_TARGETS.items()}
            agi_all = {(float(lo), float(hi)): v for (lo, hi), v in dotax_base.agi_targets_M().items()}
            agi_check = {
                b: agi_all[b] for b in agi_all
                if b in taxable and b[0] < 400_000 and filers.get(b, 0) > 0
                and taxable[b] / filers[b] >= 0.97
            }
        except (FileNotFoundError, KeyError, ValueError) as e:  # pragma: no cover - data on disk
            logger.warning("DOTAX A-1 AGI check unavailable: %s", e)

    return orchestrator.calibrate_via_rake(
        df,
        calibrate_filer_counts=calibrate_filer_counts,
        calibrate_tax_totals=calibrate_tax_totals,
        calibrate_filing_status=calibrate_filing_status,
        calibrate_soi_agi=calibrate_soi_agi,
        soi_agi_targets_M=soi_agi_targets_M,
        soi_universe_scale=soi_universe_scale,
        return_report=return_report,
        method=method,
        agi_check_targets_M=agi_check,
        agi_check_year=dotax_base.BASE_YEAR,
    )
