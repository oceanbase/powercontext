---
title: 使用 Handoff Report
description: 通过 HTTP API 选择 Scope，并请求 JSON 或 Markdown Handoff Report。
---

# 使用 Handoff Report

Handoff Report 是各个选中 Scope 最新 committed Handoff 的只读投影。它不会创建或编辑 Scope、Handoff。

## 开始之前

启动 Server 并设置 base URL：

```bash
powercontext server run
export POWERCONTEXT_URL=http://127.0.0.1:8000
```

Handoff Report API route 默认启用。启用 Bearer 鉴权后，还需设置 authorization header 变量，并在每个请求中加入
`--header "$POWERCONTEXT_AUTH_HEADER"`：

```bash
POWERCONTEXT_AUTH_HEADER="Authorization: Bearer ${POWERCONTEXT_CLIENT_API_TOKEN}"
```

Server 启动时会创建默认 Scope。通过 integration 或 Scope API 创建其他 Scope。

## 1. 提交 Handoff

在需要查看报告的 Scope 中创建 durable Handoff milestone。在 Codex 中按照
[在 Codex 中交接工作](handoff-with-codex.md)操作。报告只读取 committed Handoff Revision，不包含临时 Prepared
Handoff。

## 2. 选择 Scope

API 接受共用的 Scope selection：

- `{"mode":"all"}` 包含全部可见 Scope。
- `{"mode":"subtree","root_scope_id":"..."}` 包含一个根 Scope 及其全部后代。
- `{"mode":"exact","scope_ids":["..."]}` 只包含列出的 Scope。

Parent 关系只表达组织，不会让父 Scope 隐式看到子 Scope 的 Context 或 Handoff。选中的 Scope 没有 committed
Handoff 时，返回明确的 `no_handoff` 结果。

## 3. 请求 JSON 或 Markdown

请求 canonical JSON projection：

```bash
curl --fail \
  --request POST \
  --header 'Content-Type: application/json' \
  --data '{"selection":{"mode":"all"},"format":"json"}' \
  "$POWERCONTEXT_URL/v1/handoff-reports/get"
```

为一个精确 Scope 请求 Markdown projection：

```bash
curl --fail \
  --request POST \
  --header 'Content-Type: application/json' \
  --data "{\"selection\":{\"mode\":\"exact\",\"scope_ids\":[\"${POWERCONTEXT_SCOPE_ID}\"]},\"format\":\"markdown\"}" \
  --output handoff-report.md \
  "$POWERCONTEXT_URL/v1/handoff-reports/get"
```

两种 projection 都带有 selection digest 和 report digest，便于使用方识别本次生成的精确结果。

## 关闭 Handoff Report

重启 Server 前设置功能开关：

```bash
export POWERCONTEXT_SERVER_HANDOFF_REPORT_ENABLED=false
powercontext server run
```

关闭后不会注册 Report API route。HTTP API、MCP、Memory 和 Handoff operation 仍可独立配置。

Scope 和 Report operation 见[接口](../develop/interfaces.md)，精确 Server 设置见[配置](../operate/configuration.md)。
