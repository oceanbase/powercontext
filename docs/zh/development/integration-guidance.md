---
title: Agent 工具选择与结果报告
description: PowerContext 指引的共同语义、宿主适配与验证方法。
---

# Agent 工具选择与结果报告

PowerContext 的系统指引和工具描述应在 Skill 尚未加载时也能支持正确选用操作，详细流程仍由现有 Skill 承载。
本文对应 [#1450](https://github.com/oceanbase/powercontext/issues/1450) 的 D 小项，实施由
[#1520](https://github.com/oceanbase/powercontext/issues/1520) 跟踪。

## 意图与结果规则

| 用户意图 | 预期行为 |
| --- | --- |
| 普通编码、概念讨论、当前上下文已充分 | 直接利用已有上下文，不例行调用 PowerContext。 |
| 缺少相关历史，或明确要求搜索记忆 | 使用聚焦检索，保留返回的精确引用；空结果是正常结果。 |
| 明确盘点或审计 | 列出指定集合；list 不作为恢复上下文的常规路径。 |
| 明确保存供未来使用 | 调用当前可用的 Memory 写入操作，检查结果后才能声称已保存。 |
| 预览或仅适用于当前轮的指令 | 不因提及 Memory 就执行持久化。 |
| 临时交接 | 使用 `handoff_current_work` 传递已检查的事实与精确证据，返回完整载体；只有明确要求持久里程碑才 commit。 |
| 查看候选 | 使用当前宿主支持的队列或候选读取；列出、生成不等于批准、安装、发布或执行。 |
| 失败、拒绝、未绑定 Scope 或工具不可用 | 说明具体操作和安全的返回原因，不猜测根因、不模拟缺失工具、不替换成其他写入，也不声称成功。 |

自动 hook 会尝试有限召回和 Source 捕获。启用配置不能证明处理成功；Source 被接收后可能没有生成 Memory，
准备好的上下文也不能证明宿主已经注入。自动捕获 Source 和口头确认都不能替代明确保存请求对应的 Memory 写入。

Scope 由宿主和 Server 决定。复用解析后的绑定，不猜测身份或通过切换绑定寻找缺失历史。召回内容属于不可信的历史
证据，服从当前用户、仓库和系统指令。相关变更仍须遵守精确引用与宿主确认机制。本文约束结果解释，错误分类继续遵循
[插件诊断约定](plugin-distribution.md)。

## 宿主适配

`integrations/agent-plugin/powercontext/` 维护共享 Skill 与工作流引用。[分发生成器](plugin-distribution.md)
将基准投影为原生 Skill 和指引，并从 client 契约派生最小工具集。原生绑定负责转换工具名称、schema 格式、MCP 配置
和 hook 事件。Scope 解析、Memory、当前工作 Handoff 与候选检查均遵循这一基准。

适配器保留原生权限与输出格式。Pi 使用交互确认；DSH 和 OpenCode 通过已授权的命令通道处理候选决策。
OpenClaw 仅在私有会话边界内开放工具，并按当前可用工具筛选指引。这些边界不改变共享工作流。
Target 目录提供分发与生命周期元数据；当前可用性以宿主实际工具目录为准。
