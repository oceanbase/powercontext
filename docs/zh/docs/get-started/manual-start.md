---
title: 手动启动 PowerContext
description: 不使用配置向导，手动配置并启动 PowerContext Server，连接 Codex 并验证 Memory。
---

# 手动启动 PowerContext

本页介绍不使用 powercontext config init 配置向导时，如何手动配置和启动 PowerContext Server，并将 Codex 绑定到一个 Scope。

本指南仅适用于 macOS 和 Linux。文中的命令使用 POSIX Shell 语法，例如 `source`、`env -i` 和 here-document；本文不提供 Windows PowerShell 流程。

本页适合希望了解每一步、排查配置问题，或需要自己控制 Server 地址、端口、Dashboard 和认证方式的用户。如果希望使用交互式配置向导快速完成安装，请看[快速开始](quickstart.md)。

## 手动启动和快速启动的区别

快速启动使用配置向导：

```text
config init → 生成 .env → 启动 Server → 创建 Scope → 连接 Agent
```

手动启动不运行 config init，而是自己准备环境变量或配置文件：

```text
手动写配置 → validate → server run → 检查 Server → 创建 Scope → 绑定 Agent
```

手动启动不是从源码编译 PowerContext，而是不依赖配置向导，自己决定 Server 的运行参数。

## 前置条件

需要 Python 3.11 或更高版本、Git、[uv](https://docs.astral.sh/uv/getting-started/installation/)，以及要连接的 Agent CLI（例如 Codex CLI）。

```bash
python3 --version
git --version
uv --version
```

## 1. 安装 PowerContext

如果尚未安装 uv，可以先执行：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source "$HOME/.local/bin/env"
```

安装 PowerContext CLI 和本地 Server：

```bash
uv tool install --force "powercontext[cli,server]==1.4.1"
powercontext --version
powercontext --help
```

## 2. 停止可能正在运行的个人服务

如果之前使用过 powercontext service install，系统中可能已有个人后台 Server。它可能占用默认端口，导致你误以为检查的是手动启动的 Server。

```bash
powercontext service status
```

如果个人服务正在运行，停止它：

```bash
powercontext service stop
```

这个命令会停止个人服务并禁止它自动启动，但会保留服务配置和数据。

## 3. 创建手动配置文件

```bash
mkdir -p ~/powercontext-manual
cd ~/powercontext-manual
nano manual.env
```

在 manual.env 中写入：

```dotenv
POWERCONTEXT_SERVER_HTTP_HOST=127.0.0.1
POWERCONTEXT_SERVER_HTTP_PORT=18080
POWERCONTEXT_SERVER_DASHBOARD_ENABLED=true
POWERCONTEXT_SERVER_ACCESS_MODE=disabled
```

配置项作用：

| 配置项 | 作用 |
| --- | --- |
| POWERCONTEXT_SERVER_HTTP_HOST | 只监听本机地址 |
| POWERCONTEXT_SERVER_HTTP_PORT | 监听 18080 端口 |
| POWERCONTEXT_SERVER_DASHBOARD_ENABLED | 开启 Dashboard |
| POWERCONTEXT_SERVER_ACCESS_MODE | 关闭本机测试所需的认证 |

本配置没有指定数据库和模型，因此使用默认 SQLite，并且不启用模型推理能力。

## 4. 验证手动配置

```bash
env -i HOME="$HOME" PATH="$PATH" LANG=C.UTF-8 \
  powercontext config validate \
  --env-file ~/powercontext-manual/manual.env
```

成功时会显示 Configuration is valid。这一步只验证配置，不会启动 Server。

## 5. 手动启动 Server

```bash
env -i HOME="$HOME" PATH="$PATH" LANG=C.UTF-8 \
  powercontext server run \
  --env-file ~/powercontext-manual/manual.env
```

保持这个终端运行。成功启动时会看到类似日志：

```text
PowerContext Server is ready
Uvicorn running on http://127.0.0.1:18080
```

按 Ctrl-C 可以停止前台 Server。没有配置 Generation 和 Embedding 模型时会出现提示，但不影响基础 Memory 的显式保存和召回。

## 6. 访问 Dashboard

在浏览器打开：

```text
http://127.0.0.1:18080/dashboard/home
```

认证已关闭，因此本机可以直接访问。第一次打开页面没有数据是正常的。

## 7. 检查 Server

保持 Server 运行，另开终端执行：

```bash
env -i HOME="$HOME" PATH="$PATH" LANG=C.UTF-8 \
  POWERCONTEXT_CLIENT_SERVER_URL=http://127.0.0.1:18080 \
  powercontext doctor
```

继续检查：

```bash
env -i HOME="$HOME" PATH="$PATH" LANG=C.UTF-8 \
  POWERCONTEXT_CLIENT_SERVER_URL=http://127.0.0.1:18080 \
  powercontext ready
```

```bash
env -i HOME="$HOME" PATH="$PATH" LANG=C.UTF-8 \
  POWERCONTEXT_CLIENT_SERVER_URL=http://127.0.0.1:18080 \
  powercontext capabilities
```

预期结果包括：

```text
server liveness: ok - http://127.0.0.1:18080
server readiness: ok - http://127.0.0.1:18080
access_mode: disabled
database: ready
```

doctor 检查安装、Server 存活和就绪状态；ready 检查服务是否可以提供请求；capabilities 显示当前能力。

## 8. 手动创建 Scope

Scope 是项目上下文数据的隔离空间，用于隔离 Source、Memory、Topic Memory、Handoff 和 Agent 召回数据。

使用 Python Client 手动调用 Server 创建 Scope。保持 Server 运行，在另一个终端执行：

```bash
cd ~/powercontext-manual
POWERCONTEXT_CLIENT_SERVER_URL=http://127.0.0.1:18080 \
uv run --with "powercontext[client]==1.4.1" python - <<'PY'
import asyncio
import os
from powercontext.client import PowerContextClient
from powercontext.http import CreateScopeRequest

async def main():
    async with PowerContextClient(os.environ["POWERCONTEXT_CLIENT_SERVER_URL"]) as client:
        scope = await client.create_scope(
            CreateScopeRequest(
                title="manual-powercontext-demo",
                summary="Scope created during manual setup",
                idempotency_key="manual-powercontext-demo-v1",
            )
        )
        print("scope_id:", scope.scope_id)

asyncio.run(main())
PY
```

Server 会返回真实的 scope_id。后续绑定 Agent 时必须使用这个 ID，不能根据 Scope 标题自行拼接。

## 9. 绑定 Codex Agent

将真实 Scope ID 设置到当前终端：

```bash
export POWERCONTEXT_CODEX_SCOPE_ID=替换为Server返回的真实scope_id
```

安装与 Server 版本匹配的 Codex 插件，并指定手动启动的 Server：

```bash
powercontext setup codex \
  --ref powercontext-v1.4.1 \
  --server-url http://127.0.0.1:18080
powercontext doctor codex
```

成功时应看到插件、MCP 和工具发现检查通过。安装插件不会启动 Server，因此 Server 终端必须保持运行。

## 10. 验证显式 Memory 和跨会话召回

从设置了 POWERCONTEXT_CODEX_SCOPE_ID 的终端启动新的 Codex：

```bash
codex
```

发送一条明确要求保存的消息：

```text
请把下面这条决定保存到 PowerContext：

Aurora 项目使用 uv 管理 Python 依赖，配置文件使用 .env；第一阶段部署在 Linux 服务器上。
```

退出 Codex 后，在相同 Scope 下重新启动新会话，并询问：

```text
请从 PowerContext 找回 Aurora 项目的依赖管理和部署决定。
```

如果 Agent 找回相同内容，并显示 Memory 的 active 状态、来源和 revision，说明手动启动、Scope 绑定、显式保存和跨会话召回都验证成功。

## 能力边界

本页没有配置 Generation 和 Embedding 模型，因此可以验证显式 Memory、全文搜索和跨会话召回，但不能期待自动 Memory 提取、Topic Memory、Experience 或 Skill 自动生成。需要这些能力时，请参考[模型配置](configure-models.md)。

## 常见问题

### capabilities 显示 extraction disabled

这是因为没有配置 Generation 和 Embedding 模型，不影响基础 Memory 的显式保存与召回。

### capabilities 返回 401

通常是 Server 开启了认证但客户端没有 Token。确认 manual.env 中的 POWERCONTEXT_SERVER_ACCESS_MODE=disabled，并使用 env -i 避免继承旧环境变量。

### 检查到了 8000 而不是 18080

检查时显式设置客户端地址：

```bash
POWERCONTEXT_CLIENT_SERVER_URL=http://127.0.0.1:18080 powercontext ready
```

### Dashboard 没有数据

首次打开没有数据是正常的。需要先通过 Agent 或 API 写入 Source 或 Memory，并确认 Dashboard 和 Agent 使用同一个 Server 和 Scope。

## 相关文档

- [快速开始](quickstart.md)：使用配置向导完成完整记忆体验；
- [配置 Server 环境](configure-server-environment.md)：了解配置文件和环境变量；
- [配置模型](configure-models.md)：启用自动提取和向量检索；
- [部署 Server](../operate/deploy-server.md)：运行个人后台服务和远程 Server；
- [故障排查](../operate/troubleshoot.md)：排查 Server、模型和连接问题。
