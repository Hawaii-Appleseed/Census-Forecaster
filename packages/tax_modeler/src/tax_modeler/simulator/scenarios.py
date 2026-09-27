"""The revenue scenarios the simulator scores, from the Act 24 page.

These are the LOW / MID / HIGH entries of ``forecast_sb3125_enhanced.SCENARIOS``
restricted to what the bracket scoring uses (the rest are credit-overlay
knobs, and a spec leaves credits at current law).
``tests/tax_modeler/test_simulator.py`` checks they have not drifted.

Note the pairing: the LOW revenue scenario has the *strong* behavioral
response ("high" ETI) and HIGH the weak one.
"""
from __future__ import annotations

from dataclasses import dataclass

from tax_modeler.scenarios.behavioral_response import BehavioralParams

YEARS = (2027, 2028, 2029, 2030, 2031)


@dataclass(frozen=True)
class SimScenario:
    key: str             # "low" / "mid" / "high"
    label: str           # enhanced SCENARIOS label
    alpha: float         # Pareto index of the synthetic $1M+ tail
    top_premium: float   # top-income growth premium, per year from 2024
    behavior: str        # BehavioralParams.named(...)

    @property
    def behavioral_params(self) -> BehavioralParams:
        return BehavioralParams.named(self.behavior)


SCENARIOS = {
    "low": SimScenario("low", "LOW", alpha=1.7, top_premium=0.003, behavior="high"),
    "mid": SimScenario("mid", "MID", alpha=1.5, top_premium=0.010, behavior="mid"),
    "high": SimScenario("high", "HIGH", alpha=1.4, top_premium=0.023, behavior="low"),
}
