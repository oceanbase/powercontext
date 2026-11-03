---
title: 参考监控环境
description: 按需启动 metrics、traces 和结构化 logs 存储，自动配置 dashboard 和告警。
---

# 参考监控环境

需要在本机存储和查询三种运行信号时，可以启动
[参考部署](https://github.com/oceanbase/powercontext/tree/master/deploy/observability)。
Prometheus 存 metrics，Tempo 存 traces，Loki 存精简后的结构化 logs，Grafana 自动配置 datasource 和 dashboard。
一个 OpenTelemetry Collector 通过 OTLP/HTTP 接收 traces 和 logs。
托管环境还会启动一个本地 Phoenix，用于查看 trace。普通本地使用和嵌入 SDK 不需要这些服务；
外部 Phoenix、Langfuse 接入方式仍可通过 `connect` 使用。

## 接入已有监控平台

已有 Prometheus、Collector、Tempo、Loki 和 Grafana 时，不需要运行 `up` 或安装 Docker。
使用 `connect` 生成本地接入文件；它不修改远程配置，不启动或停止任何组件：

```bash
powercontext observability connect \
  --otlp-endpoint http://existing-collector:4318 \
  --server-env /path/to/existing.env \
  --metrics-target powercontext.example.internal:8000 \
  --prometheus-url https://monitoring.example/prometheus \
  --tempo-url https://monitoring.example/tempo \
  --loki-url https://monitoring.example/loki
```

`--otlp-endpoint` 是 OTLP/HTTP 基础地址，不能填 gRPC 端口或以 `/v1/traces` 结尾的地址。
地址应能从 Server 所在网络访问；后端查询地址应能从运行 CLI 的机器访问。查询地址支持反向代理路径。
启用 TLS 的地址默认校验证书；私有 CA 需安装到对应进程使用的信任存储中。

若要使用与自建栈不同的配置目录，先设置 `POWERCONTEXT_OBSERVABILITY_HOME`。
同一目录不能同时保存 managed 和 external 配置，命令不会覆盖已有部署。
重复执行不带参数的 `connect` 可查看已有连接；修改连接请显式编辑保存的连接和私有配置，或选择新目录。

认证信息使用 JSON 文件传入，例如 `{"Authorization":"Bearer ...","X-Scope-OrgID":"tenant"}`。
`--otlp-headers-file` 配置 Server 导出凭证；`--prometheus-headers-file`、`--tempo-headers-file` 和
`--loki-headers-file` 分别配置查询凭证，不会跨后端复用。不要把凭证放入 URL 或命令参数。

命令输出配置目录，其中 `external/README.md` 列出具体接入操作：

- 将 `prometheus-scrape.yaml` 合并到已有抓取配置；认证开启时，把生成的 `observe-token` 只读挂载给
  Prometheus，并按容器运行用户配置读取权限。该 token 沿用原有 Server 凭证。
- 在 Grafana Import 中导入 `external/dashboards/` 下的 JSON，并选择已有数据源；不要求固定 UID。
  exemplar 和 trace-to-logs 关联需在已有数据源中按说明配置。
- 在已有 Collector 的 logs pipeline 中加入 `otlp`，并接入生成的字段白名单 transform。Server 直接通过
  OTLP/HTTP 发送精简后的结构化字段。指定 `--log-directory /path/to/logs` 只配置轮转 JSON 文件供本地审计；
  不要为同一个 Server 再加 filelog，否则日志可能重复。
- 正常停止原 Server，保持原业务配置，使用 `server run --env-file /path/to/existing.env --observability`
  重启。原生个人服务也可在安装时加 `--observability`；Docker 服务可额外加载生成的 `server.env`。
- 按需审核和导入 `external/alerts.yaml`；其中不包含参考监控栈专用的 telemetry 告警。
  Collector fragment 不是完整配置，不会替换现有 receiver、exporter、pipeline 或告警通知路由。

```bash
powercontext observability status
powercontext observability check
powercontext observability check --exercise
```

`status` 只显示保存的配置。`check` 查询已配置的后端，不发送业务请求：缺少查询地址显示 `NOT VERIFIED`，
最近 15 分钟没有 trace/log 显示 `IDLE OR NOT DELIVERED`，不会宣称全链路正常。
`--exercise` 要求三个查询地址齐全，会发送一个明确采样的只读 capabilities 请求，等待同一 trace ID 的
trace、日志和 metric exemplar。已有 Prometheus 必须启用 OpenMetrics 抓取和 exemplar 存储。
`up/down` 会拒绝控制 external 连接中的组件。

## 使用 CLI 接入已有 Server

安装包含 `cli`、`server` 和 `tracing-otlp` extras 的 PowerContext。无需下载源码，也无需自己查找 Compose 文件。
Docker 和 Compose 必须已经安装，Docker 引擎必须正在运行。

```bash
powercontext observability up --server-env /path/to/existing.env
powercontext server run --env-file /path/to/existing.env --observability
powercontext observability check --exercise
```

第一条命令读取已有配置中的地址、端口和 Bearer token，不修改该文件，也不启动第二个业务 Server。
第二条命令替代原来的 Server 启动命令；先正常停止旧进程，再使用相同配置、相同数据库启动。
CLI 不会自动停止业务进程。`--observability` 只加载遥测配置；显式遥测配置冲突时会报错，且不打印凭证。
环境文件和进程环境中的 `OTEL_EXPORTER_OTLP_{TRACES,LOGS}_{ENDPOINT,HEADERS,TIMEOUT}` 专用配置，
以及 `OTEL_SDK_DISABLED`，都会被拒绝。使用 `--observability` 前应移除这些设置；如需保留，
请手动配置遥测并省略该标志。
如果没有环境文件，使用 `observability up` 和 `server run --observability`。
非默认地址可通过首次 `up --server-url http://127.0.0.1:8000` 指定。

`--backend docker-compose` 是默认值；当前版本只支持这个 backend。`local`、`kubernetes` 等值会明确报错。
backend 指监控栈的启动方式，与业务 Server 本身的启动方式独立。

配置保存在平台用户数据目录下的 `powercontext/observability`；它不依赖当前工作目录，也不跟随业务
`POWERCONTEXT_HOME` 改变。可通过 `POWERCONTEXT_OBSERVABILITY_HOME` 指定独立目录。
首次 `up` 输出配置位置、Grafana 与 Phoenix 地址，以及 Grafana 密码文件位置；密码不会打印到终端。
重复 `up` 不覆盖凭证、模板或已保存的地址。初始化选项只在首次运行使用。

`up` 会输出可复制的重启命令、`server.env`（Docker 模式为 `container.env`）路径，以及实际使用的遥测变量：
Tracing 开关、日志目录、OTLP 地址和超时、采样与批量发送设置。认证 Header 只显示存储位置。
CLI 的 `--observability` 自动加载遥测文件，无需手工 export。首次 `up` 使用 `--server-env` 时，会保存该文件路径
并带入重启命令。自定义启动程序可按输出中的 POSIX 命令导出 `server.env` 后，再执行原启动命令。
保留已有业务配置，并确保 Server 自身的 Python 环境安装了 `tracing-otlp` extra。

### 本地 Phoenix

Phoenix 是同一个 Compose 文件中的服务：一个容器同时提供 Web UI 和 OTLP/HTTP 接收端，端口为 6006。
默认地址为 `http://127.0.0.1:6006`；首次 `up` 时可用 `--phoenix-port` 指定其他端口。
Phoenix 使用 SQLite 将 Trace 存放在持久化数据卷中。UI 没有内置登录，因此只把宿主机 loopback 端口映射出来。
请保留该数据卷；重复 `up` 不会清空已存储的 Trace。

Collector 配置内置了 `otlphttp/phoenix` exporter 并接入 traces pipeline，因此同一份 Trace 同时到达 Tempo 和
Phoenix，无需在运行时改写任何随包发布的文件，Server 的遥测配置也不变。
`up` 不阻塞等待 Phoenix 就绪：它与其它后端一起启动，镜像拉取慢不会让原本健康的部署失败。
用 `status` 查看其状态，用 `check --exercise` 在 Phoenix 中查询此次请求的 Trace ID。
不增加 Prompt、内容或模型响应采集。`down` 停止 Phoenix 并保留数据卷；只有显式删除数据卷才会丢弃其中的 Trace。

完整监控栈建议给 Docker 分配约 4 GiB 内存。Phoenix 镜像约 1.5 GB，运行时额外占用约 0.5-1 GiB；
首次拉取镜像和启动可能比普通重启耗时更长。

### Docker 到宿主机的网络

Prometheus 默认通过 `host.docker.internal` 访问本地 Server。macOS Docker Desktop 和原生 Linux 的网络行为不同。
Linux 上，容器不能直接访问宿主机只监听 `127.0.0.1` 的 Server。必须显式选择 Docker 可达的监听地址和
`--metrics-target host:port`，启用认证，并用主机防火墙限制访问。CLI 不会自动放宽监听地址或关闭认证。
原生个人服务要求 loopback；Linux 上不要为了接入监控绕过这个约束，可改用已认证的前台托管部署或 Docker 部署。
`up` 成功仅表示 Compose 启动完成，不表示已经抓取到业务数据；使用 `check` 核对。

### 原生个人服务

首次注册时保存接入选项，之后使用普通服务生命周期命令：

```bash
powercontext service install --env-file /path/to/existing.env --observability
powercontext service status
```

沿用原生服务的环境文件权限和身份校验。已显式停止的服务在重新安装后仍保持停止，需要时执行
`powercontext service start`。重新注册服务时如需保留监控接入，请继续传入 `--observability`。

### 已有 Docker 服务

项目正式 `docker/Dockerfile` 包含 OTLP exporter。先更新到包含此能力的镜像，保留业务数据卷。
假设业务 Compose 服务名为 `powercontext`，其镜像使用默认 UID/GID `10001:10001`：

```bash
powercontext observability up --server-mode docker \
  --server-env /path/to/existing.env --server-url http://127.0.0.1:8000
```

CLI 输出目录中的 `server.compose.yaml` 是业务 Compose 的附加配置。将它与已有配置合并，只更新业务容器：

```bash
docker compose -f /path/to/business.yaml -f /absolute/observability/server.compose.yaml \
  up -d --no-deps powercontext
powercontext observability check --exercise
```

将附加文件替换成 CLI 输出的真实绝对路径。它增加遥测环境文件、专用日志卷和监控网络，不设置业务数据库、
镜像或启动命令。若服务名不同，修改该文件的 `powercontext` 服务键，并用
`--metrics-target actual-service-name:8000` 初始化。若用户 ID 不同，首次传入 `--server-uid` 和 `--server-gid`。
专用日志卷通过一次性容器设置目录所有者，业务容器保持非 root 运行；Collector 只读挂载日志卷，不接触业务数据库卷。
业务 Compose 的显式 `environment` 优先于附加 `env_file`；移除冲突的旧遥测配置或按现有部署手动配置。
原有自定义 networks、日志目录和容器权限也需要核对。此参考方式用于同一 Docker 主机，单副本更新会短暂停机。

API/background 拆分部署可以首次传入 `--background-target background-service:8001`；两个进程分别启用同一接入配置，
Docker background 服务也需要增加相同日志卷和监控网络。当前没有 Kubernetes 安装入口，也不下载本地监控二进制。

### 查看、验证和停止

```bash
powercontext observability status
powercontext observability check
powercontext observability down
```

`status` 只查看容器；`check` 只查询后端，报告指标抓取状态，以及最近 15 分钟是否有 trace、日志。
没有近期记录会显示“可能空闲或尚未送达”，不会冒充端到端成功。
`check --exercise` 明确发送一条已采样的只读 capabilities 请求，在 60 秒内等待 Tempo trace、Loki 对应日志、
Prometheus 对应 exemplar；不创建业务 Source 或 Memory。
`down` 仅停止该 CLI 管理的监控栈，不停止业务 Server，也不删除 volume、配置或密码。

同一目录的并发修改会被锁阻止；中断后如留下 `operation.lock`，确认没有操作运行再删除锁。
端口冲突时，在首次初始化使用 `--grafana-port`、`--gateway-port`。组件启动失败后可运行 `status`，排除原因后重复 `up`。
升级 PowerContext 不会自动替换已部署的配置；模板版本不兼容时会明确报错，应使用匹配版本处理现有部署。

### 日志导出

正式 Server 可以直接通过 OTLP/HTTP 发送字段白名单内的结构化日志，并保留原控制台输出。启用
`POWERCONTEXT_SERVER_LOGGING_OTLP_ENABLED=true` 后，生成的 `server.env` 会配置 OTLP 地址、认证头和有限的
日志导出队列；队列满、Collector 不可达或导出失败不会改变业务结果。

可选 JSON 文件用于本地审计。每个进程使用独立文件，避免多个进程同时轮转同一个文件。每个文件上限 10 MiB，
保留 3 个备份；启动时清理到最多 64 个进程文件组，约 2.5 GiB 的名义上限，活跃进程重新写入时可能超过，
因此它不是严格的磁盘配额。Collector 不读取该目录；它使用 OTLP 字段白名单，移除任意消息正文、异常堆栈和路径。

## 从源码启动

安装 Docker Compose、OpenSSL 和 `uv`，在仓库根目录执行：

```bash
uv run --locked python deploy/observability/init.py
docker compose -f deploy/observability/compose.yaml up -d
uv run --locked --extra tracing-otlp python deploy/observability/run-host.py
```

最后一条命令保持前台运行。它在 18080 端口启动带认证的独立 Server，使用单独的数据库，并清除继承的
PowerContext 和模型配置。它不会调用付费模型，也不会自动运行合成写入任务。为让 Docker 可以访问宿主机，
示例监听宿主机网络接口；应通过防火墙限制访问。现有数据库不会挂入容器。

打开 <http://127.0.0.1:13000>，用户名为 `admin`，密码见
`deploy/observability/.local/grafana-password`。在另一个终端执行：

```bash
uv run --locked --extra tracing-otlp python deploy/observability/verify.py
```

验证程序发送一个明确采样的请求，再从 Prometheus、Tempo 和 Loki 查询实际存储的数据，包括 metric exemplar
和相同 trace ID 的日志。它还检查认证、datasource 和 dashboard。结果保存在 Git 忽略的 `.local` 目录。
端口打开或容器健康不能代替存储查询验收。

## 部署与读数

部署目录的 README 包含容器模式、macOS/Linux 宿主机网络、独立观察凭据、保留时间、资源限制、采样、
队列和重试上限，以及监控故障验证命令。分离的 API/background 服务需要 OceanBase，必须分别抓取。
background listener 只有 metrics/liveness，使用独立端口。Server 启动不依赖监控存储先达到健康状态。

Service dashboard 显示请求、延迟和应用结果。Processing 显示本地待处理观察、确认完成、策略阻止和 worker/recorder
状态。Dependencies 显示缓存的依赖检查、采集链路和当前 Prometheus 告警。每个 panel 都注明数据来源和解释范围。
缺失和过期数据表示未知；usage 不是权威账单。pending 时间只是本地观察的下界，不是持久 backlog 的完整年龄。

trace 到 log 的关联查询已配置。将 log 的 trace ID 复制到 Tempo Explore，可确认它是否已存储；未采样请求也有 ID。
只有 sampled context 才能在 OpenMetrics 响应中产生 exemplar；导出失败或保留时间到期仍可能让 trace 缺失。
界面提供按已有 ID 查询调用链的入口；返回空值时保持证据缺失，不能假定调用链已保存。Collector 发往 Loki 前仅保留明确允许的运行字段，
删除自由文本 message 和 traceback；允许字段中的值仍须由应用保证安全。

告警覆盖目标不可达、依赖异常、达到最低次数的持续错误、待处理任务长期无进展、信号过期/缺失和采集链路失败。
no-op 和策略 hold 不算故障。处理方法和验收范围见部署目录中的 `RUNBOOKS.md`、`ACCEPTANCE.md`。
告警可在 Grafana/Prometheus 查看；外部通知需要运维自行接入，默认不发送消息。

该环境使用单节点本地存储，backend 端口仅发布到 loopback，容器网络是内部信任边界。
远程部署必须在受管理的入口配置 TLS。保留时间异步生效，不等于磁盘配额。关闭服务保留 named volumes；
用于生产前应检查备份、磁盘容量和故障恢复要求。

## Scope 与业务看板

**全服务总览**是默认监控首页，每 15 秒刷新，从 Prometheus 和当前规则状态接口读取数据。
首屏不查询 Scope 库存，统计适配器不可用时仍能查看服务与采集状态。先选择服务角色和实例，再查看当前异常、
观测新鲜度、依赖状态、请求趋势和后台进展。

| 看板 | 筛选参数 | 默认展示 |
| --- | --- | --- |
| 全服务总览 | 服务角色、实例 | 当前异常与处理入口、就绪、抓取时间、依赖、请求、执行失败、延迟和积压 |
| Scope 业务进展 | Scope ID、UTC 用量周期 | 当前处理状态、失败执行证据、产物、候选和反馈 |
| 后台处理 | 服务角色、实例、处理类型 | 执行/排队/并发上限、结果待确认、结果、耗时及按处理类型进入的日志 |
| 模型调用 | 服务角色、实例、Provider、协议能力、执行进程 | 调用次数、结果比例、P50/P95、父进程/Worker、HTTP 状态和逐次日志 |
| 服务与采集 | 服务角色、实例、操作、处理类型、错误分类 | 请求结果、状态码、依赖、采集状态与筛选后的诊断日志 |

五个主页保留在 `PowerContext` 文件夹。库存与用量（`pc-business`）、Scope 活动（`pc-scopes`）和依赖明细
（`pc-dependencies`）保留在 `PowerContext 高级明细`，原 URL 可继续使用。Scope 快照使用手动刷新，避免定时
重复扫描业务库存。库存、Worker 确认、请求量、宿主采用和 Token 估算保持各自统计含义。

就绪卡与抓取状态分开。就绪超过 120 秒、抓取超过 45 秒或目标不可达时，就绪显示未知。抓取失败可能来自进程、
网络或观察凭据，不能直接解释为数据库故障。当前异常通过受限的 `pc-alerts` Infinity 数据源查询
`/api/v1/alerts`，显示异常名称、状态、实例、条件开始时间、影响、检查建议和处理指南。待确认与已触发分开，
不把历史时序样本当作当前告警列表。筛选服务角色时仍保留采集平台异常。导出失败图展示速率，无流量不解释为
测得的 0% 失败率。

先点击曲线上的数据点固定提示框，再选择“查看此时的相关日志”；仅悬停不会显示链接。目标页会打开该点前后各
5 分钟的诊断日志。链接保留已有的实例、角色、操作和处理类型标签。
模型表格行按该实例和所选时间范围打开日志。日志应用可见的操作、处理类型和错误分类正则筛选，并通过
`service_owner_role` 包含所属目标及角色的 Worker。精确实例关联要求日志的 `service_target` 与 Prometheus `instance` 端点一致；旧日志未标记目标时，
仅在全部实例筛选下可见。字段定义见[身份与关联约定](observability.md#operational-fields-与关联)。

服务与采集看板先显示当前异常、就绪、抓取、进行中请求和就绪新鲜度，再显示 HTTP / MCP 请求量与延迟趋势。
请求量和业务操作结果按 outcome 汇总，操作级累计保留在下方的可排序接口表。状态与累计取页面时间范围末端；
结束时间为 `now` 时表示当前值。告警列表显示当前 pending / firing，不受历史时间范围影响。
操作、处理类型、错误分类、结果和状态码正则只筛选日志。曲线点和接口表操作名打开对应服务实例的日志；
接口表链接把中文操作名称还原为稳定的 operation ID。服务诊断页默认暂停自动刷新，避免展开详情被收起；
需要持续观察时，在右上角选择刷新间隔。显式带有 `refresh=15s` 的链接仍会自动刷新。

“请求持续失败”先打开最近 5 分钟按操作、HTTP 状态码分组的失败响应估算数，点击数量再进入同组失败日志。
401 表示认证被拒绝，不能直接解释成凭据过期；403、参数错误和 5xx 分开排查。MCP 失败不伪造 HTTP 状态码。
“后台处理停滞”打开同实例、同 family 的历史阻止证据，时间范围覆盖最近 24 小时或更早的已观察等待起点，
最多回看 30 天。历史记录需对照当前后台状态，`window_attempt_limit` 只解释停止继续尝试的原因，
不证明此前失败的原因。告警 `activeAt` 是规则开始满足条件的时间，不是原始错误发生时间。
“打开指南”进入本机服务页内置说明，不依赖远端 GitHub 分支文件。

“日志最近记录”按实例、角色显示最近 24 小时找到的最新事件时间，不受其他日志筛选影响。
有请求却长期没有新日志时应检查日志采集；空闲服务的旧日志时间不自动视为故障。缺少记录保持证据不足。
已运行的旧进程需要正常重启才能加载直接 OTLP 日志配置；更新看板或重启 Collector 不能替代此步骤。
若容器内挂载文件不随宿主机更新，不应通过反复重启 Collector 将一次性补采误报为持续采集恢复。

日志摘要显示事件、操作、结果、错误分类、耗时和执行标识。展开记录可查看结构化字段并点击 **View trace**。
Tempo 可反向查询同 Trace 的日志。调用链缺失时保持证据不足；仅凭 ID 不能判定是未采样、未投递还是已过保留期。
聚合指标对应一个时间窗，exemplar 只表示其中一个采样执行。

已有部署文件由使用者管理。先备份人工编辑的文件，再合并看板、数据源及 Collector 的 `service_target`、`service_owner_role` 字段，
仅重启受影响的监控组件。日志接收器同时支持根目录 JSONL 和一层子目录中的 JSONL，可分开存放各服务日志。
保留凭据、抓取目标和数据卷。在 `OTEL_RESOURCE_ATTRIBUTES` 中配置 API 与可选后台目标，并通过正常服务重启
加载新代码。只更新看板不会给旧进程增加字段。Dashboard 文件由 provisioning watcher 加载；替换文件时避免反复
调用 provisioning 热重载接口，固定版本无法安全重载时使用受控的 Grafana 重启。

外部导入会生成 `alerts-datasource.yaml`。将固定 Prometheus URL 调整为 Grafana 可达地址，保护其中可选的私有
查询凭据；导入总览和当前异常面板时选择此数据源。Prometheus、Loki、Tempo 和业务统计数据源仍分别管理。

HTTP 状态在服务页显示速率和当前进程累计值，排除健康检查、metrics 和 MCP bridge。它们是对外 REST 状态，
不是上游 Provider 状态；流式响应可保留 HTTP 200，同时执行失败。具体见[响应计数约定](observability.md)。

启用 Provider 观测时，加载新 Server 代码，复制 `pc-providers.json` 和已更新的 Overview、Service 导航。
合并 Collector 白名单字段 `provider`、`model_capability`、`provider_retry`、`provider_error_code`，验证配置后重启
Collector；看板使用文件自动加载。后台报告延迟、未知重试标记和 HTTP 覆盖不可用的含义，见
[Provider 观测约定](observability.md#provider-调用观测)。只更新看板不会给旧进程增加埋点。

在 **Business** 输入 Scope ID 后按 Enter，通过 **Refresh** 获取新快照。默认关闭自动刷新：统计接口会查询
持久化库存和用量，成本取决于选中数据量。`as_of` 是当前库存快照时间；`today`、`7d`、`30d` 选择 UTC 模型和
召回用量周期，与 Grafana 时间选择器独立。Memory 数量统计当前 atomic Memory head，不统计历史修订或成功的 worker
调用。失效包括 forgotten、merged、retired。已处理 Source 可能得到 no-op。缺失、无权限和不可用都表示未知；
提供方未返回的 token 数保留 null。

内部统计适配器仅调用 `POST /v1/stats`，使用原 Server 身份及其 Scope 读取权限。适配器使用独立访问凭据、固定上游、
5 秒超时、8 个并发请求上限及 16 MiB 响应上限，不发布主机端口，不提供业务写入路由，不缓存 Scope 响应。
Grafana 安装固定为 4.1.3 的签名 Infinity 插件。所有 Grafana 读者共享配置的统计身份，因此只应向可信运维人员开放；
Grafana 登录不会为每位用户建立独立的 PowerContext 身份，不能把这个运维数据源作为租户门户公开。

**活动与证据明细**（`pc-scopes`）从已保存的结构化日志计算统计值，不把 Scope ID 放入 Prometheus 标签。
`application.scope.completed` 表示一个 Scope 参与的一次应用操作。涉及两个 Scope 的请求产生两条参与观测，
原有全局请求计数仍只增加一次。Scope API 图表排除统计轮询。应用派发前的拒绝没有可信的 Scope 归属，认证和参数校验
失败仍在 Service 中查看。无流量、日志丢失或保留期届满不能解释为健康的零值；这些面板需要开启 INFO 操作日志。

Scope ID 保留在日志结构化字段 `scope` 和 span 属性 `powercontext.scope.id` / `powercontext.scope.ids` 中，
不作为 Loki 流标签或 Prometheus 标签。Runtime 和真实子进程中的模型 span 继承所属 Scope。Role 中的 **Background**
同时包含子进程 `worker` 事件；**Combined** 选择 `all`。共享 CPU/RSS、数据库健康和全局队列容量没有单一 Scope 归属，
继续明确显示为全局，不随 Scope 筛选而分摊。

外部部署会生成 `stats-proxy.py`、私有 `stats.json` 和 `stats-datasource.yaml`。按生成的 README 启动只读适配器，
安装 Infinity 4.1.3，并调整固定上游和 Grafana 可达地址。Prometheus、Tempo、Loki 仍可使用现有数据源。
已保存的托管部署配置不会在升级包后被模板自动覆盖。

## Scope 详情看板

从主看板导航打开 **Scope 业务进展**，选择一个 Scope 后刷新。页首保留
**Scope · 服务流程 · 当前快照** 原生 Canvas，连接输入库存、当前调度与执行、候选审核、产物库存、
检索和生命周期维护。Canvas 下方的处理表按失败和策略阻止优先排序，再显示产物、候选和 Experience
反馈；检索、日志、调用链和拓扑证据默认折叠。库存和任务是当前快照；日志与指标趋势使用 Grafana 时间范围。

页首使用 Grafana 原生 Canvas 展示节点和连线。点击节点进入对应明细，并保留 Scope 和时间范围。Worker 节点显示本地观察到的状态；缺失观察显示灰色。接口速率、检索耗时和非空率显示共享服务最近 5 分钟的数据。连线表示处理关系，不证明某份输入已完成全部步骤；没有宿主回执时，Agent 采用情况保持未知。

拓扑覆盖 Memory、Topic Memory、Profile、Experience、Skill、Handoff、Prompt 七类后台处理。Coding Agent 单列在 Client 分组，位于服务端接入与持久化之外。Source 调度按配置连接 Memory、Topic Memory、Profile、Experience；Skill、Handoff、Prompt 的后台工作来自显式 Dream，Dream 也可以修订前四类。Prompt 另有直接配置写入路径。新增 Source 不会自动生成 Prompt，但显式 Prompt Dream 可以选取 Source 作为证据。每行并列展示产物与待审数量，不把所有路径都解释为必须审核。Profile 是否审核由策略决定；Handoff 库存只包含 committed 记录，临时 Prepared Handoff 不计入；Prompt 库存是持久化配置，不是已生效次数。

四类可检索产物显示各自的共享直接检索 p95；上下文准备纳入下方整体检索指标，不按 Family 分摊。Profile 显示直接 head/revision 读取的 p95，包含成功的条件读取（304），不计 Context 内部的 Profile 耗时。直接读取另有结果和耗时面板，不进入检索分母。Handoff 显示 `continue_handoff` 的成功请求 p95，Prompt 显示 `get_prompt_configuration` 的成功请求 p95。这些观测均不证明宿主采用或配置生效。四类检索节点与 Profile 节点链接到各自的耗时趋势；Handoff、Prompt 的消费节点链接到相关 API 操作日志。Scope 日志中的 `artifact.read.completed` 按 Family 区分读取，并区分内容返回、304、失败和取消。

`POST /v1/stats` 支持可选的 `include_processing: true`，默认关闭。开启后，`by_scope` 的每一项提供处理绑定、持久化的请求／确认与输入／覆盖代数，以及单独标注时间的本地调度观察。`acknowledged` 不能证明已经产出。等待起点只是本地观察下界；下次检查时间不是开工承诺。API/background 拆分且没有本地主节点时，远端任务状态显示未观察；不会从沉默推断停机，也不恢复完整尝试历史。

处理节点单独显示最近失败原因，同时保留调度状态。表格补充处理建议、失败阶段、连续失败次数、最近失败时间与下次检查。`last_error_code`、`last_error_stage`、`last_error_at` 属于当前 Scope 和处理绑定的本地重试周期。代码只表示权限不足、授权服务不可用、模型配置错误、超时、模型输出无效或 Worker 失败等有限类别，不包含异常正文。阶段标识观察边界，不定位内部业务操作；时间为调度器观察失败时的 UTC 时间。再次排队或执行时保留上次失败，确认完成后清除；重启或重试缓存淘汰也会丢失观察。旧 Server 缺少原因字段时显示未接入，不推测原因。处理建议不会自动授权或改变重试行为。

检索指标覆盖已派发的 Memory、Atomic Memory、Topic Memory、通用 Artifact 搜索、上下文准备和带查询的 Skill library 请求。空结果属于执行成功；没有查询的 library 浏览不计入。共享服务指标不按当前 Scope 分摊；Scope 明细由不含正文的日志提供。相关性和采用率没有标注或宿主回执时保持未知。Experience 后续结果是累计观察，不代表因果收益；Skill 使用反馈请求次数不是去重后的调用次数。

已有部署需重启加载新 Server 代码，复制 `pc-flow.json` 和更新导航的看板，更新统计代理脚本，再将 `retrieval_family`、`retrieval_outcome`、`result_count`、`read_outcome` 合并入 Collector 白名单，仅重启受影响的监控组件。不会自动覆盖已部署配置；替换前备份人工编辑的文件。本功能不启用自动遗忘或删除策略。

失败执行链接固定已有 worker 身份和失败前后 5 分钟，分别打开对应日志和 trace；不使用新一轮 running worker 替换历史失败身份。无标识、未采样或过保留期时证据可缺失。处理后核对调度确认代数、产物库存与新事件，不能只看错误文字是否消失。告警规则按 job、instance、role 保留身份，与总览的服务角色筛选一致。
