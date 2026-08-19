---
template: home.html
title: PowerContext
description: PowerContext 将人类与智能体协作过程转化为可交接的上下文。
hide:
  - navigation
  - toc
  - footer
home:
  hero:
    label: 开源 · 项目级 · 本地运行
    title:
      - 让项目上下文
      - 留得住。
    lead: PowerContext 让下一次 Agent 会话恢复决定、结果、当前状态和下一步，不再依赖聊天记录。
    note: 支持 Codex、DeepSeek Harness、Python、HTTP 和 MCP。
    actions:
      - label: 开始使用
        href: zh/docs/
        kind: primary
      - label: 查看 GitHub
        href: https://github.com/oceanbase/powercontext
        kind: secondary
  continuity:
    label: 跨越会话
    title: 从上次停下的地方继续。
    lead: PowerContext 把项目 Memory 放在对话之外。集成层会在下一轮开始前准备相关上下文。
    steps:
      - title: 采集
        description: 在宿主会话运行时，把提示词记录为 Source 证据。
      - title: 维护
        description: 记录、搜索、修订、停用并审计项目 Memory。
      - title: 恢复
        description: 为同一项目的下一次会话准备有边界的上下文。
  ownership:
    label: 默认本地
    title:
      - 让工作留在
      - 它所在的地方。
    lead: 用 SQLite 本地运行 Server。Codex、DeepSeek Harness、CLI、Python、HTTP 和 MCP 访问同一份持久化 Memory。
    command: powercontext server run
    primary_action:
      label: 阅读快速入门
      href: zh/docs/tutorials/codex-quickstart/
    secondary_action:
      label: 浏览文档
      href: zh/docs/
---
