---
title: Scope 与访问控制
description: 隔离项目上下文、将 Agent 绑定到 Scope，并配置访问权限。
---

# Scope 与访问控制

Scope 选择项目上下文边界。其不透明 ID 用于标识数据，不负责认证调用者，也不授予数据访问权限。

## 隔离项目并绑定 Agent

1. 通过 `GET /v1/scopes/default` 读取默认 Scope，或通过 `POST /v1/scopes` 创建独立 Scope。
   保留返回的 `scope_id`，不要从目录、仓库名或 Agent 会话 ID 推导它。
2. 通过宿主的显式 Scope 设置或其支持的持久绑定操作选择该 Scope，具体设置见[宿主指南](../integrations/index.md)。
   没有绑定或显式选择时，宿主可能共用 Server 默认 Scope；切换项目目录本身不会建立隔离。
3. 保存项目信息前，在宿主诊断或 API 响应中检查解析后的 Scope。

父子关系用于组织 Scope，显式上下文引用用于描述复用；两者都不授予访问权限。
精确、子树和全部 Scope 视图改变的是查看范围，不改变调用者权限。

绑定解析返回 `404 scope_not_found` 表示没有可用绑定，或显式传入的 Scope ID 不存在。
如果持久绑定或默认指针存在，但指向的 Scope 已丢失，服务返回
`409 scope_binding_target_missing`，并在 `error.details.scope_id` 中保留原目标 ID。
解析在该绑定处停止，不回退到后续绑定或默认 Scope。客户端的 `Ensure()` 必须传播此冲突，
由运营者调查数据丢失、恢复原 Scope 或显式修复绑定；不能自动创建新 Scope 并覆盖绑定。
普通直接查询缺失 Scope 仍返回 404，因为仅凭 ID 无法判断它是否曾经存在。
启用权限控制时，Subject Source 写入在返回该冲突前，必须先校验调用者对绑定目标的贡献权限；
无此权限的调用者收到 403，响应不包含目标 ID。

## 启用访问控制

共享或远程使用时，按[部署认证](../operate/deploy-server.md)设置 `POWERCONTEXT_SERVER_ACCESS_MODE=enforced`。
内置静态 Bearer token 代表一个管理员 Principal，不能区分各个用户。
多用户部署需要注入 Authentication Provider 和相应的 AccessControlService，见[配置](../operate/configuration.md)。

通过 `GET /v1/access/me` 检查当前身份和 Provider 能力。Role 与 Binding 控制 Server、Scope 和 Artifact 操作。
创建 Scope 需要 `server.admin`；修改 Prompt 需要当前 `scope.admin` 权限。
能够读取候选材料不代表能够批准候选材料。

需要 Agent 提交新的 Experience 或 Skill Candidate、再由独立审核者决定时，为不同的已认证身份分别分配
`scope.contributor` 和 `scope.reviewer`，并将审核者凭据保留在 Agent 环境之外。同一 Principal 同时持有两种权限时，
仍可批准自己的 Candidate。角色分配示例、拒绝后重新提交的流程和已有 Artifact 的权限限制见
[分离 Agent 提交与审核](review-candidates.md#分离-agent-提交与审核)。

定时处理需要有权限的后台 Principal。标签查询需要 `scope.read`，修改目标标签遵循目标自身的写权限。
相关流程见[标签](manage-artifact-tags.md)和 [Scope Profile](use-profiles.md)。

## 团队使用入口

团队通过 API、MCP 或宿主集成使用各自的身份与权限。Dashboard 仅用于个人和演示部署，可选用静态 token 鉴权，
不作为 RBAC 团队入口；团队部署保持 `POWERCONTEXT_SERVER_DASHBOARD_ENABLED=false`。

## 恢复外部所有权写入

`create_server_app` 将内置 Runtime 与业务事务之外的 relationship/audit store 组合时，
受支持的创建回调在 `pc_access_owner_recovery` 保存所有权 intent。交付失败返回 503，此时业务对象可能已提交。
保留原业务库，Server 重启后会继续重试 pending intent。不要盲目重复非幂等创建、把重试者指定为 owner，或修改 intent payload。

能访问嵌入应用的运营者可以查看元数据，并显式重试已有 intent：

```python
recovery = app.state.owner_recovery
if recovery is not None:
    pending = await recovery.pending(scope_id=scope_id, limit=100)
    for operation in pending:
        await recovery.replay(operation.operation_id)

    after = None
    while True:
        page = await recovery.inventory_gaps(scope_id, kind="artifact", after=after, limit=100)
        # 仅向可信运营目标导出 page.items。
        after = page.after
        if after is None:
            break
```

`pending` 同样支持用 operation ID 作为 `after` 分页。使用 `kind="candidate"` 盘点拟定归属凭证。
清单只返回 identity 元数据，不返回 Artifact 或 Candidate 正文。`after` 跟随扫描过的 identity，
因此 `items` 为空时也可能有下一页。`unknown` 表示没有可信 intent；`delivered_relation_missing` 表示完成 receipt
对应的外部 relation 已缺失；`legacy_selector_required` 表示旧 Memory collection 需要显式 entry 清单。
这些情况需要基于可信原始证据确定 operator policy；重放不接受新 owner，也不会恢复已完成 receipt 对应的被删除 relation。
已有独立读取授权仍有效，但不会填补所有权缺口。

外部 Artifact 恢复要求支持按事件幂等的 `append_audit_once`；缺少该能力会在业务提交前失败。
Relationship writer 必须遵循原有不可变 owner/key 以及精确 Candidate target 契约。
冲突保持 pending，供运营者调查。完成 receipt 持续保留，应纳入业务库备份和保留策略。
没有新增公共 recovery HTTP/MCP 接口或 CLI。

恢复测试使用两个独立真实 SQLite 数据库，注入交付/确认失败，验证应用生命周期重启与并发重放。
这不等于远程权限服务、OceanBase/MySQL、多 Server 节点或不具备 commit hook 的自组装 Runtime 已通过验收。
