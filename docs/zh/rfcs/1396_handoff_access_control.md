- Proposal Name: `handoff_access_control`
- RFC Number: 1396
- Start Date: 2026-08-30
- Status: Draft
- RFC PR: [oceanbase/powercontext#1396](https://github.com/oceanbase/powercontext/pull/1396)
- Tracking Issue: [oceanbase/powercontext#1395](https://github.com/oceanbase/powercontext/issues/1395)
- Related RFCs: [RFC 0011](0011_remote_access_architecture.md)、[RFC 0048](0048_handoff_artifact.md)、
  [RFC 0050](0050_artifact_candidate_review_inbox.md)、[RFC 0051](0051_experience_skill_artifact_families.md)、
  [RFC 0082](0082_handoff_report.md)、[RFC 1223](1223_human_agent_work_continuity.md)

# Summary

本 RFC 为 PowerContext Server 定义独立的 Access Control 边界、稳定的 Resource Kind，以及由 Artifact Family 驱动的
Access Profile contract，并把 Handoff 作为第一种完整的资源级授权场景。它既回答一个具体问题——当用户 A 把一份
Handoff 交给用户 B 时，B 可以看到什么、可以做什么，以及这些权限如何撤销和审计——也规范后续 Artifact Family
如何复用同一套 Principal、action、ResourceRef、Binding、PEP（Policy Enforcement Point，策略执行点）/PDP
（Policy Decision Point，策略决策点）、列表和审计语义。

Handoff 内容不保存用户、角色或 ACL。`scope_id` 继续表示 Workstream 的稳定业务分区，不是用户身份、tenant、角色或
安全边界。身份认证和权限判定发生在 Server：认证层得到可信 Principal；PowerContext Server 的策略执行点（PEP）把
Principal、action 和 resource 交给作为策略决策点（PDP）的可替换 `AuthorizationProvider`。PDP 查询策略或关系存储并
返回允许或拒绝决定；PEP 只有在允许时才调用现有 Runtime application service。

```text
身份提供方或静态凭据
          |
          v
     已认证的主体
          |
          v
PowerContext Server 策略执行点（PEP）
          |
          | 授权请求
          v
  AuthorizationProvider（PDP） <----> 策略或关系存储
          |
          | 允许或拒绝决定
          v
PowerContext Server 策略执行点（PEP）
       |                 |
      允许               拒绝
       |                 |
       v                 v
  现有应用服务        返回 403
```

首版定义三种稳定 Resource Kind：

```text
├── server                       管理资源
├── scope                        管理资源
├── artifact                     内容资源
│   ├── family=handoff
│   ├── family=memory
│   ├── family=experience
│   ├── family=skill
│   └── family=prompt（保留但禁用）
```

- `server`：当前 PowerContext deployment；
- `scope`：一个精确 Workstream scope；
- `artifact`：一个由 Artifact Family Access Profile 解释的逻辑 Artifact identity 或 Family-owned 逻辑 selector。

`artifact` Resource Kind 当前启用 `handoff`、`memory`、`experience` 和 `skill` 四个 Artifact Family Access Profile。
`prompt` vocabulary 已保留，但在 PowerContext 实现 Prompt lifecycle 前保持禁用。`ArtifactReference.family` 是唯一的
Profile discriminator；客户端不再提交第二个可能与它冲突的内容类型。

用户 A 可以选择两种协作方式：

- 为长期协作者授予 Workstream 级角色；
- 只把一个已持久化或已批准的逻辑 resource 授予 B。

第二种方式是首版的最小权限路径。B 可以读取被分享逻辑资源的已有及未来版本，并只能执行对应 Artifact Family Access
Profile 明确授予的 action。Handoff receiver 可以通过 Handoff resolver 检查所选 Revision 明确引用的 evidence，并对该
Revision 留下 Receipt；逻辑 Memory 或 Artifact grant 不自动开放同一 scope、聚合搜索/列表、其他逻辑资源或
lineage 中引用的资源。Skill 的读取、发布到一个 target，以及宿主最终加载或执行是彼此独立的授权边界。`accepted` Receipt、Artifact
approval、Prompt read 或 Skill publication 都不会授予工具、网络、文件系统、模型 Provider 或凭据权限。

PowerContext 定义稳定的授权 request/decision、内置角色、Access API 和 OpenAPI extension，但不绑定一个策略引擎。
当前实现提供内置 Role Binding Store、可写的 embedded Casbin adapter，以及兼容 OpenID AuthZEN Authorization API 的
decision-only PDP adapter。OpenFGA、OPA 和 Cerbos 仍是未来可接入的 adapter。

# Motivation

PowerContext 已经拥有临时 Prepared Handoff、不可变 Handoff Revision、Continue、Receipt 和 Task Outcome，也拥有
Memory Entry Version、approved Experience/managed Skill Revision 和 host-local Skill projection；但现有 Server 认证是
可选的全局静态 Bearer。一个有效 token 可以访问所有受保护 operation，Server 无法表达：

- A 可以管理 Workstream，而 B 只能看一份交接；
- B 可以确认接收，但不能提交新的里程碑；
- 团队成员可以查看 Handoff Report，但不能审批 Experience 或 Skill；
- B 可以读取一条被分享 Memory Entry 的各版本，但不能搜索整个 scope 或读取其他 Entry；
- B 可以读取一个 approved Experience 或 managed Skill 的各 Revision，但不能评审 Candidate；
- 发布者可以发布选定的 managed Skill Revision，但不能借此修改源资源或获得宿主执行权限；
- 有效 Handoff Binding 覆盖后续 Revision，而被撤销的接收方之后不能读取任何 Revision；
- HTTP、MCP 和 Dashboard 对同一个 Principal 得到相同判定。

RFC 0048 要求接收方能够读取 Handoff 所属 scope 及其 evidence。直接把 B 加入整个 scope 虽然满足该要求，却会暴露
与这次交接无关的 Memory、Source 和历史。只把 Handoff 正文复制给 B 又会丢失 exact evidence、Receipt 和撤销能力。

RFC 1223 中 `acknowledge_handoff` 的 authorization check 是接收方对实时环境的观察。它用于判断“当前是否具备继续
条件”，不认证 B 的身份，也不是 ACL。自然语言里的 `receiver`、`authorization_notes` 或 “请继续执行”同样不能
成为权限凭据。

因此，Handoff 和其他可共享资源需要一个独立于内容和 Runtime domain API 的授权层。这个层必须同时支持最小权限分享、
团队角色、外部 PDP、列表过滤、审计和 fail-closed 行为，而不能让 Agent、请求 body 或 `scope_id` 自行决定权限。

# Guide-level explanation

## 建立直觉：交接内容和交接权限是两件事

Handoff 回答“工作到了哪里”；Access Binding 回答“谁现在可以对这份交接做什么”。两者具有不同生命周期：

```text
Prepared Handoff -> Commit -> logical Handoff -> immutable Revisions
                                  |
                                  +-> Access Binding for user B
                                           |
                              read any Revision / inspect / acknowledge
                                           |
                                    expire or revoke
```

第一次提交 Handoff 不会自动分享；逻辑 Handoff Binding 建立后，同一 Handoff 的后续不可变 Revision 无需替换 Binding 即可
访问。分享不修改 Handoff 内容或 Revision，撤销 Binding 不删除 Handoff、Receipt 或审计事件。

## 同一 Access Plane，Artifact Family 驱动的 Profile

Access Control 核心只回答“当前 Principal 是否可以对这个逻辑资源执行这个 action”。Resource Kind 定义授权对象的结构；
Artifact Family Access Profile 定义一种内容的授权语义：

```text
Protected Resource
├── server
├── scope
├── artifact
│   ├── family=handoff
│   ├── family=memory
│   ├── family=experience
│   ├── family=skill
│   └── family=prompt（保留但禁用）
```

每一种 Artifact Family Access Profile 必须固定回答以下问题：

| Family profile contract | 必须定义的内容 |
| --- | --- |
| share unit | 跨版本分享哪个逻辑 identity 或 Family-owned 逻辑 selector |
| shareable state | committed、approved、retained 等哪些 lifecycle state 可以创建 Binding |
| parent | scope 或 server 级角色如何单向蕴含子资源 action |
| actions | 读取、使用、确认、发布和管理分别使用什么稳定 action |
| grantable roles | 哪些固定角色可以绑定到该资源，以及谁可以创建这些 Binding |
| resolution | 哪些 operation 可以从已验证 request 确定资源，不得在授权前读取什么 |
| listing | 逻辑 grant 如何被发现，以及哪些聚合列表仍要求 scope 或 server 权限 |
| transitivity | 读取资源是否同时允许读取 lineage、citation 或其他关联资源 |

所有 Family 复用同一个 `/v1/access/*` API，不增加 `/memory/share`、`/experience/share`、`/skill/share` 或
`/prompt/share` 等平行授权接口。新增 Family 必须显式注册；只复用 `artifact.read` 的逻辑资源 Family 不需要增加新的
ResourceRef variant。若 Family 引入新的 semantic action、selector 或 role，则必须同步 OpenAPI、固定 action/role
vocabulary、Server-owned resolver、Provider conformance vector 和生成的 transport artifact。未知 Family 默认不可分享。

资源可读、进入上下文和获得外部执行能力是三个不同平面：

```text
Access Plane:      Principal 可以跨版本读取、写入或分享哪个逻辑 resource
Context Plane:     哪些已授权内容经显式选择进入有界 PreparedContext
Execution Plane:   宿主是否安装、加载或执行 Skill/Prompt，以及能使用哪些工具和凭据
```

一个 allow decision 不能跨平面传播。逻辑 Memory 或 Artifact grant 不会让内容自动进入普通 scope recall；接收方
先在 “Shared with me” 视图发现资源，再显式读取、附加到当前任务或 fork 到自己可贡献的 scope。共享内容继续视为
`untrusted_history` 或不可信 instruction，Context builder 和宿主仍执行各自的预算、优先级、approval 与 sandbox policy。

## A 把一个逻辑 Handoff 交给 B

假设 A 负责 `project:payments` Workstream，并已完成一份交接。正常流程如下：

1. A 检查并提交 Prepared Handoff，得到不可变 `ArtifactReference`：

   ```json
   {
     "family": "handoff",
     "artifact_id": "handoff",
     "revision": 12
   }
   ```

2. A 明确选择接收方 B。Dashboard 或集成层把 B 从企业身份目录解析为可信的 canonical Principal；模型输出、显示名或
   邮箱文本不能替代该解析。
3. Server 检查 A 对 `project:payments` 是否拥有 `scope.delegate`。
4. Server 验证所选 Revision 属于已提交 Handoff，再为该逻辑 Handoff 创建角色为 `handoff.receiver` 的 Access Binding，
   可选设置过期时间。
5. B 使用自己的凭据登录。`resources/list` 返回 B 有权读取的逻辑 Handoff，B 不需要知道 A 的 token，也不接收新的
   bearer share link。
6. B 使用 exact 或 latest selection 调用 Continue。Server 读取所选 Revision，并只解析它明确引用的 evidence；同一逻辑
   Handoff 的已有和未来 Revision 共用一个 Binding。
7. B 检查当前 workspace、能力和授权状态后，可以对同一 Revision 留下 `accepted`、`needs_clarification` 或
   `declined` Receipt。

创建 Binding 的请求示例为：

```json
{
  "subject": {
    "type": "user",
    "id": "00u-bob"
  },
  "resource": {
    "type": "artifact",
    "scope_id": "project:payments",
    "identity": {
      "family": "handoff",
      "artifact_id": "handoff"
    },
    "selector": null
  },
  "role": "handoff.receiver",
  "expires_at": "2026-09-06T12:00:00Z",
  "reason": "Continue the payment retry investigation",
  "idempotency_key": "transfer-payments-12-to-bob"
}
```

`granted_by`、创建时间和 policy revision 由 Server 填充，调用方不能伪造。

## B 能看到什么

`handoff.receiver` 是逻辑资源角色，不是 scope role：

| 操作 | 结果 | 原因 |
| --- | --- | --- |
| 读取 Handoff Revision 12 | 允许 | Binding 指向该逻辑 Handoff |
| 通过 Continue 检查 Revision 12 的引用 | 允许 | `handoff.evidence.inspect` 只覆盖所选 Revision 的不可变 citation manifest |
| Acknowledge Revision 12 | 允许 | receiver 可以为已检查的所选 Handoff Revision 留 Receipt |
| 请求 `latest` | 允许 | latest 只在已绑定的同一逻辑 Handoff 内解析 |
| 读取 Revision 11 或 13 | 存在时允许 | 同一逻辑 Handoff 的历史和未来 Revision 共用一个 Access identity |
| 打开聚合 Handoff Report | 拒绝 | Report 包含 scope 级历史和统计 |
| 搜索 scope Memory 或列出 Source | 拒绝 | Handoff Binding 不授予通用 scope read |
| Commit 新 Handoff 或记录 Task Outcome | 拒绝 | 需要 `scope.contribute` |
| 审批 Candidate | 拒绝 | 需要独立的 `scope.review` |

Evidence 的最小权限不是逐条复制 Source 或 Memory，也不是让外部 PDP 保存全部 citation。Server 先从已验证请求构造
逻辑 Handoff `ArtifactResourceRef`，同时检查 B 的 `artifact.read` 和 `handoff.evidence.inspect`；只有两个 decision 都允许后，
才能选择不可变 Handoff Revision、取得 citation manifest，并通过 Handoff resolver 解引用其中的 exact citation。manifest
是有界的传递授权边：B 不能把任意 Source、Memory 或 Artifact ID 填入通用读取 API 来复用这项权限。

如果一条 citation 已被删除、retire、损坏或无法解析，Continue 把对应 evidence 标记为 unavailable。Handoff Binding
不覆盖 retention、legal hold 或数据分类策略。

## 分享其他 Artifact Family

其他 Artifact Family 使用相同的逻辑分享流程，但不会继承 Handoff 的 evidence 和 Receipt 语义：

1. A 选择一个已持久化版本来标识可授权资源；Server 把 Memory 归一化为逻辑 `entry_id` selector，把其他 Artifact
   归一化为 `{family, artifact_id}`，Revision 不进入 Binding。
2. Server 先检查 A 是否可以在该资源所属 scope 创建对应 Binding，再验证资源存在且处于可分享状态。
3. B 通过 `access/resources/list` 发现逻辑 resource，并使用自己的 Principal 读取它的已有或未来版本。
4. B 若要创建派生内容，需要在自己拥有 `scope.contribute` 的 scope 中提出新的 Artifact；新逻辑 identity 归 B 所有，
   原资源和 Binding 不被修改。

首版逻辑 grant 的行为如下：

| Family role | 允许 | 不允许 |
| --- | --- | --- |
| `artifact.viewer` on `family=memory` selector | exact get 同一 `entry_id` 的任一版本 | search、list、changes、revise、retire、其他 entry |
| `artifact.viewer` | exact get 同一 Experience 或 managed Skill identity 的任一 approved Revision | Candidate read/review、publication、其他 Artifact、lineage body |

保留的 `prompt` Profile 在 `enabled=false` 时不能创建 Binding，`prompt.user` 也不会作为 enabled Family 的可用角色返回。
Memory extraction、Experience/Skill generation 和 Handoff generation 使用的内部 prompt 属于 Server
implementation/configuration，不是可分享的 Prompt Artifact。

除 Handoff 的 manifest 范围 evidence resolver 外，逻辑资源响应可以返回 schema 已定义的 lineage/citation identity，但
grant 不向引用目标传递。调用通用 Source、Memory 或 Artifact get operation 仍需对目标资源独立判定；Provider 不得因为
“A references B” 自动创建 `can_read` 继承。

## Viewer Binding 只读，owner 管理演进 identity

在 enforced mode 下，每个 enabled 逻辑 Artifact 只有一个 direct owner。Server 在首次创建 Handoff 或 Memory identity
时建立 owner；Experience/Skill Candidate 创建时记录 proposer 为 proposed owner，批准后再正式建立 ownership。
Ownership 是 Server-managed relation，不是公共 `artifact.owner` Binding，并跨所有已有及未来 Revision 覆盖同一逻辑
identity。

Owner 拥有 `artifact.read`、`artifact.write` 和 `artifact.share`；Handoff owner 还拥有
`handoff.evidence.inspect`。创建下一 Revision、revise/retire Memory、替换已有 Experience/Skill target 或修改 managed
Skill lifecycle 时必须检查 `artifact.write`。Scope role 可以授权 contribution 或 review，但不会让持有者自动成为已有
Artifact 的 owner。

Viewer/receiver Binding 对被绑定内容保持只读。Owner 创建的后续 Revision 会通过逻辑 Binding 对接收方可见，但 Binding
不能授权接收方 revise、retire、replace 或提交下一 Revision。接收方若要创建派生内容，需要对目标 scope 拥有
`scope.contribute`，并创建 ownership 与源资源相互独立的新 identity 或 Candidate。

接收方产生的状态必须与共享原件分离：

| 接收方操作 | 约束 |
| --- | --- |
| acknowledge Handoff | 创建独立 Receipt，不修改 Handoff Revision |
| 提交 feedback 或变更建议 | 创建独立 feedback/change request，不修改共享内容 |
| 发布 managed Skill | 写入 Server 配置目标的 projection/state，不修改源 Skill Revision |
| fork、import 或 copy | 必须对目标 scope 拥有 `scope.contribute`；创建新的 identity 或 Candidate，并保留到原资源的 lineage |

产品界面应使用“查看”“确认接收”“请求变更”“复制到我的 scope”或“发布到配置目标”等动作，不应把逻辑 share
呈现为“编辑共享内容”。持续共同维护需要单独授予 scope role；对于需要 Review 的 Artifact Family，贡献者仍通过 Candidate
和 Review lifecycle 产生新 Revision，而不是原地改写 approved Revision。撤销分享会阻止后续访问，但不能删除接收方已经
看到的内容，也不能自动撤销此前经独立授权创建的 Receipt、projection 或 fork。

## 跨 Scope 发布 Artifact

`POST /v1/artifact-publications` 会把一个精确的 source Artifact Revision 复制成目标 Scope 中的独立新 Artifact。
因此业务请求包含精确 `ArtifactAddress`，但 Access Resource 仍是没有 Revision 的逻辑 `{family, artifact_id}` identity。
Server 在读取或复制内容之前必须同时检查：

```text
source 逻辑 Artifact 上的 artifact.share
目标 Scope 上的 scope.admin
```

这样授权可以覆盖 source 的历史和后续 Revision，同时每次 publication 仍保留精确 provenance。复制成功后，Server 在
返回成功前把执行 publication 的 Principal 建立为新 target identity 的 direct owner；target 不继承 source 的 Binding
或 owner。相同 publication 重试会幂等修复缺失的 target owner relation，冲突 owner 则 fail closed。Binding 本身不会
复制内容，publication 也不会授予 host path、工具、网络或 credential。具体 Artifact Family 能否完整复制仍由 Runtime
决定；不支持的 complete-state copy 会在授权后失败，但不会放宽 Access 模型。

## B 真正接手 Workstream

查看交接不等于获得执行权。若 B 将长期推进该 Workstream，A 或管理员需要另行授予 `scope.contributor`：

```text
handoff.receiver
  = read one logical Handoff across Revisions + inspect selected manifest citations + acknowledge a selected Revision

scope.contributor
  = read the Workstream + contribute Sources + prepare/commit Handoffs
    + acknowledge Handoffs + record Task Outcomes
```

PowerContext 权限只控制 PowerContext 资源和 operation。修改 Git 仓库、调用云 API、访问生产环境或读取凭据仍由宿主、
操作系统和外部服务授权。Handoff、Role Binding 和 Receipt 都不能扩大这些权限。

## 长期团队协作

对固定团队，可以把用户或外部 group 绑定为 scope role，而不是为每个 Revision 创建 Binding：

- `scope.viewer`：读取当前 scope 的 Handoff、Memory、approved Artifact、Source 和只读投影；
- `scope.contributor`：在 viewer 基础上写入工作 evidence、Memory contribution、Handoff 和 Outcome，并提出 Artifact
  Candidate；
- `scope.reviewer`：在 viewer 基础上评审 Artifact Candidate；
- `scope.delegator`：在 viewer 基础上把逻辑 Handoff 分享给接收方；
- `scope.admin`：管理该 scope 的角色和策略，并可授权 Artifact 分享，但它本身不是内容 read/write role。

`scope.delegate` 只允许为 `family=handoff` Artifact 创建 viewer/receiver Binding。Artifact direct owner 也可通过
`artifact.share` 创建或撤销其 resource Binding。其他 enabled Family 的 resource Binding 可由 owner、`scope.admin` 或
`server.admin` 管理；已有 Handoff delegator 不会因此获得更宽的分享边界。`server.admin` 管理 server/scope policy，但
不会隐式获得 `server.observe`、`scope.read` 或 `artifact.write`；legacy static Principal 为兼容性会另外得到 observer 和
per-scope working role。

固定角色是 wire-contract vocabulary，不要求外部 PDP 使用相同内部存储。外部系统可以把企业角色、团队或关系映射为
这些 action。

## 撤销和过期

A、相应 grant administrator 或 scope admin 可以撤销其管理边界内的逻辑 Artifact Binding。对于 Handoff，撤销后：

- B 的后续 read、Continue 和 acknowledge 返回 403；
- B 不再从 `resources/list` 看到该 Handoff；
- 已保存的 Handoff、Receipt 和 Access Audit 不被删除；
- 已经展示、导出或复制给 B 的内容无法被远程收回。

过期时间由 PDP 使用可信 Server time 判断。Adapter 不支持条件或 expiration 时必须拒绝创建带过期时间的 Binding，
不能静默创建永久授权。

角色变更使用 revoke + create，不原地把 `handoff.viewer` 升级为 `handoff.receiver`。撤销使用 `expected_version`，并发
修改返回 409。

## 授权服务不可用

授权是安全依赖。配置为 enforced mode 时：

- 没有或无法验证身份返回 401；
- 身份有效但权限不足返回 403；
- PDP、Binding Store 或安全资源过滤不可用返回 503；
- Server 不会因为 PDP 故障而回退到全局 token、空 Principal 或 allow-all；
- `/health/live` 仍反映进程存活，`/health/ready` 报告 required authorization dependency 未就绪。

403 不区分“资源不存在”和“资源存在但不可见”。只有通过授权后，Repository 才可以返回 404，避免资源枚举。

# Reference-level explanation

## Goals and non-goals

本 RFC 的目标是：

- 在 HTTP、MCP 和 Dashboard 前建立同一个 Server PEP；
- 从认证凭据建立不可由请求覆盖的 Principal；
- 支持 scope 级 RBAC 和逻辑 Handoff receiver Binding；
- 定义稳定 Resource Kind 和 Artifact Family Access Profile contract，规范 Handoff、Memory、Experience 和 Skill 的逻辑
  资源授权，并保留 disabled Prompt vocabulary；
- 允许安全解引用已授权 Handoff 所选 Revision 引用的 evidence，而不开放整个 scope；
- 区分资源读取、上下文选择、Skill 发布与宿主执行权限；
- 提供可替换的判定接口和可选的关系写入接口；
- 提供自助检查、资源发现、Binding 管理和审计 API；
- 对直接读取、列表、分页、内部 MCP bridge 和后台 operation fail closed；
- 保留当前 Runtime、Source、Memory、Handoff 和 Work application API 的领域纯度。

本 RFC 不定义：

- 用户注册、密码、MFA、OIDC Provider 或 token issuance；
- 自定义 role DSL、wildcard scope、组织层级或 group directory；
- 匿名 bearer share link 或把授权嵌入 Handoff 内容；
- Git、文件系统、工具、网络、模型 Provider 或凭据授权；
- 数据脱敏、cross-organization export、legal hold 或 retention policy；
- 审批工作流、临时提权流程或 Agent 自动请求更高权限；
- 把 PowerContext 改造成通用 IAM 产品；
- 对 shared logical resource 进行 multi-writer collaborative editing，或通过 Binding 转移 ownership；
- 成员会动态变化的 Memory collection 或 Artifact catalog 订阅分享；
- Prompt Artifact 的内容 schema、变量语言、Review lifecycle 或宿主 instruction-priority policy；
- per-target publication delegation 或通用 `execution_target` Resource；
- 由独立 lifecycle RFC 定义的 remote managed Skill Receiver distribution contract；
- External Skill 的跨主机 locator、自动安装或 package distribution contract。

## Trust model and invariants

实现必须维持以下不变量：

1. `scope_id` 是业务分区值，不是授权证明。
2. Principal 只来自认证 middleware 或可信 internal bridge context。
3. 请求 body 中的 `receiver`、`subject`、`actor`、role text 或 Handoff 自然语言不能替换当前 Principal。
4. Handoff、Memory 和 Artifact 内容是 `untrusted_history` 或不可信 instruction，不能授予 action。
5. `is_internal_bridge()` 只能跳过重复 transport authentication，不能跳过 authorization。
6. 每个受保护的 operation 在访问 Repository 或 application service 前完成判定。
7. 逻辑 Handoff grant 允许对同一 Artifact 的已有和未来 Revision 使用 exact/latest selection，但不开放其他 Handoff 或
   父 scope collection。
8. `accepted` Receipt 不创建、更新或继承 Access Binding。
9. 模型可以建议接收方或解释拒绝原因，但不能自行确定 canonical Principal 或调用 allow-all fallback。
10. Memory Entry grant 由逻辑 `family=memory` Artifact identity 和仅含 `entry_id` 的 `memory_entry` selector 组成；其他
    Artifact grant 只含 `{family, artifact_id}`。业务请求可以选择正整数 Revision 或 version，但这些字段永远不进入 Access
    Resource 或 Binding。Server 只从 `identity.family` 派生 Access Profile；独立 content profile、未知 Family 或
    selector mismatch 必须拒绝。
11. 读取 Memory 或 Artifact 不自动授予其 lineage/citation target，也不自动进入 PreparedContext。
12. Logical-resource Binding 本身不授予 revise、retire、replace、提交下一 Revision 或其他修改共享内容的 operation；
    Receipt、feedback、projection 和 fork 是独立资源或 operation，必须分别授权，并且不能修改原资源的 identity、content
    或 Revision。
13. 每个 enabled 逻辑 Artifact 只有一条 immutable direct owner relation。公共 Binding 不能创建、替换或转移
    `artifact.owner`；owner 缺失时，Artifact authorization 必须 fail closed。
14. Host-local Skill projection 必须在解析 `target_id` 或检查文件系统前同时通过 `server.observe` 和 `artifact.read`；remote
    target 管理要求 `scope.admin`，remote publication 还要求 `artifact.read`。这些 operation 都不授予宿主执行、工具、
    网络、文件系统或 secret 权限。
15. Public error、log、metric 和 trace 不包含 credential、Handoff/Memory/Artifact 正文、Source body、target locator
    或 PDP 原始响应。

## Principal model

`PrincipalRef` 使用认证 Provider 给出的稳定 opaque identity：

```json
{
  "type": "user",
  "id": "00u-bob"
}
```

字段语义如下：

| Field | Semantics |
| --- | --- |
| `type` | `user` 或 `service` |
| `id` | deployment 范围内稳定的 opaque subject，不使用显示名或 email |
| `description` | 可选显示信息，不参与 identity equality 或 policy key |

需要 issuer namespace 时，由 Authentication Provider 把它归一化进 deployment-wide opaque `id`；`issuer` 不是公共
`PrincipalRef` 字段。Agent 名称、host、session ID 和模型名称属于 provenance，不默认成为 Principal。若企业 token 明确
证明 on-behalf-of actor，认证 adapter 可以在可信 request context 中附加 `actor`；PDP 可以同时约束 subject 和 actor。
客户端不能通过 JSON body 声明该 actor。

现有 Handoff Receipt 的 `receiver` 字段继续作为记录内容。Server 另外记录产生 Receipt 的 authenticated Principal，
两者不一致时拒绝 `accepted`；非 accepted Receipt 可以保留自报 receiver，但必须返回
`receipt_identity.principal` 和 `receipt_identity.receiver_identity_matches=false`。该身份记录复用现有
`pc_access_audit` 表，以 `handoff.receipt.identity` 操作的不可变审计事件持久化，不新增表或字段。事件 ID 由操作名、
Scope ID 和 Source ID 的无歧义编码计算得到，并通过现有唯一约束保证同一 Source 的归属不能被并发请求或重试覆盖。
该事件表示服务器已锁定回执提交者身份，不代表回执正文已写入或工作已完成；它在 Source 捕获前写入，并与 Receipt
一起保留，不能作为普通短期日志清理。身份信息随 acknowledge 响应及 Receipt Source 的 GET 响应返回，不改写 Source
正文或 content digest。缺少该身份记录的 Receipt 读取返回 503。绝不能把自由文本 `receiver` 当作 Principal。

## Resource model

内部授权 request 使用结构化 `ResourceRef`，避免把包含 `:`、`/` 或用户数据的标识直接拼成策略字符串：

| Resource Kind | Identity | Parent |
| --- | --- | --- |
| `server` | deployment identifier | none |
| `scope` | exact `scope_id` | server |
| `artifact` | 逻辑 `{family, artifact_id}`、可选 Family-owned 逻辑 selector 和 `scope_id` | scope |

`ResourceRef` 是 OpenAPI discriminated union。每个 variant 使用 `additionalProperties: false`，并且只接受下表字段：

| `type` | Required identity fields |
| --- | --- |
| `server` | `deployment_id` |
| `scope` | `scope_id` |
| `artifact` | `scope_id`, `identity`, and optional `selector` |

普通逻辑 Artifact 不包含 selector 或 Revision：

```json
{
  "type": "artifact",
  "scope_id": "project:payments",
  "identity": {"family": "experience", "artifact_id": "exp-retry-budget"},
  "selector": null
}
```

Memory Entry 使用 `memory` Family 拥有的逻辑 selector。`entry_version_id` 与底层 Memory Artifact Revision 保留在业务
citation 中，不进入 Access Resource：

```json
{
  "type": "artifact",
  "scope_id": "project:payments",
  "identity": {"family": "memory", "artifact_id": "memory"},
  "selector": {
    "type": "memory_entry",
    "entry_id": "retry-policy"
  }
}
```

`ArtifactResourceRef.identity.family` 是唯一的 Artifact Family Access Profile discriminator。请求不包含独立 `profile`
字段；Server 从已验证的逻辑 identity 派生 Profile，避免 `profile=prompt` 与 `family=skill` 等不一致组合。
每个 Family 声明 selector 为 required、forbidden 或某个固定 discriminated union variant。当前 `memory` 要求
`memory_entry` selector；`handoff`、`experience`、`skill` 和 disabled `prompt` Profile 禁止 selector。

Family registry 是 Server-owned 固定 contract，不是管理员可编辑的 policy DSL。每个注册项至少包含：

| Field | Requirement |
| --- | --- |
| `family` | 与 `ArtifactReference.family` 完全匹配的稳定名称 |
| `share_unit` | `artifact` 或一个明确的 Family-owned 逻辑 selector type |
| `shareable_states` | 允许创建 Binding 的 lifecycle state |
| `base_action` | 首版统一为 `artifact.read` |
| `additional_actions` | Family 特有的读取侧或 acknowledge action |
| `grantable_roles` | 与该 Family 兼容的固定逻辑资源 roles |
| `mutation_semantics` | 由 `artifact.write` 表达的 owner-only mutation |
| `parent_implications` | scope role 可以单向蕴含哪些 child action |
| `transitivity` | lineage、citation 或其他关联资源是否需要独立判定；未声明时为 none |
| `resolver` | 逻辑授权后如何解析所选业务版本，以及返回什么安全 identity |

当前 registry 为：

| Artifact Family | Enabled | Share unit | Shareable state | Family actions | Grantable resource roles |
| --- | --- | --- | --- | --- | --- |
| `handoff` | yes | 逻辑 Artifact | 至少一个 committed Revision | `artifact.read`, `handoff.evidence.inspect`, `handoff.acknowledge` | `handoff.viewer`, `handoff.receiver` |
| `memory` | yes | 逻辑 `memory_entry` selector | active 或 retired Entry 存在 | `artifact.read` | `artifact.viewer` |
| `experience` | yes | 逻辑 Artifact | 至少一个 approved Revision | `artifact.read` | `artifact.viewer` |
| `skill` | yes | 逻辑 Artifact | 至少一个 approved Revision | `artifact.read` | `artifact.viewer` |
| `prompt` | no | 逻辑 Artifact | reserved | 保留 `artifact.read`, `prompt.use` vocabulary | none |

每个 enabled row 还允许 direct owner 执行 `artifact.write`，并允许 owner 或 administrator 通过
`artifact.share` 管理分享。两者都不会变成 viewer action，也不能作为独立 resource Binding 授予。角色发现会把
`artifact.owner` 报告为 one-per-resource、system-managed role；owner relation 只能由 Server 业务流程建立。

Prepared Handoff 没有持久化 identity，不能创建 Access Binding。跨用户最小权限分享必须先 commit；pending/rejected
Candidate 同样不能创建 Artifact Binding。普通新 Family 即使只复用 `artifact.read`，也必须先显式注册为 shareable；
未知、disabled 或 selector 不匹配的 Family 默认拒绝。`revision`、`entry_version_id`、Memory current head 和 search query
都不是授权身份。后续 Artifact Revision 和 Memory Entry Version 由同一逻辑 Binding 覆盖；聚合发现仍要求 scope 权限。

每个 Resource Kind 都定义稳定的 canonical serialization 供 adapter 建立 object ID。Artifact key 必须包含 `scope_id`、
`family`、`artifact_id` 和存在时的逻辑 selector；相同业务身份在 HTTP、MCP 和 Dashboard 必须得到同一个 key。
不同 Family 或 selector 不得因字符串碰撞共享 Binding。

Adapter 负责把结构化 ResourceRef 映射成外部 PDP object ID。映射必须 canonical、可逆或稳定，并避免把 email、token、
资源正文、发布 target locator 或其他 PII 写入 Casbin policy、OpenFGA tuple 或 audit key。

### Artifact ownership

Server 把 ownership 与普通 `AccessBinding` 分开保存。`ArtifactOwnerRelation` 包含逻辑 resource、唯一
`PrincipalRef`、可信创建时间、policy revision 和 idempotency key，刻意不包含 Artifact Revision。Owner relation
不可变；只有相同 owner 和 key 的重复建立才是幂等操作，不同 owner 返回 conflict。本 RFC 不定义 ownership transfer。

在 enforced mode 下，owner relation 建立前的 Artifact authorization 以 `artifact_owner_pending` fail closed。新 Memory
Entry 和首次 Handoff commit 由创建者拥有。新 Experience/Skill Candidate 记录 Server-side proposed-owner attestation，
批准后再建立 ownership；以已有 identity 为 target 的 Candidate 必须保留原 owner。跨 Scope publication 的新 target
identity 归 publisher 所有。

首版假设 deployment 在持久化第一个 Artifact 前就已启用 enforced mode。它不会为 access control disabled 期间已写入的
catalog 回填或推断 owner，也不提供通用 owner repair 流程。未经过独立 operator migration 就把这类 catalog 切换到
enforced mode 时，其中的 Artifact 按设计保持不可用。若 domain persistence 已成功但 owner establishment 失败，请求仍
fail closed；只有明确支持幂等重放的业务流程才能通过重试修复 relation。通用 transactional outbox 和 operator recovery
流程属于本 RFC 之外的未来工作。

### 内置关系型存储

内置 Provider 和嵌入式 Casbin Provider 使用五张 Server-owned Access 表：

| 表 | 用途 |
| --- | --- |
| `pc_access_relationships` | 角色 Binding、历史状态和单例角色的唯一占用键 |
| `pc_access_owners` | 不可变 Artifact 所有权和 Candidate 拟定归属凭证 |
| `pc_access_relationship_heads` | 已提交的授权版本号 |
| `pc_access_idempotency` | Binding 变更的请求指纹和幂等重放结果 |
| `pc_access_audit` | 权限审计事件和可信 Handoff Receipt 身份凭证 |

`pc_access_owners.owner_kind` 区分 `artifact` 和 `candidate`。Candidate 凭证不建立 Artifact 所有权、
不授予权限，也不出现在 owned-resource discovery 中。批准时独立建立 Artifact owner 记录，同时保留原始
Candidate 凭证。身份键保持 Scope、Family 和 Memory Entry 的隔离；Candidate ID 在同一 Scope 内跨 Family 唯一。

单例 Binding 通过 `pc_access_relationships` 中可空且唯一的 `singleton_key` 占用角色；普通 Binding 的该字段为
空。撤销释放占用键；替换在同一事务中释放旧键并插入后继 Binding。新授权可以回收已过期的键，但保留旧 Binding
及其幂等重放记录。数据库唯一约束保证并发竞争不会产生多个接收者。Binding 变更先锁定授权版本，再锁定 Binding
行，过期比较使用 UTC 时间。占用键被回收的过期 Binding 不允许再被替换；撤销它也不会释放当前接收者的占用键。

## Action vocabulary

首版 action 是稳定、小写、点分隔的字符串：

| Action | Resource | Meaning |
| --- | --- | --- |
| `server.observe` | server | 读取服务级运行状态和观测数据 |
| `server.admin` | server | 管理 deployment access configuration 和 publication target configuration |
| `scope.read` | scope | 读取该 Workstream 的通用只读资源、approved content 和投影 |
| `scope.contribute` | scope | 创建 Source、新 Memory/Handoff 内容、Outcome 和 Artifact Candidate |
| `scope.review` | scope | 评审该 scope 的 Artifact Candidate |
| `scope.delegate` | scope | 为逻辑 Handoff 创建 viewer 或 receiver Binding |
| `scope.admin` | scope | 管理该 scope 的角色、Binding 和 policy |
| `artifact.read` | logical artifact | 读取 Family Profile 定义的 identity 或 selector 的已有和未来版本 |
| `artifact.write` | logical artifact | 通过 Family lifecycle 修改由 owner 控制的逻辑 identity |
| `artifact.share` | logical artifact | 管理 viewer/receiver Binding，或从逻辑 source identity 发布一个精确 Revision |
| `handoff.evidence.inspect` | `family=handoff` artifact | 通过 Handoff resolver 解引用所选 Revision 的 citation manifest |
| `handoff.acknowledge` | `family=handoff` artifact | 对所选 Revision 创建 Handoff Receipt |
| `prompt.use` | `family=prompt` artifact | 保留 action；Prompt Profile 禁用时不可使用 |

`artifact.read` 的含义在所有 enabled Family 中保持固定：只读取 Binding 标识的逻辑 identity 或 selector 的各版本。它
不自动包含 Handoff evidence、lineage body、write 或 share。只有确实具有不同安全效果的 Family operation 才新增
semantic action。

业务 operation 检查 action，不检查 role name。这样可以调整外部角色或关系模型，而不改 application code。

内置 parent implication 刻意保持收敛：`scope.viewer`、`scope.reviewer` 和 `scope.delegator` 对 child 蕴含
`artifact.read` 与 Handoff evidence inspect；`scope.contributor` 还蕴含 Handoff acknowledge。`scope.admin` 和
`server.admin` 蕴含 `artifact.share`，`server.admin` 还蕴含 `scope.admin`。管理权限不会隐式授予内容 read/write；反向
蕴含也不成立，resource viewer 或 owner 不会获得 scope role。

## Built-in roles

| Role | Granted actions |
| --- | --- |
| `handoff.viewer` | `artifact.read`, `handoff.evidence.inspect` on one logical `family=handoff` Artifact |
| `handoff.receiver` | viewer actions plus `handoff.acknowledge` on one logical Handoff |
| `artifact.viewer` | `artifact.read` on one compatible logical Artifact or selector |
| `prompt.user` | reserved role；`family=prompt` disabled 时不可使用 |
| `artifact.owner` | 对一个逻辑 Artifact 执行 `artifact.read`、`artifact.write`、`artifact.share` 和 Handoff evidence inspect；system-managed |
| `scope.viewer` | `scope.read` |
| `scope.contributor` | `scope.read`, `scope.contribute` |
| `scope.reviewer` | `scope.read`, `scope.review` |
| `scope.delegator` | `scope.read`, `scope.delegate` |
| `scope.admin` | `scope.admin`；只对 child Artifact 蕴含 `artifact.share` |
| `server.observer` | `server.observe` |
| `server.admin` | `server.admin`；蕴含 `scope.admin` 和 `artifact.share`，但不蕴含 read/write |

`handoff.receiver` 和 `artifact.owner` 的 cardinality 是 `one_per_resource`，其他 role 均为
`many_per_resource`。Owner 为 system-managed；receiver/owner subject 必须是 user 或 service。其他公共 role schema 也
允许 group subject，但 built-in/Casbin composition 当前报告 `group_subjects=false`，在配置可信 group resolver 前拒绝创建
group Binding。

所有可通过公共 API 授予的 resource role 对其绑定内容都是只读的；`handoff.receiver` 只额外允许创建独立 Receipt。
修改原资源必须同时满足 system-managed owner relation 和对应领域 lifecycle。

首版不允许通过公共 API 创建新 role 或修改 role-to-action mapping。固定角色让 OpenAPI、Dashboard 和 adapter
conformance test 拥有稳定语义；企业 PDP 可以在外部把自定义组织角色映射为这些 action。

拥有 `scope.delegate` 的 Principal 只能创建 `handoff.viewer` 或 `handoff.receiver`，且只能针对该 scope 中已经存在的
逻辑 Handoff。创建 scope role 需要 `scope.admin`；创建 `server.admin` 需要现有 `server.admin` 和 deployment policy
允许。任何 Principal 都不能授予自己高于调用方管理边界的权限。

Artifact owner 或 `scope.admin` 可以创建兼容的 viewer Binding；`server.admin` 继承该管理边界。`artifact.viewer` 只能
绑定到 enabled Family Profile 声明兼容的逻辑 Artifact 或 selector。公共 `artifact.owner` Binding 和 disabled
`family=prompt` 的所有 Binding 都必须拒绝。Role 与 Artifact Family Access Profile 或 Resource Kind 不匹配时返回
422，授权不足时返回 403；Server 不能把不匹配的 role text 原样交给外部 RelationshipWriter。

| Resource or Artifact Family Profile | Grantable resource roles | Binding administrator |
| --- | --- | --- |
| `artifact` with `family=handoff` | `handoff.viewer`, `handoff.receiver` | owner、`scope.delegate`、`scope.admin` 或 `server.admin` |
| `artifact` with `family=memory` and `memory_entry` selector | `artifact.viewer` | owner、`scope.admin` 或 `server.admin` |
| `artifact` with `family=experience` | `artifact.viewer` | owner、`scope.admin` 或 `server.admin` |
| `artifact` with `family=skill` | `artifact.viewer` | owner、`scope.admin` 或 `server.admin` |
| disabled `family=prompt` | none | none |

## Authorization request and decision

PowerContext 的判定模型与 OpenID AuthZEN Authorization API 的 subject、action、resource、context 形状对齐，但
Python protocol 不要求 PDP 使用 HTTP：

```python
class AuthorizationProvider(Protocol):
    async def check(self, request: AccessRequest, /) -> AccessDecision: ...

    async def check_batch(
        self,
        requests: Sequence[AccessRequest],
        /,
    ) -> Sequence[AccessDecision]: ...

    async def resolve_resource_filter(
        self,
        request: ResourceSearchRequest,
        /,
    ) -> AuthorizedResourceFilter: ...
```

规范化 request 示例：

```json
{
  "subject": {
    "type": "user",
    "id": "00u-bob"
  },
  "action": {"name": "artifact.read"},
  "resource": {
    "type": "artifact",
    "scope_id": "project:payments",
    "identity": {
      "family": "handoff",
      "artifact_id": "handoff"
    },
    "selector": null
  },
  "context": {
    "request_id": "pc-01K...",
    "transport": "mcp",
    "operation": "continue_handoff"
  }
}
```

`AccessDecision` 至少包含：

```json
{
  "allowed": true,
  "reason_code": "role-binding",
  "policy_revision": "42"
}
```

`reason_code` 是稳定、低敏感度枚举，用于 audit 和诊断；business 403 response 不返回 provider rule、tuple、URL、堆栈或
原始 body。`policy_revision` 允许审计和缓存关联到确定策略，但它不是授权 token。

`check_batch` 必须保持输入顺序，并对每项返回独立决定。Adapter 不能因为一个 allow 而允许整批资源。

一个业务 operation 可以解析出 1..N 个 `ResolvedAccessRequirement`。首版只支持 `all` 组合：PEP 使用一次
`check_batch` 或语义等价的 point checks，并且只有全部 decision 都为 allow 才能调用 Repository、application service、
target adapter 或 filesystem。它不提供 client-authored Boolean policy DSL。

例如跨 Scope Artifact publication 解析为两个有序 requirement：

```json
{
  "match": "all",
  "requirements": [
    {
      "action": "artifact.share",
      "resource": {
        "type": "artifact",
        "scope_id": "project:payments",
        "identity": {"family": "skill", "artifact_id": "retry-runbook"},
        "selector": null
      }
    },
    {
      "action": "scope.admin",
      "resource": {
        "type": "scope",
        "scope_id": "team:runbooks"
      }
    }
  ]
}
```

Source Revision 保留在业务 request 和 publication provenance 中，不进入 Access Resource。Host-local/remote Skill
projection 同样把 `target_id` 保留为 operation parameter，而不是 Access Resource。

“scope role 或 resource role” 这类替代关系不需要 `any` 表达式。PEP 请求 child-resource action，Provider 根据可信 parent
relation 判断 scope role 是否蕴含该 action；逻辑 Binding 则直接作用于 child resource。这样不同 Provider 不必实现任意
嵌套策略表达式。

`resolve_resource_filter` 是安全列表功能的必要能力。`AuthorizedResourceFilter` 是当前 Principal 和 action 专属的
Server-consumable filter，由两类约束组成：逻辑 Binding 产生的有界 canonical resource key，以及父级角色产生的有界
server/scope constraint。父级 constraint 表示“Repository 可以在该 parent、请求的 Resource Kind 和 Family 内查询”，不是
客户端可提交的 wildcard。Filter 还携带 policy revision；Server 必须校验其结构和上限，再把逻辑 resource key 与 parent
constraint 的并集下推到同一次 Repository query，在计算 total、排序和分页前完成过滤。

内置 Provider 可以直接从 Binding Store 产生逻辑 resource key 和 parent constraint，因此不需要镜像整个 Artifact catalog。
外部 Provider 可以返回等价的授权 filter，或由 adapter 根据可信 relationship search 生成。只支持 point check、无法安全
产生该 filter 的 Provider 不得先查询全部 Artifact、Project 或 Scope 再逐项过滤；对应 list operation 应返回 503，或在
配置阶段被判为不具备 `safe_resource_filtering` capability。

## Relationship administration

AuthZEN 定义判定接口，不定义所有 PDP 的关系写入方式。因此管理能力与判定能力分开：

```python
class RelationshipWriter(Protocol):
    async def create_binding(
        self,
        request: CreateAccessBinding,
        /,
    ) -> AccessBinding: ...

    async def revoke_binding(
        self,
        binding_id: str,
        /,
        *,
        expected_version: int,
    ) -> AccessBinding: ...
```

内置 composition 和已包含的 Casbin composition 都把各自的 `AuthorizationProvider` 与 canonical relational Access
repository 提供的 `RelationshipWriter` 配对；Provider class 本身不负责 relationship mutation。外部 decision adapter
也可以提供配套 `RelationshipWriter` 并声明 `relationship_management=true`，因此 receiver 等 Binding 不局限于内置
store。已包含的 AuthZEN adapter 只提供 decision；此时 PowerContext 的 Binding mutation endpoint 明确返回
`relationship_management_unavailable`，管理员通过外部系统配置关系。未来的 OpenFGA、OPA 或 Cerbos adapter 必须
如实声明所实现 capability。Server 不能声称 grant 成功后再只写本地影子记录。

## Access Binding model

内置 Binding Store 至少保存：

| Field | Requirement |
| --- | --- |
| `binding_id` | Server-generated opaque ID |
| `subject` | canonical `PrincipalRef` |
| `resource` | canonical logical `ResourceRef` |
| `role` | one fixed role name |
| `granted_by` | authenticated Principal recorded by Server |
| `reason` | optional bounded human explanation |
| `created_at` | trusted Server time |
| `expires_at` | optional trusted expiration |
| `state` | `active` or `revoked` |
| `version` | monotonically increasing CAS version |
| `policy_revision` | policy version after mutation when available |
| `idempotency_key` | bounded caller key scoped to grantor and resource |

Role、subject 或 resource 变化必须 revoke old + create new。相同 grantor、idempotency key 和相同 payload 的重试返回
原 Binding；同 key 不同 payload 返回 409。过期不删除记录，判定时视为 deny。

Artifact ownership 不是 `AccessBinding`。它保存在单独的 one-per-resource owner relation 中，没有 expiration，也不能
通过 `/v1/access/bindings/*` 创建或转移。

内置 Binding Repository 属于 Server access-control component，不加入 Runtime 的 `context`、`source`、`memory`、
`artifact`、`handoff` 或 `work` application object。它可以与 Server 使用相同数据库部署，但拥有独立 schema、
migration 和 API。

## Public Access API

OpenAPI source of truth 增加以下 operation：

| Operation | Purpose | Authorization |
| --- | --- | --- |
| `GET /v1/access/me` | 返回当前 Principal 和 access-control capability | authenticated Principal |
| `POST /v1/access/check` | 检查当前 Principal 的一个 `all` 或 `any` 复合权限要求 | current Principal only |
| `POST /v1/access/resources/list` | 列出当前 Principal 可访问的资源 identity | current Principal only |
| `POST /v1/access/roles/list` | 返回固定角色及 action vocabulary | authenticated Principal |
| `POST /v1/access/bindings/list` | 列出调用方可管理的 Binding | 按 resource 检查 owner `artifact.share`、`scope.delegate`、`scope.admin` 或 `server.admin` |
| `POST /v1/access/bindings/create` | 创建 Family-compatible logical-resource 或管理级 Binding | resource-specific administration action |
| `POST /v1/access/bindings/revoke` | CAS revoke 一个 Binding | same administration boundary |
| `POST /v1/access/bindings/replace` | 原子撤销不可变 Binding 并创建其后继 Binding | same administration boundary |
| `POST /v1/access/audit/list` | 查询 server/scope 边界内的安全审计事件 | `scope.admin` or `server.admin` |

`check` 和 `resources/list` 不接受 client-specified subject，只检查当前 authenticated Principal，防止普通
用户把 API 当作人员权限枚举器。管理员代查其他 Principal、subject search 和 directory integration 留给后续 RFC。

`bindings/create` 必须接收 recipient subject，因为分享需要指定 B；调用方仍然只能在自己拥有管理权限的 resource 上创建
固定角色。Server 先根据 Resource Kind 和 Artifact Family registry 校验结构与 role compatibility，再执行 grant
administration check，最后才读取 Repository，确认 Artifact 存在、属于声明的 parent 且处于可授权状态。
不存在与不可见的资源对未授权调用方返回相同 403；只有管理判定通过后才能返回 404 或 family-specific conflict。

Access API 不负责创建、修改、fork 或发布业务资源。Memory、Artifact、跨 Scope publication 和 managed Skill projection
继续使用各自 contract；target configuration 与 operator status 属于 Server 或 scope operation。它们都不进入 Access
API，也不创建 target Binding。Binding 只表达谁能对已存在资源执行哪些 action。

公共 `check` 可以用 HTTP 200 返回 `allowed=false`。业务 operation 的相同拒绝返回 403，并且不调用 application
service。Access API 只用于解释和 UI preflight，不能替代业务请求时的实时 enforcement。

## Handoff operation requirements

首版 Handoff 映射如下：

| Operation | Required authorization |
| --- | --- |
| `prepare_handoff`, `finalize_handoff`, `handoff_current_work` | `scope.contribute` on request `scope_id` |
| first `commit_handoff` | `scope.contribute` on request `scope_id`；成功后建立 caller 为 owner |
| later `commit_handoff` with `base` | `scope.contribute` on request `scope_id` and `artifact.write` on logical Handoff |
| `continue_handoff(selection=latest)` | `artifact.read` and `handoff.evidence.inspect` on logical `family=handoff` Artifact, directly or through parent `scope.read` |
| `continue_handoff(selection=exact)` | `artifact.read` and `handoff.evidence.inspect` on logical `family=handoff` Artifact, directly or through parent `scope.read` |
| `continue_handoff(selection=prepared)` | `scope.read` on request `scope_id` |
| `acknowledge_handoff` with exact receipt | `scope.contribute` or `handoff.acknowledge` on the logical Handoff selected by the exact Revision |
| `record_task_outcome` | `scope.contribute` on request `scope_id` |
| Handoff Report with exact Scope selection | 每个 selected Scope 上的 `scope.read`；logical Handoff grant 不足 |
| Handoff Report with non-exact selection | `server.observe` |

receiver 调用 Continue 时，Server 在读取 Revision 前先建立逻辑 Handoff ArtifactResourceRef。`selection=exact` 从请求的
精确 `ArtifactReference` 派生逻辑 identity；`selection=latest` 使用该 scope 注册的逻辑 Handoff identity。授权通过后才
解析所请求的 Revision 及其 manifest。

Prepared Handoff 可以包含由调用方提交的完整内容，因此窄授权模式不接受 `selection=prepared`。只有已经拥有
`scope.read` 的 Principal 才能用 prepared selection 解引用 scope evidence。

## Artifact Family operation requirements

Family operation 映射如下。表中的 “scope or logical resource” 由 Provider 的 parent relation 实现，不让客户端选择绕过路径：

| Operation family | Required authorization |
| --- | --- |
| Memory search/list/changes | `scope.read` on request `scope_id`；logical Memory Entry grant 不足 |
| exact Memory get | `artifact.read` on logical `family=memory` Artifact plus `memory_entry.entry_id`, directly or through parent `scope.read` |
| create Memory Entry | `scope.contribute`；成功后建立 caller 为 owner |
| flush Memory | `scope.contribute` plus `artifact.write` on every existing entry that may change；新 Entry 归 caller 所有 |
| revise/retire one Memory Entry | `artifact.write` on logical `memory_entry` selector |
| approved Experience/managed Skill exact get | `artifact.read` on the logical Artifact identity derived from the exact request, directly or through parent `scope.read` |
| Experience/Skill propose/generate new identity | `scope.contribute`；Server 记录 caller 为 proposed owner |
| Experience/Skill proposal targeting existing identity | `scope.contribute` plus `artifact.write` on that identity |
| Candidate list/get | `scope.read`; logical Artifact grant 不暴露 Candidate |
| Candidate revise | `scope.review`，且 authenticated Principal 必须等于 Server 保存的原提议者 |
| Candidate approve/reject | `scope.review`；approve 还要求有效的 proposed-owner attestation |
| managed Skill lifecycle mutation | `artifact.write` on logical Skill |
| host-local Skill projection status/publish/unpublish | `server.observe` and `artifact.read` on logical Skill |
| remote Skill target administration | `scope.admin` |
| publish Skill Revision to remote target | `scope.admin` and `artifact.read` on logical Skill |
| cross-Scope Artifact publication | `artifact.share` on logical source and `scope.admin` on target Scope |

Exact get resolver 必须从已验证业务 request 派生完整逻辑 identity，并在授权时丢弃 Revision 字段。缺少 scope 和 Family
的 Memory `entry_id` 或 Artifact `artifact_id` 不能单独作为授权 key。Search、aggregated projection 和
Candidate Inbox 仍是 collection operation，不能通过一个逻辑 grant 进入。

Prompt Family Access Profile 只保留 authorization vocabulary。当前部署报告 `prompt.enabled=false`，拒绝
`family=prompt` Binding，也不会把 `prompt.user` 作为 enabled Family 的可用 role 返回。

`target_id` 是 operation parameter，不是授权 key 或 Resource。Host-local target inspection 要求 `server.observe` 加逻辑
Skill read。Remote distribution lifecycle 使用 scope-owned target：管理它们要求 `scope.admin`，设置 desired publication
还要求逻辑 Skill read。Receiver-only reconcile、download 和 receipt operation 使用独立 Target credential，不走用户
Principal Access。Public status 不返回 host path、Agent home、credential 或原始 OS error。

## OpenAPI access metadata

每个受保护 operation 在 `openapi/powercontext.yaml` 中声明 `x-powercontext-access`。生成器把该 extension 生成到
`Operation.access`，Server `_add_route()` 使用它组装 PEP wrapper。示例：

```yaml
/v1/handoff/commit:
  post:
    operationId: commit_handoff
    x-powercontext-access:
      resolver: commit_handoff_access
```

具有 selection-dependent policy 的 operation 使用已注册 resolver name，而不是在 YAML 中嵌入可执行表达式：

```yaml
x-powercontext-access:
  resolver: continue_handoff_access
```

Resolver 是 Server-owned、经过单元测试的确定性函数。它只能从已验证 request model 和 route metadata 建立
AccessRequest，不能读取业务 Repository 后才决定是否授权。

需要从业务参数派生资源的 operation 使用 resolver。跨 Scope publication 会在一个确定性检查中组合 source 分享和
target 管理权限：

```yaml
/v1/artifact-publications:
  post:
    operationId: publish_artifact
    x-powercontext-access:
      resolver: publish_artifact_access
```

生成的 `Operation.access` 必须能够表示 static single requirement 或 named resolver。Resolver 的 Server-side return type
支持多个 `all` requirements；生成 transport 不复制 policy 逻辑，只携带当前 Principal 并调用同一 Server operation。

Health endpoint、静态 page shell 和认证 callback 可以显式声明 public。没有 access metadata 的新增业务 operation
使 contract generation 或 contract test 失败，不能默认 public。

## Server PEP

请求顺序固定为：

```text
transport authentication
  -> bind Principal and trusted request context
  -> validate request schema
  -> resolve action and resource
  -> AuthorizationProvider decision
  -> application service
  -> response
```

Schema validation 和不访问 Repository 的 Family/selector compatibility validation 可以在判定前完成，以安全获得 resource
identity；验证错误不得包含资源内容。任何 Repository lookup、Handoff resolution、Memory search、Artifact Family read、
target lookup、host inspection、Report aggregate 或 mutation 都在全部必要 requirement allow 之后发生。

PEP 位于 Server adapter，不向 `application.context.for_scope(...)`、Source、Memory、Handoff、Work 或 Review domain method
添加 `principal`、role 或 permission 参数。Local in-process Runtime 调用不自动获得 Server authentication；需要安全边界
的本地集成应调用同一 Access Control service 或通过 Server。

## HTTP, MCP, and Dashboard parity

HTTP 是完整远程 contract，MCP 和 Dashboard 复用同一 operation 和 PEP：

- HTTP authentication 建立 Principal 后，授权 wrapper 对每个 operation 执行；
- MCP internal ASGI bridge 把原 Principal、actor 和 request ID 放入 request-local context；
- `is_internal_bridge()` 可以避免再次解析同一个外部 credential，但授权 wrapper仍执行；
- MCP tool discovery 可以根据当前 Principal 过滤不可用工具，但隐藏工具只是 UX，调用时仍必须判定；
- Dashboard 根据 `access/me`、authorized resource list 和 batch check 展示 Handoff inbox 或 “Shared with me”，并禁用或隐藏
  不可用操作，同时不能绕过 API enforcement；
- background job 必须携带创建 job 时绑定的 service Principal 或显式 system Principal，不使用空 identity。

HTTP 和 MCP 对同一 Principal、action、resource、policy revision 必须得到相同 allow/deny。Adapter conformance test 覆盖
这一保证。

Dashboard 的 `/shared` 页面不要求 `server.observe` 或 `scope.read`。它提供按 Family 筛选的授权资源列表和
Handoff 收件箱。接收方显式选择资源后，Server 先检查逻辑资源 read 权限，再解析当前版本；Memory 只解析所选
entry 的 citation，不读取其他 entry 正文。Handoff 的“检查 Handoff 及证据”调用 Continue，回执针对所选确切
Revision，接受前要求用户确认 live state、capability 和 authorization。表单 receiver 固定为当前 Principal。
拥有共享管理权限的用户可按规范 Principal ID 创建只读或 receiver 授权、指定过期时间并撤销现有授权。

Candidate 响应中的 `permissions.can_revise/can_approve/can_reject` 是当前调用方的 UI 提示。Review 页面据此禁用操作，
并说明修改仅限拥有 review 权限的原提议者。每次提交仍重新执行 PEP；页面缓存的提示不能授予权限。

## Listing and pagination

列表最容易泄漏 Project 名称、scope ID、Artifact Family identity、Handoff objective 或 Candidate metadata。安全顺序为：

```text
AuthorizationProvider.resolve_resource_filter
  -> validate bounded logical resource keys and parent constraints
  -> Repository query applying their union
  -> stable pagination
  -> response
```

禁止以下实现：

```text
Repository.list_all -> page -> check each item -> remove denied rows
```

这种实现会泄漏总数、cursor、空洞和时序，也可能让授权用户永远看不到后面的记录。Repository 必须在同一个 query 中
应用逻辑 resource key 与 parent constraint 的并集；`total`、cursor 和 page boundary 必须只描述授权后的集合。

Artifact logical receiver 通过 `/v1/access/resources/list` 的 Resource Kind 和 Family filter 发现授权资源；这些资源不会因此
出现在聚合 Project、Workstream、Memory search、Artifact catalog 或 Candidate Inbox。只有 scope-level read 才允许进入
对应聚合查询。发布 target 不是授权资源，不出现在该列表中。可以发布所选 Skill Revision 的 Principal 通过 Skill domain
preflight 取得脱敏 target 选项；详细运维状态通过受 `server.observe` 或 `server.admin` 保护的 Server operation 查询。

Scope 级 collection 权限通过后，Server 先从内容为空的 identity 目录检查 committed Artifact 和 Memory entry 的
owner 是否就绪，再读取正文或准备上下文。任何 identity 缺少 owner 时，整个聚合请求返回 503
`artifact_owner_pending`，包括 Memory list/search、Context Prepare、Artifact catalog、Dashboard Skill library 和 Report。
对无匹配授权的调用方，缺少 owner 与已有但不可见的资源统一返回 403，避免暴露存在性。

## Audit and diagnostics

Access Audit 是 append-only Server security record，至少包含：

- request ID、time、transport 和 operation ID；
- Principal opaque identifier 和可信 actor identifier（若存在）；
- action、Resource Kind、可选 Artifact Family 和 opaque resource identity；
- allow/deny、稳定 reason code 和 policy revision；
- Binding create/revoke 的 binding ID、grantor、recipient subject、role 和 expected/result version。

Audit 不包含：

- Bearer token、cookie、client secret 或 PDP credential；
- Handoff objective/state/next action；
- Source、Memory、Artifact、PreparedContext 或 citation body；
- publication target locator、host path、credential reference 或原始 Receiver/OS error；
- 任意 exception fields、configured PDP URL 或 provider 原始 response；
- email、display name 或不必要的目录属性。

普通 log、metric 和 trace 使用同样的数据最小化边界。Public readiness 在 5 秒内实际探测 PDP decision、audit、relationship/owner/Receipt identity 存储；拒绝本身是
正常探测结果，超时、异常或无效 decision 则标记 access provider 为 not_ready 并返回 503。探测不写入授权或审计。
响应只返回稳定 component state 和安全 reason，详细
provider diagnostics 留在受保护的 operator channel。

## Consistency and failure recovery

Commit Handoff 与创建外部授权关系不是跨系统原子事务。UI 中的“发送给 B”按以下可恢复步骤执行：

1. commit 或复用属于同一逻辑 Handoff 的 Revision；
2. 使用稳定 idempotency key 创建 Binding；
3. 只有两步都成功才显示“已分享”；
4. 第二步失败时显示“交接已保存，但 B 尚不可见”，并只重试 Binding create；
5. 不重新 prepare、commit 或创建另一个 Revision。

Binding 已成功而客户端丢失响应时，同一 idempotency key 返回原 Binding。外部 RelationshipWriter 无法提供等价幂等
保证时，adapter 必须先执行安全的 canonical relationship lookup，或声明不支持 self-service mutation。

所有 Artifact Family 分享遵循相同的 “persist/approve first, bind second” 原则。Binding create 失败不回滚或重建业务
Revision；客户端只重试同一个 idempotent Binding mutation。Skill projection 由逻辑 Skill read 加适用的 server/scope
管理边界保护，不创建 source content Revision 或 Access Binding。Target apply 失败保留可重试的 desired/applied 状态和
安全 reason，不把本地路径或底层错误写入公共 audit。

Receipt 创建仍使用现有 exact-selection 和 evidence rules。授权判定发生在 Receipt transaction 前；授权在判定后立即
被并发撤销时，Provider 和 Binding Store 应在同一 deployment 中使用 policy revision 或 transaction fence 防止明显
越权。跨网络 PDP 的剩余 TOCTOU 窗口必须有界并记录 decision revision；首版不缓存 allow decision。

## Provider profiles

### Built-in provider

内置 profile 使用固定角色和 Server-owned Binding Store，支持 point check、batch check、从逻辑 Artifact/scope/server Binding
生成可下推 `AuthorizedResourceFilter`、create、revoke 和 audit。它不需要保存业务 resource inventory，是本地部署和
conformance test 的参考语义；它不提供用户密码、目录或自定义 policy language。

### Casbin adapter

已包含的 Casbin adapter 使用 canonical Access relationship 和 Casbin enforcement semantics：

- 可信 subject/group ID 在判定前用于从 canonical repository 选择 active Binding；
- `act` 使用本 RFC 的 action vocabulary，`obj` 使用 canonical server、scope 或 Artifact key；
- `scope` 和 `deployment` 是可信 parent constraint，不是认证或 tenant 证明；
- 固定 PowerContext role table 把 active Binding 展开成具体 action policy；
- canonical relational Access repository 仍是 Binding 和 ownership 的事实源。Adapter 在判定时把这些 relationship
  materialize 到新的 embedded Casbin enforcer，不维护第二套持久化 Casbin policy store。

生成列表 filter 时，逻辑 object policy 产生 canonical key，scope/server role assignment 产生对应 parent constraint；
Casbin adapter 不需要枚举业务 Repository。未来 native Casbin-backed composition 可以同时提供 decision 和 relationship
management，但 writer 必须满足相同的 canonical idempotency、versioning、ownership 和 audit contract，才能声明
`relationship_management=true`。

### Future OpenFGA adapter

当前实现不包含 OpenFGA adapter。未来可以把相同 canonical server、scope、Artifact、owner、viewer 和 receiver relation
映射为 tuple，但必须保持上面的精确 role table：管理权限不得蕴含内容 read/write，Artifact object ID 不包含 Revision，
安全列表也不能在授权前枚举业务 Repository。Adapter 还必须显式使用 authorization model ID，并如实声明 relationship、
group 和 resource-filter capability。

### AuthZEN adapter 和 future OPA/Cerbos adapter

已包含的 AuthZEN adapter 把 point/batch `AccessRequest` 映射为 Authorization API 的 subject、action、resource、context，
只把有界 decision 和可选 policy revision 映射回 `AccessDecision`。它只提供 decision，不支持安全 resource filtering
或 relationship management。OPA/Cerbos 是未来 adapter，不是当前 deployment option。

标准 AuthZEN context 保留 `request_id`、`transport` 和 `operation`。`context.powercontext` 扩展还会携带可信 `actor`：
其值为 Principal object 或 `null`；同时把 `subject_groups` 作为 Group object 列表传递。这些 identity 使用
Authentication Provider 已归一化的同一套 deployment-wide opaque ID，不接受调用方另行提交 `issuer`。Point 和 batch
decision 都必须保留该 context，使外部 PDP 能基于与 Server-owned Provider 相同的认证事实执行 group membership 和
on-behalf-of 约束。

这些 adapter 的 decision interoperability 不代表 policy administration interoperability。若组织在 GitOps、IAM 或
独立管理面维护 policy，PowerContext 只消费判定和安全 resource filter，不写 policy。部署必须明确
`relationship_management=false`，Dashboard 不显示成功的 self-service share control。若 adapter 不能从 PDP search 或
可信关系数据产生 `AuthorizedResourceFilter`，还必须报告 `safe_resource_filtering=false`。

## Configuration and compatibility

`POWERCONTEXT_SERVER_ACCESS_MODE` 是唯一正式 Access 开关，支持两种值：

| Mode | Behavior |
| --- | --- |
| `disabled` | 保持单用户、单 trust-domain 的现有行为；Access API 不可用，不宣称多用户隔离 |
| `enforced` | Authentication Provider 和 AccessControlService 是 required dependency，所有业务 operation 执行 PEP |

升级不能因为配置了外部身份但漏配 PDP 而回退到 `disabled`。Mode 必须显式，capabilities 和 readiness 报告当前 mode 与
是否支持 relationship management、batch check 和 `safe_resource_filtering`。

`POWERCONTEXT_SERVER_AUTH_TOKEN` 只用于兼容认证。在 `enforced` mode 且没有注入 Authentication Provider 时，它认证固定
`service/server-token` Principal；内置 Access service 为该 Principal 初始化相互独立的 `server.observer`、
`server.admin` 和 per-scope working role。它无法区分多个用户。Legacy
`POWERCONTEXT_SERVER_AUTH_ENABLED=true` 加 `POWERCONTEXT_SERVER_AUTH_TOKEN=...` 会映射到
`ACCESS_MODE=enforced`。未启用 enforced mode 的 token 会被拒绝；enforced deployment 若既没有 injected
Authentication Provider，也没有兼容 token，则启动失败。

`disabled` 只适用于调用方已经信任整个进程和 catalog 的本地场景。文档不能把它描述为多用户安全配置。远程、多用户或
共享 Dashboard 部署应使用 `enforced`。

`access/me` 报告 Principal、mode、Resource Kind、Provider capability 和 `artifact_families` capability list。每个 Family
条目包含 `enabled`、`share_unit`、action vocabulary 和 grantable role。Disabled Prompt 仍报告保留 action，但没有
grantable role。Readiness 另行报告稳定 Access mode、provider state、Resource Kind 和 Family enabled/disabled state。
Provider 不支持安全过滤、多 requirement check、relationship mutation、group 或 multi-principal 时，对应 capability 必须
为 false；Server 不能接受随后无法 enforce 或撤销的 Binding。

```json
{
  "resource_kinds": ["server", "scope", "artifact"],
  "provider_capabilities": {
    "safe_resource_filtering": true,
    "multi_requirement_check": true,
    "relationship_management": true,
    "group_subjects": false,
    "multi_principal": false,
    "max_direct_resource_keys": 10000
  },
  "artifact_families": [
    {
      "family": "memory",
      "enabled": true,
      "share_unit": "memory_entry",
      "actions": ["artifact.read"],
      "grantable_roles": ["artifact.viewer"]
    },
    {
      "family": "prompt",
      "enabled": false,
      "share_unit": "artifact",
      "actions": ["artifact.read", "prompt.use"],
      "grantable_roles": []
    }
  ]
}
```

现有 OpenAPI operation 首次增加 authorization metadata 不改变 request/response domain schema，但会增加 403 response
并改变未授权行为。Generated Client 把 401、403 和 503 映射为稳定、不同的 exception；不能把 403 当作空结果。

## Implementation status

当前实现交付以下可独立验证的 slice：

1. **Contract and Principal**：OpenAPI Access model、operation metadata、generated `Operation.access`、可信 request
   Principal 和 stable errors。
2. **Built-in PEP/PDP**：固定角色、Binding Store、`_add_route()` authorization wrapper、point/batch check、audit。
3. **Handoff logical receiver**：commit 后创建 Binding、exact/latest Continue、citation-manifest resolver、exact acknowledge、
   future-Revision visibility、revoke 和 expiration。
4. **Artifact Family Access Profiles and ownership**：统一 ArtifactResourceRef、Family registry、Memory selector、
   system-managed logical ownership、read/write/share resolver、角色兼容性与非传递 lineage。
5. **Publication and distribution**：跨 Scope publication、host-local Skill projection 和 remote Skill distribution，分别
   使用对应逻辑 Artifact 与管理权限。
6. **Safe listing and UI**：authorized resource listing、Handoff inbox、“Shared with me”、Dashboard permission projection、
   授权后分页。
7. **MCP parity**：Principal 通过 internal bridge 传播、tool discovery UX 和调用时 enforcement。
8. **Provider adapters**：内置及 embedded Casbin relationship-capable profile，加 decision-only AuthZEN adapter；OpenFGA、
   OPA 和 Cerbos 留给后续。
9. **Migration**：legacy static admin、configuration validation、Family capability、readiness、operator documentation。

每个 slice 都保持 Server 可运行，不能先发布只隐藏 Dashboard 按钮或只保护 HTTP、不保护 MCP 的中间状态。

## Test and acceptance plan

RFC 实现完成需要通过以下 observable scenarios：

- 无身份访问受保护 operation 返回 401；
- A 有 `scope.delegate` 时只能把所属 scope 中至少有一个 committed Revision 的逻辑 Handoff 以 `handoff.viewer` 或
  `handoff.receiver` 授予 B；其他 Artifact Family 或 role 返回 422，缺少该 action 时返回 403，且都不写 Binding；
- B 可以读取并 Continue 已授予 Handoff 的历史、当前和未来 Revision，使用 `latest`，并 acknowledge 所选精确 Revision；
- B 读取其他 Handoff、聚合 Handoff Report、Memory list、Source list 和 Task Outcome write 均被拒绝；
- B 只能通过被授权 Handoff 的 resolver 读取 manifest citation，不能用任意 citation 调用通用读取接口；
- `handoff.viewer` 不能 acknowledge，`handoff.receiver` 可以；
- `accepted` Receipt 不产生新的 Binding 或 scope role；
- revoke 或 expiration 后，B 的 access 被拒绝，authorized resource list 不再包含该逻辑 Handoff；
- Binding create/revoke 的 CAS、idempotency 和 audit 行为稳定；
- 403 不泄漏资源是否存在，list cursor 和 total 只描述授权集合；
- PDP unavailable 返回 503，且 application service、Repository 和 mutation 未被调用；
- MCP internal bridge 使用原 Principal 并执行与 HTTP 相同的 deny；
- Dashboard 隐藏控制失效或被绕过时，API 仍拒绝请求；
- 显式 `enforced` mode 下，legacy static token 只在没有注入 Authentication Provider 时映射为 local admin；
- `server.observer` 可以读取受保护的服务状态，但不能修改 access 或 target configuration；`server.admin` 可以管理这些
  resource，但不会隐式获得 content read/write；
- Built-in 和 Casbin provider 对同一 canonical relationship 返回相同 decision；AuthZEN adapter 正确映射 point/batch
  decision，并对 malformed/unavailable response fail closed；
- 请求不能提交独立的 content profile，也不能在 Access Resource 中提交 Revision；未知/disabled Family、缺失或多余 selector，以及
  Family-role mismatch 返回 422 且不写 Binding；
- `artifact.viewer` 在 Experience、Skill 和 `memory_entry` selector 上始终只映射为 `artifact.read`，不会因 Family
  不同隐式增加 use、publish、acknowledge 或 mutation action；
- `artifact.viewer` 可以通过 `family=memory` 和 `entry_id` selector get 被授权 Memory Entry 的历史及未来版本，但不能
  search/list/revise/retire 或读取其他 Entry；
- logical Artifact viewer 可以读取一个 Experience/managed Skill 的 approved Revision，但不能看到 Candidate、其他 Artifact
  或解引用 lineage body；
- `family=prompt` 报告 disabled、拒绝 Binding，且不把 `prompt.user` 作为 enabled Family 的可用 role；
- logical-resource role 即使知道 expected version，也不能 revise、retire、replace 或提交共享原件的下一 Revision；
- enabled Artifact 缺少 owner relation 时 fail closed；首次创建或批准建立唯一 immutable owner，公共 Binding API 不能
  分配或转移 `artifact.owner`；
- Artifact owner 无需单独 viewer Binding 即可跨 Revision read/write/share 其逻辑 identity；scope/server administration
  不会隐式获得 owner write；
- acknowledge 创建的 Receipt 和 publish 创建的 target projection 不改变源资源的 identity、content、Revision 或 digest；
- fork、import 或 copy 在没有目标 scope 的 `scope.contribute` 时被拒绝；授权后创建新的 identity 或 Candidate，并保持原资源
  不变；
- host-local managed Skill projection 必须同时拥有 `server.observe` 和逻辑 Skill `artifact.read`，并在 target 解析或
  filesystem inspection 前完成判定；remote target administration 要求 `scope.admin`，发布 Revision 还要求逻辑 Skill
  read；
- 跨 Scope publication 要求逻辑 source `artifact.share` 加 target `scope.admin`，保留精确 source Revision provenance，
  并把 publisher 建立为新 target identity 的 owner；
- `resources/list` 的 total、cursor 和 rows 只描述当前 Principal 对所选 Resource Kind 和 Artifact Family 有权发现的集合；
- 不支持 Prompt lifecycle 的部署拒绝 `family=prompt` Binding 并报告 `enabled=false`；
- Access Audit 不包含 token、Handoff/Memory/Artifact 正文、Source body、target locator 或 PDP 原始错误。

Cross-component acceptance scenarios 放在 `tests/e2e/`，并通过公开 HTTP/MCP contract 断言行为。Focused tests 覆盖
Family registry、selector/canonical key、resource resolver、role mapping、Binding CAS、provider failure 和 citation
membership，不冻结 private call order。

# Drawbacks

每个业务请求增加一次授权判定，外部 PDP 还会增加网络依赖和延迟。安全列表要求 Provider 产生有界、可下推的
`AuthorizedResourceFilter`，只有 point-check 的简单 adapter 无法支持全部 Dashboard 列表。

逻辑 Handoff 分享必须先 commit，因此不能把临时 Prepared Handoff 直接变成可撤销的跨用户资源。这增加一步持久化，
但避免为临时 payload 发明第二套 identity 和 ACL。

判定和关系管理分离使 adapter interface 比单一 `check()` 更复杂；另一方面，假设所有外部 PDP 都允许 PowerContext 写
policy 会制造错误的可移植性承诺。

撤销只能阻止未来访问，无法删除接收方已经阅读、截图或导出的信息。包含高度敏感内容的 Handoff、Memory 或 Artifact
仍需要最小化内容、外部数据分类和导出控制。

Artifact Family Access Profile 增加了 registry、selector、ownership、角色兼容矩阵和 conformance vector。多 requirement
publication/projection 会增加判定工作；不能原子处理 batch 的外部 PDP 会增加延迟，并留下必须记录 policy revision 的
有界 TOCTOU 风险。

Access 模型不把 `target_id` 建模为 Resource。Host-local target 使用 server-observer boundary；remote target 使用所属
scope-administration boundary。需要单 target grant 的部署必须按 Scope 隔离，或等待独立 RFC 定义通用
`execution_target` Resource。

Prompt Family Access Profile 只定义授权边界，不能代替 Prompt Artifact lifecycle 和宿主 instruction-priority contract。
部署在这些业务能力完成前必须报告该 Family 不可用，因此 RFC 可以先落地其他 Family，但产品不会同时获得全部用户体验。

固定首版角色限制了组织自定义体验。企业可以在外部 PDP 映射自己的角色，但 PowerContext 公共 API 不立即提供自定义
role editor。

# Rationale and alternatives

## Chosen: independent Server PEP plus replaceable PDP

该设计保持 Handoff、Memory、Artifact 和 Runtime model 与身份系统解耦，同时让 HTTP、MCP 和
Dashboard 共用 enforcement。稳定 action vocabulary 比稳定外部 role name 更容易跨 Casbin、OpenFGA、OPA、Cerbos 和
企业 IAM 映射。

AuthZEN-compatible request shape 使网络 PDP 有标准接入点；独立 RelationshipWriter 则诚实表达 grant mutation 并未被
AuthZEN 统一。

## Alternative: put ACL fields on Handoff or scope

在 Handoff 增加 `allowed_users`，或把 owner/tenant 编入 `scope_id`，实现看似直接，但会把身份生命周期、group expansion、
撤销、外部 policy revision 和审计塞进领域数据。不可变 Handoff 也不适合随成员变更而创建新 Revision。该方案被拒绝。

## Alternative: only use scope-level roles

只授予 `scope.viewer` 容易实现，但 B 会看到整个 Workstream 的 Memory、Source、历史和 Report。对于临时接力不符合最小
权限原则。Scope roles 保留给长期协作，logical-resource Binding 负责一次性交接或资产分享。

## Alternative: add one share API per domain

`/memory/share`、`/experience/share`、`/skill/share` 和 `/prompt/share` 会重复 Principal、Binding、expiration、revoke、audit 与
external PDP semantics，还容易让不同 transport 出现不一致。本 RFC 选择一个 Access API、统一 ArtifactResourceRef、
Family role compatibility 和 resolver；业务 API 仍由各 domain 拥有。

## Alternative: 每个 Artifact Family 使用一个 Resource Kind

为 `handoff`、`memory_entry`、`experience`、`skill` 和 `prompt` 分别增加 `ResourceRef.type`，会重复 scope parent、逻辑
Artifact identity、canonical key 和只读分享结构；每新增一个 Family 还必须扩展 OpenAPI discriminator 和外部 PDP object type。
它也会让 `ResourceRef.type` 与 `ArtifactReference.family` 成为两个可能冲突的内容 discriminator。本 RFC 选择统一
`artifact` Resource Kind，由 Server 从 `ArtifactReference.family` 派生 Access Profile；只有 Memory 等需要更细授权单元的
Family 增加显式 selector。

## Alternative: automatically recall every shared resource

把所有逻辑 grant 自动加入 PreparedContext 会混淆可见性与相关性，扩大 token budget，并让不可信 Prompt 或 Skill 在接收方
没有显式选择时影响模型。首版只提供授权发现与显式附加；后续若增加 shared collection 或 subscription，仍必须经过独立的
Context selection policy。

## Alternative: send an anonymous capability URL

Bearer share link 把“知道 URL”变成身份。链接可能进入聊天、日志、浏览器历史或模型上下文，难以确认实际接收者，也难以
执行企业 group policy 和个人审计。首版要求 B 使用自己的认证凭据，不提供匿名 capability URL。

## Alternative: copy a redacted Handoff document

复制 Markdown 可以减少 Server 权限工作，但会失去 exact Revision、evidence availability、Receipt、并发和撤销语义。
导出仍可作为显式的外部发布功能，不能替代 PowerContext 内部交接。

## Alternative: hide unauthorized Dashboard controls

UI 隐藏只能改善体验，HTTP 或 MCP 调用仍可绕过。所有 enforcement 必须发生在 Server PEP，Dashboard 仅消费相同判定。

## Alternative: require one policy engine

Casbin 适合 embedded RBAC，OpenFGA 适合关系和 group，OPA/Cerbos 适合已有 policy platform。强制一个实现会增加部署成本或
限制企业集成。PowerContext 定义语义和 conformance contract，不选择唯一 engine。

## Alternative: store roles in access token

Token role 简单但对逻辑 Handoff grant、撤销、large resource set 和 policy update 不友好。Token 可以携带可信 identity
和 group claims，最终 resource decision 仍由 PDP 完成。

## Alternative: authorize inside every Runtime method

把 Principal 参数传入 Context、Source、Memory、Handoff 和 Work 会扩散 transport policy，容易让 HTTP 与 MCP 产生不同
实现，也破坏本地 domain API。Server PEP 是当前远程 trust boundary 的单一 enforcement point。

# Prior art

PowerContext [RFC 0011](0011_remote_access_architecture.md) 已定义 HTTP 完整 contract、generated Client 和 MCP 投影共享
Server application semantics。本 RFC在同一 Server boundary 增加 authentication 和 authorization，不创建平行 MCP
policy service。

[RFC 0048](0048_handoff_artifact.md) 定义 Prepared Handoff、不可变 Handoff Revision、Continue 和 exact evidence；
[RFC 1223](1223_human_agent_work_continuity.md) 定义 Receipt 和 Task Outcome，并明确交接不能授予工具、网络或凭据权限；
[RFC 0082](0082_handoff_report.md) 提供 scope 和 Project 级聚合视图。本 RFC 为这些读取和写入补充 Principal-aware
visibility。

[RFC 0050](0050_artifact_candidate_review_inbox.md) 定义 Experience/Skill Candidate 与 Review gate；pending/rejected
Candidate 不是可分享 Artifact。[RFC 0051](0051_experience_skill_artifact_families.md) 定义 exact Experience/managed Skill
Revision、External Skill host-local authority，以及 approval/publication 不等于执行授权。本 RFC 只增加这些资源的
Principal-aware visibility 和 managed Skill publication authorization，不改变其内容权威。

[OpenID AuthZEN Authorization API 1.0](https://openid.net/specs/authorization-api-1_0.html) 定义 PEP 与 PDP 之间的
subject、action、resource、context 和 decision contract。本 RFC 对齐其信息模型，但保留 embedded Provider。

[Casbin RBAC with Domains](https://casbin.apache.org/docs/rbac-with-domains/) 展示 domain-scoped role assignment；
[OpenFGA concepts](https://openfga.dev/docs/concepts) 使用 user、relation、object tuple 表达 object-level authorization；
[OPA](https://www.openpolicyagent.org/docs/integration) 提供通用 policy decision integration；
[Cerbos CheckResources](https://docs.cerbos.dev/cerbos/latest/api/index.html) 提供 principal、resource 和 action 的批量判定。
这些系统是 adapter 目标，不改变 PowerContext 的 Handoff lifecycle。

# Open questions

以下产品选择仍在已实现安全边界之外：

- Dashboard 如何从部署方的身份目录选择 canonical recipient；目录搜索本身不由本 RFC 的 Access API 提供；
- 哪个外部 identity source 提供可信 group membership；内置 Provider 当前报告 `group_subjects=false`；
- `handoff.receiver` 的产品默认过期时间是否由 deployment policy 决定，还是 UI 必须每次显式选择；
- Handoff receiver 创建 Receipt 后，UI 是否建议管理员另行授予 `scope.contributor`，但不能自动执行该升级；
- 后续 governed workflow 是否允许 Artifact ownership transfer；
- Prompt Artifact 的后续 lifecycle 采用固定 Review policy，还是区分个人私有模板与组织 approved template。

以下问题明确推迟：custom role、organization hierarchy、cross-tenant export、anonymous share link、temporary elevation、approval
workflow、通用 Source object-level ACL、动态 Memory collection 和 Artifact catalog 分享。它们需要独立威胁模型和 RFC。

# Future possibilities

后续可以在不改变 subject/action/resource contract 的前提下增加：

- group、team 和 organization relation；
- Project 到 Workstream 的继承策略和显式 deny；
- 管理员代查、subject/resource search 和 access review campaign；
- 带审批的临时 scope elevation；
- AuthZEN Search API、obligation 和 richer decision metadata；
- policy bundle、signed decision metadata 和跨服务 audit correlation；
- 对 Handoff 导出的独立脱敏、watermark 和 data-loss-prevention policy；
- 注册更多 approved Artifact Family 使用现有 `artifact` Resource Kind 和基础 `artifact.read` action；
- 用独立 RFC 定义可供 Skill、Prompt 或其他 execution content 共用的 `execution_target` Resource Kind 和 per-target grant；
- 带显式成员和 Revision manifest 的共享 collection，以及经过 Context policy 的订阅式选择；
- 在有明确 revocation-staleness guarantee 后增加 bounded decision cache。

这些扩展不能改变首版不变量：`scope_id` 不是 ACL，资源内容不授予权限，逻辑 grant 只跨 Revision 覆盖同一 identity，
读取不自动进入 Context 或获得执行权，所有 transport 在 Server PEP fail closed。
