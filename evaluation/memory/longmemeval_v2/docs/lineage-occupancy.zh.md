# Scope 内谱系集中度与近似重复占比评测

`longmemeval-v2 lineage-fixture` 提供 #1977 的独立 fixture 模式，使用真实 Atomic Memory FTS、精确历史证据读取和进程内 `PreparedContextBuilder`。它不修改生产排序、写入、生命周期、公共 API 或交付契约，不产生 Context Receipt，也不代表 HTTP/MCP 或宿主交付验收。

## 前置条件与运行

加载的 Runtime 必须包含 #1976 的确定性 FTS 基线和 #1975 的精确 Source 证据读取接口。缺少接口时，在写入 fixture 前明确拒绝运行，不根据正文、Source 名称、当前注册配置或 adapter 审计关联猜测历史证据与可信度。

在同一 Python 环境安装评测包和固定的 `powercontext[builtin]` 源码后执行：

```bash
longmemeval-v2 lineage-fixture \
  --backend sqlite \
  --runtime-revision 固定的40位Runtime提交SHA \
  --dependency-state merged \
  --output-dir /仓库外/新的谱系报告目录
```

验证未合并的依赖快照时使用 `--dependency-state unmerged`。该状态进入 manifest、报告和决策记录，不能解释为主分支验收通过。命令记录实际加载的 Runtime 和评测源码摘要；提交 SHA 应对应安装的源码。已有输出目录不会被覆盖。SQLite 在报告目录所在文件系统创建临时数据库，退出后清理。

OceanBase 使用 `--backend oceanbase`，通过 `POWERCONTEXT_TEST_OCEANBASE_URL` 提供测试连接。账号需有创建和删除数据库的权限。只写入并清理本次创建的 `pc_lineage_` 数据库，不写入或删除连接配置中的原数据库。连接字符串、凭据、机器路径、条目和 Source 正文、单条内容摘要及精确 entry/Source/Scope 标识不会导出。

## 固定场景与指标

`scope-local-lineage-fixtures-v2` 包括独立 Scope 中的完全相同条目、同证据改写、同 Source 不同事实、修订与历史事实、周期自动来源、未知证据、#1976 的六种中文正例及英文和无关查询对照。另有不参与阈值调整的共享来源、字节预算，以及无直接 Source、正文相同但引用不同历史 Artifact 修订的保留验证案例。

事实答案由合成 fixture 显式声明，不是模型判断或可信度推断。每个查询只取一次最多 16 条的准入后 RRF 候选序列，所有实验复用其成员和顺序。精确历史读取及评测前后的权威记录快照用于验证内容、谱系和状态没有被评测修改。

谱系分组保留精确 Artifact 版本及其声明的前驱/证据边，Source 分组使用 Scope、类型、ID、material digest 和历史 Definition manifest 绑定。不同 Source 即使正文或摘要相同也不会合并。没有安全证据时明确记为未知。共享来源只是集中度观察，不代表事实重复。

| 实验组 | 行为 |
| --- | --- |
| `lineage-baseline-v1` | 原始候选池与原生最终 Context 构建。 |
| `lineage-exact-collapse-measurement-v1` | 按 kind 与精确 UTF-8 正文保留首个代表，仅在内存测量投影上调用相同 Builder，原池保持不变。 |
| `lineage-lexical-annotations-v1` | NFC、大小写及空白规范化后的字符三元组 Jaccard 标注，阈值 0.75；不抑制、不重排。 |

Jev 不是必需依赖，本模式明确记录为 `not_provided`，不作为权威。模型调用和模型费用均为零。

候选池和最终选中来源分别统计 top-k 集中度、精确冗余槽位、词汇邻近占比、独立证据及事实数、未知证据和中性声明。选中条目的事实数不等同于实际交付事实数：答案召回直接检查最终渲染字节，避免将截断后的内容当作完整交付。

证据计数使用 `scoped-source-artifact-evidence-v1`，通过 manifest 和报告中的 `evidence_version` 固定。`unique_evidence_count` 是直接 Source materialization 与精确 Artifact 引用的去重并集，Artifact 身份保留 Scope、family、ID 和 revision。`unique_source_evidence_count` 与 `unique_artifact_evidence_count` 分别报告两类数量。覆盖和 `lost_baseline_evidence` 使用相同身份规则，损失也按 Source 与 Artifact 拆分。统计不追随当前 head，也不递归展开引用；不同直接引用不代表不同事实。缺少证据计数版本的 manifest 无法参与比较。

检索漏召回、关系不确定、预算丢弃/截断、条数上限遗漏、相对基线的事实与证据损失、任务退化、负例误命中分别记录。耗时拆分为检索、精确证据读取和构建。每个类别对应一个 Scope，只导出该 Scope 查询的聚合计数和比率，不提供逐条诊断或跨 Scope 标识。

## 报告与比较

单个目录包含固定 manifest、各实验组 manifest、聚合 JSON/Markdown 报告、后端能力矩阵、无机器路径的重跑命令、Markdown 决策记录和文件摘要。比较条件包括 fixture 与源码摘要、后端能力、依赖状态、Python/库版本及算法参数。

```bash
longmemeval-v2 lineage-compare \
  --baseline /报告/lineage-baseline-v1.manifest.json \
  --alternative /报告/lineage-exact-collapse-measurement-v1.manifest.json
```

除了声明的实验组，任何 manifest 差异都会被拒绝，包括后端、未知新增字段和缺失条件。SQLite 与 OceanBase 分开执行、分开报告，未执行的后端标记 `not_run`。本评测建议保持产品行为：合成小规模结果不足以批准生产抑制或重排。

验证命令及完整指标语义见 [英文说明](lineage-occupancy.md)。在缺少 #1975 的 Runtime 上，只能验证拒绝行为；证据能力验收会显式跳过，不能据此宣称后端通过。
