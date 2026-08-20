---
template: docs-overview.html
title: 从一个项目开始
description: 安装 PowerContext，验证跨会话上下文，再选择下一个任务。
page_type: docs-overview
overview:
  intro: 先完成一次跨会话闭环。安装、参考和开发细节仍可通过文档导航访问。
  sections:
    - title: 让项目继续
      description: 保存一个决定，在另一个会话中恢复它，或交接当前工作。
      cards:
        - title: Codex 快速入门
          description: 安装本地 Server，保存一个项目决定，并在新会话中恢复它。
          href: zh/docs/tutorials/codex-quickstart/
        - title: 在 Claude Code 中继续
          description: 让 Claude Code 和 Codex 打开同一份项目 Memory。
          href: zh/docs/how-to/configure-claude-code/
        - title: 在 Pi 中继续
          description: 通过原生 package 在 Pi 中打开项目上下文。
          href: zh/docs/how-to/configure-pi/
        - title: 交接当前工作
          description: 为另一个任务、会话或模型准备一份经过检查的 Handoff。
          href: zh/docs/how-to/handoff-with-codex/
    - title: 理解与运行
      description: 判断什么需要保留，配置 Server，或排查无法工作的环境。
      cards:
        - title: Memory 与 Handoff
          description: 了解哪些信息应该长期保留，哪些内容只需要临时交接。
          href: zh/docs/explanation/memory-and-handoff/
        - title: 配置
          description: 设置存储、provider、接口和运行行为。
          href: zh/docs/reference/configuration/
        - title: 排查问题
          description: 诊断连接、配置和集成问题。
          href: zh/docs/how-to/troubleshoot/
---
