"""Throttle handling for the DOTAX monthly-collections refresh.

files.hawaii.gov sits behind Cloudflare, which answered a GitHub runner with
429 on the first file (2026-09-23). These pin the contract: retry, then stop
without touching the committed archive, and only fail the step when the
caller did not ask to tolerate throttling.
"""
import email.message
import json
import urllib.error

import pytest

pytest.importorskip("openpyxl")

from census_forecaster.scripts import refresh_dotax_collections as dotax


def _http_error(code, retry_after=None):
    hdrs = email.message.Message()
    if retry_after is not None:
        hdrs["Retry-After"] = str(retry_after)
    return urllib.error.HTTPError("https://x/y.xlsx", code, "err", hdrs, None)


def test_fetch_retries_429_then_raises_throttled(monkeypatch):
    calls, waits = [], []

    def urlopen(req, timeout):
        calls.append(req.full_url)
        raise _http_error(429, retry_after=7)

    monkeypatch.setattr(dotax.urllib.request, "urlopen", urlopen)
    with pytest.raises(dotax.Throttled):
        dotax._fetch_xlsx("https://x/y.xlsx", sleep=waits.append)
    assert len(calls) == len(dotax.RETRY_WAITS) + 1
    assert 7 in waits                      # Retry-After is honoured


def test_fetch_404_is_none_without_retry(monkeypatch):
    calls = []

    def urlopen(req, timeout):
        calls.append(1)
        raise _http_error(404)

    monkeypatch.setattr(dotax.urllib.request, "urlopen", urlopen)
    assert dotax._fetch_xlsx("https://x/y.xlsx", sleep=lambda s: None) is None
    assert calls == [1]


def _throttle_everything(monkeypatch):
    def fetch(url, **kw):
        raise dotax.Throttled("HTTP 429 from " + url)
    monkeypatch.setattr(dotax, "_fetch_xlsx", fetch)


def _bundle(tmp_path):
    out = tmp_path / "dotax.json"
    out.write_text(json.dumps({
        "fetch_date": "2026-08-06",
        "value_asof": {"2026-01": "202601collec.xlsx"},
        "monthly": {"2026-01": {"iit_withholding": 1.0}},
    }))
    return out


def test_throttle_tolerated_keeps_archive(monkeypatch, tmp_path, capsys):
    _throttle_everything(monkeypatch)
    out = _bundle(tmp_path)
    assert dotax.main(["--out", str(out), "--months-back", "3", "--tolerate-throttle"]) == 0
    kept = json.loads(out.read_text())
    assert kept["monthly"] == {"2026-01": {"iit_withholding": 1.0}}
    assert kept["fetch_date"] == "2026-08-06"   # not dressed up as a fresh fetch
    assert "::warning title=DOTAX throttled::" in capsys.readouterr().out


def test_throttle_fails_without_flag(monkeypatch, tmp_path):
    _throttle_everything(monkeypatch)
    out = _bundle(tmp_path)
    assert dotax.main(["--out", str(out), "--months-back", "3"]) == 1
    assert json.loads(out.read_text())["monthly"] == {"2026-01": {"iit_withholding": 1.0}}
