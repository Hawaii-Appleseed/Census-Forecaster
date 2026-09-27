// The counterfactual behind "with response": the plan is scored on the
// population after it responds, current law on the population before anyone
// does (behavioral_response.score_with_response):
//
//   change = Σ w′·T_plan(y′) − Σ w·T_current(y)
//
// These tests read only the population, not the golden fixtures, so they
// would catch the kernel and the Python model drifting back to the old
// accounting together (the golden tests could not: they are regenerated from
// the model). They set their own migration elasticity, so they test the
// accounting, not its calibration.
//
//   node --test tests/simulator/*.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { gunzipSync } from "node:zlib";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  decodePopulation, score, parseSpec, resolveSpec, unitArrays, prepareSystem, unitNetTaxes,
  unitTax, behave, topRateChanges, householdTax,
} from "../../site/assets/simulator/kernel.js";

const REPO = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const DATA = process.env.SIM_DATA_DIR || join(REPO, "site/data/tax-simulator");
const meta = JSON.parse(readFileSync(join(DATA, "population.json"), "utf8"));
const blob = gunzipSync(readFileSync(join(DATA, "population.bin.gz")));
const pop = decodePopulation(meta, blob.buffer.slice(blob.byteOffset, blob.byteOffset + blob.byteLength));

/** The population with the middle scenario's behavioral parameters overridden. */
const withParams = (over) => ({ ...pop,
  meta: { ...pop.meta, scenarios: { ...pop.meta.scenarios, mid: { ...pop.meta.scenarios.mid, ...over } } } });
const close = (a, b, where, tol = 1e-9) =>
  assert.ok(Math.abs(a - b) <= tol * Math.max(1, Math.abs(b)), `${where}: ${a} vs ${b}`);

test("a filer who moves away costs their whole tax under the plan", () => {
  // Migration only (ETI 0): with response = static − Σ w (1 − f) T_plan(y),
  // f from the documented tiers: full at max($1M, the plan's top floor),
  // half from the higher of the two top floors to $1M. The old accounting
  // charged only T_plan − T_current.
  const Y = 2029, e = 0.1;
  const top14 = meta.presets.find((p) => p.name === "top_rate_14");
  const systems = resolveSpec(parseSpec(top14, meta), meta);
  const p = withParams({ eti: 0, migration_elast: e });
  const r = score(p, systems, { scenarios: ["mid"], years: [Y] }).revenue[0];

  const n = meta.scenarios.mid.migration_phase_in_years;
  const base = prepareSystem(systems[Y].baseline, meta.food_excise);
  const plan = prepareSystem(systems[Y].reform, meta.food_excise);
  const u = unitArrays(pop, Y, "mid");
  const tPlan = unitNetTaxes(pop, plan, u.agi, u.item, u.cg);
  const tCurrent = unitNetTaxes(pop, base, u.agi, u.item, u.cg);
  let lostWhole = 0, lostIncrease = 0;
  for (let i = 0; i < pop.n; i++) {
    const b = base.byStatus[pop.arrays.fs[i]], s = plan.byStatus[pop.arrays.fs[i]];
    const pp = 100 * (s.rates.at(-1) - b.rates.at(-1));          // 1 point, from 2027
    const loss = e * pp * Math.min(1, (Y - 2027 + 1) / n);
    const floor = s.floors.at(-1), lower = Math.max(floor, b.floors.at(-1));
    let f = 1;
    if (u.agi[i] >= Math.max(1e6, floor)) f = 1 - loss;
    else if (lower < 1e6 && u.agi[i] >= lower) f = 1 - loss / 2;
    lostWhole += u.weight[i] * (1 - f) * tPlan[i];
    lostIncrease += u.weight[i] * (1 - f) * (tPlan[i] - tCurrent[i]);
  }
  assert.ok(lostWhole > 1e6, "someone moves");
  close(r["behavioral_$M"], r["static_$M"] - lostWhole / 1e6, "with response");
  assert.ok(Math.abs(r["behavioral_$M"] - (r["static_$M"] - lostIncrease / 1e6)) > 1, "not the old accounting");
});

test("rate cuts get no response, so a plan that only cuts scores the same both ways", () => {
  const cut = { name: "cut10", first_year: 2027, income_tax: { bracket_vintages: meta.current_law_vintages.map((v) => ({
    from: v.from,
    brackets: Object.fromEntries(Object.entries(v.brackets).map(([k, rows]) => [k, rows.map(([f, r]) => [f, r * 0.9])])),
  })) } };
  const res = score(pop, resolveSpec(parseSpec(cut, meta), meta));
  for (const r of res.revenue) {
    assert.ok(r["static_$M"] < 0);
    assert.equal(r["behavioral_$M"], r["static_$M"], `${r.scenario} ${r.tax_year}`);
  }
});

test("migrants from a temporary rise stay gone after the plan returns to current law", () => {
  // 15% top rate in 2027–2028, current law from 2029. From 2029 the plan
  // taxes everyone as current law does (static 0), but the rise's migration
  // unwinds only as the fall phases in, and those filers' tax is lost. The
  // old accounting scored these years as 0.
  const cl = meta.current_law_vintages;
  const top15 = Object.fromEntries(Object.entries(cl[0].brackets).map(([k, rows]) =>
    [k, [...rows.slice(0, -1), [rows.at(-1)[0], 15]]]));
  const spec = { name: "temp15", first_year: 2027,
    income_tax: { bracket_vintages: [{ from: 2027, brackets: top15 }, cl[1]] } };
  const res = score(withParams({ migration_elast: 0.1 }), resolveSpec(parseSpec(spec, meta), meta),
    { scenarios: ["mid"] });
  for (const r of res.revenue.filter((x) => x.tax_year >= 2029)) {
    assert.equal(r["static_$M"], 0, `${r.tax_year} static`);
    assert.ok(r["behavioral_$M"] < -1, `${r.tax_year} with response ${r["behavioral_$M"]}`);
  }
});

// ---------------------------------------------------------------------------
// capital gains: the HRS §235-51(f) alternative tax and the realization
// response (cg_alternative.alternative_tax, apply_realization_response)
// ---------------------------------------------------------------------------

const cgSpec = (name, rate, extra = {}) => ({ name, first_year: 2027,
  income_tax: { capital_gains_rate: rate, ...extra } });
const scoreSpec = (spec, p = pop, opts = {}) => score(p, resolveSpec(parseSpec(spec, meta), meta), opts);
const FIELDS = ["baseline_$M", "reform_$M", "static_$M", "behavioral_$M", "filers_1m_post_response"];

test("the alternative tax: hand cases", () => {
  // brackets 2% / 6% from $10K / 10% from $50K, no deduction or exemption,
  // so taxable income is AGI; 7.25% first reached at $50K (the model's
  // test_cg_alternative hand cases)
  const rows = [[0, 2], [10_000, 6], [50_000, 10]];
  const sys = (rate) => ({ name: "hand", credit_year: 2027, personal_exemption: 0, capital_gains_rate: rate,
    brackets: Object.fromEntries(["Single_Married_Separate", "Joint_Surviving_Spouse", "Head_of_Household"].map((k) => [k, rows])),
    standard_deduction: { Single_Married_Separate: 0, Joint_Surviving_Spouse: 0, Head_of_Household: 0 } });
  const before = (agi, gains, rate) => householdTax(sys(rate), meta.food_excise, { fs: "single", agi, cgShare: gains / agi }).before;
  for (const [agi, gains, want] of [
    [40_000, 20_000, 200 + 30_000 * 0.06],                              // below the rate's floor
    [80_000, 50_000, 2_600 + 0.0725 * 30_000],                          // straddling it
    [200_000, 50_000, 2_600 + 100_000 * 0.10 + 0.0725 * 50_000],        // above it
    [30_000, 100_000, 200 + 20_000 * 0.06],                             // gains above taxable income
    [70_000, 1_000_000, 2_600 + 0.0725 * 20_000],
  ]) close(before(agi, gains, 7.25), want, `agi ${agi} gains ${gains}`, 1e-12);
  // a rate at or above every bracket rate, or none: the regular tax
  for (const rate of [10.5, 20, "ordinary"]) assert.equal(before(200_000, 50_000, rate), 2_600 + 150_000 * 0.10);
  // rate 0: gains untaxed
  assert.equal(before(200_000, 50_000, 0), 2_600 + 100_000 * 0.10);
});

test("current law's gains rate scores exactly zero", () => {
  const res = scoreSpec(cgSpec("cg_7_25", 7.25), pop, { distributionYears: [2027] });
  for (const r of res.revenue) {
    assert.equal(r["static_$M"], 0, `${r.scenario} ${r.tax_year} static`);
    assert.equal(r["behavioral_$M"], 0, `${r.scenario} ${r.tax_year} behavioral`);
  }
  for (const g of res.distribution[2027].income_class) assert.equal(g.pct_pay_more + g.pct_pay_less, 0);
});

test("a gains rate above every bracket rate is taxing gains as ordinary income", () => {
  const a = scoreSpec(cgSpec("cg_20", 20)), b = scoreSpec(cgSpec("cg_ordinary", "ordinary"));
  a.revenue.forEach((r, i) => {
    for (const k of FIELDS) assert.equal(r[k], b.revenue[i][k], `${r.scenario} ${r.tax_year} ${k}`);
  });
});

test("per unit, the statute is never above the regular tax or the old stacked shortcut", () => {
  // stacked: the bracket tax on ordinary income plus the smaller of the
  // bracket tax on the gains and rate x gains (v1's formula)
  const stacked = (sys, fs, deps, agi, item, share) => {
    const s = sys.byStatus[fs], ded = Math.max(s.sd, item), ex = (1 + (fs === 1 ? 1 : 0) + deps) * sys.pe;
    const bt = (t) => { let i = 0; while (i + 1 < s.floors.length && s.floors[i + 1] <= t) i++; return s.cum[i] + (t - s.floors[i]) * s.rates[i]; };
    const full = bt(Math.max(0, agi - ded - ex)), g = Math.max(0, agi * share);
    if (!(g > 0)) return full;
    const ord = bt(Math.max(0, Math.max(0, agi - g) - ded - ex));
    return ord + Math.min(Math.max(0, full - ord), g * sys.cgRate);
  };
  for (const [y, rate] of [[2027, 7.25], [2031, 9]]) {
    const base = meta.current_law[y];
    const statute = prepareSystem({ ...base, capital_gains_rate: rate }, meta.food_excise);
    const regular = prepareSystem({ ...base, capital_gains_rate: "ordinary" }, meta.food_excise);
    const u = unitArrays(pop, y, "mid");
    let binding = 0;
    for (let i = 0; i < pop.n; i++) {
      const args = [pop.arrays.fs[i], pop.arrays.deps[i], u.agi[i], u.item[i], u.cg[i]];
      const t = unitTax(statute, ...args).before, r = unitTax(regular, ...args).before;
      const st = stacked(statute, ...args);
      // (up to rounding: the two formulas add the same pieces in a different order)
      const tol = 1e-12 * Math.max(1, r);
      assert.ok(t <= r + tol && t <= st + tol, `${y} unit ${i}: statute ${t}, regular ${r}, stacked ${st}`);
      if (t < r - 1) binding++;
    }
    assert.ok(binding > 100, `${y}: the alternative tax binds for only ${binding} units`);
  }
});

test("a plan that changes only the gains rate scores, and moves no one", () => {
  // resolveSpec must not drop a gains-only plan as "no change"; the gains
  // rate drives no migration, so weights are untouched
  const res = scoreSpec(cgSpec("cg_9", 9));
  for (const r of res.revenue) {
    assert.ok(r["static_$M"] > 10, `${r.scenario} ${r.tax_year} static ${r["static_$M"]}`);
    assert.ok(r["behavioral_$M"] < r["static_$M"] && r["behavioral_$M"] > 0, `${r.scenario} ${r.tax_year} with response`);
  }
  const systems = resolveSpec(parseSpec(cgSpec("cg_9", 9), meta), meta);
  const base = prepareSystem(systems[2029].baseline, meta.food_excise);
  const plan = prepareSystem(systems[2029].reform, meta.food_excise);
  const path = meta.years.map((y) => ({ year: y, changes: topRateChanges(prepareSystem(systems[y].baseline, meta.food_excise),
    prepareSystem(systems[y].reform, meta.food_excise)) }));
  const u = unitArrays(pop, 2029, "mid");
  const adj = behave(pop, base, plan, u, meta.scenarios.mid, 2029, path);
  assert.equal(adj.weight, u.weight, "weights are the population's own");
  assert.notEqual(adj.cg, u.cg, "gains respond");
  // the response is the capital-gains page's: gains fall by exp(-beta x the
  // rise in the rate on gains), and income falls by the gains not realized
  const beta = meta.scenarios.mid.cg_beta;
  let hit = 0;
  for (let i = 0; i < pop.n; i++) {
    if (adj.cg[i] === u.cg[i]) { assert.equal(adj.agi[i], u.agi[i]); continue; }
    hit++;
    const g0 = u.agi[i] * u.cg[i], g1 = adj.agi[i] * adj.cg[i];
    const d = -Math.log(g1 / g0) / beta;                      // the rise in the rate on gains
    assert.ok(d > 0 && d <= 0.09 - 0.0725 + 1e-9, `unit ${i}: rise ${d}`);
    close(u.agi[i] - adj.agi[i], g0 - g1, `unit ${i} income`, 1e-6);
  }
  assert.ok(hit > 1000, `${hit} units realize fewer gains`);
});

test("a cut in the gains rate gets no response, so it scores the same both ways", () => {
  for (const rate of [5, 0]) {
    for (const r of scoreSpec(cgSpec(`cg_${rate}`, rate)).revenue) {
      assert.ok(r["static_$M"] < 0);
      assert.equal(r["behavioral_$M"], r["static_$M"], `${rate}% ${r.scenario} ${r.tax_year}`);
    }
  }
});

test("a plan that changes only the brackets leaves gains to ETI alone", () => {
  // no realization response without a change in the gains rate: behave
  // hands back the population's own gains shares
  const top14 = meta.presets.find((p) => p.name === "top_rate_14");
  const systems = resolveSpec(parseSpec(top14, meta), meta);
  const path = meta.years.map((y) => ({ year: y, changes: topRateChanges(prepareSystem(systems[y].baseline, meta.food_excise),
    prepareSystem(systems[y].reform, meta.food_excise)) }));
  for (const s of ["low", "mid", "high"]) {
    const u = unitArrays(pop, 2031, s);
    const adj = behave(pop, prepareSystem(systems[2031].baseline, meta.food_excise),
      prepareSystem(systems[2031].reform, meta.food_excise), u, meta.scenarios[s], 2031, path);
    assert.equal(adj.cg, u.cg, s);
  }
});
