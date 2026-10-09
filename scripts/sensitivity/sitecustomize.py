"""Sensitivity override for the $1M+ class in the bundled TY2023 DOTAX edition.

Answers: how much of an Act 24 result rides on the one year (TY2023) the $1M+
class is anchored to? It rewrites the class's row of Table A-8 (returns and tax
before credits) as ``tax_modeler.calibration.dotax_base`` reads it, and nothing
else; the class table's total follows so the two stay consistent. Everything
that reads the anchors (forward targets, the IPF calibration, the tail's
synthesis target, the capital-gains class ranks) sees the override.

Python imports ``sitecustomize`` at start-up in every interpreter that has this
directory on ``PYTHONPATH``, including the scenario workers
``forecast_sb3125_enhanced.py`` spawns, which a patch made in the parent would
miss. ``DOTAX_SENS_TOP`` selects the $1M+ values:

    avg    the average of TY2022 (1,824 returns, $663.0M, the figure the model
           carried) and TY2023 (1,704, $441M): 1,764 returns, $552.0M
    y2022  the TY2022 values; with the rest on TY2023 this isolates the class

Unset, it does nothing. ``DOTAX_SENS_MARK`` is a path prefix; each process that
applies the override writes ``<prefix>.<pid>``, so a run can confirm that its
workers did (7 files for the enhanced run: the parent and six workers).

The forecast scripts write to fixed paths (``runs/``, the calibrated-base
pickle), so run each variant in its own copy:

    V=/tmp/sens/avg; mkdir -p $V/data/artifacts $V/runs
    cp *.py $V/ && cp -R scripts $V/scripts
    cp data/artifacts/tax_units_cache.* $V/data/artifacts/
    cd $V && PYTHONPATH=<repo>/scripts/sensitivity DOTAX_SENS_TOP=avg \\
        DOTAX_SENS_MARK=/tmp/sens/mark_avg \\
        <repo>/.venv/bin/python forecast_sb3125_enhanced.py --cd 2
    # then, once data/artifacts/sb3125_calibrated_base.pkl exists in $V, the
    # same prefix on forecast_sb3125_vs_fy26base.py --cd 2 and
    # forecast_act24_vs_pre_act46.py

The enhanced run's log confirms it: "Synthesis: 1,764 filers @ $1M+" for ``avg``.
Under ``y2022`` the tail scale factors come out at 1.7743 / 1.4210 / 1.2343, the
values the model recorded on the TY2022 base.

Changing the class's tax also shifts how ``dotax_base.agi_targets_M`` splits
Table A-1's $400K+ AGI among its four classes (the split follows A-8's implied
AGI); that is a side effect, and the gains base stays on TY2023.
"""
import json
import os
import pathlib

_MODE = os.environ.get("DOTAX_SENS_TOP")
if _MODE:
    _read_text = pathlib.Path.read_text
    _TY2022_RETURNS, _TY2022_TAX_M = 1_824, 663.0

    def _read_text_with_override(self, *args, **kwargs):
        text = _read_text(self, *args, **kwargs)
        if self.name != "dotax_indinc_2023.json":
            return text
        d = json.loads(text)
        a8 = d["table_a8_resident_liability"]
        top = next(r for r in a8["classes"] if r["agi_lo"] == 1_000_000)
        returns_2023, tax_2023 = top["returns"], top["tax_before_M"]
        if _MODE == "avg":
            returns = round((returns_2023 + _TY2022_RETURNS) / 2)
            tax = (tax_2023 + _TY2022_TAX_M) / 2
        elif _MODE == "y2022":
            returns, tax = _TY2022_RETURNS, _TY2022_TAX_M
        else:
            raise ValueError(f"DOTAX_SENS_TOP must be 'avg' or 'y2022', not {_MODE!r}")
        top["returns"], top["tax_before_M"] = returns, tax
        a8["total"]["returns"] += returns - returns_2023
        a8["total"]["tax_before_M"] += tax - tax_2023
        mark = os.environ.get("DOTAX_SENS_MARK")
        if mark:
            pathlib.Path(f"{mark}.{os.getpid()}").write_text(f"{_MODE} {returns} {tax}\n")
        return json.dumps(d)

    pathlib.Path.read_text = _read_text_with_override
