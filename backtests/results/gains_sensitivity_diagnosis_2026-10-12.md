# Gains growth-shift sensitivity: diagnosis (2026-10-12)

Branch feat/aging-inputs @ 0127c7c. MID, CD2, vs Act 46, statutory alternative tax. Diagnosis only; nothing in defaults, the forecast doc or the site changed.

## Verdict

The +$620M is carried by the $1M+ class (+$673M of the five-year static delta; the synthetic tail alone is +$612M) through an anchoring-order artifact (ii), not Act 24 economics. The DOTAX anchor pins each class's gains **dollars** to a target that grows with wages, not with the gains shift (`cg_growth` model "window"), so `anchor_factors` shrinks k (1mp 1.06 -> 0.54 in 2027, 1.00 -> 0.22 in 2031) and the shift survives only as extra non-gains income: the $1M+ class's income rises 2.1x by 2031 (10.9B -> 25.2B) with gains dollars unchanged, and that extra ordinary income is taxed at the Act 24 top rates (which are what Act 24 changes). Holding k at its base value (gains really grow) gives a total of $608.4M (delta -$83.5M); holding ordinary income at base gives $633.0M, so 100%+ of the 620 is this channel (it explains all 620 and overshoots by the 51-59M residual, which is within-class redistribution of gains dollars toward gains-heavy units). Minimal fix (not implemented): make the anchor target follow the same shifted gains growth (grow the Table 21 target by the shift, or apply factors computed on the unshifted population as a base k), or, for the band, re-anchor on gains-only so that shifted gains do not become reclassified ordinary income. The published sigma band for gains (248.8 SD) should be treated as overstated until then.

Direct unit check (`gains_unit_test.py`, TY2029): changing only a unit's gains (gains +50%, ordinary income held) leaves Act 24 minus Act 46 liability exactly unchanged on all 5 $1M+ units and all 5 $200K-$1M units (statute alt-tax: 7.25% in both laws). Adding ordinary income (+25% of income, gains dollars held) raises it, e.g. $1.04M unit +$4.4K -> +$7.8K, the $12.3M unit $134K -> $195K. So gains do not move the difference; ordinary income does, and the anchored shifted run manufactures ordinary income.

## Step 1: runs

`forecast_sb3125_enhanced.py --cd 2 --no-replicate-se`, all four scenarios (script has no MID-only flag). MID totals: base 86.93, 114.38, 135.67, 172.11, 182.81 = **$691.90M** (confirmed); shifted 138.71, 192.43, 251.01, 332.09, 397.72 = $1,311.96M; delta +$620.06M by year +51.8, +78.1, +115.3, +160.0, +214.9. Logs and outputs in /tmp/gs (not committed). `scripts/diagnostics/gains_shift_decomp.py` (MID-only driver, same calls as `run_one_scenario`) reproduces both totals to the cent, so the hold runs below are on the same pipeline.

### Decomposition of delta (shift - base), $M

| term | 2027 | 2028 | 2029 | 2030 | 2031 | 5-yr |
|---|--:|--:|--:|--:|--:|--:|
| Act 46 baseline tax (level) | +358.1 | +549.1 | +768.5 | +1064.7 | +1440.6 | +4180.9 |
| Act 24 tax, static (level) | +414.3 | +634.6 | +895.1 | +1241.1 | +1679.8 | +4864.9 |
| static bracket delta | +56.2 | +85.5 | +126.7 | +176.4 | +239.2 | +684.0 |
| behavioral (ETI) | -4.4 | -7.3 | -11.2 | -16.4 | -24.3 | -63.6 |
| post-response bracket | +51.8 | +78.2 | +115.5 | +160.0 | +214.9 | +620.4 |
| credits | -0.0 | -0.2 | -0.1 | +0.0 | +0.0 | -0.3 |
| TOTAL | +51.8 | +78.1 | +115.3 | +160.0 | +214.9 | +620.1 |

Base levels, 5-yr: static 322.2, ETI -84.9, post 237.2, credit 454.7, total 691.9

### Tail vs PUMS (static delta contribution, delta shift-base)

| part | 2027 | 2028 | 2029 | 2030 | 2031 | 5-yr |
|---|--:|--:|--:|--:|--:|--:|
| $1M+ synthetic tail static | +52.8 | +79.9 | +113.4 | +156.5 | +209.5 | +612.1 |
| PUMS units static | +3.4 | +5.6 | +13.3 | +19.9 | +29.7 | +71.9 |
| tail Act 46 tax | +288.9 | +437.5 | +617.5 | +850.0 | +1139.5 | +3333.3 |
| PUMS Act 46 tax | +69.2 | +111.6 | +151.0 | +214.7 | +301.1 | +847.6 |

### By AGI class (rank-assigned DOTAX classes): static delta Act24-Act46 and Act 46 tax, base -> shift, $M

TY2027

| class | units base | units shift | income base | income shift | gains $ base | gains $ shift | Act46 tax base | shift | static d base | shift | d static |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| lt100 | 501,163 | 501,163 | 21,068 | 21,073 | 0 | 0 | 453 | 454 | -18.4 | -18.4 | +0.0 |
| 100_150 | 64,222 | 64,222 | 9,331 | 9,485 | 166 | 166 | 378 | 387 | -6.3 | -6.3 | -0.0 |
| 150_200 | 29,693 | 29,700 | 6,110 | 6,217 | 172 | 172 | 272 | 278 | -2.9 | -2.9 | +0.0 |
| 200_300 | 20,934 | 20,927 | 6,122 | 6,371 | 299 | 299 | 302 | 317 | -0.6 | -0.3 | +0.4 |
| 300_400 | 6,452 | 6,470 | 2,618 | 2,735 | 245 | 245 | 150 | 159 | 2.5 | 3.1 | +0.6 |
| 400_1m | 6,516 | 6,222 | 4,237 | 4,238 | 718 | 718 | 266 | 272 | 6.7 | 7.7 | +1.0 |
| 1mp | 2,180 | 2,456 | 8,785 | 12,055 | 2,873 | 2,873 | 704 | 1,017 | 69.3 | 123.5 | +54.3 |

TY2031

| class | units base | units shift | income base | income shift | gains $ base | gains $ shift | Act46 tax base | shift | static d base | shift | d static |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| lt100 | 501,160 | 501,171 | 24,504 | 24,529 | 0 | 0 | 513 | 515 | -24.8 | -24.8 | -0.0 |
| 100_150 | 64,228 | 64,209 | 10,853 | 11,539 | 193 | 193 | 399 | 430 | -10.2 | -10.2 | +0.0 |
| 150_200 | 29,702 | 29,703 | 7,110 | 7,580 | 199 | 199 | 294 | 325 | -4.0 | -3.4 | +0.7 |
| 200_300 | 20,922 | 20,930 | 7,121 | 8,248 | 347 | 347 | 326 | 401 | 1.3 | 6.1 | +4.8 |
| 300_400 | 6,475 | 6,469 | 3,021 | 3,508 | 284 | 284 | 165 | 194 | 7.0 | 10.7 | +3.7 |
| 400_1m | 5,948 | 3,649 | 4,323 | 2,785 | 834 | 834 | 261 | 165 | 14.0 | 6.1 | -8.0 |
| 1mp | 2,726 | 5,029 | 10,923 | 25,210 | 3,337 | 3,337 | 855 | 2,225 | 97.3 | 335.3 | +238.0 |

5-yr static delta by class (shift - base): lt100 -0.0, 100_150 -0.0, 150_200 +1.4, 200_300 +10.8, 300_400 +10.1, 400_1m -10.9, 1mp +672.6

### Anchor factors k (1mp / 400_1m / 300_400 / 200_300), base vs shift

| year | base | shift |
|---|---|---|
| 2027 | 1.058 / 1.948 / 1.833 / 0.971 | 0.535 / 1.164 / 1.006 / 0.534 |
| 2028 | 1.039 / 2.050 / 1.810 / 0.961 | 0.430 / 1.035 / 0.816 / 0.434 |
| 2029 | 1.027 / 2.074 / 1.791 / 0.952 | 0.342 / 1.055 / 0.664 / 0.352 |
| 2030 | 1.011 / 2.160 / 1.775 / 0.943 | 0.270 / 1.269 / 0.538 / 0.286 |
| 2031 | 0.997 / 2.201 / 1.754 / 0.934 | 0.217 / 1.321 / 0.436 / 0.232 |

### Holds (shift on; one thing held at base value), total by year and 5-yr

| run | 2027 | 2028 | 2029 | 2030 | 2031 | 5-yr | delta vs base 691.9 | share of 620 removed |
|---|--:|--:|--:|--:|--:|--:|--:|--:|
| base (no shift) | 86.9 | 114.4 | 135.7 | 172.1 | 182.8 | 691.9 | +0.0 | 100% |
| shift, nothing held | 138.7 | 192.4 | 251.0 | 332.1 | 397.7 | 1312.0 | +620.1 | -0% |
| hold tail aging at base (tail_k identical in both: 1.3514) | 125.6 | 177.4 | 233.4 | 311.4 | 373.1 | 1220.9 | +529.0 | 15% |
| hold anchor factors k at base | 80.8 | 106.0 | 118.8 | 150.3 | 152.5 | 608.4 | -83.5 | 113% |
| hold AGI-class membership at base | 138.3 | 191.9 | 247.6 | 322.0 | 381.8 | 1281.6 | +589.7 | 5% |
| hold credit overlay input at base | 138.7 | 192.6 | 251.1 | 332.1 | 397.7 | 1312.3 | +620.4 | -0% |
| hold non-gains (ordinary) income at base, gains $ as anchored | 80.3 | 107.8 | 125.2 | 157.9 | 161.8 | 633.0 | -58.9 | 110% |
| hold tail aging + ordinary income | 81.4 | 109.0 | 126.7 | 159.5 | 163.8 | 640.5 | -51.4 | 108% |

Notes on the tables: tail_k (DOTAX tax refit) is 1.3514 in both runs, because `build_units` calibrates it before the tail is aged and the shift enters only afterwards (the tail ages x gains growth at its cg share), so hold (a) is the tail aging, not tail_k. Credits barely move (-$0.3M): the overlay's only microdata input is the AGI-eligibility share. ETI is a partial offset (-$63.6M), applied to the inflated ordinary income. The anchor targets themselves do not grow with the shift (gains $ column is identical); k absorbs it. Class membership: the 1mp class (income >= $1M at the rank cut) goes 2,726 -> 5,029 units by 2031 and 400_1m 5,948 -> 3,649, so units cross the $1M boundary, but holding labels at base removes only $30M.

## Hold-outs: what each explains

(a) tail aging held at base: removes $91M (15%). (b) anchor factors held at base: removes all (total 608.4). The anchor is where the shift is converted to ordinary income. (c) class labels held: $30M (5%). (d) credits: 0. (e) ETI: -$63.6M of the 620 is the ETI offset, so gross static is +684; ETI is a consequence, not a source. Ordinary-income hold: -58.9 vs base (residual, within-class composition of gains).

Scratch drivers: `scripts/diagnostics/gains_shift_decomp.py` (flags: --hold tail,factors,labels,credit,ordinary; --save), `scripts/diagnostics/gains_unit_test.py`. Needs /tmp/gs/cal.pkl (copy of data/artifacts/sb3125_calibrated_base.pkl) and /tmp/gs/base.pkl from the base run.
