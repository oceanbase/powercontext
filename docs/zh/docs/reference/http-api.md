---
title: HTTP API
description: 通过 HTTP 调用 PowerContext Server，并找到完整 OpenAPI 契约。
---

# HTTP API

HTTP API 是访问 PowerContext Server 的语言无关接口。默认 base URL 为 `http://127.0.0.1:8000`。

## 查看契约

本地未启用鉴权的 Server 运行后，可以打开：

- `/docs`：交互式 Swagger UI；
- `/redoc`：ReDoc；
- `/openapi.json`：该进程实际提供的契约。

仓库中的契约源文件是
[`openapi/powercontext.yaml`](https://github.com/oceanbase/powercontext/blob/master/openapi/powercontext.yaml)。
生成客户端或检查全部请求、响应字段时以它为准。启用 Server 鉴权后，这三个发现路由与其他受保护路由一样需要 Bearer
token。浏览器地址栏无法添加该 header；应使用可信的代理或浏览器配置注入 header，或者设置下方变量后，通过带鉴权的
命令下载 `/openapi.json`。不要把 token 放进 URL。

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

为项目或租户选择稳定的 `scope_id`，并在不同会话中复用。会话 ID 不是持久的项目身份。

保存一条已经整理好的 Memory：

```bash
curl --fail \
  --request POST \
  --header 'Content-Type: application/json' \
  --header "$POWERCONTEXT_AUTH_HEADER" \
  --data '{
    "scope_id": "project:example",
    "kind": "decision",
    "text": "公开 API 保持异步。"
  }' \
  "$POWERCONTEXT_URL/v1/memory/remember"
```

响应包含精确 citation。后续请求需要修订、停用或读取这个不可变 revision 时，应保留并传回该 citation。

在同一个 scope 中搜索 active entry：

```bash
curl --fail \
  --request POST \
  --header 'Content-Type: application/json' \
  --header "$POWERCONTEXT_AUTH_HEADER" \
  --data '{
    "scope_id": "project:example",
    "query": "公开 API",
    "limit": 5
  }' \
  "$POWERCONTEXT_URL/v1/memory/search"
```

## 查找操作

| 领域 | 主要路径 | 用途 |
| --- | --- | --- |
| 健康与能力 | `/health/*`、`/v1/capabilities` | 探测部署状态并查看已启用的 Runtime 行为 |
| Source 与 Context | `/v1/sources/content`、`/v1/context/prepare` | 采集证据并准备有界 Context |
| 工作连续性 | `/v1/work/*` | 创建 Work Contract、准备或确认 Handoff、记录 Outcome |
| 底层 Handoff | `/v1/handoff/*` | activate、prepare、finalize、commit 或 continue Handoff |
| Memory | `/v1/memory/*` | flush、remember、search、list、get、revise、retire 和查看变更 |
| Experience 与 Skill | `/v1/experience/*`、`/v1/skill/*` | propose、generate 和读取 Artifact Revision |
| 审核 | `/v1/artifact-candidates/*` | 列出、检查、修订、批准或拒绝 pending Candidate |
| 外部 Skill | `/v1/external-skills/*` | 扫描已配置 target，解析或导入 package |
| Handoff Report | `/v1/handoff-reports/*` | 管理 Project、Workstream、activity、report 和 workspace binding |
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
| `404` | 请求的不可变值不存在 |
| `409` | 请求与当前不可变状态或 expected version 冲突 |
| `413` | 选中的 Handoff Report 超过输出限制 |
| `422` | JSON body 不符合传输或应用契约 |
| `503` | 必需的 Runtime 绑定或依赖不可用 |
| `500` | Server 发生错误，但不会暴露内部细节 |

每个响应都包含 `X-PowerContext-Request-ID`，排查失败请求时应记录它。修订或停用 Memory 时应传回精确 citation。
Candidate 审核写操作需要当前 `expected_version`；收到 `409` 后，应重新读取 Candidate，再决定是否重试。
