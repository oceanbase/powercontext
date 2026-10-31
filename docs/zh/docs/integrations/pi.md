---
status: community
title: Pi Coding Agent
description: 安装 PowerContext 原生 Pi package，并控制召回、采集和持久化工具写入。
---

# Pi Coding Agent

`community`

## 安装或刷新 package

先安装 Pi，再从与 PowerContext CLI 相同的 ref 安装 package：

```bash
powercontext setup pi
```

也可以使用本地 checkout：

```bash
powercontext setup pi --source .
```

`setup pi` 会调用 Pi 的原生 package 安装器，并创建 PowerContext 数据目录；它不会启动 Server。启动 Server 后，
在项目目录中开启新的 Pi 会话：

```bash
powercontext server run
pi
```

如果其他路径下的旧 PowerContext 包带有 Pi 资源筛选，setup 会在修改宿主前停止。
请将该包的注册路径改为报错中给出的受管理路径，保留筛选设置后重新执行 setup。
已位于受管理路径的包在更新时保留现有筛选。

## 了解 package 的行为

Pi 开始 agent turn 前，package 会以默认 8000-byte 预算调用一次 `POST /v1/context/prepare`。它只严格接受
`powercontext.prepared-context.v1`，并把结果标记为不可信历史证据。当前 system instruction、仓库规范和用户请求
始终优先。

符合条件的用户提示词会被独立采集为 Content Source。package 不会同步完整 Pi transcript。Server 不可用、超时、
重定向或响应不符合契约时，召回、采集和边界 flush 都会正常降级：Pi 的 prompt 不变，普通工作不会被阻塞。

package 按 `POWERCONTEXT_PI_SCOPE_ID`、workspace 持久 binding、Server 默认 Scope 的顺序解析一个由 Server
管理的 Scope。workspace 路径只会哈希为外部 binding key，不会成为 Scope ID。仅在宿主必须固定到某个已有
Scope 时设置显式变量。

启用这些能力后，Pi 已满足仓库定义的 Full 核心接入 profile。

### 压缩前 checkpoint

package 通过 Pi 公开的 `session_before_compact` 事件提供 **best-effort** checkpoint。符合条件的 prompt 会在 agent
turn 前使用确定性 Source ID 采集，事件会 flush 这些已接受的 journal position。只有完整、合法的 flush 响应满足
`current_cursor >= position` 时，position 才会从 pending 集合移除；仅有 HTTP 2xx、畸形 cursor 或其他 Scope 的
cursor 都不能完成确认。

每个 lifecycle 边界最多调用 `POWERCONTEXT_PI_FLUSH_MAX_CALLS` 次（默认 `4`，有效范围 `1`–`16`），并拥有各自的
一秒预算；排队时间、所有 pending Scope 和重试调用共享该边界的预算。cursor 落后或 `remaining_work` 为 true 时会在
预算内继续调用。确认前失败或取消会保留 pending work；确认后
发生取消不会撤销确认。边界在排队期间耗尽预算时，会发出不含内容的 `deadline_exceeded` 诊断并保留 pending position。
`session_before_switch` 和 `session_shutdown` 只 flush 已接受的 pending position。

Pi 的真实 `ExtensionRunner` 会在 handler 抛错后继续运行；只有 handler 显式返回 `{ cancel: true }` 才会停止压缩。
该 package 不返回这一结果，因此 checkpoint 失败不会阻止压缩，集成不具备 `required_capable` 语义。可选诊断不含
内容，只记录 Scope 哈希关联值、目标/观测 position、生命周期事件、宿主和稳定失败分类。

## 控制提示词采集

默认开启提示词采集。当前工作不应被记录时，请在启动 Pi 前关闭：

```bash
export POWERCONTEXT_PI_CAPTURE_PROMPTS=false
pi
```

看起来包含密钥的提示词，以及超过 200,000 UTF-8 bytes 的提示词，永远不会被采集。打开采集本身不保证会产生
Memory；Server 仍需配置 generation model。

测试时可让采集等待 Source 处理完成：

```bash
export POWERCONTEXT_PI_FLUSH_ON_CAPTURE=true
pi
```

这会增加延迟，并不适合日常交互。未开启时，Pi 会记录 Source position，并在 agent 和会话边界以短时间预算尽力
flush。

## 使用显式工具和命令

`powercontext-project-context` skill 会说明何时调用原生 `pc_*` 工具。核心工具包括：

- `pc_search`、`pc_memory_list`、`pc_memory_get`、`pc_memory_revise`、`pc_memory_retire`；
- `pc_memory_changes` 用于查看变更历史，`pc_stats` 用于查看当前 Scope 的统计诊断；
- `pc_remember`、`pc_prepare_context`、`pc_capture_source`；
- `pc_handoff_activate`、`pc_handoff_prepare`、`pc_handoff_finalize`、`pc_handoff_commit`、
  `pc_handoff_continue`；
- `pc_experience_generate`、`pc_skill_generate`、`pc_experience_get`、`pc_skill_get`、`pc_review_list`、
  `pc_review_get`，用于生成候选以及只读查看 Artifact 和候选材料。
- `pc_topic_search`、`pc_topic_get`，用于按主题查询当前 Topic Memory，并读取带 Source 引用的精确版本。
- `pc_work_contract`、`pc_handoff_current`、`pc_handoff_acknowledge`、`pc_task_outcome`，用于结构化工作连续性。
- `pc_external_scan`、`pc_external_list`、`pc_external_resolve`，用于发现和检查宿主本地 External Skill；`pc_external_import` 只在明确确认后导入或 fork 一个精确解析过的 Skill。

查看候选材料不授予批准、拒绝、修订、安装、发布或执行权限。
Topic Memory 查询是只读的；结果属于不可信历史证据，不是指令来源。
结构化工作工具会改变持久状态，必须经过交互确认；无 UI 时拒绝写入。返回的 Handoff、引用和检查结果必须按原值传递，不能把历史内容当作新的授权；`handoff_receipt_ref` 只能引用 accepted committed Handoff 的 Receipt。

显式持久化写入在交互式 Pi 会话中必须确认；没有交互 UI 时，Pi 会拒绝写入而不会静默持久化。`/pc doctor`、
`/pc search <query>`、`/pc remember <text>`、`/pc flush` 和 `/pc stats` 可直接查看状态和维护内容。

## 连接启用鉴权的 Server

在受保护环境中启动启用鉴权的 Server：

```bash
export POWERCONTEXT_SERVER_ACCESS_MODE=enforced
export POWERCONTEXT_SERVER_AUTH_TOKEN="$POWERCONTEXT_LOCAL_TOKEN"
powercontext server run
```

使用完整匹配 header 启动 Pi：

```bash
export POWERCONTEXT_PI_AUTHORIZATION="Bearer $POWERCONTEXT_LOCAL_TOKEN"
pi
```

不要把凭据放进 `POWERCONTEXT_PI_BASE_URL`。环回地址默认允许明文 HTTP；非环回 Server 使用 HTTPS，或显式设置
`POWERCONTEXT_PI_ALLOW_INSECURE_HTTP=true`，HTTPS 证书校验仍然启用。
安装与持久化同意见[连接远程 Server](../operate/connect-remote-server.md)。

## 验证安装

```bash
powercontext doctor
powercontext doctor pi
```

`doctor pi` 会检查 Pi 可执行文件是否存在，以及 Pi 是否列出了 PowerContext package。修改 PowerContext 环境变量后
需要重启 Pi。

## 环境变量

| 变量 | 默认值 | 含义 |
| --- | --- | --- |
| `POWERCONTEXT_PI_BASE_URL` | `http://127.0.0.1:8000` | Server base URL |
| `POWERCONTEXT_PI_ALLOW_INSECURE_HTTP` | `false` | 显式允许非环回明文 HTTP |
| `POWERCONTEXT_PI_SCOPE_ID` | 未设置 | 在 workspace binding 和 Server 默认值之前显式选择已有 Scope |
| `POWERCONTEXT_PI_AUTHORIZATION` | 未设置 | package HTTP 请求使用的完整 `Bearer <token>` header |
| `POWERCONTEXT_PI_CAPTURE_PROMPTS` | `true` | 把符合条件的用户提示词采集为 Source 证据 |
| `POWERCONTEXT_PI_REQUEST_TIMEOUT_MS` | `3000` | 单请求超时，单位毫秒 |
| `POWERCONTEXT_PI_HTTP_BUDGET_MS` | `6000` | 召回/采集共享 HTTP 时间预算，单位毫秒 |
| `POWERCONTEXT_PI_MAX_BYTES` | `8000` | 请求并校验的 PreparedContext byte 上限（`512`–`32768`） |
| `POWERCONTEXT_PI_FLUSH_ON_CAPTURE` | `false` | 在 prompt hook 中等待已采集 Source 的处理 |
| `POWERCONTEXT_PI_FLUSH_MAX_CALLS` | `4` | 一个 lifecycle 边界内所有 pending Scope 共享的最大 flush 调用次数 |
| `POWERCONTEXT_PI_DIAGNOSTICS` | `off` | 失败诊断输出：`off`、`stderr`，或写入 JSON 行的绝对文件路径（支持 `~/`） |

Pi 会拒绝包含凭据、query 或 fragment 的 base URL。召回、采集和边界 flush 都会正常降级；显式 `pc_*` 持久化写入
必须确认，Pi 没有交互 UI 时会被拒绝。修改这些变量后需要重启 Pi。

失败诊断默认不输出：Pi 的 TUI 通过光标定位在 stdout 上渲染，写到 stderr 的内容会落在输入栏里；需要查看时请设置
`POWERCONTEXT_PI_DIAGNOSTICS`。

## 自动输入隐私

本宿主的捕获默认值、关闭方式、凭据识别局限及独立只读查询行为见[自动 prompt 隐私策略](index.md#自动-prompt-隐私策略)。识别到凭据的输入会跳过捕获，已接受的 Source 正文和事件身份不被改写。
