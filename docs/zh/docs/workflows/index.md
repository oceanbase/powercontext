---
title: 管理上下文
description: 保存知识、继续工作、采集证据并控制访问。
---

# 管理上下文

管理上下文的核心是把一个 Scope 中的证据，按需加工为可复用的制品，并在需要时组装成一次 Agent turn
可以使用的临时上下文。推荐按下面的顺序理解和使用：

Scope 管理 Source、Artifact 和 Candidate。Source 保存证据；制品可以由证据处理形成，也可以按类型显式创建和更新。
需要审核的提案先进入 Candidate，批准后才提交不可变 Revision；其他路径遵循对应类型的提交规则。

制品提交后，可以用于上下文召回、Handoff 交接、Skill 导出或 Prompt 配置。详细路径见[上下文如何产生和使用](architecture.md)。

这条链路描述数据和权限如何流转，不包含服务可用性检查、故障排查或恢复操作；这些内容属于[部署与运维](../operate/index.md)，
不属于本目录的数据生命周期。

## 按内容类型选择入口

| 你要解决的问题 | 内容类型或概念 | 入口 |
| --- | --- | --- |
| 先理解边界、角色和部署关系 | 架构与职责 | [架构与职责](architecture.md) |
| 隔离项目并控制访问范围 | Scope | [Scope 与访问控制](scopes-and-access.md) |
| 保存原始证据或外部材料引用 | Source | [Sources 与采集](sources.md)、[从文本文件采集](ingest-text-files-with-opendal.md) |
| 读取制品版本并选择写入方式 | Artifact | [Artifact](artifacts.md) |
| 保存和召回长期事实、决定与约束 | Memory | [Memory 与上下文](memory-and-context.md) |
| 形成可复用的经验并经过审核 | Experience | [创建和审核 Experience](create-and-review-experience.md)、[Experience 生命周期](experience-and-skill-lifecycle.md) |
| 管理可导出的指令集合 | Skill | [创建和导出 Skill](create-and-export-skill.md)、[配置 Skill 目标](configure-agent-skill-targets.md) |
| 维护 Scope 的稳定背景信息 | Profile | [使用 Profile](use-profiles.md) |
| 从长期 Source 处理中提炼主题摘要 | Topic Memory | [使用 Topic Memory](topic-memory.md) |
| 跨会话传递任务边界和结果 | Handoff | [Memory 与 Handoff](memory-and-handoff.md)、[与 Codex 交接](handoff-with-codex.md)、[Handoff Report](use-handoff-report.md) |
| 给制品增加可筛选的元数据 | Tags | [管理 Artifact 标签](manage-artifact-tags.md) |
| 配置 Scope 级操作提示词 | Prompt | [管理 Prompt](manage-prompts.md) |
| 为一次 Agent turn 选择并渲染上下文 | PreparedContext | [准备上下文文本](prepare-context-text.md) |
| 启用可选的向量或混合检索 | 检索部署选项 | [配置向量检索](configure-vector-search.md) |

当前实现中的写入型 Artifact family 是 `memory`、`experience`、`skill`、`handoff`、`profile` 和 `prompt`；
读取制品时还支持专用的 `topic-memory` family。Source 是证据，不是 Artifact；Tag 是元数据，也不是独立的
Artifact family。具体能力仍以[集成能力](../integrations/capabilities.md)为准。

这些指南描述当前实现。使用前核对集成能力，不要假设 Agent 暴露了与 HTTP API 相同的全部操作。
