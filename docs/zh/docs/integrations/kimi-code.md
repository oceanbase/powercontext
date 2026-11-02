---
title: Kimi Code
description: 通过 Kimi Code 原生 MCP 接入项目记忆、任务交接和经过审核的 Experience/Skill 工作流。
status: community
---

# Kimi Code

本接入为 experimental，支持 minimal、recommended 和 full profile。默认 minimal 提供记忆读写、自动 Source 采集和上下文注入。可以显式共用一个 Scope，也可以通过工作区绑定为不同工作目录选择各自的 Scope。

## 安装

安装 Kimi Code 和 Node 24+，从与插件相同的 PowerContext checkout 安装 CLI/Server 并启动 Server：

```text
uv pip install -e ".[cli,server]"
powercontext server run --env-file .env
```

Server 开启鉴权时，先设置 POWERCONTEXT_KIMI_TOKEN 再运行 setup，才能为原生 MCP 配置鉴权；无鉴权 Server 保持变量未设置。已有 endpoint 保留既定鉴权模式，缺失运行时 token 会报错。

在另一终端，通过 Server、CLI 或 Dashboard 创建或选择已有 Scope，然后执行：

```text
powercontext setup kimi-code --source /path/to/powercontext --server-url http://127.0.0.1:8000 --scope-id <已有 Scope ID>
powercontext doctor kimi-code
kimi
```

source 仅支持本地 checkout 或其中未配置的插件目录，不能将已配置的受管理副本当作更新源。Kimi 和 Node 需在 PATH；KIMI_CLI_BIN 可指向真实二进制或构建后的 main.mjs，用于诊断和验收。KIMI_CODE_HOME 指定宿主目录，默认 ~/.kimi-code。

安装器将副本放到 <Kimi home>/plugins/managed/powercontext，注册到 plugins/installed.json；保留模型配置、其他插件和已有插件/MCP 禁用状态，发布失败时恢复原插件与注册记录。回滚本身被拒绝时，会报告原内容的保留路径，需按报错恢复对应目标后再重试。重复 setup 更新插件，省略的 endpoint、Scope 和采集参数保留已安装设置。修改源文件后需要重新安装，宿主运行的是受管理副本。

安装或改配置后开启**新会话**；现有会话可能保留旧 Skill/MCP 配置。插件对用户全局生效，不限当前仓库。setup 不创建 Scope、不修改绑定。支持 HTTPS 和环回 HTTP，拒绝非环回明文 HTTP。

## 鉴权与使用

Server 开启鉴权时，在启动 Kimi 的终端设置原始 token：

```powershell
$env:POWERCONTEXT_KIMI_TOKEN = '<原始 Server token>'
```

Bash 使用 export POWERCONTEXT_KIMI_TOKEN='<原始 Server token>'。Hook 与 MCP 共用此变量，不将 token 写入插件；无鉴权 Server 可以不设置。setup 不替 Kimi 加载环境文件。

让 Kimi 保存一条项目决策，然后在新会话中搜索。插件开放 search_memory、remember_memory 和 get_artifact_revision。当前记忆返回 Atomic Memory Artifact 引用，读回时使用实际返回的 family、artifact_id、revision。自动采集 Source 不等于完成显式保存。

普通文本请求自动尝试准备上下文、采集 Source；混合输入仅提取文本，纯媒体或不支持的输入跳过。过长或敏感内容不采集，也可关闭采集：

```text
powercontext setup kimi-code --source /path/to/powercontext --no-capture-prompts
```

Hook 总预算 4 秒，通过 Kimi 的 message 字段注入。失败时普通对话继续，stderr 输出脱敏诊断。已发送采集请求但丢失响应时，结果为 unknown，不自动重试；独立提交即使文字相同，也分配独立 Source ID。

显式记忆读写不需要 Generation API。自动生成记忆需要 Server 配置 Generation 和定时处理；Source 接受不证明记忆已生成，插件不调用 flush。Kimi 登录凭据不等于 Server 的模型凭据。


## 工作区 Scope 绑定

需要在同一 Kimi home 中按项目选择 Scope 时，以工作区模式安装或更新：

```text
powercontext setup kimi-code --source /path/to/powercontext --workspace-scope
node "<Kimi home>/plugins/managed/powercontext/scripts/scope.mjs" bind --cwd /绝对路径/项目 --scope-id <已有 Scope ID>
node "<Kimi home>/plugins/managed/powercontext/scripts/scope.mjs" resolve --cwd /绝对路径/项目
powercontext doctor kimi-code --cwd /绝对路径/项目 --json
node "<Kimi home>/plugins/managed/powercontext/scripts/scope.mjs" unbind --cwd /绝对路径/项目
```

修改安装模式后开启新 Kimi 会话。显式 `--scope-id` 优先于工作区绑定；`--workspace-scope` 清除显式选择，两者互斥。更新时均省略则保留当前模式；首次安装必须选择其中一种。

Hook、Scope 脚本和 doctor 共用 Server 的解析顺序：显式 Scope、工作区绑定、Server 默认 Scope。绑定目标缺失或不可用时解析失败，客户端不自行回退或创建 Scope。显式 Scope 解析不要求工作区键，该模式下 `scope.mjs resolve` 可以省略 `--cwd`。工作区模式的解析和所有 bind/unbind 操作均要求绝对工作目录。

绑定键使用 integration `kimi-code`、kind `workspace`，external_id 为绝对工作目录经原生真实路径解析后的 SHA-256，不含原始路径。对于已有目录，symlink/junction 别名和文件系统认可的大小写变体共用绑定。POSIX 先跟随符号链接，再解释后续 `..` 分量；Windows 遵循原生路径语义。请使用 Kimi 会话实际提交的工作目录；子目录保留独立身份，不查找 Git 根目录。真实路径解析失败时，使用词法规范化后的绝对路径。请绑定已存在且可访问的目录；解析身份变化后需要重新绑定。本接入不提供 session binding。

bind/unbind 只修改该工作区在 Server 上的绑定。JSON 将 `binding` 写入结果与后续解析分开：绑定保存成功后仍可能被显式 Scope 遮盖（`bound_scope_is_current: false`），或在后续解析失败时继续保留。无法确认的写入为 `unknown`，不自动重试；重新 resolve 成功后才能宣称当前 Scope 已切换。

原生 MCP 操作使用本轮 Hook 的 binding metadata，或实际工作区的 Scope 脚本只读解析结果，不从历史内容推断 Scope。doctor 显示当前 Scope ID 与解析策略（`explicit` 或 `workspace_or_default`），后者不表示已确认具体命中了工作区绑定还是默认值。

## 选择工作流 profile

高级工具需要显式启用。首次安装默认 `minimal`；更新时省略 profile 保留已有选择，旧安装未保存该字段时按 minimal 处理。切换 profile 保留 endpoint、鉴权模式、Scope、采集偏好、其他 Kimi 插件和已有禁用状态。

```text
powercontext setup kimi-code --source /path/to/powercontext --profile recommended
powercontext setup kimi-code --source /path/to/powercontext --profile full
powercontext doctor kimi-code --mcp --json
```

只修改已安装的 profile、不替换插件文件时，运行 `powercontext config agent kimi-code --profile full`。仅更新连接或 Scope 时省略 `--profile`，保留当前原生工具选择。

更新后开启新会话。profile 控制原生 MCP 工具注册：

| Profile | 工具数 | 工作流 |
| --- | --- | --- |
| `minimal` | 3 | 搜索、显式保存和精确 Artifact 读回。 |
| `recommended` | 27 | 记忆盘点/修改、Source 操作、Work Contract、交接、接续、确认接收和任务结果。 |
| `full` | 44 | recommended 工作流，以及 Experience、托管/外部 Skill、候选检查和决策。 |

工具清单从 Server MCP 目录和能力契约生成并检入仓库。较大的 profile 会增加每次模型调用的工具 schema 开销，请按实际工作流启用。这些 profile 不启用可选 Handoff 报告、自动 flush、Scope 管理或 Artifact 发布。需要 Server Generation 或外部 Skill registry 的操作仍依赖相应 Server 能力；选择 profile 不会配置模型或授予执行权限。

路由 Skill 按当前 profile 按需加载工作流参考。用户要求 Work Contract 时，先核对目标、事实、范围和完成条件再写入。普通交接只准备临时 carrier；明确要求持久里程碑才 commit。接续时使用精确 prepared carrier 或已提交 Revision，检查实际状态、能力和授权后确认同一目标，并在 Task Outcome 中保留返回的 Receipt SourceRef。准备/提交的部分失败不算完成交接，也不授权另写一次边界。

full 模式下，按请求提出或生成 Experience/Skill，检查候选，并对已授权的批准/拒绝/修订传入当前 `expected_version`。冲突后先重读变化后的提案，再重新判断授权。pending 候选不等于已批准 Artifact；批准后通过精确 ArtifactRef 读回。外部 Skill 解析/导入使用已检查的 `external_skill_id`、`fingerprint`；导入不授权批准、安装或执行。外部注册由 Server 管理，不表示发现了 Kimi 本地 Skill。

所有操作使用当前 Scope，并以 Kimi 实际原生工具目录中的名称为准；Kimi 可能缩短较长的限定名称。工具缺失、被拒绝或失败时保持未完成，不使用 HTTP/shell 领域写入代替。setup 和 profile 选择不绕过宿主审批。

## 诊断与移除

doctor 只读检查注册/启用状态、Node、MCP/Hook 声明、Server readiness 和 Scope 可访问性，不证明真实 MCP 调用或模型注入已成功。缺失/错误 token 显示 authentication_failed，已禁用的插件/MCP 不会被 setup 自动开启。错误 endpoint 或不可访问的 Scope 需显式修正后开启新会话。

`--mcp` 额外建立有界 MCP transport session，发现 Server 工具（包含分页），核对所选 profile 后关闭 session。`mcp` 表示已安装声明，`mcp_discovery` 表示当前 Server 发现结果；两者均不证明当前 Kimi 窗口已加载工具，也不证明领域操作成功。发现检查不执行领域工具、不采集、不 flush。

使用 Kimi 的 /plugins remove powercontext 移除注册。宿主保留受管理副本；确认注册已移除并检查 .powercontext-owned 标记后，才删除 PowerContext 管理目录。接入不添加全局 Hook 或项目配置。

## 查看 Hook 运行观测

在出现问题的工作区执行：

```text
powercontext doctor kimi-code --runtime --json
# 精确查询某个会话；省略 session-id 时显示该工作区的最近历史：
powercontext doctor kimi-code --runtime --cwd /path/to/workspace --session-id <Kimi session ID> --json
```

`runtime` 显示配置匹配的 Scope、开始时间和 `scope`、`prepare`、`capture`、`injection` 阶段。`prepare: ready` 表示取得有效上下文，`empty` 是正常空结果；`capture: accepted` 只证明 Source 请求被接受，不证明 Memory 已生成。`injection: emitted` 只证明 Hook 输出了历史上下文，不证明宿主或模型接收、采纳了它。失败保留安全的错误码；采集请求已发出但无法确认响应时为 `unknown`，不自动重试。

未找到对应历史显示 `not_observed`，配置改变后显示 `configuration_mismatch`；超过五分钟标 `stale`，未写入完成标记的尝试标 `incomplete`。这些结果不会使其他正常的安装/连接诊断失败。存在记录也不证明当前窗口加载了插件；不带 session-id 的查询只选择工作区历史。损坏、容量超限或不可读的观测存储单独报失败。

观测保存在 `<Kimi home>/powercontext/runtime`，按工作区、会话和配置指纹隔离；配置指纹覆盖 endpoint、Scope、采集偏好与鉴权模式，不包含 token。每次提交使用独立文件，旧请求晚完成不会覆盖较新的尝试；记录不包含提示词、上下文正文、回答、原始工作区路径、session ID、endpoint 或凭据。插件更新保留这些记录。每次写入只枚举有界目录；到达 64 条记录时保留最近 32 条已完成记录及未完成尝试。并发写入可能暂时超过该保留阈值；扫描预算内历史仍可查询，下次写入会清理已完成记录。最多检查 128 个目录项、单条最多读取 4 KiB。未知文件不清理；存储故障使观测降级，现有 prepare/capture 继续。

只读查询脚本无需 Server 或运行时 token，也不会整理、删除记录：

```text
node "<Kimi home>/plugins/managed/powercontext/scripts/status.mjs" --cwd /path/to/workspace --session-id <Kimi session ID>
```

工作区模式下历史保留当时实际解析的 Scope；绑定后来改变时，只读历史不会联网重新核对。当前解析结果查看 doctor 的 `scope`。默认 `doctor` 仍执行当前 Server/Scope 的只读探测；`--runtime` 附加本地历史，不执行 capture 或 flush，也不将当前连接成功解释为历史阶段成功。

## 验证

固定 Kimi 2.1.1、Node 24.21.0 已在 Windows 和 Linux 验证 minimal/recommended/full 原生 MCP 注册、临时和持久跨会话交接、精确接续/Receipt/Task Outcome 读回、Experience 和托管 Skill 提案/批准/读回、候选版本冲突、外部 Skill 指纹解析/导入、工具缺失、宿主 Hook 拒绝，以及 full profile 工作区隔离和显式覆盖。测试使用真实 CLI、Server 和 SQLite，agent 推理由本地模型服务控制，不证明线上模型的工作流路由能力。

Windows 上 Kimi 2.1.1（npm CLI）、Node 24.15.0 已通过真实 CLI + 真实 Server + SQLite 验收，覆盖原生 MCP 保存/读回/搜索、实际模型请求注入、后台 Worker 生成记忆及精确 Source 证据、关闭采集、错误 token 和 Server 停机。该可重复验收的两处推理服务均为受控模型边界。

同一 Windows、Kimi 2.1.1 和 Node 24.15.0 环境还通过了 DeepSeek 官方 OpenAI Chat Completions 接口的人工在线验收（model = deepseek-v4-flash）：真实模型调用原生 MCP 保存、精确读回及新会话搜索；真实 Generation 从 Hook 采集的 Source 生成记忆并保留来源证据；新会话在不调用工具的情况下使用自动注入的记忆回答。测试使用独立 Kimi home、Scope 和 SQLite 数据库。该单次在线验收不属于自动化测试，也不证明模型在任意提示词下都能正确选择工具。该次线上模型验收不覆盖其他版本/平台、HTTPS 入口或 TUI reload。

在 checkout 执行：

```text
node --test integrations/kimi-code/plugins/powercontext/tests/plugin.test.mjs
uv run pytest tests/test_cli_kimi_code.py
```

设置 KIMI_CLI_BIN 指向真实二进制或构建后的 main.mjs，再运行 uv run pytest tests/e2e/test_kimi_code_host.py tests/e2e/test_kimi_code_workflows.py。该 opt-in 验收使用隔离 Kimi home、workspace、Server 数据库与本地推理，不需要线上模型 key，也不修改用户宿主配置。未设置变量时跳过宿主验收，跳过不代表通过。

[Kimi Code acceptance 工作流](https://github.com/oceanbase/powercontext/actions/workflows/kimi-code-acceptance.yml) 固定 Kimi 2.1.1 和 Node 24.21.0，在 PR 和 master 更新时于 Linux、Windows 执行 Hook 测试与真实宿主验收。两处推理服务均受控，不需要线上模型 Key；宿主测试被跳过时 CI 失败。产物仅保留 CLI 版本来源、JUnit 结果和验收摘要，不上传 Kimi profile 或数据库。

## 自动输入隐私

本宿主的捕获默认值、关闭方式、凭据识别局限及独立只读查询行为见[自动 prompt 隐私策略](index.md#自动-prompt-隐私策略)。识别到凭据的输入会跳过捕获，已接受的 Source 正文和事件身份不被改写。
