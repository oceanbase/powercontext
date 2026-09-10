---
title: 快速开始
description: 通过配置向导安装完整记忆能力，接入 Codex，并验收 Source、Topic Memory 与跨会话召回。
---

# 快速开始

本页从安装开始，带你完成一次真实的记忆体验：在 Codex 中讨论项目，看到原始输入进入 Source、主题记忆生成并演进，
再在新会话中找回决策。以下命令使用 PowerContext 1.0.0 RC2 预发布版本，Agent 插件使用对应的
`powercontext-v1.0.0rc2` tag，供测试使用。

需要 macOS 或 Linux、Python 3.11+、Git、[uv](https://docs.astral.sh/uv/getting-started/installation/) 和已安装的 Codex CLI。
完整记忆还需要可用的 Generation 和 Embedding 模型 API；准备好各自的地址、模型名和 API key。
Codex 或 Claude 的订阅登录不会自动为 PowerContext Server 提供这些 API。只有 Agent 登录、没有模型 API 时，
仍可选择基础记忆，验收显式保存与召回，但不能据此期待自动 Topic Memory。

## 1. 安装并进入配置向导

```bash
uv tool install --force "powercontext[cli,server]==1.0.0rc2"
mkdir -p ~/powercontext-demo
cd ~/powercontext-demo
powercontext config init --language zh --output .env
```

首次本机体验可以这样选择：

1. **存储**：SQLite 可直接开始；需要体验嵌入式 seekdb 时选择 seekdb，并同意后台安装缺失依赖。
2. **使用场景**：Agent、浏览器和 Server 都在这台机器时选“只在当前机器”。Server 在另一台机器时，先看[连接远程 Server](../operate/connect-remote-server.md)。
3. **记忆能力**：选择“完整记忆能力”，填写 Generation 和 Embedding API；不确定协议与维度时看[模型配置](configure-models.md)。
4. **Dashboard**：开启，便于观察 Source 和记忆。向导会生成或沿用 Server Token。
5. **后台处理**：可先使用各制品的推荐周期。检查间隔不是完成时限，模型处理还需要时间。
6. **Agent**：选择 Codex，规划新的独立 Scope；如需 Claude Code，再选择它，最后选择“结束 Agent 配置”。
7. 核对并保存。

保存后会得到以下文件：

| 文件 | 用途 |
| --- | --- |
| `.env` | Server 配置，包括数据库、模型凭据和 Server Token |
| `.env` | 本次安装使用的 Server、客户端、Agent 和模型配置 |
| `.env.next-steps.md` | 与本次选择对应的启动、Scope 创建、插件连接和验收说明 |

向导会打印 Dashboard 地址、新生成的 Token，以及所选 SSH 转发命令。以后可在 `.env` 中查看
`POWERCONTEXT_SERVER_AUTH_TOKEN`。文件包含凭据，不要提交到 Git。
若 seekdb 仍在安装，向导会在当前界面显示活动进度并等待；安装失败时先按提示完成依赖安装。
保存配置或装好依赖，都不代表 Server 已经启动。

## 2. 启动 Server

在当前终端执行：

```bash
powercontext config validate --env-file .env
powercontext server run --env-file .env
```

保持终端运行。在浏览器打开向导输出的 Dashboard 地址；本机默认是
`http://127.0.0.1:8000/dashboard/home`，使用 **Server Token** 登录，不是模型 API key。
首次没有数据是正常现象。需要关闭终端后继续运行时，改用[个人后台服务](../operate/deploy-server.md#运行持久个人-server)。

另开终端，加载客户端连接配置并检查服务：

```bash
cd ~/powercontext-demo
set -a
. ./.env
set +a
powercontext ready
powercontext capabilities
```

确认服务就绪且所选处理能力可用；`degraded` 表示部分依赖仍有问题，不能算完整记忆验收通过。
`config validate` 只检查配置，在线结果以启动后的检查为准。

## 3. 创建 Scope 并安装 Codex 插件

打开 `.env.next-steps.md`，执行其中“创建计划中的独立 Scope”的请求。Server 响应会返回真正的 `scope_id`。
将它写入 `.env`：

```dotenv
POWERCONTEXT_CODEX_SCOPE_ID=替换为返回的scope_id
```

`codex-xxxxxxxx` 是向导规划的标题，不是 ID。配置 Claude Code 时，将其创建请求返回的 ID 写入
`POWERCONTEXT_CLAUDE_SCOPE_ID`。不同 Agent 可以各自隔离，也可以显式绑定同一已有 Scope。
切换目录本身不会创建隔离。验收期间，Dashboard 和 Agent 必须使用相同的 Scope。

重新加载客户端文件并安装匹配的插件：

```bash
set -a
. ./.env
set +a
powercontext setup codex --ref powercontext-v1.0.0rc2
powercontext doctor codex
codex
```

在 Codex 中确认 PowerContext Hook 和 MCP 均已加载。非默认地址还需按 `.env.next-steps.md` 的 Codex 连接说明，
检查已安装插件的 `.mcp.json`：MCP 的 URL 必须和 Hook 使用同一 Server，Authorization 从
`POWERCONTEXT_CODEX_AUTHORIZATION` 读取。仅安装插件不会启动 Server。

这些命令以 Codex CLI 为例。桌面 App 不一定继承终端的环境变量；在桌面中验收前，要确认 App 的 Hook 和 MCP
都拿到了相同的地址、Token 和 Scope。更多宿主行为见[Codex](../integrations/codex.md)和[Claude Code](../integrations/claude-code.md)。

完整能力中的 Profile 还需要为这个真实 Scope 启用生成策略。按[Profile 策略步骤](configure-models.md#为-scope-启用-profile-策略)
读取当前版本并更新；只生成配置文件不会自动完成它。

## 4. 用普通对话验收 Topic Memory

在刚启动的 Codex 会话中发送一条包含具体决策的普通输入，例如：

```text
我们正在设计 Aurora 验收项目。决定用 uv 管理 Python 依赖，配置文件使用 .env。
第一阶段只在一台 Linux 服务器运行，Mac 通过 SSH 隧道访问。请复述这些约束，暂时不要修改代码。
```

在 Dashboard 选择刚绑定的 Scope，依次检查：

1. **Source**：能找到这条用户输入。Codex 的提示词采集不意味着自动采集所有 Agent 回复。
2. **Topic Memory**：经过所配置的检查周期和模型处理后，出现相关主题，内容与 Source 相符。
3. 继续发送相关决策：“Aurora 的部署方案更新：服务需要由用户级服务管理器运行；SSH 隧道方案保持不变。”
4. 确认第二条 Source 入库，主题内容或修订记录反映这次更新。不要以“等了一分钟”代替检查。
5. 开启新的 Codex 会话，保留同一 Scope，询问：“从 PowerContext 找回 Aurora 的依赖管理和部署决定，带上引用。”

找到正确内容及其 citation，才完成从采集到演进再到跨会话召回的验收。`doctor codex` 成功仅说明安装与连接检查通过，
不能替代上述业务检查。模型可能决定合并或不更新主题，不保证每条消息都创建一条记忆。

基础记忆模式可改为显式要求 Agent“把 Aurora 使用 uv 的决定保存到 PowerContext”，再在新会话搜索并核对 citation；
该测试不覆盖自动提取。完整能力中的 Profile、Experience 和 Skill 还有各自的触发或审核条件，
见[模型与能力配置](configure-models.md#完整能力不等于每条对话都会生成所有制品)。

## 继续使用

- [安装和运行](install-and-run.md)：版本、seekdb 依赖、已有存储与更新。
- [部署 Server](../operate/deploy-server.md)：后台服务、SSH、HTTPS 和数据备份。
- [配置模型](configure-models.md)：API 协议、向量维度与提取检查。
- [故障排查](../operate/troubleshoot.md)：服务、模型、采集或召回失败。
