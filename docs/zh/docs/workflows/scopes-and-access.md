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

## 在 Dashboard 中读取团队内容

成员使用自己的凭据登录同一个 Server。内容和材料请求继续使用该身份，不借用管理员权限。
当前 `GET /v1/scopes` 和 `GET /v1/scopes/default` 都要求 `server.observe`；范围选单不是自动过滤的
“我的项目”列表。普通项目成员应使用管理员提供的 `/dashboard/home?scope=<scope_id>` 链接。

| 已有权限 | Dashboard 中的访问范围 |
| --- | --- |
| 一个 Scope 的 `scope.viewer` | 该 Scope 的记忆、经验、技能、交接和用量；不要求 `server.observer` |
| 只有 `scope.admin` 或 `server.admin` | 管理权限不自动授予内容阅读权，仍需相应阅读角色 |
| 一条经验、技能或交接 Artifact 的阅读角色 | 可通过包含 Scope、Artifact ID 和 Revision 的详情链接阅读；不因此获得 Scope 目录或其他记录 |
| 只有父 Scope 的阅读权 | 不自动获得子 Scope 的内容，子 Scope 需另行授权 |

Server 默认 Scope 是部署级选择，不是每位成员的个人默认值。没有 `server.observe` 时，无参数首页返回
权限提示，不能据此判断该成员没有项目阅读权。带 Scope 的链接可直接读取已授权项目，页面会提示范围列表不可用。
即使能查询默认 Scope，仍需该 Scope 的内容读取权限。不要仅为使用选单而扩大成员的 Server 观察权限。

登录表单提交后统一返回无参数首页；权限受限的成员需要再打开原项目链接。切换范围不会修改 Server 默认值，
用量页只统计当前 Scope。这些是当前入站和发现能力的限制，Dashboard 没有成员默认项目设置。

能够阅读记录不代表能够读取其原始材料。材料权限不足时，记录保持可读，材料区域显示错误；授权关系变化后，
后续 API 请求按最新权限判断。Dashboard 不提供授权入口，管理员通过现有 Access API 管理 Binding。
