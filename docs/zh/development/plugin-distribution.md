---
title: 插件架构与分发
description: 共享执行、模板、宿主适配与安装流程。
---

# 插件架构与分发

PowerContext 插件通过已安装的客户端执行领域操作。原生适配器处理宿主事件和权限，共享模板生成包资源。Server 拥有 Scope 和持久化产物。插件复用客户端的 HTTP 执行器和 Python 环境。

## 执行边界

`powercontext.client.integration` 接收操作 ID、参数、连接、绝对截止时间及可选的 Scope 绑定。客户端根据操作契约校验请求和响应。原生 Python Hook 直接调用；TypeScript 和 Hermes 通过 `powercontext-hook` 的串行 JSON Lines worker 接入。Bub、框架适配器和 OpenDAL 直接使用类型化 SDK。

```text
Codex / Claude Code / WorkBuddy -> Shared prompt -> Core
MiniMax ----------------------------------------> Core
TS hosts / Hermes -> JSONL worker ---------------> Core
                                                   |
Bub / framework adapters / OpenDAL ------------> Client SDK -> HTTP API

Native MCP -> host Scope/auth handling -> Server MCP
```

Core 将 Hook 响应限制为 1 MiB，禁止重定向，并执行请求期限。普通 SDK 调用保留调用者的 HTTP 策略，包括大报告下载和显式配置的重定向。

适配器按宿主原生格式返回结果。原生 MCP 调用经过宿主检查和 MCP 客户端后到达 Server MCP。Codex 将工具参数绑定到当前 Scope，并通过原生凭据 helper 提供认证。这些步骤由 Codex 适配器负责，提示词 Hook 则复用共享执行。

自动提示词 Hook 先解析 Scope、准备上下文，再按采集配置保存提示词。只有 Server 确认接收 Source 后才能启动 checkpoint。游标推进、调用次数与共享截止时间约束处理过程。写入结果未知时保留该状态，不自动重试。Source 已接收不代表 Memory 已生成。

共享检查点工具从已确认的 Source 接收推进位置，并在重新抛出错误或取消前记录未知写入结果。Python 适配器使用 `Checkpoints.attempt()`，TypeScript 使用生成的 `Checkpoints.run()` 和 `flushThrough()`。宿主决定检查点时机并维护原生待处理状态。

适配器负责原生事件时机、工具可见性、用户确认、Scope 身份和输出通道。Pi 在生命周期边界处理 checkpoint。OpenClaw 根据会话资格限制工具，MiniMax 读取具名 MCP 端点并应用私有覆盖配置。DSH 使用独立的受限 GET 发现 OpenAPI 文档。

错误分类使用类型化结果，不匹配异常文本。保留拒绝、冲突、非法输入、非法响应和未知写入结果。空上下文是有效结果。自动召回失败时继续宿主工作，非法工具参数绑定则拒绝执行。诊断不包含凭据、提示词、响应正文或堆栈。

`powercontext_integrations.host.HostAdapter` 是 setup、资源生成和 doctor 的公共入口。单宿主 setup 和 `setup select` 调用同一个 `install()`：检查已安装的客户端、解析连接策略、执行原生安装、验证安装结果，最后保存连接配置。验证失败不会保存新的连接。每个宿主只声明原生模块、接受的参数，以及安装器是否已经验证结果。原生安装器保留回滚规则，并在对应的暂存目录或缓存目录调用 `prepare()`。

setup、doctor、配置选择与分发读取选定仓库源码中的同一份 Target 目录。所有目标均提供 `powercontext setup <target>` 和 `powercontext doctor <target>`，包括 Python 包和通用插件。Python 负责安装与诊断主流程，`system.py` 注册命令，`codex.py`、`claude_code.py` 等原生模块提供安装、发现及实际配置；TypeScript 只注册命令，将配置传给 `powercontext-hook --doctor`，再展示结果。

公共诊断检查客户端前提和连接策略。`doctor <target> --server` 与宿主内 `/pc doctor` 共用只读的存活、就绪和上下文 schema 检查。HTTP 503 仍保留依赖状态；存活检查失败时跳过后续探测，输出不包含私有响应文字。运行中的宿主提供当前连接，CLI 读取可发现的配置；无法识别原生配置时明确失败，不猜测端点。安装失败统一使用 `SetupError`，重复的命令与输入错误共用构造。

```text
Repository rules (source / ref)
     Target catalog + templates
          |
          +-- build -> shared renderer -> native package
          |
     HostAdapter (Python)
          +-- setup  -> prerequisites -> connection -> generate/install -> verify -> save
          +-- doctor -> prerequisites + native discovery + transport
          |                  +-- optional Server checks

Host command -> connection -> Client Server checks -> native display
```

## Agent Plugin 基准

- `integrations/agent-plugin/powercontext/` 是可直接维护的基准，包含标准 Skills、工作流引用和 MCP 配置。`integrations/agent-plugin/operations.json` 选择最小公共工具集，参数 schema 和描述来自客户端契约。其他宿主从基准生成，不再各自定义方法论。
- `integrations/distribution/powercontext_integrations/assets/` 保存原生格式模板和绑定。`resources.json` 提供 MCP 字段与注册形式，`tool-bindings.json` 保留已有工具名。Scope 解析、当前工作直接 Handoff、Memory 列表和候选读取遵循相同流程。
- `resources.py` 为 setup 与分发生成同一组资源。DSH 将基准引用注册为运行时 Skill，其他支持 Skill 的宿主生成文件。MCP 的封装、端点路径、schema 元数据和凭据字段仅做原生格式转换；WorkBuddy 合并配置也使用同一生成入口。交互确认与工具可见性由宿主代码控制。
- `integrations/distribution/powercontext_integrations/assets/targets/` 声明源码布局，以及原生事件、处理器、操作、效果和失败行为。操作 ID 来自客户端契约。Hook 声明描述原生绑定，不是可执行的生命周期计划；原生事件注册仍由适配器完成。不另行维护能力清单或源码探测器。
- `scripts/build_agent_distributions.py` 将生成资源叠加到原生适配器并记录文件哈希。除基准包外，Skills、MCP、工具 schema、指引和桥接代码均为生成产物。原生包携带 `tools.generated.json`，SDK 适配器直接读取，避免将重复 schema 嵌入 JavaScript bundle。

```text
Agent Plugin baseline + API contract + Target/templates
                         |
                   Shared generator
                         |
            Generated resources + native adapter
                         |
                 Target-native plugin/package
```

`integrations/distribution/` 提供可独立修改的宿主规则和模板，客户端提供 `powercontext-hook` 与公共服务诊断。Setup 在原生安装前生成资源，并按需刷新安装缓存。提示词 Hook 在召回为空时仍提供已解析 Scope，MCP 调用复用同一绑定，无需执行宿主专属解析命令。重新生成会保留已有 MCP 配置；显式指定新端点时只更新 URL。凭据与用户设置仍由宿主配置流程管理。完成 setup 后重启宿主。

## 开发与分发

先安装 `powercontext[cli]`，确保 PATH 中存在 `uvx`、`npx` 和 `powercontext-hook`，无需安装额外的管理包。离线源码包保留仓库布局，包含 marketplace 清单、分发规则、Agent Plugin 基准和所选宿主源码；`--source` 指向包根目录。Setup 直接从 `--source/--ref` 指定源码的 `integrations/distribution/` 加载 Python 规则。远程源码共用 Git checkout，本地源码直接读取；显式选择远程源码时，仅刷新没有本地修改的受管理 checkout。安装成功后将源码位置与连接、安装位置一并保存。Doctor 复用已保存源码，不重新拉取；也可通过 `--source/--ref` 显式选择其他源码。

客户端只保留源码加载入口、Core Hook 与公共服务诊断，Target、模板、原生安装和发现逻辑留在仓库。`powercontext-hook --doctor` 与默认 CLI doctor 不需要集成源码。仓库开发直接使用当前 checkout：

```bash
make agent-resources
make agent-distributions
uv run python scripts/build_agent_distributions.py --list
powercontext setup pi --source /path/to/powercontext
powercontext setup minimax --source /path/to/powercontext
powercontext setup langchain --source /path/to/powercontext --python /app/.venv/bin/python
powercontext setup agent-plugin --source /path/to/powercontext --destination /app/plugins/powercontext
powercontext doctor langchain --server
```

`agent-resources` 生成本地开发和原生源码加载所需的资源；`agent-distributions` 在 `build/agent-distributions/<target>/` 生成包含资源与适配器的独立包。直接使用宿主插件命令加载仓库前，先生成资源。构建器保留非自身所有或已修改的输出文件。修改模板后重新构建即可，无需逐个修改插件副本。

生成的插件或集成包通过对应宿主加载，共享执行使用已安装的客户端。仓库规则复用已有客户端操作和 worker 协议时，可以独立修改。需要新增客户端能力时，也需要配套兼容的客户端版本。

Python 包通过 `uvx` 安装到应用解释器，依次选择显式参数、已保存解释器、当前项目的 `.venv`，不会隐式使用 CLI 工具环境。MiniMax 使用原生插件目录，通过 `mcode plugin list` 验证发现；通用插件需要指定目标目录，并在加载它的 Agent 中注册。setup 记录安装位置，后续 doctor 直接复用。多目标选择包含多个目录型目标时，分别安装到 `<destination>/<target>`。

目录命令直接报告构建使用的配置，包括语言和 Hook 操作。它描述已声明的原生绑定，不表示服务当前可用，也不穷举动态授权后的工具。安装状态使用 `powercontext doctor integrations` 检查；当前权限以宿主实际工具目录为准。

新增宿主时，实现原生事件、确认和输出适配器，增加 Target 配置并绑定原生格式。复用基准工具集和工作流，在适配边界转换宿主返回封装，不单独维护方法论。

## 改动与修复归属

根据需要改变的行为选择要修改的源码：

```text
Change or fix
+-- Shared workflow / Skill -> Agent Plugin baseline -> regenerate
+-- Execution / validation / deadline -> Core / Client -> client release
+-- Native event / permission / auth / output -> host adapter -> plugin update
`-- Install / discovery / format / argument wiring -> distribution -> regenerate/setup

Core already expresses the required behavior?
+-- No  -> implement it at the boundary above
`-- Yes -> does the adapter pass the needed inputs and handle the result?
           +-- No  -> repair Target / adapter wiring
           `-- Yes -> confirm with an observable behavior test
```

共享执行器约束单次请求超时和总截止时间。Codex、Claude Code 和 WorkBuddy 默认使用三秒单次请求超时和六秒提示词总预算。Pi、DSH 和 OpenCode 的 Target 配置必须将已解析的 Server URL 传给安装器，让凭据绑定到运行时端点。Codex 适配器负责其原生 MCP 凭据 helper 和认证诊断，helper 使用已安装客户端的 Python 解释器运行。只有宿主正确传入配置并处理结果，共享实现才算覆盖了相应修复。

## 方法与验证

从可观察约束出发：谁选择 Scope、哪个事件拥有写入、什么结果确认完成、哪条边界终止执行。事件、操作、效果、失败策略和传输彼此独立。模型生成内容也必须通过相同操作边界，文本不能授予权限或替代确认结果。

用消融判断边界是否必要：在隔离夹具中去掉校验、截止时间、绑定或未知结果处理，确认对应错误变得可观察，再把该行为保留在现有 Core 或宿主测试中。无需另一套评估流水线、推测的支持表或针对生成实现细节的测试。

分发边界使用隔离环境验证：只安装客户端，在另一个源码 checkout 增加 Target 后通过 setup 选择它，doctor 应复用该源码，客户端版本和 Core 内容保持不变。

执行 `make check` 及受影响的 Python 或原生插件测试。分发测试关注资源完整性、可重复生成和用户配置保留；宿主测试关注事件映射、权限及输出；Core 测试覆盖校验、截止时间和不确定写入。真实模型质量单独测量。
