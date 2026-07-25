---
title: 安装和运行
description: 从 Git 安装 PowerContext，并运行本地 Server。
---

# 安装和运行

## 安装应用

先安装 `uv`，再从指定 Git ref 直接安装 PowerContext：

```bash
uv tool install "powercontext[cli,client,server] @ git+https://github.com/oceanbase/powercontext.git@main"
```

该方式支持 macOS 和 Linux，不需要用户自行管理仓库工作副本。Git 会沿用本机的凭据配置，包括 credential
helper 和 SSH 设置。如需使用 SSH，请把 HTTPS URL 换成当前环境允许的 Git URL。

安装指定分支或 tag 时，替换最后一个 `@` 后的 `main`。配置集成时应使用同一个 ref：

```bash
powercontext setup codex --source oceanbase/powercontext --ref <ref>
```

## 运行本地 Server

```bash
powercontext server run
```

未设置环境变量时，Server 会：

- 监听 `127.0.0.1:8000`；
- 在 `/mcp` 启用 Streamable HTTP MCP；
- 在操作系统的用户数据目录中创建持久化 SQLite 数据库；
- 无需推理服务即可支持显式 Memory 操作。

按 `Ctrl-C` 可正常关闭。再次运行该命令会打开同一个数据库。

## 验证安装

```bash
powercontext doctor
powercontext client ready
powercontext client capabilities
```

`doctor` 检查已安装的包、Codex 插件、Server 就绪状态和数据库。Client 命令会经过公开 HTTP SDK 路径。

## 更新或替换安装

使用指定 ref 替换现有工具：

```bash
uv tool install --force "powercontext[cli,client,server] @ git+https://github.com/oceanbase/powercontext.git@<ref>"
powercontext setup codex --source oceanbase/powercontext --ref <ref>
```

更新后重启 Server，并开启新的 Codex 会话。只要没有修改 `POWERCONTEXT_HOME` 或数据库 URL，现有 SQLite
数据会继续保留。

## 为 Python 项目安装角色

如果应用需要导入异步 Client SDK，应把它加入该应用自己的环境：

```bash
uv add "powercontext[client] @ git+https://github.com/oceanbase/powercontext.git@main"
```

进程内存储和 Runtime 组合使用 `builtin`，服务使用 `server`，命令发现使用 `cli`。只安装在 `uv tool`
隔离环境中的 extra 不能被另一个 Python 项目直接导入。
