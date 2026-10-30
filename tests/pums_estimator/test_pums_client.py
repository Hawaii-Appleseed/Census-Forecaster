"""Tests for fetch_pums via mocked requests."""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
import requests
from pums_estimator.pums.client import CENSUS_API_BASE, fetch_pums

_HEADERS = ["SERIALNO", "PUMA", "WGTP", "VEH", "state", "public use microdata area"]
_ROWS = [
    _HEADERS,
    ["HI001", "00100", "87", "2", "15", "00100"],   # valid, weight=87
    ["HI002", "00100", "0",  "1", "15", "00100"],   # weight=0 → excluded
    ["HI003", "00200", "42", "0", "15", "00200"],   # valid, weight=42
]


def _mock_resp(rows):
    m = MagicMock()
    m.raise_for_status = MagicMock()
    m.json.return_value = rows
    return m


class TestFetchPums:
    def test_fetch_pums_constructs_correct_url(self):
        with patch("pums_estimator.pums.client.requests.get") as mock_get:
            mock_get.return_value = _mock_resp([_HEADERS])
            fetch_pums("15", ["VEH"], year=2022, vintage="1")
            url = mock_get.call_args[0][0]
            assert url == f"{CENSUS_API_BASE}/2022/acs/acs1/pums"

    def test_fetch_pums_parses_records(self):
        with patch("pums_estimator.pums.client.requests.get") as mock_get:
            mock_get.return_value = _mock_resp(_ROWS)
            records = fetch_pums("15", ["VEH"], year=2022, vintage="1")
        assert len(records) == 2
        serials = {r.serial for r in records}
        assert "HI001" in serials
        assert "HI003" in serials

    def test_fetch_pums_filters_zero_weight(self):
        with patch("pums_estimator.pums.client.requests.get") as mock_get:
            mock_get.return_value = _mock_resp(_ROWS)
            records = fetch_pums("15", ["VEH"])
        assert all(r.weight > 0 for r in records)
        assert not any(r.serial == "HI002" for r in records)

    def test_fetch_pums_uses_env_api_key(self):
        with patch("pums_estimator.pums.client.requests.get") as mock_get:
            mock_get.return_value = _mock_resp([_HEADERS])
            with patch.dict(os.environ, {"CENSUS_API_KEY": "TEST_KEY_123"}):
                fetch_pums("15", ["VEH"])
            params = mock_get.call_args.kwargs["params"]
            assert params.get("key") == "TEST_KEY_123"

    def test_fetch_pums_raises_on_http_error(self):
        with patch("pums_estimator.pums.client.requests.get") as mock_get:
            m = MagicMock()
            m.raise_for_status.side_effect = requests.HTTPError("404 Not Found")
            mock_get.return_value = m
            with pytest.raises(requests.HTTPError):
                fetch_pums("15", ["VEH"])
