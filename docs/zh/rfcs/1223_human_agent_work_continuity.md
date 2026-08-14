- Proposal Name: `human_agent_work_continuity`
- RFC Number: 1223
- Start Date: 2026-08-13
- Status: Draft
- RFC PR: [oceanbase/powercontext#1223](https://github.com/oceanbase/powercontext/pull/1223)
- Tracking Issue: [oceanbase/powercontext#1224](https://github.com/oceanbase/powercontext/issues/1224)
- Related RFCs: [RFC 0001](0001_product_definition_and_vision.md)、[RFC 0048](0048_handoff_artifact.md)、
  [RFC 0051](0051_experience_skill_artifact_families.md)、[RFC 0082](0082_handoff_report.md)

# Summary

本 RFC 定义 PowerContext 的工作连续性闭环。可以把它理解成一套有证据的“工作接力”：开始前说清楚要做什么，交接时说明做到哪里，接收方明确是否接得住，最后记录实际发生了什么。这样，同一段工作在人和人、人和 Agent、Agent 和 Agent 之间转移时，不需要依赖口头补充或复制完整聊天记录，也不会因为交接内容写着“继续执行”就自动获得权限。

这里的 `Work` 表示正在推进的工作过程，不是新增加的持久化实体。工作主线仍然是已有的 Workstream，稳定身份仍然是`scope_id`。本 RFC 新增的是围绕 Workstream 的四类连续性记录和一个只读投影，不新增 `Work` 表、Task 系统或 Workflow 引擎。

闭环由四个用户动作组成：

```text
Delegation 委托
  -> Work Contract 工作基线
  -> Execution 人或 Agent 推进
  -> Handoff 交接
  -> Continue + Acknowledge 接手并回执
  -> Task Outcome 记录实际结果
  -> 完成，或进入下一次 Handoff
```

首版不创建与 Handoff 平行的工作流引擎或数据库。Work Contract、当前工作边界、Handoff Receipt 和 Task Outcome
都作为有类型的 `ContentSource` 保存；临时和持久化交接继续使用 RFC 0048 的 Prepared Handoff、不可变 Handoff
Revision、evidence check 和 Continue。Task Outcome 保留现有 `metadata.kind="task-outcome"`，因此可以直接进入 RFC 0051 已有的 Experience 孵化与 Review 路径。

首版提供四个高层 operation：

- `create_work_contract`：把人的目标落成可检查的委托基线；
- `handoff_current_work`：保存集成层已经检查的当前状态，并准备 Prepared Handoff；
- `acknowledge_handoff`：重新解析 exact Handoff evidence 并记录接收、澄清或拒绝回执；
- `record_task_outcome`：在真实完成或中断边界记录结果和检查状态。

已有 `commit_handoff` 和 `continue_handoff` 保持不变。持久化里程碑仍然必须显式提交；接收回执仍然不能授予工具、网络、凭据或执行权限。

# Motivation

PowerContext 已经具备单个 Workstream 的 Handoff 生命周期和 Project 级 Handoff Report，但普通用户仍需要理解
`capture -> activate -> inspect -> finalize -> commit -> continue` 等内部步骤。更重要的是，仅有 Handoff 还不能回答：

- 人第一次把一项新工作交给 Agent 时，目标、范围和完成标准如何固定；
- 接收方是否真的拿到并理解了交接，还是只完成了传输；
- 接手之后实际发生了什么，检查是通过、失败、跳过还是未知；
- Agent 何时应该继续，何时应该把决策权还给人；
- 一次成功或失败是否能成为后续可评审的 Experience evidence。

三种参与关系需要不同体验，但不应形成三套互不兼容的模型：

| 关系 | 首要问题 | 产品体验 |
| --- | --- | --- |
| 人 -> 人 | 对方能否看懂状态并承担后续责任 | 人类可读报告、精确 evidence、显式接收回执 |
| 人 <-> Agent | Agent 是否理解意图，人是否保留取舍和授权权力 | Grounded Delegation、Task Outcome、决策回交 |
| Agent -> Agent | 新 Agent 能否脱离原 Session 安全继续 | Canonical JSON、exact selection、evidence gate、能力和权限复核 |

共同内核仍然是同一个 Workstream、同一份 Handoff 内容和同一组 exact evidence。人类、Agent 和审计系统只是消费不同投影。

# Guide-level explanation

## 先建立直觉：这是一套工作接力记录

可以先把五个核心概念理解成下面几样东西：

| 概念 | 通俗理解 | 回答的问题 |
| --- | --- | --- |
| Workstream / `scope_id` | 一条可以持续推进的工作主线 | 大家正在继续的是哪一件事 |
| Work Contract | 开工前的任务基线 | 一开始答应做什么，什么不做，怎样才算完成 |
| Handoff | 交接单 | 当前做到哪里，下一棒从哪里开始 |
| Handoff Receipt | 接收回执 | 接收方是否看懂、是否具备条件和权限接手 |
| Task Outcome | 一次尝试的结果单 | 接手后实际做了什么，检查结果如何，还剩什么 |

它们的关系是：

```text
一个 Workstream（身份是 scope_id）
  ├── 开始时可以有 Work Contract
  ├── 转移工作时产生 Handoff
  ├── 接收方对某个精确 Handoff 留下 Receipt
  └── 执行到完成或中断边界时产生 Task Outcome
```

因此，本 RFC 没有引入名为 `Work` 的聚合根或业务表。`WorkContract`、`WorkClaim` 和 `WorkContinuity` 中的 `Work`表示这些记录都在描述工作过程；Workstream 仍然是身份边界，Handoff Revision 仍然是持久化交接里程碑。

## 贯穿示例：把自动刷新功能交给 Coding Agent

下面用同一个任务走完整个闭环。示例中的内容为方便阅读做了省略，不规定 API payload 的具体格式。

### 第一步：人提出目标，系统形成 Work Contract

用户对 Coding Agent 说：

> 给 Handoff Report 增加每 5 秒自动刷新；用户有未发送修改时必须暂停，不能覆盖草稿；不要修改 Handoff Core；
> 完成后运行相关测试和 `make docs-test`。

这是一个新目标，所以首先发生的是 Delegation，而不是 Handoff。集成层检查当前仓库、已有 Handoff 和仓库规则，
然后创建 Work Contract：目标是“安全的自动刷新”；范围包括前端定时读取、草稿保护和相关测试；明确不修改 Handoff
Core、不新增通用 Scheduler；完成标准要求刷新不覆盖草稿，并为各项测试记录精确状态；授权只包括修改当前仓库，
不包括部署生产环境。

`declared` 表示这是参与者的声明；`verified` 必须带有同 scope 的精确 evidence。即使某个引用可以读取，也只证明引用
存在且身份匹配，不保证里面的事实现在仍然正确。Agent 仍然要以当前 workspace 和实时工具结果为准。

这一阶段不应要求用户重新描述能从环境查到的信息。例如“测试命令是什么”可以读取仓库规则；但如果发现每 5 秒刷新
会明显增加远程 Runtime 成本，是否接受这个取舍会改变结果，就应该把问题交还给用户决定。

### 第二步：Agent 工作到一半，准备 Handoff

Agent A 已经实现自动轮询和草稿保护，但一个 Revision 切换测试仍然失败，而且当前会话无法继续。它调用
`handoff_current_work`，形成类似下面的交接边界：

```yaml
objective: 为 Handoff Report 增加安全的自动刷新
current_state:
  - 自动轮询已经实现
  - 有未发送修改时会暂停刷新
  - frozen selection 切换测试仍然失败
disposition: continue
next_action: 修复 frozen selection 与当前 Report Revision 的比较逻辑
omissions:
  - 当前环境无法访问远程 CI
```

每条当前状态和下一步都引用这次保存的边界 Source；已有 verified claim 的原始 exact citation 继续保留。这里只允许一个
`next_action`，因为 Handoff 要告诉下一位参与者从哪里开始，不是建立另一套待办队列。

如果 Agent B 马上在另一个会话中继续，可以直接传递 Prepared Handoff。需要把它作为团队可长期查询的里程碑时，用户或
集成层再显式调用 `commit_handoff`，得到不可变 Handoff Revision。`handoff_current_work` 本身不自动 commit。

### 第三步：接收方检查后留下 Receipt

Agent B 收到交接后，不能因为交接里写着“下一步修复测试”就立刻修改代码。它先调用 Continue，并检查：

- Handoff 引用的 evidence 现在是否还能读取；
- 当前 checkout 是否确实对应这条 Workstream；
- 自己是否具备运行和修改相关代码的能力；
- 当前请求是否授予了所需的文件、工具、网络或其他权限。

检查后，Agent B 只能留下三种回执之一：

| 回执 | 本例中的情况 |
| --- | --- |
| `accepted` | evidence 可读，workspace 对得上，具备能力，也有修改当前仓库的授权 |
| `needs_clarification` | 交接引用的失败日志已不可用，无法判断应该修哪一个断言 |
| `declined` | 下一步实际要求部署生产环境，但当前接收方没有相应能力或授权 |

Receipt 只表示“这份交接是否接得住”，不表示工作完成。即使状态是 `accepted`，后面仍然需要 Task Outcome 或下一次
Handoff 才能闭环。

### 第四步：为什么必须接收精确 Revision

假设 Agent B 检查的是 Handoff Revision 7。在它确认接收之前，Agent A 又提交了 Revision 8，内容改成“停止修改，等待
负责人决定是否回滚”。如果 B 可以直接对 `latest` 留下 `accepted`，系统就会错误记录成“B 接受了 Revision 8”，但 B
实际上没有看过它。

因此，接收方必须先用 Continue 解析一个 Prepared Handoff 或 exact Revision，再对同一个精确选择留下回执。Report 预检
发现页面展示的 Revision 已经落后时，应要求刷新，不能替用户接受一个没有展示和检查过的新版本。

### 第五步：记录实际结果，而不是推断成功

Agent B 修复了测试，但 `make docs-test` 因环境问题超时。它记录的 Task Outcome 可以是：

```yaml
status: partial
summary: 自动刷新和草稿保护已完成，完整文档构建没有得到最终结果
handoff_receipt_ref: 对 Handoff Revision 7 的 accepted Receipt
checks:
  - name: focused pytest
    status: passed
  - name: make docs-test
    status: timed_out
  - name: remote CI
    status: unavailable
remaining_work:
  - 在文档构建环境正常后重新运行 make docs-test
```

没有失败记录不能推断为通过：没运行就是 `skipped`，超时就是 `timed_out`，环境不可用就是 `unavailable`，无法判断就是
`unknown`。这个 Outcome 精确引用了 Agent B 接受的 Receipt，所以该次交接的结果状态可以从 `awaiting_outcome` 变成
`covered`。`covered` 只表示已经记录结果；结果即使是 `failed`，也仍然可以是 covered。

Task Outcome 只保存“这一次发生了什么”。例如“后台刷新时必须保护未发送草稿”是否值得成为后续任务可复用的
Experience，仍然要经过 Candidate、Review 和批准，不能因为一次任务成功就自动进入 PreparedContext。

## 详细规则：委托不等于交接

人第一次提出一个尚未开展的新目标时，这是 Delegation，不是 Handoff。Codex 或其他集成层检查当前仓库、已有 Handoff、
项目约束和用户输入，再由 PowerContext 校验并保存 Work Contract：

```text
Human intent
  -> retrieve current facts
  -> ask only consequential goal, trade-off, or authorization questions
  -> Work Contract
  -> Agent execution
```

Work Contract 至少包含：

- `objective`：要达成的结果；
- `facts`：接手前的已知事实，并区分 `declared` 和 `verified`；
- `in_scope` 和 `exclusions`：本次工作边界；
- `completion_criteria`：什么才算完成；
- `authorization_notes`：已有授权或明确缺失的授权说明；
- `open_questions`：仍会改变结果的关键问题。

能从当前环境确认的事实不再要求人重复描述。只有影响目标、风险接受或授权的问题才应询问人。Work Contract 是
`untrusted_input`，不能覆盖当前 system/developer instruction、仓库规则或后续用户请求。

## 人和人

交接人使用 `handoff_current_work` 提交自己已经检查过的目标、当前状态、工作 disposition、唯一下一步和已知缺口。
PowerContext 将完整边界保存为 Source，并以该 Source 作为每条 Handoff statement 的直接 evidence，返回 Prepared Handoff。

普通临时交接直接传递 Prepared Handoff。需要团队里程碑时，交接人显式调用 `commit_handoff`。接收人在 Handoff
Report 中选择 Workstream，读取人类 Markdown 视图，再通过同一个 exact selection 调用 Continue。

接收人最后记录以下一种回执：

- `accepted`：已理解交接、PowerContext 引用当前可读，并已逐项确认实时工作区、能力与授权；
- `needs_clarification`：缺少事实、证据或必要决策；
- `declined`：范围、能力或授权不匹配。

回执只说明接收方的观察，不代表任务完成。真正的闭环由后续 Task Outcome 或下一份 Handoff 完成。

例如，开发者小王休假前提交一份 Handoff：“批量导入后的内存增长已经复现，修复分支有初步改动，下一步验证十万条数据；
生产日志因权限无法附上。”小李可以接受交接，也可以因为缺少复现数据选择 `needs_clarification`。发送了一份文档不等于
责任已经成功转移，Receipt 记录的就是这个区别。

## 人和 Agent

Human -> Agent 的最短路径是：

```text
create_work_contract
  -> Agent executes under current instructions
  -> record_task_outcome
  -> human review
  -> complete or handoff_current_work
```

Agent 不能把普通 Prompt、SessionEnd 或 Stop 自动声明为完成。只有 completion-aware integration 能确认任务真实到达完成、部分完成、阻塞、失败、取消或未知边界时，才调用 `record_task_outcome`。

Agent 遇到需要人类价值判断或新授权的情况时，应准备一份 `blocked` Handoff，把问题、选项、影响和 evidence 交还给人，而不是推测答案或把 `authorization_notes` 当作权限令牌。

## Agent 和 Agent

发送 Agent 使用 `handoff_current_work` 得到 canonical Prepared Handoff。宿主通过 MCP、A2A 或 provider metadata 原样传输该结构；接收 Agent 不解析人类 Markdown，也不依赖复制完整 Session transcript。

接收流程是：

```text
receive exact Prepared Handoff or Revision
  -> continue_handoff
  -> inspect trust and evidence checks
  -> compare current request and live workspace
  -> check capabilities and authorization
  -> acknowledge_handoff
  -> execute one applicable next action
  -> record_task_outcome or create the next Handoff
```

`acknowledge_handoff(status="accepted")` 会再次解析同一个 prepared 或 exact selection。它不接受 `latest`：接收方必须
先用 Continue 解析并检查，再用已检查的 exact Revision 回执。任一 statement 或 next action 的 evidence 不可用，或实时
工作区、能力、授权三项没有全部确认为 `confirmed` 时，Server 拒绝 `accepted`。这些确认仍是
`untrusted_observation`，不构成身份认证或 ACL。

## 一屏交接工作台

Handoff Report 为每个 Workstream 提供同一个一屏工作台，而不是把交接拆成多步向导：

- 左侧回答“我要交出什么”：编辑并发送当前目标、状态、唯一下一步和缺失材料；
- 右侧回答“我能不能接”：展示 exact evidence 预检，以及 workspace、能力和授权复核；
- 底部回答“这件事后来怎样了”：按稳定顺序展示 Contract、Handoff、Receipt 和 Outcome。

具体行为如下：

- 左侧交接卡从当前 committed Handoff 预填目标、状态、disposition、下一步和缺失项；全部字段在发送前可修改；
- “发送交接”是明确的持久化动作：先调用 `handoff_current_work` 固化已检查的边界，再调用 `commit_handoff`
  发布不可变 Revision；只在浏览器里编辑不会写 Source 或 Handoff；
- 页面为一次发送保留稳定的 `source_id` 和 Prepared Handoff。若边界已经准备但 commit 未确认成功，页面明确显示部分成功，
  重试只提交同一个 Prepared Handoff，不再次创建边界 Source；
- 右侧在接手前自动调用 Continue 检查最新 committed Handoff 和 exact evidence。预检结束后锁定返回的 exact
  Revision；如果它与当前 Report 卡片不同，必须先刷新，不能对未展示的新版本做选择；
- 接手方只有三个明确选择：`accepted`、`needs_clarification`、`declined`。Evidence 不可用时禁用 accepted；
  accepted 还要求实时 workspace、能力、授权全部确认，后两个选择必须写明原因；
- 自动预检分别显示“引用可读取”和接手方的实时检查，不能用 Evidence 绿色状态暗示陈述已经验证；
- accepted 后若结果尚未覆盖，时间线直接提供 Task Outcome 表单，并将结果精确关联到当前 accepted Receipt。
- 页面在已认证且可见时每 5 秒重新读取同一个 Project。卡片存在未发送修改或交接动作执行中时自动暂停；后台刷新不禁用
  页面控件、不清空错误区，也不覆盖当前或已切换 Workstream 的未发送草稿；
- 工作台展示当前 Workstream 的 Handoff Revision 轨迹。Report 返回 frozen selection 之前的 Revision 总数和最近 20 个
  有界摘要，页面按最新优先显示并标出当前 exact Revision；并发产生的更晚 Revision 不进入旧报告；
- Codex 可以把当前 Git 工作区一次性绑定到该 Workstream 的 `scope_id`。绑定保存在 Git 私有目录，后续会话优先复用，
  因此一句“交接”沿同一 Artifact lifecycle 产生下一个 Revision；显式配置仍优先，多个候选不能静默选择。

工作台底部按 Source journal position 展示 Delegation、Handoff、Receipt 和 Task Outcome 的连续性时间线。position
只表达稳定先后顺序，不伪造记录时间。投影分别返回交接状态
`not_applicable/awaiting_receipt/needs_clarification/declined/accepted` 和结果状态
`not_expected/awaiting_outcome/covered`。只有 Task Outcome 的 `handoff_receipt_ref` 精确引用当前 accepted Receipt，结果才是
`covered`；同 scope 中较晚出现但没有该引用的 Outcome 不会关闭交接。

## Task Outcome

Task Outcome 记录一次尝试实际发生了什么，而不是保存可复用结论：

| Field | 含义 |
| --- | --- |
| `objective` | 本次尝试对应的目标 |
| `status` | `succeeded/partial/blocked/failed/cancelled/unknown` |
| `summary` | 有界结果摘要 |
| `handoff_receipt_ref` | 可选；本结果精确覆盖的 accepted committed Handoff Receipt SourceRef |
| `observations` | 实际观察，并区分 declared/verified |
| `checks` | 检查名称、精确状态、依据和 evidence |
| `produced_artifacts` | 已形成的 exact Artifact Revision |
| `remaining_work` | 尚未完成的工作 |

检查状态固定为 `passed/failed/skipped/timed_out/unavailable/cancelled/unknown`。没有失败标记不能推断为通过，声明通过也
不能自动升级为 verified。Task Outcome 保存为 Source；Experience 仍然需要生成 Candidate、Review 和批准后才能进入
PreparedContext。

最容易混淆的边界是：`accepted` 不等于完成，`covered` 不等于成功，Task Outcome 也不等于可复用 Experience。它们分别
表示“接收方确认可以接手”“这次交接已经有精确关联的结果记录”和“这次尝试实际发生了什么”。

# Scope

首版包括：

- Work Contract、Current Work Handoff、Handoff Receipt 和 Task Outcome 的版本化模型；
- 四个 HTTP、Python Client 和 MCP operation；
- 同 scope exact evidence validation；
- 高层 current-work 到 Prepared Handoff 的确定性转换；
- evidence-gated acknowledgement；
- Task Outcome 与现有 Experience incubation 的兼容；
- Codex `project-context` skill 的低侵入使用流程。
- Handoff Report 的可编辑一屏交接卡、自动接手预检、三个接手选择和只读连续性时间线。
- Handoff Report 的自动刷新、Revision 轨迹，以及 Codex 工作区到 Workstream scope 的 Git 私有持久绑定。

首版不包括：

- 通用 Task、Workflow、Queue、Scheduler 或 Agent orchestrator；
- 把 Session、Agent、模型、Git branch 或 Issue 当作 Workstream identity；
- 自动 commit 每一次 Handoff；
- SessionEnd/Stop 强制生成 Outcome 或 Handoff；
- 保存完整 Prompt、Transcript、工具 stdout/stderr 或凭据；
- 根据历史 Handoff 自动授权或执行 next action；
- 跨 Runtime 自动复制 Project 或 Handoff history；
- 新的 per-scope ACL 或跨 trust-domain authorization。

# Reference-level explanation

## 身份和记录

Workstream 的稳定身份继续是 `scope_id`。Agent、Session 和人类名称只用于不可信归因，不形成工作 identity、ACL 或
CAS key。

四类记录映射到现有 Content Source：

| Record | `metadata.kind` | 持久化作用 |
| --- | --- | --- |
| Work Contract | `work-contract` | 委托时的目标和边界基线 |
| Current Work Handoff | `handoff-boundary` | Prepared Handoff 的直接 Source evidence |
| Handoff Receipt | `handoff-receipt` | 接收方对 exact selection 的观察回执 |
| Task Outcome | `task-outcome` | 一次尝试的 completion-aware evidence |

每条 Source 仍使用调用方稳定 `source_id` 和现有 Source 冲突语义。返回的 `WorkSourceReceipt` 包含 SourceRef、journal
position 和 canonical content digest。新增能力不增加业务表，也不修改 Artifact/Handoff 持久化 schema。

## Claim evidence

`WorkClaim.basis` 只有两种：

- `declared`：producer 声明，没有 exact evidence；
- `verified`：必须携带至少一个同 scope exact Handoff Citation。

调用方不能给 declared claim 附上 evidence 后仍称其为 declared，也不能在没有 evidence 时称其为 verified。Runtime 在
保存 verified claim、verified check 或 produced Artifact 前验证 exact citation。验证只证明引用可读和身份匹配，不能
代替事实新鲜度判断。

## Operation contract

| operationId | Path | Behavior |
| --- | --- | --- |
| `create_work_contract` | `POST /v1/work/contracts/create` | 验证 exact evidence 并保存 Work Contract Source |
| `handoff_current_work` | `POST /v1/work/handoffs/prepare-current` | 保存边界 Source，确定性构造并 finalize Prepared Handoff |
| `acknowledge_handoff` | `POST /v1/work/handoffs/acknowledge` | Continue exact selection，检查 evidence，保存 Receipt Source |
| `record_task_outcome` | `POST /v1/work/outcomes/record` | 保存 completion-aware Task Outcome Source |

`handoff_current_work` 不调用生成模型。每个 state 和 next action 都引用边界 Source；verified claim 的原 exact evidence
作为额外 citation 保留。该 operation 不 commit。`commit_handoff` 仍是唯一发布持久化里程碑的入口。

`acknowledge_handoff` 只接受 `prepared/exact` selection，不接受 `latest`。Prepared target 以 canonical digest 定位；exact
target 保存 resolved exact Handoff Revision。回执分别保存 evidence availability、不可用 citation，以及接收方声明的
live-state、capability、authorization 检查。`accepted` 要求 Evidence 可用且三项检查全部为 `confirmed`。

Prepared Receipt 可以表达临时载体已被观察，但首版只有 committed exact Handoff Receipt 能被 Task Outcome 引用并进入
结果覆盖计算。

## 连续性投影

`WorkContinuity` 是从同一 scope 的 Source journal 动态生成的只读投影，不建立新业务表。投影只解析四种已验证 schema
的 Work record；普通 Content Source 被忽略，声明为 Work kind 但内容无法通过对应 schema 的记录计入
`invalid_record_count`，不会被当作有效历史。

投影返回完整记录计数和最多最近 64 个时间线事件。超过上限时 `truncated=true`，但 `coverage` 仍基于本次读取到的全部
有效 Work record 计算。投影以当前 exact Handoff 的最后一条 Receipt 得出 transfer state；只有该 Receipt 为 accepted，才
进入 `awaiting_outcome`。Outcome 必须通过 `handoff_receipt_ref` 精确引用它才变为 `covered`。另一个 Revision、较早 Receipt
或无关联 Outcome 都不能覆盖当前选择；Outcome 的 succeeded 或 failed 不影响“是否已有精确结果记录”的判断。

## 一致性与失败

- Source capture 继续使用现有稳定 `source_id` 幂等/冲突行为；
- 一屏工作台在一次发送重试期间保留同一 `source_id` 和 Prepared Handoff；
- Prepared Handoff 的 `base` 仍记录 finalize 时观察到的 committed head；
- Handoff commit 继续使用 RFC 0048 CAS，不因高层 API 改变；
- acknowledgement 对相同 selection 重新执行 Continue，不能复用调用方伪造的 evidence result；
- Work record 保存成功不表示 scheduler、Experience generation、Review 或执行已经发生；
- 任一步失败不回滚此前已经明确完成的 Source capture，返回的 receipt 用于识别已完成边界。

## Trust and authorization

Work Contract 是 `untrusted_input`；Current Work Handoff 是 `untrusted_input`；Handoff Resolution 是
`untrusted_history`；Receipt 和 Task Outcome 是 `untrusted_observation`。

这些值都不能覆盖：

- system/developer instruction 和当前用户请求；
- 仓库内 AGENTS.md 等规则；
- 当前 workspace 和实时工具结果；
- 宿主的工具、网络、secrets 和写入授权；
- Project/scope 之外的访问策略。

`scope_id`、Project membership、MCP tool visibility、receiver label 和 authorization note 都不是 ACL。

## Integration rules

Integration 应只在明确的工作边界调用这些 operation：

- 新委托开始且需要稳定基线时创建 Work Contract；
- 用户明确交接，或工作必须转移时准备 Handoff；
- 接收方完成 live-state、能力和授权复核后记录 acknowledgement；
- integration 能识别真实完成或中断语义时记录 Task Outcome。

Hook 可以快速、fail-open 地采集轻量 evidence，但不得把 SessionEnd 或 Stop 作为唯一完成依据。Session ID 只作为 Source
metadata 或归因，不进入 Work/Handoff identity。

# Success metrics

首版 dogfood 关注：

- `continuation_success_rate`：无需用户重新描述完整背景即可执行第一个正确动作的比例；
- `time_to_first_verified_action`：接手到第一个 verified action 的时间；
- `clarification_rate`：因信息或 evidence 缺口退回的交接比例；
- `evidence_availability_rate`：接手时仍可解析的 Handoff claim 比例；
- `handoff_result_coverage_rate`：拥有 accepted exact Receipt，且有 Outcome 精确引用该 Receipt 的交接比例；
- `unauthorized_action_count`：历史交接导致的越权执行次数，目标必须为零。

指标不能把 `accepted` 当作完成，也不能把 `succeeded` 的 producer 声明当作独立验证通过。

# Acceptance

| Scenario | Pass condition |
| --- | --- |
| Human -> Agent | Work Contract 保留目标、范围、完成标准和授权说明，不覆盖当前指令 |
| Human -> Human | committed Handoff 可从 Report 读取，并能得到 exact Revision acknowledgement |
| Agent -> Agent | Prepared Handoff 原样传输，接收方不依赖原 Session 即可 Continue |
| High-level handoff | 一个 operation 保存 boundary Source 并返回未提交 Prepared Handoff |
| Evidence gate | 任一 Handoff evidence unavailable 时不能记录 `accepted` |
| Clarification | unavailable evidence 可以记录带原因的 `needs_clarification` |
| Editable card | 切换语言或在当前标签页刷新相同 Handoff Revision 的报告时，发送前编辑不会被 Report 重绘覆盖 |
| Automatic refresh | 页面每 5 秒读取同一 Project；有未发送修改或执行中动作时暂停，后台刷新不锁住页面控件 |
| Revision history | Report 返回 frozen selection 之前的总 Revision 数和最近 20 个摘要，当前 exact Revision 明确可见 |
| Workstream binding | Git 工作区绑定一次固定 `scope_id` 后，后续 Codex 会话和一句交接继续同一 Artifact lifecycle |
| Retryable send | prepare 成功而 commit 未确认时，重试复用同一 `source_id` 和 Prepared Handoff |
| Exact preflight | 自动预检解析 latest；若与 Report 卡片不一致则阻止选择，否则三个选择绑定 exact Revision |
| Three choices | 接手方始终看到三个动作；accepted 要求 Evidence 可用和三项接收检查确认，另两个动作要求原因 |
| Continuity | Report 按 journal position 展示四类 Work record，不把 position 表示为时间戳 |
| Result coverage | accepted 后为 awaiting_outcome；只有精确引用该 Receipt 的 Outcome 才变为 covered |
| Outcome status | failed/skipped/timed_out/unavailable/cancelled/unknown 不会升级为 passed |
| Experience boundary | 只有 `task-outcome` Source 进入已有 Experience incubation，且结果仍为 pending Candidate |
| Persistence | Work records 复用 Source journal；Handoff 继续使用不可变 Revision 和 CAS |
| Compatibility | 历史 Work record 仍可读；新的 accepted 请求必须使用 prepared/exact 并携带 receiver checks |
| MCP | 四个高层 operation 可供 Agent 使用，但 tool visibility 不授予执行权限 |
| Codex | Skill 不再要求用户或 Agent手动拼接 capture/activate/finalize 生命周期 |

# Rollout

1. 在 SQLite/Codex dogfood 中启用四个 operation，验证单 Workstream 完整闭环；
2. 把 completion-aware Task Outcome producer 接到稳定、公开的 integration boundary；
3. 通过 Handoff Report 一屏工作台 dogfood 发送、exact 预检、三种回执和 Outcome coverage，不改变 Handoff Core；
4. 用真实多人和多 Agent 任务收集 success metrics，再决定是否扩展其他 Agent provider。

# Drawbacks

- 四种 Work record 增加了产品词汇，需要通过高层动作而不是字段列表向用户解释；
- acknowledgement 只能验证 PowerContext evidence，可用性不等于 live state 正确；
- Source-backed record 没有独立查询索引，首版主要通过 exact evidence、Handoff 和后续 Report projection 消费；
- completion-aware integration 必须理解宿主的真实任务边界，不能仅依赖通用 Stop hook。

# Unresolved questions

- Handoff Report 何时需要增加按 receiver/status 的连续性筛选，而不让主工作台变成审计控制台；
- 不同 Agent provider 是否能提供足够稳定的 completion signal，而无需读取私有 Session 数据库；
- 跨 Runtime Prepared Handoff 传输需要怎样的签名、撤销和 authorization contract；
- 何时值得把 Work Contract 作为独立可查询 projection，而不是继续保留为 Source-backed evidence。
