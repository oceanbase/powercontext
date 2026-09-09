---
title: 管理 Artifact
description: 读取当前与历史版本，并按 Artifact 家族选择修改方式。
---

# 管理 Artifact

Artifact 保存有版本的结果。Memory、Experience、Skill、Handoff 和 Prompt 各自有不同的写入规则；
共用 REST 外层结构不意味着可以互换这些工作流。

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

## 按对应工作流修改

- [Memory](memory-and-context.md)：显式写入、修订或退役条目。
- [Experience 与 Skill](experience-and-skill-lifecycle.md)：发布或导出前检查并批准 Candidate。
- [Handoff](handoff-with-codex.md)：检查并提交当前工作边界。
- [Prompt](manage-prompts.md)：在一个 Scope 内自定义操作提示词。
- [标签](manage-artifact-tags.md)：组织逻辑 Artifact 和单独的 Memory 条目，不重写内容。

直接通过 REST 替换内容时，先读取当前 `ETag`，再通过 `If-Match` 发送。
缺少前置条件返回 `428`，head 已过期返回 `412`。重新读取并协调内容后再重试。
Candidate 审核使用独立的 `expected_version` 契约。两类操作都会检查所选目标的访问权限。
