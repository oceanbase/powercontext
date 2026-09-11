---
title: Topic Memory
description: 将采集到的 Source 证据整理为持续演进、可检索且带精确版本的主题制品。
---

# Topic Memory

Topic Memory 保存的是项目主题的持续演进视图，而不是互不关联的事实集合。Server 从已采集的 Source 证据生成主题，
发布不可变修订，并在 Scope 中维护当前 head。它适合回答“关于 Aurora 部署，我们在几次对话中形成了什么决定”这类
需要串起多次输入的问题。

Topic Memory 属于历史证据，不会覆盖当前 Prompt、实时项目状态、授权边界或 Agent 的其他指令。

## 与其他上下文能力的区别

| 能力 | 保存内容 | 典型用途 |
| --- | --- | --- |
| Source | 用户输入及其他证据 | 追溯信息来源 |
| Memory | 已整理的持久决策、约束或事实 | 召回一条稳定知识 |
| Topic Memory | 一个持续演进主题的标题、摘要、详情和 Source 引用 | 跟踪跨多次输入的项目主线 |
| Prepared Context | 为一次请求组装的有界结果 | 向 Agent 提供相关历史上下文 |

Topic Memory 不会因为显式写入一条 Memory 就自动生成。它需要有意义的 Source 窗口、满足 Topic 要求的 Generation 配置，
以及已启用的 Topic 处理。模型可以修订已有主题、合并证据，也可以判断本轮不需要发布新主题修订。

## 处理生命周期

1. Agent Hook、Connector 或应用将 Source 采集到目标 Scope。
2. Topic Worker 发现符合条件的 Source。配置的调度周期负责接纳新任务；显式 HTTP flush 可以请求尽快处理。接纳是
   异步且可恢复的，所以 flush 成功不等于处理完成。
3. Generation 将相关证据归并，发布当前 Topic Memory head 和不可变修订。每个修订都保留直接的 Source 引用。
4. 客户端搜索当前 head，再用返回的完整 Artifact 引用读取详情和证据。Prepared Context 可以在新请求或新会话中包含
   Topic Memory。

Scope 是隔离边界。Agent、Dashboard、Source 采集和召回必须使用同一个真实 Scope；仅切换目录不会自动创建 Scope。

## 开启自动 Topic Memory

可以在配置向导中选择完整记忆，也可以直接配置 Server。Topic Memory 需要：

- 受支持的 Generation 模型和有效的 Provider 凭据；
- 将 `POWERCONTEXT_SERVER_RUNTIME_TOPIC_MEMORY_SCHEDULE_SECONDS` 设为正数，以接纳新的自动处理任务；
- Scope 中存在可归纳的 Source 证据。

Topic Memory 本身不强制要求 Embedding。需要向量或混合检索时，再配置兼容的 Embedding 模型和维度。Server 会在每次
搜索响应中返回实际使用的检索模式；调用方不能任意指定检索控制参数。

调度周期是接纳间隔，不是完成时限。Topic 生成还受到 Provider 请求超时和 Worker 总超时控制。调度未设置时，不再接纳新的
自动任务，但已经接纳的任务不会因此丢失。配置了 Generation 模型的 Topic Worker 要求持久化的文件型 SQLite，不能使用
内存 SQLite。完整设置和部署约束见[配置模型与完整记忆](../get-started/configure-models.md)及[配置选项](../operate/configuration.md)。

## 验收一个真实主题

按[快速开始中的 Topic Memory 验收](../get-started/quickstart.md#4-用普通对话验收-topic-memory)完成完整的
Agent 和 Dashboard 检查：

1. 在新会话中发送一条具体的项目决策。
2. 确认同一 Scope 中出现这条输入对应的 Source。
3. 等待配置的检查周期和模型处理，确认出现相关 Topic Memory。
4. 再发送一条相关更新，检查主题内容或修订历史。
5. 使用同一 Scope 开启新会话，用 citation 召回该主题。

不要把 `doctor codex`、readiness 响应或“已经过了一分钟”当作业务验收证据。它们只能说明安装或服务状态，不能证明
Source 采集、生成、发布和新会话召回链路成功。

## 通过 HTTP 搜索和读取

从受信任的环境中设置 `POWERCONTEXT_CLIENT_SERVER_URL`、`POWERCONTEXT_CLIENT_API_TOKEN` 和 `POWERCONTEXT_SCOPE_ID`。
本机且关闭 Access 时可以省略 Authorization Header；不要把 Token 放进 URL。

搜索当前 Topic Memory head：

```bash
curl --fail \
  --request POST \
  --header 'Content-Type: application/json' \
  --header "Authorization: Bearer ${POWERCONTEXT_CLIENT_API_TOKEN}" \
  --data "{\
    \"scope_id\": \"${POWERCONTEXT_SCOPE_ID}\",\
    \"query\": \"Aurora 部署\",\
    \"limit\": 8\
  }" \
  "$POWERCONTEXT_CLIENT_SERVER_URL/v1/topic-memory/search"
```

响应包含 `mode`（`fts` 或 `hybrid`）和 `hits`。每个 hit 包含标题、摘要、可选匹配片段，以及带有 `family`、
`artifact_id` 和 `revision` 的完整 `artifact` 引用。保存并原样传递这份引用。

读取搜索结果对应的精确修订：

```bash
curl --fail \
  --request POST \
  --header 'Content-Type: application/json' \
  --header "Authorization: Bearer ${POWERCONTEXT_CLIENT_API_TOKEN}" \
  --data '{
    "scope_id": "project:demo",
    "artifact": {
      "family": "topic-memory",
      "artifact_id": "替换为搜索结果中的 ID",
      "revision": 1
    }
  }' \
  "$POWERCONTEXT_CLIENT_SERVER_URL/v1/topic-memory/get"
```

精确响应包含 `title`、`summary`、`detail` 和 `source_refs`。`source_refs` 是证据指针，不是指令；采取有影响的操作前，
仍需检查当前 Source 和实时状态。

## 通过 HTTP 请求处理

HTTP flush 为 Scope 记录一个持久化处理请求并立即返回：

```bash
curl --fail \
  --request POST \
  --header 'Content-Type: application/json' \
  --header "Authorization: Bearer ${POWERCONTEXT_CLIENT_API_TOKEN}" \
  --data "{\"scope_id\":\"${POWERCONTEXT_SCOPE_ID}\"}" \
  "$POWERCONTEXT_CLIENT_SERVER_URL/v1/topic-memory/flush"
```

`status: "accepted"` 表示请求已排队或推进；`status: "idle"` 表示 Source 游标已经是最新。两者都不保证 Generation 已
完成。应在 Worker 完成后再次搜索。这个 flush 操作有意保持为 HTTP-only，不会暴露成 MCP 工具。

## 在 Prepared Context 中使用 Topic Memory

向 `POST /v1/context/prepare` 增加 `topic-memory` section，可以显式选择 Topic Memory：

```json
{
  "scope_id": "project:demo",
  "query": "Aurora deployment",
  "max_bytes": 8000,
  "assembly": {
    "sections": [
      {"family": "topic-memory", "limit": 2},
      {"family": "memory", "limit": 3}
    ],
    "show": ["recall_rank"]
  }
}
```

Prepared Context 会返回有界的标题、摘要、可选匹配片段、Scope 和精确修订 citation，不会返回完整 Topic 详情；需要渐进披露
时，再用 citation 调用 `get_topic_memory`。显式 `assembly: {}` 使用文档定义的 Memory 和 Experience 默认选择，并排除
Topic Memory。省略 `assembly` 时，当前默认配置可以召回 Topic Memory；如果集成需要稳定的 section 选择，应使用显式策略。
详见[组装标准上下文文本](prepare-context-text.md)。

MCP 暴露只读的 `search_topic_memory` 和 `get_topic_memory` 工具。Agent 应使用聚焦的查询，并将搜索返回的完整 Artifact
引用原样传给精确读取操作。

## 排查主题缺失或过期

| 现象 | 优先检查 |
| --- | --- |
| 没有 Source | Agent Hook 或 Connector、Server 地址、Token 和 Scope 绑定 |
| 有 Source 但没有主题 | Generation readiness、正数 Topic 调度、Worker 错误，以及证据是否足够有意义 |
| 搜索没有结果 | 当前 Scope、查询文本、检索能力，以及处理是否已经完成 |
| 搜索模式是 `fts` 而不是 `hybrid` | Embedding 地址、模型、维度和检索 Profile |
| flush 后主题仍旧 | flush 只负责异步接纳；检查 Worker 状态，完成后再搜索 |
| 新会话召回不到 | Scope 是否相同、上下文组装策略、精确 citation 和 Agent 集成诊断 |

服务和模型故障见[故障排查](../operate/troubleshoot.md)。游标、重试、预算和部署角色见[配置选项](../operate/configuration.md)及
[Topic Memory RFC](../../rfcs/1417_topic_memory.md)。
