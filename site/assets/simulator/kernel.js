// Hawaiʻi income tax simulator: the scoring kernel.
//
// Scores user-defined income tax systems on the Act 24 population, in the
// browser. It mirrors the Python model (tax_modeler: TaxCalculator.
// unit_liabilities, apply_behavioral_response, generate_quintile_report)
// operation for operation, and tests/simulator/kernel.test.mjs checks it
// against that model's own results (golden fixtures written by
// scripts/build_simulator_population.py). Change the model, and those tests
// fail until this file follows. See TAX_SIMULATOR_SCOPE.md.
//
// A "system" is plain data (tax_modeler.simulator.systems.system_to_json):
//   { credit_year, personal_exemption,
//     capital_gains_rate: 7.25,       // HRS §235-51(f) alternative rate, %, or "ordinary"
//     brackets: { Single_Married_Separate: [[floor, rate %], ...], ... },
//     standard_deduction: { Single_Married_Separate: 8000, ... } }

export const STATUSES = ["single", "married_filing_jointly", "head_of_household",
  "married_filing_separately"];
// Frame filing-status code -> the bracket/deduction schedule it uses.
export const CSV_STATUS = ["Single_Married_Separate", "Joint_Surviving_Spouse",
  "Head_of_Household", "Single_Married_Separate"];
export const SPEC_STATUS = { single: "Single_Married_Separate",
  head_of_household: "Head_of_Household", married_filing_jointly: "Joint_Surviving_Spouse" };
const MFJ = 1, HOH = 2;

// income_tax.capital_gains_rate / a system's capital_gains_rate: no
// alternative tax, gains taxed at the bracket rates
export const CG_ORDINARY = "ordinary";
const MIGRATION_FULL_TIER = 1_000_000;
const PTE_RATE = 0.09;
const isNum = (v) => typeof v === "number" && Number.isFinite(v);

// ---------------------------------------------------------------------------
// population
// ---------------------------------------------------------------------------

const VIEWS = { f8: Float64Array, u4: Uint32Array, u1: Uint8Array };
// population.json's web_format_version this kernel reads
// (tax_modeler.simulator.web.WEB_FORMAT_VERSION; 2 added the gains anchor)
export const WEB_FORMAT_VERSION = 2;

/** Typed-array views over the decompressed population.bin. */
export function decodePopulation(meta, buffer) {
  if (meta.web_format_version !== WEB_FORMAT_VERSION) {
    throw new Error(`population format ${meta.web_format_version}; this page reads format ${WEB_FORMAT_VERSION}`);
  }
  const arrays = {};
  for (const a of meta.arrays) {
    arrays[a.name] = new VIEWS[a.dtype](buffer, a.offset, a.length);
  }
  return { meta, arrays, n: arrays.fs.length, nHouseholds: arrays.hh_weight.length };
}

/** Each unit's DOTAX capital-gains class code for one (year, scenario):
 * MID's codes with LOW/HIGH's differing units swapped in
 * (tax_modeler.simulator.gains.gains_classes). */
export function gainsClasses(pop, year, scenario = "mid") {
  const A = pop.arrays;
  let cls = A[`cgcls_${year}`];
  if (!cls) throw new Error(`population has no capital-gains classes for ${year}`);
  if (scenario !== "mid") {
    const idx = A[`cgcls_${scenario}_${year}_idx`], val = A[`cgcls_${scenario}_${year}_val`];
    if (!idx || !val) throw new Error(`population has no ${scenario} capital-gains classes for ${year}`);
    cls = Uint8Array.from(cls);
    for (let k = 0; k < idx.length; k++) cls[idx[k]] = val[k];
  }
  return cls;
}

/** Income, itemized deduction, weight and capital-gains share for one
 * (year, scenario): MID's arrays with LOW/HIGH's differing units swapped in
 * (tax_modeler.simulator.population.unit_arrays). The share is the
 * DOTAX-anchored one, min(1, model share x k[class]), with the model share
 * after the scenario's tail overrides; there is no fallback to the model's
 * own base. */
export function unitArrays(pop, year, scenario = "mid") {
  const A = pop.arrays;
  let agi = A[`agi_${year}`], item = A[`item_${year}`], weight = A.weight, cgModel = A.cg;
  const k = pop.meta.cg_anchor?.k?.[scenario]?.[String(year)];
  if (!k) throw new Error(`population has no capital-gains scale factors for ${scenario} ${year}`);
  const cls = gainsClasses(pop, year, scenario);
  if (scenario !== "mid") {
    agi = Float64Array.from(agi); item = Float64Array.from(item);
    weight = Float64Array.from(weight); cgModel = Float64Array.from(cgModel);
    const idx = A[`ovr_${scenario}_${year}_idx`];
    const oa = A[`ovr_${scenario}_${year}_agi`], oi = A[`ovr_${scenario}_${year}_item`];
    for (let j = 0; j < idx.length; j++) { agi[idx[j]] = oa[j]; item[idx[j]] = oi[j]; }
    const t = A[`tail_${scenario}_idx`];
    const tw = A[`tail_${scenario}_weight`], tc = A[`tail_${scenario}_cg`];
    for (let j = 0; j < t.length; j++) { weight[t[j]] = tw[j]; cgModel[t[j]] = tc[j]; }
  }
  const cg = new Float64Array(pop.n);
  for (let i = 0; i < pop.n; i++) cg[i] = Math.min(1, cgModel[i] * k[cls[i]]);
  return { agi, item, weight, cg };
}

// ---------------------------------------------------------------------------
// tax on one unit
// ---------------------------------------------------------------------------

/** The floor of the first bracket whose rate reaches the alternative rate
 * `cap` (decimal), or Infinity when none does (cg_alternative.cap_floor). */
function capFloor(floors, rates, cap) {
  for (let i = 0; i < rates.length; i++) if (rates[i] >= cap - 1e-12) return floors[i];
  return Infinity;
}

/** Brackets as floors, decimal rates and the tax owed at each floor
 * (TaxCalculator._bracket_schedule), per frame status code, and the
 * capital-gains alternative rate (decimal; null: gains taxed as ordinary
 * income) with each schedule's floor for it. */
export function prepareSystem(system, foodExcise) {
  const r = system.capital_gains_rate;
  if (r !== CG_ORDINARY && !isNum(r)) {
    throw new Error(`system ${system.name}: capital_gains_rate must be a percent or "${CG_ORDINARY}", got ${JSON.stringify(r)}`);
  }
  const cgRate = r === CG_ORDINARY ? null : r / 100;   // TaxSystemConfig.cg_alt_rate
  const byStatus = CSV_STATUS.map((csv) => {
    const rows = system.brackets[csv];
    const floors = rows.map((row) => row[0]);
    const rates = rows.map((row) => row[1] / 100);
    const cum = [0];
    // np.cumsum(widths * rates[:-1]): sequential, same order.
    let acc = 0;
    for (let i = 1; i < floors.length; i++) {
      acc += (floors[i] - floors[i - 1]) * rates[i - 1];
      cum.push(acc);
    }
    return { floors, rates, cum, sd: system.standard_deduction[csv],
      capFloor: cgRate === null ? Infinity : capFloor(floors, rates, cgRate) };
  });
  const fe = foodExcise[String(system.credit_year)];
  if (!fe) throw new Error(`no food/excise table for credit year ${system.credit_year}`);
  return { byStatus, pe: system.personal_exemption, fe, creditYear: system.credit_year, cgRate };
}

// searchsorted(floors, t, side="right") - 1, clipped to [0, n-1]
function bracketIndex(floors, t) {
  let lo = 0, hi = floors.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (floors[mid] <= t) lo = mid + 1; else hi = mid;
  }
  const i = lo - 1;
  return i < 0 ? 0 : i;
}

function bracketTax(s, t) {
  const i = bracketIndex(s.floors, t);
  return s.cum[i] + (t - s.floors[i]) * s.rates[i];
}

function marginalRate(s, t) {
  return t <= 0 ? 0 : s.rates[bracketIndex(s.floors, t)];
}

/** Statutory exemptions: filer, spouse on a joint return, dependents. */
export function exemptionCount(fs, deps) {
  return 1 + (fs === MFJ ? 1 : 0) + deps;
}

// credits.hi_food_excise: amount per exemption from the table, 0 past its last ceiling
function foodExcisePerExemption(agi, table, factor) {
  let k = 0;
  while (k < table.length && table[k][0] * factor <= agi) k++;
  return k < table.length ? table[k][1] : 0;
}

/** HawaiiTaxCredits.calculate_total_credits, credit_scenario None. */
export function credits(fe, agi, fs, deps, taxBefore) {
  // food/excise (refundable): joint table for every status but single
  const nEx = 1 + deps + (fs === MFJ ? 1 : 0);
  const table = fs === 0 ? fe.single : fe.joint;
  const food = foodExcisePerExemption(agi > 0 ? agi : 0, table, fe.income_threshold_factor)
    * Math.max(nEx, 0) * fe.amount_pct;
  // renewable energy (expected value, nonrefundable)
  let reec = 0;
  if (!(agi < 50000 || agi > 300000)) {
    const claimRate = 0.02;
    reec = agi < 100000 ? claimRate * 1500 : agi < 200000 ? claimRate * 2500 : claimRate * 3500;
  }
  // dependent care, current law (nonrefundable)
  let cdcc = 0;
  if (deps !== 0 && !(agi > 100000)) {
    const cap = deps === 1 ? 3000 : 6000;
    let pct;
    if (agi <= 25000) pct = 0.25;
    else if (agi <= 43000) pct = 0.25 - 0.05 * (agi - 25000) / 18000;
    else pct = 0.20;
    cdcc = Math.min(12000 * deps, cap) * pct;
  }
  // low-income renters (expected value, refundable)
  const threshold = fs === MFJ ? 40000 : fs === HOH ? 35000 : fs === 3 ? 20000 : 30000;
  let renters = 0;
  if (!(agi > threshold)) {
    const share = 0.30;
    renters = agi < 15000 ? share * 150 : agi < 25000 ? share * 100 : share * 50;
  }
  const refundable = food + renters;
  let nonref = reec + cdcc;
  nonref = Math.min(nonref, taxBefore);
  return refundable + nonref;
}

/** Tax on one unit (TaxCalculator.unit_liabilities, one row). */
export function unitTax(sys, fs, deps, agi, itemized, cgShare) {
  const s = sys.byStatus[fs];
  const ded = Math.max(s.sd, itemized);
  const exemptions = exemptionCount(fs, deps) * sys.pe;
  const taxableFull = Math.max(0, agi - ded - exemptions);
  let before = bracketTax(s, taxableFull);
  // HRS §235-51(f) alternative tax (cg_alternative.alternative_tax): the
  // bracket tax on the greater of taxable income less the gains and the
  // income below the first bracket that reaches the rate, plus the rate on
  // the rest, if lower. With no gains it is the bracket tax, so it is skipped.
  const cgIncome = Math.max(0, agi * cgShare);
  if (sys.cgRate !== null && cgIncome > 0) {
    const ncg = Math.min(cgIncome, taxableFull);
    const base = Math.max(taxableFull - ncg, Math.min(taxableFull, s.capFloor));
    const alt = bracketTax(s, base) + sys.cgRate * (taxableFull - base);
    if (alt < before) before = alt;
  }
  const cr = credits(sys.fe, agi, fs, deps, before);
  return { before, credits: cr, net: before - cr };
}

/** Net tax for every unit (a Float64Array). */
export function unitNetTaxes(pop, sys, agi, item, cg) {
  const { fs, deps } = pop.arrays;
  const out = new Float64Array(pop.n);
  for (let i = 0; i < pop.n; i++) out[i] = unitTax(sys, fs[i], deps[i], agi[i], item[i], cg[i]).net;
  return out;
}

function weightedMillions(values, weight) {
  let acc = 0;
  for (let i = 0; i < values.length; i++) acc += values[i] * weight[i];
  return acc / 1e6;
}

// ---------------------------------------------------------------------------
// behavioral response (scenarios.behavioral_response)
// ---------------------------------------------------------------------------

/** Per status: change in the top statutory rate (points), the reform's and
 * the baseline's top-bracket floors (top_rate_changes). */
export function topRateChanges(baseSys, reformSys) {
  return CSV_STATUS.map((_, fs) => {
    const b = baseSys.byStatus[fs], r = reformSys.byStatus[fs];
    return { pp: 100 * (r.rates[r.rates.length - 1] - b.rates[b.rates.length - 1]),
      floor: r.floors[r.floors.length - 1], baseFloor: b.floors[b.floors.length - 1] };
  });
}

/** The top-rate change in effect for migration in `year`: each year's
 * increment phases in from the year it takes effect
 * (behavioral_response._phased_rate_change). `path` is [{ year, changes }]
 * for every scored year, in order. */
function phasedRateChange(path, code, year, phaseInYears) {
  let total = 0, prev = 0;
  for (const { year: y, changes } of path) {
    if (y > year) break;
    const pp = changes[code].pp;
    total += (pp - prev) * Math.min(1, (year - y + 1) / phaseInYears);
    prev = pp;
  }
  return total;
}

/** ETI, migration, then capital-gains realization; returns adjusted
 * incomes, weights and gains shares. */
export function behave(pop, baseSys, reformSys, u, params, year, path) {
  const { fs, deps } = pop.arrays;
  let agi = u.agi, weight = u.weight;
  if (params.eti > 0) {
    agi = Float64Array.from(u.agi);
    for (let i = 0; i < pop.n; i++) {
      const code = fs[i];
      const b = baseSys.byStatus[code], r = reformSys.byStatus[code];
      const ex = exemptionCount(code, deps[i]);
      const tb = Math.max(0, u.agi[i] - Math.max(b.sd, u.item[i]) - ex * baseSys.pe);
      const tr = Math.max(0, u.agi[i] - Math.max(r.sd, u.item[i]) - ex * reformSys.pe);
      const mb = marginalRate(b, tb), mr = marginalRate(r, tr);
      if (mr > mb && mb < 1 && mr < 1) agi[i] = u.agi[i] * ((1 - mr) / (1 - mb)) ** params.eti;
    }
  }
  if (params.migration_elast > 0) {
    const changes = topRateChanges(baseSys, reformSys);
    const factor = new Float64Array(pop.n).fill(1);
    let moved = false;
    changes.forEach(({ floor, baseFloor }, code) => {
      const pp = phasedRateChange(path, code, year, params.migration_phase_in_years);
      if (pp <= 0) return;
      // never more than all of a group's weight
      const loss = params.migration_elast * pp;
      const full = Math.max(MIGRATION_FULL_TIER, floor), lower = Math.max(floor, baseFloor);
      for (let i = 0; i < pop.n; i++) {
        if (fs[i] !== code) continue;
        if (agi[i] >= full) { factor[i] = Math.max(0, 1 - loss); moved = true; }
        else if (lower < MIGRATION_FULL_TIER && agi[i] >= lower && agi[i] < MIGRATION_FULL_TIER) {
          factor[i] = Math.max(0, 1 - loss * 0.5); moved = true;
        }
      }
    });
    if (moved) {
      weight = Float64Array.from(u.weight);
      for (let i = 0; i < pop.n; i++) if (factor[i] !== 1) weight[i] = weight[i] * factor[i];
    }
  }
  // Realization (apply_realization_response): at a higher alternative rate
  // fewer gains are realized. The rate on gains is min(m, rate) (m with no
  // alternative tax), m the reform's bracket rate at taxable income before
  // any response, on both sides; gains after ETI shrink by exp(-cg_beta x
  // the rise). Only units with a rise and gains change: elsewhere
  // agi x share / agi can differ from the share by a rounding error.
  let cg = u.cg;
  if (params.cg_beta > 0 && baseSys.cgRate !== reformSys.cgRate) {
    const c0 = baseSys.cgRate, c1 = reformSys.cgRate;
    for (let i = 0; i < pop.n; i++) {
      if (!(u.cg[i] > 0)) continue;
      const code = fs[i], r = reformSys.byStatus[code];
      const tr = Math.max(0, u.agi[i] - Math.max(r.sd, u.item[i]) - exemptionCount(code, deps[i]) * reformSys.pe);
      const m = marginalRate(r, tr);
      const d = Math.max(0, (c1 === null ? m : Math.min(m, c1)) - (c0 === null ? m : Math.min(m, c0)));
      if (!(d > 0)) continue;
      if (agi === u.agi) agi = Float64Array.from(u.agi);
      if (cg === u.cg) cg = Float64Array.from(u.cg);
      const y = agi[i], g = y * u.cg[i];
      const kept = g * Math.exp(-params.cg_beta * d);
      agi[i] = y - (g - kept);
      cg[i] = kept / agi[i];
    }
  }
  if (params.pte_capture > 0) {
    throw new Error("PTE election response is not modeled by the simulator (capture is 0)");
  }
  return { agi, item: u.item, weight, cg };
}

// ---------------------------------------------------------------------------
// scoring
// ---------------------------------------------------------------------------

/** Revenue for one (scenario, year): baseline, reform, static change, and the
 * change after the behavioral response ($M). After the response only the
 * reform is scored again: the baseline is current law with nobody
 * responding, so a filer who moves away costs their whole tax
 * (behavioral_response.score_with_response). */
export function scoreYear(pop, year, scenario, baseSys, reformSys, path) {
  const params = pop.meta.scenarios[scenario];
  const u = unitArrays(pop, year, scenario);
  const base = weightedMillions(unitNetTaxes(pop, baseSys, u.agi, u.item, u.cg), u.weight);
  const reform = weightedMillions(unitNetTaxes(pop, reformSys, u.agi, u.item, u.cg), u.weight);
  const adj = behave(pop, baseSys, reformSys, u, params, year, path);
  const reform2 = weightedMillions(unitNetTaxes(pop, reformSys, adj.agi, adj.item, adj.cg), adj.weight);
  let filers1m = 0;
  for (let i = 0; i < pop.n; i++) if (adj.agi[i] >= 1_000_000) filers1m += adj.weight[i];
  return { scenario, tax_year: year, "baseline_$M": base, "reform_$M": reform,
    "static_$M": reform - base, "behavioral_$M": reform2 - base,
    filers_1m_post_response: filers1m };
}

// A change within half a cent of zero is no change (score.NO_CHANGE_TOLERANCE).
const NO_CHANGE_TOLERANCE = 0.005;

function sharesOf(change, w, idx) {
  let more = 0, less = 0, tot = 0;
  for (const i of idx) {
    tot += w(i);
    if (change[i] > NO_CHANGE_TOLERANCE) more += w(i); else if (change[i] < -NO_CHANGE_TOLERANCE) less += w(i);
  }
  if (tot <= 0) return [0, 0, 0];
  const m = more / tot * 100, l = less / tot * 100;
  return [m, l, 100 - m - l];
}

/** MID distribution tables for one year, static
 * (generate_quintile_report with no credit changes). */
export function distribution(pop, year, baseSys, reformSys) {
  const A = pop.arrays, meta = pop.meta;
  const u = unitArrays(pop, year, "mid");
  const baseTax = unitNetTaxes(pop, baseSys, u.agi, u.item, u.cg);
  const reformTax = unitNetTaxes(pop, reformSys, u.agi, u.item, u.cg);
  // per-year arrays when they differ between years, else one shared array
  const H = pop.nHouseholds, hh = A.hh;
  const quint = A[`quint_${year}`] ?? A.quint, hhfw = A[`hhfw_${year}`] ?? A.hhfw;
  // households: summed tax of their units (weight > 0.01)
  // (the change is summed unit by unit, as pandas does, not taken as a
  // difference of the sums: a near-zero household must not flip sign)
  const hBase = new Float64Array(H), hReform = new Float64Array(H), hChange = new Float64Array(H);
  const hIn = new Uint8Array(H);
  for (let i = 0; i < pop.n; i++) {
    if (!(u.weight[i] > 0.01)) continue;
    const h = hh[i];
    hBase[h] += baseTax[i]; hReform[h] += reformTax[i]; hChange[h] += reformTax[i] - baseTax[i];
    hIn[h] = 1;
  }
  const quintile = [];
  meta.quintile_labels.forEach((label, q) => {
    const members = [];
    for (let h = 0; h < H; h++) if (hIn[h] && quint[h] === q) members.push(h);
    if (!members.length) return;
    let hw = 0, b = 0, r = 0, c = 0;
    for (const h of members) {
      hw += A.hh_weight[h];
      b += hBase[h] * hhfw[h]; r += hReform[h] * hhfw[h]; c += hChange[h] * hhfw[h];
    }
    const [more, less, same] = sharesOf(hChange, (h) => A.hh_weight[h], members);
    quintile.push({ group: label, household_count: hw,
      avg_per_hh_baseline_tax: b / hw, avg_per_hh_reform_tax: r / hw, avg_per_hh_bracket_change: c / hw,
      "total_baseline_$M": b / 1e6, "total_reform_$M": r / 1e6, "total_bracket_$M": c / 1e6,
      pct_pay_more: more, pct_pay_less: less, pct_no_change: same });
  });
  // AGI classes: filer level, right-open bins
  const breaks = meta.agi_class_breaks;
  const change = new Float64Array(pop.n);
  for (let i = 0; i < pop.n; i++) change[i] = reformTax[i] - baseTax[i];
  const income_class = [];
  meta.agi_class_labels.forEach((label, k) => {
    const members = [];
    for (let i = 0; i < pop.n; i++) {
      if (!(u.weight[i] > 0.01)) continue;
      let cls = 0;
      while (cls < breaks.length && u.agi[i] >= breaks[cls]) cls++;
      if (cls === k) members.push(i);
    }
    if (!members.length) return;
    let w = 0, b = 0, r = 0, c = 0;
    for (const i of members) {
      w += u.weight[i]; b += baseTax[i] * u.weight[i]; r += reformTax[i] * u.weight[i];
      c += change[i] * u.weight[i];
    }
    const [more, less, same] = sharesOf(change, (i) => u.weight[i], members);
    income_class.push({ group: label, filer_count: w,
      avg_baseline_tax: b / w, avg_reform_tax: r / w, avg_bracket_change: c / w,
      "total_baseline_$M": b / 1e6, "total_reform_$M": r / 1e6, "total_bracket_$M": c / 1e6,
      pct_pay_more: more, pct_pay_less: less, pct_no_change: same });
  });
  return { quintile, income_class };
}

/** Score per-year systems: { [year]: { baseline, reform } } (plain systems,
 * for every population year: migration phases in along the whole path).
 * Returns { revenue: [...], distribution: { [year]: {...} } }. */
export function score(pop, systemsByYear, { scenarios = ["low", "mid", "high"],
  years = pop.meta.years, distributionYears = [] } = {}) {
  const fe = pop.meta.food_excise;
  const prepared = {};
  for (const y of pop.meta.years) {
    prepared[y] = { base: prepareSystem(systemsByYear[y].baseline, fe),
      reform: prepareSystem(systemsByYear[y].reform, fe) };
  }
  const path = pop.meta.years.map((y) => ({ year: y, changes: topRateChanges(prepared[y].base, prepared[y].reform) }));
  const revenue = [];
  for (const s of scenarios) {
    for (const y of years) revenue.push(scoreYear(pop, y, s, prepared[y].base, prepared[y].reform, path));
  }
  const dist = {};
  for (const y of distributionYears) dist[y] = distribution(pop, y, prepared[y].base, prepared[y].reform);
  return { revenue, distribution: dist };
}

// ---------------------------------------------------------------------------
// specs (tax_modeler.reform.income_tax_spec)
// ---------------------------------------------------------------------------

const NAME_RE = /^[a-z0-9][a-z0-9_-]{0,63}$/;
const TOP_KEYS = new Set(["name", "label", "first_year", "income_tax", "behavior", "baseline",
  "model_version", "metadata"]);
const IT_KEYS = new Set(["brackets", "bracket_vintages", "standard_deduction", "personal_exemption",
  "capital_gains_rate"]);
const BEHAVIORS = ["static", "low", "mid", "high"];
const isMap = (v) => v !== null && typeof v === "object" && !Array.isArray(v);

class SpecError extends Error {}

// Python's repr() of a JSON value, for error texts that match the model's
// word for word (JSON cannot tell 9 from 9.0; both print as 9).
function pyRepr(v) {
  if (v === null || v === undefined) return "None";
  if (v === true) return "True";
  if (v === false) return "False";
  if (typeof v === "number") return Number.isNaN(v) ? "nan" : v === Infinity ? "inf" : v === -Infinity ? "-inf" : String(v);
  if (typeof v === "string") {
    const q = v.includes("'") && !v.includes('"') ? '"' : "'";
    const esc = v.replace(/\\/g, "\\\\").replace(/\n/g, "\\n").replace(/\r/g, "\\r").replace(/\t/g, "\\t");
    return q + (q === "'" ? esc.replace(/'/g, "\\'") : esc) + q;
  }
  if (Array.isArray(v)) return `[${v.map(pyRepr).join(", ")}]`;
  return `{${Object.entries(v).map(([k, x]) => `${pyRepr(k)}: ${pyRepr(x)}`).join(", ")}}`;
}

/** income_tax.capital_gains_rate (income_tax_spec._resolve_cg_rate): null
 * for current law, a percent in [0, 100), or CG_ORDINARY. A present null is
 * rejected. The error texts are the model's. */
function cgRate(raw) {
  const where = "income_tax.capital_gains_rate";
  if (typeof raw === "string") {
    if (raw === "current_law") return null;
    if (raw === CG_ORDINARY) return CG_ORDINARY;
    throw new SpecError(`${where}: 'current_law', 'ordinary' or a rate in percent, got ${pyRepr(raw)}`);
  }
  if (typeof raw !== "number") throw new SpecError(`${where}: expected a number, got ${pyRepr(raw)}`);
  if (!Number.isFinite(raw) || raw < 0 || raw >= 100) throw new SpecError(`${where}: ${pyRepr(raw)} is outside [0, 100)`);
  return raw;
}

function schedule(rows, where, maxBrackets, maxFloor) {
  if (!Array.isArray(rows) || !rows.length) throw new SpecError(`${where}: expected a non-empty list of [floor, rate] pairs`);
  if (rows.length > maxBrackets) throw new SpecError(`${where}: ${rows.length} brackets; at most ${maxBrackets}`);
  const out = [];
  rows.forEach((row, i) => {
    if (!Array.isArray(row) || row.length !== 2) throw new SpecError(`${where}[${i}]: expected [floor, rate]`);
    const [floor, rate] = row;
    if (!isNum(floor) || floor < 0 || floor > maxFloor) throw new SpecError(`${where}[${i}]: floor must be a number from 0 to ${maxFloor.toLocaleString("en-US")}`);
    if (!isNum(rate) || rate < 0 || rate >= 100) throw new SpecError(`${where}[${i}]: rate must be at least 0 and below 100`);
    if (i === 0 && floor !== 0) throw new SpecError(`${where}: the first bracket must start at 0`);
    if (i > 0 && floor <= out[i - 1][0]) throw new SpecError(`${where}[${i}]: floor must be above ${out[i - 1][0]}`);
    out.push([floor, rate]);
  });
  return out;
}

function bracketSet(raw, where, maxBrackets, maxFloor) {
  if (!isMap(raw)) throw new SpecError(`${where}: expected a mapping of filing status to schedule`);
  const own = Object.hasOwn;       // not `in`: that also finds "constructor", "toString", ...
  const keys = Object.keys(raw);
  for (const k of keys) if (!own(SPEC_STATUS, k)) throw new SpecError(`${where}: unknown filing status ${k}`);
  for (const k of Object.keys(SPEC_STATUS)) if (!own(raw, k)) throw new SpecError(`${where}: missing ${k}`);
  const explicit = {};
  for (const k of keys) if (!isMap(raw[k])) explicit[k] = schedule(raw[k], `${where}.${k}`, maxBrackets, maxFloor);
  const out = { ...explicit };
  for (const k of keys) {
    if (!isMap(raw[k])) continue;
    const v = raw[k];
    const vk = Object.keys(v).sort().join(",");
    if (vk !== "factor,scale") throw new SpecError(`${where}.${k}: a derived schedule is {scale, factor}`);
    if (typeof v.scale !== "string" || !own(explicit, v.scale)) throw new SpecError(`${where}.${k}: scale must name a status with its own schedule`);
    if (!isNum(v.factor) || v.factor <= 0) throw new SpecError(`${where}.${k}.factor must be positive`);
    // re-checked: a large factor can push a floor past the maximum
    out[k] = schedule(explicit[v.scale].map(([f, r]) => [f * v.factor, r]), `${where}.${k}`, maxBrackets, maxFloor);
  }
  return out;
}

/** Validate a spec; returns the resolved form or throws SpecError.
 * The same rules as IncomeTaxSpec.from_dict. */
export function parseSpec(data, meta) {
  const [fyLo, fyHi] = meta.first_year_range, maxB = meta.max_brackets, maxF = meta.max_floor;
  if (!isMap(data)) throw new SpecError("spec: expected a mapping");
  for (const k of Object.keys(data)) if (!TOP_KEYS.has(k)) throw new SpecError(`spec: unknown key ${k}`);
  if (typeof data.name !== "string" || !NAME_RE.test(data.name)) throw new SpecError("spec.name: lowercase letters, digits, _ or -, 1-64 characters");
  // A key that is present with the value null is not the same as a missing
  // key: the Python parser (dict.get with a default) only defaults missing ones.
  const has = (o, k) => Object.prototype.hasOwnProperty.call(o, k);
  const get = (o, k, dflt) => (has(o, k) ? o[k] : dflt);
  // characters counted as code points, as Python's len() does
  if (has(data, "label") && (typeof data.label !== "string" || [...data.label].length > 200)) throw new SpecError("spec.label: at most 200 characters");
  if (get(data, "baseline", "act24") !== "act24") throw new SpecError("spec.baseline: specs are scored against current law, act24");
  if (has(data, "model_version") && data.model_version !== null && typeof data.model_version !== "string") throw new SpecError("spec.model_version: a string");
  if (has(data, "metadata") && data.metadata !== null && !isMap(data.metadata)) throw new SpecError("spec.metadata: a mapping");
  const fy = data.first_year;
  if (!Number.isInteger(fy) || fy < fyLo || fy > fyHi) throw new SpecError(`spec.first_year: a tax year from ${fyLo} to ${fyHi}`);
  const it = get(data, "income_tax", {});
  if (!isMap(it)) throw new SpecError("spec.income_tax: expected a mapping");
  for (const k of Object.keys(it)) if (!IT_KEYS.has(k)) throw new SpecError(`spec.income_tax: unknown key ${k}`);
  const rawB = get(it, "brackets", "current_law"), rawV = it.bracket_vintages;   // null vintages = absent, as in Python
  if (rawV != null && rawB !== "current_law") throw new SpecError("spec.income_tax: give brackets or bracket_vintages, not both");
  let vintages = null;
  if (rawV != null) {
    if (!Array.isArray(rawV) || !rawV.length) throw new SpecError("income_tax.bracket_vintages: a non-empty list");
    vintages = [];
    rawV.forEach((v, i) => {
      if (!isMap(v) || Object.keys(v).sort().join(",") !== "brackets,from") throw new SpecError(`income_tax.bracket_vintages[${i}]: expected {from, brackets}`);
      if (!Number.isInteger(v.from)) throw new SpecError(`income_tax.bracket_vintages[${i}].from: a tax year`);
      if (i === 0 && v.from !== fy) throw new SpecError("income_tax.bracket_vintages: the first vintage must start at first_year");
      if (i > 0 && v.from <= vintages[i - 1].from) throw new SpecError(`income_tax.bracket_vintages[${i}].from must be after the previous vintage`);
      vintages.push({ from: v.from, brackets: bracketSet(v.brackets, `income_tax.bracket_vintages[${i}].brackets`, maxB, maxF) });
    });
  } else if (rawB !== "current_law") {
    vintages = [{ from: fy, brackets: bracketSet(rawB, "income_tax.brackets", maxB, maxF) }];
  }
  let sd = null;
  const rawSd = get(it, "standard_deduction", "current_law");
  if (rawSd !== "current_law") {
    if (!isMap(rawSd) || Object.keys(rawSd).sort().join(",") !== Object.keys(SPEC_STATUS).sort().join(",")) {
      throw new SpecError("income_tax.standard_deduction: current_law or an amount for each filing status");
    }
    sd = {};
    for (const [k, v] of Object.entries(rawSd)) {
      if (!isNum(v) || v < 0 || v > 1e7) throw new SpecError(`income_tax.standard_deduction.${k}: a number from 0 to 10,000,000`);
      sd[k] = v;
    }
  }
  let pe = null;
  const rawPe = get(it, "personal_exemption", "current_law");
  if (rawPe !== "current_law") {
    if (!isNum(rawPe) || rawPe < 0 || rawPe > 1e6) throw new SpecError("income_tax.personal_exemption: a number from 0 to 1,000,000");
    pe = rawPe;
  }
  const cg = cgRate(get(it, "capital_gains_rate", "current_law"));
  const behavior = get(data, "behavior", BEHAVIORS);
  if (!Array.isArray(behavior) || !behavior.length || behavior.some((b) => !BEHAVIORS.includes(b))) {
    throw new SpecError(`spec.behavior: a non-empty subset of ${BEHAVIORS.join(", ")}`);
  }
  return { name: data.name, label: get(data, "label", ""), first_year: fy, vintages, sd, pe, cg,
    behavior: BEHAVIORS.filter((b) => behavior.includes(b)) };
}

/** Per-year { baseline, reform } systems for a parsed spec
 * (IncomeTaxSpec.system_for / baseline_for). */
export function resolveSpec(parsed, meta) {
  const out = {};
  for (const y of meta.years) {
    const base = meta.current_law[String(y)];
    const changes = parsed.vintages || parsed.sd || parsed.pe !== null || parsed.cg !== null;
    if (y < parsed.first_year || !changes) { out[y] = { baseline: base, reform: base }; continue; }
    const reform = { ...base, name: `${parsed.name}_${y}`,
      brackets: { ...base.brackets }, standard_deduction: { ...base.standard_deduction } };
    let vintage = null;
    for (const v of parsed.vintages || []) if (v.from <= y) vintage = v.brackets;
    if (vintage) {
      for (const [k, csv] of Object.entries(SPEC_STATUS)) reform.brackets[csv] = vintage[k].map((r) => [r[0], r[1]]);
    }
    if (parsed.sd) for (const [k, csv] of Object.entries(SPEC_STATUS)) reform.standard_deduction[csv] = parsed.sd[k];
    if (parsed.pe !== null) reform.personal_exemption = parsed.pe;
    if (parsed.cg !== null) reform.capital_gains_rate = parsed.cg;
    out[y] = { baseline: base, reform };
  }
  return out;
}

/** Tax on one household under a plain system (the page's household
 * calculator). `cgShare`: net long-term capital gains as a share of `agi`
 * (0 to 1), as the population stores them. */
export function householdTax(system, foodExcise, { fs, agi, dependents = 0, itemized = 0, cgShare = 0 }) {
  const sys = prepareSystem(system, foodExcise);
  return unitTax(sys, typeof fs === "number" ? fs : STATUSES.indexOf(fs), dependents, agi, itemized, cgShare);
}

export { SpecError };
