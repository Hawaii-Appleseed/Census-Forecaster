"""scripts/conveyance/honolulu_entity_share.py: the owner classifier and the
band weighting, on synthetic names and rolls, and the committed CSV's shape."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("honolulu_entity_share", REPO / "scripts" / "conveyance" / "honolulu_entity_share.py")
hes = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = hes
spec.loader.exec_module(hes)

pytestmark = pytest.mark.smoke


@pytest.mark.parametrize("name", [
    "PACIFIC VIEW HOLDINGS LLC", "OCEAN VIEW L.L.C.", "KAHALA 4 L L C", "ABC, INC.", "KAHALA CORP",
    "MAKAI CORPORATION", "DOE FAMILY LIMITED PARTNERSHIP", "XYZ LP", "XYZ L.P.", "SURF LLP", "DIAMOND HEAD LTD",
    "SMITH & COMPANY", "DOE PROPERTIES", "ALOHA INVESTMENTS", "KAIMANA KK", "SUNSET TRUST LLC",
])
def test_entities(name):
    assert hes.kind(name) == "entity"


@pytest.mark.parametrize("name", [
    "SMITH,JOHN TR", "SMITH,JOHN A TRS", "SMITH,JOHN CO-TR", "DOE,JANE TRUSTEE", "BANK OF HAWAII TR",
    "OCEAN HOLDINGS LLC TR", "DOE FAMILY TRUST", "DOE REVOCABLE TRUST", "SMITH,JOHN TTEE",
])
def test_trusts_and_trustees(name):
    assert hes.kind(name) == "trust"


@pytest.mark.parametrize("name", [
    "SMITH,JOHN", "SMITH,JOHN A/JANE B", "TRAN,COLTON", "INCE,JOHN", "CORPUZ,JOSE", "LLOYD,CORA", "LIMA,COREY",
    "GROUP,JOHN", "ESTATE OF DOE,JOHN", "", None,
])
def test_people_and_everything_else(name):
    assert hes.kind(name) == "individual"


def test_a_developer_holding_ten_units_in_a_project_is_not_an_entity_seller():
    tower = [(f"99999999{u:04d}", "TOWER DEVELOPER LLC", None) for u in range(1, 11)]
    roll = pd.DataFrame([
        *tower,
        ("999999990011", "SMITH,JOHN", None),
        ("999999990012", "UNIT 12 LLC", None),
        ("888888880000", "PACIFIC", "HOLDINGS LLC"),        # the name continues on the second line
        ("777777770000", "SMITH,JOHN TR", None),
    ], columns=["parid", "taxbillowner", "own2"])
    k = hes.owner_kinds(roll)
    assert (k[[p for p, _, _ in tower]] == "holder").all()
    assert k["999999990011"] == "individual"
    assert k["999999990012"] == "entity"
    assert k["888888880000"] == "entity"
    assert k["777777770000"] == "trust"


def test_seller_is_the_first_principal_owner_of_the_older_roll():
    roll = pd.DataFrame([
        ("111111110001", 0, "Condo Master", "MASTER LESSOR LLC", None),
        ("111111110001", 1, "Lessee", "SMITH,JOHN", None),
        ("111111110001", 2, "Fee Owner", "LAND CO LLC", None),
        ("222222220000", 1, "Fee Owner", "DOE,JANE", None),
        ("222222220000", 0, "Fee Owner", "BEACH HOUSE LLC", "BEACH HOUSE LLC"),
        ("333333330000", 0, None, "DOE,JANE TR", None),
    ], columns=["parid", "ownseq", "owntype_desc", "taxbillown", "taxbillown1"])
    k = hes.seller_kinds(roll)
    assert k.to_dict() == {"111111110001": "individual", "222222220000": "entity", "333333330000": "trust"}


def _homes(rows: list[tuple]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["parid", "value", "owner_occupied", "kind"])


def test_band_shares_weight_occupancy_by_its_sales():
    h = _homes([
        ("a", 4.0e6, 0, "entity"), ("b", 4.5e6, 0, "entity"), ("c", 5.9e6, 0, "trust"), ("d", 5.0e6, 0, "individual"),
        ("e", 4.2e6, 1, "entity"), ("f", 4.2e6, 1, "trust"), ("g", 4.2e6, 1, "individual"), ("h", 4.3e6, 1, "individual"),
        ("i", 4.4e6, 1, "individual"),
        ("j", 6.0e6, 0, "holder"), ("k", 7.0e6, 0, "entity"),       # a developer's unit is not an entity seller
        ("l", 3.99e6, 0, "entity"),                                  # below the bands
        ("m", 25e6, 0, "entity"),
    ])
    cells = pd.DataFrame({"rb": [4e6, 4e6, 5e6, 5e6, 6e6, 8e6, 20e6], "owner_occupied": [0, 1, 0, 1, 0, 1, 0],
                          "resales": [4, 1, 2, 1, 3, 1, 1], "developer_first_sales": [2, 0, 0, 0, 0, 0, 0]})
    t = hes.entity_by_band(h, cells).set_index("band_lo")
    assert t.index.tolist() == [4e6, 6e6, 10e6] and t.loc[10e6, "band_hi"] == np.inf
    lo = t.loc[4e6]
    assert (lo.homes_nonowner_occupied, lo.homes_owner_occupied) == (4, 5)
    assert (lo.entity_share_nonowner_occupied, lo.entity_share_owner_occupied) == (0.5, 0.2)
    assert (lo.resales_nonowner_occupied, lo.resales_owner_occupied, lo.developer_first_sales) == (6, 2, 2)
    assert lo.seller_weighted_entity_share == pytest.approx((6 * 0.5 + 2 * 0.2) / 10)
    mid = t.loc[6e6]
    assert mid.entity_share_nonowner_occupied == 0.5 and mid.homes_owner_occupied == 0
    assert mid.seller_weighted_entity_share == pytest.approx(3 * 0.5 / 4)
    assert t.loc[10e6, "seller_weighted_entity_share"] == 1.0


def test_committed_csv_holds_only_band_aggregates():
    t = pd.read_csv(hes.OUT)
    assert t.columns.tolist() == [
        "band_lo", "band_hi", "homes_nonowner_occupied", "entity_share_nonowner_occupied", "homes_owner_occupied",
        "entity_share_owner_occupied", "resales_nonowner_occupied", "resales_owner_occupied", "developer_first_sales",
        "seller_weighted_entity_share", "seller_entity_share_resales", "owndat_taxyr", "owndat_pulled",
        "resale_window_start", "resale_window_end"]
    assert t["band_lo"].tolist() == [4e6, 6e6, 10e6] and t["band_hi"].tolist() == [6e6, 10e6, np.inf]
    shares = t.filter(like="share")
    assert ((shares >= 0) & (shares <= 1)).all().all()
    sales = t["resales_nonowner_occupied"] + t["resales_owner_occupied"] + t["developer_first_sales"]
    again = (t["resales_nonowner_occupied"] * t["entity_share_nonowner_occupied"]
             + t["resales_owner_occupied"] * t["entity_share_owner_occupied"]) / sales
    assert t["seller_weighted_entity_share"].to_numpy() == pytest.approx(again.to_numpy(), abs=1e-3)
    assert (t["seller_weighted_entity_share"] <= t["entity_share_nonowner_occupied"]).all()
    # the check from the sellers' own names agrees within sampling error
    se = np.sqrt(t["seller_entity_share_resales"] * (1 - t["seller_entity_share_resales"]) / sales)
    assert ((t["seller_weighted_entity_share"] - t["seller_entity_share_resales"]).abs() < 2 * se).all()
