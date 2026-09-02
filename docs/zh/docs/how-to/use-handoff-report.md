---
title: 使用 Handoff Report
description: 选择 Scope 视图，检查当前 Handoff，并下载 Markdown 报告。
---

# 使用 Handoff Report

Handoff Report 是各个选中 Scope 最新 committed Handoff 的只读视图。它不会创建或编辑 Scope、Handoff。

## 开始之前

启动 Server：

```bash
powercontext server run
```

Handoff Report 默认启用，地址是 `http://127.0.0.1:8000/handoff-reports`。它使用 Server 的 listener 和鉴权设置，
但不要求启用统计 Dashboard。启用 Bearer 鉴权后，在页面登录表单中输入配置的 token。

Server 启动时会创建默认 Scope。通过 integration 或 Scope API 创建其他 Scope 后，它们也会出现在报告中。Scope
不需要已经包含 committed Handoff。

## 1. 提交 Handoff

在需要查看报告的 Scope 中创建 durable Handoff milestone。在 Codex 中按照
[在 Codex 中交接工作](handoff-with-codex.md)操作。Codex integration 会写入当前 Session 绑定的 Scope。

报告只读取 committed Handoff Revision，不包含临时 Prepared Handoff。

## 2. 选择 Scope 视图

打开 Handoff Report，选择一项共用的 Scope selection：

- **全部**包含该 Server 可见的所有 Scope。
- **Scope 及其下级**包含一个根 Scope 及其所有后代。
- **聚焦**只包含一个精确 Scope。

Parent 只表达组织关系，不会让父 Scope 隐式看到子 Scope 的 Context 或 Handoff。因此，每一行只显示该 Scope
自身最新的 Handoff。选中的 Scope 没有 committed Handoff 时，显示为**无交接**。

Handoff 或 Scope 发生变化后，使用**刷新**重新加载。

## 3. 阅读或下载报告

页面显示选中的 Scope、Parent 关系、Handoff 状态、目标、下一步和精确 Revision 地址。摘要计数和明细行使用同一份
冻结 selection。

选择**下载 Markdown**，由 Server 生成 Markdown projection；浏览器不会根据已渲染页面重新拼接。JSON 和 Markdown
projection 都带有 selection digest 和 report digest，便于使用方识别本次生成的精确结果。

## 关闭 Handoff Report

重启 Server 前设置功能开关：

```bash
export POWERCONTEXT_SERVER_HANDOFF_REPORT_ENABLED=false
powercontext server run
```

关闭后不会注册 `/handoff-reports` 和 Report API route。Dashboard、HTTP API、MCP、Memory 和 Handoff operation
仍可独立配置。

Scope 和 Report operation 见[接口](../reference/interfaces.md)，精确 Server 设置见[配置](../reference/configuration.md)。
