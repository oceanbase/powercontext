# 端侧 AI 推理 + seekdb 混合检索：技术说明

> 全部数字来自本仓库 `scripts/evaluate.mjs` 与 `scripts/compare-precision.mjs` 的实测输出，
> 原始数据见 `results/`。测试设备：Apple M1 / 16 GB / macOS 15.7.3 / Chrome（无头，真实浏览器内核）。

## 1. 模型与量化

| 项 | 值 |
| --- | --- |
| 模型 | `Xenova/bge-small-zh-v1.5`（BAAI/bge-small-zh-v1.5 的 ONNX 版本，Apache-2.0） |
| 来源 | https://huggingface.co/Xenova/bge-small-zh-v1.5 （公开可获取） |
| 维度 | 512 |
| 池化 | CLS + L2 归一化 |
| 运行时 | Transformers.js 3.7.5（ONNX Runtime Web） |

**体积（实测，实际加载的文件口径，含权重 + 分词器 + 配置）**

| 精度 | 权重文件 | 总体积 | 相对 FP32 压缩比 |
| --- | --- | ---: | ---: |
| FP32 基线 | `onnx/model.onnx` | 95,292,085 B（90.88 MiB） | 1.00× |
| FP16 | `onnx/model_fp16.onnx` | 47,937,635 B（45.72 MiB） | **1.99×** |
| INT8 | `onnx/model_quantized.onnx` | 24,451,050 B（23.32 MiB） | **3.89×** |

分词器与配置合计 440,208 B，三种配置相同。

**量化对检索质量的影响（实测）**

| 指标 | FP32 | INT8 | 变化 |
| --- | ---: | ---: | ---: |
| 文档向量与 FP32 的余弦（均值 / 最小） | 1.0000 | 0.9891 / 0.9851 | −1.1% |
| 查询向量与 FP32 的余弦（均值 / 最小） | 1.0000 | 0.9910 / 0.9863 | −0.9% |
| 纯向量 Recall@3 / @5 | 0.7667 | 0.7667 | 0 |
| 混合检索 Recall@5 / nDCG@5 | 0.85 / 0.8625 | 0.85 / 0.8625 | 0 |

评测集：50 条中文语料、20 条查询（含标准答案），指标定义见 `data/queries.jsonl`。
结论：INT8 把体积压到 1/3.9，向量方向偏移约 1%，在本评测集上排序结果没有变化；
FP16 与 INT8 的排序结果同样一致。

## 2. 端侧运行与后端降级

- 后端选择：`navigator.gpu.requestAdapter()` 拿到适配器才启用 WebGPU，否则用 WASM。
  只检查 `navigator.gpu` 是否存在会误判，因此这里以适配器为准。
- 权重精度跟随后端：WebGPU → FP16，WASM → INT8。
- 降级路径可用 `?device=wasm&dtype=q8` 强制触发，界面顶部徽章始终显示当前后端与精度。
- 模型文件由本服务的 `/hf/...` 路由代理下载（避免模型源的 CORS 限制），并在 `stepfun/models/` 落盘缓存，
  第二次启动不再走网络；推理全过程不出本机。
- 页面响应头带 `Cross-Origin-Opener-Policy: same-origin` 与 `Cross-Origin-Embedder-Policy: require-corp`，
  使 WASM 后端可用多线程。

## 3. seekdb 使用

| 项 | 值 |
| --- | --- |
| 接入方式 | `seekdb-js`（npm `seekdb@1.4.0`，Server 模式，MySQL 协议） |
| 集合 | `kb_stepfun_demo`，维度 512，距离 `cosine` |
| 全文索引 | `ik` 分词器（`ik_mode=smart`），字段 `document` |
| 向量索引 | HNSW，字段 `embedding` |
| 元数据 | `metadata` JSON 列（`title`、`category`） |
| 写入 | `collection.upsert()` 分批写入（默认 32 条/批），保证重复导入幂等 |
| 刷新 | 写入批次结束后调用 `collection.refresh_index()`，刷新后新增内容立即可检索 |

**混合检索在数据库内完成**：`seekdb-js` 的 `hybridSearch()` 生成一条 SQL —— 全文分支
（`MATCH ... AGAINST` + `RANK()`）与向量分支（`cosine_distance`）`FULL JOIN`，
按 `1/(rank+60)` 的 RRF 融合打分。客户端只发送一条查询向量，不做两路结果的客户端拼合。

## 4. 状态与优化

- 集合与会话：服务端按「集合名 + 维度」缓存集合句柄；切换模型（维度变化）会落到不同集合，
  避免维度混用；「清空知识库」删除集合后会自动重建。
- 增量导入：重复导入只 upsert 命中的文档，未变更文档不重复向量化。
- 批处理：默认 16 条/批，可调。批越大吞吐越高，单批延迟越大。
- 预热：模型加载后先跑一次前向，避免首个真实查询承担内核编译开销。
- 首屏：模型文件由服务端缓存，浏览器二次访问命中 `Cache-Control`。

**分阶段计时（实测，中位数，50 条语料 / 20 条查询，各重复 3 次取中位数）**

| 配置 | 模型加载 | 文档向量化（50 条） | 写入 seekdb | 索引刷新 | 查询向量化 | 混合检索 | 端到端 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| FP16 + WebGPU | 11.8 s | 313 ms（160 条/秒） | 427 ms | 622 ms | 9.1 ms | 4.9 ms | **16.5 ms** |
| INT8 + WASM | 7.9 s | 1,446 ms（34.6 条/秒） | 225 ms | 512 ms | 12.3 ms | 8.9 ms | **23.5 ms** |
| FP32 + WASM（基线） | 272.6 s | 1,834 ms（27.3 条/秒） | 359 ms | 606 ms | 12.8 ms | 9.3 ms | **22.7 ms** |

（FP32 的模型加载含 90 MB 权重下载与更大的图编译；INT8 为量化方案，体积为其 1/3.9。）

**检索质量（同一评测集，同一混合检索语句）**

| 配置 | Recall@5 | nDCG@5 |
| --- | ---: | ---: |
| FP16 + WebGPU | 0.85 | 0.8625 |
| INT8 + WASM | 0.85 | 0.8625 |
| FP32 + WASM | 0.85 | 0.8625 |

**降级路径验证**：以 `--disable-features=WebGPU --disable-gpu` 启动 Chrome，页面徽章显示
「推理后端：WASM · INT8」，导入、写入、检索全链路正常，端到端 23.5 ms，
Recall@5 与 nDCG@5 与 WebGPU 配置完全一致。

## 5. 复现

```bash
npm install
SEEKDB_PORT=2881 npm start            # 应用
node scripts/evaluate.mjs --label webgpu
node scripts/evaluate.mjs --wasm true --label wasm-int8
node scripts/evaluate.mjs --wasm true --dtype fp32 --label wasm-fp32
node scripts/compare-precision.mjs
```
