// The simulator page's state logic (site/assets/simulator/plan.js): the
// editor must turn every plan into the spec it shows, so a preset or a
// shared link scores exactly as the model scores that spec.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { gunzipSync } from "node:zlib";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { parseSpec, resolveSpec } from "../../site/assets/simulator/kernel.js";
import {
  stateFromSpec, specFromState, activeVintages, setTopRate, setTopFloor, addBracket,
  removeBracket, editorRows, warnings, encodeSpec, decodeSpec, isLinked, systemsWithoutGainsRate,
} from "../../site/assets/simulator/plan.js";

const REPO = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const DATA = process.env.SIM_DATA_DIR || join(REPO, "site/data/tax-simulator");
const GOLDEN = process.env.SIM_GOLDEN || join(REPO, "tests/simulator/golden.json.gz");
const meta = JSON.parse(readFileSync(join(DATA, "population.json"), "utf8"));
const golden = JSON.parse(gunzipSync(readFileSync(GOLDEN)).toString("utf8"));

const systemsOf = (spec) => resolveSpec(parseSpec(spec, meta), meta);

function sameSystems(a, b, where) {
  for (const y of meta.years) {
    for (const side of ["baseline", "reform"]) {
      assert.deepEqual(a[y][side].brackets, b[y][side].brackets, `${where} ${y} ${side} brackets`);
      assert.deepEqual(a[y][side].standard_deduction, b[y][side].standard_deduction, `${where} ${y} ${side} deductions`);
      assert.equal(a[y][side].personal_exemption, b[y][side].personal_exemption, `${where} ${y} ${side} exemption`);
      assert.equal(a[y][side].capital_gains_rate, b[y][side].capital_gains_rate, `${where} ${y} ${side} capital gains rate`);
    }
  }
}

test("every preset survives the editor unchanged", () => {
  for (const p of meta.presets) {
    const spec = specFromState(stateFromSpec(p, meta), meta);
    sameSystems(systemsOf(spec), systemsOf(p), p.name);
  }
});

test("every golden spec survives the editor unchanged", () => {
  for (const { spec } of golden.spec_resolution) {
    const round = specFromState(stateFromSpec(spec, meta), meta);
    sameSystems(systemsOf(round), systemsOf(spec), spec.name);
  }
});

test("current law's schedules are linked 1.5 / 2 times single", () => {
  for (const v of meta.current_law_vintages) assert.ok(isLinked(v.brackets), `vintage ${v.from}`);
  assert.equal(stateFromSpec(meta.presets[0], meta).linked, true);
});

test("the first tax year picks the periods that apply", () => {
  const s = stateFromSpec(meta.presets[0], meta);
  const cl = meta.current_law_vintages;
  for (const [fy, froms] of [[2027, [2027, 2029]], [2028, [2028, 2029]], [2029, [2029]], [2031, [2031]]]) {
    s.firstYear = fy;
    const act = activeVintages(s);
    assert.deepEqual(act.map((v) => v.from), froms, `first year ${fy}`);
    assert.deepEqual(act[act.length - 1].brackets, cl[cl.length - 1].brackets);
    const spec = specFromState(s, meta);
    assert.equal(spec.first_year, fy);
    // unchanged current law resolves to current law in every year
    for (const [y, sys] of Object.entries(systemsOf(spec))) {
      assert.deepEqual(sys.reform.brackets, sys.baseline.brackets, `fy ${fy} year ${y}`);
    }
  }
});

test("the top-rate control changes only top rates, in every period and status", () => {
  const s = stateFromSpec(meta.presets[0], meta);
  setTopRate(s, 14.5);
  for (const sys of Object.values(systemsOf(specFromState(s, meta)))) {
    for (const [k, rows] of Object.entries(sys.reform.brackets)) {
      const base = sys.baseline.brackets[k];
      assert.deepEqual(rows.slice(0, -1), base.slice(0, -1), k);
      assert.deepEqual(rows[rows.length - 1], [base[base.length - 1][0], 14.5], k);
    }
  }
});

test("the top-threshold control sets single and scales the others", () => {
  const s = stateFromSpec(meta.presets[0], meta);
  setTopFloor(s, 600000);
  for (const v of s.vintages) {
    assert.equal(v.brackets.single.at(-1)[0], 600000);
    assert.equal(v.brackets.head_of_household.at(-1)[0], 900000);
    assert.equal(v.brackets.married_filing_jointly.at(-1)[0], 1200000);
  }
  const spec = specFromState(s, meta);
  parseSpec(spec, meta);
  assert.equal(systemsOf(spec)[2027].reform.brackets.Joint_Surviving_Spouse.at(-1)[0], 1200000);
});

test("adding and removing brackets keeps linked schedules in step", () => {
  const s = stateFromSpec(meta.presets[0], meta);
  const n = s.vintages[0].brackets.single.length;
  addBracket(s);
  assert.equal(s.vintages[0].brackets.single.length, n + 1);
  assert.equal(s.vintages[0].brackets.married_filing_jointly.length, n + 1);
  const added = s.vintages[0].brackets.single.at(-1);
  assert.ok(added[0] > s.vintages[0].brackets.single.at(-2)[0]);
  parseSpec(specFromState(s, meta), meta);
  removeBracket(s, 0);                              // the first bracket stays
  assert.equal(s.vintages[0].brackets.single.length, n + 1);
  removeBracket(s, n);
  assert.equal(s.vintages[0].brackets.head_of_household.length, n);
  s.activeStatus = "married_filing_jointly";
  assert.deepEqual(editorRows(s), s.vintages[0].brackets.single.map((r) => [r[0] * 2, r[1]]));
});

test("unlinked schedules are edited separately", () => {
  const s = stateFromSpec(meta.presets[0], meta);
  s.linked = false;
  s.activeStatus = "head_of_household";
  addBracket(s);
  assert.equal(s.vintages[0].brackets.head_of_household.length, s.vintages[0].brackets.single.length + 1);
  const spec = specFromState(s, meta);
  assert.ok(Array.isArray(spec.income_tax.bracket_vintages[0].brackets.head_of_household));
  parseSpec(spec, meta);
});

test("a flat schedule has no top threshold to move", () => {
  const s = stateFromSpec(meta.presets[0], meta);
  for (const v of s.vintages) for (const k of Object.keys(v.brackets)) v.brackets[k] = [[0, 5]];
  setTopFloor(s, 50000);
  for (const v of s.vintages) for (const rows of Object.values(v.brackets)) assert.deepEqual(rows, [[0, 5]]);
  parseSpec(specFromState(s, meta), meta);
});

test("a shared link's own bracket periods survive the editor", () => {
  const cl = meta.current_law_vintages;
  const top15 = Object.fromEntries(Object.entries(cl[1].brackets).map(([k, rows]) =>
    [k, [...rows.slice(0, -1), [rows.at(-1)[0], 15]]]));
  const spec = { name: "p2030", first_year: 2027, income_tax: { bracket_vintages: [
    { from: 2027, brackets: cl[0].brackets }, { from: 2030, brackets: top15 }] } };
  const s = stateFromSpec(spec, meta);
  assert.deepEqual(s.vintages.map((v) => v.from), [2027, 2029, 2030]);
  sameSystems(systemsOf(specFromState(s, meta)), systemsOf(spec), "p2030");
});

test("guardrail warnings", () => {
  const ids = (s) => warnings(s, meta).map((w) => w.id).sort();
  const s = stateFromSpec(meta.presets[0], meta);
  assert.deepEqual(ids(s), []);
  setTopRate(s, 20);
  assert.deepEqual(ids(s), ["extreme"]);
  setTopRate(s, 12);
  assert.deepEqual(ids(s), ["cut"]);
  setTopRate(s, 13);
  s.vintages[0].brackets.single[3][1] = 1.0;       // a rate that falls
  assert.deepEqual(ids(s), ["falling"]);
});

test("share links round-trip, and garbage is refused", () => {
  const s = stateFromSpec(meta.presets[1], meta);
  s.label = "Hawaiʻi plan — test ✓";
  const spec = specFromState(s, meta);
  const hash = `#${encodeSpec(spec)}`;
  assert.deepEqual(decodeSpec(hash), spec);
  assert.deepEqual(decodeSpec(`#x=1&${encodeSpec(spec)}`), spec);
  assert.equal(decodeSpec("#plan=!!!"), null);
  assert.equal(decodeSpec("#plan=bm90IGpzb24"), null);
  assert.equal(decodeSpec(""), null);
  assert.equal(stateFromSpec(decodeSpec(hash), meta).label, "Hawaiʻi plan — test ✓");
});

test("the capital gains rate: presets, current law, a rate and ordinary income", () => {
  const preset = (name) => meta.presets.find((p) => p.name === name);
  assert.equal(stateFromSpec(meta.presets[0], meta).cg, null);
  assert.equal(stateFromSpec(preset("cg_9"), meta).cg, 9);
  assert.equal(stateFromSpec(preset("cg_ordinary"), meta).cg, "ordinary");
  // "current_law" and a missing key are the same plan
  const s = stateFromSpec({ name: "x", first_year: 2027, income_tax: { capital_gains_rate: "current_law" } }, meta);
  assert.equal(s.cg, null);
  assert.equal("capital_gains_rate" in specFromState(s, meta).income_tax, false);
  for (const cg of [0, 5, 9.5, 99.99, "ordinary"]) {
    s.cg = cg;
    const spec = specFromState(s, meta);
    assert.equal(spec.income_tax.capital_gains_rate, cg);
    const sys = systemsOf(spec);
    for (const y of meta.years) {
      assert.equal(sys[y].reform.capital_gains_rate, cg, `${cg} ${y}`);
      assert.equal(sys[y].baseline.capital_gains_rate, meta.current_law[String(y)].capital_gains_rate);
    }
    assert.equal(stateFromSpec(decodeSpec(`#${encodeSpec(spec)}`), meta).cg, cg);
  }
  // before the first tax year the plan is current law, gains rate included
  s.firstYear = 2029;
  s.cg = 9;
  const sys = systemsOf(specFromState(s, meta));
  assert.equal(sys[2028].reform.capital_gains_rate, 7.25);
  assert.equal(sys[2029].reform.capital_gains_rate, 9);
});

test("a link made before the capital gains control reopens unchanged", () => {
  // Made by the page as first published (site/assets/simulator/plan.js at
  // 65e1a6e, before capital gains): top rate 14 percent from 2028, its own
  // standard deduction and personal exemption.
  const v1 = "plan=eyJuYW1lIjoibXlfcGxhbiIsImxhYmVsIjoiSGF3YWnKu2kgcGxhbiIsImZpcnN0X3llYXIiOjIwMjgsImluY29tZV90YXgiOnsiYnJhY2tldF92aW50YWdlcyI6W3siZnJvbSI6MjAyOCwiYnJhY2tldHMiOnsic2luZ2xlIjpbWzAsMS40XSxbMTQ0MDAsMi41XSxbMTkyMDAsNV0sWzI0MDAwLDYuNF0sWzM2MDAwLDYuOF0sWzQ4MDAwLDcuMl0sWzEyNTAwMCw3LjZdLFsxNzUwMDAsOC4yNV0sWzIyNTAwMCw5XSxbMjc1MDAwLDEwXSxbMzI1MDAwLDExXSxbNTAwMDAwLDE0XV0sImhlYWRfb2ZfaG91c2Vob2xkIjp7InNjYWxlIjoic2luZ2xlIiwiZmFjdG9yIjoxLjV9LCJtYXJyaWVkX2ZpbGluZ19qb2ludGx5Ijp7InNjYWxlIjoic2luZ2xlIiwiZmFjdG9yIjoyfX19LHsiZnJvbSI6MjAyOSwiYnJhY2tldHMiOnsic2luZ2xlIjpbWzAsMS40XSxbMTkyMDAsMi41XSxbMjQwMDAsNV0sWzM2MDAwLDYuNF0sWzQ4MDAwLDYuOF0sWzEyNTAwMCw3LjJdLFsxNzUwMDAsOC4yNV0sWzIyNTAwMCw5XSxbMjc1MDAwLDEwXSxbMzI1MDAwLDExXSxbNTAwMDAwLDE0XV0sImhlYWRfb2ZfaG91c2Vob2xkIjp7InNjYWxlIjoic2luZ2xlIiwiZmFjdG9yIjoxLjV9LCJtYXJyaWVkX2ZpbGluZ19qb2ludGx5Ijp7InNjYWxlIjoic2luZ2xlIiwiZmFjdG9yIjoyfX19XSwic3RhbmRhcmRfZGVkdWN0aW9uIjp7InNpbmdsZSI6OTAwMCwiaGVhZF9vZl9ob3VzZWhvbGQiOjEzNTAwLCJtYXJyaWVkX2ZpbGluZ19qb2ludGx5IjoxODAwMH0sInBlcnNvbmFsX2V4ZW1wdGlvbiI6MTUwMH0sIm1vZGVsX3ZlcnNpb24iOiIyMDI2LTA5LTI3KzFjOTFiYzMxNDkifQ";
  const spec = decodeSpec(`#${v1}`);
  assert.equal(spec.model_version, "2026-09-27+1c91bc3149");
  assert.equal("capital_gains_rate" in spec.income_tax, false);
  parseSpec(spec, meta);
  const s = stateFromSpec(spec, meta);
  assert.equal(s.cg, null);
  // The editor gives back the same bytes; only the model version, which
  // the page compares to say the numbers have moved, would differ.
  assert.equal(encodeSpec(specFromState(s, { ...meta, model_version: spec.model_version })), v1);
  assert.notEqual(spec.model_version, meta.model_version);
  // and it scores at current law's capital gains rate
  for (const [y, sys] of Object.entries(systemsOf(spec))) {
    assert.equal(sys.reform.capital_gains_rate, meta.current_law[y].capital_gains_rate, y);
  }
});

test("capital gains warnings", () => {
  const ids = (s) => warnings(s, meta).map((w) => w.id).sort();
  const s = stateFromSpec(meta.presets[0], meta);
  const clCg = meta.current_law["2027"].capital_gains_rate;
  s.cg = clCg;                                        // current law's rate, set by hand
  assert.deepEqual(ids(s), []);
  s.cg = 9;
  assert.deepEqual(ids(s), ["cg_response"]);
  s.cg = 5;
  assert.deepEqual(ids(s), ["cg_cut"]);
  s.cg = 0;
  assert.deepEqual(ids(s), ["cg_cut"]);
  s.cg = 13;                                          // current law's top rate: never lower than the brackets
  assert.deepEqual(ids(s), ["cg_inert", "cg_response"]);
  s.cg = 20;
  assert.deepEqual(ids(s), ["cg_inert", "cg_response"]);
  // ordinary income under current law's brackets is the largest change the
  // capital gains page scored (13 - 7.25 = 5.75 points), so not "large"
  s.cg = "ordinary";
  assert.deepEqual(ids(s), ["cg_response"]);
  setTopRate(s, 14);
  assert.deepEqual(ids(s), ["cg_large", "cg_response"]);
  s.cg = 13.5;                                        // below the new top rate, more than 5.75 points up
  assert.deepEqual(ids(s), ["cg_large", "cg_response"]);
  s.cg = 14;
  assert.deepEqual(ids(s), ["cg_inert", "cg_large", "cg_response"]);
  // a flat schedule below current law's rate: no filer's gains rate moves
  const flat = stateFromSpec(meta.presets[0], meta);
  for (const v of flat.vintages) for (const k of Object.keys(v.brackets)) v.brackets[k] = [[0, 5]];
  flat.cg = 9;
  assert.deepEqual(ids(flat), ["cg_inert", "cut"]);
  flat.cg = 6;
  assert.deepEqual(ids(flat), ["cg_inert", "cut"]);
  flat.cg = 4;
  assert.deepEqual(ids(flat), ["cg_cut", "cut"]);
  // the text names each scenario's realization response
  s.cg = 9; setTopRate(s, 13);
  const text = warnings(s, meta).find((w) => w.id === "cg_response").text;
  for (const k of ["low", "mid", "high"]) assert.ok(text.includes(`${meta.scenarios[k].cg_beta} percent`), k);
  assert.ok(text.includes(`only ${meta.top_tail.records_1m} records`));
  assert.ok(!/%/.test(warnings(s, meta).map((w) => w.text).join(" ")));
});

test("the capital gains rate's share of a plan that changes both", () => {
  const preset = (name) => meta.presets.find((p) => p.name === name);
  const parsedOf = (spec) => parseSpec(spec, meta);
  // no gains change, or nothing but the gains change: no split
  assert.equal(systemsWithoutGainsRate(parsedOf(preset("top_rate_14")), meta), null);
  assert.equal(systemsWithoutGainsRate(parsedOf(preset("cg_9")), meta), null);
  // through the editor, which writes out current law's brackets
  const s = stateFromSpec(preset("cg_9"), meta);
  assert.equal(systemsWithoutGainsRate(parsedOf(specFromState(s, meta)), meta), null);
  // a top rate of 14 percent and a 9 percent gains rate: the rest is the top rate alone
  setTopRate(s, 14);
  const both = specFromState(s, meta);
  const rest = systemsWithoutGainsRate(parsedOf(both), meta);
  assert.ok(rest);
  s.cg = null;
  const alone = systemsOf(specFromState(s, meta));
  sameSystems(rest, alone, "without the gains rate");
  for (const y of meta.years) assert.equal(rest[y].reform.capital_gains_rate, meta.current_law[String(y)].capital_gains_rate);
  // a standard deduction or exemption change also counts
  const d = stateFromSpec(preset("cg_ordinary"), meta);
  d.pe = 1500;
  assert.ok(systemsWithoutGainsRate(parsedOf(specFromState(d, meta)), meta));
});

test("a bracket period after the last scored year, with a capital gains rate", () => {
  // parseSpec accepts a later period in any year after the one before it, as
  // income_tax_spec does, and the editor keeps it. The capital gains
  // warnings once looked up current law's gains rate by that year, which
  // meta.current_law does not have, and threw, so the page never drew the
  // results for such a link.
  const cl = meta.current_law_vintages;
  const last = meta.years[meta.years.length - 1];
  for (const late of [last, last + 1, last + 2, 2099]) {
    for (const cg of [undefined, 9, "ordinary"]) {
      const it = { bracket_vintages: [{ from: 2027, brackets: cl[0].brackets }, { from: late, brackets: cl[1].brackets }] };
      if (cg !== undefined) it.capital_gains_rate = cg;
      const spec = { name: "late", first_year: 2027, income_tax: it };
      systemsOf(spec);
      const s = stateFromSpec(spec, meta);
      assert.equal(activeVintages(s).at(-1).from, late);
      const ids = warnings(s, meta).map((w) => w.id);
      assert.deepEqual(ids, cg === undefined ? [] : ["cg_response"], `${late} ${cg}`);
      // and after picking a gains rate in the editor
      s.cg = 5;
      assert.deepEqual(warnings(s, meta).map((w) => w.id), ["cg_cut"], `${late} ${cg} then 5`);
    }
  }
});

test("a rate that falls back below the capital gains rate says how the cap is read", () => {
  const ids = (s) => warnings(s, meta).map((w) => w.id).sort();
  const withRows = (rows) => {
    const s = stateFromSpec(meta.presets[0], meta);
    for (const v of s.vintages) for (const k of Object.keys(v.brackets)) v.brackets[k] = rows.map((r) => [...r]);
    return s;
  };
  // 10 percent reaches current law's 7.25 percent, then 5 percent is below it
  const s = withRows([[0, 1], [10000, 10], [20000, 5], [40000, 12]]);
  assert.deepEqual(ids(s), ["cut", "falling", "falling_cg"]);
  s.cg = 6;                                           // 10 reaches it, 5 is below
  assert.deepEqual(ids(s), ["cg_cut", "cut", "falling", "falling_cg"]);
  s.cg = 5;                                           // nothing below 5 after 10
  assert.deepEqual(ids(s), ["cg_cut", "cut", "falling"]);
  s.cg = 11;                                          // only 12 reaches it, the last bracket
  assert.deepEqual(ids(s), ["cg_response", "cut", "falling"]);
  s.cg = "ordinary";                                  // no alternative tax, nothing to read
  assert.deepEqual(ids(s), ["cg_response", "cut", "falling"]);
  // a rate that falls but stays above the gains rate: the readings agree
  assert.deepEqual(ids(withRows([[0, 1], [10000, 13], [20000, 12]])), ["cut", "falling"]);
  // one status is enough, when the schedules are not linked
  const u = stateFromSpec(meta.presets[0], meta);
  u.linked = false;
  const joint = u.vintages[0].brackets.married_filing_jointly;
  joint[joint.length - 1][1] = 7;                     // 13 percent from $1M becomes 7
  assert.deepEqual(ids(u), ["cut", "falling", "falling_cg"]);
  const text = warnings(u, meta).find((w) => w.id === "falling_cg").text;
  assert.ok(text.includes("first bracket whose rate reaches the gains rate"));
  assert.ok(!/%|!/.test(text));
});
