// Tax simulator page: controls, results, sharing.
//
// The model runs in worker.js (kernel.js on the Act 24 population); this file
// owns the page state, turns it into a spec (tax_modeler.reform.
// income_tax_spec's format), and renders what the worker sends back.
import { CG_ORDINARY, parseSpec, householdTax } from "./kernel.js";
import {
  STATUS_KEYS, LINK, clone, stateFromSpec, activeVintages, specFromState, syncLinked, setTopRate,
  setTopFloor, editorRows, editableRows, addBracket, removeBracket, warnings, encodeSpec, decodeSpec,
} from "./plan.js";

const root = document.getElementById("sim-app");
const DATA = new URL(root.dataset.data, document.baseURI);
const STATUS_LABEL = { single: "Single", head_of_household: "Head of household",
  married_filing_jointly: "Joint" };
const MINUS = "−";

let meta = null;
let worker = null;
let state = null;
let latest = null;      // last result from the worker
let pending = 0;        // id of the latest request
let timer = null;

// ---------------------------------------------------------------------------
// formatting (the site's conventions: U+2212 minus, rounded millions)
// ---------------------------------------------------------------------------

const nf0 = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const nf1 = new Intl.NumberFormat("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const sign = (v) => (v < 0 ? MINUS : v > 0 ? "+" : "");
function millions(v, digits = 0) {
  const a = Math.abs(v), f = digits ? nf1 : nf0;
  if (digits === 0 && a > 0 && a < 1) return `${sign(v)}$${nf1.format(a)} million`;
  return `${sign(v)}$${f.format(a)} million`;
}
const m1 = (v) => `${v < 0 ? MINUS : ""}${nf1.format(Math.abs(v))}`;
const dollars = (v) => `${v < 0 ? MINUS : ""}$${nf0.format(Math.abs(v))}`;
const signedDollars = (v) => `${sign(Math.round(v))}$${nf0.format(Math.abs(Math.round(v)))}`;
const pct = (v) => `${nf0.format(v)}`;
const nf2 = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });
const pctText = (v) => nf2.format(v);     // a rate as written: 7.25, 9
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

// ---------------------------------------------------------------------------
// rendering: controls
// ---------------------------------------------------------------------------

function yearOptions(selected, years = meta.years) {
  return years.map((y) => `<option value="${y}"${y === selected ? " selected" : ""}>${y}</option>`).join("");
}

function vintageLabel(v, list) {
  const next = list[list.indexOf(v) + 1];
  if (!next) return `${v.from} and later`;
  return next.from - 1 === v.from ? `${v.from}` : `${v.from} to ${next.from - 1}`;
}

// renderControls rebuilds the form, so note which control had focus and put
// it back afterwards; otherwise keyboard users land on the page body after
// every tab, add, remove or checkbox. A removed row's button falls back to
// the row above, then to "Add a bracket".
function focusSelectors(el) {
  if (!el || !root.contains(el)) return [];
  if (el.id) return [`#${el.id}`];
  const d = el.dataset;
  if (d.vintage !== undefined) return [`[data-vintage="${d.vintage}"]`];
  if (d.status) return [`[data-status="${d.status}"]`];
  if (d.row !== undefined) return [`[data-row="${d.row}"][data-col="${d.col}"]`];
  if (d.remove !== undefined) return [`[data-remove="${d.remove}"]`, `[data-remove="${d.remove - 1}"]`, "#sim-add"];
  if (el.name) return [`input[name="${el.name}"][value="${el.value}"]`];
  return [];
}

function renderControls() {
  const refocus = focusSelectors(document.activeElement);
  drawControls();
  const form = $("#sim-controls");
  for (const sel of refocus) {
    const el = form.querySelector(sel);
    if (el && !el.disabled) { el.focus({ preventScroll: true }); break; }
  }
}

function drawControls() {
  const s = state;
  const act = activeVintages(s);
  if (!act.some((v) => v.index === s.activeVintage)) s.activeVintage = act[0].index;
  const av = act.find((v) => v.index === s.activeVintage);
  const statusEditable = !s.linked || s.activeStatus === "single";
  const rows = editorRows(s);
  const topRate = av.brackets.single[av.brackets.single.length - 1][1];
  const topFloor = av.brackets.single[av.brackets.single.length - 1][0];
  const clSd = meta.current_law[String(s.firstYear)].standard_deduction;
  const pePlaceholder = meta.current_law[String(s.firstYear)].personal_exemption;
  const clCg = meta.current_law[String(s.firstYear)].capital_gains_rate;
  const cgOwn = typeof s.cg === "number";

  $("#sim-controls").innerHTML = `
<div class="ha-sim__field">
  <label for="sim-preset">Start from</label>
  <select id="sim-preset">${meta.presets.map((p) => `<option value="${p.name}"${p.name === s.preset ? " selected" : ""}>${esc(p.label)}</option>`).join("")}
  ${s.preset === "custom" ? '<option value="custom" selected>Your changes</option>' : ""}</select>
</div>
<div class="ha-sim__field">
  <label for="sim-first-year">First tax year it applies</label>
  <select id="sim-first-year">${yearOptions(s.firstYear)}</select>
  <p class="ha-sim__hint">Current law (Act 24) applies before this year.</p>
</div>

<fieldset class="ha-sim__group">
  <legend>Quick changes</legend>
  <div class="ha-sim__row2">
    <div class="ha-sim__field"><label for="sim-top-rate">Top rate (%)</label>
      <input id="sim-top-rate" type="number" inputmode="decimal" min="0" max="99.99" step="0.05" value="${topRate}"></div>
    <div class="ha-sim__field"><label for="sim-top-floor">Top rate starts at (single)</label>
      <input id="sim-top-floor" type="number" inputmode="numeric" min="1" step="1000" value="${topFloor}"${av.brackets.single.length > 1 ? "" : " disabled"}></div>
  </div>
  <p class="ha-sim__hint">Applies to every schedule below. Head of household's top rate starts at 1.5 times the single amount and joint filers' at 2 times.</p>
</fieldset>

<fieldset class="ha-sim__group">
  <legend>Capital gains</legend>
  <label class="ha-sim__check"><input type="radio" name="sim-cg" value="law"${s.cg === null ? " checked" : ""}> Current law: no more than ${pctText(clCg)} percent</label>
  <label class="ha-sim__check"><input type="radio" name="sim-cg" value="own"${cgOwn ? " checked" : ""}> A different rate</label>
  ${cgOwn ? `<div class="ha-sim__row2"><div class="ha-sim__field"><label for="sim-cg-rate">Rate on capital gains (%)</label>
    <input id="sim-cg-rate" type="number" inputmode="decimal" min="0" max="99.99" step="0.25" value="${s.cg}"></div></div>` : ""}
  <label class="ha-sim__check"><input type="radio" name="sim-cg" value="ordinary"${s.cg === CG_ORDINARY ? " checked" : ""}> Tax as ordinary income, at the bracket rates</label>
  <p class="ha-sim__hint">Net long-term capital gains are taxed at the bracket rates, but no higher than this rate. Your choice applies from ${s.firstYear}.</p>
</fieldset>

<fieldset class="ha-sim__group">
  <legend>Brackets</legend>
  ${act.length > 1 ? `<div class="ha-sim__tabs" role="tablist" aria-label="Schedule years">${act.map((v) =>
    `<button type="button" role="tab" data-vintage="${v.index}" aria-selected="${v.index === s.activeVintage}" tabindex="${v.index === s.activeVintage ? 0 : -1}">${vintageLabel(v, act)}</button>`).join("")}</div>
  <p class="ha-sim__hint">${act.every((v) => v.from === s.firstYear || meta.current_law_vintages.some((c) => c.from === v.from))
    ? `Current law already schedules different brackets from ${meta.current_law_vintages[meta.current_law_vintages.length - 1].from}, so each period has its own schedule.`
    : "This plan has its own schedule for each period."}</p>` : ""}
  ${act.length === 1 && s.vintages.length > 1 ? `<p class="ha-sim__hint">From ${s.firstYear}, only current law’s ${s.vintages[act[0].index].from}-and-later schedule applies, so that is the one you are editing.</p>` : ""}
  <div class="ha-sim__tabs" role="tablist" aria-label="Filing status">${STATUS_KEYS.map((k) =>
    `<button type="button" role="tab" data-status="${k}" aria-selected="${k === s.activeStatus}" tabindex="${k === s.activeStatus ? 0 : -1}">${STATUS_LABEL[k]}</button>`).join("")}</div>
  <label class="ha-sim__check"><input type="checkbox" id="sim-linked"${s.linked ? " checked" : ""}> Head of household brackets are 1.5 times single, joint 2 times (as in current law)</label>
  <table class="ha-sim__sched">
    <caption class="ha-sim__sr">${STATUS_LABEL[s.activeStatus]} brackets, ${vintageLabel(av, act)}</caption>
    <thead><tr><th scope="col">Taxable income from</th><th scope="col">Rate (%)</th><th scope="col"><span class="ha-sim__sr">Remove</span></th></tr></thead>
    <tbody>${rows.map((r, i) => `<tr>
      <td><input type="number" inputmode="numeric" data-row="${i}" data-col="0" value="${r[0]}" min="0" step="100" aria-label="Bracket ${i + 1} starts at"${i === 0 || !statusEditable ? " disabled" : ""}></td>
      <td><input type="number" inputmode="decimal" data-row="${i}" data-col="1" value="${r[1]}" min="0" max="99.99" step="0.05" aria-label="Bracket ${i + 1} rate"${statusEditable ? "" : " disabled"}></td>
      <td>${i && statusEditable ? `<button type="button" class="ha-sim__x" data-remove="${i}" aria-label="Remove bracket ${i + 1}">×</button>` : ""}</td></tr>`).join("")}</tbody>
  </table>
  ${statusEditable ? '<button type="button" class="ha-sim__btn ha-sim__btn--quiet" id="sim-add">Add a bracket</button>' +
    (act.length > 1 ? ' <button type="button" class="ha-sim__btn ha-sim__btn--quiet" id="sim-copy">Use this schedule for every period</button>' : "")
    : '<p class="ha-sim__hint">Linked to the single schedule. Uncheck the box above to edit it separately.</p>'}
</fieldset>

<fieldset class="ha-sim__group">
  <legend>Standard deduction</legend>
  <label class="ha-sim__check"><input type="radio" name="sim-sd" value="law"${s.sd ? "" : " checked"}> Current law (joint: ${dollars(clSd.Joint_Surviving_Spouse)} in ${s.firstYear}, rising to ${dollars(meta.current_law[String(meta.years[meta.years.length - 1])].standard_deduction.Joint_Surviving_Spouse)} by ${meta.years[meta.years.length - 1]})</label>
  <label class="ha-sim__check"><input type="radio" name="sim-sd" value="own"${s.sd ? " checked" : ""}> Set my own for every year</label>
  ${s.sd ? `<div class="ha-sim__row3">${STATUS_KEYS.map((k) => `<div class="ha-sim__field"><label for="sim-sd-${k}">${STATUS_LABEL[k]}</label>
    <input id="sim-sd-${k}" type="number" inputmode="numeric" min="0" step="100" data-sd="${k}" value="${s.sdLinked && k !== "single" ? s.sd.single * LINK[k] : s.sd[k]}"${s.sdLinked && k !== "single" ? " disabled" : ""}></div>`).join("")}</div>
  <label class="ha-sim__check"><input type="checkbox" id="sim-sd-linked"${s.sdLinked ? " checked" : ""}> Head of household 1.5 times single, joint 2 times</label>
  <p class="ha-sim__hint">These amounts apply in every year from ${s.firstYear}. Current law’s deduction keeps rising (joint: ${dollars(meta.current_law[String(meta.years[meta.years.length - 1])].standard_deduction.Joint_Surviving_Spouse)} by ${meta.years[meta.years.length - 1]}), so a fixed amount raises revenue in the years it falls below that.</p>` : ""}
</fieldset>

<fieldset class="ha-sim__group">
  <legend>Personal exemption</legend>
  <div class="ha-sim__field"><label for="sim-pe">Dollars per person (filer, spouse, each dependent)</label>
    <input id="sim-pe" type="number" inputmode="numeric" min="0" step="10" value="${s.pe ?? ""}" placeholder="${pePlaceholder} (current law)"></div>
</fieldset>`;
}

// ---------------------------------------------------------------------------
// rendering: results
// ---------------------------------------------------------------------------

function columnChart(labels, values, label) {
  // Columns up for gains, down for losses; the zero line sits where the data puts it.
  const maxPos = Math.max(0, ...values), maxNeg = Math.max(0, ...values.map((v) => -v));
  const span = (maxPos + maxNeg) || 1;
  const cols = labels.map((l, i) => {
    const v = values[i];
    // 88%, not 100%: the value label needs room beside the tallest bar
    const up = v > 0 ? v / maxPos * 88 : 0, down = v < 0 ? -v / maxNeg * 88 : 0;
    return `<div class="ha-sim__vcol">
      <div class="ha-sim__vpos" style="flex:${(maxPos / span).toFixed(4)}">${v > 0 ? `<span class="ha-sim__vval">${m1(v)}</span><span class="ha-sim__vbar" style="height:${up.toFixed(2)}%"></span>` : ""}</div>
      <div class="ha-sim__vneg" style="flex:${(maxNeg / span).toFixed(4)}">${v < 0 ? `<span class="ha-sim__vbar ha-sim__vbar--neg" style="height:${down.toFixed(2)}%"></span><span class="ha-sim__vval">${m1(v)}</span>` : ""}</div>
      ${v === 0 ? '<span class="ha-sim__vval ha-sim__vzero">0.0</span>' : ""}
      <div class="ha-est__col-cat">${l}</div></div>`;
  });
  return `<div class="ha-est__chart" role="img" aria-label="${esc(label)}"><div class="ha-sim__vgrid" aria-hidden="true">${cols.join("")}</div></div>`;
}

function divergingBars(cats, values, label) {
  const lo = Math.min(0, ...values), hi = Math.max(0, ...values), span = (hi - lo) || 1;
  const zero = 100 * -lo / span;
  const rows = cats.map((c, i) => {
    const v = values[i], w = 100 * Math.abs(v) / span, left = v >= 0 ? zero : zero - w;
    const side = v >= 0 ? "pos" : "neg";
    return `<div class="ha-est__div-row"><div class="ha-est__hbar-cat">${esc(c)}</div><div class="ha-est__div-track">
<span class="ha-est__div-axis" style="left:${zero.toFixed(2)}%"></span>
<span class="ha-est__div-bar" style="left:${left.toFixed(2)}%;width:${Math.max(w, 0.3).toFixed(2)}%;background:${v >= 0 ? "#52796F" : "#84A98C"}"></span>
<span class="ha-est__div-val ha-est__div-val--${side}" style="${v >= 0 ? "left" : "right"}:${(v >= 0 ? zero + w : 100 - zero + w).toFixed(2)}%">${signedDollars(v)}</span></div></div>`;
  });
  return `<div class="ha-est__chart" role="img" aria-label="${esc(label)}"><div aria-hidden="true">${rows.join("")}</div></div>`;
}

function table(headers, rows, caption, emRow = -1) {
  return `<p class="ha-est__figcap">${caption}</p><div class="ha-est__table-wrap"><table><thead><tr>${headers.map((h) => `<th scope="col">${h}</th>`).join("")}</tr></thead><tbody>${
    rows.map((r, i) => `<tr${i === emRow ? ' class="ha-est__row-em"' : ""}>${r.map((c, k) => (k ? `<td>${c}</td>` : `<th scope="row">${c}</th>`)).join("")}</tr>`).join("")}</tbody></table></div>`;
}

const FIFTH = { "Q1 (bottom 20%)": "Lowest 20 percent", Q2: "Second 20 percent", Q3: "Middle 20 percent",
  Q4: "Fourth 20 percent", "Q5 (top 20%)": "Top 20 percent" };

const CG_LINE = "#B08D57";

function rateChart(systems, year) {
  // marginal rate by taxable income, joint filers: current law vs the plan
  const sys = systems[year], key = "Joint_Surviving_Spouse";
  const cl = sys.baseline.brackets[key], plan = sys.reform.brackets[key];
  // The plan's capital gains rate, from the first bracket whose rate reaches
  // it (the alternative tax's floor); none when gains are taxed as ordinary
  // income or the rate is at or above every bracket rate.
  const cgr = sys.reform.capital_gains_rate;
  const reach = typeof cgr === "number" && cgr < Math.max(...plan.map((r) => r[1])) ? plan.findIndex((r) => r[1] >= cgr) : -1;
  const cgFrom = reach >= 0 ? plan[reach][0] : null;
  const xMax = Math.max(1.3 * Math.max(cl[cl.length - 1][0], plan[plan.length - 1][0]), 100000);
  const yMax = Math.max(...cl.map((r) => r[1]), ...plan.map((r) => r[1]), 1) * 1.1;
  const W = 600, H = 220;
  const path = (rows) => {
    const pts = [];
    rows.forEach((r, i) => {
      const x0 = r[0] / xMax * W, x1 = (i + 1 < rows.length ? rows[i + 1][0] : xMax) / xMax * W, y = H - r[1] / yMax * H;
      pts.push(`${x0.toFixed(1)},${y.toFixed(1)}`, `${Math.min(x1, W).toFixed(1)},${y.toFixed(1)}`);
    });
    return pts.join(" ");
  };
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => `<span style="left:${f * 100}%">$${nf0.format(Math.round(f * xMax / 1000))}K</span>`).join("");
  const cgY = cgFrom === null ? 0 : H - cgr / yMax * H;
  const cgLine = cgFrom === null ? "" : `<polyline points="${(cgFrom / xMax * W).toFixed(1)},${cgY.toFixed(1)} ${W},${cgY.toFixed(1)}" fill="none" stroke="${CG_LINE}" stroke-width="2" stroke-dasharray="2 4" vector-effect="non-scaling-stroke"/>`;
  const cgText = cgFrom === null
    ? "your plan taxes capital gains at the bracket rates"
    : `your plan taxes capital gains at no more than ${pctText(cgr)} percent, from $${nf0.format(cgFrom)}`;
  return `<div class="ha-est__chart"><div class="ha-est__legend" aria-hidden="true"><span><span class="ha-est__swatch" style="background:#84A98C"></span>Current law</span><span><span class="ha-est__swatch" style="background:#2F3E46"></span>Your plan</span>${cgFrom === null ? "" : `<span><span class="ha-est__swatch" style="background:${CG_LINE}"></span>Your plan’s rate on capital gains</span>`}</div>
<div class="ha-sim__ratechart" role="img" aria-label="Marginal tax rate by taxable income for joint filers in ${year}: current law's top rate ${cl[cl.length - 1][1]} percent from $${nf0.format(cl[cl.length - 1][0])}; your plan's top rate ${plan[plan.length - 1][1]} percent from $${nf0.format(plan[plan.length - 1][0])}; ${cgText}.">
<span class="ha-sim__ymax" aria-hidden="true">${nf1.format(yMax)}%</span>
<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true"><polyline points="${path(cl)}" fill="none" stroke="#84A98C" stroke-width="4" vector-effect="non-scaling-stroke"/><polyline points="${path(plan)}" fill="none" stroke="#2F3E46" stroke-width="2" stroke-dasharray="6 4" vector-effect="non-scaling-stroke"/>${cgLine}</svg>
<div class="ha-sim__xticks" aria-hidden="true">${ticks}</div></div>
<p class="ha-est__source">Marginal rate on taxable income (after deductions and exemptions), joint filers, ${year}. ${cgFrom === null
    ? "Your plan taxes long-term capital gains at the bracket rates."
    : `The dotted line is your plan’s rate on long-term capital gains: gains are taxed at the bracket rates up to the income where those reach ${pctText(cgr)} percent, and at no more than that above it.`}</p></div>`;
}

function renderResults() {
  if (!latest) return;
  const { result, systems } = latest;
  const s = state;
  const years = meta.years;
  const rev = (scen, y) => result.revenue.find((r) => r.scenario === scen && r.tax_year === y);
  const mid = years.map((y) => rev("mid", y)["behavioral_$M"]);
  const total = (scen) => years.reduce((a, y) => a + rev(scen, y)["behavioral_$M"], 0);
  const midStatic = years.map((y) => rev("mid", y)["static_$M"]);
  const fy = s.firstYear;
  // The scenarios are named for their effect on Act 24's gain, so for other
  // plans "low" and "high" need not be the ends: the range spans all three.
  const lo = Math.min(total("low"), total("mid"), total("high")), hi = Math.max(total("low"), total("mid"), total("high"));
  const dist = result.distribution[s.distYear];
  const q = dist.quintile;
  const hhAll = q.reduce((a, g) => a + g.household_count, 0);
  const payMore = q.reduce((a, g) => a + g.household_count * g.pct_pay_more, 0) / hhAll;
  const payLess = q.reduce((a, g) => a + g.household_count * g.pct_pay_less, 0) / hhAll;
  const verb = (v) => (v >= 0 ? "raises" : "costs");
  const t5 = total("mid");

  $("#sim-headline").innerHTML = `
<div class="ha-est__stat"><div class="ha-est__stat-num">${millions(rev("mid", fy)["behavioral_$M"])}</div><div class="ha-est__stat-label">Change in revenue in <strong>tax year ${fy}</strong>, the first year of your plan, compared with current law.</div></div>
<div class="ha-est__stat"><div class="ha-est__stat-num">${millions(t5)}</div><div class="ha-est__stat-label">Over <strong>tax years ${years[0]} to ${years[years.length - 1]}</strong>. Across the three scenarios: ${millions(lo)} to ${millions(hi)}.</div></div>
<div class="ha-est__stat"><div class="ha-est__stat-num">${pct(payLess)} percent</div><div class="ha-est__stat-label">Of households <strong>pay less</strong> income tax in ${s.distYear}.</div></div>
<div class="ha-est__stat"><div class="ha-est__stat-num">${pct(payMore)} percent</div><div class="ha-est__stat-label">Of households <strong>pay more</strong> in ${s.distYear}.</div></div>`;
  $("#sim-mini").textContent = `Your plan: ${millions(t5)} over ${years[0]} to ${years[years.length - 1]} · See the results`;
  showMini();
  $("#sim-live").textContent = `Your plan ${verb(t5)} $${nf0.format(Math.abs(t5))} million over ${years[0]} to ${years[years.length - 1]} compared with current law; ${pct(payLess)} percent of households pay less and ${pct(payMore)} percent pay more in ${s.distYear}.`;

  const w = warnings(s, meta).map((x) => x.text);
  $("#sim-warnings").innerHTML = w.length ? `<div class="ha-est__callout ha-sim__warn">${w.map((x) => `<p>${esc(x)}</p>`).join("")}</div>` : "";

  const revRows = years.map((y, i) => [String(y), m1(midStatic[i]), m1(mid[i]), m1(rev("low", y)["behavioral_$M"]), m1(rev("high", y)["behavioral_$M"])]);
  revRows.push(["Total", m1(midStatic.reduce((a, b) => a + b, 0)), m1(t5), m1(total("low")), m1(total("high"))]);
  const totalRow = revRows.length - 1;
  const cgPart = gainsPart(latest);
  if (cgPart) {
    revRows.push(["Of which, capital gains rate", m1(cgPart.reduce((a, r) => a + r["static_$M"], 0)),
      m1(cgPart.reduce((a, r) => a + r["behavioral_$M"], 0)), "—", "—"]);
  }
  $("#sim-revenue").innerHTML = `
<figure class="ha-est__figure"><p class="ha-est__figcap">Figure 1. Change in Income Tax Revenue Under Your Plan, Compared With Current Law (Tax Years ${years[0]} to ${years[years.length - 1]})</p>
${columnChart(years.map(String), mid, "Change in revenue by tax year, middle scenario: " + years.map((y, i) => `${y} ${m1(mid[i])} million`).join(", ") + ".")}
<p class="ha-est__source">Millions of dollars, middle scenario, after the behavioral response. Positive numbers are more revenue for the state.</p></figure>
${table(["Tax year", "Static", "With response", "Low scenario", "High scenario"], revRows, "Table 1. Change in Revenue Compared With Current Law ($ Millions)", totalRow)}
<p class="ha-est__source">Static: before any change in behavior. With response: after filers whose rates rise report less income, filers whose rate on capital gains rises sell fewer investments and, when the top rate rises, a few of the highest earners move away, each taking all of their income tax with them. Rate cuts get no response. The low and high scenarios vary that response, how fast top incomes grow, and the number of filers above $1 million.${cgPart
    ? " Of which, capital gains rate: your plan compared with the same plan at current law’s rate on capital gains, middle scenario only."
    : ""}</p>`;

  const cats = q.map((g) => FIFTH[g.group] || g.group);
  const distFocused = document.activeElement?.id === "sim-dist-year";
  $("#sim-dist").innerHTML = `
<div class="ha-sim__field ha-sim__inline"><label for="sim-dist-year">Tax year</label><select id="sim-dist-year">${yearOptions(s.distYear)}</select></div>
<figure class="ha-est__figure"><p class="ha-est__figcap">Figure 2. Average Change in Income Tax per Household Under Your Plan (Tax Year ${s.distYear})</p>
${divergingBars(cats, q.map((g) => g.avg_per_hh_bracket_change), `Average change per household in ${s.distYear}: ` + q.map((g, i) => `${cats[i]} ${signedDollars(g.avg_per_hh_bracket_change)}`).join("; ") + ".")}
<p class="ha-est__source">Households ranked by income; each group holds one fifth of households. Static, before the behavioral response. Negative numbers mean households pay less.</p></figure>
${table(["Household income", "Households", "Average change", "Total ($M)", "Percent paying less", "Percent paying more"],
  q.map((g, i) => [cats[i], nf0.format(g.household_count), signedDollars(g.avg_per_hh_bracket_change), m1(g["total_bracket_$M"]), pct(g.pct_pay_less), pct(g.pct_pay_more)]),
  `Table 2. Change in Income Tax by Fifth of Households, Tax Year ${s.distYear}`)}
${table(["Income (AGI)", "Tax returns", "Average change", "Total ($M)", "Percent paying less", "Percent paying more"],
  dist.income_class.map((g) => [g.group.replace("–", " to "), nf0.format(g.filer_count), signedDollars(g.avg_bracket_change), m1(g["total_bracket_$M"]), pct(g.pct_pay_less), pct(g.pct_pay_more)]),
  `Table 3. Change in Income Tax by Income, Tax Year ${s.distYear}`)}`;

  if (distFocused) $("#sim-dist-year").focus({ preventScroll: true });
  $("#sim-rates").innerHTML = rateChart(systems, s.distYear);
  renderHousehold();
}

/** "Of which, capital gains rate", per year (middle scenario): the plan less
 * the same plan at current law's capital gains rate, which the worker scores
 * when a plan changes both. null otherwise. */
function gainsPart(res) {
  if (!res?.withoutGains) return null;
  return res.withoutGains.map((r) => {
    const p = res.result.revenue.find((x) => x.scenario === r.scenario && x.tax_year === r.tax_year);
    return { scenario: r.scenario, tax_year: r.tax_year, "static_$M": p["static_$M"] - r["static_$M"],
      "behavioral_$M": p["behavioral_$M"] - r["behavioral_$M"] };
  });
}

function showMini() {
  const mini = $("#sim-mini");
  if (!mini) return;
  const r = $("#sim-results").getBoundingClientRect();
  const away = r.top > innerHeight * 0.6 || r.bottom < 0;   // results not on screen
  mini.hidden = !(latest && away);
}

function renderHousehold() {
  if (!latest) return;
  const h = state.household, y = state.distYear, sys = latest.systems[y];
  // gains are part of income, stored as a share of it (as the population stores them)
  const cgShare = h.agi > 0 ? Math.min(1, h.gains / h.agi) : 0;
  const args = { fs: h.fs, agi: h.agi, dependents: h.dependents, itemized: h.itemized, cgShare };
  const a = householdTax(sys.baseline, meta.food_excise, args);
  const b = householdTax(sys.reform, meta.food_excise, args);
  $("#sim-household-out").innerHTML = `<table><tbody>
<tr><th scope="row">Current law, ${y}</th><td>${dollars(a.net)}</td></tr>
<tr><th scope="row">Your plan, ${y}</th><td>${dollars(b.net)}</td></tr>
<tr class="ha-est__row-em"><th scope="row">Change</th><td>${signedDollars(b.net - a.net)}</td></tr></tbody></table>
<p class="ha-est__source">Hawaiʻi income tax after the credits the model includes: food/excise, dependent care (assuming the most eligible expenses), and the renters and renewable energy credits, which are averages over similar filers rather than what this household would claim. Negative means a refund. Capital gains are net long-term gains and are part of the income above${h.gains > h.agi ? "; gains above the income are counted as all of it" : ""}.</p>`;
}

// ---------------------------------------------------------------------------
// events
// ---------------------------------------------------------------------------

function $(sel) { return root.querySelector(sel); }

function setStatus(text) { $("#sim-status").textContent = text; }

// A notice about the plan itself (a shared link that could not be read, or
// was made with an earlier model run). It stays until the plan is edited;
// the status line changes with every scoring.
function setNotice(text) {
  $("#sim-notice").innerHTML = text ? `<div class="ha-est__callout"><p>${esc(text)}</p></div>` : "";
}

const SHARE_BUTTONS = ["sim-share", "sim-download-spec", "sim-download-csv"];

function request() {
  const spec = specFromState(state, meta);
  let problem = null;
  try { parseSpec(spec, meta); } catch (err) { problem = err.message; }
  $("#sim-errors").innerHTML = problem ? `<div class="ha-est__callout ha-sim__error" role="alert"><p><strong>Can’t score this yet.</strong> ${esc(humanize(problem))}</p></div>` : "";
  // The link, the plan download and the results download all describe the
  // plan on screen, so they wait until it can be scored.
  for (const id of SHARE_BUTTONS) $(`#${id}`).disabled = Boolean(problem);
  if (problem) return;
  history.replaceState(null, "", `#${encodeSpec(spec)}`);
  clearTimeout(timer);
  timer = setTimeout(() => {
    pending += 1;
    setStatus("Scoring…");
    worker.postMessage({ type: "score", id: pending, spec, distYear: state.distYear });
  }, 150);
}

function humanize(msg) {
  // spec vintage i is the i-th period in force (specFromState)
  const act = activeVintages(state);
  const period = (i) => (act.length > 1 && act[i] ? `${vintageLabel(act[i], act)}, ` : "");
  return msg.replace(/above (\d{4,})/g, (_, n) => `above ${dollars(Number(n))}`)
    .replace(/income_tax\.bracket_vintages\[(\d+)\]\.brackets\.(\w+)/g, (_, v, k) => `${period(Number(v))}${STATUS_LABEL[k] || k} brackets`)
    .replace(/income_tax\.brackets\.(\w+)/g, (_, k) => `${STATUS_LABEL[k] || k} brackets`)
    .replace(/income_tax\.standard_deduction\.(\w+)/g, (_, k) => `Standard deduction (${(STATUS_LABEL[k] || k).toLowerCase()})`)
    .replace(/income_tax\.personal_exemption/g, "Personal exemption")
    .replace(/income_tax\.capital_gains_rate: \S+ is outside \[0, 100\)/g, "Capital gains rate: must be at least 0 and below 100")
    .replace(/income_tax\.capital_gains_rate/g, "Capital gains rate")
    .replace(/\[(\d+)\]/g, (_, i) => ` (row ${Number(i) + 1})`);
}

function markCustom() {
  state.preset = "custom"; state.label = ""; setNotice("");
  // the "Start from" menu stops naming a preset the plan no longer matches
  const sel = $("#sim-preset");
  if (sel && sel.value !== "custom") {
    if (!sel.querySelector('option[value="custom"]')) sel.add(new Option("Your changes", "custom"));
    sel.value = "custom";
  }
}

function onInput(e) {
  if (!state) return;                    // the model has not loaded yet
  const t = e.target;
  const num = t.value === "" ? NaN : Number(t.value);
  if (t.dataset.row !== undefined) {
    const rows = editableRows(state);
    if (Number.isFinite(num)) {
      rows[Number(t.dataset.row)][Number(t.dataset.col)] = num; syncLinked(state); markCustom(); request();
      refreshQuick();
    }
    return;
  }
  if (t.dataset.sd) {
    if (Number.isFinite(num)) {
      state.sd[t.dataset.sd] = num;
      if (state.sdLinked && t.dataset.sd === "single") {
        for (const k of ["head_of_household", "married_filing_jointly"]) {
          state.sd[k] = num * LINK[k];
          const other = root.querySelector(`[data-sd="${k}"]`);
          if (other) other.value = state.sd[k];
        }
      }
      markCustom(); request();
    }
    return;
  }
  switch (t.id) {
    case "sim-top-rate":
      if (!Number.isFinite(num)) return;
      setTopRate(state, num);
      markCustom(); request(); refreshTable(); return;
    case "sim-top-floor":
      if (!Number.isFinite(num)) return;
      setTopFloor(state, num);
      markCustom(); request(); refreshTable(); return;
    case "sim-pe":
      state.pe = t.value === "" ? null : (Number.isFinite(num) ? num : state.pe);
      markCustom(); request(); return;
    case "sim-cg-rate":
      if (!Number.isFinite(num)) return;
      state.cg = num;
      markCustom(); request(); return;
    case "sim-hh-agi": state.household.agi = Number.isFinite(num) ? Math.max(0, num) : 0; renderHousehold(); return;
    case "sim-hh-cg": state.household.gains = Number.isFinite(num) ? Math.max(0, num) : 0; renderHousehold(); return;
    case "sim-hh-deps": state.household.dependents = Number.isFinite(num) ? Math.max(0, Math.min(20, Math.round(num))) : 0; renderHousehold(); return;
    case "sim-hh-item": state.household.itemized = Number.isFinite(num) ? Math.max(0, num) : 0; renderHousehold(); return;
    default:
  }
}

function refreshQuick() {
  // the quick fields show the active period's single top bracket
  const single = state.vintages[state.activeVintage].brackets.single, top = single[single.length - 1];
  $("#sim-top-rate").value = top[1];
  $("#sim-top-floor").value = top[0];
}

function refreshTable() {
  // keep focus in the quick controls; re-render only the schedule table body
  const rows = editorRows(state);
  root.querySelectorAll(".ha-sim__sched input").forEach((inp) => {
    inp.value = rows[Number(inp.dataset.row)][Number(inp.dataset.col)];
  });
}

function onChange(e) {
  if (!state) return;
  const t = e.target;
  switch (t.id) {
    case "sim-preset": {
      const p = meta.presets.find((x) => x.name === t.value);
      if (p) { const hh = state.household; state = stateFromSpec(p, meta); state.household = hh; renderControls(); request(); }
      return;
    }
    case "sim-first-year": state.firstYear = Number(t.value); state.distYear = Math.max(state.distYear, state.firstYear); markCustom(); renderControls(); request(); return;
    case "sim-linked":
      state.linked = t.checked;
      syncLinked(state);
      markCustom(); renderControls(); request(); return;
    case "sim-sd-linked": state.sdLinked = t.checked; if (t.checked) { state.sd.head_of_household = state.sd.single * 1.5; state.sd.married_filing_jointly = state.sd.single * 2; } markCustom(); renderControls(); request(); return;
    case "sim-dist-year": state.distYear = Number(t.value); request(); return;
    case "sim-hh-fs": state.household.fs = t.value; renderHousehold(); return;
    default:
  }
  if (t.name === "sim-sd") {
    if (t.value === "own") {
      const cl = meta.current_law[String(state.firstYear)].standard_deduction;
      state.sd = { single: cl.Single_Married_Separate, head_of_household: cl.Head_of_Household,
        married_filing_jointly: cl.Joint_Surviving_Spouse };
      state.sdLinked = true;
    } else state.sd = null;
    markCustom(); renderControls(); request();
  }
  if (t.name === "sim-cg") {
    // "A different rate" starts from current law's, as the deduction does
    state.cg = t.value === "own" ? meta.current_law[String(state.firstYear)].capital_gains_rate
      : t.value === "ordinary" ? CG_ORDINARY : null;
    markCustom(); renderControls(); request();
  }
}

function onClick(e) {
  const t = e.target.closest("button");
  if (!t || !state) return;
  if (t.dataset.vintage !== undefined) { state.activeVintage = Number(t.dataset.vintage); renderControls(); return; }
  if (t.dataset.status) { state.activeStatus = t.dataset.status; renderControls(); return; }
  if (t.dataset.remove !== undefined) {
    removeBracket(state, Number(t.dataset.remove));
    markCustom(); renderControls(); request(); return;
  }
  switch (t.id) {
    case "sim-add": addBracket(state); markCustom(); renderControls(); request(); return;
    case "sim-copy": {
      const src = state.vintages[state.activeVintage].brackets;
      for (const v of state.vintages) v.brackets = clone(src);
      markCustom(); renderControls(); request(); return;
    }
    case "sim-share": {
      const url = `${location.href.split("#")[0]}#${encodeSpec(specFromState(state, meta))}`;
      navigator.clipboard?.writeText(url).then(() => setStatus("Link copied."), () => setStatus("Copy the address in your browser's address bar to share."));
      if (!navigator.clipboard) setStatus("Copy the address in your browser's address bar to share.");
      return;
    }
    case "sim-download-spec": download("my-tax-plan.json", JSON.stringify(specFromState(state, meta), null, 2), "application/json"); return;
    case "sim-download-csv": download("my-tax-plan-results.csv", resultsCsv(), "text/csv"); return;
    case "sim-reset": { const hh = state.household; state = stateFromSpec(meta.presets[0], meta); state.household = hh; setNotice(""); renderControls(); request(); return; }
    default:
  }
}

// Tabs: arrow keys, Home and End move between them (the ARIA tabs pattern).
function onKeydown(e) {
  const t = e.target;
  if (t.getAttribute("role") !== "tab") return;
  const tabs = [...t.parentElement.querySelectorAll('[role="tab"]')];
  const i = tabs.indexOf(t);
  const j = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: tabs.length - 1 }[e.key];
  if (j === undefined) return;
  e.preventDefault();
  const next = tabs[(j + tabs.length) % tabs.length];
  next.focus();
  next.click();
}

function download(name, text, type) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type }));
  a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

function resultsCsv() {
  // latest.spec and latest.distYear are what produced latest.result
  if (!latest) return "";
  const lines = [`# Hawaiʻi Appleseed tax simulator, model ${meta.model_version}; change vs current law (Act 24), $M`,
    "scenario,tax_year,static_M,with_response_M"];
  for (const r of latest.result.revenue) lines.push([r.scenario, r.tax_year, r["static_$M"].toFixed(3), r["behavioral_$M"].toFixed(3)].join(","));
  const cgPart = gainsPart(latest);
  if (cgPart) {
    lines.push("", "# of which, capital gains rate: the plan less the same plan at current law's capital gains rate, $M",
      "scenario,tax_year,static_M,with_response_M");
    for (const r of cgPart) lines.push([r.scenario, r.tax_year, r["static_$M"].toFixed(3), r["behavioral_$M"].toFixed(3)].join(","));
  }
  lines.push("", `# distribution, tax year ${latest.distYear}, static`, "group,households_or_returns,avg_change,total_change_M,pct_pay_less,pct_pay_more");
  const d = latest.result.distribution[latest.distYear];
  for (const g of d.quintile) lines.push([`"${g.group}"`, g.household_count.toFixed(0), g.avg_per_hh_bracket_change.toFixed(2), g["total_bracket_$M"].toFixed(3), g.pct_pay_less.toFixed(2), g.pct_pay_more.toFixed(2)].join(","));
  for (const g of d.income_class) lines.push([`"${g.group}"`, g.filer_count.toFixed(0), g.avg_bracket_change.toFixed(2), g["total_bracket_$M"].toFixed(3), g.pct_pay_less.toFixed(2), g.pct_pay_more.toFixed(2)].join(","));
  lines.push("", "# spec", `"${JSON.stringify(latest.spec).replace(/"/g, '""')}"`);
  return "\uFEFF" + lines.join("\n") + "\n";      // BOM: Excel reads the file as UTF-8
}

// ---------------------------------------------------------------------------
// start
// ---------------------------------------------------------------------------

function layout() {
  // The app goes in its own element, ahead of the no-JavaScript table
  // (#sim-fallback), which stays on the page until the model is ready and
  // comes back if it cannot load.
  const app = document.createElement("div");
  app.id = "sim-live-app";
  app.innerHTML = `
<p class="ha-sim__status" id="sim-status" role="status">Loading the model…</p>
<div id="sim-notice"></div>
<div class="ha-sim__grid" id="sim-grid" hidden>
  <form class="ha-sim__controls" id="sim-controls" aria-label="Your income tax plan" onsubmit="return false"></form>
  <div class="ha-sim__results" id="sim-results">
    <div id="sim-errors"></div>
    <div class="ha-sim__stats" id="sim-headline"></div>
    <p class="ha-sim__sr" aria-live="polite" id="sim-live"></p>
    <div id="sim-warnings"></div>
    <h2 class="ha-sim__h">What it raises</h2>
    <div id="sim-revenue"></div>
    <h2 class="ha-sim__h">Who pays more and who pays less</h2>
    <div id="sim-dist"></div>
    <h2 class="ha-sim__h">A household like yours</h2>
    <form class="ha-sim__household" onsubmit="return false" aria-label="Household calculator">
      <div class="ha-sim__field"><label for="sim-hh-fs">Filing status</label><select id="sim-hh-fs">
        <option value="single">Single</option><option value="married_filing_jointly" selected>Married, filing jointly</option>
        <option value="head_of_household">Head of household</option><option value="married_filing_separately">Married, filing separately</option></select></div>
      <div class="ha-sim__field"><label for="sim-hh-agi">Income (adjusted gross income)</label><input id="sim-hh-agi" type="number" inputmode="numeric" min="0" step="1000" value="120000"></div>
      <div class="ha-sim__field"><label for="sim-hh-cg">Long-term capital gains, part of that income</label><input id="sim-hh-cg" type="number" inputmode="numeric" min="0" step="1000" value="0"></div>
      <div class="ha-sim__field"><label for="sim-hh-deps">Dependents</label><input id="sim-hh-deps" type="number" inputmode="numeric" min="0" max="20" step="1" value="2"></div>
      <div class="ha-sim__field"><label for="sim-hh-item">Itemized deductions, if any</label><input id="sim-hh-item" type="number" inputmode="numeric" min="0" step="1000" value="0"></div>
    </form>
    <div id="sim-household-out"></div>
    <h2 class="ha-sim__h">Your rates and current law</h2>
    <div id="sim-rates"></div>
    <div class="ha-sim__actions">
      <button type="button" class="ha-sim__btn" id="sim-share">Copy a link to this plan</button>
      <button type="button" class="ha-sim__btn ha-sim__btn--quiet" id="sim-download-spec">Download the plan (JSON)</button>
      <button type="button" class="ha-sim__btn ha-sim__btn--quiet" id="sim-download-csv">Download the results (CSV)</button>
      <button type="button" class="ha-sim__btn ha-sim__btn--quiet" id="sim-reset">Start over</button>
    </div>
  </div>
</div>`;
  root.prepend(app);
  // Phones: the controls stack above the results, so a bar at the bottom of
  // the screen shows the plan's total until the results themselves are in view.
  // It scrolls rather than following its #sim-results link, which would
  // replace the plan in the address bar.
  const mini = document.createElement("a");
  mini.className = "ha-sim__mini"; mini.id = "sim-mini"; mini.href = "#sim-results"; mini.hidden = true;
  mini.addEventListener("click", (e) => { e.preventDefault(); $("#sim-results").scrollIntoView({ behavior: "smooth" }); });
  app.appendChild(mini);
  addEventListener("scroll", showMini, { passive: true });
  addEventListener("resize", showMini, { passive: true });
  addEventListener("hashchange", onHashChange);
  root.addEventListener("input", onInput);
  root.addEventListener("change", onChange);
  root.addEventListener("click", onClick);
  root.addEventListener("keydown", onKeydown);
}

/** Open a plan from a share link (or current law if there is none). */
function loadPlan(hash) {
  const shared = decodeSpec(hash);
  let initial = meta.presets[0], notice = "";
  if (shared) {
    try {
      parseSpec(shared, meta); initial = shared;
      if (shared.model_version && shared.model_version !== meta.model_version) {
        notice = "This link was made with an earlier model run; the numbers have been updated since.";
      }
    } catch { notice = "The plan in this link could not be read, so the page starts from current law."; }
  } else if (/(?:^#|&)plan=/.test(hash)) {
    notice = "The plan in this link could not be read, so the page starts from current law.";
  }
  const household = state?.household;
  state = stateFromSpec(initial, meta);
  if (household) state.household = household;
  if (shared && initial === shared) state.preset = meta.presets.some((p) => p.name === shared.name) ? shared.name : "custom";
  renderControls();
  setNotice(notice);
  request();
}

// A plan pasted into the address bar of an open page arrives as a hash
// change, not a reload. Any other hash (an endnote, say) is put back to the
// plan on screen, so the address bar can always be shared or reloaded.
function onHashChange() {
  if (!meta || !state) return;
  const current = `#${encodeSpec(specFromState(state, meta))}`;
  if (location.hash === current) return;
  if (/(?:^#|&)plan=/.test(location.hash)) loadPlan(location.hash);
  else history.replaceState(null, "", current);
}

function start() {
  const fallback = document.getElementById("sim-fallback");
  layout();
  const fail = (message) => {
    // Keep the precomputed presets table, which needs no JavaScript.
    $("#sim-grid").hidden = true;
    setStatus(`${message} The table below shows the model's results for a few options.`);
    if (fallback) fallback.hidden = false;
  };
  worker = new Worker(new URL("./worker.js", import.meta.url), { type: "module" });
  worker.onerror = () => fail("The simulator could not start in this browser.");
  worker.onmessage = (e) => {
    const m = e.data;
    if (m.type === "fatal") { fail(`The simulator could not start: ${m.message}.`); return; }
    if (m.type === "ready") {
      meta = m.meta;
      if (fallback) fallback.hidden = true;
      $("#sim-grid").hidden = false;
      setStatus("Ready.");
      loadPlan(location.hash);
      return;
    }
    if (m.id !== pending) return;       // a newer request is on its way
    if (m.type === "invalid") { setStatus(""); $("#sim-errors").innerHTML = `<div class="ha-est__callout ha-sim__error" role="alert"><p>${esc(humanize(m.message))}</p></div>`; return; }
    if (m.type === "result") {
      latest = m;
      setStatus(`Scored in ${Math.round(m.ms)} ms. Model run ${meta.model_version}.`);
      renderResults();
    }
  };
  worker.postMessage({ type: "init", metaUrl: new URL("population.json", DATA).href,
    binUrl: new URL("population.bin.gz", DATA).href });
}

start();
