---
title: 使用 Topic Memory
description: 从长期 Source 处理中查找主题摘要，并按精确 Revision 读取详情。
---

# 使用 Topic Memory

Topic Memory 是面向长期主题的只读检索型 Artifact family。它把一个 Scope 中持续累积的 Source 处理成
`title`、`summary` 和渐进式展开的 `detail`，适合先定位主题、再按需读取全文；它不替代 Memory、Experience、Skill
或 Handoff。

Topic Memory 只属于当前 Scope。Source 被采集后不会同步生成主题，必须由已配置的后台处理能力推进。

它适合回答“关于 Aurora 部署，我们在几次对话中形成了什么决定”这类需要串起多次输入的问题。Topic Memory 属于历史
证据，不会覆盖当前 Prompt、实时项目状态、授权边界或 Agent 的其他指令。

## 与其他上下文能力的区别

| 能力 | 保存内容 | 典型用途 |
| --- | --- | --- |
| Source | 用户输入及其他证据 | 追溯信息来源 |
| Memory | 已整理的持久决策、约束或事实 | 召回一条稳定知识 |
| Topic Memory | 一个持续演进主题的标题、摘要、详情和 Source 引用 | 跟踪跨多次输入的项目主线 |
| Prepared Context | 为一次请求组装的有界结果 | 向 Agent 提供相关历史上下文 |

Topic Memory 不会因为显式写入一条 Memory 就自动生成。它需要有意义的 Source 窗口、满足 Topic 要求的 Generation 配置，
以及已启用的 Topic 处理。模型可以修订已有主题、合并证据，也可以判断本轮不需要发布新主题修订。

已注册的远程 Source Observation 可以与内置 Source 在同一窗口中处理。Topic 处理优先使用已保存的标准文本证据投影，
没有该投影时使用采集的 payload，不要求 Server 安装远程 Source 的本地适配器。发布的修订保留原始 Source 引用，便于追溯。

## 生命周期

```text
Source → 后台处理游标 → flush 请求 → 新建或更新不可变 Topic Memory Revision
      → search 当前 head → get 精确 Revision
```

`flush` 只是持久化一次处理请求，不等待后台处理完成。响应为 `accepted` 表示请求已接受，`idle` 表示 Source
游标已经处于最新状态；两者都不等于某个主题已经生成。

## 开启自动 Topic Memory

可以在配置向导中选择完整记忆，也可以直接配置 Server。Topic Memory 需要：

- 受支持的 Generation 模型和有效的 Provider 凭据；
- 将 `POWERCONTEXT_SERVER_RUNTIME_TOPIC_MEMORY_SCHEDULE_SECONDS` 设为正数，以接纳新的自动处理任务；
- Scope 中存在可归纳的 Source 证据。

Topic Memory 本身不强制要求 Embedding。需要向量或混合检索时，再配置兼容的 Embedding 模型和维度。Server 会在每次
搜索响应中返回实际使用的检索模式；调用方不能任意指定检索控制参数。

调度周期是接纳间隔，不是完成时限。Topic 生成还受到 Provider 请求超时和 Worker 总超时控制。调度未设置时，不再接纳新的
自动任务，但已经接纳的任务不会因此丢失。使用 SQLite 部署时，配置了 Generation 模型的 Topic Worker 要求持久化的
文件型数据库，不能使用内存 SQLite。完整设置和部署约束见[配置模型与完整记忆](../get-started/configure-models.md)及[配置选项](../operate/configuration.md)。

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

## 请求处理

调用方需要对目标 Scope 具有 `scope.contribute` 权限。以下 HTTP 示例应使用受信任环境中的 Server 地址和凭据；
启用 Access 时，添加 `Authorization: Bearer <token>` Header。所有请求都要将 `project-a` 替换为同一个真实 Scope ID。

发送 flush 请求：

```http
POST /v1/topic-memory/flush
Content-Type: application/json

{"scope_id":"project-a"}
```

响应只包含 `status`。Source capture、主题生成和索引更新由部署配置的 worker 异步完成；没有配置生成模型或后台
处理能力时，flush 不会凭空产生主题。向量或混合检索是否可用也是部署选项，不由调用方在请求中指定。

## 搜索当前主题

搜索需要 `scope.read` 权限，输入 `scope_id`、非空 `query`，可选 `limit`（范围 1–20，默认 10）：

```http
POST /v1/topic-memory/search
Content-Type: application/json

{"scope_id":"project-a","query":"release process","limit":5}
```

响应包含部署实际使用的 `mode`（`fts` 或 `hybrid`）和 `hits`。每个命中包括 `artifact` 精确引用、`title`、
`summary`、可为空的 `snippet`、`score` 和 `matched_by`。搜索只查看当前 Scope 的 Topic Memory head，不跨 Scope
检索，也不接受调用方自选的检索模式。

## 读取精确详情

不要根据标题自行拼接最新版本。把搜索结果中的 `artifact`（`family`、`artifact_id`、`revision`）原样传给
`get`，读取不可变快照和直接 Source 证据：

```http
POST /v1/topic-memory/get
Content-Type: application/json

{
  "scope_id":"project-a",
  "artifact":{
    "family":"topic-memory",
    "artifact_id":"topic-release",
    "revision":3
  }
}
```

响应包含 `title`、`summary`、完整 `detail` 和 `source_refs`。即使主题的当前 head 后续前进，精确引用仍指向同一
历史 Revision，适合审计、引用和渐进式 disclosure。

## 组装到 PreparedContext

需要把主题摘要注入一次 Agent turn 时，在 `POST /v1/context/prepare` 的 `assembly` 中显式加入
`topic-memory`，例如：

```json
{
  "scope_id": "project-a",
  "query": "release process",
  "assembly": {
    "sections": [
      {"family": "topic-memory", "limit": 2}
    ]
  }
}
```

省略 `assembly` 时，Runtime 可以沿用默认的 Topic Memory recall（若当前部署和数据可用）；传入空的 sections 会
关闭候选制品召回。PreparedContext 仍是临时结果，不会创建新的 Topic Memory Revision。显式 `assembly: {}` 使用
Memory 和 Experience 默认选择，并排除 Topic Memory。组装结果包含有界的标题、摘要、可选匹配片段、Scope 和精确修订
citation；完整 Topic 详情需要按精确修订读取。完整的 Markdown 分组规则见[准备上下文文本](prepare-context-text.md)。

## 通过 MCP 搜索和读取

MCP 暴露只读的 `search_topic_memory` 和 `get_topic_memory` 工具。Agent 应使用聚焦的查询，并将搜索返回的完整 Artifact
引用原样传给精确读取操作。flush 操作只通过 HTTP 提供，不会暴露成 MCP 工具。

## 当前边界

- 没有通用的 Topic Memory create、update、delete 或 retire 接口；主题由 Source 处理产生，并以不可变 Revision 保存。
- Topic Memory 不在当前 Taggable Artifact family 列表中，不能套用 Memory、Experience、Skill 或 Handoff 的标签接口。
- Source capture 不会同步生成主题；需要后台处理和相应的生成能力。
- 备份、恢复、worker 可用性和检索故障属于[部署与运维](../operate/index.md)，不是本生命周期的一部分。

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
