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
│   └── family=prompt
```

- `server`：当前 PowerContext deployment；
- `scope`：一个精确 Workstream scope；
- `artifact`：一个由 Artifact Family Access Profile 解释的精确 Artifact Revision 或 Family-owned selector。

`artifact` Resource Kind 首版注册 `handoff`、`memory`、`experience`、`skill` 和 `prompt` 五个 Artifact Family Access
Profile。`ArtifactReference.family` 是唯一的 Profile discriminator；客户端不再提交第二个可能与它冲突的内容类型。

用户 A 可以选择两种协作方式：

- 为长期协作者授予 Workstream 级角色；
- 只把一个已持久化或已批准的精确 resource 授予 B。

第二种方式是首版的最小权限路径。B 可以读取被分享的精确资源，并只能执行对应 Artifact Family Access Profile 明确
授予的 action。精确 Handoff receiver 可以通过 Handoff resolver 检查其中明确引用的 evidence，并对同一个 Revision 留下 Receipt；精确
Memory、Artifact 或 Prompt grant 不自动开放同一 scope、current head、未来 Revision、搜索结果或 lineage 中引用的
其他资源。Skill 的读取、发布到一个 target，以及宿主最终加载或执行是彼此独立的授权边界。`accepted` Receipt、Artifact
approval、Prompt read 或 Skill publication 都不会授予工具、网络、文件系统、模型 Provider 或凭据权限。

PowerContext 定义稳定的授权 request/decision、内置角色、Access API 和 OpenAPI extension，但不绑定一个策略引擎。
首版提供内置 Role Binding Store；Casbin、OpenFGA 和兼容 OpenID AuthZEN Authorization API 的 Policy Decision
Point（PDP）可以通过 adapter 接入。

# Motivation

PowerContext 已经拥有临时 Prepared Handoff、不可变 Handoff Revision、Continue、Receipt 和 Task Outcome，也拥有
Memory Entry Version、approved Experience/managed Skill Revision 和 host-local Skill projection；但现有 Server 认证是
可选的全局静态 Bearer。一个有效 token 可以访问所有受保护 operation，Server 无法表达：

- A 可以管理 Workstream，而 B 只能看一份交接；
- B 可以确认接收，但不能提交新的里程碑；
- 团队成员可以查看 Handoff Report，但不能审批 Experience 或 Skill；
- B 只能读取一条被分享的 Memory Entry Version，不能搜索整个 scope 或跟随它的未来版本；
- B 可以读取一个 approved Experience 或 managed Skill Revision，但不能评审 Candidate；
- B 可以使用一个精确 Prompt，但不能把它静默提升为宿主的 system/developer instruction；
- 发布者可以发布一个精确 managed Skill，但不能借此修改源 Revision 或获得宿主执行权限；
- 被撤销的接收方不能继续读取后续 Revision；
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
Prepared Handoff -> Commit -> immutable Handoff Revision
                                  |
                                  +-> Access Binding for user B
                                           |
                                  read / inspect / acknowledge
                                           |
                                    expire or revoke
```

提交新 Handoff 不会自动分享，分享也不修改 Handoff 内容或 Revision。撤销 Binding 不删除 Handoff、Receipt 或审计事件。

## 同一 Access Plane，Artifact Family 驱动的 Profile

Access Control 核心只回答“当前 Principal 是否可以对这个精确资源执行这个 action”。Resource Kind 定义授权对象的结构；
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
│   └── family=prompt
```

每一种 Artifact Family Access Profile 必须固定回答以下问题：

| Family profile contract | 必须定义的内容 |
| --- | --- |
| share unit | 分享整个精确 Revision，还是一个 Family-owned exact selector |
| shareable state | committed、approved、retained 等哪些 lifecycle state 可以创建 Binding |
| parent | scope 或 server 级角色如何单向蕴含子资源 action |
| actions | 读取、使用、确认、发布和管理分别使用什么稳定 action |
| grantable roles | 哪些固定角色可以绑定到该资源，以及谁可以创建这些 Binding |
| resolution | 哪些 operation 可以从已验证 request 确定资源，不得在授权前读取什么 |
| listing | exact grant 如何被发现，以及哪些聚合列表仍要求 scope 或 server 权限 |
| transitivity | 读取资源是否同时允许读取 lineage、citation 或其他关联资源 |

所有 Family 复用同一个 `/v1/access/*` API，不增加 `/memory/share`、`/experience/share`、`/skill/share` 或
`/prompt/share` 等平行授权接口。新增 Family 必须显式注册；只复用 `artifact.read` 的 exact-read Family 不需要增加新的
ResourceRef variant。若 Family 引入新的 semantic action、selector 或 role，则必须同步 OpenAPI、固定 action/role
vocabulary、Server-owned resolver、Provider conformance vector 和生成的 transport artifact。未知 Family 默认不可分享。

资源可读、进入上下文和获得外部执行能力是三个不同平面：

```text
Access Plane:      Principal 可以读取或使用哪个 exact resource
Context Plane:     哪些已授权内容经显式选择进入有界 PreparedContext
Execution Plane:   宿主是否安装、加载或执行 Skill/Prompt，以及能使用哪些工具和凭据
```

一个 allow decision 不能跨平面传播。精确 Memory、Artifact 或 Prompt grant 不会让内容自动进入普通 scope recall；接收方
先在 “Shared with me” 视图发现资源，再显式读取、附加到当前任务或 fork 到自己可贡献的 scope。共享内容继续视为
`untrusted_history` 或不可信 instruction，Context builder 和宿主仍执行各自的预算、优先级、approval 与 sandbox policy。

## A 把一份精确 Handoff 交给 B

假设 A 负责 `project:payments` Workstream，并已完成一份交接。正常流程如下：

1. A 检查并提交 Prepared Handoff，得到不可变 `ArtifactReference`：

   ```json
   {
     "family": "handoff",
     "artifact_id": "project:payments",
     "revision": 12
   }
   ```

2. A 明确选择接收方 B。Dashboard 或集成层把 B 从企业身份目录解析为可信的 canonical Principal；模型输出、显示名或
   邮箱文本不能替代该解析。
3. Server 检查 A 对 `project:payments` 是否拥有 `scope.delegate`。
4. Server 创建角色为 `handoff.receiver` 的 Access Binding，资源是上面的精确 Revision，可选设置过期时间。
5. B 使用自己的凭据登录。`resources/list` 返回 B 有权读取的精确 Handoff，B 不需要知道 A 的 token，也不接收新的
   bearer share link。
6. B 使用 exact selection 调用 Continue。Server 读取同一 Revision，并只解析它明确引用的 evidence。
7. B 检查当前 workspace、能力和授权状态后，可以对同一 Revision 留下 `accepted`、`needs_clarification` 或
   `declined` Receipt。

创建 Binding 的请求示例为：

```json
{
  "subject": {
    "type": "user",
    "issuer": "https://id.example.com/",
    "id": "00u-bob"
  },
  "resource": {
    "type": "artifact",
    "scope_id": "project:payments",
    "reference": {
      "family": "handoff",
      "artifact_id": "project:payments",
      "revision": 12
    }
  },
  "role": "handoff.receiver",
  "expires_at": "2026-09-06T12:00:00Z",
  "reason": "Continue the payment retry investigation",
  "idempotency_key": "transfer-payments-12-to-bob"
}
```

`granted_by`、创建时间和 policy revision 由 Server 填充，调用方不能伪造。

## B 能看到什么

`handoff.receiver` 是精确资源角色，不是 scope role：

| 操作 | 结果 | 原因 |
| --- | --- | --- |
| 读取 Handoff Revision 12 | 允许 | Binding 指向该精确 Revision |
| 通过 Continue 检查 Revision 12 的引用 | 允许 | `handoff.evidence.read` 只覆盖该 Revision 的 citation manifest |
| Acknowledge Revision 12 | 允许 | receiver 可以为已检查的 exact Handoff 留 Receipt |
| 请求 `latest` | 拒绝 | latest 可能是 B 未获授权的后续 Revision |
| 读取 Revision 11 或 13 | 拒绝 | 精确 Binding 不继承到其他 Revision |
| 打开聚合 Handoff Report | 拒绝 | Report 包含 scope 级历史和统计 |
| 搜索 scope Memory 或列出 Source | 拒绝 | Handoff Binding 不授予通用 scope read |
| Commit 新 Handoff 或记录 Task Outcome | 拒绝 | 需要 `scope.contribute` |
| 审批 Candidate | 拒绝 | 需要独立的 `scope.review` |

Evidence 的最小权限不是逐条复制 Source 或 Memory，也不是让外部 PDP 保存全部 citation。Server 先从已验证请求构造
exact Handoff `ArtifactResourceRef`，同时检查 B 的 `artifact.read` 和 `handoff.evidence.read`；只有两个 decision 都允许后，
才能读取不可变 Handoff Revision、取得 citation manifest，并通过 Handoff resolver 解引用其中的 exact citation。B 不能把
任意 Source ID 填入通用读取 API 来复用这项权限。

如果一条 citation 已被删除、retire、损坏或因更高层策略被拒绝，Continue 把对应 evidence 标记为 unavailable。
Handoff Binding 不覆盖 retention、legal hold、数据分类或显式 deny policy。

## 分享其他 Artifact Family

其他 Artifact Family 使用相同的 exact-share 流程，但不会继承 Handoff 的 evidence 和 Receipt 语义：

1. A 选择一个已经持久化且可授权的精确资源；Memory 使用完整 `MemoryCitation`，Experience、managed Skill 和 Prompt
   使用带正整数 Revision 的 `ArtifactReference`。
2. Server 先检查 A 是否可以在该资源所属 scope 创建对应 Binding，再验证资源存在且处于可分享状态。
3. B 通过 `access/resources/list` 发现 exact resource，并使用自己的 Principal 读取或显式使用它。
4. B 若要修改或长期维护内容，需要在自己拥有 `scope.contribute` 的 scope 中显式 fork 或提出新 Candidate；原资源和
   Binding 不被修改。

首版 exact grant 的行为如下：

| Family role | 允许 | 不允许 |
| --- | --- | --- |
| `artifact.viewer` on `family=memory` selector | exact get 一个 `entry_version_id` | search、list、changes、current head、revise、retire、其他 entry/version |
| `artifact.viewer` | exact get 一个 approved Experience 或 managed Skill Revision | Candidate read/review、future Revision、publication、lineage body |
| `artifact.viewer` on `family=prompt` | exact get 一个 approved Prompt Revision | render/use、future Revision、自动注入 |
| `prompt.user` | `artifact.viewer` 加显式 render/use | 改变 instruction priority、自动启用工具或读取凭据 |

普通用户输入仍是 Source evidence，不因包含文字 “prompt” 就成为 Prompt Artifact。可复用、参数化的任务模板可以由后续
Prompt Artifact lifecycle 定义；Memory extraction、Experience/Skill generation 和 Handoff generation 使用的内部 prompt
属于 Server implementation/configuration，由 `server.admin` 管理，不通过 `family=prompt` Artifact Binding 分享。如果一个
内容描述 Agent 何时使用、如何执行和如何验证一项能力，它应建模为 managed Skill，而不是重复创建 Prompt Artifact。

精确资源响应可以返回 schema 已定义的 lineage/citation identity，但 grant 不向引用目标传递。调用通用 Source、Memory 或
Artifact get operation 仍需对目标资源独立判定；Provider 不得因为 “A references B” 自动创建 `can_read` 继承。

## 分享是只读快照，不是共同编辑

Exact-resource Binding 只授予读取、显式使用或向 Server-configured target 执行受控发布 operation 的权限，不转移原资源的
content authority。
Binding 本身不能授权接收方 revise、retire、replace、提交下一 Revision，或原地覆盖共享内容。即使接收方另外拥有原 scope
的 `scope.contribute` 或更高权限，其写入能力也来自该独立的 scope role，而不是这次分享。

接收方产生的状态必须与共享原件分离：

| 接收方操作 | 约束 |
| --- | --- |
| acknowledge Handoff | 创建独立 Receipt，不修改 Handoff Revision |
| 提交 feedback 或变更建议 | 创建独立 feedback/change request，不修改共享内容 |
| 发布 managed Skill | 写入 Server 配置目标的 projection/state，不修改源 Skill Revision |
| fork、import 或 copy | 必须对目标 scope 拥有 `scope.contribute`；创建新的 identity 或 Candidate，并保留到原资源的 lineage |

产品界面应使用“查看”“使用”“确认接收”“请求变更”“复制到我的 scope”或“发布到配置目标”等动作，不应把 exact share
呈现为“编辑共享内容”。持续共同维护需要单独授予 scope role；对于需要 Review 的 Artifact Family，贡献者仍通过 Candidate
和 Review lifecycle 产生新 Revision，而不是原地改写 approved Revision。撤销分享会阻止后续访问，但不能删除接收方已经
看到的内容，也不能自动撤销此前经独立授权创建的 Receipt、projection 或 fork。

## 发布 managed Skill

读取 Skill 内容和把 Skill 发布到配置的 host-local Agent target 是不同 operation。发布请求只接受 exact managed Skill
`ArtifactReference` 和 Server 配置的 opaque `target_id`，不接受 destination path、Agent home、SSH credential 或任意
filesystem locator。Server 必须在读取 Skill body、解析 `target_id`、检查 target host 状态或写入 projection 前同时得到两个
关于同一个 exact Skill Artifact 的 allow decision：

```text
artifact.read AND skill.publish on exact family=skill Artifact
```

`skill.publisher` 只绑定到一个 exact managed Skill Revision，并同时授予这两个 action。`target_id` 是由 `server.admin`
配置的 opaque operation parameter，不是 `ResourceRef`、Access Binding 或 `/access/resources/list` 中的授权资源。授权通过后，
Server 才能确认 `target_id` 已注册并把它解析为 host-local Agent projection configuration；未注册或 disabled target 拒绝
发布。Host ID、destination path、Agent home、credential reference 和 locator 不进入请求、Binding、普通 audit 或公共错误。

普通 publisher 通过 `POST /v1/skills/publication-targets/list` 选择 target。请求携带 `scope_id` 和 exact Skill
`ArtifactReference`，Server 复用上述两个 requirement；只有全部 allow 后才读取 Skill Repository 和 target registry。响应
只列出 enabled target 的 opaque `target_id`、Agent kind、installation scope 和安全 capability，不返回 desired/applied
state、host path、Agent home、credential reference 或底层错误。该 operation 是 Skill publication domain contract，不是
Access Resource listing，也不为 target 创建 Binding。

```json
{
  "scope_id": "project:payments",
  "artifact": {"family": "skill", "artifact_id": "retry-runbook", "revision": 4}
}
```

```json
{
  "artifact": {"family": "skill", "artifact_id": "retry-runbook", "revision": 4},
  "targets": [
    {
      "target_id": "codex-project",
      "agent_kind": "codex",
      "installation_scope": "project",
      "capabilities": ["publish"]
    }
  ]
}
```

首版不提供 per-target delegation：获得一个 exact Skill 的 `skill.publisher` 后，可以把该 Revision 发布到当前 deployment
中任意 enabled configured target。只有 `server.admin` 能配置、修改或删除 target；target 状态属于受 `server.observe` 或
`server.admin` 保护的运维信息。若产品需要表达“B 可以发布到 X，但不能发布到 Y”，后续由独立分发 RFC 定义通用
`execution_target` Resource，而不把 Skill 专用 target 混入 Artifact 分享模型。

发布成功只表示配置的 host-local target projection 接收到该 exact Revision，不授予宿主加载、执行、工具、网络、文件系统
或 secret 权限。External Skill registration 和 host-local locator 不是可跨主机分享的 Artifact Family Access Profile；
需要协作时应显式 import/fork 为 managed Skill。Remote Receiver distribution 不属于首版。

## B 真正接手 Workstream

查看交接不等于获得执行权。若 B 将长期推进该 Workstream，A 或管理员需要另行授予 `scope.contributor`：

```text
handoff.receiver
  = read one exact Handoff + inspect its citations + acknowledge it

scope.contributor
  = read the Workstream + contribute Sources + prepare/commit Handoffs
    + acknowledge Handoffs + record Task Outcomes
```

PowerContext 权限只控制 PowerContext 资源和 operation。修改 Git 仓库、调用云 API、访问生产环境或读取凭据仍由宿主、
操作系统和外部服务授权。Handoff、Role Binding 和 Receipt 都不能扩大这些权限。

## 长期团队协作

对固定团队，可以把用户或外部 group 绑定为 scope role，而不是为每个 Revision 创建 Binding：

- `scope.viewer`：读取当前 scope 的 Handoff、Memory、approved Artifact、Prompt、Source 和只读投影，并显式使用 approved
  Prompt；
- `scope.contributor`：在 viewer 基础上写入工作 evidence、Memory contribution、Handoff 和 Outcome，并提出 Artifact/Prompt
  Candidate；
- `scope.reviewer`：在 viewer 基础上评审 Artifact Candidate；
- `scope.delegator`：在 viewer 基础上把精确 Handoff 分享给接收方；
- `scope.admin`：管理该 scope 的全部角色和策略。

`scope.delegate` 在本 RFC 中继续只允许为 `family=handoff` Artifact 创建 viewer/receiver Binding。首版其他 Artifact
Family 的 exact Binding 只能由 `scope.admin` 创建，不能因为已有 Handoff delegator 就静默扩大分享边界。后续可以增加
资源级 delegation action，但必须作为显式 wire-contract 变更。发布 target 由 `server.admin` 通过 deployment configuration
管理，不创建 Access Binding。

固定角色是 wire-contract vocabulary，不要求外部 PDP 使用相同内部存储。外部系统可以把企业角色、团队或关系映射为
这些 action。

## 撤销和过期

A、相应 grant administrator 或 scope admin 可以撤销其管理边界内的 exact Artifact Binding。对于 Handoff，撤销后：

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
- 支持 scope 级 RBAC 和精确 Handoff receiver Binding；
- 定义稳定 Resource Kind 和 Artifact Family Access Profile contract，并规范 Handoff、Memory、Experience、Skill 和 Prompt
  的精确授权；
- 允许安全解引用精确 Handoff 已引用的 evidence，而不开放整个 scope；
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
- 对 exact shared resource 进行 multi-writer collaborative editing，或通过 Binding 转移 ownership；
- Memory collection、Artifact catalog 或 “自动跟随 latest” 的动态订阅分享；
- Prompt Artifact 的内容 schema、变量语言、Review lifecycle 或宿主 instruction-priority policy；
- per-target publication delegation 或通用 `execution_target` Resource；
- remote managed Skill projection 或 Receiver distribution contract；
- External Skill 的跨主机 locator、自动安装或 package distribution contract。

## Trust model and invariants

实现必须维持以下不变量：

1. `scope_id` 是业务分区值，不是授权证明。
2. Principal 只来自认证 middleware 或可信 internal bridge context。
3. 请求 body 中的 `receiver`、`subject`、`actor`、role text 或 Handoff 自然语言不能替换当前 Principal。
4. Handoff、Memory、Artifact 和 Prompt 内容是 `untrusted_history` 或不可信 instruction，不能授予 action。
5. `is_internal_bridge()` 只能跳过重复 transport authentication，不能跳过 authorization。
6. 每个受保护的 operation 在访问 Repository 或 application service 前完成判定。
7. 精确 Handoff grant 不允许 `latest`，不自动覆盖同 Artifact 的其他 Revision。
8. `accepted` Receipt 不创建、更新或继承 Access Binding。
9. 模型可以建议接收方或解释拒绝原因，但不能自行确定 canonical Principal 或调用 allow-all fallback。
10. Exact Memory Entry grant 必须由 `family=memory` 的精确 `ArtifactReference` 和完整 `memory_entry` selector 组成；其他
    exact Artifact grant 必须包含正整数 Revision，不允许 `latest` 或自动继承到未来 Revision。Server 只从
    `ArtifactReference.family` 派生 Access Profile；独立 content profile、未知 Family 或 selector mismatch 必须拒绝。
11. 读取 Memory、Artifact 或 Prompt 不自动授予其 lineage/citation target，也不自动进入 PreparedContext。
12. Exact-resource Binding 本身不授予 revise、retire、replace、提交下一 Revision 或其他修改共享内容的 operation；
    Receipt、feedback、projection 和 fork 是独立资源或 operation，必须分别授权，并且不能修改原资源的 identity、content
    或 Revision。
13. `prompt.use` 不改变宿主 instruction priority；`skill.publish` 不授予宿主加载、执行、工具、网络、文件系统或 secret
    权限。
14. Skill publish 必须同时允许 exact `family=skill` Artifact 的 `artifact.read` 和 `skill.publish`，且授权发生在解析
    `target_id` 或任何 host/filesystem inspection 前；`target_id` 不是授权资源，首版只解析已配置的 host-local target。
15. Public error、log、metric 和 trace 不包含 credential、Handoff/Memory/Artifact/Prompt 正文、Source body、target locator
    或 PDP 原始响应。

## Principal model

`PrincipalRef` 使用认证 Provider 给出的稳定 opaque identity：

```json
{
  "type": "user",
  "issuer": "https://id.example.com/",
  "id": "00u-bob"
}
```

字段语义如下：

| Field | Semantics |
| --- | --- |
| `type` | `user`、`service` 或后续注册的 Principal type |
| `issuer` | 建立该 identity 的可信 issuer；本地凭据使用 deployment-specific issuer |
| `id` | issuer 内稳定 opaque subject，不使用显示名或 email |

Agent 名称、host、session ID 和模型名称属于 provenance，不默认成为 Principal。若企业 token 明确证明 on-behalf-of actor，
认证 adapter 可以在可信 request context 中附加 `actor`；PDP 可以同时约束 subject 和 actor。客户端不能通过 JSON body
声明该 actor。

现有 Handoff Receipt 的 `receiver` 字段继续作为记录内容。Server 另外记录产生 Receipt 的 authenticated Principal，
两者不一致时拒绝 `accepted` 或在非 accepted Receipt 中明确标记 mismatch；绝不能把自由文本 `receiver` 当作 Principal。

## Resource model

内部授权 request 使用结构化 `ResourceRef`，避免把包含 `:`、`/` 或用户数据的标识直接拼成策略字符串：

| Resource Kind | Identity | Parent |
| --- | --- | --- |
| `server` | deployment identifier | none |
| `scope` | exact `scope_id` | server |
| `artifact` | exact `ArtifactReference`、可选 Family-owned selector 和 `scope_id` | scope |

`ResourceRef` 是 OpenAPI discriminated union。每个 variant 使用 `additionalProperties: false`，并且只接受下表字段：

| `type` | Required identity fields |
| --- | --- |
| `server` | `deployment_id` |
| `scope` | `scope_id` |
| `artifact` | `scope_id`, `reference`, and optional `selector` |

普通 Artifact Revision 不包含 selector：

```json
{
  "type": "artifact",
  "scope_id": "project:payments",
  "reference": {"family": "experience", "artifact_id": "exp-retry-budget", "revision": 3}
}
```

Memory Entry 使用 `memory` Family 拥有的 exact selector。`reference` 和 `selector` 合在一起等价于完整
`MemoryCitation`：

```json
{
  "type": "artifact",
  "scope_id": "project:payments",
  "reference": {"family": "memory", "artifact_id": "memory", "revision": 18},
  "selector": {
    "type": "memory_entry",
    "entry_id": "retry-policy",
    "entry_version_id": "01K..."
  }
}
```

`ArtifactResourceRef.reference.family` 是唯一的 Artifact Family Access Profile discriminator。请求不包含独立 `profile`
字段；Server 从已验证的 exact `ArtifactReference` 派生 Profile，避免 `profile=prompt` 与 `family=skill` 等不一致组合。
每个 Family 声明 selector 为 required、forbidden 或某个固定 discriminated union variant。首版 `memory` 要求
`memory_entry` selector，`handoff`、`experience`、`skill` 和 `prompt` 禁止 selector。

Family registry 是 Server-owned 固定 contract，不是管理员可编辑的 policy DSL。每个注册项至少包含：

| Field | Requirement |
| --- | --- |
| `family` | 与 `ArtifactReference.family` 完全匹配的稳定名称 |
| `share_unit` | `revision` 或一个明确的 Family-owned selector type |
| `shareable_states` | 允许创建 Binding 的 lifecycle state |
| `base_action` | 首版统一为 `artifact.read` |
| `additional_actions` | Family 特有的 use、acknowledge 或 publish action |
| `grantable_roles` | 与该 Family 兼容的固定 exact roles |
| `parent_implications` | scope role 可以单向蕴含哪些 child action |
| `transitivity` | lineage、citation 或其他关联资源是否需要独立判定；未声明时为 none |
| `resolver` | 授权后如何解析 exact resource 以及返回什么安全 identity |

首版 registry 为：

| Artifact Family | Share unit | Shareable state | Exact actions | Grantable exact roles |
| --- | --- | --- | --- | --- |
| `handoff` | Revision | committed | `artifact.read`, `handoff.evidence.read`, `handoff.acknowledge` | `handoff.viewer`, `handoff.receiver` |
| `memory` | `memory_entry` selector | active in the referenced Revision | `artifact.read` | `artifact.viewer` |
| `experience` | Revision | approved | `artifact.read` | `artifact.viewer` |
| `skill` | Revision | approved | `artifact.read`, `skill.publish` | `artifact.viewer`, `skill.publisher` |
| `prompt` | Revision | approved | `artifact.read`, `prompt.use` | `artifact.viewer`, `prompt.user` |

Prepared Handoff 没有持久化 identity，不能创建精确 Access Binding。跨用户最小权限分享必须先 commit；pending/rejected
Candidate 同样不能创建 Artifact Binding。普通新 Family 即使只复用 `artifact.read`，也必须先显式注册为 shareable；
未知、disabled 或 selector 不匹配的 Family 默认拒绝。`revision=latest`、只有 `entry_id`、Memory current head 或 search query
都不是稳定授权身份。后续 Revision 或 Memory Entry Version 不继承 exact Binding。

每个 Resource Kind 都定义稳定的 canonical serialization 供 adapter 建立 object ID。Artifact key 必须包含 `scope_id`、
`family`、`artifact_id`、正整数 `revision` 和完整 selector；相同业务身份在 HTTP、MCP 和 Dashboard 必须得到同一个 key。
不同 Family 或 selector 不得因字符串碰撞共享 Binding。

Adapter 负责把结构化 ResourceRef 映射成外部 PDP object ID。映射必须 canonical、可逆或稳定，并避免把 email、token、
资源正文、发布 target locator 或其他 PII 写入 Casbin policy、OpenFGA tuple 或 audit key。

## Action vocabulary

首版 action 是稳定、小写、点分隔的字符串：

| Action | Resource | Meaning |
| --- | --- | --- |
| `server.observe` | server | 读取服务级运行状态和观测数据 |
| `server.admin` | server | 管理 deployment access configuration 和 publication target configuration |
| `scope.read` | scope | 读取该 Workstream 的通用只读资源、approved content 和投影 |
| `scope.contribute` | scope | 写入 Source、Memory contribution、Handoff/Outcome，并提出 Artifact/Prompt Candidate |
| `scope.review` | scope | 评审该 scope 的 Artifact Candidate |
| `scope.delegate` | scope | 为精确 Handoff 创建 viewer 或 receiver Binding |
| `scope.admin` | scope | 管理该 scope 的角色、Binding 和 policy |
| `artifact.read` | exact artifact | 读取 Family Profile 定义的 exact Revision 或 selector |
| `handoff.evidence.read` | `family=handoff` artifact | 通过 Handoff resolver 解引用该 Revision 的 citation manifest |
| `handoff.acknowledge` | `family=handoff` artifact | 对该 Revision 创建 Handoff Receipt |
| `prompt.use` | `family=prompt` artifact | 显式 render 或附加一个已授权 Prompt；不决定宿主 instruction priority |
| `skill.publish` | `family=skill` artifact | 发现安全 target 选项，并选择一个 exact managed Skill Revision 用于发布 |

`artifact.read` 的含义在所有 Family 中保持固定：只读取 Binding 标识的 exact Revision 或 selector。它不自动包含 Handoff
evidence、Prompt use、Skill publish、lineage body 或任何 mutation。只有确实具有不同安全效果的 Family operation 才新增
semantic action。

业务 operation 检查 action，不检查 role name。这样可以调整外部角色或关系模型，而不改 application code。

`scope.read` 可以通过策略蕴含 scope 下所有已注册 Family 的 `artifact.read`、Handoff 的 `handoff.evidence.read` 和
Prompt 的 `prompt.use`；`scope.contribute` 可以蕴含 acknowledge、prepare、commit、Memory contribution、Artifact/Prompt
Candidate proposal 和 Outcome 写入。反向蕴含不成立：任何 exact viewer/user role 都不能得到 `scope.read` 或
`scope.contribute`。`scope.read` 不蕴含 `skill.publish`。

## Built-in roles

| Role | Granted actions |
| --- | --- |
| `handoff.viewer` | `artifact.read`, `handoff.evidence.read` on one exact `family=handoff` Artifact |
| `handoff.receiver` | viewer actions plus `handoff.acknowledge` on one exact Handoff |
| `artifact.viewer` | `artifact.read` on one compatible exact Artifact Revision or selector |
| `prompt.user` | `artifact.read`, `prompt.use` on one exact `family=prompt` Artifact |
| `skill.publisher` | `artifact.read`, `skill.publish` on one exact managed Skill Revision |
| `scope.viewer` | `scope.read` |
| `scope.contributor` | `scope.read`, `scope.contribute` |
| `scope.reviewer` | `scope.read`, `scope.review` |
| `scope.delegator` | `scope.read`, `scope.delegate` |
| `scope.admin` | all scope and child Artifact Family actions, including delegation and Binding administration |
| `server.observer` | `server.observe` |
| `server.admin` | all server, scope, and Artifact Family actions |

所有 exact-resource role 对其绑定内容都是只读的。`handoff.receiver` 只额外允许创建独立 Receipt；`skill.publisher` 只允许
向 Server 配置的 target 写 projection。两者都不能修改源 Handoff 或 Skill Revision。原资源的 mutation 必须由独立的
scope role 和对应领域 lifecycle 授权。

首版不允许通过公共 API 创建新 role 或修改 role-to-action mapping。固定角色让 OpenAPI、Dashboard 和 adapter
conformance test 拥有稳定语义；企业 PDP 可以在外部把自定义组织角色映射为这些 action。

拥有 `scope.delegate` 的 Principal 只能创建 `handoff.viewer` 或 `handoff.receiver`，且只能针对该 scope 中已经存在的
精确 Handoff。创建 scope role 需要 `scope.admin`；创建 `server.admin` 需要现有 `server.admin` 和 deployment policy
允许。任何 Principal 都不能授予自己高于调用方管理边界的权限。

首版只有 `scope.admin` 可以在所管理的 scope 中创建 `artifact.viewer`、`prompt.user` 或 `skill.publisher` Binding。
`artifact.viewer` 只能绑定到 Family registry 声明兼容的 exact Revision 或 selector；`prompt.user` 和 `skill.publisher` 分别
只能绑定 approved `family=prompt` 和 `family=skill` Artifact。Role 与 Artifact Family Access Profile 或 Resource Kind
不匹配时返回 422，
授权不足时返回 403；Server 不能把不匹配的 role text 原样交给外部 RelationshipWriter。

| Resource or Artifact Family Profile | Grantable exact roles | Binding administrator |
| --- | --- | --- |
| `artifact` with `family=handoff` | `handoff.viewer`, `handoff.receiver` | `scope.delegate`, `scope.admin`, or `server.admin` |
| `artifact` with `family=memory` and `memory_entry` selector | `artifact.viewer` | `scope.admin` or `server.admin` |
| `artifact` with `family=experience` | `artifact.viewer` | `scope.admin` or `server.admin` |
| `artifact` with `family=skill` | `artifact.viewer`, `skill.publisher` | `scope.admin` or `server.admin` |
| `artifact` with `family=prompt` | `artifact.viewer`, `prompt.user` | `scope.admin` or `server.admin` |

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
    "issuer": "https://id.example.com/",
    "id": "00u-bob"
  },
  "action": {"name": "artifact.read"},
  "resource": {
    "type": "artifact",
    "scope_id": "project:payments",
    "reference": {
      "family": "handoff",
      "artifact_id": "project:payments",
      "revision": 12
    }
  },
  "context": {
    "request_id": "pc-01K...",
    "transport": "mcp"
  }
}
```

`AccessDecision` 至少包含：

```json
{
  "allowed": true,
  "reason_code": "role_binding",
  "policy_revision": "42"
}
```

`reason_code` 是稳定、低敏感度枚举，用于 audit 和诊断；business 403 response 不返回 provider rule、tuple、URL、堆栈或
原始 body。`policy_revision` 允许审计和缓存关联到确定策略，但它不是授权 token。

`check_batch` 必须保持输入顺序，并对每项返回独立决定。Adapter 不能因为一个 allow 而允许整批资源。

一个业务 operation 可以解析出 1..N 个 `ResolvedAccessRequirement`。首版只支持 `all` 组合：PEP 使用一次
`check_batch` 或语义等价的 point checks，并且只有全部 decision 都为 allow 才能调用 Repository、application service、
target adapter 或 filesystem。它不提供 client-authored Boolean policy DSL。

例如 managed Skill 发布解析为：

```json
{
  "combination": "all",
  "requirements": [
    {
      "action": {"name": "artifact.read"},
      "resource": {
        "type": "artifact",
        "scope_id": "project:payments",
        "reference": {"family": "skill", "artifact_id": "retry-runbook", "revision": 4}
      }
    },
    {
      "action": {"name": "skill.publish"},
      "resource": {
        "type": "artifact",
        "scope_id": "project:payments",
        "reference": {"family": "skill", "artifact_id": "retry-runbook", "revision": 4}
      }
    }
  ]
}
```

业务请求中的 `target_id` 不进入 requirements。只有上述两个 decision 都 allow 后，Server 才解析该参数。

“scope role 或 exact role” 这类替代关系不需要 `any` 表达式。PEP 请求 child-resource action，Provider 根据可信 parent
relation 判断 scope role 是否蕴含该 action；exact Binding 则直接作用于 child resource。这样不同 Provider 不必实现任意
嵌套策略表达式。

`resolve_resource_filter` 是安全列表功能的必要能力。`AuthorizedResourceFilter` 是当前 Principal 和 action 专属的
Server-consumable filter，由两类约束组成：exact Binding 产生的有界 canonical resource key，以及父级角色产生的有界
server/scope constraint。父级 constraint 表示“Repository 可以在该 parent、请求的 Resource Kind 和 Family 内查询”，不是
客户端可提交的 wildcard。Filter 还携带 policy revision；Server 必须校验其结构和上限，再把 exact key 与 parent
constraint 的并集下推到同一次 Repository query，在计算 total、排序和分页前完成过滤。

内置 Provider 可以直接从 Binding Store 产生 exact key 和 parent constraint，因此不需要镜像整个 Artifact catalog。
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

内置 Provider、Casbin adapter 和 OpenFGA adapter 可以同时提供 `AuthorizationProvider` 与 `RelationshipWriter`。
OPA、Cerbos 或通用 AuthZEN adapter 可以只提供 decision；此时 PowerContext 的 Binding mutation endpoint 明确返回
`relationship_management_unavailable`，管理员通过外部系统配置关系。Server 不能声称 grant 成功后再只写本地影子记录。

## Access Binding model

内置 Binding Store 至少保存：

| Field | Requirement |
| --- | --- |
| `binding_id` | Server-generated opaque ID |
| `subject` | canonical `PrincipalRef` |
| `resource` | canonical exact `ResourceRef` |
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

内置 Binding Repository 属于 Server access-control component，不加入 Runtime 的 `context`、`source`、`memory`、
`artifact`、`handoff` 或 `work` application object。它可以与 Server 使用相同数据库部署，但拥有独立 schema、
migration 和 API。

## Public Access API

OpenAPI source of truth 增加以下 operation：

| Operation | Purpose | Authorization |
| --- | --- | --- |
| `GET /v1/access/me` | 返回当前 Principal 和 access-control capability | authenticated Principal |
| `POST /v1/access/check` | 检查当前 Principal 的一个 action/resource | current Principal only |
| `POST /v1/access/check-batch` | 批量检查当前 Principal | current Principal only |
| `POST /v1/access/resources/list` | 列出当前 Principal 可访问的资源 identity | current Principal only |
| `POST /v1/access/roles/list` | 返回固定角色及 action vocabulary | authenticated Principal |
| `POST /v1/access/bindings/list` | 列出调用方可管理的 Binding | `scope.delegate`, `scope.admin`, or `server.admin` |
| `POST /v1/access/bindings/create` | 创建 Family-compatible exact-resource 或管理级 Binding | resource-specific administration action |
| `POST /v1/access/bindings/revoke` | CAS revoke 一个 Binding | same administration boundary |
| `POST /v1/access/audit/list` | 查询安全审计事件 | `scope.admin` or `server.admin` |

`check`、`check-batch` 和 `resources/list` 不接受 client-specified subject，只检查当前 authenticated Principal，防止普通
用户把 API 当作人员权限枚举器。管理员代查其他 Principal、subject search 和 directory integration 留给后续 RFC。

`bindings/create` 必须接收 recipient subject，因为分享需要指定 B；调用方仍然只能在自己拥有管理权限的 resource 上创建
固定角色。Server 先根据 Resource Kind 和 Artifact Family registry 校验结构与 role compatibility，再执行 grant
administration check，最后才读取 Repository，确认 Artifact 存在、属于声明的 parent 且处于可授权状态。
不存在与不可见的资源对未授权调用方返回相同 403；只有管理判定通过后才能返回 404 或 family-specific conflict。

Access API 不负责创建、修改、fork、render 或发布业务资源。Memory、Artifact、Prompt 和 managed Skill publication 的
业务 operation 继续使用各自 contract；Binding 只表达谁能对已存在资源执行哪些 action。Publisher-safe target selection
属于 Skill publication contract；target configuration 和 operator status 属于 Server operation。三者都不进入 Access API，
也不创建 target Binding。

公共 `check` 可以用 HTTP 200 返回 `allowed=false`。业务 operation 的相同拒绝返回 403，并且不调用 application
service。Access API 只用于解释和 UI preflight，不能替代业务请求时的实时 enforcement。

## Handoff operation requirements

首版 Handoff 映射如下：

| Operation | Required authorization |
| --- | --- |
| `prepare_handoff`, `finalize_handoff`, `handoff_current_work` | `scope.contribute` on request `scope_id` |
| `commit_handoff` | `scope.contribute` on request `scope_id` |
| `continue_handoff(selection=latest)` | `scope.read` on request `scope_id` |
| `continue_handoff(selection=exact)` | `artifact.read` and `handoff.evidence.read` on exact `family=handoff` Artifact, directly or through parent `scope.read` |
| `continue_handoff(selection=prepared)` | `scope.read` on request `scope_id` |
| `acknowledge_handoff` with exact receipt | `scope.contribute` or `handoff.acknowledge` on exact Revision |
| `record_task_outcome` | `scope.contribute` on request `scope_id` |
| aggregated Handoff Report queries | scope-level read; exact Handoff grant is insufficient |
| Handoff Report administration | `scope.admin` or appropriate server administration action |

当 exact receiver 调用 Continue 时，请求必须提供 `selection=exact` 和 exact `ArtifactReference`。Server 先建立 Handoff
ArtifactResourceRef 并判定，再读取 Revision。它不能先解析 latest 再检查，也不能在 exact 缺失时回退到 latest。

Prepared Handoff 可以包含由调用方提交的完整内容，因此窄授权模式不接受 `selection=prepared`。只有已经拥有
`scope.read` 的 Principal 才能用 prepared selection 解引用 scope evidence。

## Artifact Family operation requirements

Family operation 映射如下。表中的 “scope or exact” 由 Provider 的 parent relation 实现，不让客户端选择绕过路径：

| Operation family | Required authorization |
| --- | --- |
| Memory search/list/changes | `scope.read` on request `scope_id`；exact Memory grant 不足 |
| exact Memory get | `artifact.read` on exact `family=memory` Artifact plus complete `memory_entry` selector, directly or through parent `scope.read` |
| Memory flush/remember/revise/retire | `scope.contribute`; exact viewer grant 不足 |
| approved Experience/managed Skill exact get | `artifact.read` on exact `ArtifactReference`, directly or through parent `scope.read` |
| Experience/Skill propose or generate | `scope.contribute` |
| Candidate list/get | `scope.read`; exact Artifact grant 不暴露 Candidate |
| Candidate revise/approve/reject | `scope.review` |
| approved Prompt exact get | `artifact.read` on exact `family=prompt` Artifact, directly or through parent `scope.read` |
| approved Prompt render/use | `prompt.use`, directly or through parent `scope.read` |
| Prompt propose/revise | Prompt lifecycle 定义的 Candidate operation plus `scope.contribute` |
| list enabled publication targets for an exact managed Skill | `artifact.read` **and** `skill.publish` on the same exact `family=skill` Artifact |
| publish managed Skill | `artifact.read` **and** `skill.publish` on the same exact `family=skill` Artifact |

Exact get resolver 必须从已验证 request 中直接取得完整 identity。Memory `entry_id`、Artifact `artifact_id` 或 Prompt name
都不能单独作为授权 key。Search、current-head selection、aggregated projection 和 Candidate Inbox 仍是 collection
operation，不能通过一个 exact grant 进入。

Prompt Family Access Profile 只规范 authorization vocabulary 和 resolver contract。部署只有在注册 `family=prompt` 的
immutable approved Artifact lifecycle，并提供与本节一致的 exact get/use operation 后，才能报告该 Family enabled。
不支持 Prompt domain operation 的版本仍可实现其他 Family，但不能接受 `family=prompt` Binding 或在 `roles/list` 中声称
`prompt.user` 可用。

`target_id` 是 Server 配置的发布 operation parameter，不是授权 key 或 Resource。只有 `server.admin` 可以配置、修改或
移除 target；详细 target status 由 `server.observe` 或 `server.admin` 保护。Operator status response 只能返回 target ID、
Agent kind、capability、desired/applied exact Revision、稳定 state 和安全 reason code，不能返回 host path、Agent home、
credential 或原始 OS error。在发布和 publisher target-list 请求中，Server 必须先允许 exact Skill 的两个 requirement，再
解析 `target_id` 或读取 target registry；独立的 operator status 请求则先判定 server-level action。

## OpenAPI access metadata

每个受保护 operation 在 `openapi/powercontext.yaml` 中声明 `x-powercontext-access`。生成器把该 extension 生成到
`Operation.access`，Server `_add_route()` 使用它组装 PEP wrapper。示例：

```yaml
/v1/handoff/commit:
  post:
    operationId: commit_handoff
    x-powercontext-access:
      action: scope.contribute
      resource:
        type: scope
        scope-id-from: body.scope_id
```

具有 selection-dependent policy 的 operation 使用已注册 resolver name，而不是在 YAML 中嵌入可执行表达式：

```yaml
x-powercontext-access:
  resolver: continue_handoff_access
```

Resolver 是 Server-owned、经过单元测试的确定性函数。它只能从已验证 request model 和 route metadata 建立
AccessRequest，不能读取业务 Repository 后才决定是否授权。

需要多个 requirement 的 operation 使用 resolver。Publisher target selection 和 publish 复用同一个 exact Skill resolver：

```yaml
/v1/skills/publication-targets/list:
  post:
    operationId: list_skill_publication_targets
    x-powercontext-access:
      resolver: publish_managed_skill_access

/v1/skills/publish:
  post:
    operationId: publish_managed_skill
    x-powercontext-access:
      resolver: publish_managed_skill_access
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

## Listing and pagination

列表最容易泄漏 Project 名称、scope ID、Artifact Family identity、Handoff objective 或 Candidate metadata。安全顺序为：

```text
AuthorizationProvider.resolve_resource_filter
  -> validate bounded exact keys and parent constraints
  -> Repository query applying their union
  -> stable pagination
  -> response
```

禁止以下实现：

```text
Repository.list_all -> page -> check each item -> remove denied rows
```

这种实现会泄漏总数、cursor、空洞和时序，也可能让授权用户永远看不到后面的记录。Repository 必须在同一个 query 中
应用 exact key 与 parent constraint 的并集；`total`、cursor 和 page boundary 必须只描述授权后的集合。

Artifact exact receiver 通过 `/v1/access/resources/list` 的 Resource Kind 和 Family filter 发现授权资源；这些资源不会因此
出现在聚合 Project、Workstream、Memory search、Artifact catalog 或 Candidate Inbox。只有 scope-level read 才允许进入
对应聚合查询。发布 target 不是授权资源，不出现在该列表中。拥有 exact Skill 发布权限的 Principal 通过 Skill domain
preflight 取得脱敏 target 选项；详细运维状态通过受 `server.observe` 或 `server.admin` 保护的 Server operation 查询。

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
- Source、Memory、Artifact、Prompt、PreparedContext 或 citation body；
- publication target locator、host path、credential reference 或原始 Receiver/OS error；
- 任意 exception fields、configured PDP URL 或 provider 原始 response；
- email、display name 或不必要的目录属性。

普通 log、metric 和 trace 使用同样的数据最小化边界。Public readiness 只返回稳定 component state 和安全 reason，详细
provider diagnostics 留在受保护的 operator channel。

## Consistency and failure recovery

Commit Handoff 与创建外部授权关系不是跨系统原子事务。UI 中的“发送给 B”按以下可恢复步骤执行：

1. commit 或复用同一精确 Handoff Revision；
2. 使用稳定 idempotency key 创建 Binding；
3. 只有两步都成功才显示“已分享”；
4. 第二步失败时显示“交接已保存，但 B 尚不可见”，并只重试 Binding create；
5. 不重新 prepare、commit 或创建另一个 Revision。

Binding 已成功而客户端丢失响应时，同一 idempotency key 返回原 Binding。外部 RelationshipWriter 无法提供等价幂等
保证时，adapter 必须先执行安全的 exact relationship lookup，或声明不支持 self-service mutation。

所有 Artifact Family 分享遵循相同的 “persist/approve first, bind second” 原则。Binding create 失败不回滚或重建业务
Revision；客户端只重试同一个 idempotent Binding mutation。Skill publish 则是一次受双重授权保护的 projection
operation，不创建内容 Revision，也不创建 target Binding 或改变 target authorization state。Target apply 失败保留可重试的
desired/applied 状态和安全 reason，不把本地路径或底层错误写入公共 audit。

Receipt 创建仍使用现有 exact-selection 和 evidence rules。授权判定发生在 Receipt transaction 前；授权在判定后立即
被并发撤销时，Provider 和 Binding Store 应在同一 deployment 中使用 policy revision 或 transaction fence 防止明显
越权。跨网络 PDP 的剩余 TOCTOU 窗口必须有界并记录 decision revision；首版不缓存 allow decision。

## Provider profiles

### Built-in provider

内置 profile 使用固定角色和 Server-owned Binding Store，支持 point check、batch check、从 exact/scope/server Binding
生成可下推 `AuthorizedResourceFilter`、create、revoke 和 audit。它不需要保存业务 resource inventory，是本地部署和
conformance test 的参考语义；它不提供用户密码、目录或自定义 policy language。

### Casbin adapter

Casbin adapter 可以使用带 domain 的 RBAC：

- subject 映射为 issuer-scoped opaque ID；
- domain 对 server resource 映射为 deployment access namespace，对 scope/artifact resource 映射为 canonical scope
  resource namespace；
- object 映射为 canonical server key、scope key 或包含 Family/selector 的 canonical Artifact key；
- action 使用本 RFC 的 action vocabulary；
- role assignment 和 policy mutation 通过 Casbin management API 与持久化 adapter 完成。

Casbin domain 是 adapter policy namespace，不把 `scope_id` 变成认证或 tenant 证明。Adapter 仍从 Server 传入的可信
ResourceRef 建立 domain。生成列表 filter 时，exact object policy 产生 canonical key，scope/server role assignment 产生
对应 parent constraint；Casbin adapter 不需要枚举业务 Repository。

### OpenFGA adapter

OpenFGA 适合表达用户、group、scope 和 exact child resource 的关系。所有 Artifact Family 使用一个 `artifact` object type；
object ID 包含 canonical Family、Revision 和 selector，Server 在 tuple write 前用 Family registry 校验 relation compatibility。
这样新增只读 Family 不需要新增 OpenFGA type：

```text
type user

type server
  relations
    define observer: [user]
    define admin: [user]
    define can_observe: observer or admin
    define can_admin: admin

type scope
  relations
    define parent: [server]
    define viewer: [user]
    define contributor: [user]
    define reviewer: [user]
    define delegator: [user]
    define admin: [user]
    define can_read: viewer or contributor or reviewer or delegator or admin or admin from parent
    define can_contribute: contributor or admin or admin from parent
    define can_review: reviewer or admin or admin from parent
    define can_delegate: delegator or admin or admin from parent
    define can_admin: admin or admin from parent

type artifact
  relations
    define parent: [scope]
    define viewer: [user]
    define handoff_viewer: [user]
    define handoff_receiver: [user]
    define prompt_user: [user]
    define skill_publisher: [user]
    define can_read: viewer or handoff_viewer or handoff_receiver or prompt_user or skill_publisher or can_read from parent
    define can_read_handoff_evidence: handoff_viewer or handoff_receiver or can_read from parent
    define can_acknowledge_handoff: handoff_receiver or can_contribute from parent
    define can_use_prompt: prompt_user or can_read from parent
    define can_publish_skill: skill_publisher or can_admin from parent
```

Adapter 把 `server.observe` 映射到 `server#can_observe`，把 `server.admin` 映射到 `server#can_admin`。`admin from parent`
继续使 deployment `server.admin` 单向蕴含 scope administration 和 child Artifact Family action；`server.observer` 不获得
这些权限。

Adapter 使用固定 authorization model ID 执行 Check、ListObjects 和 tuple write。Tuple 只保存 opaque ID，不保存 email
或 Handoff 文本。Model migration 在 deployment configuration 中显式切换，不自动使用“latest model”。
列表中，exact relation 可以通过 ListObjects 产生 canonical key；scope/server role 直接产生可信 parent constraint，不要求
为每一个没有 exact Binding 的业务 Artifact 预先写入 object tuple。

### AuthZEN, OPA, and Cerbos adapters

AuthZEN adapter 把 `AccessRequest` 映射为 Authorization API 的 subject、action、resource、context，把 decision 映射回
`AccessDecision`。OPA adapter 可以把相同结构作为 input document；Cerbos adapter 可以映射为 principal、resource
和 actions。

这些 adapter 的 decision interoperability 不代表 policy administration interoperability。若组织在 GitOps、IAM 或
独立管理面维护 policy，PowerContext 只消费判定和安全 resource filter，不写 policy。部署必须明确
`relationship_management=false`，Dashboard 不显示成功的 self-service share control。若 adapter 不能从 PDP search 或
可信关系数据产生 `AuthorizedResourceFilter`，还必须报告 `safe_resource_filtering=false`。

## Configuration and compatibility

Server 提供三种显式 mode：

| Mode | Behavior |
| --- | --- |
| `disabled` | 保持单用户、单 trust-domain 的现有行为；Access API 不可用，不宣称多用户隔离 |
| `legacy-static-admin` | 现有静态 Bearer 映射为 deployment-local `server.admin` Principal |
| `enforced` | 认证 Provider 和 AuthorizationProvider 都是 required dependency，所有业务 operation 执行 PEP |

升级不能因为配置了外部身份但漏配 PDP 而回退到 `disabled`。Mode 必须显式，capabilities 和 readiness 报告当前 mode 与
是否支持 relationship management、batch check 和 `safe_resource_filtering`。

`disabled` 只适用于调用方已经信任整个进程和 catalog 的本地场景。文档不能把它描述为多用户安全配置。远程、多用户或
共享 Dashboard 部署应使用 `enforced`。

`access/me` 和 readiness 还必须报告启用的 Resource Kind，以及 `artifact_families` capability map。每个 Family 条目至少
包含 `enabled`、`share_unit`、可用 action 和 grantable role；例如未实现 Prompt lifecycle 时 `prompt.enabled=false`。
`operation_capabilities.skill_publication` 单独报告 host-local managed Skill 发布及其 publisher-safe target selection 是否
可用；只有 Skill Family、两个 domain operation 和至少一个 enabled host-local target 都可用时才能为 true。它不是
Resource Kind 或可绑定 profile。Provider 不支持 `safe_resource_filtering`、多 requirement check 或 relationship mutation
时，相应 capability 必须为 false；Server 不能接受随后无法 enforce 或撤销的 Binding。

```json
{
  "resource_kinds": ["server", "scope", "artifact"],
  "provider_capabilities": {
    "safe_resource_filtering": true,
    "multi_requirement_check": true,
    "relationship_management": true
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
      "share_unit": "revision",
      "actions": [],
      "grantable_roles": []
    }
  ],
  "operation_capabilities": {
    "skill_publication": {"enabled": true}
  }
}
```

现有 OpenAPI operation 首次增加 authorization metadata 不改变 request/response domain schema，但会增加 403 response
并改变未授权行为。Generated Client 把 401、403 和 503 映射为稳定、不同的 exception；不能把 403 当作空结果。

## Implementation slices

实现按以下可独立验证的 slice 推进：

1. **Contract and Principal**：OpenAPI Access model、operation metadata、generated `Operation.access`、可信 request
   Principal 和 stable errors。
2. **Built-in PEP/PDP**：固定角色、Binding Store、`_add_route()` authorization wrapper、point/batch check、audit。
3. **Handoff exact receiver**：commit 后创建 Binding、exact Continue、citation-manifest resolver、exact acknowledge、
   revoke 和 expiration。
4. **Artifact Family Access Profiles**：统一 ArtifactResourceRef、Family registry、Memory selector、exact read/use resolver、
   角色兼容性与非传递 lineage。
5. **Skill publication**：Server-configured host-local target registry、publisher-safe selection、operator status、同一
   exact Skill 上的 read plus publish requirement，以及脱敏失败状态。
6. **Safe listing and UI**：authorized resource listing、Handoff inbox、“Shared with me”、Dashboard permission projection、
   授权后分页。
7. **MCP parity**：Principal 通过 internal bridge 传播、tool discovery UX 和调用时 enforcement。
8. **External adapters**：先完成 Casbin 或 OpenFGA 之一，再用同一 conformance suite 验证 AuthZEN-compatible PDP。
9. **Migration**：legacy static admin、configuration validation、Family capability、readiness、operator documentation。

每个 slice 都保持 Server 可运行，不能先发布只隐藏 Dashboard 按钮或只保护 HTTP、不保护 MCP 的中间状态。

## Test and acceptance plan

RFC 实现完成需要通过以下 observable scenarios：

- 无身份访问受保护 operation 返回 401；
- A 有 `scope.delegate` 时只能把所属 scope 中已存在、committed 的 exact Handoff Revision 以 `handoff.viewer` 或
  `handoff.receiver` 授予 B；其他 Artifact Family 或 role 返回 422，缺少该 action 时返回 403，且都不写 Binding；
- B 可以读取、Continue 和 acknowledge 被授予的 exact Revision；
- B 请求 latest、相邻 Revision、聚合 Handoff Report、Memory list、Source list 和 Task Outcome write 均被拒绝；
- B 只能通过被授权 Handoff 的 resolver 读取 manifest citation，不能用任意 citation 调用通用读取接口；
- `handoff.viewer` 不能 acknowledge，`handoff.receiver` 可以；
- `accepted` Receipt 不产生新的 Binding 或 scope role；
- revoke 或 expiration 后，B 的后续 access 被拒绝，authorized resource list 不再包含该 Revision；
- Binding create/revoke 的 CAS、idempotency 和 audit 行为稳定；
- 403 不泄漏资源是否存在，list cursor 和 total 只描述授权集合；
- PDP unavailable 返回 503，且 application service、Repository 和 mutation 未被调用；
- MCP internal bridge 使用原 Principal 并执行与 HTTP 相同的 deny；
- Dashboard 隐藏控制失效或被绕过时，API 仍拒绝请求；
- legacy static token 只在显式 mode 中映射为 local admin；
- `server.observer` 可以读取受保护的服务和 publication status，但不能修改 access 或 target configuration；
  `server.admin` 可以执行两类 operation，且 Built-in、Casbin 和 OpenFGA 的结果一致；
- Built-in、Casbin/OpenFGA 和 AuthZEN adapter 对同一 conformance vector 返回相同结果；
- 请求不能提交独立的 content profile；未知/disabled Family、`revision=latest`、缺失或多余 selector，以及
  Family-role mismatch 返回 422 且不写 Binding；
- `artifact.viewer` 在 Experience、Skill、Prompt 和 `memory_entry` selector 上始终只映射为 `artifact.read`，不会因 Family
  不同隐式增加 use、publish、acknowledge 或 mutation action；
- `artifact.viewer` 可以通过 `family=memory` 和完整 `memory_entry` selector get 被授权的 Memory Entry，但不能
  search/list/current/revise/retire 或读取相邻版本；
- exact Artifact viewer 可以读取 approved Experience/managed Skill Revision，但不能看到 Candidate、future Revision 或
  解引用 lineage body；
- `artifact.viewer` 只能读取 Prompt，`prompt.user` 可以显式 use；两者都不能改变宿主 instruction priority 或自动进入
  普通 recall；
- exact-resource role 即使知道 expected version，也不能 revise、retire、replace 或提交共享原件的下一 Revision；
- acknowledge 创建的 Receipt 和 publish 创建的 target projection 不改变源资源的 identity、content、Revision 或 digest；
- fork、import 或 copy 在没有目标 scope 的 `scope.contribute` 时被拒绝；授权后创建新的 identity 或 Candidate，并保持原资源
  不变；
- managed Skill publish 只有在同一个 exact Skill 的 `artifact.read` 和 `skill.publish` 均 allow 时执行，任一
  deny/unavailable 都不得解析 `target_id`、检查 host path 或写 projection；授权通过后，unknown 或 disabled target 仍必须
  拒绝发布；
- publisher target-list 只有在同一个 exact Skill 的两个 requirement 均 allow 后才能读取 registry，并且只返回 enabled
  target 的 safe identity/capability；详细 status 仍要求 `server.observe` 或 `server.admin`；
- 首版拒绝 remote Receiver target，并且不得尝试读取 remote credential 或建立网络连接；
- `skill.publisher` 可以把被授权的 exact Skill 发布到 deployment 中任一 enabled target；首版没有 target Binding 或
  per-target delegation；
- `resources/list` 的 total、cursor 和 rows 只描述当前 Principal 对所选 Resource Kind 和 Artifact Family 有权发现的集合；
- 不支持 Prompt lifecycle 的部署拒绝 `family=prompt` Binding；没有可用发布 operation 的部署准确报告
  `operation_capabilities.skill_publication.enabled=false`；
- Access Audit 不包含 token、Handoff/Memory/Artifact/Prompt 正文、Source body、target locator 或 PDP 原始错误。

Cross-component acceptance scenarios 放在 `tests/e2e/`，并通过公开 HTTP/MCP contract 断言行为。Focused tests 覆盖
Family registry、selector/canonical key、resource resolver、role mapping、Binding CAS、provider failure 和 citation
membership，不冻结 private call order。

# Drawbacks

每个业务请求增加一次授权判定，外部 PDP 还会增加网络依赖和延迟。安全列表要求 Provider 产生有界、可下推的
`AuthorizedResourceFilter`，只有 point-check 的简单 adapter 无法支持全部 Dashboard 列表。

精确 Handoff 分享必须先 commit，因此不能把临时 Prepared Handoff 直接变成可撤销的跨用户资源。这增加一步持久化，
但避免为临时 payload 发明第二套 identity 和 ACL。

判定和关系管理分离使 adapter interface 比单一 `check()` 更复杂；另一方面，假设所有外部 PDP 都允许 PowerContext 写
policy 会制造错误的可移植性承诺。

撤销只能阻止未来访问，无法删除接收方已经阅读、截图或导出的信息。包含高度敏感内容的 Handoff、Memory、Artifact 或
Prompt 仍需要最小化内容、外部数据分类和导出控制。

Artifact Family Access Profile 增加了 registry、selector、角色兼容矩阵和 conformance vector。Skill publish 还需要在
同一个 exact Artifact 上判定 `artifact.read` 和 `skill.publish`；外部 PDP 不提供原子 multi-requirement decision 时会增加
延迟，并留下必须记录 policy revision 的有界 TOCTOU 风险。

首版不把 target 纳入授权策略。拥有某个 exact Skill 的 `skill.publisher` 可以把它发布到 deployment 中任一 enabled
target。需要按 target 隔离发布权限的部署必须暂缓该能力、隔离 deployment，或等待独立 RFC 定义通用
`execution_target` Resource；本 RFC 不用一个 Skill 专属资源提前固化这套模型。

Prompt Family Access Profile 只定义授权边界，不能代替 Prompt Artifact lifecycle 和宿主 instruction-priority contract。
部署在这些业务能力完成前必须报告该 Family 不可用，因此 RFC 可以先落地其他 Family，但产品不会同时获得全部用户体验。

固定首版角色限制了组织自定义体验。企业可以在外部 PDP 映射自己的角色，但 PowerContext 公共 API 不立即提供自定义
role editor。

# Rationale and alternatives

## Chosen: independent Server PEP plus replaceable PDP

该设计保持 Handoff、Memory、Artifact、Prompt 和 Runtime model 与身份系统解耦，同时让 HTTP、MCP 和
Dashboard 共用 enforcement。稳定 action vocabulary 比稳定外部 role name 更容易跨 Casbin、OpenFGA、OPA、Cerbos 和
企业 IAM 映射。

AuthZEN-compatible request shape 使网络 PDP 有标准接入点；独立 RelationshipWriter 则诚实表达 grant mutation 并未被
AuthZEN 统一。

## Alternative: put ACL fields on Handoff or scope

在 Handoff 增加 `allowed_users`，或把 owner/tenant 编入 `scope_id`，实现看似直接，但会把身份生命周期、group expansion、
撤销、外部 policy revision 和审计塞进领域数据。不可变 Handoff 也不适合随成员变更而创建新 Revision。该方案被拒绝。

## Alternative: only use scope-level roles

只授予 `scope.viewer` 容易实现，但 B 会看到整个 Workstream 的 Memory、Source、历史和 Report。对于临时接力不符合最小
权限原则。Scope roles 保留给长期协作，exact-resource Binding 负责一次性交接或资产分享。

## Alternative: add one share API per domain

`/memory/share`、`/experience/share`、`/skill/share` 和 `/prompt/share` 会重复 Principal、Binding、expiration、revoke、audit 与
external PDP semantics，还容易让不同 transport 出现不一致。本 RFC 选择一个 Access API、统一 ArtifactResourceRef、
Family role compatibility 和 resolver；业务 API 仍由各 domain 拥有。

## Alternative: 每个 Artifact Family 使用一个 Resource Kind

为 `handoff`、`memory_entry`、`experience`、`skill` 和 `prompt` 分别增加 `ResourceRef.type`，会重复 scope parent、exact
Revision、canonical key 和只读分享结构；每新增一个 Family 还必须扩展 OpenAPI discriminator 和外部 PDP object type。
它也会让 `ResourceRef.type` 与 `ArtifactReference.family` 成为两个可能冲突的内容 discriminator。本 RFC 选择统一
`artifact` Resource Kind，由 Server 从 `ArtifactReference.family` 派生 Access Profile；只有 Memory 等需要更细授权单元的
Family 增加显式 selector。

## Alternative: automatically recall every shared resource

把所有 exact grant 自动加入 PreparedContext 会混淆可见性与相关性，扩大 token budget，并让不可信 Prompt 或 Skill 在接收方
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

Token role 简单但对 exact Handoff grant、撤销、large resource set 和 policy update 不友好。Token 可以携带可信 identity
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

# Unresolved questions

以下问题需要在 RFC 合并前确认，但不改变核心安全边界：

- 首个外部 conformance adapter 选择 Casbin 还是 OpenFGA；
- 内置 Provider 是否随默认 Server extra 安装，还是作为独立 optional extra；
- Dashboard 如何从部署方的身份目录选择 canonical recipient；目录搜索本身不由本 RFC 的 Access API 提供；
- enforced deployment 是否要求 Provider 同时支持 `safe_resource_filtering`，还是允许禁用相关 Dashboard 列表；
- `handoff.receiver` 的产品默认过期时间是否由 deployment policy 决定，还是 UI 必须每次显式选择；
- exact receiver 创建 Receipt 后，UI 是否建议管理员另行授予 `scope.contributor`，但不能自动执行该升级；
- Prompt Artifact 的后续 lifecycle 采用固定 Review policy，还是区分个人私有模板与组织 approved template。

以下问题明确推迟：custom role、organization hierarchy、cross-tenant export、anonymous share link、temporary elevation、approval
workflow、通用 Source object-level ACL、动态 Memory collection、Artifact catalog 分享和自动跟随 future Revision。它们需要
独立威胁模型和 RFC。

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
- 在独立 Receiver distribution contract 和 trust-boundary review 完成后增加 remote managed Skill target；
- 带显式成员和 Revision manifest 的共享 collection，以及经过 Context policy 的订阅式选择；
- 在有明确 revocation-staleness guarantee 后增加 bounded decision cache。

这些扩展不能改变首版不变量：`scope_id` 不是 ACL，资源内容不授予权限，exact grant 不跟随 future Revision，读取不自动
进入 Context 或获得执行权，所有 transport 在 Server PEP fail closed。
