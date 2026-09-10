+ Proposal Name: `topic_memory_unified_artifact_read_api`
+ Start Date: 2026-09-10
+ Status: Proposed
+ RFC PR: [oceanbase/powercontext#1546](https://github.com/oceanbase/powercontext/pull/1546)
+ Related RFCs: [Topic Memory](1417_topic_memory.md)、[Source 与 Artifact REST API](1437_source_artifact_rest_api.md)、[Profile Artifact](1485_profile_artifact.md)、[Artifact Processing Supervisor](1515_artifact_processing_supervisor.md)

# Summary

本 RFC 将 `topic-memory` 纳入统一的 Artifact 只读接口。Topic Memory 仍保留专用的搜索、精确展开和后台处理接口，
但当前制品目录、当前 Head 读取和历史 Revision 读取使用标准 Artifact 路径与身份模型。

统一入口如下：

```text
GET /v1/scopes/{scope_id}/artifacts/topic-memory
GET /v1/scopes/{scope_id}/artifacts/topic-memory/{artifact_id}
GET /v1/scopes/{scope_id}/artifacts/topic-memory/{artifact_id}/revisions
GET /v1/scopes/{scope_id}/artifacts/topic-memory/{artifact_id}/revisions/{revision}
```

本 RFC 不增加 Topic Memory 的手动 Create、Replace、Delete 或 Retire。Topic Memory 仍由 Source-driven processing
自动生成和演进；统一 Artifact 接口只负责读取已经正式发布的内容。

# Motivation

Topic Memory 已经是持久化层中的 Artifact Family，并拥有稳定的 `family`、`artifact_id` 和 `revision` 身份。
但当前公共接口只提供 Topic Memory 专用的 `search` 和 `get`，没有进入标准 Artifact 目录。结果是：

- 需要展示所有主题的页面必须依赖旧版 Dashboard 私有接口或直接调用内部 browse 能力；
- 调用方无法用统一 Artifact 目录发现 `topic-memory` 当前 Heads；
- 新的 Artifact Family 是否能够被标准 List/Get 读取，取决于额外的路由适配，而不是统一资源模型；
- `family` 枚举与实际已注册的 Artifact 类型容易发生不一致。

统一 Artifact API 的价值是提供稳定的资源发现和精确读取边界，而不是抹平不同 Family 的内容语义。Topic Memory
仍然可以保留渐进式披露、专用搜索模式和检索投影等领域特性。

# Goals and non-goals

## Goals

- 让 `list_artifacts` 支持 `topic-memory`，列举一个 Scope 内的当前 Topic Memory Heads。
- 让标准 Artifact current-head、revision list 和 exact revision read 路径接受 `topic-memory`。
- 保持标准 Artifact 的 Scope 隔离、分页、cursor、lineage 和 content digest 语义。
- 让统一接口与 Topic Memory 专用接口共享同一持久化事实，不复制数据或生成第二套身份。
- 明确 Topic Memory 的只读边界，避免统一接口误导调用方以为可以手动写入主题。

## Non-goals

- 不把 Topic Memory 搜索改造成 `list_artifacts` 的 query 参数。搜索排序、score、snippet、matched_by 和实际检索模式
  仍由 `POST /v1/topic-memory/search` 负责。
- 不改变 Topic Memory 的 Source Window、生成、索引、原子发布或自动调度语义。
- 不要求所有 Family 返回相同的内容字段。
- 不为统一目录增加跨 Scope、跨 Family 聚合或 total count。

# Guide-level explanation

## 统一目录

调用方可以使用标准 Artifact List 发现指定 Scope 的 Topic Memory 当前 Heads：

```http
GET /v1/scopes/SCOPE_ID/artifacts/topic-memory?limit=50
```

响应继续使用 `ArtifactPage`：

```json
{
  "items": [
    {
      "scope_id": "SCOPE_ID",
      "family": "topic-memory",
      "artifact_id": "architecture",
      "revision": 4,
      "sources": [
        {"source_type": "content", "source_id": "source-17"}
      ],
      "artifacts": [],
      "content_digest": "sha256:..."
    }
  ],
  "next_cursor": null
}
```

`ArtifactCollectionItem` 仍是目录摘要，不返回完整 `title`、`summary` 和 `detail`。调用方可以：

1. 使用 `POST /v1/topic-memory/search` 获取带标题、摘要和 snippet 的相关主题；
2. 使用标准 exact revision 路径获取通用 Artifact Revision；或
3. 使用 `POST /v1/topic-memory/get` 获取带有 Topic Memory 渐进式披露和 direct Source references 的领域响应。

标准目录只保证资源身份和可读取性，不把 Topic Memory 内容字段强行塞入所有 Family 的通用 schema。

## 当前 Head 与历史 Revision

标准 List 只返回当前 Head。Topic Memory 的旧 Revision 仍然通过标准 revision list 和 exact revision path 可发现和
读取，但旧 Revision 不应被当作当前可检索主题。

Topic Memory 的特殊 `get` 接口继续使用精确 `ArtifactRef`，并返回：

- `title`、`summary` 和完整 `detail`；
- direct Source references；
- 与当前 Head 的关系和发布状态（如果该领域响应需要）。

当调用方从标准目录取得 `artifact_id` 和 `revision` 后，必须继续携带原始 revision 读取，不得自动替换为最新 Head。

# Reference-level explanation

## Public contract

`BaseArtifactFamily` 增加 `topic-memory`。以下标准 Artifact read operation 将 `topic-memory` 加入允许的 Family：

| operationId | URI | Topic Memory 行为 |
| --- | --- | --- |
| `list_artifacts` | `GET /v1/scopes/{scope_id}/artifacts/{family}` | 列举当前 Topic Memory Heads |
| `get_artifact` | `GET /v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}` | 读取当前 Head 的通用 Artifact Revision |
| `list_artifact_revisions` | `GET /v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}/revisions` | 列举该主题的不可变 Revision |
| `get_artifact_revision` | `GET /v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}/revisions/{revision}` | 读取指定 Revision |

`create_artifact` 和 `replace_artifact` 不因本 RFC 获得 Topic Memory 写能力。调用方仍应使用 Source capture、
`flush_topic_memory` 和后台处理流程等待自动发布。

## List consistency

Topic Memory 的标准 List 必须满足以下不变量：

- 只返回 `ARTIFACT_HEADS` 中 family 为 `topic-memory` 的当前 Revision；
- 返回的 Revision 必须同时存在于 Topic Memory active topic 和完整检索 projection；
- 不返回已经被新 Revision 替换的旧 Head；
- 列表项的 `sources`、`artifacts` 和 `content_digest` 与同一 Revision 的 Artifact 事实一致；
- cursor 绑定 Scope、Family、分页参数和排序策略，不能跨 Family 或 Scope 复用。

统一接口的 canonical order 默认仍为 `artifact_id` 升序。若 Topic Memory 页面需要按发布时间倒序，应该由专用 browse
projection 提供，并使用自己的不透明 cursor；页面不得把两种 cursor 混用。

## Implementation boundary

统一 HTTP handler 负责路径校验、Scope authorization、cursor 解析和标准响应映射。Family-specific service 负责确认
Topic Memory 的 active head、发布完整性和内容解码。

实现可以复用现有的 `ArtifactRepository` 和 `RelationalRecordService.query_artifacts`，但不能绕过 Topic Memory
自己的 active-topic、publication 和 retrieval-shape 校验。若标准 List 读取到的 head 违反 Topic Memory 完整性不变量，
服务端应返回一致的服务错误并记录可诊断信息，而不是返回部分主题。

专用 `POST /v1/topic-memory/get` 继续使用 `TopicMemoryRepository`，因为它提供的领域响应与标准
`ArtifactRevision` 不同。两条读取路径必须指向同一 Revision，不能各自维护一份 Topic 内容。

## Authorization

标准 Topic Memory read 使用当前 Scope 的 `scope.read` 权限。Topic Memory 是自动生成的 Scope-owned projection，
不建立依赖调用方身份的 Artifact owner relation。现有 Scope isolation、access audit 和 content readiness 检查继续生效。

没有 Scope read 权限时，List、Get Head、List Revision 和 Get Revision 都必须拒绝；不能通过专用 Topic Memory 接口绕过
标准 Scope 边界。

# Alternatives

## 保持 Dashboard 私有 list

不采用。Dashboard 私有接口无法为 API 调用方提供稳定契约，也会让产品页面和外部集成各自维护一套发现路径。

## 继续只用 Topic Memory search 充当 list

不采用。search 需要非空 query，并带有检索分数和部署相关的 FTS/vector/hybrid 语义，不能稳定表达“列举全部当前主题”。

## List 后逐条调用 Topic Memory get

不禁止，但不作为标准目录实现。它会产生 N+1 请求，并且在并发发布时可能让目录与详情观察到不同的版本。统一目录应先返回
稳定的 Artifact identity，详情读取再使用精确引用。

# Compatibility and rollout

增加一个合法 Family 值对遵守开放枚举、只读 unknown value 的调用方是向后兼容的。生成客户端需要重新生成
`BaseArtifactFamily` 和相关 operation schema；旧客户端仍可继续访问原有 Family。

实现分为以下步骤：

1. 在 OpenAPI 的 `BaseArtifactFamily` 以及四个标准 read operation 的 Family 枚举中加入 `topic-memory`。
2. 重新生成 checked-in HTTP models、operations 和 schema。
3. 让标准 read handler 使用 Topic Memory 的 Family reader 和完整性校验。
4. 增加 contract、分页、并发发布、Scope authorization 和 exact revision 回归测试。
5. 更新 HTTP API 文档和 Topic Memory 使用示例。

不需要数据迁移。已有 Topic Memory Revision、Head、publication 和 retrieval projection 继续作为唯一事实来源。

# Acceptance criteria

- `GET /v1/scopes/{scope_id}/artifacts/topic-memory` 可列举当前 Topic Memory Heads，并返回标准 `ArtifactPage`。
- 非法或过期 cursor 的错误语义与其他 Family 一致。
- 新 Revision 发布后，List 只显示新 Head，旧 Revision 仍可通过 exact revision 读取。
- 标准 exact revision 读取与 `POST /v1/topic-memory/get` 指向同一 Revision 内容。
- 无 Scope read 权限时，四个标准 read operation 和两个 Topic Memory 专用 read operation 均被拒绝。
- 不新增 Topic Memory 的手动 Create、Replace、Delete 或 Retire 行为。
