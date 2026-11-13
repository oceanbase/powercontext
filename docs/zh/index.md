---
title: PowerContext
description: 让项目知识和当前任务状态在人、Agent 与不同会话之间延续。
home:
  hero:
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
    demo_label: Claude Code 准备一份 Handoff，PowerContext 把它记录在项目 Scope 中，Codex 确认后接着完成工作。
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
    board:
      title: PowerContext Dashboard
      scope: payments-api
      kind: Handoff
      heading: 重试失败的 Webhook
      ref: handoff/h_7c3d
      revision_label: 版本
      waiting_label: 等待中
      steps:
        - name: Work Contract
          note: 已约定目标和检查项
        - name: Prepared Handoff
          note: Claude Code · 检查 41 项通过，1 项受阻
        - name: Acknowledgement
          note: Codex 已解析第 3 版
        - name: Task Outcome
          note: 工作结束时记录
      carried_label: 随工作一起交接
      carried:
        - label: Memory
          value: 4 条在役
        - label: 来源
          value: 3 个链接
        - label: 下一步
          value: 解决签名校验受阻
  features:
    title: 会话结束了，工作还没有。
    lead: 你做了决定、改了代码，但任务还没有完成。PowerContext 把有用的上下文留在项目中，让下一次接手从当前状态继续。
    scope: payments-api
    memory:
      title: 决定和约束不随会话消失，每一版都能回看。
      label: Dashboard 的 Memory 列表，其中一条按保存的版本展开
      window: Memory
      revision_label: 版本
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
    handoff:
      title: 下一次会话从当前状态开始，不必重读整段对话。
      label: 一份已准备的 Handoff，列出目标、已验证的进展、受阻项和下一步
      window: Handoff
      ref: handoff/h_7c3d@3
      fields:
        - label: 目标
          value: 重试失败的 Webhook
        - label: 已验证
          value: 检查 41 项通过
        - label: 受阻
          value: 重放事件的签名校验失败
        - label: 下一步
          value: 解决签名校验受阻
    review:
      title: 有效的做法先经过审核，再被复用。
      label: 审核收件箱中的一条 Experience 候选，等待批准或拒绝
      window: 审核收件箱
      kind: Experience 候选
      fields:
        - label: 情境
          value: 重放的 Webhook 签名校验失败
        - label: 做法
          value: 解析前先按原始请求体验签
        - label: 结果
          value: 41 次重放全部通过
        - label: 经验
          value: 对字节签名和验签，不对解析后的 JSON
      approve: 批准
      reject: 拒绝
      approved: 已批准为第 1 版
    skill:
      title: 审核通过的流程成为 Skill，需要时才导出。
      label: 一个托管 Skill，包含校验检查、已批准的版本和导出目标
      window: Skill
      name: replay-failed-webhooks
      description: 安全地重放进入死信列表的 Webhook 事件。
      checks_label: 校验检查
      checks:
        - 带有幂等键
        - 重放前已验签
      status: 已批准 · 第 2 版
      export: 已导出到 Codex
    recall:
      title: 召回有上限。每一轮拿到的是字节预算，不是全部历史。
      label: 一次 PreparedContext 结果，显示各部分如何占用同一份字节预算
      window: PreparedContext
      status: ready
      budget_label: 字节预算
      budget: 2.9 / 4 KB
      sections:
        - name: Memory
          size: 1.6 KB
          share: 40
        - name: Experience
          size: 0.9 KB
          share: 22
        - name: Topic Memory
          size: 0.4 KB
          share: 10
  ecosystem:
    title:
      - 工作换了 Agent，
      - 上下文仍然延续。
    lead: 一项任务可能从一个 Agent 开始，再交给另一个 Agent。PowerContext 把过程中形成的知识、进展和做法留在项目中，让下一位参与者从当前状态继续。
    docs_label: 打开配置文档
    all_agents_label: 查看全部支持的 Agent
    status:
      official: 官方
      community: 社区
      evaluation: 评估
    setup_title: 一次接入多个 Agent
    setup_lead: setup 命令会为你指定的每个宿主完成安装、配置和诊断，并连接到同一个 Server。
    interfaces_title: 或者基于同一套契约自行集成
    interfaces:
      - name: HTTP
        description: 完整的远程应用契约。
        href: zh/docs/develop/http-api/
      - name: Python Client
        description: 在你自己的应用中使用带类型的接口。
        href: zh/docs/develop/api-quickstart/
      - name: MCP
        description: 面向 Agent 的精选能力，通过 /mcp 提供。
        href: zh/docs/integrations/
      - name: Core 协议
        description: 组合你自己的 Source、Artifact 和 Trigger 实现。
        href: zh/docs/develop/interfaces/
  proof:
    title: 在公开基准上测过。
    lead: 两项结果都来自已公开的评测工具。评测页写明了方法，以及这些数字不再适用的边界。
    stats:
      - value: 90.78%
        label: LoCoMo 准确率，共 1,540 个计分问题
        detail: 同一批问题上，全量上下文重放的准确率为 52.9%。
      - value: 25.4%
        label: SWE-bench Pro 输入 token 减少
        detail: 每 100 题开启 PowerContext 约 2.25 亿，关闭时约 3.02 亿。
    link:
      label: 评测方法、结果和适用边界
      href: zh/benchmarks/
  testimonials:
    title: 用户怎么说
    pause: 暂停滚动
    play: 继续滚动
    profile: "{name} 的 X 主页"
  onboarding:
    title: 从安装到第一条记忆
    lead: 先部署 Server，通过向导选择所需能力，再接入 Agent。完整指南覆盖本机使用和从其他设备访问。
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
---
