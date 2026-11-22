"""Suite-wide hooks.

The skip guard: with ``CF_SKIP_GUARD=1`` (set in CI) a run fails if any test skips that is not listed in
``ALLOWED_SKIPS``. A test that skips when its data or fixture is missing passes silently and checks nothing, so a new
skip has to be added here on purpose, with the reason. (Until 2026-11-21 the SSI override test skipped on every run
because the fixture held no SSI-eligible unit.)
"""
from __future__ import annotations

import os

import pytest

#: Node-id prefixes allowed to skip, and why.
ALLOWED_SKIPS = {
    "tests/tax_modeler/test_cex_loader.py::": "the CEX interview files are not committed",
    "tests/test_headline_figures.py::": "needs the local PUMS and tax-unit cache, which are not committed",
}


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    if os.environ.get("CF_SKIP_GUARD") != "1":
        return
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    skipped = [r.nodeid for r in (reporter.stats.get("skipped", []) if reporter else [])]
    unexpected = sorted({n for n in skipped if not n.startswith(tuple(ALLOWED_SKIPS))})
    if unexpected:
        lines = "\n  ".join(unexpected)
        print(f"\nCF_SKIP_GUARD: {len(unexpected)} test(s) skipped that tests/conftest.py does not allow:\n  {lines}\n"
              "Make them run, or add them to ALLOWED_SKIPS with the reason.", flush=True)
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
