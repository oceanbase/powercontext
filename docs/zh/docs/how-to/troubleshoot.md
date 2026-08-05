---
title: 排查问题
description: 诊断 PowerContext 安装、Server、数据库和 Codex 插件问题。
---

# 排查问题

先执行：

```bash
powercontext doctor
```

任一检查失败时，命令会以状态码 1 退出。自动化场景可添加 `--json`。

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

随附的 Codex 插件默认使用 8000 端口。

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
