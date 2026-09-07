---
title: 基准测试
description: 查看 PowerContext 能否找回长期上下文，以及这些上下文能否帮助 Codex 解决代码仓库问题。
benchmark:
  hero:
    label: 产品评测
    title:
      - 衡量上下文
      - 带来的改变。
    lead: LoCoMo 衡量 PowerContext 能否从长期对话中找回正确依据；SWE-bench Pro 衡量项目上下文能否帮助 Codex 解决更多代码仓库问题。
    actions_label: 跳转到具体评测
    actions:
      - label: LoCoMo 结果
        target: locomo
      - label: SWE-bench Pro 结果
        target: swe-bench
      - label: 方法与来源
        target: methods
    visual_label: LoCoMo 准确率与搜索 p95 延迟散点图
    results:
      - name: LoCoMo
        value: 90.78
        decimals: 2
        suffix: "%"
        display: 90.78%
        accessible: 问答准确率 90.78%
        metric: 问答准确率
      - name: SWE-bench Pro
        value: 86.73
        decimals: 2
        suffix: "%"
        display: 86.73%
        accessible: 任务解决率 86.73%
        metric: 开启 PowerContext 后的任务解决率
  orientation:
    title: 两项评测，两个问题。
    lead: Agent 先要找回发生过什么，再要把上下文用于真实工作。两项评测分别检验这两层能力。
    tests:
      - name: LoCoMo
        question: 系统能记住一段长期对话吗？
        answer: 评测直接回忆、时间推理、多步推理，以及结合对话证据的开放域回答。
        target: locomo
        link: 查看记忆评测
      - name: SWE-bench Pro
        question: Agent 能把上下文变成可用补丁吗？
        answer: 给 Codex 一个真实仓库和 Issue，再用可执行测试评判最终补丁。
        target: swe-bench
        link: 查看编码评测
  locomo:
    title: LoCoMo：找回正确上下文
    lead: LoCoMo 根据跨 Session 的长对话提问。PowerContext 需要先找到相关对话依据，再生成答案。本次结果覆盖类别 1 至 4，共 1,540 道计分题。
    facts:
      - label: 长对话
        value: "10"
      - label: 计分问题
        value: 1,540
      - label: 问题类型
        value: "4"
    categories_label: PowerContext 结果覆盖的 LoCoMo 问题类型
    categories:
      - name: 单跳回忆
        count: "841"
        description: 从长对话中找回一条明确事实。
      - name: 时间推理
        count: "321"
        description: 推理跨 Session 的日期、顺序与持续时间。
      - name: 多跳推理
        count: "282"
        description: 连接多条事实后生成答案。
      - name: 开放域问答
        count: "96"
        description: 结合对话证据与通用知识回答。
    results_title: 准确率与检索开销
    results_lead: 我们报告 PowerContext、PowerMem 和完整上下文 Prompt 的回答准确率、搜索 p95 延迟与回答 Token。
    tabs_label: LoCoMo 结果指标
    metrics:
      - id: accuracy
        label: 准确率
        callout: +37.88 个百分点
        callout_detail: 相比完整上下文
        chart_label: 准确率对比，越高越好。
        direction: 越高越好。
        rows:
          - name: PowerContext
            display: 90.78%
            scale: 90.78
            value: 90.78
          - name: PowerMem
            display: 87.79%
            scale: 87.79
            value: 87.79
          - name: 完整上下文
            display: 52.9%
            scale: 52.9
            value: 52.9
      - id: latency
        label: 搜索 p95
        callout: 12.4 倍
        callout_detail: 完整上下文耗时更多
        chart_label: 搜索 p95 延迟对比，越低越好。
        direction: 越低越好。
        rows:
          - name: PowerContext
            display: 1.38 秒
            scale: 8.06
            value: 1.38
          - name: PowerMem
            display: 1.44 秒
            scale: 8.41
            value: 1.44
          - name: 完整上下文
            display: 17.12 秒
            scale: 100
            value: 17.12
      - id: tokens
        label: 回答 Token
        callout: 减少 93.7%
        callout_detail: 相比完整上下文
        chart_label: 单个问题的回答 Token 对比，越低越好。
        direction: 越低越好。
        rows:
          - name: PowerContext
            display: 约 1.65k
            scale: 6.35
            value: 1650
          - name: PowerMem
            display: 约 0.9k
            scale: 3.46
            value: 900
          - name: 完整上下文
            display: 26k
            scale: 100
            value: 26000
    scope_title: 结果覆盖范围
    scope: 90.78% 来自类别 1-4 的 1,540 个问题，其中答对 1,398 个。该结果不代表 LoCoMo 的事件总结或多模态对话生成任务。
  swe:
    title: SWE-bench Pro：把上下文转化为可用补丁
    lead: 为了衡量 PowerContext 对结果的影响，我们用同一套 Codex 配置在 public v2 的 731 个任务上完成两次运行。两组仅改变是否启用 PowerContext。
    task_count: 731
    method:
      - title: 相同任务集
        description: OFF 与 ON 都运行 public v2 的 731 个仓库问题。
      - title: 相同模型
        description: Codex 使用 gpt-5.6-sol，推理等级为 medium。
      - title: 受控开关
        description: OFF 禁用 Plugin，ON 启用已安装的 PowerContext Plugin。
    scores:
      - label: PowerContext OFF
        count: 602
        rate: 解决率 82.35%
        accessible: 关闭 PowerContext 时，731 个任务中解决 602 个
        kind: "off"
      - label: PowerContext ON
        count: 634
        rate: 解决率 86.73%
        accessible: 开启 PowerContext 时，731 个任务中解决 634 个
        kind: "on"
    delta: "+32"
    delta_label: 个任务被额外解决
    delta_accessible: 开启 PowerContext 后多解决 32 个任务
    caption: ON 运行解决 634 个任务，OFF 运行解决 602 个，相差 32 个任务，即 4.38 个百分点。
    scope_title: 结果范围
    scope: 这是一组固定任务集上的配对运行，不是 SWE-bench Pro 官方提交。Agent 运行存在随机性，因此分数只描述这两次运行。
  leaderboards:
    title: 与公开结果对照
    lead: LoCoMo 结果采用的 Reader、Judge 与答案匹配规则并不统一。SWE-bench Pro 数据来自官方 Public 榜单，因此两组榜单需要按各自口径解读。
    tabs_label: 选择评测榜单
    updated: 数据核验于 2026 年 8 月 31 日
    source_label: 查看来源
    locomo:
      id: locomo-rankings
      tab: LoCoMo
      count: 15 个系统
      title: 已报告的 LoCoMo 分数
      lead: 所选系统均报告了全部 1,540 个问题的分数，但 Reader、Judge 与答案匹配方式仍有差异。
      table_label: 15 个完成 1,540 题评测的 LoCoMo 公开分数索引
      columns:
        rank: 名次
        system: 系统
        score: 分数
        protocol: 公开评测口径
        evidence: 证据类型
      note_title: 对比限制
      note: 每项引用结果都报告了全部 1,540 道计分题，但 Reader、Judge 与答案判定策略并未统一，因此这不是 LoCoMo 官方排行榜。
      rows:
        - rank: 1
          name: Zep
          score: 94.70%
          protocol: 1,540 题；GPT-5.4 Reader 和 Judge
          evidence: 供应商实测
          source: https://www.getzep.com/research/
        - rank: 2
          name: EverMemOS
          score: 94.50%
          protocol: 1,540 题；宽松共享工具；预计算检索
          evidence: 第三方实测
          source: https://github.com/buildingjoshbetter/TrueMemory/blob/main/benchmarks/locomo/BENCHMARK_RESULTS.md
        - rank: 3
          name: XMDB
          score: 93.20%
          protocol: 1,540 题；内部验证
          evidence: 供应商实测
          source: https://xmdb.ai/memory
        - rank: 4
          name: TrueMemory Pro
          score: 93.00%
          protocol: 1,540 题；三次均值；宽松 Judge
          evidence: 开放评测工具
          source: https://github.com/buildingjoshbetter/TrueMemory/blob/main/benchmarks/locomo/BENCHMARK_RESULTS.md
        - rank: 5
          name: Mem0
          score: 92.50%
          protocol: 1,540 题；最新供应商评测装置
          evidence: 供应商实测
          source: https://mem0.ai/research
        - rank: 6
          name: PowerContext
          score: 90.78%
          protocol: 1,540 题；答对 1,398 题；topical Judge
          evidence: 项目实测
          source: https://github.com/oceanbase/powercontext#benchmarks
          highlight: true
        - rank: 7
          name: Honcho
          score: 89.90%
          protocol: 1,540 题；当前完整系统结果
          evidence: 供应商实测
          source: https://honcho.dev/blog/blog/benchmarking-honcho
        - rank: 8
          name: Dakera
          score: 88.20%
          protocol: 1,540 题；单次检索；没有 LLM 重排
          evidence: 可复现供应商实测
          source: https://dakera.ai/benchmark/
        - rank: 9
          name: PowerMem
          score: 87.79%
          protocol: 1,540 题；历史项目实测
          evidence: 项目实测
          source: https://github.com/oceanbase/powercontext#benchmarks
        - rank: 10
          name: Memvid
          score: 85.65%
          protocol: 1,540 题；GPT-4o Reader；宽松 Judge
          evidence: 开放评测工具
          source: https://github.com/memvid/memvidbench
        - rank: 11
          name: Genesys
          score: 85.55%
          protocol: 1,540 题；十次均值；固定 Mem0 协议
          evidence: 认证供应商实测
          source: https://genesys.astrixlabs.ai/developers/methodology
        - rank: 12
          name: Engram
          score: 84.50%
          protocol: 1,540 题；共享宽松评测工具
          evidence: 第三方实测
          source: https://github.com/buildingjoshbetter/TrueMemory/blob/main/benchmarks/locomo/BENCHMARK_RESULTS.md
        - rank: 13
          name: MemHQ
          score: 83.20%
          protocol: 1,540 题；gpt-4o-mini；部分正确计入
          evidence: 开放评测工具
          source: https://memhq.ai/benchmark
        - rank: 14
          name: Logica Mind
          score: 72.50%
          protocol: 1,540 题；Mem0 论文协议
          evidence: 开放评测工具
          source: https://huggingface.co/datasets/rovemark/locomo-benchmark-results
        - rank: 15
          name: Supermemory
          score: 65.40%
          protocol: 1,540 题；共享宽松评测工具
          evidence: 第三方实测
          source: https://github.com/buildingjoshbetter/TrueMemory/blob/main/benchmarks/locomo/BENCHMARK_RESULTS.md
    swe:
      id: swe-rankings
      tab: SWE-bench Pro
      count: 25 个官方条目
      title: SWE-bench Pro Public 官方榜
      lead: 图中展示五个官方条目及其置信区间。PowerContext 使用不同的运行协议，因此单独列出。
      table_label: 包含 25 个条目的 SWE-bench Pro 官方 Public 排行榜
      source: https://labs.scale.com/leaderboard/swe_bench_pro_public
      note_title: PowerContext 不是官方条目
      note: Scale 使用置信区间对正式提交进行排名。PowerContext 在相同的 731 个 Public 任务上运行了另一组 Codex 配对测试。86.73% 不是官方提交，因此这里不为它分配名次。
      spotlight:
        label: PowerContext 配对实测
        value: 86.73%
        detail: PowerContext ON 解决 634 / 731 题
        status: 不是官方排行榜条目
      columns:
        rank: 排名上界（Rank UB）
        system: 模型
        score: 解决率
        provider: 提供方
        harness: 评测工具
      rank_note: 排名上界根据置信区间计算；区间重叠时会出现并列和跳号。
      harness_default: Scale 实测
      harness_star: mini-swe-agent
      rows:
        - rank: 1
          name: Muse Spark 1.1
          provider: Meta
          score: 61.50%
          ci: ±3.10
          star: true
        - rank: 1
          name: gpt-5.4 (xHigh)
          provider: OpenAI
          score: 59.10%
          ci: ±3.56
          star: true
        - rank: 3
          name: Muse Spark
          provider: Meta
          score: 55.00%
          ci: ±3.60
          star: true
        - rank: 3
          name: claude-opus-4-6 (thinking)
          provider: Anthropic
          score: 51.90%
          ci: ±3.61
          star: true
        - rank: 5
          name: gemini-3.1-pro (thinking)
          provider: Google
          score: 46.10%
          ci: ±3.60
          star: true
        - rank: 5
          name: claude-opus-4-5-20251101
          provider: Anthropic
          score: 45.89%
          ci: ±3.60
        - rank: 5
          name: claude-4-5-Sonnet
          provider: Anthropic
          score: 43.60%
          ci: ±3.60
        - rank: 5
          name: gemini-3-pro-preview
          provider: Google
          score: 43.30%
          ci: ±3.60
        - rank: 5
          name: claude-4-Sonnet
          provider: Anthropic
          score: 42.70%
          ci: ±3.59
        - rank: 10
          name: gpt-5-2025-08-07 (High)
          provider: OpenAI
          score: 41.78%
          ci: ±3.49
        - rank: 10
          name: gpt-5.2-codex
          provider: OpenAI
          score: 41.04%
          ci: ±3.57
        - rank: 10
          name: claude-4-5-haiku
          provider: Anthropic
          score: 39.45%
          ci: ±3.55
        - rank: 10
          name: qwen3-coder-480b-a35b
          provider: Alibaba
          score: 38.70%
          ci: ±3.55
        - rank: 14
          name: minimax-2.1
          provider: MiniMax
          score: 36.81%
          ci: ±3.55
        - rank: 14
          name: gemini-3-flash
          provider: Google
          score: 34.63%
          ci: ±3.55
        - rank: 16
          name: gpt-5.2
          provider: OpenAI
          score: 29.94%
          ci: ±2.15
        - rank: 16
          name: kimi-k2-instruct
          provider: Moonshot
          score: 27.67%
          ci: ±3.25
        - rank: 18
          name: qwen3-235b-a22b
          provider: Alibaba
          score: 21.41%
          ci: ±2.25
        - rank: 19
          name: gpt-oss-120b
          provider: OpenAI
          score: 16.20%
          ci: ±2.67
        - rank: 19
          name: deepseek-v3p2
          provider: DeepSeek
          score: 15.56%
          ci: ±2.63
        - rank: 21
          name: gemma-3-27b-it
          provider: Google
          score: 11.38%
          ci: ±2.15
        - rank: 21
          name: llama3-1-405b-instruct
          provider: Meta
          score: 11.18%
          ci: ±2.15
        - rank: 21
          name: glm-4.6
          provider: Z.ai
          score: 9.67%
          ci: ±2.15
        - rank: 24
          name: llama4-maverick-17b-instruct
          provider: Meta
          score: 5.24%
          ci: ±1.24
        - rank: 25
          name: codestral-2405
          provider: Mistral
          score: 1.51%
          ci: ±1.51
  reading:
    title: 先看清每项结果测什么。
    lead: 两项评测都与上下文有关，但输入、输出与评分方式不同，不能把两个分数直接横向比较。
    scroll_hint: 左右滑动，比较两项评测。
    table_label: LoCoMo 与 SWE-bench Pro 评测对比
    columns:
      dimension: 评测维度
    rows:
      - dimension: 评测内容
        locomo: 长程对话记忆与推理
        swe: 仓库级 Issue 修复
      - dimension: 输入
        locomo: 多 Session 对话历史与一个问题
        swe: 代码仓库、Issue 与干净任务环境
      - dimension: 输出
        locomo: 有对话依据的自然语言答案
        swe: 代码补丁
      - dimension: 主要评分
        locomo: Judge 判定的答案准确率
        swe: 正式可执行测试是否通过
  sources:
    title: 评测方法与来源
    lead: 两项评测的方法依据与复现资源。
    groups:
      - id: locomo
        title: LoCoMo
        items:
          - type: 论文与任务定义
            label: Evaluating Very Long-Term Conversational Memory of LLM Agents
            href: https://aclanthology.org/2024.acl-long.747/
          - type: 数据集
            label: snap-research/locomo
            href: https://github.com/snap-research/locomo
      - id: swe
        title: SWE-bench Pro
        items:
          - type: 任务集与评测规范
            label: scaleapi/SWE-bench_Pro-os
            href: https://github.com/scaleapi/SWE-bench_Pro-os
          - type: 本次运行配置
            label: PowerContext evaluation console
            href: https://github.com/oceanbase/powercontext/tree/master/evaluation
  cta:
    title: 把 PowerContext 接入工作流
    lead: 可以从受支持的 Agent 开始，也可以通过 HTTP API 接入自己的应用。仓库包含运行时、集成和评测工具。
    label: 打开 GitHub 仓库
    href: https://github.com/oceanbase/powercontext
---
