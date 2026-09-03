---
title: 排查问题
description: 诊断 PowerContext 安装、Server、数据库和宿主集成问题。
---

# 排查问题

先执行：

```bash
powercontext doctor
```

该命令检查安装包、Server liveness 和 Server readiness；只有所有检查均为 `ok` 时才以状态码 0 退出。
`degraded` 表示仍可使用，但不算完整诊断成功。自动化场景可添加 `--json`，顶层结果和每个检查都会包含
`ok` 与 `status`。可单独检查可选的宿主集成：

```bash
powercontext doctor integrations
powercontext doctor codex
powercontext doctor claude-code
powercontext doctor dsh
powercontext doctor openclaw
powercontext doctor opencode
powercontext doctor pi
powercontext doctor hermes
```

`doctor integrations` 会打印全部一级宿主。CLI 不在 PATH 上时该行是 `missing`，不会让整条命令失败；已安装但异常的
宿主仍会以状态码 1 退出。单宿主命令（如 `doctor codex`）在该 CLI 缺失时仍然失败。

## 安装时无法读取 Git 地址

确认 Git 能够读取仓库：

```bash
git ls-remote https://github.com/oceanbase/powercontext.git HEAD
```

如果失败，请配置 Git 使用的 credential helper 或 SSH key，再重新运行 `uv tool install`。`uv` 使用 Git
凭据配置；PowerContext 不接收或保存仓库凭据。

## 找不到 PowerContext 或宿主 CLI

执行：

```bash
uv tool dir --bin
command -v powercontext
command -v codex
command -v claude
command -v dsh
command -v openclaw
command -v opencode
command -v pi
command -v hermes
```

必要时把 uv tool bin 目录加入 `PATH`。宿主 CLI 不可用时，`powercontext setup codex`、
`powercontext setup claude-code`、`powercontext setup dsh`、`powercontext setup openclaw`、
`powercontext setup opencode`、`powercontext setup pi` 和 `powercontext setup hermes` 都会报告错误，而不会尝试安装。
`powercontext setup select` 只安装你选中的宿主。某个选中宿主未安装时，该行失败，但不会阻塞其余选中项；
未选中的宿主即使已在 `PATH` 上也不会安装。

## 插件缺失或版本不一致

先在不涉及 Server 的情况下确认集成故障：

```bash
powercontext doctor codex
powercontext doctor dsh
powercontext doctor pi
```

使用与工具一致的 ref 重新安装：

```bash
powercontext setup codex --source oceanbase/powercontext --ref <ref>
codex plugin list --json
```

然后开启新的 Codex 会话。如果提示词恢复和采集没有运行，请检查 `/hooks`。

对于 Claude Code，执行：

```bash
powercontext doctor claude-code
powercontext setup claude-code --source oceanbase/powercontext --ref <ref>
claude plugin list --json
```

然后开启新的 Claude Code 会话并检查 `/hooks` 与 `/mcp`。插件清单应只包含一个
`UserPromptSubmit` Hook 和一个 `powercontext` MCP Server。

如果 setup 在创建新的 user scope 对象时失败，它会尝试只删除本次调用创建的插件与 Marketplace 项，
setup 前已有的对象会保留。修正命令报告的 Claude CLI 或仓库错误后，重新执行同一个 setup 命令。

对于 DeepSeek Harness，执行：

```bash
powercontext doctor dsh
powercontext setup dsh --source oceanbase/powercontext --ref <ref>
dsh --profile web --dump-config
```

然后开启新的 DeepSeek Harness 会话，并确认 dump-config 含有 `id: powercontext-dsh`。DSH 插件目录必须包含
`lib/index.js`。

对于 Pi，执行：

```bash
powercontext doctor pi
powercontext setup pi --source oceanbase/powercontext --ref <ref>
pi list
```

然后开启新的 Pi 会话，并确认 `pi list` 列出了 PowerContext package source。

## Server 检查失败

启动服务：

```bash
powercontext server run
```

如果 8000 端口已被占用，请停止冲突进程。若 Server 有意使用其他地址，可在检查时传入 base URL：

```bash
powercontext doctor --server-url http://127.0.0.1:9000
powercontext --server-url http://127.0.0.1:9000 ready
```

随附的 Codex 和 Claude Code 插件以及 Pi package 默认使用 8000 端口。liveness 失败表示进程无法响应健康请求，此时不会继续检查
readiness。HTTP 503 的 `not_ready` 表示 Runtime 或数据库无法接受工作；HTTP 200 的 `degraded` 表示已配置的
推理能力异常，但数据库操作仍然可用。Human 与 JSON 输出都会保留 Server 返回的各项检查状态。

## Server 无法打开数据库

数据库在 Server 启动时创建，而不是在工具安装时创建。先检查 Server 的启动错误，再运行
`powercontext doctor`。

如需指定位置：

```bash
export POWERCONTEXT_HOME=/path/with/write/access
powercontext server run
```

每次启动或诊断该实例时都应使用同一个环境变量。对于文件型 SQLite 数据库，PowerContext 会创建缺失的父
目录。

## OceanBase 因 schema 不兼容而拒绝启动

当前 PowerContext 使用 `utf8mb4_bin` 对不透明 identity column 进行逐字节比较。旧版本创建的数据库可能仍然
使用 `utf8mb4_general_ci` 等不区分大小写的 collation。Server 会在创建任何缺失表之前检查已有 identity
column；发现不兼容时拒绝启动。启动错误会列出每个受影响的 `table.column`、实际 collation 和要求的
collation，但不会包含数据库 URL 或凭据。

不要直接修改这些 column。它们参与主键、外键和索引，而且旧部署可能已经把本应不同的 identity 当作同一个值。
请使用新的空数据库，使旧数据库可以继续用于恢复：

1. 停止 Server 以及所有会写入该数据库的进程。
2. 按现有 OceanBase 备份流程创建并验证一份可恢复的完整备份。
3. 使用 OceanBase `obdumper` 的 CSV 或 SQL 数据模式导出 PowerContext 表数据，且**不要使用 `--ddl`**。
   在迁移验证完成之前，保持导出文件和原数据库不变。请通过获批的 secret 管理流程提供凭据，不要把凭据写入日志
   或文档。
4. 新建一个空的 OceanBase MySQL-mode 数据库，将 `POWERCONTEXT_SERVER_DATABASE_URL` 指向它。启动一次当前
   PowerContext，使其创建使用 `utf8mb4_bin` 的表；恢复数据前再次停止 Server。
5. 使用 OceanBase `obloader` 只把导出的数据导入已经存在的新表，同样**不要使用 `--ddl`**。保持外键检查开启，
   并分别运行下面三层命令。示例使用 CSV；如果导出的是 SQL 数据，请把三个命令中的 `--csv` 全部替换为
   `--sql`。通过获批的 secret 管理流程填写 `<connection-options>`，并让 `<new-database>` 指向第 4 步创建的
   数据库。

   运行命令前，对照导出的表文件和目标数据库中的 `SHOW TABLES`。下面列出的每张已导出表都必须存在于目标数据库；
   如果目标表缺失，请停止恢复，并先使用当前 PowerContext 配置创建该表。只有源导出不包含某张表时，才能从命令中
   删除它。源数据库早于三张 Skill 生命周期表（`pc_skill_packages`、`pc_agent_skill_targets` 和
   `pc_skill_publications`）或七张 `pc_handoff_report_*` 表时，应从第 1 层删除缺失的表；如果导出包含 Handoff
   Report 表但 Handoff Report 已关闭，请在第 4 步临时启用该功能以创建当前表并恢复其数据，验证完成后再恢复
   预期配置。

   第 1 层包含父表和无外键的表：

   ```bash
   obloader <connection-options> -D <new-database> --csv \
     --table 'pc_source_journal_heads,pc_sources,pc_artifacts,pc_source_cursors,pc_connector_checkpoints,pc_source_definition_manifests,pc_external_skill_registrations,pc_skill_packages,pc_agent_skill_targets,pc_skill_publications,pc_model_usage_daily,pc_recall_token_daily,pc_handoff_report_projects,pc_handoff_report_project_revisions,pc_handoff_report_workstreams,pc_handoff_report_workstream_revisions,pc_handoff_report_workspace_bindings,pc_handoff_report_activity_heads,pc_handoff_report_activities' \
     -f <export-directory>
   ```

   第 1 层成功完成后，导入第 2 层中的子表：

   ```bash
   obloader <connection-options> -D <new-database> --csv \
     --table 'pc_artifact_heads,pc_artifact_lineage_sources,pc_artifact_lineage_artifacts,pc_artifact_candidate_versions,pc_memory_entry_versions' \
     -f <export-directory>
   ```

   第 2 层成功完成后，导入第 3 层中剩余的子表：

   ```bash
   obloader <connection-options> -D <new-database> --csv \
     --table 'pc_artifact_candidate_heads,pc_memory_entry_heads' \
     -f <export-directory>
   ```

   每个命令成功完成后才能开始下一层。OBLoader 出现任何错误、bad record 或 conflict record 时，都应判定恢复
   失败。同一层内的表互不引用，因此层内顺序无关。
6. 如果安装中还存在上面未列出的 PowerContext 管理表，则这些已测试的层并未对它们分类。检查其外键约束，将每张
   表放在其所有父表之后；不要把它们加入全表导入命令。
7. 逐表比较源数据库和目标数据库的记录数，检查 identity column collation，并测试仅大小写或重音不同的
   identity。所有检查通过后才能恢复正常流量。在整个回滚窗口内，保留源数据库、已验证的备份和导出文件。

如果旧 collation 曾因 identity 相等而合并记录，重建 schema 无法恢复这些记录。接受新的写入之前，请从权威数据源
修复它们。

## 推理服务 readiness 检查失败

配置 generation 或 embedding 后，Server readiness 会向 provider 发起一次最小化真实请求。这样可以发现只有
实际请求时才能确认的凭据或 endpoint 问题，包括 base URL 遗漏 provider API 前缀。稳定状态包括 `ready`、
`unavailable`、`timeout` 和 `misconfigured`；响应不会包含凭据、provider 响应正文或已配置 URL。

推理检查失败时，overall readiness 为 HTTP 200 的 `degraded`，不会使整个 Server 退出流量。`ready` 和
`misconfigured` 会缓存 300 秒；临时的 `timeout` 和 `unavailable` 会在 30 秒后重试。并发健康请求共用同一次
刷新。修改静态配置后如需立即检查，请重启 Server；否则等待缓存过期。

## Memory 可以显式写入，但采集的提示词没有生成 Memory

显式 Memory 操作不需要模型；把采集的 Source 证据转换为 Memory 则需要。请配置 generation model 及其
provider 凭据，然后启用 scheduler 或显式 flush 对应 scope。查看 Server 当前提供的能力：

```bash
powercontext capabilities
```

`Memory extraction: disabled` 表示 Server 没有 generation model。

## 宿主可见的集成诊断

Codex、Claude Code、DSH、OpenClaw、Pi 和 Hermes 集成都遵循 fail-open：PowerContext 故障不会阻塞宿主任务。
同时，它们会通过宿主支持的通道输出有界、无内容的诊断：

| 宿主 | 诊断通道 | component |
| --- | --- | --- |
| Codex | Hook stdout `systemMessage` | `powercontext.codex.recall` |
| Claude Code | Hook stdout `systemMessage` | `powercontext.claude_code.recall` |
| DSH | 宿主 logger warning | `powercontext.dsh` |
| OpenClaw | 插件 logger warning | `powercontext.openclaw` |
| Pi | 宿主终端 warning | `powercontext.pi` |
| Hermes | Python 宿主 logger warning | `powercontext.hermes` |

例如，传输失败会通过 Hook 顶层的 `systemMessage` 返回；它的值是类似下面的单行、无内容 JSON 事件：

```json
{"systemMessage":"{\"component\":\"powercontext.codex.recall\",\"event\":\"context_prepare\",\"outcome\":\"server_unavailable\",\"recovery\":\"powercontext doctor\"}"}
```

稳定的 outcome 仍然彼此区分：`authentication_failed`、`version_mismatch`、`server_unavailable` 和
`invalid_response`。诊断不会包含 prompt、召回内容、scope、URL、凭据、响应正文或异常文本。同一次调用内的
相同 outcome 会去重，跨 Hook 进程会使用本地状态限流 60 秒；诊断失败不会改变宿主任务结果。

Bub 不包含在本次第一阶段的宿主诊断切片中。待其宿主诊断通道和原生生命周期行为单独明确并完成支持验证后再纳入。

## Server 停止后编程 Agent 仍继续工作

这是预期行为。已支持的集成都遵循 fail-open，Memory 故障不能阻塞普通工作。请查看宿主可见的诊断并运行
`powercontext doctor`；重启 Server 后即可恢复召回和采集，现有数据库会被自动重新打开。

## Codex 没有注入召回上下文

对于故障，查看 Hook 顶层 `systemMessage` 中的单行 JSON 事件。`empty` 表示 Runtime 没有为本轮准备上下文，
它仍是本地诊断，不作为宿主 warning。`version_mismatch`
表示已安装插件要求 `POST /v1/context/prepare`，但 Server 尚未提供该接口；请从同一个 ref 重新安装插件和工具
并重启 Server。`server_unavailable` 和 `invalid_response` 分别表示传输与 contract 问题。诊断事件会刻意
省略 query 与准备好的上下文正文。

执行 `powercontext capabilities`，确认 Context versions 中包含
`powercontext.prepared-context.v1`。

## Claude Code 没有注入召回上下文

先区分安装问题和 Server 健康问题：

```bash
powercontext doctor claude-code
powercontext doctor
```

第一个命令只检查 Claude CLI 和已启用插件，不连接 Server；第二个命令检查 Server liveness 和 readiness。
然后查看 Hook 顶层 `systemMessage` 中的单行事件。Claude Code 使用与 Codex 相同的 Prepared Context contract，
component 为 `powercontext.claude_code.recall`：

| Outcome | 处理方式 |
| --- | --- |
| `empty` | 没有准备出相关 Memory，无需处理 |
| `authentication_failed` | 启动 Claude Code 前导出完整的 `POWERCONTEXT_CLAUDE_AUTHORIZATION` header |
| `version_mismatch` | 从同一个 ref 安装 package 和插件，再重启两个进程 |
| `server_unavailable` | 启动 Server，或修正 `POWERCONTEXT_CLAUDE_SERVER_URL` |
| `invalid_response` | 检查 proxy、redirect、不兼容 schema、错误 JSON 或超大响应 |

诊断不会记录 token、query、scope、Prepared Context 正文或响应正文。Prompt 采集与召回彼此独立：采集失败
不会抑制有效上下文，召回失败也不会抑制采集。

## Claude Code MCP 认证失败

Hook 与 MCP `headersHelper` 都从启动 Claude Code 的进程环境读取
`POWERCONTEXT_CLAUDE_AUTHORIZATION`。停止当前进程，导出完整 header，再重新启动：

```bash
export POWERCONTEXT_CLAUDE_AUTHORIZATION="Bearer $POWERCONTEXT_LOCAL_TOKEN"
claude
```

不要把 token 加入 `.mcp.json`、Server URL 或插件选项。重启后使用 `/mcp` 确认 `powercontext` Server 已连接。

## Pi 没有注入召回上下文

先分别检查 package 和 Server：

```bash
powercontext doctor pi
powercontext doctor
```

安装 package 或修改 `POWERCONTEXT_PI_*` 变量后，请重启 Pi。在新的 Pi 会话中运行 `/pc doctor`，直接检查已配置的
Server。召回会正常降级，并在 Server 不可用、重定向、超时或返回无效 PreparedContext 时通过宿主终端输出无内容
warning；Pi 会继续运行且不添加上下文。恢复 Server 后，运行 `powercontext capabilities`，确认 Context versions 中包含
`powercontext.prepared-context.v1`。
