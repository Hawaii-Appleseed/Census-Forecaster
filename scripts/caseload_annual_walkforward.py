"""Annual walk-forward: does lagged caseload change improve the ACS baseline?

Exploratory; writes only docs/data_source_tests/caseload-annual-tables.md.

Baseline = the forecaster's own project_damped_trend (phi=0.85, log space),
refit on observations before the target year only. Candidate = baseline +
b * x, where x is the log December-to-December change in the caseload
BEFORE the target year (Dec T-2 -> Dec T-1; known when the forecast is
made) and b is a through-origin OLS slope of PAST baseline residuals on
the x of those same years (only residuals already realised, so no
look-ahead). A "same-year" row uses Dec T-1 -> Dec T instead: a nowcast
(ACS year T is only partly observed by then), reported to show the
relation exists, not as a leading test.

Targets: Hawaii statewide ACS 1-year poverty rate and SNAP-household share
(data/caseloads/hi_state_acs_poverty_snap.json), and Honolulu County
poverty rate from the bundled calibration panel. ACS 1-year 2020 does not
exist, so the series has a hole at 2020 and 2021 is a 2-year-ahead forecast.

    uv run python scripts/caseload_annual_walkforward.py
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
from census_forecaster.acs.projection import project_damped_trend
from common.models import AcsObservation
from scipy import stats

ROOT = Path(__file__).resolve().parents[1]
PANEL = (ROOT / "packages/census_forecaster/src/census_forecaster/data/"
         "calibration_panel/acs_panel.json")
K_MIN = 4          # realised residuals needed before b is estimated
FIRST_T = 2015     # >=5 prior ACS observations for the baseline


def monthly(name: str) -> dict[tuple[int, int], float]:
    rows = json.loads((ROOT / "data/caseloads/hi_caseloads_monthly.json"
                       ).read_text())["series"][name]
    return {(r["year"], int(r["period"][1:])): r["value"] for r in rows}


def dec_change(m: dict, year: int) -> float | None:
    """log(Dec year / Dec year-1)."""
    a, b = m.get((year - 1, 12)), m.get((year, 12))
    return math.log(b / a) if a and b else None


def targets() -> dict[str, dict[int, float]]:
    st = json.loads((ROOT / "data/caseloads/hi_state_acs_poverty_snap.json"
                     ).read_text())["series"]
    panel = json.loads(PANEL.read_text())["observations"]
    hon = {o["year"]: o["estimate"] for o in panel
           if o["geoid"] == "15003" and o["indicator"] == "S1701_C03_001E"}
    return {
        "HI poverty rate (state, fetched)": {int(y): v["poverty_pct"] for y, v in st.items()},
        "HI SNAP-receipt share (state, fetched)": {int(y): v["snap_share_pct"] for y, v in st.items()},
        "Honolulu poverty rate (bundled panel)": hon,
    }


def run(y: dict[int, float], x_of, geoid: str, drop=lambda t: False):
    """Return fold list [(T, actual, base, aug|None, x)]."""
    years = sorted(y)
    obs = {t: AcsObservation(estimate=y[t], moe=1.0, year=t, vintage="1y",
                             geoid=geoid, indicator="T") for t in years}
    folds, res = [], []   # res: (T, x, resid) realised
    for t in years:
        if t < FIRST_T:
            continue
        fp = project_damped_trend([obs[s] for s in years if s < t], t)
        if fp is None:
            continue
        base = fp.point
        x = x_of(t)
        aug = None
        past = [(xs, e) for (s, xs, e) in res if xs is not None and not drop(s)]
        if x is not None and len(past) >= K_MIN and not drop(t):
            xs = np.array([p[0] for p in past])
            es = np.array([p[1] for p in past])
            b = float(xs @ es / (xs @ xs)) if (xs @ xs) > 0 else 0.0
            aug = base + b * x
        folds.append((t, y[t], base, aug, x))
        res.append((t, x, y[t] - base))
    return folds


def score(folds):
    f = [r for r in folds if r[3] is not None]
    if not f:
        return None
    eb = np.array([a - b for _, a, b, _, _ in f])
    ea = np.array([a - c for _, a, _, c, _ in f])
    return dict(n=len(f), years=[r[0] for r in f],
                rmse_base=float(np.sqrt((eb ** 2).mean())),
                rmse_aug=float(np.sqrt((ea ** 2).mean())),
                mae_base=float(np.abs(eb).mean()), mae_aug=float(np.abs(ea).mean()),
                aug_better=int((np.abs(ea) < np.abs(eb)).sum()))


def corr(folds):
    p = [(x, a - b) for _, a, b, _, x in folds if x is not None]
    if len(p) < 4:
        return None
    r, pv = stats.pearsonr([q[0] for q in p], [q[1] for q in p])
    return float(r), float(pv), len(p)


def main() -> None:
    snap = monthly("SNAP_HI_PERSONS")
    med = monthly("MEDICAID_CHIP_HI_TOTAL")
    variants = {
        "SNAP persons, lagged (Dec T-2 -> T-1)": lambda t: dec_change(snap, t - 1),
        "SNAP persons, same-year (Dec T-1 -> T) [nowcast]": lambda t: dec_change(snap, t),
        "Medicaid+CHIP, lagged (Dec T-2 -> T-1)": lambda t: dec_change(med, t - 1),
    }
    robust = {
        "all folds": lambda t: False,
        "drop T=2021 (pandemic x)": lambda t: t == 2021,
        "drop T>=2023 (EA end, unwinding, Maui)": lambda t: t >= 2023,
    }
    L = ["# Annual caseload walk-forward tables (generated by scripts/caseload_annual_walkforward.py)", "",
         f"Baseline: project_damped_trend, phi=0.85. b fit on >= {K_MIN} realised past residuals. "
         f"First forecast year {FIRST_T}.", ""]
    for tname, y in targets().items():
        L += [f"## {tname}", "", f"ACS years available: {sorted(y)} (n={len(y)})", "",
              "| caseload x | fold set | folds with b | years | RMSE base | RMSE +caseload | "
              "MAE base | MAE +caseload | caseload closer | corr(resid, x) r / p / n |",
              "|---|---|---|---|---|---|---|---|---|---|"]
        for vname, xf in variants.items():
            for rname, dr in robust.items():
                folds = run(y, xf, "15", dr)
                s = score(folds)
                c = corr([f for f in folds if not dr(f[0])])
                cs = f"{c[0]:+.2f} / {c[1]:.2f} / {c[2]}" if c else "-"
                if s is None:
                    L.append(f"| {vname} | {rname} | 0 | - | - | - | - | - | - | {cs} |")
                else:
                    L.append(f"| {vname} | {rname} | {s['n']} | {s['years'][0]}-{s['years'][-1]} | "
                             f"{s['rmse_base']:.3f} | {s['rmse_aug']:.3f} | {s['mae_base']:.3f} | "
                             f"{s['mae_aug']:.3f} | {s['aug_better']}/{s['n']} | {cs} |")
        L.append("")
    out = ROOT / "docs/data_source_tests/caseload-annual-tables.md"
    out.write_text("\n".join(L))
    print("\n".join(L))


if __name__ == "__main__":
    main()
