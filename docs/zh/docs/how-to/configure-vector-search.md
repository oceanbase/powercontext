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

timeout、batch size、storage 设置和准确默认值见[配置](../reference/configuration.md)。
