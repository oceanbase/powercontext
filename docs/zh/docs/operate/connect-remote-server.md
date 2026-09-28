---
title: 连接远程 Server
description: 配置客户端地址，并在需要时明确允许非环回明文 HTTP。
---

# 连接远程 Server

远程部署推荐使用 HTTPS。IPv4/IPv6 环回 HTTP（包括 `localhost`）默认可用；PowerContext 自己发起的其他 HTTP
连接需要显式同意。内网 IP、VPN 地址仍是非环回地址，处于内网不等于 HTTP 已加密。

## 使用引导安装

PowerContext 工具与插件应使用同一份 source/ref。在运行 Agent 的机器上执行：

```bash
powercontext setup claude-code --source oceanbase/powercontext --ref master \
  --server-url http://192.0.2.10:8000
```

交互终端中，setup 会说明明文风险并询问是否允许，直接回车代表拒绝。地址也可以来自该 Agent 的 URL 环境变量或
`POWERCONTEXT_CLIENT_SERVER_URL`，setup 会先解析实际地址再判断是否需要确认。

`--json` 或非交互终端不会提问。自动安装必须显式给出选择：

```bash
powercontext setup claude-code --server-url http://192.0.2.10:8000 --allow-insecure-http --json
```

`claude-code` 可替换为 `codex`、`dsh`、`openclaw`、`opencode`、`pi`、`hermes` 或 `workbuddy`。
`setup select --host ...` 也支持这两个选项，覆盖其目录中的 Agent；WorkBuddy 使用独立 setup 命令。
拒绝或未提供同意时，在安装操作前退出。风险警告写入 stderr，不污染 stdout 的 JSON。
安装成功后保存地址及同意状态，新会话无需重复手贴这两项配置；鉴权凭据仍需单独配置。

## 配置与优先级

配置文件为 `~/.config/powercontext/clients.json`，可用 `POWERCONTEXT_CLIENT_CONFIG_FILE` 指定其他位置。
格式包含 `version: 1` 与 `hosts` 映射，每个 Agent 只保存 `server_url` 和 `allow_insecure_http`，不保存 Token。
持久化同意绑定地址；切换到其他地址时，不会自动继承原地址的同意。

setup 的优先级为：显式命令行选项 > Agent 环境变量 > 通用环境变量 > 已保存配置。
运行时还可能读取宿主原生配置，具体 URL 来源以对应接入文档为准。显式 false 可以覆盖继承的 true。
布尔环境变量支持 `true/false`、`1/0`、`yes/no`、`on/off`，无效值不会被当成允许。

| 接入端 | 明文 HTTP 同意环境变量 |
| --- | --- |
| 所有 PowerContext 客户端 | `POWERCONTEXT_CLIENT_ALLOW_INSECURE_HTTP` |
| Codex | `POWERCONTEXT_CODEX_ALLOW_INSECURE_HTTP` |
| Claude Code | `POWERCONTEXT_CLAUDE_ALLOW_INSECURE_HTTP` |
| DSH、Pi、OpenCode | `POWERCONTEXT_DSH_ALLOW_INSECURE_HTTP`、`POWERCONTEXT_PI_ALLOW_INSECURE_HTTP`、`POWERCONTEXT_OPENCODE_ALLOW_INSECURE_HTTP` |
| OpenClaw、Hermes、WorkBuddy | `POWERCONTEXT_OPENCLAW_ALLOW_INSECURE_HTTP`、`POWERCONTEXT_HERMES_ALLOW_INSECURE_HTTP`、`POWERCONTEXT_WORKBUDDY_ALLOW_INSECURE_HTTP` |

通用内容 CLI 的选项放在子命令前：

```bash
powercontext --server-url http://192.0.2.10:8000 --allow-insecure-http ready
powercontext doctor --server-url http://192.0.2.10:8000 --allow-insecure-http
powercontext doctor claude-code --json
```

doctor 对已同意的明文连接报告 `degraded`，退出码非零，保留风险提示。这条警告本身不表示安装失败或连接不通。

## MCP、Hook 与升级边界

Codex、Claude Code、WorkBuddy 的 setup 会同时配置原生 MCP 地址与 Hook 地址。
Codex Hook 特意读取已安装插件中的 `.mcp.json`，插件升级覆盖该文件后需重新运行 setup。
只修改 Hook 的 URL 环境变量，不能视为宿主原生 MCP 的地址也已修改。

DSH 自定义 `cordis.patch.yml` 可能覆盖 setup 保存的地址与同意状态。标准空白 profile patch 可直接安装；
检测到自定义覆盖层时，setup 会在安装前退出，请先手动对齐地址及同意设置，或移除覆盖后重试。
对无法解析的原生配置组合（包括未支持的 JSON5/include），doctor 会报告未知/失败，不会假定连接是安全的环回地址。

MiniMax 和通用 Agent Plugin 由宿主负责 MCP 传输，没有单独的 PowerContext HTTP Client 或 setup 子命令；
本选项不能绕过宿主自己的限制。LangChain、LangGraph、Pydantic AI 及 Bub 评测适配器支持客户端显式同意，
但不会因此增加 Agent 安装器。

## 安全边界

该选项只允许明文 HTTP，不会关闭 HTTPS 证书校验，不会配置 Token、修改 Server 监听地址、开启 Dashboard，
也不会绕过宿主 MCP 安全策略。传输中的 Token 与对话内容可能被读取或篡改；IP 白名单不是传输加密。

远程访问仍应启用 Server 鉴权。Server 的监听安全检查与 Receiver 注册检查是独立机制，客户端选项不能代替它们。

另一种方式是在 Agent 所在机器运行 `ssh -N -L 18000:127.0.0.1:8000 your-server`，再配置
`http://127.0.0.1:18000`，无需允许非环回明文 HTTP。参见[部署 Server](deploy-server.md)。
