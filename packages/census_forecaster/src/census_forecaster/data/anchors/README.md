# Anchor data sources

Embedded historical series used as macro anchors for ACS projection.
Each file documents:

* **source** — original publisher and series ID where applicable
* **frequency** — release cadence
* **units** — what each value represents
* **last_refresh** — when the embedded data was last verified against the
  upstream publisher
* **limitations** — known caveats relevant to using this series as an
  ACS anchor

The values are annual averages or annual end-of-period readings. The
projection module converts them to YoY log-rates internally; downstream
weighting is derived from out-of-sample back-tests, not hardcoded.

| File | Series | Anchors | Source |
|---|---|---|---|
| `cpi_honolulu_allitems.json` | BLS CUUSA426SA0 | income (general inflation) | bls.gov |
| `cpi_honolulu_rent.json` | BLS CUUSA426SEHA | rent | bls.gov |
| `pce_deflator.json` | BEA Table 2.3.4 line 1 | income (national PCE) | bea.gov |
| `qcew_hawaii_wages.json` | BLS QCEW (state of HI, all industries) | income (wage) | bls.gov |
| `hud_fmr_honolulu.json` | HUD FMR (Honolulu MSA = Honolulu County, 2BR, by HUD fiscal year), from the `FMR_2Bed_1983_<year>.xlsx` history workbook | rent | huduser.gov |
| `fred_hi_hpi.json` | FRED HISTHPI (FHFA all-transactions) | home value | fred.stlouisfed.org |
| `bls_national_unemployment.json` | BLS LNS14000000 (CPS, SA, annual avg) | **not an anchor** — registration tried and rejected (blended-RMSE regression + rate-band violation; market_ml_ablation_2026-07-14.md). Instead feeds the ML `natl_unemp_*` leading-indicator feature (natl_unemp_ablation) + the causal screen | bls.gov |

`pce_deflator`, `qcew_hawaii_wages`, `hud_fmr_honolulu` and `fred_hi_hpi` are
rewritten whole, monthly, by `scripts/refresh_annual_anchors.py` (the
`refresh-data` workflow); do not hand-edit them. A hand-compiled version of each
sat from January 2026 and did not reproduce from the source it named.

To re-validate a series: open the upstream link in the JSON file and
compare. Any updates should be paired with a re-run of the calibration
back-test (`scripts/calibrate_anchors.py`) so weights track current data.
