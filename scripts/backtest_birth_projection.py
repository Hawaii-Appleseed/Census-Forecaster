"""Walk-forward back-test of the RxKids birth-cohort projection.

Why this exists
---------------
The birth series is the least externally-anchored input in the RxKids model.
Unlike every dollar indicator in the repo it has no entry in the anchor-source
registry, gets no v3 bias/kappa calibration, and — critically — its prediction
interval had never been coverage-tested against anything. It simply inherits
``EMPIRICAL_SE_INFLATOR = 1.30``, which was calibrated on ACS *survey* dollar
series (income, rent, home value). Vital-statistics counts are far less noisy
than ACS estimates, so that inflator is a guess in the wrong direction.

This script answers two questions with data instead of intuition:

  1. Which method should drive the birth cohort — the legacy damped-trend +
     AR(1) ensemble (current default), or the Kalman state-space filter?
     They differ in a way that matters here: ``project_kalman`` consumes each
     observation's MOE as measurement noise (``R = (se/estimate)^2``), whereas
     ``fit_damped_trend`` / ``fit_ar1_log_diff`` ignore per-observation MOE
     entirely. Since the DOH nowcasts carry deliberately wide MOEs, only the
     Kalman path can let that uncertainty reach the POINT estimate rather than
     just the interval.
  2. Is the 90% PI honest? Coverage should land near 90%; materially below
     means the interval is too tight to quote.

Design
------
Expanding-window walk-forward on the NVSR finals only (the nowcasts are not
ground truth, so they cannot be scored). For each cutoff year the model sees
finals up to and including that year and predicts every later final, giving
horizons 1..N. Also scores a naive persistence baseline (carry the last value
forward) — a projection that cannot beat persistence is not earning its keep.

With the series extended to the full WONDER window (2007-2024, 18 finals)
this is a genuine calibration, not just a sanity gate: 46 scoreable folds at
the production-relevant horizons (h <= 4; the 2028 target sits at h=2 from the
last nowcast and h=4 from the last final).

It also derives the split-conformal kappa for the Kalman path, using the same
finite-sample convention as acs/conformal.py (non-conformity score
s_i = |actual - point| / se_total; kappa = q / 1.645 where q is the
ceil((n+1)*0.9)-th order statistic). This matters because ``project_kalman``
applies NO empirical SE calibration on a direct call — its PI is purely
analytical, which the repo's own discipline forbids quoting uncalibrated
("empirically calibrated 90% PIs via backtest, NOT analytical"). The derived
kappa is pinned in ``forecast_rxkids_2028.BIRTH_KALMAN_SE_KAPPA``; re-run this
script and update the constant when the series gains a new final.

Usage
-----
    python scripts/backtest_birth_projection.py
    python scripts/backtest_birth_projection.py --min-train 5 --max-horizon 4
"""
from __future__ import annotations

import argparse
import math
import statistics
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "analyses"))   # forecast_rxkids_2028
sys.path.insert(0, str(REPO_ROOT / "scripts"))


def _obs(year: int, value: float):
    from common.models import AcsObservation
    return AcsObservation(
        estimate=float(value), moe=1.645 * math.sqrt(value), year=year,
        vintage="1y", geoid="15", indicator="births",
    )


def _predict(method: str, train: list, target_year: int):
    """Return (point, ci_low, ci_high, se_total) or None."""
    if method == "persistence":
        last = max(train, key=lambda o: o.year)
        return last.estimate, float("nan"), float("nan"), float("nan")

    if method == "ensemble":
        from census_forecaster import project_acs_ensemble
        fp = project_acs_ensemble(train, target_year=target_year)
    elif method == "kalman":
        from census_forecaster.kalman import project_kalman
        fp = project_kalman(train, target_year=target_year,
                            end_year=int(max(o.year for o in train)), geoid="15")
    else:
        raise ValueError(method)
    if fp is None:
        return None
    return float(fp.point), float(fp.ci90_low), float(fp.ci90_high), float(fp.se_total)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--min-train", type=int, default=5,
                    help="Minimum number of finals in the training window (default 5).")
    ap.add_argument("--max-horizon", type=int, default=4,
                    help="Score horizons up to this many years (default 4 — the 2028 "
                         "target sits at h=2 from the last nowcast, h=4 from the last "
                         "final; longer horizons are not production-relevant).")
    args = ap.parse_args(argv)

    import forecast_rxkids_2028 as F
    series = dict(sorted(F.HI_BIRTHS_BY_YEAR.items()))
    years = sorted(series)
    print(f"NVSR finals: {years[0]}-{years[-1]} ({len(years)} years)\n")

    methods = ["persistence", "ensemble", "kalman"]
    # rows: (method, horizon, cutoff, actual, point, lo, hi, se_total)
    rows = []

    for i in range(args.min_train, len(years)):
        cutoff = years[i - 1]
        train = [_obs(y, series[y]) for y in years[:i]]
        for j in range(i, len(years)):
            ty = years[j]
            horizon = ty - cutoff
            if horizon > args.max_horizon:
                break
            for m in methods:
                out = _predict(m, train, ty)
                if out is None:
                    continue
                point, lo, hi, se = out
                rows.append((m, horizon, cutoff, series[ty], point, lo, hi, se))

    if not rows:
        print("No folds produced — series too short for --min-train.", file=sys.stderr)
        return 1

    print(f"{'method':>12} {'n':>3} {'MAPE':>7} {'bias':>8} {'RMSE':>8} {'CI90 cov':>9}")
    summary = {}
    for m in methods:
        sub = [r for r in rows if r[0] == m]
        if not sub:
            continue
        ape = [abs(p - a) / a for _, _, _, a, p, _, _, _ in sub]
        bias = [(p - a) / a for _, _, _, a, p, _, _, _ in sub]
        sqe = [(p - a) ** 2 for _, _, _, a, p, _, _, _ in sub]
        covered = [lo <= a <= hi for _, _, _, a, _, lo, hi, _ in sub
                   if math.isfinite(lo) and math.isfinite(hi)]
        cov = (sum(covered) / len(covered)) if covered else float("nan")
        summary[m] = {"mape": statistics.fmean(ape), "bias": statistics.fmean(bias),
                      "rmse": math.sqrt(statistics.fmean(sqe)), "cov": cov,
                      "n": len(sub)}
        cov_s = f"{100*cov:8.1f}%" if covered else "       —"
        print(f"{m:>12} {len(sub):>3} {100*summary[m]['mape']:>6.2f}% "
              f"{100*summary[m]['bias']:>+7.2f}% {summary[m]['rmse']:>8.0f} {cov_s:>9}")

    print(f"\n{'method':>12} {'h':>3} {'MAPE':>7} {'CI90 cov':>9}   (by horizon)")
    for m in methods:
        for h in sorted({r[1] for r in rows}):
            sub = [r for r in rows if r[0] == m and r[1] == h]
            if not sub:
                continue
            ape = statistics.fmean(abs(p - a) / a for _, _, _, a, p, _, _, _ in sub)
            cvd = [lo <= a <= hi for _, _, _, a, _, lo, hi, _ in sub
                   if math.isfinite(lo) and math.isfinite(hi)]
            cov_s = f"{100*sum(cvd)/len(cvd):8.1f}%" if cvd else "       —"
            print(f"{m:>12} {h:>3} {100*ape:>6.2f}% {cov_s:>9}   (n={len(sub)})")

    # ---- bias + split-conformal kappa for the Kalman path ----------------
    # Repo order (see ensemble.py): geometric bias correction FIRST, then the
    # SE calibration on bias-corrected residuals — kappa applied to a still-
    # biased point would double-count the level miscalibration.
    #
    # Bias: b = mean(log(point/actual)). Pooled across horizons, matching the
    # repo's strata discipline — per-horizon cells here are n=10-13, below the
    # n>=20 threshold at which the v3 machinery trusts a cell, so the marginal
    # (pooled) estimate is the disciplined choice.
    #
    # Kappa: same finite-sample convention as acs/conformal.py — the
    # ceil((n+1)*0.9)-th order statistic of s_i = |actual - p_corr| / se_corr,
    # expressed as a multiplier on the analytical 90% half-width
    # (kappa = q/1.645), where p_corr = point*exp(-b) and se_corr scales by
    # the same factor (a level shift is multiplicative on count-space SEs).
    print("\n--- bias + split-conformal kappa (kalman) ---")
    ksub = [r for r in rows if r[0] == "kalman" and math.isfinite(r[7]) and r[7] > 0]
    n = len(ksub)

    log_bias = statistics.fmean(math.log(p / a) for _, _, _, a, p, _, _, _ in ksub)
    by_h = {}
    for h in sorted({r[1] for r in ksub}):
        hh = [r for r in ksub if r[1] == h]
        by_h[h] = statistics.fmean(math.log(p / a) for _, _, _, a, p, _, _, _ in hh)
    print(f"log bias b: pooled {log_bias:+.4f} "
          f"({', '.join(f'h={h}: {b:+.4f} (n={len([r for r in ksub if r[1]==h])})' for h, b in by_h.items())})")
    print(f"  -> pooled point correction factor exp(-b) = {math.exp(-log_bias):.4f}")

    shrink = math.exp(-log_bias)
    scores = sorted(
        abs(p * shrink - a) / (se * shrink)
        for _, _, _, a, p, _, _, se in ksub
    )
    level = math.ceil((n + 1) * 0.9)
    if level > n:
        print(f"n={n} too small for a finite 90% conformal quantile.")
        q = float("nan")
    else:
        q = scores[level - 1]
    kappa = q / 1.645
    print(f"n={n} folds  q90={q:.3f}  kappa = q/1.645 = {kappa:.3f}")
    print("-> paste into analyses/forecast_rxkids_2028.py:")
    print(f"     BIRTH_KALMAN_LOG_BIAS = {log_bias:.4f}")
    print(f"     BIRTH_KALMAN_SE_KAPPA = {kappa:.3f}")

    # In-sample check (the folds that produced kappa — tautologically >= 90%):
    with_k = [abs(p * shrink - a) <= 1.645 * kappa * se * shrink
              for _, _, _, a, p, _, _, se in ksub]
    print(f"coverage with bias+kappa applied (in-sample): "
          f"{100*sum(with_k)/len(with_k):.1f}%")
    corr_ape = statistics.fmean(abs(p * shrink - a) / a for _, _, _, a, p, _, _, _ in ksub)
    print(f"bias-corrected MAPE (in-sample): {100*corr_ape:.2f}% "
          f"(raw kalman was {100*summary['kalman']['mape']:.2f}%)")

    # ---- verdict ---------------------------------------------------------
    print("\n--- verdict ---")
    if "ensemble" in summary and "persistence" in summary:
        e, p = summary["ensemble"]["mape"], summary["persistence"]["mape"]
        verdict = "beats" if e < p else "LOSES TO"
        print(f"ensemble {verdict} persistence ({100*e:.2f}% vs {100*p:.2f}% MAPE)")
    if "kalman" in summary and "persistence" in summary:
        k, p = summary["kalman"]["mape"], summary["persistence"]["mape"]
        verdict = "beats" if k < p else "LOSES TO"
        print(f"kalman {verdict} persistence ({100*k:.2f}% vs {100*p:.2f}% MAPE)")
    if "ensemble" in summary and "kalman" in summary:
        e, k = summary["ensemble"]["mape"], summary["kalman"]["mape"]
        better = "kalman" if k < e else "ensemble"
        print(f"better point accuracy: {better} "
              f"(ensemble {100*e:.2f}% vs kalman {100*k:.2f}% MAPE)")
        ec, kc = summary["ensemble"]["cov"], summary["kalman"]["cov"]
        print(f"raw CI90 coverage: ensemble {100*ec:.0f}%, kalman {100*kc:.0f}% "
              f"(target 90%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
