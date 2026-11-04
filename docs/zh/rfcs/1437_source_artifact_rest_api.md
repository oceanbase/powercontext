---
title: RFC 1437：Source 与 Artifact REST API
description: Source 与 Artifact REST API 的设计提案，规定 Scope 内的通用操作、家族写入职责与精确来源记录。
---

+ Proposal Name: `source_artifact_rest_api`
+ Start Date: 2026-09-01
+ RFC PR: [oceanbase/powercontext#1437](https://github.com/oceanbase/powercontext/pull/1437)

# Summary

本 RFC 为 PowerContext 增加两组基础 HTTP API：

- Source：Create、Get；
- Artifact：Create、Get head、Get Revision、List、Replace。

新增接口位于 `/v1/scopes/{scope_id}/sources` 与 `/v1/scopes/{scope_id}/artifacts` 两棵 Scope 子资源树下。
本文不增加统一 Resource 概念，也不定义 Scope API。

Source Create 只公开 `content` 类型。Artifact 只公开 `memory`、`experience`、`skill`、`handoff` 四个
family。Artifact Create/Replace 通过 Family-owned management writer 复用现有领域校验、权威写入和派生投影，
不在基础 API 中复制 Family 逻辑。每次写入都会在新 Revision 的同一事务中保存一条系统 Source，并将其放在该
Revision 直接 Source lineage 的首位；Handoff 再通过现有服务派生 citation lineage。系统 Source 不得进入其他
Artifact 的生成流程。

Source Create 不触发 Memory、Experience、Skill 或 Handoff 生成。需要继续执行领域操作的调用方先创建 Source，
再调用对应的既有领域命令。

# Motivation

PowerContext 已有接口主要表达 Source capture、Memory flush、Experience/Skill 演进和 Handoff workflow 等领域动作，
但缺少稳定的 Source 与 Artifact 基础访问接口。调用方需要在不引入第二套数据或身份空间的前提下，按完整资源身份
创建和读取 Source，以及创建、读取、列举和替换正式 Artifact。

基础 API 必须继续使用现有 Source journal、Artifact Revision/head、lineage 和授权能力。通过基础 API 写入的每个
Artifact Revision 都需要留下可追溯的直接输入，同时避免这条为 provenance 保存的 Source 被模型或其他生成流程再次消费。

# Guide-level explanation

## 两类基础资源

Source 是没有 Revision 的耐久证据。公开身份为：

```json
{
  "source_key": ["scope_id", "source_type", "source_id"]
}
```

Source Create 在 Scope 的 Source 父集合执行。调用方提交 `content`，可省略 `source_type`；本期唯一公开类型和缺省值
都是 `content`。`scope_id` 来自 Path，`source_id` 由服务端生成。

Artifact 是可提交、可演进的正式制品。head 和精确 Revision 的公开身份分别为：

```json
{
  "artifact_head_key": ["scope_id", "family", "artifact_id"],
  "artifact_revision_key": ["scope_id", "family", "artifact_id", "revision"]
}
```

Artifact Create 提交 Revision 1，Replace 创建下一条不可变 Revision 并移动 head。调用方可以读取当前 head、读取精确
历史 Revision，或在单个 family 中列举当前 heads。

## 公开类型

公开 Source type：

| `source_type` | Create/Get | 内容要求 |
| --- | --- | --- |
| `content` | 支持 | `content` 为合法 JSON value，并由现有 Content Source adapter 规范化和持久化。 |

`external-skill-snapshot` 等内部类型不进入本期 OpenAPI enum。服务端拒绝未知公开值，不把自由字符串直接交给内部
adapter。

公开 Artifact family：

| `family` | Create | Get | List | Replace | 校验要求 |
| --- | --- | --- | --- | --- | --- |
| `memory` | 支持 | 支持 | 支持 | 支持 | Create/Replace 使用 Memory 命令；读取返回标准 `MemoryContent`。 |
| `experience` | 支持 | 支持 | 支持 | 支持 | 使用既有 `ExperienceContent` 和搜索投影。 |
| `skill` | 支持 | 支持 | 支持 | 支持 | 使用既有 `SkillContent`、标准 package 校验和搜索投影。 |
| `handoff` | 支持 | 支持 | 支持 | 支持 | 使用既有 `HandoffContent`、citation 校验和 Scope 单例身份。 |

服务端先按 `family` 选择领域模型，再反序列化、校验并按该 family 的 canonical 规则序列化 `content`。未知 family 或
不符合对应数据标准的内容返回 `422 Unprocessable Entity`。本文不增加其他 direct family。

## Artifact lineage

Artifact response 将自身身份平铺在顶层，并以两个数组返回多值 lineage：

```json
{
  "scope_id": "scp_01J...",
  "family": "memory",
  "artifact_id": "mem_01J...",
  "revision": 3,
  "sources": [
    {"source_type": "content", "source_id": "src_01J..."}
  ],
  "artifacts": [
    {"family": "experience", "artifact_id": "exp_01J...", "revision": 2}
  ]
}
```

顶层 `scope_id` 同时适用于数组中的 Source 和 Artifact，本期只表达同 Scope lineage。数组按持久化的 `ordinal`
排序，没有关系时返回 `[]`，不得返回 `null`。

`sources` 与 `artifacts` 是只读结果。Create 和 Replace request 不接受这两个字段，也不接受 `source_refs` 或
`artifact_refs`；调用方不能通过基础 HTTP API 直接写 lineage。

## Artifact Create 和 Replace 的 provenance

Artifact Create 和 Replace 都不接收 Source 引用。每次创建新 Revision 时，服务端会把校验和 canonicalization 后的
写入命令保存为一条新的 `source_type=content` 系统 Source，并将它放在该 Revision 直接 Source lineage 的
ordinal 0。对 Memory 而言，该 Source 保存 `entries[].kind/text` 命令，而 Artifact GET 返回命令执行后生成的标准
`MemoryContent`。Handoff 还会通过现有 Handoff service 从直接 citation 派生 Source 和 Artifact lineage。
Replace 不删除或改写旧 Revision 关联的 Source；非 Handoff writer 继承上一 Revision 的有序 Artifact lineage，
Handoff 则从完整替换 content 重新派生该 Revision 的 citation lineage。

这条 Source 的内部角色是 `lineage_only`：它是真实、可追溯的创建输入，但不是供模型再次消费的普通 evidence。
内部 payload 将其唯一绑定到目标 `(scope_id, family, artifact_id, revision)`；公开 Source response 不暴露这些
内部用途字段。

Artifact Create 或 Replace 在同一数据库事务中写入系统 Source、journal position、新 Artifact Revision、head 和
Source lineage。任一步失败都整体回滚。

## Non-goals

- 不提供 Source List 或 Search；
- 不提供 Artifact Search 或跨 family List；
- 不提供 Artifact Delete、物理清除或批量操作；
- 不提供客户端可写的 lineage；
- 不增加写入时同步生成参数、组合响应或生成任务模型；
- 不定义 Scope API 或共享权限；
- 不修改既有领域命令的业务语义。

# Reference-level explanation

## Scope、URI 与资源身份

`scope_id` 是资源 owner、授权边界和公开身份的一部分。Scope 的创建、读取、列举、组织关系和 binding 由
[RFC 1345](1345_scope_organization_and_agent_integration.md) 及其
[实现 PR #1401](https://github.com/oceanbase/powercontext/pull/1401) 负责；本 RFC 只定义已有 Scope 下的 Source 和
Artifact 子资源。

本文允许的 Resource Path：

```text
/v1/scopes/{scope_id}/sources
/v1/scopes/{scope_id}/sources/{source_type}/{source_id}

/v1/scopes/{scope_id}/artifacts
/v1/scopes/{scope_id}/artifacts/{family}
/v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}
/v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}/revisions/{revision}
```

Source item、Artifact head 和 Artifact Revision 的 canonical URI 分别为：

```text
/v1/scopes/{scope_id}/sources/{source_type}/{source_id}
/v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}
/v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}/revisions/{revision}
```

所有具名 GET 都在 Path 中携带完整公开唯一键。`source_type` 和 `family` 编码为单个 URL segment。路径使用小写复数
名词，静态多单词 segment 使用 `kebab-case`，JSON 和 query 参数使用 `snake_case`，URI 末尾不加 `/`。

## 新增 operation

本文定义 7 个 operation：

| 对象 | operationId | HTTP 方法与 URI | 成功状态 | 说明 |
| --- | --- | --- | --- | --- |
| Source | `create_source` | `POST /v1/scopes/{scope_id}/sources` | `201 Created` | 创建不可变 Content Source。 |
| Source | `get_source` | `GET /v1/scopes/{scope_id}/sources/{source_type}/{source_id}` | `200 OK` | 按完整身份读取 Source。 |
| Artifact | `create_artifact` | `POST /v1/scopes/{scope_id}/artifacts` | `201 Created` | 创建 Artifact、Revision 1 和系统 Source。 |
| Artifact | `get_artifact` | `GET /v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}` | `200 OK` / `304 Not Modified` | 读取当前 head。 |
| Artifact | `get_artifact_revision` | `GET /v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}/revisions/{revision}` | `200 OK` | 读取不可变历史 Revision。 |
| Artifact | `list_artifacts` | `GET /v1/scopes/{scope_id}/artifacts/{family}` | `200 OK` | 列举指定 family 的当前 heads。 |
| Artifact | `replace_artifact` | `PUT /v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}` | `200 OK` | 完整替换并创建下一 Revision。 |

## Wire schemas

`CreateSourceRequest`：

```json
{
  "source_type": "可选；单值 enum content；缺省为 content",
  "content": "必填 JSON value；待持久化的原文内容"
}
```

`SourceRecord`：

```json
{
  "scope_id": "scp_01J...",
  "source_type": "content",
  "source_id": "src_01J...",
  "content": "退款流程必须保留人工复核。",
  "position": 42,
  "content_digest": "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
}
```

Source request/response 不公开 `metadata`、服务端时间或内部用途字段。

`CreateArtifactRequest` 是以 `family` 为 discriminator 的联合类型：

```text
CreateArtifactRequest
├── CreateMemoryArtifactRequest
├── CreateExperienceArtifactRequest
├── CreateSkillArtifactRequest
└── CreateHandoffArtifactRequest
```

统一外形保持 `{ "family": "<family>", "content": {} }`，但 `content` 不强行统一：

| family | Create 的 `content` | 写入结果 |
| --- | --- | --- |
| `memory` | `entries[].kind/text` 创建命令 | 生成 Entry Version、manifest、changes、entry head 和搜索投影；GET 返回标准 `MemoryContent`。 |
| `experience` | `ExperienceContent` | 写入 Revision/head，并刷新 Experience 搜索投影。 |
| `skill` | `SkillContent` | 校验或生成标准 Skill package，写入 Revision/head，并刷新 Skill 搜索投影。 |
| `handoff` | `HandoffContent` | 校验 citations，写入 Scope 唯一的 `handoff` Artifact。 |

Memory Create 示例：

```json
{
  "family": "memory",
  "content": {
    "entries": [
      {"kind": "preference", "text": "用户偏好使用中文回答"}
    ]
  }
}
```

`kind` 必填，保持开放字符串，长度为 1 到 128。推荐值为 `fact`、`preference`、`decision`、`constraint`、
`working_note`；允许业务自定义值，服务端只校验并原样保留，不猜测、不自动覆盖。`text` 必填且非空。

Experience Create 示例：

```json
{
  "family": "experience",
  "content": {
    "situation": "发布前发现兼容性问题",
    "action": "增加跨版本测试",
    "outcome": "避免了线上回归",
    "lesson": "公共接口变更需要覆盖兼容性测试"
  }
}
```

`ReplaceArtifactRequest`：

```json
{
  "content": "必填 object；按 Path family 解释的写入命令或完整 family-specific 内容"
}
```

OpenAPI 中 Create 使用 `oneOf` 与 `family` discriminator，让 Python/TypeScript 客户端获得准确类型。Replace 的
family 已由 Path 确定，因此 body 仍只有 `content`，但也以 family-specific union 表达。

`ArtifactCreated`：

```json
{
  "scope_id": "scp_01J...",
  "family": "memory",
  "artifact_id": "mem_01J...",
  "revision": 1,
  "sources": [
    {"source_type": "content", "source_id": "src_01J..."}
  ],
  "artifacts": []
}
```

`ArtifactRevision`：

```json
{
  "scope_id": "scp_01J...",
  "family": "memory",
  "artifact_id": "mem_01J...",
  "revision": 2,
  "content": {"summary": "退款必须经过人工复核"},
  "sources": [
    {"source_type": "content", "source_id": "src_01J..."}
  ],
  "artifacts": [],
  "content_digest": "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
}
```

`ArtifactCollectionItem` 不返回完整 `content`：

```json
{
  "scope_id": "scp_01J...",
  "family": "memory",
  "artifact_id": "mem_01J...",
  "revision": 2,
  "sources": [
    {"source_type": "content", "source_id": "src_01J..."}
  ],
  "artifacts": [],
  "content_digest": "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
}
```

`ArtifactPage`：

```json
{
  "items": [],
  "next_cursor": null
}
```

Artifact response 不返回服务端时间字段，也不使用 `artifact_ref`、`source_refs` 或 `artifact_refs` envelope。

## Operation behavior

### Create Source

`POST /v1/scopes/{scope_id}/sources` 接收 `CreateSourceRequest`。服务端生成 `source_id`，同步写入 Source journal，
返回 `201 SourceRecord` 与完整 canonical URI 的 `Location`。每次成功调用都创建新 Source，不接受
`Idempotency-Key`。

### Get Source

`GET /v1/scopes/{scope_id}/sources/{source_type}/{source_id}` 按三段完整身份读取。任一身份分量不匹配都返回
`404 Not Found`，不得退化为只按 `source_id` 查询。

### Create Artifact

`POST /v1/scopes/{scope_id}/artifacts` 接收判别联合类型的 `family` 和 `content`。服务端按 family 选择 writer，
生成系统 `source_id`，并在同一事务中创建系统 Source、Revision 1、head、Family 派生状态和 ordinal 0 的 Source
lineage。除 Handoff 外，服务端生成 `artifact_id`。成功返回 `201 ArtifactCreated`、Artifact head 的 `Location`
和新 head 的 `ETag`。

Handoff 使用 Scope 内固定 `artifact_id=handoff`。不存在时 Create；已存在时返回 `409 artifact_already_exists`，
错误 details 提示 `use_replace=true`，调用方必须使用 Replace 更新，不得通过第二次 Create 隐式替换。

除 Handoff 单例冲突外，每次成功调用都创建新的 Artifact 和与其 Revision 1 唯一绑定的 `lineage_only` Source，
不接受 `Idempotency-Key`。response 的 `sources` 必须包含该 Source，`artifacts` 固定为 `[]`。

### Get Artifact head

`GET /v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}` 返回当前 `ArtifactRevision` 和 head ETag。可选
`If-None-Match` 命中时返回 `304 Not Modified` 且无 body。

### Get Artifact Revision

`GET /v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}/revisions/{revision}` 返回指定的不可变 Revision，
不得用当前 head 替换 Path 中的 revision。精确 Revision 不返回 ETag，也不接受条件 header。

### List Artifacts

`GET /v1/scopes/{scope_id}/artifacts/{family}` 只列举该 family 的当前 heads。query 只接受可选 `limit` 和
`cursor`。items 不返回完整 `content`，response 不返回 `total`。

Cursor 是不透明字符串，绑定调用方、`scope_id`、`family`、稳定排序和过期时间。与当前集合不匹配或非法的 cursor
返回 `400 Bad Request`，过期返回 `410 Gone`。HTTP cursor 与内部 `pc_source_cursors` 无关。

### Replace Artifact

`PUT /v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}` 按 Path 中的 family 解释和校验 `content`。
Memory 使用 entries 命令（`entry_id` 省略时新增，提供时修订当前 logical entry），其余 family 使用完整内容。
`If-Match` 必填；匹配当前 head ETag 后，服务端创建一条绑定下一 Revision 的新 `lineage_only`
Source，并将其放在该 Revision Source lineage 的首位。writer 随后继承上一 Revision 的 Artifact lineage，或由
Handoff 从完整 content 派生 citation lineage，再创建下一条
不可变 Revision，并返回新的 `ArtifactRevision` 和 ETag。返回的 `sources` 数组包含新生成的 `source_type` 和
`source_id`。接口不支持 merge patch 或自动合并。

## 系统 Source 不变量

Artifact Create 或 Replace 生成的系统 Source 使用公开 `source_type=content`，公开 `content` 是该次经过
family-specific 校验和 canonicalization 的写入命令。Memory 的系统 Source 保存 entries 命令，而 Artifact GET
返回生成后的标准 `MemoryContent`。类型化 payload 的服务端保留部分包含：

```json
{
  "role": "lineage_only",
  "operation": "artifact_replace",
  "target": {
    "scope_id": "scp_01J...",
    "family": "memory",
    "artifact_id": "mem_01J...",
    "revision": 2
  }
}
```

这些字段不是 OpenAPI 字段，调用方不能提交、覆盖或伪装；历史 Source 缺少 `role` 时按普通 `evidence` 处理。

`lineage_only` Source 只能写入 `target` 指定的精确 Revision lineage。显式生成、Propose、Candidate Revise、Handoff
citation 或其他流程尝试把它用于不同目标时，返回 `422 source_not_eligible`。

实现提供统一的 Source 生成准入校验，并在完整 Source 从持久化层解析后、进入模型或 Candidate 前执行。Memory、
Experience、Skill、Handoff 和未来生成流程必须复用该校验。Candidate Approve 和 Artifact commit 在持久化前再次
校验，防止非法引用进入正式 Artifact。

系统 Source 仍写入现有 Source journal 并取得正常 `journal_position`。Source-window consumer 从模型输入中过滤
`lineage_only` Source，但业务成功后仍按完整窗口边界推进 cursor。如果窗口全部被过滤，则不调用模型、不创建
Revision、正常推进 cursor 并返回 no-op。

Artifact Create/Replace 的原子事务顺序为：

```text
1. 校验 scope_id、family、content，以及 Replace 的前置条件
2. 确定新的 Artifact 身份与 Revision，并生成 source_id
3. 构造绑定该精确 Revision 的 lineage_only Source
4. 插入 pc_sources 并分配 journal_position
5. 调用 Family-owned writer 写入新的 Revision/head，并维护对应 Family 的约束与派生状态
6. 插入 pc_artifact_lineage_sources，新 Source 的 ordinal = 0
7. Memory 写入 Entry Version、manifest、changes、entry head 和搜索投影；Experience/Skill 刷新搜索投影，Skill 同步 package；Handoff 校验 citations 和单例身份
8. 提交事务
```

任一步失败都整体回滚，不得遗留孤立 Source、没有 Source lineage 的 Artifact，或已经推进但没有对应记录的 journal
head。实现不得先调用独立 Source Create，再在另一个事务中写入 Artifact Revision。

## HTTP headers、请求和响应

所有成功和错误响应返回：

```http
X-PowerContext-Request-ID: <request-id>
```

ETag 是当前 Artifact head 表示状态的不透明 HTTP 校验值，不是业务字段，也不替代 `revision`。客户端不得解析或
自行拼接 ETag，后续请求必须原样回传，包括引号。

- Artifact Create、Get head 和 Replace 返回 ETag；
- Replace 缺少 `If-Match` 返回 `428`，与当前 head ETag 不一致返回 `412`；
- Get head 的 `If-None-Match` 命中时返回无 body 的 `304`；
- 精确 Revision 不使用 ETag。

Path 中已经确定的身份字段不在 body 中重复接收。Create Source 的 `source_type` 和 Create Artifact 的 `family` 是
从父集合选择类型的例外。未声明的 request body 字段返回 `422`，不静默忽略。

`content_digest` 只覆盖 canonical `content`，不覆盖身份或 lineage。Source 和 Artifact Create 每次成功都创建新
资源；客户端超时后应先根据业务上下文确认结果，不能假定重试幂等。

## 状态和错误模型

| 状态码 | 场景 |
| --- | --- |
| `200 OK` | Get、List 或 Replace 成功。 |
| `201 Created` | Source Create 或 Artifact Create 成功。 |
| `304 Not Modified` | Get Artifact head 的 `If-None-Match` 命中。 |
| `400 Bad Request` | Path、query、header 格式错误，或 cursor 与当前集合不匹配。 |
| `401 Unauthorized` | 缺少或无效凭证。 |
| `403 Forbidden` | 已认证但无 Scope 或资源权限。 |
| `404 Not Found` | Scope、Source、Artifact、Revision 不存在或不可见。 |
| `409 Conflict` | 唯一约束、Revision 提交或内部状态冲突。 |
| `410 Gone` | List cursor 已过期。 |
| `412 Precondition Failed` | Replace 的 `If-Match` 与当前 head ETag 不一致。 |
| `413 Content Too Large` | `content` 超出限制。 |
| `422 Unprocessable Entity` | 类型、family content、额外字段或 Source 准入校验失败。 |
| `428 Precondition Required` | Replace 缺少 `If-Match`。 |
| `429 Too Many Requests` | 限流。 |
| `503 Service Unavailable` | 暂时无法访问依赖服务。 |

统一错误 body：

```json
{
  "error": {
    "code": "precondition_failed",
    "message": "artifact head changed",
    "details": {}
  }
}
```

错误不得泄漏其他 Scope 的资源是否存在；权限不足与资源不可见按统一安全策略返回 `403` 或 `404`。

## OpenAPI contract

`openapi/powercontext.yaml` 是 HTTP 契约唯一事实来源。主要 schema：

```json
{
  "schemas": [
    "CreateSourceRequest",
    "SourceRecord",
    "CreateArtifactRequest",
    "ReplaceArtifactRequest",
    "ArtifactCreated",
    "ArtifactRevision",
    "ArtifactCollectionItem",
    "ArtifactPage"
  ],
  "request_headers": ["If-Match", "If-None-Match"],
  "response_headers": ["Location", "ETag", "X-PowerContext-Request-ID"]
}
```

operationId 必须唯一稳定，并提供成功和错误示例。不得增加 Source/Artifact union、统一 selector、`source_ref`、
`artifact_ref`，或可写的 `source_refs`、`artifact_refs`、`sources`、`artifacts` request 字段。

## API 与持久化映射

OpenAPI 字段及语义是公开契约；表名和列名只是当前实现映射。本文复用现有表结构，不要求增加字段。

### Source 字段

| API 字段 | 持久化字段 | 映射 | 含义 |
| --- | --- | --- | --- |
| `scope_id` | `pc_sources.scope_id` | `direct` | Source 所属 Scope、授权边界和公开身份分量。 |
| `source_type` | `pc_sources.source_type` | `direct` | 本期公开值固定为 `content`。 |
| `source_id` | `pc_sources.source_id` | `direct` | 服务端生成的 Source ID。 |
| `content` | `pc_sources.payload` | `encoded` | Content Source 正文；系统 Source 保存 canonical Artifact Create/Replace 写入命令。 |
| `position` | `pc_sources.journal_position` | `direct` | Source 在所属 Scope journal 中的位置。 |
| `content_digest` | 无独立列 | `derived` | canonical `content` 的 SHA-256 摘要。 |

`pc_source_journal_heads.position` 是 Scope 级高水位和下一位置分配依据，不是单条 Source 的 position。

系统 Source 的内部用途字段编码在 `pc_sources.payload` 的服务端保留部分，不进入 OpenAPI，也不增加数据库列：

| 内部字段 | 含义 |
| --- | --- |
| `role=lineage_only` | 禁止该 Source 进入其他 Artifact 生成和 Candidate evidence。 |
| `operation` | 取 `artifact_create` 或 `artifact_replace`，记录输入来自哪一种基础 Artifact 写操作。 |
| `target.scope_id` | 绑定目标 Artifact Scope。 |
| `target.family` | 绑定目标 family。 |
| `target.artifact_id` | 绑定目标 Artifact ID。 |
| `target.revision` | 只允许作为本次写入所创建精确 Revision 的 lineage。 |

### Artifact 字段

| API 字段 | 持久化字段 | 映射 | 含义 |
| --- | --- | --- | --- |
| `scope_id` | Artifact、head 和 lineage 表的 `scope_id` | `direct` | Artifact 所属 Scope、授权边界和身份分量。 |
| `family` | `pc_artifacts.family`、`pc_artifact_heads.family` | `direct` | family 和 adapter 路由。 |
| `artifact_id` | `pc_artifacts.artifact_id`、`pc_artifact_heads.artifact_id` | `direct` | 服务端生成的 Artifact ID。 |
| `revision` | `pc_artifacts.revision`、`pc_artifact_heads.revision` | `direct` | 从 1 开始递增的不可变 Revision。 |
| `content` | `pc_artifacts.content` | `encoded` | family-specific writer 生成或校验后的标准完整内容；Memory 保存命令执行结果。 |
| `sources` | `pc_artifact_lineage_sources` | `relation/derived` | 按 Revision 身份和 ordinal 组装的同 Scope Source 身份数组。 |
| `artifacts` | `pc_artifact_lineage_artifacts` | `relation/derived` | 按 Revision 身份和 ordinal 组装的上游 Artifact Revision 数组。 |
| `content_digest` | 无独立列 | `derived` | canonical `content` 的 SHA-256 摘要。 |

`sources` 关系映射：

```json
{
  "child_identity": ["scope_id", "family", "artifact_id", "revision"],
  "ordinal": "数组顺序",
  "sources[].source_type": "source_type",
  "sources[].source_id": "source_id"
}
```

`artifacts` 关系映射：

```json
{
  "child_identity": ["scope_id", "family", "artifact_id", "revision"],
  "ordinal": "数组顺序",
  "artifacts[].family": "upstream_family",
  "artifacts[].artifact_id": "upstream_artifact_id",
  "artifacts[].revision": "upstream_revision"
}
```

### HTTP 与分页字段

| 字段 | 映射 | 含义 |
| --- | --- | --- |
| `limit` | `runtime` | Artifact List 单页上限。 |
| `cursor` | `runtime` | 不透明分页令牌，与 `pc_source_cursors` 无关。 |
| `next_cursor` | `derived/runtime` | 根据页末位置和查询上下文生成。 |
| `Location` | `derived` | 根据 Create 后的完整身份生成 canonical URI。 |
| `ETag` | `derived` | 当前 Artifact head 的不透明 HTTP 校验值。 |
| `If-Match` | `runtime` | Replace 前置条件。 |
| `If-None-Match` | `runtime` | Get head 条件读取参数。 |
| `X-PowerContext-Request-ID` | `runtime` | 单次请求追踪 ID。 |

Digest 规则：

```json
{
  "algorithm": "sha256",
  "input": "API content 的 UTF-8 canonical JSON bytes",
  "object_key_order": "lexicographic",
  "insignificant_whitespace": "removed",
  "included_fields": ["content"],
  "output": "sha256:<64 lowercase hexadecimal characters>"
}
```

## 既有接口兼容性

本文只定义新增接口。既有路径、request/response、状态码和领域行为不在本 RFC 修改范围内。新增入口必须读写同一份
权威 Source journal、Artifact Revision、lineage 和授权结果。

## 实现与验收

实现步骤：

1. 在 OpenAPI 中增加本文 7 个 operation；
2. 将公开 `source_type` 固定为 `content`，将 `family` 固定为四个公开值；
3. 以 discriminator 生成四类 Create request，并让 Artifact Create/Replace 分发到 Family-owned writer；
4. 在每次 Artifact Create/Replace 事务中创建目标绑定的 `lineage_only` Source 并放在 ordinal 0，再由 Handoff
   writer 派生 citation lineage；
5. 增加共享 Source 生成准入校验，并覆盖所有模型、Candidate 和 commit 路径；
6. Source-window 过滤 `lineage_only` Source，但按完整窗口推进 cursor，全过滤时返回 no-op；
7. Artifact response 按完整 Revision 身份批量读取 lineage，组装顶层身份和数组；
8. 复用现有 Source journal、Artifact repository、Memory service、Handoff service、Skill package 及各 Family 搜索投影；
9. 运行生成、契约、单元、文档及 SQLite/OceanBase 行为测试。

验收条件：

- 只在两棵 Scope 子资源树下新增 7 个 operation，不重复定义 Scope API；
- Source 只提供 Create/Get，公开类型只允许 `content`，且不公开 metadata、时间或内部用途字段；
- Artifact 只提供 Create/Get/Get Revision/List/Replace，不提供 Search 或 Delete；
- 四个公开 family 均通过对应 writer 支持 Create/Get/List/Replace，并维护其权威约束与派生状态；
- Memory Create 接受非空 `entries[].kind/text`，保留开放 `kind`，并生成标准 Memory 状态与搜索投影；
- Experience/Skill Create 与 Replace 刷新现有搜索投影，Skill 同步标准 package；
- Handoff Create 使用 Scope 单例 `handoff`，重复 Create 返回 409 并提示使用 Replace，Create/Replace 均复用 citation 校验；
- Artifact Create body 只有 `family` 和 `content`，Replace body 只有 `content`；
- Artifact request 不接受任何 lineage 字段；
- Artifact Create 和每次成功 Replace 都在同一事务中生成目标绑定的 `lineage_only` Source 并放在 ordinal 0；
  Handoff 还会通过现有服务派生直接 citation lineage；
- Replace 返回新 Source 身份并保留旧 Revision 的 Source；非 Handoff writer 继承上一 Revision 的 Artifact
  lineage，Handoff 则从替换 content 派生；
- 所有生成、Candidate 和 commit 路径统一拒绝将该 Source 用于其他目标；
- Source-window 跨过被过滤记录，全过滤时不调用模型、不创建 Revision，并返回 no-op；
- Source、Artifact Revision、head 或 lineage 任一步写入失败时全部回滚；
- Artifact response 平铺身份并以 `sources`、`artifacts` 返回有序 lineage；
- Source 与 Artifact response 不返回服务端时间字段；
- Replace 使用不透明 ETag/If-Match，Get head 支持 If-None-Match；
- 所有响应包含 `X-PowerContext-Request-ID`；
- 不要求在现有元数据表增加字段；
- SQLite 与 OceanBase 通过相同 contract 和行为测试。

# Drawbacks

- Family-specific Create `content` 通过 discriminator 获得准确客户端类型，但 response 仍需按 family 解释标准内容；
- 每个 family 都需要稳定的反序列化、校验和 canonical serialization adapter；
- List 返回 lineage 数组会增加关系查询成本，实现需要避免逐条查询；
- 每次 Artifact Create 和成功 Replace 都会额外写入一条 Source 和一条 lineage，增加 Source journal 体量；
- Source 生成准入成为跨 family 安全不变量，新入口绕过共享校验会导致 `lineage_only` Source 泄漏；
- 新旧读取入口需要共享 application service 和 parity tests，避免行为漂移；
- 复合身份使 URI 更长，owner Scope、Source type 或 Artifact family 变化会改变 canonical URI；
- Source Create 与后续领域命令不是事务，调用方需要处理后续操作失败后的重试。

# Rationale and alternatives

## 不增加统一 Resource API

Source 和 Artifact 的生命周期、身份与写入约束不同。统一 CRUD 会模糊 Source 的无 Revision 语义、Artifact 的不可变
Revision，以及 family-specific 校验，因此保留两棵资源树。

## 只公开确定的 type 和 family

自由字符串会把未知值直接暴露给内部 adapter，并让 OpenAPI 无法表达能力。本期只公开已有稳定模型：Source
`content` 与四个 Artifact family。增加新值需要同时提供领域模型、canonical serializer 和行为测试。

## 不提供 Search 和 Source List

本期目标是稳定的身份访问与 Artifact 生命周期，不引入跨适配器的检索语义。Artifact List 仅在单一 family 内返回当前
heads；Source List/Search、Artifact Search 和跨 family List 需要独立契约。

## 每次基础 Artifact 写入都生成系统 Source

让客户端提交 lineage refs 无法保证每条 Revision 都有真实直接输入，也会扩大非法引用面。每次 Create 或 Replace
事务内都生成新的目标绑定 `lineage_only` Source，可以同时保证逐 Revision provenance、原子性和生成隔离。

## 平铺身份和只读 lineage 数组

顶层身份避免 `artifact_ref` envelope；数组保留一对多 lineage 和 ordinal，又不引入新的公开领域对象。lineage 由
服务端根据权威关系表派生，防止基础 API 绕过领域流程写入任意关系。

## ETag 保持不透明

`revision` 是领域版本，ETag 是 HTTP 表示校验值。即使实现可从 head Revision 派生 ETag，客户端也不能依赖编码格式，
以便服务端未来改变表示策略而不破坏业务契约。

# Prior art

- HTTP 条件请求为缓存读取和乐观并发提供 `ETag`、`If-None-Match`、`If-Match`、`304`、`412` 和 `428`；
- 仓库现有 Source journal 提供稳定 position 和 consumer cursor；
- 现有 Artifact repository 使用不可变 Revision、head 指针和有序 lineage 表；
- Memory、Experience、Skill、Handoff 领域模型提供本期 family-specific 校验基础。

# Unresolved questions

本 RFC 没有阻塞合并的未决问题。以下实现细节不改变公开契约：

- 服务端生成 ID 的算法，只要值不透明、path-safe 且满足长度限制；
- ETag 与 cursor 的编码或签名方式，只要保持不透明并满足条件请求与过期语义；
- `lineage_only` 保留字段在 Content Source payload 中的具体版本化表示；
- Artifact List 批量读取 lineage 的查询和缓存策略。

# Future possibilities

- 通过独立 RFC 增加 Source List/Search、Artifact Search 或跨 family List；
- 在具备领域模型、canonical serializer 和测试后扩展公开 Source type 或 Artifact family；
- 设计跨 Scope lineage、共享与授权；
- 设计 Artifact restore、retention、管理员 purge 和批量 mutation；
- 为内部 Source 角色增加显式版本迁移与观测能力。
