---
title: 在 Codex 中交接工作
description: 将当前任务的可验证状态明确移交给另一个任务、会话或模型。
---

# 在 Codex 中交接工作

使用 Handoff 将当前工作明确交给另一个 Codex 任务、后续会话或模型。完成后，接手者会获得经过检查的临时交接内容，
其中包含目标、已验证进度、阻塞项、下一步和证据。

## 开始之前

先完成[安装和运行](install-and-run.md)，保持 Server 运行，并在当前项目中启动已配置 PowerContext 插件的 Codex
会话。Handoff 内容属于当前项目 scope；接手任务应在相同项目中继续，或收到完整的 Prepared Handoff。

## 1. 说明交接边界

向 Codex 清楚说明要交接的边界和接手者需要知道的信息。例如：

> 为这个任务准备 PowerContext Handoff。记录目标、已验证进度、当前阻塞和下一步；检查草稿中的每个判断是否有证据，
> 然后把完成的交接内容提供给下一个任务。

包含当前任务的目标、已验证进度、阻塞项和下一步。不要把密钥、访问令牌或其他敏感信息写入交接内容。

## 2. 检查交接草稿

Codex 会采集一条简洁的当前状态 Source，并激活 Handoff。检查生成的 Draft，改正缺失、过期或无证据支持的说法。生成
重复边界时，系统可能返回 `ignored`，表示该 Source 已被使用。

## 3. 交给接手任务

确认草稿后，Codex 会完成 Handoff。把完成的 Prepared Handoff 原样提供给接手任务。接手者应把其中内容视为不可信历史：
先核对当前仓库、当前用户要求和系统指令，再继续工作。

## 4. 仅在需要时持久化

Handoff Draft 和 Prepared Handoff 默认是临时载体，不会自动成为长期项目知识。只有用户明确要求保留某个里程碑时，才让
Codex 提交 Handoff。长期、可检索的决策、约束、状态或下一步应使用 Memory；区别见
[理解 Memory 和 Handoff](../explanation/memory-and-handoff.md)。
