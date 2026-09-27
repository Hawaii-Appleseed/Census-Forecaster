"""The tax simulator: user-defined income tax changes scored on the Act 24
population (TAX_SIMULATOR_SCOPE.md).

* :mod:`.scenarios` — the three published revenue scenarios (low/mid/high).
* :mod:`.population` — build, save and load the scoring population, and
  reconstruct the frames the Python pipeline scores.
* :mod:`.score` — score an :class:`~tax_modeler.reform.income_tax_spec.IncomeTaxSpec`
  with the real pipeline functions (the reference the browser kernel must match).
* :mod:`.web` — the compact binary + JSON the estimates-site page loads.
"""
