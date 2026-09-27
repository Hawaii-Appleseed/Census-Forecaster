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
