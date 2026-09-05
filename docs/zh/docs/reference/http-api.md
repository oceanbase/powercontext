---
title: HTTP API
description: 通过 HTTP 调用 PowerContext Server，并找到完整 OpenAPI 契约。
---

# HTTP API

HTTP API 是访问 PowerContext Server 的语言无关接口。默认 base URL 为 `http://127.0.0.1:8000`。

如果你要把 PowerContext 接入自己的 AI 应用，而不是查找单个字段，请先完成
[HTTP API 生命周期教程](../tutorials/api-quickstart.md)。本页保留为路径、契约和错误语义参考。

## 查看契约

本地未启用鉴权的 Server 运行后，可以打开 `/docs` 查看交互式 Scalar API 参考，或打开 `/openapi.json` 获取该进程实际提供的
契约。

仓库中的契约源文件是
[`openapi/powercontext.yaml`](https://github.com/oceanbase/powercontext/blob/master/openapi/powercontext.yaml)。
生成客户端或检查全部请求、响应字段时以它为准。启用 Server 鉴权后，`/docs` 仍保持公开以渲染参考页，但在其中发起的
请求仍需鉴权。`/openapi.json` 需要 Bearer token。浏览器地址栏无法添加该 header；应使用可信的代理或浏览器配置注入
header，或者通过带鉴权的命令下载 `/openapi.json`。不要把 token 放进 URL。

## 请求鉴权

默认的 loopback 安装不启用鉴权。运维者启用鉴权后，API 和 MCP 请求需要携带：

```http
Authorization: Bearer <token>
```

下面的示例使用两个可选 shell 变量：

```bash
POWERCONTEXT_URL=http://127.0.0.1:8000
POWERCONTEXT_AUTH_HEADER="Authorization: Bearer ${POWERCONTEXT_CLIENT_API_TOKEN}"
```

未启用鉴权时，请去掉 `--header "$POWERCONTEXT_AUTH_HEADER"`。`/health/live` 和 `/health/ready` 始终公开。
允许远程访问前，请先阅读[部署 Server](../how-to/deploy-server.md)。

Server 启用鉴权时，可以用以下命令下载该进程实际提供的契约：

```bash
curl --fail \
  --header "$POWERCONTEXT_AUTH_HEADER" \
  --output powercontext-openapi.json \
  "$POWERCONTEXT_URL/openapi.json"
```

## 保存并搜索一条 Memory

将 `POWERCONTEXT_SCOPE_ID` 设置为 `create_scope` 返回的已有 ID，并在不同会话中复用。会话 ID 不是持久的
项目身份。

保存一条已经整理好的 Memory：

```bash
curl --fail \
  --request POST \
  --header 'Content-Type: application/json' \
  --header "$POWERCONTEXT_AUTH_HEADER" \
  --data "{
    \"scope_id\": \"${POWERCONTEXT_SCOPE_ID}\",
    \"kind\": \"decision\",
    \"text\": \"公开 API 保持异步。\"
  }" \
  "$POWERCONTEXT_URL/v1/memory/remember"
```

响应包含精确 citation。后续请求需要修订、停用或读取这个不可变 revision 时，应保留并传回该 citation。

在同一个 scope 中搜索 active entry：

```bash
curl --fail \
  --request POST \
  --header 'Content-Type: application/json' \
  --header "$POWERCONTEXT_AUTH_HEADER" \
  --data "{
    \"scope_id\": \"${POWERCONTEXT_SCOPE_ID}\",
    \"query\": \"公开 API\",
    \"limit\": 5
  }" \
  "$POWERCONTEXT_URL/v1/memory/search"
```

## 把一个逻辑 Handoff 授予接收者

`scope_id` 本身从不授予权限。Handoff owner 或获授权的 delegator 通过创建 Binding，把一个逻辑 committed Handoff 授予接收者已经认证的
Principal：

```bash
curl --fail \
  --request POST \
  --header 'Content-Type: application/json' \
  --header "$POWERCONTEXT_AUTH_HEADER" \
  --data '{
    "subject": {"type": "user", "id": "idp:user-b", "description": "用户 B"},
    "resource": {
      "type": "artifact",
      "scope_id": "project:example",
      "identity": {"family": "handoff", "artifact_id": "handoff-42"},
      "selector": null
    },
    "role": "handoff.receiver",
    "idempotency_key": "handoff-42-to-user-b"
  }' \
  "$POWERCONTEXT_URL/v1/access/bindings/create"
```

接收者可以读取和确认这个 Handoff 的历史、当前及未来 Revision。Continue 会展示所选 Revision 的不可变 manifest 中的
citation，并检查这些被引用资源，不需要为每条 citation 再创建 Binding。这种 manifest 范围内的检查不会授权通用的
Source、Memory 或 Artifact 接口；除非另有 scope 或 Artifact role，否则接收者仍不能发现其他 Handoff 或读取父 scope。
它只能对已绑定的逻辑 Handoff 请求 `latest`。用 `/v1/access/me` 确认部署建立的 Principal，用 `/v1/access/check`
检查一个由 `all` 或 `any` 组合的权限要求，
用 `/v1/access/resources/list` 非发现式地列出已经可见的资源。创建操作按授权者与幂等键保证幂等；撤销时必须提交
`binding_id` 和 `expected_version`。`/v1/access/bindings/replace` 会原子撤销一个不可变 Binding，并用相同 Resource 和 role
创建后继 Binding；角色描述通过 `many_per_resource` 或 `one_per_resource` 声明活动 Binding 数量约束。Server 管理员可通过
`/v1/access/audit/list` 查看关系变更与决策事件。认证层确认代办执行时，
每条审计事件会把 effective `principal` 与可信 `actor` 记录为两个独立的 opaque identity。

Access wire contract 只使用 `server`、`scope` 和 `artifact` 三种 Resource Kind。Artifact Resource 使用逻辑 identity
`{family, artifact_id}`，刻意不包含 Revision；Memory 可使用仅含 `entry_id` 的 `memory_entry` selector 缩小授权单位。
未知 Family、未实现 Prompt lifecycle 的 `prompt` 或不匹配的 selector/role 都不会创建 Binding。`/v1/access/me` 会报告
当前 mode、Provider 能力和每个 Artifact Family 的启用状态。

跨 Scope 的 Artifact 发布统一使用 `POST /v1/artifact-publications`。请求选择一个精确 source Revision，但授权检查的是
其逻辑 `{family, artifact_id}` identity 上的 `artifact.share`，以及目标 Scope 上的 `scope.admin`。因此一个逻辑分享授权
可以覆盖 source 的历史与后续 Revision，而每次 publication 仍会记录实际复制的精确 Revision 和 provenance。
host-local Dashboard projection 属于运维界面，由对应的 Scope 与 Artifact 权限保护。

标准 Skill 生命周期复用同一 Access 边界：Library 列表要求 `scope.read`，生命周期变更要求 `artifact.write`，
package manifest/download 要求 `artifact.read`，package proposal 要求 `scope.contribute`，替换已有 Skill 时还要求
`artifact.write`；usage capture 同时要求 `scope.contribute` 与 `artifact.read`。远端 target 管理要求
`scope.admin`，发布精确 Revision 还要求该 Skill 的 `artifact.read`。注册接口由一次性 enrollment code 保护，
Receiver 的 reconcile/download/receipt 使用单独签发的 `TargetBearerAuth` 凭据，而不是用户 Principal。
Dashboard 数据接口会在 scope 查询、package 检查、target 查询或文件系统操作之前执行对应 Access 检查。

内置静态 token 只代表一个本地管理员，无法表达不同的 A/B 用户。真正的多用户部署必须把每个调用者认证为不同的
Principal，并注入 Authorization Provider。HTTP 与 MCP 使用同一个策略执行点；MCP tool 可见不等于有权限。

## 查找操作

| 领域 | 主要路径 | 用途 |
| --- | --- | --- |
| 健康与能力 | `/health/*`、`/v1/capabilities` | 探测部署状态并查看已启用的 Runtime 行为 |
| Access Control | `/v1/access/*` | 查看身份、检查决策，并管理 role、Binding 和审计事件 |
| Source 与 Context | `/v1/sources/content`、`/v1/context/prepare` | 采集证据并准备有界 Context |
| 工作连续性 | `/v1/work/*` | 创建 Work Contract、准备或确认 Handoff、记录 Outcome |
| 底层 Handoff | `/v1/handoff/*` | activate、prepare、finalize、commit 或 continue Handoff |
| Memory | `/v1/memory/*` | flush、remember、search、list、get、revise、retire 和查看变更 |
| Experience 与 Skill | `/v1/experience/*`、`/v1/skill/*`、`/v1/skills/*` | propose、review、打包、治理、分发并读取 managed Skill Revision |
| 审核 | `/v1/artifact-candidates/*` | 列出、检查、修订、批准或拒绝 pending Candidate |
| 外部 Skill | `/v1/external-skills/*` | 扫描已配置 target，解析或导入 package |
| Handoff Report | `/v1/handoff-reports/*` | 按 Scope selection 生成只读报告 |
| 统计 | `/v1/stats` | 读取指定 scope 的使用统计 |

完整路径、schema、限制和状态码以 OpenAPI 契约为准。高层工作流和 Python 示例见[接口](interfaces.md)。

## 处理错误和并发变更

错误统一使用以下 JSON envelope：

```json
{
  "error": {
    "code": "invalid_request",
    "message": "The request violates the API contract.",
    "details": {}
  }
}
```

常见状态码：

| 状态码 | 含义 |
| --- | --- |
| `401` | Server 要求有效的 Bearer token |
| `403` | 已认证 Principal 无权对目标资源执行请求的 action |
| `404` | 请求的不可变值不存在 |
| `409` | 请求与当前不可变状态或 expected version 冲突 |
| `413` | 选中的 Handoff Report 超过输出限制 |
| `422` | JSON body 不符合传输或应用契约 |
| `503` | 必需的 Runtime 绑定或依赖不可用 |
| `500` | Server 发生错误，但不会暴露内部细节 |

每个响应都包含 `X-PowerContext-Request-ID`，排查失败请求时应记录它。修订或停用 Memory 时应传回精确 citation。
Candidate 审核写操作需要当前 `expected_version`；收到 `409` 后，应重新读取 Candidate，再决定是否重试。
