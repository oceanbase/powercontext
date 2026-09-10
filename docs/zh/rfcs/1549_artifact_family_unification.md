---
title: Artifact Family 统一读取与注册机制
---

# Artifact Family 统一读取与注册机制

- Proposal Name: `artifact_family_unification`
- Start Date: 2026-09-10
- RFC PR: [#1549](https://github.com/oceanbase/powercontext/pull/1549)
- Status: Proposed
- Related RFCs: [1437](1437_source_artifact_rest_api.md), [1417](1417_topic_memory.md), [1485](1485_profile_artifact.md), [1515](1515_artifact_processing_supervisor.md)

## 摘要

PowerContext 已经使用统一的 Source 与 Artifact 抽象，但公共 HTTP contract 对 Artifact Family 的声明仍是分散的：Topic Memory 已经能够作为持久化 artifact 访问，却不能通过标准 `list_artifacts` 接口列出；新增 family 时还需要同步修改 OpenAPI、生成模型、运行时装配和其他 family 列表。

本 RFC 提议：

1. 将 Topic Memory 纳入标准 Artifact 读取接口，至少支持 `list_artifacts`，并让标准的 get、revision list、get revision 使用同一 family contract。
2. 建立唯一的 family registry，集中声明 family 的标识、artifact 类型、Source 能力、读取/写入能力和可选适配器；通用接口只依赖 registry，不再为每个新 family 增加分支。

Topic Memory 的生成、flush、search 和 detail 语义保持现有专用接口不变。标准 Artifact 写接口是否开放给某个 family，仍由 registry 的能力声明决定。

## 动机

记忆展示页面需要稳定的 list 和 search 能力。当前 Topic Memory 的底层 browse 能力已经存在，但公共 list 路由使用静态枚举，无法接受 `topic-memory`。如果页面先调用通用 list，再对每个结果调用 Topic Memory detail，会造成协议不一致、字段缺失和 N+1 请求。

同时，Source 和 Artifact Family 会继续增加。将 family 名称分别复制到 OpenAPI 参数、生成代码、repository 装配、权限配置和测试中，会让新增制品容易遗漏某个入口，并使通用 API 的演进成本随 family 数量增长。

## 设计

### 1. Topic Memory 纳入标准 Artifact 读取接口

将 `topic-memory` 加入 `BaseArtifactFamily`，并将下列接口的 `family` 参数统一引用该 schema：

- `GET /v1/scopes/{scope_id}/artifacts/{family}` (`list_artifacts`)
- `GET /v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}` (`get_artifact`)
- `GET /v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}/revisions` (`list_artifact_revisions`)
- `GET /v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}/revisions/{revision}` (`get_artifact_revision`)

`list_artifacts` 的响应继续使用统一的 `ArtifactPage`。Topic Memory 的 collection item 需要能够表达展示页面所需的通用元数据：artifact ref、title、summary、发布时间、来源数量和必要的 revision 信息。Topic Memory 的 detail 内容由现有 `topic-memory/get` 继续提供；若标准 get 的字段模型无法表达完整 Topic Memory 内容，则通过 family-specific detail contract 扩展，而不是在通用路由中增加 family 分支。

Topic Memory 的 list 排序使用其已发布版本的时间倒序，并保持 cursor 分页语义。只返回当前可展示的已发布 Topic Memory；临时草稿、处理中状态和内部工作记录不进入标准 list。

Topic Memory 只读能力的安全边界保持不变：读取必须在 scope 内完成，并沿用 Topic Memory 当前的 scope ownership 规则。将 family 加入通用读取枚举不等于开放 Topic Memory 的手工 create、replace 或 delete。

### 2. 唯一的 family registry

新增 `ArtifactFamilyDefinition` 和 `ArtifactFamilyRegistry` 概念。每个 family 在 registry 中注册一条定义，定义至少包含：

- 稳定的 wire name，例如 `memory`、`topic-memory`；
- Python artifact 类型和持久化标识；
- 是否是 Source、是否允许标准读取、是否允许标准写入；
- 是否支持 tags、revisions、search 等通用能力；
- 可选的 family-specific collection/detail 映射器、查询器和写入适配器。

Source 继续使用现有 SourceDefinitionRegistry；Artifact Family registry 保存 Artifact 与 Source 的关联能力，避免把 Source 类型和 Artifact family 的公共协议混为一个枚举。运行时的 repository、management writer、访问能力和 OpenAPI family schema 都应从这两个 registry 的定义生成或校验。

通用 HTTP handler 只做以下工作：解析 registry 接受的 wire name、执行通用鉴权/分页/错误映射、调用 family capability 对应的接口、返回统一响应。它不应出现 `if family == "topic-memory"` 这类按 family 分支。某个 family 的差异只能通过 registry 注册的能力或适配器表达。

### 3. OpenAPI 与生成代码

OpenAPI 仍是公共 HTTP contract 的唯一来源。所有通用 artifact 路由共享 `BaseArtifactFamily` schema；生成代码由 `make api-generate` 更新，禁止手工编辑 `src/powercontext/http/_generated/`。

registry 的运行时定义必须与 OpenAPI family schema 做一致性校验：

- registry 中可公开读取的 family 必须出现在 `BaseArtifactFamily`；
- OpenAPI 中声明的 family 必须有对应的 registry 定义；
- 只有声明了对应能力的 family 才能出现在 tag、write 或其他专用参数中。

这样，新增 family 的最小流程是新增一条 registry 配置、实现该 family 声明的必选能力，并重新生成 contract；通用 list/get 路由、分页和基础鉴权无需修改。

## 备选方案

### 保持 Topic Memory 专用 list 接口

实现成本较低，但展示端需要维护两套 list 协议，通用 artifact 页面无法复用，且 family 增长后会形成更多专用路由。不采用。

### 复用当前 `list_artifacts`，再逐条调用 `topic-memory/get`

当前 contract 不接受 Topic Memory，且会产生 N+1 请求；通用列表也无法直接返回 Topic Memory 的标题、摘要和发布时间。不采用。

### 只扩展一个全局枚举

能够暂时解决路由拒绝问题，但 family 注册仍然散落在运行时、权限和映射代码中，无法避免新增 family 遗漏适配。不采用。

### 为每个 family 保留独立 handler

可以表达差异，但会让通用分页、鉴权和响应协议持续分叉。仅在 family 的语义无法通过能力接口表达时，允许增加专用业务接口；标准读取路径仍由 registry 驱动。

## 兼容性与迁移

- `topic-memory` 是新增的标准读取 family，现有 memory、experience、skill、handoff、profile 和 prompt 调用保持兼容。
- 现有 Topic Memory 专用 search/get/flush 接口保留，不要求客户端立即迁移。
- 不改变 Topic Memory 持久化表结构、artifact ref 格式或生成流程。
- 如果标准 collection item 对旧客户端不可见，新增字段必须遵守现有可选字段和向后兼容规则。
- family registry 与 OpenAPI 不一致时，服务应在启动或 contract test 阶段快速失败，而不是运行时静默遗漏。

## 测试计划

- OpenAPI contract test：确认四个标准读取路由共享 family schema，并接受 `topic-memory`。
- Registry contract test：确认所有公开 family 都完成 registry 注册，且 OpenAPI 与运行时定义一致。
- Runtime test：通过标准 list/get/revision 路径读取 Topic Memory，验证 scope、排序、cursor 和空结果行为。
- Regression test：确认现有 family 的 list、tag filter、revision 和权限行为不变。
- Extension test：注册一个最小测试 family 后，验证通用读取和基础装配无需新增 family 分支。

## 未决问题

1. Topic Memory 的标准 collection item 是否需要直接携带完整 content，还是只携带通用摘要并由 detail 接口按需读取？本 RFC 采用“摘要 list、detail 按需读取”的默认方案。
2. registry 是否在本次实现中直接作为 OpenAPI enum 的生成输入，还是先通过一致性检查约束两者？本次实现优先采用低风险的一致性检查，并为后续 contract 生成自动化保留入口。

## 结论

统一 Artifact 读取接口解决当前 Topic Memory 展示需求，family registry 则把未来扩展的变化点收敛到一个注册位置。两者结合后，新增 family 只需要声明自己的能力和必要适配器，通用接口不再随 family 数量线性修改。
