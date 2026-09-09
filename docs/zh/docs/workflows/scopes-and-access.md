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
3. 保存项目信息前，在 Dashboard 或宿主诊断中检查解析后的 Scope。

父子关系用于组织 Scope，显式上下文引用用于描述复用；两者都不授予访问权限。
精确、子树和全部 Scope 视图改变的是查看范围，不改变调用者权限。

## 启用访问控制

共享或远程使用时，按[部署认证](../operate/deploy-server.md)设置 `POWERCONTEXT_SERVER_ACCESS_MODE=enforced`。
内置静态 Bearer token 代表一个管理员 Principal，不能区分各个用户。
多用户部署需要注入 Authentication Provider 和相应的 AccessControlService，见[配置](../operate/configuration.md)。

通过 `GET /v1/access/me` 检查当前身份和 Provider 能力。Role 与 Binding 控制 Server、Scope 和 Artifact 操作。
创建 Scope 需要 `server.admin`；修改 Prompt 需要当前 `scope.admin` 权限。
能够读取候选材料不代表能够批准候选材料。

定时处理需要有权限的后台 Principal。标签查询需要 `scope.read`，修改目标标签遵循目标自身的写权限。
相关流程见[标签](manage-artifact-tags.md)和 [Scope Profile](use-profiles.md)。
