---
template: docs-overview.html
title: 选择 Agent 或 API
description: 为现有 AI 应用接入 HTTP Memory API，或为选定 Agent 配置集成并验证 Memory 与 Handoff。
page_type: docs-overview
overview:
  intro: 选择教程学习一条完整路径，选择操作指南完成具体任务，选择原理说明理解系统行为，或选择参考页查询当前契约和设置。
  sections:
    - title: 从这里开始
      description: 先通过 API 或 Agent 跑通一次。其余说明帮助你在所用 Agent 中继续工作。
      cards:
        - title: HTTP API 生命周期教程
          description: 用一个小型 Python 流程接入上下文、Experience、Skill 与 Review；接口参考统一使用 Scalar。
          href: zh/docs/tutorials/api-quickstart/
          featured: true
        - title: Agent 分步入门
          description: 选择 Host，安装并诊断集成，跑通 Memory，再按一句话、pc_* 或 /pc 路径完成 Handoff。
          href: zh/docs/tutorials/agent-quickstart/
          featured: true
        - title: Codex 完整教程
          description: 深入 Codex Hook、MCP Skill、Memory 生命周期与一句话 durable Handoff。
          href: zh/docs/tutorials/codex-quickstart/
        - title: 交接当前工作
          description: 使用 Work Contract、Handoff、Acknowledgement 和 Task Outcome 完成完整任务闭环。
          href: zh/docs/how-to/handoff-with-codex/
        - title: 在 Claude Code 中继续
          description: 让 Claude Code 和 Codex 打开同一份项目 Memory。
          href: zh/docs/how-to/configure-claude-code/
        - title: 使用 DeepSeek Harness
          description: 在每个 model step 准备上下文，并使用 pc_* Memory 与 Handoff tools。
          href: zh/docs/how-to/configure-dsh/
        - title: 在 Pi 中继续
          description: 通过原生 package 在 Pi 中打开项目上下文。
          href: zh/docs/how-to/configure-pi/
        - title: 在 OpenClaw 中继续
          description: 通过 memory 插件在 OpenClaw 中打开项目上下文。
          href: zh/docs/how-to/configure-openclaw/
        - title: 在 OpenCode 中继续
          description: 通过原生 OpenCode 插件召回并维护项目上下文。
          href: zh/docs/how-to/configure-opencode/
        - title: 在 Hermes 中继续
          description: 使用 MemoryProvider、/pc companion 和 Handoff 生命周期操作。
          href: zh/docs/how-to/configure-hermes/
        - title: 在 WorkBuddy 中继续
          description: 使用 Prompt Hook、MCP Memory 和一句话 durable Handoff。
          href: zh/docs/how-to/configure-workbuddy/
        - title: 加载 Agent Plugin
          description: 在兼容 Agent 中使用可复用的 PowerContext skills 和 MCP 配置。
          href: zh/docs/how-to/configure-agent-plugin/
    - title: 理解与运行
      description: 判断什么需要保留，配置 Server，或排查无法工作的环境。
      cards:
        - title: 完整功能 Quick Start
          description: 生成一份经过校验的配置，并验证抽取、向量搜索和 Agent 闭环。
          href: zh/docs/how-to/full-capability-runtime/
        - title: 核心概念
          description: 理解 scope、证据、带 Revision 的 Artifact、prepared context 和工作连续性。
          href: zh/docs/explanation/core-concepts/
        - title: Memory 与 Handoff
          description: 了解哪些信息应该长期保留，哪些内容只需要临时交接。
          href: zh/docs/explanation/memory-and-handoff/
        - title: Experience 与 Skill 生命周期
          description: 了解证据如何变成经过审核的 Artifact Revision，以及它何时可用。
          href: zh/docs/explanation/experience-and-skill-lifecycle/
        - title: 配置
          description: 设置存储、provider、接口和运行行为。
          href: zh/docs/reference/configuration/
        - title: 配置 Server 环境
          description: 生成一份显式环境文件，完成校验，并用相同设置启动 Server。
          href: zh/docs/how-to/configure-server-environment/
        - title: 配置向量检索
          description: 设置 embedding profile，并确认 vector 和 hybrid search 是否可用。
          href: zh/docs/how-to/configure-vector-search/
        - title: 配置 Agent Skill target
          description: 注册本地 Codex 或 Claude Code Skill 目录，用于发现和 managed publication。
          href: zh/docs/how-to/configure-agent-skill-targets/
        - title: 部署 Server
          description: 使用持久化数据、健康检查、鉴权和安全网络边界运行 Server。
          href: zh/docs/how-to/deploy-server/
        - title: HTTP API
          description: 查阅所有 Server 路径、错误语义和完整 OpenAPI 契约。
          href: zh/docs/reference/http-api/
        - title: 审核 Candidate
          description: 检查、修订、批准或拒绝待审核的 Experience 和 Skill 提案。
          href: zh/docs/how-to/review-candidates/
        - title: 创建 Experience
          description: 根据精确证据生成 Experience，完成审核并验证 approved Revision。
          href: zh/docs/how-to/create-and-review-experience/
        - title: 创建 managed Skill
          description: 生成并审核 managed Skill，再将一个精确 Revision 导出给 Codex。
          href: zh/docs/how-to/create-and-export-skill/
        - title: Handoff Report
          description: 检查 scope、保存 Handoff Revision，并了解当前报告能力。
          href: zh/docs/how-to/use-handoff-report/
        - title: 排查问题
          description: 诊断连接、配置和集成问题。
          href: zh/docs/how-to/troubleshoot/
---
