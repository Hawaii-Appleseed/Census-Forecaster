# Registry-pattern migration — scope + status

**Status (2026-07-16): candidate 1 DONE, candidate 2 declined.** Written
after the `natl_unemp` → `NATIONAL_SERIES` migration proved the pattern: one
registry tuple drives fetch, injection, column names, and the row-builder
loop, so adding or moving a series is a one-line spec instead of a 3-file
edit. The county migration below has since been executed with golden-row
equivalence (all 13,174 × 67 feature values bit-identical).

## The pattern being replicated

A "channel" is a family of auxiliary ML feature columns fed from an external
dataset. The registry form gives each channel: a spec tuple (single source
of truth), one generic injection block, generated column names, one generic
`_build_row` reader loop, and one loader. The bespoke form scatters ~30
references per channel across `ml_features.py`, `ml_trend.py`, and
`calibration.py` (sentinel + param + injection + column block + reader block
+ loader + threading at 2 sites + calibration threading).

## Inventory of remaining bespoke channels

Measured references across the three files (2026-07-15):

| channel | shape | status |
|---|---|---|
| `county_data` (bps / saipe / laus) | `{name: {geoid: {year: val}}}` | ✅ **registry (3 series, 12 cols)** — migrated 2026-07-16 |
| `national_data` (national macro) | `{name: {year: val}}` | ✅ registry (14 series, 22 cols) |
| `market_data` (screen-gated momenta) | `{name: {year: val}}` | **half-generic** — injection generic, reader hardcoded; declined below |

Already registry/spec-driven elsewhere (no work needed): the anchor registry
(`_REGISTRY_SPEC`), `BEA_ANCHOR_SPECS`, the BLS panel area×kind grid,
`markets/universe.TICKERS`, and the screen's `MONTHLY_TARGETS` /
`NATIONAL_PREDICTORS` / `HYPOTHESIS_PAIRS`.

## Candidate 1 — county-level registry (`bps`/`saipe`/`laus`) — ✅ DONE (2026-07-16)

Executed exactly as scoped. `COUNTY_SERIES` with two column policies:

- `log_lags3_mean` (BPS): log-transformed lag0/1/2 + mean of valid lags
- `level_lags3_mean` (SAIPE, LAUS): raw lag0/1/2 + mean of valid lags

Collapsed: three params → one `county_data: {name: {geoid: {year: val}}}`;
three loaders → one `load_county_data()`; three reader blocks → one loop;
three injection blocks → one. Column names are unchanged
(`bps_log_lag0…`, `saipe_lag0…`, `laus_lag0…`) — the policies were designed
around the existing names, so nothing downstream renames.

**Verification — golden-row equivalence.** Feature rows for 4 indicators
(13,174 rows × 67 columns) were captured from the pre-migration code and
compared byte-for-byte after: **bit-identical**, including NaN placement and
row metadata. Plus the BPS ablation re-run (its `county_data` with/without
`"bps"` arm exercises the new toggle end-to-end).

**One latent bug fixed en route.** `test_zero_permits_not_stored` was named
and commented as "zero treated as missing" but the injection guard was
`>= 0`, so zeros WERE stored — the test never asserted the zero case, so it
passed regardless. The registry unified the guard to `> 0` (matching the
documented intent; the readers NaN'd zeros anyway, so features are identical
— proved by the golden), and the test now actually asserts it.

## Candidate 2 — market channel reader — DECLINED (revisit if channels grow)

`market_data` injection is already generic, but the reader hardcodes the
three channel sentinels into four `mkt_*` columns. Folding the channels into
a registry (source `MARKET_SIGNALS`, policies `level1` / `level_lag1`) is a
small win (4 columns) and slightly awkward: market channels are
**screen-gated** — they appear/disappear with `selected_signals.json` — so
their spec is dynamic where the registry is static. Recommendation: leave
as-is unless the screen grows more channels; the mkt block is small and
stable.

## Not candidates

- `tax_modeler` growth-rate blending — different package, different pattern.
- The anchor registry — already the repo's original registry precedent.
- Kalman / projection internals — cherry-picked by
  Housing-Affordability-Tracker; do not touch for cosmetic reasons.

## Outcome

Both registry-eligible channels are now registry-driven (`county_data`,
`national_data`). The aux feature block is fully generated from two
registries plus the small hardcoded `mkt_*` block. Adding a future
county-level indicator (building costs, migration flows) or national series
is a one-line spec entry.

The reusable recipe, if a third channel family ever appears:
1. Capture golden feature rows from the current code FIRST.
2. Design column policies around the **existing** column names so nothing
   renames.
3. Collapse param → registry-driven `*_data` dict; injection/reader/loader
   → one generic loop each.
4. Prove bit-identical rows against the golden; re-run the affected
   ablation as behavioral proof.
