// Measures how much the quantised weights change the vectors, and how much
// that changes the retrieval quality, by running the same corpus through the
// FP32 and the INT8 model in the same browser.
//
//   node scripts/compare-precision.mjs
import { mkdir, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import puppeteer from "puppeteer-core";

const HERE = dirname(fileURLToPath(import.meta.url));
const URL_BASE = process.env.KB_URL || "http://127.0.0.1:8787";

const browser = await puppeteer.launch({
  executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless: true,
  protocolTimeout: 900000,
  args: ["--no-sandbox", "--disable-dev-shm-usage", "--disable-features=WebGPU", "--disable-gpu"],
});
const page = await browser.newPage();
page.on("pageerror", (err) => console.log("[pageerror]", String(err).slice(0, 200)));
await page.goto(`${URL_BASE}/?device=wasm&dtype=q8`, { waitUntil: "domcontentloaded", timeout: 120000 });
await page.waitForFunction(() => window.__kb && window.__kb.state.ready === true, { timeout: 900000 });

const corpus = await (await fetch(`${URL_BASE}/data/corpus.jsonl`)).text();
const queries = await (await fetch(`${URL_BASE}/data/queries.jsonl`)).text();

const result = await page.evaluate(
  async ({ corpusText, queryText }) => {
    const { env, pipeline } = await import("https://cdn.jsdelivr.net/npm/@huggingface/transformers@3.7.5");
    env.allowLocalModels = true;
    env.localModelPath = "/models/";
    env.allowRemoteModels = true;
    env.remoteHost = `${location.origin}/hf/`;
    env.useBrowserCache = true;

    const MODEL_ID = "Xenova/bge-small-zh-v1.5";
    const docs = corpusText
      .trim()
      .split("\n")
      .map((line) => JSON.parse(line));
    const texts = docs.map((d) => (d.title ? `${d.title}。${d.text}` : d.text));
    const queryList = queryText
      .trim()
      .split("\n")
      .map((line) => JSON.parse(line));

    const cosine = (a, b) => {
      let dot = 0;
      let na = 0;
      let nb = 0;
      for (let i = 0; i < a.length; i += 1) {
        dot += a[i] * b[i];
        na += a[i] * a[i];
        nb += b[i] * b[i];
      }
      return dot / (Math.sqrt(na) * Math.sqrt(nb));
    };

    const embedWith = async (dtype) => {
      const extractor = await pipeline("feature-extraction", MODEL_ID, { device: "wasm", dtype });
      const vectors = [];
      for (let i = 0; i < texts.length; i += 16) {
        const out = await extractor(texts.slice(i, i + 16), { pooling: "cls", normalize: true });
        vectors.push(...out.tolist());
      }
      const qVectors = [];
      for (let i = 0; i < queryList.length; i += 16) {
        const out = await extractor(queryList.slice(i, i + 16).map((q) => q.query), {
          pooling: "cls",
          normalize: true,
        });
        qVectors.push(...out.tolist());
      }
      return { vectors, qVectors };
    };

    const fp32 = await embedWith("fp32");
    const int8 = await embedWith("q8");

    const docCos = fp32.vectors.map((v, i) => cosine(v, int8.vectors[i]));
    const queryCos = fp32.qVectors.map((v, i) => cosine(v, int8.qVectors[i]));
    const mean = (xs) => xs.reduce((s, x) => s + x, 0) / xs.length;
    const min = (xs) => Math.min(...xs);

    // retrieval quality of each precision, computed on the vectors themselves
    const rank = (qv, corpusVectors) =>
      corpusVectors
        .map((v, i) => ({ id: docs[i].id, score: cosine(qv, v) }))
        .sort((a, b) => b.score - a.score);

    const recallAt = (k) => {
      const calc = (qVectors, corpusVectors) => {
        let hit = 0;
        let total = 0;
        queryList.forEach((item, i) => {
          const ranked = rank(qVectors[i], corpusVectors);
          const top = new Set(ranked.slice(0, k).map((r) => r.id));
          item.gold.forEach((g) => {
            total += 1;
            if (top.has(g)) hit += 1;
          });
        });
        return hit / total;
      };
      return {
        fp32: calc(fp32.qVectors, fp32.vectors),
        int8: calc(int8.qVectors, int8.vectors),
      };
    };

    return {
      documents: docs.length,
      queries: queryList.length,
      docCosineMean: mean(docCos),
      docCosineMin: min(docCos),
      queryCosineMean: mean(queryCos),
      queryCosineMin: min(queryCos),
      pureVectorRecallAt5: recallAt(5),
      pureVectorRecallAt3: recallAt(3),
    };
  },
  { corpusText: corpus, queryText: queries },
);

await mkdir(resolve(HERE, "../results"), { recursive: true });
await writeFile(resolve(HERE, "../results/precision-comparison.json"), JSON.stringify(result, null, 2));
console.log(JSON.stringify(result, null, 2));
await browser.close();
