- Proposal Name: `experience_skill_review_page`
- Start Date: 2026-08-20
- RFC PR: [oceanbase/powercontext#1304](https://github.com/oceanbase/powercontext/pull/1304)
- Related RFCs: [RFC 0050](0050_artifact_candidate_review_inbox.md)、
  [RFC 0051](0051_experience_skill_artifact_families.md)、
  [RFC 0072](0072_scoped_statistics_and_usage.md)

# Summary

本 RFC 为 Experience 和 PowerContext-managed Skill Candidate 新增由 Server 托管的 Review 页面。该页面是现有
Candidate/Review 生命周期的用户界面投影，不会创建另一套审核模型、改变 Candidate 持久化，也不会绕过现有 HTTP
operation。

PowerContext 已经提供个人 Dashboard、配置好的 Dashboard scope、Bearer authentication，以及列出、读取、修改、批准和
拒绝 Candidate 的 Review operation。提议的 `/reviews` 页面把这些能力组合成一个 scoped Review Inbox。审核者可以：

1. 选择一个已配置的 scope；
2. 按状态和 Family 筛选当前 Candidate head；
3. 查看类型化 Experience 或 Skill proposal 及其精确证据引用；
4. 在不改变证据的情况下修改 proposal；
5. 批准精确的当前版本，或填写原因后拒绝；
6. 将 approved managed Skill 显式发布为配置好的本地 Agent Skill package，并验证本地发现状态；
7. 当另一位审核者先修改 Candidate 时，显式处理并发冲突。

页面默认显示 pending。approved 和 rejected Candidate 作为只读视图提供。Experience 和 Skill 共用一个页面，因为它们
共享同一 Candidate 生命周期；每个 Family 仍保留自己的展示方式和编辑表单。首版不增加 Candidate generation、证据内容
预览、审核者身份、RBAC、任务分派、通知、批量操作或 Skill 执行能力。发布是 approval 之后的独立显式操作，只能写入
配置中明确允许的 host-local Agent target。

# Motivation

Experience 和 managed Skill 被有意置于 Review gate 后。生成的 proposal 不受信任，不会获得最终 Artifact identity，也不能
在审核者批准前进入检索或 PreparedContext。该边界已经在 HTTP、Python Client、CLI 和 MCP 中实现。

Server Dashboard 目前会显示 pending Candidate 的数量，却不能让用户查看或处理它们。完成审核需要通过命令行或 MCP
调用，并准确提供 ID、版本、proposal shape 和证据引用。这适合自动化和调试，但日常人工治理难以发现，也容易被搁置。

不同 Family 的审核判断也不同。Experience 审核者需要判断 situation、action、outcome 和 lesson 能否构成可复用的结论。
Skill 审核者需要查看 name、description、instructions 和 validation checklist，同时记住批准只治理内容，不授予安装或执行
权限。通用 JSON editor 只会暴露 transport shape，无法帮助任何一种判断。

因此，最小有用产品切片不是新的工作流引擎，而是现有 Review contract 上的 scoped、structured 页面：

```text
Dashboard pending count
  -> Review Inbox
  -> 选择 scope 和 Candidate
  -> 查看类型化 proposal 与精确证据引用
  -> approve | 填写原因后 reject | revise 后 approve
  -> approved Artifact Revision
  -> 显式 publish -> 标准 SKILL.md package -> 本地 Registry 验证可发现
```

# Guide-level explanation

## 进入 Review Inbox

Dashboard 启用时，Server 主导航会在 Dashboard 和 Handoff Report 旁增加 **Review** 入口。打开后从同一个 Server
origin 加载 `/reviews`。它复用 Dashboard 的登录方式和 Bearer token 行为，不会引入另一套凭据存储或认证流程。

审核者首先从 `POWERCONTEXT_SERVER_DASHBOARD_SCOPES` 配置的 scope 中选择一个。如果没有配置 scope，页面会说明
Review 至少需要一个 Dashboard scope，并且不会发出 Candidate 请求。

切换 scope 时，页面会在加载新 scope 前清空当前列表、选中的 Candidate、pagination cursor、冲突状态和未保存的修改
draft。来自前一个 scope 的延迟响应不能更新页面。

## 处理一个统一队列

页面默认使用：

```text
status = pending
family = all
limit = 50
```

审核者可以选择 Experience、Skill 或所有 Family，也可以在 pending、approved 和 rejected 状态之间切换。改变任一筛选条件
都会从第一个 cursor page 重新开始。**Load more** 操作沿用 `next_cursor`；页面不会虚构 offset pagination 或 API 未提供的
total count。

宽屏使用列表和详情并列布局，窄屏使用上下堆叠布局。每一行只包含 Candidate contract 中已有的稳定字段：

- Family 和状态；
- Candidate ID 和当前版本；
- Experience 的 situation 和 lesson，或 Skill 的 name 和 description；
- 存在时显示 Candidate reason。

contract 不包含创建或更新时间，因此页面不会展示或按照虚构日期排序，而是保留 Server 返回的 cursor order。

## 审核 Experience

Experience 详情会分别展示四个类型化字段：

| 字段 | 审核问题 |
| --- | --- |
| `situation` | 情境是否足够具体，能够判断何时适用？ |
| `action` | 是否描述了实际执行的动作？ |
| `outcome` | 是否陈述了观察到的结果，没有过度推断？ |
| `lesson` | 结论是否可复用，并且有证据支持？ |

页面还会展示 Candidate reason、存在时的 target Artifact、精确 Source 引用和精确 Artifact 引用。首版把这些引用展示为
可复制的结构化 identifier。它不会读取或渲染 Source body，因为公开 HTTP contract 目前没有通用的精确 Source-read
operation。

例如，审核者可能看到：

```text
Candidate: cand_exp_123@2
Situation: OpenAPI source contract 发生变化。
Action: 重新生成 checked-in client 并运行 contract tests。
Outcome: generated operations 与 bundle 保持同步。
Lesson: 将 contract generation 和 contract tests 作为一个整体变更。
Evidence: source:task-outcome/run_42
```

审核者可以批准版本 2、填写原因后拒绝，或打开结构化 revision form。

## 审核 managed Skill

Skill 详情会展示：

- `name`；
- `description`；
- 以不受信任纯文本展示的 `instructions`；
- 每个 `validation` item，分别作为 checklist 条目。

页面不会把 instructions 解释成 HTML，也不会执行它们。批准只会创建或替换受治理的 Skill Artifact Revision。批准成功后，
页面切到该 approved Candidate，并显示独立的发布区域；发布仍需要审核者再次确认。

approved Candidate 本身保持不可变。交付区域同时提供 **创建新修订**：页面要求填写简短的修改证据，并用已批准 Skill 内容
初始化编辑表单。保存时先将说明捕获为有界 Source evidence，再创建一项新的 pending Skill Candidate；其 `target` 和 Artifact
evidence 都指向精确的 approved Skill Revision。该操作不会修改已批准 Revision 或已发布 package；新的 Candidate 必须重新经过
Review，产生的 Revision 才能作为更新发布。

批准操作旁会显示该区别：

```text
批准只治理此 Skill 的内容；发布是独立操作，发布也不会执行 Skill 或授予新的权限。
```

## 发布 approved managed Skill

发布区域只对带有精确 `result_artifact` 的 approved Skill 显示。目标来自
`POWERCONTEXT_SERVER_EXTERNAL_SKILLS` 中 `allow_managed_publish=true` 的 Codex 或 Claude Code target；页面不能提交任意
文件系统路径。

首次发布会在目标 root 下创建与 Skill name 同名的标准 package，包含 `SKILL.md` 和 `powercontext.json`。后者记录精确
Artifact Revision 和 `SKILL.md` digest。发布完成后 Server 立即刷新当前 scope 的 External Skill Registry；页面分别显示
package 是否是当前 Revision，以及 binding 是否已通过 locator 和 fingerprint 校验为 available。

后续 approved Revision 可以显式更新同一 PowerContext-owned package，包括合法的名称变化。更新前必须验证现有 manifest、
Artifact identity、Revision 和内容 digest。以下情况拒绝覆盖并显示冲突：

- 目标目录属于外部 Skill 或无法证明由当前 managed Artifact 创建；
- `SKILL.md`、manifest 或 package 文件集合已被本地修改；
- 同一 Artifact 在一个 root 中出现多个 projection；
- 目标中已经发布了更高 Revision；
- managed content 不满足所选 Agent 的 name、description 或 package 约束。

发布不会加载或执行 instructions，也不会绕过 Agent 的 discovery、approval、sandbox、tool 或 secret policy。它只让标准
Skill package 出现在配置好的本地 root 中；正在运行的 Agent 是否需要新会话才能看到更新，由宿主决定。

## 批准前修改

选择 **Revise** 后会打开根据当前 proposal 初始化的 Family-specific form。Experience 使用四个必填 textarea；Skill 使用
必填的 name、description 和 instructions 字段，以及有序 validation list。

保存 revision 时，页面使用当前 `expected_version` 发送完整 replacement proposal。首版页面会原样保留 Candidate 现有的
Source references、Artifact references、target 和 reason，不提供通用 evidence/lineage editor。需要修改 lineage 的审核者必须
使用现有 programmatic surface，或通过所属 generation flow 创建新的 Candidate。

revision 成功后会产生下一个不可变 pending version，页面随后展示返回的新版本。它不会自动批准；审核者必须再次查看并
单独批准修改后的内容。

## 批准或拒绝

Approve 需要简短确认，其中明确 Candidate 和版本。页面不会在批准请求中发送内容修改。成功后 Candidate 变为 approved，
并标识精确 result Artifact。

Reject 需要提供非空且不超过 2,000 个字符的原因。成功后不会写入 Artifact，Candidate 进入 terminal 状态。

决策成功后，页面切换到对应 terminal filter 并继续显示刚处理的 Candidate。approved Skill 因此可以创建 successor Candidate
或立即进入显式发布步骤；approved Experience 和 rejected Candidate 保持只读。页面不能重新打开 terminal Candidate。

## 显式处理并发审核

每个 revise、approve 和 reject 请求都使用详情中当前显示的版本。如果 Server 返回 Candidate 或 Artifact conflict，页面不会
重试写入，也不会自动合并内容。

页面会读取当前 Candidate head，并说明另一项写入已经赢得并发竞争。对于 approval 或 rejection，审核者必须查看新版本后
才能再次操作。对于 revision，页面会保留未保存的本地文本，直到审核者主动丢弃，或手动应用到新的当前 proposal。

# Reference-level explanation

## Goals and non-goals

首版目标如下：

- 让现有 Experience 和 managed Skill Review 生命周期可以从 Server UI 使用；
- 明确选择 scope，并将选择范围限制为配置好的 Dashboard scope；
- 将每个 Family 展示为可审核的 domain object，而不是通用 JSON；
- 保留精确 Candidate-version 和 target CAS 行为；
- 让不受信任内容保持 inert，并把批准与执行权限分离；
- 让 approved managed Skill 可以显式发布到配置好的本地 Agent target，并验证 package 与 Registry 状态；
- 支持英文和中文、键盘操作、窄屏以及现有明暗主题；
- 保持为当前 OpenAPI contract 上的薄投影。

以下内容不在范围内：

- generation、incubation、import 或 fork Candidate；
- 审核 Memory 或 Handoff；
- 编辑 Candidate evidence、target、lineage 或 generation reason；
- 渲染 Source 内容或任意 Artifact evidence preview；
- 自动发布、任意路径导出、Skill 执行、运行时热加载或回滚；
- reviewer identity、RBAC、SSO、分派、通知、服务级目标和批量操作；
- Candidate retention、reopen、delete、semantic diff 或 version-history 浏览；
- 面向未来 Artifact Family 的 generic form renderer；
- 新的前端框架或独立 Web application。

## Existing foundation

本设计复用当前 Server 行为：

| 现有 surface | Review 页面用途 |
| --- | --- |
| `GET /dashboard/scopes` | 列出 Server 配置显式暴露的 scope |
| `POST /v1/artifact-candidates/list` | 按 scope、状态、Family 和 cursor 分页读取当前 head |
| `POST /v1/artifact-candidates/get` | 刷新一个当前 Candidate head |
| `POST /v1/artifact-candidates/revise` | 追加一个完整 replacement proposal |
| `POST /v1/artifact-candidates/approve` | 原子批准精确的当前展示版本 |
| `POST /v1/artifact-candidates/reject` | 填写原因后拒绝精确的当前展示版本 |
| managed Skill exact read 与 Agent projection helper | 读取 approved Revision 并生成标准 `SKILL.md` package |
| `POST /dashboard/skill-projections/status` | 检查配置目标中的 package Revision、完整性和 Registry 状态 |
| `POST /dashboard/skill-projections/publish` | 显式创建或安全更新 package，然后刷新当前 scope 的 Registry |
| Dashboard authentication utilities | 将现有 Bearer token 附加到 same-origin request |
| Dashboard page UI utilities | 复用 locale、theme、status 和 stale-request handling 模式 |

本 RFC 不需要 OpenAPI 变更、generated client 变更、数据库 migration 或新的公开 persistence contract。两个
`/dashboard/skill-projections/*` endpoint 与 `/dashboard/scopes` 一样，是 authenticated Server UI supporting surface：它们
操作 Server host 上明确配置的本地 root，不是跨 host 的 PowerContext API，也不接受调用方提供的路径。可移植的 exact-read
仍由公开 `get_skill` contract 提供，CLI export 保持可用。

## Page availability and routing

Review 页面属于个人 Dashboard feature：

- route：`GET /reviews`；
- availability：仅在 `DashboardConfig.enabled` 为 true 时 mount；
- scopes：使用 statistics Dashboard 的同一组有序 `DashboardConfig.scopes`；
- authentication：使用相同 Server Bearer policy 和 same-origin request helper；
- navigation order：三者都可用时依次为 Dashboard、Review、Handoff Report。
- publication targets：只包含 `allow_managed_publish=true` 的显式 `AgentSkillTarget`；旧的 `CodexSkillRoot` 继续作为
  Codex-only 兼容格式，默认没有可写目标。

禁用 Dashboard 会同时移除 Dashboard 和 Review route。Handoff Report 仍可按其现有配置独立使用。

首版 Review 页面不接受 query parameter 中任意传入的 `scope_id`。它默认选择第一个已配置 scope，并允许审核者通过已配置的
picker 切换。这可以避免把形似 scope 的输入表现成权限，也避免 deep link 暴露未配置的 scope。

## Page state and request ordering

页面维护以下 client-side value：

```text
authentication state
configured scopes
selected scope
selected family filter
selected status filter
Candidate rows and next cursor
selected Candidate ID and current head
optional revision draft
optional managed Skill projection state and selected publish root
optional conflict or request error
```

scope 切换会取消或作废所有 in-flight list、detail 和 decision response，并重置全部 Candidate state。filter 切换会作废 list 和
detail response，并重置 pagination。选中列表行时，页面会先读取 current head 再启用写入操作，避免 stale row 直接变成
approval request。

同一时刻只允许一个针对当前 Candidate 的 decision 或 publish request。请求执行期间禁用相应写入控件。来自之前选择对象、
scope 或 Artifact Revision 的延迟成功响应不能更新新的选择。

## List, pagination, and selection

list request 为：

```json
{
  "scope_id": "project:powercontext",
  "status": "pending",
  "family": null,
  "cursor": null,
  "limit": 50
}
```

合并队列省略 `family` 或传 `null`；Family filter 则传 `experience` 或 `skill`。只有当 **Load more** response 属于相同
scope、filters 和 request generation 时，页面才追加列表行。Candidate ID 是 row key；版本变化时替换当前行，而不是创建
重复行。

完成 pending decision 后，页面切换到 returned terminal status，并在刷新后的列表中重新选择同一个 Candidate。这样
approved Skill 可以继续发布，而 rejected Candidate 仍可核对 decision reason。页面不会重置 scope；刷新后的列表始终是
权威状态。

## Family-specific rendering and editing

页面根据当前封闭 Family set 分派：

| Family | 摘要 | 详情和 revision 字段 |
| --- | --- | --- |
| Experience | `situation`，然后是 `lesson` | `situation`、`action`、`outcome`、`lesson` |
| Skill | `name`，然后是 `description` | `name`、`description`、`instructions`、有序 `validation` |

实现必须拒绝未知 Family，或与其 Family 不匹配的 proposal shape。页面显示 unsupported-content error 并禁用所有 decision
action，不能猜测 generic form 并提交无法验证的数据。

revision 使用公开 contract 已经强制的限制：

- 每个 Experience 字段必填，最多 8,000 个字符；
- Skill name 最多 128 个字符；
- Skill description 和每个 validation item 最多 2,000 个字符；
- Skill instructions 最多 32,000 个字符；
- Skill validation 包含 1 至 32 个非空 item。

client validation 用于改善反馈，但不能替代 Server validation。`422` response 在表单旁展示，不会改变当前 Candidate head。

## Evidence and trust boundary

Candidate proposal、reason、rejection reason、instructions 和 reference identifier 都是不受信任数据。页面：

- 通过 text node 或 form value 插入它们，绝不使用 `innerHTML`；
- 不渲染 Candidate Markdown，也不加载 Candidate 内容指定的 remote resource；
- 不执行 instructions，也不把它们转换为 link；
- browser code 不记录 proposal body、reason 或 evidence identifier；
- 保留 Server 当前严格的 Content Security Policy。

Source 和 Artifact reference 以精确结构化 value 展示。页面不会根据 identifier 推断本地路径、URL、permission 或
availability。`scope_id` 仍是业务 partition，不是 ACL。把一个 scope 加入 Dashboard 配置只控制 UI discovery；Server
authentication 和 deployment policy 仍负责访问控制。

pending 和 rejected 内容仍被排除在 Artifact discovery 与 PreparedContext 之外。页面绝不会用读取 approved Artifact 代替
审核 pending Candidate。

## Review actions and concurrency

UI 将 action 映射到现有生命周期：

```text
pending version N --revise(expected=N)--> pending version N+1
pending version N --approve(expected=N)-> approved + exact result Artifact
pending version N --reject(expected=N)--> rejected + decision reason
approved Skill Revision --create revision-> new pending Candidate targeting that exact Revision
approved Skill Revision --publish(target_id)-> exact Agent-local package + refreshed Registry
```

Approve 和 reject 仅对 current pending head 可用。Revise 仅对 proposal shape 受支持的 current pending head 可用。approved 和
rejected head 保持只读；创建 Skill 新修订会产生新的 Candidate，不会重新打开或修改 terminal head。

对于 `409 Conflict`：

1. 停止尝试的 transition；
2. 存在未保存 revision draft 时，将其保留在页面内存；
3. 读取 Candidate current head；
4. 显示旧版本号、新版本号和 Server 返回的 conflict category；
5. 要求新的显式审核操作。

发生冲突后，页面不会自动修改 `expected_version`、重试、批准或合并。

## Publication action and overwrite boundary

publication status request 使用精确 approved ArtifactRef：

```json
{
  "scope_id": "project:powercontext",
  "candidate_id": "cand_123",
  "artifact": {"family": "skill", "artifact_id": "skill_123", "revision": 2}
}
```

Server 首先验证该 Artifact 是指定 approved Skill Candidate 的精确 `result_artifact`，随后只对配置好的 Dashboard scope 和允许
managed publish 的 Agent target 返回目标。每个目标携带 `target_id`、`agent_kind` 和 installation scope，并返回稳定 state：`unpublished`、
`current`、`update_available`、`conflict`、`drifted` 或 `incompatible`，并独立返回 discovery 的 `available`、
`unavailable` 或 `not_published`。

publish request 额外携带 `target_id`，browser 不提交 Agent kind 或 destination path；Server 从配置中解析两者，再次读取 exact
approved Skill、重新检查文件状态，随后在同一 target 内 staging。已有 projection 只有在 manifest identity 与 digest 完整匹配时
才可被临时移出并替换；失败时恢复旧
package。相同 Revision 的重复 publish 是幂等的，但仍会刷新 Registry。文件或版本状态改变时返回 `409`，页面重新读取状态，
不会扩大覆盖范围。

该操作只管理 PowerContext 自己生成的 `SKILL.md` 与 `powercontext.json`。首版 managed content 不承载 arbitrary scripts、
references 或 assets，因此检测到额外 package 文件也视为 drift，不会删除它们。

## Loading, empty, and failure states

页面区分：

| 状态 | 行为 |
| --- | --- |
| 没有配置 scope | 说明 Dashboard scope 配置要求，不发送 Candidate request |
| filtered page 为空 | 说明哪个 scope、status 和 Family 没有 Candidate |
| 正在加载 list | 保持 filter 可见，并将 list 标记为 busy |
| 正在加载 detail | 保持 selected row 可见，并将 detail pane 标记为 busy |
| `401` | 清除当前 tab 保存的 token，返回现有 login screen |
| detail 返回 `404` | 移除 stale row，刷新当前 filtered page |
| `409` | 执行显式 conflict flow，不自动写入 |
| `422` | 保留 form 并展示 validation feedback |
| 没有 publish root | approved Skill 保持可读，并说明需要显式配置可写目标 |
| projection conflict/drift | 禁止发布，保留现有目录并展示安全错误 |
| package current 但 Registry unavailable | 允许显式刷新 discovery，不重写相同内容 |
| `503` 或 network failure | 保留 scope 和 filters，提供显式 retry |

list failure 不能保留其他 scope 的既有列表并让它看起来仍然有效。scope 一旦切换，stale content 必须立即隐藏。

## Accessibility, localization, and responsive behavior

英文和中文文案同步发布。Family 和 status value 在展示时翻译，但提交时使用稳定 API value。Candidate content 和 identifier
绝不翻译。

页面支持：

- 合理的 heading order 和有名称的 primary navigation region；
- 每个 form control 都有显式 label 和 error association；
- keyboard list selection 和可见 focus state；
- decision 后将 focus 返回下一行；
- 使用 announced status region 呈现成功决策、validation error 和 conflict；
- 使用原生 button 和 form control，而非可点击的通用 container；
- 不仅依靠颜色区分状态；
- 窄屏使用堆叠 list/detail flow，不隐藏任何审核字段或 action。

theme 和 locale 使用现有 Server page utility。页面不创建 Review-specific preference storage。

## Implementation slices

实现应分为五个可独立评审的 slice：

1. **Read-only Inbox**：route、navigation、authentication、scope picker、filters、pagination、list 和 typed detail；
2. **Decisions**：approve/reject、expected-version confirmation 和 pending-list advancement；
3. **Revision and conflict**：Family form、完整 replacement proposal、本地 draft preservation 和显式 `409` recovery；
4. **Managed Skill publication**：显式 root allowlist、status、safe create/update、manifest integrity 和 Registry refresh；
5. **Product hardening**：中英文一致性、responsive behavior、accessibility、packaging 和 browser tests。

每个 slice 都使用真实 Server endpoint。mocked unit test 可以覆盖 rendering helper，但不能替代以下 acceptance scenario：持久化
Candidate、通过页面加载、执行决策，并验证最终 Candidate 与 Artifact state。

## Acceptance

| 场景 | 通过条件 |
| --- | --- |
| Availability | `/reviews` 仅在 Dashboard 启用时存在，并出现在 primary navigation |
| Authentication | 现有 optional Bearer flow 保护页面数据并处理 `401`，不增加 token store |
| Scope isolation | 切换 scope 时在其他响应渲染前清除 rows、detail、cursor、conflicts 和 drafts |
| Default Inbox | 首个请求列出第一个配置 scope 下 pending Experience 和 Skill current head |
| Filtering | Family 或 status 变化会重置 pagination，不混合不同 filter 的 row |
| Pagination | Load more 沿用 `next_cursor`，保留 Server order，并按 Candidate ID 去重 |
| Experience | 四个类型化字段、reason、target 和精确 evidence reference 可读 |
| Skill | name、description、instructions、validation、reason、target 和精确 evidence reference 可读 |
| Revise | 完整 replacement proposal 创建 N+1 版本，保留 lineage 字段并保持 pending |
| Approve | 只有精确 current version 成功；response 标识 committed Artifact |
| Reject | 非空 reason 产生 rejected terminal Candidate，不产生 Artifact |
| Decision continuation | decision 成功后切到 returned terminal view，并重新选择同一 Candidate |
| Conflict | stale write 不会重试；读取 current head，并保留本地 revision draft |
| Successor revision | approved Skill 可以创建一项以精确 current Artifact Revision 为 target 的新 pending Candidate |
| Publish target | 页面只列出显式配置 `allow_managed_publish=true` 的 root，不接受任意路径 |
| First publication | exact approved Revision 生成标准 `SKILL.md` 与 manifest，并在 Registry 中 available |
| Safe update | 后续 Revision 只更新 identity/digest 完整匹配的 PowerContext-owned package |
| Drift and conflict | 外部目录、本地修改、重复 projection 和版本倒退均不被覆盖 |
| Trust boundary | Candidate text 保持 inert；批准不发布，发布也不授予 Skill execution 权限 |
| Terminal views | terminal content 只读；approved Skill 可以创建 successor Candidate 或发布其精确 result |
| Accessibility | 核心 review、revision 和 decision flow 可通过键盘使用，并向辅助技术播报 |
| Responsive UI | 窄屏堆叠布局仍提供相同字段和 action |
| Localization | 英文和中文覆盖相同状态、action、error 和 authority warning |
| Packaging | built wheel 包含 template 和 static asset，并可从安装后的 Server 使用 |

实现 pull request 必须运行 `make check`、`make test` 和 `make docs-test`，以及 focused Server-page tests 和真实浏览器流程。
浏览器流程覆盖两个 Family、scope 切换、三种 decision、stale-version conflict、approved Skill successor Revision 和
publication update、optional authentication、两种 locale 和窄屏 viewport。

# Drawbacks

- 页面会增加另一个 Server-owned JavaScript state machine，并重复 Dashboard/Handoff Report 已使用的部分 authentication、
  scope 和 status 模式。
- 只有精确 reference 而没有 Source-body preview，限制了审核者在单个页面中查看证据的深度。
- 统一 Inbox 虽共享生命周期，仍需要 Family-specific rendering 和 validation branch。
- 首版 revision form 不允许修改 evidence，因此部分修正仍需要 CLI、MCP 或新的 Candidate。
- 页面改善了治理入口，但不提供 reviewer attribution、authorization separation 或组织级 audit log。
- host-local publish 只对 Server 进程所在主机有效；远程浏览器操作的是 Server host，不是浏览器所在设备。

# Rationale and alternatives

| 方案 | 决定 |
| --- | --- |
| Review 只保留在 CLI 和 MCP | 拒绝；Dashboard 展示 pending 工作却没有人工完成路径 |
| 分别构建 Experience 和 Skill 页面 | 拒绝；会重复同一生命周期并拆散一个 scoped queue |
| 统一 Inbox + typed Family detail | **采用**；共享导航和 action，同时保留 domain shape |
| 展示和编辑任意 Candidate JSON | 拒绝；会暴露 transport detail，并更容易提交不安全内容 |
| 增加 Review-specific backend 或 persistence table | 拒绝；现有 OpenAPI 和 Candidate store 已经拥有生命周期 |
| 用新 SPA framework 替换 Server page | 本阶段拒绝；当前 packaged HTML/static model 已足够 |
| 在本 RFC 增加 evidence-body read | 延后；通用 Source reading 需要独立的 trust、retention 和 authorization contract |
| 支持 bulk approval | 首版拒绝；每个 Candidate 都需要内容和证据判断 |
| Approve 后自动发布 | 不采用；内容治理与 host filesystem mutation 必须是两个显式授权步骤 |
| 让浏览器传任意 destination | 不采用；只能引用配置好的 root ID，避免把 Dashboard 变成任意文件写入接口 |

不实现该页面会让治理 contract 在技术上完整，却在操作上难以发现。审核者仍可使用现有 programmatic surface，但 pending
Experience 和 Skill 更可能堆积，或在没有符合 domain 的结构化查看流程时被批准。

# Prior art

- RFC 0050 定义 Family-neutral Candidate 生命周期、expected-version write、terminal state 和 Review Inbox query model。
  本 RFC 展示该 contract，不改变它。
- RFC 0051 定义 Experience 和 managed Skill proposal shape、lineage，以及 Skill approval 与 execution authority 的边界。
  本 RFC 为这些 shape 提供独立 review view。
- RFC 0072 和现有 Dashboard 建立 configured scope discovery、scoped pending count、authentication、localization、theme 和
  Server-owned static delivery。
- Handoff Report 页面证明：focused workflow 可以共享 Server navigation 和 page utility，而无需成为 statistics Dashboard
  的一部分。

本设计不采用任何外部 review product 作为 protocol 或 compatibility target，而是遵循 PowerContext 当前 Candidate contract，
不会复制与 Artifact gate 不同的 issue tracker 或 code review semantics。

# Unresolved questions

没有未决问题阻碍首版。以下决定被有意延后：

- future exact Source-read contract 是否能安全支持 evidence preview；
- reviewer identity 和 decision attribution 应属于 Candidate persistence 还是独立 audit stream；
- 在更强的 authorization 和 scope discovery 完成后，稳定 Candidate deep-link contract 是否有价值；
- 当 review volume 增长时，是否需要 assignment、notification 或 bulk triage，但仍不提供 bulk approval。

# Future possibilities

自然扩展包括：

- 由显式 exact-read 和 redaction contract 支持的安全、有界 evidence preview；
- Candidate version history，以及 generated proposal 与 revised proposal 之间的 semantic diff；
- reviewer identity、decision attribution、RBAC、assignment、notification 和服务级报告；
- scope authorization 明确后的 URL-addressable Candidate detail；
- 面向大型安装的 saved filter 和 queue triage；
- 受治理的 rollback、unpublish 和跨 host publication receipt；
- 从 Dashboard pending count 跳转到对应 Review filter 的只读 link。

这些扩展必须保留核心边界：Candidate 在批准前不受信任；批准 managed Skill content 永远不授予安装或执行权限。
