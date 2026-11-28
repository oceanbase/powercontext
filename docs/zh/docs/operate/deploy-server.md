---
title: 部署 Server
description: 使用持久化数据、健康检查、鉴权和安全网络边界运行 PowerContext。
---

# 部署 Server

远程 Agent 的地址配置与 `--allow-insecure-http` 确认见[连接远程 Server](connect-remote-server.md)。这是客户端选项，不会修改 Server 的监听地址或鉴权设置。

Windows 支持为 `experimental`。

`powercontext server run` 是前台进程。在个人 macOS、Linux 或 Windows 工作站上，PowerContext 可以把同一个 Server runner 注册到原生当前用户服务管理器。托管部署仍应使用容器平台或管理员拥有的服务管理器。

## 运行持久个人 Server

安装并启动可选的当前用户服务：

```bash
powercontext service install
powercontext service status
```

Linux 使用 `systemd --user`，日志进入 user journal；macOS 使用当前用户 LaunchAgent；Windows 使用当前用户的 Task Scheduler task。macOS 和 Windows 的 stdout、stderr 写入 PowerContext 用户数据目录。

`service status` 会返回精确的日志 selector 或路径。

个人服务仅支持 loopback 地址；即使已启用鉴权，把 `POWERCONTEXT_SERVER_HTTP_HOST` 配置为非 loopback 地址也会导致 `service install` 拒绝安装。需要从其他机器访问时，请使用容器或管理员拥有的服务管理器，或者由同机反向代理转发到 loopback Server。

在 Windows 上，如果没有提供 `--start-on-login` 或 `--no-start-on-login`，命令会询问是否在当前用户下次登录时自动启动；直接按 Enter 的默认选择是不启用。需要非交互选择时，请提供其中一个选项。

使用显式 Server 配置时，先保护并验证环境文件：

```bash
chmod 600 /path/to/powercontext.env
powercontext config validate --env-file /path/to/powercontext.env
powercontext service install --env-file /path/to/powercontext.env
```

安装成功后的摘要会显示实际使用的环境文件路径。若启用了 Bearer 鉴权，请从该文件中的 `POWERCONTEXT_SERVER_AUTH_TOKEN` 读取令牌；命令不会在终端打印令牌值。默认配置关闭鉴权，因此不会自动生成令牌。

在 Windows 上，校验前需要移除继承权限，只授予当前用户、`SYSTEM` 和本机 `Administrators` 访问权限，例如：

```powershell
icacls $env:USERPROFILE\powercontext.env /inheritance:r /grant:r "${env:USERNAME}:(F)" "SYSTEM:(F)" "Administrators:(F)"
```

该 `icacls` 命令只调整 ACL，不会更改文件 owner；如果 owner 不是当前用户，需要先修正 owner。

原生定义只记录环境文件的绝对路径和不含内容的文件 identity metadata；在 Windows 上还记录当前用户的 owner SID， launcher 每次启动都会重新校验它。不复制 credential 或调用者的 shell environment。升级 PowerContext 或修改环境文件后应重新执行 `service install`。显式执行 `service stop` 后，可以在新版环境中运行 `service install`；如果原服务使用环境文件，请继续传入同一个 `--env-file`。即使旧 Python 程序已删除，也可以更新注册，服务仍保持停止并禁止自动启动。确认可以恢复服务后，再执行 `service start`。尚未验证完成的数据库迁移仍会阻止更新注册。

以下命令会删除注册，但保留 Server 数据和日志：

```bash
powercontext service uninstall
```

## 选择网络边界

Server 默认在未启用鉴权的情况下监听 `127.0.0.1:8000`，适合本机客户端使用。鉴权关闭时，不要把监听地址改为非 loopback 地址。

如果需要从其他机器访问：

1. 启用 Bearer 鉴权；
2. 把 Server 放在负责 TLS 的反向代理或私有网络边界后面；
3. 通过 secret manager 或受保护的进程环境提供 token；
4. 只允许 Server 运维者访问数据目录。

内置命令只提供 HTTP，没有 TLS 选项。HTTPS 必须在 PowerContext 外部终止。

## 从已安装工具运行

按照[安装和运行](../get-started/install-and-run.md)安装 PowerContext，然后选择持久化数据目录：

```bash
export POWERCONTEXT_HOME=/srv/powercontext
powercontext server run
```

运行进程必须能创建和更新该目录。默认 SQLite 数据库和 scheduler 状态都保存在这里。服务管理器每次重启进程时都应提供相同的环境变量。

当前目录存在 `.env` 时，`server run` 会自动加载。托管部署应导出变量、由服务管理器或容器平台提供，或者显式传入文件，避免启动行为依赖工作目录：

```bash
powercontext config validate --env-file /etc/powercontext/powercontext.env
powercontext server run --env-file /etc/powercontext/powercontext.env
```

文件可能包含 Provider 凭据或 Bearer token，因此只能允许 Server 运维者读取。对于 `server run`，进程环境变量会覆盖文件中的同名值。`config init` 生成的是不含模型的基础配置；需要启用完整推理能力时，请阅读[启用提取与向量搜索](../get-started/configure-models.md)并补充模型配置。全量配置参数及默认值可参考 [配置选项](configuration.md)。

无论使用前台进程、Docker 还是个人服务安装，只要 generation 或 embedding model 未配置，启动或安装输出都会提示缺少 model 可能影响部分制品功能，具体影响范围及配置方式请参考 [官网配置说明](https://powercontext.oceanbase.io/en/docs/reference/configuration/)；两类 model 都已配置时不输出该提示。

## 监听 Unix domain socket

在 Linux 和 macOS 上，同机代理或网关可以通过 Unix domain socket（UDS）向 Server 发送 HTTP 请求。设置 `POWERCONTEXT_SERVER_HTTP_UDS` 即可选择该监听方式：

```bash
export POWERCONTEXT_SERVER_HTTP_UDS="$HOME/.pc-run/core.sock"
export POWERCONTEXT_SERVER_ACCESS_MODE=enforced
export POWERCONTEXT_SERVER_AUTH_TOKEN="$POWERCONTEXT_DEPLOYMENT_TOKEN"
powercontext server run
```

也可以使用 `powercontext server run --uds /absolute/path/core.sock`，CLI 参数优先于环境变量。启用 UDS 后，Server 不创建 TCP listener，并忽略配置中的 `HTTP_HOST` 和 `HTTP_PORT`。如果有效配置已选择 UDS，再显式传入 `--host` 或 `--port` 会被拒绝。未设置 UDS 时沿用 TCP 行为。UDS 支持 `all` 和 `api` role，其中 `api` 仍要求使用 OceanBase；`background` role 的 infrastructure listener 使用 TCP，因此拒绝 UDS 配置。

仅当同用户运行的可信代理终止 HTTPS，且它是该私有 socket 的唯一外部入口时，设置 `POWERCONTEXT_SERVER_HTTP_UDS_EXTERNAL_SCHEME=https`，并让代理保留浏览器所访问的 `Host` header。这一固定配置使 Dashboard 按 HTTPS 校验 Origin，并设置 Secure cookie。它作用于 socket 上的**每个**请求，因此代理同时转发外部明文 HTTP 时不得启用。直接使用 HTTP-over-UDS 时保持未设置。只接受 `http` 和 `https`，未设置 `HTTP_UDS` 时会拒绝该配置。即使设置 `FORWARDED_ALLOW_IPS=*`，Server 仍忽略 `X-Forwarded-Proto` 和 `X-Forwarded-For`，不会把原生 UDS peer 替换为 loopback 身份。`PUBLIC_URL` 只声明访问地址，不建立协议信任。

请选择较短的私有运行目录：

- socket 路径必须是绝对路径，不含 `..` 或 NUL，按文件系统编码计算不超过 103 字节。 Windows 会拒绝 UDS 配置，不会回退到 TCP。
- Server 会以 `0700` 权限创建缺失的父目录。已有的直接父目录必须是真实目录，由 Server 的有效用户拥有，且 group 和 other 均无访问权限；符号链接父目录会被拒绝。socket 权限为 `0600`，网关应使用同一用户运行。
- socket 旁的 `core.sock.lock` 文件权限为 `0600`，用于串行化启动，退出后仍会保留。正常退出只删除当前进程创建的 socket。 崩溃后重启时，仅在 socket 身份匹配 lock 记录且连接探测确认其已失效时才回收。普通文件、符号链接、仍在监听或身份 未知的 socket 都会保留，并导致启动失败。请保留 lock 文件，以便崩溃恢复。

使用同一用户检查 readiness 和需要鉴权的 API：

```bash
curl --fail --unix-socket "$POWERCONTEXT_SERVER_HTTP_UDS" http://localhost/health/ready
curl --fail --unix-socket "$POWERCONTEXT_SERVER_HTTP_UDS" \
  --header "Authorization: Bearer ${POWERCONTEXT_DEPLOYMENT_TOKEN}" \
  http://localhost/v1/capabilities
```

Python Client 可以传入 HTTPX 的 UDS transport：

```python
import asyncio
import os

import httpx

from powercontext.client import PowerContextClient


async def main():
    transport = httpx.AsyncHTTPTransport(uds=os.environ["POWERCONTEXT_SERVER_HTTP_UDS"], retries=0)
    async with httpx.AsyncClient(transport=transport, trust_env=False) as http:
        client = PowerContextClient(
            "http://localhost",
            token=os.environ["POWERCONTEXT_DEPLOYMENT_TOKEN"],
            http_client=http,
            trust_transport_security=True,
        )
        print((await client.get_capabilities()).model_dump_json())


asyncio.run(main())
```

这里的 `http://localhost` 只是路由标签，实际连接通过 socket 建立。现有 API 路径、请求与响应结构、认证和授权规则继续适用；socket 权限不能替代 Bearer 鉴权。`create_server_app()` 负责创建应用，上述 CLI 负责监听与生命周期。

个人 `service` 命令、本机 `ops` 管理和 Desktop 本机管理仅支持 TCP listener，会拒绝 UDS 配置；配置向导也仅生成 TCP 配置。Core 使用 UDS 时，Agent 宿主接入需要显式提供代理的 HTTP(S) URL。远端 Skill Receiver endpoint 仍要求 HTTPS 或 loopback peer；直接 UDS 连接没有 loopback peer，不会豁免该检查。

Server 使用 UDS 时，设备上的 `powercontext source-agent` 通过 `--server` 传入代理的 HTTP(S) URL 连接中心； 它不会根据 `HTTP_PORT` 推断 TCP 地址。Server 自带的采集 runner 在 Server 内部运行，不需要额外的地址。

这一监听入口提供 [Rust 网关设计（RFC #2199）](https://github.com/oceanbase/powercontext/pull/2199)所需的 Core 传输能力。 Rust 可执行程序、联合 launcher、网关配置、私有通道认证、私有路由和网关状态表仍需单独实现。设计中的网关负责原有公开 TCP 端口，并携带通道认证与用户认证通过 UDS 调用 Core。

可以[安装预编译 Gateway](install-gateway.md)，无需本地 Rust 工具链。Core 仍需单独安装和配置。

### 网关认证边界

`PC_GATEWAY_API_KEY` 仅适用于 `/gateway/v1/chat/completions` 和 `/gateway/v1/responses`。
所有网关模型监听都要求有效的非空 key；该启动校验不负责其他路由的认证。
组合部署（`PC_GATEWAY_CORE_UDS`）转发到 Core 的请求会保留客户端凭据，Core API、MCP 和 Dashboard 请求
继续遵循 Core 自身的认证与访问控制规则。Gateway key 不授予 Core API 权限，UDS 也不能替代 Core 认证。

所有 Gateway 部署（包括独立 TCP 和组合 UDS）都要求 Core 将访问控制设为 `enforced`，并为 Gateway 服务身份配置有效的认证凭据。即使推理隐私保护为 `disabled`、`shadow` 或 `advisory`，特权目标解析仍要求此配置；关闭认证/访问控制的 Core 无法批准 Gateway 模型发送。启动 Gateway 前，请设置 `POWERCONTEXT_SERVER_ACCESS_MODE=enforced` 并配置受支持的认证 Provider。访问受保护 Core 路由的客户端必须使用各自的 Core 凭据；反向代理不会用 `PC_GATEWAY_CORE_TOKEN` 替换客户端凭据。

### 网关 OAuth 订阅

通过 `PC_GATEWAY_SUBSCRIPTION` 选择 `codex`、`copilot`、`grok`、`qwen`、`kimi` 或 `gemini`。Claude CLI 的内部发送不可观察，因此不支持 Core 绑定的出口。HTTP 渠道要求 `PC_GATEWAY_SUBSCRIPTION_CREDENTIALS` 指向私有凭据文件。Codex 和 Gemini 复用 CLI 登录，Grok 与 CLI 协调续期；Copilot、Qwen 和 Kimi 可创建独立设备授权。

构建可执行程序、配置 Core 鉴权及批准的上游 profile 后，在 `gateway/` 下执行：

```bash
install -d -m 700 /path/to/private/oauth
./target/release/pc-gateway login qwen /path/to/private/oauth/qwen.json

PC_GATEWAY_CORE_UDS=/path/to/core.sock \
PC_GATEWAY_CORE_TOKEN="$POWERCONTEXT_DEPLOYMENT_TOKEN" \
PC_GATEWAY_API_KEY="$GATEWAY_CLIENT_TOKEN" \
PC_GATEWAY_SUBSCRIPTION=qwen \
PC_GATEWAY_SUBSCRIPTION_CREDENTIALS=/path/to/private/oauth/qwen.json \
PC_GATEWAY_UPSTREAM_PROFILE=approved-qwen \
./target/release/pc-gateway
```

设备登录也支持 `copilot` 和 `kimi`，创建权限为 `0600` 的新文件，拒绝覆盖已有文件。重新授权应使用新路径并更新服务配置。HTTP 渠道只接受对应服务支持的 HTTPS endpoint，发生错误或送达不确定时不会自动重放模型请求。

Gemini 关闭 Gateway 上下文注入。Codex 的端点无法执行输出 token 上限，因此要求该上限的 Core 策略可能暂缓请求。只有 Core 批准服务商默认的精确地址时，订阅凭据才会发送。凭据、路由和 Core profile 配置见 [Gateway OAuth 订阅](https://github.com/oceanbase/powercontext/blob/master/gateway/SUBSCRIPTIONS.md)。

不要在缺少独立递归边界时，让 Core 内部生成回经自动召回或捕获对话的 Gateway。

通过 TCP 调用 Core（`PC_GATEWAY_CORE_BASE`）时，特权目标解析要求 HTTPS，或使用显式回环 IP 的 HTTP 地址，
例如 `http://127.0.0.1:8000`、`http://[::1]:8000`。普通 HTTP 主机名地址（包括 `http://localhost:8000`）
会被拒绝；DNS 解析不作为回环信任的依据。请改用回环 IP、HTTPS 或 `PC_GATEWAY_CORE_UDS`。
所有 TCP Core 调用均绕过进程环境中的 HTTP 代理（如 `HTTP_PROXY`、`ALL_PROXY`），Core 地址必须可以直连，
以免代理伪造回环地址上的特权目标批准。

### 网关模型目标

模型入口的可信 endpoint、协议和模型由 Core `InferenceConfig.protection.targets` 配置。
Chat 入口选择 `PC_GATEWAY_UPSTREAM_PROFILE`；需要启用 Responses 入口时再设置
`PC_GATEWAY_UPSTREAM_PROFILE_RESPONSES`。每个 Gateway 实例的每个入口只选择一个 profile；同一入口中
请求其他模型会在发送前被拒绝。此前按请求选择模型的部署需要为这些模型分别配置 Gateway 实例。
`PC_GATEWAY_CORE_TOKEN` 必须代表 Gateway 服务身份，且不能与面向客户端的必填 `PC_GATEWAY_API_KEY` 相同。
所有 Gateway 部署（包括组合 UDS 和非 enforcing 推理隐私模式）都要求 Core 将访问控制设为 `enforced`；
关闭认证/访问控制的 Core 无法授权模型发送。专用 Gateway 服务主体需要在客户端通过
`X-PowerContext-Scope-ID` 选择的每个 Scope 上获得 `gateway.user`（`gateway.invoke`），而不只是
`PC_GATEWAY_DEFAULT_SCOPE`；同时要在该部署的 Server resource 上获得 `server.observer`（`server.observe`）。
若设置 `PC_GATEWAY_SEDIMENT=on`，还要授予 `scope.contributor`，替代 `scope.viewer`；前者同时包含准备所需的
`scope.read` 和 Source 采集所需的 `scope.contribute`。关闭采集时，`scope.viewer`（`scope.read`）即可。

引导阶段生成的 Server Token 会映射为内置管理员，并获得静态 Scope 角色。单 Token 部署仍需在客户端选择的每个
Scope 上为它授予 `gateway.user`，而不只是默认 Scope；管理员权限覆盖 Server observer 检查，静态 Scope preset 已包含
viewer/contributor 角色。
托管部署建议为 Gateway 使用最小权限的专用服务身份。使用内置可写 Access Provider 时，先携带 Gateway Token 调用
`GET /v1/access/me`
确认 Principal，再由 Access 管理员为启用的功能创建所需绑定。将下面的 `server-token` 替换为该接口返回的
`service` 主体；POST 请求使用 Access 管理员的凭据。Server resource 的 deployment ID 来自
`POWERCONTEXT_SERVER_ACCESS_DEPLOYMENT_ID`，默认值为 `powercontext`：

```bash
curl --fail "$CORE_URL/v1/access/me" -H "Authorization: Bearer $PC_GATEWAY_CORE_TOKEN"
curl --fail "$CORE_URL/v1/access/bindings/create" -H "Authorization: Bearer $ACCESS_ADMIN_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"subject":{"type":"service","id":"server-token"},"resource":{"type":"scope","scope_id":"<scope_id>"},"role":"gateway.user","idempotency_key":"gateway-user-<scope_id>"}'
curl --fail "$CORE_URL/v1/access/bindings/create" -H "Authorization: Bearer $ACCESS_ADMIN_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"subject":{"type":"service","id":"server-token"},"resource":{"type":"server","deployment_id":"powercontext"},"role":"server.observer","idempotency_key":"gateway-observer-powercontext"}'

# 仅在 PC_GATEWAY_SEDIMENT=on 时需要；它替代 scope.viewer。
curl --fail "$CORE_URL/v1/access/bindings/create" -H "Authorization: Bearer $ACCESS_ADMIN_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"subject":{"type":"service","id":"server-token"},"resource":{"type":"scope","scope_id":"<scope_id>"},"role":"scope.contributor","idempotency_key":"gateway-contributor-<scope_id>"}'
```

关闭采集时，请改为创建 `scope.viewer` 绑定。若 deployment ID 不是默认值，请替换 Server resource 中的
`powercontext`。引导 Server Token 不需要为独立服务身份说明中的 `server.observer` 或 Scope reader 绑定。

非内置可写 Provider 请使用其对应的角色绑定方式。网关只使用获准 `before_send` 返回的 endpoint 和协议；Core
不可用、缺少目标解析或协议不匹配时不发送。
服务商凭据仍由部署方通过 `PC_GATEWAY_UPSTREAM_API_KEY_CHAT` / `PC_GATEWAY_UPSTREAM_API_KEY_RESPONSES`
（或共用的 `PC_GATEWAY_UPSTREAM_API_KEY`）管理；此阶段 Core **不校验**账号/区域身份。权限不同的凭据身份
不能共用一个 profile。上游传输直连 Core 批准的 endpoint，不使用进程环境中的 HTTP 代理设置
（如 `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY`）；请配置可直达的 Core 批准目标。
此前依靠这些代理连接服务商的部署，升级前必须提供直连能力；无法直连时发送会失败，不会回退使用代理。
Core 无法批准目标时，Gateway 不会发送模型请求。

特权发送请求中的模型在所有隐私模式下都必须匹配 Core profile，包括 `disabled`、`shadow` 和 `advisory`。
关闭隐私强制检查不会授予调用其他模型的权限。

### Gateway 请求 Scope

向 `/gateway/v1/chat/completions` 或 `/gateway/v1/responses` 发送 `X-PowerContext-Scope-ID` 请求头，可选择召回、Core 两阶段检查和轮次采集使用的 Scope。请发送一个 Scope ID，由 1–256 个可见 ASCII 字符组成，不含逗号。请求头无效或重复时，返回 HTTP 400 和 `invalid_scope_id`。未发送该请求头时，Gateway 使用 `PC_GATEWAY_DEFAULT_SCOPE`（默认值为 `gateway:default`）。同一对话的每个请求应使用相同的 Scope。Gateway 按 Scope 和对话 ID 隔离已准备上下文的重放。

请只让受信任的客户端使用 Gateway，这些客户端可以选择其 Core 凭据可访问的 Scope。Core 按配置的访问模式检查该凭据的权限。客户端或受信任的代理可以在每个请求中设置此请求头。

### 网关上下文注入

网关通过 UDS 连接 Core（`PC_GATEWAY_CORE_UDS`，即同机组合部署）时，默认向属于某个会话的 Chat 和 Responses 请求注入准备好的上下文。在同一会话内，每条新的用户消息只准备一次，该回合的后续请求（例如 Agent 的工具循环）精确重放同一结果。

网关按以下顺序，取第一个值有效的请求头确定会话：

| 请求头 | 发送方 | 会话 |
| --- | --- | --- |
| `X-PowerContext-Conversation-ID` | 接入 PowerContext 的宿主 | 头的取值；优先于其他所有来源 |
| `x-claude-code-session-id`，以及 `x-claude-code-agent-id` | Claude Code | 会话，每个子代理各自独立 |
| `x-opencode-session-id` | OpenCode | 会话；子会话有自己的 ID |
| `thread-id`（仅 Responses 入口） | Codex | 线程；子代理有自己的线程 ID |

客户端在父代理与子代理之间共享的头不用于确定会话，例如 Codex 的 `session-id` 和 `prompt_cache_key`，以及 OpenCode 的 `x-opencode-session` 和 `x-session-affinity`。Claude Code 使用 Anthropic Messages API，而网关不提供该接口，所以只有在网关前的代理把它的请求转换到 Chat 或 Responses 入口并转发这些头时，Claude Code 的头才会生效。

如果这些头均未提供 ID，网关可以根据入口以及模型、开头的指令和第一条用户消息的哈希，识别携带完整历史的 Chat 或 Responses 请求。历史开头必须有用户消息，且其前面只能有 system 或 developer 消息。通过 `previous_response_id` 或 `conversation` 续接的 Responses 请求需要提供请求头才能复用准备结果。推导出的 ID 仅在当前网关进程内用于重放准备结果。开头完全相同的会话可能共享推导出的 ID，需要区分时请发送显式会话头。

如果既没有显式 ID 也无法推导 ID，组合部署默认跳过注入，并记录 `context injection skipped: no conversation identity`。设置 `PC_GATEWAY_INJECTION=on` 后，没有 ID 的请求如果带有可用的用户消息，就会各自准备。设置 `PC_GATEWAY_INJECTION=off` 关闭注入，设置 `PC_GATEWAY_INJECTION_RESPONSES=off` 仅关闭 Responses 入口的注入。独立部署（`PC_GATEWAY_CORE_BASE`）必须设置 `PC_GATEWAY_INJECTION=on` 才会注入。准备结果为空或准备失败时，请求照常发送，不注入内容。只有用户回合会向 Core 查询上下文；同一回合的后续请求复用已准备的块，不再查询，没有可重放身份的非用户尾部直接发送、不召回。网关为证据与日志判定每个请求尾部的类型，见[Gateway 调用证据](gateway-evidence.md#回合类型)。

设置 `PC_GATEWAY_INJECTION_CODE=on` 后，准备的上下文同时包含 Scope 代码索引中的代码证据；该选项默认关闭，并要求 Scope 已配置代码仓库。重放的内容因被引用的来源变化（例如 Agent 修改了被引用的文件）而不再通过检查时，网关丢弃该会话的重放，为该请求重新准备一次，而不是把 withhold 返回给客户端。

### 网关固定指令

网关默认向 Chat 入口（`/gateway/v1/chat/completions`）和 Responses 入口（`/gateway/v1/responses`）的每个请求加入内置的成本指导指令（S），提示模型定向搜索、合并读取、集中修改并进行针对性验证。`PC_GATEWAY_STEERING=off` 关闭指令；未设置或设为 `on` 时开启，其他值会阻止启动。

`PC_GATEWAY_STEERING_FILE` 用自定义指令替换内置文本。开启指令时，网关只在启动时读取一次该文件。文件必须是 UTF-8，去掉首尾空白后不能为空，不超过 4096 字节，并且不能含有 PowerContext 保留的 `BEGIN_POWERCONTEXT_` 标记；否则网关在启动时退出，不会改用其他指令。`PC_GATEWAY_STEERING=off` 优先于文件配置，关闭时不读取文件。

在 Responses 入口，指令文本成为客户端第一条 developer 消息之后那条 developer 消息的第一个内容块，也就是 Codex 放置自身 `developer_instructions` 的位置。没有这样的消息时，网关在客户端第一条 developer 消息之后插入一条新的 developer 消息；请求中没有 developer 消息时，插在第一条用户消息之前。在 Chat 入口，网关将指令追加到第一条 system 消息，或在第一条用户消息之前插入一条 system 消息。每个请求中的文本都相同，因此它始终是缓存前缀的一部分。Core 把它作为最终请求的一部分，像其他请求文本一样检查。它与上下文注入、沉淀和重放相互独立。

### Gateway 字节容量

Rust Gateway 在启动时通过 `/v1/capabilities` 查询一次 `context_protection`。模型入口要求 OpenAPI 定义的完整 `ContextProtectionCapabilities` DTO： `check_versions`、`payload_versions`、`reuse_versions`、`reuse_families`、`enforcing`，以及包含 `payload_bytes`、`binding_bytes`、`prepared_contexts`、`dependencies`、`send_seconds` 和 `correlate_seconds` 的 `limits`。即使存在输入容量，任何必需字段缺失或类型错误也会暂停准入。 check/payload 版本必须包含 v1，且 `limits.payload_bytes` 必须是正整数。查询失败、版本不兼容、DTO 不完整或容量无效时，两个模型入口返回 HTTP 503 暂停服务； `/healthz` 和已配置的 Core/MCP 转发仍可访问。恢复 Core 契约后，重启 Gateway 以重新发现容量。每次模型发送和响应释放仍须分别通过 Core 检查。若 Core 在 Gateway 重启前收紧限制，这次检查可以拒绝调用；增大 Core 限制不会在重启前放大 Gateway 启动时确认的额度。启动诊断通过固定消息区分传输失败、查询被拒、响应畸形/不完整、版本不兼容和容量无效，不输出 Core 错误值。

`PC_GATEWAY_MAX_BODY_BYTES` 默认 16 MiB（1 MiB = 1,048,576 字节）。模型输入的有效上限是该配置、 Core 声明的输入容量和当前完整正文解析器 1 MiB 硬上限的最小值。部署上限为零时，模型输入禁用，返回 HTTP 503 和 `input_capacity_disabled`，不表示 Core 能力查询失败。 Content-Length 预检和 chunked 正文实际读取都受限。执行器还会在 prepared context 注入/重放、 steering 和协议渲染后，检查最终 UTF-8 JSON 正文字节数。入口正文超限返回 HTTP 413；最终正文超限返回 HTTP 403 和 `request_payload_too_large`，不会发送模型。放大配置不能绕过解析器硬上限。

固定长度和 chunked/SSE provider 响应的默认缓冲上限均为 1 MiB。传输器显式设置的缓冲上限可以进一步收紧该值。输入 `payload_bytes` **不声明**输出额度。 Core 的响应配置、内容检查以及 JSON/RPC 包装仍有各自的限制，因此低于 1 MiB 的正文也可能被拒绝。 UDS 客户端的 8 MiB RPC 响应解析上限属于另一个传输方向，不是模型正文容量。

响应缓冲超限时丢弃响应，返回 HTTP 400 和 `response_buffer_limit_exceeded`。不会释放 provider 正文，也不会自动重发。执行观察保留 `PossiblySent` 与 `Withheld` 终态：provider 可能已经计费。这不代表收到了完整响应，也不承诺零费用。相同的生命周期事实会写入有界且不含正文的调用证据：调用记录保留尝试、终态和发送状态，生产 JSONL sink 已由 Gateway 证据验证覆盖。更小的完整响应仍须通过 `before_release` 才能释放。

透明 Core/MCP 转发继续使用部署正文上限，不消耗模型输入额度。 `PC_GATEWAY_MAX_CONCURRENT`（默认 64）分别限制模型调用与 fallback 调用， 活跃的 fallback SSE 连接不会耗尽模型并发许可。两类合计最多可持有配置值两倍的许可， 不含豁免的健康探测；该设置不是进程全局并发上限。 模型许可由实际 HTTP/1 连接 owner 持有，覆盖本地响应交付及相关请求工作结束， 包括 body EOF/drop 后仍由框架缓冲的待写数据。模型响应声明 `Connection: close`； 连接正常完成表示本地完成，不证明客户端已经读取或接受响应。 原始 180 秒单调期限覆盖入口、执行和本地交付；到期直接结束连接，不另加交付或清理窗口， 部分响应可能截断，错误 JSON 仅尽力返回。框架实际观察到断连时以 `request_cancelled` 停止本地工作； 没有观察到断连的网络失联由原期限收束。交付中断保留已完成的执行终态和模型发送事实， 包括 `ReleaseAuthorized` 与 `ResponseReceived`；取消不证明 provider 回滚，也不代表可以安全重发。 进程内 `ExecutionSnapshot::local_delivery` 用 `NotStarted`、`InProgress`、`Completed` 与 `Interrupted(reason)` 表示模型内容的交付状态；拒绝和生命周期错误响应不启动模型内容交付。 它独立于执行终态，不持有响应字节或并发许可。 fallback 许可仍随响应 body 持有，直到 body drop；fallback 保留认证、keep-alive 与流式行为， 不继承模型期限或强制关闭连接策略。provider 发送与 HTTP Core RPC 使用全新 HTTP/1 连接，不复用空闲连接； UDS Core 客户端保留有界空闲连接池。Gateway 从首个停机信号起最多排空 25 秒，并将进程退出限制在 30 秒内。 `/healthz` 豁免两类并发限制。已知 Content-Length 的 fallback 超限请求在转发前返回 413。 未知长度超限会产生 body error：前缀可能已经到达 Core，转发失败时代理返回 502。 这不是成功截断，Gateway 不会通过完整缓冲 MCP 流来强制统一错误状态码。

这些是逐正文和分类并发限制，不是驻留字节测量，也不构成进程 RSS 上限。全局驻留字节记账与输出容量发现属于 [#2464 的 G5-B 后续工作](https://github.com/oceanbase/powercontext/issues/2464)。

### 复用缓存前缀抽取记忆

网关与 Server 部署在同一主机时，为一个已沉淀回合抽取 Atomic Memory 可以复用服务商为 Agent 会话写下的提示缓存，
而不必再次发送对话记录。网关对支持的上游默认开启前缀抽取；`PC_GATEWAY_PREFIX_EXTRACTION=off` 关闭，`on` 显式开启，其他值会阻止启动。使用 OAuth 订阅时，只有 `codex` 支持前缀抽取，并且必须确认 Server 为非 enforcing：抽取请求和 agent 自己的请求一样用订阅的凭据发出，计入该账号的用量限额，并且不能带输出上限（该端点拒绝这个参数）。其他订阅的流量使用普通抽取。还需配置沉淀与 Server 回连：

- **网关：** 设置 `PC_GATEWAY_SEDIMENT=on` 开启沉淀；沉淀仍默认关闭。网关在内存中保留每个回合终止响应对应的、实际发出的请求及其响应输出，最多 30 分钟、最多 64 个回合、总计最多 64 MiB，
  超出时先丢弃最早的，抽取前已被丢弃的回合走普通抽取；并在沉淀的 Source
  元数据中记录一个不透明的句柄，以及该终止响应的输入和输出 token 数。Chat 前缀抽取适用于携带会话 ID、
  只有一条完整文本回复的非流式 Chat Completions 请求。请求和响应本身从不进入 Source、证据或日志。
  沉淀的 Source 完整记录这一回合（投影 `gateway-turn-text.v2`）：字段不截断、步骤不省略，工具调用的全部参数字段、
  工具输出与错误、其他消息和无法识别的条目都在其中；只有推理密文、ID 和其他传输元数据不进入记录，二进制数据记为带字节数
  的占位文本。注入的记忆块不在 Source 里。一段 Source 按 Core 的字符估算最多 50,000 个 token；Server 生效的
  Atomic Memory 输入上限（`POWERCONTEXT_SERVER_RUNTIME_ATOMIC_MEMORY_INPUT_TOKENS_LIMIT`，默认 64,000）更低时以它为准。
  网关在启动时从 Server 读取这个上限，修改后需重启网关。这个上限针对整个抽取输入，其中还包括抽取指令和 Source 元数据；
  把它设在 50,000 附近或更低时，接近上限的一段 Source 加上这些内容会超出上限，Atomic Memory 对这段 Source 的处理每次都会失败。
  更大的回合按顺序拆成几段 Source，各段在步骤边界结束，元数据的 `turn` 标明所属回合与段号，
  句柄在最后一段上。一个回合的各段在一次批量请求（`POST /v1/sources/content/batch`）里写入 Server：要么全部入库，
  要么都不入库；同一回合已以不同内容入库时整批被拒绝，先写入的记录保留。第一段总是使用回合本身的 Source ID，
  所以重试的回合拆成不同段数时也是如此。沉淀因此要求 Server 提供批量写入；
  Server 的能力报告里没有 `context_protection.limits.capture_batch_sources` 时，网关在启动时记录一条错误，
  回合不沉淀，其他请求不受影响。段数超过该上限的回合同样不沉淀。
- **Server：** 将 `POWERCONTEXT_SERVER_INFERENCE_PREFIX_EXTRACTION_GATEWAY_URL` 设为网关的监听地址；网关要求密钥时，
  将 `POWERCONTEXT_SERVER_INFERENCE_PREFIX_EXTRACTION_GATEWAY_HEADERS` 设为
  `{"Authorization": "Bearer <PC_GATEWAY_API_KEY>"}`。所有 Gateway 监听（包括回环地址）都必须配置 Gateway Key。仍需配置生成模型，因为普通抽取是回退路径。

配置 `POWERCONTEXT_SERVER_INFERENCE_PREFIX_EXTRACTION_GATEWAY_URL` 后，Server 在没有显式指定处理模式时默认使用 `one_pass` 整理 Atomic Memory。设置 `POWERCONTEXT_SERVER_RUNTIME_ATOMIC_MEMORY_PROCESSING_MODE=per_candidate` 可保留逐候选处理，设为 `one_pass` 可显式选择单次整理。未配置回连地址的 Server 和直接使用 SDK 的配置仍默认使用 `per_candidate`。网关不会替独立启动的 Server 自动发现或配置这个地址。

命中缓存的输入并非免费，服务商仍按较低的价格计费。处理窗口恰好只含一个这样的回合时，Server 先估算两种请求的成本：
前缀请求为 Core 的指令加上终止响应的输出 token（按全价），再加上命中缓存的输入 token 乘以
`POWERCONTEXT_SERVER_INFERENCE_PREFIX_EXTRACTION_CACHED_INPUT_RATIO`（默认 `0.1`）；普通抽取为其指令加上 Source
证据。处理窗口须恰好含一个完整回合：一段 Source，或同一回合按顺序排列的全部分段；各段合起来放不进一次抽取输入的回合，
因此逐段走普通抽取：这样的窗口第一次处理时超出输入预算，`/v1/memory/flush` 返回错误并缩小窗口，后续处理逐段完成；
自动处理按退避间隔重试。只有前者更低时才复用前缀。工具输出较多的回合通常满足这一条件；长会话的前缀不断增长后，就不再满足。随后 Server 把抽取指令、输出 Schema 和该 Source 的来源信息发给
`POST /gateway/v1/extract`。网关把它们作为一条用户消息追加在保留的请求和响应之后，关闭抽取请求的工具选择，并发往
Agent 自己的目标和账号。请求保留工具、`prompt_cache_key`、推理设置和响应格式等缓存相关字段。该请求
照常经过 Server 的 `before_send` 和 `before_release` 检查。前缀里含有网关注入的记忆块，抽取指令说明
`BEGIN_POWERCONTEXT_*` 与 `END_POWERCONTEXT_*` 标记之间的内容不是这一回合的证据。enforcing 模式下，Server 会暂扣重放服务商加密推理的请求，
因此对 Codex 等客户端，抽取会回退到普通抽取。它不注入、也不沉淀。Server
用与普通抽取相同的 Schema 和规则校验回复。Server 等待这次请求的时间与普通抽取相同，为
`POWERCONTEXT_SERVER_INFERENCE_GENERATION_TIMEOUT_SECONDS`（默认 30 秒）；网关一侧，从开始接收请求起的 180 秒期限同样适用。
超时后 Server 回退到普通抽取，而已经发出的前缀请求可能仍被服务商计费。

其他情况都回退到普通抽取：窗口不是恰好一个完整回合或没有句柄、估算复用前缀并不更便宜或前缀规模未知、句柄过期或未知、目标不一致、检查被暂扣或阻断、服务商或网关
出错，以及回复未通过校验。每次尝试都记录为 `atomic_memory.prefixed_extraction` 日志事件，参见
[日志、指标与追踪](observability.md#operational-fields-与关联)。模型用量（包括命中缓存的输入 token）与其他
生成一样上报。

设置 `POWERCONTEXT_SERVER_RUNTIME_ATOMIC_MEMORY_PROCESSING_MODE=one_pass` 时，这类窗口的整段单次整理对话都接在前缀
之后进行。第一次请求携带单次整理的流程指令和 Source 的溯源信息。之后每一步重发同一条指令，并带上
`continuation`：模型此前的回复和 Server 的后续消息，按顺序排列，由网关追加在指令之后。因此每一步都原样重复前缀和
此前各步，模型服务可以从提示词缓存读取它们；某一步是否命中缓存以及 token 用量由模型服务决定，以它上报的用量为准。
这段对话发往智能体自己的目标和账号，而不是 Server 的生成模型：后续步骤要比对的已有记忆和写入的文本都会发往该目标。
单次整理的流程指令是智能体会话之后的一条用户消息，而不是系统指令；Server 对每次回复的校验与普通的单次整理对话
相同。估算比较的是每个窗口都有的
前两步：缓存输入 token 按缓存价格计两次，加上指令和最终响应的输出 token，与 Server 自己发送 Source 证据时的同样
两步相比。由于每一步都要读一遍前缀，仍然划算的前缀长度大约是前缀抽取的一半。任何一步无法完成，或回复在请求数上限
允许的纠正之后仍未通过校验时，这段对话的结果全部丢弃，窗口按普通的单次整理对话重新开始。不接受 `continuation`
的网关会拒绝第二步，这样的窗口要多付一次第一步的费用；请将网关与 Server 一同升级。

## 使用 Docker 运行

在仓库根目录构建镜像：

```bash
POWERCONTEXT_VERSION=$(uvx --from hatchling --with hatch-vcs hatchling version)
docker build \
  --file docker/Dockerfile \
  --build-arg "POWERCONTEXT_VERSION=${POWERCONTEXT_VERSION}" \
  --tag powercontext-server:local \
  .
```

使用 named volume，并且只在宿主机 loopback 地址发布端口：

```bash
docker run --rm \
  --name powercontext-server \
  --publish 127.0.0.1:8000:8000 \
  --volume powercontext-data:/data \
  powercontext-server:local
```

镜像内部监听 `0.0.0.0:8000`，所以 `--publish` 中的宿主机地址非常重要。容器停止后，named volume 仍会保留 SQLite 数据库和 scheduler 状态。

## 启用鉴权

从 secret manager 把强 token 加载到 Server 进程环境：

```bash
export POWERCONTEXT_SERVER_ACCESS_MODE=enforced
export POWERCONTEXT_SERVER_AUTH_TOKEN="$POWERCONTEXT_DEPLOYMENT_TOKEN"
powercontext server run
```

使用 Docker 时，只传递已经加载的环境变量，不要把 token 值写进命令：

```bash
docker run --rm \
  --name powercontext-server \
  --publish 127.0.0.1:8000:8000 \
  --volume powercontext-data:/data \
  --env POWERCONTEXT_SERVER_ACCESS_MODE=enforced \
  --env POWERCONTEXT_SERVER_AUTH_TOKEN \
  powercontext-server:local
```

此后客户端需要发送 `Authorization: Bearer <token>`。liveness 和 readiness endpoint 保持公开，便于编排系统探测； API、MCP、metrics 和 `/openapi.json` 需要鉴权。`/docs` 页面外壳保持公开，但在交互式参考页中发起的请求仍需鉴权。

个人或演示部署可额外设置 `POWERCONTEXT_SERVER_DASHBOARD_ENABLED=true`，启用同一端口上的 `/dashboard/home`。`ACCESS_MODE=enforced` 时要求上述静态 Bearer 配置，没有 token 时启动会明确失败；使用 `ACCESS_MODE=disabled` 的本地部署可直接打开 Dashboard，无需登录。启用认证时，浏览器登录使用 Server token，不是模型 API key。凭据存入仅限 `/dashboard` 的 HttpOnly、SameSite=Strict Cookie，最长八小时；HTTPS 下设置 Secure。反向代理应正确传递外部 scheme 和 host，以通过登录同源检查。

静态 token 的所有持有者具有同一个管理员身份。Dashboard 不支持多成员 RBAC，也不提供账号、SSO、邀请和授权管理。注入 Authentication Provider 或 AccessControlService 的部署必须关闭 Dashboard；不兼容的启用配置会在启动时被拒绝。关闭 Dashboard 不影响团队的 API 和 MCP。个人启用步骤见[安装和运行](../get-started/install-and-run.md)。

共享静态 token 不会强制人工审核，也不会阻止 Agent 批准自己的 Candidate。需要分离提交和审批时，应使用不同的已认证 Principal，并仅向审核者授予审核权限。参见[分离 Agent 提交与审核](../workflows/review-candidates.md#分离-agent-提交与审核)。

### 迁移认证开启前写入的数据

`ACCESS_MODE=disabled` 期间接受的数据没有 Artifact owner，也没有 Candidate owner attestation。切换到 `enforced` 之后，这些数据仍可读取，但修改会返回 `403`，审批其待审 Candidate 会返回 `503 artifact_owner_pending`；在认证模式下新建的数据不受影响。Server 不会推断归属，需要管理员显式迁移缺失的授权信息：

1. 先备份数据库。下面的离线命令在 Server 停止时执行，切换前后都可以运行。
2. 用 `powercontext server access-ownership-migrate --action plan` 盘点受影响的数据。`ACCESS_MODE=disabled` 期间同样可以运行。添加 `--background-principal service:后台身份ID`，填写切换后准备使用的 `ACCESS_BACKGROUND_PRINCIPAL_ID`，即可检查每个指定 Scope 的 `scope.contribute` 和已有 Profile 写权限。 owner 缺口补齐后仍会执行这些检查。已配置单 Token 的部署会计入后台的静态 Scope 权限预设，预检不会创建授权。 已有 Profile 仍须单独通过 Artifact 写权限检查。HTTP 预检在授权存储可用时接受 `background_principal`；否则返回 `503 relationship_management_unavailable`。开启认证前请使用离线命令执行后台权限预检。
3. 用显式指定的归属 Principal 回补。单用户静态 token 部署就是 `server-token` 服务 Principal：

   ```bash
   powercontext server access-ownership-migrate \
     --action apply --owner service:server-token --maintenance-confirmed
   ```

   `apply` 要求先用 `--maintenance-confirmed` 确认所有 Server 进程已停止。大型部署可以重复传 `--scope-id` 分批迁移。每一项都是一次幂等写入，中断或重复执行只会补齐仍缺失的条目；已有的 owner 和 Candidate attestation 会被保留，冲突时明确报告，不会被覆盖。不同身份需要拥有不同 Scope 时，用另一个 `--owner` 再次执行；多用户部署由此保持明确的身份边界。

   盘点包含已合并和已退休的 Artifact 身份，以及已提交 Handoff 和各发布副本的实际身份，以保留恢复操作所需的权限。 如果持久化恢复记录已经确定归属， 离线 `apply`、HTTP `apply` 及 HTTP 预览会报告 `owner-recovery-pending` 或 `owner-recovery-delivered_relation_missing`， 并保留原始记录及其归属。待投递的归属信息应按[归属恢复流程](../workflows/scopes-and-access.md)继续处理。 Candidate 恢复预检覆盖迁移接受的家族，包括 Prompt Candidate，不受独立 Artifact owner 家族范围限制。 使用外部 Access 存储时，如果审计写入失败，迁移会保留可重放的归属投递任务。即使 owner 关系已存在， 重复同一 HTTP `apply` 也会先完成原始审计，再报告成功。预览不会重放待处理任务。

   如果没有停机窗口，也可以对运行中的 Server 走 HTTP：`POST /v1/access/ownership/apply`（先用 `dry_run: true` 预览）。认证模式下两个 HTTP 操作都要求 `server.admin`；`ACCESS_MODE=disabled` 时 `apply` 返回 `503 ownership_migration_unavailable`。
4. 验证：修改一个此前不可变的 Artifact，审批一个此前被阻塞的 Candidate，并确认 `plan` 不再报告归属缺口。 预检时传入后台 Principal，确认 `background` 为空。没有相应 Artifact 写权限或 Candidate 审批权限的身份 仍会收到 `403`。

## 检查部署

使用 liveness 判断进程能否响应 HTTP 请求：

```bash
curl --fail http://127.0.0.1:8000/health/live
```

发送业务流量前检查 readiness：

```bash
curl --fail http://127.0.0.1:8000/health/ready
```

必需的 Runtime 或数据库绑定不可用时，readiness 返回 HTTP 503。可选推理服务故障时可能返回 HTTP 200 和 `degraded`，数据库操作仍然可用。因此 `curl --fail` 只能判断 HTTP 状态，不能把 `degraded` 判定为失败。如果部署依赖推理能力，应进一步要求响应中的 `status` 为 `ready`：

```bash
curl --fail --silent --show-error http://127.0.0.1:8000/health/ready \
  | python3 -c 'import json,sys; data=json.load(sys.stdin); print(data["status"]); sys.exit(data["status"] != "ready")'
```

启用鉴权后，还应检查一个受保护的 endpoint：

```bash
curl --fail \
  --header "Authorization: Bearer ${POWERCONTEXT_DEPLOYMENT_TOKEN}" \
  http://127.0.0.1:8000/v1/capabilities
```

请求示例见 [HTTP API](../develop/http-api.md)，全部 Server 设置见[配置](configuration.md)。

## 保护和备份数据

- 备份 `POWERCONTEXT_HOME` 指向的目录，或挂载到 `/data` 的 Docker volume。
- 执行文件系统级 SQLite 备份时，应先停止写入或停止 Server。
- 不要把数据库备份或 Bearer token 放进仓库。
- 在依赖备份流程前先验证恢复操作。
