// Extract Maui County conveyance sales for forecast_conveyance_sb3028.py.
//
// Maui's public "RPT Sales Data File" has every recorded conveyance with its
// price and the conveyance tax paid. The county site blocks scripted
// downloads, so this runs in a browser: open
//   https://www.mauicounty.gov/DocumentCenter/Index/229
// paste this whole file into the developer console, and it saves three CSVs
// (your browser's download folder). Copy them to
//   packages/tax_modeler/src/tax_modeler/data/raw/conveyance/
//
// What it does, per recorded document (the sales file has one row per parcel;
// a multi-parcel deed repeats its price and tax on each row):
//   1. Groups rows by instrument number (INSTRUNO).
//   2. Fixes transposed fields: when "tax" exceeds "price" they are swapped
//      (e.g. a $19.5M lease recorded as price 195,000 / tax 19,500,016).
//   3. Identifies the conveyance tax schedule actually applied by matching
//      tax / price to HRS §247-2 (1) or (2) within 0.5%: (2) is a condo or
//      single-family sale to a purchaser ineligible for the homeowner
//      exemption ("nonowner"); (1) on residential parcels (assessment land
//      use 1-2) is "owner"; (1) elsewhere, or no match, is "nonres".
//   4. Bins sales of $600K+ at edges that include every break point of both
//      the current and SB 3028 HD2 schedules, so bin count and summed price
//      reproduce either law's tax exactly. Sales of $10M+ are listed singly.
//
// Output files:
//   maui_sales_bins_fy2023_2026.csv     fy,category,bin_lo,count,sum_price
//   maui_sales_over_10m_fy2023_2026.csv fy,category,price
//   maui_sales_totals_fy2016_2026.csv   fy,category,count,sum_price,sum_tax_recorded
//
// And, for the statewide housing-stock method (forecast_conveyance_sb3028.py
// stock_method), each record of the assessment listing (land class 1, 2,
// 10-12; value = land + building; owner-occupied = tax rate class 9 with an
// exemption of $300,000 or less; fully exempt records dropped; improved =
// building value > 0) and every FY2023-26 home sale joined to it:
//   maui_residential_stock_2026.csv     value_lo,owner_occupied,improved,count,sum_value
//   maui_sales_by_value_fy2023_2026.csv fy,category,value_lo,owner_occupied_now,improved,count,sum_price,sum_value
//                                       (arm's-length only: price 0.5-2.5x assessed value)
//   maui_multifamily_sales_fy2023_2026.csv fy,category,price,units
// Buildings with 5+ dwelling units (State GIS dwelling layer) and non-condo
// records worth $20M+ are left out of the first two; the 5+ unit sales are
// listed in the third, which the forecast scores under HD2's per-unit rule.
(async () => {
  const unzip = async (url) => {
    const buf = new Uint8Array(await (await fetch(url)).arrayBuffer());
    const dv = new DataView(buf.buffer);
    let eocd = -1;
    for (let i = buf.length - 22; i >= 0; i--) if (dv.getUint32(i, true) === 0x06054b50) { eocd = i; break; }
    let p = dv.getUint32(eocd + 16, true); const n = dv.getUint16(eocd + 10, true); const out = {};
    for (let k = 0; k < n; k++) {
      const csize = dv.getUint32(p + 20, true), nl = dv.getUint16(p + 28, true), el = dv.getUint16(p + 30, true),
            cl = dv.getUint16(p + 32, true), lho = dv.getUint32(p + 42, true), method = dv.getUint16(p + 10, true);
      const name = new TextDecoder().decode(buf.slice(p + 46, p + 46 + nl)); p += 46 + nl + el + cl;
      if (!/\.(csv|txt)$/.test(name)) continue;
      const start = lho + 30 + dv.getUint16(lho + 26, true) + dv.getUint16(lho + 28, true);
      const comp = buf.slice(start, start + csize);
      out[name] = method === 0 ? new TextDecoder().decode(comp)
        : await new Response(new Blob([comp]).stream().pipeThrough(new DecompressionStream('deflate-raw'))).text();
    }
    return out;
  };
  const sales = (await unzip('/DocumentCenter/View/8070/RPT-Sales-Data-File-'))['sales.csv'];
  const assess = (await unzip('/DocumentCenter/View/8079/RPT-Full-Assessment-Listing-as-of-4062026'))['fullasmt26.txt'];

  // Parcel -> land-use codes (fixed width: TMK in cols 1-12, land use at col 18).
  const landuse = new Map();
  assess.split(/\r?\n/).filter(l => l.length > 20).forEach(l => {
    const par = l.slice(1, 13); if (!landuse.has(par)) landuse.set(par, []); landuse.get(par).push(l.slice(18, 19).trim()); });

  const lines = sales.split(/\r?\n/); const hdr = lines[1].split(',').map(s => s.trim());
  const I = Object.fromEntries(hdr.map((h, i) => [h, i]));
  const docs = new Map();
  for (let i = 2; i < lines.length; i++) {
    const f = lines[i].split(',').map(s => s.trim()); if (f.length < hdr.length) continue;
    const rd = f[I.RECORDDATE]; if (!/^\d{4}\/\d{2}/.test(rd)) continue;
    const key = f[I.INSTRUNO] || (rd + '|' + f[I.PARID]);
    const d = docs.get(key) || {rd, parids: [], taxes: [], prices: []};
    d.parids.push(f[I.PARID]); d.taxes.push(parseFloat(f[I.CONV_TAX])); d.prices.push(parseFloat(f[I.PRICE])); docs.set(key, d);
  }
  const s1 = [[6e5,.001],[1e6,.002],[2e6,.003],[4e6,.005],[6e6,.007],[1e7,.009],[Infinity,.010]];
  const s2 = [[6e5,.0015],[1e6,.0025],[2e6,.004],[4e6,.006],[6e6,.0085],[1e7,.011],[Infinity,.0125]];
  const rate = (t, v) => t.find(x => v < x[0])[1];
  const within = (t, a) => Math.abs(t - a) <= Math.max(2, .005 * a);
  const one = arr => new Set(arr.map(String)).size === 1 ? arr[0] : arr.filter(x => x > 0).reduce((a, b) => a + b, 0);
  const edges = [6e5, 8e5, 1e6];
  for (let x = 1.25e6; x <= 6e6 + 1; x += 2.5e5) edges.push(Math.round(x));
  for (let x = 6.5e6; x <= 1e7 + 1; x += 5e5) edges.push(Math.round(x));
  const binOf = v => { if (v < 6e5) return 0; let lo = 6e5; for (const e of edges) { if (v >= e) lo = e; else break; } return lo; };

  const bins = new Map(), tops = [], totals = new Map();
  for (const d of docs.values()) {
    let t = one(d.taxes), v = one(d.prices);
    if (!(t > 0 && v > 0)) continue;
    if (t > v) [t, v] = [v, t];
    const [y, m] = d.rd.split('/').map(Number); const fy = m >= 7 ? y + 1 : y;
    if (fy < 2016 || fy > 2026) continue;
    const sched = within(t, v * rate(s1, v)) ? 1 : within(t, v * rate(s2, v)) ? 2 : 0;
    const residential = d.parids.every(p => ['1', '2'].includes((landuse.get(p) || ['?'])[0]));
    const cat = sched === 2 ? 'nonowner' : sched === 1 && residential ? 'owner' : sched === 1 ? 'nonres' : 'unmatched';
    const tk = fy + ',' + cat; const tt = totals.get(tk) || [0, 0, 0]; tt[0]++; tt[1] += v; tt[2] += t; totals.set(tk, tt);
    if (fy < 2023) continue;
    const c = cat === 'unmatched' ? 'nonres' : cat;     // no schedule match: treated as nonresidential
    if (v >= 1e7) { tops.push([fy, c, Math.round(v)].join(',')); continue; }
    const k = fy + ',' + c + ',' + binOf(v); const b = bins.get(k) || [0, 0]; b[0]++; b[1] += v; bins.set(k, b);
  }
  const save = (name, text) => { const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([text], {type: 'text/csv'})); a.download = name; a.click(); };
  save('maui_sales_bins_fy2023_2026.csv', 'fy,category,bin_lo,count,sum_price\n' +
    [...bins.entries()].sort().map(([k, b]) => k + ',' + b[0] + ',' + Math.round(b[1])).join('\n') + '\n');
  save('maui_sales_over_10m_fy2023_2026.csv', 'fy,category,price\n' + tops.join('\n') + '\n');
  save('maui_sales_totals_fy2016_2026.csv', 'fy,category,count,sum_price,sum_tax_recorded\n' +
    [...totals.entries()].sort().map(([k, b]) => k + ',' + b.map(Math.round).join(',')).join('\n') + '\n');
  console.log('saved', bins.size, 'bins,', tops.length, '$10M+ sales,', totals.size, 'fiscal-year totals');

  // ---- Housing stock and sales by assessed value (see header).
  // Assessment layout (data_FULLASMT.pdf in the zip): TMK cols 1-13, land class 19-22,
  // tax rate class 23-26, land value 27-39, land exemption 40-52, building value 53-65,
  // building exemption 66-78.
  const units = new Map();
  for (let off = 0; ; ) {
    const q = new URLSearchParams({where: "county='Maui' AND COUNT_Units>=5", outFields: 'TMK,COUNT_Units',
      returnGeometry: 'false', resultOffset: off, resultRecordCount: 2000, orderByFields: 'OBJECTID', f: 'json'});
    const d = await (await fetch('https://services1.arcgis.com/x4h61KaW16vFs7PM/arcgis/rest/services/tmk_state_2025_dwelling_data/FeatureServer/0/query?' + q)).json();
    (d.features || []).forEach(x => units.set(String(x.attributes.TMK).slice(1), x.attributes.COUNT_Units));
    if (!(d.features || []).length || !d.exceededTransferLimit) break; off += d.features.length;
  }
  const RES = new Set(['1', '2', '10', '11', '12']); const par = new Map();
  assess.split(/\r?\n/).filter(l => l.length >= 79).forEach(l => {
    const k = l.slice(1, 13), land = l.slice(18, 22).trim(), trc = l.slice(22, 26).trim();
    const lv = +l.slice(26, 39), le = +l.slice(39, 52), bv = +l.slice(52, 65), be = +l.slice(65, 78);
    const p = par.get(k) || {v: 0, b: 0, ex: 0, h: false, res: false};
    p.v += lv + bv; p.b += bv; p.ex += le + be; if (trc === '9') p.h = true; if (RES.has(land)) p.res = true; par.set(k, p); });
  for (const [k, p] of par) {
    p.full = p.v > 0 && p.ex >= p.v; p.owner = p.h && p.ex > 0 && p.ex <= 3e5 && !p.full; p.imp = p.b > 0;
    const cpr0 = k.slice(8) === '0000'; p.units = cpr0 ? (units.get(k.slice(0, 8)) || 0) : 0;
    p.mf = p.units >= 5; p.big = cpr0 && !p.mf && p.v >= 2e7; }
  const vedges = [0, 3e5, 6e5, 8e5, 1e6]; for (let x = 1.25e6; x <= 6e6 + 1; x += 2.5e5) vedges.push(Math.round(x));
  for (let x = 6.5e6; x <= 1e7 + 1; x += 5e5) vedges.push(Math.round(x)); vedges.push(12.5e6, 15e6, 20e6, 30e6);
  const vbin = v => { let lo = 0; for (const e of vedges) { if (v >= e) lo = e; else break; } return lo; };
  const stock = new Map();
  for (const p of par.values()) { if (!p.res || p.full || p.mf || p.big) continue;
    const k = [vbin(p.v), +p.owner, +p.imp].join(','); const s = stock.get(k) || [0, 0]; s[0]++; s[1] += p.v; stock.set(k, s); }
  const byVal = new Map(), mfSales = [];
  for (const d of docs.values()) {
    let t = one(d.taxes), v = one(d.prices); if (!(t > 0 && v > 0)) continue; if (t > v) [t, v] = [v, t];
    const [y, m] = d.rd.split('/').map(Number); const fy = m >= 7 ? y + 1 : y; if (fy < 2023 || fy > 2026) continue;
    const sched = within(t, v * rate(s1, v)) ? 1 : within(t, v * rate(s2, v)) ? 2 : 0;
    const ps = [...new Set(d.parids)].map(q => par.get(q)); const known = ps.filter(Boolean);
    const residential = known.length === ps.length && known.every(q => q.res);
    const cat0 = sched === 2 ? 'nonowner' : sched === 1 && residential ? 'owner' : 'nonres';
    const u = known.reduce((a, q) => a + (q.mf ? q.units : 0), 0);
    if (u >= 5) { mfSales.push([fy, cat0, Math.round(v), u].join(',')); continue; }
    if (cat0 === 'nonres' || known.length !== ps.length || known.some(q => q.full || q.big)) continue;
    const val = known.reduce((a, q) => a + q.v, 0); const r = val > 0 ? v / val : 99; if (r < 0.5 || r > 2.5) continue;
    const k = [fy, cat0, vbin(val), +known.some(q => q.owner), +known.some(q => q.imp)].join(',');
    const o = byVal.get(k) || [0, 0, 0]; o[0]++; o[1] += v; o[2] += val; byVal.set(k, o); }
  save('maui_residential_stock_2026.csv', 'value_lo,owner_occupied,improved,count,sum_value\n' +
    [...stock.entries()].map(([k, s]) => k + ',' + s[0] + ',' + Math.round(s[1])).join('\n') + '\n');
  save('maui_sales_by_value_fy2023_2026.csv', 'fy,category,value_lo,owner_occupied_now,improved,count,sum_price,sum_value\n' +
    [...byVal.entries()].sort().map(([k, o]) => k + ',' + o[0] + ',' + Math.round(o[1]) + ',' + Math.round(o[2])).join('\n') + '\n');
  save('maui_multifamily_sales_fy2023_2026.csv', 'fy,category,price,units\n' + mfSales.sort().join('\n') + '\n');
  console.log('saved', stock.size, 'stock cells,', byVal.size, 'sales-by-value cells,', mfSales.length, 'multifamily sales');
})();
