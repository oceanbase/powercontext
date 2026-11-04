---
status: community
title: Hermes
description: 安装 Hermes MemoryProvider 和独立 slash-command 插件，并连接到 PowerContext Server。
---

# Hermes

`community`

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
powercontext setup hermes
powercontext doctor hermes
```

Setup 命令会把 provider 复制到 `$HERMES_HOME/plugins/powercontext`，安装
`$HERMES_HOME/plugins/powercontext-command`。除非用户已显式禁用，否则会启用 slash-command 插件，且不授予内置工具覆盖权限。
未配置 `memory.provider` 时，setup 自动选择 PowerContext；已有 provider 选择（包括显式空值）会保留。
Setup 不会启动 Server。

需要从现有 provider 切换到 PowerContext，或调整 provider 配置时，运行 Hermes Memory 设置向导并选择 `PowerContext`：

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
| `POWERCONTEXT_HERMES_ALLOW_INSECURE_HTTP` | 显式允许非环回明文 HTTP；默认为 `false` |
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

应由 Hermes 向导把 authorization 保存到受保护的 `.env` secret store，不要把 token 写入 `config.json`。环回地址默认
允许明文 HTTP；远程连接推荐 HTTPS，也可显式设置 `POWERCONTEXT_HERMES_ALLOW_INSECURE_HTTP=true`，
该选项不会关闭 HTTPS 证书校验。引导安装和绑定地址的持久化同意见[连接远程 Server](../operate/connect-remote-server.md)。
Evaluation trace 包含 prompt 和召回上下文，应保留在本机并按敏感数据保护。

## 数据流与捕获控制

这些控制作用于已激活的 PowerContext provider，环境变量优先于 provider 配置。
关闭 turn 捕获不会关闭召回、压缩前捕获或显式写入。

| 事件/入口 | 发送的数据 | 直接接收方 | 默认/触发条件 | 关闭方式 | 关闭后影响 |
| --- | --- | --- | --- | --- | --- |
| 召回 / prefetch | 截至 8192 字符且不做凭据脱敏的 query、Scope 和准备选项；Prepared Context 返回 Hermes | PowerContext Server | provider 已激活、query 非空且 Scope 已解析；可能复用预取缓存 | 切换 memory provider 可停止该 provider 的自动召回；此处没有独立召回开关 | PowerContext 自动召回停止；仅关闭捕获不影响召回 |
| 完成 turn | 有长度上限且不做 secret 脱敏的 user/assistant 文本对，以及 session 元数据；不是完整工具 transcript | PowerContext Server，保存为 Source | `capture_turns=true`；非主 Agent 上下文抑制自动写入 | 输入敏感文本前设置 `POWERCONTEXT_HERMES_CAPTURE_TURNS=false` | 停止该 Source 捕获；召回、压缩前捕获和显式写入仍独立工作 |
| 会话结束 | 针对已接受输入的 flush 请求，不会重新上传会话 transcript | PowerContext Server | `flush_on_session_end=true`；等待后台写入并检查提取能力 | `POWERCONTEXT_HERMES_FLUSH_ON_SESSION_END=false` | 停止此 flush 触发；已接受的 Source 保留，其他处理触发不受影响 |
| 压缩前 | 新 user/assistant 证据窗口，排除 system/tool 消息并脱敏检测到的 secret | PowerContext Server，保存为 Source | `capture_pre_compress=false`；用 `POWERCONTEXT_HERMES_CAPTURE_PRE_COMPRESS=true` 显式开启 | `POWERCONTEXT_HERMES_CAPTURE_PRE_COMPRESS=false` | 停止此捕获及其后续提取尝试；必需 checkpoint 会失败而非报告成功 |
| 显式 remember / 内置 Memory mirror | 请求的 Memory 内容或变更，更新携带精确引用 | PowerContext Server | 显式操作，或主上下文中 add/replace/remove 的 `on_memory_write` | 不调用该写入或产生 mirror 的内置操作；没有专用 mirror 开关 | 关闭 turn 捕获不会禁用这些写入 |
| Evaluation trace | prompt/query、召回上下文及关联元数据；检测到的 secret 会脱敏 | 本地 JSONL 文件，默认位于 `$HERMES_HOME/powercontext/evaluation-trace/` | `evaluation_trace=false`；用 `POWERCONTEXT_HERMES_EVALUATION_TRACE=true` 显式开启 | `POWERCONTEXT_HERMES_EVALUATION_TRACE=false` | 停止新增 trace，不禁用召回或捕获；已有文件仍是敏感数据 |

召回请求与完成 turn 的 Source 不做凭据脱敏。Evaluation trace 的脱敏不会处理已经发送到 Server 的 query；
压缩前过滤只作用于那条独立的捕获路径。

压缩前捕获优先使用宿主提供的规范化 `evidence_messages`，避免把先前压缩摘要再次作为新证据。
provider 声明 checkpoint API v2，必需 checkpoint 未提交时会抛出异常。这描述的是 provider 契约：仓库的
[集成能力矩阵](capabilities.md) 仍将 Hermes pre-compaction 声明为 `unsupported`，并未确立经过真实宿主验证的
required-capable 生命周期。checkpoint 时 Source 已持久化与提取成功是不同结果；Prepared Context、checkpoint
已提交、宿主注入、模型使用和答案正确也不能等同。Secret 匹配只是一组有限规则，不保证清除所有敏感信息。

数据有三个独立方向：Hermes 向 Server 发送 query、捕获内容和显式操作；Server 可能向配置的 embedding、reranking
或 generation 后端发送 query、候选或证据；Hermes 可能把返回的历史上下文放进自己的模型请求。
环回 Server URL 只描述第一段连接，不能推出后续没有网络外发。使用第三方插件代码本身也不等于第三方网络服务接收数据。
部署和支持载荷的边界见 [Server 配置](../operate/configuration.md)、
[推理隐私保护](../operate/inference-protection.md) 和[上下文复用与最终准入](../operate/context-protection.md)。
推理保护默认 `disabled`；`shadow` 和 `advisory` 只观察请求，不阻止交付，也不检查响应。
安装该 provider 不代表所有 Hermes 模型流量都具备 enforcing 保护。

改变这些开关不会删除已有 Source、Memory、revision、trace 或模型供应商记录。
关闭 generation 不会关闭 Source 捕获；Source 已接受也不代表 Memory 已生成。

## 只在需要时启用自动提取

完成轮次采集只创建 Source 证据，不会自行创建 Memory。自动 Source-to-Memory 提取需要在 Server 上配置 generation
model：

```bash
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL=provider:model-name
powercontext server run
powercontext capabilities
```

能力输出必须显示 Memory extraction 已启用。显式执行 `hermes powercontext remember` 不需要模型。
