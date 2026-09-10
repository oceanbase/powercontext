---
title: 保存与召回 Memory
description: 保存项目决策、检索相关历史并纠正过时 Memory。
---

# 保存与召回 Memory

Memory 保存持久的决策、约束和事实。PreparedContext 为单次请求筛选相关历史，属于临时结果，不会再创建一条 Memory。

## 保存、召回与纠正

1. 完成 [Quick Start](../get-started/quickstart.md)，让不同会话解析到相同 Scope。
2. 显式请 Agent 保存信息。直接调用 `remember_memory` 不需要模型。
3. 使用 `search_memory` 搜索，或在宿主提供对应工具时用 `list_memory_entries` 和 `get_memory_entry` 检查条目。
   引用结果时保留返回的 citation。
4. 使用当前 citation 修订错误信息，或退役不应继续召回的信息。退役会将条目移出活跃召回范围，同时保留历史。

各宿主工具名称不同，见[接入 Agent](../integrations/index.md)。完整请求结构和并发要求见 [HTTP API](../develop/http-api.md)。

## 自动上下文与提取

召回 Hook 向 Server 请求有大小上限的 PreparedContext。没有相关信息时返回 `empty` 是正常结果。
历史内容不能覆盖当前指令或项目现状。

按[输出标准上下文文本](prepare-context-text.md)选择 Memory、Experience、Profile 和 Topic Memory，
设置输出顺序、各类别条数及总条数上限。

采集 Prompt 会创建 Source 证据，不保证提取出 Memory。按[模型配置](../get-started/configure-models.md)
启用生成模型与 Source 处理；只有需要[向量或混合搜索](configure-vector-search.md)时才配置 Embedding。
记录项目信息前，检查宿主的 Prompt 采集开关。

Topic Memory 有独立的处理与检索入口，包括 MCP 的 `search_topic_memory` 和 `get_topic_memory`。
基本的显式 Memory 写入不会启用 Topic Memory；相关设置和运行时能力见[配置](../operate/configuration.md)。

交接当前任务边界使用 [Handoff](memory-and-handoff.md)，保留原始证据使用 [Sources](sources.md)。
