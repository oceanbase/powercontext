- Proposal Name: `experience_skill_artifact_families`
- Start Date: 2026-07-29
- RFC PR: [oceanbase/powercontext#51](https://github.com/oceanbase/powercontext/pull/51)
- Related RFCs: [RFC 0001](0001_product_definition_and_vision.md)、[RFC 0014](0014_memory_layer_design.md)、
  [RFC 0016](0016_pydantic_ai_inference_integration.md)、[RFC 0028](0028_context_pack.md)
- Depends on: [RFC 0031 Artifact Candidate 与 Review Inbox](https://github.com/oceanbase/powercontext/pull/50)

# Summary

本 RFC 定义 `experience` Artifact Family、PowerContext-managed `skill` Artifact Family，以及当前 Agent
环境中本地可用的外部 Agent-native Skill 纳管边界。

先记住四句话：

1. Experience 是从实际工作证据中提炼出的可复用判断，回答“在什么情况下，什么做法产生了什么结果，因此学到了什么”。
2. Skill 是 Agent 可以发现并用于完成某类任务的能力包，回答“什么时候用、入口在哪里、如何使用和验证”。
3. Session 和 task 只是 evidence 的采集边界与演化触发点，不是 Experience 或 Skill 的身份和生命周期边界。
4. Skill 有外部纳管和 PowerContext 托管两种来源；二者可以在当前 Agent 环境中统一发现，但内容权威不能混为一谈。

PowerContext-managed Skill 可以从一个或多个 approved Experience、官方文档、人工输入和后续使用反馈中孵化，但并非所有
Skill 都必须从 Experience 生成。当前 Agent 环境已经安装或提供的 Skill 由原始系统持有，PowerContext 只负责本地发现、
索引、关联和解析，不静默复制或改写它们，也不负责把它们交接到其他 Agent 或主机。

Experience 和 managed Skill 都复用现有 Artifact identity、不可变 Revision、lineage 和 CAS。它们的 create 和 revise 必须先进入 RFC 0031 定义的 Review Inbox。外部 Skill 的发现不产生 managed Artifact；只有显式 import 或 fork 才会提出新的 managed Skill Candidate。

Experience 和 managed Skill 的生成与演化是需要配置生成模型的高级能力。未配置模型时，Runtime 不生成 Candidate，也不使用
规则降级；Artifact 的存储、Review、exact read 和本地 External Skill registration 仍可独立工作。模型不能批准自己的
Candidate、分配最终 Artifact identity 或获得执行权限。

# Motivation

## Task result 不等于 Experience

一次任务通常会留下改动文件、命令结果、风险和下一步。这些信息对当前交接有价值，但大部分只描述“这一次发生了什么”。

```text
“本次修改了 3 个文件，make contract-test 通过。”
```

这是 task result，不是 Experience。Experience 还需要从 evidence 中提炼出有适用边界的关系：

```text
“修改 openapi/powercontext.yaml 后，需要重新生成 Client，再运行 contract tests；
否则生成代码可能与公开 contract 不一致。”
```

单次任务可能足以提出 Experience，但 Experience 不局限于单次任务。后续任务可以补强、缩小、推翻或拆分原有判断，并通过新的
Candidate 和 Revision 继续演化。

## Procedure 不等于完整 Skill

`preconditions -> steps -> validation -> failure handling` 描述的是可执行 procedure。它可以是 Skill 的重要组成部分，但不能完整代表 Coding Agent 使用的 Skill。

一个 Agent Skill 通常还需要：

- 可发现的名称和说明，告诉 Agent 什么时候应当加载它；
- Agent 可读取的 instructions 入口；
- 当前 Agent 或宿主能够解释的格式和能力；
- 可能引用的脚本、模板、示例和参考材料；
- 在某个用户、项目或插件环境中的安装位置。

因此，本 RFC 不再把 Skill 定义成固定的线性步骤列表，也不把复杂到出现分支的内容立即升级为 workflow。首版 managed Skill
只定义说明和指令核心；外部 Skill 保持当前 Agent 能够读取的原生 package 形态。Routine/Procedure 是否成为独立 Family
留给后续设计。

## Skill 已经存在于 PowerContext 之外

用户在接入 PowerContext 前，当前 Agent 环境中可能已经存在：

- Codex 用户级或项目级 Skill；
- Claude Code Skill；
- 仓库中的 `.agents/skills/`；
- 由插件、Git 仓库或团队工具安装的 Skill。

如果 PowerContext 只认识自己从 Experience 孵化出来的 Skill，就无法发现当前 Agent 已经可以使用的能力，还可能重复创建
managed Skill。

PowerContext 需要“纳管”而不是“接管”外部 Skill：

- 原始 package 仍是内容权威；
- provider adapter 只扫描当前 Agent 配置允许的本地目录和安装范围；
- PowerContext 维护可重建的本地目录、content fingerprint 和 binding；
- 只有当前 Agent 能够解析、读取且 fingerprint 与最近扫描一致的 binding 才标记为 available；
- binding 失效时返回 unavailable，不查找远端来源，不提供安装提示，也不把本地 locator 当作跨端 contract。

## 可复用资产必须跨越 session 和 task

如果每次 session 都重新生成孤立的 Experience 或 Skill，系统得到的只是另一种会话摘要。真正有价值的闭环是：

```text
bounded task evidence
  -> propose or revise Experience
  -> Review
  -> reusable Experience Revision
  -> propose or revise managed Skill
  -> Review
  -> publish or bind to an Agent
  -> collect bounded usage evidence from later tasks
  -> propose the next Experience or Skill Revision
```

Session/task 是可观察边界和触发点。Artifact identity 属于长期 scope，例如用户、项目或团队，不能由 session ID 或 task ID 决定。

# Guide-level explanation

## 用两个维度理解设计

第一个维度是内容语义：

| 对象 | 回答的问题 | 典型内容 |
| --- | --- | --- |
| Memory | 未来需要直接记住什么 | 事实、偏好、决定、约束 |
| Experience | 什么做法在什么情况下产生了什么结果 | situation、action、outcome、lesson |
| Skill | Agent 何时以及如何使用一项能力 | name、description、instructions、validation |
| Procedure | 一项能力内部按什么流程执行 | 前置条件、步骤、分支、失败处理 |

第二个维度是 Skill 的内容权威：

| Skill 来源 | 内容权威 | PowerContext 负责什么 | PowerContext 不做什么 |
| --- | --- | --- | --- |
| External / Agent-native | 当前 Agent 环境中的原生 package | 本地发现、索引、关联和 exact resolve | 静默改写、远端安装或跨 Agent 解析 |
| PowerContext-managed | exact Skill Artifact Revision | Candidate、Review、Revision、lineage 和发布投影 | 批准后自动执行或授予工具权限 |

“来自哪里”和“内容是什么”是两条独立的轴。一个外部 Skill 也可能包含高质量 procedure；一个 managed Skill 也可能来自人工或官方文档，而不是 Experience。

## 哪些是现有能力，哪些是新增能力

```text
[现有] SourceRef / ArtifactRef / immutable Artifact Revision / lineage / CAS
[现有] ArtifactLineage 可以引用多个 exact SourceRef 和 ArtifactRef
[依赖] RFC 0031 Candidate / Review Inbox
[现有] Memory 与 Memory-backed PreparedContext

[新增] Experience typed content 与跨任务演化规则
[新增] PowerContext-managed Skill typed content 与演化规则
[新增] 当前 Agent 环境中的 External Skill registration、可重建索引和本地 binding 语义

[不改] Memory 写入、flush、MCP、Codex Hook 和当前 PreparedContext
[不做] 跨 Agent/主机交接、自动安装、自动执行、格式转换、workflow engine 和权限授予
```

“本地可用”是相对于当前 Agent kind、当前 host 和当前安装 scope 的判断，不是 Skill 的全局属性。本 RFC 不把本地
registration 或 binding 序列化成跨 Agent contract，也不要求立即修改当前 Context Pack contract。

## 例子一：跨三个任务形成 Experience

三个独立任务留下有边界 evidence：

```text
Task A: 修改 OpenAPI 后没有生成 Client，contract-test 失败
Task B: 修改 OpenAPI 后执行 api-generate，contract-test 通过
Task C: 手工修改生成代码，api-generate-check 失败
```

配置生成模型后，生成器可以同时选择三个 exact SourceRef，提出：

```yaml
family: experience
proposal:
  situation: 修改 PowerContext 的公开 OpenAPI contract
  action: 修改 source 后重新生成 Client，并检查生成差异
  outcome: 生成代码与 OpenAPI 一致，contract checks 通过
  lesson: OpenAPI 是公开 HTTP contract 的权威来源，不能只修改或手工维护生成代码
sources:
  - source:task-outcome/task_a
  - source:task-outcome/task_b
  - source:task-outcome/task_c
status: pending
```

这三个 task 不需要属于同一个 session。Review 只关心 scope 是否一致、evidence 是否能支持 proposal，以及是否存在被隐藏的反例。

## 例子二：后续任务修正已有 Experience

假设 `experience/exp_openapi_change@1` 只要求运行 `contract-test`。后续任务发现还必须先生成 Client，Runtime 可以提出：

```yaml
family: experience
target: artifact:experience/exp_openapi_change@1
proposal:
  situation: 修改 PowerContext 的公开 OpenAPI contract
  action: 运行 api-generate，检查生成差异，再运行 contract-test
  outcome: generated Client 与 OpenAPI 一致，contract tests 通过
  lesson: contract-test 不能替代生成步骤
sources:
  - source:task-outcome/task_d
artifacts:
  - artifact:experience/exp_openapi_change@1
status: pending
```

批准后形成 Revision 2。exact reader 仍可以读取 Revision 1；后续任务默认读取 active Revision 2。若新 evidence 与旧结论
冲突，生成器应缩小 situation、拆分 Experience 或保留冲突，不能按相似度静默覆盖。

## 例子三：发现当前 Codex 环境中的本地 Skill

Codex provider 在用户级 Skill 目录中发现一个 Python Skill：

```yaml
skill:
  identity: external:codex:user/friendly-python
  origin: external
  content_ref: source:agent-skill/friendly-python@sha256:abc123
bindings:
  - agent: codex
    scope: user
    locator: user-skill:friendly-python
```

当前 Codex integration 查询时，Registry 重新确认 locator 可读且 fingerprint 一致，然后返回本地入口。目录被删除、内容已经变化
或调用方不在同一 scope 时，返回 unavailable，并从 available discovery 结果中排除。Claude Code、另一台主机或另一个用户
不能复用这条 binding；它们需要各自在本地完成 discovery。

## 例子四：从多个 Experience 孵化 managed Skill

配置生成模型后，Runtime 选择两个 approved Experience 和一份项目命令文档，提出 managed Skill Candidate：

```yaml
family: skill
proposal:
  name: powercontext-openapi-change
  description: 修改 PowerContext 公开 HTTP contract 时使用；覆盖生成、差异检查和验证
  instructions: |
    先修改 openapi/powercontext.yaml，然后运行 make api-generate。
    检查生成代码差异，禁止手工修补 src/powercontext/http/_generated/。
    最后运行 make contract-test；失败时保留失败输出，不声明任务完成。
  validation:
    - make api-generate-check 通过
    - make contract-test 通过
artifacts:
  - artifact:experience/exp_openapi_change@2
  - artifact:experience/exp_generated_code_edit@1
sources:
  - source:repository/makefile_contract_targets
status: pending
```

批准后，Skill Artifact Revision 才是内容权威。把它渲染到当前 Agent 的 Skill 目录只是 host-local projection；projection
丢失或过期时可以从 exact Revision 重建。

## 例子五：使用反馈继续演化 Skill

Skill 在后续任务中可能成功、失败或根本没有被采用。Owning integration 可以在 task end 或明确的 Agent 停止边界提交有界
使用证据：

```text
Skill@1 在 Task E 中被选中 -> validation 通过
Skill@1 在 Task F 中被选中 -> 缺少 packaging 验证，任务失败
```

这些结果可以产生新的 Experience，也可以与 `Skill@1` 一起提出 replacement Candidate。它们不能直接修改 Skill，也不能只因
“调用次数多”就自动提升信任等级。

# Reference-level explanation

## Scope

本 RFC 定义：

- Experience 的 typed content、evidence 和跨 session/task 演化规则；
- PowerContext-managed Skill 的 typed content、evidence、Review 和 Revision；
- External Skill 与 managed Skill 的权威边界；
- 当前 Agent 环境中 External Skill registration、可重建索引和本地 binding 的最小语义；
- create、revise、import 和 fork 的 Review 边界；
- 模型能力门禁、检索、发布和执行权限边界。

本 RFC 不重复定义 RFC 0031 已负责的 Candidate identity、状态机、CAS、Review API 或 Candidate persistence。

本 RFC 不定义：

- 跨 Agent 或跨主机的 Skill 交接、解析和可移植性 contract；
- 自动安装、自动更新或自动卸载外部 Skill；
- 脚本、二进制文件和大型 assets 的托管格式；
- Routine/Procedure、workflow 或 DAG Family；
- Skill 执行沙箱、工具授权或 secret policy；
- 全量历史会话扫描和无边界后台蒸馏。

## Experience Family

`experience` content 包含四个字段：

| 字段 | 含义 | 不应写成什么 |
| --- | --- | --- |
| `situation` | 经验成立的场景、触发条件和边界 | “开发时”“通常情况下”这类无边界描述 |
| `action` | 在该场景中实际采取的做法 | 没有发生过的建议或猜测 |
| `outcome` | 可由 evidence 支持的实际结果，包括失败 | “应该有效”“大概通过” |
| `lesson` | 从 action 与 outcome 中得到的可复用判断 | 原始任务摘要、计数或空泛口号 |

Evidence 不重复存入 content。Candidate 使用 RFC 0031 的 `sources/artifacts`，批准后写入 `ArtifactLineage`：

- 至少存在一个 exact SourceRef 或 ArtifactRef；
- 可以引用一个或多个 session/task 的 evidence；
- 所有 evidence 必须处于调用方允许的同一 scope，不能跨 tenant/project 泄漏；
- evidence 必须支持 situation、action 和 outcome，而不是只与主题相似；
- 单次任务可能足以提出 Experience，不要求等待固定样本数；
- 多次任务可以共同提出首个 Revision，也可以修正已有 Revision；
- 成功和失败都可以形成 Experience，失败不能被改写成成功建议；
- 矛盾 evidence 必须在 Candidate 中显式收窄、拆分或保留冲突。

Experience Artifact ID 是 scope 内长期 identity，不包含 session ID 或 task ID。Revision lineage 保存本次生成直接使用的
SourceRef 和 ArtifactRef；需要历史证据时，通过 exact previous ArtifactRef 继续追溯，不复制无界历史。

首版不增加自动 `confidence`、`importance`、`decay` 或自由 metadata。任务数量不等于可信度，Review 仍需判断证据质量和
适用边界。

## PowerContext-managed Skill Family

首版 managed `skill` content 包含：

| 字段 | 含义 | 最小要求 |
| --- | --- | --- |
| `name` | 人和 Agent 可识别的显示名称 | 非空；不是 Artifact identity |
| `description` | 适用任务、触发条件和预期结果 | 必须能帮助 Agent 判断何时加载 |
| `instructions` | Agent 可读取的完整核心指令 | 有界文本；不能只写标题或口号 |
| `validation` | 判断使用结果是否成功的方法 | 至少一项可观察结果 |

`instructions` 可以包含 preconditions、步骤、约束、有限分支和 failure handling，但首版不把它解析成 workflow/DAG。
需要脚本、模板或参考材料时，可以通过 exact SourceRef 记录来源；首版不托管任意 package assets。

Managed Skill Candidate 不再统一要求引用 approved Experience：

- 从 Experience 自动孵化时，至少引用一个 exact approved Experience ArtifactRef；
- 从官方文档或人工完整编写时，可以只引用 exact SourceRef；
- 从外部 Skill import/fork 时，必须引用该外部 Skill 的 exact snapshot/fingerprint；
- 从使用反馈 revise 时，必须引用 exact target Skill Revision 和直接使用的 bounded evidence。

无论来源如何，approval 后这些 direct reference 都写入 Skill Artifact lineage。

## External Skill registration and index

External Skill 不是 managed Skill Artifact。其原始 package 是内容权威，PowerContext 只维护 registry projection。一个最小
registration 需要表达：

- scope 内稳定的 Skill identity；
- external origin/provider 与本地 locator；
- 当前观察到的 content fingerprint 或 exact SourceRef；
- 用于发现的 name 和 description；
- 当前 Agent kind；
- 至少一个当前环境能够解析的本地 binding。

Registry/index 是可重建投影，不是第二套内容真相：

- rescan 可以更新 availability、fingerprint 和 binding；
- 上游内容变化不能伪装成同一个 exact version；
- 外部 Skill 消失时从当前 Registry/index 中移除；已写入 Artifact lineage 的 exact SourceRef 不受影响；
- discovery 只证明“发现了内容”，不证明内容安全、正确或适合当前任务；
- 只有显式 import/fork 才创建 managed Skill Candidate 和新的 Artifact identity。

## Local Skill binding and resolution

Binding 表示当前 Agent 环境如何访问 Skill。它至少区分 Agent kind、host、安装 scope 和 locator。Binding 是本地环境状态，
不是 Skill 内容，也不进入 managed Skill Revision。

一个 external Skill 只有同时满足以下条件才算“本地可用”：

- registration 对当前调用 scope 可见；
- binding 属于当前 Agent kind、当前 host 和允许的安装 scope；
- locator 当前存在并可读；
- 当前 content fingerprint 与 registration 一致。

Exact resolve 只返回满足上述条件的本地入口。任一条件不满足时返回 unavailable，不回退到其他版本，不查询远端来源，也不生成
安装提示。

Registration 和 binding 不作为跨 Agent 或跨主机 contract。另一个 Agent、host 或用户必须通过自己的 provider adapter 重新
扫描本地环境。本地 resolve 成功也不代表 Agent 已获准加载或执行 Skill。

## Cross-session generation and evolution

首版不依赖后台无限扫描。只有配置生成模型后，Owning integration 才在以下有边界事件中显式选择 evidence 并触发生成：

- task outcome 形成；
- session/turn 到达 integration 能解释的停止边界；
- Git change 或验证结果改变已有判断；
- Skill 使用产生明确 outcome；
- 用户主动要求沉淀或更新。

每次 evolution run 只能得到四种结果：

```text
没有可复用变化 -> no-op
形成新判断     -> create Candidate
修正已有内容   -> replacement Candidate targeting exact active ArtifactRef
证据相互矛盾   -> scoped split or explicit conflict Candidate
```

Runtime 可以使用检索建议可能相关的 active Artifact，但不能仅凭相似度选择 target 并覆盖。首次实现可以由 owning integration 或
人工显式选择 target；后续索引只是候选发现机制。

## Generation and Review

生成器使用已配置的生成模型提出 typed proposal，不分配最终 Artifact identity，也不直接写 Revision：

```text
bounded exact evidence
  -> generate typed proposal outside transaction
  -> validate shape, scope, target and references
  -> persist pending Candidate
  -> human approve/revise/reject
  -> commit Artifact Revision on approval
```

Experience 和 managed Skill 使用 RFC 0031 的 `review` policy：

- create 只生成 Candidate；
- revise 以 exact active ArtifactRef 为 target，生成完整 replacement Candidate；
- import/fork 外部 Skill 生成新的 managed Skill Candidate，不修改 registration；
- `approve` 不能同时修改 proposal；
- approval 通过 Family validation 后，在同一事务提交 Artifact Revision 和 Candidate 状态；
- stale target 返回 conflict，不自动三路合并。

External Skill discovery 不进入 Review Inbox，因为它只记录可重建的观察结果；把它推荐给任务、import 成 managed Skill、发布或
执行属于后续显式决策。

## Model capability gate

Experience/managed Skill generation 是高级能力，只有配置生成模型后才启用。底层 Artifact contract、Review 和本地 External
Skill Registry 不依赖 LLM：

| 能力 | 是否需要 LLM |
| --- | --- |
| typed validation、Revision、Review、exact read | 不需要 |
| 本地 External Skill 扫描、fingerprint、binding 和 exact resolve | 不需要 |
| 生成或 revise Experience Candidate | 需要 |
| 生成、import、fork 或 revise managed Skill Candidate | 需要 |
| 判断冲突 evidence 应缩小还是拆分场景 | 需要，最终由 Review 决定 |

没有配置生成模型时：

- Runtime 不暴露可用的 Experience/managed Skill generation capability；
- task、session 或 usage outcome 不触发 Experience/managed Skill generation；
- 显式 generate、revise、import 或 fork 请求在持久化 Candidate 前返回 typed capability error；
- 本地 External Skill discovery、index、binding 和 exact resolution 正常工作；
- 已有 Candidate 的 Review 以及已批准 Artifact 的 exact read 正常工作；
- Runtime 不用确定性规则或原始摘要强行包装 Experience/managed Skill。

规则仍负责输入上限、scope、exact reference、typed shape 和 lineage 校验，但这些校验不能代替语义生成。LLM 可用时也不获得
额外权限：模型不能批准 Candidate、选择执行授权、伪造 evidence、自动安装 Skill 或提交最终 Revision。

## Identity, Revision, persistence, and projections

Experience 与 managed Skill 复用现有 Artifact contract：

- Family 分别固定为 `experience` 和 `skill`；
- Artifact ID 是 scope 内 opaque identity，不从标题、内容、session 或 task 计算；
- 每次批准的修改产生不可变 Revision；
- lineage 保存直接 SourceRef 和 ArtifactRef；
- ArtifactStore CAS 防止基于过期 head 提交。

External Skill Registry、全文/向量索引和 Agent binding 都是可重建 projection，不是新的内容权威。Builtin Runtime 不创建
`experiences`、`skills` 等平行真相表；具体 registry persistence 可以复用通用 registry/projection 能力，在实现 RFC 中确定。

当前通用 Artifact contract 没有 retire 语义，因此本 RFC 不增加自动淘汰或时间衰减。首版用 reviewed Revision 修正 managed
内容，用 rescan 刷新 external registration。使用次数、任务数量和向量分数都不能静默删除或覆盖内容。

## Retrieval, Context Pack, publication, and execution boundary

- pending/rejected Candidate 不进入 Artifact 或 Skill discovery；
- approved Experience/managed Skill 可以通过 exact ArtifactRef 读取；
- external registration 只有在当前 scope 可见、本地 binding 可用且 fingerprint 一致时才可被解析；
- 本 RFC 不为 Experience/Skill 固定 FTS、vector、graph、sparse 或 reranker；
- 当前 PreparedContext 继续只读取 active Memory；
- 发布 managed Skill 只创建 host-local projection/binding，不改变内容权威。

任何 Skill 都是不可信内容。Review、发现或本地 resolve 都不授予以下权限：

- 在 Agent 中加载或执行；
- 自动安装、更新或删除 package；
- 作为 MCP tool 发布；
- 访问 secrets、网络、文件系统或其他工具；
- 绕过宿主 approval 和 sandbox policy。

## Implementation order

实现按三个可独立 dogfood 的 vertical slice 推进：

1. Local External Skill Registry：先接入当前 Agent 的一个 provider，完成本地 discovery、fingerprint、binding 和 exact resolve；
2. Experience：在已配置生成模型时支持单任务和多任务 evidence、Review、exact read，以及后续任务驱动的 replacement
   Candidate；
3. Managed Skill：在已配置生成模型时支持多来源 Candidate、Review、Revision、host projection 和使用反馈驱动的
   replacement Candidate。

三个 slice 都不需要先抽象通用蒸馏框架、复杂 ranking 或自动发布系统。

## Acceptance

| 场景 | 通过条件 |
| --- | --- |
| Experience shape | situation/action/outcome/lesson 均存在，evidence 可 exact resolve |
| Cross-task Experience | 一个 Candidate 可引用多个 session/task 的 evidence，Artifact identity 不绑定 session/task |
| Experience evolution | 后续任务通过 exact target 提出 replacement Candidate，旧 Revision 仍可读取 |
| Conflict handling | 矛盾 evidence 不被相似度静默合并，必须缩小、拆分或显式冲突 |
| Scope isolation | 跨任务聚合不会越过调用方允许的 tenant/project scope |
| Managed Skill shape | name/description/instructions/validation 满足最小要求 |
| Managed Skill provenance | 按生成方式引用 Experience、Source、external snapshot 或 usage evidence |
| External authority | 外部 package 保持权威，Registry/index 可重建且不会静默改写内容 |
| Exact external version | 本地内容变化产生新的 fingerprint，过期 registration 不再被解析为 available |
| Local binding | 只返回当前 Agent、host、scope 下存在、可读且 fingerprint 一致的本地入口 |
| Review gate | Experience/managed Skill create、revise、import 和 fork 只产生 pending Candidate |
| LLM gate | 未配置模型时不生成 Experience/managed Skill Candidate，生成请求返回 typed capability error |
| No-LLM baseline | 本地 external discovery、已有 Candidate Review 和 approved Artifact exact read 仍可工作 |
| Retrieval gate | pending/rejected 内容不进入 Artifact 或 Skill discovery |
| Execution boundary | discovered/approved/resolved Skill 都不自动安装、加载、执行或获得权限 |
| Compatibility | Memory、flush、MCP、Codex Hook 和当前 PreparedContext 行为保持不变 |

# Drawbacks

- 区分 external registration 与 managed Artifact 增加了一层概念，但它避免形成两个内容权威。
- External Skill 可能移动、消失或内容漂移，Registry 必须及时刷新本地 availability。
- 跨任务 evidence 会带来真实冲突，Review 成本高于每次生成孤立摘要。
- Experience/managed Skill generation 必须配置模型，增加了部署和运行成本。
- 首版只支持当前 Agent 环境中的本地可用 Skill，不能直接复用其他 Agent、主机或用户的 binding。
- Instruction-only managed Skill 不能覆盖所有脚本和 assets package，复杂 packaging 需要后续设计。

# Rationale and alternatives

| 方案 | 结论 |
| --- | --- |
| Experience 只属于一次 task/session | 不采用；会退化成任务摘要，无法形成长期可复用判断 |
| 所有 Skill 必须由 Experience 蒸馏 | 不采用；会排除现有 Agent-native Skill、官方文档和人工创作 |
| 把 procedure 等同于完整 Skill | 不采用；无法表达发现、入口、兼容性、资源和安装位置 |
| 把外部 Skill 全量复制成 Artifact | 不采用；会制造第二套真相并隐藏上游版本漂移 |
| 把本地 binding 当成跨 Agent contract | 不采用；本地 locator 只对当前 Agent、host 和安装 scope 有效 |
| 发现外部 Skill 后自动安装/加载 | 不采用；发现和引用都不等于执行授权 |
| 自动合并高相似 Experience/Skill | 不采用；相似度不能证明适用范围和内容权威相同 |
| 现在增加 workflow/DAG 和 package runtime | 不采用；先完成可解释的 Experience、本地 Registry 和 managed Skill 闭环 |
| 现在增加 graph/sparse/reranker | 不采用；先用真实跨任务数据评测基线 |

# Prior art

- RFC 0001 将 Memory、Experience、Routine 和 Skill 放在 Artifact registry 与自进化底盘中，并把 mount 定义为向 Agent
  投射受治理内容；本 RFC 明确 Experience、Procedure 和 Agent Skill 的边界。
- RFC 0014 定义跨 session 的 Artifact identity 和 exact evidence；本 RFC 复用这些长期资产原则。
- RFC 0016 提供统一模型接入边界；Experience/managed Skill generation 通过该边界要求生成模型，External Skill Registry
  不直接依赖模型或供应商 SDK。
- RFC 0031 定义 Candidate 与 Review Inbox；本 RFC 复用其 envelope、lifecycle、CAS 和 approval transaction。
- 仓库 `skills-lock.json` 已使用 source、ref、skill path 和 content hash 描述可恢复的外部 Skill；本 RFC 借用其中的
  exact-content 思路定义本地 Registry，而不是把 lock file 变成运行时数据库。

# Unresolved questions

实现前需要确认：

- 当前 Agent 的首个 provider 如何生成 scope 内稳定 external identity、本地 locator 与多文件 package fingerprint；
- generation capability 如何检查模型配置，并将 provider/model failure 映射为稳定的 typed error；
- 每个文本字段、evidence count、registration 和 binding 的具体上限；
- 第一个 dogfood integration 使用哪一种 task/usage outcome Source。

以下问题不阻塞本 RFC：

- Routine/Procedure 是否成为独立 Artifact Family；
- scripts、templates 和 assets 的 managed package format；
- 跨 Agent/主机 Skill 交接、自动安装、发布、卸载和格式转换；
- Experience/Skill 的 retire、排序和使用归因；
- Experience/Skill 进入 multi-Artifact Context Profile 的选择和预算规则。

# Future possibilities

形成真实跨任务数据后，可以继续：

- 由 Skill 使用反馈生成新的 Experience 或 managed Skill Candidate；
- 将 external Skill 显式 import/fork 为可治理的 managed Skill；
- 为 approved Experience/managed Skill 和 external descriptors 建立可重建搜索 projection；
- 在 multi-Artifact Context Profile 中统一选择 Memory、Experience 和 Skill，并只预算一次；
- 用固定任务评测本地 availability、freshness、conflict 和 useful-use rate；
- 只有评测证明增益后，再考虑 graph、sparse、reranker、自动推荐和自动发布。

所有扩展都必须保留两条边界：Session/task 只是 evidence 边界，不限制长期资产演化；External Skill discovery 只描述当前
Agent 环境中的本地可用内容，只有受治理的 managed Artifact Revision 才是 PowerContext 拥有的 Skill 内容真相。
