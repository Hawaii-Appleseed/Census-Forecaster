// Parity tests: the browser kernel against the Python model.
//
//   node --test tests/simulator/*.test.mjs
//
// Every expected value was computed by the Python pipeline (TaxCalculator,
// apply_behavioral_response, generate_quintile_report) on the same population
// the kernel loads; scripts/build_simulator_population.py writes both. Paths
// can be overridden with SIM_DATA_DIR and SIM_GOLDEN.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { gunzipSync } from "node:zlib";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  decodePopulation, score, householdTax, parseSpec, resolveSpec, unitArrays,
  prepareSystem, unitTax, behave, topRateChanges, SpecError, WEB_FORMAT_VERSION,
} from "../../site/assets/simulator/kernel.js";

const REPO = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const DATA = process.env.SIM_DATA_DIR || join(REPO, "site/data/tax-simulator");
const GOLDEN = process.env.SIM_GOLDEN || join(REPO, "tests/simulator/golden.json.gz");

const meta = JSON.parse(readFileSync(join(DATA, "population.json"), "utf8"));
const blob = gunzipSync(readFileSync(join(DATA, "population.bin.gz")));
const buffer = blob.buffer.slice(blob.byteOffset, blob.byteOffset + blob.byteLength);
const pop = decodePopulation(meta, buffer);
const golden = JSON.parse(gunzipSync(readFileSync(GOLDEN)).toString("utf8"));

// Totals are sums of ~40,000 terms in a different order from numpy's pairwise
// sum; 1e-6 ($1 on a $M figure) is far above that noise and far below any
// real difference.
const TOL = 1e-6;

function close(actual, expected, where, tol = TOL) {
  const scale = Math.max(1, Math.abs(expected));
  assert.ok(Math.abs(actual - expected) <= tol * scale,
    `${where}: kernel ${actual} vs model ${expected} (diff ${actual - expected})`);
}

test("the kernel reads only its own population format", () => {
  assert.equal(meta.web_format_version, WEB_FORMAT_VERSION);
  assert.throws(() => decodePopulation({ ...meta, web_format_version: 1 }, buffer), /format 1/);
  assert.throws(() => decodePopulation({ ...meta, web_format_version: undefined }, buffer), /format/);
});

test("there is no fallback to the model's own gains base", () => {
  // a population without the DOTAX anchor, or a system without a gains
  // rate, is an error, not current law
  assert.throws(() => unitArrays({ ...pop, meta: { ...meta, cg_anchor: undefined } }, 2027, "mid"), /scale factors/);
  assert.throws(() => unitArrays({ ...pop, arrays: { ...pop.arrays, cgcls_2029: undefined } }, 2029, "mid"), /classes/);
  assert.throws(() => unitArrays({ ...pop, arrays: { ...pop.arrays, cgcls_low_2029_idx: undefined } }, 2029, "low"), /low/);
  const { capital_gains_rate: _, ...noRate } = meta.current_law["2027"];
  assert.throws(() => prepareSystem(noRate, meta.food_excise), /capital_gains_rate/);
  assert.throws(() => prepareSystem({ ...noRate, capital_gains_rate: "7.25" }, meta.food_excise), /capital_gains_rate/);
});

test("population decodes to the model's shape", () => {
  assert.equal(pop.n, golden.population_meta.n_units);
  assert.equal(pop.nHouseholds, golden.population_meta.n_households);
  assert.deepEqual(meta.years, golden.population_meta.years);
  for (const y of meta.years) {
    for (let i = 0; i < pop.n; i++) {
      if (!Number.isFinite(pop.arrays[`agi_${y}`][i]) || !Number.isFinite(pop.arrays[`item_${y}`][i])) {
        assert.fail(`non-finite value for unit ${i} in ${y}`);
      }
    }
  }
});

for (const c of golden.cases) {
  test(`revenue matches the model: ${c.name}`, () => {
    const years = Object.keys(c.systems).map(Number);
    const systems = Object.fromEntries(years.map((y) => [y, c.systems[y]]));
    const res = score(pop, systems);
    const byKey = new Map(res.revenue.map((r) => [`${r.scenario}/${r.tax_year}`, r]));
    for (const e of c.expected.revenue) {
      const r = byKey.get(`${e.scenario}/${e.tax_year}`);
      assert.ok(r, `missing ${e.scenario}/${e.tax_year}`);
      for (const k of ["baseline_$M", "reform_$M", "static_$M", "behavioral_$M", "filers_1m_post_response"]) {
        close(r[k], e[k], `${c.name} ${e.scenario} ${e.tax_year} ${k}`);
      }
    }
  });

  const distYears = Object.keys(c.expected.distribution);
  if (distYears.length) {
    test(`distribution matches the model: ${c.name}`, () => {
      const years = Object.keys(c.systems).map(Number);
      const systems = Object.fromEntries(years.map((y) => [y, c.systems[y]]));
      const res = score(pop, systems, { scenarios: [], distributionYears: distYears.map(Number) });
      for (const y of distYears) {
        for (const kind of ["quintile", "income_class"]) {
          const got = res.distribution[y][kind], want = c.expected.distribution[y][kind];
          assert.deepEqual(got.map((g) => g.group), want.map((g) => g.group), `${c.name} ${y} ${kind} groups`);
          want.forEach((w, i) => {
            for (const [k, v] of Object.entries(w)) {
              if (k === "group") continue;
              close(got[i][k], v, `${c.name} ${y} ${kind} ${w.group} ${k}`);
            }
          });
        }
      }
    });
  }
}

test("per-unit tax matches the model", () => {
  const byName = new Map(golden.cases.map((c) => [c.name, c]));
  assert.ok(golden.unit_sample.units.some((s) => s.case === "cg_ordinary"), "capital-gains cases sampled");
  for (const s of golden.unit_sample.units) {
    const sys = prepareSystem(byName.get(s.case).systems[s.year][s.side], meta.food_excise);
    const u = unitArrays(pop, s.year, "mid");
    s.units.forEach((i, k) => {
      const t = unitTax(sys, pop.arrays.fs[i], pop.arrays.deps[i], u.agi[i], u.item[i], u.cg[i]);
      close(t.before, s.before_credits[k], `${s.case} ${s.year} ${s.side} unit ${i} before`, 1e-9);
      close(t.credits, s.credits[k], `${s.case} ${s.year} ${s.side} unit ${i} credits`, 1e-9);
      close(t.net, s.net[k], `${s.case} ${s.year} ${s.side} unit ${i} net`, 1e-9);
    });
  }
});

test("the responded population matches the model, unit by unit", () => {
  // income, gains share and weight after ETI, migration and the
  // realization response, and the plan's tax on them (apply_behavioral_response)
  const r = golden.unit_sample.response;
  const c = golden.cases.find((x) => x.name === r.case);
  const prepared = Object.fromEntries(meta.years.map((y) => [y, {
    base: prepareSystem(c.systems[y].baseline, meta.food_excise),
    reform: prepareSystem(c.systems[y].reform, meta.food_excise) }]));
  const path = meta.years.map((y) => ({ year: y, changes: topRateChanges(prepared[y].base, prepared[y].reform) }));
  const u = unitArrays(pop, r.year, r.scenario);
  const adj = behave(pop, prepared[r.year].base, prepared[r.year].reform, u, meta.scenarios[r.scenario], r.year, path);
  let realized = 0;
  r.units.forEach((i, k) => {
    const where = `${r.case} ${r.scenario} ${r.year} unit ${i}`;
    close(adj.agi[i], r.agi[k], `${where} income`, 1e-9);
    close(adj.cg[i], r.cg_share[k], `${where} gains share`, 1e-9);
    close(adj.weight[i], r.weight[k], `${where} weight`, 1e-9);
    const t = unitTax(prepared[r.year].reform, pop.arrays.fs[i], pop.arrays.deps[i], adj.agi[i], adj.item[i], adj.cg[i]);
    close(t.net, r.net[k], `${where} net`, 1e-9);
    if (adj.cg[i] < u.cg[i]) realized++;
  });
  assert.ok(realized > 50, `${realized} sampled units realize fewer gains`);
});

test("household calculator matches the model", () => {
  for (const h of golden.households) {
    const t = householdTax(golden.household_systems[h.system], meta.food_excise, {
      fs: h.filing_status, agi: h.agi, dependents: h.dependents, itemized: h.itemized, cgShare: h.cg_share,
    });
    const where = `${h.system} ${h.filing_status} agi=${h.agi} deps=${h.dependents}`;
    close(t.before, h.before_credits, `${where} before`, 1e-9);
    close(t.credits, h.credits, `${where} credits`, 1e-9);
    close(t.net, h.net, `${where} net`, 1e-9);
  }
});

test("specs resolve to the model's systems", () => {
  for (const { spec, systems } of golden.spec_resolution) {
    const resolved = resolveSpec(parseSpec(spec, meta), meta);
    for (const [y, want] of Object.entries(systems)) {
      const got = resolved[y].reform;
      assert.deepEqual(got.brackets, want.brackets, `${spec.name} ${y} brackets`);
      assert.deepEqual(got.standard_deduction, want.standard_deduction, `${spec.name} ${y} deductions`);
      assert.equal(got.personal_exemption, want.personal_exemption, `${spec.name} ${y} exemption`);
      assert.equal(got.capital_gains_rate, want.capital_gains_rate, `${spec.name} ${y} capital gains rate`);
      assert.equal(got.credit_year, want.credit_year, `${spec.name} ${y} credit year`);
      assert.deepEqual(resolved[y].baseline, meta.current_law[y]);
    }
  }
});

test("invalid specs are rejected, as the model rejects them", () => {
  for (const spec of golden.invalid_specs) {
    assert.throws(() => parseSpec(spec, meta), undefined, JSON.stringify(spec));
  }
});

test("a capital-gains rate is refused in the model's own words", () => {
  assert.equal(golden.cg_invalid_messages.length, 9);
  for (const { spec, message } of golden.cg_invalid_messages) {
    assert.throws(() => parseSpec(spec, meta), (e) => e instanceof SpecError && e.message === message,
      `${JSON.stringify(spec.income_tax)}: expected "${message}"`);
  }
});

test("unusual but valid specs are accepted, as the model accepts them", () => {
  for (const spec of golden.valid_edge_specs) parseSpec(spec, meta);
});

test("current law scores exactly zero", () => {
  const cl = meta.presets.find((p) => p.name === "current_law");
  const res = score(pop, resolveSpec(parseSpec(cl, meta), meta), { distributionYears: [2027] });
  for (const r of res.revenue) {
    assert.equal(r["static_$M"], 0, `${r.scenario} ${r.tax_year} static`);
    assert.equal(r["behavioral_$M"], 0, `${r.scenario} ${r.tax_year} behavioral`);
  }
  for (const g of res.distribution[2027].quintile) assert.equal(g.pct_pay_more + g.pct_pay_less, 0);
});

test("no scenario ever has a negative number of filers", () => {
  for (const c of golden.cases) {
    for (const r of c.expected.revenue) assert.ok(r.filers_1m_post_response >= 0, `${c.name} ${r.scenario} ${r.tax_year}`);
  }
  // With an elasticity large enough to take more than everyone (0.15 per
  // point, the value before September 27, 2026), a 30% top rate removes
  // every $1M+ filer by 2031 in the low scenario, and no more than everyone.
  const big = golden.cases.find((c) => c.name === "top_rate_30");
  assert.ok(big, "golden case top_rate_30");
  const strong = { ...pop, meta: { ...pop.meta, scenarios: { ...pop.meta.scenarios,
    low: { ...pop.meta.scenarios.low, migration_elast: 0.15 } } } };
  const res = score(strong, big.systems, { scenarios: ["low"] });
  for (const r of res.revenue) assert.ok(r.filers_1m_post_response >= 0, `strong ${r.tax_year}`);
  assert.equal(res.revenue.find((r) => r.tax_year === 2031).filers_1m_post_response, 0);
});

test("the same law scores the same whichever year the plan says it starts", () => {
  // top_15_from_2029: current law until 2029, then 15% (16% from 2030),
  // written from 2027. The same law written from 2029 must match it.
  const c = golden.cases.find((x) => x.name === "top_15_from_2029");
  const spec = golden.spec_resolution.find((x) => x.spec.name === "top_15_from_2029").spec;
  const late = { ...spec, first_year: 2029, income_tax: { bracket_vintages: spec.income_tax.bracket_vintages.slice(1) } };
  const a = score(pop, Object.fromEntries(Object.entries(c.systems).map(([y, v]) => [y, v])));
  const b = score(pop, resolveSpec(parseSpec(late, meta), meta));
  a.revenue.forEach((r, i) => {
    close(b.revenue[i]["behavioral_$M"], r["behavioral_$M"], `${r.scenario} ${r.tax_year}`, 1e-12);
  });
});

test("splitting a bracket at the same rate changes no one", () => {
  const c = golden.cases.find((x) => x.name === "split_top_bracket");
  for (const kind of ["quintile", "income_class"]) {
    for (const g of c.expected.distribution["2029"][kind]) {
      assert.equal(g.pct_pay_more, 0, `${kind} ${g.group}`);
      assert.equal(g.pct_pay_less, 0, `${kind} ${g.group}`);
    }
  }
});

test("a full scoring is fast enough for a live page", () => {
  // top_rate_14: ETI and migration; top14_cg9 adds the realization response
  for (const [name, spec] of [["top_rate_14", meta.presets.find((p) => p.name === "top_rate_14")],
    ["top14_cg9", golden.spec_resolution.find((x) => x.spec.name === "top14_cg9").spec]]) {
    const systems = resolveSpec(parseSpec(spec, meta), meta);
    const t0 = performance.now();
    score(pop, systems, { distributionYears: [2027] });
    const ms = performance.now() - t0;
    // 3 scenarios x 5 years x 3 passes over ~40,000 units, plus one distribution.
    assert.ok(ms < 5000, `full scoring of ${name} took ${ms.toFixed(0)} ms`);
    console.log(`# full scoring, ${name}: ${ms.toFixed(0)} ms`);
  }
});
