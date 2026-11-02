---
title: 快速开始
description: 通过配置向导安装完整记忆能力，接入 Codex，并验收 Source、Topic Memory 与跨会话召回。
---

# 快速开始

本页从安装开始，带你完成一次真实的记忆体验：在 Codex 中讨论项目，看到原始输入进入 Source、主题记忆生成并演进，
再在新会话中找回决策。以下命令使用 PowerContext 1.4.1，Agent 插件使用对应的
`powercontext-v1.4.1` tag。

需要 macOS 或 Linux、Python 3.11+、Git、[uv](https://docs.astral.sh/uv/getting-started/installation/) 和已安装的 Codex CLI。
完整记忆还需要可用的 Generation 和 Embedding 模型 API；准备好各自的地址、模型名和 API key。
Codex 或 Claude 的订阅登录不会自动为 PowerContext Server 提供这些 API。只有 Agent 登录、没有模型 API 时，
仍可选择基础记忆，验收显式保存与召回，但不能据此期待自动 Topic Memory。

## 1. 安装并进入配置向导

```bash
uv tool install --force "powercontext[cli,server]==1.4.1"
mkdir -p ~/powercontext-demo
cd ~/powercontext-demo
powercontext config init --language zh --output .env
```

首次本机体验可以这样选择：

1. **存储**：SQLite 可直接开始；需要体验嵌入式 seekdb 时选择 seekdb，并同意后台安装缺失依赖。
2. **使用场景**：Agent、浏览器和 Server 都在这台机器时选“只在当前机器”。Server 在另一台机器时，先看[连接远程 Server](../operate/connect-remote-server.md)。
3. **记忆能力**：选择“完整记忆能力”，填写 Generation 和 Embedding API；不确定协议与维度时看[模型配置](configure-models.md)。
4. **Dashboard**：开启，便于观察 Source 和记忆。本地认证默认关闭，需要访问认证时再开启，见[可选认证](configure-server-environment.md#本地-dashboard-与可选认证)。
5. **后台处理**：可先使用各制品的推荐周期。检查间隔不是完成时限，模型处理还需要时间。
6. **Agent**：选择 Codex，规划新的独立 Scope；如需 Claude Code，再选择它，最后选择“结束 Agent 配置”。
7. 核对并保存。

保存后会得到以下文件：

| 文件 | 用途 |
| --- | --- |
| `.env` | 本次安装使用的 Server、客户端、Agent、数据库和模型配置，包括已配置的凭据 |
| `.env.next-steps.md` | 与本次选择对应的启动、Scope 创建、插件连接和验收说明 |

向导会打印 Dashboard 地址和所选 SSH 转发命令。启用认证时，还会显示一次新生成的 Server Token，
以后可在 `.env` 中查看 `POWERCONTEXT_SERVER_AUTH_TOKEN`。文件可能包含凭据，不要提交到 Git。
若 seekdb 仍在安装，向导会在当前界面显示活动进度并等待；安装失败时先按提示完成依赖安装。
保存配置或装好依赖，都不代表 Server 已经启动。

## 2. 启动 Server

在当前终端执行：

```bash
powercontext config validate --env-file .env
powercontext server run --env-file .env
```

保持终端运行。在浏览器打开向导输出的 Dashboard 地址，端口以 `.env` 中保存的
`POWERCONTEXT_SERVER_HTTP_PORT` 为准。未启用认证时可直接进入页面；启用后使用 **Server Token** 登录，不是模型 API key。
首次没有数据是正常现象。需要关闭终端后继续运行时，先停止前台 Server，再通过
`powercontext service install --env-file .env` 安装[个人后台服务](../operate/deploy-server.md#运行持久个人-server)，复用同一份配置。

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
powercontext setup codex --ref powercontext-v1.4.1
powercontext doctor codex
codex
```

在新会话中发送第一条消息前：

1. 打开 `/hooks` 检查 PowerContext Hook，在收到提示时授予信任。`SessionStart` 解析会话 Scope；
   `UserPromptSubmit` 自动准备相关上下文，并将用户输入采集为 Source。
2. 确认 PowerContext MCP 工具可用。工具发现和 Hook 执行是不同的检查。
3. 如果跳过或拒绝了信任，先按[Hook 恢复步骤](../integrations/codex.md#首次会话-hook-信任)处理，再验收采集。
   `UserPromptSubmit` Hook 未启用时，不会自动采集或召回。

非默认地址还需按 `.env.next-steps.md` 的 Codex 连接说明检查已安装插件的 `.mcp.json`：
MCP 的 URL 必须和 Hook 使用同一 Server，启用认证时还需配置凭据。仅安装插件不会启动 Server。
`doctor codex` 检查安装、连接和工具覆盖，不能证明 Hook 已获信任、已执行或记忆处理成功。

这些命令以 Codex CLI 为例。桌面 App 不一定继承终端的环境变量；在桌面中验收前，要确认 App 的 Hook 和 MCP
都拿到了相同的地址和 Scope，启用认证时还需取得对应凭据。更多宿主行为见[Codex](../integrations/codex.md)和[Claude Code](../integrations/claude-code.md)。

完整能力中的 Profile 还需要为这个真实 Scope 启用生成策略。按[Profile 策略步骤](configure-models.md#为-scope-启用-profile-策略)
读取当前版本并更新；只生成配置文件不会自动完成它。

## 4. 按所选模式验收记忆

Hook 启用后，两种模式都可以采集 Source。基础记忆无需独立的 Server 模型 API；Agent 仍使用自己的模型。
按实际配置选择下面的验收路径。

### 基础记忆：显式保存与检索

在绑定 Scope 的 Codex 会话中发送：

```text
把这条决定保存到 PowerContext：Aurora 验收项目使用 uv 管理 Python 依赖。返回已保存记录的引用。
```

检查实际写入结果，然后在使用同一 Scope 的新会话中询问：

```text
从 PowerContext 搜索 Aurora 的 Python 依赖管理决定，带上 citation。
```

核对已保存内容与引用。对话中的“记住了”不能证明写入成功；这条路径验证显式保存与检索，
不验证自动提取或 Topic Memory 生成。

### 完整记忆：Source 处理与 Topic Memory

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

完整能力不代表每条对话都会生成所有制品。Profile、Experience 和 Skill 还有各自的触发或审核条件，
见[模型与能力配置](configure-models.md#完整能力不等于每条对话都会生成所有制品)。

| 检查 | 成功依据 |
| --- | --- |
| 安装与 MCP 发现 | `doctor codex` 报告连接与工具覆盖；不能验证 Hook 执行 |
| 输入采集 | 真实输入出现在绑定 Scope 的 Source 中 |
| 显式保存 Memory | 返回真实记录引用，新会话能找到对应内容 |
| 自动处理 | 相关 Topic Memory 及其 Source 依据；检查更新，不假设固定完成时间 |
| 跨会话检索 | 同一 Server 和 Scope 下返回正确内容及 citation |

## 5. 选择下一项工作流

七类 Artifact 的输入和消费路径不同。Source 是证据，Tag 是元数据，两者都不是 Artifact family。
下面的例句是请求示例，不是保证生效的关键词触发器。以当前连接实际提供的工具为准；没有专用 MCP 工具的操作，
按链接进入 HTTP/CLI 工作流。概念区别见[Artifact 速览](faq.md#artifact-速览)。

| 能力与用途 | 请求示例或入口 | 前提与结果 |
| --- | --- | --- |
| [Atomic Memory](../workflows/atomic-memory.md)：独立决定与约束 | “在 PowerContext 记住：Aurora 使用 uv。” | `remember_memory` 显式保存无需 Server 生成模型；用 `search_memory` 检索并核对记录 citation。自动提取需要 Generation 和调度。 |
| [Topic Memory](../workflows/topic-memory.md)：持续演进的主题摘要 | “搜索 Aurora 部署方案的主题记忆，再读取相关修订。” | `search_topic_memory` / `get_topic_memory` 读取已有主题。自动生成需要配置 Generation 和调度；核对主题的 Source 引用。 |
| [Profile](../workflows/use-profiles.md)：Scope 背景 | 按画像工作流生成并检查当前 Scope 的画像。 | 自动生成需要 Generation、Scope 策略和调度；策略决定审核或自动提交。召回需在 assembly 中显式选择。 |
| [Handoff](../workflows/handoff-with-codex.md)：接续未完成工作 | “交接当前工作”用于临时转交；“保存持久交接里程碑”用于持久提交；“预览交接，不要提交”用于只读预览。 | 插件 Skill 将交接请求路由到对应流程。区分临时 Prepared 内容和精确的已提交 Revision；无需后台提取。 |
| [Experience](../workflows/create-and-review-experience.md)：可复用经验 | “根据这些精确 Source 引用生成 Experience 候选。” | `generate_experience` 需要 Generation；`propose_experience` 接收完整内容，无需生成模型。检查 pending Candidate 或 `no_op`；生成提案经审核后才能复用。 |
| [Skill](../workflows/create-and-export-skill.md)：可复用流程 | “根据这些精确 Source 引用生成 Skill 候选。” | `generate_skill` 需要 Generation；`propose_skill` 接收完整内容，无需生成模型。审核候选后，显式导出精确的 approved Revision 到 Agent 主机。 |
| [Prompt](../workflows/manage-prompts.md)：操作生成指令 | 按 HTTP 工作流读取并配置某项操作的 Prompt。 | 不是采集的用户消息。检查自定义支持；启用访问控制时需要 `scope.admin`。保存 Revision 不会启用操作或重处理历史。 |
| [Source](../workflows/sources.md)：原始证据 | 发送含独特项目名的普通消息；显式材料可使用 `capture_content_source`。 | Hook 采集无需生成模型。核对真实 Source；采集不能证明 Memory 已生成，也不代表所有 Agent 回复都被采集。 |
| [Tag](../workflows/manage-artifact-tags.md)：整理已保存制品 | 按 Python Client 或 HTTP 工作流，为精确制品设置标签并查询。 | 无需模型；使用已有 Artifact ID、对应权限和标签 ETag，核对保存及查询结果。没有专用 MCP 标签管理工具。 |

### 插件引导 Skill 与受管 Skill

插件自带的 `powercontext-project-context` 将请求路由到对应工作流，和保存在 PowerContext 的 Managed Skill 是两个对象。
宿主目录中找不到引导 Skill 时，先检查插件安装；自然语言示例不会增加缺失工具，也不会授予权限。

生成受管 Skill 后，先检查 Candidate，再由有权限的主体作出审核决定。批准创建不可变 Revision，不代表安装或执行。
按[创建与导出 Skill](../workflows/create-and-export-skill.md)，使用 `powercontext skill export --target codex`
将精确的 approved Revision 导出到目标宿主的 `.agents/skills/<skill-name>` 目录。
确认 Codex 能发现导出的 Skill；没有出现时再重启。直接 Artifact 写入有自己的契约，这里的 Candidate 流程描述生成和提案路径。
谁能审核取决于权限，不要求一定由人审核；宿主发现 Skill 也不会授予执行权限。

## 继续使用

- [安装和运行](install-and-run.md)：版本、seekdb 依赖、已有存储与更新。
- [部署 Server](../operate/deploy-server.md)：后台服务、SSH、HTTPS 和数据备份。
- [配置模型](configure-models.md)：API 协议、向量维度与提取检查。
- [故障排查](../operate/troubleshoot.md)：服务、模型、采集或召回失败。
