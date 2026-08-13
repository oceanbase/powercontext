---
title: 排查问题
description: 诊断 PowerContext 安装、Server、数据库和 Codex 插件问题。
---

# 排查问题

先执行：

```bash
powercontext doctor
```

该命令检查安装包、Server liveness 和 Server readiness；只有所有检查均为 `ok` 时才以状态码 0 退出。
`degraded` 表示仍可使用，但不算完整诊断成功。自动化场景可添加 `--json`，顶层结果和每个检查都会包含
`ok` 与 `status`。可单独检查可选的 Codex 集成：

```bash
powercontext doctor codex
```

## 安装时无法读取 Git 地址

确认 Git 能够读取仓库：

```bash
git ls-remote https://github.com/oceanbase/powercontext.git HEAD
```

如果失败，请配置 Git 使用的 credential helper 或 SSH key，再重新运行 `uv tool install`。`uv` 使用 Git
凭据配置；PowerContext 不接收或保存仓库凭据。

## 找不到 `powercontext` 或 `codex`

执行：

```bash
uv tool dir --bin
command -v powercontext
command -v codex
```

必要时把 uv tool bin 目录加入 `PATH`。Codex CLI 不可用时，`powercontext setup codex` 会报告错误，不会继续
安装插件。

## 插件缺失或版本不一致

先在不涉及 Server 的情况下确认集成故障：

```bash
powercontext doctor codex
```

使用与工具一致的 ref 重新安装：

```bash
powercontext setup codex --source oceanbase/powercontext --ref <ref>
codex plugin list --json
```

然后开启新的 Codex 会话。如果提示词恢复和采集没有运行，请检查 `/hooks`。

## Server 检查失败

启动服务：

```bash
powercontext server run
```

如果 8000 端口已被占用，请停止冲突进程。若 Server 有意使用其他地址，可在检查时传入 base URL：

```bash
powercontext doctor --server-url http://127.0.0.1:9000
powercontext --server-url http://127.0.0.1:9000 ready
```

随附的 Codex 插件默认使用 8000 端口。liveness 失败表示进程无法响应健康请求，此时不会继续检查
readiness。HTTP 503 的 `not_ready` 表示 Runtime 或数据库无法接受工作；HTTP 200 的 `degraded` 表示已配置的
推理能力异常，但数据库操作仍然可用。Human 与 JSON 输出都会保留 Server 返回的各项检查状态。

## Server 无法打开数据库

数据库在 Server 启动时创建，而不是在工具安装时创建。先检查 Server 的启动错误，再运行
`powercontext doctor`。

如需指定位置：

```bash
export POWERCONTEXT_HOME=/path/with/write/access
powercontext server run
```

每次启动或诊断该实例时都应使用同一个环境变量。对于文件型 SQLite 数据库，PowerContext 会创建缺失的父
目录。

## 推理服务 readiness 检查失败

配置 generation 或 embedding 后，Server readiness 会向 provider 发起一次最小化真实请求。这样可以发现只有
实际请求时才能确认的凭据或 endpoint 问题，包括 base URL 遗漏 provider API 前缀。稳定状态包括 `ready`、
`unavailable`、`timeout` 和 `misconfigured`；响应不会包含凭据、provider 响应正文或已配置 URL。

推理检查失败时，overall readiness 为 HTTP 200 的 `degraded`，不会使整个 Server 退出流量。`ready` 和
`misconfigured` 会缓存 300 秒；临时的 `timeout` 和 `unavailable` 会在 30 秒后重试。并发健康请求共用同一次
刷新。修改静态配置后如需立即检查，请重启 Server；否则等待缓存过期。

## Memory 可以显式写入，但采集的提示词没有生成 Memory

显式 Memory 操作不需要模型；把采集的 Source 证据转换为 Memory 则需要。请配置 generation model 及其
provider 凭据，然后启用 scheduler 或显式 flush 对应 scope。查看 Server 当前提供的能力：

```bash
powercontext capabilities
```

`Memory extraction: disabled` 表示 Server 没有 generation model。

## Server 停止后 Codex 仍继续工作

这是预期行为。Prompt Hook 会正常降级，Memory 故障不能阻塞普通 Codex 工作。重启 Server 后即可恢复
检索和采集，现有数据库会被自动重新打开。

## Codex 没有注入召回上下文

查看 Hook 在 stderr 输出的单行 JSON 事件。`empty` 表示 Runtime 没有为本轮准备上下文。`version_mismatch`
表示已安装插件要求 `POST /v1/context/prepare`，但 Server 尚未提供该接口；请从同一个 ref 重新安装插件和工具
并重启 Server。`server_unavailable` 和 `invalid_response` 分别表示传输与 contract 问题。诊断事件会刻意
省略 query 与准备好的上下文正文。

执行 `powercontext capabilities`，确认 Context versions 中包含
`powercontext.prepared-context.v1`。
