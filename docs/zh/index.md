---
template: home.html
title: PowerContext
description: 换个会话继续工作，不必重新交代决定、约束和进展。可以使用受支持的 Agent，也可以通过 HTTP 接入自己的 AI。
hide:
  - navigation
  - toc
  - footer
home:
  hero:
    label: 开源，数据默认保存在本地
    title:
      - 换个会话，
      - 接着做。
    lead: 把已确认的决定、约束和进展存进 PowerContext。受支持的 Agent 和通过 HTTP 接入的 AI 应用可以在后续会话中继续使用。
    note: 模型、身份和写入权限由你控制。PowerContext 保存带来源和版本历史的记忆。
    actions:
      - label: 从 Agent 开始
        href: zh/docs/tutorials/agent-quickstart/
        kind: primary
      - label: 通过 HTTP 接入 AI
        href: zh/docs/tutorials/api-quickstart/
        kind: secondary
  continuity:
    label: 跨会话继续
    title: 背景不必再讲一遍。
    lead: 需要长期保留的信息，只需记下一次。后续会话可以找回它，核对来源，也可以查看变更记录。
    steps:
      - title: 记下
        description: 保存应该在当前对话结束后继续使用的决定、约束和下一步。
      - title: 继续
        description: 换到另一个 Agent 或应用继续工作，不必粘贴之前的聊天记录。
      - title: 核对
        description: 继续之前，查看已保存的目标、进展、依据和遗漏。
  ownership:
    label: 记忆与交接
    title:
      - 记住以后还要用的，
      - 交接手头正在做的。
    lead: 记忆保存以后仍会用到、单独拿出来也能理解的信息。更新或停用其中一条，不会丢失历史版本。
    handoff: 交接保存当前目标和进度，也说明哪里卡住、下一步做什么。换人、换 Agent 或稍后再做时，可以从这里继续。已经验证的做法可以整理成经验，需要反复执行的工作可以整理成技能。技能经过审核后再发布给 Agent。
    result: "LOCOMO：答对率 90.78% · 搜索 p95 延迟 1.38 秒"
    command: powercontext server run
    primary_action:
      label: 阅读 Agent 快速入门
      href: zh/docs/tutorials/agent-quickstart/
    secondary_action:
      label: 通过 HTTP 接入 AI
      href: zh/docs/tutorials/api-quickstart/
---
