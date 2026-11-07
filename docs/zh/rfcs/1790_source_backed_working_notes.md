---
title: RFC 1790：基于 Source 的 Working Note
description: Working Note 的设计决策，将短期工作记录保存为 Source，规定精确身份、过期与自动记忆生成的排除规则。
---

# 基于 Source 的 Working Note

- 提案名称：`source_backed_working_notes`
- 跟踪 Issue：[oceanbase/powercontext#1790](https://github.com/oceanbase/powercontext/issues/1790)
- 状态：实现决策

## 决策

PowerContext 对短期工作记录采用仅增加 Source kind 的方案，不新增持久化的 `notes` Artifact family。Working Note
保存为由服务端字段标记的 `ContentSource`，并携带描述性的 session 和 note kind 元数据。它仍然通过完整的
`(scope_id, source_type, source_id)` 身份寻址。

这与 Memory 中 `kind=working_note` 的 Entry 不同：后者是显式 Memory 写入，会进入 Memory 生命周期和检索；捕获的
Working Note 只是 evidence，除非调用方显式把它用于领域操作。

宿主可以通过 `POST /v1/sources/working-notes`、生成的 Python Client `capture_working_note`，或启用时的同名 MCP tool
完成捕获。请求必须包含调用方稳定的 `source_id`、宿主 `session_id`、正文，以及 `inspection`、
`abandoned_approach`、`validation`、`unresolved` 或 `host_context` 之一。使用相同 identity 重放不同内容仍然属于
Source conflict。调用方也可以设置一个带时区且晚于当前时间的 `expires_at`。已接受捕获的完全相同重放即使发生在
过期后，也会返回原 receipt；过期不会削弱捕获幂等性。

Working Note 分类由服务端拥有的 `ContentSource` 字段决定。`metadata.kind=working_note` 仍作为描述性元数据保留，
但它本身不是权威标记。通用 content capture 会拒绝这个保留 kind，也无法设置服务端字段。升级前仅碰巧携带该
metadata 值的 Source 仍按普通 Source 处理，并保持原有 generation 行为。

## 处理边界

Working Note 不进入自动 Source-window generation。仅仅捕获它不能触发 Memory、Topic Memory、Experience 或其他
生成 Artifact。显式消费者可以选择精确的 Source 引用；Handoff evidence resolution 会显式允许这一用途。

Working Note 内容是不可信的历史材料，不是指令、Skill、prompt policy 或可执行工具输入。

## 保留与访问

Working Note 继承 Scope Source 的授权。`session_id` 只用于按宿主/session 分组和未来清理，不构成额外授权边界。
未设置 `expires_at` 时，Note 会保持有效，直到未来显式生命周期操作改变它；时间到期后，包括 Handoff evidence
resolution 在内的新消费者必须将其视为不可用。

这里的过期是逻辑过期：它不会删除或改写不可变 Source，Source 仍可通过受支持的审计读取路径访问。物理清理、
session 结束时清理、legal hold 和跨 Scope 导出属于独立的后续生命周期工作，不在本决策的实现范围内。

Handoff 引用记录的始终是精确 Source identity。后续清理不得把该引用静默解析为另一份内容。

## 晋升

首期实现关闭晋升。未来的显式晋升操作必须提交精确 Source 引用，并通过所属领域的现有校验。不能仅因为 Note 存在，
就自动推断出 durable `fact`、`decision` 或 `constraint`。

## 不采用的替代方案

- 立即新增 Notes Artifact family：在场景验证前复制 Handoff 和 Source 的 revision/lineage 机制。
- 直接写入 Memory：会把 session 进度污染到持久检索表面。
- 只保存在宿主 transcript：无法跨宿主进行 evidence 解析。
