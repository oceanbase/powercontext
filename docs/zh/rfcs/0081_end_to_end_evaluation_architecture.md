- Proposal Name: `end_to_end_evaluation_architecture`
- Start Date: 2026-08-07
- RFC PR: [oceanbase/powercontext#81](https://github.com/oceanbase/powercontext/pull/81)
- Related RFCs: [RFC 0014](0014_memory_layer_design.md)、
  [RFC 0016](0016_pydantic_ai_inference_integration.md)、
  [RFC 0046](0046_observability_foundations.md) 和
  [RFC 0080](0080_memory_search_reranking.md)

# 摘要

PowerContext 需要一套从公开边界判断产品是否正常的 E2E。它提供三类证据：

| 范围 | 证明什么 | Oracle |
| --- | --- | --- |
| 一般 E2E | PowerContext 组件可以组成完整业务流 | 确定性的公开行为 |
| Scenario replay | 真实 agent 可以跨独立 session 捕获和召回 context | Behavior、regression 和 replay expectation |
| Sampled scenario | 同一 harness 可以处理固定的 conversation 与 repository case | 已物化预期或 source-native result |

首个 replay harness 使用 Bub，同时运行 SQLite 与 OceanBase，调用真实模型，并记录足以解释失败或离线重新评分的证据。
Bub 是实现选择，不是本 RFC 的主题。

# 动机

聚焦测试有用，但不能证明 CLI、Client、Server、数据库、agent integration 和模型可以一起工作。Benchmark score 可以
显示质量变化，却不说明链路中的哪一部分失败。E2E 位于两者之间：运行完整路径，并把输入、中间状态和结果放在一起。

测试必须值得维护。一个从头读到尾就能判断正确、以后很可能只会重写或增加参数的短脚本，可以不写测试。检查私有 buffer
长度、调用顺序、模块布局，或者穷举被统一处理的内部错误，只会增加实现变化时必须同步修改的噪音，并没有保护用户。

只有两类 case 适合放进这套体系：

- Behavior test 保护用户能观察到的接口或体验。内部实现重写后，它仍然应该成立。
- Regression test 记录一个可能再次发生的缺陷。它复现外部错误结果，而不是固定当时出错的实现细节。

# Guide-level explanation

## Harness-driven development

Harness-driven development 把可执行 scenario 作为变更的 acceptance boundary。Developer 或 development agent 负责生成
待评估 commit，另一个 evaluation agent 只通过用户可用的接口验证该 commit：

```text
development agent -> commit under test

scenario -> harness -> evaluation agent -> system under evaluation
                |                              |
                +<------ recorded evidence <---+
                             |
                             v
                       oracle and report
```

Evaluation agent 使用独立的 session、workspace 和 PowerContext scope。它只能看到 scenario 与允许的 repository state，
不能读取开发对话或未写明的实现理由。它可以与 development agent 使用相同模型。这里的独立性来自 context 与 state
隔离，不要求更换 model vendor。

这种分离不会把验收过程写进 development agent 的 history 或 working state，也不会打断开发循环。Harness 可以在本地或
CI 中独立运行。Evaluation agent 必须从已提交行为中发现并使用功能，不能依赖开发时获得的知识，因此能发现缺失说明、
隐藏 setup 和对 dirty workspace 的意外依赖。它提供接近第三方使用者的视角，但不替代人工评审或外部审计。

工作循环很短：声明可观察行为，实现变更，让 evaluation agent replay scenario，检查 evidence，再把结果用于下一次变更。
Scenario 保持稳定，内部实现可以被替换。

Harness-driven development 不要求每个变更都新增 case：

| 变更 | 评估动作 |
| --- | --- |
| 现有行为只改变内部实现 | 运行已有相关 case |
| 新增用户可见契约 | 新增或扩展 behavior case |
| 修复可能复发的缺陷 | 新增 regression case |
| 只修改私有实现 | 除非风险跨越公开边界，否则不增加 E2E case |

## Evaluation target 与 evidence mode

Evaluation target 说明本次运行要验证什么：PowerContext flow、agent journey 或 sampled workload。Evidence mode 说明如何
获得判断依据：

| Evidence mode | 执行方式 | 用途 |
| --- | --- | --- |
| Deterministic acceptance | 公开接口与确定性 capability | 每个 commit 的 behavior 与 regression 检查 |
| Live replay | 独立 evaluation agent 与真实 provider | 完整 agent 与 model 路径 |
| Offline rescore | 不重新运行系统，只读取 recorded replay | Evaluator 变更与结果比较 |

每个结果都要标识两者。Deterministic run 不能证明 provider 正常，offline rescore 不能证明当前 commit 仍可执行。Live
replay 只对已记录的 model、budget、database 和 scenario 提供证据，不能外推为普遍正确。

# Reference-level explanation

## 架构边界

Scenario 定义目标、有序 input、可观察 expectation 和 budget。Harness 负责环境、agent lifecycle、隔离与 evidence capture。
Evaluation agent 操作产品。System under evaluation 包含待测 PowerContext commit，以及 integration、model configuration、
workspace 和 database。Oracle 解释 recorded evidence 并生成 report。

Development agent 不属于被评估的 execution path。Harness 不从开发日志推测意图，evaluator 也不通过检查私有实现细节
判断 scenario 是否通过。

## 一般 E2E

一般 E2E 位于 `tests/e2e/`，通过 `make e2e-test` 运行。它从受支持的 CLI、Client、HTTP、MCP 或 Runtime 边界进入，
再通过受支持边界观察结果。Case 覆盖 Source capture、Memory processing 与 recall、Handoff、reviewed Artifact、
authentication、statistics、restart 和 observability 等完整流程。

这些测试是确定性的。如果 case 不评估 provider，可以注入确定性的 generation 或 embedding capability。这只能证明
产品组合与状态语义，不能证明真实 provider compatibility。

## Scenario replay

Live replay 通过 agent harness 调用真实 provider：

```text
scenario input
  -> agent harness
  -> PowerContext integration
  -> PowerContext Server
  -> Memory and prepared context
  -> agent output
  -> evaluation report
```

一个 replay 使用一个 PowerContext scope。每一步使用新的 agent session，不能读取先前的 agent history。状态只能通过
PowerContext 跨越 session 边界。

Harness 在每次 agent run 前记录 prepared context，运行后记录 final output、agent span 和公开 Memory state。某一步失败
后停止剩余序列，并保留可报告的部分结果。

## 样本集成

Sampling 是 authoring step。它把选中的 source case 转为已提交的 fixture 或 manifest。CI 评估 commit 时不会重新随机
抽样。

每个 sample set 记录：

- source revision 或 fingerprint；
- 稳定 case ID 和版本化 selection policy；
- agent-visible input 与 evaluator-only reference data；
- 结果使用的 model、harness、PowerContext commit、execution budget 和 attempt policy。

LoCoMo conversation sample 与 SWE-bench Pro repository sample 遵守相同规则。Conversation sample 包含声明的时序输入，
不能只包含 gold evidence 指定的 session。Repository case 的每次 attempt 都使用新的 workspace 与 PowerContext scope。
Gold answer、evidence annotation、hidden test、reference patch 和 grading result 不能进入 agent 或 PowerContext input。

Source-native result 存在时保持权威。Memory、prepared context、span、latency 和 usage 用于解释运行，不能替代该结果。
已提交的小型 sample set 用于快速反馈，不能据此外推完整 source distribution。

## Scenario fixture

首个 replay contract 使用严格 YAML：

```yaml
schema: powercontext.session-replay/v1
id: project-database-decision
sessions:
  - id: capture
    input: >-
      Store this durable project decision in PowerContext: the project selected OceanBase because it needs
      MySQL-compatible, multi-node persistent storage for shared agent context.
    expected_memory:
      - OceanBase
      - MySQL-compatible
      - multi-node persistent storage
  - id: recall
    input: What database did this project select, and why?
    expected_context:
      - OceanBase
      - MySQL-compatible
      - multi-node persistent storage
    expected_answer: >-
      The project selected OceanBase because it needs MySQL-compatible, multi-node persistent storage for
      shared agent context.
```

Expectation 描述通过公开边界可见的含义。它不保存 prompt、SQL、数据库 ID、完整模型文本、私有 trace shape 或 tool
order 的 snapshot。

Sample-derived fixture 还可以包含 source identity、source revision、selection policy 和稳定 case ID。这些字段组成一个
完整 provenance block。Source revision 或 ID 不匹配时，加载失败。

## 证据

每次 live run 生成三种 artifact：

| Artifact | 内容 |
| --- | --- |
| `replay.json` | Scenario、非敏感 run identity、output、prepared context、Memory snapshot 和 agent span tree |
| `eval-report.json` | Assertion、label、score、metric 和 reason |
| `report.md` | 供 reviewer 或 CI summary 阅读的短报告 |

`replay.json` 是自包含的。离线评分不需要再关联独立的 input、trace、Memory 和 output 文件。它区分 setup 或 execution
failure 与已经完成但质量较低的结果。

Bundle 包含用户可见文本，因此比普通 telemetry 更敏感。Credential、authorization header、database URL 和 provider
secret 不得写入 artifact。Live run 只允许可信事件，并使用有界 retention。

## Trace 与模型配置

Evaluator 持有 OpenTelemetry tracer provider。Harness 通过该 provider 发出 agent 与 model span，Pydantic Evals 直接
评估原生 span tree。Harness 不增加 OTLP receiver、protobuf decoder、通用 attribute converter 或第二套 span model。

每次运行分别记录以下模型角色：

- agent model；
- PowerContext generation；
- PowerContext embedding；
- evaluation judge。

首个 Bub harness 从 `BUB_MODEL`、`BUB_API_KEY`、可选的 `BUB_API_BASE` 和有界的 `BUB_CLIENT_ARGS` 读取 agent model。
只有映射显式且无损时，才能把该配置传给 PowerContext generation 与 judge。显式 PowerContext 配置优先。Embedding
始终使用独立 profile。

## 评估

Pydantic Evals 接收完整 replay observation。以下情况阻断 acceptance：

- setup 或 agent execution 没有完成；
- 声明的 session 没有运行；
- 某一步之后缺少预期 Memory；
- 依赖步骤运行前缺少预期 prepared context。

真实模型输出可能波动，因此 answer quality 默认只用于诊断。某个可信配置表现出足够稳定性后，可以把它设为阻断条件。
Duration、token usage、span count 和 Memory addition 是 metric。只有 scenario 声明外部 budget 时，它们才是 assertion。

## 数据库矩阵

Behavior 与 replay scenario 同时针对 SQLite 和 OceanBase 运行。两者使用相同输入和预期。矩阵检查公开行为，不要求相同
latency、SQL 或物理执行计划。

# CI

Pull request 和 main branch commit 针对两个数据库运行一般 E2E 与确定性 scenario acceptance。Live replay 只在具备
provider credential 的可信事件中运行。已提交的 sampled set 按声明的可信或定时 cadence 运行。

矩阵不 fail fast。一个数据库失败时，另一个数据库仍上传自己的证据。长期报告只比较 sample set、model configuration、
execution budget 和 attempt policy 相同的运行。

# 非目标

本 RFC 不定义通用 evaluation platform、dataset registry、agent protocol 或 harness plugin system，也不替换
source-native grading。它不会为 coverage 测试私有实现细节。

首个实现贴近 Bub 与 PowerContext。只有第二个可工作的 harness 出现并完成单独设计评审后，才提取共享抽象。

# Acceptance criteria

设计完成需要满足：

- 一般 E2E 覆盖完整的公开 PowerContext 业务流；
- 独立 agent session 只通过 PowerContext 共享持久状态；
- SQLite 与 OceanBase 运行相同的 behavior 与 replay scenario；
- live replay 使用真实 provider，并记录 model identity；
- 一个 replay bundle 包含 input、output、Memory、prepared context 和 agent span；
- Pydantic Evals 可以在线或离线评估 bundle，不需要自定义 OTLP receiver；
- sampled input 固定、可评审、相互隔离，并且没有 reference leakage；
- CI 分开报告 infrastructure failure、阻断性 acceptance 和诊断性 quality；
- 每个测试都保护可观察行为或具体 regression。

# 未决问题

- 哪些 model 与 embedding profile 足够稳定且经济，可以用于可信 CI？
- 哪些 case 与 budget 组成首个提交的 sample set？
- 长期趋势报告应保留哪些非敏感字段？
