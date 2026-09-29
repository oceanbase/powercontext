---
status: community
title: ZCode
description: 安装 PowerContext ZCode 插件，验证自动生成 Memory、新会话召回和 MCP 记忆读写。
---

# ZCode

`community` · `experimental`

本集成支持[开源 ZCode CLI](https://github.com/zai-org/ZCode)。官方 Windows 桌面版 3.14.3 已用真实
PowerContext Server 和 GLM-5.3-Flash 验证普通提示词采集、自动生成 Memory、新会话召回及 MCP Memory 读写。
同一版本也已验证 Handoff 的准备、临时读取、提交和新会话读取，以及本地 Bearer 鉴权与断服恢复。
开源 CLI 还通过 SSH 端口转发验证了跨机器 HTTPS 连接。远端端口的直接 HTTPS 接入及其他官方版本尚未验证。

## 安装匹配的 Server 和插件

先安装开源 ZCode CLI，或安装官方 Windows 桌面版；确保 `node` 在 `PATH` 中且版本不低于 24。
使用同一个 PowerContext checkout 提供 Server 和插件，避免两端契约不一致：

```bash
powercontext setup zcode --source /path/to/powercontext
```

`powercontext` 命令本身也应来自该 checkout。开发环境可先安装 `powercontext[cli,server]`，再运行上述命令。
若通过 GitHub 安装，`--source owner/repository --ref <git-ref>` 应指定与已安装 Server 相同的提交或发布标签；
省略 `--source` 会使用默认 PowerContext 仓库。移动分支可能在两次安装间发生变化，不能仅凭分支名认定版本匹配。

安装器将插件复制到 `~/.zcode/cli/plugins/powercontext`，并把路径加入共享用户配置
`~/.zcode/cli/config.json` 的 `plugins.dirs`。它保留其他模型、Provider 和插件配置；重复运行会更新
PowerContext 管理的插件副本。官方 Windows 桌面版通常可从
`%LOCALAPPDATA%\Programs\ZCode\ZCode.exe` 自动识别。使用源码构建的 CLI 时，先指定构建产物：

```powershell
$env:ZCODE_CLI_BIN = 'C:\path\to\ZCode\apps\zcode-cli\packages\cli\dist\zcode.cjs'
powercontext setup zcode --source 'C:\path\to\powercontext'
```

安装后完全退出 ZCode，包括 Windows 系统托盘中的进程，再重新打开。仅关闭窗口可能不会重新加载插件。

## 启动 Server 和宿主

需要自动从 Source 提取 Memory 时，先为独立运行的 Server 配置 Generation、Source 窗口和 Memory 定时处理。
首次配置可运行向导；已有 Server 配置时，保留原有存储、监听和鉴权设置：

```bash
powercontext config init --output powercontext.env
```

例如，使用 Z.ai 的 GLM Coding Plan API Key 处理**编程项目**的记忆，可在仅供 Server 读取的
`powercontext.env` 中配置，并将该文件排除在 Git 提交之外：

```dotenv
POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL=openai-chat:glm-5.3-flash
OPENAI_BASE_URL=https://api.z.ai/api/coding/paas/v4
OPENAI_API_KEY=<你的 Coding Plan API Key>
POWERCONTEXT_SERVER_RUNTIME_SCHEDULE_SECONDS=60
POWERCONTEXT_SERVER_RUNTIME_MEMORY_SCHEDULE_SECONDS=60
```

两个调度都需要启用：前者处理已采集的 Source 窗口，后者调度 Memory 生成。只配置后者时，
新 Source 可能一直不会进入自动提取；`/health/ready` 的 Generation ready 不能代替条目读回。

BigModel 国内 Coding Plan 的 `OPENAI_BASE_URL` 应为 `https://open.bigmodel.cn/api/coding/paas/v4`。
Coding Plan 的专用端点仅用于 Coding 场景，不能与通用 API 端点混用；具体账户和模型可用性见
[ZCode 官方模型配置](https://zcode.z.ai/cn/docs/configuration)。`ZCODE_CODING_PLAN_API_KEY` 不会自动映射到
Server 所需的 `OPENAI_API_KEY`。配置完成后运行：

```bash
powercontext config validate --env-file powercontext.env
powercontext server run --env-file powercontext.env
```

已有持久化数据库首次启用 Generation 处理能力时，直接用新配置启动可能报
`Processing configuration differs from the completed maintenance manifest`。这时先用新配置执行只读
`powercontext server processing-migrate --action plan --env-file powercontext.env`；备份数据库并停止旧 Server、
Worker 和写入后，按[后台处理状态迁移](../operate/artifact-processing-migration.md)使用新的 migration ID 执行
`apply`、`verify`。仅在验证返回 `ready: true` 后启动新配置。不要通过删除原数据库来绕过迁移。

Server 是独立进程，保持它运行。显式 `remember_memory` 写入和全文检索不依赖 Generation 或 Embedding；
未配置这些模型时，Server 仍可健康运行，但不会自动从普通 Source 生成 Memory，语义检索也不可用。
`/health/ready` 中的 `inference.generation: ready` 只说明连接已配置；真实提取须检查 Memory 及 Source 引用。
模型和处理设置见[配置模型与完整记忆](../get-started/configure-models.md)。

Hook 和 MCP 使用安装时保存的同一个 Server URL，默认 `http://127.0.0.1:8000`。
Server 使用其他地址时，重新运行 `powercontext setup zcode --server-url https://host --source /path/to/powercontext`。
非环回明文 HTTP 还需在 setup 时显式传入 `--allow-insecure-http`。只在启动 ZCode 的终端修改
`POWERCONTEXT_ZCODE_SERVER_URL`，不会覆盖已安装插件保存的 URL。

连接远程 Server 时，在远端启用访问控制，将 PowerContext Server 放在有效证书的 HTTPS 反向代理后，
再用上述 `https://host` 地址安装插件。按[部署认证](../operate/deploy-server.md)配置 Server 身份与 Token；
启动 ZCode 的进程提供完整的 `POWERCONTEXT_ZCODE_AUTHORIZATION`，如下一节所示。
验收时分别检查无凭据请求被拒绝、ZCode MCP 工具成功，以及普通提示词在同一远程 Scope 中形成 Source。
开源 CLI 已通过 SSH 端口转发在跨机器 HTTPS 环境完成这些检查。使用私有 CA 时，给 ZCode 进程设置
`NODE_EXTRA_CA_CERTS`，给 Python 诊断进程设置 `SSL_CERT_FILE`，均指向该 CA 证书；保持证书验证开启。
该隧道验收不能证明远端监听端口可被客户端直接通过 HTTPS 访问。

启动 ZCode 前准备一个已有 Scope：使用 Server 默认 Scope，或为 ZCode workspace/session 建立持久 binding；
也可用 `POWERCONTEXT_ZCODE_SCOPE_ID` 显式指定。Hook 不会自动创建 Scope。项目隔离方法见
[Scope 与访问控制](../workflows/scopes-and-access.md)。官方桌面版的模型凭据由 ZCode 自身管理；
ZCode 的 GLM Coding Plan API Key 不会自动成为 PowerContext Server 的 Generation 凭据。

`.env` 可以通过 `powercontext setup --env-file .env zcode` 提供安装参数，但 ZCode 进程不会因此自动读取该文件。
Generation 的 `OPENAI_API_KEY` 应提供给 Server 进程，例如写在其 `--env-file` 指定的文件中；
ZCode 自身的模型凭据由 ZCode 管理。
PowerContext 授权变量必须在启动 ZCode 时可用。不要把密钥写进插件目录。

## 诊断安装与运行中的配置

```bash
powercontext doctor zcode --json
```

该命令分别检查 ZCode CLI 或 Windows 桌面版、插件注册、Hook 文件及 Node 语法、MCP 声明、Server readiness。
`ok: true` 只证明这些静态和只读检查通过，不证明已运行的 ZCode 进程加载了新配置，也不证明 Scope
解析、上下文注入或 MCP 操作已经发生。完全退出并重启 ZCode，再在新会话里检查 PowerContext 工具是否出现。

| 失败项 | 检查方向 |
| --- | --- |
| `zcode` | 确认 CLI 在 `PATH`、`ZCODE_CLI_BIN` 指向构建文件，或官方桌面版安装在可检测路径。 |
| `plugin` | 检查 `plugins.dirs` 中的 PowerContext 路径，以及插件的 `.zcode-plugin/plugin.json`。 |
| `hooks` | 检查 `node --version`、`hooks/hooks.json` 和 `hooks/user_prompt_submit.mjs`。 |
| `mcp` | 检查安装后的 `.mcp.json` 与 `powercontext.json` 是否指向同一 Server，鉴权占位符是否匹配。 |
| `server` | 启动 Server，核对监听地址及 `/health/ready`；这个检查不执行 Memory 读写。 |

## 查看自动执行的证据

ZCode 集成没有 DSH 的 `/pc` 或 `/pc doctor` 会话内命令。普通提示词触发的 Hook 会按顺序尝试解析
Scope、调用 `POST /v1/context/prepare`、采集提示词到 `POST /v1/sources/content`。排查一次会话时，
分别核对 Server 请求、Source 记录以及宿主实际送给模型的上下文；`doctor zcode` 不能代替这些观测。

| 阶段 | 成功证据 | 边界 |
| --- | --- | --- |
| Scope | resolver 返回已有 `scope_id` | 没有 Scope 时本轮跳过注入和采集。 |
| 准备上下文 | `context/prepare` 返回非空 `ready` | `empty` 是正常空结果，模型答对也不能单独证明发生召回。 |
| 采集 | `sources/content` 返回 `202 accepted`，Source 可按 ID 回读 | Source 是证据，不等于 Memory entry 或自动提取成功。 |
| 注入 | 模型请求包含以 `PowerContext context for this request` 开头的历史上下文 | 注入不保证模型采纳；当前指令和仓库状态仍优先。 |

Hook 为同一 Scope、session、turn 和提示词计算稳定 Source ID。若宿主没有提供 `turnId`，同一会话内
重复的相同文本会保守地复用 Source ID，无法区分独立提交和重试。默认采集提示词；疑似包含密钥的
文本不会自动采集。准备上下文与采集相互独立：前者失败后仍可能采集，后者失败也不会丢弃已准备的上下文。

## 验证自动采集、处理和新会话召回

这项验收不调用 `remember_memory`，也不手动调用 `memory/flush`：

1. 准备一个独立 Scope，将它设为 Server 默认 Scope 或绑定到 ZCode workspace。确认其中没有目标测试事实。
2. 在 ZCode 会话中发送包含独特代号的普通**持久决策或约束**，不要求 Agent 使用记忆工具。
   核对 `POST /v1/sources/content` 返回 `202 accepted`，并在同一 Scope 中找到该 Source。
3. 等待 Memory 调度处理。通过 `POST /v1/memory/entries/list` 或 `POST /v1/memory/search` 找到新条目，
   核对 `source_refs` 指向第 2 步采集的 Source。Cursor 前进但没有条目，可能只是该提示词不符合
   Memory 提取规则；普通问答或容易从代码重新查到的事实并不保证被保存。
4. 在同一 Scope 下开启全新 ZCode 会话，提问时不要包含代号。核对该轮 `context/prepare` 返回 `ready`、
   模型输入包含 PowerContext 注入的事实，以及回答中的代号。仅看最终答案不足以证明召回路径。

官方 Windows 桌面版 3.14.3 已在隔离 Scope 中用真实 GLM-5.3-Flash 完成上述链路：Memory 条目引用了
ZCode Hook 采集的 Source，新会话模型输入包含 PreparedContext，并正确回答未在新问题中出现的代号。

## 验证显式写入和新会话召回

这是会写入测试证据的验收。先确认 Server、插件和 Scope 已准备好，再使用独特的合成事实：

1. 在 ZCode 新会话明确要求调用 PowerContext 的 `remember_memory`，提供已有的 `scope_id` 和测试事实，
   例如“项目 aurora 的验证颜色是 violet-cedar-1457”。核对工具成功结果，而非只看模型声称“已记住”。
2. 通过 `POST /v1/memory/search` 或 ZCode 的 `search_memory` MCP 工具搜索这个代号，确认 Server 返回
   Memory entry。提示词被采集为 Source 本身不足以证明显式写入成功。
3. 在同一 Scope 下开启全新 ZCode 会话，询问测试颜色；同时核对该轮的 `context/prepare` 与实际注入。
   只凭最终答案正确，无法排除模型从当前提示词或旧会话文本获知答案。

官方 Windows 桌面版 3.14.3 已验证 `search_memory` 返回已存在的 Memory，以及 `remember_memory`
写入后可由 Server 搜索。这与上一节的自动提取是两条独立路径。
开源 CLI 的宿主测试可运行：

```bash
node --test integrations/zcode/plugins/powercontext/tests/plugin.test.mjs
node --test integrations/zcode/plugins/powercontext/tests/host.test.mjs
```

第二条需要将 `ZCODE_CLI_BIN` 指向已构建的开源 CLI；它使用模拟模型和 Server，不能代替真实宿主验收。
官方 Windows 桌面版 3.14.3 已分别连接本地无鉴权与 Bearer 鉴权 Server：MCP `list_scopes` 成功，
普通提示词经 Hook 形成可读回的 Source。断服期间普通对话仍能回答；Server 恢复后，无需重启 ZCode，
MCP 读取和 Hook 采集重新成功。Handoff 已完成 `handoff_current_work` → `continue_handoff`（prepared）→
`commit_handoff` → 全新会话 `continue_handoff`（latest），提交的 revision 1 能从 Server 读回。
其他已实测工具包括 `get_scope`、`list_memory_entries`、`capture_content_source`、
`list_artifact_candidates` 和 `list_dream_runs`；后两者返回合法的空列表。
另在由 Windows 登录用户创建的全新用户目录中安装插件，官方桌面版通过 MCP `list_scopes` 读取隔离 Server，
Hook 也把该轮普通提示词写成 Source。其他官方版本尚未实测。
开源 ZCode CLI 0.16.9 也已用真实模型和本地 Server 完成采集 → 自动生成 Memory → 全新会话召回：
隔离 Scope 中的项目回滚约束由普通提示词采集为 Source，定时处理生成的 Memory 条目引用了该 Source；
新会话问题未包含构建标记，`context/prepare` 返回含该标记的 `ready` 内容，CLI 回答了正确标记。
容易从代码重新找到的常量在同次验收中未被提取，符合 Memory 的筛选规则。
同一开源 CLI 还通过 SSH 端口转发、Caddy HTTPS、受信任的私有 CA 和 Bearer 鉴权连接了另一台 Linux
机器上的 PowerContext Server。无凭据 API 请求返回 401，有凭据请求返回 200；MCP `list_scopes`
读到远端 Scope，普通 CLI 提示词产生的 Source 可从该 Scope 读回。当前网络路径下，直接连接远端端口的
TLS 握手失败，因此远端端口的直接 HTTPS 接入尚未验收。

## 理解插件行为

插件通过两条路径访问同一个 PowerContext Server：

- `UserPromptSubmit` Hook 在模型分析当前提示词前，请求最多 8000 字节的 PreparedContext，并独立采集提示词为 Source；
- ZCode 原生 MCP 客户端加载插件的 `.mcp.json`，暴露显式 Memory 和 Handoff 工具，例如 `search_memory`、
  `remember_memory`。首次 Handoff 的 `base` 和 `generation` 可以为 `null`，后续工具应原样传递
  `PreparedHandoff`，不要省略或编造字段。读取结果标记为 `untrusted_history`，需结合当前项目状态核实。

Hook 按 `POWERCONTEXT_ZCODE_SCOPE_ID`、当前 session binding、workspace binding、Server 默认 Scope
解析 Scope。workspace 使用 Git 根目录或工作目录的规范化路径哈希作为 binding key；路径本身不是 Scope ID。
远程工作区需要在启动宿主前设置 `POWERCONTEXT_ZCODE_REMOTE_WORKSPACE=true`，并给出已有的
`POWERCONTEXT_ZCODE_SCOPE_ID`，Hook 才会跳过本地路径推断。MCP 工具执行时仍需选择正确的 Scope。

## 排查 MCP 工具与自动 Hook

PowerContext 工具未出现在会话里时，先完全退出并重启 ZCode，再查 `plugins.dirs`、插件目录和 `.mcp.json`。
工具已出现但调用失败时，检查 MCP 结果中的 Scope、鉴权、Server URL 和 HTTP 错误；不要把模型的文字
回答当成工具结果。`doctor zcode` 只核对声明，不会发起 `search_memory` 或 `remember_memory`。
在 Windows 上，安装插件的目录还必须可由启动桌面版的账户读取。若另一个受限账户创建了目录，安装账户下的
`doctor` 可能通过，但桌面版会报 `plugin_manifest_not_found`。用桌面版账户检查
`Test-Path <插件目录>\.zcode-plugin\plugin.json`；若显示拒绝访问，请用该账户在其可读的新目录重新安装。

自动 Hook 失败不会中断 ZCode 对话。Hook 将脱敏的 `component=powercontext.zcode`、阶段和 code 写入
stderr，宿主是否展示取决于其日志配置。常见结果如下：

| code | 含义与恢复 |
| --- | --- |
| `scope_unresolved` | 检查显式 Scope、session/workspace binding 或 Server 默认 Scope。 |
| `server_unavailable` / `timeout` | 检查 Server 是否运行、保存的 URL、网络和请求耗时。 |
| `unauthorized` / `forbidden` | 检查运行中的 ZCode 进程是否取得完整 Authorization header，以及主体权限。 |
| `not_found` / `conflict` | 核对 Scope 或路由，以及相关业务状态；单凭 404 不能断定版本不匹配。 |
| `invalid_response` / `invalid_server_url` | 核对 Server 响应契约或重新运行 setup 保存有效地址。 |

有效的空召回不会注入错误通知。Hook 对响应形状和 8000 字节上限进行校验，读取失败会降级；
显式 MCP 写入仍应检查工具结果，不能把自动 Hook 的 fail-open 当作写入成功。

## 控制提示词采集

默认开启采集。需要关闭时，在启动 ZCode 前设置环境变量，或在安装时使用 `--no-capture-prompts`：

```powershell
$env:POWERCONTEXT_ZCODE_CAPTURE_PROMPTS = 'false'
```

环境变量会覆盖插件安装时保存的采集设置。改变已运行进程之外的环境变量不会影响当前会话；
重启 ZCode 后才会生效。采集关闭不影响 PreparedContext 召回或显式 MCP 工具。

## 连接启用鉴权的本地 Server

Server 启用访问控制后，安装插件时提供完整 Authorization header，让 MCP 配置引用运行时变量：

```powershell
$env:POWERCONTEXT_ZCODE_AUTHORIZATION = "Bearer $env:POWERCONTEXT_LOCAL_TOKEN"
powercontext setup zcode --source 'C:\path\to\powercontext'
```

启动 ZCode 的进程也必须取得同一个 `POWERCONTEXT_ZCODE_AUTHORIZATION`。Hook 读取该环境变量，
MCP 配置通过 `${POWERCONTEXT_ZCODE_AUTHORIZATION}` 占位符读取它；不要把 token 写入 `.mcp.json`、
`powercontext.json` 或 Server URL。若启用鉴权后没有重新 setup，Hook 和 MCP 的配置可能不一致。
Server 的鉴权与访问控制设置见[部署认证](../operate/deploy-server.md)。

## 环境变量

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `ZCODE_CLI_BIN` | 未设置 | 使用源码构建 CLI 时指定入口；官方 Windows 桌面版可自动检测。 |
| `POWERCONTEXT_ZCODE_SCOPE_ID` | 未设置 | 在 session/workspace binding 与默认 Scope 前显式选择已有 Scope。 |
| `POWERCONTEXT_ZCODE_REMOTE_WORKSPACE` | `false` | 远程工作区禁用本地路径 binding；需同时设置 Scope ID。 |
| `POWERCONTEXT_ZCODE_CAPTURE_PROMPTS` | 安装时设置，默认 `true` | 在运行时覆盖提示词采集开关。 |
| `POWERCONTEXT_ZCODE_AUTHORIZATION` | 未设置 | Hook 与 MCP 使用的完整 `Bearer <token>` header。 |

Server URL、非环回明文 HTTP 同意和默认采集设置由 `setup zcode` 保存到插件的 `powercontext.json`。
修改这些安装参数后重新运行 setup 并重启 ZCode。`POWERCONTEXT_ZCODE_SERVER_URL` 只在插件没有保存
Server URL 时作为 Hook 的回退值，不能用于修改正常安装的 MCP 端点。

## 卸载

从 `~/.zcode/cli/config.json` 的 `plugins.dirs` 中只移除 PowerContext 路径；确认
`~/.zcode/cli/plugins/powercontext` 中存在 `.powercontext-owned` 后，再删除该受管理目录。
最后完全退出并重启 ZCode。卸载插件不会删除 Server 数据或 ZCode 模型配置。
