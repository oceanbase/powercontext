// Browser side: everything that touches the model runs here. The server only
// stores vectors and runs the hybrid query.

const MODEL_ID = "Xenova/bge-small-zh-v1.5";
const MODEL_LABEL = "bge-small-zh-v1.5";
const COLLECTION = "kb_stepfun_demo";
const DIMENSION = 512;

const CDN_CANDIDATES = [
  "https://cdn.jsdelivr.net/npm/@huggingface/transformers@3.7.5",
  "https://unpkg.com/@huggingface/transformers@3.7.5",
  "https://esm.sh/@huggingface/transformers@3.7.5",
];

const el = (id) => document.getElementById(id);
const state = {
  extractor: null,
  backend: null,
  dtype: null,
  ready: false,
  docs: 0,
  metrics: { modelLoadMs: 0, embedMs: 0, writeMs: 0, refreshMs: 0, searchMs: 0, e2eMs: 0 },
};

function setBadge(node, text, tone) {
  node.textContent = text;
  node.className = `badge ${tone || ""}`.trim();
}

function metricCard(label, value) {
  return `<div class="metric"><b>${value}</b><span>${label}</span></div>`;
}

function renderMetrics(target, items) {
  target.innerHTML = items.map(([label, value]) => metricCard(label, value)).join("");
}

function setProgress(ratio, text) {
  el("progress-bar").style.width = `${Math.max(0, Math.min(1, ratio)) * 100}%`;
  if (text) el("progress-text").textContent = text;
}

async function loadTransformers() {
  const errors = [];
  for (const url of CDN_CANDIDATES) {
    try {
      return await import(/* @vite-ignore */ url);
    } catch (err) {
      errors.push(`${url}: ${err.message}`);
    }
  }
  throw new Error(`无法加载 transformers.js\n${errors.join("\n")}`);
}

async function detectBackend() {
  // The backend and the weight precision can be forced from the query string,
  // which is what the comparison runs use (fp16+WebGPU vs q8+WASM vs fp32).
  const params = new URLSearchParams(location.search);
  const forcedDevice = params.get("device");
  const forcedDtype = params.get("dtype");
  if (forcedDevice === "wasm" || forcedDtype === "q8" || forcedDtype === "int8") {
    return { device: "wasm", dtype: forcedDtype || "q8", reason: "由 URL 参数指定" };
  }
  if (!("gpu" in navigator)) return { device: "wasm", dtype: "q8", reason: "navigator.gpu 不存在" };
  try {
    const adapter = await navigator.gpu.requestAdapter();
    if (!adapter) return { device: "wasm", dtype: "q8", reason: "requestAdapter() 返回空" };
    return { device: "webgpu", dtype: forcedDtype || "fp16", reason: "拿到 GPU adapter" };
  } catch (err) {
    return { device: "wasm", dtype: forcedDtype || "q8", reason: `requestAdapter 失败：${err.message}` };
  }
}

async function buildExtractor(env, pipeline, device, dtype) {
  return pipeline("feature-extraction", MODEL_ID, {
    device,
    dtype,
    progress_callback: (p) => {
      if (p.status === "progress" && p.total) {
        setProgress(p.progress / 100, `下载 ${p.file} ${p.progress.toFixed(0)}%`);
      } else if (p.status === "ready") {
        setProgress(1, "模型就绪");
      }
    },
  });
}

async function initModel() {
  const started = performance.now();
  const { env, pipeline } = await loadTransformers();
  env.allowLocalModels = true;
  env.localModelPath = "/models/";
  env.allowRemoteModels = true;
  // Model files go through this app's own server: same origin (no CORS
  // dependency on the model host) and cached on disk after the first download.
  env.remoteHost = `${location.origin}/hf/`;
  env.useBrowserCache = true;

  const picked = await detectBackend();
  let extractor = null;
  let backend = picked;
  try {
    extractor = await buildExtractor(env, pipeline, picked.device, picked.dtype);
  } catch (err) {
    console.warn("primary backend failed, falling back to wasm/q8", err);
    backend = { device: "wasm", dtype: "q8", reason: `主后端失败：${err.message}` };
    extractor = await buildExtractor(env, pipeline, "wasm", "q8");
  }

  state.extractor = extractor;
  state.backend = backend.device;
  state.dtype = backend.dtype;
  state.metrics.modelLoadMs = performance.now() - started;
  state.ready = true;

  const gpu = backend.device === "webgpu";
  setBadge(el("backend-badge"), gpu ? "推理后端：WebGPU · FP16" : "推理后端：WASM · INT8", gpu ? "badge-gpu" : "badge-warn");
  el("model-line").textContent = `模型：${MODEL_LABEL} · ${backend.dtype.toUpperCase()} · ${backend.device.toUpperCase()}（${backend.reason}）`;

  // warm up so the first real query does not pay kernel compilation
  const warmStart = performance.now();
  await state.extractor(["预热"], { pooling: poolingFor(MODEL_ID), normalize: true });
  el("status-line").textContent = `模型加载 ${state.metrics.modelLoadMs.toFixed(0)} ms · 预热 ${(performance.now() - warmStart).toFixed(0)} ms`;
}

function poolingFor(modelId) {
  return modelId.includes("bge") ? "cls" : "mean";
}

async function embed(texts, onProgress) {
  const vectors = [];
  const batch = Math.max(1, Number(el("batch").value) || 16);
  const started = performance.now();
  for (let i = 0; i < texts.length; i += batch) {
    const slice = texts.slice(i, i + batch);
    const out = await state.extractor(slice, { pooling: poolingFor(MODEL_ID), normalize: true });
    const list = out.tolist();
    for (const row of list) vectors.push(row);
    onProgress?.(Math.min(1, (i + slice.length) / texts.length), i + slice.length, texts.length);
  }
  return { vectors, ms: performance.now() - started };
}

function parseCorpus(raw) {
  const trimmed = raw.trim();
  if (!trimmed) return [];
  if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
    const items = trimmed.startsWith("[") ? JSON.parse(trimmed) : trimmed.split(/\n+/).map((l) => JSON.parse(l));
    return items.map((item, index) => ({
      id: String(item.id ?? `doc-${Date.now()}-${index}`),
      title: item.title ?? "",
      text: item.text ?? item.content ?? "",
      category: item.category ?? "",
    }));
  }
  return trimmed
    .split(/\n+/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line, index) => ({ id: `doc-${Date.now()}-${index}`, title: "", text: line, category: "" }));
}

async function ingest() {
  const docs = parseCorpus(el("corpus-input").value);
  if (!docs.length) return setProgress(0, "没有可导入的文本");
  el("ingest").disabled = true;
  try {
    setProgress(0, `向量化 0/${docs.length}`);
    const { vectors, ms: embedMs } = await embed(
      docs.map((d) => (d.title ? `${d.title}。${d.text}` : d.text)),
      (ratio, done, total) => setProgress(ratio, `向量化 ${done}/${total}`),
    );
    setProgress(1, `写入 seekdb…`);
    const started = performance.now();
    const resp = await fetch("/api/ingest", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        collection: COLLECTION,
        dimension: vectors[0].length,
        batchSize: Number(el("batch").value) || 32,
        docs: docs.map((d, i) => ({
          id: d.id,
          text: d.text,
          title: d.title,
          metadata: { category: d.category || "未分类" },
          vector: vectors[i],
        })),
      }),
    });
    const data = await resp.json();
    if (!data.ok) throw new Error(data.error || "写入失败");
    const roundTripMs = performance.now() - started;
    state.docs = data.total;
    state.metrics.embedMs = embedMs;
    state.metrics.writeMs = data.writeMs;
    state.metrics.refreshMs = data.refreshMs;
    setBadge(el("doc-badge"), `${data.total} 条文档`, "badge-ok");
    setProgress(1, `完成：${data.written} 条写入，集合共 ${data.total} 条`);
    renderMetrics(el("ingest-metrics"), [
      ["向量化耗时", `${embedMs.toFixed(0)} ms`],
      ["吞吐", `${(docs.length / (embedMs / 1000)).toFixed(1)} 条/秒`],
      ["写入 seekdb", `${data.writeMs.toFixed(0)} ms`],
      ["索引刷新", `${data.refreshMs.toFixed(0)} ms`],
      ["往返总耗时", `${roundTripMs.toFixed(0)} ms`],
    ]);
  } catch (err) {
    setProgress(0, `失败：${err.message}`);
  } finally {
    el("ingest").disabled = false;
  }
}

async function search() {
  const query = el("query").value.trim();
  if (!query || !state.ready) return;
  el("search").disabled = true;
  const started = performance.now();
  try {
    const { vectors, ms: embedMs } = await embed([query]);
    const dbStart = performance.now();
    const resp = await fetch("/api/search", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        collection: COLLECTION,
        vector: vectors[0],
        text: query,
        k: Number(el("topk").value) || 5,
      }),
    });
    const data = await resp.json();
    if (!data.ok) throw new Error(data.error || "检索失败");
    const dbMs = performance.now() - dbStart;
    const e2eMs = performance.now() - started;
    state.metrics.searchMs = dbMs;
    state.metrics.e2eMs = e2eMs;
    renderResults(data.hits);
    renderMetrics(el("search-metrics"), [
      ["查询向量化", `${embedMs.toFixed(1)} ms`],
      ["seekdb 混合检索", `${data.tookMs.toFixed(1)} ms`],
      ["端到端", `${e2eMs.toFixed(1)} ms`],
      ["命中", `${data.hits.length} 条`],
    ]);
    el("status-line").textContent = `混合检索由 seekdb 单条语句完成 · ${new Date().toLocaleTimeString()}`;
  } catch (err) {
    el("results").innerHTML = `<li class="text">检索失败：${err.message}</li>`;
  } finally {
    el("search").disabled = false;
  }
}

function renderResults(hits) {
  const list = el("results");
  if (!hits.length) {
    list.innerHTML = `<li class="text">没有命中，试试换个说法或先导入语料。</li>`;
    return;
  }
  list.innerHTML = hits
    .map((hit, index) => {
      const score = hit.distance == null ? "" : `<span class="tag">score ${Number(hit.distance).toFixed(4)}</span>`;
      const category = hit.metadata?.category ? `<span class="tag">${escapeHtml(hit.metadata.category)}</span>` : "";
      return `<li>
        <div class="meta"><span>#${index + 1}</span><span>${escapeHtml(hit.id)}</span>${category}${score}</div>
        <div class="title">${escapeHtml(hit.metadata?.title || "（无标题）")}</div>
        <div class="text">${escapeHtml(hit.text)}</div>
      </li>`;
    })
    .join("");
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]);
}

async function refreshHealth() {
  try {
    const resp = await fetch("/api/health");
    const data = await resp.json();
    if (data.ok) {
      setBadge(el("db-badge"), `seekdb ${data.seekdb.version || ""}`.trim(), "badge-ok");
    } else {
      setBadge(el("db-badge"), "seekdb 不可用", "badge-bad");
    }
  } catch {
    setBadge(el("db-badge"), "seekdb 不可用", "badge-bad");
  }
}

el("load-sample").addEventListener("click", async () => {
  const resp = await fetch("/data/corpus.jsonl");
  el("corpus-input").value = await resp.text();
  setProgress(0, `已载入 ${el("corpus-input").value.trim().split(/\n+/).length} 行语料`);
});

el("file-input").addEventListener("change", async (event) => {
  const file = event.target.files?.[0];
  if (!file) return;
  el("corpus-input").value = await file.text();
  setProgress(0, `已读取 ${file.name}`);
});

el("ingest").addEventListener("click", ingest);
el("search").addEventListener("click", search);
el("query").addEventListener("keydown", (event) => {
  if (event.key === "Enter") search();
});
el("reset").addEventListener("click", async () => {
  await fetch("/api/reset", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ collection: COLLECTION }),
  });
  state.docs = 0;
  setBadge(el("doc-badge"), "0 条文档");
  el("results").innerHTML = "";
  setProgress(0, "知识库已清空");
});

window.__kb = {
  state,
  ingest,
  search,
  embed,
  setQuery: (value) => {
    el("query").value = value;
  },
  setCorpus: (value) => {
    el("corpus-input").value = value;
  },
};

refreshHealth();
initModel().catch((err) => {
  setBadge(el("backend-badge"), "模型加载失败", "badge-bad");
  setProgress(0, `模型加载失败：${err.message}`);
});
