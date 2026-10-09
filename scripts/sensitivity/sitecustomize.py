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

    avg     the average of TY2022 (1,824 returns, $663.0M) and TY2023 (1,704,
            $441M): 1,764 returns, $552.0M
    y2022   the TY2022 values; with the rest on TY2023 this isolates the class
    custom  ``DOTAX_SENS_TOP_RETURNS`` and ``DOTAX_SENS_TOP_TAX_M`` as given. The
            multi-year windows in SB3125_CD1_FORECAST.md are simple averages of
            Table A-8's $1M+ row (returns / tax before credits, $M):
              TY2019-23  1,633 / 546.2    TY2021-23  1,871 / 634.33
            from TY2019 1,163 / 413, TY2020 1,387 / 415, TY2021 2,086 / 799,
            TY2022 1,824 / 663, TY2023 1,704 / 441 (each edition's own table;
            TY2019-2022 are at files.hawaii.gov/tax/stats/stats/indinc/archive/)

Unset, it does nothing. ``DOTAX_SENS_MARK`` is a path prefix; each process that
applies the override writes ``<prefix>.<pid>``, so a run can confirm that its
workers did: 7 files per variant across the three runs below (the enhanced run's
parent and its 4 scenario workers, plus the frozen-baseline and pre-Act-46 runs).

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
    # forecast_act24_vs_pre_act46.py, run from $V (the scripts find their
    # output and artifact directories next to themselves; from the repo they
    # would read the repo's calibrated base and overwrite its runs/)

The enhanced run's log confirms it: "Synthesis: 1,764 filers @ $1M+" for ``avg``,
and the mark files number 7 (above).
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
        elif _MODE == "custom":
            returns = int(os.environ["DOTAX_SENS_TOP_RETURNS"])
            tax = float(os.environ["DOTAX_SENS_TOP_TAX_M"])
        else:
            raise ValueError(f"DOTAX_SENS_TOP must be 'avg', 'y2022' or 'custom', not {_MODE!r}")
        top["returns"], top["tax_before_M"] = returns, tax
        a8["total"]["returns"] += returns - returns_2023
        a8["total"]["tax_before_M"] += tax - tax_2023
        mark = os.environ.get("DOTAX_SENS_MARK")
        if mark:
            pathlib.Path(f"{mark}.{os.getpid()}").write_text(f"{_MODE} {returns} {tax}\n")
        return json.dumps(d)

    pathlib.Path.read_text = _read_text_with_override
