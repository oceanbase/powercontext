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

运行进程必须能创建和更新该目录。默认 SQLite 数据库和 scheduler 状态都保存在这里。服务管理器每次重启进程时都应
提供相同的环境变量。

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
SQLite 数据库和 scheduler 状态。

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
