"""The tax simulator's model version must match the files it was computed from.

``population.json``'s ``model_version`` hashes the population the page loads,
its metadata and ``site/assets/simulator/kernel.js``
(:func:`tax_modeler.simulator.web.model_version`). Share links carry it, so a
link made before a rebuild can say its numbers have moved. An edit to
``kernel.js`` without rerunning ``scripts/build_simulator_population.py``
leaves the version stale, and old links would get no notice; these tests fail
first. Fix by rebuilding the simulator data (``--from-saved`` is enough), then
``python scripts/build_site.py --import-runs tax-simulator``.
"""
from __future__ import annotations

import gzip
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from tax_modeler.artifacts import DIRTY_SUFFIX, git_sha_for_code
from tax_modeler.simulator.web import CODE_PATHS, model_version

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "site" / "data" / "tax-simulator"
KERNEL = REPO / "site" / "assets" / "simulator" / "kernel.js"

pytestmark = pytest.mark.smoke


def _meta() -> dict:
    return json.loads((DATA / "population.json").read_text())


def _recomputed(kernel_source: bytes) -> str:
    meta = _meta()
    blob = gzip.decompress((DATA / "population.bin.gz").read_bytes())
    # The version's date is the calibrated base's, which web_meta publishes
    # as the metadata's provenance.
    pop = SimpleNamespace(meta={"calibrated_base": meta["provenance"]})
    return model_version(pop, blob, meta, kernel_source)


def test_model_version_matches_the_committed_data_and_kernel():
    stored = _meta()["model_version"]
    assert _recomputed(KERNEL.read_bytes()) == stored, (
        f"site/data/tax-simulator/population.json says model_version {stored}, but the committed "
        "population and kernel.js give another; rebuild with "
        "`python scripts/build_simulator_population.py --from-saved`, then "
        "`python scripts/build_site.py --import-runs tax-simulator`")


def test_the_version_covers_the_kernel():
    assert _recomputed(KERNEL.read_bytes() + b"\n// edited\n") != _meta()["model_version"]


def test_the_manifest_cites_a_commit_that_holds_the_code():
    """The page's endnote cites the manifest's git_sha as the commit of the
    simulator's code. Built from uncommitted code, it reads "<HEAD>-dirty"
    (build_simulator_population.py); the capital-gains build once cited
    65e1a6e, the v1 kernel with none of that code. A -dirty build is fine
    while that code is still uncommitted, and wrong once it is committed,
    so this fails in CI and after the commit, not during the work."""
    sha = json.loads((DATA / "manifest.json").read_text())["git_sha"]
    assert re.fullmatch(r"[0-9a-f]{7,40}(-dirty)?", sha), sha
    if sha.endswith(DIRTY_SUFFIX):
        assert git_sha_for_code(CODE_PATHS, cwd=REPO).endswith(DIRTY_SUFFIX), (
            f"site/data/tax-simulator/manifest.json was built from uncommitted code (git_sha {sha}), "
            "and that code is now committed, so the page cites a commit without it. On the commit "
            "that holds the code, run `python scripts/build_simulator_population.py --from-saved`, "
            "`python scripts/build_site.py --import-runs tax-simulator` and "
            "`python scripts/build_site.py`, and commit the result")
