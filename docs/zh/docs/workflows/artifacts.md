---
title: 管理 Artifact
description: 读取当前与历史版本，并按 Artifact 家族选择修改方式。
---

# 管理 Artifact

Artifact 保存有版本的结果。Memory、Experience、Skill、Handoff、Profile 和 Prompt 各自有不同的写入规则；
共用 REST 外层结构不意味着可以互换这些工作流。

## 创建和替换

管理 Artifact 主要使用以下两个写接口。两者都会生成不可变 Revision，并返回新的 head 和 `ETag`：

| 操作 | 路由 | 语义 |
| --- | --- | --- |
| Create | `POST /v1/scopes/{scope_id}/artifacts` | 按请求体中的 `family` 和 `content` 创建 Artifact 及 Revision 1。除 Handoff 外由服务端生成 `artifact_id`。 |
| Replace | `PUT /v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}` | 完整替换指定 Artifact，生成下一条 Revision。必须使用当前 head 的 `If-Match`。 |

请求体是按 `family` 判别的联合类型，不能把一个家族的内容提交给另一个家族。Memory 的 Replace 使用
`entries` 命令；其他家族提交完整内容。Handoff 是 Scope 内的单例：已存在时 Create 返回 `409`，应改用
Replace。缺少 `If-Match` 返回 `428`，ETag 过期返回 `412`；接口不支持自动合并。

## 查看当前内容和历史

使用 [API 契约](../develop/http-api.md)中的 Scope 路由：

| 操作 | 路由 |
| --- | --- |
| 列举一个家族 | `GET /v1/scopes/{scope_id}/artifacts/{family}` |
| 读取当前 head | `GET /v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}` |
| 列举版本 | `GET /v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}/revisions` |
| 读取精确证据 | `GET /v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}/revisions/{revision}` |

引用结果或向另一个 Agent 交付时，保留家族、Artifact ID 和精确 Revision。
当前 head 可以前进，历史 Revision 保持不变。

完整的请求字段、响应模型和可调试的接口示例请参阅[完整 HTTP API 参考](/api/)。
接口鉴权、并发控制和常用调用流程见 [HTTP API 使用说明](../develop/http-api.md)。
Topic Memory 当前是专用的只读检索视图，不使用上述通用写接口。

## 按对应工作流修改

- [Memory](memory-and-context.md)：显式写入、修订或退役条目。
- [Experience 与 Skill](experience-and-skill-lifecycle.md)：发布或导出前检查并批准 Candidate。
- [Handoff](handoff-with-codex.md)：检查并提交当前工作边界。
- [Prompt](manage-prompts.md)：在一个 Scope 内自定义操作提示词。
- [标签](manage-artifact-tags.md)：组织逻辑 Artifact 和单独的 Memory 条目，不重写内容。

直接通过 REST 替换内容时，先读取当前 `ETag`，再通过 `If-Match` 发送。
缺少前置条件返回 `428`，head 已过期返回 `412`。重新读取并协调内容后再重试。
Candidate 审核使用独立的 `expected_version` 契约。两类操作都会检查所选目标的访问权限。
