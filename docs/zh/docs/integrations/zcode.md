---
title: ZCode
description: 安装 PowerContext ZCode 插件，验证自动生成 Memory、新会话召回和 MCP 记忆读写。
status: community
---

# ZCode

`community`

本集成支持[开源 ZCode CLI](https://github.com/zai-org/ZCode)。官方 Windows 桌面版 3.14.3 已用真实
PowerContext Server 验证普通提示词采集、自动生成 Memory、新会话召回及 MCP Memory 读写。
本轮宿主模型为 GLM-5.3，Server Generation 为 `openai-chat:glm-5.3-flash`。
同一版本也已验证 Handoff 的准备、临时读取、提交和新会话读取，以及本地 Bearer 鉴权与断服期间普通对话。
开源 CLI 还通过 SSH 端口转发验证了跨机器 HTTPS 连接。远端端口的直接 HTTPS 接入及其他官方版本尚未验证。
开源 CLI 0.16.9 另已在真实本地 Server 上验证普通提示词自动采集、真实模型定时生成带 Source 引用的 Memory，
以及全新会话召回。桌面版 3.14.3 另已通过 Scope 绑定、只读状态诊断、Memory citation 冲突、
准确 Handoff revision 的 Receipt/Outcome 关联及候选版本授权验收。
仓库提供[可重复 CLI 验收](https://github.com/oceanbase/powercontext/tree/master/integrations/zcode/acceptance)
与人工桌面步骤；可控模型、真实模型和桌面证据分别记录，单个场景通过不代表整套验收完成。

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
`incompatible_processing_configuration: Processing ownership differs from the migrated declaration`。这时先用新配置执行只读
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
`NODE_EXTRA_CA_CERTS`，Node doctor probe 也读取这个变量；Python API 核验可使用 `SSL_CERT_FILE`。均指向该 CA 证书，保持证书验证开启。
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

该命令分别检查 ZCode CLI 或 Windows 桌面版、插件注册、Hook 文件及 Node 语法、MCP 声明、Server readiness、受保护 API 与 Scope 解析。
`ok: true` 只证明这些静态和只读检查通过，不证明已运行的 ZCode 进程加载了新配置，也不证明 Scope
解析、上下文注入或 MCP 操作已经发生。完全退出并重启 ZCode，再在新会话里检查 PowerContext 工具是否出现。

| 失败项 | 检查方向 |
| --- | --- |
| `zcode` | 确认 CLI 在 `PATH`、`ZCODE_CLI_BIN` 指向构建文件，或官方桌面版安装在可检测路径。 |
| `plugin` | 检查 `plugins.dirs` 中的 PowerContext 路径，以及插件的 `.zcode-plugin/plugin.json`。 |
| `hooks` | 检查 `node --version`、`hooks/hooks.json` 和 `hooks/user_prompt_submit.mjs`。 |
| `mcp` | 检查安装后的 `.mcp.json` 与 `powercontext.json` 是否指向同一 Server，鉴权占位符是否匹配。 |
| `server` | 启动 Server，核对监听地址及 `/health/ready`；这个检查不执行 Memory 读写。 |
| `protected_api` / `scope_probe` | 检查启动诊断进程的鉴权、CA、Scope 权限与显式 Scope；不要关闭证书校验。 |
| `runtime` | 检查实际 data 路径、时间、配置一致性与各阶段；缺失记录不表示当前宿主加载成功。 |

## 查询 Hook 运行观察

Hook 将无正文的观察写入宿主提供的 `ZCODE_PLUGIN_DATA/runtime`。本轮绑定元数据包含 `status_script` 和
`plugin_data_dir`，请使用实际值；普通模型工具不保证继承 Hook 专用环境变量。外部 PowerShell 示例：

```powershell
node '<已安装的 status_script>' --cwd (Get-Location).Path --session-id '<精确 session ID>' --data-dir '<实际 plugin_data_dir>'
# 当前工作区的最近历史，不代表当前窗口：
node '<已安装的 status_script>' --cwd (Get-Location).Path --latest --data-dir '<实际 plugin_data_dir>'
powercontext doctor zcode --runtime-data-dir '<实际 plugin_data_dir>' --prepare --json
```

`powercontext.zcode.runtime-status.v1` 区分 `observed`、`not_observed`、`configuration_mismatch` 和 `invalid_state`。
未观察到记录是合法查询结果；参数错误、存储问题或损坏记录退出非零。`--latest` 查询工作区历史，不能冒充当前会话。
匹配记录分别显示 Scope、时间、prepare、capture 和 context output。超过五分钟标 `stale`；中断尝试标 `incomplete`，
二者都不证明当前运行状态。写请求发出后无法确认结果时保留 `unknown`。

`emitted` 只表示 Hook 本地写出了 additionalContext JSON；宿主或模型实际接收仍须查输入证据。
`accepted` 要求匹配的 Source receipt，不证明 Memory 已生成。关闭采集、敏感内容、超长 Source 和 Scope 未解析
分别给出原因；prepare 失败不会覆盖 capture 成功。

Doctor 使用执行诊断进程的 endpoint、CA 和鉴权配置。`--prepare` 用固定查询执行只读 prepare，不采集 Source 或写 Handoff。
readiness 成功但受保护 API 返回 401/403 时，独立报告访问失败。`mcp: configured` 是安装声明；
`mcp_session: not_observed` 不声称当前宿主已加载工具。缺失、过期或未完成的可选历史不会使其他成功的安装/连接检查失败，
报告中仍保留 skipped。当前探测成功也不会把历史阶段失败改成成功。

记录不包含提示词、召回正文、模型回答、原始 session/路径或凭据。endpoint/profile/session/workspace fingerprint
用于本地隔离，不是权限控制。每次尝试使用独立原子文件，按开始时间选最近记录，旧请求晚完成不会覆盖新请求。
查询或容量维护保留 64 条已完成记录，保留未完成尝试；最多枚举 256 个观察文件或 512 个目录项，超过后报告容量问题，
包括遗留临时文件。单条读取上限 64 KiB。data 缺失或不可写仅使观察降级，正常 prepare/capture 继续。
清理不会删除未知格式或其他文件；重试诊断前检查目录权限和存储容量。

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

`UserPromptSubmit` 和 `SessionStart` 的各次请求共用 4 秒内部网络预算，单次 HTTP 请求上限为 3 秒，
宿主进程超时为 5 秒；总预算和单次请求期限中，先到达者生效。prepare 超时后，若仍有预算便继续尝试提示词采集；
预算耗尽后不再发出新请求。Stop 仍采用下文所述的独立短预算。

## 会话生命周期与可选边界处理

`SessionStart` 解析 Scope 并记录事件。`startup`/`clear` 不额外泛化召回或采集 Source，首条普通提示词仍进行任务相关 prepare。
`resume`/`compact` 可用固定查询“Current project decisions, constraints, and outstanding work”准备最多 8000 字节上下文。
历史仍不可信；泛化查询不保证完整恢复任务，也不会取消下一条提示词的 prepare。

已测试的开源 CLI 实际发出了 startup/resume，resume 上下文进入了模型输入；执行 `/compact` 后却**没有发出**
SessionStart compact。公开类型包含事件不等于宿主会触发。clear/compact payload 已通过本地处理测试，
真实宿主触发仍属未支持或未验证。官方 Windows 桌面版 3.14.3 执行 `/compact` 后也未发出 SessionStart(compact)。
桌面版 resume 实际发出事件，分别记录了 empty 和 ready；ready 分支输出上下文且不额外采集 Source。
开启后的桌面 Stop 在 778ms 超时后保留 unknown 跟踪和 pause；这证明有界处理，不代表处理已完成。

`boundary_flush` 默认 `false`。开启表示希望 Stop 请求 Memory 处理，可能调用 Server Generation 并产生模型费用；
开关不配置 Generation，也不保证生成新 Memory：

```powershell
$env:POWERCONTEXT_ZCODE_BOUNDARY_FLUSH = 'true'
powercontext setup zcode --source 'C:\path\to\powercontext'
# 完全退出宿主，再从同一终端启动；保留匹配的 endpoint、鉴权与 CA 配置。
```

Setup 保存该选项，升级时省略设置会保留原值。启动环境优先于保存值，false 别名与 capture 相同：`0`、`false`、`no`、`off`。
要关闭并保存，设置为 `false` 后重新 setup。省略 `--capture-prompts` 也会保留已保存的采集偏好。
刷新受管理插件副本或关闭边界开关不会删除宿主 data 中的 pending。

有效 Source receipt 保存为无正文 pending，按 Scope/session/profile/endpoint 隔离。workspace fingerprint 记录来源，
同 session 的 claim/pause 在工作区变化后仍覆盖相同 Scope。Stop 再解析当前 Scope，每次最多调用一次
`POST /v1/memory/flush`，请求只包含 `scope_id`；本地目标 position 不变成虚构的 API 字段。其他 Scope 保留 pending。

Stop 内部总预算 1000ms，网络使用前 800ms，宿主 timeout 为 1500ms。五秒 claim 窗口阻止并发/重复发送，窗口最后一秒延后处理。
Stop 不阻断或续跑模型，不总结回答，不创建 Handoff/Receipt/TaskOutcome，也不在本次边界重试写入。
取消或宿主退出时不保证处理完成。

`status` 增加 `powercontext.zcode.pending-status.v1`，显示 receipt 数量、目标 position、tracking 问题和未知 flush 暂停。
`cursor_reached` 仅证明合法处理 cursor 覆盖本次选中 receipt 快照；Memory 与实际召回须单独回读。
并发新增的更大 position 保留；idle/未追平时也保留。未知 capture 不伪造 position，并暂停受影响 Scope 的自动跟踪处理。

发出 flush **之前**先完整发布不确定性标记。发布后，超时、断连、无效响应或进程中止都会保留 pause，claim 过期不会解除它。
当前公共契约无法完整确认在途工作，因此不自动重试；scheduler 和普通任务可以继续。
检查 Server 后，用户明确接受重复未知 flush 的风险时，使用本轮元数据中的 `pending_script`：

```powershell
node '<已安装的 pending_script>' resume-flush --cwd (Get-Location).Path --session-id '<精确 session ID>' `
  --data-dir '<实际 plugin_data_dir>' --scope-id '<当前精确 Scope ID>' --accept-unknown-outcome
```

该命令核对当前 Scope，仅解除对应 flush pause，不发 flush、不丢弃 receipt，也不清未知 capture 或 tracking-incomplete 标记。
`claim_busy` 表示等待当前 claim 窗口后再执行这项已授权控制；后续开启的 Stop 才能处理保留的 receipt。Skill 不自动解除暂停。

pause 文件在发送 flush 前完整写入并原子发布，不覆盖已有 pause。写入中断留下的残缺 pause 仍保持暂停。
保留的 receipt 能确认其精确 Server/profile/session 和 Scope 时，同一显式恢复命令可以解除暂停，同时保留 receipt。
无法通过匹配 receipt 确认归属的不可读 pause 会被拒绝恢复，不会自动清除。

Pending 最多 256 条 receipt/tracking、512 个目录项，单条读取最多 16 KiB。达到上限时保留未确认记录、报告 tracking incomplete，
停止自动 flush；Source capture 仍继续。采集前保存 guard，接受后才能转换成 receipt。过期的已知版本 claim 文件可清理，
未知格式和未确认记录保留。查询只维护本地派生状态；data 缺失或不可写使边界跟踪降级，不阻断普通 recall/capture。

## 主动操作的 Scope 与工作流

普通提示词 Hook 会提供本轮的 Scope、session 和已安装 Scope 脚本路径，另行标记为 current-request binding metadata。
即使历史召回为空，这份元数据也可用于主动操作。它来自本轮绑定解析，不是旧 Memory；不要从历史内容提取 session 或 Scope。

在模型主动调用 Memory、Handoff 或候选工具前，使用该脚本核对当前绑定。外部 PowerShell 可这样执行：

```powershell
$plugin = Join-Path $HOME '.zcode/cli/plugins/powercontext'
node (Join-Path $plugin 'scripts/scope.mjs') resolve --cwd (Get-Location).Path --session-id '<当前精确 session ID>'
```

普通终端没有 session 时可省略参数，但输出 `session_key_used: false` 只证明 workspace 解析，不证明当前会话不存在更高优先级的
session binding。需要会话内写入时，取得真实身份或设置已有的显式 `POWERCONTEXT_ZCODE_SCOPE_ID`。脚本与 Hook 使用同一保存的 endpoint。

明确要求改变当前 checkout 的绑定时，使用 `bind --scope-id <精确 ID>` 或 `unbind`；仍传入相同 `--cwd` 和 session。
脚本只改变 workspace binding，再解析当前 Scope。显式 Scope/session binding 可遮盖该修改，结果会分别显示绑定结果和实际 Scope；
不会自动删除其他 binding。绑定已成功而后续解析失败时，输出仍保留成功的绑定结果；重新解析后才能宣称会话 Scope 已切换。
远程工作区模式不支持本地绑定写入，并要求显式 Scope。

插件 Skill 按需加载三份指导：Scope/Memory、Work Handoff、候选审核。普通交接返回临时 carrier；只有明确要求持久里程碑才提交。
接续后以精确 prepared/revision 目标确认接收；任务完成或中断记录保留真实 Receipt 引用和检查结果。
Memory 修改使用当前 citation，候选决策使用当前 expected_version；版本冲突后重读，不自动批准变化后的内容。
仅有工具配置不代表工具已加载；缺失 MCP 工具时报告未完成，不用 HTTP/shell 替代领域操作。

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

官方 Windows 桌面版 3.14.3 已在隔离 Scope 中完成上述链路，宿主使用 GLM-5.3，Server Generation 使用
`openai-chat:glm-5.3-flash`。公开读回确认 Memory 引用了 ZCode Hook 采集的 Source；全新会话的 Hook
记录显示 prepare ready、上下文已输出，用户报告回答了未在新问题中出现的准确代号。

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
仓库的 ZCode acceptance CI 在 Windows 上构建固定提交的开源 CLI，运行完整 Node 测试和两轮
真实 CLI + PowerContext Server 的可控模型核心验收；PR、master 更新和每周检查都会触发。
构建版本、逐场景摘要和选定证据随 Actions artifact 保留 14 天；详细范围与升级步骤见
[可重复 CLI 验收](https://github.com/oceanbase/powercontext/blob/master/integrations/zcode/acceptance/README.md#continuous-integration)。
官方 Windows 桌面版 3.14.3 已分别连接本地无鉴权与 Bearer 鉴权 Server：MCP `list_scopes` 成功，
普通提示词经 Hook 形成可读回的 Source。断服期间普通对话仍能回答；本轮隔离验收中，Server 重启后
同一任务的原生 MCP 调用返回 `Session not found`；新建任务后原生 MCP 恢复，无需重启程序。
这不证明原任务恢复或自动重连。Handoff 已完成
`handoff_current_work` → `continue_handoff`（prepared）→
`commit_handoff` → 全新会话 `continue_handoff`（latest），提交的 revision 1 能从 Server 读回。
其他已实测工具包括 `get_scope`、`list_memory_entries`、`capture_content_source`、
`list_candidates` 和 `list_dream_runs`；后两者返回合法的空列表。
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

Scope ID 是最多 256 个 Unicode 字符的非空白不透明字符串。解析、pending receipt 和运行观察均保留原值，
包括标点、Unicode 字符和首尾空白。

## 排查 MCP 工具与自动 Hook

PowerContext 工具未出现在会话里时，先完全退出并重启 ZCode，再查 `plugins.dirs`、插件目录和 `.mcp.json`。
工具已出现但调用失败时，检查 MCP 结果中的 Scope、鉴权、Server URL 和 HTTP 错误；不要把模型的文字
回答当成工具结果。`doctor zcode` 核对安装声明并执行只读连接与 Scope 探测，不会发起
`search_memory` 或 `remember_memory`，也不检查当前 ZCode 会话的原生 MCP 工具清单。
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

## 把提示词时上下文交给 gateway

ZCode 的模型请求经过 PowerContext gateway 时，gateway 为每一轮准备并注入上下文；设置 `PC_GATEWAY_SEDIMENT=on`
后，gateway 也会采集这一轮。Core 检查 gateway 转发的每个请求，在任何模式下都拒绝不是 gateway 注入的
prepared-context 块。插件的提示词 Hook 会在提示词之前加入这样的块，因此 Core 以 HTTP 422 拒绝请求，
gateway 返回 HTTP 503 `gate_paused`，ZCode 重试同一请求（默认在几分钟内重试十次），模型收不到这一轮。
ZCode 直接调用模型时，请保留插件的默认设置。

在较长的工具循环中，ZCode 会把自己的提醒作为用户消息放在工具结果之后，
例如模型连续三次发起相同调用，或连续十条 assistant 消息未使用 TodoWrite 时。
gateway 选择这一轮的提示词时会跳过这些提醒：它为提示词准备上下文并注入到提示词中，
在工具循环的每一步重放，并把提示词采集为这一轮的文本。它把提醒原样转发给模型。

以下步骤已用开源 ZCode CLI 0.16.9、真实的 Server 和 gateway，以及 fixture 模型实际运行过。

### 让 Core 运行在非 enforcing 模式

ZCode 在 Responses 路由上不发送 `store: false`，它的 provider 配置也无法加上这个字段。
enforcing 模式的 Core 要求 Responses 请求带 `store: false`，因此会以 `provider_state_unsupported` 暂缓每个
ZCode 请求，gateway 返回 HTTP 400，这一轮在模型收到请求前失败。请把 Core 的推理保护设为 `disabled`、
`shadow` 或 `advisory` 模式，并为 `PC_GATEWAY_UPSTREAM_PROFILE` 指定的 profile（未设置时为 `default`）
配置目标。任何模式下，Core 都会暂缓目标未知的检查。例如：

```bash
export POWERCONTEXT_SERVER_INFERENCE_PROTECTION='{
  "mode": "advisory",
  "targets": {
    "default": {
      "endpoint": "https://models.example/v1/responses",
      "model": "approved-model",
      "protocol": "openai-responses"
    }
  }
}'
```

这几种模式都不会因隐私规则阻止交付。Core 报告不完整的隐私覆盖；只有 gateway 启动时发现 Core 不是 enforcing，
它才接受这种覆盖，因此修改模式后请重启 gateway。选择模式前请阅读
[上下文复用与最终准入](../operate/context-protection.md)和[推理隐私保护](../operate/inference-protection.md)。

### 让 ZCode 指向 gateway

ZCode 0.16.9 把 provider 保存在 `~/.zcode/v2/provider_config.json` 中。它只会把
`~/.zcode/cli/config.json` 中的 provider 和默认模型复制到该文件一次，所以请在该文件中添加 gateway。
添加一条 provider 规则，使用 `openai-responses` API 指向 gateway 的 `/gateway/v1`，
把 gateway key 用作它的 API key，并把插件的 Scope 作为静态请求头；同时添加一条模型规则，并选择该模型：

```json
{
  "schemaVersion": 1,
  "config": {
    "providerConfigRules": {
      "providerRules": [
        {
          "providerId": "powercontext-gateway",
          "config": {
            "group": "standard-personal",
            "access": { "type": "api-key", "apiKey": "<PC_GATEWAY_API_KEY 的值>" },
            "api": {
              "type": "openai-responses",
              "baseUrl": "http://127.0.0.1:8787/gateway/v1",
              "headers": { "X-PowerContext-Scope-ID": "<插件的 Scope ID>" }
            },
            "personalModelIds": ["approved-model"],
            "modelOrder": ["approved-model"]
          }
        }
      ]
    },
    "modelConfigRules": {
      "providerModelRules": [
        {
          "providerId": "powercontext-gateway",
          "modelId": "approved-model",
          "config": { "properties": { "contextWindow": 128000 } }
        }
      ],
      "manualProviderModelRules": []
    },
    "defaultModelSelection": {
      "providerId": "powercontext-gateway",
      "modelId": "approved-model",
      "options": { "reasoningLevel": "high" }
    }
  }
}
```

请保留该文件中已有的其他规则。ZCode 把 API key 保存在这个文件中，请妥善保管。
请使用 Responses API。ZCode 总是发送流式请求。在 Responses 路由上，gateway 以非流式方式调用模型，
再把检查过的响应以流的形式回放给 ZCode，因此这一轮能完成并被采集。

gateway 接受的模型请求最多为 1 MiB，按注入上下文并加入成本指引之后的大小计算，配置无法提高这个上限；
见 [Gateway 字节容量](../operate/deploy-server.md#gateway-字节容量)。到达时就超过上限的请求以 HTTP 413 失败，
因注入或成本指引而超过上限的请求在调用模型之前以 HTTP 403 `request_payload_too_large` 失败。
ZCode 对这两种失败的反应没有观察过。
ZCode 在每个请求中发送完整历史，因此长会话会达到这个上限，除非 ZCode 先压缩会话。
ZCode 0.16.9 在会话的 token 数达到 `contextWindow` 减 34000（21000 的输出预留和 13000 的缓冲）时压缩，
token 数按模型报告的输入 token 加上对较新消息的估算计算。
示例中的 `128000` 让 ZCode 在约 94000 个 token 时压缩；按每个 token 约四个 UTF-8 字节、另加 JSON 开销计算，
远低于 1 MiB。在录制的运行中，带有 ZCode 79 个工具的第一个请求约 195 KB，每个工具步骤增加约 1.3 KB，
gateway 增加约 1.2 KB。这个值是根据 ZCode 的源码和这些大小推算的；没有实际运行接近上限的会话。

`X-PowerContext-Scope-ID` 选择 gateway 准备、检查和采集所用的 Scope。
ZCode 在每个会话的每个请求中都发送 provider 的 `headers`，
因此该请求头的 Scope 在每个 workspace 中都生效，而不是插件为每个 workspace 解析的 Scope。
请把它设为 `node ~/.zcode/cli/plugins/powercontext/scripts/scope.mjs resolve --cwd /absolute/project`
输出的 `scope_id`，并在启动 ZCode 的环境中把 `POWERCONTEXT_ZCODE_SCOPE_ID` 设为同一个 Scope，
让 Hook 和 Scope 脚本也选择它。setup 不保存这个变量。也可以为每个项目添加一个 provider。
未发送该请求头时，gateway 使用 `PC_GATEWAY_DEFAULT_SCOPE`（未设置时为 `gateway:default`）。

在组合部署（`PC_GATEWAY_CORE_UDS`）中，gateway 用客户端自己的凭据把 Core API 和 MCP 请求转发给 Core，
因此插件的 Server URL（Hook 和 MCP 工具共用）可以是 gateway 的地址。

### 关闭插件的提示词时召回和采集

在启动 ZCode 的环境中关闭插件的提示词时召回和提示词采集：

```bash
export POWERCONTEXT_ZCODE_PROMPT_RECALL=false
export POWERCONTEXT_ZCODE_CAPTURE_PROMPTS=false
```

```powershell
$env:POWERCONTEXT_ZCODE_PROMPT_RECALL = 'false'
$env:POWERCONTEXT_ZCODE_CAPTURE_PROMPTS = 'false'
```

这些变量已在 CLI 上验证；ZCode 桌面应用是否把它们传给 Hook 没有验证。

关闭召回后，提示词 Hook 不发送准备请求，也不加入任何上下文，包括当前请求的绑定元数据；
`SessionStart` 不发送任何请求：它不解析 Scope，恢复和压缩时也不恢复上下文。
没有绑定元数据时，Skill 在执行主动操作前按[主动操作的 Scope 与工作流](#主动操作的-scope-与工作流)
用 Scope 脚本解析 Scope。采集开启时，Hook 仍会采集提示词；两项都关闭时，它不再为提示词发送任何请求。
MCP 工具、Scope 绑定、诊断和可选的 Stop 处理沿用各自的设置。setup 不保存
`POWERCONTEXT_ZCODE_PROMPT_RECALL`；它接受与采集开关相同的 false 取值。

修改后请重启 ZCode。已有会话可以在 gateway 后继续：ZCode 不会在之后的轮次中再次发送某一轮的 Hook 上下文，
因此恢复的会话不带有不是 gateway 准备的块。

### 配置 gateway

- 把 `PC_GATEWAY_UPSTREAM_URL_RESPONSES` 和 `PC_GATEWAY_UPSTREAM_API_KEY_RESPONSES` 设为 target 的 `endpoint` 和
  provider key。未设置 URL 时，Responses 路由返回 HTTP 501 `responses_route_disabled`。
- 把 `PC_GATEWAY_API_KEY` 设为 ZCode provider 中的 key。监听非回环地址时必须设置。
- 设置 `PC_GATEWAY_SEDIMENT=on`；Core 启用鉴权时还要设置 `PC_GATEWAY_CORE_TOKEN`。
- 未发送 `X-PowerContext-Scope-ID` 时，gateway 在 `PC_GATEWAY_DEFAULT_SCOPE` 指定的同一个 Scope 中准备和采集所有会话。
  这种情况下，请把它设为插件的 Scope。
- 请以组合部署（`PC_GATEWAY_CORE_UDS`）运行 gateway，它默认会向 ZCode 的请求注入上下文。
  ZCode 把会话 ID 作为 `x-session-id` 发送，gateway 不读取这个请求头；而 provider 的 `headers` 对每个会话都相同，
  因此 gateway 改为从请求本身推导会话。ZCode 在每个请求中发送完整历史，不使用 `previous_response_id` 或
  `conversation`，开头是它的 system 消息，第一条用户消息是它的 Skill 列表。
  因此，gateway 每条用户消息只准备一次，并把该上下文重放给工具循环步骤。
- 推导出的 ID 仅在当前 gateway 进程内用于重放准备结果。
  system 消息和 Skill 列表相同的会话共用同一个 ID。
  gateway 仍会采集每个完成的回合。没有会话头时，采集没有在 gateway 重启后保持不变的会话标识，也无法使用前缀提取。
- ZCode 把压缩请求发往同一个 provider。该请求带有会话历史，并把 ZCode 的摘要指令作为最后一条用户消息，
  而插件的 Hook 无法把它标记为内部请求。因此，gateway 把它当作一轮：为该指令准备上下文；
  设置 `PC_GATEWAY_SEDIMENT=on` 后，还会把该指令和摘要作为一轮采集。压缩之后，
  ZCode 在下一条提示词之前以用户消息发送摘要，gateway 照常为该提示词准备上下文。
- ZCode 在 `x-zcode-session-type` 中把每个模型请求标记为 `main`、`subagent` 或 `other`；
  启用标题生成的客户端还会向会话的模型发送单独的标题请求。gateway 不读取这个请求头。
  subagent 请求或标题请求的开头与主会话不同，因此 gateway 为它推导出单独的会话，并把它当作一轮准备和采集。
  这一点来自阅读 ZCode 和 gateway 的源码，没有测试；上面的无头 CLI 运行没有发送这两种请求。
- 独立部署的 gateway（`PC_GATEWAY_CORE_BASE`）只有在 `PC_GATEWAY_INJECTION=on` 时才注入。
  不要在 provider 的 `headers` 中放固定的 `X-PowerContext-Conversation-ID`，否则 gateway 会把所有会话当作同一个会话。

参见[网关上下文注入](../operate/deploy-server.md#网关上下文注入)和[网关认证边界](../operate/deploy-server.md#网关认证边界)。

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
| `POWERCONTEXT_ZCODE_PROMPT_RECALL` | `true` | gateway 注入上下文时设为 `false`，停止提示词时召回；参见[把提示词时上下文交给 gateway](#把提示词时上下文交给-gateway)。 |
| `POWERCONTEXT_ZCODE_BOUNDARY_FLUSH` | 保存值，默认 `false` | 开启有预算的 Stop Memory 处理；setup 保存显式环境值。 |
| `POWERCONTEXT_ZCODE_AUTHORIZATION` | 未设置 | Hook 与 MCP 使用的完整 `Bearer <token>` header。 |

Server URL、非环回明文 HTTP 同意和默认采集设置由 `setup zcode` 保存到插件的 `powercontext.json`。
修改这些安装参数后重新运行 setup 并重启 ZCode。`POWERCONTEXT_ZCODE_SERVER_URL` 只在插件没有保存
Server URL 时作为 Hook 的回退值，不能用于修改正常安装的 MCP 端点。

## 卸载

从 `~/.zcode/cli/config.json` 的 `plugins.dirs` 中只移除 PowerContext 路径；确认
`~/.zcode/cli/plugins/powercontext` 中存在 `.powercontext-owned` 后，再删除该受管理目录。
最后完全退出并重启 ZCode。卸载插件不会删除 Server 数据或 ZCode 模型配置。

## 自动输入隐私

本宿主的捕获默认值、关闭方式、凭据识别局限及独立只读查询行为见[自动 prompt 隐私策略](index.md#自动-prompt-隐私策略)。识别到凭据的输入会跳过捕获，已接受的 Source 正文和事件身份不被改写。
