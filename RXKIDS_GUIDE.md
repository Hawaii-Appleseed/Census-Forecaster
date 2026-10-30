# RxKids Hawaiʻi — Reader's Guide (start here)

A self-guided tour for investigating the RxKids Hawaiʻi cost estimate: what it
is, how to run it, where every decision lives, and how to check it yourself.
For the full methodology, read **[`RXKIDS_METHODOLOGY.md`](RXKIDS_METHODOLOGY.md)**.

## 1. What this is

An estimate of the **annual cost and household impact** of a hypothetical
Hawaiʻi RxKids program — unrestricted cash "prescribed" during pregnancy and
infancy — modeled on the real RxKids program in Flint, Michigan. Eligibility
follows a draft Hawaiʻi statute: a family qualifies if it **(1) qualifies for
the State's Medicaid program, OR (2) has family income ≤ 300% of the federal
poverty level**, including the expected unborn child.

**Headline (TY2028, statutory design — the default):** ~**$21M/yr** in benefit
dollars (~$22M with 8% admin), serving **7,969 births** across ~7,340 eligible
families. Assumption band ~**$12M–$26M**. Design: **$1,500 one-time prenatal +
3 × $500 monthly = $3,000 per birth**. The estimate is dominated by
*specification* uncertainty, not sampling — see §6.

**Which scenario is "the headline" matters — it is `statutory_3mo`**
(`DEFAULT_SCENARIO_KEY` in `analyses/forecast_rxkids_2028.py`): Medicaid OR ≤300% FPL,
**3-month** postnatal window. The model also prices a 6-month variant and two
**universal** designs (no income or Medicaid test), which all cost more but —
for the 6-month variant — serve exactly the same births:

| Design | $/birth | Benefit cost | With 8% admin | **Births served/yr** |
|---|---|---|---|---|
| **`statutory_3mo` (default)** | **$3,000** | **~$21M** | ~$22M | **7,969** |
| `statutory_6mo` | $4,500 | ~$31M | ~$34M | 7,969 |
| `universal_6mo` | $4,500 | ~$52M | ~$56M | 13,225 |
| `universal_12mo` | $7,500 | ~$88M | ~$95M | 13,225 |

Note the first two rows: **halving the postnatal window cuts cost 34% and
serves the identical number of births.** Window length is a pure cost lever;
only the *depth* per family changes ($4,280 → $2,814 average benefit).

*Figures re-derived from live runs on 2026-08-07, after the county-split fix,
the NVSR series correction, and the switch to the Kalman projector (all in
RXKIDS_METHODOLOGY.md §3); they match
[`RXKIDS_METHODOLOGY.md`](RXKIDS_METHODOLOGY.md) §0 and §10, which are the
source of truth. An earlier version of this block quoted ~$54M/~24,000/~$65M as
the "conservative default" — those were the **universal 6-month** figures
(and ~24,000 was recipients, not births), so the guide was advertising the
most expensive means-test-free design as if it were the statutory one.*

**"People affected" means BIRTHS SERVED — 7,969.** Two other numbers get
confused with it: *eligible families* (7,340 — families clearing the test, most
with no birth this year) and *recipient-payments* (13,771 — roughly 2× births,
since each served birth draws one prenatal and one postnatal payment). The 2028
birth cohort is ~13,842, so the program reaches ~58% of all Hawaiʻi births.

**The one open question to know about:** "family income" is modeled as **MAGI
at the tax-unit (MAGI-household) grain**, because the statute anchors clause 1
to Medicaid (which uses MAGI households). If the bill defines "family" more
broadly, the alternative SPM-family (resource-sharing) grain is a material
**~15–20% lever** on the headline. This is a legal-definitional question, not a
modeling one — and note it is **not exposed as a CLI flag** and has not been
re-run on the current pipeline, so treat the percentage as an order-of-magnitude
estimate rather than a produced number.

## 2. Set up (one time)

The repo is a [`uv`](https://docs.astral.sh/uv/)-managed Python package.

```bash
uv sync --package census-forecaster --extra dev   # creates .venv with all deps
```

## 3. Run it yourself

```bash
# Statutory default — statutory_3mo, $1,500 + 3x$500 (real PUMS, committed):
uv run python analyses/forecast_rxkids_2028.py \
    --tax-year 2028 \
    --pums-data-dir packages/data/raw/pums_2024_1yr \
    --out reports/rxkids_2028/

# Flint-equivalent scenario:
uv run python analyses/forecast_rxkids_2028.py --tax-year 2028 \
    --pums-data-dir packages/data/raw/pums_2024_1yr \
    --takeup-rate 0.98 --fertility-response 0.10 \
    --out reports/rxkids_2028_flint/

# No PUMS download needed for a smoke run — synthetic fixture:
uv run python analyses/forecast_rxkids_2028.py --use-fixture --out /tmp/rxkids_smoke
```

Each run writes a workbook, a one-page PDF, CSVs, and the SPM-unit parquet to
the `--out` directory. Run `--help` to see every knob (take-up, fertility,
launch-year ramp, 12-month-extension, etc.).

## 4. The file map

| File | What it is |
|---|---|
| **[`RXKIDS_METHODOLOGY.md`](RXKIDS_METHODOLOGY.md)** | The full methodology — sourcing, eligibility, every parameter, limitations, cross-checks, MOE. **Read this for the substance.** |
| **[`analyses/forecast_rxkids_2028.py`](analyses/forecast_rxkids_2028.py)** | The runnable end-to-end script (the *process*): load PUMS → project to 2028 → eligibility → cost + impact → workbook/PDF. |
| `packages/tax_modeler/src/tax_modeler/programs/rxkids_hi.py` | Core eligibility + benefit math (`compute_rxkids_for_units`). The two clauses and both arms. (Also holds the *legacy* birth proxy — `--use-proxy-births` only; the default drives off observed age-0 dependents.) |
| `packages/tax_modeler/src/tax_modeler/benefits/medicaid_hi_quest.py` | Clause 1 — Medicaid eligibility (`medicaid_receives`). |
| `packages/tax_modeler/src/tax_modeler/benefits/_fpl.py` | HHS Hawaiʻi FPL tables (2024–2025 published; 2026–2028 projected). |
| `tests/tax_modeler/programs/test_rxkids.py` | The rules as executable tests — a precise, runnable spec of who qualifies and what they get. |
| `reports/rxkids_2028/` , `reports/rxkids_2028_flint/` | The committed outputs (xlsx workbook, PDF, CSVs, parquet). |

## 5. Check it yourself

```bash
# The eligibility/benefit rules, as ~56 fast tests:
uv run pytest tests/tax_modeler/programs/test_rxkids.py -v

# The whole suite (excluding tests that need large external data):
uv run pytest tests/ -m "not requires_dotax_raw and not requires_irs_external"

# Reproduce the headline: re-run §3 and diff the CSV against the committed one:
uv run python analyses/forecast_rxkids_2028.py --tax-year 2028 \
    --pums-data-dir packages/data/raw/pums_2024_1yr --out /tmp/check
diff /tmp/check/cost_by_state.csv reports/rxkids_2028/cost_by_state.csv
```

The workbook's **Assumptions** and **Notes** tabs list every parameter and
caveat behind that specific run, so a reviewer can audit a result without
reading code.

## 6. Trace the methodology — *the process*, decision by decision

Every methodology choice is a separate git commit with a full explanation of
*why*. To read the evolution of the model:

```bash
git log --oneline -- analyses/forecast_rxkids_2028.py \
    packages/tax_modeler/src/tax_modeler/programs/rxkids_hi.py \
    RXKIDS_METHODOLOGY.md
```

Notable decisions in the trail (each is a commit message worth reading):
postnatal stock→flow correction · prenatal birth-anchor → unified birth-driven
arms · recipients vs eligible base · joint-corner assumption band · launch-year
ramp · take-up & fertility scenarios · cash income → MAGI proxy · SPM-family →
MAGI-household grain. `git show <hash>` on any of these shows the exact code
change and the reasoning.

## 7. The assumptions that drive the number

These are the soft inputs (all in §2/§10 of the methodology; most are overridable
by CLI flag — the income grain is the exception). They — not sampling error — are
why the honest MOE is ±30–40%:

| Assumption | Default | Why it's uncertain |
|---|---|---|
| Take-up rate | 0.90 newborn / 0.83 prenatal | No Hawaiʻi program to anchor against (Flint observed 0.98 newborn / ~0.90 prenatal); scales the estimate linearly |
| 2028 birth cohort | ~13,842 (90% PI ~12,800–14,900) | CDC NVSR finals 2007-2024 (via WONDER) + DOH nowcast 2025–26; Kalman-projected, then bias+κ calibrated on a 46-fold back-test; scales the whole estimate. The legacy 0.066 births/dependent proxy is `--use-proxy-births` only — it does **not** drive the default. |
| Eligibility gate | Medicaid OR ≤300% FPL | The statutory design. Dropping the test entirely (universal) is ~+$22M — the single largest lever in the model |
| Fertility response | 0 (off) | Flint saw ~+10% births; a real upside risk, off by default |
| "Family income" grain | MAGI household | Legal-definitional (see §1); ~15–20% lever, not CLI-exposed |
| Payment design / postnatal window | $1,500 + $500×3 (default) | Policy choice; not fixed in a bill. Window length is a pure cost lever — 6-mo and 12-mo variants cost more but serve the **same** births |

There is **no administrative caseload** to calibrate against (RxKids doesn't
exist in Hawaiʻi yet), so these are judgment calls, transparently parameterized
rather than hidden.
