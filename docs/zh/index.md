---
title: PowerContext
description: 让项目知识和当前任务状态在人、Agent 与不同会话之间延续。
home:
  hero:
    label: 开源 · 本地优先
    title:
      - 让工作跨会话
      - 继续推进。
    lead: PowerContext 把决定、约束、依据和当前进展留在项目中。换人、换 Agent 或换个会话时，可以直接核对当前状态并继续工作。
    actions:
      - label: 开始使用
        href: zh/docs/tutorials/agent-quickstart/
        kind: primary
      - label: 了解工作原理
        href: zh/docs/explanation/core-concepts/
        kind: secondary
  continuity:
    title: 会话结束了，工作还没有。
    lead: 你做了决定、改了代码，但任务还没有完成。PowerContext 把有用的上下文留在项目中，让下一次接手从当前状态继续。
    visual_label: PowerContext 如何把上下文从当前会话带到下一次工作
    steps:
      - title: 当前会话
        items:
          - 决定
          - 约束
          - 依据
          - 已验证进展
      - title: PowerContext
        items:
          - Memory
          - Handoff
      - title: 下一次接手
        items:
          - 相关上下文
          - 当前目标
          - 来源链接
          - 下一步
  ecosystem:
    title:
      - 工作换了 Agent，
      - 上下文仍然延续。
    lead: 一项任务可能从一个 Agent 开始，再交给另一个 Agent。PowerContext 把过程中形成的知识、进展和做法留在项目中，让下一位参与者从当前状态继续。
    visual_label: 一项任务在不同 Agent 之间流转，PowerContext 保留过程中形成的项目 Artifact
    agents_label: 工作从已接入的 Agent 开始
    all_agents_label: 查看全部支持的 Agent
    docs_label: 打开配置文档
    runtime_label: 上下文在当前项目 Scope 中持续积累
    artifacts_label: 工作过程中形成可复用的资产
    artifacts:
      - name: Memory
        description: 长期项目知识
      - name: Handoff
        description: 当前任务状态
      - name: Experience
        description: 审核后的做法
      - name: Skill
        description: 已导出的流程
    output_label: 换一个 Agent，继续当前工作
---
