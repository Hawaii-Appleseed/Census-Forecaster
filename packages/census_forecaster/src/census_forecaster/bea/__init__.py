"""BEA (Bureau of Economic Analysis) client for the Regional dataset.

Used by `scripts/refresh_bea_anchors.py` to fetch the three BEA-derived
ACS anchor sources (Hawaii per-capita income, Honolulu metro RPP,
Hawaii state RPP housing) and persist them as bundled JSON files in
`data/anchors/`.

Currently only the Regional dataset is exposed (NIPA / IIP / etc. could
be added later if needed). The client mirrors the AcsClient pattern:
file-backed JSON cache, offline mode, atomic writes.
"""
from .client import (
    BEA_API_URL,
    BeaApiError,
    BeaClient,
)

__all__ = [
    "BEA_API_URL",
    "BeaClient",
    "BeaApiError",
]
