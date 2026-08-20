---
template: home.html
title: PowerContext
description: 让项目决定、约束和下一步在 Codex 与 Claude Code 会话之间继续可用。
hide:
  - navigation
  - toc
  - footer
home:
  hero:
    label: 开源 · 项目级 · 默认本地
    title:
      - 换一个会话，
      - 项目继续向前。
    lead: PowerContext 把项目里的决定、约束和下一步保存在对话之外。再次打开 Codex 或 Claude Code 时，相关上下文已经准备好。
    note: Codex、Claude Code、Python、HTTP 和 MCP 连接同一份项目 Memory。
    actions:
      - label: 从 Codex 开始
        href: zh/docs/tutorials/codex-quickstart/
        kind: primary
      - label: 了解上下文如何延续
        href: zh/docs/explanation/memory-and-handoff/
        kind: secondary
  continuity:
    label: 同一项目，两次会话
    title: 项目背景，不必重讲。
    lead: 决定只需记录一次。后续会话可以恢复它，并核对来源和精确 Revision。
    steps:
      - title: 记录
        description: 在 Codex 中留下规则：Handoff 默认保持临时，用户明确要求后才提交。
      - title: 继续
        description: 在 Claude Code 中打开同一项目，不必重新解释之前的对话。
      - title: 核对
        description: 恢复这条规则，同时查看它的来源和精确 Revision。
  ownership:
    label: Memory 与 Handoff
    title:
      - 留下长期信息，
      - 交接当前工作。
    lead: Memory 保存决定、约束、约定和下一步，并保留可检索的历史。修订或停用条目，不会丢失记录。
    handoff: Handoff 记录当前目标、已验证进展、阻塞项和下一步行动。工作形成项目里程碑后再提交。
    result: "LOCOMO：答对率 90.78% · 搜索 p95 延迟 1.38 秒"
    command: powercontext server run
    primary_action:
      label: 阅读快速入门
      href: zh/docs/tutorials/codex-quickstart/
    secondary_action:
      label: 浏览文档
      href: zh/docs/
---
