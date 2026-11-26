---
title: "RFC 2857：PowerContext 组织、团队与项目"
description: "定义组织、团队与项目的关系，并说明组织管理所需的数据结构、接口和权限。"
---

- Proposal Name: `organization_team_and_project`
- Start Date: 2026-11-26
- RFC PR: [oceanbase/powercontext#2857](https://github.com/oceanbase/powercontext/pull/2857)

# Summary

本 RFC 定义 PowerContext 的组织、团队、成员和项目模型。组织直接拥有团队和项目，团队负责管理成员，
同一成员可以参与组织内的多个项目。组织部署首版每套共享数据承载一个组织，可由多个 Server 共同提供服务。
独立使用时，用户通过相同的 Server 和 Scope 接口处理自己的记忆。

# Motivation

组织需要统一管理成员和团队名册，团队和项目的边界则服务于不同的协作关系。成员换队时，其现有项目参与
关系不应随之变化。同一组织需要能通过增加 Server 实例扩容，同时保持成员、项目和授权的一致性。

# Guide-level explanation

## 为自己部署 PowerContext

小梅首次在空部署注册时，若没有组织邀请，PowerContext 会创建一个组织，将小梅加入为管理员，并将该
组织作为她的默认组织。她可以直接使用入组时分配的个人 Scope 整理笔记，以后再按需要添加团队和项目。
已有部署中的 Scope 仍可按原方式使用。

## 加入组织

组织是管理员接纳用户、管理成员资格的入口。管理员通过邮箱邀请小梅。如果小梅已有账号，她可以接受
待处理邀请。如果她还没有账号，则使用邀请码完成注册。待处理邀请会出现在成员名册中，成员资格生效后，
小梅就可以在组织内开展工作。

## 建立团队并安排项目成员

管理员创建平台团队，以及搜索和导入两个项目。团队名册记录小梅属于哪个团队。两个项目都直接归组织，
管理员分别授予小梅两个项目的访问权限。小陈同样可以加入平台团队，只参与搜索项目。

```text
Organization
  +-- Platform team ---- roster: Mei, Chen
  +-- Search project --- participants: Mei, Chen
  +-- Import project --- participant: Mei
```

小梅可以打开任意一个项目，小陈按所获权限打开搜索项目。小梅在搜索项目中查找或记录决策时，项目记忆
是其中一个应用场景。对应的 Scope 和访问规则见详细设计。

## 换队、退出项目与移出组织

如果小梅从平台团队调到另一个团队，管理员只需更新她的团队归属。她不再访问平台团队的团队记忆，
原有项目权限继续有效。如果小梅结束导入项目的工作，管理员可以单独撤销该项目的访问权限。
将小梅移出组织后，她失去对组织内团队和项目的访问权限，已有项目材料仍留在原项目。

| 管理员操作 | 小梅的团队归属 | 小梅的项目访问 |
| --- | --- | --- |
| 将小梅调到另一团队 | 改为新团队 | 保留搜索和导入项目权限 |
| 撤销小梅的导入项目权限 | 保持原团队 | 保留搜索项目权限 |
| 将小梅移出组织 | 解除团队归属 | 终止组织内的项目访问 |

## 由多个 Server 服务同一个组织

运维人员可以增加第二个 Server 来服务同一组织。两个 Server 共用同一个 OceanBase 逻辑数据库，
用户从任一 Server 访问，看到的成员名册、项目和权限判定都一致。管理员可以在一个 Server 发出邀请，
小梅可以在另一个 Server 接受。

# Reference-level explanation

## 注册、默认组织与初始 Scope

注册和邀请接受沿用现有账号身份。持有效组织邀请的账号加入邀请指定的组织。没有组织邀请的新账号注册时，
如果逻辑数据库尚无组织，则创建组织，注册者成为初始管理员。如果已有组织，注册返回 `409`，需要由该
组织发出邀请。无效、过期或已撤销的组织邀请应被拒绝，不进入创建组织的分支。

```text
Registration with an organization invitation --> join invited organization
Registration without an organization invitation
  +-- empty logical database -----------------> create organization + admin
  +-- organization already exists ------------> invitation required

Join or create --> activate membership + personal Scope --> first session
```

两条成功路径共用成员激活流程。成员关系、个人 Scope 和 Access 绑定一并发布，重试复用同一个结果。
系统将本次加入或创建的组织作为账号的默认组织。首版每库只有一个组织，默认值从活跃成员记录推导，无需为
账号另存字段。客户端在首次组织会话中选中该成员的个人 Scope。部署级默认 Scope、已有绑定和数据都保留。

之后的 Scope 选择沿用 [RFC 1345](1345_scope_organization_and_agent_integration.md) 的顺序，即显式
选择、持久化绑定、配置的默认 Scope。默认组织和 Scope 选择本身不授予读写权限。

## 组织边界与共享数据库

首版每个**逻辑数据库**最多有一个组织。多个 Server 可以连接同一 OceanBase 实例中的同一逻辑数据库，
共同服务该组织。Server 副本共享部署身份、组织记录、成员、团队、项目、Scope 和 Access 绑定。
连接同一物理实例但使用不同逻辑库的 Server，则各自使用其逻辑库的配置和数据。

```text
Server A ----+
            +--> OceanBase logical database --> Organization
Server B ----+                                  +-- Teams
                                                +-- Projects
```

首位无组织邀请的获准注册者在共享数据库中建立组织。并发初始化必须得到同一个组织，配置不一致时初始化失败。
副本从共享数据库读取组织和角色状态，使用一致的 Access 部署
标识与身份映射。副本的进程标识不进入组织、成员或 Scope 的键。同一账号通过任一副本发起请求，使用
同一组织成员记录。邀请码消费、成员修改和 Scope 分配依赖共享数据库事务与约束。副本不得用过期的本地
状态继续授权，成员或角色撤销提交后，任一副本的后续授权都须看到撤销结果。迁移按逻辑数据库协调一次，
遵循现有的[共享数据库迁移流程](../docs/operate/database-migrations.md)。

账号的默认组织由本部署内唯一的活跃成员关系解析，首版无需另存字段或提供切换入口。路由中的组织 slug 必须指向本部署的组织。
持久化键、查询及授权仍显式使用 `org_id`，团队和项目按组织定位，成员用 `(org_id, account_id)` 定位。
账号身份、Access 部署标识和组织 ID 分开保存。未来可将组织解析改为请求级选择，再验证账号在该组织的成员
关系。已有组织 ID、记录和 Scope ID 无需重编号。多组织上线前仍需补齐已有独立 Scope、数据库查询、后台任务和其他共享状态的组织隔离。首版上线后，已有
独立 Scope 沿用现有访问路径。受组织管理的 Scope 无法验证归属时，必须拒绝访问。

共享库验收使用部署标识相同的两个 Server。通过 A 创建团队和项目，在 B 上应发现相同 ID。通过任一副本
接受邀请，应只产生一份成员记录和个人 Scope。通过 A 撤销权限后，B 上的下一次请求应被拒绝。重启 B 后，
应仍能解析相同的组织与 Scope。

## 应用场景：团队、项目与个人记忆

| 层级 | Scope 分配 | 用途 |
| --- | --- | --- |
| 组织 | 首版不分配组织级 Scope | 管理账号、成员、团队和项目。 |
| 团队 | 分配团队自己的 Scope | 保存团队持续工作所需的记忆。 |
| 项目 | 分配项目自己的 Scope | 保存项目记忆，供组织内参与该项目的成员协作。 |
| 个人 | 给成员分配个人 Scope | 保存个人记忆，团队或项目关系不会因此获得读取权限。 |

```text
PowerContext Server
  +-- Organization
      +-- Platform team  ------> Team Scope
      +-- Search project ------> Project Scope
      +-- Mei ----------------> Personal Scope
```

团队、项目和个人的 Scope 各自拥有其记忆。团队归属、项目参与和选择 Scope 本身都不授予读写权限。
组织及成员状态决定主体是否有资格访问本部署，目标 Scope 和具体操作仍需授权。

## 数据结构与约束

下表定义首版逻辑 SQL 结构，物理类型可由各后端映射。ID 为不透明字符串，时间戳使用 UTC，字段默认
非空，表中 `?` 表示可空。账号 ID 来自现有 Server `PrincipalRef`，本 RFC 不增加账号或凭据表。

| 表 | 列 | 键与约束 |
| --- | --- | --- |
| `pc_organizations` | `org_id, slug, name, state, creator_id, created_at, updated_at` | 主键 `org_id`，`slug` 唯一。由空部署内首位无组织邀请的获准注册者建立。状态为 `initializing / active / removed`。 |
| `pc_organization_members` | `member_id, org_id, account_id?, invite_email, kind, state, invited_at?, created_at, updated_at, team_id?, personal_scope_id?` | 主键 `member_id`，外键 `org_id`，账号绑定后 `(org_id, account_id)` 唯一；索引 `account_id` 与 `(org_id, invite_email)`。活跃成员必须绑定账号与个人 Scope。 |
| `pc_invitation_codes` | `invitation_id, code, email, org_id, member_kind, expire_seconds, created_at, used_at?, deleted_at?` | 主键 `invitation_id`，`code` 唯一，外键 `org_id`。组织注册邀请码仅在未使用、未撤销且未过期时有效。 |
| `pc_teams` | `org_id, team_id, name, scope_id` | 主键 `(org_id, team_id)`，外键 `org_id`，`scope_id` 唯一。 |
| `pc_projects` | `org_id, project_id, name, scope_id` | 主键 `(org_id, project_id)`，外键 `org_id`，`scope_id` 唯一。 |

成员的 `kind` 为 `admin` 或 `collaborator`，`state` 为 `invited / active / rejected / expired`。
首版通过成员行上的可空 `team_id` 表达主要团队，使用 `(org_id, team_id)` 复合外键确保同组织。
项目不保存团队 ID。首个记忆场景用既有 Access 的项目 Scope 直接用户角色绑定表达项目参与，无需项目成员表。

每个团队、项目和活跃成员分别绑定一个由现有 Scope 创建流程分配的专属 Scope。`personal_scope_id`
在成员表内唯一。三类绑定都引用现有 Scope，首版不接管已有 Scope，因此同一个 Scope 不能再次分配给其他对象。
Scope 和归属记录一起发布，创建未完成的 Scope 不出现在任何发现入口。

## 组织初始化与成员流程

无组织邀请的首次注册创建组织时，组织先进入初始化状态，并为创建者建立管理员关系。邀请注册和已有
账号接受邀请均沿用同一成员激活流程。首次建组织必须通过部署现有的引导授权。

```text
Invite a registered account --> pending membership --> accept / reject
Invite an unregistered email -> pending membership + signup code
                                                      |
                                                      v
                                           signup joins organization
```

创建者的成员记录和个人 Scope 就绪后，组织才进入活跃状态。已有账号只处理绑定到自己的待接受记录，
接受后转为活跃，拒绝后转为已拒绝。注册邀请码有效期为七天，注册身份必须匹配受邀邮箱。
消费邀请码与激活成员必须一并成功。重试及并发接受不得创建重复的成员或个人 Scope。
移除成员删除成员关系；移除尚未注册的受邀者还会撤销该组织和邮箱的注册邀请码。

## HTTP API

以下路径相对于 `/v1`，均要求认证。路径参数 `slug` 是组织 slug，`member_id` 是该组织的成员记录 ID。
JSON 使用小写下划线字段名。带对象或数组的成功响应为 `200`，空数组表示无记录，`204` 无响应体。
请求体中的 `?` 表示字段可省略。

| 方法和路径 | JSON 请求体 | 成功响应 | 行为 |
| --- | --- | --- | --- |
| `GET /orgs/{slug}` | `—` | `Organization` | 活跃成员读取组织详情。 |
| `POST /orgs/{slug}/edit` | `{name?, slug?}` | `204` | 管理员更新提供的字段。 |
| `GET /orgs/{slug}/members` | `—` | `Member[]` | 活跃成员列出名册与状态。 |
| `GET /orgs/{slug}/member-kinds` | `—` | `MemberKind[]` | 返回固定角色选项。 |
| `POST /orgs/{slug}/members/invite` | `{email, org_member_kind}` | `204` | 管理员邀请已有账号或未注册邮箱。 |
| `POST /orgs/{slug}/members/{member_id}/edit` | `{member_kind}` | `204` | 管理员更改目标成员的角色，不可编辑自己。 |
| `DELETE /orgs/{slug}/members/{member_id}` | `—` | `204` | 管理员移除成员或取消邀请，不可移除自己。 |
| `GET /my/orgs` | `—` | `Member[]` | 返回调用者当前的成员记录，最多一条。 |
| `GET /my/org-membership-requests` | `—` | `MembershipRequest[]` | 列出发给调用者的待接受邀请。 |
| `POST /my/org-membership-requests/{slug}/accept` | `—` | `204` | 接受调用者自己的待处理邀请。 |
| `POST /my/org-membership-requests/{slug}/reject` | `—` | `204` | 拒绝调用者自己的待处理邀请。 |

响应结构如下。时间戳使用 RFC 3339 字符串，ID、名称、邮箱、角色和状态均为字符串。`account_id`
可为 `null`，表示受邀者尚未绑定账号。

| 结构 | JSON 字段 |
| --- | --- |
| `Organization` | `name, member_count, state, created_at, updated_at`。其中 `member_count` 为整数，统计该组织的成员记录，包括待接受的记录。 |
| `Member` | `id, account_id, account_name, account_email, account_avatar_url, org_slug, org_name, org_state, member_kind, state, created_at, updated_at`。 |
| `MembershipRequest` | `org_name, org_slug, account_id, state, created_at, updated_at`。 |
| `MemberKind` | `value`，为 `admin` 或 `collaborator`。 |

参数无效返回 `400`，未认证返回 `401`，权限不足返回 `403`，目标组织、成员或调用者的待处理邀请
不存在时返回 `404`，重复邀请活跃成员返回 `409`。错误使用现有 PowerContext HTTP 错误结构。
组织创建由首次注册流程完成。向已有组织无邀请注册也返回 `409`。首版无需组织切换入口。记忆操作沿用现有 Scope API。

### 团队与项目管理接口

以下接口只管理归属与发现。项目、团队和个人的记忆内容继续使用已有 Scope API。路径中的 `team_id`
和 `project_id` 是部署组织下的不透明 ID。

| 方法和路径 | JSON 请求体 | 成功响应 | 行为 |
| --- | --- | --- | --- |
| `POST /orgs/{slug}/teams` | `{name}` | `201 Team` | 管理员创建团队和专属 Scope。 |
| `GET /orgs/{slug}/teams` | — | `Team[]` | 管理员列出团队以管理名册，其他活跃成员仅发现自己的团队。 |
| `PUT /orgs/{slug}/members/{member_id}/team` | `{team_id: ID or null}` | `204` | 管理员设置活跃成员的团队，`null` 解除归属。外组织或未知团队返回 `404`。 |
| `POST /orgs/{slug}/projects` | `{name}` | `201 Project` | 管理员创建项目和专属 Scope。 |
| `GET /orgs/{slug}/projects` | — | `Project[]` | 管理员可以管理项目元数据。其他活跃成员只发现具有读取权限的项目。 |

`Team` 返回 `team_id, name, scope_id`，`Project` 返回 `project_id, name, scope_id`。
以上字段均为非空字符串。`Member` 响应增加可空 `team_id`。成员可通过既有的
`POST /v1/access/resources/list` 发现其已获授权的 Scope，包括组织分配的个人 Scope。
有权管理项目元数据的调用者仍须通过数据面检查才能读取其记忆。

## RBAC、属性检查与现有 Scope 权限

组织读取允许活跃协作者和管理员，组织编辑要求活跃管理员。角色决定操作范围，
账号、目标组织与成员状态是授权时检查的属性。路由中的 slug 用于解析目标组织。

```text
Authenticated account + organization slug
                    |
                    v
          Resolve organization ID
                    |
                    v
     Find membership for account + org
                    |
             active membership?
              /             \
            no               yes
           deny         read allowed
                              |
                        admin role?
                              |
                        yes: edit allowed
```

PowerContext 已有的 `scope.read` 和 `scope.contribute` 等操作仍由 Server Access 判定。
Source 和 Artifact 修订版本仍归 Scope 所有。产品组织与 Scope 的 `Organization Parent` 是不同的概念，
后者用于组织 Scope，父关系不授予读取权限。[RFC 1345](1345_scope_organization_and_agent_integration.md)

受组织管理的 Scope 沿用现有 Access 的绑定创建、替换和撤销入口。
组织管理员只能将团队 Scope 角色授予当前团队的活跃成员，项目 Scope 角色只能授予当前组织的活跃成员，
并且任何授权仍须满足 Access 原有的管理权限。创建团队或项目的管理员获得该 Scope 的
`scope.admin`，该角色不隐含 `scope.read`。初始成员和新成员激活时，通过现有 Access 授予其个人
Scope 的 `scope.contributor`，同时发布成员记录。

## 记忆场景的访问行为

- **团队。** 管理员创建团队并分配团队 Scope。管理员将活跃成员加入团队、移除或调往另一团队。
  换队只修改组织成员行上的团队字段，既有项目绑定不变。团队记忆要求当前团队归属与对应 Scope 操作权限。
- **项目。** 管理员在当前组织建立项目及其 Scope。符合组织成员条件的用户可通过现有 Access 获得多个
  项目 Scope 的角色。一次记忆操作只使用明确选定的项目，Scope 授权按当前操作及目标执行。
- **个人。** 初始管理员和获准入组的成员各获得一个个人 Scope，在成员激活时一起发布。
  数据面只允许该成员本人在其个人 Scope 内由 Access 授权的操作。
- **移除成员。** 删除名册记录前撤销成员在组织内的 Scope 绑定。删除生效后，成员资格检查立即拒绝后续
  访问，即使另有资源绑定。移除不迁移记忆或改变既有 Scope 的归属。以后重新加入须重新获得项目权限。
- **访问边界。** 组织活跃、成员活跃与 Scope 操作权限必须同时满足。该检查应用于读取、写入、发现和
  Prepare Context 的候选 Scope；Context Reference 不代替授权。管理组织或名册本身不赋予读取记忆的权限。

# Drawbacks

首版托管多个组织需要独立的逻辑数据库和 Server 部署上下文，增加运维成本。同组织仍可增加 Server 副本。

# Rationale and alternatives

一套共享数据承载一个组织，已经能支持组织内的多团队和多项目协作，并简化了账号的组织选择。
如果同一服务承载多个组织，每次请求都需要确定组织上下文，成员和发现操作按组织隔离，并检查目标团队、
项目、个人 Scope 和引用的资源是否都归该组织。个人 Scope 按组织和成员定位，同一个人可以在不同组织
拥有各自的个人 Scope。是否需要额外分配组织级 Scope，则取决于是否要支持全组织共享记忆。

# Prior art

[RFC 1345](1345_scope_organization_and_agent_integration.md) 定义了 Scope 的持久化记忆归属。
Server Access 已能对 Scope 资源判定操作权限。

# Unresolved questions

- 已有独立 Scope 的认领，以及退出组织后保留的个人 Scope 的处置，需要独立的生命周期规则。
- 是否需要管理员以外的团队管理角色，留待后续工作。

# Future possibilities

未来可在同一服务内托管多个组织，沿用已经按组织归属的表和稳定 ID，补充请求级组织解析和数据隔离。
