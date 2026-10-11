# DOTAX scorecard: TY2023 forecast vs. actual

Base: `/tmp/cf-48761b8/data/artifacts/sb3125_calibrated_base.pkl` (built by build_calibrated_base.py at code `48761b8`, raked to the DOTAX TY2022 edition, law year 2023). Actuals: `packages/tax_modeler/src/tax_modeler/data/calibration/dotax_indinc_2023.json`. Model incomes are the scoring `income` column; tax is before credits under 2017_system with the statutory §235-51(f) alternative tax.

## MID (alpha=1.5, aging=cbo, top_share_drift=+0.0%/yr, tail_k=1.4210)

### Table A-8: returns and tax before credits by AGI class

| class | returns_model | returns_actual | returns_err_pct | tax_model_M | tax_actual_M | tax_err_M | tax_err_pct |
|:---|---:|---:|---:|---:|---:|---:|---:|
| Loss | 1,108 | 13,506 | -91.8 | 0.0 | 0.0 | +0.0 |  |
| $0K-$10K | 110,261 | 110,447 | -0.2 | 1.5 | 3.0 | -1.5 | -51.2 |
| $10K-$20K | 60,330 | 63,776 | -5.4 | 18.9 | 21.0 | -2.1 | -10.0 |
| $20K-$30K | 58,613 | 56,358 | +4.0 | 54.9 | 50.0 | +4.9 | +9.8 |
| $30K-$40K | 65,498 | 58,625 | +11.7 | 97.6 | 90.0 | +7.6 | +8.5 |
| $40K-$50K | 54,743 | 54,823 | -0.1 | 119.7 | 120.0 | -0.3 | -0.2 |
| $50K-$75K | 96,803 | 96,433 | +0.4 | 308.7 | 311.0 | -2.3 | -0.7 |
| $75K-$100K | 63,544 | 57,836 | +9.9 | 287.7 | 277.0 | +10.7 | +3.9 |
| $100K-$150K | 69,080 | 65,585 | +5.3 | 461.4 | 467.0 | -5.6 | -1.2 |
| $150K-$200K | 31,035 | 30,326 | +2.3 | 298.2 | 319.0 | -20.8 | -6.5 |
| $200K-$300K | 19,999 | 21,375 | -6.4 | 287.1 | 349.0 | -61.9 | -17.7 |
| $300K-$400K | 6,138 | 6,607 | -7.1 | 130.5 | 166.0 | -35.5 | -21.4 |
| $400K-$500K | 3,033 | 2,952 | +2.7 | 102.1 | 103.0 | -0.9 | -0.9 |
| $500K-$750K | 2,079 | 3,120 | -33.4 | 84.5 | 157.0 | -72.5 | -46.2 |
| $750K-$1,000K | 984 | 1,158 | -15.0 | 66.0 | 87.0 | -21.0 | -24.1 |
| $1M+ | 1,824 | 1,704 | +7.0 | 721.3 | 441.0 | +280.3 | +63.6 |
| Total | 645,072 | 644,631 | +0.1 | 3,040.1 | 2,960.0 | +80.1 | +2.7 |

### Table A-1: AGI of taxable returns by class

| class | returns_model | returns_actual | returns_err_pct | agi_model_M | agi_actual_M | agi_err_pct |
|:---|---:|---:|---:|---:|---:|---:|
| $0K-$10K | 35,142 | 35,364 | -0.6 | 268 | 231 | +15.9 |
| $10K-$20K | 60,134 | 55,687 | +8.0 | 884 | 831 | +6.4 |
| $20K-$30K | 58,613 | 53,308 | +10.0 | 1,482 | 1,334 | +11.1 |
| $30K-$40K | 65,498 | 57,213 | +14.5 | 2,280 | 2,004 | +13.8 |
| $40K-$50K | 54,743 | 54,078 | +1.2 | 2,463 | 2,426 | +1.5 |
| $50K-$75K | 96,803 | 95,675 | +1.2 | 5,956 | 5,868 | +1.5 |
| $75K-$100K | 63,544 | 57,507 | +10.5 | 5,494 | 4,983 | +10.3 |
| $100K-$150K | 69,080 | 65,370 | +5.7 | 8,423 | 7,963 | +5.8 |
| $150K-$200K | 31,035 | 30,251 | +2.6 | 5,315 | 5,202 | +2.2 |
| $200K-$300K | 19,999 | 21,328 | -6.2 | 4,779 | 5,090 | -6.1 |
| $300K-$400K | 6,138 | 6,596 | -6.9 | 2,086 | 2,258 | -7.6 |
| $400K+ | 7,921 | 8,917 | -11.2 | 11,670 | 9,319 | +25.2 |
| Total | 568,649 | 541,294 | +5.1 | 51,100 | 47,510 | +7.6 |

### Table 4: returns by filing status

| filing_status | returns_model | returns_actual | share_model_pct | share_actual_pct | returns_err_pct |
|:---|---:|---:|---:|---:|---:|
| Single | 330,081 | 341,973 | 51.2 | 53.0 | -3.5 |
| Married Filing Jointly | 232,176 | 216,407 | 36.0 | 33.6 | +7.3 |
| Head of Household | 67,189 | 69,345 | 10.4 | 10.8 | -3.1 |
| Married Filing Separately | 15,626 | 16,718 | 2.4 | 2.6 | -6.5 |
| Qualifying Widow(er) | 0 | 188 | 0.0 | 0.0 | -100.0 |
| Total | 645,072 | 644,631 | 100.0 | 100.0 | +0.1 |

### The $1M+ class

- Returns: model 1,824, actual 1,704 (+7.0%).
- Tax before credits: model $721.3M, actual $441.0M (+280.3M, +63.6%).
- Average tax: model $395,459, actual $258,803 (+52.8%).
- Split: count +31.1M (at the actual average), per-filer +249.3M; in logs +0.492 = +0.068 (count) + +0.424 (per filer).

Dials, TY2022 -> TY2023: Honolulu B19013 x1.0678, engine wages x1.0493 / gains x1.0722, drift x1.0000, tail aging to the PUMS dollar year (40% gains) x1.1275, capital-gains anchor growth x1.0722 (cbo).

## LOW (alpha=1.7, aging=cbo, top_share_drift=-1.0%/yr, tail_k=1.7743)

### Table A-8: returns and tax before credits by AGI class

| class | returns_model | returns_actual | returns_err_pct | tax_model_M | tax_actual_M | tax_err_M | tax_err_pct |
|:---|---:|---:|---:|---:|---:|---:|---:|
| Loss | 1,108 | 13,506 | -91.8 | 0.0 | 0.0 | +0.0 |  |
| $0K-$10K | 110,261 | 110,447 | -0.2 | 1.5 | 3.0 | -1.5 | -51.2 |
| $10K-$20K | 60,330 | 63,776 | -5.4 | 18.9 | 21.0 | -2.1 | -10.0 |
| $20K-$30K | 58,613 | 56,358 | +4.0 | 54.9 | 50.0 | +4.9 | +9.8 |
| $30K-$40K | 65,498 | 58,625 | +11.7 | 97.6 | 90.0 | +7.6 | +8.5 |
| $40K-$50K | 54,743 | 54,823 | -0.1 | 119.7 | 120.0 | -0.3 | -0.2 |
| $50K-$75K | 96,803 | 96,433 | +0.4 | 308.7 | 311.0 | -2.3 | -0.7 |
| $75K-$100K | 63,544 | 57,836 | +9.9 | 287.7 | 277.0 | +10.7 | +3.9 |
| $100K-$150K | 69,080 | 65,585 | +5.3 | 461.4 | 467.0 | -5.6 | -1.2 |
| $150K-$200K | 31,035 | 30,326 | +2.3 | 298.2 | 319.0 | -20.8 | -6.5 |
| $200K-$300K | 19,999 | 21,375 | -6.4 | 287.1 | 349.0 | -61.9 | -17.7 |
| $300K-$400K | 6,138 | 6,607 | -7.1 | 130.5 | 166.0 | -35.5 | -21.4 |
| $400K-$500K | 3,033 | 2,952 | +2.7 | 102.1 | 103.0 | -0.9 | -0.9 |
| $500K-$750K | 2,079 | 3,120 | -33.4 | 84.5 | 157.0 | -72.5 | -46.2 |
| $750K-$1,000K | 984 | 1,158 | -15.0 | 66.0 | 87.0 | -21.0 | -24.1 |
| $1M+ | 1,824 | 1,704 | +7.0 | 713.7 | 441.0 | +272.7 | +61.8 |
| Total | 645,072 | 644,631 | +0.1 | 3,032.4 | 2,960.0 | +72.4 | +2.4 |

### Table A-1: AGI of taxable returns by class

| class | returns_model | returns_actual | returns_err_pct | agi_model_M | agi_actual_M | agi_err_pct |
|:---|---:|---:|---:|---:|---:|---:|
| $0K-$10K | 35,142 | 35,364 | -0.6 | 268 | 231 | +15.9 |
| $10K-$20K | 60,134 | 55,687 | +8.0 | 884 | 831 | +6.4 |
| $20K-$30K | 58,613 | 53,308 | +10.0 | 1,482 | 1,334 | +11.1 |
| $30K-$40K | 65,498 | 57,213 | +14.5 | 2,280 | 2,004 | +13.8 |
| $40K-$50K | 54,743 | 54,078 | +1.2 | 2,463 | 2,426 | +1.5 |
| $50K-$75K | 96,803 | 95,675 | +1.2 | 5,956 | 5,868 | +1.5 |
| $75K-$100K | 63,544 | 57,507 | +10.5 | 5,494 | 4,983 | +10.3 |
| $100K-$150K | 69,080 | 65,370 | +5.7 | 8,423 | 7,963 | +5.8 |
| $150K-$200K | 31,035 | 30,251 | +2.6 | 5,315 | 5,202 | +2.2 |
| $200K-$300K | 19,999 | 21,328 | -6.2 | 4,779 | 5,090 | -6.1 |
| $300K-$400K | 6,138 | 6,596 | -6.9 | 2,086 | 2,258 | -7.6 |
| $400K+ | 7,921 | 8,917 | -11.2 | 11,587 | 9,319 | +24.3 |
| Total | 568,649 | 541,294 | +5.1 | 51,017 | 47,510 | +7.4 |

### Table 4: returns by filing status

| filing_status | returns_model | returns_actual | share_model_pct | share_actual_pct | returns_err_pct |
|:---|---:|---:|---:|---:|---:|
| Single | 330,081 | 341,973 | 51.2 | 53.0 | -3.5 |
| Married Filing Jointly | 232,176 | 216,407 | 36.0 | 33.6 | +7.3 |
| Head of Household | 67,189 | 69,345 | 10.4 | 10.8 | -3.1 |
| Married Filing Separately | 15,626 | 16,718 | 2.4 | 2.6 | -6.5 |
| Qualifying Widow(er) | 0 | 188 | 0.0 | 0.0 | -100.0 |
| Total | 645,072 | 644,631 | 100.0 | 100.0 | +0.1 |

### The $1M+ class

- Returns: model 1,824, actual 1,704 (+7.0%).
- Tax before credits: model $713.7M, actual $441.0M (+272.7M, +61.8%).
- Average tax: model $391,260, actual $258,803 (+51.2%).
- Split: count +31.1M (at the actual average), per-filer +241.6M; in logs +0.481 = +0.068 (count) + +0.413 (per filer).

Dials, TY2022 -> TY2023: Honolulu B19013 x1.0678, engine wages x1.0493 / gains x1.0722, drift x0.9900, tail aging to the PUMS dollar year (40% gains) x1.1051, capital-gains anchor growth x1.0722 (cbo).

## HIGH (alpha=1.4, aging=cbo, top_share_drift=+1.0%/yr, tail_k=1.2343)

### Table A-8: returns and tax before credits by AGI class

| class | returns_model | returns_actual | returns_err_pct | tax_model_M | tax_actual_M | tax_err_M | tax_err_pct |
|:---|---:|---:|---:|---:|---:|---:|---:|
| Loss | 1,108 | 13,506 | -91.8 | 0.0 | 0.0 | +0.0 |  |
| $0K-$10K | 110,261 | 110,447 | -0.2 | 1.5 | 3.0 | -1.5 | -51.2 |
| $10K-$20K | 60,330 | 63,776 | -5.4 | 18.9 | 21.0 | -2.1 | -10.0 |
| $20K-$30K | 58,613 | 56,358 | +4.0 | 54.9 | 50.0 | +4.9 | +9.8 |
| $30K-$40K | 65,498 | 58,625 | +11.7 | 97.6 | 90.0 | +7.6 | +8.5 |
| $40K-$50K | 54,743 | 54,823 | -0.1 | 119.7 | 120.0 | -0.3 | -0.2 |
| $50K-$75K | 96,803 | 96,433 | +0.4 | 308.7 | 311.0 | -2.3 | -0.7 |
| $75K-$100K | 63,544 | 57,836 | +9.9 | 287.7 | 277.0 | +10.7 | +3.9 |
| $100K-$150K | 69,080 | 65,585 | +5.3 | 461.4 | 467.0 | -5.6 | -1.2 |
| $150K-$200K | 31,035 | 30,326 | +2.3 | 298.2 | 319.0 | -20.8 | -6.5 |
| $200K-$300K | 19,999 | 21,375 | -6.4 | 287.1 | 349.0 | -61.9 | -17.7 |
| $300K-$400K | 6,138 | 6,607 | -7.1 | 130.5 | 166.0 | -35.5 | -21.4 |
| $400K-$500K | 3,033 | 2,952 | +2.7 | 102.1 | 103.0 | -0.9 | -0.9 |
| $500K-$750K | 2,079 | 3,120 | -33.4 | 84.5 | 157.0 | -72.5 | -46.2 |
| $750K-$1,000K | 984 | 1,158 | -15.0 | 66.0 | 87.0 | -21.0 | -24.1 |
| $1M+ | 1,824 | 1,704 | +7.0 | 749.7 | 441.0 | +308.7 | +70.0 |
| Total | 645,072 | 644,631 | +0.1 | 3,068.5 | 2,960.0 | +108.5 | +3.7 |

### Table A-1: AGI of taxable returns by class

| class | returns_model | returns_actual | returns_err_pct | agi_model_M | agi_actual_M | agi_err_pct |
|:---|---:|---:|---:|---:|---:|---:|
| $0K-$10K | 35,142 | 35,364 | -0.6 | 268 | 231 | +15.9 |
| $10K-$20K | 60,134 | 55,687 | +8.0 | 884 | 831 | +6.4 |
| $20K-$30K | 58,613 | 53,308 | +10.0 | 1,482 | 1,334 | +11.1 |
| $30K-$40K | 65,498 | 57,213 | +14.5 | 2,280 | 2,004 | +13.8 |
| $40K-$50K | 54,743 | 54,078 | +1.2 | 2,463 | 2,426 | +1.5 |
| $50K-$75K | 96,803 | 95,675 | +1.2 | 5,956 | 5,868 | +1.5 |
| $75K-$100K | 63,544 | 57,507 | +10.5 | 5,494 | 4,983 | +10.3 |
| $100K-$150K | 69,080 | 65,370 | +5.7 | 8,423 | 7,963 | +5.8 |
| $150K-$200K | 31,035 | 30,251 | +2.6 | 5,315 | 5,202 | +2.2 |
| $200K-$300K | 19,999 | 21,328 | -6.2 | 4,779 | 5,090 | -6.1 |
| $300K-$400K | 6,138 | 6,596 | -6.9 | 2,086 | 2,258 | -7.6 |
| $400K+ | 7,921 | 8,917 | -11.2 | 11,953 | 9,319 | +28.3 |
| Total | 568,649 | 541,294 | +5.1 | 51,383 | 47,510 | +8.2 |

### Table 4: returns by filing status

| filing_status | returns_model | returns_actual | share_model_pct | share_actual_pct | returns_err_pct |
|:---|---:|---:|---:|---:|---:|
| Single | 330,081 | 341,973 | 51.2 | 53.0 | -3.5 |
| Married Filing Jointly | 232,176 | 216,407 | 36.0 | 33.6 | +7.3 |
| Head of Household | 67,189 | 69,345 | 10.4 | 10.8 | -3.1 |
| Married Filing Separately | 15,626 | 16,718 | 2.4 | 2.6 | -6.5 |
| Qualifying Widow(er) | 0 | 188 | 0.0 | 0.0 | -100.0 |
| Total | 645,072 | 644,631 | 100.0 | 100.0 | +0.1 |

### The $1M+ class

- Returns: model 1,824, actual 1,704 (+7.0%).
- Tax before credits: model $749.7M, actual $441.0M (+308.7M, +70.0%).
- Average tax: model $411,044, actual $258,803 (+58.8%).
- Split: count +31.1M (at the actual average), per-filer +277.7M; in logs +0.531 = +0.068 (count) + +0.463 (per filer).

Dials, TY2022 -> TY2023: Honolulu B19013 x1.0678, engine wages x1.0493 / gains x1.0722, drift x1.0100, tail aging to the PUMS dollar year (40% gains) x1.1502, capital-gains anchor growth x1.0722 (cbo).

## The $1M+ class in the DOTAX A-8 series: what drift does it support?

| tax_year | returns | tax_before_M | returns_share_pct | tax_share_pct | dlog_returns_share | dlog_tax_share |
|:---|---:|---:|---:|---:|---:|---:|
| 2019 | 1,163 | 413.0 | 0.178 | 16.8 |  |  |
| 2020 | 1,387 | 415.0 | 0.212 | 16.1 | +0.174 | -0.041 |
| 2021 | 2,086 | 799.0 | 0.324 | 25.3 | +0.423 | +0.453 |
| 2022 | 1,824 | 663.0 | 0.287 | 21.9 | -0.121 | -0.146 |
| 2023 | 1,704 | 441.0 | 0.264 | 14.9 | -0.083 | -0.385 |

Fitted trend in log share, per year: tax share +0.7% (SE 8.2%), returns share +10.9% (SE 6.1%); 5 editions. Neither is distinguishable from zero, so the supported top-share drift is 0 with a band of roughly ±1 SE; the scenarios carry -1/0/+1 %/yr (projection.engine.TOP_SHARE_DRIFT).
