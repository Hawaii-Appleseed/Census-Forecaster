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
| $100K-$150K | 69,080 | 65,585 | +5.3 | 461.0 | 467.0 | -6.0 | -1.3 |
| $150K-$200K | 31,035 | 30,326 | +2.3 | 297.9 | 319.0 | -21.1 | -6.6 |
| $200K-$300K | 19,999 | 21,375 | -6.4 | 286.8 | 349.0 | -62.2 | -17.8 |
| $300K-$400K | 6,138 | 6,607 | -7.1 | 130.2 | 166.0 | -35.8 | -21.6 |
| $400K-$500K | 3,033 | 2,952 | +2.7 | 100.4 | 103.0 | -2.6 | -2.5 |
| $500K-$750K | 2,079 | 3,120 | -33.4 | 82.0 | 157.0 | -75.0 | -47.8 |
| $750K-$1,000K | 984 | 1,158 | -15.0 | 63.8 | 87.0 | -23.2 | -26.7 |
| $1M+ | 1,824 | 1,704 | +7.0 | 688.7 | 441.0 | +247.7 | +56.2 |
| Total | 645,072 | 644,631 | +0.1 | 2,999.8 | 2,960.0 | +39.8 | +1.3 |

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
- Tax before credits: model $688.7M, actual $441.0M (+247.7M, +56.2%).
- Average tax: model $377,592, actual $258,803 (+45.9%).
- Split: count +31.1M (at the actual average), per-filer +216.7M; in logs +0.446 = +0.068 (count) + +0.378 (per filer).

Dials, TY2022 -> TY2023: Honolulu B19013 x1.0678, engine wages x1.0493 / gains x1.0722, drift x1.0000, tail aging to the PUMS dollar year (40% gains) x1.1275, capital-gains anchor growth x1.0493 (window).
The step from the PUMS dollar year (2024) to TY2023: this engine's wages x0.9675, the county-median scalar x0.9803; Hawaiʻi realized over the same years: QCEW average pay x0.9531, BEA per-capita income x0.9399. The base's tax totals are the base edition's on PUMS-dollar-year incomes, so below $1M a smaller step scores better whatever the realized growth.

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
| $100K-$150K | 69,080 | 65,585 | +5.3 | 461.0 | 467.0 | -6.0 | -1.3 |
| $150K-$200K | 31,035 | 30,326 | +2.3 | 297.9 | 319.0 | -21.1 | -6.6 |
| $200K-$300K | 19,999 | 21,375 | -6.4 | 286.8 | 349.0 | -62.2 | -17.8 |
| $300K-$400K | 6,138 | 6,607 | -7.1 | 130.2 | 166.0 | -35.8 | -21.6 |
| $400K-$500K | 3,033 | 2,952 | +2.7 | 100.4 | 103.0 | -2.6 | -2.5 |
| $500K-$750K | 2,079 | 3,120 | -33.4 | 82.0 | 157.0 | -75.0 | -47.8 |
| $750K-$1,000K | 984 | 1,158 | -15.0 | 63.8 | 87.0 | -23.2 | -26.7 |
| $1M+ | 1,824 | 1,704 | +7.0 | 684.7 | 441.0 | +243.7 | +55.3 |
| Total | 645,072 | 644,631 | +0.1 | 2,995.7 | 2,960.0 | +35.7 | +1.2 |

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
- Tax before credits: model $684.7M, actual $441.0M (+243.7M, +55.3%).
- Average tax: model $375,364, actual $258,803 (+45.0%).
- Split: count +31.1M (at the actual average), per-filer +212.6M; in logs +0.440 = +0.068 (count) + +0.372 (per filer).

Dials, TY2022 -> TY2023: Honolulu B19013 x1.0678, engine wages x1.0493 / gains x1.0722, drift x0.9900, tail aging to the PUMS dollar year (40% gains) x1.1051, capital-gains anchor growth x1.0493 (window).
The step from the PUMS dollar year (2024) to TY2023: this engine's wages x0.9675, the county-median scalar x0.9803; Hawaiʻi realized over the same years: QCEW average pay x0.9531, BEA per-capita income x0.9399. The base's tax totals are the base edition's on PUMS-dollar-year incomes, so below $1M a smaller step scores better whatever the realized growth.

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
| $100K-$150K | 69,080 | 65,585 | +5.3 | 461.0 | 467.0 | -6.0 | -1.3 |
| $150K-$200K | 31,035 | 30,326 | +2.3 | 297.9 | 319.0 | -21.1 | -6.6 |
| $200K-$300K | 19,999 | 21,375 | -6.4 | 286.8 | 349.0 | -62.2 | -17.8 |
| $300K-$400K | 6,138 | 6,607 | -7.1 | 130.2 | 166.0 | -35.8 | -21.6 |
| $400K-$500K | 3,033 | 2,952 | +2.7 | 100.4 | 103.0 | -2.6 | -2.5 |
| $500K-$750K | 2,079 | 3,120 | -33.4 | 82.0 | 157.0 | -75.0 | -47.8 |
| $750K-$1,000K | 984 | 1,158 | -15.0 | 63.8 | 87.0 | -23.2 | -26.7 |
| $1M+ | 1,824 | 1,704 | +7.0 | 713.7 | 441.0 | +272.7 | +61.8 |
| Total | 645,072 | 644,631 | +0.1 | 3,024.8 | 2,960.0 | +64.8 | +2.2 |

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
- Tax before credits: model $713.7M, actual $441.0M (+272.7M, +61.8%).
- Average tax: model $391,276, actual $258,803 (+51.2%).
- Split: count +31.1M (at the actual average), per-filer +241.6M; in logs +0.481 = +0.068 (count) + +0.413 (per filer).

Dials, TY2022 -> TY2023: Honolulu B19013 x1.0678, engine wages x1.0493 / gains x1.0722, drift x1.0100, tail aging to the PUMS dollar year (40% gains) x1.1502, capital-gains anchor growth x1.0493 (window).
The step from the PUMS dollar year (2024) to TY2023: this engine's wages x0.9675, the county-median scalar x0.9803; Hawaiʻi realized over the same years: QCEW average pay x0.9531, BEA per-capita income x0.9399. The base's tax totals are the base edition's on PUMS-dollar-year incomes, so below $1M a smaller step scores better whatever the realized growth.

## The $1M+ class in the DOTAX A-8 series: what drift does it support?

| tax_year | returns | tax_before_M | returns_share_pct | tax_share_pct | dlog_returns_share | dlog_tax_share |
|:---|---:|---:|---:|---:|---:|---:|
| 2019 | 1,163 | 413.0 | 0.178 | 16.8 |  |  |
| 2020 | 1,387 | 415.0 | 0.212 | 16.1 | +0.174 | -0.041 |
| 2021 | 2,086 | 799.0 | 0.324 | 25.3 | +0.423 | +0.453 |
| 2022 | 1,824 | 663.0 | 0.287 | 21.9 | -0.121 | -0.146 |
| 2023 | 1,704 | 441.0 | 0.264 | 14.9 | -0.083 | -0.385 |

Fitted trend in log share, per year: tax share +0.7% (SE 8.2%), returns share +10.9% (SE 6.1%); 5 editions. Neither is distinguishable from zero, so the supported top-share drift is 0 with a band of roughly ±1 SE; the scenarios carry -1/0/+1 %/yr (projection.engine.TOP_SHARE_DRIFT).
