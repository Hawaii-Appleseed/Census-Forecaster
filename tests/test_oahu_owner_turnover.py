"""scripts/conveyance/oahu_owner_turnover.py: the owner-roll sale classifier
and project keys on small synthetic rolls, the Maui composition and the
like-for-like ratio, and the committed files against the forecast's check
and the Maui extract they must share a snapshot with."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("oahu_owner_turnover", REPO / "scripts" / "conveyance" / "oahu_owner_turnover.py")
ot = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = ot
spec.loader.exec_module(ot)

pytestmark = pytest.mark.smoke

FEE = "Fee Owner"
BUYERS = [f"{s},ANN" for s in ("AKANA", "BALDWIN", "CHUN", "DOI", "ESPINDA", "FONG", "GOMES", "HIGA", "IGE", "JIM")]


@pytest.mark.parametrize(("name", "want"), [
    ("SMITH,JOHN/JONES,MARY", {"SMITH", "JONES"}),
    ("SMITH,JOHN & JONES,MARY", {"SMITH", "JONES"}),
    ("SMITH,JOHN AND JONES,MARY", {"SMITH", "JONES"}),
    ("LOUIE,THOMAS/CYNTHIA", {"LOUIE"}),                 # a given-name continuation adds nothing
    ("SMITH FAMILY TRUST", {"SMITH"}),
    ("TO,ANNA", {"TO"}),                                 # a one-word surname stays, generic or not
    ("ALOHA SUNSET HOLDINGS LLC", {"ALOHA", "SUNSET"}),
    ("", set()), ("   ", set()), (None, set()),
])
def test_tokens(name, want):
    assert ot.tokens(name) == want


def test_project_is_the_condo_tmk_or_the_five_digit_plat():
    got = ot.project(pd.Index(["450960760000", "450961760000", "450970760000", "450960760001", "450960760002"]))
    assert list(got) == ["plat 45096", "plat 45096", "plat 45097", "45096076", "45096076"]


def _roll(rows: list[tuple[str, str, str | None]]) -> pd.DataFrame:
    """(parid, owner type, name) rows as an owner roll, ownseq in row order."""
    d = pd.DataFrame(rows, columns=["parid", "owntype_desc", "taxbillown"])
    return d.assign(taxbillown1=None, ownseq=d.groupby("parid").cumcount())


# parid: (2024 roll, 2027 roll), each a list of (owner type, name); one plat each
SINGLE = {
    "110010010000": ([(FEE, "SMITH,JOHN")], [(FEE, "SMITH FAMILY TRUST")]),                       # into a trust
    "110020010000": ([(FEE, "LEE,KEN")], [(FEE, "LEE,KEN"), (FEE, "PARK,MIN")]),                   # spouse added
    "110030010000": ([(FEE, "NAKA,JOHN/OTA,MARY")], [(FEE, "OTA,MARY")]),                         # '/' part stays
    "110040010000": ([(FEE, "KIM,DAVID & PAK,SUE")], [(FEE, "PAK,SUE TR")]),                      # '&' part stays
    "110050010000": ([(FEE, "SMITH,JOHN")], [(FEE, "TANAKA,KEN")]),                               # unrelated buyer
    "110060010000": ([(FEE, "KAMEHAMEHA SCHOOLS")],
                     [(FEE, "KAMEHAMEHA SCHOOLS"), ("Lessee", "WONG,AL")]),                       # lessee appears
    "110070010000": ([(FEE, "KAMEHAMEHA SCHOOLS"), ("Lessee", "WONG,AL")],
                     [(FEE, "KAMEHAMEHA SCHOOLS")]),                                              # lessee goes
    "110080010000": ([(FEE, "BISHOP ESTATE"), ("Lessee", "WONG,AL")],
                     [(FEE, "BISHOP ESTATE"), ("Lessee", "ITO,JAN")]),                            # leasehold sale
    "110090010000": ([(FEE, None)], [(FEE, "TANAKA,KEN")]),                                       # blank before
    "110100010000": ([(FEE, "SMITH,JOHN")], [(FEE, "  ")]),                                       # blank after
}
TOWER = [f"22001001{u:04d}" for u in range(1, 11)]          # 10 units of one holder: developer first sales
NINE = [f"22002001{u:04d}" for u in range(1, 10)]           # 9 units: resales
LOTS = [f"33001{p:03d}0000" for p in range(95, 105)]        # 10 lots on plat 33001, across parcel 099/100
BULK = [f"44001001{u:04d}" for u in range(1, 6)]            # 5 units to one buyer
FOUR = [f"44002001{u:04d}" for u in range(1, 5)]            # 4 units to one buyer: resales


@pytest.fixture(scope="module")
def t() -> pd.DataFrame:
    old = [(p, k, n) for p, (a, _) in SINGLE.items() for k, n in a]
    new = [(p, k, n) for p, (_, b) in SINGLE.items() for k, n in b]
    for units, holder in ((TOWER, "TOWER DEVELOPMENT LLC"), (NINE, "NINE UNITS LLC"), (LOTS, "LOT BUILDER LLC")):
        old += [(p, FEE, holder) for p in units]
        new += [(p, FEE, b) for p, b in zip(units, BUYERS, strict=False)]
    for units, buyer in ((BULK, "MAKAI RENTALS LLC"), (FOUR, "MAUKA RENTALS LLC")):
        old += [(p, FEE, s) for p, s in zip(units, BUYERS, strict=False)]
        new += [(p, FEE, buyer) for p in units]
    return ot.transfers(_roll(old), _roll(new))


FLAGS = ["sale_like", "resale", "developer_first_sale", "bulk_buyer", "lessee_change"]
NOTHING, RESALE = (False, False, False, False, False), (True, True, False, False, False)


@pytest.mark.parametrize(("parid", "want"), [
    ("110010010000", NOTHING), ("110020010000", NOTHING), ("110030010000", NOTHING), ("110040010000", NOTHING),
    ("110050010000", RESALE),
    ("110060010000", (False, False, False, False, True)), ("110070010000", (False, False, False, False, True)),
    ("110080010000", RESALE),
    ("110090010000", NOTHING), ("110100010000", NOTHING),
])
def test_single_transfers(t, parid, want):
    assert tuple(bool(x) for x in t.loc[parid, FLAGS]) == want


@pytest.mark.parametrize(("units", "want"), [
    (TOWER, (True, False, True, False, False)),
    (NINE, RESALE),
    (LOTS, (True, False, True, False, False)),          # one plat, not split at the parcel's hundreds digit
    (BULK, (True, False, False, True, False)),
    (FOUR, RESALE),
])
def test_developer_and_bulk_buyer_rules(t, units, want):
    assert all(tuple(bool(x) for x in t.loc[p, FLAGS]) == want for p in units)


def test_maui_composition_on_synthetic_sales():
    u = pd.DataFrame([
        (2023, "2022/09/01", 5e5, 1, "0", ["H"]),              # before the repeat window
        (2025, "2024/10/01", 5e5, 1, "0", ["A"]),
        (2025, "2025/01/10", 5e5, 1, ot.MAUI_DEVELOPER, ["B"]),
        (2026, "2026/03/01", 5e5, 1, ot.MAUI_RELATED, ["A"]),  # A again
        (2026, "2026/04/01", 5e5, 1, "0", ["C"]),
        (2026, "2026/05/01", 5e5, 0, ot.MAUI_DEVELOPER, ["D"]),  # vacant land: not in the rates
        (2027, "2026/08/01", 5e6, 1, "0", ["E", "F"]),         # after FY2026: repeat window only
    ], columns=["fy", "date", "value", "improved", "validity", "parids"])
    c = ot.maui_composition(u, 2.0, "2026-09-22T06:02:10").set_index("band")
    assert list(c.index) == list(ot.BANDS)
    low = c.loc["<1M"]
    assert (low["n"], low["developer_share"], low["related_share"]) == (5, 0.2, 0.2)
    assert low["repeat_factor"] == round(4 / 3, 4)               # A, B, A, C: 4 sales of 3 parcels
    assert c.loc["4M+", ["n", "repeat_factor"]].isna().all()     # no FY2023-26 sale: E and F do not count
    assert set(c["repeat_window_start"]) == {"2024-08-01"} and set(c["repeat_window_end"]) == {"2026-08-01"}
    assert set(c["maui_sales_file_date"]) == {"2026-09-22T06:02:10"}


def test_maui_snapshot_refuses_cached_files_the_manifest_does_not_record(tmp_path, monkeypatch):
    names = {"sales": "sales.zip", "assessment": "assessment.zip", "dwelling_units": "units_ge5.json"}
    for f in names.values():
        (tmp_path / f).write_bytes(f.encode())
    (tmp_path / "maui_sources.json").write_text(json.dumps({"files": {
        k: {"sha256": hashlib.sha256(f.encode()).hexdigest(), "file_date": "2026-09-22T06:02:10"}
        for k, f in names.items()}}))

    def cached(name: str, refresh: bool = False) -> Path:
        assert not refresh                                        # never downloads
        return tmp_path / names[name]

    monkeypatch.setattr(ot, "REPO", tmp_path)
    monkeypatch.setattr(ot.mx, "OUT", tmp_path)
    monkeypatch.setattr(ot.mx, "download", cached)
    monkeypatch.setattr(ot.mx, "dwelling_units", lambda refresh: cached("dwelling_units", refresh))
    assert ot.maui_snapshot() == "2026-09-22T06:02:10"
    (tmp_path / "sales.zip").write_bytes(b"a newer weekly file")
    with pytest.raises(SystemExit, match="sales"):
        ot.maui_snapshot()


def test_like_for_like_is_one_when_oahu_turns_over_as_maui_does():
    expected, dev, rel, repeat, prec = 100.0, 0.13, 0.05, 1.04, 0.91
    visible = expected * (1 - dev - rel)             # Maui-rate sales that are neither developer nor related
    resales = visible / repeat / prec                # parcels changing hands, with the review's false positives
    assert ot.like_for_like(resales, expected, repeat, prec, dev, rel) == pytest.approx(1.0)
    # the shares are disjoint and come off together: not (1 - dev) / (1 + rel)
    assert ot.like_for_like(1.0, 1.0, 1.0, 1.0, 0.1328, 0.0517) == pytest.approx(1 / 0.8155)


def test_committed_ratio_matches_the_forecast_check():
    cells, comp = pd.read_csv(ot.OUT_CELLS), pd.read_csv(ot.OUT_MAUI)
    r = ot.pooled_ratio(cells, comp, cells["years"].iloc[0])
    check = ot.fc.oahu_turnover_ratio()
    for b in ot.BANDS:
        assert r.loc[b, "ratio"] == pytest.approx(check[b]["ratio"], rel=1e-9)
        assert r.loc[b, "rel_se"] == pytest.approx(check[b]["rel_se"], rel=1e-9)


def test_committed_maui_composition_is_the_snapshot_behind_maui_rates():
    comp = pd.read_csv(ot.OUT_MAUI).set_index("band")
    sv = pd.read_csv(ot.DATA / "maui_sales_by_value_fy2023_2026.csv").query("improved == 1")
    assert comp["n"].to_dict() == sv.groupby(ot.band(sv["value_lo"]))["count"].sum().to_dict()
    sales = json.loads((ot.DATA / "maui_sources.json").read_text())["files"]["sales"]
    assert set(comp["maui_sales_file_date"]) == {sales["file_date"]}
    assert ((comp["developer_share"] + comp["related_share"]) < 1).all()
