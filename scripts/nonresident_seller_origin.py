"""Where Oʻahu's out-of-state home sellers live, for the nonresident capital-gains
response (``tax_modeler.calibration.nonresident_cg``).

Usage:
  python scripts/nonresident_seller_origin.py

Reads the two cached Honolulu owner rolls and the resales between them, as
scripts/conveyance/oahu_owner_turnover.py finds them (December 5, 2023 to the
latest OWNINFO pull; sale-like, not a developer's first sale or a bulk
purchase), and the City's tax-year-2026 assessed values of improved homes.
Each resale's seller is the principal owner in the 2023 roll; the seller's
home is that owner's mailing address: a US state where the last address line
ends in a state code and ZIP, a country where it ends in a country name.

Writes packages/tax_modeler/src/tax_modeler/data/calibration/
nonresident_seller_origin_oahu.csv: resales and their 2026 assessed value
($M) by seller origin (a state code, ``foreign:<country>`` or ``unknown``).
Hawaiʻi addresses are kept as ``HI``. The cached rolls carry owner names and
are never committed; this file holds counts and dollars only.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts" / "conveyance"))
OUT = (REPO / "packages" / "tax_modeler" / "src" / "tax_modeler" / "data" / "calibration"
       / "nonresident_seller_origin_oahu.csv")

STATES = frozenset([
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY",
    "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH",
    "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    "PR", "GU", "VI", "AS", "MP", "AA", "AE", "AP",   # territories; military post offices (AA/AE/AP)
])
COUNTRIES = ("JAPAN", "CANADA", "KOREA", "SINGAPORE", "HONG KONG", "AUSTRALIA", "CHINA", "TAIWAN",
             "NEW ZEALAND", "UNITED KINGDOM", "ENGLAND", "GERMANY", "SWITZERLAND", "FRANCE", "PHILIPPINES",
             "MEXICO", "THAILAND", "INDONESIA", "MALAYSIA", "NETHERLANDS", "ITALY", "SPAIN", "BRAZIL")
_US = re.compile(r"\b([A-Z]{2})\.?\s+\d{5}(?:-?\d{4})?$")


def origin(lines: list[str]) -> str:
    """Seller origin from an owner's address lines (last non-blank line first)."""
    for line in reversed([str(x).upper().strip() for x in lines if isinstance(x, str) and x.strip()]):
        m = _US.search(line)
        if m and m.group(1) in STATES:
            return m.group(1)
        for c in COUNTRIES:
            if line.endswith(c):
                return f"foreign:{'UNITED KINGDOM' if c == 'ENGLAND' else c}"
        return "unknown"
    return "unknown"


def main() -> int:
    import oahu_owner_turnover as oot

    (start, old), (end, new) = oot.owner_rolls(False)
    t = oot.transfers(old, new)
    resales = t.index[t["resale"]]
    sellers = (old[old["owntype_desc"].ne("Condo Master")].sort_values(["parid", "ownseq"])
               .groupby("parid")[["address1", "address2", "address3"]].first())
    stock = oot.oahu_stock().set_index("parid")
    d = sellers.reindex(resales).join(stock["value"], how="inner")
    d["origin"] = [origin([a, b, c]) for a, b, c in zip(d["address1"], d["address2"], d["address3"], strict=True)]
    out = (d.groupby("origin").agg(resales=("value", "size"), value_M=("value", "sum"))
           .assign(value_M=lambda x: (x["value_M"] / 1e6).round(3))
           .sort_values("value_M", ascending=False).reset_index())
    out = out.assign(window_start=start, window_end=end)
    out.to_csv(OUT, index=False)
    print(f"resales {start} to {end}: {len(d):,} improved homes, ${d['value'].sum() / 1e9:.2f}B assessed")
    print(out.head(25).to_string(index=False))
    print(f"wrote {OUT.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
