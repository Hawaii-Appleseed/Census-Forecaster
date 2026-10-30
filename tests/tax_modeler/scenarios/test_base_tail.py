"""``base.build_tail_units`` and ``act24_population.build_units`` share one core."""
from __future__ import annotations

import tax_modeler.calibration.cg_imputation as cg_imputation
from tax_modeler import base
from tax_modeler.scenarios import top_income_synthesis
from tax_modeler.scenarios.act24_population import build_units


def test_build_tail_units_shares_act24_core(monkeypatch, tail_units, ded_params):
    """Same steps as ``act24_population.build_units`` up to the aging; the
    redistribution is the only extra, and only when asked."""
    monkeypatch.setattr(top_income_synthesis, "synthesize_top_filers",
                        lambda df, pareto_alpha: df.copy())
    monkeypatch.setattr(cg_imputation, "impute_capital_gains_from_soi", lambda df: df)
    calls = []
    monkeypatch.setattr(top_income_synthesis, "redistribute_mid_high_incomes",
                        lambda df, pareto_alpha: calls.append(pareto_alpha) or df)

    units, k = base.build_tail_units(tail_units, alpha=1.5, ded_params=ded_params,
                                     cal_tax_year=2023, redistribute=False)
    assert calls == []
    _, k_act24 = build_units(tail_units, alpha=1.5, top_premium=0.01, ded_params=ded_params,
                             cal_tax_year=2023, enrich=True)
    assert k == k_act24
    assert units["total_cash_income"].notna().all()   # enriched

    base.build_tail_units(tail_units, alpha=1.4, ded_params=ded_params, cal_tax_year=2023)
    assert calls == [1.4]
