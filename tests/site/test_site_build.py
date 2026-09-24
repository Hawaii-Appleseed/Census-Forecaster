"""The committed estimates site must be exactly what its committed data produces.

site/ is deployed as-is by .github/workflows/pages.yml, with no build step,
so a data refresh without a rebuild (or a hand edit of a generated page) would
publish numbers that disagree with the downloadable CSVs. These tests fail
first. Fix by running `python scripts/build_site.py` and committing site/.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("build_site", REPO / "scripts" / "build_site.py")
build_site = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = build_site   # dataclasses resolve their module through sys.modules
spec.loader.exec_module(build_site)

pytestmark = pytest.mark.smoke


def test_committed_site_matches_a_fresh_build(tmp_path):
    written = build_site.build(out=tmp_path)
    for path in written:
        rel = path.relative_to(tmp_path)
        committed = REPO / "site" / rel
        assert committed.exists(), f"site/{rel} is missing; run scripts/build_site.py"
        assert committed.read_text() == path.read_text(), (
            f"site/{rel} is stale; run `python scripts/build_site.py` and commit site/")


def test_endnotes_numbered_in_reading_order():
    text = (REPO / "site" / "capital-gains" / "index.html").read_text()
    first_seen = []
    for n in re.findall(r'href="#note-(\d+)"', text):
        if n not in first_seen:
            first_seen.append(n)
    assert first_seen == [str(i) for i in range(1, len(first_seen) + 1)]
    assert len(set(re.findall(r'id="(ref-\d+)"', text))) == len(re.findall(r'id="ref-\d+"', text))


def test_downloads_resolve():
    page = REPO / "site" / "capital-gains" / "index.html"
    for href in re.findall(r'href="(\.\./data/[^"]+)"', page.read_text()):
        assert (page.parent / href).resolve().exists(), href


def test_okina_is_the_real_character():
    for page in (REPO / "site").rglob("*.html"):
        text = page.read_text()
        assert "Hawai'i" not in text and "Hawaiʻi" in text, page  # okina-lint:ignore
        assert not re.search(r"Hawai[‘’`]i", text), page  # okina-lint:ignore
