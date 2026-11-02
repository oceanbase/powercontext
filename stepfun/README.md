# 端侧知识库 · seekdb 混合检索

浏览器本地完成向量化，seekdb 负责存储与混合检索的迷你知识库应用。

- 文档向量化与查询向量化都在浏览器里跑（Transformers.js + ONNX Runtime Web），**不调用任何云端推理接口**
- 向量、原文与元数据写入 seekdb 集合；向量检索与全文检索的融合排序由**数据库内一条 SQL**完成
- 默认优先 WebGPU（FP16 权重），不支持时自动降级到 WASM（INT8 权重），界面顶部显示当前后端

## 1. 前置条件

| 项 | 要求 |
| --- | --- |
| Node.js | ≥ 20（实测 22.22） |
| 浏览器 | Chrome / Edge 最新版（WebGPU）；其他浏览器自动走 WASM |
| seekdb 服务端 | **必须使用 `2026-competition` 分支构建的 seekdb**（见下方说明） |

> **关于 seekdb 版本**：seekdb-js 把集合存放在内部表 `c$v2$<collection id>` 中，混合检索语句直接读取该表。
> 官方 1.4.0 发行包不包含集合表能力，会报 `Table doesn't exist`；服务启动时会自动做一次能力探测，
> 若不支持会在日志里明确提示。构建方式见 `scripts/start-seekdb.sh`。

## 2. 启动

```bash
# 1) 启动 seekdb（已有实例可跳过，直接改端口）
bash scripts/start-seekdb.sh /path/to/seekdb/build_release/src/observer/seekdb 2881

# 2) 启动应用
cd stepfun
npm install
SEEKDB_PORT=2881 npm start
# 打开 http://127.0.0.1:8787
```

环境变量：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `SEEKDB_HOST` / `SEEKDB_PORT` | `127.0.0.1` / `2881` | seekdb 服务地址 |
| `SEEKDB_USER` / `SEEKDB_PASSWORD` | `root` / 空 | 账号密码 |
| `SEEKDB_DATABASE` | `test` | 数据库名 |
| `PORT` | `8787` | 应用端口 |
| `MODEL_HOSTS` | `https://hf-mirror.com/,https://huggingface.co/` | 模型文件下载源 |

首次启动时模型文件由服务端代理下载并缓存到 `stepfun/models/`，之后可离线运行；
模型权重不会进入浏览器以外的网络请求，也不会有任何推理流量出本机。

## 3. 使用

1. 点「载入内置语料（50 条）」或粘贴/选择自己的文本（每行一段，或 JSONL）。
2. 点「向量化并写入」：浏览器本地批量编码 → 写入 seekdb → 刷新索引。
3. 输入查询，点「检索」：查询向量在本地生成 → seekdb 混合检索 → 展示排序结果与原文。

「清空知识库」会删除集合；重复导入同一份语料是幂等的（写入用 upsert）。

## 4. 复现实验数据

```bash
# 三种配置的完整对照（模型加载、向量化、写入、刷新、检索、端到端）
node scripts/evaluate.mjs --label webgpu              # FP16 + WebGPU
node scripts/evaluate.mjs --wasm true --label wasm-int8   # INT8 + WASM
node scripts/evaluate.mjs --wasm true --dtype fp32 --label wasm-fp32  # FP32 基线

# 量化精度漂移（同一批文本在 FP32 / INT8 下的向量余弦与纯向量召回）
node scripts/compare-precision.mjs
```

结果写入 `stepfun/results/*.json`，汇总见 [TECHNICAL.md](./TECHNICAL.md)。

## 5. 目录

```
stepfun/
├── server/index.mjs        # 静态服务 + seekdb-js 写入与混合检索 + 模型文件代理缓存
├── web/                    # 前端：Transformers.js、WebGPU/WASM 选择、批量向量化、检索界面
├── data/corpus.jsonl       # 内置语料 50 条
├── data/queries.jsonl      # 评测查询 20 条（含标准答案）
├── scripts/evaluate.mjs    # 端到端评测（真实浏览器）
├── scripts/compare-precision.mjs  # 量化精度对比
├── scripts/start-seekdb.sh # 本地 seekdb 启动脚本
├── results/                # 评测输出
├── README.md
└── TECHNICAL.md            # 一页技术说明
```
