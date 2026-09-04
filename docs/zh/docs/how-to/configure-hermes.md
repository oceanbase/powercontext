---
title: 配置 Hermes
description: 安装 Hermes MemoryProvider 和独立 slash-command 插件，并连接到 PowerContext Server。
---

# 配置 Hermes

该集成包含标准 Hermes `MemoryProvider` 和独立 slash-command 插件。Hermes 继续负责对话和 Memory 生命周期，
provider 把召回、采集和显式操作发送给单独运行的 PowerContext Server；该插件会在 provider 激活前注册
`/pc` 和 `/powercontext`。后端故障不会中断 Hermes 对话。

## 前置条件

- `PATH` 中存在 Hermes Agent 0.20.4 或更新版本；
- 从 `master` 安装 PowerContext CLI 和 Server；
- PowerContext Server 正在运行。

```bash
uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@master"
powercontext server run
```

## 安装两个插件

在另一个终端从同一 revision 安装或刷新两个插件：

```bash
powercontext setup hermes --source oceanbase/powercontext --ref master
powercontext doctor hermes
```

Setup 命令会把 provider 复制到 `$HERMES_HOME/plugins/powercontext`，安装
`$HERMES_HOME/plugins/powercontext-command`，并在不授予内置工具覆盖权限的情况下启用 slash-command 插件。它不会启动
Server，也不会在 Hermes 中选择 provider。

运行 Hermes Memory 设置向导，并选择 `PowerContext`：

```bash
hermes memory setup
```

Hermes 0.20.4 应使用上面的通用命令。`hermes memory setup powercontext` 只选择 provider，不会打开配置向导。
设置完成后重启 Hermes。

## 验证召回和写入

```bash
hermes powercontext status
hermes powercontext remember preference "The user prefers uv"
hermes powercontext search "Python package manager"
```

在交互式 Hermes 会话中，`/pc status` 应连接到同一个 active provider。输入 `/pc ` 后按 Tab/Down，可查看
Memory、Handoff、Experience、Skill、审核、统计、trace 和 Scope 命令。Hermes 0.20.4 没有为 gateway
slash command 提供足够的调用上下文，因此该插件会拒绝 gateway 调用；gateway 会话应使用 provider 提供的
Hermes tools。

provider 默认连接 `http://127.0.0.1:8000`。Server 依次解析显式 Scope、持久 session binding、持久 workspace
binding 和默认 Scope。Hermes 只把 workspace 路径哈希用作外部 binding key，不会根据 profile、user、repository
或目录生成 Scope ID。

## 配置连接

向导把非敏感设置写入 `$HERMES_HOME/powercontext/config.json`。环境变量会覆盖文件：

| 变量 | 用途 |
| --- | --- |
| `POWERCONTEXT_HERMES_CONFIG` | 配置文件路径；默认为 `$HERMES_HOME/powercontext/config.json` |
| `POWERCONTEXT_HERMES_BASE_URL` | PowerContext Server URL |
| `POWERCONTEXT_HERMES_AUTHORIZATION` | 完整 authorization header，例如 `Bearer <token>` |
| `POWERCONTEXT_HERMES_TOKEN` | 未设置 `AUTHORIZATION` 时使用的裸 token 简写 |
| `POWERCONTEXT_HERMES_SCOPE_ID` | 显式的服务端 Scope ID |
| `POWERCONTEXT_HERMES_MAX_BYTES` | Prepared Context 上限，范围为 512 到 32768 字节 |
| `POWERCONTEXT_HERMES_TIMEOUT` | HTTP 请求超时秒数 |
| `POWERCONTEXT_HERMES_CAPTURE_TURNS` | 是否把完成的 turn 采集为 Source |
| `POWERCONTEXT_HERMES_FLUSH_ON_SESSION_END` | 是否在会话结束时执行 Memory extraction |
| `POWERCONTEXT_HERMES_CAPTURE_PRE_COMPRESS` | compression 前采集过滤后的新 turn；默认关闭 |
| `POWERCONTEXT_HERMES_EVALUATION_TRACE` | 把召回上下文记录到敏感的本地 JSONL trace；默认关闭 |
| `POWERCONTEXT_HERMES_EVALUATION_TRACE_PATH` | 覆盖 evaluation trace 目录 |

应由 Hermes 向导把 authorization 保存到受保护的 `.env` secret store，不要把 token 写入 `config.json`。明文 HTTP
只用于 loopback Server；连接远程部署前请阅读[部署 Server](deploy-server.md)。Evaluation trace 包含 prompt 和
召回上下文，应保留在本机并按敏感数据保护。

## 只在需要时启用自动提取

完成轮次采集只创建 Source 证据，不会自行创建 Memory。自动 Source-to-Memory 提取需要在 Server 上配置 generation
model：

```bash
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL=provider:model-name
powercontext server run
powercontext capabilities
```

能力输出必须显示 Memory extraction 已启用。显式执行 `hermes powercontext remember` 不需要模型。
