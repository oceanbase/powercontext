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

能力标记只说明 Runtime 已加载向量通道，还需要用一条合成 Source 验证模型调用、索引写入和实际命中。完整的
Source → flush → entry → vector search 验收命令见[启用 Memory 提取与向量搜索](../get-started/configure-models.md)。
其中搜索响应必须包含 `mode: "vector"`、目标 `entry_id`，并且 `matched_by` 包含 `vector`；如果使用 `hybrid`，应把请求
中的 `mode` 改为 `hybrid` 并核对实际返回模式。显式 `vector`/`hybrid` 不会在 Embedding 不可用时静默降级。

如果在已有 Memory 上首次启用 Embedding，先用一条临时 Scope 完成上述闭环，再对真实 Scope 运行受控的重处理/索引流程，
并确认旧 citation 没有变化。不要仅修改 profile ID 或 dimension 来绕过已有向量与模型不匹配的错误。

timeout、batch size、storage 设置和准确默认值见[配置](../operate/configuration.md)。
