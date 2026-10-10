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

旧 Memory 路由只保留五个入口，由 server 请求层转换为 Atomic 调用。保留路由不代表旧响应模型仍适用：
结果只包含真实的 `atomic-memory` ArtifactRef，不分配旧集合版本、entry_version_id 或 MemoryCitation。

| 保留入口 | 升级后行为与响应 |
| --- | --- |
| `entries/get` 传旧 `target` | 用旧集合 ID 和 entry ID 计算对应 Atomic ID，返回当前 `AtomicMemoryRecord` |
| `entries/list` | 返回 Scope 下的 `entries: AtomicMemoryRecord[]` 与 `next_cursor`；`include_inactive=true` 包含四态 |
| `search` | `fts` 按 `text` 模式执行，`auto/vector/hybrid` 与 tag_filter 保留；每个 hit 的 `memory` 是 AtomicMemoryRecord |
| `remember` 不传或传 null `expected_revision` | 创建独立记忆；返回 `changed`、`records: AtomicMemoryRecord[]` |
| `flush` | 运行 Atomic Source 处理，保留原消费进度、status 和计数；`memory` 为 null，held_count 为 0、hold_codes 为空 |

以下旧行为在执行前拒绝，不读取旧存储：

- `entries/get` 传旧 `citation`。精确历史读取使用 `ArtifactRef(atomic-memory, artifact_id, revision)`。
- `remember` 带非空集合 `expected_revision`。调用方按原并发意图改用新 API。
- `entries/revise`、`entries/retire`。分别改用 Atomic Replace 和 forgotten lifecycle。
- `changes`、`capacity` 和集合 compact。
- `family=memory` 的创建、替换和集合回滚。改用独立 Artifact 写入与恢复。
- 旧集合标签的读取和替换，以及在标签查询中显式选择 `memory` family 或 `memory_entry` 目标。默认标签查询不包含旧 Memory；
  迁移后的 entry 标签通过 Atomic Artifact 目标读写。
- 以旧 `memory_entry` selector 发起的授权检查和 binding 创建。

不支持的旧操作返回 HTTP `422`、`error.code: "legacy_memory_operation_unsupported"`。
`error.details` 包含 `operation`、替代 `alternatives` 路由和 `instruction`，并保留 `kind`、`name`。
无对应操作时 alternatives 为空。客户端不得自动删除旧集合 CAS 前提后重试。

旧逻辑身份读最新使用 `POST /v1/memory/entries/get`：

```json
{
  "scope_id": "S",
  "target": {"type": "memory_entry", "family": "memory", "artifact_id": "OLD", "entry_id": "E"}
}
```

服务端在请求 Scope 中把旧身份映射到对应 Atomic Artifact，再读取其 head 和当前状态。若原记忆已 merged，
返回原对象的冻结正文、merged 状态及 merged_into_id；不会跳到合并结果。升级后新建的记忆直接使用新 Artifact ID。

Python Client 方法沿用旧名称，但 `remember_memory` 返回 `.records`，`search_memory` 的 hit 使用
`.memory.artifact`，`list_memory_entries` 返回 `.entries` 和 `.next_cursor`。
`get_memory_entry` 只接受 target 模式，返回具有 `.artifact` 的 `AtomicMemoryRecord`。
应升级 SDK 并按实际模型处理；旧集合 `.memory` 和 citation 字段不能用于新结果。
unsupported 通过 `ServerResponseError.status_code`、`.code`、`.details` 检查。
新入口包括 `get_atomic_memory_state`、`list_atomic_memories`、`search_atomic_memory`、
`merge_atomic_memories`、`change_atomic_memory_lifecycle`、`preview_atomic_memory_restoration` 和 `restore_atomic_memory`；
创建、修订与精确历史读取使用通用 Artifact Client 方法。

搜索和列表沿用 Memory、Topic Memory 的 Scope 边界，要求 `scope.read`。单条 Artifact 分享允许
精确读取和历史读取，不授予整个 Scope 的列表或搜索权限。重排或上下文组装前若 Scope 读取权限已被
撤销，请求返回 `403`，即使单条分享仍有效。Source 提取会完整枚举同 Scope 中满足阈值、
属于当前主体的记忆，再由实际配置的授权服务检查 Artifact 读取和写入权限后交给模型比较。明确拒绝的
候选会被跳过；授权服务故障会终止处理。不可变的 Owner 列只用于提取时的所有者预筛选。

使用同一数据库的受支持 Builtin 或 Casbin 服务进行 Scope 检查时，授权读取与业务数据共享快照。
独立的决策存储和自定义服务保留各自配置的读取一致性契约。
