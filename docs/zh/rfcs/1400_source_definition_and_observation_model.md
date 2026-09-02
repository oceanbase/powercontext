- Proposal Name: `source_definition_and_observation_model`
- Start Date: 2026-08-27
- Related Discussion: [oceanbase/powercontext#1240](https://github.com/oceanbase/powercontext/issues/1240)
- Tracking Issue: [oceanbase/powercontext#1363](https://github.com/oceanbase/powercontext/issues/1363)
- Related Design: [oceanbase/powercontext#1345](https://github.com/oceanbase/powercontext/pull/1345)
- Related RFCs: [RFC 0002](0002_core_sdk_product_model.md)、[RFC 0014](0014_memory_layer_design.md)、 [RFC 0019](0019_local_source_memory_runtime.md)、[RFC 0048](0048_handoff_artifact.md)

# Summary

本 RFC 定义标准 Source 模型，以及新增 Source 类型时必须遵守的契约。

每个 Source 只属于一个 Scope。在该 Scope 内，`SourceKey` 标识一个逻辑 Source，`SourceRef` 标识这个 Source 的一次不可变观察。推进当前观察、观察到删除、修改外部 locator 或断开 Connector，都不会改变已经接受的观察，也不会将其移动到另一个 Scope。

Source Definition 为一个稳定的 Source 类型定义 value schema、provenance schema、身份规则、观察规则、materialization 契约、canonicalization 与兼容策略。Definition 显式注册，并在组合完成的 Runtime 生命周期内保持不变。持久化、传输与 Artifact consumer 按稳定的 Definition 名称和版本路由，而不是按具体 Python 类路由。

Definition 可以为无法理解 native value 的 consumer 声明 named projection capability。每个 projection 拥有独立版本的 schema，并对一个精确 observation 具有确定语义。Consumer 按 capability name 与 version 选择 projection，而不是检查具体 Source class。

Connector lifecycle 将 provider acquisition 绑定到 Scope，在 worker 内解析 definition-native input，提交 materialized observation，记录 per-item outcome，并且只在接受的 observation 已持久化后推进 opaque checkpoint。Connector run 区分 complete discovery 与 incomplete discovery，避免把缺失对象静默转换为删除。

Materialization 表达解析某个精确观察时所依赖的权威来源。Captured observation 从 PowerContext 保留的 canonical value 解析；referenced observation 从外部不可变 revision 解析。仅有外部 locator、修改时间、ETag 或 provider 当前值读取，并不能满足 referenced 契约。

`ContentSource` 继续作为简单的 captured-text Source。调用方提供稳定身份，加上 immutable-payload 冲突规则，适合一次性内容捕获，但它不是通用的外部集成模型。

本 RFC 定义 Source、projection、Connector lifecycle，以及 worker 与 PowerContext Server 之间的远程摄取边界。它不定义插件发现、scheduler、credential transport、具体 Source family 或 Connector 实现。

# Motivation

`ContentSource` 与 `POST /v1/sources/content` 提供 captured-text ingestion。调用方选择一个 `source_id`；提交完全相同的 payload 具有幂等性，而用不同 payload 复用该身份会产生冲突。只有调用方把这个身份当作不可变身份时，它才能表达精确证据。

外部系统通常具有不同的生命周期。Wiki 页面、issue、object、message 或 file 拥有一个逻辑身份，但会随时间产生多个值。外部对象可能被重命名、修订、删除、恢复或暂时无法读取。使用过旧值的 Artifact 必须继续引用当时的精确证据。二元 `(source_type, source_id)` Source reference 无法同时表达稳定的逻辑对象和不可变观察，只能迫使每个集成自行发明复合 `source_id`。

例如，只使用 provider object ID 时，第二个 value 会与第一个冲突或替换它。只使用 value digest 虽然能保留两个 value，却无法表达它们来自同一个持续存在的对象：

```text
provider object 42
      |
      +-- value v1 ----> exact observation 1
      `-- value v2 ----> exact observation 2
             ^
             |
       same logical Source
```

因此，本模型分别保存 logical identity 与 exact evidence。需要 current state 的 consumer 可以沿着同一个 logical Source 读取，而 Artifact 继续引用它实际使用的 observation。

扩展边界也不完整。Source adapter 将 native input class 绑定到具体 Source class 和读取结果，而内置 Runtime 与关系型持久化会组装固定 adapter 集合。它没有说明独立定义的 Source 类型在身份、持久化、传输与 Artifact evidence 上必须长期满足哪些规则。

标准模型必须回答六个问题，且不能把它们压进同一个 identifier：

1. 哪个 Scope 拥有这份证据？
2. 它描述哪个逻辑上的外部或内部 Source？
3. Artifact 使用的是哪个精确观察值？
4. PowerContext 从哪里读取该精确值？
5. 哪个 Definition 赋予 value 与 provenance 语义？
6. Consumer 可以使用哪个 declared view，而不必理解 native value？

Connector concerns 与此相邻但不同。Discovery、credentials、filtering、checkpoints、retries、provider change handling 与 deletion detection 决定提交哪些观察；它们不定义 Source identity，不能削弱精确证据，也不能改变 Scope ownership。

# Guide-level explanation

## Domain model

理解该模型时，依次确定 ownership、logical identity、exact observation、materialization authority 与 type semantics：

| Concept | Representation | Question answered |
| --- | --- | --- |
| Ownership | Scope | Source 属于哪里？ |
| Logical identity | `SourceKey` | 这是哪个持续存在的 Source？ |
| Exact evidence | `SourceRef` | 引用的是哪个不可变观察？ |
| Read authority | materialization | 从哪里解析该精确值？ |
| Type semantics | Source Definition | 如何解释 value、provenance 与 identity？ |
| Consumer view | named projection | Consumer 可以使用哪个 declared representation？ |
| Acquisition | Connector or direct caller | 如何发现并提交新观察？ |

这些职责形成单向依赖：

```text
Connector or direct caller
          |
          v
Source Definition
          |
          v
Scope-owned Source history
          |
          +---- mutable head selection
          |
          `---- exact SourceRef ----> Artifact evidence
```

一个 Connector 可以使用一个 Source Definition，多个 Connector 可以共用同一个 Definition，直接调用方也可以在没有 Connector 的情况下提交 Source。因此 Connector identity 不会成为 Source type identity。

## Scope ownership

每个 SourceKey 与 observation 都只属于一个 Scope。Scope ownership 不从外部 workspace、path、repository、provider account、Connector instance 或 Source locator 推导。这些值可以参与 binding 或 provenance，但不能分配或替代 `scope_id`。

完整限定的逻辑身份为：

```text
SourceKey = (scope_id, source_type, source_id)
```

完整限定的精确身份为：

```text
SourceRef = (scope_id, source_type, source_id, observation_id)
```

Scope-bound operation 可以从固定 request binding 获得 `scope_id`，而不把它作为任意参数接收。持久化的解析结果仍然保留 owner Scope，使证据在 publication、reporting 或 export 后仍无歧义。

修改 Scope Parent、Context References、Agent binding 或 observation selection，都不会改变 SourceKey 或 SourceRef。跨 Scope 发布 Artifact 时，provenance 保留原始 Scope 与精确 SourceRef；不会移动或隐式复制 Source history。

## Logical Source and immutable observation

`source_id` 在一个 `(scope_id, source_type)` namespace 内命名逻辑 Source，其含义由 Source Definition 规定。它可以对应 provider object ID、稳定 import identity 或其他 normalized key。观察到新值时，它不能静默改变。

`observation_id` 在一个 SourceKey 下命名一次不可变观察。对通用 PowerContext component 而言它是不透明的；可以派生自 provider revision、canonical value digest 或 Definition 特有组合。它不隐含整数序列、时间顺序或祖先关系。

适用以下不变量：

- 一个 `(SourceKey, observation_id)` 永远标识同一个 canonical observation；
- 再次观察相同 canonical observation 具有幂等性；
- 不同 canonical observation 不能复用 observation ID；
- 如果 identity-bearing provenance 不同，同一个 SourceKey 下可以有 value digest 相同的多个 observation；
- value digest 相同的 observation 不会自动成为同一个逻辑 Source；
- Artifact 只引用精确 SourceRef，绝不引用移动的 SourceKey 或 `latest` observation。

例如，更新一个逻辑 Source 会保留其 SourceKey，并产生新的 SourceRef：

```text
SourceKey(scope-a, record, provider-object-42)
|-- SourceRef(..., observation-1)  "Initial value"
`-- SourceRef(..., observation-2)  "Revised value"
```

即使 `observation-2` 已成为 current，派生自 `observation-1` 的 Artifact 仍然引用后者。

## Source Definition

Source Definition 是一个 `source_type` 的持久语义契约。它声明：

- 稳定的 Definition name 与 version；
- Source value 与 typed provenance 的结构；
- Source ID normalization 与 equality；
- observation ID normalization 与 equality；
- identity-bearing fields 与 non-identifying annotations；
- canonical bytes 与 value digest algorithm；
- 支持的 materialization modes 与 exact-read requirements；
- limits 与 validation failures；
- older Definition versions 的 compatibility rules。

Definition 将 definition-native input 解析为 canonical observation，并从精确的 persisted observation 读取 Definition 拥有的 value。解析不会选择 Scope、修改 catalog、推进 head 或发现外部 object；读取不会解析 `latest`，也不会替换为另一个 observation。

Definition 必须显式且类型化。新的集成不能通过在 `ContentSource.metadata` 中放置未声明 schema 来模拟新 Source 类型。Provider-specific provenance 可以扩展 Definition 声明的 schema，但影响 identity、exactness 或 compatibility 的字段必须由 Definition 命名。

## Named projection capabilities

Named projection 是一个 exact observation 的可选 Definition-owned view。它让 Artifact family 或其他 consumer 无需理解 native Source value 或具体 Python class，就能使用声明过的 representation。

Projection 通过稳定的 name 与 version 选择。其 Definition 声明 output schema、canonicalization、digest rules 与 failures。Projection 针对精确 SourceRef 求值，不能解析 head、`latest` 或 provider current value。对于相同的 Definition version、projection version 与 exact observation，它必须返回相同的 canonical result。

Projection capability 必须显式声明。需要某个 projection 的 consumer 会拒绝未声明兼容 capability 的 Source，而不会从 metadata 推断 content，也不会回退到形态相似的 Source class。Projection 可以作为 derivative 被缓存或持久化，但其 authority 仍是 exact Source observation，lineage 保留对应 SourceRef。

本契约不规定标准 projection name 或 payload schema 的目录。只有当多个 Definition 与 consumer 的互操作证明其语义稳定后，projection 才成为 shared standard。在此之前，Definition 可以暴露 namespaced projection，但不会让它成为其他 Source type 的 mandatory capability。

## Materialization authority

Materialization 回答精确 SourceRef 的返回值来自哪里：

| Materialization | Authority | Required guarantee |
| --- | --- | --- |
| `captured` | PowerContext 保留的 canonical value | 保留值与 observation digest 一致 |
| `referenced` | Immutable external revision | 重读 reference 得到相同 canonical value 与 digest |

Captured Source 可以把 external locator、provider revision 与 digest 保留为 provenance。因为读取权威仍是保留值，所以它依然是 captured。这覆盖了 hybrid design 中有价值的部分，而不引入 fallback 语义含糊的第三种模式。

只有当外部系统及其 reader 能够寻址不可变历史值时，Definition 才能使用 referenced materialization。读取 path、page ID、issue ID 或 URL 的当前值并不足够。Modification time 与 ETag 可以参与 provenance 或 conflict detection，但 Definition 必须说明 provider 是否保证它们指向不可变值。

Referenced value 不可用或 digest 不同时，精确解析失败。PowerContext 不返回 provider 当前值、stale cache entry 或其他 observation。不能满足该规则的 provider 必须使用 captured materialization，或者拒绝该 observation。

## Current head and deletion

Source history 不可变；current head 是可变的 catalog selection。Head 可以选择一个精确 SourceRef，或记录已明确观察到逻辑 Source 被删除。Head 可用于 current-state query 与后续 acquisition，但它不是 evidence，不能出现在 Artifact citation 中。

推进或删除 head 不改变任何 observation。Timeout、permission failure、incomplete listing、Connector unavailable 或 disconnect 都不是明确的 deletion evidence，不能改变 head。只有当 deletion 本身是有意义的 Source evidence 时，Source Definition 才可以定义 tombstone value；通用 head deletion 不会伪造这种值。

## ContentSource

`ContentSource` 继续作为 RFC 0019 定义的 neutral captured-text path。调用方选择一个只能与一个 canonical payload 一起提交的身份。Persistence conflict rule 使接受后的 ContentSource 可作为精确证据，但它不提供独立的 logical Source lifecycle。

标准模型把它视为有效的 single-observation Source：

- 现有 identity 保持不可变；
- 相同内容提交继续保持幂等；
- 同一 identity 下的不同内容继续发生冲突；
- 解析 ContentSource 的 reference 保持精确且不变；
- 不从 metadata 推导 mutable head 或 multi-observation behavior。

ContentSource 适合 prompt、显式文本捕获、import record，以及调用方已经拥有不可变身份的其他场景。持续观察同一逻辑对象的集成应定义或复用 multi-observation Source type。

两条 ingestion 路径的 acquisition 方式不同，但最终都进入 Scope-owned Source history：

| Concern | `ContentSource` capture | Source Definition 与 Connector ingestion |
| --- | --- | --- |
| Typical input | 调用方已经持有的文本 | 从外部系统发现的对象 |
| Identity | 一个由调用方保持稳定的不可变身份 | 一个 logical identity 及其 exact observations |
| Type contract | 内置 captured text 与 metadata | Definition-owned value、provenance 与 projections |
| Synchronization | 单次请求，没有 checkpoint | Discovery、per-item outcomes、retry 与 checkpoint comparison |
| Downstream use | 内置 text evidence | Consumer 能理解的 named projection |

当调用方已经持有最终文本和不可变身份时，`ContentSource` 是更短的路径。Remote ingestion API 不替代它；这组 API 用于 worker 需要发现、规范化并重复观察外部对象的场景，同时避免把 provider code 或 credentials 加载进 Server。

## Source 如何参与 Scope 流程

Server 持久化接受 exact observation 后，会将它追加到 owner Scope 的 Source journal。接受 observation 本身不会创建 Memory 或其他 Artifact。Scope-local processor 随后选择一个有界 Source window，请求它能理解的 projection，并可能产生一个引用 exact SourceRef 的新 Artifact revision：

```text
Connector or direct caller
          |
          | bind Scope A
          v
Source observation
          |
          v
Scope A Source journal ----> Scope-local processor
                                   |
                          named projection
                                   |
                                   v
                         Scope A Memory revision
                         cites exact SourceRef
```

新的 observation 可以触发后续处理，但不会重写旧 Artifact revision：

```text
SourceKey(scope-a, record, provider-object-42)
|-- observation-1 ----> Memory revision 3
`-- observation-2 ----> Memory revision 4

Memory revision 3 continues to cite observation-1.
```

Consumer 只能通过 native Definition 或兼容的 named projection 使用 Source。例如，需要 text evidence 的 Memory extractor 可以处理任何声明了对应 text projection 的 Source Definition，不需要知道 Source 最初来自 file、page、issue 还是 `ContentSource`。缺失的 capability 必须保持显式，consumer 不会从 metadata 推断文本。

跨 Scope 使用 Source 时，需要先确定预期的 ownership 与 delivery 行为：

```text
Scope A Source history
          |
          +-- Context Reference from Scope B
          |      `-- later Prepare Context may read eligible Scope A material
          |
          +-- publish exact Artifact revision
          |      `-- Scope B receives one selected result with origin provenance
          |
          `-- deliberate capture into Scope B
                 `-- Scope B owns a new Source and runs its own downstream flow
```

持续读取使用 Context Reference；交付一个选定结果时，发布 exact Artifact revision。如果 Scope B 必须拥有并独立处理这个外部值，应在 Scope B 中显式 capture，形成新的 Scope-owned observation，并在适用时把 origin reference 保留到 provenance。以上操作都不会移动原始 Source，Parent 也不会因此获得 read access。

# Reference-level explanation

## Source identity contract

`scope_id` 是 Scope organization design 定义的 ownership boundary。`source_type` 是稳定的 Source Definition name。`source_id` 是非空的 normalized identifier，其 equality 与 bounds 由 Definition 声明。

Source identity 以 Scope 为本地边界。两个 Scope 可以包含等价的外部材料，但不共享 ownership 或 identity。需要避免碰撞时，Definition 可以在 `source_id` 规则中包含稳定的 external instance 或 connection discriminator，但 discriminator 不替代 `scope_id`。

Rename 行为由 Definition 决定。Provider object ID 可以在 locator 变化时保留 SourceKey；path-derived identity 通常把 rename 视为一次逻辑 deletion 与一次 creation。当 provider 与 acquisition path 无法证明 rename-stable identity 时，Definition 不能宣称支持它。

## Observation contract

Observation 包含以下标准字段：

```text
SourceObservation
|-- source_key
|-- observation_id
|-- definition_version
|-- materialization
|-- value_digest
|-- provenance
`-- definition-owned value or exact external reference
```

`value_digest` 对 Definition 声明的 canonical bytes 使用 SHA-256，并编码为 `sha256:<lowercase-hex>`。对结构化 value，Definition 指定 deterministic canonicalization。Digest 用于验证 value equality，不替代 SourceKey 或 observation identity。

Canonical observation 包含所有被 Definition 认定会影响 identity 或 exact meaning 的字段。Retry count、last scan time 或 processing status 等 operational facts 不是 Source value，不改变 observation identity。如果 timestamp 或 provider attribute 会影响 provenance meaning，Definition 必须显式分类并 canonicalize。

## Source reference contract

SourceRef 标识精确 observation，并包含 owner Scope。它不接受缺失 observation ID、`latest`、head version 或 current provider locator。

在 scope-bound operation 内，只有当 current Scope 固定且解析出的 durable value 会恢复 `scope_id` 时，紧凑的 local representation 才可以省略重复的 `scope_id`。跨越 Scope boundary、离开 Runtime 或进入 durable cross-Scope provenance 的 reference 必须显式携带 owner Scope。

Reference resolution 会验证全部四个 identity components，以及 stored observation 的 Definition version 与 digest。无法解析精确 observation，不等同于 logical Source 已删除、head 已推进或 Connector 不可用。

不带 `observation_id` 的已接受兼容引用仍然标识一个不可变 observation。解析时不得将其视为 SourceKey、current head 或 `latest`。compatibility layer 可以在边界恢复完整 SourceRef，但不得重定向该 evidence。

## Definition registration contract

Executable Definition 属于 worker。Worker 用它解析 definition-native input、canonicalize Source value，并计算 named projection。Server 不导入 Connector 或 Definition package，也不执行其中的 Python class。

提交 observation 前，worker 注册不可变的声明式 manifest。Manifest 包含稳定的 Definition name 与 version、canonical Source JSON Schema、每个 projection key 与 output JSON Schema，以及覆盖完整声明的 fingerprint。Fingerprint 是 RFC 8785 canonical JSON 的 SHA-256。相同 manifest 的注册是幂等的；同一个 `(source_type, definition_version)` 对应不同声明时必须拒绝。

Server 验证 manifest schema，以及其识别为 shared standard 的 named projection。Manifest 不传输可执行的 identity rule、canonicalization code、read behavior、credential 或 provider configuration；这些仍由 worker 持有。注册后的 manifest 足以让 Server 在不加载 plugin code 的情况下验证并保存 opaque canonical observation。

Definition discovery 与 registration 相互独立。Package entry point 或其他 discovery mechanism 可以报告可用 Definition，但安装不意味着激活。本 RFC 不选择 entry points、central settings format、pluggy 或 Connector marketplace。

## Remote worker ingestion contract

Connector 在独立 worker 进程中运行。Worker 拥有 provider access 与所有 executable Definition behavior；Server 拥有 durable Source history、Artifact consumption 与 checkpoint comparison。双方的数据面交互只有四个通用操作：

1. 注册不可变的 Source Definition manifest；
2. 读取一个 Connector binding 的 opaque checkpoint；
3. 提交 worker 已物化的 Source observation 及其全部声明 projection；
4. 从 run 开始时读到的值 compare-and-swap binding checkpoint。

正常时序如下：

```text
Connector worker                              PowerContext Server
       |                                               |
       |-- register Definition manifest -------------->|
       |<---------------- exact registered manifest ---|
       |                                               |
       |-- get binding checkpoint -------------------->|
       |<-------------------------- checkpoint C0 -----|
       |                                               |
       |-- submit observation 1 ---------------------->|
       |<---------------- durable SourceRef receipt ---|
       |-- submit observation 2 ---------------------->|
       |<---------------- durable SourceRef receipt ---|
       |                                               |
       |-- commit checkpoint expected=C0, next=C1 ---->|
       |<-------------------------- committed C1 ------|
```

如果 worker 在收到 durable receipt 后、提交 checkpoint 前停止，下次 run 会从较早的 checkpoint 开始，并可能再次提交同一个 observation。相同提交具有幂等性。Checkpoint comparison 会阻止同一 binding 的两个 run 静默覆盖彼此的进度。

Observation envelope 携带 Definition name、version 与 fingerprint、canonical Source payload，以及 manifest 声明的每个 projection value。Server 在 durable acceptance 前验证 envelope identity、payload schema、projection key 集合相等、projection schema 与标准 projection invariant。Provider name、storage service、path、credential 或其他 Connector-specific configuration 不出现在该 API 中；只有 Definition 刻意将其声明为 canonical Source schema 的一部分时才例外。

Server 必须先返回 durable Source receipt，worker 才能提交 checkpoint。Checkpoint operation 使用 optimistic comparison，防止同一 binding 的并发 run 静默覆盖。相同 Source identity 与 payload 的提交是幂等的；已接受 identity 对应不同内容时必须拒绝。

## Definition compatibility contract

Definition name 在兼容 schema 演进中保持稳定。每个 persisted observation 记录验证和 canonicalize 它时使用的 Definition version。新的 Definition version 必须声明如何在不改变 canonical meaning 的前提下读取旧 observation，或与旧版本 reader 共存。

如果 Definition change 会改变已接受 observation 的 SourceKey equality、observation equality、canonical value bytes、provenance meaning 或 materialization guarantee，它就是不兼容变更。此类变更需要新的 Definition version，且不能重写已有 SourceRef。

如果 projection change 会改变已接受 observation 的 output schema、canonical bytes 或 meaning，它就是不兼容变更，需要新的 projection version。如果 Source value 与 observation semantics 保持不变，则不要求新的 Source Definition version。

重命名 Definition 会产生新的 `source_type`。把已有 observation 重新分类到另一个 Definition 是带 provenance 的显式 derivation，不是 identity 的原地 migration。

## Connector lifecycle contract

Connector 负责 provider interaction：discovery、credentials、filtering、checkpoints、retries、rate limits、provider change handling 与 positive deletion detection。它依据 Scope binding 提交 definition-native input，并接收接受后的精确 SourceRef。

Source Definition 负责 semantic normalization：logical identity、observation identity、canonical value、provenance、materialization validity 与 exact read。Connector 不能覆盖这些规则。如果 provider capabilities、Connector behavior 与 Definition requirements 的交集无法满足选定 materialization，则拒绝 observation，或在合法模式下 captured。

```text
provider capabilities
  intersect Connector behavior
  intersect Source Definition requirements
  = valid Source observation
```

Connector type 声明稳定的 name 与 version、configuration schema，以及可提交的 Source Definition。额外的 acquisition guarantee 是可选且显式的，例如 complete snapshot、change feed、checkpoint resume 与 authoritative deletion event。Connector 不能声明 provider 与 acquisition path 无法兑现的 guarantee。

Connector binding 为一个 Scope 激活一份 Connector configuration。Binding 拥有用于 checkpoint 与 provider namespace continuity 的稳定 identity，但不拥有 Source，也不替代 `scope_id` 或 `source_type`。Credential 由 hosting environment 解析，不会成为 Source value 或 provenance。

Connector run 从 opaque binding checkpoint 开始，在 worker 内解析零个或多个 definition-native input，再提交其 materialized observation，并记录每个 item 的 outcome。Accepted observation 返回精确 SourceRef；再次提交相同 identity 与 payload 会得到相同的 accepted 结果。Rejected 或 failed item 会保留在 run outcome 中；如果尚不能安全重试，checkpoint 不能越过这些工作。

Run 以 complete 或 incomplete 结束。Complete snapshot 可以为之前已知但本次缺失的 provider object 产生 positive deletion evidence。Incomplete listing、timeout、permission failure、cancellation 或 lost connection 不会产生 absence-based deletion evidence。当 binding 与 object identity 均已验证时，authoritative provider deletion event 可以独立于 snapshot completeness 产生 positive deletion evidence。

Completed checkpoint 只有在 accepted observation 与 deletion evidence 均已持久化后才能推进。由于 Source observation submission 具有幂等性，从更早 checkpoint 重试是合法行为。Connector checkpoint、health、retry 与 run-status record 是 operational state，而不是 Source observation 或 Artifact evidence。

Installation、discovery、activation 与 execution 相互独立。安装 Connector package 不会激活 binding。Connector package 在 PowerContext Server 之外执行，并使用 remote worker ingestion contract；调度与进程监管属于部署环境。

## Artifact evidence and cross-Scope delivery

Artifact revision 记录其计算直接使用的精确 SourceRef。推进 Source head 不改变现有 Artifact lineage。针对较新 observation 的重新计算会产生新的 Artifact revision，而不是重写旧 evidence。

被 durable Artifact revision 引用的 observation 受普通 retention 与 garbage collection 保护。推进或删除 Source head 不会授权删除该 observation。显式 deletion policy 可以使被引用的 evidence 不再可用，但必须在 lineage 中保留 SourceRef 并报告不可用状态，不能把该引用解析到另一个 observation。

Source 保留在 producing Scope。Context Reference 可以按照 Scope organization contract 扩展 read selection，但不会改变 Source ownership。跨 Scope 的精确 Artifact publication 在 lineage 中保留 origin Scope 与精确 SourceRef。发布 Artifact 不会发布其 origin Scope 中的所有 Source。

如果 application 刻意把同一个外部值 captured 到另一个 Scope，target 会得到由该 Scope 拥有的新 Source observation。其 provenance 可以引用 origin scoped SourceRef，但原始 Source 不会移动，两个 SourceKey 也不会因此变成同一 identity。

## Conformance

Conformance 以声明为边界。实现可以逐步兑现这些契约，但每项对外保证都必须通过对应 scenario；未兑现的契约保持 unsupported，不能通过 metadata 或 provider convention 近似代替。

Source Definition 的完整支持要求通过以下 conformance scenario：

- identity normalization 与 collision rejection；
- identical observation acceptance 与 idempotency；
- 同一 observation ID 的 conflicting payload rejection；
- 一个 SourceKey 下的多个 immutable observation；
- head advancement 与 deletion 后仍能精确读取旧 observation；
- captured 与 referenced value 的 digest verification；
- referenced-value unavailability 与 mutation；
- Scope isolation 与显式 owner preservation；
- Definition version compatibility 与 unavailable-definition behavior；
- explicit registration conflict handling。

Named projection 只有在 conformance 验证 exact observation 的 deterministic output、schema 与 version conflict handling、exact SourceRef lineage，以及 capability 缺失时显式失败之后才能被声明。

Acquisition guarantee 只有在 conformance 验证其 checkpoint retry、per-item outcome visibility、durable checkpoint ordering、complete-versus-incomplete run behavior 与 deletion evidence 后才能被声明。Provider-specific behavior 由对应实现证据确定，不会被直接推广为标准契约。

Remote worker path 还必须验证 manifest fingerprint 与 conflict handling、拒绝未注册或 schema-invalid observation、projection set 精确校验、durable receipt ordering，以及跨 Server restart 的 stale checkpoint CAS rejection。

# Drawbacks

- 分离 SourceKey、SourceRef、Source head 与 Definition version，比一个不可变的 `(source_type, source_id)` pair引入更多概念。
- 精确 SourceRef 保留 owner Scope 与 observation identity，会增加 lineage payload 大小。
- Definition author 必须声明 canonicalization、provenance 与 compatibility，而不能依赖任意 metadata。
- Named projection 与 Connector lifecycle state 增加了需要独立于 Source value 演进的契约。
- 只暴露当前值的 provider 无法使用 Referenced Source，因此部分集成必须保留 captured data。
- Custom Source observation 被接受之前，显式 registration 需要部署协调。

# Rationale and alternatives

## Extend ContentSource into the general integration model

向 ContentSource 添加 provider fields 可以保留 `POST /v1/sources/content` capture API，但仍会把 logical identity、observation identity 与 provenance 留在调用方约定中。不同集成会在 metadata 中编码不兼容 schema，non-text Source value 仍需要另一套模型。因此 ContentSource 继续作为有用的 single-observation implementation。

## Use one opaque Source envelope

通用 JSON payload 可以统一 persistence 与 transport，但会把 schema validation 和 compatibility 推给 runtime convention。Definition-owned typed value 与 provenance 让扩展边界可审查，并允许 consumer 在解释前拒绝不支持的 Source type。

## Put an observation digest inside source_id

集成可以把 logical identity 与 digest 组合进 `source_id`，从而维持二元 SourceRef 形态。这可以表达 immutable capture，却会在 catalog 中隐藏持续存在的 logical Source。Update、current-head selection、deletion 与 provider identity 都会变成 integration-private convention。标准模型直接表达两类 identity。

## Make SourceRef logical and add a separate ObservationRef

两个 public reference type 可以让 SourceRef 表示逻辑身份，但 Artifact evidence 必须拒绝 SourceRef，只接受 ObservationRef。让 SourceRef 本身保持精确，符合现有 ArtifactRef 原则：durable lineage 引用 immutable state。

## Add hybrid materialization

增加一种有时从外部读取、有时回退到 captured data 的第三种模式，会掩盖哪个 value 才是 authoritative，以及哪些 failure 应对外可见。Captured observation 可以把完整 external reference 保留为 provenance；referenced observation 要么被精确解析，要么失败。

## Let Parent or Connector identity own Sources

Scope Parent 用于 organization，Connector identity 是 acquisition provenance，二者都不是持久 ownership boundary。使用其中任意一个都会与 Scope organization contract 冲突，并让 reorganization 或 Connector replacement 改变 Source identity。

# Prior art

- [Scope organization and Agent integration design](https://github.com/oceanbase/powercontext/pull/1345) 分离 Scope ownership、read sharing、organization、delivery 与 observation。本 RFC 对 Source ownership、identity、exact evidence 与 acquisition 应用同样的分离原则。
- DataHub stateful ingestion 把 connector checkpoint 与 stale-entity detection 同 emitted metadata identity 分离。Airbyte 把 connector state 当作 opaque recovery boundary，而不是 record identity。
- OpenMetadata 把负责生成 record 的 Source 与 connection check、workflow status、sink 分离。
- Nowledge Mem 的 TiddlyWiki importer 使用 stable logical ID、canonical payload digest、source revalidation 与 per-item outcome。这些行为为 Source observation 与 Connector run state 的分离提供依据。
- [TencentDB-Agent-Memory](https://github.com/TencentCloud/TencentDB-Agent-Memory/tree/5299c00aaf65481703c180fd69df066d11254eb7) 使用 SourceFetcher registry 获取 provider value，以 provider revision 与 content hash 检测变化，并单独维护 synchronization 和 audit state。这些模式属于 Connector acquisition，不能替代 immutable Source observation，因为 Artifact citation 必须在 provider current state 变化后仍然保留它使用过的值。

# Unresolved questions

- 每个 durable SourceRef 是否必须直接携带 `scope_id`，还是可以由 canonical scoped envelope 包含 local exact SourceRef，同时保留相同的 fully qualified identity？
- Runtime 必须同时保留哪些 Source Definition version，才能宣称某个 Definition 受支持？
- Source head deletion 应是通用 catalog state，还是标准契约只暴露 active exact head，并把 deletion 完全留给 Connector state？
- 哪些 projection name 与 schema 已有足够实现证据，可以成为 shared standard 而不是 namespaced capability？

# Future possibilities

显式 plugin discovery 与 deployment policy 可以建立在 Definition 和 Connector registration 之上，但不会让 package installation 等同于 activation。

Retention policy 只有在定义精确 Artifact evidence 如何报告 unavailable content，以及 legal/user-requested deletion 如何与 immutable lineage 交互之后，才能回收 captured value。Source head deletion 本身不授权删除证据。
