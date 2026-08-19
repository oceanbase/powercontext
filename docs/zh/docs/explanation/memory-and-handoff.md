---
title: 理解 Memory 和 Handoff
description: 了解长期项目 Memory 与临时工作 Handoff 的不同用途和边界。
---

# 理解 Memory 和 Handoff

PowerContext 提供长期项目 Memory 和临时 Handoff。后续任务会用到的内容不同，因此两者的用途也不同。

## Memory：长期项目知识

Memory 保存后续任务仍可能需要的、可独立理解的信息，例如决策、约束、当前状态和下一步。它属于项目 scope，可被检索，
也可以修订或停用；修订和停用保留历史，不会静默覆盖旧记录。

用户明确要求保存时，Codex 才应写入 Memory。Prompt Hook 会采集提示词作为 Source 证据，但采集不等同于自动创建
Memory，也不应为了复制当前提示词而额外写入一条 Memory。

## Handoff：临时工作交接

Handoff 将一个任务当前的目标、已验证进度、阻塞项、下一步和证据组织为可交给接手者的临时内容。它需要显式准备、检查
和完成；接手者应收到完整的 Prepared Handoff，并先核对当前代码和指令。

Draft 和 Prepared Handoff 默认不是长期项目知识。只有用户明确要求保留某个里程碑时，才提交 Handoff。

## 如何选择

| 你的需求 | 使用 |
| --- | --- |
| 后续项目工作仍需了解一项决策、约束或下一步 | Memory |
| 把正在进行的任务完整交给另一个任务、会话或模型 | Handoff |
| 记录用户当前提示词作为处理证据 | 让 Prompt Hook 采集 Source |
| 保存已经验证、需要长期复用的交接里程碑 | 经用户要求提交 Handoff，或保存为 Memory |

无论使用哪一种，都不要存储密钥、访问令牌或其他敏感信息。使用 Handoff 的具体步骤见
[在 Codex 中交接工作](../how-to/handoff-with-codex.md)。
