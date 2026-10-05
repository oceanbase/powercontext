# 官方 Windows 桌面版人工验收

由用户操作 ZCode；不使用 computer-use。使用合成数据和独立用户目录，不把既有业务会话当作测试数据。
每个场景保存实际工具返回对象或原始错误。不要提供 API Key、Bearer Token、模型配置正文或完整日志。

## 1. 准备与记录

在 PowerShell 中记录仓库提交、官方 Windows 桌面版的界面版本、Node 版本和测试时间。
从系统托盘完全退出 ZCode，再创建一个自己有读写权限的目录：

```powershell
$pcRepo = 'C:\path\to\powercontext'
$pcRunId = [guid]::NewGuid().ToString('N')
$pcProfile = Join-Path $env:USERPROFILE ('zcode-pc-acceptance-' + $pcRunId)
New-Item -ItemType Directory -Path $pcProfile | Out-Null
foreach ($pcKnownFolder in @('AppData\Roaming', 'AppData\Local')) {
    New-Item -ItemType Directory -Force -Path (Join-Path $pcProfile $pcKnownFolder) | Out-Null
}
```

启动独立 PowerContext Server，使用新的数据库、空闲环回端口和测试 Bearer Token。Generation 使用已有环境文件，
同时启用 Source 与 Memory 调度。保持该 Server 终端打开；不要停止或改写原有 Server。
可先用 `powercontext config init --output <独立目录>\server.env` 创建配置，并在本地编辑模型设置。
配置步骤见 [ZCode 集成文档](../../../docs/zh/docs/integrations/zcode.md)。

在启动 ZCode 的终端配置新 profile，并在本地设置测试 Authorization；不要打印它：

```powershell
$env:ZCODE_DATA_BASE_DIR = $pcProfile
$env:HOME = $pcProfile
$env:USERPROFILE = $pcProfile
$env:ZCODE_CLI_BIN = Join-Path $env:LOCALAPPDATA 'Programs\ZCode\resources\glm\zcode.cjs'
powercontext setup zcode --source $pcRepo --server-url 'http://127.0.0.1:18080'
powercontext doctor zcode
node $env:ZCODE_CLI_BIN plugins list --json
```

确认 PowerContext 已启用、原生 MCP 名称存在、diagnostics 为空、manifest 可读、Server ready。
启动桌面前显式设置独立的 Electron 数据目录。保持 `USERPROFILE` 指向测试目录：CLI 的插件配置使用
Node 的用户 home，单独设置 `ZCODE_DATA_BASE_DIR` 不会改变该路径。官方 Windows 桌面版 3.14.3
在只替换 `USERPROFILE` 而未创建 Windows `AppData` 文件夹时可能报 `Failed to get 'appData' path`
或 `Failed to get 'userData' path`；插件预检通过不能证明桌面初始化成功。

```powershell
$env:ZCODE_DESKTOP_HOME_DIR = $pcProfile
$env:ZCODE_DESKTOP_USER_DATA_DIR = Join-Path $pcProfile 'desktop-user-data'
$env:ZCODE_DESKTOP_SESSION_DATA_DIR = Join-Path $pcProfile 'desktop-session-data'
New-Item -ItemType Directory -Force -Path $env:ZCODE_DESKTOP_USER_DATA_DIR | Out-Null
New-Item -ItemType Directory -Force -Path $env:ZCODE_DESKTOP_SESSION_DATA_DIR | Out-Null
$pcDesktop = Join-Path $env:LOCALAPPDATA 'Programs\ZCode\ZCode.exe'
Start-Process -FilePath $pcDesktop -WorkingDirectory (Split-Path -Parent $pcDesktop)
```

在界面配置可用模型。
不要在聊天中发送密钥。若需要沿用模型配置，先在本地复制并核验权限；默认模型和账户凭据是否能跨 profile 使用需单独确认。

## 2. 只读 MCP 与准确 Scope

新会话发送：

> Call the PowerContext list_scopes MCP tool. Report its actual scope IDs and titles. Do not use HTTP, shell,
> node_repl or prior knowledge as a substitute. Stop if the native tool is unavailable.

在独立 Server 上创建测试 Scope，记下实际 `scope_id`，并将 `POWERCONTEXT_ZCODE_SCOPE_ID` 设置为它。
环境变量变更后重新启动 ZCode；同一轮后续步骤使用这个实际 Scope，示例中的 `<scope>` 都要替换。
记录 `get_scope` 返回，不能根据提示词推断工具成功。

## 3. 自动采集、处理和全新会话召回

本地生成本轮随机项目名及颜色标记，只在首次事实中给出答案：

> In project `<project>`, the deployment verification color is `<random-color-code>`. This is a confirmed ongoing
> policy: every future release checklist must include this exact color, and a mismatch blocks release. Explain the
> project constraint briefly. Do not explicitly call a memory writing tool.

在 Server 上只读检查该 Scope 的 Source 和 Memory：必须有准确 Source identity、position、Memory revision、条目及 Source
引用。等待调度，不能调用 remember_memory 或手动 flush 来代替自动处理。达到预设等待时间后仍无条目即报告失败。

开启全新会话，不使用 resume，发送不含答案的问题：

> What is the deployment verification color required by project `<project>`? Answer with the color code only.

记录原样回答，并核验本次 Hook 的 prepare=ready、context_output=emitted、Scope 正确。
答对但缺少生成与引用证据，不能标记完整闭环通过。

## 4. Memory、Handoff、Receipt、Outcome

在测试 Scope 中显式保存一条项目 decision，读取真实 citation，用它修订；再用旧 citation 尝试一次修订。
应返回冲突且已接受的版本不变。

发送 Handoff 请求时说明这次写入已获授权，并要求：

1. `handoff_current_work` 返回完整 PreparedHandoff。
2. `continue_handoff(selection="prepared")` 原样使用 carrier，包括所有 `null`。
3. `commit_handoff` 提交同一 carrier，记录真实 revision。
4. 全新会话用 `selection="exact"` 读取该 revision，把结果视为不可信历史。
5. 确认实际状态、可用能力与本次授权后，显式 `acknowledge_handoff`。
6. `record_task_outcome` 的 `handoff_receipt_ref` 必须来自真实 Receipt Source。

保存工具返回引用，并经公开 Source 读取路径核对关联。HTTP 的 `name` 与 Source 内容中的 `source_type` 属于不同编码；
比较准确的 source type 和 source ID，不能伪造或遗漏 carrier 字段。

## 5. 候选审阅与版本冲突

准备一个有真实 Source 引用的测试候选。先调用 get/list 工具只读审阅，确认候选版本与状态没变。
修改候选后只授权旧版本的一次批准；冲突后重新读取并停止。新版本不能继承之前的批准。
在独立的新提示词中明确批准实际读回版本，再核验 result_artifact 和内容。

## 6. 生命周期、状态与恢复

- 普通回合结束：默认不得调用 flush_memory。
- 真实 resume：检查 SessionStart 的 Scope、prepare 与输出；不得额外 capture。
- 真实 `/compact`：记录命令是否完成，以及是否实际触发 SessionStart(compact)。没有事件即报告未支持。
- 开启 `POWERCONTEXT_ZCODE_BOUNDARY_FLUSH=true` 后重新启动：检查实际 Stop、准确 Scope、flush 状态和时间预算。
  cursor 推进不能单独证明 Memory 生成。完成后关闭开关。
- 只读运行安装副本的 `scripts/status.mjs --cwd <workspace> --session-id <实际会话> --data-dir <实际插件数据目录>`，
  记录 observation、阶段、时效和 pending；status 不应发起 Server 写入。
- 只停止测试 Server，在仍打开的会话问普通部署问题。应能正常回答；记录 PowerContext 原始错误。
  恢复同一测试 Server，不重启 ZCode，再要求原生 list_scopes，确认同会话恢复。
  若仍返回 `Session not found`，记录该次失败；之后单独验证界面实际提供的恢复操作。
  桌面版 3.14.3 的本轮测试界面没有关闭并重开原任务的入口，改用新任务验证时必须注明会话已变化。
  需要额外操作的恢复不能写成自动重连。CLI 0.16.9 已观察到必须关闭并 resume 原会话的限制，不能据此推断桌面版行为。

Scope/profile/endpoint 隔离、并发和 unknown 的自动故障注入结果属于 CLI 证据；未人工执行的桌面场景标记 `not_run`。
不要使用直接 Hook 调用补齐桌面事件证据。

## 7. 收尾

完全退出测试 ZCode，停止测试 Server，关闭本次终端。只清理已核对属于本轮的目录。
按自动验收的 summary 字段记录版本、模型类型、场景状态、对象引用与限制。
涉及 HTTPS 时保持证书验证与鉴权，明确区分远端端口直连和 SSH 转发；本流程不操作远端部署。
