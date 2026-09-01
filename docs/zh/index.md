---
template: home.html
title: PowerContext
description: 通过 HTTP 为自己的 AI 接入 Memory，或选择受支持的 Agent 跑通跨会话恢复与 Handoff。
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
    lead: PowerContext 把决定、约束、下一步和任务边界保存在对话之外。现有 AI 应用可以直接调用 HTTP API；Codex、Claude Code、DSH、OpenCode 等 Agent 也可以连接同一个 Server。
    note: 你控制模型、身份和写入授权；PowerContext 提供带 citation 的 Memory、请求时上下文和保留历史的修订。
    actions:
      - label: 选择你的 Agent
        href: zh/docs/tutorials/agent-quickstart/
        kind: primary
      - label: 为自己的 AI 接入 API
        href: zh/docs/tutorials/api-quickstart/
        kind: secondary
  continuity:
    label: 同一项目，多个会话
    title: 一个 Agent 停下，工作仍能继续。
    lead: 实现、独立检查和兼容性验证可以由不同 Agent 完成；Handoff 传递经过检查的边界，人决定是否继续。
    steps:
      - title: Agent A 实现
        description: 记录项目决定、约束和下一步，并把目标、改动、检查与遗漏整理为 Handoff。
      - title: Agent B 独立核对
        description: 在另一个 Host 中按 exact Revision 接收，再用当前仓库重新验证证据和风险。
      - title: 人决定
        description: 人确认范围、能力和授权，决定继续、澄清或拒绝；接收不等于任务已经完成。
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
      label: 打开 Agent 分步入门
      href: zh/docs/tutorials/agent-quickstart/
    secondary_action:
      label: 打开 HTTP API 生命周期教程
      href: zh/docs/tutorials/api-quickstart/
---
