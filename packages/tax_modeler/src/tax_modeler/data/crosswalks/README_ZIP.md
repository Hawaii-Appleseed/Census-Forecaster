# Hawaiʻi ZIP-code crosswalks

## What's here

- **`hawaii_zip_to_county.csv`** — 122 active Hawaiʻi ZIP codes → 1 of 4 counties (Honolulu / Hawaii / Maui / Kauai). Deterministic, no probabilistic splits. 100% coverage of the 58 ZIPs that appear in IRS SOI ZIP Code Data 2022.
  - Source: USPS Hawaiʻi ZIP directory + Hawaiʻi DOTAX. Built by `scripts/build_zip_county_crosswalk.py`.
  - Use: county-level raking of unit weights to IRS SOI ZIP filer / AGI / EITC / CTC totals.

- **`hawaii_puma_district_population_2022.csv`** — PUMA → county, House and Senate district pieces (2022 plan, all 51 and 25) with 2020 census population, built from blocks by `scripts/build_puma_district_crosswalk.py`. It replaced `hawaii_puma_districts_official_2022.csv` (35 and 18 districts, no populations) in November 2026.

## What's missing (TODO: ZIP → House/Senate district)

For HD/SD-level raking with `tax_modeler.analysis.district_raking.rake_weights_to_irs_zip`,
we need a `hawaii_zip_to_house_district.csv` and `hawaii_zip_to_senate_district.csv`
that include probabilistic splits where ZIPs cross district boundaries (common in
Honolulu urban districts where one ZIP code spans 3–5 House districts).

Two production-grade paths:

### Path A — HUD USPS ZIP-to-tract crosswalk + tract-to-HD spatial join

1. Download HUD USPS ZIP-to-tract crosswalk: <https://www.huduser.gov/portal/datasets/usps_crosswalk.html> (Hawaiʻi subset).
2. Download Hawaiʻi 2022 State Legislative District shapefile: <https://www2.census.gov/geo/tiger/TIGER2022/SLDL/> (`tl_2022_15_sldl.zip`) and `SLDU` for Senate.
3. Spatial join: tract → HD/SD using census tract centroids.
4. Compose: ZIP → tract (HUD) × tract → HD/SD (spatial) = ZIP → HD/SD with population-weighted residential ratios.

### Path B — Direct ZIP centroid spatial join

1. Pull ZIP centroid lat/lon (USPS ZCTA centroids, Census ZCTA shapefile).
2. Point-in-polygon test against the Hawaiʻi SLDL/SLDU shapefiles.
3. Single-district assignment per ZIP (loses sub-ZIP precision but simpler).

Both paths require GIS tooling (`geopandas` / `shapely`). Once the file is in place,
`analysis.district_raking.rake_weights_to_irs_zip` no longer raises `MissingDataError`
and `--rake-to-irs-zip` becomes defensible to flip default ON.

**Estimated effort:** 4–6 hours for Path A; 2–3 hours for Path B.
