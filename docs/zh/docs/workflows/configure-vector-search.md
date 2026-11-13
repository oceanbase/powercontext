---
title: 配置向量检索
description: 配置 Embedding 模型，启用 Memory 向量与混合检索，并检查 Runtime 报告的检索能力。
---

# 配置向量检索

向量检索需要 embedding model、稳定的 profile ID 和该模型的输出 dimension。

## 1. 设置一份 embedding profile

```bash
export POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_MODEL=provider:embedding-model
export POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_PROFILE_ID=embedding-model-v1
export POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_DIMENSION=1024
```

将示例值替换为一个受支持的 provider model、该模型保持稳定的 profile ID，以及其文档给出的输出 dimension。
`POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_NORMALIZATION` 默认值为 `unit`。

输出维度始终要配置，它决定本地向量索引的宽度，并用来校验模型返回的向量。默认还会把这个值作为请求参数 `dimensions` 发给模型。有些 OpenAI 兼容服务不接受该参数，例如硅基流动上的 `BAAI/bge-m3`。这类模型保留文档中的输出维度（bge-m3 为 1024），并关闭发送：

```bash
export POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_SEND_DIMENSIONS=false
```

## 2. 启动 Server

```bash
powercontext server run
```

使用 SQLite 时，PowerContext 会在打开数据库时加载内置 sqlite-vec extension。extension 与当前 platform 或 SQLite build
不兼容时，启动会失败。

## 3. 验证 capability

```bash
powercontext capabilities
```

结果会报告已启用的 search mode。未配置 embedding profile 时，SQLite full-text search 仍可使用。

能力标记说明 Runtime 已加载向量通道；实际调用还需核对 Source 抽取、投影写入和命中结果。
[配置模型](../get-started/configure-models.md)介绍 Source 与 flush 的检查方法。
搜索响应中，`mode` 应与实际使用模式一致，目标身份位于 `hits[].memory.artifact`，`matched_by` 应包含 `vector`。
显式 `vector` / `hybrid` 在向量不可用时返回错误，不会静默降级。

首次启用或更换 Embedding profile 时，需要停服并迁移共享该 profile 的所有派生投影。仅重建 Atomic Memory 时，命令与恢复流程见
[Atomic Memory 迁移](../operate/atomic-memory-migration.md)。如果托管 Topic Memory 的 profile 迁移也需要重建 Atomic Memory，
请使用下方协调迁移选项，不要先独立执行 Atomic 迁移。重建不改变 Artifact 身份、内容版本或状态版本。不能仅修改
profile ID 或 dimension 来绕过不匹配错误。

## 4. 重建 Topic Memory 向量索引

Topic Memory 可以迁移到当前配置的目标 profile，无需删除其 Artifact。命令会重新嵌入每个活跃 Topic 的
标题/摘要和每个活跃 chunk，然后激活新的冻结检索形状。权威 Artifact 内容、Topic 与 chunk 的身份、版本和文本均不改变。

执行迁移前，停止所有 API、host 和 Worker，并暂停全部 Topic 写入。先检查现有数据库与目标 profile：

```bash
powercontext server topic-memory-reindex --action dry-run
```

默认操作就是 dry-run。它不会初始化 embedding provider、调用模型、创建暂存表或修改数据库。检查就绪后，对同一目标配置执行：

```bash
powercontext server topic-memory-reindex --action apply --maintenance-confirmed --batch-size 25
```

每个暂存批次独立提交。已提交的 Topic 和 chunk 计数以 JSON 进度写入 stderr，最终 JSON 报告写入 stdout；
可使用 `--no-progress` 关闭进度输出。命令中断或推理失败后，保持旧服务使用旧 profile，并使用相同目标配置重新执行同一
apply 命令，即可续用已经完整暂存的版本。只有待处理的 Topic 向量或请求执行的 Atomic 重建需要推理时，apply 才会初始化 provider；
激活完整暂存数据或完成恢复不需要 provider 凭据。不要在运行中途更改目标 profile，也不要手动删除
`pc_topic_memory_reindex*` 表。

激活前发生失败会保留旧形状和旧活跃向量。OceanBase 与 SeekDB 会保留上一代派生向量表，直到激活后的向量和共享存储声明通过验证。
如果激活后的验证或清理失败，请在维护模式下重新执行同一 apply 命令，完成验证与恢复后才丢弃上一代数据。

对于托管 Server 存储，Atomic Memory 与 Topic Memory 共享声明的 embedding profile。默认情况下，此命令不会重新嵌入
Atomic Memory：Atomic 投影不兼容，或 OceanBase Atomic 向量物理列不兼容时，激活会被阻止。如果托管迁移还需要重建
Atomic Memory，必须在 dry-run 以及每次 apply 或恢复调用中显式添加 `--rebuild-atomic-memory`：

```bash
powercontext server topic-memory-reindex --action apply --maintenance-confirmed \
  --rebuild-atomic-memory --batch-size 25
```

该选项使用同一目标 provider，根据保留的权威历史重建当前 Atomic 投影，不改变 Atomic Artifact 的身份、版本或状态。
Topic 暂存先提交，Atomic 重建随后独立提交；保留证据投影和当前 Atomic 投影都通过验证后，才允许激活 Topic 或清理恢复世代。
如果后续 Topic 激活失败，
权威数据和旧 Topic 世代仍然保留，但 Atomic 可能已经使用目标 profile，而共享声明和活跃 Topic 形状仍是旧 profile。
此时必须保持停服，并使用相同选项重新执行同一 apply 命令；不要重启旧服务 profile，也不要改为仅执行 Atomic 迁移。

当前 Atomic Memory 的普通向量搜索和抽取阈值枚举均使用精确 L2 距离。普通搜索有返回数量限制；
抽取枚举返回全部符合资格和阈值的结果。计算量随合格向量数和维度增长，不能把返回条数当成扫描成本。

timeout、batch size、storage 设置和准确默认值见[配置](../operate/configuration.md)。
