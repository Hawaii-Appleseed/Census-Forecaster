"""The committed estimates site must be exactly what its committed data produces.

site/ is deployed as-is by .github/workflows/pages.yml, with no build step,
so a data refresh without a rebuild (or a hand edit of a generated page) would
publish numbers that disagree with the downloadable CSVs. These tests fail
first. Fix by running `python scripts/build_site.py` and committing site/.
"""
from __future__ import annotations

import importlib.util
import json
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


ESTIMATE_PAGES = sorted((REPO / "site").glob("*/index.html"))


def test_every_estimate_is_on_the_home_page():
    home = (REPO / "site" / "index.html").read_text()
    assert len(ESTIMATE_PAGES) == len(build_site.ESTIMATES)
    for est in build_site.ESTIMATES:
        assert f'href="{est.slug}/"' in home, est.slug


@pytest.mark.parametrize("page", ESTIMATE_PAGES, ids=lambda p: p.parent.name)
def test_endnotes_numbered_in_reading_order(page):
    text = page.read_text()
    first_seen = []
    for n in re.findall(r'href="#note-(\d+)"', text):
        if n not in first_seen:
            first_seen.append(n)
    assert first_seen == [str(i) for i in range(1, len(first_seen) + 1)]
    assert len(set(re.findall(r'id="(ref-\d+)"', text))) == len(re.findall(r'id="ref-\d+"', text))


@pytest.mark.parametrize("page", ESTIMATE_PAGES, ids=lambda p: p.parent.name)
def test_downloads_resolve(page):
    hrefs = re.findall(r'href="(\.\./data/[^"]+)"', page.read_text())
    assert hrefs
    for href in hrefs:
        assert (page.parent / href).resolve().exists(), href


def test_tax_simulator_scores_act24_as_the_act24_page_does():
    """The simulator's current law is the Act 24 page's: its golden
    act24_vs_act46 case (the model's own answer, which the kernel tests
    reproduce) equals the Act 24 page's published bracket change, static and
    after response, every scenario and year, to the page's two decimals. Both
    score capital gains on the DOTAX-anchored base with the statutory
    alternative tax; a change to either that the other does not follow fails
    here."""
    import gzip
    fis = build_site.read_csv(REPO / "site" / "data" / "act-24" / "fiscal_by_scenario.csv")
    golden = json.loads(gzip.open(REPO / "tests" / "simulator" / "golden.json.gz").read())
    rev = next(c for c in golden["cases"] if c["name"] == "act24_vs_act46")["expected"]["revenue"]
    assert len(rev) == 15
    for r in rev:
        pub = build_site._fiscal(fis, r["scenario"].upper(), r["tax_year"])
        assert round(r["static_$M"], 2) == pub["bracket_delta_static_$M"], r
        assert round(r["behavioral_$M"], 2) == pub["bracket_delta_post_$M"], r


def test_tax_simulator_compares_with_the_capital_gains_page():
    """The simulator page quotes its capital gains method next to the capital
    gains page. build_simulator_population.py takes that page's figures from
    site/data/capital-gains/; they must be the figures committed for it, or
    the comparison is with a stale run."""
    d = REPO / "site" / "data"
    rev = build_site.read_csv(d / "capital-gains" / "revenue_by_year.csv")
    cg = json.loads((d / "tax-simulator" / "cg_page_comparison.json").read_text())["rows"]
    assert {(r["option"], r["tax_year"]) for r in cg} == {(o, y) for o in ("cap9", "ordinary") for y in range(2027, 2032)}
    for r in cg:
        pub = build_site._rev(rev, r["tax_year"], "act24", r["option"])
        assert r["published_static_$M"] == pub["static_M"], r
        assert r["published_behavioral_$M"] == pub["behavioral_M"], r


def test_tax_simulator_states_its_reading_of_the_gains_cap():
    """The page says the alternative tax is computed as the statute defines
    it. For schedules whose rates fall back below the gains rate, the
    statute's income "taxed at a rate below" it is read as the income below
    the first bracket whose rate reaches it (cg_alternative.cap_floor); the
    page must say so."""
    text = (REPO / "site" / "tax-simulator" / "index.html").read_text()
    assert "as the statute defines it" in text
    assert "can be read more than one way" in text
    assert "first bracket whose rate reaches the gains rate" in text


def test_okina_is_the_real_character():
    for page in (REPO / "site").rglob("*.html"):
        text = page.read_text()
        assert "Hawai'i" not in text and "Hawaiʻi" in text, page  # okina-lint:ignore
        assert not re.search(r"Hawai[‘’`]i", text), page  # okina-lint:ignore
