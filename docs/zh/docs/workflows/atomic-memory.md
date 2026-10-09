---
title: 使用 Atomic Memory
description: 创建、搜索、修订和恢复独立记忆，并接入旧 Memory API。
---

Atomic Memory 将每条事实、偏好或决策保存为独立的 `atomic-memory` Artifact。正文 revision 不可变，
当前生命周期单独保存。显式创建和修订不调用生成模型；配置了 Embedding 时，发布还会准备当前检索向量。
已有集合数据库先完成[停服迁移](../operate/atomic-memory-migration.md)。

## 创建、读取和修订

以下 HTTP 示例中的 `S` 是已有 Scope ID，`M` 是创建响应返回的 Artifact ID。请求使用
`Content-Type: application/json`，启用鉴权时携带部署要求的 Bearer token。

向 `POST /v1/scopes/S/artifacts` 提交：

```json
{
  "family": "atomic-memory",
  "content": {
    "schema": "powercontext.atomic-memory.v1",
    "kind": "decision",
    "text": "公开 API 保持异步。"
  }
}
```

服务端分配身份，创建 revision 1，并返回正文 `ETag`。`kind` 是应用自定义名称，最多 128 字符；
新写入先对 `text` 做 Unicode NFC 规范化并去掉首尾空白，结果必须非空且不超过 8192 个 UTF-8 字节。
已有正文及历史版本按原样读取；恢复旧版本保留当时的正文，不重新套用新写入的规范化规则。
普通写入不接受合并标记 `creation`。

`GET /v1/scopes/S/artifacts/atomic-memory/M` 读取当前正文与 `ETag`；
`GET /v1/scopes/S/artifacts/atomic-memory/M/revisions/1` 始终读取精确历史。
使用版本列表 `/v1/scopes/S/artifacts/atomic-memory/M/revisions` 查看内容历史。

向 `PUT /v1/scopes/S/artifacts/atomic-memory/M` 提交完整正文，并将刚读到的正文 `ETag` 放入 `If-Match`：

```json
{
  "content": {
    "kind": "decision",
    "text": "公开 API 保持异步，内部同步适配器另行提供。"
  }
}
```

成功后正文产生下一条 revision。缺少 `If-Match` 返回 `428 precondition_required`，
过期返回 `412 revision_conflict`。重新读取并协调正文后再提交。

## 当前状态与遗忘

`GET /v1/scopes/S/artifacts/atomic-memory/M/state` 返回当前精确 `artifact` 引用、`state`、
`state_version` 和 `merged_into_id`。此接口的 ETag 同时包含正文 revision 和状态版本，
支持 `If-None-Match` 与 `304`；修订正文仍使用正文读取接口的 ETag。

| 状态 | 含义 |
| --- | --- |
| `active` | 参与普通搜索和上下文召回 |
| `forgotten` | 正文和历史保留，可恢复在役 |
| `merged` | 作为合并输入被冻结；`merged_into_id` 指向合并结果 |
| `retired` | 退出当前使用，不能编辑或恢复；如需重新采用正文，应创建新 Artifact |

遗忘使用 `POST /v1/atomic-memory/lifecycle`。将状态读取响应的精确引用和 `state_version` 原样带回：

```json
{
  "scope_id": "S",
  "target": {
    "artifact": {"family": "atomic-memory", "artifact_id": "M", "revision": 2},
    "state_version": 0
  },
  "state": "forgotten"
}
```

示例里的 revision 和 state_version 必须替换为实际读取值。遗忘不增加正文 revision。
普通 Replace 可修订 active 或 forgotten 正文，并保留其状态；merged、retired 不能直接修订。
当前 lifecycle 接口只接受 `forgotten`。

## 搜索和管理列表

向 `POST /v1/atomic-memory/search` 提交：

```json
{"scope_id": "S", "query": "公开 API", "mode": "text", "limit": 10}
```

`mode` 可选 `text`、`vector`、`hybrid`，默认 `text`。向量和混合模式要求可用且匹配的 Embedding profile。
搜索只返回 active 记忆，`hits[].memory` 包含正文、精确 Artifact 引用和状态版本；还返回 `score`、
`matched_by`。引用结果时保留 `memory.artifact`。普通向量搜索在资格过滤后计算精确 L2，再排序和限制条数；
当前路径不使用 ANN，不能据此推断生产后端性能或验收结果。

需要完整 Artifact 结果和高级检索参数时，使用 `POST /v1/scopes/S/artifacts/atomic-memory/search`。
统一入口要求 `scope.read`，默认文本检索，支持 `filters`、准入、RRF 排名常数与权重、`min_score` 和可选评分元数据。
默认返回上限为 10，最大为 100。响应 `results` 包含不可变的 `content`、`lineage`，与专用搜索返回当前状态的
`hits[].memory` 不同。检索评分归一化到 `[0, 1]`；通道原分为实际 BM25/MATCH 相关性或 L2 距离。
已配置的重排器仍会生效，但请求不开放 `rerank` 参数。详见[检索 Artifact](search-artifacts.md)和
[融合算法与参数](search-fusion.md)。只有单个 Artifact 读取授权的调用者，仍可使用 Atomic 专用搜索入口。

向 `POST /v1/atomic-memory/list` 提交：

```json
{
  "scope_id": "S",
  "states": ["active", "forgotten", "merged", "retired"],
  "kind": "decision",
  "limit": 50
}
```

列表不接受语义查询，未传 `states` 时只列 active。`items` 包含当前正文与状态；有 `next_cursor` 时，
保持 Scope、主体和过滤条件相同，用该 cursor 继续读取。搜索和列表都支持 `kind`、`tags` 与
`tag_match: "all" | "any"`，条数范围为 1–100。标签使用
[Artifact 标签接口](manage-artifact-tags.md)，新目标为 `{type: "artifact", family: "atomic-memory", artifact_id: "M"}`。

## 合并与恢复

`POST /v1/atomic-memory/merges` 至少需要两个 active 输入的精确引用和状态版本，以及结果正文：

```json
{
  "scope_id": "S",
  "inputs": [
    {"artifact": {"family": "atomic-memory", "artifact_id": "A", "revision": 1}, "state_version": 0},
    {"artifact": {"family": "atomic-memory", "artifact_id": "B", "revision": 3}, "state_version": 2}
  ],
  "content": {"kind": "decision", "text": "公开 API 异步，内部适配器可同步。"}
}
```

服务端创建结果 C，将 A、B 设为 merged。额外依据可放入 `source_refs`、`artifact_refs`；
合并输入本身会成为精确 Artifact 依据。合并与恢复会检查全部实际目标的权限，写入目标须由当前主体拥有。

直接恢复调用 `POST /v1/atomic-memory/restorations`：

```json
{"scope_id": "S", "target": {"artifact_id": "B"}}
```

恢复 forgotten 使其重新在役；恢复已经 active 且未指定 revision 的记忆返回未变化。
恢复 merged 会撤销冻结该目标的后续合并。例如 A+B→C、C+D→E，恢复 B 会让 A、B、D 在役，C、E 退役。
普通下游 Artifact 和 Source cursor 不随之回滚。指定 `target.revision` 时，将所选历史正文保存为目标的新 revision。

先查看影响范围可调用 `POST /v1/atomic-memory/restoration-previews`：

```json
{"scope_id": "S", "operation": "restore", "target": {"artifact_id": "B", "revision": 3}}
```

响应包含 `preview_token`、`expires_at`、当前链终点 `endpoint`、待恢复的 `restore`、待退役的精确引用
`retire`，以及 `undo_merge_results`。预览不保留锁，也不写入待审批记录。检查结果后，使用同一主体、Scope、
operation 和 target 向 restorations 提交原 token：

```json
{
  "scope_id": "S",
  "operation": "restore",
  "target": {"artifact_id": "B", "revision": 3},
  "preview_token": "复制预览响应中的原值"
}
```

成功响应返回 `changed`、恢复后的精确 `restored` 引用、`retired` 和 `undo_merge_results`。
若要撤销创建结果 C 的合并，改用 `operation: "undo_merge"`、`target: {"artifact_id": "C"}`；
该操作不能同时指定内容 revision。

| 错误 | HTTP 状态 | 处理 |
| --- | --- | --- |
| `invalid_preview` | 422 | 检查 token、主体、Scope、operation、target 及共享签名配置 |
| `preview_expired` | 409 | 重新预览 |
| `preview_stale` | 409 | 重新读取影响范围并预览 |
| `invalid_memory_state` | 409 | 不直接编辑 merged，也不恢复 retired |
| `atomic_memory_changed` | 409 | 当前内容或状态已变，重新读取后决定是否重试 |
| `invalid_memory_relation` | 409 | 关系数据不一致，需要排查存储数据 |

恢复与合并没有 `idempotency_key`，不返回某次旧请求的持久回执。不带 token 的恢复按每次调用时的当前关系解释；
目标后来又被合并，重复请求可能撤销新的合并。携带 token 的成功请求重发也可能返回 `preview_stale`。
连接在提交时中断，应先读取各对象状态确认结果，再决定是否重新预览。Python SDK 不盲目重试结果未知的写入。
签名密钥、有效期及直接恢复重试预算见[配置](../operate/configuration.md#atomic-memory)。

## 旧 Memory API 兼容

旧路由保留不代表旧响应模型仍适用。升级后的新记忆和 revision 只产生真实的 `atomic-memory` ArtifactRef，
不分配旧集合版本、entry_version_id 或新 MemoryCitation。

| 旧调用 | 升级后行为与响应 |
| --- | --- |
| `entries/get` 传旧 `citation` | 返回旧 `MemoryEntry`，校验当时集合成员与精确 entry 版本 |
| `entries/get` 传旧 `target` | 返回当前 `AtomicMemoryRecord`；见下面的两种读取模式 |
| 旧集合的精确 Artifact revision 读取 | 保留原集合正文、manifest 和 changes；冻结 head 不代表升级后的当前记忆 |
| `search` | 保留 `auto/fts/vector/hybrid` 请求模式与 tag_filter；响应为 `mode`、`hits`，每个 hit 的 `memory` 是 AtomicMemoryRecord |
| `entries/list` | 返回 `entries: AtomicMemoryRecord[]` 与 `next_cursor`；`include_inactive=true` 包含四态 |
| `remember` 不传或传 null `expected_revision` | 创建独立记忆；返回 `changed`、`records: AtomicMemoryRecord[]` |
| `flush` | 运行 Atomic Source 处理，保留 status、cursor、计数；`memory` 为 null，held_count 为 0、hold_codes 为空 |
| 有效旧 entry target 的标签 GET/PUT | 映射到新 Artifact 标签，保留旧 target 响应和旧标签 ETag 并发校验；已 compact 目标为 404 |
| `remember` 非空集合 `expected_revision` | 写入前拒绝；调用方按原并发意图改用新 API |
| `entries/revise`、`entries/retire` 传旧 citation | 写入前拒绝；分别改用 Atomic Replace 和 forgotten lifecycle |
| `changes`、`capacity`、集合 compact | 不支持跨迁移集合变更流、集合容量或压缩；历史 changes 从精确旧 revision 读取 |
| `family=memory` Create/Replace、集合回滚 | 拒绝；改用独立 Artifact 写入与恢复 |

不支持的旧集合操作返回 HTTP `422`、`error.code: "legacy_memory_operation_unsupported"`。
`error.details` 包含 `operation`、替代 `alternatives` 路由和 `instruction`，并保留 `kind`、`name`。
无对应操作时 alternatives 为空。客户端不得自动删除旧集合 CAS 前提后重试。

旧逻辑身份读最新使用 `POST /v1/memory/entries/get`：

```json
{
  "scope_id": "S",
  "target": {"type": "memory_entry", "family": "memory", "artifact_id": "OLD", "entry_id": "E"}
}
```

服务端验证保留的旧身份，映射到新 Artifact，再读取新 head 和当前状态。若原记忆已 merged，
返回原对象的冻结正文、merged 状态及 merged_into_id；不会跳到合并结果。

精确历史读取仍使用原 citation：

```json
{
  "scope_id": "S",
  "citation": {
    "memory_ref": {"family": "memory", "artifact_id": "OLD", "revision": 7},
    "entry_id": "E",
    "entry_version_id": "V3"
  }
}
```

两种模式必须且只能选一种。citation 不会忽略版本改查最新；升级后新建记忆直接用新 Artifact ID。

Python Client 方法沿用旧名称，但 `remember_memory` 返回 `.records`，`search_memory` 的 hit 使用
`.memory.artifact`，`list_memory_entries` 返回 `.entries` 和 `.next_cursor`。
`get_memory_entry` 直接返回 `MemoryEntry | AtomicMemoryRecord`：citation 模式具有 `.citation`，target 模式具有 `.artifact`。
应升级 SDK 并按实际模型处理；旧集合 `.memory` 和 citation 字段不能用于新结果。
unsupported 通过 `ServerResponseError.status_code`、`.code`、`.details` 检查。
新入口包括 `get_atomic_memory_state`、`list_atomic_memories`、`search_atomic_memory`、
`merge_atomic_memories`、`change_atomic_memory_lifecycle`、`preview_atomic_memory_restoration` 和 `restore_atomic_memory`；
创建、修订与精确历史读取使用通用 Artifact Client 方法。
