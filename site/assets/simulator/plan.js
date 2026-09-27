// Tax simulator page state: the editor's plan <-> a spec
// (tax_modeler.reform.income_tax_spec's format), quick edits, guardrail
// warnings and share links. No DOM here, so tests/simulator/plan.test.mjs
// can check it in Node.

export const STATUS_KEYS = ["single", "head_of_household", "married_filing_jointly"];
export const LINK = { head_of_household: 1.5, married_filing_jointly: 2 };
const nf1 = new Intl.NumberFormat("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 });

export const clone = (x) => JSON.parse(JSON.stringify(x));

/** {status: rows | {scale, factor}} -> {status: rows} */
export function expand(brackets) {
  const out = {};
  for (const k of STATUS_KEYS) {
    const v = brackets[k];
    if (Array.isArray(v)) out[k] = v.map((r) => [r[0], r[1]]);
  }
  for (const k of STATUS_KEYS) {
    const v = brackets[k];
    if (!Array.isArray(v)) out[k] = out[v.scale].map((r) => [r[0] * v.factor, r[1]]);
  }
  return out;
}

/** Head of household exactly 1.5 times single and joint 2 times? */
export function isLinked(b) {
  return ["head_of_household", "married_filing_jointly"].every((k) =>
    b[k].length === b.single.length &&
    b[k].every((r, i) => r[0] === b.single[i][0] * LINK[k] && r[1] === b.single[i][1]));
}

/** Editor state from a spec (a preset or a shared link). The editor holds
 * current law's bracket periods (vintages), split again wherever a later
 * spec vintage starts; a spec vintage fills every editor period from its
 * year until the next vintage, and the first also fills periods before it
 * (those precede the first tax year, so they are not scored, but the editor
 * shows the plan). */
export function stateFromSpec(spec, meta) {
  const it = spec.income_tax || {};
  const years = new Set(meta.current_law_vintages.map((v) => v.from));
  if (Array.isArray(it.bracket_vintages)) it.bracket_vintages.slice(1).forEach((v) => years.add(v.from));
  const vintages = [...years].sort((a, b) => a - b)
    .map((y) => ({ from: y, brackets: clone(currentLawFor(meta, y)) }));
  if (Array.isArray(it.bracket_vintages)) {
    it.bracket_vintages.forEach((v, i) => {
      const next = it.bracket_vintages[i + 1];
      for (const ev of vintages) {
        const inRange = ev.from >= v.from && (!next || ev.from < next.from);
        if (inRange || (i === 0 && ev.from < v.from)) ev.brackets = expand(v.brackets);
      }
    });
  } else if (it.brackets && it.brackets !== "current_law") {
    const b = expand(it.brackets);
    for (const ev of vintages) ev.brackets = clone(b);
  }
  const sd = it.standard_deduction && it.standard_deduction !== "current_law" ? { ...it.standard_deduction } : null;
  const pe = typeof it.personal_exemption === "number" ? it.personal_exemption : null;
  return {
    preset: spec.name, label: spec.label || "", firstYear: spec.first_year,
    vintages, linked: vintages.every((v) => isLinked(v.brackets)),
    sd, sdLinked: !sd || (sd.head_of_household === sd.single * 1.5 && sd.married_filing_jointly === sd.single * 2),
    pe, activeVintage: 0, activeStatus: "single",
    distYear: spec.first_year,
    household: { fs: "married_filing_jointly", agi: 120000, dependents: 2, itemized: 0 },
  };
}

/** The editor periods in force from the first tax year on:
 * [{index, from, brackets}], the first starting at the first tax year. */
export function activeVintages(s) {
  const out = [];
  s.vintages.forEach((v, i) => {
    const next = s.vintages[i + 1];
    const start = Math.max(v.from, s.firstYear);
    if (!next || start < next.from) out.push({ index: i, from: start, brackets: v.brackets });
  });
  return out;
}

export function specFromState(s, meta) {
  const bracket_vintages = activeVintages(s).map((v) => ({
    from: v.from,
    brackets: s.linked
      ? { single: v.brackets.single, head_of_household: { scale: "single", factor: 1.5 },
        married_filing_jointly: { scale: "single", factor: 2 } }
      : { single: v.brackets.single, head_of_household: v.brackets.head_of_household,
        married_filing_jointly: v.brackets.married_filing_jointly },
  }));
  const it = { bracket_vintages };
  if (s.sd) {
    it.standard_deduction = s.sdLinked
      ? { single: s.sd.single, head_of_household: s.sd.single * 1.5, married_filing_jointly: s.sd.single * 2 }
      : { ...s.sd };
  }
  if (s.pe !== null) it.personal_exemption = s.pe;
  // An untouched preset keeps its name, so a shared link reopens on that preset.
  const name = s.preset && s.preset !== "custom" ? s.preset : "my_plan";
  return clone({ name, label: s.label || "My plan", first_year: s.firstYear, income_tax: it,
    model_version: meta.model_version });
}

// ---- edits -----------------------------------------------------------------

/** Make head of household and joint schedules 1.5 and 2 times single. */
export function syncLinked(s) {
  if (!s.linked) return;
  for (const v of s.vintages) {
    for (const k of ["head_of_household", "married_filing_jointly"]) {
      v.brackets[k] = v.brackets.single.map((r) => [r[0] * LINK[k], r[1]]);
    }
  }
}

/** Quick control: the top rate, in every period and filing status. */
export function setTopRate(s, rate) {
  for (const v of s.vintages) for (const k of STATUS_KEYS) v.brackets[k][v.brackets[k].length - 1][1] = rate;
}

/** Quick control: where the top rate starts, set for single; head of
 * household and joint at 1.5 and 2 times, in every period. A one-bracket
 * (flat) schedule has no top threshold to move: its only bracket starts at 0. */
export function setTopFloor(s, floor) {
  for (const v of s.vintages) {
    for (const k of STATUS_KEYS) {
      const rows = v.brackets[k];
      if (rows.length > 1) rows[rows.length - 1][0] = floor * (LINK[k] || 1);
    }
  }
}

/** The rows the editor shows for the active period and status. */
export function editorRows(s) {
  const b = s.vintages[s.activeVintage].brackets;
  return s.linked && s.activeStatus !== "single"
    ? b.single.map((r) => [r[0] * LINK[s.activeStatus], r[1]])
    : b[s.activeStatus];
}

/** The rows an edit changes (single's when linked). */
export function editableRows(s) {
  return s.vintages[s.activeVintage].brackets[s.linked ? "single" : s.activeStatus];
}

export function addBracket(s) {
  const rows = editableRows(s), last = rows[rows.length - 1];
  rows.push([Math.round(Math.max(last[0] * 1.5, last[0] + 10000) / 100) * 100, last[1]]);
  syncLinked(s);
}

export function removeBracket(s, i) {
  if (i < 1) return;
  editableRows(s).splice(i, 1);
  syncLinked(s);
}

// ---- guardrails --------------------------------------------------------------

function currentLawFor(meta, year) {
  let chosen = meta.current_law_vintages[0];
  for (const c of meta.current_law_vintages) if (c.from <= year) chosen = c;
  return chosen.brackets;
}

/** Warnings for the plan (TAX_SIMULATOR_SCOPE.md, "Guardrails"). */
export function warnings(s, meta) {
  const out = [];
  let maxRate = 0, falling = false, topUp = 0, topDown = false;
  for (const v of activeVintages(s)) {
    const cl = currentLawFor(meta, v.from);
    for (const k of STATUS_KEYS) {
      const rows = s.linked && k !== "single" ? v.brackets.single : v.brackets[k];
      rows.forEach((r, i) => { maxRate = Math.max(maxRate, r[1]); if (i && r[1] < rows[i - 1][1]) falling = true; });
      const top = rows[rows.length - 1][1], clTop = cl[k][cl[k].length - 1][1];
      topUp = Math.max(topUp, top - clTop);
      if (top < clTop) topDown = true;
    }
  }
  if (maxRate > 15) {
    out.push({ id: "extreme", text: "A rate above 15 percent is far outside anything Hawaiʻi has had. The model’s behavioral response was set for top-rate changes of a few points, so these results are an extrapolation." });
  } else if (topUp > 3) {
    out.push({ id: "large", text: `Your top rate is ${nf1.format(topUp)} points above current law’s. The behavioral response was set for changes of a few points, so treat these results as rough.` });
  }
  if (topDown) out.push({ id: "cut", text: "You lowered the top rate. The model gives rate cuts no behavioral response: filers whose rates fall are scored as if they reported the same income, though some would report more and offset part of the cost. Filers whose rates rise anywhere in your plan still respond." });
  if (falling) out.push({ id: "falling", text: "In your schedule a rate falls as income rises, so some higher-income filers face a lower rate than people earning less." });
  return out;
}

// ---- share links: #plan=<base64url JSON> ---------------------------------------

export function encodeSpec(spec) {
  const bytes = new TextEncoder().encode(JSON.stringify(spec));
  let bin = "";
  bytes.forEach((b) => { bin += String.fromCharCode(b); });
  return "plan=" + btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function decodeSpec(hash) {
  const m = /(?:^#|&)plan=([A-Za-z0-9_-]+)/.exec(hash);
  if (!m) return null;
  try {
    const b64 = m[1].replace(/-/g, "+").replace(/_/g, "/");
    const bin = atob(b64 + "===".slice((b64.length + 3) % 4));
    return JSON.parse(new TextDecoder().decode(Uint8Array.from(bin, (c) => c.charCodeAt(0))));
  } catch {
    return null;
  }
}
