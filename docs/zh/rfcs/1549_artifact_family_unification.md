---
title: 标准 Artifact 读取接口支持 Topic Memory
---

# 标准 Artifact 读取接口支持 Topic Memory

- Proposal Name: `topic_memory_artifact_reads`
- Start Date: 2026-09-10
- RFC PR: [#1549](https://github.com/oceanbase/powercontext/pull/1549)
- Status: Proposed
- Related RFC: [1417](1417_topic_memory.md)

## 摘要

Topic Memory 已经作为 Artifact 持久化，但公共标准 Artifact 读取 contract 还不接受 `topic-memory`。本 RFC 只为记忆展示页面补齐标准 Artifact 读取能力：

1. 列出当前 Artifact head；
2. 获取当前 Artifact head；
3. 列出 Artifact revisions；
4. 获取指定的 Artifact revision。

现有 Artifact repository 装配和各 family 的管理写入逻辑保持不变，只扩展读取 contract 以及 Topic Memory 的 list 行为。Topic Memory 的生成、flush、search 和现有 detail 语义继续使用专用接口。

## 动机

记忆展示页面需要稳定的 list 接口。Topic Memory 底层已经具备 browse 能力，但标准 `list_artifacts` 路由会拒绝 `topic-memory`。如果继续使用 dashboard 专用 list，客户端就需要维护两套列表协议。

Topic Memory 的 list 还有明确的 family-specific 语义：只展示已发布主题，按发布时间倒序，并返回页面需要的展示元数据。这些差异只存在于 Topic Memory list 的实现，不需要新增通用 family registry 或通用能力框架。

## 设计

### 1. 标准 Artifact 读取接口支持 Topic Memory

以下读取接口接受 `topic-memory` 作为 family：

- `GET /v1/scopes/{scope_id}/artifacts/{family}`（`list_artifacts`）
- `GET /v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}`（`get_artifact`）
- `GET /v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}/revisions`（`list_artifact_revisions`）
- `GET /v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}/revisions/{revision}`（`get_artifact_revision`）

读取 family 的校验与现有可写 Artifact family 的校验保持分离。将 `topic-memory` 加入读取 contract，不应使它自动适用于 create、replace 或 tag 接口。

标准 Artifact response 继续作为 detail 和 revision 读取的统一协议，不新增 Topic Memory detail response。现有 `ArtifactRecord.content` 已足够承载标准响应；Topic Memory 特有的投影继续通过现有 detail 专用接口提供。

`list_artifacts` 中的 Topic Memory 实现：

- 只返回当前已发布的主题；
- 按发布时间倒序；
- 保持标准的签名 cursor 分页语义；
- 返回统一的 collection item 以及可用的展示元数据，包括标题、摘要、发布时间和来源数量；
- 保持现有 scope-read 鉴权边界。

Topic Memory 的 list 行为沿用现有 Artifact family 装配方式显式接入，不引入 family registry、自动生成注册机制或新的通用 adapter 框架。

### 2. 保持现有写入和 family 装配逻辑

当前 `master` 中的 Artifact repository family 列表、management writer、tag 行为和写入权限保持不变。本次只通过标准 Artifact 路由提供 Topic Memory 读取能力，不开放其标准写入；现有 Topic Memory 专用接口也不替换、不扩展。

### 3. OpenAPI 与生成代码

OpenAPI 继续作为公共 HTTP contract 的唯一来源。四个标准读取路由使用包含 `topic-memory` 的 read-family schema；写入和 tag 相关路由继续使用现有 family schema。生成的 HTTP model 通过 `make api-generate` 更新，禁止手工编辑生成文件。

## 备选方案

### 保持 Topic Memory 专用 list 接口

展示页面需要维护第二套 list 协议，也无法复用标准 Artifact 页面模型。本场景不采用。

### 复用 `list_artifacts`，再逐条调用 Topic Memory detail

当前 contract 不接受 Topic Memory，而且逐条获取 detail 会产生 N+1 请求。不采用。

### 引入通用 family registry

这会把本次变更扩大到通用 family 扩展框架，并要求重新设计现有 Artifact 装配和能力处理。当前 Topic Memory 读取需求不需要这层抽象，后续有明确的第二个 family 场景时再评估。

## 兼容性

- 现有 Artifact 读取、写入、tag 和 revision 调用保持兼容。
- `topic-memory` 只加入标准读取 family contract。
- 现有 Topic Memory generation、search、get 和 flush 接口继续保留。
- 不修改持久化 schema，也不修改 Artifact family 的现有装配方式。

## 测试计划

- Contract test 验证四个标准读取路由接受 `topic-memory`，同时写入和 tag 路由保持原有 family 集合。
- Runtime test 验证只返回已发布主题、发布时间排序、签名 cursor 分页、collection 元数据、scope 鉴权和空结果。
- Regression test 验证现有 Artifact family 的 list、revision、tag 和写入行为不变。

## 结论

本 RFC 只做记忆展示页面所需的最小改动：让 Topic Memory 通过标准 Artifact 读取接口访问，同时保留现有 Artifact 架构和 family 装配逻辑。
