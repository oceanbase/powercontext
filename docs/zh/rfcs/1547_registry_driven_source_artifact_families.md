+ Proposal Name: `registry_driven_source_artifact_families`
+ Start Date: 2026-09-10
+ Status: Proposed
+ RFC PR: [oceanbase/powercontext#1547](https://github.com/oceanbase/powercontext/pull/1547)
+ Related RFCs: [Source 与 Artifact REST API](1437_source_artifact_rest_api.md)、[Topic Memory](1417_topic_memory.md)、[Profile Artifact](1485_profile_artifact.md)、[Artifact Processing Supervisor](1515_artifact_processing_supervisor.md)

# Summary

本 RFC 为 Source type 和 Artifact Family 增加集中注册机制。新增内置 Source type 或 Artifact Family 时，只需要在一处
声明其身份、模型、能力和必要的 Family-owned adapter；标准 Source、Artifact、权限、能力发现和客户端契约从注册信息
派生，不再在通用接口中增加新的 `if family == ...` 分支。

注册信息只描述“这个类型是什么、支持哪些通用能力以及特殊语义由谁负责”。它不把不同 Family 的内容模型强行合并，
也不把专用生成、检索或处理流程塞进基础接口。

目标形态如下：

```python
register_artifact_family(
    ArtifactFamilySpec(
        name="topic-memory",
        artifact_type=TopicMemory,
        catalog=topic_memory_catalog,
        reader=topic_memory_reader,
        writer=None,
        search=topic_memory_search,
        processing=topic_memory_processing,
    )
)
```

普通 Family 不提供特殊 `catalog` 或 `reader` 时，自动复用共享 Artifact Revision、Head、lineage、digest 和分页实现。

# Motivation

当前 Source 与 Artifact 已经共享相同的 Scope、identity、lineage、Revision 和授权基础，但“一个新 Family 如何进入公共
接口”仍然分散在多个地方：

- OpenAPI 的 Family 或 Source type enum；
- 生成的 HTTP model、operation 和 schema；
- Artifact Repository 的类型注册；
- Family management writer 和内容校验；
- Access Profile、capabilities 和 readiness；
- processing binding、专用 search/get 以及 Dashboard 适配。

这种分散方式会造成两类问题：已经注册的类型没有进入公共接口，或者公共接口允许了一个没有完整运行时能力的类型。
`Topic Memory` 就暴露了前一种问题：它已在 Artifact Repository 和 runtime 中注册，却没有进入标准 Artifact List。

基础接口应该稳定地处理所有共性资源行为；Family-specific 差异应该由注册项声明并通过可选 adapter 承担。这样新增一个普通
Family 时，通用路由、分页、cursor、Source lineage、Artifact digest 和权限骨架不需要再次修改。

# Goals and non-goals

## Goals

- 为公开 Source type 和 Artifact Family 建立唯一的运行时注册表。
- 让标准 Source/Artifact read、list、revision、lineage 和能力发现从注册表工作。
- 为 Family 提供可选的 catalog、reader、writer、search 和 processing adapter。
- 没有特殊语义的 Family 自动使用共享实现；只对特殊语义注册最小 adapter。
- 在启动时拒绝重复名称、模型不一致、能力声明不完整和不安全的注册项。
- 让 OpenAPI、生成客户端、runtime registry 和 capabilities 保持一致。

## Non-goals

- 不允许调用方通过请求动态注册任意 Source type 或 Artifact Family。
- 不把 Family-specific content schema 改造成一个宽松的公共 JSON 黑盒。
- 不要求所有 Family 支持 Create、Replace、Search、Generation 或后台 Processing。
- 不消除 Topic Memory、Memory、Profile、Experience、Skill 和 Handoff 的领域校验与生命周期差异。
- 不自动为新 Family 发明数据迁移、索引、模型提示词或审核流程。

# Guide-level explanation

## Family 是注册能力集合

每个公开 Artifact Family 注册一个不可变的 `ArtifactFamilySpec`。示例：

```python
ArtifactFamilySpec(
    name="experience",
    artifact_type=Experience,
    catalog=shared_artifact_catalog,
    reader=shared_artifact_reader,
    writer=experience_writer,
    search=experience_search,
    processing=experience_processing,
    access=experience_access_profile,
)
```

字段语义：

| 字段 | 作用 |
| --- | --- |
| `name` | 对外稳定的 Family 名称，必须符合公开 identity 约束 |
| `artifact_type` | Artifact 及其 `content` 的运行时模型 |
| `catalog` | 可选的当前 Head 列表投影；缺省使用共享 Artifact catalog |
| `reader` | 可选的 current head/exact revision reader；缺省使用共享 Artifact Repository |
| `writer` | 可选的 Create/Replace writer；缺省表示该 Family 只读 |
| `search` | 可选的 Family-specific search 能力 |
| `processing` | 可选的 Source-driven 或其他后台处理能力 |
| `access` | Family 的访问、共享和 selector 约束 |
| `tags` | 是否支持通用 Artifact/entry tag target |

`Topic Memory` 可以注册专用 `catalog` 和 `reader`，以确保 active topic、publication 和 retrieval projection 完整；
普通 Artifact 则只注册 `artifact_type` 和必要的 writer。

## Source type 注册

Source type 使用相同的注册思想：

```python
SourceTypeSpec(
    name="content",
    adapter=content_source_adapter,
    public=True,
    readable=True,
    capture=True,
    generation_eligible=True,
)
```

Source adapter 负责 canonicalization、wire content、materialization 和 generation eligibility。Source journal、Scope
identity、分页和授权仍由通用 Source service 负责。

内部 Source type 可以注册但设置 `public=False`。它们可以被内部 processing 使用，但不能因为注册而自动进入公共
OpenAPI 或允许调用方自由提交。

## 标准接口的能力推导

标准接口根据 Family capability 选择实现，而不是按名称写分支：

| 能力 | 缺省行为 | 特殊 Family 的选择 |
| --- | --- | --- |
| Artifact List | 共享 current-head catalog、统一分页和 cursor | 使用 `catalog` 返回 Family 自己的稳定摘要 |
| Artifact Get head | 共享 Artifact Repository | 使用 `reader` 返回领域响应或额外完整性检查 |
| Artifact List revisions | 共享不可变 Revision 查询 | 仅在 Family 有非标准历史规则时覆盖 |
| Artifact Get revision | 共享精确 Revision 查询 | 使用 `reader` 进行 Family-specific 解码 |
| Artifact Create/Replace | 未声明则不公开写操作 | 使用 `writer` 负责校验、提交和派生状态 |
| Source Create/Get/List | 共享 Source service | 使用 Source adapter 规范化和读取 |
| Tags | 按 `tags` capability 暴露 | Family 可声明不支持或自定义 target |
| Search | 不自动生成 | 注册 Family-specific search operation |
| Processing | 不自动启动 | 注册 processing binding 和调度策略 |

通用接口只处理请求边界、Scope authorization、cursor、错误映射、事务和响应外形。注册的 adapter 不能绕过这些边界。

## 公共契约与生成

OpenAPI 继续是完整 wire contract 的 source of truth。为了避免同一个 Family 名称在多个路径重复维护：

1. `BaseArtifactFamily` 和公开 Source type 各自只定义一次完整 enum；
2. 各个 Path parameter 引用该 schema，不再复制 inline enum；
3. `make api-generate` 根据契约生成 HTTP models、operations 和 schema；
4. 启动检查比较公开 OpenAPI Family、runtime registry 和 capabilities，发现漂移就失败。

如果后续需要完全自动化，可以把 Family manifest 作为构建输入，生成 OpenAPI enum 和 runtime registry；但客户端仍然使用
构建期稳定的 enum，而不是接收任意运行时字符串。

因此新增普通内置 Family 的最低步骤是：

```text
1. 在 Family registry 注册 Family、Artifact model 和 capabilities
2. 在 OpenAPI 的集中 Family schema 增加一个值并声明对应 content schema
3. 运行 make api-generate
4. 为该 Family 补充内容校验和行为测试
```

通用 HTTP 路由、分页、Source lineage、Artifact digest 和标准 read 适配不需要修改。只有 Family-specific writer、search
或 processing 存在时，才需要注册对应 adapter。

# Reference-level explanation

## Registry lifecycle

注册表在 Server Application 组装阶段构建，并在应用启动前冻结：

```text
加载内置 Source type 和 Artifact Family
  -> 校验名称唯一性和公开性
  -> 校验 Artifact model、content model 和 adapter 能力
  -> 校验 Access Profile 与公开 operation 的兼容性
  -> 构建 Repository、Source service、capabilities 和 processing bindings
  -> 冻结 registry
```

启动后不允许请求、插件或数据库内容改变公开 Family 集合。这样同一进程内的 OpenAPI、访问控制、持久化解码和能力发现
不会观察到不同的 Family 集合。

## Validation rules

注册项必须满足：

- `name` 唯一且符合 `[a-z][a-z0-9-]*`；
- `artifact_type.family == spec.name`；
- `artifact_type.content` 是可验证的 Pydantic model；
- 声明 `writer` 时，Create/Replace 的内容校验和事务边界明确；
- 声明 `catalog` 时，返回的 identity 必须属于该 Scope 和 Family；
- 声明 `reader` 时，精确读取必须保留请求中的 ArtifactRef revision；
- 声明 `processing` 时，binding、配置、权限和失败恢复策略必须完整；
- `public=False` 的类型不能进入公共 enum、capabilities artifact/source 列表或标准外部 operation；
- 所有 adapter 都必须使用通用 Scope authorization 和 error normalization。

重复名称或不满足约束时，Server 不应启动成功，也不应部分注册后继续提供服务。

## Family-specific adapter boundary

Adapter 只负责无法从共享 Artifact/Source 事实推导的部分：

- content-specific validation 和 canonicalization；
- Family-owned derived tables 或 retrieval projections；
- Family-specific current-head completeness；
- domain search、generation、processing 和 publication；
- Family-specific response projection。

Adapter 不负责重新实现 Scope、cursor 签名、通用 Artifact identity、lineage 存储、访问检查或请求 ID。通用服务向 adapter
传递已经校验过的 Scope 和 exact identity，adapter 返回 typed result。

## Capabilities and error behavior

Capabilities 必须从冻结 registry 生成，不能再维护独立的 Family 字符串列表。客户端可以先读取 capabilities 判断部署是否
启用了某个 Family 或 operation。

当 Family 已在公共契约中声明但当前部署缺少其运行时能力时，服务端返回既有的 unavailable/configuration error，并在
readiness 中说明原因；不能把缺失能力误报成空列表或 404。未知或非公开 Family 继续返回 invalid request。

## Authorization and security

注册项的 `access` 必须明确 Scope read/write、Artifact owner、share unit、selector 和 transitivity。默认拒绝未声明的
能力；新增 Family 不能因为复用了通用 Artifact handler 就自动获得写入、共享或跨 Scope 能力。

自动生成的 Family（例如 Topic Memory）可以声明 Scope-owned read，而不建立调用方 owner relation；手动可写 Family 则
必须由 writer 和 Access Profile 同时声明写权限。

## Persistence and processing

共享 Artifact Repository 继续使用统一的 Revision、Head、lineage 和 digest 表。Family-specific derived tables、索引或
processing state 由其 adapter 自己声明并在 composition 阶段装配。

新增 Family 不自动回填历史 Source，也不自动生成历史 Revision。若需要迁移或建立 projection，必须由该 Family 的独立
迁移和 readiness 检查负责。

# Migration plan

迁移现有代码时采用兼容的分阶段方式：

1. 用 registry 包装当前 `ArtifactRepository` 的 Artifact 类型集合和 Source registry。
2. 将 Access Profile、capabilities、management writer 和 processing binding 映射到对应注册项。
3. 让标准 Source/Artifact handler 依赖 registry 查询能力，删除按 Family 名称的通用分支。
4. 将 OpenAPI Path parameter 的重复 enum 改成对集中 Family schema 的引用。
5. 为 Topic Memory、Profile 和现有可写 Family 加入 registry consistency tests。
6. 在所有旧 Family 迁移完成后，删除重复的静态 Family 列表。

迁移期间，旧的显式接口和 response schema 保持兼容。注册表只改变能力来源，不改变已有 Artifact identity、Source
journal、Revision、lineage 或权限语义。

# Alternatives

## 继续在每个通用接口里增加 Family 分支

不采用。它会让每个新 Family 重复修改 route、client、权限、测试和文档，并且容易遗漏其中一处。

## 将 family/source_type 改成任意字符串

不采用。完全开放的字符串会削弱 OpenAPI 客户端类型、权限校验和内容解码边界，也会让部署能力无法被静态发现。

## 每个 Family 单独提供完整 CRUD 路由

不采用。Family-specific operation 可以保留，但 Scope、identity、cursor、lineage 和标准 read/list 会出现多套重复实现。

## 只从 OpenAPI 生成 runtime registry

不单独采用。OpenAPI 能描述 wire schema，但不能完整表达 processing、derived projection、权限和 adapter 生命周期。
OpenAPI 与 runtime registry 应通过启动检查和构建期生成保持一致，各自负责适合自己的契约。

# Compatibility and rollout

增加公开 enum 值是向后兼容的 additive contract change；不识别新值的旧客户端仍可继续使用旧 Family。生成客户端需要
重新生成类型和 operation schema。

registry 冻结后，Family 的名称和能力在一个 Server 进程生命周期内保持不变。Family capability 从 disabled 变为 enabled
属于部署配置变化，不改变已持久化身份。

安全默认值如下：

- 未注册、非公开或未启用的 Family 不对外提供；
- 未声明 writer 的 Family 不支持 Create/Replace；
- 未声明 search 的 Family 不生成通用 search；
- 未声明 processing 的 Family 不进入后台调度；
- adapter 错误不能降级为成功的空响应。

# Acceptance criteria

- 所有公开 Source type 和 Artifact Family 都能从冻结 registry 枚举得到。
- capabilities、Access Profile、Repository family 集合和 OpenAPI 公开集合一致。
- 新增一个仅支持标准 read/list 的测试 Family 时，不修改通用 HTTP handler 即可工作。
- 新增一个带自定义 catalog 或 writer 的测试 Family 时，只增加注册项和 Family adapter，不增加通用路由分支。
- 未注册 Family、重复注册、能力缺失和 model/family 名称不一致都会在启动或 contract test 阶段失败。
- 标准 Source/Artifact 的 Scope authorization、cursor、lineage、digest 和错误语义保持不变。
- Topic Memory 可以通过注册的 catalog/reader 接入统一 Artifact read/list，同时保留其专用 search、processing 和只读写入边界。
- 不产生历史数据迁移，也不因注册新 Family 自动改写已有 Source 或 Artifact。
