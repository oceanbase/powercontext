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

### 迁移认证开启前写入的数据

`ACCESS_MODE=disabled` 期间接受的数据没有 Artifact owner，也没有 Candidate owner attestation。切换到 `enforced`
之后，这些数据仍可读取，但修改会返回 `403`，审批其待审 Candidate 会返回 `503 artifact_owner_pending`；在认证模式下
新建的数据不受影响。Server 不会推断归属，需要管理员显式迁移缺失的授权信息：

1. 先备份数据库。下面的离线命令在 Server 停止时执行，切换前后都可以运行。
2. 用 `powercontext server access-ownership-migrate --action plan` 盘点受影响的数据。`ACCESS_MODE=disabled`
   期间同样可以运行。添加 `--background-principal service:后台身份ID`，填写切换后准备使用的
   `ACCESS_BACKGROUND_PRINCIPAL_ID`，即可检查每个指定 Scope 的 `scope.contribute` 和已有 Profile 写权限。
   owner 缺口补齐后仍会执行这些检查。HTTP 预检在授权存储可用时接受 `background_principal`；否则返回
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

   盘点包含已合并和已退休的 Artifact 身份，以保留恢复操作所需的权限。如果持久化恢复记录已经确定归属，
   离线 `apply`、HTTP `apply` 及 HTTP 预览会报告 `owner-recovery-pending` 或 `owner-recovery-delivered_relation_missing`，
   并保留原始记录及其归属。待投递的归属信息应按[归属恢复流程](../workflows/scopes-and-access.md)继续处理。
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
