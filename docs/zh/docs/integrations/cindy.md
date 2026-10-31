---
title: Cindy
description: 在 Cindy 0.1.115 中使用统一 Scope 的召回与自动采集，并通过原生 MCP 接入完整工具目录。
---

# Cindy

`experimental`

`powercontext-hooks` Ghost 的自动钩子和显式召回工具共用 Server 连接、凭据和 Scope 解析。
独立配置的原生 MCP 连接提供完整 Server 工具目录。兼容目标为 [Cindy 0.1.115](https://github.com/makecindy/cindy/releases/tag/v0.1.115)。

## 连接 Server

按[安装与运行](../get-started/install-and-run.md)安装 PowerContext，再按[部署 Server](../operate/deploy-server.md)完成配置和验证。
受支持的个人 macOS/Linux 环境可运行：

```bash
powercontext service install --env-file /path/to/powercontext.env
powercontext service status
```

加载对应的客户端连接设置，再执行 `powercontext ready` 和 `powercontext capabilities`。
导入 `powercontext-cindy-hooks-0.1.0.cindy`，打开插件设置，填写 Server URL 和可选的 Bearer token。
凭据与 Server URL 一起存入 Cindy 的秘密存储，仅注入 Node worker。

显式写入、Handoff、审核及其他 Server 工具还需要在 Cindy 设置中新增原生自定义 MCP 连接：

| 字段 | 值 |
| --- | --- |
| 名称 | `PowerContext` |
| 传输 | `http`，即 Streamable HTTP |
| URL | `http://127.0.0.1:8000/mcp`，使用实际端口 |
| Bearer token | Server token，不带 `Bearer ` 前缀；本地匿名模式留空 |
| 额外请求头 | 普通 Bearer 认证时留空 |

为任务选中该连接，并检查实际发现的工具。使用 Streamable HTTP；Cindy 的 Codex/Pi 配置路径不支持旧 SSE。
另一台机器上的 Server 应使用可达的 HTTPS 地址。桌面的环回地址不等于远程 Agent 的环回地址；SSH Pi 会直接连接自定义 MCP 地址。
详细要求见[连接远程 Server](../operate/connect-remote-server.md)。

## 选择 Scope 并显式调用工具

在插件设置中填写 Server 返回的显式 Scope ID，或留空以使用当前 Cindy 会话的已注册绑定。
宿主通过 `sessionContext` 注入可信会话身份；模型不能通过插件召回工具选择另一个 Scope 或连接。
缺失绑定时读取失败，不回退到默认 Scope。

| 插件工具 | 行为 |
| --- | --- |
| `status` | 返回当前会话 ID 及最近的钩子结果，不请求 Server |
| `resolve_scope` | 向 Server 检查当前绑定 |
| `prepare_context` | 返回有界且经过校验的 PreparedContext，作为历史证据 |
| `search_memory` | 在同一解析出的 Scope 中搜索 Memory |

关闭自动记忆后，这些只读工具仍可使用。每次调用重新解析绑定，不复用先前消息的过期 Scope；
工具不会采集查询，也不会执行持久写入。

对于单独配置的原生 MCP 连接：

使用 `list_scopes` 和 `get_scope` 检查已有 Scope，仅在用户要求时创建。
将选中的 Server 返回的 ID 作为 `explicit_scope_id`，并设置 `allow_default: false`，调用 `resolve_scope_binding`。
后续操作复用返回的 `scope_id`。缺失或无效绑定时停止记忆操作，不从目录猜测 Scope，也不回退到全局 Scope。

使用实际工具目录中的命名空间调用 `search_memory`、`remember_memory` 和 `capture_content_source`。
保存 Memory 需要用户意图，并检查返回结果。采集 Source 使用原始材料，重试时保持相同 `source_id`；采集成功不代表 Memory 提取完成。
Handoff 和候选审核复用其他 MCP 宿主使用的 Server 工具与授权边界。

原生 MCP 连接不向 PowerContext 提供可信的 Cindy 会话身份或工作目录，其显式工具调用必须使用选定的 Scope，
可通过插件的 `resolve_scope` 工具核对。插件无法约束独立原生 MCP 连接的 Scope 参数，需保持两边的 Server URL
和 Scope 一致。不从 Agent 编写的文本推导工作目录绑定。

历史内容不能覆盖当前指令。操作失败时明确报告该操作，普通任务可使用已有上下文继续。
写入结果不确定时，先检查 Server 状态再决定是否重试。

## 开启自动召回与 Prompt 采集

启用插件，在设置中勾选 **Enable automatic memory**。
自动钩子与插件召回工具共用连接和 Scope 解析，用户不需要安装 DSH 或额外运行时依赖。

可在设置中填写显式 Scope；或留空，由操作者使用 Host 返回的会话 ID，通过 `set_scope_binding` 注册
`integration: "cindy"`、`kind: "session"` 的绑定。插件 `status` 工具提供当前会话 ID 及其最近的钩子结果。
缺失绑定时原始消息正常放行，不执行记忆操作，也不会自动创建 Scope 或回退到默认 Scope。

`will-user-message` 钩子在派发前召回有界 PreparedContext，并独立采集原始 Prompt 为 Source。
采集可单独关闭；成功采集不代表提取完成或 Memory 已生成。
采集的是本适配器改写前的输入，前置 Ghost 可能已经改写它；Cindy 没有单独提供未经改写的草稿字段。
显式写入、Handoff 和审核使用原生 MCP 工具。

召回成功时，在原文后追加不可信历史证据。Cindy 的 rewrite 会更新显示、存储及发给 Agent 的消息正文。
召回失败或为空时原文放行，迟到结果不会改写消息。上下文遵守 UTF-8 字节预算，完整改写不能超过 Cindy 的 16,000 字符限制。
适配器在本地超时后发送放行裁决，Cindy 则独立执行整个钩子派发的三秒期限。

同一事件投递复用 Source ID；重新提交的消息使用新的 Host hook ID。
带有适配器历史标记的编辑后消息正常放行，不重复采集或注入；已识别的凭据内容跳过处理。
写入结果不确定时记录状态，不自动重试。
当前覆盖本地用户消息派发和插话，未验收远程、Worker、定时、目标自动续跑或压缩前流程。
Server 就绪检查仍使用 CLI。

## 运行通道与内置记忆

Ghost 的 `main.js` 运行在浏览器沙箱中。Cindy 按需启动并复用打包的 Node worker，官方 Node broker
负责进程管理和 JSON-RPC 转发；worker 使用与 DSH 相同的 PowerContext HTTP 操作及 PreparedContext 校验器。
无需额外部署桥接服务。`cindy.fetch` 仅允许不带自定义端口的 HTTPS，无法替代默认本地 HTTP Server 所需的 worker。

自动上下文使用宿主支持的 `will-user-message` rewrite 通道，插件显式召回通过工具结果返回。
两者都不安装系统提示词，也不修改 Cindy 的内置记忆。当前没有公开 hook API 可以复用单独配置的原生 MCP 连接。

Cindy 共享记忆和所选 Harness 的原生记忆分别配置。评估仅使用 PowerContext 的行为时，应在 Cindy 中关闭共享记忆，
同时关闭相关 Claude Code / Codex 原生记忆开关，再创建新会话。仅关闭共享记忆可能恢复 Harness 原生记忆。
插件不会更改这些设置或删除已有记忆，旧会话中已经注入的历史仍可能保留。

## 实验性支持

Cindy 0.1.115 的 provider 和钩子网关测试通过真实 Server 验证 MCP 工具、会话隔离、Prompt 持久化、消息改写及插件召回。
原生 MCP 传输检查覆盖 provider 的直接连接兼容路径，Desktop 默认的 MCP Apps facade 属于独立边界。

可选的真实模型检查通过上游 `maker-core` 的 Codex app-server 宿主运行已登录的本机 Codex，
验证模型使用注入历史及显式召回工具作答，再从 Server 回读原始 Prompt。执行命令见
[集成 README](https://github.com/oceanbase/powercontext/blob/master/integrations/cindy/README.md)。Host 凭据和消息桥由测试模拟；Electron 安装、秘密存储 UI
及完整 Desktop Harness 装配尚未验收，Cindy 暂不计入已验证宿主目录。
