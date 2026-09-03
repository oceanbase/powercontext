---
title: 部署 Server
description: 使用持久化数据、健康检查、鉴权和安全网络边界运行 PowerContext。
---

# 部署 Server

`powercontext server run` 是前台进程。在个人 macOS 或 Linux 工作站上，PowerContext 可以把同一个 Server runner 注册到
原生当前用户服务管理器。托管部署仍应使用容器平台或管理员拥有的服务管理器。

## 运行持久个人 Server

安装并启动可选的当前用户服务：

```bash
powercontext service install
powercontext service status
```

Linux 使用 `systemd --user`，日志进入 user journal；macOS 使用当前用户 LaunchAgent，stdout 和 stderr 写入
PowerContext 用户数据目录。`service status` 会返回精确的日志 selector 或路径。安装器不请求管理员权限，并且只接受
loopback Server bind。

使用显式 Server 配置时，先保护并验证环境文件：

```bash
chmod 600 /path/to/powercontext.env
powercontext config validate --env-file /path/to/powercontext.env
powercontext service install --env-file /path/to/powercontext.env
```

原生定义只记录环境文件的绝对路径和不含内容的文件 identity metadata，不复制 credential 或调用者的 shell environment。
升级 PowerContext 或修改环境文件后应重新执行 `service install`。以下命令会删除注册，但保留 Server 数据和日志：

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

按照[安装和运行](install-and-run.md)安装 PowerContext，然后选择持久化数据目录：

```bash
export POWERCONTEXT_HOME=/srv/powercontext
powercontext server run
```

运行进程必须能创建和更新该目录。默认 SQLite 数据库和 scheduler 状态都保存在这里。服务管理器每次重启进程时都应
提供相同的环境变量。

PowerContext 不会自动搜索 `.env` 文件。可以导出变量、由服务管理器或容器平台提供，或者显式传入一个文件：

```bash
powercontext config validate --env-file /etc/powercontext/powercontext.env
powercontext server run --env-file /etc/powercontext/powercontext.env
```

文件可能包含 Provider 凭据或 Bearer token，因此只能允许 Server 运维者读取。文件中的值会覆盖进程中的同名值；
文件中不存在的旧 `POWERCONTEXT_SERVER_*` 进程变量会被忽略。需要交互式生成并校验配置文件时，请阅读
[完整功能 Quick Start](full-capability-runtime.md)。

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
export POWERCONTEXT_SERVER_AUTH_ENABLED=true
export POWERCONTEXT_SERVER_AUTH_TOKEN="$POWERCONTEXT_DEPLOYMENT_TOKEN"
powercontext server run
```

使用 Docker 时，只传递已经加载的环境变量，不要把 token 值写进命令：

```bash
docker run --rm \
  --name powercontext-server \
  --publish 127.0.0.1:8000:8000 \
  --volume powercontext-data:/data \
  --env POWERCONTEXT_SERVER_AUTH_ENABLED=true \
  --env POWERCONTEXT_SERVER_AUTH_TOKEN \
  powercontext-server:local
```

此后客户端需要发送 `Authorization: Bearer <token>`。liveness 和 readiness endpoint 保持公开，便于编排系统探测；
API、MCP、metrics 和 `/openapi.json` 需要鉴权。`/docs` 页面外壳保持公开，但在交互式参考页中发起的请求仍需鉴权。
Server 的网页外壳和静态资源仍保持公开，以便显示登录表单；未提供 token 时不会返回受保护数据。打开 Dashboard、
Skills、Review 或 Handoff Report 页面后，在表单中输入同一个 token。浏览器会把它保存在当前标签页的 session storage
中，而不是加入 URL。

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
`degraded`，数据库操作仍然可用。

启用鉴权后，还应检查一个受保护的 endpoint：

```bash
curl --fail \
  --header "Authorization: Bearer ${POWERCONTEXT_DEPLOYMENT_TOKEN}" \
  http://127.0.0.1:8000/v1/capabilities
```

请求示例见 [HTTP API](../reference/http-api.md)，全部 Server 设置见[配置](../reference/configuration.md)。

## 保护和备份数据

- 备份 `POWERCONTEXT_HOME` 指向的目录，或挂载到 `/data` 的 Docker volume。
- 执行文件系统级 SQLite 备份时，应先停止写入或停止 Server。
- 不要把数据库备份或 Bearer token 放进仓库。
- 在依赖备份流程前先验证恢复操作。
