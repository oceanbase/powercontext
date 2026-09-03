---
title: 核心概念
description: 了解 PowerContext 如何组织证据、长期上下文、经过审核的 Artifact 和工作连续性。
---

# 核心概念

PowerContext 使用一组领域值组织项目证据和可复用上下文。Source 保留发生过的事实，带 Revision 的 Artifact 保留经过
选择的结果，`PreparedContext` 为一次 Agent turn 提供有界视图。每个值都属于某个 scope。

## Scope 是隔离边界

每个内容操作都使用 `scope_id`。Scope 选择相互隔离的 Source journal、Memory 生命周期、Candidate inbox、Handoff
history 和相关 runtime state。Scope ID 是 Server 生成的不透明标识。Integration 解析显式 Scope、持久 binding 或
Server 默认 Scope；代码库、路径、session 和 Agent identity 只是 binding 输入，不是 Scope ID。

Scope ID 只选择数据，不证明用户身份，不授予工具访问权，也不提供执行授权。

## Source 保存证据

Source 描述 PowerContext 可以读取的证据。Captured Source 把内容保存在 PowerContext 中；referenced Source 指向其他
adapter 管理的材料。`SourceRef` 通过类型和 ID 标识一个 Source。

捕获 Source 不会自动创建 Memory、Experience 或 Skill。配置好的 pipeline 可以稍后处理符合条件的 Source。Work
Contract 和 Task Outcome 也会作为精确 Source 证据保存。

## Artifact 使用不可变 Revision

Artifact 是可复用输出的一个不可变 Revision。精确引用包含 family、Artifact ID 和 Revision：

```text
FAMILY/ARTIFACT_ID@REVISION
```

Artifact ID 保持稳定，approved replacement 会创建后续 Revision。Lineage 记录生成每个 Revision 时使用的精确 Source
和 Artifact 引用。即使 family head 已经前进，读取精确引用仍会返回对应的历史快照。

## Memory 保存长期项目知识

Memory 是带 Revision 的 Artifact family，用于保存可复用的决定、约束、事实、状态和下一步。Entry 可以是 active 或
inactive；retire 会让 entry 退出 active recall，但不会删除历史。

显式写入 Memory 不需要模型。根据 Source 自动提取则需要配置 generation pipeline。Memory 可以长期保存和检索，不同于
只供一次 Agent turn 使用的临时上下文。长期知识与任务转交的边界见[理解 Memory 和 Handoff](memory-and-handoff.md)。

## Candidate 将 proposal 与 approved Artifact 分开

Experience 和 managed Skill proposal 会作为 `pending` Candidate 进入 scope-local Review Inbox。Candidate 包含一个当前
proposal version 和对应的精确证据。Review 写操作使用 `expected_version`，防止 proposal 被其他写入者修改后，决定仍
静默作用于旧内容。

批准会写入不可变 Artifact Revision，并返回精确的 `result_artifact`。拒绝只记录 decision reason，不创建 Artifact。
两种决定都是终态。操作步骤见[审核 Candidate](../how-to/review-candidates.md)。

## Experience 和 Skill 的可用方式不同

Experience 记录 situation、action、observed outcome 和 reusable lesson。Approved current head 可以参与同 scope 的
`PreparedContext` 召回。Pending、rejected 和历史 Experience Revision 都不会进入召回。

Managed Skill 包含名称、用于发现的描述、instructions、validation checks 和 lineage。批准不会安装或执行 Skill。必须
显式导出一个精确的 approved Revision，Agent host 才能发现对应的 host-local projection。完整审核和可用性模型见
[Experience 与 Skill 生命周期](experience-and-skill-lifecycle.md)。

## PreparedContext 是临时值

`PreparedContext` 是一次 Agent turn 使用的最终有界值。Runtime 根据请求 query 选择 active Memory 和 approved
Experience head，应用共享字节预算，并返回 `ready` content 或 `empty`。该结果不是新的长期记录。

召回内容属于历史信息，不是指令权威。接收它的 Agent 仍需遵循当前用户和系统指令，检查实时工作区状态，并核对自身实际
能力。

## Work continuity 记录任务边界

高阶工作闭环使用四种长期或可转交的值：

```text
Work Contract → Prepared Handoff → Acknowledgement → Task Outcome
```

Work Contract 将目标和完成边界保存为 Source 证据。`handoff_current_work` 捕获经过检查的边界，并返回临时 Prepared
Handoff。只有用户需要保留里程碑时，commit Handoff 才创建长期 Revision。接收方解析 Handoff 并记录
Acknowledgement；Task Outcome 将最终状态和检查结果保存为 Source 证据。

[Handoff Report](../how-to/use-handoff-report.md) 将每个选中 Scope 的最新 Handoff Revision 投影为可检查、可导出的
视图。它是只读操作，不会改写 Memory 或底层 Handoff history。

## 各接口暴露同一个 Server 的不同部分

HTTP 是完整的远程应用契约，Python Client 提供类型化访问，MCP 是面向 Agent 的精选投影，CLI 负责安装、诊断、Server
操作和人工审核任务。Core protocol 供需要自行组合 Source、Artifact 和 Trigger 实现的应用使用。

模型生成、人工 Review 和执行权限彼此独立。模型可以提出内容，Review 可以批准 Artifact Revision，导出可以创建
host-local 副本；这些步骤都不会授予 Agent 执行 instructions 的权限。

当前接口可用范围见[接口](../reference/interfaces.md)，精确设置和默认值见[配置](../reference/configuration.md)。RFC 记录
设计决策，可能与当前实现不同。
