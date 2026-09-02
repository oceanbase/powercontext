- Proposal Name: `unified_workloads_and_long_horizon_memory_evaluation`
- Start Date: 2026-08-13
- RFC PR: [oceanbase/powercontext#1229](https://github.com/oceanbase/powercontext/pull/1229)
- Related RFC: [RFC 0081：端到端评估架构](0081_end_to_end_evaluation_architecture.md)

# Summary

PowerContext 将内置端到端样例和长程任务表示为 workload。每个 workload 选择固定的 task、指定 execution adapter、设置预算，
并声明如何评估本次运行产生的 Memory。

所有 workload 共用 catalog、replay envelope、Memory evaluator、report 格式和 `acceptance` 命令。当前实现使用
Bub adapter，因为 Bub 的 model call、tool、context injection、capture 与 checkpoint 都可以观察。以后可以增加其他 adapter，
而不改变这些公共 contract。

任务原生 reward 只用于诊断，不决定 PowerContext 是否采集到有依据、可召回的 Memory。

# Motivation

RFC 0081 定义了更广泛的端到端评估架构，但本地确定性样例、使用 model 的 agent 运行和长程任务仍然使用不同的命令与
artifact 路径。这种分离会重复实现 selection、execution setup、evidence handling 与 reporting。

统一的 workload contract 应稳定回答以下问题：

- 运行了哪个固定 task 与 revision？
- 哪个 execution adapter 与运行时配置驱动了任务？
- execution 是否从隔离且为空的 PowerContext scope 开始？
- execution 期间采集了哪些 evidence？
- 本次运行是否创建了有依据的 Memory，并在之后成功召回？
- 是否可以在不重新运行任务的情况下使用相同 evidence 重新评分？

Bub 适合作为首个 adapter，因为确定性 tool flow 和使用 model 的 agent flow 都可以经过真实的 ACP、command、tool、hook 与
plugin 边界。确定性执行不需要伪装成 model run，长程执行则可以暴露 model loop 与 capture policy。

## 范围

本 RFC 包括：

- 一套 Pydantic workload manifest 与 catalog；
- 按 workload ID 或 category 选择；
- 每个 workload 使用隔离的 PowerContext scope；
- 通过 Harbor 运行仓库内与 registry-backed 的 Bub task；
- 标准化 replay evidence、Memory evaluation 与 report rendering；
- 在 SQLite 与 OceanBase 上运行确定性 acceptance；
- 显式选择使用 model 的 workload 与 long-horizon workload；
- 根据固定的 evidence contract 离线重新评分。

完整 LoCoMo benchmark 与独立的 SWE-Pro evaluation 不进入该 catalog。本 RFC 不迁移它们，也不改变其原生输入、评分、结果
或运行命令。

# Guide-level explanation

## Workload manifest

Workload manifest 同时是 catalog entry 与执行契约。Manifest 和运行时配置都在 execution 前经过 Pydantic 校验。

```yaml
schema: powercontext.e2e-task/v1
id: project-database-decision
categories:
  - acceptance
  - sample
dataset:
  path: e2e/bub/harbor-tasks
  task_id: project-database-decision
  checksum: <harbor-task-checksum>
execution:
  type: bub
  model: false
  max_steps: 10
  max_tokens: 4096
evaluation:
  expected_memory:
    - OceanBase
  probes:
    - id: database-decision
      query: Which project decision selected multi-node persistent storage?
      expected_context:
        - OceanBase
      forbidden_context:
        - SQLite
```

`dataset` 可以指向仓库自行维护的 Harbor task，也可以指向带版本的 registry task。两者使用相同的 execution 与 evidence
路径。

Recall probe 的匹配不区分大小写，并进行 Unicode 归一化。包含 `expected_context` 的正向 probe 参与
`probe_coverage` 计算，并要求 prepared context 包含全部 expected fragment。仅包含 `forbidden_context` 的纯负向
probe 表达 abstention，不参与 coverage；没有正向 probe 时，`probe_coverage` 为 `1`。每个 probe 都会拒绝包含
forbidden fragment 的 prepared context；任何 forbidden match 都会直接令 acceptance 失败，不受
`probe_coverage` 阈值影响。

`execution.type` 选择 adapter，当前 contract 实现 `bub`。`execution.model` 只声明 workload 是否需要 model：

- `false` 保持 Bub execution 确定性，不向 agent environment 传入 model；
- `true` 要求运行时在 Harbor Job 启动前解析出 model。

Model identity、provider、endpoint 与 authentication 都属于运行时配置，不应进入可移植的 workload manifest。使用 model
时，replay evidence 会记录最终解析出的 model identity，但不会记录 credential。
Harness 不复制这些 setting：Client 消费 `POWERCONTEXT_CLIENT_*`，Bub adapter 在需要 model 时原样转发 `BUB_*`，integration
消费 `POWERCONTEXT_BUB_*`。Harbor 直接接收 `AgentConfig`，不需要 model provider key。

Adapter package version 与 timeout 遵循相同的所有权规则。Adapter runtime 固定 Bub 与 ACP server 版本，并把解析后的版本写入
replay evidence。Harbor task definition 拥有 agent 与 environment timeout；Bub 拥有 `BUB_MODEL_TIMEOUT_SECONDS`。Workload manifest
不覆盖这两个 timeout domain。

## Selection 与命令入口

Workload 使用稳定 ID，category 只是选择元数据。`acceptance` 命令可以选择一个或多个 ID、category，默认选择
`acceptance` category：

```bash
powercontext-e2e acceptance --output e2e/bub/results

powercontext-e2e acceptance \
  --id locomo-support-group \
  --id project-database-decision \
  --output e2e/bub/results

powercontext-e2e acceptance \
  --category long-horizon \
  --output e2e/bub/results
```

两类 selector 都只使用可重复的 command option；contract 不提供 environment alias 或逗号分隔语法。

Long-horizon 与 live workload 仍然是 acceptance evaluation。Category 控制选择，不需要引入新的 execution mode 或命令。

SQLite 与 OceanBase 是运行时 database variant，不是 execution adapter，也不是 workload category。Required CI 在两个数据库上
运行相同的确定性 `acceptance` workload。

## 当前 Bub adapter

当前 adapter 通过 Harbor Job 与 Harbor ACP runner 进入每个 workload。Harbor 管理 task environment 与 agent lifecycle，
Bub 通过受支持的安装路径运行，并加载 PowerContext integration。

确定性 workload 使用 `model: false`，执行 `powercontext.remember` 与 `powercontext.context` 等 Bub command。它们在不调用
model 的情况下验证 Harbor-to-ACP-to-Bub tool path。使用 model 的 workload 设置 `model: true`，并额外覆盖 Bub model、
context injection、trajectory capture 与 checkpoint hook。

两种形式生成相同的 replay envelope，并使用相同的 Memory evaluator。确定性 workload 仍然属于 Bub adapter，因为它经过
Bub adapter；确定性描述是否使用 model，而不是 adapter identity。

## 共享执行流程

Harness 在 adapter execution 前后完成公共工作：

```text
manifest and task provenance
  -> validated workload and isolated PowerContext scope
  -> execution adapter
  -> normalized replay evidence
  -> Memory evaluation
  -> report rendering
```

Harness 记录 execution 前的 Memory baseline，随后调用 adapter，记录最终 Memory，并运行声明的 recall probe。Workload
中途失败时，已经采集的 evidence 仍会写入 artifact。

## 输入与指令边界

固定的 task 拥有 execution input，Harbor task 拥有 agent 可见的 instruction。Workload manifest 只引用这些输入，不复制
内容。Replay evidence 记录最终解析出的 instruction identity，并在安全时记录其内容。

Evaluation probe 与 execution input 相互独立。Probe 在任务结束后运行，不能向 agent 或 task verifier 提供提示。

## Memory acceptance

Memory acceptance 使用可观察的 evidence：

- 最终解析出的 task checksum 与 execution adapter 符合 manifest；
- 记录了所需的原生 execution evidence；
- 要求 capture 时采集了符合条件的 event；
- 本次运行创建了 Memory，并完成要求的 checkpoint 或 flush；
- 新建 Memory 引用了 execution 期间采集的 Source；
- 声明的 recall probe 能获得满足必需片段与禁用片段约束的 prepared context。

确定性 workload 可以要求固定的 Memory 片段。长程任务通常评估 capture coverage、grounding 与 recall，不要求固定的任务答案。

任务原生 reward、verifier result、运行时长与 model usage 保留为 label、score 或 metric。任务可以没有通过原生 grader，同时
通过 Memory acceptance。

# Reference-level explanation

## Workload 与 adapter contract

Manifest 是 harness 层的 workload 抽象。Adapter 拥有 execution-specific setting，并把固定 task 转换为标准化 evidence。
Dataset adapter 只生成标准 task layout 并固定 upstream provenance；它不运行 workload、不评估 Memory，也不渲染 report。

依赖保持单向：

```text
manifest and task provenance
  -> execution adapter
  -> replay evidence
  -> Memory evaluation
  -> report rendering
```

Evaluator 只读取 replay evidence，不能控制 adapter。Report renderer 只读取 evaluation result，不重新计算 acceptance。

## Evidence contract

每个 workload 生成一个 artifact 目录：

| Artifact | 用途 |
| --- | --- |
| `replay.json` | Workload identity、adapter、task provenance、runtime observation、Memory snapshot、probe 与原生 evidence reference。 |
| `eval-report.json` | Assertion、score、label、metric 与判断理由。 |
| `report.md` | Evaluation result 的可读表示。 |

Replay 记录 dataset checksum、workload 唯一的 `execution.type`、存在时最终解析出的 model identity、database identity、最终 instruction 与
PowerContext scope state。公共 envelope 支持离线重新评分。Adapter 原生 evidence 在 envelope 中保留类型信息；当前 Bub
adapter 记录 ACP summary、captured event、checkpoint、tool observation 与 trajectory artifact。

对于负向 recall contract，replay 记录脱敏前的匹配结论，而不是被匹配的文本。这样既能让离线重新评分与实时结果保持一致，
也不会通过 replay 暴露已配置的 secret。

最终 artifact sink 会移除已配置的 secret。原生 task artifact 可能包含任务内容，发布前需要检查。

## Adapter 扩展

如果未来的评估无法由 Bub 忠实表达，可以将 `execution` 扩展成包含新 adapter 的 discriminated union。例如：

```yaml
execution:
  type: basic
```

```yaml
execution:
  type: codex
```

这些示例不预留实现，也不把任何 benchmark 分配给其中一个 adapter。新 adapter 必须定义自己的 typed execution setting 与
原生 evidence，同时复用 workload identity、selection、replay envelope、Memory evaluation、artifact layout 与
reporting。迁移现有 benchmark 需要单独确定范围，并验证其原生语义。

## 兼容性

公开命令保持为 `acceptance`。现有 SQLite 与 OceanBase CI job 继续调用 `make harness-compose-acceptance`，并评估相同的默认
acceptance category。ID 与 category selector 扩展该命令，不引入通用 `run` 命令。

当前 LoCoMo benchmark 与 SWE-Pro evaluation 保留现有 command、artifact 与 result contract。LoCoMo 衍生的内置 workload
保持为固定 sample，不代表完整 benchmark 结果。

## Non-goals

本 RFC 不替代 RFC 0081，不定义 leaderboard，不要求发布到 registry，也不引入新的 agent protocol。它不统一 adapter 私有
实现，也不替代任务原生 grader。本 RFC 不迁移、重写或移除当前 LoCoMo benchmark 与 SWE-Pro evaluation，也不实现其他
execution adapter。

## Acceptance criteria

满足以下条件时，本提案完成：

- 一套 Pydantic manifest 可以表示确定性、使用 model 与 long-horizon workload；
- `execution.type: bub` 选择当前 adapter，`execution.model` 只声明是否需要 model；
- 各组件的原生运行时配置选择 model identity、provider、endpoint 与 credential，不增加 harness mapping layer；
- 一个 `acceptance` 命令可以选择一个或多个 workload ID 与 category；
- 仓库内与 registry Harbor task 使用相同的 execution 与 provenance contract；
- SQLite 与 OceanBase 在 required CI 中运行相同的确定性 acceptance category；
- 使用 model 的 Bub workload 通过 Harbor 与 ACP 记录原生 evidence；
- long-horizon Memory acceptance 与任务原生 reward 保持独立；
- 每个 replay 都标识 adapter，并支持离线重新评分；
- 当前 LoCoMo benchmark 与 SWE-Pro evaluation 保持不变。

# Drawbacks

公共 replay envelope 必须保留 adapter 原生 evidence，不能把它压成无类型 dictionary。长程运行可能消耗付费模型额度、
需要 privileged container，并产生较大的 artifact。因此 required database matrix 只覆盖确定性 workload，使用 model 的
category 保持为显式选择的 evaluation。

# Rationale and alternatives

为确定性样例、live agent run 与长程任务维护独立 harness，会重复 selection、evidence、evaluation 与 reporting。
公共 contract 将这些职责放在一处，adapter 则隔离 execution semantics。

把 model name 或 authentication method 放入 manifest，会让 workload 依赖某个 operator environment。布尔 requirement 可以
保留确定性边界，同时由运行时配置选择可用的 model 与 credential。

直接用任务原生 reward 作为 Memory acceptance，只能回答任务是否完成，不能回答 PowerContext 是否采集到有效 Memory。
原生结果会保留，但不会取代 Memory evaluator。

# Prior art

RFC 0081 将 runtime integration、workload execution、evidence collection、evaluation 与 reporting 分开。本提案保持这些
边界，并为内置 acceptance scenario 定义公共的 workload 与 artifact contract。

Harbor 提供固定的 task environment、agent lifecycle management 与任务原生验证。Bub 是首个可观察的 execution adapter。
Replay envelope 保留 Harbor 与 Bub evidence 的类型，同时让 Memory acceptance 与任务原生 score 保持独立。

# Unresolved questions

无。增加其他 execution adapter、迁移现有 benchmark、发布使用 model 的 artifact 都需要单独评审。

# Future possibilities

当 Bub 无法忠实表示某个 workload 时，workload contract 可以增加 `basic` 或 `codex` execution variant。新 adapter 复用
selection、replay、evaluation 与 reporting，不创建另一套 harness。

完整 LoCoMo benchmark 或 SWE-Pro evaluation 可以在其原生 scoring 与 artifact contract 经过验证后迁入 catalog。Registry
发布与公共 artifact retention 可以在隐私和运行要求明确后单独评审。
