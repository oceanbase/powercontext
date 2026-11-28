---
title: 让 Agent 帮你安装
description: 把一段话发给 Coding Agent，由它检测环境、询问几个问题，并按回答安装和验收 PowerContext。
---

# 让 Agent 帮你安装

把下面这段话发给你正在使用的 Coding Agent，例如 Codex、OpenCode 或 Claude Code：

```text
按照 https://powercontext.oceanbase.io/zh/llms.mdx/docs/get-started/agent-install/content.md 在这台机器上安装 PowerContext。需要我决定的事先问我，不要猜；不要读取或显示我的任何密钥。
```

Agent 会先检测环境，再问你几个问题，然后安装、配置并带你完成验收。以下内容写给执行安装的 Agent。

## 执行规则

1. **先检测，再提问。**能从系统中检测到的事实不要问用户；只问[第 2 步](#2-询问用户)列出的问题，可以一次问完。
2. **不接触密钥。**不读取、不打印、不复制密钥文件的内容，例如 `~/.codex/auth.json` 和 `.env` 中的 API key；只检查文件是否存在。需要密钥时，告诉用户写入哪个文件的哪一行，由用户自己填写。
3. **改动前先确认。**修改用户已有的配置文件前先备份，并说明要改什么；注册后台服务前征得同意。
4. **逐步检查，失败就停。**每一步都按"检查"核对结果。失败时停下，向用户报告执行的命令和输出，不要换一种方式绕过。
5. **以验收为准。**服务启动或 `doctor` 通过只说明安装和连接正常，不代表记忆已经生效。完成[第 4 步](#4-验收)前，不要告诉用户安装完成。

## 1. 检测环境

依次执行，记录结果，不需要询问用户：

```bash
uname -s; uname -m
getconf GNU_LIBC_VERSION 2>/dev/null || sw_vers -productVersion 2>/dev/null
for agent in codex opencode claude zcode kimi pi openclaw hermes dsh; do command -v "$agent" >/dev/null && echo "agent: $agent"; done
test -f "$HOME/.codex/auth.json" && echo "codex login: present"
command -v powercontext >/dev/null && powercontext --version
```

据此判断：

- **平台能否运行网关**：Linux x86_64 或 arm64 且 glibc 2.28 以上，或 macOS 15 以上。Windows 原生环境、musl 系统（如 Alpine）和其他架构只能走[插件路径](#3b-插件路径)。在 Windows 上运行时，告诉用户网关需要在 WSL2 中安装。
- **已安装的 Agent**：只把检测到的 Agent 作为第 2 步的选项。
- **Codex 订阅**：`~/.codex/auth.json` 存在表示 Codex CLI 已登录。只检查是否存在，不要读取。
- **已有安装**：已经装有 PowerContext 时，先告诉用户版本，询问是升级还是保留，再继续。用户的 Agent 已经装有 PowerContext 插件时，记下来，第 3a 步要处理。

## 2. 询问用户

一次问完以下问题，每个问题说明默认选项：

1. **只在这台电脑上用，还是部署给团队用？**默认"只在这台电脑上用"。选择部署给团队时，转到[第 3c 步](#3c-团队部署)，不再询问后面的问题。
2. **接入哪个 Agent？**列出第 1 步检测到的 Agent，按下表确定路径：

   | 条件 | 路径 |
   | --- | --- |
   | Agent 是 Codex 或 OpenCode，平台能运行网关，Codex CLI 已登录 | 推荐[网关路径](#3a-网关路径) |
   | 其他 Agent，或不满足上一行的任一条件 | [插件路径](#3b-插件路径) |

   网关路径只适用于能使用 OpenAI Responses 接口、并在请求中发送会话标识的 Agent，目前是 Codex 和 OpenCode。其他 Agent 经过网关时，上下文注入或对话采集不完整，所以使用插件。

   推荐网关路径时，向用户说明取舍，由用户决定：
   - **好处**：网关在模型请求中加入节省成本的指令，完整采集每一轮对话，并在 Agent 已缓存的前缀之后生成记忆，减少重复支付的输入 token。生成记忆使用 Codex 订阅，不需要另外的生成模型 API。
   - **代价**：网关是一个常驻进程；所有项目的对话都进入同一个 Scope；不提供插件的 MCP 工具，也就是不能让 Agent 显式保存记忆或交接工作；用订阅生成时不支持 Topic Memory 和 Dream，生成请求计入订阅额度。
3. **记忆用什么模型生成？**网关路径使用 Codex 订阅，不需要问。插件路径下，用户可以配置模型 API（需要协议、地址、模型名和 API key），或者选择"基础记忆"：不自动生成记忆，只能显式保存和检索。
4. **用哪个嵌入模型？**订阅不提供嵌入模型，需要单独的 API。
   - 有 OpenAI 兼容的嵌入 API 时，询问模型名和输出维度，例如 OpenAI 的 `text-embedding-3-small`，维度 1536。
   - 用户不想配置时，明确告诉后果：只剩全文检索，较长的提问常常召回不到记忆。用户确认后再继续。

## 3a. 网关路径

以下步骤中，`<HOME>` 指用户主目录的绝对路径，`<model>` 是 Codex 订阅可用的模型名。读取 `~/.codex/config.toml` 顶层的 `model` 作为 `<model>`；文件或该项不存在时询问用户。

### 安装

```bash
curl -fsSL https://powercontext.oceanbase.io/install.sh | bash -s -- --gateway
```

执行安装器最后打印的 `export PATH=...` 命令。

**检查**：`powercontext --version` 和 `pc-gateway --version` 报告相同的版本。

### 已经装有插件时

网关路径由网关负责每一轮的上下文和采集，插件不能同时这样做，否则请求会被拒绝（HTTP 503 `gate_paused`）。不需要卸载插件，只关闭它的提示词召回和采集，下文的启动脚本会设置这两项。先向用户说明：

- 经过网关后，插件的 MCP 工具仍然可用，但写入插件绑定的 Scope，而不是网关使用的 Scope。
- Codex：以前的会话不能经过网关继续，请开新会话；直接运行 `codex` 打开的旧会话不受影响。
- OpenCode：以前的会话通常可以继续；模型在某个会话中调用过 `pc_prepare_context` 时，那个会话需要新开。

### 配置并启动 Server

```bash
mkdir -p "$HOME/powercontext"
```

在 `<HOME>/powercontext/.env` 写入以下内容，替换尖括号中的值，然后执行 `chmod 600 "$HOME/powercontext/.env"`：

```dotenv
POWERCONTEXT_SERVER_HTTP_PORT=17429
POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL=openai-codex:<model>
POWERCONTEXT_SERVER_INFERENCE_PROTECTION='{"mode":"disabled","targets":{"approved-codex":{"endpoint":"https://chatgpt.com/backend-api/codex/responses","model":"<model>","protocol":"openai-responses","allow_streaming":true}}}'
POWERCONTEXT_SERVER_INFERENCE_PREFIX_EXTRACTION_GATEWAY_URL=http://127.0.0.1:8787
POWERCONTEXT_SERVER_RUNTIME_MEMORY_SCHEDULE_SECONDS=60
POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_MODEL=openai:<embedding-model>
POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_PROFILE_ID=<embedding-model>-<dimension>
POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_DIMENSION=<dimension>
OPENAI_API_KEY=
```

- 用户不配置嵌入模型时，删除最后四行。嵌入 API 不是 OpenAI 时，按[模型配置](configure-models.md)改写嵌入的几行。
- `OPENAI_API_KEY=` 这一行留空，请用户自己在编辑器中填写。用户确认填好后再继续。
- JSON 值必须用单引号包住，否则解析配置时会去掉其中的双引号。
- 推理保护必须保持 `disabled`，并保留 `approved-codex` 这个目标：Codex 订阅的请求不带输出上限，开启保护会暂缓这些请求。
- 即使是个人回环部署，Gateway 也要求 Core 开启认证。用下面的命令开启访问控制并生成 Server Token；不要显示 Token：

  ```bash
  printf 'POWERCONTEXT_SERVER_ACCESS_MODE=enforced\nPOWERCONTEXT_SERVER_AUTH_TOKEN=%s\n' "$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')" >> "$HOME/powercontext/.env"
  ```

验证配置并注册个人服务。服务在登录后自动启动：Linux 使用 systemd 用户服务，macOS 使用 LaunchAgent。

```bash
cd "$HOME/powercontext"
powercontext config validate --env-file .env
powercontext service install --env-file .env
powercontext service status
export POWERCONTEXT_CLIENT_SERVER_URL=http://127.0.0.1:17429
export POWERCONTEXT_CLIENT_API_TOKEN="$(sed -n 's/^POWERCONTEXT_SERVER_AUTH_TOKEN=//p' "$HOME/powercontext/.env")"
powercontext ready
powercontext scope create --title personal --summary "Personal coding context"
```

请在启动 CLI 和宿主插件的 shell 中保留 `POWERCONTEXT_CLIENT_API_TOKEN`；Core 认证同样适用于它们，Gateway Key 不能替代 Core 凭据。最后一条命令输出形如 `scp_... personal (version 1)`，开头的 `scp_...` 就是 Scope ID，后文记作 `<scope_id>`。

**检查**：`service status` 显示 `server liveness: live`；`ready` 中 `database`、`inference.generation`、`runtime` 均为 `ready`，配置了嵌入模型时 `inference.embedding` 也为 `ready`。

### 配置网关

在 `<HOME>/powercontext/gateway.env` 写入：

```dotenv
PC_GATEWAY_CORE_BASE=http://127.0.0.1:17429
PC_GATEWAY_DEFAULT_SCOPE=<scope_id>
PC_GATEWAY_INJECTION=on
PC_GATEWAY_SEDIMENT=on
PC_GATEWAY_SUBSCRIPTION=codex
PC_GATEWAY_SUBSCRIPTION_CREDENTIALS=<HOME>/.codex/auth.json
PC_GATEWAY_UPSTREAM_PROFILE_RESPONSES=approved-codex
```

网关通过 HTTP 连接 Server，必须设置 `PC_GATEWAY_INJECTION=on` 才会注入上下文。为它配置 Server Token，并生成不同的面向客户端 Key；不要显示这两个值：

```bash
printf 'PC_GATEWAY_CORE_TOKEN=%s\n' "$(sed -n 's/^POWERCONTEXT_SERVER_AUTH_TOKEN=//p' "$HOME/powercontext/.env")" >> "$HOME/powercontext/gateway.env"
printf 'PC_GATEWAY_API_KEY=%s\n' "$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')" >> "$HOME/powercontext/gateway.env"
chmod 600 "$HOME/powercontext/gateway.env"
```

本个人安装流程复用上面生成的 Server Token：`CORE_URL` 对应 `gateway.env` 中的 `PC_GATEWAY_CORE_BASE`；
`PC_GATEWAY_CORE_BASE` 和 `PC_GATEWAY_CORE_TOKEN` 均写入该文件，`ACCESS_ADMIN_TOKEN` 则是 `.env` 中的
`POWERCONTEXT_SERVER_AUTH_TOKEN`。该 Server Token 是引导阶段的
管理员凭据，并拥有静态 Scope 角色。启动 Gateway 前，请在 `<scope_id>` 上为它授予 `gateway.user`。若改用独立的
最小权限 Gateway 身份，则需授予 `gateway.user`、Server resource 上的 `server.observer`，并且由于此配置开启了采集，
还要在 Scope 上授予 `scope.contributor`。`/v1/access/me` 与绑定流程见[网关认证与目标要求](../operate/deploy-server.md#网关模型目标)。

创建启动脚本 `<HOME>/powercontext/run-gateway.sh`，并执行 `chmod 755`。它等待 Server 就绪后再启动网关：

```sh
#!/bin/sh
set -a
. "$HOME/powercontext/gateway.env"
set +a
until curl -sf http://127.0.0.1:17429/health/ready >/dev/null; do sleep 2; done
exec "<pc-gateway 的绝对路径>" >>"$HOME/powercontext/gateway.log" 2>&1
```

用 `command -v pc-gateway` 取得绝对路径。由于上文已开启前缀抽取，还要将 Gateway Key 加入 Server 回调请求头并重新加载配置：

```bash
printf "POWERCONTEXT_SERVER_INFERENCE_PREFIX_EXTRACTION_GATEWAY_HEADERS='{\"Authorization\":\"Bearer %s\"}'\n" "$(sed -n 's/^PC_GATEWAY_API_KEY=//p' "$HOME/powercontext/gateway.env")" >> "$HOME/powercontext/.env"
powercontext service install --env-file "$HOME/powercontext/.env"
```

### 让网关自动启动

`powercontext service` 只管理 Server，网关需要单独注册。征得用户同意后：

**Linux**：创建 `~/.config/systemd/user/powercontext-gateway.service`：

```ini
[Unit]
Description=PowerContext Gateway

[Service]
ExecStart=/bin/sh <HOME>/powercontext/run-gateway.sh
Restart=on-failure

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now powercontext-gateway.service
```

**macOS**：创建 `~/Library/LaunchAgents/io.oceanbase.powercontext.gateway.plist`：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>io.oceanbase.powercontext.gateway</string>
  <key>ProgramArguments</key>
  <array><string>/bin/sh</string><string><HOME>/powercontext/run-gateway.sh</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict>
</plist>
```

```bash
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/io.oceanbase.powercontext.gateway.plist
```

**检查**：`curl -s http://127.0.0.1:8787/healthz` 有响应。失败时查看 `<HOME>/powercontext/gateway.log`。

### 接入 Codex

在 `~/.codex/powercontext-gateway.config.toml` 写入以下内容。这是一个单独的配置文件，不影响用户原有的 Codex 配置：

```toml
model = "<model>"
model_provider = "powercontext-gateway"

[model_providers.powercontext-gateway]
name = "PowerContext gateway"
base_url = "http://127.0.0.1:8787/gateway/v1"
wire_api = "responses"
env_key = "PC_GATEWAY_KEY"
```

征得用户同意后，创建启动脚本 `~/.local/bin/codex-pc`，并执行 `chmod 755`：

```sh
#!/bin/sh
# Codex through the PowerContext gateway; the gateway owns prompt-time context.
export PC_GATEWAY_KEY=local
export POWERCONTEXT_CODEX_PROMPT_RECALL=false
export POWERCONTEXT_CODEX_CAPTURE_PROMPTS=false
exec codex --profile powercontext-gateway "$@"
```

网关只监听本机，`PC_GATEWAY_KEY` 可以是任意值，但必须设置。后两项只在装有插件时起作用，没有插件时也无害。

### 接入 OpenCode

在 `<HOME>/powercontext/opencode-gateway.json` 写入以下内容。OpenCode 必须使用 `@ai-sdk/openai`，它走 Responses 接口；`@ai-sdk/openai-compatible` 走 Chat 接口，流式响应不会被采集：

```json
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "powercontext-gateway": {
      "npm": "@ai-sdk/openai",
      "name": "PowerContext gateway",
      "options": { "baseURL": "http://127.0.0.1:8787/gateway/v1", "apiKey": "local" },
      "models": { "<model>": {} }
    }
  },
  "model": "powercontext-gateway/<model>"
}
```

征得用户同意后，创建启动脚本 `~/.local/bin/opencode-pc`，并执行 `chmod 755`：

```sh
#!/bin/sh
# OpenCode through the PowerContext gateway; the gateway owns prompt-time context.
export OPENCODE_CONFIG="$HOME/powercontext/opencode-gateway.json"
export POWERCONTEXT_OPENCODE_PROMPT_RECALL=false
export POWERCONTEXT_OPENCODE_CAPTURE_PROMPTS=false
exec opencode "$@"
```

## 3b. 插件路径

适用于其他 Agent，或者不使用网关的情况。按[快速开始](https://powercontext.oceanbase.io/zh/llms.mdx/docs/get-started/quickstart/content.md)的步骤执行，并注意：

- 安装命令为 `curl -fsSL https://powercontext.oceanbase.io/install.sh | bash`。
- **配置向导是交互界面，Agent 无法代为操作。**请用户在自己的终端运行 `powercontext config init --language zh --output .env`。在 Claude Code 和 Codex 中，用户可以输入 `! powercontext config init --language zh --output .env` 直接运行。告诉用户根据第 2 步的回答如何选择：存储选 SQLite，使用场景选"只在当前机器"，记忆能力按第 3 题选择"完整记忆能力"或"基础记忆"，Agent 选第 2 题的回答。
- 快速开始用 `powercontext service install` 注册个人服务，Server 会在登录后自动启动。
- 把快速开始里的 `codex` 换成用户选择的 Agent，宿主特有的步骤见[集成指南](../integrations/index.md)。

## 3c. 团队部署

适用于管理员为团队部署 PowerContext，包括先搭一个测试环境。本节在当前 Linux 主机上完成部署。在 Kubernetes 或 Docker 中部署需要自行构建镜像，请管理员阅读[部署 Server](../operate/deploy-server.md) 和[在 Kubernetes 上部署](../operate/kubernetes.md)，然后停止。

### 和管理员确认

一次问完以下问题，说明默认选项：

1. **成员怎么访问？**
   - **反向代理或 SSH 转发**（默认）：Server 只监听本机，由管理员已有的 Nginx、Caddy 等提供 HTTPS，或者成员用 SSH 端口转发。Server 可以注册为个人服务，自动启动。
   - **直接监听网络地址**：适合内网测试环境，成员通过 `http://<主机地址>:17429` 访问，没有 HTTPS。
2. **开启认证吗？**默认开启。
   - 开启后，Server 生成一个 Token，所有成员共用这一个身份，权限和可见范围相同。要区分用户，需要在部署中注入 Authentication Provider。
   - 不开启只适合可信网络中的测试环境。之后再开启时，未开启期间写入的数据没有归属，需要管理员按[迁移认证开启前写入的数据](../operate/deploy-server.md#迁移认证开启前写入的数据)补齐后才能修改。
3. **存储用什么？**默认 SQLite，数据在本机。已有 OceanBase 时提供连接 URL。
4. **生成模型和嵌入模型？**团队部署使用模型 API，不使用个人订阅。询问各自的服务商、Base URL（OpenAI 官方可省略）、模型名，以及嵌入模型的维度。
5. **使用哪些功能？**逐项说明下表，由管理员决定：

| 功能 | 默认 | 什么时候开启 | 前提与代价 |
| --- | --- | --- | --- |
| 网关 | 不部署 | 成员的 Agent 能使用 Responses 接口（如 Codex、OpenCode），希望自动采集对话、注入上下文、节省 token | 成员把模型地址指向网关。网关目前只有一个共享 Key 和一个默认 Scope，所有成员的对话进入同一个 Scope。部署网关时还要询问上游模型的 Responses 地址和模型名 |
| 上下文注入 | 部署网关时开启 | 希望网关在每一轮自动加入相关记忆 | 关闭后，Agent 插件的提示词召回仍然不能和网关同时使用：Core 会拒绝网关转发的请求中任何不是网关加入的上下文 |
| 对话采集 | 部署网关时开启 | 希望每一轮对话都成为 Source | Source 保存完整的对话内容，包括工具调用和输出 |
| 成本引导指令（Steering） | 开启 | 希望模型少做重复的读取和调用 | 只作用于 Responses 接口。可以换成团队自己的指令文件，不超过 4096 字节 |
| 前缀抽取 | 部署网关时开启 | 希望生成记忆时复用 Agent 已缓存的前缀，减少重复支付的输入 | 需要对话采集；网关和 Server 在同一台主机。抽取请求发往网关的上游账号并计费；不划算时自动回退到普通生成。见[复用缓存前缀抽取记忆](../operate/deploy-server.md#复用缓存前缀抽取记忆) |
| one-pass 记忆整理 | 开启前缀抽取时使用 | 希望提取、合并和复查在一次对话中完成 | 关闭时逐条整理。经过前缀时，参与比较的已有记忆也会发给网关的上游模型和账号 |
| 推理保护 | `disabled` | 需要在每次模型调用前后做隐私与合规检查 | `enforcing` 模式下，不带输出上限的请求会被暂缓。Codex 不发送输出上限，所以 Codex 经过网关的所有请求都会被暂缓。见[推理隐私保护](../operate/inference-protection.md) |

### 安装

部署网关时：

```bash
curl -fsSL https://powercontext.oceanbase.io/install.sh | bash -s -- --gateway
```

不部署网关时去掉 `-s -- --gateway`。执行安装器最后打印的 `export PATH=...` 命令。

### 写入 Server 配置

```bash
mkdir -p "$HOME/powercontext"
```

在 `<HOME>/powercontext/.env` 写入下面的基础配置，再按管理员的选择追加各组配置，然后执行 `chmod 600 "$HOME/powercontext/.env"`：

```dotenv
POWERCONTEXT_SERVER_HTTP_PORT=17429
POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL=openai:<generation-model>
POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_MODEL=openai:<embedding-model>
POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_PROFILE_ID=<embedding-model>-<dimension>
POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_DIMENSION=<dimension>
POWERCONTEXT_SERVER_RUNTIME_MEMORY_SCHEDULE_SECONDS=60
POWERCONTEXT_SERVER_RUNTIME_TOPIC_MEMORY_SCHEDULE_SECONDS=300
POWERCONTEXT_SERVER_RUNTIME_EXPERIENCE_SCHEDULE_SECONDS=900
OPENAI_API_KEY=
```

- 使用 OpenAI 兼容的其他服务时，增加 `POWERCONTEXT_SERVER_INFERENCE_GENERATION_BASE_URL` 和 `POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_BASE_URL`；协议不同的服务按[模型配置](configure-models.md)改写。
- `OPENAI_API_KEY=` 留空，请管理员自己填写，确认填好后再继续。

**直接监听网络地址时**追加：

```dotenv
POWERCONTEXT_SERVER_HTTP_HOST=0.0.0.0
```

不部署 Gateway 且不开启认证时，网络监听还要追加 `POWERCONTEXT_SERVER_ALLOW_UNAUTHENTICATED_NON_LOOPBACK=true`。

**部署 Gateway 时必须开启认证，即使 Core 只监听回环地址也一样。** 其他需要认证的场景也使用此配置：追加 `POWERCONTEXT_SERVER_ACCESS_MODE=enforced`，并用下面的命令生成 Token 直接写入文件。不要显示 Token，告诉管理员它保存在 `.env` 的 `POWERCONTEXT_SERVER_AUTH_TOKEN` 中：

```bash
printf 'POWERCONTEXT_SERVER_AUTH_TOKEN=%s\n' "$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')" >> "$HOME/powercontext/.env"
```

**部署网关时**追加以下配置。`<upstream-endpoint>` 是上游的 Responses 地址，例如 `https://api.openai.com/v1/responses`；`<upstream-model>` 是成员使用的模型。推理保护模式按管理员的选择填写，JSON 值必须用单引号包住：

```dotenv
POWERCONTEXT_SERVER_INFERENCE_PROTECTION='{"mode":"disabled","targets":{"team-upstream":{"endpoint":"<upstream-endpoint>","model":"<upstream-model>","protocol":"openai-responses","allow_streaming":true}}}'
```

开启前缀抽取时再追加：

```dotenv
POWERCONTEXT_SERVER_INFERENCE_PREFIX_EXTRACTION_GATEWAY_URL=http://127.0.0.1:8787
```

关闭 one-pass 时追加 `POWERCONTEXT_SERVER_RUNTIME_ATOMIC_MEMORY_PROCESSING_MODE=per_candidate`。

**检查**：`powercontext config validate --env-file "$HOME/powercontext/.env"` 通过。

### 启动 Server

**反向代理或 SSH 转发**：注册个人服务，登录后自动启动：

```bash
cd "$HOME/powercontext"
powercontext service install --env-file .env
powercontext service status
```

**直接监听网络地址**：个人服务只接受本机地址，所以要单独注册。征得管理员同意后，创建 `~/.config/systemd/user/powercontext-server.service`：

```ini
[Unit]
Description=PowerContext Server

[Service]
WorkingDirectory=<HOME>/powercontext
ExecStart=<powercontext 的绝对路径> server run --env-file <HOME>/powercontext/.env
Restart=on-failure

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now powercontext-server.service
loginctl enable-linger "$USER"
```

`enable-linger` 让服务在管理员退出登录后继续运行；命令失败时，请管理员用有权限的账号执行。

然后检查并创建团队使用的 Scope。部署 Gateway 或启用 Core 认证时，先从 `.env` 读取 Token，不要显示；客户端和插件必须使用各自的 Core 凭据，不能用 Gateway API Key：

```bash
export POWERCONTEXT_CLIENT_SERVER_URL=http://127.0.0.1:17429
export POWERCONTEXT_CLIENT_API_TOKEN="$(sed -n 's/^POWERCONTEXT_SERVER_AUTH_TOKEN=//p' "$HOME/powercontext/.env")"
powercontext ready
powercontext scope create --title team --summary "Team context"
```

最后一条命令输出形如 `scp_... team (version 1)`，开头的 `scp_...` 就是 Scope ID，后文记作 `<scope_id>`。

**检查**：`ready` 中 `database`、`inference.generation`、`inference.embedding`、`runtime` 均为 `ready`。

### 配置网关

不部署网关时跳过本小节。在 `<HOME>/powercontext/gateway.env` 写入：

```dotenv
PC_GATEWAY_CORE_BASE=http://127.0.0.1:17429
PC_GATEWAY_DEFAULT_SCOPE=<scope_id>
PC_GATEWAY_INJECTION=on
PC_GATEWAY_SEDIMENT=on
PC_GATEWAY_UPSTREAM_PROFILE_RESPONSES=team-upstream
PC_GATEWAY_UPSTREAM_API_KEY_RESPONSES=
```

- `PC_GATEWAY_UPSTREAM_API_KEY_RESPONSES=` 留空，请管理员填写上游的 API key，确认填好后再启动网关。
- 关闭注入时改为 `PC_GATEWAY_INJECTION=off`，关闭采集时删除 `PC_GATEWAY_SEDIMENT=on`。关闭 Steering 时追加 `PC_GATEWAY_STEERING=off`，使用团队指令时追加 `PC_GATEWAY_STEERING_FILE=<文件路径>`。关闭前缀抽取时追加 `PC_GATEWAY_PREFIX_EXTRACTION=off`。
- 网关调用 Core 必须使用已认证的 Server Token。用下面的命令复制，不要显示；它必须与 `PC_GATEWAY_API_KEY` 不同：

  ```bash
  printf 'PC_GATEWAY_CORE_TOKEN=%s\n' "$(sed -n 's/^POWERCONTEXT_SERVER_AUTH_TOKEN=//p' "$HOME/powercontext/.env")" >> "$HOME/powercontext/gateway.env"
  ```

- 所有监听（包括回环地址）都必须配置非空的面向客户端 Key。成员用它访问模型路由，且它必须与 Core Token 不同。生成时不要显示：

  ```bash
  printf 'PC_GATEWAY_API_KEY=%s\n' "$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')" >> "$HOME/powercontext/gateway.env"
  ```

- 直接监听网络地址时，再追加 `PC_GATEWAY_LISTEN=0.0.0.0:8787`。

在本流程中，`CORE_URL` 对应 `gateway.env` 中的 `PC_GATEWAY_CORE_BASE`，`PC_GATEWAY_CORE_TOKEN` 也在该文件中，
而 `ACCESS_ADMIN_TOKEN` 是 `.env` 中的 Server Token；它同时是引导管理员凭据。若替换为专用 Gateway 身份，则按
[网关认证与目标要求](../operate/deploy-server.md#网关模型目标)授予 `gateway.user`、`server.observer`，以及（开启采集时）
`scope.contributor`。

开启前缀抽取时，用 Gateway Key 设置回调请求头并重新加载 Server：

```bash
printf "POWERCONTEXT_SERVER_INFERENCE_PREFIX_EXTRACTION_GATEWAY_HEADERS='{\"Authorization\":\"Bearer %s\"}'\n" "$(sed -n 's/^PC_GATEWAY_API_KEY=//p' "$HOME/powercontext/gateway.env")" >> "$HOME/powercontext/.env"
powercontext config validate --env-file "$HOME/powercontext/.env"
powercontext service install --env-file "$HOME/powercontext/.env"
```

本团队流程同样使用引导 Server Token 作为 Gateway 凭据，请在 `<scope_id>` 上为它授予 `gateway.user`。若改用专用 Gateway 身份，则按上文授予所需权限；Gateway API Key 只认证模型路由请求，Core 客户端和插件仍须使用各自的 Core 凭据。

执行 `chmod 600 "$HOME/powercontext/gateway.env"`，然后按[第 3a 步](#让网关自动启动)创建 `run-gateway.sh` 并让网关自动启动。

**检查**：`curl -s http://127.0.0.1:8787/healthz` 有响应。

### 验收

部署网关时，用一个请求模拟成员的 Agent，带上会话标识和网关 Key：

```bash
. "$HOME/powercontext/gateway.env"
curl -s http://127.0.0.1:8787/gateway/v1/responses \
  -H 'Content-Type: application/json' -H "Authorization: Bearer $PC_GATEWAY_API_KEY" \
  -H 'X-PowerContext-Conversation-ID: admin-check-1' \
  -d '{"model":"<upstream-model>","input":"我们团队决定：所有接口返回的时间一律使用 UTC。请复述这条约定。"}'
```

等待一到两分钟，在团队 Scope 中检索这条决定：

```bash
curl -s http://127.0.0.1:17429/v1/memory/search -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $POWERCONTEXT_CLIENT_API_TOKEN" \
  -d '{"scope_id":"<scope_id>","query":"接口 时间 UTC","limit":5}'
```

结果的 `hits` 中应有这条约定。记忆由模型改写，按意思核对，不要按原句匹配。不部署网关时，用 `powercontext` CLI 或 MCP 显式保存一条记忆再检索。

完成后告诉管理员：

- 成员需要的连接信息：Server 地址，开启认证时还有 `.env` 中的 Token；部署了网关时还有网关地址和 `gateway.env` 中的 `PC_GATEWAY_API_KEY`。地址取决于访问方式：使用反向代理时是代理对外的 HTTPS 地址，使用 SSH 转发时是成员本机转发后的地址，直接监听网络地址时是 `http://<主机地址>:17429` 和 `http://<主机地址>:8787/gateway/v1`。成员如何接入由管理员安排，本页不涉及。
- 对外提供 HTTPS 时，在反向代理中转发到 `127.0.0.1:17429` 和 `127.0.0.1:8787`。
- `.env` 和 `gateway.env` 包含密钥，不要提交到 Git。

## 4. 验收

团队部署按第 3c 步的验收执行，不使用本节。

请用户启动 Agent：网关路径使用 `codex-pc` 或 `opencode-pc`，并开启新的会话；插件路径直接启动所选 Agent，Codex 和 Claude Code 首次会话要先在 `/hooks` 中信任 PowerContext 的 Hook。

1. 请用户发送一条包含具体决定的消息，例如："我们的项目决定：金额一律用整数分存储，不用浮点数。请复述这条约定。"
2. 等待一到两分钟，查询这条决定是否已经生成记忆：

   ```bash
   curl -s http://127.0.0.1:17429/v1/memory/search -H 'Content-Type: application/json' \
     -d '{"scope_id":"<scope_id>","query":"金额 整数分","limit":5}'
   ```

   结果的 `hits` 中应有这条约定。记忆由模型改写，措辞和标点会变化，按意思核对，不要按原句匹配。插件路径下，把地址换成 `.env` 中的 `POWERCONTEXT_CLIENT_SERVER_URL`，使用 Agent 绑定的 Scope；启用认证时加上 `Authorization: Bearer` 请求头。
3. 请用户开启**新的会话**，提问："这个项目里金额怎么存储？"回答应当给出整数分的约定。
4. 网关路径下，确认生成记忆复用了 Agent 的缓存前缀：在 `powercontext service status` 给出的日志中查找 `atomic_memory.prefixed_extraction`。找到表示 Server 尝试过前缀抽取；轮次较短时，Server 会因为不划算而回退到普通生成，这是正常行为。

第 3 步回答正确后，告诉用户安装完成，并说明：

- 网关路径：以后用 `codex-pc` 或 `opencode-pc` 启动才会经过 PowerContext；直接运行 `codex` 或 `opencode` 不受影响。
- 配置文件在 `<HOME>/powercontext`，其中 `.env` 包含密钥，不要提交到 Git。
- 以后要让其他电脑使用同一份记忆，见[连接远程 Server](../operate/connect-remote-server.md)。
