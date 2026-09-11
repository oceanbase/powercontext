---
title: 使用 Topic Memory
description: 从长期 Source 处理中查找主题摘要，并按精确 Revision 读取详情。
---

# 使用 Topic Memory

Topic Memory 是面向长期主题的只读检索型 Artifact family。它把一个 Scope 中持续累积的 Source 处理成
`title`、`summary` 和渐进式展开的 `detail`，适合先定位主题、再按需读取全文；它不替代 Memory、Experience、Skill
或 Handoff。

Topic Memory 只属于当前 Scope。Source 被采集后不会同步生成主题，必须由已配置的后台处理能力推进。

## 生命周期

```text
Source → 后台处理游标 → flush 请求 → 新建或更新不可变 Topic Memory Revision
      → search 当前 head → get 精确 Revision
```

`flush` 只是持久化一次处理请求，不等待后台处理完成。响应为 `accepted` 表示请求已接受，`idle` 表示 Source
游标已经处于最新状态；两者都不等于某个主题已经生成。

## 请求处理

调用方需要对目标 Scope 具有 `scope.contribute` 权限：

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
关闭候选制品召回。PreparedContext 仍是临时结果，不会创建新的 Topic Memory Revision。完整的 Markdown 分组规则见
[准备上下文文本](prepare-context-text.md)。

## 当前边界

- 没有通用的 Topic Memory create、update、delete 或 retire 接口；主题由 Source 处理产生，并以不可变 Revision 保存。
- Topic Memory 不在当前 Taggable Artifact family 列表中，不能套用 Memory、Experience、Skill 或 Handoff 的标签接口。
- Source capture 不会同步生成主题；需要后台处理和相应的生成能力。
- 备份、恢复、worker 可用性和检索故障属于[部署与运维](../operate/index.md)，不是本生命周期的一部分。
