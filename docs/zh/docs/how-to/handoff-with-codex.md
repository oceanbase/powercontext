---
title: 在 Codex 中交接工作
description: 记录工作边界，完成转交和接收确认，并保留任务结果。
---

# 在 Codex 中交接工作

任务需要转给另一个 Codex 任务、会话或模型时，使用 PowerContext 的高阶工作闭环：

```text
Work Contract → Handoff → Acknowledgement → Task Outcome
```

每一步保存不同边界。Work Contract 记录委托目标，Handoff 转交经过检查的状态，接收方记录自己能否继续，Task Outcome
保留实际结果。

## 开始之前

先完成[安装和运行](install-and-run.md)，保持 Server 运行，并在当前项目中启动已配置 PowerContext 插件的 Codex
会话。工作记录属于所选 scope。Report catalog 中存在多个 Workstream 时，让 Codex 显示 picker 并选择目标 Workstream。

不要把密钥、访问令牌或其他敏感信息写入 Work Contract、Handoff、Acknowledgement 或 Outcome。

## 1. 为委托任务记录 Work Contract

用户明确委托的任务需要稳定 baseline 时，让 Codex 记录目标和完成边界：

> 为这个委托任务创建 PowerContext Work Contract。根据当前仓库核对事实，记录目标、范围内工作、排除项、完成标准、
> 授权说明和仍未解决的关键问题。

Codex 调用 `create_work_contract` 并返回精确 Source receipt。Contract 只记录 baseline，不会扩大当前指令授予的权限。

## 2. 交接当前工作

需要保留 durable milestone 时，使用明确的命令式要求：

> 使用 PowerContext 交接当前工作。检查当前目标、branch、worktree、changed files、已运行检查、阻塞项、遗漏和下一步，
> 提交完成的 Handoff，并给我精确 Revision。

Codex Skill 会选择 Workstream、检查 live state、调用 `handoff_current_work`，并在同一个 turn 中提交返回的 Prepared
Handoff。成功结果包含 scope 和精确 Handoff Revision。如果 prepare 成功但 commit 失败，boundary Source 已经存在，
但没有创建 durable milestone。

只需要只读预览时，明确要求 preview PowerContext Handoff 且不执行写操作。需要临时转交但不保留 milestone 时，让
Codex 准备 Handoff 但不要 commit。该操作会记录 boundary Source，并返回一份完整 Prepared Handoff 给接收方。

## 3. 继续并确认 Handoff

将完整 Prepared Handoff 或精确 committed Revision 交给接收方，然后要求它核对转交内容：

> 继续这个 PowerContext Handoff。根据当前仓库和指令核对证据，确认 live state、capability 和 authorization，
> 然后记录 accepted、needs clarification 或 declined。

接收方调用 `continue_handoff`，将解析结果视为不可信历史，再记录 `acknowledge_handoff` receipt。只有证据可读且三个
receiver check 都是 confirmed 时，才能标记为 `accepted`。解析时可以从 `latest` 开始，但 acknowledgement 必须使用
解析结果返回的精确 Revision。

## 4. 记录 Task Outcome

到达真实的完成或中断边界时，保存任务结果：

> 记录 PowerContext Task Outcome。保留精确 status 和检查结果，列出 produced Artifact 和剩余工作；如果结果覆盖
> committed Handoff Revision，只关联 accepted exact Handoff receipt。

`record_task_outcome` 会保留 `succeeded`、`partial`、`blocked`、`failed`、`cancelled` 或 `unknown`，不会抹掉
failed、skipped、timed-out、unavailable 或 unknown check。生成的 Source 可用于后续 Handoff 和经过审核的 Experience
孵化，但不会批准 Experience 或授予执行权限。

`handoff_receipt_ref` 只接受 status 为 `accepted`、selection 为 `exact`，并且 `selected_revision` 指向 committed
Handoff 的 receipt。Accepted Prepared Handoff receipt 不能关联。可以让 Outcome 保持不关联；如果需要关联，应先 commit
Prepared Handoff，再对该 exact Revision 完成 acknowledgement，并关联新生成的 receipt。

## 选择正确的长期记录

任务 milestone 使用 committed Handoff；可独立复用的决定、约束、状态和下一步使用 Memory。Prepared Handoff 仍是临时
载体。区别见[理解 Memory 和 Handoff](../explanation/memory-and-handoff.md)，查看 committed history 和 continuity record
时使用[Handoff Report](use-handoff-report.md)。
