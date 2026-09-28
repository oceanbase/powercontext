---
title: Topic Memory 通用 Artifact 接口补全
---

# Topic Memory 通用 Artifact 接口补全

- Proposal Name: `topic_memory_generic_api_completion`
- Start Date: 2026-09-15
- Status: Proposed
- 代码核对基线：`9ff03eedffe6e73a54bb3adf40d42badd7cacdf7`
- 关联实现：[Topic Memory 标准读取接口 #1550](https://github.com/oceanbase/powercontext/pull/1550)
- 业务目录：[货拉拉记忆需求](https://yuque.antfin.com/obopensrc/knowledge_sharing/tccng2l0qzc6iuw0)

## 1. 摘要

本 RFC 提议为 `topic-memory` 补齐通用 Artifact 创建、整体替换、标签读写、标签查询、列表标签过滤和跨 Scope 发布能力。调用方可以直接提交完整主题内容，成功响应代表内容、发布记录和当前部署启用的检索通道已经一并提交。

Topic Memory 继续按 Scope 管理权限。手工写入、自动生成和跨 Scope 发布复用 Topic Memory 自身的原子发布逻辑，保证通用读取与专用检索看到一致的主题版本。

本文定义目标行为和后续实现验收标准；接口代码实现独立开展。

## 2. 当前能力与问题

在上述基线上，`list_artifacts`、`get_artifact`、`list_artifact_revisions`、`get_artifact_revision` 已接受 `topic-memory`。列表只展示已发布的当前主题，按发布时间倒序返回，并提供标题、概要、发布时间和直接 Source 数量。

尚缺的能力如下。前六项是明确的接口限制；跨 Scope 发布需要修复完整性和权限适配，不能仅凭请求体接受 family 就视为可用。

| 能力 | 当前限制 | 本 RFC 目标 |
| --- | --- | --- |
| `create_artifact` | 请求类型和 management writer 不包含 Topic Memory | 手工创建完整、可检索的 revision 1 |
| `replace_artifact` | 路由枚举和 management writer 不包含 Topic Memory | 以 If-Match 为前提提交完整下一版本 |
| `get_artifact_tags` | TaggableArtifactFamily 不包含 Topic Memory | 读取逻辑主题的标签集合 |
| `replace_artifact_tags` | 同上 | 独立更新标签，保留内容版本 |
| `query_artifact_tags` | 查询 family 范围不包含 Topic Memory | 支持主题标签查询与跨 family 查询 |
| `list_artifacts` 标签过滤 | Topic Memory reader 主动拒绝 tag_filter | 在分页前按标签筛选 |
| `publish_artifact` | 通用复制不维护 Topic Memory 发布记录及检索投影；鉴权使用 Artifact 级分享权限 | 将精确版本原子发布为目标 Scope 中的完整主题 |

当前 `ArtifactPublicationApplication` 只显式拒绝 memory、profile、prompt；`ArtifactRepository.copy_exact` 创建基础 revision、head 和跨 Scope 来源信息。Topic Memory 还要求 revision publication、active topic、active chunks 和检索索引。仅复制基础数据会破坏存储不变量；服务是否能执行到复制步骤还取决于鉴权配置。本 RFC 要求对这条路径做完整支持及回归测试。

## 3. 范围

### 3.1 包含

- 上表七项能力及对应 OpenAPI、HTTP、Python SDK、运行时装配、权限和错误映射。
- SQLite、OceanBase 两种后端；FTS 与启用向量的部署形态。
- 手工更新与后台 Topic Memory Worker 的并发一致性。
- 既有四个读接口与专用 search/get/flush 的回归验证。

### 3.2 不包含

- 通用删除、归档、批量导入、PATCH 局部修改或新的主题锁定开关。
- Artifact Candidate 的审核流程；其 family 模型是独立的 experience/skill/profile 提案模型。
- Memory Entry 专用接口；Topic Memory 的 chunk 没有公开 entry identity。
- Topic Memory 专用 search 的标签过滤参数；本次过滤覆盖通用 list 与 tag query。
- 新的全局 family registry 或自动生成能力框架。

## 4. HTTP 契约

下表中的 `S`、`A` 分别表示 Scope ID、Artifact ID。

| 操作 | HTTP 路径 | 成功语义 |
| --- | --- | --- |
| Create | `POST /v1/scopes/{S}/artifacts` | 201，ArtifactCreated、Location、内容 ETag |
| Replace | `PUT /v1/scopes/{S}/artifacts/topic-memory/{A}` | 200，ArtifactRevision、新内容 ETag |
| Get tags | `GET /v1/scopes/{S}/artifacts/topic-memory/{A}/tags` | 200，ArtifactTagSet、标签 ETag；条件命中为 304 |
| Replace tags | `PUT /v1/scopes/{S}/artifacts/topic-memory/{A}/tags` | 200，完整标签集合、新标签 ETag |
| Query tags | `POST /v1/scopes/{S}/artifact-tags/query` | 200，ArtifactTagPage |
| List with tags | `GET /v1/scopes/{S}/artifacts/topic-memory?tag=运维&tag_match=all` | 200，按发布时间排序的 ArtifactPage |
| Publish | `POST /v1/artifact-publications` | 201，源、目标精确地址及内容 digest |

### 4.1 创建与替换请求

Create 使用以下结构：

```json
{
  "family": "topic-memory",
  "content": {
    "title": "订单超时处理",
    "summary": "订单超时后先确认支付状态，再决定取消或人工介入。",
    "detail": "## 处理步骤\n1. 查询支付结果。\n2. 已支付订单转人工处理。\n3. 未支付订单按规则取消。"
  }
}
```

Replace 使用同样的 `content` 对象，family 来自路径，并必须提供当前内容的 `If-Match`。三个字段都必填，分别沿用 512、8,000、125,000 字符上限，拒绝空白文本及不属于请求契约的额外字段。Replace 是完整替换，不自动合并缺失字段。

服务端生成 Artifact ID、revision、发布时间和检索投影。客户端不能提交这些内部状态，也不能通过此请求任意指定 lineage、source_count、embedding 或 chunk。手工写入不调用文本生成模型，但向量部署需要调用 Embedding 服务。

非空白内容可能不包含 Analyzer v1 可索引的词项，例如标题和概要只有 Emoji 或标点。此类内容仍可创建、替换和发布；内部 `topic_searchable_text` 可以是空字符串（不允许为 NULL），全文检索对其不产生命中，详情分块或向量通道仍按实际内容工作。真正的空白字段仍由请求校验拒绝。

Create 保持现有通用接口的非幂等语义：相同标题或相同正文的两次请求可以创建两个主题，标题不是唯一键。客户端遇到响应丢失不得假定重试会返回同一 Artifact。Replace 沿用 ETag 并发保护；即使内容相同，只要前提匹配，也提交下一版本。

### 4.2 Schema 扩展

- 为 CreateArtifactRequest 增加 CreateTopicMemoryArtifactRequest，并在 family discriminator 中加入 `topic-memory`。
- 为 ReplaceArtifactRequest 增加 ReplaceTopicMemoryArtifactRequest，复用 TopicMemoryContent 的三个业务字段和约束。
- 在通用写入相关 family schema 与 ArtifactCreated 响应中接纳 Topic Memory；读取继续使用 ArtifactReadFamily。
- TaggableArtifactFamily 增加 `topic-memory`。TagQuery 默认 families 同步扩展；OpenAPI families 数量上限由 4 调整为 5，显式选择旧 family 的行为保持不变。
- 审计 BaseArtifactFamily 的所有使用处，逐项处理权限、响应转换和 writer 选择，避免仅放开枚举就进入不适用的默认分支。
- 修改 `openapi/powercontext.yaml` 后执行 `make api-generate`；生成目录不得手工编辑。核对 SDK 调用签名、导出类型和由契约生成的工具描述。

## 5. 写入过程与原子性

### 5.1 准备与提交分离

请求处理顺序为：鉴权和参数校验 → 读取必要的精确版本 → 准备 chunk/FTS 文本/向量 → 短事务内重新核对前提并发布 → 返回响应。

新增 TopicMemoryManagementWriter，显式装配到现有 FamilyManagementWriterRegistry。为需要检索准备的写入提供一个小范围、可选的异步准备步骤；现有 writer 无需整体改写。Topic Memory 的准备结果只存活于本次调用，不能放在共享 writer 的可变实例字段中。

准备阶段复用 `chunk_topic_memory_detail`、`prepare_topic_memory_projection` 及现有 Embedding 协议。网络推理必须在数据库写事务之外完成，并受既有超时和并发预算约束。服务端校验准备结果与内容完全一致；提交时重新校验部署的 retrieval shape，防止准备期间配置变化。

### 5.2 创建事务

同一事务内完成：

1. 创建通用手工写入所用的系统 Content Source，标记为 `lineage_only`、operation 为 `artifact_create`，目标 family 为 `topic-memory`。
2. 将该 Source 作为直接 lineage，调用 TopicMemoryRepository.publish_create 创建 revision 1。
3. 原子写入 head、revision publication、active topic、active chunks 和当前部署启用的全部索引。

任意一步失败时整体回滚。不得出现 API 已返回成功、但主题尚不可检索的中间状态。手工写入来源沿用现有系统来源的准入策略，不能作为普通新证据反复触发主题生成。

### 5.3 替换事务与自动生成并发

Replace 在准备前读取 head 并检查 If-Match；准备完成后，在事务中再次验证同一 revision，并执行 head CAS（仅当当前版本仍是预期版本时更新）。系统 Source 的 operation 为 `artifact_replace`，其目标 revision 与本次提交一致。

新 revision 的直接 Source 是本次系统 Content Source；上一精确 revision 通过 Artifact lineage 保留，并复用 publish_revision 的前序引用去重逻辑。历史证据通过版本链追溯，source_count 表示当前 revision 的直接 Source 数量，不是历史证据总数。

后台 Flush 与手工 Replace 必须使用同一 head CAS。若 Worker 先提交，手工请求返回 412，不能把旧的准备结果写到新 head 上；若手工请求先提交，Worker 对旧 revision 的提交必须失败，并通过既有重新读取、重新计算机制处理。

手工内容仍允许未来自动演进，不隐含永久锁定或“人工优先”规则。手工 Create 不按标题或语义自动合并主题，也不推进 Source Cursor、不消费 Worker Pending Window。实现必须核对 Worker 的冲突处理不会跳过尚未处理的证据。

## 6. 标签与列表过滤

标签归属于 `(scope_id, family, artifact_id)` 的逻辑主题，不属于某个 revision。手工 Replace 或自动生成新 revision 后标签保留；修改标签不会生成内容 revision，也不会改变 published_at 或内容 digest。

沿用现有标签规范：最多 32 个存储标签；查询最多 16 个标签；使用 NFC 与 casefold 生成匹配键；拒绝规范化后的重复值。Get tags 获取独立的标签 ETag，Replace tags 必须携带这个 ETag。内容 ETag 与标签 ETag 不能混用，过期前提返回 412，缺少必要前提返回 428。

TopicMemoryRepository.browse_current 增加可选 TagFilter，通过已有 tag_predicate 在 SQL 的 LIMIT 之前过滤。取当前页与判断下一页必须应用相同标签条件；禁止先分页再用 Python 丢弃不匹配项。

保留 `published_at DESC, artifact_id ASC, revision DESC` 顺序和已发布当前 head 约束。带过滤条件的签名游标绑定 Scope、family、endpoint、排序、规范化标签键和 all/any 模式；换 Scope、换标签或换匹配模式复用游标均被拒绝。无过滤的已有游标维持原绑定格式以保留其有效期内兼容性。

分页表示实时集合上的边界，不是跨请求数据库快照。分页期间主题更新到更靠前的位置或标签变化时，客户端需重新加载以获得当前完整集合。Query tags 仍使用通用标签查询顺序，不改成发布时间顺序；返回的引用必须与实际当前 head 对齐，且不能暴露未授权 Scope 的结果。

## 7. Scope 权限

| 操作 | Topic Memory 所需权限 |
| --- | --- |
| 四个标准读取、Get tags、Query tags、List 标签过滤 | 当前 Scope 的 `scope.read` |
| Create | 当前 Scope 的 `scope.contribute` |
| Replace、Replace tags | 当前 Scope 的 `scope.admin` |
| 跨 Scope Publish | 源 Scope 的 `scope.admin` 且目标 Scope 的 `scope.admin` |

Topic Memory 是 Scope 共享知识，自动生成内容也没有单一作者 owner，因此这次不引入 Topic Memory 的独立 Artifact owner 或单主题分享授权。Create 与 Publish 成功后的 owner 建立钩子必须按此语义跳过 Topic Memory；否则可能在数据提交后因不支持的权限资源导致响应失败。

发布的源端管理权限代表允许把整个主题正文导出到另一个 Scope；源端只读权限不足以发布。目标端管理权限控制引入外部主题。鉴权解析器必须针对 Topic Memory 返回 Scope 资源，并保留其他 family 的现有权限规则。

纯本地嵌入式调用继续遵循既有可信宿主模型；HTTP/MCP 等受保护入口必须应用上述策略。验收覆盖 enabled/shadow/disabled 模式，验证启用鉴权时的允许与拒绝路径。Source、lineage 和发布记录不自动授予来源 Scope 的读取权限。

## 8. 跨 Scope 发布

### 8.1 精确复制与目标状态

Publish 沿用 PublishArtifactRequest，源地址必须包含 scope_id、family、artifact_id、revision；可以选择有发布记录的历史 revision。目标生成新的 Artifact ID，revision 从 1 开始，内容与源精确版本一致；不合并目标同名主题、不覆盖已有主题。

发布前验证源 revision 具备合法 Topic Memory 发布记录，并基于目标运行环境的 retrieval shape 重新准备投影。不能直接复制源 chunk/vector 索引行。目标 published_at 使用目标事务时间，列表顺序按目标发布时间确定。

跨 Scope 来源继续使用 `publication_source`、`publication_digest` 与 ArtifactPublication 记录，保持既有多次发布的来源链语义。源 Scope 的 SourceRef 和 ArtifactRef 不直接写成目标本地 lineage；因此纯发布副本的直接 sources 为空、source_count 为 0。源标签、权限、Worker cursor/pending 状态均不复制。

### 8.2 原子提交与幂等

为 TopicMemoryRepository 增加 family-owned 的跨 Scope 发布方法，在一个事务内完成基础 Artifact 复制、Topic Memory 激活及通用 ArtifactPublication 记录写入。该方法可内部复用 copy_exact 和激活逻辑，但调用方不能先提交基础复制再补投影，也不直接调用 repository 私有方法。

复用现有 target_scope_id 与 idempotency_key 唯一约束。同一键、同一完整请求返回原目标；同一键、不同源版本或请求内容返回 409。并发请求最多留下一个完整目标，不产生孤儿 Artifact。重复命中应在昂贵投影准备前读取已有结果，并在事务内重新检查；已有成功结果不应依赖 Embedding 服务再次可用。

源 revision 不存在时返回 404；源 revision 缺失应有发布记录属于存储完整性错误，沿用错误映射且禁止写目标。任意索引或发布记录写入失败都必须回滚目标的全部状态。成功后以专用 get/search、标准 list/get 和服务重启后的完整性校验验证可用性。

## 9. 错误与资源预算

| 情况 | 约定 |
| --- | --- |
| 内容、family 或标签组合不合法 | 422，沿用已有参数错误格式 |
| 缺少 Replace / Replace tags 前提 | 428 |
| 内容或标签版本已变化 | 412；客户端重新读取后决定是否重试 |
| 无目标主题或无指定源版本 | 404，按现有鉴权顺序处理 |
| 游标篡改、绑定不一致 | 沿用现有 InvalidCursorError 映射 |
| 游标过期 | 410 |
| 发布幂等键冲突 | 409 |
| Embedding 服务不可用或超时 | 沿用既有推理错误映射，失败时零持久化写入 |
| 部署形态冲突或存储不变量损坏 | 沿用既有能力/完整性错误，禁止静默降级为不完整主题 |

手工正文上限与现有 TopicMemoryContent 一致；chunk 数量与向量维度继续由 family 约束。向量部署写入延迟包含 Embedding 耗时，响应仍为同步 201/200。本次不引入 202 任务型协议；超过预算应明确失败，不提前返回成功。

## 10. 实现划分

| 区域 | 所需改动 |
| --- | --- |
| `openapi/powercontext.yaml`、HTTP 生成代码和 SDK | 新写请求、family 范围、标签查询上限及导出类型 |
| `builtin/persistence/family_management.py`、`records.py` | Topic Memory writer、事务外准备、系统 Source 与 CAS |
| `builtin/persistence/topic_memory.py` | 带标签 browse、family-owned 跨 Scope 发布方法 |
| `builtin/persistence/artifact_readers.py` | 移除标签拒绝，绑定过滤游标，统一分页条件 |
| `builtin/tags.py`、标签持久化服务 | family、默认查询范围及标签语义 |
| `builtin/publication.py` | Topic Memory 完整发布、幂等和投影准备 |
| `builtin/runtime/relational.py` | 显式注入 repository、writer、投影准备依赖 |
| `server/app.py` 及相关权限适配 | Scope 读写/发布授权，Create/Publish owner 钩子 |

实现顺序：先完成契约与权限，再完成 Create/Replace 原子写入，然后接入标签和过滤，最后接入跨 Scope 发布并执行端到端验收。所有入口复用 family-owned 状态维护逻辑，不在 HTTP handler 中拼装索引写入。

## 11. 兼容性与上线

复用 Artifact、标签及 Topic Memory 已有表。标签表的 family CHECK 约束需要扩展到 `topic-memory`：SQLite 在事务中重建标签表并保留原有数据、主键、外键和索引；OceanBase 更新对应 CHECK 约束。升级期间暂停旧实例写入，重复启动不得重复迁移或丢失标签。回滚到旧程序前需评估新增 family 数据的兼容性。开启前检查历史主题是否满足发布记录、head、active topic/chunks 与索引一致性。对于既有通用复制产生的不完整主题，单独制定诊断与修复流程；服务不得在启动时猜测来源并自动补数据。

四个已支持的读取接口、主题专用 search/get/flush 与其他 family 的读写语义保持兼容。标签查询不指定 families 时会新增匹配的 Topic Memory 项，这是有意的结果集合扩展，应在发布说明中注明；需要固定范围的客户端显式传 families。

完整代码应一起发布。若实际交付被拆分，在跨 Scope 发布完成前先对 Topic Memory 通用发布返回明确的 unsupported 错误，禁止出现仅放开请求校验的中间版本。此保护不视为完成本 RFC 的发布能力。

## 12. 验收标准

1. Create 后标准 list/get/revision 与专用 get/search 均能读取完整 revision 1；系统来源正确，进程重启后仍可检索。
2. Replace 后内容与所有索引切换到新 revision，历史版本可读取，旧内容不再以当前 head 命中；新 ETag 生效。
3. 并发 Replace、Worker/Replace 竞争各自只允许符合 CAS 的提交；冲突后不遗留 Source、revision 或部分索引。
4. 标签读写遵守独立 ETag；Replace/Flush 后标签保留，标签修改不改变内容 revision、发布时间或 Source 数量。
5. all/any、多页、空结果及发布时间相同场景过滤正确；跨 Scope/跨 filter 复用游标被拒绝；不因分页后过滤漏结果。
6. 混合 family 标签查询包含 Topic Memory；显式旧 family 查询结果不变，未授权 Scope 无法查询。
7. Publish 精确历史版本到目标 Scope 后可 list/get/search；目标 revision 为 1，标签为空、source_count 为 0、来源记录正确。
8. 重复与并发 Publish 返回同一目标；幂等键冲突返回 409；重复成功请求在 Embedding 不可用时仍可读取原结果。
9. 投影准备失败、提交过程中索引失败、发布记录失败分别验证零部分提交；失败后既有主题保持可用。
10. 权限矩阵覆盖只读、贡献者、管理员、跨 Scope 未授权访问及已撤销权限；特别验证 Topic Memory 不进入 Artifact owner/share 默认路径。
11. 覆盖 SQLite FTS、OceanBase FTS 与支持向量的部署形态，并验证重启完整性。
12. 执行 `make api-generate`、`make contract-test`、相关 HTTP/权限/持久化/E2E 测试以及 `make check`；环境不足的后端测试明确记录为未执行。

## 13. 设计取舍

采用现有 writer 与 repository 的小范围扩展，便于分别验证业务内容、并发和索引不变量。Create/Replace 不把人工文本再交给生成模型改写，返回内容与调用方提交的字段一致。Scope 管理权限用于修改已有共享知识，避免自动生成主题缺少 owner 时无法维护。

采用同步完整发布，使既有 Artifact 接口的成功响应仍然表示持久化已完成；代价是向量准备增加请求延迟。标签保持独立元数据，既不参与主题正文生成，也不改变权限。

## 14. 参考依据

- [Topic Memory 模型与分块约束](https://github.com/oceanbase/powercontext/tree/9ff03eedffe6e73a54bb3adf40d42badd7cacdf7/src/powercontext/builtin/artifacts/topic_memory)
- [Topic Memory 原子发布与检索](https://github.com/oceanbase/powercontext/blob/9ff03eedffe6e73a54bb3adf40d42badd7cacdf7/src/powercontext/builtin/persistence/topic_memory.py)
- [通用跨 Scope 发布](https://github.com/oceanbase/powercontext/blob/9ff03eedffe6e73a54bb3adf40d42badd7cacdf7/src/powercontext/builtin/publication.py)
- [OpenAPI 契约](https://github.com/oceanbase/powercontext/blob/9ff03eedffe6e73a54bb3adf40d42badd7cacdf7/openapi/powercontext.yaml)
- [Topic Memory 原始 RFC #1417](https://github.com/oceanbase/powercontext/pull/1417)
