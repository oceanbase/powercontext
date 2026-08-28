# Source 集成形态

- 提案名称：source_integration_shape
- 状态：Proposed
- 开始日期：2026-08-25
- Tracking Issue：[oceanbase/powercontext#1240](https://github.com/oceanbase/powercontext/issues/1240)
- 相关 RFC：[RFC 0019](0019_local_source_memory_runtime.md)、[RFC 0020](0020_runtime_backed_memory_remote_access.md)、[RFC 0051](0051_experience_skill_artifact_families.md)

# 摘要

PowerContext 需要为会随时间变化的外部系统定义 Source contract。GitHub issue、Notion page、Slack message 或
Linear issue 具有稳定的 provider identity，但当前值允许变化；已经被 Artifact 使用的原文必须保持精确。本 RFC
因此将可变的 Source head 与某个时间点观察到的不可变 snapshot 分开。

建议的模型分为两层：

    Source：标识外部对象，并指向最新 snapshot。
    Source Snapshot：保存某个 provider revision 和捕获时间下的确切值。

Artifact、Candidate、Handoff 以及其他 exact-evidence 消费者必须引用不可变 Source Snapshot。只有 provider URL 或
逻辑对象 ID 不是充分证据，除非 provider 保证该 revision 不可变且可重新读取。

默认读取路径可以使用最新 snapshot 进行检索和 ingest；历史 Artifact lineage 必须继续使用 Artifact 生成时采用的
snapshot。本 RFC 只定义 PowerContext 的边界；Connector 继续负责 discovery、同步、checkpoint、重试、凭证和
provider-specific change handling。

# 动机

当前 Source 实现用同一个 identity 同时表示逻辑对象和捕获值。关系数据库主键实际上是
(scope_id, source_type, source_id)，同一 key 写入第二个 payload 会冲突。这对不可变 capture 是正确的，但无法表示
可变 Source head 以及保留的历史 snapshot。

当前 SourceRef 也只有 source_type 和 source_id。因此 Artifact lineage 只能标识逻辑 key，不能标识生成 Artifact 时
实际使用的 provider revision 或 payload。provider 后续更新后，同一个引用可能解析到不同内容，或者无法再解析。

设计必须保持：最新 Source 可以变化并重新 ingest 而不改写历史证据；被 Artifact 引用的 snapshot 在 provider 变化或
不可用后仍可读取；同一个 snapshot 的重复投递幂等；connector 可以
使用 Git commit SHA、Notion edit marker、Slack message timestamp 或文件 SHA-256；现有 captured text 仍然有用；
typed Source 可以接入 persistence、Runtime、transport 和 evidence projection；connector 同步逻辑不进入 Source model。

# 设计说明

## Source head 与 snapshot

公共模型应区分可变 Source head 与不可变 snapshot 引用。名称仅为示意：

    {
      "source_type": "github-issue",
      "source_id": "oceanbase/powercontext#1240",
      "snapshot_id": "snap_01J..."
    }

可变 Source head 至少包含：

- source_type、source_id：稳定逻辑身份；
- latest_snapshot_id：当前读取和 ingest 使用的 snapshot；
- connector 所需的 locator 与 provider metadata。

Snapshot 至少包含：

- snapshot_id：PowerContext 为不可变 snapshot 生成的身份；
- source_type、source_id：稳定逻辑对象身份；
- provider_revision：provider-native revision（如果有）；
- materialization：captured 或 referenced；
- content_hash：canonical observed value 的 hash；
- captured_at：PowerContext 捕获时间；
- payload：materialized 时的 canonical captured value；
- locator：需要时保存 provider URL 或 provider-specific locator。

snapshot_id 必须只对应一份不可变 payload。相同 snapshot identity 写入不同 payload 必须冲突。provider revision
变化时生成新 snapshot 并推进 latest_snapshot_id；不得更新已经被 Artifact 引用的 snapshot payload。provider
provenance 等价时，相同 canonical payload 可以复用 snapshot。

SnapshotRef 是 exact-evidence 的 citation boundary。迁移期间，缺少 snapshot_id 的旧 SourceRef 只能解析到现有旧行
代表的那份不可变 payload。新创建的 exact evidence 必须带 snapshot_id；如果会改变历史 Artifact lineage 的含义，
两段式引用不得静默解析为 latest snapshot。

## Capture、Ref 与 Hybrid

进入 Artifact lineage 的证据默认使用 Capture。它将 canonical value 和 hash 作为 snapshot 保存在本地，因此
provider 可用性和未来编辑不会改变历史证据。Connector 可以捕获每次变化，也可以只把进入 PowerContext ingestion
boundary 的变化保存下来；一旦 snapshot 被 Artifact 引用，就必须按 retention policy 保留。

只有在 provider 明确保证 revision 不可变且可重新读取时，才允许使用 Ref。可变 URL、对象 ID 或当前 updated_at 不够。
如果 Ref 无法按记录的 revision 解析，snapshot 必须标记为 unavailable，不能静默替换为 provider 当前值。变得
unavailable 的 Ref 不能满足 Artifact 的 exact-evidence 要求，除非先物化为保留的 snapshot。

Hybrid 在 snapshot 中同时保存 provider locator/revision 和 canonical captured value。当需要外部追溯、provider
读取成本较高，或 connector 需要协调后续变更时，优先使用 Hybrid。

流程为：

    connector 发现 source
      -> 内容变化时生成带 provider revision 和 hash 的新 snapshot
      -> 推进 Source.latest_snapshot_id
      -> Artifact lineage 引用生成时采用的 snapshot

## ContentSource 与 content API

ContentSource 保持为一个具体的内置 captured-text Source。它当前实际上是每个 source ID 只有一份不可变 snapshot；未来
同一逻辑 ID 收到新内容时应允许生成新 snapshot。POST /v1/sources/content 继续作为已有调用方的兼容入口和
最小 ingestion path，不升级为 GitHub、Notion、Slack、Linear 等 provider 对象的统一表示。

新增集成应定义 typed Source 和 capture model。它们可以复用通用 snapshot persistence、hash、幂等和 citation 机制，
但 provider-specific 字段应放在 typed payload 中。

## Connector 边界

Connector 负责 discovery、provider authentication、cursor、checkpoint、polling、webhook、retry、rate limit，以及将
provider response 转换为 typed snapshot。

PowerContext 负责校验 Source 和 snapshot identity、durable snapshot storage 与幂等、按 snapshot reference
精确读取、scope isolation、Artifact foreign-key integrity、evidence projection 和 citation validation。

这部分借鉴 TencentDB-Agent-Memory 的 ISourceFetcher 和 SourceFetcherRegistry：fetcher 路由 provider protocol 并返回
provider version，core 保存 metadata 并提供 memory operation。PowerContext 还需要额外保留不可变 evidence，因为
Artifact lineage 需要更强的 replay guarantee。

# TencentDB-Agent-Memory 调研参考

本设计参考了 [TencentDB-Agent-Memory](https://github.com/TencentCloud/TencentDB-Agent-Memory)：

- MemoryKnowledge 通过 ISourceFetcher 和 SourceFetcherRegistry 路由 source protocol；
- Git 同步返回 commit hash，并保存 repo_url、branch、version、last_sync_at 等 metadata；
- Wiki source file 使用 filename 加 sha256 做增量变化检测，并独立跟踪 ingest status；
- Wiki 和 CodeGraph 使用 asset-level version counter 与 audit row 记录同步生命周期；
- MemoryCore 将 knowledge metadata 与 content/indexing service 分离，并用 versioned record 管理 memory 演进。

这些做法适合借鉴 connector 边界、provider revision metadata、content hashing、增量同步和运维审计。
TencentDB-Agent-Memory 把 hash 变化视为重新拉取并 ingest 最新值的信号，保留旧 source 原文不是它的主要 contract。
PowerContext 的要求不同：当 Artifact 已经引用这份原文时，当前 Source 变化后旧 payload 仍必须可用。因此 PowerContext
采用 metadata 与 fetcher 分离，同时为 Artifact citation 增加保留的 immutable snapshot。

上游关键参考文件：

- [SourceFetcher types](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/main/MemoryKnowledge/src/source-fetcher/types.ts)
- [SourceFetcher registry](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/main/MemoryKnowledge/src/source-fetcher/registry.ts)
- [Git fetcher](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/main/MemoryKnowledge/src/source-fetcher/git-fetcher.ts)
- [Wiki source index](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/main/MemoryKnowledge/src/engines/wiki/index-db.ts)
- [Knowledge metadata schema](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/main/MemoryKnowledge/src/db/client.ts)
- [CodeGraph service](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/main/MemoryKnowledge/src/store/code-graph-service.ts)

# Persistence 与 Runtime 影响

实现预计需要在可变 Source/current-head 表示之外增加不可变 snapshot 表。Artifact lineage 应引用不可变 snapshot key，
而不是只保存 source_type/source_id。具体迁移结构留给实现阶段，但必须保留旧 captured Source，并拒绝在已有
snapshot identity 下替换 payload。

新增 typed Source 必须接入 Source resolve 与 exact read、persistence encoding 与 decoding、Runtime composition、远程暴露时
的 HTTP/client mapping、evidence projection、citation validation，以及聚焦的 persistence 和 end-to-end tests。Evidence
projector 应通过 adapter capability 或 registry 路由，避免 Runtime composition 不断累积 ContentSource 特判。

现有 ContentSource capture 必须继续可读；现有 POST /v1/sources/content 在当前 identity/payload contract 下继续幂等。旧的
两段式引用需要显式迁移规则，不能在会改变历史 Artifact lineage 含义时静默解释为 latest snapshot。OpenAPI 变更应在
identity 和 legacy behavior 达成共识后进行。

# 备选方案

## 保持当前 stable Source key

API 最小，但 provider 对象变化仍然冲突，也无法引用多次 snapshot，只适合已经不可变的 capture。

## 只使用 provider reference

存储成本低，但 exact evidence 依赖 provider retention、权限、可用性和历史读取语义，无法满足任意集成的 replay 要求。

## 所有 provider value 都使用 ContentSource

类型更少，但 provider semantics 进入无类型 metadata，校验能力变弱，evidence projection 也无法感知 provider，不是持久的
扩展边界。

## 只保存本地 revision

本地 revision 有助于排序和 cursor，但不能证明观察到哪一份 provider state，只能补充而不能替代 provider revision 和 hash。

# 落地与验证

首个有界验证应覆盖一个会变化的 provider object 和一个不可变 provider revision。GitHub issue/commit 或 Git repository
commit 都是候选。验证至少应证明：

1. 同一个 provider revision 的重复投递幂等；
2. 后续 provider revision 产生新 snapshot 并推进当前 head，而不替换旧 snapshot；
3. 当前检索和 ingest 使用新 snapshot；
4. provider 变化后 Artifact 仍能引用并读取旧 snapshot；
5. 缺失或无法验证的 Ref 被拒绝，而不是静默刷新；
6. evidence projection 与 HTTP/client mapping 保留精确 snapshot reference。

# 未决问题

- snapshot_id 应由 PowerContext 生成 opaque ID、使用 content-addressed ID，还是同时提供两者？
- SourceRef 是否增加 snapshot_id，还是引入独立 SnapshotRef？
- 哪些 provider revision guarantee 可以允许 Ref-only materialization？
- 不再被 Artifact 引用的 snapshot 应采用什么 retention 和 garbage-collection policy？
- 大型 capture 是否使用外部 blob store，同时保留 canonical hash 和 durable locator？
- logical Source head 是否应成为首版 public API，还是初期只作为 connector/persistence metadata？

# 请求决策

请评审并批准：可变 Source head 加不可变 snapshot 模型；snapshot 作为 exact-evidence boundary；Capture 作为默认
materialization；Hybrid 作为可追溯场景的优先形态；ContentSource 作为一个具体 capture 类型而不是 universal provider
model。批准后再定义具体 schema、migration、OpenAPI 字段、retention 规则和一个有界 connector validation。
