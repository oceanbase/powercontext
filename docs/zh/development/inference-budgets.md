---
title: 推理预算与准入
description: 完整模型请求的发送前检查、执行配额、选材规则与适用边界。
---

# 推理预算与准入

推理预算分别约束单次请求容量与一次操作的执行额度。前者检查输入、输出预留和上下文；后者限制调用次数、时间，
并沿用 Topic Memory 和 Dream 已有的累计工作账本。它们必须同时满足。

## 完整请求检查

内置 OpenAI Chat、Responses 和 Anthropic SDK 的 HTTP request hook 在请求序列化完成后、进入网络 transport 前检查
实际 JSON body。该检查独立于隐私保护模式，`protection.mode=disabled` 不会关闭预算检查。

计量包括实际指令、provider 渲染的结构化输出说明、工具与 Schema、证据、历史回答和重试反馈。不计量未发送的本地
输入 Schema；模型名、输出长度和采样控制等不是输入正文。扩展请求字段保守计量；无法看到完整内容的远程历史引用、
图片、音频和其他不透明内容会被拒绝，不能用一个文本估算声称已覆盖它们。

模型参数快照只用于选材预估。FunctionModel、TestModel 等进程内测试模型没有 HTTP 协议，使用其准备后的参数验证
本地行为；这些测试不能代替 SDK 装配测试。自定义 Model 必须提供可观察的完整请求边界，否则受控生成拒绝启动。

对输入输出共用窗口的部署：

```text
允许完整输入 = min(适用输入上限, 上下文窗口 - 本次输出预留 - 安全余量)
```

`separate_limits` 分别检查输入、输出，并在声明联合窗口时额外检查输入与输出总量。估算器固定为可执行的身份和版本；
估算值与 provider usage 分开。字符估算加安全余量不能保证与任意模型 tokenizer 完全一致。

## 配置和输出

`generation_model_context_window_tokens` 默认 125000，是配置默认值，不是对任意模型或 gateway alias 真实容量的保证。
部署方应明确声明实际容量；不会根据自定义别名推断窗口。

`generation_request_budget` 定义部署输入、输出、安全余量和估算器；`generation_operation_budgets` 为注册操作收紧输入、
输出、次数和时间。未知 operation、非法计数、突破明确部署上限的 allocation 会被拒绝。现有 `max_tokens` 与明确部署
输出上限同时配置时须符合配置校验规则。

实际发送的输出上限同时受 Family/stage、operation、部署硬上限和显式输出预留约束。未显式声明预留时，使用本次实际
输出上限解析窗口；仅在没有操作专有输出策略和请求设置时使用普通生成默认值。Topic 的派生输出上限不会被普通生成
默认 4096 隐式覆盖。最终 HTTP body 再核对输出字段，`extra_body` 不能提高已获准的上限。

普通生成按 `generation_limits(operation)` 执行生效次数和超时。独立 decision/rerank endpoint 采用各自配置；未声明独立
容量时不借用生成模型的窗口，这类通路不宣称已有容量保护。默认 endpoint 和显式容量均需按实际部署核实。

## 各入口的规则

| Family / 入口 | 请求容量与选材 | 进度和累计额度 |
| --- | --- | --- |
| Atomic extraction / reconciliation | 保留连续窗口、比较批次；选材同时检查旧业务上限和新增 request allowance；最终 SDK 请求另行检查 | 只有尚未调用模型的可缩减输入拒绝接入既有窗口缩减；单条或最小比较输入不能放下时明确拒绝；不推进未处理 cursor |
| Profile 普通生成 | 保留已有 Profile，按 Source 连续前缀缩小 | 所有本地选材共用一个截止时间；已发生调用或重试上下文超限后不重开生成；成功只提交已处理前缀 |
| Experience 自动 incubation | 保留完整 Task Outcome；超长单条不再切片；完整请求检查 | 失败不发布不完整案例，不推进未处理输入 |
| Experience / Skill 显式生成 | 调用者选择的证据必需；单条字符边界及完整请求均检查 | 放不下就拒绝，不删证据凑预算，不创建降级 Candidate |
| Handoff | objective、rollover reasons、必要证据和输出说明均计量 | 不为通过准入省略必要交接信息 |
| Topic 七个 stage | 保留 probe、planner、temporary、reduce 等策略；各阶段绑定自身 operation，最终检查重试历史 | 沿用同一权威 work budget、预留、fence、cursor CAS 与原子发布；不因重试创建新账本或重置额度 |
| Prompt demonstrations | 动态生成的输出 Schema 纳入最终请求 | operation 次数与时间生效，不私自减少示例数 |
| Dream 各 operation | 按实际输入和部署容量检查，输出上限受 DreamBudget 与部署共同约束 | 保留 Run 次数、deadline、attempt 与审核语义；不消费普通 Family 的 Source cursor |

Topic 初始选材上限与包含重试历史的完整请求上限不是同一个计量范围。现有阶段预留仍涵盖整次 stage 的结构化重试；
最终检查消费该预留授权，不对每个请求重复预留整份额度。已耗尽工作预算不能被新准入层重置。

失败之后的恢复策略属于 #2128，用户可见阻塞状态属于 #2130。这里提供准确的发送前拒绝，不实现新的通用恢复状态机。

## Embedding 独立限制

两个 embedding adapter 分别检查单条 token、每批 token、每批条数。未声明的维度不设限；generation 的窗口不保护
embedding。保留 `embedding_batch_size` 的连续分批，首次发送前验证所有计划批次；超限批次明确拒绝，不自动拆开单条
文本，也不改变向量数量、顺序或 profile。这不是 token 自适应分批算法。

运行中发生类型化推理错误时，保留此前已完成批次的已知 usage，供现有 reporter 上报；usage 缺失保持未知。

## 拒绝和观测

本地拒绝抛 `InferenceBudgetExceededError`，HTTP 返回 422 / `inference_budget_exceeded`，MCP 与 Python Client 保留
相同领域事实。`details` 包含 operation、stage、dimension、估算值、上限、单位、预留、margin、估算器和 phase，
不包含证据、Prompt、凭据或 provider 原始异常。SDK 包装本地错误后仍恢复该类型，不把它当作临时连接故障重试。

HTTP/MCP 的 `inference.budget.rejected` 日志通过 allowlist 输出到 JSON、console 和 Server-owned Worker 通道。
`unit=inference`，计量单位使用 `budget_unit`。后台失败码保留 `inference_budget_exceeded`；日志不是持久预算账本。

## 生效值矩阵

下表由 `scripts/inference_budget_matrix.py` 从默认配置和现有 Family 策略生成。完整请求 ceiling 与选材 ceiling 的计量
范围不同，不能把部署窗口当作所有 Family 的可用材料额度。实际 model settings、显式 allocation 和绑定 Prompt 可能
继续收紧本次执行；表中的次数和超时使用与 Runtime 相同的解析入口。

<!-- budget-matrix:begin -->
| operation | binding dimension | complete input ceiling (tokens) | selection ceiling (tokens) | output reserve (tokens) | safety margin (tokens) | max requests | timeout (s) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `atomic_memory.extract` | `context_tokens` | 114654 | 24000 | 4096 | 6250 | 2 | 30 |
| `atomic_memory.reconcile` | `context_tokens` | 114654 | 24000 | 4096 | 6250 | 2 | 30 |
| `experience.generate` | `context_tokens` | 114654 | not applicable | 4096 | 6250 | 2 | 30 |
| `experience.incubate` | `context_tokens` | 114654 | not applicable | 4096 | 6250 | 2 | 30 |
| `handoff.generate` | `context_tokens` | 114654 | not applicable | 4096 | 6250 | 2 | 30 |
| `memory.rerank` | `context_tokens` | 114654 | not applicable | 4096 | 6250 | 2 | 30 |
| `memory.write-gate` | `context_tokens` | 114654 | not applicable | 4096 | 6250 | 2 | 30 |
| `profile.generate` | `context_tokens` | 114654 | not applicable | 4096 | 6250 | 2 | 30 |
| `prompt_demonstrations` | `context_tokens` | 114654 | not applicable | 4096 | 6250 | 2 | 30 |
| `skill.generate` | `context_tokens` | 114654 | not applicable | 4096 | 6250 | 2 | 30 |
| `topic_memory.evolve` | `context_tokens` | 110460 | 100000 | 8290 | 6250 | 2 | 30 |
| `topic_memory.global` | `context_tokens` | 110460 | 100000 | 8290 | 6250 | 2 | 30 |
| `topic_memory.planner` | `context_tokens` | 110460 | 100000 | 8290 | 6250 | 2 | 30 |
| `topic_memory.probe` | `context_tokens` | 110460 | 100000 | 8290 | 6250 | 2 | 30 |
| `topic_memory.reconcile` | `context_tokens` | 110460 | 100000 | 8290 | 6250 | 2 | 30 |
| `topic_memory.reduce` | `context_tokens` | 110460 | 100000 | 8290 | 6250 | 2 | 30 |
| `topic_memory.temporary` | `context_tokens` | 110460 | 100000 | 8290 | 6250 | 2 | 30 |

Embedding (independent limits; unset means unbounded):

- items per batch: not set
- input tokens per text: not set
- input tokens per batch: not set
- estimator: `character:weighted@1`

Dream (charged per Run against `DreamBudget`, admitted against the deployment window only):

- allowed input (tokens): 114654
- context limit (tokens): 114654
- output reserve (tokens): 4096
- safety margin (tokens): 6250
- max output (tokens): 4096
- max model calls per Run: 2
- timeout per Run (s): 120
<!-- budget-matrix:end -->

## 验证范围

本地 SDK 测试验证真实请求装配与零 outbound；测试 provider 不代表真实模型服务的 tokenizer 或返回行为。
SQLite、真实 spawned Worker、累计额度和日志输出各有行为测试。真实 OceanBase 和生产模型验收需独立执行，不能由
本地替身或 SQLite 结果代替。覆盖声明应与实际执行记录保持一致，不根据文档表格推断已完成外部验收。
