---
title: 安装和运行
description: 从 Git 安装 PowerContext，并运行本地 Server。
---

# 安装和运行

## 安装应用

先安装 `uv`，再从指定 Git ref 直接安装 PowerContext：

```bash
uv tool install "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@master"
```

该方式支持 macOS 和 Linux，不需要用户自行管理仓库工作副本。Git 会沿用本机的凭据配置，包括 credential
helper 和 SSH 设置。如需使用 SSH，请把 HTTPS URL 换成当前环境允许的 Git URL。

安装指定分支或 tag 时，替换最后一个 `@` 后的 `master`。配置集成时应使用同一个 ref：

```bash
powercontext setup codex --source oceanbase/powercontext --ref <ref>
powercontext setup dsh --source oceanbase/powercontext --ref <ref>
```

宿主专有选项见[配置 Codex](configure-codex.md)和[配置 DeepSeek Harness](configure-dsh.md)。

## 运行本地 Server

```bash
powercontext server run
```

未设置环境变量时，Server 会：

- 监听 `127.0.0.1:8000`；
- 在 `/mcp` 启用 Streamable HTTP MCP；
- 在 `/` 启用 Dashboard；尚未配置 scope 时，页面会显示明确的空状态；
- 在操作系统的用户数据目录中创建持久化 SQLite 数据库；
- 无需推理服务即可支持显式 Memory 操作。

启动成功后，终端会输出 Dashboard 地址，例如 `http://127.0.0.1:8000/`。Dashboard 与 HTTP API、MCP 共用 Server
的监听地址和端口。Dashboard 初始化失败时，Server 会记录包含直接原因的 warning，并继续提供其他接口；可通过
`POWERCONTEXT_SERVER_DASHBOARD_ENABLED=false` 显式关闭 Dashboard。

按 `Ctrl-C` 可正常关闭。再次运行该命令会打开同一个数据库。

## 验证安装

```bash
powercontext doctor
powercontext doctor codex
powercontext doctor dsh
powercontext ready
powercontext capabilities
```

`doctor` 检查已安装的包、Server 存活状态和 Server 就绪状态，不要求安装集成。Server 就绪检查涵盖数据库和
每个已配置的推理服务。Runtime 或数据库故障返回 `not_ready`；推理服务故障返回 `degraded`，不会使数据库
操作退出流量。`doctor codex` 和 `doctor dsh` 单独检查可选的宿主 CLI 与 PowerContext 插件。`ready` 和
`capabilities` 用于查看运行中服务的就绪状态和已启用能力。完整的状态解释和恢复步骤见[排查问题](troubleshoot.md)。

## 更新或替换安装

使用指定 ref 替换现有工具：

```bash
uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@<ref>"
powercontext setup codex --source oceanbase/powercontext --ref <ref>
powercontext setup dsh --source oceanbase/powercontext --ref <ref>
```

更新后重启 Server，并开启新的宿主会话。只要没有修改 `POWERCONTEXT_HOME` 或数据库 URL，现有 SQLite
数据会继续保留。

## 为 Python 项目安装角色

如果应用需要导入异步 Client SDK，应把它加入该应用自己的环境：

```bash
uv add "powercontext[client] @ git+https://github.com/oceanbase/powercontext.git@master"
```

进程内 Python 组合使用 `builtin`，服务使用 `server`，Python SDK 使用 `client`，基于 Server 的命令行使用
`cli`。只安装在 `uv tool` 隔离环境中的 extra 不能被另一个 Python 项目直接导入。
