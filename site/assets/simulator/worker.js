// Tax simulator worker: holds the population and scores specs off the main
// thread, so the page stays responsive while the kernel runs.
import { decodePopulation, parseSpec, resolveSpec, score } from "./kernel.js";
import { systemsWithoutGainsRate } from "./plan.js";

let pop = null;

async function fetchGzip(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`could not load ${url} (${r.status})`);
  if (typeof DecompressionStream === "undefined") {
    throw new Error("this browser cannot decompress the model data (no DecompressionStream)");
  }
  return new Response(r.body.pipeThrough(new DecompressionStream("gzip"))).arrayBuffer();
}

self.onmessage = async (event) => {
  const m = event.data;
  if (m.type === "init") {
    try {
      const [meta, buffer] = await Promise.all([
        fetch(m.metaUrl).then((r) => {
          if (!r.ok) throw new Error(`could not load ${m.metaUrl} (${r.status})`);
          return r.json();
        }),
        fetchGzip(m.binUrl),
      ]);
      pop = decodePopulation(meta, buffer);
      const { arrays, ...rest } = meta;   // the array index stays here
      self.postMessage({ type: "ready", meta: rest });
    } catch (err) {
      self.postMessage({ type: "fatal", message: String(err.message || err) });
    }
    return;
  }
  if (m.type === "score") {
    try {
      const parsed = parseSpec(m.spec, pop.meta);
      const systems = resolveSpec(parsed, pop.meta);
      const t0 = performance.now();
      const result = score(pop, systems, { distributionYears: [m.distYear] });
      // A plan that changes both the capital gains rate and the rest of the
      // income tax: the same plan at current law's gains rate, middle
      // scenario, for the "of which, capital gains rate" row.
      const rest = systemsWithoutGainsRate(parsed, pop.meta);
      const withoutGains = rest ? score(pop, rest, { scenarios: ["mid"] }).revenue : null;
      // the spec and year go back with the result, so the page's downloads
      // pair these numbers with the plan that produced them
      self.postMessage({ type: "result", id: m.id, result, systems, withoutGains, spec: m.spec, distYear: m.distYear,
        ms: performance.now() - t0 });
    } catch (err) {
      self.postMessage({ type: "invalid", id: m.id, message: String(err.message || err) });
    }
  }
};
