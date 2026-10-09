---
title: 配置向量检索
description: 启用基于 embedding 的 vector 和 hybrid Memory 检索，并验证 Runtime capability。
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

首次启用或更换 Embedding profile，需要停服重建当前投影；命令与恢复流程见
[Atomic Memory 迁移](../operate/atomic-memory-migration.md)。重建不改变 Artifact 身份、内容版本或状态版本。
不能仅修改 profile ID 或 dimension 来绕过不匹配错误。

当前 Atomic Memory 的普通向量搜索和抽取阈值枚举均使用精确 L2 距离。普通搜索有返回数量限制；
抽取枚举返回全部符合资格和阈值的结果。计算量随合格向量数和维度增长，不能把返回条数当成扫描成本。

timeout、batch size、storage 设置和准确默认值见[配置](../operate/configuration.md)。
