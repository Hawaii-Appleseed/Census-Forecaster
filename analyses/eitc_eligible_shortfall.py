#!/usr/bin/env python3
"""Read-only diagnosis: where the model's missing federal-EITC-eligible tax units are (Hawaii, TY2022).

Loads the cached chain tax units (data/artifacts/working_family_units.pkl: 2024 1-year PUMS) and the PUMS
persons, then (1) decomposes the raw (un-reweighted) eligible population against IRS SOI Table 2 claims by number
of children, filing status, AGI class and household structure, and (2) runs one counterfactual at a time on the
units in memory, reporting the eligible units each recovers. Changes no default, calibration, run or site file.

Run from the repo root (about a minute, well under 1 GB):  uv run python analyses/eitc_eligible_shortfall.py
"""
from __future__ import annotations

import pickle
import sys

import numpy as np
import pandas as pd

sys.path[:0] = ["scripts"]
from tax_modeler.credits.eitc import calculate_eitc, calculate_eitc_for_tax_units  # noqa: E402

TY = 2022
CPI_2022, CPI_2024 = 292.655, 313.689          # CPI-U annual averages (BLS): PUMS 2024 incomes -> 2022 dollars
SOI_AGI_LABELS = ["<$1", "$1-10K", "$10-25K", "$25-50K", "$50-75K", "$75K+"]
SOI_AGI_BINS = [-1e12, 1, 10_000, 25_000, 50_000, 75_000, 1e12]
# IRS SOI Historic Table 2, Hawaii TY2022 (22in12hi.xlsx, "Earned income credit with N qualifying children: Number"),
# by AGI class; the classes above $75K are all zero.
SOI = {
    0: [600, 14_420, 11_890, 0, 0, 0],
    1: [130, 3_750, 9_320, 14_610, 0, 0],
    2: [80, 1_610, 5_310, 10_630, 840, 0],
    3: [40, 940, 3_000, 6_380, 1_440, 0],
}
SOI_TOTAL = {0: 26_900, 1: 27_820, 2: 18_460, 3: 11_790}   # published totals (the cells are rounded to 10s)
CENSUS_RATE = 0.833


def load():
    with open("data/artifacts/working_family_units.pkl", "rb") as fh:
        u = pickle.load(fh)[0].reset_index(drop=True)
    p = pd.read_parquet("packages/data/raw/pums_2024_1yr/psam_p15.parquet")
    p["pid"] = p.SERIALNO.astype(str) + "_" + p.SPORDER.astype(str)
    return u, p.set_index("pid")


class Model:
    def __init__(self, u: pd.DataFrame, p: pd.DataFrame):
        self.u, self.p = u, p
        self.w = u["weight"].to_numpy(float)
        self.hh = u["SERIALNO"].astype(str).to_numpy()
        self.base_deps = [list(d) for d in u["dependent_person_ids"]]
        self.unit_of: dict[str, int] = {}
        for i, (a, b) in enumerate(zip(u["primary_filer_id"], u["secondary_filer_id"], strict=True)):
            self.unit_of[str(a)] = i
            if isinstance(b, str):
                self.unit_of[b] = i
        self.persons_of = [[x for x in (a, b) if isinstance(x, str)]
                           for a, b in zip(u["primary_filer_id"], u["secondary_filer_id"], strict=True)]
        self.hh_units: dict[str, list[int]] = {}
        for i, h in enumerate(self.hh):
            self.hh_units.setdefault(h, []).append(i)
        self.rec_cache: dict[tuple, dict] = {}
        self.cols = ["filing_status", "federal_earned_income", "federal_agi", "earned_income", "income",
                     "investment_income", "primary_agep"]

    def rec(self, pid: str, rel: int | None = None) -> dict:
        key = (pid, rel)
        if key not in self.rec_cache:
            r = self.p.loc[pid]

            def si(v, d=0):
                return d if pd.isna(v) else int(v)
            self.rec_cache[key] = {
                "age": si(r.AGEP), "relationship": rel if rel is not None else si(r.RELSHIPP),
                "citizenship": si(r.CIT, 1), "months_in_home": 12, "school_level": si(r.SCHL),
                "disabled": si(r.DIS, 2) == 1, "pwgtp": float(r.PWGTP),
            }
        return self.rec_cache[key]

    def frame(self, deps, **over) -> pd.DataFrame:
        f = self.u[self.cols].copy()
        for k, v in over.items():
            f[k] = v
        f["dependents_details"] = [[self.rec(d) if not isinstance(d, tuple) else self.rec(*d) for d in ds]
                                   for ds in deps]
        return f

    def run(self, deps=None, weights=None, tax_year=TY, **over) -> pd.DataFrame:
        deps = self.base_deps if deps is None else deps
        r = calculate_eitc_for_tax_units(self.frame(deps, **over), tax_year=tax_year)
        w = self.w if weights is None else weights
        return pd.DataFrame({"w": w, "amt": r.eitc_amount.to_numpy(float), "k": r.eitc_qualifying_children.to_numpy(),
                             "elig": (r.eitc_amount.to_numpy(float) > 0)})

    @staticmethod
    def summ(r: pd.DataFrame) -> dict:
        e = r.elig.to_numpy()
        kk = np.minimum(r.k.to_numpy(), 3)
        out = {"n": float(r.w[e].sum()), "usd_m": float((r.w * r.amt).sum() / 1e6)}
        for b in range(4):
            out[b] = float(r.w[e & (kk == b)].sum())
        return out

    # one-unit EITC for the greedy assignment (scalar path of the same function)
    def unit_eitc(self, i: int, deps: list, fs: str | None = None) -> float:
        row = self.u.iloc[i]
        d = {c: row[c] for c in self.cols}
        if fs:
            d["filing_status"] = fs
        d["dependents_details"] = [self.rec(x) if not isinstance(x, tuple) else self.rec(*x) for x in deps]
        d["num_dependents"] = len(deps)
        return calculate_eitc(d, tax_year=TY)["eitc_amount"]


def household_structure(p: pd.DataFrame) -> pd.Series:
    """Per SERIALNO: nuclear / partner_or_unrelated / extended (parents, grandchildren, siblings, in-laws, other relatives)."""
    rel = p.groupby("SERIALNO").RELSHIPP.apply(set)
    out = {}
    for h, s in rel.items():
        if s & {22, 24, 34, 36}:
            out[str(h)] = "partner/unrelated adults"
        elif s & {28, 29, 30, 31, 32, 33}:
            out[str(h)] = "extended (multigen/relatives)"
        else:
            out[str(h)] = "nuclear (householder, spouse, own children)"
    return pd.Series(out)


def tab(title: str, rows: list[tuple], header: list[str]):
    print(f"\n{title}")
    print(" | ".join(header))
    for r in rows:
        print(" | ".join(f"{x:,.0f}" if isinstance(x, (int, float, np.floating)) else str(x) for x in r))


def decompose(m: Model, base: pd.DataFrame):
    u, w = m.u, m.w
    e = base.elig.to_numpy()
    k = np.minimum(base.k.to_numpy(), 3)
    print(f"Baseline raw eligible units: {w[e].sum():,.0f}  (${(base.w*base.amt).sum()/1e6:,.1f}M)")
    # sampling error from the 80 ACS replicate weights
    reps = [c for c in u.columns if c.startswith("weight_r")]
    th = np.array([u[c].to_numpy(float)[e].sum() for c in reps])
    se = np.sqrt(4 / 80 * ((th - w[e].sum()) ** 2).sum())
    print(f"Replicate-weight SE of the eligible total (fixed eligibility): {se:,.0f}  -> +/-1.645 SE = {1.645*se:,.0f}")
    for b in range(4):
        thb = np.array([u[c].to_numpy(float)[e & (k == b)].sum() for c in reps])
        print(f"   kids {b}: {w[e&(k==b)].sum():,.0f}  SE {np.sqrt(4/80*((thb-w[e&(k==b)].sum())**2).sum()):,.0f}")

    tab("A. By qualifying children (raw model vs SOI claims vs Census-implied eligible = claims/0.833)",
        [(f"{b}{'+' if b == 3 else ''}", w[e & (k == b)].sum(), SOI_TOTAL[b], SOI_TOTAL[b] / CENSUS_RATE,
          w[e & (k == b)].sum() - SOI_TOTAL[b] / CENSUS_RATE) for b in range(4)] +
        [("all", w[e].sum(), sum(SOI_TOTAL.values()), sum(SOI_TOTAL.values()) / CENSUS_RATE,
          w[e].sum() - sum(SOI_TOTAL.values()) / CENSUS_RATE)],
        ["kids", "model eligible", "SOI claims", "claims/0.833", "model - implied"])

    fs = u["filing_status"].to_numpy()
    rows = []
    for s in ["single", "head_of_household", "married_filing_jointly", "married_filing_separately"]:
        rows.append((s, *[w[e & (fs == s) & (k == b)].sum() for b in range(4)], w[e & (fs == s)].sum(),
                     w[fs == s].sum()))
    tab("B. Model eligible by filing status x kids (last column: all units of that status)", rows,
        ["status", "0", "1", "2", "3+", "eligible", "all units"])

    cls = pd.cut(u["federal_agi"].to_numpy(), SOI_AGI_BINS, labels=SOI_AGI_LABELS, right=False)
    rows = []
    for b in range(4):
        for c, lab in enumerate(SOI_AGI_LABELS):
            mv = w[e & (k == b) & (np.asarray(cls) == lab)].sum()
            if mv or SOI[b][c]:
                rows.append((f"{b}{'+' if b==3 else ''} kids", lab, mv, SOI[b][c], mv - SOI[b][c]))
    tab("C. Eligible by AGI class (federal_agi, as SOI Table 2) x kids: model vs SOI claims", rows,
        ["kids", "AGI class", "model", "SOI claims", "model-SOI"])

    # the pool: units that have the child and earnings but sit in the EITC AGI range, vs SOI claims
    ei = u["federal_earned_income"].to_numpy() > 0
    rows = []
    for b in range(1, 4):
        pool = w[(k_all(base) == b) & ei & (u["federal_agi"].to_numpy() < 50_000)].sum()
        rows.append((f"{b}{'+' if b==3 else ''} kids", pool, w[e & (k == b) & (u['federal_agi'].to_numpy() < 50_000)].sum(),
                     sum(SOI[b][:4])))
    tab("D. The pool: units with b EITC children, earnings > 0 and AGI < $50K (model) vs eligible vs SOI claims < $50K",
        rows, ["kids", "pool (any unit)", "model eligible", "SOI claims"])

    hs = household_structure(m.p.reset_index())
    st = pd.Series(m.hh).map(hs).to_numpy()
    allp = m.p.reset_index()
    hh_w = allp.drop_duplicates("SERIALNO").assign(S=lambda d: d.SERIALNO.astype(str).map(hs))
    tot_hh = hh_w.groupby("S").WGTP.sum() if "WGTP" in hh_w.columns else None
    rows = []
    for s in sorted(set(st)):
        rows.append((s, *[w[e & (st == s) & (k == b)].sum() for b in range(4)], w[e & (st == s)].sum(),
                     w[(st == s) & (k_all(base) >= 1)].sum(), w[st == s].sum()))
    tab("E. Model eligible by household structure x kids (last two: units with children; all units)", rows,
        ["structure", "0", "1", "2", "3+", "eligible", "units w/ EITC child", "all units"])
    if tot_hh is not None:
        print("households (WGTP) by structure:", {a: round(b) for a, b in tot_hh.items()})
    return st


def k_all(base):  # EITC-qualifying child count including units not eligible (uncapped)
    return np.minimum(base.k.to_numpy(), 3)


def report_cf(name: str, how: str, base_s: dict, s: dict, rows: list):
    rows.append((name, how, s["n"] - base_s["n"], *[s[b] - base_s[b] for b in range(4)], s["n"], s["usd_m"] - base_s["usd_m"]))


def counterfactuals(m: Model, base: pd.DataFrame):
    u, p = m.u, m.p
    bs = m.summ(base)
    rows: list = []

    def add(name, how, r):
        report_cf(name, how, bs, m.summ(r), rows)

    # --- V: dollar-year / rules-year
    f = CPI_2022 / CPI_2024
    inc = ["federal_earned_income", "federal_agi", "earned_income", "income", "investment_income"]
    add("V1 deflate incomes to 2022 dollars (CPI-U x0.933)", "measured",
        m.run(**{c: u[c].to_numpy(float) * f for c in inc}))
    add("V1b deflate x0.90 (wage growth > CPI)", "measured", m.run(**{c: u[c].to_numpy(float) * 0.90 for c in inc}))
    r24 = m.run(tax_year=2024)
    print(f"\nTY2024 rules on the same 2024-dollar units: eligible {m.summ(r24)['n']:,.0f} (${m.summ(r24)['usd_m']:,.1f}M); "
          f"IRS TY2024 claims 81,800, DOTAX 78,399")

    # --- C3: income measurement
    add("C3a drop the investment-income test", "measured", m.run(investment_income=np.zeros(len(u))))
    wages = u["primary_wagp"].fillna(0).clip(lower=0).to_numpy(float) + u["secondary_wagp"].fillna(0).clip(lower=0).to_numpy(float)
    add("C3b W-2 wage factor 1.00 instead of 0.87", "measured",
        m.run(federal_earned_income=u["earned_income"].to_numpy(float),
              federal_agi=u["federal_agi"].to_numpy(float) + 0.13 * wages))
    nontax = sum(u[c].fillna(0).to_numpy(float) for c in ["primary_ssip", "secondary_ssip", "primary_pap",
                                                          "secondary_pap", "primary_oip", "secondary_oip"])
    add("C3c AGI excludes SSI, public assistance, other income (non-taxable in PINCP)", "measured",
        m.run(federal_agi=np.maximum(u["federal_agi"].to_numpy(float) - nontax, 0)))
    add("C3d AGI = earned income only (floor on what a return could show)", "measured",
        m.run(federal_agi=u["federal_earned_income"].to_numpy(float)))

    # --- C4: filing status
    fsr = u["filing_status"].to_numpy()
    add("C4a MFS units allowed to claim (post-2021 rule)", "measured",
        m.run(filing_status=np.where(fsr == "married_filing_separately", "single", fsr)))
    print(f"   MFS units: {m.w[fsr == 'married_filing_separately'].sum():,.0f} weighted; "
          f"median AGI {u.loc[fsr == 'married_filing_separately', 'federal_agi'].median():,.0f}")

    # --- C5: weights
    fac = pd.Series(fsr).map({"single": 0.82, "married_filing_jointly": 1.0, "head_of_household": 1.30,
                              "married_filing_separately": 1.35}).to_numpy()
    add("C5 remove the DOTAX filing-status weight factors (0.82/1.00/1.30/1.35)", "measured",
        m.run(weights=m.w / fac))

    # --- C1/C2: construction and child rules. Each rule moves children between the household's existing units
    # (it never creates units); `reassign` starts from `deps_in`, so the rules can also be chained.
    def hh_unit(h):
        return [i for i in m.hh_units.get(h, []) if any(int(p.loc[x].RELSHIPP) == 20 for x in m.persons_of[i])]

    def units_with_rel(h, rels, min_age=None):
        out = []
        for i in m.hh_units.get(h, []):
            for pid in m.persons_of[i]:
                r = p.loc[pid]
                if int(r.RELSHIPP) in rels and (min_age is None or min_age <= r.AGEP):
                    out.append(i)
                    break
        return out

    def reassign(selector, cand_fn, mode, deps_in=None, rel_override=25):
        """selector(pid, row, claimed) picks children; cand_fn(h, row) gives candidate unit indices.
        mode 'unique': move only when exactly one candidate; 'best': move to the candidate with the largest marginal
        EITC (the child's parent claims); 'opt': best of staying put and the candidates (family-optimal)."""
        deps = [list(d) for d in (m.base_deps if deps_in is None else deps_in)]
        claimed = {}
        for i, ds in enumerate(deps):
            for d in ds:
                claimed[d if isinstance(d, str) else d[0]] = i
        moved = 0
        for pid, row in p.iterrows():
            if not selector(pid, row, claimed):
                continue
            h = str(row.SERIALNO)
            cur = claimed.get(pid)
            cands = [c for c in cand_fn(h, row) if c != cur]
            if not cands or (mode == "unique" and len(cands) != 1):
                continue
            kid = (pid, rel_override)
            if cur is not None:
                deps[cur] = [d for d in deps[cur] if (d if isinstance(d, str) else d[0]) != pid]
            opts = cands + ([cur] if (mode == "opt" and cur is not None) else [])
            best_c, best_v = None, -1.0
            for c in opts:
                v = m.unit_eitc(c, deps[c] + [kid]) - m.unit_eitc(c, deps[c])   # marginal EITC of claiming the child
                if v > best_v:
                    best_c, best_v = c, v
            if mode == "unique":
                best_c = cands[0]
            deps[best_c].append(kid)
            claimed[pid] = best_c
            moved += best_c != cur
        return deps, moved

    def parent_cands(h, row):                       # adult child / child-in-law of the householder, 15+ years older
        return [c for c in units_with_rel(h, {25, 26, 27, 32}, row.AGEP + 15) if row.name not in m.persons_of[c]]

    def unrel_cands(h, row):                        # unmarried partner, else an unrelated adult 15+ years older
        return units_with_rel(h, {22, 24}) or units_with_rel(h, {34, 36}, row.AGEP + 15)

    def rel_cands(h, row):                          # householder, or a sibling / other relative / parent 15+ years older
        return hh_unit(h) + units_with_rel(h, {28, 29, 33}, row.AGEP + 15)

    def any_rel(h, row):
        return units_with_rel(h, {25, 26, 27, 28, 29, 31, 32, 33})

    def to_hh(h, row):
        return hh_unit(h)

    def sel_gc(pid, r, cl):
        return int(r.RELSHIPP) == 30 and r.AGEP < 19

    def sel_un(pid, r, cl):
        return int(r.RELSHIPP) in (34, 36) and r.AGEP < 19

    def sel_or(pid, r, cl):
        return int(r.RELSHIPP) in (28, 33) and r.AGEP < 19

    def sel_unclaimed(pid, r, cl):
        return pid not in cl and pid not in m.unit_of and r.AGEP < 19

    def sel_18(pid, r, cl):
        return r.AGEP == 18 and int(r.RELSHIPP) in (25, 26, 27) and pid not in cl

    def sel_dis(pid, r, cl):
        earn = (0 if pd.isna(r.WAGP) else r.WAGP) + (0 if pd.isna(r.SEMP) else r.SEMP)
        return r.AGEP >= 19 and int(r.RELSHIPP) in (25, 26, 27) and int(r.DIS) == 1 and earn <= 0 and pid not in cl

    def sel_own(pid, r, cl):
        return int(r.RELSHIPP) in (25, 26, 27, 30) and r.AGEP < 19

    for mode, lab in (("unique", "only households with one candidate parent"),
                      ("best", "parent claims; best of several candidates"),
                      ("opt", "family-optimal (householder or parent)")):
        deps, mv = reassign(sel_gc, parent_cands, mode)
        add(f"C1a grandchildren to their parent: {lab}", f"measured, {mv} kids moved in the sample", m.run(deps))
    deps, mv = reassign(sel_un, unrel_cands, "best")
    add("C1b minors of an unmarried partner / unrelated adult go to that adult", f"measured, {mv} moved", m.run(deps))
    deps, mv = reassign(sel_or, rel_cands, "opt")
    add("C2a nieces/nephews/siblings (RELSHIPP 33/28 minors) count, go to parent or householder (family-optimal)",
        f"measured, {mv} moved", m.run(deps))
    deps, mv = reassign(sel_unclaimed, to_hh, "opt")
    add("C2b every minor no unit claims goes to the householder unit", f"measured, {mv} moved", m.run(deps))
    deps, mv = reassign(sel_18, to_hh, "opt")
    add("C2c unclaimed 18-year-old own children stay the householder's qualifying children (<19 at year end)",
        f"measured, {mv} moved", m.run(deps))
    deps, mv = reassign(sel_dis, to_hh, "opt")
    add("C2d disabled (DIS=1), non-earning adult children qualify at any age", f"measured, {mv} moved", m.run(deps))
    deps, mv = reassign(sel_own, any_rel, "opt")
    add("C1e UPPER BOUND ignoring the AGI tie-break: any adult relative claims any minor, family-optimal",
        f"measured, {mv} moved; unlawful when a higher-AGI parent can claim", m.run(deps))

    # --- chained: the lawful construction rules together, then with the dollar-year fix
    d = None
    for sel, cf, mode in ((sel_gc, parent_cands, "opt"), (sel_un, unrel_cands, "best"), (sel_or, rel_cands, "opt"),
                          (sel_unclaimed, to_hh, "opt"), (sel_18, to_hh, "opt"), (sel_dis, to_hh, "opt")):
        d, _ = reassign(sel, cf, mode, deps_in=d)
    rc = m.run(d)
    add("CHAIN all lawful construction/child-rule fixes (C1a opt, C1b, C2a-d)", "measured", rc)
    defl = {c: u[c].to_numpy(float) * f for c in inc}
    rcv = m.run(d, **defl)
    add("CHAIN construction fixes + 2022-dollar incomes (CPI)", "measured", rcv)
    rall = m.run(d, **{**defl, "federal_agi": np.maximum((u["federal_agi"].to_numpy(float) - nontax) * f, 0)})
    add("CHAIN ... + AGI excludes non-taxable PINCP components", "measured", rall)
    r24c = m.run(d, tax_year=2024)
    print(f"\nTY2024 rules, 2024 dollars, construction fixes chained: eligible {m.summ(r24c)['n']:,.0f} "
          f"(unfixed {m.summ(r24)['n']:,.0f}); implied eligible at 83.3% from IRS TY2024 claims: "
          f"{81_800 / CENSUS_RATE:,.0f}")
    cv = m.summ(rcv)
    print("CHAIN construction+vintage by kids:", {b: round(cv[b]) for b in range(4)},
          "vs claims/0.833:", {b: round(SOI_TOTAL[b] / CENSUS_RATE) for b in range(4)})
    # Independent check of the reassignment: the same units' child-tax-credit counts against SOI Table 2
    # (nonrefundable CTC 154,800 returns, ACTC 61,270, dependents = individuals - returns - joint returns = 307,820).
    from tax_modeler.projection.tax_unit_projector import _recalculate_ctc

    def ctc(deps):
        fr = u.copy()
        fr["dependents_details"] = m.frame(deps)["dependents_details"].to_numpy()
        fr["dependents"] = fr["dependents_details"]
        fr["num_dependents"] = [len(x) for x in deps]
        c = _recalculate_ctc(fr, tax_year=TY)
        return (m.w[(c.ctc_nonrefundable > 0).to_numpy()].sum(), m.w[(c.ctc_refundable > 0).to_numpy()].sum(),
                (m.w * [len(x) for x in deps]).sum())
    b_ctc, c_ctc = ctc(m.base_deps), ctc(d)
    own = sum(m.rec(x if isinstance(x, str) else x[0])["pwgtp"] for ds in m.base_deps for x in ds)
    tab("F. Returns with a child: model units vs SOI Table 2 (TY2022, Hawaii)",
        [("CTC nonrefundable returns", b_ctc[0], c_ctc[0], 154_800), ("ACTC returns", b_ctc[1], c_ctc[1], 61_270),
         ("dependents (unit weight x claimed)", b_ctc[2], c_ctc[2], 307_820),
         ("dependents (own PWGTP, baseline)", own, own, 307_820)],
        ["measure", "model baseline", "model after chain", "SOI"])
    st = household_structure(p.reset_index())
    sts = pd.Series(m.hh).map(st).to_numpy()
    for nm, r_ in (("baseline", base), ("chain construction", rc)):
        print(f"   eligible by structure, {nm}:",
              {s_: round(r_.w[r_.elig.to_numpy() & (sts == s_)].sum()) for s_ in sorted(set(sts))})
    return rows, bs


def main():
    u, p = load()
    m = Model(u, p)
    base = m.run()
    print(f"Check vs the earlier test: raw eligible {m.summ(base)['n']:,.0f} (earlier test 68,620)")
    decompose(m, base)
    rows, bs = counterfactuals(m, base)
    print("\nCOUNTERFACTUALS, one at a time (change in weighted eligible units vs baseline "
          f"{bs['n']:,.0f}; shortfall to 102,005 is {102_005 - bs['n']:,.0f})")
    print("name | basis | d_total | d_0 | d_1 | d_2 | d_3+ | new total | d_usd_M")
    for r in rows:
        print(" | ".join(f"{x:,.0f}" if isinstance(x, (float, np.floating)) else str(x) for x in r))


if __name__ == "__main__":
    main()
