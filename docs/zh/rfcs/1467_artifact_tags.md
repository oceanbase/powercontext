- Proposal Name: `unified_artifact_tags`
- Start Date: 2026-09-05
- RFC PR: [oceanbase/powercontext#1467](https://github.com/oceanbase/powercontext/pull/1467)
- Related Discussion: [oceanbase/powercontext#1466](https://github.com/oceanbase/powercontext/issues/1466)
- Related RFCs: [RFC 0014](0014_memory_layer_design.md)、[RFC 0019](0019_local_source_memory_runtime.md)、
  [RFC 0048](0048_handoff_artifact.md)、[RFC 0051](0051_experience_skill_artifact_families.md)、
  [RFC 1396](1396_handoff_access_control.md) 和 [RFC 1437](1437_source_artifact_rest_api.md)

# Summary

本 RFC 为 PowerContext 管理的内容增加 Scope-local 自定义标签。用户可以给 managed Experience、Skill、Handoff、
整个 Memory Artifact 或单条 Memory entry 添加 `customer-a`、`cockpit`、`verified` 等标签，再按精确标签成员关系
检索当前资源。

该能力为所有受支持 target 共用一张 `pc_artifact_tags` 分配表。每一行都保留所属 Artifact 身份，并标识该 Artifact
自身或其中的一条逻辑 Memory entry。这样既能提供统一的产品与 API 模型，也不会把 Memory entry 伪装成独立
Artifact。

标签是逻辑 target 的可变 catalog 属性，不是 Artifact content、Source metadata、lineage、授权策略、生命周期状态或
Prompt 指令。修改标签不会创建 Artifact Revision、改变 content digest、重建 embedding 或改变精确 citation。标签留在
owner Scope 内；除非后续 contract 显式要求，否则 Artifact publication 不复制标签。

第一阶段交付标签读取和 compare-and-swap 替换、精确的 `all` 与 `any` 标签查询、current-resource list 和 Memory
search 的可选标签过滤，以及最小 Dashboard 编辑器和筛选器。标签层级、颜色、别名、自动打标、基于标签的授权及历史
标签快照不进入第一阶段。

# Motivation

团队会在同一个 Scope 中积累大量 Memory entry、Experience 和 Skill。文本与语义搜索能回答“哪些内容与查询相似”，
但不能稳定回答以下组织问题：

- 哪些条目属于客户 A？
- 哪些 Skill 已在智能座舱环境中验证？
- 哪些 Experience 描述发布操作，而不是推理行为？
- 哪些已退役或 inactive 资源仍属于某个合规检查集合？

用户可以把这些信息编码进 content，但这样会混淆分类与知识本身。修改分类将产生新的不可变 Revision、改变 content
digest，并可能重建 search projection，即使可复用内容没有变化。

现有名为 `metadata` 的字段不能提供共享方案。`ContentSource.metadata` 包含 provenance 和 `kind` 等影响行为的字段；
managed Skill metadata 属于精确 Skill content，并参与其 search projection。把任一字段当成通用可变标签袋，都会模糊
ownership、version、indexing 和 security 语义。

持久化模型还有两种用户可见的 target 粒度：

- Experience、Skill、Handoff 和整个 Memory 是由 `artifact_id` 选择的逻辑 Artifact lifecycle；
- 用户看到的一条事实或偏好 Memory，是 Scope 的 Memory Artifact 中由 `entry_id` 选择的逻辑 entry。

只在 `pc_artifact_heads` 增加一个 JSON 标签列，会让标准 one-Memory-per-Scope profile 中的所有 entry 共用同一组标签。
分别增加 family-specific 标签存储虽然能保留粒度，却会分裂 API 和跨 Family 查询路径。PowerContext 需要一个能处理两种
target 形状的显式分配模型。

# Guide-level explanation

## 用户模型

标签是用户在一个 Scope 内为一个 current logical target 编写的字符串，用于精确分组和筛选。它不会证明某个断言为真，
不会批准 Artifact、授予访问权或指示 Agent。

所有受支持资源具有相同的可见行为：

```text
Scope: vehicle-assistant

Memory entry: "驾驶员冬季偏好 24 C"
Tags: [customer-a, cockpit, preference]

Experience: "修改 OpenAPI 后重新生成 Client"
Tags: [release, verified]

Skill: "vehicle-log-triage"
Tags: [customer-a, diagnostics]

Handoff: "完成座舱延迟调查"
Tags: [customer-a, in-progress]
```

当分类作用于整个集合而不是单条 entry 时，用户也可以给整个 Memory Artifact 打标签。UI 必须区分 `Memory` 与
`Memory entry`，避免把集合标签误认为条目标签。

## 添加和删除标签

Dashboard 将标签显示为 inert、已转义的 chip。具有 write authority 的用户打开资源，编辑完整标签集并保存。保存操作
以编辑器读取到的标签集为条件，避免两个编辑器静默覆盖对方。

例如，读取当前集合时，response header 返回 opaque `ETag`，body 为：

```json
{
  "tags": ["cockpit", "customer-a"],
  "tag_digest": "sha256:92f..."
}
```

用户将其替换为：

```json
{
  "tags": ["cockpit", "customer-a", "verified"]
}
```

client 在 `If-Match` 中发送读取到的 ETag，而不是根据 `tag_digest` 构造 precondition。

用相同 normalized tags 替换是幂等操作。用空数组替换会删除全部标签，但不会删除、退役或修订 target。

## 按标签检索

标签经过规范化后按精确值匹配。筛选 `customer-a` 不会匹配 `customer-a-archive`；名为 `release/security` 的标签也
不会隐含一个名为 `release` 的父标签。

多个标签具有显式 match mode：

- `all` 选择拥有所有请求标签的 target；
- `any` 选择至少拥有一个请求标签的 target。

例如：

```json
{
  "families": ["memory", "experience", "skill"],
  "target_types": ["artifact", "memory_entry"],
  "tags": ["customer-a", "verified"],
  "match": "all",
  "limit": 50
}
```

响应返回 logical target 及其当前精确 reference。Artifact 结果包含当前 `ArtifactReference`，Memory entry 结果包含当前
`MemoryCitation`。精确 reference 让调用方读取内容时不需要再次解析 `latest`。

文本检索可以组合 query 与标签过滤。PowerContext 在 FTS、vector top-k selection、fusion 或 reranking 前，对 eligible
candidate set 应用标签过滤。先截断 top-k 再过滤是错误的，因为相关且带标签的条目可能在过滤发生前就已被排除。

## Revision 行为

标签跟随逻辑身份：

```text
Experience exp-1 Revision 1 --\
Experience exp-1 Revision 2 ----> logical exp-1 的 tags

Memory entry entry-1 Version 1 --\
Memory entry entry-1 Version 2 ----> logical entry-1 的 tags
```

因此，修订 `exp-1` 或 `entry-1` 会保留其标签；给任一 target 打标签都不会创建 content Revision。精确历史 Artifact
或 Memory citation 仍然只声明不可变 content 与 evidence，不会隐式获得历史标签快照。

## Scope 与 publication 行为

标签属于分配它们的 Scope。将 Artifact 发布或复制到另一个 Scope 时，目标 logical Artifact 以空标签集创建。该默认行为
避免泄漏客户名称、内部工作流分类或其他本地分类。调用方可以在 publication 后显式分配目标标签。

# Reference-level explanation

## Goals

第一阶段实现必须：

- 为 managed Artifact 与 logical Memory entry 提供一个标签模型；
- 用一张表持久化所有分配关系；
- 使标签变更独立于不可变 Artifact 和 Memory-entry content；
- 以确定性规范化支持精确 `all` 与 `any` 过滤；
- 正确组合标签与 current Artifact listing、Memory retrieval；
- 保留 Scope isolation、target visibility 和现有 lifecycle filter；
- 提供并发安全且幂等的 complete-set replacement；
- 暴露足够的 current exact identity，使查询结果可以被安全解析。

## Non-goals

第一阶段不定义：

- 标签颜色、描述、别名、层级、继承或标签定义 catalog；
- 由模型、Source metadata 或 content keyword 自动生成标签；
- 把标签作为授权、批准、信任、生命周期、路由或保留策略；
- 历史标签快照或标签分配 audit history；
- 通过 lineage、publication、fork、import 或 Handoff evidence 传播标签；
- revision-specific tag；
- 允许 Agent 自动修改标签的 MCP tool；
- 任意 key/value Artifact metadata。

## 术语与 target identity

`ArtifactTagTarget` 是一个 discriminated union：

```text
ArtifactTagTarget =
  ArtifactTarget {
    type: "artifact",
    family: string,
    artifact_id: string
  }
  | MemoryEntryTarget {
    type: "memory_entry",
    family: "memory",
    artifact_id: string,
    entry_id: string
  }
```

`ArtifactTarget` 标识一个 logical Artifact lifecycle，并刻意省略 `revision`。`MemoryEntryTarget` 标识一条 logical
Memory entry，并刻意省略 Memory Revision 与 `entry_version_id`。所属 `artifact_id` 保持显式，因为 `entry_id` 的作用域
是其 Memory Artifact。

canonical persistence form 使用 `target_id`：

| Target | `family` | `artifact_id` | `target_type` | `target_id` |
| --- | --- | --- | --- | --- |
| Experience | `experience` | Experience ID | `artifact` | 相同 Experience ID |
| Skill | `skill` | Skill ID | `artifact` | 相同 Skill ID |
| Handoff | `handoff` | Handoff ID | `artifact` | 相同 Handoff ID |
| 整个 Memory | `memory` | Memory ID | `artifact` | 相同 Memory ID |
| Memory entry | `memory` | 所属 Memory ID | `memory_entry` | entry ID |

新的 nested resource kind 不能复用 `memory_entry` 或重载 `target_id`。它们需要在后续 contract 中定义显式 target type
和 validation rule。

## 标签值与规范化

提交的标签必须满足以下全部条件：

- 是 1 至 64 个 Unicode code point 的字符串；
- 没有前导或尾随空白；
- 不包含 Unicode control、surrogate 或 unassigned code point；
- normalized key 不超过 128 个 Unicode code point；
- 完整提交集合最多包含 32 个标签。

PowerContext 将提交值保留为用于展示的 `tag`，先应用 Unicode NFC，再应用 Unicode default case folding，生成
`tag_key`。它不会折叠内部空白、拆分标点、解析 `/`、翻译、词干化或推断层级。`tag` 与 `tag_key` 都在规范化后校验。

两个拥有相同 `tag_key` 的提交标签视为重复，整个请求会被拒绝；Server 不会静默选择某种展示拼写。后续成功替换可以只
改变保留的展示拼写，同时保持相同 `tag_key`。

标签按 `tag_key` 的 UTF-8 byte 升序排列。响应和 digest 计算都使用该顺序，避免数据库 collation 改变公开行为。

## Persistence

共享关系型 schema 只新增一张业务表：

```text
pc_artifact_tags
  scope_id       identity string, not null
  family         identity string, not null
  artifact_id    identity string, not null
  target_type    identity string, not null
  target_id      identity string, not null
  tag_key        identity string, not null
  tag             display string, not null
  assigned_at    UTC timestamp, not null

  primary key (
    scope_id,
    family,
    artifact_id,
    target_type,
    target_id,
    tag_key
  )

  foreign key (scope_id, family, artifact_id)
    references pc_artifact_heads (scope_id, family, artifact_id)
    on delete cascade

  check target_type in ('artifact', 'memory_entry')
  check target_type != 'artifact' or target_id = artifact_id
  check target_type != 'memory_entry' or family = 'memory'
```

该表包含以下 secondary indexes：

```text
(scope_id, family, tag_key, target_type, artifact_id, target_id)
(scope_id, tag_key, family, target_type, artifact_id, target_id)
```

primary key 支持加载一个 target 的标签；第一个 secondary index 支持 family-specific 过滤，第二个支持 Scope 内跨 Family
查询。实现必须使用 binary identity comparison 或应用构造的 `tag_key`，不能依赖数据库默认大小写或 locale collation。

每一行都强制所属 Artifact foreign key。对于 `memory_entry`，repository 必须在同一个 transaction 中锁定所属
`(scope_id, family="memory", artifact_id)` Artifact head，加载其指向的精确 Revision，并根据该 Revision 的权威
`MemoryContent.manifest.entries` 校验 `entry_id`，再修改分配关系。manifest 中 `active` 和 `inactive` entry 都是有效
target。当前 manifest 中不存在的 entry 必须被拒绝，即使旧 Revision 或不可变 entry-version row 中仍保留该 entry。

`pc_memory_entry_heads` 只包含 active search projection。停用 entry 会删除它的 projection，但其逻辑身份与内容仍保留在
权威 manifest 中。标签读取与变更不能要求该表中存在对应 row，标签分配也不能通过 foreign key 引用该表。projection
清理与重建必须保持标签分配不变。

inactive Memory entry 与 deprecated 或 retired Artifact 保留其分配关系。标签查询先应用现有 visibility 和 lifecycle
selection，再返回结果。拥有 target write authority 的调用方可以重新组织标签，而不会重新激活或修订 content。

## 标签集与 digest

`ArtifactTagSet` 包含：

```json
{
  "scope_id": "vehicle-assistant",
  "target": {
    "type": "memory_entry",
    "family": "memory",
    "artifact_id": "memory",
    "entry_id": "mem_ent_123"
  },
  "tags": ["cockpit", "customer-a"],
  "tag_digest": "sha256:..."
}
```

`tag_digest` 是对象 `{"tags": [...]}` 的 RFC 8785 canonical JSON 的 SHA-256 digest；标签按 canonical `tag_key`
顺序排列，每个 array item 使用保留的展示字符串。空集合也有稳定 digest。它是 tag set 的 content checksum，不是
client 可见的 compare-and-swap token、Artifact content digest、Memory entry content hash 或 authorization generation。

HTTP operation 使用绑定完整 logical target identity 与 `tag_digest` 的 opaque ETag。client 不能假定 ETag 等于、包含或
可以由 `tag_digest` 重建。

## Repository contract 与 transaction

tag repository 暴露三个操作：

```text
get(scope_id, target) -> ArtifactTagSet

replace(
  scope_id,
  target,
  expected_tag_digest,
  tags
) -> ArtifactTagSet

query(
  scope_id,
  tags,
  match,
  families,
  target_types,
  lifecycle_selection,
  limit,
  cursor
) -> ArtifactTagPage
```

`replace` 在一个 transaction 内执行以下步骤：

1. 解析并授权 target，同时不返回隐藏的存在性细节。
2. 锁定所属 Artifact head，加载其指向的精确 Revision；对于 Memory entry，根据该 Revision 的 manifest 校验
   `entry_id`，接受 `active` 或 `inactive` 状态。
3. 加载完整 current tag set 并计算 digest。
4. expected digest 不匹配时，按 precondition failure 拒绝请求。
5. 校验并规范化完整 replacement set。
6. 删除 replacement 中缺少的分配，并插入或更新保留的展示值。
7. 返回 canonical set 与新 digest。

即使 current tag set 为空，锁定已有所属 head 也能串行化 replacement。实现不能依赖锁定零行 assignment，也不能依赖
process-local lock，因为二者都不能提供所需的分布式 compare-and-swap 行为。

如果当前 canonical set 与请求集合相同，`replace` 幂等成功，并且不修改任何 row 或 `assigned_at` 值。

`get` 对 Memory entry 使用相同的权威 manifest 成员校验规则。授权调用方可以读取、替换或清空 inactive entry 的标签，
无需重新激活 entry，也不要求存在 search projection。

## HTTP contract

第一阶段 contract 在 RFC 1437 建立的 Scope resource tree 下新增五个 operation：

| Method | Path | operationId | Purpose |
| --- | --- | --- | --- |
| `GET` | `/v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}/tags` | `get_artifact_tags` | 读取 Artifact current tags |
| `PUT` | `/v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}/tags` | `replace_artifact_tags` | 替换 Artifact 完整 tag set |
| `GET` | `/v1/scopes/{scope_id}/artifacts/memory/{artifact_id}/entries/{entry_id}/tags` | `get_memory_entry_tags` | 读取 Memory entry current tags |
| `PUT` | `/v1/scopes/{scope_id}/artifacts/memory/{artifact_id}/entries/{entry_id}/tags` | `replace_memory_entry_tags` | 替换 Memory entry 完整 tag set |
| `POST` | `/v1/scopes/{scope_id}/artifact-tags/query` | `query_artifact_tags` | 按精确 tag 检索可见 current targets |

两种 target 形状使用相同的 `ArtifactTagSet` schema、validation、authorization rule 与 repository。额外的
`entries/{entry_id}` path segment 只表达 containment，不创建第二套标签模型或表。path 只命名 current logical target；
精确 Artifact Revision 与 Memory entry-version path 不提供 tag subresource。

### Get

两个 GET operation 都返回 `200 ArtifactTagSet` 和 opaque `ETag`。它们支持 `If-None-Match`，匹配时返回没有 body 的
`304 Not Modified`。没有任何 assignment 的可见 target 返回 `200`、空集合和 ETag。缺失或不可见的 target 与读取该
target 一样返回 `404`。

### Replace

两个 PUT operation 都接收完整 replacement set：

```json
{
  "tags": ["Customer-A", "cockpit", "verified"]
}
```

`If-Match` 是必需 header。server 将 opaque validator 解析为预期的 target-bound tag state，执行 repository replacement，
然后返回 `200 ArtifactTagSet` 和新 ETag。缺少 `If-Match` 返回 `428 Precondition Required`；不匹配返回
`412 Precondition Failed`。response 不向不能读取 target 的调用方泄露 current ETag 或 tag value。PUT 不创建
Artifact Revision。

### Query

```json
{
  "tags": ["customer-a", "verified"],
  "match": "all",
  "families": ["memory", "experience", "skill"],
  "target_types": ["artifact", "memory_entry"],
  "include_inactive": false,
  "limit": 50,
  "cursor": null
}
```

`tags` 包含 1 至 16 个 normalized 后唯一的值。`match` 默认为 `all`。省略 `families` 和 `target_types` 表示选择
全部支持值。`include_inactive` 默认为 false，并且永远不会绕过授权；它只为已经能够 inspect inactive content 的调用方
扩展 lifecycle selection。

每个 page item 包含 logical `target`、所有 current tags，以及一个 current exact content reference：

```json
{
  "target": {
    "type": "memory_entry",
    "family": "memory",
    "artifact_id": "memory",
    "entry_id": "mem_ent_123"
  },
  "current": {
    "memory_ref": {
      "family": "memory",
      "artifact_id": "memory",
      "revision": 12
    },
    "entry_id": "mem_ent_123",
    "entry_version_id": "mem_ver_456"
  },
  "tags": ["Customer-A", "cockpit", "verified"]
}
```

Artifact target 的 `current` 使用 `ArtifactReference`；Memory entry target 使用 `MemoryCitation`。item 按
`(family, target_type, artifact_id, target_id)` 的 UTF-8 byte order 排序。opaque cursor 绑定 Scope、normalized tag
key、match mode、所选 family、target type、lifecycle selection、caller、expiration 和最后一个 ordering key。非法或
filter 不匹配的 cursor 返回 `400 Bad Request`；过期 cursor 返回 `410 Gone`。

对于每个标签分配命中的 Memory entry target，只解析一次所属 Artifact head，并加载该精确 Revision 的 manifest。使用
manifest entry 的 state 进行 lifecycle selection，再用它的 `entry_version_id` 和同一个 Memory Revision 构造 citation。
当 `include_inactive=true` 时，符合条件的 inactive entry 即使没有 `pc_memory_entry_heads` row，也仍可被发现。不能通过
与 active projection 的 inner join 判断其存在性或解析 citation。manifest 成员校验、lifecycle selection 和 authorization
必须在 page limit 之前应用。

page 之间发生的标签变更可能改变成员关系。pagination 为每次查询提供确定性 keyset traversal，但不承诺跨请求数据库
snapshot。

## 现有 list 与 search 集成

以下现有 request surface 增加可选 filter，使用相同的 normalized `tags` 和 `match` 语义：

- RFC 1437 的 `GET /v1/scopes/{scope_id}/artifacts/{family}` 增加可重复的 `tag` query parameter 和可选的
  `tag_match=all|any`；
- Memory entry listing 增加可选 `tag_filter` request field；
- Memory search 增加可选 `tag_filter` request field。

parameter 或 field 默认缺失，因此保留现有行为。filter 存在时必须至少包含一个标签；没有 `tag` 时设置 `tag_match` 属于
非法请求。Artifact-head listing 只匹配 `artifact` target；Memory-entry list 和 search 只匹配 `memory_entry` target。
Artifact-list cursor 还要绑定 normalized tag 与 match mode；RFC 1437 的非法、不匹配和过期 cursor status 保持不变。

Memory-entry listing 在 `include_inactive=true` 时，使用与专用 tag query 相同的 manifest 成员校验、state 与 citation
规则。Memory search 保持现有的 active-entry eligibility；inactive 条目的 catalog discovery 不会使它进入搜索结果。

现有 Artifact-list、Memory-entry-list 和 Memory-search item schema 不新增 `tags` 字段；tag filter 只改变 eligibility。
专用 tag query 包含 current tags；需要 current catalog metadata 时，调用方可以 GET logical target 的 tag subresource。
精确历史 Artifact Revision 和 Memory entry-version response 不新增 `tags` 字段。

Memory search 在两个 FTS 和 vector candidate query 内、channel limit 前应用 tag eligibility。Hybrid search 在 fusion 与
reranking 前，对两个 channel 应用同一个 eligible target set。无法在 top-k 前应用过滤的 backend 必须报告 combined mode
不可用，不能静默 over-fetch 并返回不完整结果。

本 RFC 不给自动 `PreparedContext` assembly 增加标签约束。后续用例可以新增 typed selection profile，但标签不会因为
存在而进入 model prompt。

## Authorization 与 trust boundary

`scope_id` 是业务分区，不是 authority 证明。标签 operation 使用与 target resource 相同的 Server authentication 和
authorization boundary：

- 读取标签需要 target read 权限；
- 替换标签需要修改 target catalog metadata 的权限；
- query result 只包含 principal 可以发现和读取的 target；
- `include_inactive` 不会扩大资源访问范围。

初始实现可以把 metadata mutation 映射到现有 target write authority。deployment 不能从标签值推导访问权、用标签创建
grant，或用标签代替 Access Control Resource Profile。如果后续产品需要在没有 content write authority 的情况下委派
taxonomy management，则需要独立 action 与 audit 设计。

标签是不可信的 display string。Dashboard rendering 必须转义标签，不能把它解释为 HTML、Markdown、URL、command 或
CSS class。Search 与 application code 必须使用 bound parameter。本 RFC 永远不会执行标签，也不会把标签注入 Agent
instruction。

## Publication、import 与 lineage

标签分配是 Scope-local catalog state，不参与：

- `ArtifactLineage`；
- content 或 package digest；
- publication digest；
- Source evidence；
- Candidate approval；
- managed Skill package metadata。

发布、复制、导入或 fork Artifact 都不会复制 assignment。授权 publisher 可以在写入前看到 source tag，但不能把它们当成
content provenance。目标分配是独立的授权写入。

## Compatibility 与 migration

关系型 initializer 为每个受支持 database profile 新增 `pc_artifact_tags`。现有 Artifact 和 Memory entry 不需要 backfill，
行为等同于拥有空标签集。

任何 migration 都不会从 `ContentSource.metadata`、`SkillContent.metadata`、Memory `kind`、lifecycle state、review
status 或 integration provenance 复制值。这些字段具有不同的 authority 和语义。

现有 list 与 search request 的所有新增字段都是 optional。既未发送 Artifact-list parameter（`tag` 与
`tag_match`），也未发送 Memory request field（`tag_filter`）的旧 Client 保持 current result 语义。新 operation 与
schema 添加到 `openapi/powercontext.yaml`；generated Python 和 integration contract 必须通过仓库正常 contract
workflow 重新生成。

## Observability

Server 可以记录 operation outcome、target type、family、提交标签数量、过滤标签数量、match mode、result count 和
latency。log、trace、metric 与 error message 不能记录 raw tag value。只有 deployment policy 允许时，才能用 normalized
tag digest 进行关联。

当普通 authenticated audit boundary 可用时，标签 mutation 必须使用该边界。本 RFC 不增加 historical assignment
table；需要完整标签变更账本的 deployment 必须禁用该能力，或在声称拥有该保证前通过后续设计增加账本。

## Delivery plan

实现分为两个可评审的 vertical slice：

1. 增加 target model、normalization、共享表和 repository、ETag-guarded read/replace 与 query、OpenAPI generated
   contract，以及 SQLite 与 OceanBase/seekDB 的确定性行为测试。
2. 增加 current Artifact 与 Memory-entry list filter、pre-top-k Memory search filtering，以及最小 Dashboard 标签
   editor 与精确筛选。

只增加表不代表功能完成。首个 customer-visible release 需要两个 slice 都完成，使用户能够分配标签、看到标签、通过标签
检索 target，并在不修改 Artifact content 的情况下移除标签。

## Acceptance criteria

只有以下可观察场景全部通过，才视为本 RFC 已实现：

1. 调用方通过相同 tag-set 语义给 Experience、Skill、Handoff 和 whole-Memory Artifact target 分配和读取标签。
2. 调用方给一条 Memory entry 分配独立标签集，不改变同一 Memory 中其他 entry 的标签。
3. 修订 Artifact 或 Memory entry 会保留其 logical target tag，单纯 Revision 变化不会改变任何标签。
4. 替换标签不会改变 Artifact Revision、content digest、lineage、Memory entry version、FTS text 或 embedding。
5. empty-set replacement 删除所有 assignment，随后仍可以读取为空 tag set。
6. `all` 和 `any` matching 跨 Family 返回正确 target，并拒绝 normalized 后重复的输入。
7. Artifact listing 和 Memory entry listing 在应用标签过滤时保留现有 lifecycle default。
8. FTS、vector 和 hybrid Memory search 在各自 candidate limit 前应用 tag eligibility，不返回未标记结果。
9. 即使初始标签集为空，过期 tag-set ETag 也不能覆盖并发 replacement。
10. pagination 拒绝用不同 normalized filter 重用 cursor，并返回确定性 keyset order。
11. principal 在没有相应 target authority 时，不能发现、读取或修改该 target 的标签。
12. publication 到另一个 Scope 时不创建目标 assignment，也不能隐式暴露 raw source tag。
13. raw tag value 不进入 telemetry，Dashboard 将恶意值作为 inert text 渲染。
14. 现有未过滤 API 行为和精确历史 content response 保持不变。
15. schema、repository、HTTP contract、generated Client 与受支持 backend test 通过仓库标准命令。
16. 带标签的 Memory entry 停用且其 search projection 消失后，授权调用方仍能读取和替换它的标签。匹配的 tag query 与
    Memory-entry list 在 `include_inactive=true` 时返回该 entry，citation 从 current manifest 解析；默认请求和
    Memory search 不返回它。重建 projection 后，这些行为与标签分配保持不变。随后清空标签会返回空 tag set，使该 entry
    不再命中相应 tag query，同时保持其 inactive 状态、Memory Revision 和 entry version 不变。
17. 当前 manifest 中不存在的 Memory-entry tag target 会被拒绝，即使其不可变 entry version 仍保留在存储中。授权 GET
    返回 `404`，replacement 不创建 assignment，tag query 不返回该 target。

# Drawbacks

关系型 foreign key 可以校验所属 Artifact，但无法强制 logical Memory entry 属于该 Artifact 的 current manifest。
Repository 必须在 transaction 内校验该成员关系。universal catalog-item registry 可以提供一个外键，但会在没有其他功能
需要它之前增加另一层持久身份和另一张表。

无法重建过去某一时刻的 current logical tag。精确 Artifact content 仍可复现，但 tag set 只是 current catalog state。
需要历史 taxonomy audit 的 deployment 需要额外 event 或 history 设计。

两个 query index 会增加写入和存储成本。每个 target 有界的标签数量让成本可预测，而且标签写入频率预计远低于读取。

每个受支持 Memory search backend 都必须实现 tag pre-filtering。虽然这比过滤最终 hit 工作量更大，但它是保证 top-k
正确性的必要条件。

# Rationale and alternatives

## 把标签保存在 Artifact content 中

该方案可以获得不可变历史标签，但会把一次分类修改变成 content Revision，并在可复用知识没有变化时改变 content digest、
lineage expectation、CAS behavior 和 derived index。因此不用于 user-managed catalog tag。

## 给 `pc_artifact_heads` 增加 JSON 标签列

对于整个 Artifact，这只是一个较小的 schema change，但它不能区分标准 one-Memory-per-Scope 模型中的单条 entry。
此外，SQLite 与 MySQL-compatible backend 对可移植的 indexed `all`/`any` query 支持不同。因此拒绝该方案。

## 分别给 Artifact 和 Memory entry 增加一张表

分开的 assignment table 可以简化 family-local join，但会分裂 cross-family query，并重复标签规范化、mutation、
pagination 与 API behavior。除非再增加持久的 logical-entry registry，否则独立的 Memory-entry tag table 仍需要校验
current manifest。单张多态表保留一个 assignment contract，并承担相同的显式 application-level Memory-entry invariant。

## 增加 universal catalog-item registry

registry 可以给每个 nested 与 top-level resource 一个统一 ID，并让标签只引用一个 parent table，但它需要为所有现有资源
新增 lifecycle、migration、ownership、deletion 与 synchronization 语义。第一阶段标签能力不足以证明该抽象的必要性。

## 把每条 Memory entry 重构为 Artifact

该方案会让物理标签 target 统一，却会替换现有 Memory Manifest、atomic collection Revision、entry-version、citation、
flush 和 index 模型。相对客户需求改动过大，因此拒绝。

## 把标签实现为任意 metadata key/value

当前需求是集合成员关系与精确过滤。通用 nested metadata object 需要额外的 type、operator、indexing、conflict 与
authorization 语义。本 RFC 不需要这些能力；后续 key/value metadata 功能不能静默重新解释标签。

## 不做任何改变

用户只能继续把分类编码进 content、维护外部表格或依赖有歧义的文本查询。这些方案都无法提供一致、Scope-aware、
cross-family 的检索 contract。

# Prior art

PowerContext 已经将不可变 Artifact Revision 与可变 current head、可重建 search projection 分开。RFC 0014 定义 Memory
entry identity 与 exact citation；RFC 0019 定义标准 profile 中每个 Scope 一个 current Memory Artifact；RFC 0048 将
Handoff 定义为 self-contained Artifact lifecycle；RFC 0051 将 Experience 与 managed Skill 定义为独立 Artifact
Family。本 RFC 把 logical identity 与 immutable content 的同一分离原则应用到 user-managed classification。

RFC 1396 将 resource authorization 与 Artifact content 分离，并强调 Scope identity 不是 authorization。标签遵守该边界：
它可以帮助用户找到资源，但永远不能决定用户是否可以访问资源。

RFC 1437 建立 Scope-owned Artifact URI tree、mutable current representation 的 opaque HTTP validator，以及绑定 caller
与 query 的 expiring cursor。本 RFC 为该 URI tree 增加 logical-target tag subresource，并扩展 Artifact listing；它不改变
exact Revision response，也不把 `tag_digest` 当成 Artifact ETag。

本 RFC 不以任何外部系统为规范依据。常见代码仓库和 issue tracker 表明，可变 label 可以组织 immutable 或 versioned
content；但 PowerContext 的 Memory-entry containment 与 exact citation model 需要本文定义的 target contract。

# Unresolved questions

没有未决问题阻塞第一阶段 contract 的接受。

实现 PR 仍需确认 bounded `all` filter 的 backend-specific query plan，并选择符合仓库命名长度限制的精确 index name。
这些属于实现验证细节，不能改变公开 normalization、matching、ordering 或 pre-top-k filtering 语义。

以下问题刻意排除在本 RFC 之外：

- 组织是否需要 managed tag definition、颜色、描述、别名或 rename operation；
- tag mutation 是否需要可独立委派的 authorization action；
- 某些 publication workflow 是否应显式提供复制所选标签的选项；
- 自动分类是否可以安全地提议、但不能静默分配标签；
- 是否需要完整 historical tag-assignment ledger。

# Future possibilities

后续 RFC 可以增加 Scope-local tag catalog，提供描述、展示颜色、别名、usage count、受控 rename 或委派 taxonomy
management。该 catalog 用于描述标签；`pc_artifact_tags` 仍然是 assignment relation。

另一个扩展可以允许用户保存由 tag、family、lifecycle state 和 text query 组成的 named search view。saved view 必须
保持为 query，不能成为 authorization policy。

模型辅助分类可以通过 reviewed Candidate-like flow 提议标签。模型不能静默分配标签，在授权用户接受前，提议标签保持
untrusted。

如果多个 nested resource type 都需要 tag、access control、favorite、comment 和其他 catalog metadata，项目届时可以
考虑 universal logical-resource registry。该决策应基于多个经过验证的用例，而不是由本 RFC 提前投机引入。
