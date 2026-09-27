// The simulator's capital gains against the capital-gains page
// (site/data/capital-gains, forecast_cg_rate_options.py).
//
//   node --test tests/simulator/*.test.mjs
//
// The kernel's gains base must be the page's: in MID, which DOTAX anchors,
// each DOTAX AGI class's gains equal DOTAX's TY2022 resident gains grown to
// the year (anchor_check.csv), and the total is the page's nltcg_M. LOW and
// HIGH keep MID's scale factors on their own classes and model gains, so
// their gains move with their top incomes. A plan that changes only the
// gains rate then lands near the page's published
// estimates, scored here with the kernel's own tax before credits, as the page
// scores. The levels cannot match exactly (the page projects its own
// population); the bounds are the build's (build_simulator_population.py,
// check_cg_page_parity). Like accounting.test.mjs, these read the committed
// data, not the golden fixtures.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { gunzipSync } from "node:zlib";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  decodePopulation, parseSpec, resolveSpec, unitArrays, gainsClasses, prepareSystem, unitTax,
  behave, topRateChanges,
} from "../../site/assets/simulator/kernel.js";

const REPO = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const DATA = process.env.SIM_DATA_DIR || join(REPO, "site/data/tax-simulator");
const CG_PAGE = process.env.CG_PAGE_DIR || join(REPO, "site/data/capital-gains");
const meta = JSON.parse(readFileSync(join(DATA, "population.json"), "utf8"));
const blob = gunzipSync(readFileSync(join(DATA, "population.bin.gz")));
const pop = decodePopulation(meta, blob.buffer.slice(blob.byteOffset, blob.byteOffset + blob.byteLength));

// check_cg_page_parity's bounds
const LEVEL_BAND = [0.90, 1.02];
const RESPONSE_RATIO_TOL = 0.005;

function csv(name) {
  const [head, ...rows] = readFileSync(join(CG_PAGE, name), "utf8").trim().split("\n");
  const keys = head.split(",");
  return rows.map((r) => Object.fromEntries(r.split(",").map((v, i) => [keys[i], v === "" || Number.isNaN(Number(v)) ? v : Number(v)])));
}
const anchorCheck = csv("anchor_check.csv");
const published = csv("revenue_by_year.csv").filter((r) => r.law === "act24" && r.top_share === "central");

const rel = (a, b) => Math.abs(a - b) / Math.max(1, Math.abs(b));

// Weighted gains by class code for (year, scenario): the anchored gains the
// kernel scores and the model's own (the scenario's tail overrides applied).
function classTotals(y, s) {
  const A = pop.arrays, u = unitArrays(pop, y, s), cls = gainsClasses(pop, y, s);
  const cgModel = Float64Array.from(A.cg);
  if (s !== "mid") {
    const t = A[`tail_${s}_idx`], tc = A[`tail_${s}_cg`];
    for (let j = 0; j < t.length; j++) cgModel[t[j]] = tc[j];
  }
  const anchored = new Float64Array(meta.cg_anchor.classes.length), model = anchored.slice();
  for (let i = 0; i < pop.n; i++) {
    anchored[cls[i]] += u.agi[i] * u.cg[i] * u.weight[i];
    model[cls[i]] += u.agi[i] * cgModel[i] * u.weight[i];
  }
  return { anchored, model, sum: anchored.reduce((a, b) => a + b, 0) / 1e6 };
}

test("DOTAX anchors MID: each class's gains are DOTAX's, the total the page's", () => {
  const classes = meta.cg_anchor.classes;
  assert.equal(meta.cg_anchor.top_share, 0.8);
  for (const y of meta.years) {
    const { anchored: total, sum: nltcg } = classTotals(y, "mid");
    assert.equal(total[0], 0, `${y}: gains below the $100K class are unallocated`);
    for (let c = 1; c < classes.length; c++) {
      const want = anchorCheck.find((r) => r.tax_year === y && r.group === classes[c]);
      assert.ok(want, `anchor_check.csv ${y} ${classes[c]}`);
      assert.ok(rel(total[c] / 1e6, want.dotax_target_M) < 1e-9,
        `mid ${y} ${classes[c]}: $${total[c] / 1e6}M vs DOTAX $${want.dotax_target_M}M`);
    }
    const pub = [...new Set(published.filter((r) => r.tax_year === y).map((r) => r.nltcg_M))];
    assert.equal(pub.length, 1, `${y} nltcg_M`);
    assert.ok(rel(nltcg, pub[0]) < 1e-9, `${y}: $${nltcg}M vs the page's nltcg_M $${pub[0]}M`);
  }
});

test("LOW and HIGH keep MID's factors, so their gains move with their top incomes", () => {
  const classes = meta.cg_anchor.classes;
  for (const y of meta.years) {
    const kMid = meta.cg_anchor.k.mid[String(y)];
    assert.equal(kMid[0], 0);
    for (let c = 1; c < classes.length; c++) assert.ok(kMid[c] > 0, `mid ${y} ${classes[c]}: k ${kMid[c]}`);
    const sums = {};
    for (const s of ["low", "high"]) {
      assert.deepEqual(meta.cg_anchor.k[s][String(y)], kMid, `${s} ${y}: MID's scale factors`);
      const { anchored, model, sum } = classTotals(y, s);
      assert.equal(anchored[0], 0, `${s} ${y}: gains below the $100K class are unallocated`);
      for (let c = 1; c < classes.length; c++) {
        assert.ok(rel(anchored[c], kMid[c] * model[c]) < 1e-9,
          `${s} ${y} ${classes[c]}: $${anchored[c] / 1e6}M vs MID's k x model $${kMid[c] * model[c] / 1e6}M`);
      }
      sums[s] = sum;
    }
    // not pinned to DOTAX: fewer gains where top incomes are lower, more where higher
    const mid = classTotals(y, "mid").sum;
    assert.ok(sums.low < mid && mid < sums.high, `${y}: LOW ${sums.low} / MID ${mid} / HIGH ${sums.high}`);
  }
});

test("no gains share is capped, and no gains sit on zero income", () => {
  // the share form, min(1, share x k), then equals the page's dollars
  for (const s of ["low", "mid", "high"]) {
    for (const y of meta.years) {
      const u = unitArrays(pop, y, s);
      for (let i = 0; i < pop.n; i++) {
        if (u.cg[i] === 1) assert.fail(`${s} ${y} unit ${i}: share capped at 1`);
        if (u.cg[i] > 0 && !(u.agi[i] > 0)) assert.fail(`${s} ${y} unit ${i}: gains at income ${u.agi[i]}`);
      }
    }
  }
});

test("a gains-only plan lands near the capital-gains page's estimates", () => {
  // MID, before credits: static and after the realization response
  for (const [option, preset] of [["cap9", "cg_9"], ["ordinary", "cg_ordinary"]]) {
    const spec = meta.presets.find((p) => p.name === preset);
    assert.ok(spec, preset);
    const systems = resolveSpec(parseSpec(spec, meta), meta);
    const prepared = Object.fromEntries(meta.years.map((y) => [y, {
      base: prepareSystem(systems[y].baseline, meta.food_excise),
      reform: prepareSystem(systems[y].reform, meta.food_excise) }]));
    const path = meta.years.map((y) => ({ year: y, changes: topRateChanges(prepared[y].base, prepared[y].reform) }));
    for (const y of meta.years) {
      const { base, reform } = prepared[y];
      const u = unitArrays(pop, y, "mid");
      const adj = behave(pop, base, reform, u, meta.scenarios.mid, y, path);
      let b = 0, r = 0, r2 = 0;
      for (let i = 0; i < pop.n; i++) {
        const fs = pop.arrays.fs[i], deps = pop.arrays.deps[i];
        b += unitTax(base, fs, deps, u.agi[i], u.item[i], u.cg[i]).before * u.weight[i];
        r += unitTax(reform, fs, deps, u.agi[i], u.item[i], u.cg[i]).before * u.weight[i];
        r2 += unitTax(reform, fs, deps, adj.agi[i], adj.item[i], adj.cg[i]).before * adj.weight[i];
      }
      const stat = (r - b) / 1e6, resp = (r2 - b) / 1e6;
      const pub = published.find((x) => x.tax_year === y && x.option === option);
      assert.ok(pub, `${option} ${y} published`);
      for (const [what, v] of [["static", stat / pub.static_M], ["with response", resp / pub.behavioral_M]]) {
        assert.ok(v >= LEVEL_BAND[0] && v <= LEVEL_BAND[1], `${option} ${y} ${what}: simulator / published ${v.toFixed(4)}`);
      }
      const ratio = resp / stat, pubRatio = pub.behavioral_M / pub.static_M;
      assert.ok(Math.abs(ratio - pubRatio) <= RESPONSE_RATIO_TOL,
        `${option} ${y}: response ratio ${ratio.toFixed(4)} vs the page's ${pubRatio.toFixed(4)}`);
    }
  }
});

test("the response is the page's: MID's realization parameter is its BETA", () => {
  // forecast_cg_rate_options.py BETA = 2.0; the strong-response parameters
  // (the LOW revenue scenario) 2.6, the weak ones (HIGH) 1.6
  assert.equal(meta.scenarios.mid.cg_beta, 2.0);
  assert.equal(meta.scenarios.low.cg_beta, 2.6);
  assert.equal(meta.scenarios.high.cg_beta, 1.6);
});
