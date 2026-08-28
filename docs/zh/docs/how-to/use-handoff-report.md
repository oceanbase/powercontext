---
title: 使用 Handoff Report
description: 打开 Server 报告，选择 scope，检查 Handoff history，并保存 Revision。
---

# 使用 Handoff Report

Handoff Report 在 Server 托管的网页中展示 committed Handoff Revision。使用该页面可以按 scope 检查当前工作、
保存完整 Handoff snapshot，或请求 Markdown projection。

## 开始之前

启动 Server：

```bash
powercontext server run
```

Handoff Report 默认启用，地址是 `http://127.0.0.1:8000/handoff-reports`。它使用 Server 的 listener 和鉴权设置，
但不要求启用统计 Dashboard 或配置其 scope list。启用 Bearer 鉴权后，在页面登录表单中输入配置的 token。

默认未鉴权模式下，首次加载和手动加载不需要 token。当前浏览器代码要求存在已保存的 Bearer token 才执行后台刷新和
Markdown 下载，因此只有启用鉴权并在页面保存 token 后，这两个控件才会发送报告请求。

页面会发现至少包含一个 committed Handoff 的 scope。没有这类 scope 时，页面显示无数据模板预览，并禁用搜索、周期
筛选、编辑和下载。

## 1. 在目标 scope 中提交 Handoff

在需要查看报告的 scope 中创建 durable Handoff milestone。在 Codex 中按照
[在 Codex 中交接工作](handoff-with-codex.md)操作，并保留结果中的精确 scope 和 Handoff Revision。

Commit 成功后重新加载 Handoff Report。页面调用 `list_handoff_report_known_scopes`，结果中应包含这个 scope。提交
Handoff 后即可发现 scope，不需要创建 Report Project 或注册 Workstream。

## 2. 选择 scope

按 `scope_id` 搜索并选择 scope。页面使用 `scope_id` 请求报告，显示当前 objective、state、disposition、next action
和 known omissions。

报告还会按最新优先展示 Handoff history。JSON projection 最多包含最近 20 个 Revision 摘要，并标明更早的 history
是否被截断。HTTP request schema 保留可选的 `project_id` 字段以兼容旧 wire contract，但该字段已 deprecated，
Server 生成 scope report 时会忽略它。

页面会启动 5 秒刷新 timer，但当前只有保存了 Bearer token 才会发送后台请求。未启用鉴权时，使用 **刷新** 手动加载
变更。存在未保存编辑或正在执行 Handoff action 时，后台刷新也会暂停。

## 3. 保存新的 Handoff Revision

选择 **编辑**，将五个 current snapshot 字段作为一份完整文档修改，再选择 **保存新版本**。Server 会 prepare 并
commit 完整内容，创建新的不可变 Handoff Revision。

保存属于写操作。编辑器打开时，scope 切换保持暂停。页面不记录接收方是否接受；Acknowledgement 和 Task Outcome
只作为只读记录显示在 **连续性时间线** 中。

## 4. 了解当前周期控件

当前 scope report 接收并规范化本日、本周、自然月或自定义 period，但尚未配置 Activity integration。响应不包含
Activity event，`activity_coverage` 为 `not_configured`，也不会生成 previous-period comparison。

目前不要使用周期控件推断历史工作或比较 Activity。Handoff snapshot 表示当前精确 selection，不是根据所选周期结束
时间重建的状态。

## 5. 下载 Markdown

启用 Bearer 鉴权并在页面保存 token 后，选择 **下载 Markdown**，导出相同 scope、locale 和规范化 period。浏览器
直接向 Server 请求 Markdown，不会根据已渲染页面重新拼接。下载默认启用 evidence check，文件名为
`handoff-report.md`。

默认未鉴权模式下，当前浏览器 guard 不会发送下载请求。Server 未启用鉴权时，底层 HTTP operation 和 Python Client
仍可在不提供 token 的情况下使用。

## 关闭 Handoff Report

重启 Server 前设置功能开关：

```bash
export POWERCONTEXT_SERVER_HANDOFF_REPORT_ENABLED=false
powercontext server run
```

关闭后不会注册 `/handoff-reports` 和 Report API route。Dashboard、HTTP API、MCP、Memory 和 Handoff operation
仍可独立配置。

Scope discovery 和 Report operation 见[接口](../reference/interfaces.md)，精确 Server 设置见
[配置](../reference/configuration.md)。
