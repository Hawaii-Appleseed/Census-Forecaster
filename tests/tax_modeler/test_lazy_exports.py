"""tax_modeler's and tax_modeler.calibration's lazy (PEP 562) exports."""
from __future__ import annotations

import subprocess
import sys

import pytest
import tax_modeler
import tax_modeler.calibration as calibration


@pytest.mark.parametrize("pkg", [tax_modeler, calibration], ids=lambda p: p.__name__)
def test_every_export_resolves(pkg):
    for name in pkg.__all__:
        assert getattr(pkg, name) is not None, name


@pytest.mark.parametrize("pkg", [tax_modeler, calibration], ids=lambda p: p.__name__)
def test_every_lazy_name_is_exported_from_its_module(pkg):
    import importlib

    for name, module in pkg._LAZY.items():
        assert getattr(pkg, name) is getattr(importlib.import_module(module, pkg.__name__), name)


def test_a_submodule_is_reachable_as_an_attribute():
    assert tax_modeler.units.base.TaxUnitConstructor is tax_modeler.TaxUnitConstructor
    assert calibration.dotax_base.__name__ == "tax_modeler.calibration.dotax_base"


def test_unknown_name_is_an_attribute_error():
    with pytest.raises(AttributeError):
        tax_modeler.no_such_name  # noqa: B018
    assert not hasattr(calibration, "__wrapped__")


def test_joint_ipf_is_the_function_after_its_module_loads():
    """joint_ipf names both a submodule and the function the package exports."""
    import tax_modeler.calibration.joint_ipf  # noqa: F401
    from tax_modeler.calibration import joint_ipf

    assert callable(joint_ipf) and joint_ipf.__module__ == "tax_modeler.calibration.joint_ipf"


def test_importing_tax_modeler_loads_no_submodule():
    code = ("import sys, tax_modeler; "
            "print(sorted(m for m in sys.modules if m.startswith('tax_modeler.')))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"


def test_importing_tax_modeler_configures_logging_as_before():
    code = ("import logging, tax_modeler; h = logging.getLogger().handlers; "
            "print(logging.getLogger().level, h[0].formatter._fmt)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "20 %(asctime)s - %(name)s - %(levelname)s - %(message)s"
