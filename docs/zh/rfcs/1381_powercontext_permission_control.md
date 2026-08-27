---
Proposal Name: powercontext_permission_control
Start Date: 2026-08-27
RFC PR: #1381
Related RFCs: 0011, 0048, 0072
---

# 摘要

本文为 PowerContext 设计一套统一的权限管控模型，覆盖企业（B 端）和个人（C 端）场景，重点解决记忆、上下文、交接产物以及相关操作的授权、委托和审计问题。

设计保留最小且稳定的权限核心：主体（Subject）、资源范围（Resource Scope）、操作（Action）和请求上下文（Context）。租户隔离是系统强制的安全边界；团队、项目、地域、环境、业务线、场景类别和数据域等业务维度不固化为权限模型中的必选层级，而是通过可配置的 Context Attribute 扩展。

本文不定义 Prompt 的内容结构、继承和解析逻辑，也不把 User/Agent 级自定义 Prompt 设计为权限功能。Prompt 仅作为一种可受控访问的资源类型出现在权限模型中，Prompt 功能本身应在记忆/上下文能力 RFC 中单独定义。

# 动机

PowerContext 同时服务于企业应用、Agent、个人用户和本地进程。不同调用方需要访问的对象不同：有的调用方只允许查询个人记忆，有的需要写入项目记忆，有的需要使用一个交接包继续工作。若直接为每种业务场景增加固定角色和权限维度，模型会迅速膨胀，且无法覆盖用户自定义的场景划分。

当前需要明确以下边界：

1. 谁可以访问哪些 PowerContext 资源。
2. 一个操作实际作用于哪些资源对象，以及复合操作如何拆解授权。
3. B 端管理员、普通用户、Agent、应用和进程之间如何授权。
4. C 端用户如何以最少配置授权给自己的 Agent 或应用。
5. Handoff 交接后，接收方何时、以什么范围访问原始上下文。
6. 如何让用户在进程级别按自定义场景授权，同时避免把进程号等不稳定信息作为安全身份。

# Guide-level explanation

## 1. 四个固定权限维度

权限判断统一抽象为：

```text
Authorize(subject, scope, action, context) -> AuthorizedAccess | Deny
```

- `Subject`：发起请求的身份，包括 User、Agent、Application 和 Process/Workload。进程本身不是长期身份，进程权限绑定到可验证的 Service 或 Workload Identity。
- `Resource Scope`：资源的租户、归属域和生命周期边界。Scope 可以代表个人域、团队域、项目域、应用域或一次 Handoff 的隔离域。
- `Action`：对资源进行的操作，例如 `memory.search`、`memory.write`、`handoff.consume`。
- `Context`：本次请求携带的受信上下文和属性，例如场景、环境、地域、数据分类、时间和设备状态。

租户（Tenant）是不可由用户自定义或跨越的系统隔离边界。除租户外，系统只提供少量稳定概念，其余业务划分通过 Context Attribute 完成。

## 2. B 端和 C 端的授权入口

B 端采用分层管理，但不要求所有业务维度都成为固定角色：

- Tenant Admin 负责租户级安全边界、主体注册、全局策略和紧急访问约束。
- Authorization Domain/Project Admin 负责在租户允许的边界内维护 PolicySet，例如为某类应用绑定项目 Scope。
- Scope Owner 负责授予指定 Scope 内的访问权，也可以向 Agent 或应用发起有限委托。

C 端默认采用 Personal Domain。用户对 Agent/Application 的授权由用户显式确认，至少包含 Scope、Action、Context 条件和有效期。C 端不要求用户理解角色继承，授权界面应展示“这个主体可以在什么范围做什么事，什么时候失效”。

## 3. 进程级和场景级自定义权限

进程级授权绑定稳定的 `Workload Identity`，例如经过签发的服务身份、应用安装实例或本地 Agent 实例，而不是 PID、命令行或临时端口。调用请求必须同时携带：

- `subject_id`：可验证的用户、Agent、应用或 Workload 身份。
- `scope_id`：请求要访问的资源范围。
- `action`：要执行的操作。
- `context`：本次运行的场景和环境属性。

用户可以创建自定义场景类别，例如“客服质检”“司机画像”“研发排障”“财务对账”，并为场景设置属性条件和允许操作。场景类别只是用户定义的 Context Attribute，不会绕过 Tenant、Scope 或资源本身的 ACL。

示例：

```json
{
  "name": "客服质检",
  "attributes": {
    "scenario.category": "customer_service_review",
    "data.classification": "internal",
    "environment": "production"
  },
  "allowed_actions": ["memory.search", "memory.extract"],
  "scope_refs": ["scope://tenant-a/project/cs"],
  "expires_at": "2026-09-30T00:00:00Z"
}
```

请求上下文中的属性必须标注来源和可信等级：

- `system`：由服务端或可信运行时产生，例如租户、主体、Workload Identity。
- `managed`：由管理员配置并由服务端校验，例如项目、场景类别和数据域。
- `request`：由调用方声明，只能用于满足显式条件，不能提升主体权限。

# Reference-level explanation

## 1. Scope 与资源边界

PowerContext 的资源必须归属于一个明确 Scope。建议支持以下 Scope 类型，但不把它们全部变成固定授权层级：

| Scope 类型 | 典型用途 | 默认管理者 |
| --- | --- | --- |
| Tenant | 租户隔离和全局治理 | Tenant Admin |
| Personal | C 端个人记忆和上下文 | User |
| Team/Project | B 端团队或项目共享资源 | Scope Owner |
| Application/Workload | 应用或进程运行所需的资源 | Application Owner |
| Handoff | 一次交接产生的受保护资源 | 创建方或指定接收方 |

Scope 解决“资源属于哪里”的问题；PolicySet 和 AuthorizationGrant 解决“谁在什么条件下可以做什么”的问题。仅知道主体类型（例如 Agent）不能直接推导资源访问权。

PowerContext 中的核心资源包括：

- `Memory`：长期记忆、短期记忆及其版本、来源和元数据。
- `Source`：用于记忆提取或构建上下文的原始输入、会话或外部引用。
- `Artifact`：上下文包、经验、技能、摘要等可复用产物。
- `Candidate`：待审核或待写入的记忆候选。
- `Prompt`：可被策略允许使用的提示词资源；本文只定义访问控制，不定义 Prompt 功能。
- `Handoff`：交接请求、交接包和交接后的读取凭证。
- `Audit`：授权、拒绝、委托、交接和紧急访问事件。

## 2. Action 与可操作对象

Action 必须与资源对象绑定。不能因为主体具备一个 Action，就默认获得其他资源的同名权限。

| 资源 | 典型 Action | 操作对象 |
| --- | --- | --- |
| Scope | `scope.read`, `scope.grant`, `scope.revoke` | Scope 元数据、成员和授权关系 |
| Source | `source.read`, `source.attach` | 原始输入、会话、外部引用 |
| Memory | `memory.search`, `memory.read`, `memory.write`, `memory.update`, `memory.delete` | 记忆内容、版本、关联元数据 |
| Memory/Candidate | `memory.extract`, `candidate.review`, `candidate.accept`, `candidate.reject` | 提取任务、候选记忆及审核结果 |
| Artifact | `artifact.read`, `artifact.write`, `artifact.share`, `artifact.revoke` | 上下文包、经验、技能等产物 |
| Prompt | `prompt.read`, `prompt.use`, `prompt.bind`, `prompt.approve`, `prompt.revoke` | Prompt 资源的访问和绑定关系 |
| Handoff | `handoff.prepare`, `handoff.read`, `handoff.consume`, `handoff.accept`, `handoff.share`, `handoff.commit`, `handoff.revoke` | 交接包、交接授权和消费状态 |
| Audit | `audit.read`, `audit.export` | 审计事件和合规导出 |

`memory.extract` 是复合操作：它需要读取被授权的 Source/Artifact/当前 Memory，并在生成结果落库时具备对应 Scope 的 `memory.write`。复合操作的内部依赖只在本次请求内生效，不能被解释为永久授予 Source 或 Memory 的其他权限。

## 3. Handoff 的授权语义

Handoff 本身是独立的受保护资源。创建方可以选择接收方、可访问的 Scope、允许的 Action、数据范围和有效期；接收方只有在交接授权成立后才能读取或消费。

交接不产生隐式权限继承：

- 接收 Agent 不会因为“接收了交接”自动获得创建方 Agent 或 User 的全部权限。
- Handoff 中引用的源 Scope 不会自动对接收方开放。
- 接收方只能使用 Handoff 授予的资源引用和操作集合。
- Handoff 过期、撤销或被消费后，服务端应拒绝超出状态机允许范围的访问。

建议默认使用 `Reference` 交接：接收方获得受控引用，读取源数据时仍执行源 Scope 的权限校验。只有在业务明确需要时，创建方才选择 `Snapshot`，将交接时允许的数据复制到 Handoff Scope；Snapshot 也必须遵守数据分类、脱敏、有效期和撤销策略。

典型流程为：

```text
创建方 prepare -> 服务端校验并生成 Handoff
接收方 accept/read -> 校验接收方、Scope、Action、状态和有效期
接收方 consume/commit -> 标记消费结果，可按策略撤销或关闭
```

`handoff.share` 只允许在原授权明确允许再次分享时使用，且新授权范围不得超过分享方当前的有效权限。

## 4. Context Attribute 与自定义场景

固定权限核心只负责判断，不负责穷举所有组织结构。PolicySet 可以引用以下通用属性：

```text
context.scenario.category
context.project
context.team
context.region
context.environment
context.data.classification
context.device.trust_level
context.time_window
context.request.origin
```

用户或租户可以新增属性命名空间和取值约束，例如 `context.business_line`、`context.customer_tier`。为了防止调用方伪造高权限属性，策略应声明每个条件需要的可信等级；`request` 来源的属性只能作为限制条件，不能作为“管理员”“内部数据”等身份事实。

自定义场景的推荐配置方式：

1. 创建场景模板，定义名称、属性 Schema 和可选值。
2. 将场景模板绑定到一个或多个 Scope。
3. 在 PolicySet 中为场景授予最小 Action 集合和数据分类上限。
4. 将 Workload Identity、Agent 或 User 绑定到场景。
5. 运行时校验主体、Scope、Action、Context 和有效期，并记录审计事件。

场景类别可以随时被停用；停用只影响后续请求，已经签发的短期 AuthorizedAccess 是否立即失效由租户的撤销策略决定。

## 5. PolicySet、授权和委托

### PolicySet

PolicySet 是长期生效的策略集合，适合表达租户、项目和应用的治理规则。建议采用默认拒绝（default deny），显式声明资源、Action、条件和决策效果：

```json
{
  "policy_id": "pol_cs_review",
  "subject_selector": {"workload_id": "agent://qa-bot"},
  "resource_selector": {"scope_id": "scope://tenant-a/project/cs"},
  "actions": ["memory.search", "memory.extract"],
  "conditions": {
    "context.scenario.category": {"equals": "customer_service_review"},
    "context.data.classification": {"in": ["public", "internal"]}
  },
  "effect": "allow"
}
```

拒绝规则和租户级约束优先于普通允许规则。PolicySet 的变更需要版本、发布者、审批状态和生效时间。

### AuthorizationGrant

AuthorizationGrant 是面向一次用户同意、临时授权或 Handoff 的短期授权。Grant 至少包含：授予者、被授予主体、Scope、Action、Context 条件、有效期、是否允许再次委托和撤销状态。

委托必须满足：

```text
new_grant ⊆ issuer_effective_permissions
```

Grant 不能突破租户边界、Scope Owner 的限制、数据分类上限或 Handoff 自身的范围。授权服务端签发短期 `AuthorizedAccess`，调用方不能自行构造主体、角色、资源所有者或权限集合。

## 6. 运行时授权流程

一次 PowerContext 请求建议经过以下阶段：

1. 认证并验证 Subject 和 Workload Identity。
2. 解析目标资源、所属 Scope 和资源状态。
3. 合并 PolicySet、AuthorizationGrant、资源 ACL、Handoff 状态和租户约束。
4. 校验 Context Attribute 的来源、可信等级、时间窗口和数据分类。
5. 对复合 Action 展开最小依赖权限，并执行每个对象的授权判断。
6. 签发带有 `decision_id`、范围、Action、过期时间和撤销信息的短期 AuthorizedAccess。
7. 执行读写操作，并记录允许或拒绝的审计事件。

授权结果至少应包含：

```json
{
  "decision": "allow",
  "decision_id": "dec_123",
  "subject_id": "agent://qa-bot",
  "scope_id": "scope://tenant-a/project/cs",
  "actions": ["memory.search"],
  "expires_at": "2026-08-27T10:05:00Z",
  "constraints": {
    "data.classification": ["public", "internal"]
  }
}
```

客户端只携带服务端签发的访问凭证。服务端不信任客户端提交的 owner、tenant、role 或 permission 字段。

## 7. 数据模型建议

```text
Subject
  subject_id, subject_type, tenant_id, workload_id, status

Scope
  scope_id, tenant_id, parent_scope_id, scope_type, owner_subject_id, status

PolicySet
  policy_id, tenant_id, version, statements, state, effective_at

AuthorizationGrant
  grant_id, issuer, grantee, scope_id, actions, conditions,
  expires_at, delegable, revoked_at

Handoff
  handoff_id, source_subject, receiver_subject, handoff_scope_id,
  mode, references, allowed_actions, state, expires_at

AuthorizedAccess
  decision_id, subject_id, scope_id, actions, constraints,
  issued_at, expires_at, revocation_ref
```

所有服务端对象都必须带 `tenant_id`，并在持久化层和服务层同时执行租户隔离。资源删除、授权撤销和 Handoff 关闭应采用可审计的状态变更，而不是只依赖客户端删除本地凭证。

## 8. 审计、紧急访问和安全边界

以下事件必须审计：授权创建、修改、撤销、策略发布、策略拒绝、记忆写入/删除、Prompt 使用、Handoff 创建/读取/分享/消费、管理员和紧急访问。

审计字段至少包括：

```text
event_id, time, tenant_id, subject_id, workload_id,
resource_refs, action, decision, decision_id,
policy_refs, reason, source_ip, request_id
```

系统可以配置 Break-glass 紧急访问，但紧急权限必须同时满足：明确原因或工单、最短有效期、限定 Scope 和 Action、必要的审批、全量审计以及事后复核。紧急访问不能成为默认的“超级用户”通道。

对于确需支持的租户根管理员，建议将其实现为受强约束的系统管理主体：可以管理租户策略和授权，但默认不直接读取个人私有 Memory、Source 或 Handoff 内容。读取他人私有内容必须通过显式 Break-glass 流程，受时间、范围、原因和审计约束。

# Drawbacks

- 资源、Scope、策略和上下文属性的联合判断会增加授权服务和 SDK 的实现复杂度。
- 自定义 Context Attribute 提高了灵活性，但需要属性 Schema、来源可信度和生命周期治理，否则容易出现语义漂移。
- Handoff 的 Reference 模式需要接收方在运行时再次访问源 Scope，可能增加延迟和失败点。
- Snapshot 模式会带来数据复制、撤销困难和敏感数据扩散风险。
- 授权决策、短期凭证和撤销机制需要在高并发下保持一致性。

# Rationale and alternatives

## 选择最小固定核心加自定义 Context Attribute

这是在可理解性和可扩展性之间的折中。固定维度只保留 Subject、Scope、Action、Context，保证 API 和授权引擎稳定；组织结构和业务场景由用户定义，避免 PowerContext 为每个行业增加一套角色模型。

## 不采用仅 RBAC

RBAC 易于理解，但无法自然表达 Handoff、一次性授权、Workload Identity、数据分类和场景条件。角色仍可作为 PolicySet 的一种主体选择器，但不是唯一模型。

## 不采用仅 ABAC

完全依赖属性会使核心资源边界不清晰，也容易受不可信请求属性影响。因此先由 Scope 固定资源归属，再使用带可信等级的属性做条件判断。

## 不默认授予最大权限管理员

“能管理授权”与“能读取所有业务数据”应分离。默认最大权限会扩大内部越权和凭证泄露的影响面，且不利于合规审计。通过受控 Break-glass 可以覆盖运维场景，同时保留最小权限原则。

# Prior art

本文复用 PowerContext 已有 RFC 中关于远程访问、Handoff、统计 Scope 和 Artifact 的资源边界，并将这些边界统一到 Subject/Scope/Action/Context 授权判断中。

# Unresolved questions

1. Scope 的层级继承是否需要固定规则，还是完全由 PolicySet 表达。
2. `Reference` Handoff 的源数据读取应采用实时授权，还是允许短期缓存授权结果。
3. 自定义 Context Attribute 的 Schema、版本和命名空间由租户管理还是由平台托管。
4. 授权缓存和撤销传播的最终一致性窗口应如何配置。
5. C 端授权是否需要增加二次确认、设备绑定或高风险 Action 的强认证。
6. Prompt 资源的具体模型、版本和运行时解析策略应在哪个记忆/上下文 RFC 中定义。

# Future possibilities

- 提供面向租户管理员的策略模拟器，展示某个主体在指定 Context 下可执行的 Action。
- 提供场景模板市场和策略 lint，检查自定义属性冲突、过宽 Scope 和无效委托。
- 对 Handoff 增加数据最小化、字段级脱敏和接收方确认回执。
- 将授权决策与使用量统计、异常检测和合规报表关联起来。
- 在 SDK 中提供本地策略预检查，但最终决策仍由服务端授权服务给出。
