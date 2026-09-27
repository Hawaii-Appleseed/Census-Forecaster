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
  removeBracket, editorRows, warnings, encodeSpec, decodeSpec, isLinked,
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
