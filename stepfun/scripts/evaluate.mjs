// Drives the real page in Chrome, so every number in TECHNICAL.md comes from an
// actual run: model download/load, document embedding, seekdb write, index
// refresh, query embedding, hybrid search and end-to-end latency.
//
//   node scripts/evaluate.mjs [--url http://127.0.0.1:8787] [--chrome <path>]
import { mkdir, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import puppeteer from "puppeteer-core";

const HERE = dirname(fileURLToPath(import.meta.url));
const args = Object.fromEntries(
  process.argv.slice(2).reduce((acc, cur, i, arr) => {
    if (cur.startsWith("--")) acc.push([cur.slice(2), arr[i + 1]]);
    return acc;
  }, []),
);
const URL_BASE = args.url || "http://127.0.0.1:8787";
const CHROME =
  args.chrome ||
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
// --wasm forces the fallback path: WebGPU is disabled in the browser profile.
const FORCE_WASM = args.wasm === "true" || args.wasm === "1";
const DTYPE = args.dtype || null;
const LABEL = args.label || (FORCE_WASM ? "wasm" : "default");
const PAGE_URL = `${URL_BASE}/${DTYPE ? `?device=${FORCE_WASM ? "wasm" : "webgpu"}&dtype=${DTYPE}` : ""}`;

const launchArgs = ["--no-sandbox", "--disable-dev-shm-usage"];
if (FORCE_WASM) {
  launchArgs.push("--disable-features=WebGPU", "--disable-gpu");
} else {
  launchArgs.push("--enable-unsafe-webgpu", "--enable-features=Vulkan");
}

const browser = await puppeteer.launch({
  executablePath: CHROME,
  headless: true,
  protocolTimeout: 900000,
  args: launchArgs,
});

const page = await browser.newPage();
page.on("console", (msg) => {
  if (msg.type() === "error") console.error("[page error]", msg.text().slice(0, 300));
});

console.log(`opening ${PAGE_URL}`);
await page.goto(PAGE_URL, { waitUntil: "domcontentloaded", timeout: 120000 });

await page.waitForFunction(() => window.__kb && window.__kb.state.ready === true, { timeout: 600000 });
const backend = await page.evaluate(() => ({
  device: window.__kb.state.backend,
  dtype: window.__kb.state.dtype,
  modelLoadMs: window.__kb.state.metrics.modelLoadMs,
}));
console.log("backend:", JSON.stringify(backend));

// import the bundled corpus
const corpus = await (await fetch(`${URL_BASE}/data/corpus.jsonl`)).text();
const queries = (await (await fetch(`${URL_BASE}/data/queries.jsonl`)).text())
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line));

await page.evaluate((text) => window.__kb.setCorpus(text), corpus);
console.log(`embedding ${corpus.trim().split("\n").length} documents in the browser`);
const ingestStarted = Date.now();
await page.evaluate(() => window.__kb.ingest());
const ingestWall = Date.now() - ingestStarted;
const ingestMetrics = await page.evaluate(() => window.__kb.state.metrics);
const docCount = await page.evaluate(async () => {
  const resp = await fetch("/api/health");
  const data = await resp.json();
  return data.collections?.length ?? 0;
});
console.log("ingest done in", ingestWall, "ms; metrics:", JSON.stringify(ingestMetrics));

// warm up the query path, then measure
const evaluation = await page.evaluate(async (queryList) => {
  const kb = window.__kb;
  const run = async (q, k) => {
    const started = performance.now();
    const { vectors, ms: embedMs } = await kb.embed([q]);
    const dbStart = performance.now();
    const resp = await fetch("/api/search", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ collection: "kb_stepfun_demo", vector: vectors[0], text: q, k }),
    });
    const data = await resp.json();
    const dbMs = performance.now() - dbStart;
    return { ids: data.hits.map((h) => h.id), embedMs, dbMs, sqlMs: data.tookMs, e2eMs: performance.now() - started };
  };

  await run("预热查询", 5);

  const perQuery = [];
  for (const item of queryList) {
    const runs = [];
    for (let i = 0; i < 3; i += 1) runs.push(await run(item.query, 5));
    const last = runs[runs.length - 1];
    const hits = last.ids;
    const gold = item.gold;
    const recall = gold.filter((g) => hits.includes(g)).length / gold.length;
    let dcg = 0;
    let idcg = 0;
    gold.forEach((g, i) => {
      const rank = hits.indexOf(g);
      if (rank >= 0) dcg += 1 / Math.log2(rank + 2);
      idcg += 1 / Math.log2(i + 2);
    });
    perQuery.push({
      query: item.query,
      gold,
      hits,
      recallAt5: recall,
      ndcgAt5: idcg ? dcg / idcg : 0,
      embedMs: last.embedMs,
      dbMs: last.dbMs,
      sqlMs: last.sqlMs,
      e2eMs: last.e2eMs,
      runs: runs.map((r) => r.e2eMs),
    });
  }
  return perQuery;
}, queries);

const median = (values) => {
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
};

const summary = {
  backend,
  documents: corpus.trim().split("\n").length,
  queries: evaluation.length,
  recallAt5: evaluation.reduce((s, q) => s + q.recallAt5, 0) / evaluation.length,
  ndcgAt5: evaluation.reduce((s, q) => s + q.ndcgAt5, 0) / evaluation.length,
  medianQueryEmbedMs: median(evaluation.map((q) => q.embedMs)),
  medianHybridSearchMs: median(evaluation.map((q) => q.sqlMs)),
  medianEndToEndMs: median(evaluation.map((q) => q.e2eMs)),
  ingest: { ...ingestMetrics, wallMs: ingestWall, collections: docCount },
  perQuery: evaluation,
};

await mkdir(resolve(HERE, "../results"), { recursive: true });
await writeFile(resolve(HERE, `../results/evaluation-${LABEL}.json`), JSON.stringify(summary, null, 2));
console.log(JSON.stringify({ ...summary, perQuery: undefined }, null, 2));

await browser.close();
