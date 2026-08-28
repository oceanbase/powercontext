---
title: 理解 Experience 与 Skill 生命周期
description: 了解证据如何变成经过审核的 Experience 和 managed Skill Artifact，以及它们何时可用。
---

# 理解 Experience 与 Skill 生命周期

Experience 和 managed Skill 使用同一个审核边界。证据支撑 proposal，人工审核一个精确 Candidate version，批准后
创建不可变的 Artifact Revision。Generation 不会自行批准结果。

## 先有证据

Source 记录已完成的任务结果、人工材料等证据。Artifact 引用指向一个精确的 approved Revision。Generation 和 proposal
操作保留这些引用作为 lineage，reviewer 可以据此核对内容来源。

引用必须与 Candidate 属于同一个 scope。替换现有内容时，还必须标明要替换的精确 Artifact Revision。

## Candidate 是待审核 proposal

Experience 或 Skill 操作会创建 `pending` Candidate。Candidate 有当前 head 和带编号的不可变 version。修订会把一份完整的
替代 proposal 追加为下一个 version。

Reviewer 只能操作自己检查过的 version。`expected_version` 防止 Candidate 被其他写入者修改后，批准、拒绝或修订仍静默
作用于旧内容。批准和拒绝都是终态。

Candidate 不是 Artifact Revision：

- `pending` 内容没有 `result_artifact`；
- 批准会在同一个事务中写入 proposal，并返回精确的 `result_artifact`；
- 拒绝只记录 decision reason，不写入 Artifact。

## Experience 保存可复用结果

Experience 包含 situation、action、observed outcome 和 reusable lesson。使用模型生成时必须配置 generation inference。
已经拥有完整类型化内容的人或 integration 可以通过 HTTP 或 Python `propose_experience` operation 提交，不需要模型。
两条路径都只会创建 pending Candidate。

批准后，当前 Experience head 可以在同一 scope 内参与 `PreparedContext` 召回。是否被选中仍取决于 query 和共享输出预算。
Pending、rejected Candidate 以及历史 Experience Revision 都不会进入召回。

## Managed Skill 保存指令

Managed Skill 包含名称、用于发现的描述、instructions、validation checks 和精确 lineage。Generation origin 声明 proposal
使用哪种直接证据：

| Origin | 必需的直接证据 |
| --- | --- |
| `experience` | 一个或多个 approved Experience Revision；可以附带精确 Source |
| `source` | 一个或多个精确 Source，不能引用 Artifact |
| `usage` | 一个精确 target Skill Revision，以及记录使用情况的 Source |

批准会创建不可变的 Skill Revision。Managed Skill 不会进入 `PreparedContext`，也不会自行安装或授予执行权限。只有显式
导出并创建 host-local projection 后，Codex 才能使用某个 approved Revision。

## Revision 保留历史

创建 replacement Candidate 时必须提供精确的当前 target。批准会在同一 Artifact identity 下创建下一个 Revision。如果
其他批准先推进了 target head，旧 replacement 会保持 pending，批准请求返回 conflict。Reviewer 必须先检查新的 head，
再决定如何处理。

更早的 approved Revision 仍可被精确读取。导出的 Skill 目录只是某个精确 Revision 的副本，managed Artifact 始终是内容
权威。

审核步骤见[审核 Candidate](../how-to/review-candidates.md)，Experience 操作见
[创建并审核 Experience](../how-to/create-and-review-experience.md)，Skill 操作见
[创建并导出 managed Skill](../how-to/create-and-export-skill.md)。
