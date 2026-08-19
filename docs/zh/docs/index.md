---
template: docs-overview.html
title: PowerContext 文档
description: 安装 PowerContext、连接 Codex，并选择合适的集成方式。
page_type: docs-overview
overview:
  intro: 启动本地 Server，连接 Agent 宿主，再按需查找接口或配置。
  sections:
    - title: 开始使用
      description: 安装 PowerContext，把项目上下文带入下一次 Agent 会话。
      cards:
        - title: Codex 快速入门
          description: 安装 Server，并在不同 Codex 会话之间验证 Memory。
          href: zh/docs/tutorials/codex-quickstart/
        - title: 安装和运行
          description: 从 Git 引用安装并启动本地 Server。
          href: zh/docs/how-to/install-and-run/
        - title: 排查问题
          description: 处理安装、存储和连接问题。
          href: zh/docs/how-to/troubleshoot/
    - title: 使用项目上下文
      description: 根据内容的生命周期选择长期 Memory 或单次 Handoff，并在任务、会话和模型之间继续工作。
      cards:
        - title: 理解 Memory 和 Handoff
          description: 区分长期保留的内容与单次交接信息。
          href: zh/docs/explanation/memory-and-handoff/
        - title: 在 Codex 中交接工作
          description: 把当前工作移交到另一个任务、会话或模型。
          href: zh/docs/how-to/handoff-with-codex/
    - title: 集成方式
      description: 让 Agent 和观测工具连接到同一个 Server。
      cards:
        - title: 配置 Codex
          description: 准备上下文、采集提示词并维护 Memory。
          href: zh/docs/how-to/configure-codex/
        - title: 配置 Claude Code
          description: 在 Claude Code 与 Codex 之间共享项目 Memory。
          href: zh/docs/how-to/configure-claude-code/
        - title: 用 Phoenix 查看 trace
          description: 检查传输、应用和推理 span。
          href: zh/docs/how-to/trace-with-phoenix/
    - title: 参考
      description: 查找稳定的公共接口和配置边界。
      cards:
        - title: 接口
          description: Codex、DSH、CLI、Python、HTTP 和 MCP。
          href: zh/docs/reference/interfaces/
        - title: 配置
          description: 路径、Server、推理和集成设置。
          href: zh/docs/reference/configuration/
        - title: API 参考
          description: 公开的 Python 模块、模型和契约。
          href: zh/modules/
    - title: 开发
      description: 了解 Memory 和远程访问背后的契约。
      cards:
        - title: Core Protocol
          description: Source、Artifact、Trigger 和应用契约。
          href: zh/development/core-protocol/
        - title: Memory Layer
          description: 存储、恢复、维护和候选审查。
          href: zh/development/memory-layer/
        - title: 远程访问
          description: HTTP、MCP 和 Python client 的实现。
          href: zh/development/remote-access-implementation/
---
