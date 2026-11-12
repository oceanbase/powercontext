---
title: 部署 Server
description: 使用持久化数据、健康检查、鉴权和安全网络边界运行 PowerContext。
---

# 部署 Server

远程 Agent 的地址配置与 `--allow-insecure-http` 确认见[连接远程 Server](connect-remote-server.md)。
这是客户端选项，不会修改 Server 的监听地址或鉴权设置。

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

个人服务仅支持 loopback 地址；即使已启用鉴权，把 `POWERCONTEXT_SERVER_HTTP_HOST` 配置为非 loopback 地址也会导致
`service install` 拒绝安装。需要从其他机器访问时，请使用容器或管理员拥有的服务管理器，或者由同机反向代理转发到
loopback Server。

在 Windows 上，如果没有提供 `--start-on-login` 或 `--no-start-on-login`，命令会询问是否在当前用户下次登录时
自动启动；直接按 Enter 的默认选择是不启用。需要非交互选择时，请提供其中一个选项。

使用显式 Server 配置时，先保护并验证环境文件：

```bash
chmod 600 /path/to/powercontext.env
powercontext config validate --env-file /path/to/powercontext.env
powercontext service install --env-file /path/to/powercontext.env
```

安装成功后的摘要会显示实际使用的环境文件路径。若启用了 Bearer 鉴权，请从该文件中的
`POWERCONTEXT_SERVER_AUTH_TOKEN` 读取令牌；命令不会在终端打印令牌值。默认配置关闭鉴权，因此不会自动生成令牌。

在 Windows 上，校验前需要移除继承权限，只授予当前用户、`SYSTEM` 和本机 `Administrators` 访问权限，例如：

```powershell
icacls $env:USERPROFILE\powercontext.env /inheritance:r /grant:r "${env:USERNAME}:(F)" "SYSTEM:(F)" "Administrators:(F)"
```

该 `icacls` 命令只调整 ACL，不会更改文件 owner；如果 owner 不是当前用户，需要先修正 owner。

原生定义只记录环境文件的绝对路径和不含内容的文件 identity metadata；在 Windows 上还记录当前用户的 owner SID，
launcher 每次启动都会重新校验它。不复制 credential 或调用者的 shell environment。
升级 PowerContext 或修改环境文件后应重新执行 `service install`。显式执行 `service stop` 后，可以在新版环境中运行
`service install`；如果原服务使用环境文件，请继续传入同一个 `--env-file`。即使旧 Python 程序已删除，也可以更新注册，
服务仍保持停止并禁止自动启动。确认可以恢复服务后，再执行 `service start`。尚未验证完成的数据库迁移仍会阻止更新注册。

以下命令会删除注册，但保留 Server 数据和日志：

```bash
powercontext service uninstall
```

## 选择网络边界

Server 默认在未启用鉴权的情况下监听 `127.0.0.1:8000`，适合本机客户端使用。鉴权关闭时，不要把监听地址改为非
loopback 地址。

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

运行进程必须能创建和更新该目录。默认 SQLite 数据库也保存持久 Scheduler、Worker lease 和 Operation 状态。
服务管理器每次重启进程时都应提供相同的环境变量。

当前目录存在 `.env` 时，`server run` 会自动加载。托管部署应导出变量、由服务管理器或容器平台提供，或者显式传入文件，
避免启动行为依赖工作目录：

```bash
powercontext config validate --env-file /etc/powercontext/powercontext.env
powercontext server run --env-file /etc/powercontext/powercontext.env
```

文件可能包含 Provider 凭据或 Bearer token，因此只能允许 Server 运维者读取。对于 `server run`，进程环境变量会覆盖
文件中的同名值。`config init` 生成的是不含模型的基础配置；需要启用完整
推理能力时，请阅读[启用提取与向量搜索](../get-started/configure-models.md)并补充模型配置。全量配置参数及默认值可参考
[配置选项](configuration.md)。

无论使用前台进程、Docker 还是个人服务安装，只要 generation 或 embedding model 未配置，启动或安装输出都会提示
缺少 model 可能影响部分制品功能，具体影响范围及配置方式请参考
[官网配置说明](https://powercontext.oceanbase.io/en/docs/reference/configuration/)；两类 model 都已配置时不输出该提示。

## 监听 Unix domain socket

在 Linux 和 macOS 上，同机代理或网关可以通过 Unix domain socket（UDS）向 Server 发送 HTTP 请求。
设置 `POWERCONTEXT_SERVER_HTTP_UDS` 即可选择该监听方式：

```bash
export POWERCONTEXT_SERVER_HTTP_UDS="$HOME/.pc-run/core.sock"
export POWERCONTEXT_SERVER_ACCESS_MODE=enforced
export POWERCONTEXT_SERVER_AUTH_TOKEN="$POWERCONTEXT_DEPLOYMENT_TOKEN"
powercontext server run
```

也可以使用 `powercontext server run --uds /absolute/path/core.sock`，CLI 参数优先于环境变量。
启用 UDS 后，Server 不创建 TCP listener，并忽略配置中的 `HTTP_HOST` 和 `HTTP_PORT`。如果有效配置已选择 UDS，
再显式传入 `--host` 或 `--port` 会被拒绝。未设置 UDS 时沿用 TCP 行为。UDS 支持 `all` 和 `api` role，
其中 `api` 仍要求使用 OceanBase；`background` role 的 infrastructure listener 使用 TCP，因此拒绝 UDS 配置。

仅当同用户运行的可信代理终止 HTTPS，且它是该私有 socket 的唯一外部入口时，设置
`POWERCONTEXT_SERVER_HTTP_UDS_EXTERNAL_SCHEME=https`，并让代理保留浏览器所访问的 `Host` header。
这一固定配置使 Dashboard 按 HTTPS 校验 Origin，并设置 Secure cookie。它作用于 socket 上的**每个**请求，
因此代理同时转发外部明文 HTTP 时不得启用。直接使用 HTTP-over-UDS 时保持未设置。只接受 `http` 和 `https`，
未设置 `HTTP_UDS` 时会拒绝该配置。即使设置 `FORWARDED_ALLOW_IPS=*`，Server 仍忽略 `X-Forwarded-Proto`
和 `X-Forwarded-For`，不会把原生 UDS peer 替换为 loopback 身份。`PUBLIC_URL` 只声明访问地址，不建立协议信任。

请选择较短的私有运行目录：

- socket 路径必须是绝对路径，不含 `..` 或 NUL，按文件系统编码计算不超过 103 字节。
  Windows 会拒绝 UDS 配置，不会回退到 TCP。
- Server 会以 `0700` 权限创建缺失的父目录。已有的直接父目录必须是真实目录，由 Server 的有效用户拥有，且 group 和
  other 均无访问权限；符号链接父目录会被拒绝。socket 权限为 `0600`，网关应使用同一用户运行。
- socket 旁的 `core.sock.lock` 文件权限为 `0600`，用于串行化启动，退出后仍会保留。正常退出只删除当前进程创建的 socket。
  崩溃后重启时，仅在 socket 身份匹配 lock 记录且连接探测确认其已失效时才回收。普通文件、符号链接、仍在监听或身份
  未知的 socket 都会保留，并导致启动失败。请保留 lock 文件，以便崩溃恢复。

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

这里的 `http://localhost` 只是路由标签，实际连接通过 socket 建立。现有 API 路径、请求与响应结构、认证和授权规则
继续适用；socket 权限不能替代 Bearer 鉴权。`create_server_app()` 负责创建应用，上述 CLI 负责监听与生命周期。

个人 `service` 命令、本机 `ops` 管理和 Desktop 本机管理仅支持 TCP listener，会拒绝 UDS 配置；配置向导也仅生成 TCP
配置。Core 使用 UDS 时，Agent 宿主接入需要显式提供代理的 HTTP(S) URL。远端 Skill Receiver endpoint 仍要求 HTTPS
或 loopback peer；直接 UDS 连接没有 loopback peer，不会豁免该检查。

Server 使用 UDS 时，通过 `POWERCONTEXT_SERVER_SOURCE_PLUGINS_DIR` 或
`POWERCONTEXT_SERVER_SOURCE_MANAGEMENT_AUTOSTART` 启用 Source 自动部署，还需将
`POWERCONTEXT_SERVER_PUBLIC_URL` 配置为代理地址。collect manager 和 runtime 通过该 URL 连接；未配置时会在创建
部署凭据或进程前拒绝启动。`source load --kind write` 使用同一配置，也可显式传入 `--server-url`。UDS 模式下，
这些命令不会根据 `HTTP_PORT` 推断 TCP 地址。

这一监听入口提供 [Rust 网关设计（RFC #2199）](https://github.com/oceanbase/powercontext/pull/2199)所需的 Core 传输能力。
Rust 可执行程序、联合 launcher、网关配置、私有通道认证、私有路由和网关状态表仍需单独实现。设计中的网关负责原有
公开 TCP 端口，并携带通道认证与用户认证通过 UDS 调用 Core。

### 网关认证边界

`PC_GATEWAY_API_KEY` 仅适用于 `/gateway/v1/chat/completions` 和 `/gateway/v1/responses`。
非 loopback 网关监听要求配置有效的非空 key，但该启动校验不负责其他路由的认证。
组合部署（`PC_GATEWAY_CORE_UDS`）转发到 Core 的请求会保留客户端凭据，Core API、MCP 和 Dashboard 请求
继续遵循 Core 自身的认证与访问控制规则。Gateway key 不授予 Core API 权限，UDS 也不能替代 Core 认证。

组合部署对外开放前，须设置 `POWERCONTEXT_SERVER_ACCESS_MODE=enforced` 并配置有效的 Core 认证凭据，
如上文的 UDS 示例所示。访问受保护 Core 路由的客户端必须使用 Core 凭据。
`PC_GATEWAY_CORE_TOKEN` 用于网关主动发起的 Core 调用；反向代理不会用它替换客户端凭据。

### 网关上下文注入

网关通过 UDS 连接 Core（`PC_GATEWAY_CORE_UDS`，即同机组合部署）时，默认向属于某个会话的 Chat 和 Responses 请求注入
准备好的上下文。在同一会话内，每条新的用户消息只准备一次，该回合的后续请求（例如 Agent 的工具循环）精确重放同一结果。

网关按以下顺序，取第一个出现的请求头确定会话：

| 请求头 | 发送方 | 会话 |
| --- | --- | --- |
| `X-PowerContext-Conversation-ID` | 接入 PowerContext 的宿主 | 头的取值；优先于其他所有来源 |
| `x-claude-code-session-id`，以及 `x-claude-code-agent-id` | Claude Code | 会话，每个子代理各自独立 |
| `x-opencode-session-id` | OpenCode | 会话；子会话有自己的 ID |
| `thread-id`（仅 Responses 入口） | Codex | 线程；子代理有自己的线程 ID |

客户端在父代理与子代理之间共享的头不用于确定会话，例如 Codex 的 `session-id` 和 `prompt_cache_key`，以及 OpenCode 的
`x-opencode-session` 和 `x-session-affinity`。Claude Code 使用 Anthropic Messages API，而网关不提供该接口，所以只有在
网关前的代理把它的请求转换到 Chat 或 Responses 入口并转发这些头时，Claude Code 的头才会生效。设置 `PC_GATEWAY_INJECTION=on` 会注入所有
请求（没有会话 ID 的请求各自准备），设置 `PC_GATEWAY_INJECTION=off` 关闭注入，设置
`PC_GATEWAY_INJECTION_RESPONSES=off` 只关闭 Responses 入口的注入。独立运行的网关（`PC_GATEWAY_CORE_BASE`）只有
设置 `PC_GATEWAY_INJECTION=on` 时才注入。准备结果为空或准备失败时，请求照常发送，不注入内容。

设置 `PC_GATEWAY_INJECTION_CODE=on` 后，准备的上下文同时包含 Scope 代码索引中的代码证据；该选项默认关闭，并要求 Scope
已配置代码仓库。重放的内容因被引用的来源变化（例如 Agent 修改了被引用的文件）而不再通过检查时，网关丢弃该会话的重放，
为该请求重新准备一次，而不是把 withhold 返回给客户端。

### 网关固定指令

`PC_GATEWAY_STEERING_FILE` 指定一个文件，其中是一段简短的固定指令，网关把它加入 Responses 入口
（`/gateway/v1/responses`）的每个请求；Chat 入口的请求不受影响。网关只在启动时读取一次该文件。文件必须是 UTF-8，
去掉首尾空白后不能为空，不超过 4096 字节，并且不能含有 PowerContext 保留的 `BEGIN_POWERCONTEXT_` 标记；否则网关在
启动时退出，而不是不带指令运行。未设置该变量时不加入任何内容。

指令文本成为客户端第一条 developer 消息之后那条 developer 消息的第一个内容块，也就是 Codex 放置自身
`developer_instructions` 的位置。没有这样的消息时，网关在客户端第一条 developer 消息之后插入一条新的 developer 消息；
请求中没有 developer 消息时，插在第一条用户消息之前。每个请求中的文本都相同，因此它始终是缓存前缀的一部分。Core 把它
作为最终请求的一部分，像其他请求文本一样检查。它与上下文注入、沉淀和重放相互独立。

### Gateway 字节容量

Rust Gateway 在启动时通过 `/v1/capabilities` 查询一次 `context_protection`。
模型入口要求 OpenAPI 定义的完整 `ContextProtectionCapabilities` DTO：
`check_versions`、`payload_versions`、`reuse_versions`、`reuse_families`、`enforcing`，以及
包含 `payload_bytes`、`binding_bytes`、`prepared_contexts`、`dependencies`、`send_seconds` 和
`correlate_seconds` 的 `limits`。即使存在输入容量，任何必需字段缺失或类型错误也会暂停准入。
check/payload 版本必须包含 v1，且 `limits.payload_bytes` 必须是正整数。
查询失败、版本不兼容、DTO 不完整或容量无效时，两个模型入口返回 HTTP 503 暂停服务；
`/healthz` 和已配置的 Core/MCP 转发仍可访问。恢复 Core 契约后，重启 Gateway 以重新发现容量。
每次模型发送和响应释放仍须分别通过 Core 检查。若 Core 在 Gateway 重启前收紧限制，
这次检查可以拒绝调用；增大 Core 限制不会在重启前放大 Gateway 启动时确认的额度。
启动诊断通过固定消息区分传输失败、查询被拒、响应畸形/不完整、版本不兼容和容量无效，
不输出 Core 错误值。

`PC_GATEWAY_MAX_BODY_BYTES` 默认 16 MiB（1 MiB = 1,048,576 字节）。模型输入的有效上限是该配置、
Core 声明的输入容量和当前完整正文解析器 1 MiB 硬上限的最小值。
部署上限为零时，模型输入禁用，返回 HTTP 503 和 `input_capacity_disabled`，不表示 Core 能力查询失败。
Content-Length 预检和 chunked 正文实际读取都受限。执行器还会在 prepared context 注入/重放、
Responses steering 和协议渲染后，检查最终 UTF-8 JSON 正文字节数。
入口正文超限返回 HTTP 413；最终正文超限返回 HTTP 403 和 `request_payload_too_large`，不会发送模型。
放大配置不能绕过解析器硬上限。

固定长度和 chunked/SSE provider 响应的默认缓冲上限均为 1 MiB。
传输器显式设置的缓冲上限可以进一步收紧该值。输入 `payload_bytes` **不声明**输出额度。
Core 的响应配置、内容检查以及 JSON/RPC 包装仍有各自的限制，因此低于 1 MiB 的正文也可能被拒绝。
UDS 客户端的 8 MiB RPC 响应解析上限属于另一个传输方向，不是模型正文容量。

响应缓冲超限时丢弃响应，返回 HTTP 400 和 `response_buffer_limit_exceeded`。
不会释放 provider 正文，也不会自动重发。执行观察保留 `PossiblySent` 与 `Withheld` 终态：
provider 可能已经计费。这不代表收到了完整响应，也不承诺零费用。
该观察仅在进程内可见；本阶段尚未导出含尝试次数、执行终态与发送状态的可读取调用记录。
G5-A 的“可能已计费进入调用证据”仍待接入 [#2506](https://github.com/oceanbase/powercontext/pull/2506)，
并通过导出记录的回归测试后才能验收。
更小的完整响应仍须通过 `before_release` 才能释放。

透明 Core/MCP 转发继续使用部署正文上限，不消耗模型输入额度。
`PC_GATEWAY_MAX_CONCURRENT`（默认 64）分别限制模型调用与 fallback 调用，
活跃的 fallback SSE 连接不会耗尽模型并发许可。两类合计最多可持有配置值两倍的许可，
不含豁免的健康探测；该设置不是进程全局并发上限。每个许可随响应 body 持有，直到 body drop；
`/healthz` 豁免两类并发限制。已知 Content-Length 的 fallback 超限请求在转发前返回 413。
未知长度超限会产生 body error：前缀可能已经到达 Core，转发失败时代理返回 502。
这不是成功截断，Gateway 不会通过完整缓冲 MCP 流来强制统一错误状态码。

这些是逐正文和分类并发限制，不是驻留字节测量，也不构成进程 RSS 上限。
全局驻留字节记账与输出容量发现属于 [#2464 的 G5-B 后续工作](https://github.com/oceanbase/powercontext/issues/2464)。

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

镜像内部监听 `0.0.0.0:8000`，所以 `--publish` 中的宿主机地址非常重要。容器停止后，named volume 仍会保留
SQLite 数据库和持久 work 状态。

## 运行分布式角色

分布式模式要求 OceanBase，并且任何角色启动前都必须先迁移 schema。仓库中的
`docker/compose.distributed.yaml` 提供两 API、两 Scheduler、两 Worker 的拓扑示例。一个 Scheduler 成为 leader，
另一个保持 ready 并可接管；两个 API 和两个 Worker 都会同时工作。

分布式 Work Ledger 负责调度 Memory、Experience 和 Profile 工作。分布式 v1 不支持 Topic Memory processing，必须
保持其周期未设置。该拓扑由 `POWERCONTEXT_SERVER_DEPLOYMENT_ROLE` 拆分进程，因此单机 Artifact Processing
Supervisor 的 role 必须保持为 `all`。

通过环境传入 secret 和部署选择，不要把它们写进 Compose：

```bash
export POWERCONTEXT_SERVER_DATABASE_URL="$OCEANBASE_URL"
export POWERCONTEXT_SERVER_AUTH_TOKEN="$POWERCONTEXT_DEPLOYMENT_TOKEN"
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL="openai:gpt-4.1-mini"
export OPENAI_API_KEY
docker compose --file docker/compose.distributed.yaml up migrate
docker compose --file docker/compose.distributed.yaml up -d api-a api-b scheduler-a scheduler-b worker-a worker-b
```

两个 API 示例分别监听宿主机 8001 和 8002 端口。应在它们前面配置终止 TLS 的 load balancer，并使用不带 session
affinity 的 round-robin；分布式 MCP 是 stateless。示例有意只把模型 credential 交给 Worker，也不向宿主机发布
Scheduler 或 Worker 的端口。生产环境还应为各角色使用独立最小权限数据库账号，而不是复用示例 URL。

同一轮发布的所有副本必须使用相同 behavior revision。升级顺序如下：

1. 使用专门的 DDL 账号运行 `migrate` 任务（即 Compose 文件中接线的 `server db-migrate plan` →
   `apply` 流程），配备固定维护主机上的持久 evidence 目录以及文档要求的备份与确认选项；
2. 替换 Worker 并等待 readiness；
3. 替换 Scheduler 并确认 leader 可以扫描；
4. 替换 API。

旧 Worker 排空前，`POWERCONTEXT_SERVER_COORDINATION_EMIT_PAYLOAD_VERSION` 必须保持旧的受支持值。回滚顺序相反，
并且绝不能让分布式角色连接尚未升级到 packaged revision 的 schema。

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

此后客户端需要发送 `Authorization: Bearer <token>`。liveness 和 readiness endpoint 保持公开，便于编排系统探测；
API、MCP、metrics 和 `/openapi.json` 需要鉴权。`/docs` 页面外壳保持公开，但在交互式参考页中发起的请求仍需鉴权。

个人或演示部署可额外设置 `POWERCONTEXT_SERVER_DASHBOARD_ENABLED=true`，启用同一端口上的
`/dashboard/home`。`ACCESS_MODE=enforced` 时要求上述静态 Bearer 配置，没有 token 时启动会明确失败；
使用 `ACCESS_MODE=disabled` 的本地部署可直接打开 Dashboard，无需登录。
启用认证时，浏览器登录使用 Server token，不是模型 API key。凭据存入仅限 `/dashboard` 的 HttpOnly、SameSite=Strict
Cookie，最长八小时；HTTPS 下设置 Secure。反向代理应正确传递外部 scheme 和 host，以通过登录同源检查。

静态 token 的所有持有者具有同一个管理员身份。Dashboard 不支持多成员 RBAC，也不提供账号、SSO、邀请和授权管理。
注入 Authentication Provider 或 AccessControlService 的部署必须关闭 Dashboard；不兼容的启用配置会在启动时被拒绝。
关闭 Dashboard 不影响团队的 API 和 MCP。个人启用步骤见[安装和运行](../get-started/install-and-run.md)。

共享静态 token 不会强制人工审核，也不会阻止 Agent 批准自己的 Candidate。需要分离提交和审批时，应使用不同的已认证
Principal，并仅向审核者授予审核权限。参见[分离 Agent 提交与审核](../workflows/review-candidates.md#分离-agent-提交与审核)。

### 迁移认证开启前写入的数据

`ACCESS_MODE=disabled` 期间接受的数据没有 Artifact owner，也没有 Candidate owner attestation。切换到 `enforced`
之后，这些数据仍可读取，但修改会返回 `403`，审批其待审 Candidate 会返回 `503 artifact_owner_pending`；在认证模式下
新建的数据不受影响。Server 不会推断归属，需要管理员显式迁移缺失的授权信息：

1. 先备份数据库。下面的离线命令在 Server 停止时执行，切换前后都可以运行。
2. 用 `powercontext server access-ownership-migrate --action plan` 盘点受影响的数据。`ACCESS_MODE=disabled`
   期间同样可以运行。添加 `--background-principal service:后台身份ID`，填写切换后准备使用的
   `ACCESS_BACKGROUND_PRINCIPAL_ID`，即可检查每个指定 Scope 的 `scope.contribute` 和已有 Profile 写权限。
   owner 缺口补齐后仍会执行这些检查。已配置单 Token 的部署会计入后台的静态 Scope 权限预设，预检不会创建授权。
   已有 Profile 仍须单独通过 Artifact 写权限检查。HTTP 预检在授权存储可用时接受 `background_principal`；否则返回
   `503 relationship_management_unavailable`。开启认证前请使用离线命令执行后台权限预检。
3. 用显式指定的归属 Principal 回补。单用户静态 token 部署就是 `server-token` 服务 Principal：

   ```bash
   powercontext server access-ownership-migrate \
     --action apply --owner service:server-token --maintenance-confirmed
   ```

   `apply` 要求先用 `--maintenance-confirmed` 确认所有 Server 进程已停止。大型部署可以重复传 `--scope-id`
   分批迁移。每一项都是一次幂等写入，中断或重复执行只会补齐仍缺失的条目；已有的 owner 和 Candidate
   attestation 会被保留，冲突时明确报告，不会被覆盖。不同身份需要拥有不同 Scope 时，用另一个 `--owner`
   再次执行；多用户部署由此保持明确的身份边界。

   盘点包含已合并和已退休的 Artifact 身份，以及已提交 Handoff 和各发布副本的实际身份，以保留恢复操作所需的权限。
   如果持久化恢复记录已经确定归属，
   离线 `apply`、HTTP `apply` 及 HTTP 预览会报告 `owner-recovery-pending` 或 `owner-recovery-delivered_relation_missing`，
   并保留原始记录及其归属。待投递的归属信息应按[归属恢复流程](../workflows/scopes-and-access.md)继续处理。
   Candidate 恢复预检覆盖迁移接受的家族，包括 Prompt Candidate，不受独立 Artifact owner 家族范围限制。
   使用外部 Access 存储时，如果审计写入失败，迁移会保留可重放的归属投递任务。即使 owner 关系已存在，
   重复同一 HTTP `apply` 也会先完成原始审计，再报告成功。预览不会重放待处理任务。

   如果没有停机窗口，也可以对运行中的 Server 走 HTTP：`POST /v1/access/ownership/apply`（先用
   `dry_run: true` 预览）。认证模式下两个 HTTP 操作都要求 `server.admin`；`ACCESS_MODE=disabled` 时 `apply` 返回
   `503 ownership_migration_unavailable`。
4. 验证：修改一个此前不可变的 Artifact，审批一个此前被阻塞的 Candidate，并确认 `plan` 不再报告归属缺口。
   预检时传入后台 Principal，确认 `background` 为空。没有相应 Artifact 写权限或 Candidate 审批权限的身份
   仍会收到 `403`。

## 检查部署

使用 liveness 判断进程能否响应 HTTP 请求：

```bash
curl --fail http://127.0.0.1:8000/health/live
```

发送业务流量前检查 readiness：

```bash
curl --fail http://127.0.0.1:8000/health/ready
```

必需的 Runtime 或数据库绑定不可用时，readiness 返回 HTTP 503。可选推理服务故障时可能返回 HTTP 200 和
`degraded`，数据库操作仍然可用。因此 `curl --fail` 只能判断 HTTP 状态，不能把 `degraded` 判定为失败。如果部署依赖
推理能力，应进一步要求响应中的 `status` 为 `ready`：

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
