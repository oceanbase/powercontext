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
    action:
      label: 安装并开始使用
      href: zh/docs/get-started/quickstart/
    learn_more:
      label: 了解工作原理
      href: zh/docs/get-started/core-concepts/
    community:
      star_label: Star on GitHub
      contributors_label: 查看全部贡献者
      contribute_label: 参与贡献
      license_label: Apache-2.0 开源
    cursors:
      visitor: 你
      claude_code: Claude Code
      codex: Codex
      opencode: OpenCode
    figure:
      description: 一张转台载着项目 Scope 和其中的 Memory 卡片、Source 便签、审核托盘与 Handoff 卡匣，从即将结束的会话一侧转到下一次会话一侧。
      figure: 图 1
      title: 交接转台
      hint: 选择一侧，转动转台
      readout:
        from: 当前会话 · Handoff 已准备
        to: 下一次会话 · Handoff 已确认
      steps:
        - Work Contract
        - Prepared Handoff
        - Acknowledgement
        - Task Outcome
    sessions:
      ending:
        title: Claude Code
        action: 交接
        action_done: 已交接
        lines:
          - 用 PowerContext 交接这项工作。
          - 检查 41 项通过 · 1 项受阻
          - 已保存 atomic-memory/m_k2f9@2
        result_label: 已准备
        result: handoff/h_7c3d@3
      next:
        title: Codex
        action: 确认接手
        action_done: 已确认
        prompt: 继续这个 PowerContext Handoff。
        waiting: 等待 Handoff…
        result_label: 已接受
        received:
          - label: 目标
            value: 重试失败的 Webhook
          - label: 记忆
            value: 4 条
          - label: 来源
            value: 3 个链接
          - label: 下一步
            value: 解决签名校验受阻
  photo_credit: 摄影：{author}，来自 {source}
  onboarding:
    title: 从安装到第一条记忆
    lead: 先部署 Server，通过向导选择所需能力，再接入 Agent。完整指南覆盖本机使用和从其他设备访问。
    preview_label: 配置向导验收版。
    preview_note: 本站安装命令使用 oceanbase/powercontext 的官方 master 分支。
    repository_label: 查看当前源码分支
    guide_label: 按完整指南开始
    copy_label: 复制命令
    copied_label: 已复制
    steps:
      - title: 安装 PowerContext
        description: 准备 Python 3.11 或更高版本、Git 和 uv，在运行 Server 的机器上安装本分支。
        command: uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@master"
      - title: 完成配置向导
        description: 依次选择存储、访问场景、记忆能力、Dashboard 和 Agent。向导会生成配置文件，以及适合当前配置的后续操作说明。
        command: powercontext config init
      - title: 启动 Server
        description: 在生成 .env 的目录执行启动命令。若开启 Dashboard，使用向导给出的地址和 Token 登录。
        command: powercontext server run --env-file .env
      - title: 接入 Agent 并验收记忆
        description: 按 .env.next-steps.md 创建或选择 Scope、接入 Agent。先确认真实对话进入 Source，再检查已启用的记忆处理能力。
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
    dashboard:
      title: PowerContext Dashboard
      scope: payments-api
      list_label: 记忆
      revision_label: 版本
      current_label: 当前版本
      state_label: 状态
      sources_label: 来源引用
      states:
        active: 在役
        forgotten: 已遗忘
        merged: 已合并
        retired: 已退役
      entries:
        - id: m_k2f9
          title: Webhook 失败后按指数退避重试，最多五次
          revision: 3
          state: active
        - id: m_p41x
          title: 签名密钥从 Vault 读取，不写进 .env
          revision: 1
          state: active
        - id: m_w8tz
          title: 重试队列放在 Redis
          revision: 2
          state: retired
        - id: m_c3ha
          title: Webhook POST 使用幂等键
          revision: 1
          state: merged
        - id: m_j06n
          title: 预发环境部署需人工审批
          revision: 1
          state: forgotten
      body: Webhook 投递失败后从 2 秒开始指数退避重试，最多五次。第五次仍失败时，事件转入死信列表等待处理。
      sources:
        - Claude Code 会话 · 重试方案
        - "拉取请求 #482 的评审"
        - Task Outcome · 部分完成
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
    status:
      official: 官方
      community: 社区
      evaluation: 评估
---
